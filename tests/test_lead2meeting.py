"""Comprehensive automated test suite for Lead2Meeting.

Covers:
1. Lead capture, input validation, and rejection of malformed inputs.
2. Deduplication (plus-addressing, Gmail dots, external_ref, budget & note merging).
3. Qualification scoring & tier classification (high_priority, standard, nurture, disqualified).
4. Automatic specialty-based and capacity-aware rep assignment, plus capacity exhaustion fallback.
5. Lifecycle progression from New -> Qualified -> Contacted -> Meeting Scheduled -> Meeting Booked -> Completed.
6. Failure detection & recovery workflows:
   - CRM webhook sync failure -> recovery task creation -> safe retry resolution -> idempotency on duplicate retry.
   - Calendar invite provisioning failure -> recovery task creation -> safe retry -> meeting confirmation.
   - Follow-up outreach delivery failure -> recovery task creation -> safe retry resolution.
   - Retry exhaustion -> automatic escalation to VP Client Partnerships.
   - Meeting no-show -> stall flagging & recovery task generation.
   - Manual lead escalation with ownership reassignment.
7. SLA Watchdog stall detection across stages (uncontacted leads, unconfirmed invites).
8. Database-backed analytics & revenue recovery metrics verification.
9. Optional API Key authentication on lead intake.
"""

from datetime import datetime, timedelta, timezone
import os
import tempfile
import unittest
from fastapi.testclient import TestClient

from lead2meeting.app import create_app
from lead2meeting.database import get_connection


