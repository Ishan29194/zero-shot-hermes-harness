# Data Model

> **Assumed conventions:** All data flows through PostgreSQL (audit/session store) + SQLite (per-session CSV cache) + Phase 2 PostgreSQL MSSQL mirror. See `harness/patterns/project-layout.md` rule 2 (no repository pattern — direct SQLAlchemy queries).

---

## Storage Technology

| Store | Purpose | Driver | Location |
|-------|---------|--------|----------|
| **PostgreSQL 16** | Audit/session DB: run history, token budgets, MSSQL mirror schema, acceleration MVs (Phase 2) | `psycopg2-binary` 2.9+ | `AGENT_DATABASE_URL` (env var; on-premises localhost in Phase 1) |
| **SQLite 3** | Per-session CSV cache: created fresh per upload, destroyed on session expiry (file deletion or TTL) | stdlib `sqlite3` via SQLAlchemy | `data/sessions/{run_id}.db` (app data dir; gitignored) |
| **MSSQL (Phase 2)** | Source of truth — read-only; data is replicated (not queried directly) into PostgreSQL mirror | `pyodbc` 5.x via SQLAlchemy | `AGENT_MSSQL_CONNECTION_STRING` (env var; only used by ETL ingest job) |

> **Assumed:** The PostgreSQL server is managed by the operator; the agent connects via `AGENT_DATABASE_URL` (e.g., `postgresql://analyst:password@localhost:5432/up_police_analyst`). Running `uv run alembic upgrade head` initialises the schema. The SQLite data directory (`data/sessions/`) exists at startup (created by `init_db()` if missing).

---

## Entities

### Entity: `runs` (PostgreSQL audit/session DB)

One row per user question. The primary audit record — never deleted; rows are append-only.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `TEXT` (UUID) | Yes | Primary key — same `run_id` used in graph state and SQLite filename |
| `status` | `TEXT` | Yes | `"pending"` → `"running"` → `"completed"` or `"failed"` |
| `question` | `TEXT` | Yes | The user's plain-language question |
| `instruction` | `TEXT` | Yes | System-level prompt template name (`transform` → `analyze` in Phase 1) |
| `csv_filename` | `TEXT` \| `NULL` | No | Original filename of the uploaded CSV (Phase 1); set at ingest |
| `datasource` | `TEXT` | Yes | `"sqlite"` (Phase 1 CSV) or `"mssql_mirror"` (Phase 2 live) |
| `sql_text` | `TEXT` \| `NULL` | No | The SQL executed (SELECT only); NULL if run failed before SQL generation |
| `sql_attempts` | `INTEGER` | Yes | Number of SQL generation attempts (0 if failed before SQL) |
| `row_count` | `INTEGER` \| `NULL` | No | Number of rows returned by the query |
| `answer_text` | `TEXT` \| `NULL` | No | The formatted plain-text answer; NULL if failed |
| `provider` | `TEXT` \| `NULL` | No | Always `"ollama"` in this deployment |
| `model` | `TEXT` \| `NULL` | No | Ollama model name (e.g., `llama3.1:8b-instruct-q4_0`) |
| `input_tokens` | `INTEGER` \| `NULL` | No | Cumulative input tokens across all LLM calls in this run |
| `output_tokens` | `INTEGER` \| `NULL` | No | Cumulative output tokens |
| `duration_ms` | `INTEGER` \| `NULL` | No | Wall-clock ms from graph entry to finalize |
| `error_message` | `TEXT` \| `NULL` | No | Set only when `status="failed"`; contains user-visible error |
| `created_at` | `TIMESTAMPTZ` | Yes | Run creation time |
| `updated_at` | `TIMESTAMPTZ` | Yes | Last time the row was updated (auto-updated by SQLAlchemy `onupdate`) |
| `completed_at` | `TIMESTAMPTZ` \| `NULL` | No | Set by `finalize_node` or `handle_error_node` |

**Migrations:** Baseline table created by `create_db_session() / init_db()` in Phase 0. No schema changes in Phase 1 (the `runs` table already has all Phase 1 fields). Phase 2 adds `run_sessions` and MSSQL mirror tables via Alembic revisions.

---

### Entity: `run_sessions` (PostgreSQL, Phase 2)

Groups related runs into a conversation session. Added in Phase 2.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | `TEXT` (UUID) | Yes | Primary key |
| `user_label` | `TEXT` \| `NULL` | No | Operator-assigned label (e.g., "FIR analysis 2024-07") |
| `datasource` | `TEXT` | Yes | `"sqlite"` or `"mssql_mirror"` |
| `created_at` | `TIMESTAMPTZ` | Yes | Session creation time |
| `expires_at` | `TIMESTAMPTZ` \| `NULL` | No | Session expiry (SQLite cache is deleted at/after this) |

---

### Entity: `mv_monthly_incidents` (PostgreSQL, Phase 2, materialised view)

