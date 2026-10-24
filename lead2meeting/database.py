"""SQLite database management, schema migrations, and connection pooling for Lead2Meeting."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional
from lead2meeting.config import get_settings


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    specialty TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    capacity INTEGER NOT NULL DEFAULT 15,
    active_lead_count INTEGER NOT NULL DEFAULT 0,
    calendar_connected INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_ref TEXT,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    normalized_email TEXT NOT NULL,
    company TEXT NOT NULL,
    normalized_company TEXT NOT NULL,
    domain TEXT NOT NULL,
    phone TEXT,
    service_interest TEXT NOT NULL,
    estimated_budget INTEGER NOT NULL DEFAULT 0,
    project_timeline TEXT NOT NULL DEFAULT 'unspecified',
    company_size TEXT NOT NULL DEFAULT '11-50',
    source TEXT NOT NULL DEFAULT 'website_form',
    inquiry_notes TEXT,
    qualification_score INTEGER NOT NULL DEFAULT 0,
    qualification_tier TEXT NOT NULL DEFAULT 'unqualified',
    qualification_breakdown TEXT NOT NULL DEFAULT '{}',
    stage TEXT NOT NULL DEFAULT 'new',
    substatus TEXT NOT NULL DEFAULT 'awaiting_qualification',
    assigned_rep_id INTEGER,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    is_stalled INTEGER NOT NULL DEFAULT 0,
    stall_reason TEXT,
    has_failed_operation INTEGER NOT NULL DEFAULT 0,
    failed_operation_type TEXT,
    last_error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    escalated INTEGER NOT NULL DEFAULT 0,
    escalated_to TEXT,
    recovered INTEGER NOT NULL DEFAULT 0,
    recovered_at TEXT,
    first_response_at TEXT,
    response_time_minutes REAL,
    last_activity_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (assigned_rep_id) REFERENCES reps(id)
);

CREATE INDEX IF NOT EXISTS idx_leads_normalized_email ON leads(normalized_email);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_leads_stalled ON leads(is_stalled);
CREATE INDEX IF NOT EXISTS idx_leads_failed ON leads(has_failed_operation);

CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    rep_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    scheduled_start TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 30,
    meeting_link TEXT,
    provider TEXT NOT NULL DEFAULT 'cal_webhook',
    execution_mode TEXT NOT NULL DEFAULT 'simulated',
    status TEXT NOT NULL DEFAULT 'invite_pending',
    calendar_event_id TEXT,
    sync_status TEXT NOT NULL DEFAULT 'synced',
    sync_error TEXT,
    followup_count INTEGER NOT NULL DEFAULT 0,
    last_followup_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE,
    FOREIGN KEY (rep_id) REFERENCES reps(id)
);

CREATE INDEX IF NOT EXISTS idx_meetings_lead_id ON meetings(lead_id);
CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);

CREATE TABLE IF NOT EXISTS recovery_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'high',
    status TEXT NOT NULL DEFAULT 'open',
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    operation_payload TEXT NOT NULL DEFAULT '{}',
    failure_reason TEXT,
    idempotency_key TEXT UNIQUE,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    last_attempted_at TEXT,
    resolved_at TEXT,
    resolution_notes TEXT,
    escalated_to TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recovery_tasks_lead_id ON recovery_tasks(lead_id);
CREATE INDEX IF NOT EXISTS idx_recovery_tasks_status ON recovery_tasks(status);

CREATE TABLE IF NOT EXISTS lead_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'lifecycle',
    status TEXT NOT NULL DEFAULT 'success',
    execution_mode TEXT NOT NULL DEFAULT 'internal',
    actor TEXT NOT NULL DEFAULT 'system',
    summary TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_lead_events_lead_id ON lead_events(lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_events_created_at ON lead_events(created_at DESC);
"""


def get_db_path(override_path: Optional[str] = None) -> str:
    if override_path:
        return override_path
    return get_settings().database_path


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize SQLite schema and record initial migration."""
    target_path = get_db_path(db_path)
    Path(target_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target_path) as conn:
        conn.executescript(SCHEMA_SQL)
        cur = conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = 1")
        if cur.fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (1, 'initial_core_schema', datetime('now'))"
            )
        conn.commit()


@contextmanager
def get_connection(db_path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    """Provide a transactional SQLite connection with Row factory enabled."""
    target_path = get_db_path(db_path)
    conn = sqlite3.connect(target_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