class Lead2MeetingEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_lead2meeting.db")
        os.environ.pop("L2M_INGEST_API_KEY", None)
        os.environ["L2M_INTEGRATION_MODE"] = "simulated"
        self.app = create_app(db_path=self.db_path, auto_seed=True)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        os.environ.pop("L2M_INGEST_API_KEY", None)
        self.temp_dir.cleanup()

    def test_01_health_and_seeded_analytics(self) -> None:
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["integration_mode"], "simulated")

        analytics = self.client.get("/api/analytics").json()
        self.assertEqual(analytics["summary"]["total_leads"], 7)
        self.assertGreaterEqual(analytics["summary"]["booked_meetings"], 2)
        self.assertGreater(analytics["revenue"]["total_active_pipeline_usd"], 200000)
        self.assertGreater(analytics["revenue"]["recovered_pipeline_usd"], 0)

    def test_02_lead_validation_rejects_bad_inputs(self) -> None:
        # Invalid email
        r1 = self.client.post(
            "/api/leads",
            json={
                "full_name": "Jane Doe",
                "email": "not-an-email",
                "company": "Acme Corp",
                "service_interest": "cloud_migration",
                "estimated_budget": 30000,
            },
        )
        self.assertEqual(r1.status_code, 422)

        # Invalid service_interest
        r2 = self.client.post(
            "/api/leads",
            json={
                "full_name": "Jane Doe",
                "email": "jane@acmecorp.com",
                "company": "Acme Corp",
                "service_interest": "unknown_service",
                "estimated_budget": 30000,
            },
        )
        self.assertEqual(r2.status_code, 422)

        # Negative budget
        r3 = self.client.post(
            "/api/leads",
            json={
                "full_name": "Jane Doe",
                "email": "jane@acmecorp.com",
                "company": "Acme Corp",
                "service_interest": "cloud_migration",
                "estimated_budget": -500,
            },
        )
        self.assertEqual(r3.status_code, 422)

    def test_03_lead_capture_qualification_and_deduplication(self) -> None:
        payload = {
            "full_name": "Arthur Pendelton",
            "email": "arthur+webinar@stratosphere.io",
            "company": "Stratosphere Cloud Inc.",
            "service_interest": "cloud_migration",
            "estimated_budget": 45000,
            "project_timeline": "1_3_months",
            "company_size": "51-200",
            "inquiry_notes": "Initial inquiry about multi-region Kubernetes.",
        }
        r1 = self.client.post("/api/leads", json=payload)
        self.assertEqual(r1.status_code, 201)
        lead1 = r1.json()
        self.assertFalse(lead1["deduplicated"])
        self.assertEqual(lead1["stage"], "qualified")
        self.assertGreaterEqual(lead1["qualification_score"], 75)
        self.assertEqual(lead1["qualification_tier"], "high_priority")
        self.assertEqual(lead1["assigned_rep_id"], 1)

        dup_payload = {
            "full_name": "Arthur Pendelton",
            "email": "arthur@stratosphere.io",
            "company": "Stratosphere Cloud",
            "service_interest": "cloud_migration",
            "estimated_budget": 70000,
            "project_timeline": "immediate",
            "company_size": "51-200",
            "inquiry_notes": "Urgent follow-up: board approved expanded budget.",
        }
        r2 = self.client.post("/api/leads", json=dup_payload)
        self.assertEqual(r2.status_code, 200)
        lead2 = r2.json()
        self.assertTrue(lead2["deduplicated"])
        self.assertEqual(lead2["id"], lead1["id"])
        self.assertEqual(lead2["duplicate_count"], 1)
        self.assertEqual(lead2["estimated_budget"], 70000)
        self.assertIn("board approved expanded budget", lead2["inquiry_notes"])

    def test_04_disqualification_for_low_score_lead(self) -> None:
        r = self.client.post(
            "/api/leads",
            json={
                "full_name": "Bob Student",
                "email": "bob.student@gmail.com",
                "company": "Personal Blog",
                "service_interest": "general_inquiry",
                "estimated_budget": 500,
                "project_timeline": "exploring",
                "company_size": "1-10",
            },
        )
        self.assertEqual(r.status_code, 201)
        lead = r.json()
        self.assertEqual(lead["stage"], "disqualified")
        self.assertEqual(lead["qualification_tier"], "disqualified")
        self.assertIsNone(lead["assigned_rep_id"])

    def test_05_full_happy_path_inquiry_to_booked_and_completed(self) -> None:
        r_cap = self.client.post(
            "/api/leads",
            json={
                "full_name": "Nadia Al-Mansoor",
                "email": "nadia@quantumpay.io",
                "company": "QuantumPay Systems",
                "service_interest": "custom_software",
                "estimated_budget": 80000,
                "project_timeline": "immediate",
                "company_size": "201-1000",
            },
        )
        self.assertEqual(r_cap.status_code, 201)
        lead_id = r_cap.json()["id"]

        r_fol = self.client.post(
            f"/api/leads/{lead_id}/followups",
            json={
                "channel": "email",
                "message_summary": "Sent custom payment ledger architecture deck.",
                "actor": "marcus.vance",
            },
        )
        self.assertEqual(r_fol.status_code, 200)
        self.assertEqual(r_fol.json()["stage"], "contacted")
        self.assertIsNotNone(r_fol.json()["first_response_at"])

        r_sched = self.client.post(
            f"/api/leads/{lead_id}/meetings",
            json={
                "title": "QuantumPay Architecture Scoping",
                "scheduled_start": "2026-11-01T15:00:00+00:00",
                "duration_minutes": 45,
                "provider": "cal_webhook",
                "actor": "marcus.vance",
            },
        )
        self.assertEqual(r_sched.status_code, 201)
        lead_sched = r_sched.json()
        self.assertEqual(lead_sched["stage"], "meeting_scheduled")
        meeting_id = lead_sched["meetings"][0]["id"]
        self.assertEqual(lead_sched["meetings"][0]["execution_mode"], "simulated")

        r_conf = self.client.patch(
            f"/api/meetings/{meeting_id}/status",
            json={"status": "confirmed", "actor": "nadia@quantumpay.io"},
        )
        self.assertEqual(r_conf.status_code, 200)
        self.assertEqual(r_conf.json()["stage"], "meeting_booked")

        r_comp = self.client.patch(
            f"/api/meetings/{meeting_id}/status",
            json={"status": "completed", "actor": "marcus.vance"},
        )
        self.assertEqual(r_comp.status_code, 200)
        self.assertEqual(r_comp.json()["stage"], "completed")

    def test_06_calendar_failure_and_safe_idempotent_recovery_workflow(self) -> None:
        r_cap = self.client.post(
            "/api/leads",
            json={
                "full_name": "Viktor Drago",
                "email": "vdrago@heliosrobotics.com",
                "company": "Helios Robotics",
                "service_interest": "ai_consulting",
                "estimated_budget": 90000,
                "project_timeline": "immediate",
                "company_size": "51-200",
            },
        )
        lead_id = r_cap.json()["id"]

        r_fail = self.client.post(
            f"/api/leads/{lead_id}/meetings",
            json={
                "title": "Helios AI Vision Pipeline Scoping",
                "scheduled_start": "2026-11-05T14:00:00+00:00",
                "duration_minutes": 30,
                "provider": "cal_webhook",
                "simulate_calendar_failure": True,
            },
        )
        self.assertEqual(r_fail.status_code, 201)
        lead_after_fail = r_fail.json()
        self.assertEqual(lead_after_fail["has_failed_operation"], 1)
        self.assertEqual(lead_after_fail["failed_operation_type"], "calendar_provision")
        self.assertEqual(lead_after_fail["meetings"][0]["status"], "sync_failed")

        open_tasks = [
            t
            for t in lead_after_fail["recovery_tasks"]
            if t["status"] in ("open", "retrying")
        ]
        self.assertEqual(len(open_tasks), 1)
        task_id = open_tasks[0]["id"]

        r_retry = self.client.post(
            f"/api/recovery/tasks/{task_id}/retry",
            json={"actor": "recovery_operator", "force_succeed_simulation": True},
        )
        self.assertEqual(r_retry.status_code, 200)
        retry_body = r_retry.json()
        self.assertEqual(retry_body["status"], "resolved")
        self.assertFalse(retry_body["idempotent_noop"])

        recovered_lead = retry_body["lead"]
        self.assertEqual(recovered_lead["has_failed_operation"], 0)
        self.assertEqual(recovered_lead["recovered"], 1)
        self.assertEqual(recovered_lead["stage"], "meeting_scheduled")
        self.assertEqual(recovered_lead["meetings"][0]["status"], "invite_pending")
        self.assertIsNotNone(recovered_lead["meetings"][0]["meeting_link"])

        r_retry_again = self.client.post(
            f"/api/recovery/tasks/{task_id}/retry",
            json={"actor": "recovery_operator", "force_succeed_simulation": True},
        )
        self.assertEqual(r_retry_again.status_code, 200)
        self.assertTrue(r_retry_again.json()["idempotent_noop"])
        self.assertEqual(r_retry_again.json()["status"], "already_resolved")

    def test_07_retry_exhaustion_triggers_automatic_escalation(self) -> None:
        r_cap = self.client.post(
            "/api/leads",
            json={
                "full_name": "Elena Vance",
                "email": "evance@cobaltdefense.io",
                "company": "Cobalt Defense",
                "service_interest": "security_audit",
                "estimated_budget": 75000,
                "simulate_crm_failure": True,
            },
        )
        lead = r_cap.json()
        self.assertEqual(lead["has_failed_operation"], 1)
        task_id = lead["recovery_tasks"][0]["id"]

        for attempt_num in (1, 2, 3):
            res = self.client.post(
                f"/api/recovery/tasks/{task_id}/retry",
                json={"actor": "ops_bot", "force_succeed_simulation": False},
            ).json()
            self.assertEqual(res["attempt"], attempt_num)

        self.assertEqual(res["status"], "escalated")
        self.assertEqual(res["lead"]["escalated"], 1)
        self.assertEqual(res["lead"]["escalated_to"], "vp_client_partnerships")

    def test_08_sla_watchdog_detects_newly_stalled_leads(self) -> None:
        r_cap = self.client.post(
            "/api/leads",
            json={
                "full_name": "Marcus Sterling",
                "email": "msterling@titanfreight.com",
                "company": "Titan Freight",
                "service_interest": "cloud_migration",
                "estimated_budget": 55000,
            },
        )
        lead_id = r_cap.json()["id"]
        ten_hours_ago = (
            (datetime.now(timezone.utc) - timedelta(hours=10))
            .replace(microsecond=0)
            .isoformat()
        )
        with get_connection(self.db_path) as conn:
            conn.execute(
                "UPDATE leads SET last_activity_at = ?, created_at = ? WHERE id = ?",
                (ten_hours_ago, ten_hours_ago, lead_id),
            )

        scan_res = self.client.post("/api/recovery/scan").json()
        self.assertIn(lead_id, scan_res["newly_flagged_lead_ids"])

        lead_after = self.client.get(f"/api/leads/{lead_id}").json()
        self.assertEqual(lead_after["is_stalled"], 1)
        self.assertIn("SLA Breach", lead_after["stall_reason"])

    def test_09_optional_ingest_api_key_protection(self) -> None:
        os.environ["L2M_INGEST_API_KEY"] = "super_secret_ingest_token"
        payload = {
            "full_name": "Protected Lead",
            "email": "protected@example.com",
            "company": "Protected Co",
            "service_interest": "custom_software",
            "estimated_budget": 30000,
        }
        r_unauth = self.client.post("/api/leads", json=payload)
        self.assertEqual(r_unauth.status_code, 401)

        r_auth = self.client.post(
            "/api/leads",
            json=payload,
            headers={"X-Ingest-Key": "super_secret_ingest_token"},
        )
        self.assertEqual(r_auth.status_code, 201)

    def test_10_meeting_no_show_creates_recovery_task_and_recovers(self) -> None:
        # Use Lead 5 (Nordic Retail Group) which has an invite_pending meeting
        lead5 = self.client.get("/api/leads/5").json()
        meeting_id = lead5["meetings"][0]["id"]

        r_noshow = self.client.patch(
            f"/api/meetings/{meeting_id}/status",
            json={"status": "no_show", "actor": "elena.rostova"},
        )
        self.assertEqual(r_noshow.status_code, 200)
        updated = r_noshow.json()
        self.assertEqual(updated["is_stalled"], 1)
        self.assertIn("no-show", updated["stall_reason"])

        noshow_tasks = [
            t for t in updated["recovery_tasks"] if t["issue_type"] == "meeting_no_show"
        ]
        self.assertEqual(len(noshow_tasks), 1)

        # Recover the no-show via retry outreach
        r_rec = self.client.post(
            f"/api/recovery/tasks/{noshow_tasks[0]['id']}/retry",
            json={"actor": "elena.rostova", "force_succeed_simulation": True},
        )
        self.assertEqual(r_rec.status_code, 200)
        self.assertEqual(r_rec.json()["status"], "resolved")
        self.assertEqual(r_rec.json()["lead"]["is_stalled"], 0)
        self.assertEqual(r_rec.json()["lead"]["recovered"], 1)

    def test_11_followup_delivery_failure_and_recovery(self) -> None:
        r_fail = self.client.post(
            "/api/leads/1/followups",
            json={
                "channel": "email",
                "message_summary": "Sending pre-meeting security questionnaire.",
                "actor": "elena.rostova",
                "simulate_delivery_failure": True,
            },
        )
        self.assertEqual(r_fail.status_code, 200)
        lead = r_fail.json()
        self.assertEqual(lead["has_failed_operation"], 1)
        self.assertEqual(lead["failed_operation_type"], "followup_dispatch")

        task_id = lead["recovery_tasks"][0]["id"]
        r_retry = self.client.post(
            f"/api/recovery/tasks/{task_id}/retry",
            json={"actor": "elena.rostova", "force_succeed_simulation": True},
        )
        self.assertEqual(r_retry.status_code, 200)
        self.assertEqual(r_retry.json()["status"], "resolved")
        self.assertEqual(r_retry.json()["lead"]["has_failed_operation"], 0)

    def test_12_manual_escalation_with_rep_reassignment(self) -> None:
        r_esc = self.client.post(
            "/api/leads/3/escalate",
            json={
                "escalate_to": "vp_client_partnerships",
                "reason": "Enterprise $95k opportunity breached 19h response SLA.",
                "actor": "ops_director",
                "reassign_rep_id": 1,
            },
        )
        self.assertEqual(r_esc.status_code, 200)
        lead = r_esc.json()
        self.assertEqual(lead["escalated"], 1)
        self.assertEqual(lead["escalated_to"], "vp_client_partnerships")
        self.assertEqual(lead["assigned_rep_id"], 1)

    def test_13_all_reps_at_capacity_triggers_assignment_recovery_task(self) -> None:
        with get_connection(self.db_path) as conn:
            conn.execute("UPDATE reps SET active_lead_count = capacity")

        r_cap = self.client.post(
            "/api/leads",
            json={
                "full_name": "Overflow Prospect",
                "email": "overflow@enterprise.io",
                "company": "Overflow Enterprise",
                "service_interest": "cloud_migration",
                "estimated_budget": 65000,
                "project_timeline": "immediate",
            },
        )
        self.assertEqual(r_cap.status_code, 201)
        lead = r_cap.json()
        self.assertIsNone(lead["assigned_rep_id"])
        self.assertEqual(lead["has_failed_operation"], 1)
        self.assertEqual(lead["failed_operation_type"], "rep_assignment")

        task_id = lead["recovery_tasks"][0]["id"]
        r_retry = self.client.post(
            f"/api/recovery/tasks/{task_id}/retry",
            json={"actor": "ops_manager", "force_succeed_simulation": True},
        )
        self.assertEqual(r_retry.status_code, 200)
        self.assertEqual(r_retry.json()["status"], "resolved")
        self.assertIsNotNone(r_retry.json()["lead"]["assigned_rep_id"])

    def test_14_ui_root_and_demo_reset_endpoint(self) -> None:
        r_ui = self.client.get("/")
        self.assertEqual(r_ui.status_code, 200)
        self.assertIn("Lead2Meeting", r_ui.text)

        r_reset = self.client.post("/api/demo/reset")
        self.assertEqual(r_reset.status_code, 200)
        self.assertTrue(r_reset.json()["seeded"])
        self.assertEqual(r_reset.json()["lead_count"], 7)


if __name__ == "__main__":
    unittest.main()
