"""FastAPI web application and REST API for Lead2Meeting.

# SISMAX — Core API & Revenue Recovery Orchestration Layer
"""

from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from lead2meeting import __version__
from lead2meeting.config import get_settings
from lead2meeting.database import init_db
from lead2meeting.schemas import (
    EscalationRequest,
    FollowUpRequest,
    LeadAssignRequest,
    LeadCreateRequest,
    LeadStageUpdateRequest,
    MeetingScheduleRequest,
    MeetingStatusUpdateRequest,
    RecoveryActionRequest,
)
from lead2meeting.seed import seed_demo_data
from lead2meeting.services.analytics_service import get_dashboard_analytics
from lead2meeting.services.lead_service import (
    assign_lead,
    capture_lead,
    get_lead_detail,
    list_leads,
    list_reps,
    record_followup,
    schedule_meeting,
    update_lead_stage,
    update_meeting_status,
)
from lead2meeting.services.recovery_service import (
    escalate_lead_issue,
    execute_recovery_retry,
    list_recovery_tasks,
    scan_for_stalled_leads,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(db_path: Optional[str] = None, auto_seed: bool = True) -> FastAPI:
    app = FastAPI(
        title="Lead2Meeting",
        description="B2B Lead-to-Meeting Conversion & Revenue Recovery Platform",
        version=__version__,
    )
    app.state.db_path = db_path

    init_db(db_path)
    if auto_seed:
        seed_demo_data(db_path=db_path, force_reset=False)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _db() -> Optional[str]:
        return app.state.db_path

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def serve_ui() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/api/health")
    def health_check() -> dict:
        settings = get_settings()
        return {
            "status": "ok",
            "version": __version__,
            "integration_mode": settings.integration_mode,
            "live_integrations": {
                "crm_webhook": settings.is_live_crm_configured,
                "calendar_webhook": settings.is_live_calendar_configured,
                "smtp_email": settings.is_live_smtp_configured,
            },
        }

    @app.get("/api/analytics")
    def api_get_analytics() -> dict:
        return get_dashboard_analytics(db_path=_db())

    @app.get("/api/reps")
    def api_list_reps() -> dict:
        return {"reps": list_reps(db_path=_db())}

    @app.get("/api/leads")
    def api_list_leads(
        stage: Optional[str] = Query(None),
        stalled_only: bool = Query(False),
        failed_only: bool = Query(False),
        recovered_only: bool = Query(False),
        rep_id: Optional[int] = Query(None),
        search: Optional[str] = Query(None),
    ) -> dict:
        leads = list_leads(
            stage=stage,
            stalled_only=stalled_only,
            failed_only=failed_only,
            recovered_only=recovered_only,
            rep_id=rep_id,
            search=search,
            db_path=_db(),
        )
        return {"count": len(leads), "leads": leads}

    @app.post("/api/leads", status_code=201)
    def api_capture_lead(payload: LeadCreateRequest, request: Request) -> JSONResponse:
        settings = get_settings()
        if settings.ingest_api_key:
            provided_key = request.headers.get("X-Ingest-Key", "")
            if provided_key != settings.ingest_api_key:
                raise HTTPException(status_code=401, detail="Invalid X-Ingest-Key header")

        lead = capture_lead(payload, db_path=_db())
        status_code = 200 if lead.get("deduplicated") else 201
        return JSONResponse(status_code=status_code, content=lead)

    @app.get("/api/leads/{lead_id}")
    def api_get_lead(lead_id: int) -> dict:
        try:
            return get_lead_detail(lead_id, db_path=_db())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.patch("/api/leads/{lead_id}/stage")
    def api_update_stage(lead_id: int, payload: LeadStageUpdateRequest) -> dict:
        try:
            return update_lead_stage(lead_id, payload, db_path=_db())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/leads/{lead_id}/assign")
    def api_assign_lead(lead_id: int, payload: LeadAssignRequest) -> dict:
        try:
            return assign_lead(lead_id, payload, db_path=_db())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/leads/{lead_id}/followups")
    def api_record_followup(lead_id: int, payload: FollowUpRequest) -> dict:
        try:
            return record_followup(lead_id, payload, db_path=_db())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/leads/{lead_id}/meetings", status_code=201)
    def api_schedule_meeting(lead_id: int, payload: MeetingScheduleRequest) -> dict:
        try:
            return schedule_meeting(lead_id, payload, db_path=_db())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/meetings/{meeting_id}/status")
    def api_update_meeting_status(
        meeting_id: int, payload: MeetingStatusUpdateRequest
    ) -> dict:
        try:
            return update_meeting_status(meeting_id, payload, db_path=_db())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/recovery/tasks")
    def api_list_recovery_tasks(status: Optional[str] = Query(None)) -> dict:
        tasks = list_recovery_tasks(status=status, db_path=_db())
        return {"count": len(tasks), "tasks": tasks}

    @app.post("/api/recovery/scan")
    def api_scan_stalled() -> dict:
        return scan_for_stalled_leads(db_path=_db())

    @app.post("/api/recovery/tasks/{task_id}/retry")
    def api_retry_recovery_task(task_id: int, payload: RecoveryActionRequest) -> dict:
        try:
            return execute_recovery_retry(task_id, payload, db_path=_db())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/leads/{lead_id}/escalate")
    def api_escalate_lead(lead_id: int, payload: EscalationRequest) -> dict:
        try:
            return escalate_lead_issue(lead_id, payload, db_path=_db())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/demo/reset")
    def api_reset_demo() -> dict:
        return seed_demo_data(db_path=_db(), force_reset=True)

    return app


app = create_app()
