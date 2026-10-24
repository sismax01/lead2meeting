"""Core lead lifecycle, deduplication, qualification, assignment, and scheduling logic.

# SISMAX — Deterministic B2B Qualification & Smart Deduplication Engine
"""

from datetime import datetime, timezone
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from lead2meeting.database import get_connection
from lead2meeting.schemas import (
    FollowUpRequest,
    LeadAssignRequest,
    LeadCreateRequest,
    LeadStageUpdateRequest,
    MeetingScheduleRequest,
    MeetingStatusUpdateRequest,
)
from lead2meeting.services.integration_service import (
    dispatch_followup_message,
    provision_calendar_invite,
    sync_lead_to_crm,
)


FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "icloud.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_email(email: str) -> Tuple[str, str]:
    """Normalize email address (strip plus-addressing for standard providers) and extract domain."""
    cleaned = email.strip().lower()
    local, _, domain = cleaned.partition("@")
    if "+" in local:
        local = local.split("+", 1)[0]
    if domain == "gmail.com":
        local = local.replace(".", "")
    return f"{local}@{domain}", domain


def normalize_company(company: str) -> str:
    cleaned = re.sub(
        r"\b(inc|llc|ltd|corp|corporation|gmbh|co|company)\b\.?",
        "",
        company.strip().lower(),
    )
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned).strip()
    return cleaned or company.strip().lower()


def compute_qualification(
    budget: int,
    service_interest: str,
    timeline: str,
    company_size: str,
    domain: str,
) -> Tuple[int, str, Dict[str, Any]]:
    """Deterministic B2B lead qualification engine (0-100 score + tier + breakdown)."""
    breakdown: Dict[str, int] = {}

    # 1. Budget score (up to 35 pts)
    if budget >= 50000:
        breakdown["budget"] = 35
    elif budget >= 25000:
        breakdown["budget"] = 28
    elif budget >= 10000:
        breakdown["budget"] = 20
    elif budget >= 5000:
        breakdown["budget"] = 10
    else:
        breakdown["budget"] = 0

    # 2. Service fit (up to 25 pts)
    high_value_services = {
        "cloud_migration": 25,
        "custom_software": 25,
        "ai_consulting": 22,
        "security_audit": 20,
        "devops_retainer": 20,
        "general_inquiry": 10,
    }
    breakdown["service_fit"] = high_value_services.get(service_interest, 10)

    # 3. Timeline urgency (up to 20 pts)
    timeline_scores = {
        "immediate": 20,
        "1_3_months": 16,
        "3_6_months": 10,
        "exploring": 4,
        "unspecified": 6,
    }
    breakdown["timeline"] = timeline_scores.get(timeline, 6)

    # 4. Company size (up to 10 pts)
    size_scores = {
        "1000+": 10,
        "201-1000": 10,
        "51-200": 8,
        "11-50": 6,
        "1-10": 3,
    }
    breakdown["company_size"] = size_scores.get(company_size, 5)

    # 5. Corporate domain signal (up to 10 pts)
    breakdown["corporate_domain"] = 2 if domain in FREE_EMAIL_DOMAINS else 10

    total = min(100, sum(breakdown.values()))
    if total >= 75:
        tier = "high_priority"
    elif total >= 50:
        tier = "standard"
    elif total >= 35:
        tier = "nurture"
    else:
        tier = "disqualified"

    return total, tier, breakdown


