"""Realistic B2B sample dataset seeder for Lead2Meeting.

Populates consultation reps, active and historical leads, booked meetings,
stalled journeys, failed calendar/CRM operations, and recovered revenue examples
so the application is immediately informative out-of-the-box.
"""

from datetime import datetime, timedelta, timezone
import json
from typing import Optional

from lead2meeting.database import get_connection, init_db
from lead2meeting.services.lead_service import (
    compute_qualification,
    create_recovery_task_internal,
    normalize_company,
    normalize_email,
    record_event,
)


def _iso_offset(hours_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.replace(microsecond=0).isoformat()


def seed_demo_data(db_path: Optional[str] = None, force_reset: bool = False) -> dict:
    """Seed the SQLite database with realistic B2B consultancy leads and journeys."""
    init_db(db_path)

    with get_connection(db_path) as conn:
        existing_count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        if existing_count > 0 and not force_reset:
            return {"seeded": False, "reason": "Database already contains data", "lead_count": existing_count}

        if force_reset:
            conn.execute("DELETE FROM lead_events")
            conn.execute("DELETE FROM recovery_tasks")
            conn.execute("DELETE FROM meetings")
            conn.execute("DELETE FROM leads")
            conn.execute("DELETE FROM reps")

        now_iso = _iso_offset(0)

        # 1. Seed Consultation Reps / Practice Leads
        reps_data = [
            (
                1,
                "Elena Rostova",
                "elena.rostova@apexconsult.io",
                "Principal Cloud Architect",
                "cloud_migration",
                1,
                12,
                3,
                1,
                _iso_offset(240),
            ),
            (
                2,
                "Marcus Vance",
                "marcus.vance@apexconsult.io",
                "Lead Solutions Engineer",
                "custom_software",
                1,
                12,
                2,
                1,
                _iso_offset(240),
            ),
            (
                3,
                "Priya Nair",
                "priya.nair@apexconsult.io",
                "Director of Cybersecurity",
                "security_audit",
                1,
                10,
                2,
                1,
                _iso_offset(240),
            ),
            (
                4,
                "David Lindqvist",
                "david.lindqvist@apexconsult.io",
                "AI & Data Practice Lead",
                "ai_consulting",
                1,
                10,
                1,
                1,
                _iso_offset(240),
            ),
        ]
        conn.executemany(
            """
            INSERT INTO reps (
                id, name, email, role, specialty, active, capacity,
                active_lead_count, calendar_connected, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            reps_data,
        )

        # Helper to insert a lead cleanly
        def _insert_lead(
            full_name: str,
            email: str,
            company: str,
            phone: str,
            service: str,
            budget: int,
            timeline: str,
            company_size: str,
            source: str,
            notes: str,
            stage: str,
            substatus: str,
            rep_id: Optional[int],
            created_hours_ago: float,
            last_activity_hours_ago: float,
            first_resp_hours_ago: Optional[float] = None,
            resp_min: Optional[float] = None,
            dup_count: int = 0,
            is_stalled: int = 0,
            stall_reason: Optional[str] = None,
            has_failed: int = 0,
            failed_type: Optional[str] = None,
            last_err: Optional[str] = None,
            retry_count: int = 0,
            escalated: int = 0,
            escalated_to: Optional[str] = None,
            recovered: int = 0,
            recovered_hours_ago: Optional[float] = None,
        ) -> int:
            norm_email, domain = normalize_email(email)
            norm_comp = normalize_company(company)
            score, tier, breakdown = compute_qualification(
                budget, service, timeline, company_size, domain
            )
            created_ts = _iso_offset(created_hours_ago)
            last_act_ts = _iso_offset(last_activity_hours_ago)
            first_resp_ts = (
                _iso_offset(first_resp_hours_ago)
                if first_resp_hours_ago is not None
                else None
            )
            rec_ts = (
                _iso_offset(recovered_hours_ago)
                if recovered_hours_ago is not None
                else None
            )

            cur = conn.execute(
                """
                INSERT INTO leads (
                    external_ref, full_name, email, normalized_email, company,
                    normalized_company, domain, phone, service_interest,
                    estimated_budget, project_timeline, company_size, source,
                    inquiry_notes, qualification_score, qualification_tier,
                    qualification_breakdown, stage, substatus, assigned_rep_id,
                    duplicate_count, is_stalled, stall_reason, has_failed_operation,
                    failed_operation_type, last_error_message, retry_count,
                    escalated, escalated_to, recovered, recovered_at,
                    first_response_at, response_time_minutes, last_activity_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"ext_{norm_comp.replace(' ', '_')}",
                    full_name,
                    email,
                    norm_email,
                    company,
                    norm_comp,
                    domain,
                    phone,
                    service,
                    budget,
                    timeline,
                    company_size,
                    source,
                    notes,
                    score,
                    tier,
                    json.dumps(breakdown),
                    stage,
                    substatus,
                    rep_id,
                    dup_count,
                    is_stalled,
                    stall_reason,
                    has_failed,
                    failed_type,
                    last_err,
                    retry_count,
                    escalated,
                    escalated_to,
                    recovered,
                    rec_ts,
                    first_resp_ts,
                    resp_min,
                    last_act_ts,
                    created_ts,
                    last_act_ts,
                ),
            )
            return int(cur.lastrowid)

        # Lead 1: Healthy Booked Meeting (High-value Cloud Migration)
        l1 = _insert_lead(
            full_name="Hannah Sterling",
            email="h.sterling@vanguardlogistics.com",
            company="Vanguard Logistics Corp",
            phone="+1-312-555-0192",
            service="cloud_migration",
            budget=85000,
            timeline="immediate",
            company_size="201-1000",
            source="partner_referral",
            notes="Migrating legacy on-prem ERP & warehouse telemetry cluster to AWS EKS.",
            stage="meeting_booked",
            substatus="confirmed_on_calendar",
            rep_id=1,
            created_hours_ago=72,
            last_activity_hours_ago=18,
            first_resp_hours_ago=71.5,
            resp_min=28.0,
        )
        record_event(
            conn,
            l1,
            "lead.captured",
            "Captured new inquiry from Hannah Sterling (Vanguard Logistics Corp) via partner_referral.",
            created_at=_iso_offset(72),
        )
        record_event(
            conn,
            l1,
            "lead.qualified",
            "Scored 100/100 (high priority) based on $85,000 budget and immediate timeline.",
            category="qualification",
            created_at=_iso_offset(72),
        )
        record_event(
            conn,
            l1,
            "lead.assigned",
            "Assigned to Elena Rostova (Principal Cloud Architect).",
            category="assignment",
            created_at=_iso_offset(72),
        )
        record_event(
            conn,
            l1,
            "meeting.scheduled",
            "[SIMULATED] Provisioned sandbox calendar slot sim_cal_vanguard1.",
            category="scheduling",
            execution_mode="simulated",
            actor="elena.rostova",
            created_at=_iso_offset(71.5),
        )
        record_event(
            conn,
            l1,
            "meeting.confirmed",
            "Prospect confirmed meeting 'AWS EKS Migration Architecture Review'. Lead converted to Booked Meeting.",
            category="scheduling",
            execution_mode="simulated",
            actor="h.sterling@vanguardlogistics.com",
            created_at=_iso_offset(18),
        )
        conn.execute(
            """
            INSERT INTO meetings (
                lead_id, rep_id, title, scheduled_start, duration_minutes,
                meeting_link, provider, execution_mode, status,
                calendar_event_id, sync_status, followup_count, notes, created_at, updated_at
            ) VALUES (?, 1, 'AWS EKS Migration Architecture Review', ?, 45,
                      'https://sandbox.lead2meeting.local/meet/sim_cal_vanguard1',
                      'cal_webhook', 'simulated', 'confirmed', 'sim_cal_vanguard1', 'synced', 1,
                      'CTO and VP Infrastructure attending.', ?, ?)
            """,
            (l1, _iso_offset(-24), _iso_offset(71.5), _iso_offset(18)),
        )

        # Lead 2: Failed Calendar Provisioning -> Needs Recovery Retry
        cal_err_msg = (
            "[SIMULATED] Calendar invite provisioning failed: Calendar provider API "
            "timed out waiting for slot lock (CAL_TIMEOUT_504)."
        )
        l2 = _insert_lead(
            full_name="Lucas Meyer",
            email="lucas.meyer@kestrelfintech.io",
            company="Kestrel FinTech GmbH",
            phone="+49-30-5550-8821",
            service="security_audit",
            budget=62000,
            timeline="immediate",
            company_size="51-200",
            source="website_form",
            notes="SOC2 Type II readiness + penetration testing for payment API before Series B close.",
            stage="contacted",
            substatus="calendar_sync_failed",
            rep_id=3,
            created_hours_ago=14,
            last_activity_hours_ago=3,
            first_resp_hours_ago=13.2,
            resp_min=48.0,
            has_failed=1,
            failed_type="calendar_provision",
            last_err=cal_err_msg,
        )
        record_event(
            conn,
            l2,
            "lead.captured",
            "Captured new inquiry from Lucas Meyer (Kestrel FinTech GmbH) via website_form.",
            created_at=_iso_offset(14),
        )
        record_event(
            conn,
            l2,
            "lead.assigned",
            "Assigned to Priya Nair (Director of Cybersecurity).",
            category="assignment",
            created_at=_iso_offset(14),
        )
        record_event(
            conn,
            l2,
            "followup.sent",
            "[SIMULATED] Logged email follow-up in local sandbox. Summary: Shared SOC2 audit scope questionnaire.",
            category="outreach",
            execution_mode="simulated",
            actor="priya.nair",
            created_at=_iso_offset(13.2),
        )
        cur_m2 = conn.execute(
            """
            INSERT INTO meetings (
                lead_id, rep_id, title, scheduled_start, duration_minutes,
                meeting_link, provider, execution_mode, status,
                calendar_event_id, sync_status, sync_error, notes, created_at, updated_at
            ) VALUES (?, 3, 'SOC2 Type II & PenTest Scoping Call', ?, 30,
                      NULL, 'cal_webhook', 'simulated', 'sync_failed', NULL, 'failed', ?,
                      'Prospect requested Thursday morning slot.', ?, ?)
            """,
            (l2, _iso_offset(-48), cal_err_msg, _iso_offset(3), _iso_offset(3)),
        )
        m2_id = int(cur_m2.lastrowid)
        record_event(
            conn,
            l2,
            "meeting.schedule_failed",
            cal_err_msg,
            category="scheduling",
            status="failed",
            execution_mode="simulated",
            actor="priya.nair",
            details={"meeting_id": m2_id, "error_code": "CAL_TIMEOUT_504"},
            created_at=_iso_offset(3),
        )
        create_recovery_task_internal(
            conn,
            lead_id=l2,
            issue_type="calendar_sync_failure",
            severity="critical",
            title="Calendar invite failed for Kestrel FinTech GmbH (SOC2 Scoping)",
            description=cal_err_msg,
            operation_type="calendar_provision",
            operation_payload={
                "lead_id": l2,
                "meeting_id": m2_id,
                "rep_id": 3,
                "title": "SOC2 Type II & PenTest Scoping Call",
                "scheduled_start": _iso_offset(-48),
                "duration_minutes": 30,
                "provider": "cal_webhook",
            },
            failure_reason=cal_err_msg,
            idempotency_key=f"cal_fail_meeting_{m2_id}",
            created_at=_iso_offset(3),
        )

        # Lead 3: Stalled High-Priority Lead (SLA Breach: >4h with no initial outreach)
        stall_msg_3 = "SLA Breach: No initial outreach after 19.5h (SLA threshold: 4h)"
        l3 = _insert_lead(
            full_name="Sophia Chen",
            email="schen@meridianbio.com",
            company="Meridian BioSystems",
            phone="+1-617-555-0148",
            service="custom_software",
            budget=95000,
            timeline="1_3_months",
            company_size="201-1000",
            source="inbound_webinar",
            notes="Needs custom LIMS portal & automated clinical lab reporting dashboard. Submitted twice.",
            stage="qualified",
            substatus="assigned_awaiting_outreach",
            rep_id=2,
            created_hours_ago=19.5,
            last_activity_hours_ago=19.5,
            dup_count=1,
            is_stalled=1,
            stall_reason=stall_msg_3,
        )
        record_event(
            conn,
            l3,
            "lead.captured",
            "Captured new inquiry from Sophia Chen (Meridian BioSystems) via inbound_webinar.",
            created_at=_iso_offset(19.5),
        )
        record_event(
            conn,
            l3,
            "lead.deduplicated",
            "Merged duplicate submission (#2) for schen@meridianbio.com from website_form.",
            category="deduplication",
            created_at=_iso_offset(19.0),
        )
        record_event(
            conn,
            l3,
            "lead.assigned",
            "Assigned to Marcus Vance (Lead Solutions Engineer).",
            category="assignment",
            created_at=_iso_offset(19.5),
        )
        record_event(
            conn,
            l3,
            "lead.stalled_detected",
            stall_msg_3,
            category="recovery",
            status="warning",
            actor="sla_watchdog",
            created_at=_iso_offset(2.0),
        )
        create_recovery_task_internal(
            conn,
            lead_id=l3,
            issue_type="stalled_lead",
            severity="critical",
            title="Stalled in 'qualified': Meridian BioSystems ($95k)",
            description=stall_msg_3,
            operation_type="nudge_followup",
            operation_payload={
                "lead_id": l3,
                "stage": "qualified",
                "hours_idle": 19.5,
                "channel": "email",
            },
            failure_reason=stall_msg_3,
            idempotency_key=f"stall_{l3}_qualified",
            created_at=_iso_offset(2.0),
        )

        # Lead 4: Previously Failed CRM Sync & Stalled -> Successfully Recovered & Booked!
        l4 = _insert_lead(
            full_name="Omar Al-Mansoor",
            email="omar@crescentenergy.ae",
            company="Crescent Grid Analytics",
            phone="+971-4-555-0199",
            service="ai_consulting",
            budget=110000,
            timeline="immediate",
            company_size="201-1000",
            source="executive_briefing",
            notes="Predictive grid load forecasting model & LLM operator copilot.",
            stage="meeting_booked",
            substatus="confirmed_on_calendar",
            rep_id=4,
            created_hours_ago=96,
            last_activity_hours_ago=12,
            first_resp_hours_ago=88,
            resp_min=480.0,
            retry_count=1,
            recovered=1,
            recovered_hours_ago=40,
        )
        record_event(
            conn,
            l4,
            "lead.captured",
            "Captured new inquiry from Omar Al-Mansoor (Crescent Grid Analytics).",
            created_at=_iso_offset(96),
        )
        record_event(
            conn,
            l4,
            "integration.crm_sync_failed",
            "[SIMULATED] CRM webhook endpoint returned HTTP 503 Service Unavailable.",
            category="integration",
            status="failed",
            execution_mode="simulated",
            created_at=_iso_offset(96),
        )
        t4_id = create_recovery_task_internal(
            conn,
            lead_id=l4,
            issue_type="integration_failure",
            severity="medium",
            title="CRM webhook sync failed for Crescent Grid Analytics",
            description="HTTP 503 Service Unavailable during initial lead intake.",
            operation_type="crm_lead_sync",
            operation_payload={"lead_id": l4, "company": "Crescent Grid Analytics"},
            failure_reason="HTTP 503 Service Unavailable",
            idempotency_key=f"crm_sync_{l4}",
            created_at=_iso_offset(96),
        )
        conn.execute(
            """
            UPDATE recovery_tasks
            SET status = 'resolved',
                attempt_count = 1,
                last_attempted_at = ?,
                resolved_at = ?,
                resolution_notes = '[SIMULATED] Recovered CRM sync on retry #1 (ref: sim_crm_99a1b2c3).'
            WHERE id = ?
            """,
            (_iso_offset(40), _iso_offset(40), t4_id),
        )
        record_event(
            conn,
            l4,
            "recovery.succeeded",
            "Recovered 'CRM webhook sync failed for Crescent Grid Analytics' on attempt #1.",
            category="recovery",
            status="recovered",
            execution_mode="simulated",
            actor="ops_recovery_bot",
            created_at=_iso_offset(40),
        )
        record_event(
            conn,
            l4,
            "meeting.confirmed",
            "Prospect confirmed meeting 'AI Grid Forecasting Architecture Workshop'. Lead converted to Booked Meeting.",
            category="scheduling",
            execution_mode="simulated",
            actor="omar@crescentenergy.ae",
            created_at=_iso_offset(12),
        )
        conn.execute(
            """
            INSERT INTO meetings (
                lead_id, rep_id, title, scheduled_start, duration_minutes,
                meeting_link, provider, execution_mode, status,
                calendar_event_id, sync_status, followup_count, notes, created_at, updated_at
            ) VALUES (?, 4, 'AI Grid Forecasting Architecture Workshop', ?, 60,
                      'https://sandbox.lead2meeting.local/meet/sim_cal_crescent4',
                      'cal_webhook', 'simulated', 'confirmed', 'sim_cal_crescent4', 'synced', 2,
                      'Recovered after initial webhook failure; VP Engineering attending.', ?, ?)
            """,
            (l4, _iso_offset(-18), _iso_offset(36), _iso_offset(12)),
        )

        # Lead 5: Meeting Scheduled (Invite Pending > 24h -> Unconfirmed Invite Stall)
        stall_msg_5 = "Unconfirmed Invite: Calendar invite pending for 31.0h without prospect confirmation (threshold: 24h)"
        l5 = _insert_lead(
            full_name="Rachel Lindholm",
            email="rlindholm@nordicretail.se",
            company="Nordic Retail Group",
            phone="+46-8-555-0133",
            service="cloud_migration",
            budget=48000,
            timeline="1_3_months",
            company_size="51-200",
            source="website_form",
            notes="Multi-region Postgres HA & Kubernetes storefront modernization.",
            stage="meeting_scheduled",
            substatus="invite_pending_confirmation",
            rep_id=1,
            created_hours_ago=40,
            last_activity_hours_ago=31,
            first_resp_hours_ago=39,
            resp_min=60.0,
            is_stalled=1,
            stall_reason=stall_msg_5,
        )
        conn.execute(
            """
            INSERT INTO meetings (
                lead_id, rep_id, title, scheduled_start, duration_minutes,
                meeting_link, provider, execution_mode, status,
                calendar_event_id, sync_status, followup_count, notes, created_at, updated_at
            ) VALUES (?, 1, 'Storefront Cloud Modernization Discovery', ?, 30,
                      'https://sandbox.lead2meeting.local/meet/sim_cal_nordic5',
                      'cal_webhook', 'simulated', 'invite_pending', 'sim_cal_nordic5', 'synced', 1,
                      'Invite sent; awaiting prospect calendar RSVP.', ?, ?)
            """,
            (l5, _iso_offset(-12), _iso_offset(31), _iso_offset(31)),
        )
        record_event(
            conn,
            l5,
            "lead.captured",
            "Captured new inquiry from Rachel Lindholm (Nordic Retail Group).",
            created_at=_iso_offset(40),
        )
        record_event(
            conn,
            l5,
            "meeting.scheduled",
            "[SIMULATED] Provisioned sandbox calendar slot sim_cal_nordic5.",
            category="scheduling",
            execution_mode="simulated",
            actor="elena.rostova",
            created_at=_iso_offset(31),
        )
        record_event(
            conn,
            l5,
            "lead.stalled_detected",
            stall_msg_5,
            category="recovery",
            status="warning",
            actor="sla_watchdog",
            created_at=_iso_offset(5),
        )
        create_recovery_task_internal(
            conn,
            lead_id=l5,
            issue_type="stalled_lead",
            severity="high",
            title="Stalled in 'meeting_scheduled': Nordic Retail Group",
            description=stall_msg_5,
            operation_type="nudge_followup",
            operation_payload={
                "lead_id": l5,
                "stage": "meeting_scheduled",
                "hours_idle": 31.0,
                "channel": "email",
            },
            failure_reason=stall_msg_5,
            idempotency_key=f"stall_{l5}_meeting_scheduled",
            created_at=_iso_offset(5),
        )

        # Lead 6: Completed Discovery Meeting
        l6 = _insert_lead(
            full_name="Devon Brooks",
            email="dbrooks@harborhealth.org",
            company="Harbor Health Network",
            phone="+1-415-555-0174",
            service="security_audit",
            budget=54000,
            timeline="immediate",
            company_size="201-1000",
            source="partner_referral",
            notes="HIPAA compliance audit & zero-trust network segmentation.",
            stage="completed",
            substatus="meeting_held",
            rep_id=3,
            created_hours_ago=120,
            last_activity_hours_ago=24,
            first_resp_hours_ago=119.5,
            resp_min=30.0,
        )
        conn.execute(
            """
            INSERT INTO meetings (
                lead_id, rep_id, title, scheduled_start, duration_minutes,
                meeting_link, provider, execution_mode, status,
                calendar_event_id, sync_status, followup_count, notes, created_at, updated_at
            ) VALUES (?, 3, 'HIPAA Security Audit Discovery Call', ?, 45,
                      'https://sandbox.lead2meeting.local/meet/sim_cal_harbor6',
                      'cal_webhook', 'simulated', 'completed', 'sim_cal_harbor6', 'synced', 1,
                      'Completed discovery call; SOW drafted.', ?, ?)
            """,
            (l6, _iso_offset(25), _iso_offset(110), _iso_offset(24)),
        )
        record_event(
            conn,
            l6,
            "lead.captured",
            "Captured new inquiry from Devon Brooks (Harbor Health Network).",
            created_at=_iso_offset(120),
        )
        record_event(
            conn,
            l6,
            "meeting.completed",
            "Discovery meeting 'HIPAA Security Audit Discovery Call' completed.",
            category="scheduling",
            actor="priya.nair",
            created_at=_iso_offset(24),
        )

        # Lead 7: Low-budget / Free-email Disqualified Inquiry
        l7 = _insert_lead(
            full_name="Kevin Miller",
            email="kevin.freelance99@gmail.com",
            company="Kevin Side Project",
            phone="",
            service="general_inquiry",
            budget=1500,
            timeline="exploring",
            company_size="1-10",
            source="website_form",
            notes="Looking for someone to fix a WordPress theme CSS bug.",
            stage="disqualified",
            substatus="below_qualification_threshold",
            rep_id=None,
            created_hours_ago=48,
            last_activity_hours_ago=48,
        )
        record_event(
            conn,
            l7,
            "lead.captured",
            "Captured new inquiry from Kevin Miller (Kevin Side Project).",
            created_at=_iso_offset(48),
        )
        record_event(
            conn,
            l7,
            "lead.qualified",
            "Scored 19/100 (disqualified) — below minimum agency engagement threshold.",
            category="qualification",
            created_at=_iso_offset(48),
        )

        return {"seeded": True, "lead_count": 7, "rep_count": 4}
