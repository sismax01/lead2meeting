# Lead2Meeting Architecture & Engineering Guide

## 1. Domain Model & Database Schema

Lead2Meeting uses an embedded relational SQLite database (`lead2meeting/database.py`) with `PRAGMA foreign_keys = ON` and indexed columns for fast filtering.

### Tables
- **`schema_migrations`**: Tracks applied schema versions (`version`, `name`, `applied_at`).
- **`reps`**: Consultation reps and practice leads (`id`, `name`, `email`, `role`, `specialty`, `active`, `capacity`, `active_lead_count`, `calendar_connected`).
- **`leads`**: Central lifecycle record for each prospect (`id`, `external_ref`, `full_name`, `email`, `normalized_email`, `company`, `normalized_company`, `domain`, `service_interest`, `estimated_budget`, `project_timeline`, `company_size`, `qualification_score`, `qualification_tier`, `qualification_breakdown`, `stage`, `substatus`, `assigned_rep_id`, `duplicate_count`, `is_stalled`, `stall_reason`, `has_failed_operation`, `failed_operation_type`, `last_error_message`, `retry_count`, `escalated`, `escalated_to`, `recovered`, `recovered_at`, `first_response_at`, `response_time_minutes`, `last_activity_at`).
- **`meetings`**: Discovery call scheduling records (`id`, `lead_id`, `rep_id`, `title`, `scheduled_start`, `duration_minutes`, `meeting_link`, `provider`, `execution_mode`, `status`, `calendar_event_id`, `sync_status`, `sync_error`, `followup_count`, `last_followup_at`).
- **`recovery_tasks`**: Actionable queue of failed operations and SLA stalls (`id`, `lead_id`, `issue_type`, `severity`, `status`, `title`, `description`, `operation_type`, `operation_payload`, `failure_reason`, `idempotency_key`, `attempt_count`, `max_attempts`, `last_attempted_at`, `resolved_at`, `resolution_notes`, `escalated_to`).
- **`lead_events`**: Immutable audit log of every lifecycle transition, integration call, failure, retry, and recovery (`id`, `lead_id`, `event_type`, `category`, `status`, `execution_mode`, `actor`, `summary`, `details`, `created_at`).

## 2. Lead Lifecycle State Machine

```text
[Inquiry Captured]
       │
       ├──(Duplicate Email / Ref)──► Merged into Existing Lead (duplicate_count++, budget/notes updated)
       │
       ▼
  [Qualification]
       │
       ├──(Score < 35)─────────────► disqualified
       │
       ▼
   qualified ──(Rep Assigned + Outreach Logged)──► contacted
                                                      │
                                                      ▼
                                              meeting_scheduled (invite_pending)
                                                      │
                        ┌─────────────────────────────┴───────────────────────────┐
                        ▼                                                         ▼
                 meeting_booked (confirmed)                                  no_show (stalled)
                        │                                                         │
                        ▼                                                         ▼
                    completed                                            [Recovery Queue]
```

## 3. Failure Handling & Idempotent Recovery Design

- **Idempotency Keys**: Every recovery task has a unique `idempotency_key` (e.g., `cal_fail_meeting_2`, `crm_sync_4`, `stall_3_qualified`). Repeated watchdog scans or duplicate webhook failures update or reuse existing open tasks rather than flooding the operator queue.
- **Safe Retry Execution**: `execute_recovery_retry` checks if `task.status == 'resolved'` first; if so, it returns `idempotent_noop: True` without re-sending messages or duplicating calendar events.
- **Retry Ceiling & Escalation**: Each recovery task enforces `max_attempts` (default `3`). If an operation fails all 3 retry attempts, the task and lead automatically transition to `escalated` (`escalated_to = 'vp_client_partnerships'`).