def record_event(
    conn: sqlite3.Connection,
    lead_id: int,
    event_type: str,
    summary: str,
    category: str = "lifecycle",
    status: str = "success",
    execution_mode: str = "internal",
    actor: str = "system",
    details: Optional[Dict[str, Any]] = None,
    created_at: Optional[str] = None,
) -> int:
    ts = created_at or utc_now_iso()
    cur = conn.execute(
        """
        INSERT INTO lead_events (
            lead_id, event_type, category, status, execution_mode, actor, summary, details, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lead_id,
            event_type,
            category,
            status,
            execution_mode,
            actor,
            summary,
            json.dumps(details or {}),
            ts,
        ),
    )
    return int(cur.lastrowid)


def create_recovery_task_internal(
    conn: sqlite3.Connection,
    lead_id: int,
    issue_type: str,
    severity: str,
    title: str,
    description: str,
    operation_type: str,
    operation_payload: Dict[str, Any],
    failure_reason: str,
    idempotency_key: str,
    created_at: Optional[str] = None,
) -> int:
    ts = created_at or utc_now_iso()
    existing = conn.execute(
        "SELECT id FROM recovery_tasks WHERE idempotency_key = ? AND status IN ('open', 'retrying')",
        (idempotency_key,),
    ).fetchone()
    if existing:
        return int(existing["id"])

    cur = conn.execute(
        """
        INSERT OR IGNORE INTO recovery_tasks (
            lead_id, issue_type, severity, status, title, description,
            operation_type, operation_payload, failure_reason, idempotency_key,
            attempt_count, max_attempts, created_at, updated_at
        ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, 0, 3, ?, ?)
        """,
        (
            lead_id,
            issue_type,
            severity,
            title,
            description,
            operation_type,
            json.dumps(operation_payload),
            failure_reason,
            idempotency_key,
            ts,
            ts,
        ),
    )
    if cur.lastrowid:
        return int(cur.lastrowid)
    row = conn.execute(
        "SELECT id FROM recovery_tasks WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    return int(row["id"]) if row else 0


def select_best_rep(
    conn: sqlite3.Connection, service_interest: str
) -> Optional[sqlite3.Row]:
    """Select active rep matching specialty with lowest load, or lowest load overall."""
    specialty_row = conn.execute(
        """
        SELECT * FROM reps
        WHERE active = 1 AND specialty = ? AND active_lead_count < capacity
        ORDER BY active_lead_count ASC, id ASC
        LIMIT 1
        """,
        (service_interest,),
    ).fetchone()
    if specialty_row:
        return specialty_row

    fallback_row = conn.execute(
        """
        SELECT * FROM reps
        WHERE active = 1 AND active_lead_count < capacity
        ORDER BY active_lead_count ASC, id ASC
        LIMIT 1
        """
    ).fetchone()
    return fallback_row


def capture_lead(
    req: LeadCreateRequest, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Capture a lead, deduplicate, qualify, assign a rep, and sync to CRM."""
    norm_email, domain = normalize_email(req.email)
    norm_comp = normalize_company(req.company)
    now = utc_now_iso()

    with get_connection(db_path) as conn:
        # 1. Deduplication check: same normalized email or same external_ref in active pipeline
        existing = conn.execute(
            """
            SELECT * FROM leads
            WHERE (normalized_email = ? OR (external_ref IS NOT NULL AND external_ref = ?))
              AND stage NOT IN ('completed', 'lost', 'disqualified')
            ORDER BY id DESC LIMIT 1
            """,
            (norm_email, req.external_ref),
        ).fetchone()

        if existing:
            lead_id = int(existing["id"])
            new_dup_count = int(existing["duplicate_count"]) + 1
            merged_budget = max(int(existing["estimated_budget"]), req.estimated_budget)
            merged_notes = existing["inquiry_notes"] or ""
            if req.inquiry_notes and req.inquiry_notes not in merged_notes:
                merged_notes = (
                    f"{merged_notes}\n[Duplicate Inquiry {now[:10]}]: {req.inquiry_notes}".strip()
                )

            score, tier, breakdown = compute_qualification(
                merged_budget,
                req.service_interest or existing["service_interest"],
                req.project_timeline,
                req.company_size,
                domain,
            )

            conn.execute(
                """
                UPDATE leads
                SET duplicate_count = ?,
                    estimated_budget = ?,
                    inquiry_notes = ?,
                    qualification_score = ?,
                    qualification_tier = ?,
                    qualification_breakdown = ?,
                    last_activity_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    new_dup_count,
                    merged_budget,
                    merged_notes,
                    score,
                    tier,
                    json.dumps(breakdown),
                    now,
                    now,
                    lead_id,
                ),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type="lead.deduplicated",
                category="deduplication",
                status="success",
                execution_mode="internal",
                actor="system",
                summary=f"Merged duplicate submission (#{new_dup_count + 1}) for {req.email} from {req.source}.",
                details={
                    "submitted_email": req.email,
                    "normalized_email": norm_email,
                    "previous_budget": existing["estimated_budget"],
                    "updated_budget": merged_budget,
                    "updated_score": score,
                },
                created_at=now,
            )
            lead_dict = get_lead_detail(lead_id, db_path=db_path, conn=conn)
            lead_dict["deduplicated"] = True
            return lead_dict

        # 2. New Lead Qualification
        score, tier, breakdown = compute_qualification(
            req.estimated_budget,
            req.service_interest,
            req.project_timeline,
            req.company_size,
            domain,
        )

        if req.auto_qualify:
            if tier == "disqualified":
                stage = "disqualified"
                substatus = "below_qualification_threshold"
            else:
                stage = "qualified"
                substatus = "awaiting_assignment"
        else:
            stage = "new"
            substatus = "awaiting_qualification"

        # 3. Insert Lead
        cur = conn.execute(
            """
            INSERT INTO leads (
                external_ref, full_name, email, normalized_email, company,
                normalized_company, domain, phone, service_interest,
                estimated_budget, project_timeline, company_size, source,
                inquiry_notes, qualification_score, qualification_tier,
                qualification_breakdown, stage, substatus, last_activity_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                req.external_ref,
                req.full_name,
                req.email,
                norm_email,
                req.company,
                norm_comp,
                domain,
                req.phone,
                req.service_interest,
                req.estimated_budget,
                req.project_timeline,
                req.company_size,
                req.source,
                req.inquiry_notes,
                score,
                tier,
                json.dumps(breakdown),
                stage,
                substatus,
                now,
                now,
                now,
            ),
        )
        lead_id = int(cur.lastrowid)

        record_event(
            conn,
            lead_id=lead_id,
            event_type="lead.captured",
            category="lifecycle",
            status="success",
            execution_mode="internal",
            actor="intake_router",
            summary=f"Captured new inquiry from {req.full_name} ({req.company}) via {req.source}.",
            details={
                "service_interest": req.service_interest,
                "estimated_budget": req.estimated_budget,
                "timeline": req.project_timeline,
            },
            created_at=now,
        )

        if req.auto_qualify:
            record_event(
                conn,
                lead_id=lead_id,
                event_type="lead.qualified",
                category="qualification",
                status="success",
                execution_mode="internal",
                actor="qualification_engine",
                summary=f"Scored {score}/100 ({tier.replace('_', ' ')}) based on budget, service fit, and urgency.",
                details={"score": score, "tier": tier, "breakdown": breakdown},
                created_at=now,
            )

        # 4. Auto-Assignment for qualified leads
        if req.auto_assign and stage == "qualified":
            rep = select_best_rep(conn, req.service_interest)
            if rep:
                conn.execute(
                    "UPDATE leads SET assigned_rep_id = ?, substatus = 'assigned_awaiting_outreach', updated_at = ? WHERE id = ?",
                    (rep["id"], now, lead_id),
                )
                conn.execute(
                    "UPDATE reps SET active_lead_count = active_lead_count + 1 WHERE id = ?",
                    (rep["id"],),
                )
                record_event(
                    conn,
                    lead_id=lead_id,
                    event_type="lead.assigned",
                    category="assignment",
                    status="success",
                    execution_mode="internal",
                    actor="assignment_router",
                    summary=f"Assigned to {rep['name']} ({rep['role']}) matching specialty '{rep['specialty']}'.",
                    details={"rep_id": rep["id"], "rep_name": rep["name"], "rep_email": rep["email"]},
                    created_at=now,
                )
            else:
                conn.execute(
                    """
                    UPDATE leads
                    SET has_failed_operation = 1,
                        failed_operation_type = 'rep_assignment',
                        last_error_message = 'No active rep with available capacity found',
                        substatus = 'assignment_failed',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, lead_id),
                )
                record_event(
                    conn,
                    lead_id=lead_id,
                    event_type="lead.assignment_failed",
                    category="assignment",
                    status="failed",
                    execution_mode="internal",
                    actor="assignment_router",
                    summary="Automatic rep assignment failed: all active reps are at maximum capacity.",
                    details={"service_interest": req.service_interest},
                    created_at=now,
                )
                create_recovery_task_internal(
                    conn,
                    lead_id=lead_id,
                    issue_type="assignment_failure",
                    severity="high",
                    title=f"Unassigned qualified lead: {req.company}",
                    description="All reps were at capacity during intake. Rebalance capacity or assign manually.",
                    operation_type="assign_rep",
                    operation_payload={"lead_id": lead_id, "service_interest": req.service_interest},
                    failure_reason="All active reps at capacity",
                    idempotency_key=f"assign_fail_{lead_id}",
                    created_at=now,
                )

        # 5. CRM Sync (Live or Simulated, with failure handling)
        crm_res = sync_lead_to_crm(
            {
                "id": lead_id,
                "full_name": req.full_name,
                "email": req.email,
                "company": req.company,
                "score": score,
                "tier": tier,
            },
            simulate_failure=req.simulate_crm_failure,
        )
        if crm_res.success:
            record_event(
                conn,
                lead_id=lead_id,
                event_type="integration.crm_synced",
                category="integration",
                status="success",
                execution_mode=crm_res.execution_mode,
                actor="crm_connector",
                summary=crm_res.message,
                details=crm_res.to_dict(),
                created_at=now,
            )
        else:
            conn.execute(
                """
                UPDATE leads
                SET has_failed_operation = 1,
                    failed_operation_type = 'crm_lead_sync',
                    last_error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (crm_res.message, now, lead_id),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type="integration.crm_sync_failed",
                category="integration",
                status="failed",
                execution_mode=crm_res.execution_mode,
                actor="crm_connector",
                summary=crm_res.message,
                details=crm_res.to_dict(),
                created_at=now,
            )
            create_recovery_task_internal(
                conn,
                lead_id=lead_id,
                issue_type="integration_failure",
                severity="medium",
                title=f"CRM webhook sync failed for {req.company}",
                description=crm_res.message,
                operation_type="crm_lead_sync",
                operation_payload={
                    "lead_id": lead_id,
                    "full_name": req.full_name,
                    "email": req.email,
                    "company": req.company,
                    "score": score,
                    "tier": tier,
                },
                failure_reason=crm_res.message,
                idempotency_key=f"crm_sync_{lead_id}",
                created_at=now,
            )

        lead_dict = get_lead_detail(lead_id, db_path=db_path, conn=conn)
        lead_dict["deduplicated"] = False
        return lead_dict


def assign_lead(
    lead_id: int, req: LeadAssignRequest, db_path: Optional[str] = None
) -> Dict[str, Any]:
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            raise ValueError(f"Lead #{lead_id} not found")

        if req.rep_id is not None:
            rep = conn.execute(
                "SELECT * FROM reps WHERE id = ? AND active = 1", (req.rep_id,)
            ).fetchone()
            if not rep:
                raise ValueError(f"Active rep #{req.rep_id} not found")
        else:
            rep = select_best_rep(conn, lead["service_interest"])
            if not rep:
                raise ValueError("No active rep with available capacity")

        old_rep_id = lead["assigned_rep_id"]
        if old_rep_id and old_rep_id != rep["id"]:
            conn.execute(
                "UPDATE reps SET active_lead_count = MAX(0, active_lead_count - 1) WHERE id = ?",
                (old_rep_id,),
            )
        if old_rep_id != rep["id"]:
            conn.execute(
                "UPDATE reps SET active_lead_count = active_lead_count + 1 WHERE id = ?",
                (rep["id"],),
            )

        new_stage = "qualified" if lead["stage"] == "new" else lead["stage"]
        conn.execute(
            """
            UPDATE leads
            SET assigned_rep_id = ?,
                stage = ?,
                substatus = 'assigned_awaiting_outreach',
                last_activity_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (rep["id"], new_stage, now, now, lead_id),
        )
        record_event(
            conn,
            lead_id=lead_id,
            event_type="lead.assigned",
            category="assignment",
            status="success",
            execution_mode="internal",
            actor=req.actor,
            summary=f"Assigned lead to {rep['name']} ({rep['email']})."
            + (f" Note: {req.reason}" if req.reason else ""),
            details={"rep_id": rep["id"], "rep_name": rep["name"], "reason": req.reason},
            created_at=now,
        )
        return get_lead_detail(lead_id, db_path=db_path, conn=conn)


def record_followup(
    lead_id: int, req: FollowUpRequest, db_path: Optional[str] = None
) -> Dict[str, Any]:
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            raise ValueError(f"Lead #{lead_id} not found")

        lead_dict = dict(lead)
        res = dispatch_followup_message(
            lead_dict,
            channel=req.channel,
            summary=req.message_summary,
            simulate_failure=req.simulate_delivery_failure,
        )

        if not res.success:
            conn.execute(
                """
                UPDATE leads
                SET has_failed_operation = 1,
                    failed_operation_type = 'followup_dispatch',
                    last_error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (res.message, now, lead_id),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type="followup.failed",
                category="outreach",
                status="failed",
                execution_mode=res.execution_mode,
                actor=req.actor,
                summary=res.message,
                details={**res.to_dict(), "message_summary": req.message_summary},
                created_at=now,
            )
            create_recovery_task_internal(
                conn,
                lead_id=lead_id,
                issue_type="outreach_delivery_failure",
                severity="high",
                title=f"Follow-up {req.channel} delivery failed for {lead['company']}",
                description=res.message,
                operation_type="followup_dispatch",
                operation_payload={
                    "lead_id": lead_id,
                    "channel": req.channel,
                    "message_summary": req.message_summary,
                    "actor": req.actor,
                },
                failure_reason=res.message,
                idempotency_key=f"followup_fail_{lead_id}_{now[:16]}",
                created_at=now,
            )
            return get_lead_detail(lead_id, db_path=db_path, conn=conn)

        # Calculate first response time if not yet set
        first_resp = lead["first_response_at"]
        resp_minutes = lead["response_time_minutes"]
        if not first_resp:
            first_resp = now
            try:
                created_dt = datetime.fromisoformat(lead["created_at"])
                now_dt = datetime.fromisoformat(now)
                resp_minutes = round((now_dt - created_dt).total_seconds() / 60.0, 1)
            except Exception:
                resp_minutes = 5.0

        next_stage = (
            "contacted"
            if lead["stage"] in ("new", "qualified")
            else lead["stage"]
        )
        was_stalled = bool(lead["is_stalled"])
        recovered_flag = 1 if was_stalled or lead["recovered"] else 0
        recovered_at = now if was_stalled and not lead["recovered_at"] else lead["recovered_at"]

        conn.execute(
            """
            UPDATE leads
            SET stage = ?,
                substatus = 'followup_logged',
                first_response_at = ?,
                response_time_minutes = ?,
                is_stalled = 0,
                stall_reason = NULL,
                recovered = ?,
                recovered_at = ?,
                last_activity_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                next_stage,
                first_resp,
                resp_minutes,
                recovered_flag,
                recovered_at,
                now,
                now,
                lead_id,
            ),
        )

        # Resolve any open stall recovery tasks for this lead
        if was_stalled:
            conn.execute(
                """
                UPDATE recovery_tasks
                SET status = 'resolved',
                    resolved_at = ?,
                    resolution_notes = 'Resolved via follow-up outreach',
                    updated_at = ?
                WHERE lead_id = ? AND issue_type = 'stalled_lead' AND status IN ('open', 'retrying', 'escalated')
                """,
                (now, now, lead_id),
            )

        # Increment meeting followup_count if there is an active meeting
        conn.execute(
            """
            UPDATE meetings
            SET followup_count = followup_count + 1,
                last_followup_at = ?,
                updated_at = ?
            WHERE lead_id = ? AND status IN ('invite_pending', 'rescheduled', 'no_show')
            """,
            (now, now, lead_id),
        )

        record_event(
            conn,
            lead_id=lead_id,
            event_type="followup.sent",
            category="outreach",
            status="success",
            execution_mode=res.execution_mode,
            actor=req.actor,
            summary=f"{res.message} Summary: {req.message_summary}",
            details={**res.to_dict(), "message_summary": req.message_summary},
            created_at=now,
        )
        return get_lead_detail(lead_id, db_path=db_path, conn=conn)


def schedule_meeting(
    lead_id: int, req: MeetingScheduleRequest, db_path: Optional[str] = None
) -> Dict[str, Any]:
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            raise ValueError(f"Lead #{lead_id} not found")

        rep_id = lead["assigned_rep_id"]
        if not rep_id:
            rep = select_best_rep(conn, lead["service_interest"])
            if not rep:
                raise ValueError("Cannot schedule meeting: lead has no assigned rep and no rep is available")
            rep_id = rep["id"]
            conn.execute(
                "UPDATE leads SET assigned_rep_id = ? WHERE id = ?", (rep_id, lead_id)
            )
        else:
            rep = conn.execute("SELECT * FROM reps WHERE id = ?", (rep_id,)).fetchone()

        cal_res = provision_calendar_invite(
            dict(lead),
            dict(rep),
            {
                "title": req.title,
                "scheduled_start": req.scheduled_start,
                "duration_minutes": req.duration_minutes,
                "provider": req.provider,
            },
            simulate_failure=req.simulate_calendar_failure,
        )

        if not cal_res.success:
            cur = conn.execute(
                """
                INSERT INTO meetings (
                    lead_id, rep_id, title, scheduled_start, duration_minutes,
                    meeting_link, provider, execution_mode, status,
                    calendar_event_id, sync_status, sync_error, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 'sync_failed', NULL, 'failed', ?, ?, ?, ?)
                """,
                (
                    lead_id,
                    rep_id,
                    req.title,
                    req.scheduled_start,
                    req.duration_minutes,
                    req.provider,
                    cal_res.execution_mode,
                    cal_res.message,
                    req.notes,
                    now,
                    now,
                ),
            )
            meeting_id = int(cur.lastrowid)
            conn.execute(
                """
                UPDATE leads
                SET has_failed_operation = 1,
                    failed_operation_type = 'calendar_provision',
                    last_error_message = ?,
                    substatus = 'calendar_sync_failed',
                    last_activity_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (cal_res.message, now, now, lead_id),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type="meeting.schedule_failed",
                category="scheduling",
                status="failed",
                execution_mode=cal_res.execution_mode,
                actor=req.actor,
                summary=cal_res.message,
                details={**cal_res.to_dict(), "meeting_id": meeting_id},
                created_at=now,
            )
            create_recovery_task_internal(
                conn,
                lead_id=lead_id,
                issue_type="calendar_sync_failure",
                severity="critical",
                title=f"Calendar invite failed for {lead['company']} ({req.title})",
                description=cal_res.message,
                operation_type="calendar_provision",
                operation_payload={
                    "lead_id": lead_id,
                    "meeting_id": meeting_id,
                    "rep_id": rep_id,
                    "title": req.title,
                    "scheduled_start": req.scheduled_start,
                    "duration_minutes": req.duration_minutes,
                    "provider": req.provider,
                },
                failure_reason=cal_res.message,
                idempotency_key=f"cal_fail_meeting_{meeting_id}",
                created_at=now,
            )
            return get_lead_detail(lead_id, db_path=db_path, conn=conn)

        # Success provisioning calendar invite
        meeting_link = cal_res.payload_summary.get("meeting_link")
        cal_event_id = cal_res.external_id
        cur = conn.execute(
            """
            INSERT INTO meetings (
                lead_id, rep_id, title, scheduled_start, duration_minutes,
                meeting_link, provider, execution_mode, status,
                calendar_event_id, sync_status, sync_error, notes,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'invite_pending', ?, 'synced', NULL, ?, ?, ?)
            """,
            (
                lead_id,
                rep_id,
                req.title,
                req.scheduled_start,
                req.duration_minutes,
                meeting_link,
                req.provider,
                cal_res.execution_mode,
                cal_event_id,
                req.notes,
                now,
                now,
            ),
        )
        meeting_id = int(cur.lastrowid)

        first_resp = lead["first_response_at"] or now
        resp_minutes = lead["response_time_minutes"]
        if resp_minutes is None:
            try:
                created_dt = datetime.fromisoformat(lead["created_at"])
                now_dt = datetime.fromisoformat(now)
                resp_minutes = round((now_dt - created_dt).total_seconds() / 60.0, 1)
            except Exception:
                resp_minutes = 10.0

        conn.execute(
            """
            UPDATE leads
            SET stage = 'meeting_scheduled',
                substatus = 'invite_pending_confirmation',
                first_response_at = ?,
                response_time_minutes = ?,
                is_stalled = 0,
                stall_reason = NULL,
                last_activity_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (first_resp, resp_minutes, now, now, lead_id),
        )
        record_event(
            conn,
            lead_id=lead_id,
            event_type="meeting.scheduled",
            category="scheduling",
            status="success",
            execution_mode=cal_res.execution_mode,
            actor=req.actor,
            summary=f"{cal_res.message} Scheduled '{req.title}' for {req.scheduled_start}.",
            details={
                **cal_res.to_dict(),
                "meeting_id": meeting_id,
                "scheduled_start": req.scheduled_start,
            },
            created_at=now,
        )
        return get_lead_detail(lead_id, db_path=db_path, conn=conn)


def update_meeting_status(
    meeting_id: int, req: MeetingStatusUpdateRequest, db_path: Optional[str] = None
) -> Dict[str, Any]:
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        meeting = conn.execute(
            "SELECT * FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
        if not meeting:
            raise ValueError(f"Meeting #{meeting_id} not found")

        lead_id = int(meeting["lead_id"])
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()

        conn.execute(
            "UPDATE meetings SET status = ?, notes = COALESCE(?, notes), updated_at = ? WHERE id = ?",
            (req.status, req.notes, now, meeting_id),
        )

        # Synchronize lead lifecycle stage based on meeting status
        if req.status == "confirmed":
            was_problem = bool(lead["is_stalled"] or lead["has_failed_operation"] or lead["retry_count"] > 0)
            conn.execute(
                """
                UPDATE leads
                SET stage = 'meeting_booked',
                    substatus = 'confirmed_on_calendar',
                    is_stalled = 0,
                    stall_reason = NULL,
                    recovered = CASE WHEN ? THEN 1 ELSE recovered END,
                    recovered_at = CASE WHEN ? AND recovered_at IS NULL THEN ? ELSE recovered_at END,
                    last_activity_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (was_problem, was_problem, now, now, now, lead_id),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type="meeting.confirmed",
                category="scheduling",
                status="success",
                execution_mode=meeting["execution_mode"],
                actor=req.actor,
                summary=f"Prospect confirmed meeting '{meeting['title']}' ({meeting['scheduled_start']}). Lead converted to Booked Meeting.",
                details={"meeting_id": meeting_id, "status": req.status, "notes": req.notes},
                created_at=now,
            )
        elif req.status == "completed":
            conn.execute(
                """
                UPDATE leads
                SET stage = 'completed',
                    substatus = 'meeting_held',
                    is_stalled = 0,
                    last_activity_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, lead_id),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type="meeting.completed",
                category="scheduling",
                status="success",
                execution_mode="internal",
                actor=req.actor,
                summary=f"Discovery meeting '{meeting['title']}' completed.",
                details={"meeting_id": meeting_id, "notes": req.notes},
                created_at=now,
            )
        elif req.status == "no_show":
            conn.execute(
                """
                UPDATE leads
                SET is_stalled = 1,
                    stall_reason = 'Prospect missed scheduled meeting (no-show)',
                    substatus = 'meeting_no_show',
                    last_activity_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, lead_id),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type="meeting.no_show",
                category="scheduling",
                status="warning",
                execution_mode="internal",
                actor=req.actor,
                summary=f"Prospect did not attend '{meeting['title']}'. Triggered no-show recovery workflow.",
                details={"meeting_id": meeting_id, "notes": req.notes},
                created_at=now,
            )
            create_recovery_task_internal(
                conn,
                lead_id=lead_id,
                issue_type="meeting_no_show",
                severity="high",
                title=f"Meeting No-Show Recovery: {lead['company']}",
                description=f"Prospect missed '{meeting['title']}'. Send reschedule link or reassign.",
                operation_type="reschedule_outreach",
                operation_payload={"lead_id": lead_id, "meeting_id": meeting_id},
                failure_reason="Prospect no-show at scheduled time",
                idempotency_key=f"noshow_{meeting_id}",
                created_at=now,
            )
        else:
            conn.execute(
                "UPDATE leads SET substatus = ?, last_activity_at = ?, updated_at = ? WHERE id = ?",
                (f"meeting_{req.status}", now, now, lead_id),
            )
            record_event(
                conn,
                lead_id=lead_id,
                event_type=f"meeting.{req.status}",
                category="scheduling",
                status="success",
                execution_mode="internal",
                actor=req.actor,
                summary=f"Meeting '{meeting['title']}' status updated to {req.status}.",
                details={"meeting_id": meeting_id, "notes": req.notes},
                created_at=now,
            )

        return get_lead_detail(lead_id, db_path=db_path, conn=conn)


