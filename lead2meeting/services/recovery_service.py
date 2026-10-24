"""Stall detection, safe idempotent retries, recovery actions, and escalation service.

# SISMAX — Idempotent Revenue Recovery & SLA Watchdog Engine
"""

from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from lead2meeting.config import get_settings
from lead2meeting.database import get_connection
from lead2meeting.schemas import EscalationRequest, RecoveryActionRequest
from lead2meeting.services.integration_service import (
    dispatch_followup_message,
    provision_calendar_invite,
    sync_lead_to_crm,
)
from lead2meeting.services.lead_service import (
    create_recovery_task_internal,
    get_lead_detail,
    record_event,
    select_best_rep,
    utc_now_iso,
)


def parse_iso_utc(ts_str: str) -> datetime:
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def scan_for_stalled_leads(
    reference_time_iso: Optional[str] = None, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Scan all active leads against SLA thresholds to flag stalled prospects and open recovery tasks."""
    settings = get_settings()
    now_iso = reference_time_iso or utc_now_iso()
    now_dt = parse_iso_utc(now_iso)

    newly_flagged: List[int] = []
    already_stalled: List[int] = []

    with get_connection(db_path) as conn:
        active_leads = conn.execute(
            """
            SELECT * FROM leads
            WHERE stage IN ('new', 'qualified', 'contacted', 'meeting_scheduled')
            """
        ).fetchall()

        for lead in active_leads:
            lead_id = int(lead["id"])
            last_act_dt = parse_iso_utc(lead["last_activity_at"])
            hours_idle = (now_dt - last_act_dt).total_seconds() / 3600.0

            stall_reason: Optional[str] = None
            severity = "high"

            if lead["stage"] in ("new", "qualified") and not lead["first_response_at"]:
                if hours_idle >= settings.sla_new_lead_hours:
                    stall_reason = (
                        f"SLA Breach: No initial outreach after {hours_idle:.1f}h "
                        f"(SLA threshold: {settings.sla_new_lead_hours:.0f}h)"
                    )
                    severity = "critical" if lead["qualification_score"] >= 75 else "high"

            elif lead["stage"] == "contacted":
                if hours_idle >= settings.sla_followup_hours:
                    stall_reason = (
                        f"Follow-up Stalled: Prospect contacted {hours_idle:.1f}h ago "
                        f"with no meeting scheduled (threshold: {settings.sla_followup_hours:.0f}h)"
                    )
                    severity = "medium"

            elif lead["stage"] == "meeting_scheduled":
                if hours_idle >= settings.sla_invite_acceptance_hours:
                    stall_reason = (
                        f"Unconfirmed Invite: Calendar invite pending for {hours_idle:.1f}h "
                        f"without prospect confirmation (threshold: {settings.sla_invite_acceptance_hours:.0f}h)"
                    )
                    severity = "high"

            if stall_reason:
                if lead["is_stalled"]:
                    already_stalled.append(lead_id)
                else:
                    newly_flagged.append(lead_id)
                    conn.execute(
                        """
                        UPDATE leads
                        SET is_stalled = 1,
                            stall_reason = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (stall_reason, now_iso, lead_id),
                    )
                    record_event(
                        conn,
                        lead_id=lead_id,
                        event_type="lead.stalled_detected",
                        category="recovery",
                        status="warning",
                        execution_mode="internal",
                        actor="sla_watchdog",
                        summary=stall_reason,
                        details={"hours_idle": round(hours_idle, 1), "stage": lead["stage"]},
                        created_at=now_iso,
                    )
                    create_recovery_task_internal(
                        conn,
                        lead_id=lead_id,
                        issue_type="stalled_lead",
                        severity=severity,
                        title=f"Stalled in '{lead['stage']}': {lead['company']}",
                        description=stall_reason,
                        operation_type="nudge_followup",
                        operation_payload={
                            "lead_id": lead_id,
                            "stage": lead["stage"],
                            "hours_idle": round(hours_idle, 1),
                        },
                        failure_reason=stall_reason,
                        idempotency_key=f"stall_{lead_id}_{lead['stage']}",
                        created_at=now_iso,
                    )

    return {
        "scanned_at": now_iso,
        "newly_flagged_count": len(newly_flagged),
        "newly_flagged_lead_ids": newly_flagged,
        "already_stalled_count": len(already_stalled),
    }


