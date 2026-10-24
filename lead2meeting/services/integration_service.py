"""External and Simulated Integration Adapter for Lead2Meeting.

Clearly distinguishes between simulated local demo operations and real external
actions (CRM webhooks, Calendar scheduling APIs, and SMTP email delivery).
Never claims an external action succeeded unless the underlying transport
actually succeeded, and tags every operation with its explicit `execution_mode`
(`simulated` vs `live`).
"""

import json
import smtplib
import uuid
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple
import httpx
from lead2meeting.config import get_settings


class IntegrationResult:
    def __init__(
        self,
        success: bool,
        execution_mode: str,
        provider: str,
        operation: str,
        external_id: Optional[str] = None,
        message: str = "",
        error_code: Optional[str] = None,
        payload_summary: Optional[Dict[str, Any]] = None,
    ):
        self.success = success
        self.execution_mode = execution_mode  # "simulated" or "live"
        self.provider = provider
        self.operation = operation
        self.external_id = external_id
        self.message = message
        self.error_code = error_code
        self.payload_summary = payload_summary or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "execution_mode": self.execution_mode,
            "provider": self.provider,
            "operation": self.operation,
            "external_id": self.external_id,
            "message": self.message,
            "error_code": self.error_code,
            "payload_summary": self.payload_summary,
        }


def sync_lead_to_crm(
    lead_data: Dict[str, Any], simulate_failure: bool = False
) -> IntegrationResult:
    """Sync lead record to external CRM webhook (or simulated local sink)."""
    settings = get_settings()

    if simulate_failure:
        return IntegrationResult(
            success=False,
            execution_mode=settings.integration_mode,
            provider="crm_webhook",
            operation="crm_lead_sync",
            error_code="CRM_HTTP_503",
            message=(
                f"[{settings.integration_mode.upper()}] CRM webhook endpoint returned HTTP 503 "
                "Service Unavailable (upstream rate limit / maintenance window)."
            ),
            payload_summary={"lead_id": lead_data.get("id"), "email": lead_data.get("email")},
        )

    if settings.is_live_crm_configured:
        headers = {"Content-Type": "application/json"}
        if settings.crm_api_key:
            headers["Authorization"] = f"Bearer {settings.crm_api_key}"
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(
                    settings.crm_webhook_url,
                    json={"event": "lead.upsert", "lead": lead_data},
                    headers=headers,
                )
                if 200 <= resp.status_code < 300:
                    ext_id = f"crm_live_{lead_data.get('id')}"
                    return IntegrationResult(
                        success=True,
                        execution_mode="live",
                        provider="crm_webhook",
                        operation="crm_lead_sync",
                        external_id=ext_id,
                        message=f"[LIVE] Synced lead to CRM webhook ({resp.status_code} OK).",
                        payload_summary={"status_code": resp.status_code},
                    )
                return IntegrationResult(
                    success=False,
                    execution_mode="live",
                    provider="crm_webhook",
                    operation="crm_lead_sync",
                    error_code=f"CRM_HTTP_{resp.status_code}",
                    message=f"[LIVE] CRM webhook returned HTTP {resp.status_code}.",
                )
        except Exception as exc:
            return IntegrationResult(
                success=False,
                execution_mode="live",
                provider="crm_webhook",
                operation="crm_lead_sync",
                error_code="CRM_NETWORK_ERROR",
                message=f"[LIVE] CRM webhook connection failed: {exc}",
            )

    # Simulated mode (or live mode without URL configured)
    mode_label = "simulated"
    sim_id = f"sim_crm_{uuid.uuid4().hex[:8]}"
    return IntegrationResult(
        success=True,
        execution_mode=mode_label,
        provider="crm_sandbox",
        operation="crm_lead_sync",
        external_id=sim_id,
        message=(
            f"[SIMULATED] Recorded CRM payload locally (ref: {sim_id}). "
            "No external CRM network request was sent."
        ),
        payload_summary={"lead_id": lead_data.get("id"), "sim_ref": sim_id},
    )