def update_lead_stage(
    lead_id: int, req: LeadStageUpdateRequest, db_path: Optional[str] = None
) -> Dict[str, Any]:
    now = utc_now_iso()
    with get_connection(db_path) as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            raise ValueError(f"Lead #{lead_id} not found")

        old_stage = lead["stage"]
        substatus = req.substatus or f"manual_{req.stage}"
        clear_stall = 1 if req.stage in ("meeting_booked", "completed", "lost", "disqualified") else 0

        conn.execute(
            """
            UPDATE leads
            SET stage = ?,
                substatus = ?,
                is_stalled = CASE WHEN ? THEN 0 ELSE is_stalled END,
                stall_reason = CASE WHEN ? THEN NULL ELSE stall_reason END,
                last_activity_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (req.stage, substatus, clear_stall, clear_stall, now, now, lead_id),
        )
        record_event(
            conn,
            lead_id=lead_id,
            event_type="lead.stage_changed",
            category="lifecycle",
            status="success",
            execution_mode="internal",
            actor=req.actor,
            summary=f"Stage changed from '{old_stage}' to '{req.stage}'."
            + (f" Notes: {req.notes}" if req.notes else ""),
            details={"old_stage": old_stage, "new_stage": req.stage, "notes": req.notes},
            created_at=now,
        )
        return get_lead_detail(lead_id, db_path=db_path, conn=conn)


def list_leads(
    stage: Optional[str] = None,
    stalled_only: bool = False,
    failed_only: bool = False,
    recovered_only: bool = False,
    rep_id: Optional[int] = None,
    search: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    query = """
        SELECT l.*, r.name AS rep_name, r.email AS rep_email, r.role AS rep_role
        FROM leads l
        LEFT JOIN reps r ON l.assigned_rep_id = r.id
        WHERE 1=1
    """
    params: List[Any] = []
    if stage:
        query += " AND l.stage = ?"
        params.append(stage)
    if stalled_only:
        query += " AND l.is_stalled = 1"
    if failed_only:
        query += " AND l.has_failed_operation = 1"
    if recovered_only:
        query += " AND l.recovered = 1"
    if rep_id is not None:
        query += " AND l.assigned_rep_id = ?"
        params.append(rep_id)
    if search:
        q = f"%{search.strip().lower()}%"
        query += " AND (LOWER(l.full_name) LIKE ? OR LOWER(l.company) LIKE ? OR LOWER(l.email) LIKE ?)"
        params.extend([q, q, q])

    query += " ORDER BY l.has_failed_operation DESC, l.is_stalled DESC, l.qualification_score DESC, l.id DESC"

    with get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["qualification_breakdown"] = json.loads(
                item.get("qualification_breakdown") or "{}"
            )
            results.append(item)
        return results


def get_lead_detail(
    lead_id: int,
    db_path: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    def _fetch(c: sqlite3.Connection) -> Dict[str, Any]:
        row = c.execute(
            """
            SELECT l.*, r.name AS rep_name, r.email AS rep_email, r.role AS rep_role, r.specialty AS rep_specialty
            FROM leads l
            LEFT JOIN reps r ON l.assigned_rep_id = r.id
            WHERE l.id = ?
            """,
            (lead_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Lead #{lead_id} not found")

        lead_dict = dict(row)
        lead_dict["qualification_breakdown"] = json.loads(
            lead_dict.get("qualification_breakdown") or "{}"
        )

        meetings = [
            dict(m)
            for m in c.execute(
                "SELECT * FROM meetings WHERE lead_id = ? ORDER BY id DESC", (lead_id,)
            ).fetchall()
        ]
        tasks = [
            dict(t)
            for t in c.execute(
                "SELECT * FROM recovery_tasks WHERE lead_id = ? ORDER BY id DESC",
                (lead_id,),
            ).fetchall()
        ]
        events = []
        for e in c.execute(
            "SELECT * FROM lead_events WHERE lead_id = ? ORDER BY id DESC", (lead_id,)
        ).fetchall():
            ed = dict(e)
            ed["details"] = json.loads(ed.get("details") or "{}")
            events.append(ed)

        lead_dict["meetings"] = meetings
        lead_dict["recovery_tasks"] = tasks
        lead_dict["events"] = events
        return lead_dict

    if conn is not None:
        return _fetch(conn)
    with get_connection(db_path) as c:
        return _fetch(c)


def list_reps(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute("SELECT * FROM reps ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
