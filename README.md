# Lead2Meeting

**Lead-to-Meeting Conversion & Revenue-Recovery Platform for B2B Agencies, Consultancies, and IT Service Firms.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.92%2B-009688.svg)](https://fastapi.tiangolo.com/)

B2B service companies lose high-intent prospects when inbound inquiries sit uncontacted, duplicate submissions fragment context, calendar invites fail to sync, prospects forget to accept calendar holds, or webhook integrations silently error out.

**Lead2Meeting** tracks every B2B prospect from initial inquiry to confirmed discovery meeting, detects stalled leads and broken operations automatically, and gives revenue operations teams safe, idempotent recovery workflows to rescue missed opportunities.

---

## Core Capabilities

1. **Lead Capture, Input Validation & Smart Deduplication**
   - Validates inbound inquiries via strict Pydantic schemas (`email`, `service_interest`, `estimated_budget`, `project_timeline`, `company_size`).
   - Normalizes emails (stripping `+tag` aliases and Gmail dot variations) and merges duplicate submissions into the existing active lead while preserving higher budget signals, appending inquiry notes, and incrementing `duplicate_count`.

2. **Deterministic B2B Qualification & Specialty Assignment**
   - Scores leads from `0` to `100` across five transparent dimensions: **Budget** (up to 35 pts), **Service Fit** (up to 25 pts), **Timeline Urgency** (up to 20 pts), **Company Size** (up to 10 pts), and **Corporate Email Domain** (up to 10 pts).
   - Classifies leads into `high_priority`, `standard`, `nurture`, or `disqualified`.
   - Routes qualified leads to the consultation rep or practice lead whose specialty matches the requested service (`cloud_migration`, `custom_software`, `security_audit`, `ai_consulting`, `devops_retainer`) and who has available capacity. If all reps are at capacity, records an `assignment_failure` recovery task rather than dropping the lead.

3. **Full Lifecycle & Meeting Scheduling Management**
   - Tracks prospects through `new` → `qualified` → `contacted` → `meeting_scheduled` (invite pending) → `meeting_booked` (confirmed on calendar) → `completed` (or `disqualified` / `lost`).
   - Records follow-up outreach across Email, Phone, and LinkedIn, automatically computing first-response latency (`response_time_minutes`) and SLA compliance.
   - Manages discovery meeting provisioning, calendar confirmation, completion, and **no-show detection** (flagging a no-show immediately marks the lead stalled and opens a reschedule recovery task).

4. **SLA Watchdog, Failure Detection, Idempotent Retries & Escalation**
   - **SLA Watchdog (`/api/recovery/scan`)**: Scans active leads against configurable SLA windows (`L2M_SLA_NEW_LEAD_HOURS=4`, `L2M_SLA_INVITE_ACCEPTANCE_HOURS=24`, `L2M_SLA_FOLLOWUP_HOURS=48`) to flag stalled prospects.
   - **Integration Failure Capture**: Captures CRM webhook sync errors (`HTTP 503`), calendar slot provisioning timeouts (`CAL_TIMEOUT_504`), and outreach gateway errors, creating structured `recovery_tasks` with unique idempotency keys.
   - **Safe Idempotent Retries (`/api/recovery/tasks/{id}/retry`)**: Re-executes failed operations safely. Calling retry on an already-resolved task is a guaranteed idempotent no-op.
   - **Automatic & Manual Escalation**: Exceeding `max_attempts` (default `3`) or triggering manual escalation routes the lead to leadership (`vp_client_partnerships`) with optional rep reassignment.

5. **Transparent Execution Modes (`simulated` vs. `live`)**
   - Works out-of-the-box locally in **`simulated` sandbox mode** without paid API keys or external accounts.
   - Every event and meeting explicitly records `execution_mode` (`simulated`, `live`, or `internal`). The application **never** claims an external email was sent, a calendar invite was dispatched, or a CRM was updated unless that transport actually succeeded.
   - Switching `L2M_INTEGRATION_MODE=live` and supplying `L2M_CRM_WEBHOOK_URL`, `L2M_CALENDAR_WEBHOOK_URL`, or `L2M_SMTP_*` environment variables enables real external HTTP/SMTP calls.

6. **Database-Backed Operational Analytics**
   - Real SQLite aggregations for Lead-to-Booked conversion rate, Qualified-to-Booked rate, Median & Average First Response Time, SLA Compliance %, Active/Booked/At-Risk/Recovered Pipeline USD, Rep Capacity utilization, and a complete chronological Event Audit Stream.

---

## Architecture & Tech Stack

- **Backend**: Python 3.10+, [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/), [Uvicorn](https://www.uvicorn.org/), [HTTPX](https://www.python-httpx.org/)
- **Database**: Embedded SQLite (`sqlite3`) with foreign keys, indexed queries, and automatic schema migration tracking (`schema_migrations`)
- **Frontend**: Responsive zero-build Vanilla JS + HTML5 + CSS3 operator console (`/static/index.html`, `/static/app.js`, `/static/styles.css`) served directly by FastAPI
- **Testing**: Standard library `unittest` + FastAPI/Starlette `TestClient` covering 14 end-to-end workflow, failure, recovery, idempotency, and security tests

```text
lead2meeting/
├── lead2meeting/
│   ├── __init__.py
│   ├── __main__.py                 # CLI entrypoint (serve, seed, scan)
│   ├── app.py                      # FastAPI application & REST API routes
│   ├── config.py                   # Environment-driven configuration & SLA thresholds
│   ├── database.py                 # SQLite connection manager & schema migrations
│   ├── schemas.py                  # Pydantic request/response models & validators
│   ├── seed.py                     # Realistic B2B consultancy sample dataset seeder
│   ├── services/
│   │   ├── analytics_service.py    # Real SQL funnel, SLA, and revenue recovery metrics
│   │   ├── integration_service.py  # Simulated sandbox vs. Live CRM/Calendar/SMTP adapters
│   │   ├── lead_service.py         # Capture, deduplication, scoring, routing, scheduling
│   │   └── recovery_service.py     # SLA watchdog scanner, idempotent retries, escalation
│   └── static/
│       ├── index.html              # Operator console UI
│       ├── styles.css              # Clean B2B dashboard styling
│       └── app.js                  # Interactive frontend state & API client
├── tests/
│   └── test_lead2meeting.py        # End-to-end automated test suite
├── docs/
│   └── ARCHITECTURE.md             # Deep dive into data model, state machine & recovery design
├── .env.example                    # Example environment configuration (no secrets)
├── .gitignore
├── CONTRIBUTING.md
├── LICENSE                         # MIT License
├── Makefile                        # Developer convenience targets
├── pyproject.toml
├── requirements.txt
└── README.md
```

---

## Quickstart (Local Setup)

### 1. Clone & Create a Virtual Environment

```bash
git clone https://github.com/sismax01/lead2meeting.git
cd lead2meeting

python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment (Optional)

Lead2Meeting runs out-of-the-box with zero configuration in `simulated` sandbox mode. To customize settings or configure live webhooks/SMTP:

```bash
cp .env.example .env
```

### 3. Seed Sample B2B Dataset & Start the Server

The server automatically initializes the SQLite schema and seeds 7 realistic B2B consultancy leads (including healthy booked meetings, a failed calendar sync, a stalled $95k opportunity, and a recovered $110k AI consulting deal) if the database is empty:

```bash
python3 -m lead2meeting serve --host 127.0.0.1 --port 8000
```

Then open **http://127.0.0.1:8000** in your browser.

---

## CLI Commands

Lead2Meeting includes a built-in CLI via `python3 -m lead2meeting`:

```bash
# Start the web application server
python3 -m lead2meeting serve --host 127.0.0.1 --port 8000

# Reset and re-seed the sample B2B dataset at any time
python3 -m lead2meeting seed --reset

# Run the SLA Watchdog scan from the terminal (useful for cron jobs)
python3 -m lead2meeting scan
```

---

## Try the End-to-End Failure & Recovery Workflow

1. **Inspect Pre-Seeded Failures**:
   - Open the **Recovery & Failed Ops Queue** tab in the UI.
   - Locate **Kestrel FinTech GmbH** (`Calendar invite failed: CAL_TIMEOUT_504`) or **Meridian BioSystems** (`SLA Breach: No initial outreach after 19.5h`).
   - Click **Execute Safe Retry & Recover** to resolve the failure, provision the sandbox calendar link, and move the prospect forward.
2. **Simulate a New Integration Failure & Recover It**:
   - Click **+ Capture New Lead** in the top navigation bar.
   - Fill in a prospect (or use the defaults) and check **"Simulate CRM Webhook HTTP 503 Failure"**.
   - Submit the form — notice that the lead is captured and assigned, an error banner is displayed, and an open task appears in the Recovery Queue.
   - Click **Safe Retry & Recover Now** inside the right-hand Lead Inspector to replay the CRM sync and mark the lead as **Recovered**.
3. **Schedule & Confirm a Discovery Meeting**:
   - Select any lead in the table, go to **Meetings & Calendar Provisioning** in the right-hand inspector, and click **Provision Calendar Invite** (optionally checking *Simulate Calendar API 504 Timeout* to test scheduling failure recovery).
   - Once the invite is in `invite_pending` status, click **Mark Confirmed (Booked)** to transition the lead to `meeting_booked` and watch the **Booked Meeting Pipeline** KPI update in real time.

---

## Running Automated Tests

Run the complete automated test suite using Python's built-in `unittest` runner:

```bash
python3 -m unittest discover -s tests -v
```

 All tests use isolated temporary SQLite databases (`tempfile.TemporaryDirectory`) and never touch your local `lead2meeting.db`.

---

## REST API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Service health check and active integration mode status |
| `GET` | `/api/analytics` | Funnel conversion, response time SLA, rep load, and revenue recovery metrics |
| `GET` | `/api/reps` | List consultation reps, specialties, and active lead capacities |
| `GET` | `/api/leads` | List leads with optional `stage`, `stalled_only`, `failed_only`, `recovered_only`, `rep_id`, `search` filters |
| `POST` | `/api/leads` | Capture, validate, deduplicate, qualify, and assign a lead (`201` new, `200` merged duplicate) |
| `GET` | `/api/leads/{id}` | Full lead detail including meetings, recovery tasks, and chronological audit events |
| `PATCH` | `/api/leads/{id}/stage` | Update lead lifecycle stage (`new`, `qualified`, `contacted`, `meeting_scheduled`, `meeting_booked`, `completed`, `lost`, `disqualified`) |
| `POST` | `/api/leads/{id}/assign` | Assign or reassign a lead to a specific rep or auto-select by specialty |
| `POST` | `/api/leads/{id}/followups` | Log or dispatch follow-up outreach (`email`, `phone`, `linkedin`) and clear stalls |
| `POST` | `/api/leads/{id}/meetings` | Provision a discovery meeting slot and calendar invite |
| `PATCH` | `/api/meetings/{id}/status` | Update meeting status (`invite_pending`, `confirmed`, `rescheduled`, `no_show`, `completed`, `canceled`) |
| `GET` | `/api/recovery/tasks` | List open, retrying, escalated, or resolved recovery tasks |
| `POST` | `/api/recovery/scan` | Run SLA watchdog scan across all active leads to flag newly stalled prospects |
| `POST` | `/api/recovery/tasks/{id}/retry` | Execute a safe, idempotent retry for a recovery task |
| `POST` | `/api/leads/{id}/escalate` | Escalate a stalled or failing lead to leadership with optional rep reassignment |
| `POST` | `/api/demo/reset` | Reset database to the clean initial B2B sample dataset |

---

## License

Released under the [MIT License](LICENSE). Engineered by **SISMAX**.
