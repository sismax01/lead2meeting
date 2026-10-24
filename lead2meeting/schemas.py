"""Pydantic request and response models for Lead2Meeting."""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, validator


EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

VALID_SERVICES = {
    "cloud_migration",
    "custom_software",
    "security_audit",
    "ai_consulting",
    "devops_retainer",
    "general_inquiry",
}

VALID_TIMELINES = {
    "immediate",
    "1_3_months",
    "3_6_months",
    "exploring",
    "unspecified",
}

VALID_COMPANY_SIZES = {
    "1-10",
    "11-50",
    "51-200",
    "201-1000",
    "1000+",
}

VALID_STAGES = {
    "new",
    "qualified",
    "contacted",
    "meeting_scheduled",
    "meeting_booked",
    "completed",
    "disqualified",
    "lost",
}


class LeadCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: str = Field(..., min_length=5, max_length=180)
    company: str = Field(..., min_length=2, max_length=150)
    phone: Optional[str] = Field(None, max_length=40)
    service_interest: str = Field("custom_software")
    estimated_budget: int = Field(0, ge=0, le=100_000_000)
    project_timeline: str = Field("1_3_months")
    company_size: str = Field("11-50")
    source: str = Field("website_form", max_length=80)
    inquiry_notes: Optional[str] = Field(None, max_length=2000)
    external_ref: Optional[str] = Field(None, max_length=120)
    auto_qualify: bool = Field(True)
    auto_assign: bool = Field(True)
    simulate_crm_failure: bool = Field(
        False,
        description="Optional flag for testing/demoing CRM webhook failure & recovery",
    )

    @validator("full_name", "company")
    def strip_and_validate_nonempty(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Field must contain at least 2 non-whitespace characters")
        return cleaned

    @validator("email")
    def validate_email_format(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError("Invalid email address format")
        return cleaned

    @validator("service_interest")
    def validate_service_interest(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_SERVICES:
            raise ValueError(
                f"Invalid service_interest '{v}'. Allowed: {sorted(VALID_SERVICES)}"
            )
        return cleaned

    @validator("project_timeline")
    def validate_timeline(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_TIMELINES:
            raise ValueError(
                f"Invalid project_timeline '{v}'. Allowed: {sorted(VALID_TIMELINES)}"
            )
        return cleaned

    @validator("company_size")
    def validate_company_size(cls, v: str) -> str:
        cleaned = v.strip()
        if cleaned not in VALID_COMPANY_SIZES:
            raise ValueError(
                f"Invalid company_size '{v}'. Allowed: {sorted(VALID_COMPANY_SIZES)}"
            )
        return cleaned


class LeadStageUpdateRequest(BaseModel):
    stage: str
    substatus: Optional[str] = None
    actor: str = Field("rep", max_length=80)
    notes: Optional[str] = Field(None, max_length=1000)

    @validator("stage")
    def validate_stage(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_STAGES:
            raise ValueError(f"Invalid stage '{v}'. Allowed: {sorted(VALID_STAGES)}")
        return cleaned


class LeadAssignRequest(BaseModel):
    rep_id: Optional[int] = None
    actor: str = Field("ops_manager", max_length=80)
    reason: Optional[str] = Field(None, max_length=500)


class FollowUpRequest(BaseModel):
    channel: str = Field("email", regex="^(email|phone|linkedin)$")
    message_summary: str = Field(..., min_length=5, max_length=1000)
    actor: str = Field("assigned_rep", max_length=80)
    simulate_delivery_failure: bool = Field(False)


class MeetingScheduleRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    scheduled_start: str = Field(
        ..., description="ISO-8601 timestamp for meeting start"
    )
    duration_minutes: int = Field(30, ge=15, le=180)
    provider: str = Field("cal_webhook", regex="^(cal_webhook|google_meet|zoom)$")
    notes: Optional[str] = Field(None, max_length=1000)
    actor: str = Field("assigned_rep", max_length=80)
    simulate_calendar_failure: bool = Field(
        False,
        description="When true, simulates a calendar API timeout/sync failure for testing recovery",
    )


class MeetingStatusUpdateRequest(BaseModel):
    status: str = Field(
        ...,
        regex="^(invite_pending|confirmed|rescheduled|no_show|completed|canceled)$",
    )
    actor: str = Field("assigned_rep", max_length=80)
    notes: Optional[str] = Field(None, max_length=1000)


class RecoveryActionRequest(BaseModel):
    actor: str = Field("recovery_operator", max_length=80)
    override_notes: Optional[str] = Field(None, max_length=1000)
    force_succeed_simulation: bool = Field(
        True,
        description="In simulated mode, resolves transient failure on retry unless forced to fail again",
    )


class EscalationRequest(BaseModel):
    escalate_to: str = Field("vp_client_partnerships", min_length=2, max_length=120)
    reason: str = Field(..., min_length=5, max_length=1000)
    actor: str = Field("recovery_operator", max_length=80)
    reassign_rep_id: Optional[int] = None