One of the acceleration MVs pre-computed from the MSSQL mirror. Refreshed on the ETL schedule (e.g., hourly).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `year` | `INTEGER` | Yes | Calendar year |
| `month` | `INTEGER` | Yes | Calendar month (1–12) |
| `incident_type` | `TEXT` | Yes | Category (e.g., "Theft", "Assault") |
| `count` | `INTEGER` | Yes | Number of incidents |
| `police_station` | `TEXT` | Yes | Station name |

> **Assumed:** Phase 2 adds additional MVs as required by the MSSQL mirror schema; the query planner matches user questions to MV definitions by intent, not just table name.

---

### Entity: SQLite `data` table (per-session, Phase 1)

Created fresh by `ingest_csv_node` on each upload. Schema is inferred from the CSV header + first 100 rows.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| *(all columns)* | `TEXT` \| `INTEGER` \| `REAL` | Varies | Exactly matches CSV columns, all nullable; pandas type inference + explicit TEXT fallback for unparseable columns |

The table name is always `data`. The schema text stored in `runs.schema_text` (and shown in the UI) lists each column and its inferred type.

---

## Relationships

```
runs (PostgreSQL)                    data (SQLite, per-session)
┌─────────────────────┐              ┌─────────────────────────┐
│ id (UUID)           │───► run_id   │ *(no FK — file path is   │
│ status              │    (TEXT)    │   data/sessions/{run_id} │
│ question            │              │   — no inodes in DB)     │
│ sql_text            │              └─────────────────────────┘
│ row_count           │
│ answer_text         │
│ error_message       │
│ created_at          │
└─────────────────────┘
        │ 1:N (one session has many runs)
        ▼
run_sessions (PostgreSQL, Phase 2)
┌─────────────────────────────┐
│ id (UUID)                   │
│ user_label                  │
│ datasource                  │
│ created_at                  │
│ expires_at                  │
└─────────────────────────────┘
        │ 1:N
        ▼
runs (via session_id FK, Phase 2)

mssql_mirror schema (PostgreSQL, Phase 2)
┌──────────────────────────────────────┐
│ replicated tables from MSSQL         │
│ (e.g., incidents, fir_registrations) │
│   — queried directly or via MVs      │
└──────────────────────────────────────┘
        │
        ▼
mv_* (materialised views, Phase 2)
```

---

## Data Lifecycle

**Run creation:** `POST /runs` → `runner.py` creates a `RunRow` with `status="pending"`, flushes to get the `run_id`, then invokes the graph. The run is the atomic unit of work.

**Run progression:** `pending` → `running` (set in `runner.py` before graph `invoke()`) → `completed` or `failed` (set in `finalize_node` / `handle_error_node`).

**CSV cache lifecycle (Phase 1):**
- Created: `ingest_csv_node` writes `data/sessions/{run_id}.db` (only if `csv_bytes` is provided).
- Accessed: `execute_query_node` opens the SQLite file during the run.
- Destroyed: There is no automatic TTL in Phase 1. The operator is responsible for cleaning `data/sessions/` (or Phase 2 implements a periodic cleanup job based on `run_sessions.expires_at`). File deletion failure is non-fatal and does not block new runs.

**Audit record lifecycle:** `runs` rows are append-only. They are never deleted or updated after `completed_at` is set (except by an operator SQL command for data-retention compliance). Run history is retained indefinitely by default.

**Phase 2 session lifecycle:** A session is created when the first run in a conversation group is submitted (or explicitly via a session-create endpoint). It expires after a configurable TTL (default: 24 hours of inactivity). At expiry, the corresponding SQLite cache file (if any) and the session's history summary may be removed by a background cleanup job (deferred to Phase 2 polish).

---

## Sensitive Data

The agent processes **operational police data** (FIR registers, incident logs, arrest records) that may contain:

- Names and personal identifiers of complainants, accused persons, and witnesses.
- Crime scene addresses, dates, and incident descriptions.
- Internal police procedural notes.

**Protection measures:**
- The application runs on an internal (air-gapped) police network — no data leaves the host.
- PostgreSQL credentials and the MSSQL connection string are stored in `.env` (filesystem-level access control).
- The Ollama endpoint has no external exposure; the LLM processes data in-memory only.
- CSV uploads are stored only in the per-session SQLite file under `data/sessions/`, which is gitignored and file-system protected.
- **No answer text, full SQL, or raw data rows are written to application logs** — only a truncated (≤100-char) excerpt of `answer_text` and aggregate token counts are logged for traceability. Full data is only in the PostgreSQL `runs` row and the SQLite session cache.
- The application does not implement row-level access control; it relies on network-level access control (VPN, internal LAN, Windows auth at reverse-proxy level if deployed behind Nginx/IIS).
- Phase 2 adds per-session RBAC if required at the API layer (deferred pending operational requirements).

> **Assumed:** The operator is responsible for PostgreSQL and filesystem access controls at the OS level; the agent does not implement application-layer RBAC in Phase 1 or Phase 2 (that is an operational deployment concern, not an agent capability).
