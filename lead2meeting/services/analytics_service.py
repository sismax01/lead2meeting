"""Analytics and operational reporting service for Lead2Meeting.

Computes real, database-backed metrics for conversion, response times, meeting
bookings, stalled pipeline, and revenue recovery outcomes.
"""

from typing import Any, Dict, List, Optional
from lead2meeting.config import get_settings
from lead2meeting.database import get_connection


def get_dashboard_analytics(db_path: Optional[str] = None) -> Dict[str, Any]:
    settings = get_settings()
    with get_connection(db_path) as conn:
        total_leads = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        qualified_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE qualification_tier != 'disqualified'"
        ).fetchone()[0]
        booked_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE stage IN ('meeting_booked', 'completed')"
        ).fetchone()[0]
        scheduled_or_booked = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE stage IN ('meeting_scheduled', 'meeting_booked', 'completed')"
        ).fetchone()[0]
        stalled_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE is_stalled = 1"
        ).fetchone()[0]
        failed_ops_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE has_failed_operation = 1"
        ).fetchone()[0]
        recovered_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE recovered = 1"
        ).fetchone()[0]
        escalated_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE escalated = 1"
        ).fetchone()[0]
        duplicate_merges = (
            conn.execute("SELECT COALESCE(SUM(duplicate_count), 0) FROM leads").fetchone()[0]
        )

        # Pipeline and revenue metrics
        total_pipeline_value = (
            conn.execute(
                "SELECT COALESCE(SUM(estimated_budget), 0) FROM leads WHERE stage NOT IN ('disqualified', 'lost')"
            ).fetchone()[0]
        )
        booked_pipeline_value = (
            conn.execute(
                "SELECT COALESCE(SUM(estimated_budget), 0) FROM leads WHERE stage IN ('meeting_booked', 'completed')"
            ).fetchone()[0]
        )
        at_risk_pipeline_value = (
            conn.execute(
                "SELECT COALESCE(SUM(estimated_budget), 0) FROM leads WHERE (is_stalled = 1 OR has_failed_operation = 1) AND stage NOT IN ('disqualified', 'lost')"
            ).fetchone()[0]
        )
        recovered_pipeline_value = (
            conn.execute(
                "SELECT COALESCE(SUM(estimated_budget), 0) FROM leads WHERE recovered = 1"
            ).fetchone()[0]
        )

        # Response time statistics (minutes)
        resp_rows = conn.execute(
            "SELECT response_time_minutes FROM leads WHERE response_time_minutes IS NOT NULL ORDER BY response_time_minutes ASC"
        ).fetchall()
        resp_values = [float(r[0]) for r in resp_rows]
        if resp_values:
            avg_resp_min = round(sum(resp_values) / len(resp_values), 1)
            mid = len(resp_values) // 2
            median_resp_min = round(
                resp_values[mid]
                if len(resp_values) % 2 == 1
                else (resp_values[mid - 1] + resp_values[mid]) / 2.0,
                1,
            )
            within_sla_count = sum(
                1 for v in resp_values if v <= settings.sla_new_lead_hours * 60.0
            )
            sla_compliance_pct = round(
                (within_sla_count / len(resp_values)) * 100.0, 1
            )
        else:
            avg_resp_min = 0.0
            median_resp_min = 0.0
            sla_compliance_pct = 100.0

        # Stage funnel breakdown
        stage_counts_raw = conn.execute(
            "SELECT stage, COUNT(*) AS cnt, COALESCE(SUM(estimated_budget), 0) AS val FROM leads GROUP BY stage"
        ).fetchall()
        stage_order = [
            "new",
            "qualified",
            "contacted",
            "meeting_scheduled",
            "meeting_booked",
            "completed",
            "disqualified",
            "lost",
        ]
        stage_map = {r["stage"]: {"count": r["cnt"], "value": r["val"]} for r in stage_counts_raw}
        funnel = [
            {
                "stage": st,
                "count": stage_map.get(st, {}).get("count", 0),
                "pipeline_value": stage_map.get(st, {}).get("value", 0),
            }
            for st in stage_order
        ]

        # Recovery queue breakdown
        open_tasks = conn.execute(
            "SELECT COUNT(*) FROM recovery_tasks WHERE status IN ('open', 'retrying')"
        ).fetchone()[0]
        resolved_tasks = conn.execute(
            "SELECT COUNT(*) FROM recovery_tasks WHERE status = 'resolved'"
        ).fetchone()[0]
        escalated_tasks = conn.execute(
            "SELECT COUNT(*) FROM recovery_tasks WHERE status = 'escalated'"
        ).fetchone()[0]
        total_tasks = open_tasks + resolved_tasks + escalated_tasks
        recovery_success_rate = (
            round((resolved_tasks / total_tasks) * 100.0, 1) if total_tasks > 0 else 0.0
        )

        # Conversion rates
        lead_to_booked_rate = (
            round((booked_leads / total_leads) * 100.0, 1) if total_leads > 0 else 0.0
        )
        qualified_to_booked_rate = (
            round((booked_leads / qualified_leads) * 100.0, 1)
            if qualified_leads > 0
            else 0.0
        )

        # Rep performance breakdown
        rep_stats = [
            dict(r)
            for r in conn.execute(
                """
                SELECT r.id, r.name, r.role, r.specialty, r.capacity, r.active_lead_count, r.calendar_connected,
                       COUNT(l.id) AS total_assigned,
                       SUM(CASE WHEN l.stage IN ('meeting_booked', 'completed') THEN 1 ELSE 0 END) AS booked_count,
                       SUM(CASE WHEN l.is_stalled = 1 THEN 1 ELSE 0 END) AS stalled_count
                FROM reps r
                LEFT JOIN leads l ON l.assigned_rep_id = r.id
                GROUP BY r.id
                ORDER BY r.id ASC
                """
            ).fetchall()
        ]

        # Recent global events stream
        recent_events = []
        for e in conn.execute(
            """
            SELECT e.*, l.company, l.full_name
            FROM lead_events e
            JOIN leads l ON e.lead_id = l.id
            ORDER BY e.id DESC
            LIMIT 25
            """
        ).fetchall():
            ed = dict(e)
            recent_events.append(ed)

        return {
            "environment": {
                "integration_mode": settings.integration_mode,
                "live_crm_configured": settings.is_live_crm_configured,
                "live_calendar_configured": settings.is_live_calendar_configured,
                "live_smtp_configured": settings.is_live_smtp_configured,
                "sla_new_lead_hours": settings.sla_new_lead_hours,
            },
            "summary": {
                "total_leads": total_leads,
                "qualified_leads": qualified_leads,
                "scheduled_or_booked_leads": scheduled_or_booked,
                "booked_meetings": booked_leads,
                "lead_to_booked_rate_pct": lead_to_booked_rate,
                "qualified_to_booked_rate_pct": qualified_to_booked_rate,
                "stalled_leads": stalled_leads,
                "failed_operations_leads": failed_ops_leads,
                "recovered_leads": recovered_leads,
                "escalated_leads": escalated_leads,
                "duplicate_inquiries_merged": duplicate_merges,
            },
            "revenue": {
                "total_active_pipeline_usd": total_pipeline_value,
                "booked_meeting_pipeline_usd": booked_pipeline_value,
                "at_risk_pipeline_usd": at_risk_pipeline_value,
                "recovered_pipeline_usd": recovered_pipeline_value,
            },
            "response_times": {
                "average_minutes": avg_resp_min,
                "median_minutes": median_resp_min,
                "sla_compliance_pct": sla_compliance_pct,
                "sla_target_minutes": round(settings.sla_new_lead_hours * 60.0, 0),
            },
            "recovery_metrics": {
                "open_tasks": open_tasks,
                "resolved_tasks": resolved_tasks,
                "escalated_tasks": escalated_tasks,
                "total_tasks": total_tasks,
                "recovery_rate_pct": recovery_success_rate,
            },
            "funnel": funnel,
            "reps": rep_stats,
            "recent_events": recent_events,
        }