def list_recovery_tasks(
    status: Optional[str] = None, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    query = """
        SELECT rt.*, l.full_name, l.company, l.email, l.stage, l.qualification_score,
               l.estimated_budget, l.assigned_rep_id, r.name AS rep_name
        FROM recovery_tasks rt
        JOIN leads l ON rt.lead_id = l.id
        LEFT JOIN reps r ON l.assigned_rep_id = r.id
        WHERE 1=1
    """
    params: List[Any] = []
    if status:
        query += " AND rt.status = ?"
        params.append(status)

    query += """
        ORDER BY
            CASE rt.status WHEN 'open' THEN 0 WHEN 'retrying' THEN 1 WHEN 'escalated' THEN 2 ELSE 3 END,
            CASE rt.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
            rt.id DESC
    """
    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["operation_payload"] = json.loads(
                item.get("operation_payload") or "{}"
            )
            results.append(item)
        return results


def execute_recovery_retry(
    task_id: int, req: RecoveryActionRequest, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Execute a safe, idempotent retry/recovery action for a failed or stalled recovery task."""
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        task = conn.execute(
            "SELECT * FROM recovery_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if not task:
            raise ValueError(f"Recovery task #{task_id} not found")

        if task["status"] == "resolved":
            return {
                "task_id": task_id,
                "status": "already_resolved",
                "idempotent_noop": True,
                "message": "Recovery task is already resolved; duplicate retry skipped safely.",
                "lead": get_lead_detail(int(task["lead_id"]), conn=conn),
            }

        lead_id = int(task["lead_id"])
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        payload = json.loads(task["operation_payload"] or "{}")
        attempt = int(task["attempt_count"]) + 1
        max_attempts = int(task["max_attempts"])

        if attempt > max_attempts:
            conn.execute(
                """
                UPDATE recovery_tasks
                SET status = 'escalated',
                    escalated_to = 'vp_client_partnerships',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, task_id),
            )
            conn.execute(
                """
                UPDATE leads
                SET escalated = 1,
                    escalated_to = 'vp_client_partnerships',
                    updated_at = ?
                WHERE id = ?
                """,
                (now, lead_id),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type="recovery.max_retries_exceeded",
                category="recovery",
                status="failed",
                execution_mode="internal",
                actor=req.actor,
                summary=f"Task #{task_id} exceeded max retry limit ({max_attempts}). Automatically escalated to VP Client Partnerships.",
                details={"task_id": task_id, "attempt": attempt},
                created_at=now,
            )
            return {
                "task_id": task_id,
                "status": "escalated",
                "idempotent_noop": False,
                "message": f"Maximum retry attempts ({max_attempts}) exceeded; automatically escalated.",
                "lead": get_lead_detail(lead_id, conn=conn),
            }

        op_type = task["operation_type"]
        simulate_fail = not req.force_succeed_simulation
        op_success = False
        exec_mode = "internal"
        summary_msg = ""

        if op_type == "crm_lead_sync":
            res = sync_lead_to_crm(
                {
                    "id": lead_id,
                    "full_name": lead["full_name"],
                    "email": lead["email"],
                    "company": lead["company"],
                    "score": lead["qualification_score"],
                    "tier": lead["qualification_tier"],
                },
                simulate_failure=simulate_fail,
            )
            op_success = res.success
            exec_mode = res.execution_mode
            summary_msg = res.message

        elif op_type == "calendar_provision":
            meeting_id = payload.get("meeting_id")
            rep_id = payload.get("rep_id") or lead["assigned_rep_id"]
            rep = conn.execute("SELECT * FROM reps WHERE id = ?", (rep_id,)).fetchone()
            # Ensure rep calendar is connected during recovery
            if rep and not rep["calendar_connected"] and req.force_succeed_simulation:
                conn.execute("UPDATE reps SET calendar_connected = 1 WHERE id = ?", (rep_id,))
                rep = conn.execute("SELECT * FROM reps WHERE id = ?", (rep_id,)).fetchone()

            cal_res = provision_calendar_invite(
                dict(lead),
                dict(rep) if rep else {"email": "rep@example.com", "calendar_connected": 1},
                {
                    "title": payload.get("title", "Discovery Consultation"),
                    "scheduled_start": payload.get("scheduled_start", now),
                    "duration_minutes": payload.get("duration_minutes", 30),
                    "provider": payload.get("provider", "cal_webhook"),
                },
                simulate_failure=simulate_fail,
            )
            op_success = cal_res.success
            exec_mode = cal_res.execution_mode
            summary_msg = cal_res.message

            if op_success and meeting_id:
                conn.execute(
                    """
                    UPDATE meetings
                    SET status = 'invite_pending',
                        sync_status = 'synced',
                        sync_error = NULL,
                        calendar_event_id = ?,
                        meeting_link = ?,
                        execution_mode = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        cal_res.external_id,
                        cal_res.payload_summary.get("meeting_link"),
                        cal_res.execution_mode,
                        now,
                        meeting_id,
                    ),
                )
                conn.execute(
                    """
                    UPDATE leads
                    SET stage = CASE WHEN stage IN ('new', 'qualified', 'contacted') THEN 'meeting_scheduled' ELSE stage END,
                        substatus = 'invite_pending_confirmation'
                    WHERE id = ?
                    """,
                    (lead_id,),
                )

        elif op_type in ("followup_dispatch", "nudge_followup", "reschedule_outreach"):
            msg_body = (
                req.override_notes
                or payload.get("message_summary")
                or f"Recovery follow-up regarding {lead['service_interest'].replace('_', ' ')} consultation and scheduling link."
            )
            f_res = dispatch_followup_message(
                dict(lead),
                channel=payload.get("channel", "email"),
                summary=msg_body,
                simulate_failure=simulate_fail,
            )
            op_success = f_res.success
            exec_mode = f_res.execution_mode
            summary_msg = f_res.message
            if op_success:
                first_resp = lead["first_response_at"] or now
                resp_min = lead["response_time_minutes"] or 15.0
                next_stage = (
                    "contacted"
                    if lead["stage"] in ("new", "qualified")
                    else lead["stage"]
                )
                conn.execute(
                    """
                    UPDATE leads
                    SET stage = ?,
                        substatus = 'recovery_outreach_sent',
                        first_response_at = ?,
                        response_time_minutes = ?
                    WHERE id = ?
                    """,
                    (next_stage, first_resp, resp_min, lead_id),
                )

        elif op_type == "assign_rep":
            rep = select_best_rep(conn, lead["service_interest"])
            if not rep and req.force_succeed_simulation:
                # Increase capacity on best rep to unblock recovery
                conn.execute("UPDATE reps SET capacity = capacity + 5 WHERE active = 1")
                rep = select_best_rep(conn, lead["service_interest"])
            if rep:
                op_success = True
                exec_mode = "internal"
                summary_msg = f"Assigned lead to {rep['name']} ({rep['email']}) after capacity rebalance."
                conn.execute(
                    "UPDATE leads SET assigned_rep_id = ?, substatus = 'assigned_awaiting_outreach' WHERE id = ?",
                    (rep["id"], lead_id),
                )
                conn.execute(
                    "UPDATE reps SET active_lead_count = active_lead_count + 1 WHERE id = ?",
                    (rep["id"],),
                )
            else:
                op_success = False
                summary_msg = "Still no active rep with available capacity."

        else:
            op_success = not simulate_fail
            summary_msg = f"Executed recovery handler for {op_type}."

        if op_success:
            conn.execute(
                """
                UPDATE recovery_tasks
                SET status = 'resolved',
                    attempt_count = ?,
                    last_attempted_at = ?,
                    resolved_at = ?,
                    resolution_notes = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (attempt, now, now, summary_msg, now, task_id),
            )

            # Check if any other open failure tasks exist for this lead
            remaining_open = conn.execute(
                """
                SELECT COUNT(*) FROM recovery_tasks
                WHERE lead_id = ? AND status IN ('open', 'retrying')
                """,
                (lead_id,),
            ).fetchone()[0]

            conn.execute(
                """
                UPDATE leads
                SET has_failed_operation = CASE WHEN ? = 0 THEN 0 ELSE has_failed_operation END,
                    failed_operation_type = CASE WHEN ? = 0 THEN NULL ELSE failed_operation_type END,
                    last_error_message = CASE WHEN ? = 0 THEN NULL ELSE last_error_message END,
                    is_stalled = 0,
                    stall_reason = NULL,
                    retry_count = retry_count + 1,
                    recovered = 1,
                    recovered_at = COALESCE(recovered_at, ?),
                    last_activity_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    remaining_open,
                    remaining_open,
                    remaining_open,
                    now,
                    now,
                    now,
                    lead_id,
                ),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type="recovery.succeeded",
                category="recovery",
                status="recovered",
                execution_mode=exec_mode,
                actor=req.actor,
                summary=f"Recovered '{task['title']}' on attempt #{attempt}. {summary_msg}",
                details={
                    "task_id": task_id,
                    "operation_type": op_type,
                    "attempt": attempt,
                    "override_notes": req.override_notes,
                },
                created_at=now,
            )
            return {
                "task_id": task_id,
                "status": "resolved",
                "idempotent_noop": False,
                "attempt": attempt,
                "execution_mode": exec_mode,
                "message": summary_msg,
                "lead": get_lead_detail(lead_id, conn=conn),
            }

        # Retry failed
        next_status = "escalated" if attempt >= max_attempts else "retrying"
        conn.execute(
            """
            UPDATE recovery_tasks
            SET status = ?,
                attempt_count = ?,
                last_attempted_at = ?,
                failure_reason = ?,
                escalated_to = CASE WHEN ? = 'escalated' THEN 'vp_client_partnerships' ELSE escalated_to END,
                updated_at = ?
            WHERE id = ?
            """,
            (next_status, attempt, now, summary_msg, next_status, now, task_id),
        )
        conn.execute(
            """
            UPDATE leads
            SET retry_count = retry_count + 1,
                last_error_message = ?,
                escalated = CASE WHEN ? = 'escalated' THEN 1 ELSE escalated END,
                escalated_to = CASE WHEN ? = 'escalated' THEN 'vp_client_partnerships' ELSE escalated_to END,
                updated_at = ?
            WHERE id = ?
            """,
            (summary_msg, next_status, next_status, now, lead_id),
        )
        record_event(
            conn,
            lead_id=lead_id,
            event_type="recovery.retry_failed",
            category="recovery",
            status="failed",
            execution_mode=exec_mode,
            actor=req.actor,
            summary=f"Recovery attempt #{attempt} for '{task['title']}' failed: {summary_msg}",
            details={"task_id": task_id, "attempt": attempt, "next_status": next_status},
            created_at=now,
        )
        return {
            "task_id": task_id,
            "status": next_status,
            "idempotent_noop": False,
            "attempt": attempt,
            "execution_mode": exec_mode,
            "message": summary_msg,
            "lead": get_lead_detail(lead_id, conn=conn),
        }


def escalate_lead_issue(
    lead_id: int, req: EscalationRequest, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Escalate a stalled or failing lead to leadership and optionally reassign the owner."""
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            raise ValueError(f"Lead #{lead_id} not found")

        reassigned_rep_name = None
        if req.reassign_rep_id is not None:
            new_rep = conn.execute(
                "SELECT * FROM reps WHERE id = ? AND active = 1", (req.reassign_rep_id,)
            ).fetchone()
            if not new_rep:
                raise ValueError(f"Active rep #{req.reassign_rep_id} not found")
            reassigned_rep_name = new_rep["name"]
            conn.execute(
                "UPDATE leads SET assigned_rep_id = ? WHERE id = ?",
                (new_rep["id"], lead_id),
            )

        conn.execute(
            """
            UPDATE leads
            SET escalated = 1,
                escalated_to = ?,
                substatus = 'escalated_for_intervention',
                last_activity_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (req.escalate_to, now, now, lead_id),
        )
        conn.execute(
            """
            UPDATE recovery_tasks
            SET status = 'escalated',
                escalated_to = ?,
                resolution_notes = ?,
                updated_at = ?
            WHERE lead_id = ? AND status IN ('open', 'retrying')
            """,
            (req.escalate_to, f"Escalated: {req.reason}", now, lead_id),
        )

        summary = f"Escalated lead to {req.escalate_to}. Reason: {req.reason}"
        if reassigned_rep_name:
            summary += f" Reassigned ownership to {reassigned_rep_name}."

        record_event(
            conn,
            lead_id=lead_id,
            event_type="lead.escalated",
            category="recovery",
            status="warning",
            execution_mode="internal",
            actor=req.actor,
            summary=summary,
            details={
                "escalated_to": req.escalate_to,
                "reason": req.reason,
                "reassigned_rep_id": req.reassign_rep_id,
                "reassigned_rep_name": reassigned_rep_name,
            },
            created_at=now,
        )
        return get_lead_detail(lead_id, conn=conn)