def provision_calendar_invite(
    lead_data: Dict[str, Any],
    rep_data: Dict[str, Any],
    meeting_data: Dict[str, Any],
    simulate_failure: bool = False,
) -> IntegrationResult:
    """Create calendar invite via live webhook or simulated calendar sandbox."""
    settings = get_settings()

    if simulate_failure or not rep_data.get("calendar_connected", 1):
        reason = (
            "Rep calendar OAuth token expired (CAL_OAUTH_REVOKED)"
            if not rep_data.get("calendar_connected", 1)
            else "Calendar provider API timed out waiting for slot lock (CAL_TIMEOUT_504)"
        )
        return IntegrationResult(
            success=False,
            execution_mode=settings.integration_mode,
            provider=meeting_data.get("provider", "cal_webhook"),
            operation="calendar_provision",
            error_code="CAL_PROVISION_FAILED",
            message=f"[{settings.integration_mode.upper()}] Calendar invite provisioning failed: {reason}.",
            payload_summary={
                "lead_id": lead_data.get("id"),
                "rep_email": rep_data.get("email"),
                "scheduled_start": meeting_data.get("scheduled_start"),
            },
        )

    if settings.is_live_calendar_configured:
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.post(
                    settings.calendar_webhook_url,
                    json={
                        "event": "meeting.schedule",
                        "lead": lead_data,
                        "rep": rep_data,
                        "meeting": meeting_data,
                    },
                )
                if 200 <= resp.status_code < 300:
                    ext_id = f"cal_live_{uuid.uuid4().hex[:10]}"
                    return IntegrationResult(
                        success=True,
                        execution_mode="live",
                        provider=meeting_data.get("provider", "cal_webhook"),
                        operation="calendar_provision",
                        external_id=ext_id,
                        message=f"[LIVE] Calendar event created via webhook ({resp.status_code} OK).",
                        payload_summary={
                            "meeting_link": f"https://meet.example.com/live-{ext_id}"
                        },
                    )
                return IntegrationResult(
                    success=False,
                    execution_mode="live",
                    provider=meeting_data.get("provider", "cal_webhook"),
                    operation="calendar_provision",
                    error_code=f"CAL_HTTP_{resp.status_code}",
                    message=f"[LIVE] Calendar webhook returned HTTP {resp.status_code}.",
                )
        except Exception as exc:
            return IntegrationResult(
                success=False,
                execution_mode="live",
                provider=meeting_data.get("provider", "cal_webhook"),
                operation="calendar_provision",
                error_code="CAL_NETWORK_ERROR",
                message=f"[LIVE] Calendar webhook request failed: {exc}",
            )

    sim_event_id = f"sim_cal_{uuid.uuid4().hex[:8]}"
    sim_link = f"https://sandbox.lead2meeting.local/meet/{sim_event_id}"
    return IntegrationResult(
        success=True,
        execution_mode="simulated",
        provider=meeting_data.get("provider", "cal_webhook"),
        operation="calendar_provision",
        external_id=sim_event_id,
        message=(
            f"[SIMULATED] Provisioned sandbox calendar slot {sim_event_id}. "
            "No real calendar invite was dispatched to external attendees."
        ),
        payload_summary={"meeting_link": sim_link, "calendar_event_id": sim_event_id},
    )


def dispatch_followup_message(
    lead_data: Dict[str, Any],
    channel: str,
    summary: str,
    simulate_failure: bool = False,
) -> IntegrationResult:
    """Dispatch follow-up email/notification via live SMTP or simulated sandbox."""
    settings = get_settings()

    if simulate_failure:
        return IntegrationResult(
            success=False,
            execution_mode=settings.integration_mode,
            provider=f"{channel}_gateway",
            operation="followup_dispatch",
            error_code="MSG_DELIVERY_TEMP_FAIL",
            message=(
                f"[{settings.integration_mode.upper()}] Follow-up delivery via {channel} "
                "failed with transient gateway error (451 4.4.2 Timeout)."
            ),
            payload_summary={"channel": channel, "recipient": lead_data.get("email")},
        )

    if channel == "email" and settings.is_live_smtp_configured:
        try:
            msg = EmailMessage()
            msg["Subject"] = f"Following up on your inquiry — {lead_data.get('company')}"
            msg["From"] = settings.smtp_from_email
            msg["To"] = lead_data["email"]
            msg.set_content(summary)
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=5) as server:
                server.starttls()
                server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(msg)
            msg_id = f"smtp_live_{uuid.uuid4().hex[:8]}"
            return IntegrationResult(
                success=True,
                execution_mode="live",
                provider="smtp",
                operation="followup_dispatch",
                external_id=msg_id,
                message=f"[LIVE] Sent follow-up email via SMTP to {lead_data['email']}.",
                payload_summary={"channel": channel, "recipient": lead_data["email"]},
            )
        except Exception as exc:
            return IntegrationResult(
                success=False,
                execution_mode="live",
                provider="smtp",
                operation="followup_dispatch",
                error_code="SMTP_SEND_ERROR",
                message=f"[LIVE] SMTP email delivery failed: {exc}",
            )

    sim_msg_id = f"sim_msg_{uuid.uuid4().hex[:8]}"
    return IntegrationResult(
        success=True,
        execution_mode="simulated",
        provider=f"{channel}_sandbox",
        operation="followup_dispatch",
        external_id=sim_msg_id,
        message=(
            f"[SIMULATED] Logged {channel} follow-up in local sandbox (id: {sim_msg_id}). "
            "No external message was transmitted."
        ),
        payload_summary={"channel": channel, "recipient": lead_data.get("email")},
    )
