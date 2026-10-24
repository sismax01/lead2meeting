"""Configuration management for Lead2Meeting.

Loads environment variables safely with sensible local defaults so the platform
works out-of-the-box without requiring paid external credentials, while supporting
real SMTP, CRM Webhooks, and Calendar Webhook integrations when configured.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("L2M_APP_NAME", "Lead2Meeting")
    environment: str = os.getenv("L2M_ENV", "development")
    database_path: str = os.getenv(
        "L2M_DATABASE_PATH", str(BASE_DIR / "lead2meeting.db")
    )
    host: str = os.getenv("L2M_HOST", "127.0.0.1")
    port: int = int(os.getenv("L2M_PORT", "8000"))

    # Integration Mode: "simulated" (safe local sandbox) or "live" (real network calls)
    integration_mode: str = os.getenv("L2M_INTEGRATION_MODE", "simulated").lower()

    # Optional Live External Integration Endpoints
    crm_webhook_url: str = os.getenv("L2M_CRM_WEBHOOK_URL", "")
    crm_api_key: str = os.getenv("L2M_CRM_API_KEY", "")
    calendar_webhook_url: str = os.getenv("L2M_CALENDAR_WEBHOOK_URL", "")
    slack_alert_webhook_url: str = os.getenv("L2M_SLACK_WEBHOOK_URL", "")

    # Optional Live SMTP Settings
    smtp_host: str = os.getenv("L2M_SMTP_HOST", "")
    smtp_port: int = int(os.getenv("L2M_SMTP_PORT", "587"))
    smtp_username: str = os.getenv("L2M_SMTP_USERNAME", "")
    smtp_password: str = os.getenv("L2M_SMTP_PASSWORD", "")
    smtp_from_email: str = os.getenv("L2M_SMTP_FROM_EMAIL", "bookings@example.org")

    # Operational SLA Thresholds (in hours)
    sla_new_lead_hours: float = float(os.getenv("L2M_SLA_NEW_LEAD_HOURS", "4.0"))
    sla_invite_acceptance_hours: float = float(
        os.getenv("L2M_SLA_INVITE_ACCEPTANCE_HOURS", "24.0")
    )
    sla_followup_hours: float = float(os.getenv("L2M_SLA_FOLLOWUP_HOURS", "48.0"))
    max_retry_attempts: int = int(os.getenv("L2M_MAX_RETRY_ATTEMPTS", "3"))

    # Optional API Key for Webhook Ingestion
    ingest_api_key: str = os.getenv("L2M_INGEST_API_KEY", "")

    @property
    def is_live_crm_configured(self) -> bool:
        return self.integration_mode == "live" and bool(self.crm_webhook_url.strip())

    @property
    def is_live_calendar_configured(self) -> bool:
        return self.integration_mode == "live" and bool(
            self.calendar_webhook_url.strip()
        )

    @property
    def is_live_smtp_configured(self) -> bool:
        return self.integration_mode == "live" and bool(
            self.smtp_host.strip() and self.smtp_username.strip()
        )


def get_settings() -> Settings:
    """Return a freshly evaluated Settings instance."""
    return Settings(
        app_name=os.getenv("L2M_APP_NAME", "Lead2Meeting"),
        environment=os.getenv("L2M_ENV", "development"),
        database_path=os.getenv(
            "L2M_DATABASE_PATH", str(BASE_DIR / "lead2meeting.db")
        ),
        host=os.getenv("L2M_HOST", "127.0.0.1"),
        port=int(os.getenv("L2M_PORT", "8000")),
        integration_mode=os.getenv("L2M_INTEGRATION_MODE", "simulated").lower(),
        crm_webhook_url=os.getenv("L2M_CRM_WEBHOOK_URL", ""),
        crm_api_key=os.getenv("L2M_CRM_API_KEY", ""),
        calendar_webhook_url=os.getenv("L2M_CALENDAR_WEBHOOK_URL", ""),
        slack_alert_webhook_url=os.getenv("L2M_SLACK_WEBHOOK_URL", ""),
        smtp_host=os.getenv("L2M_SMTP_HOST", ""),
        smtp_port=int(os.getenv("L2M_SMTP_PORT", "587")),
        smtp_username=os.getenv("L2M_SMTP_USERNAME", ""),
        smtp_password=os.getenv("L2M_SMTP_PASSWORD", ""),
        smtp_from_email=os.getenv("L2M_SMTP_FROM_EMAIL", "bookings@example.org"),
        sla_new_lead_hours=float(os.getenv("L2M_SLA_NEW_LEAD_HOURS", "4.0")),
        sla_invite_acceptance_hours=float(
            os.getenv("L2M_SLA_INVITE_ACCEPTANCE_HOURS", "24.0")
        ),
        sla_followup_hours=float(os.getenv("L2M_SLA_FOLLOWUP_HOURS", "48.0")),
        max_retry_attempts=int(os.getenv("L2M_MAX_RETRY_ATTEMPTS", "3")),
        ingest_api_key=os.getenv("L2M_INGEST_API_KEY", ""),
    )
