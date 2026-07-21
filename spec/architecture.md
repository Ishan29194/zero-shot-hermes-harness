# Architecture — UP Police Data Analyst Agent

> **Assumed conventions:** See `harness/patterns/project-layout.md` for directory-tree rules (all app code in `src/` and `frontend/public/`), `harness/patterns/tech-stack.md` for tech-stack rules (port 8001, zero-build frontend, real-LLM test rule, PostgreSQL driver in production deps), `harness/patterns/spec-driven.md` for spec-first discipline.

---

## System Overview

The UP Police Data Analyst is a self-contained, air-gapped-capable AI agent system that lets police personnel query police operations data using plain-language questions. It runs entirely on-premises (no outbound internet required), uses a self-hosted Ollama LLM, and stores all audit data in a local PostgreSQL instance. In Phase 1 the system ingests CSV data into a per-session SQLite cache and answers questions via LangGraph's ReAct-style tool-use loop: plan → generate SQL → execute SQL → format answer. Phase 2 adds a live MSSQL mirror (replicated into PostgreSQL) with materialised-view acceleration, plus a full query-session history.

The system exposes a **single FastAPI server** (port 8001) serving both the REST API and the zero-build static frontend (at `/app/`). There is no separate frontend build pipeline in Phase 1 or Phase 2 — only static HTML/CSS/JS.

## Component Map

```
User browser
   │  HTTPS (localhost:8001)
   ▼
FastAPI server  ─────────────────────────────────────────────┐
   │                                                          │
   │  POST /runs (NL question + optional CSV)                 │
   ▼                                                          │
LangGraph Agent Run                                          │
   │                                                          │
   │  ┌─────────────┐   ┌───────────────┐   ┌──────────────┐│
   │  │ plan_node   │──▶│ generate_sql  │──▶│ execute_sql  ││
   │  └─────────────┘   └───────────────┘   └──────────────┘│
   │        │                  │                    │        │
   │        │            [retry on error]        [error]   │
   │        │                  │                    │        │
   │        │                  ▼                    ▼        │
   │        │           ┌───────────────┐  ┌─────────────┐  │
   │        └──────────▶│ format_answer │  │ handle_error│  │
   │                     └───────────────┘  └─────────────┘  │
   │                            │               │          │
   ▼                            ▼               ▼          ▼
PostgreSQL                    SQLite           Ollama
(audit/session)               (CSV cache)      (LLM)
                                                      │
                                      ┌───────────────┘
                                      │ (optional, Phase 2)
                                      ▼
                               PostgreSQL
                               (MSSQL mirror +
                                acceleration MVs)
```

## Layers

| Layer | Responsibility |
|-------|----------------|
| **API** | FastAPI routes — POST `/runs` (NL Q&A), POST `/runs/{id}/upload` (CSV attach), GET `/runs` (history), GET `/health` (provider + DB status) |
| **Agent orchestration** | LangGraph `StateGraph` — compiles once at startup; graph is stateless between runs (all state lives in the TypedDict passed to `invoke()`) |
| **Tools / Nodes** | Pure Python functions — `ingest_csv()`, `generate_sql()`, `execute_query()`, `format_answer()`, `plan()`, `handle_error()` — each `(state) -> partial_state` |
| **LLM** | `LLMClient` singleton — Ollama HTTP adapter; one-batched call per node; retries on 429/5xx via `llm/retry.py` |
| **Storage** | PostgreSQL (audit/session DB + Phase 2 MSSQL mirror); SQLite (per-session CSV cache, created on upload, destroyed on session expiry) |
| **Observability** | structlog — one `log_span` per run; one log event per node; LLM token counts logged per call |

## Data Flow

1. **Trigger:** User sends `POST /runs` with `question` (required) and optional `csv_upload` (multipart file).
2. **Ingest (if CSV present):** `ingest_csv` node reads the file → infers column names + SQLite types → creates `session_{run_id}.db` SQLite file → stores path in `state["sqlite_path"]` and schema summary in `state["schema_text"]`. Returns immediately; Phase 2 adds MSSQL mirror fallback path here.
3. **Plan:** `plan` node calls Ollama (one call) with the user's question + schema text → returns a 3–5 step plan of which tables/columns to use. No SQL yet.
4. **Generate SQL:** `generate_sql` node calls Ollama (one call) with plan + schema + question → returns a single `SELECT` statement. If Ollama returns non-SQL text or multiple statements, the node sanitises and retries (up to 2 retries).
5. **Execute SQL:** `execute_query` node opens the SQLite session DB (or Phase 2 PostgreSQL mirror), runs the SQL via parameterised `sqlalchemy.text()`, returns `rows` (list of dicts) + `row_count` + `error` if any. If SQL is invalid: set `state["sql_error"]`, route back to `generate_sql` (max 3 iterations).
6. **Format Answer:** `format_answer` node calls Ollama (one call) with rows + row count + question → returns a structured plain-text answer. Format rules: start with a direct answer sentence, then bullet supporting figures, end with `<row-count>` tag. If rows are empty: answer says *"No matching records found."*
7. **Finalize:** `finalize` node sets status to `completed`, persists run row to PostgreSQL (audit/session DB) with `sql_text`, `row_count`, `answer_text`, `input_tokens`, `output_tokens`, `duration_ms`.
8. **Output:** API returns `RunResult` with `status`, `answer_text`, `row_count`, `sql_text`, `provider`, `model`, `duration_ms`, `input_tokens`, `output_tokens`. If any node fails after max retries: status=`failed`, `error_message` set.

### Phase 2 extended data flow (MSSQL mirror)

- After upload, if a live MSSQL mirror is available and the user's question targets known live tables, the `plan` node may route to the acceleration layer: it checks materialised views first; if a recognised pattern match exists (e.g., "count by month" → MV `mv_monthly_incidents`), it rewrites the SQL against the MV and logs the MV hit in the audit row. Otherwise it falls through to the live PostgreSQL mirror schema.

## External Dependencies

| Dependency | Purpose | Failure Mode |
|------------|---------|--------------|
| **Ollama** (local HTTP, env `AGENT_OLLAMA_BASE_URL`) | LLM inference — plan, generate SQL, format answer | Agent surfaces `"Ollama unreachable — check that Ollama is running and AGENT_OLLAMA_BASE_URL is correct"`; run status=`failed`; no data loss (audit row persists with error_message). Retries 2× with 2 s back-off before surfacing. |
| **PostgreSQL** (env `AGENT_DATABASE_URL`) | Audit/session store — run history, token budgets, (Phase 2) MSSQL mirror schema + acceleration MVs | App fails to start (fatal — DB is terminal dependency). surfaced as startup error; `init_db()` retries 3× then exits. |
| **SQLite** (file: `data/sessions/{run_id}.db`) | Per-session CSV cache; created fresh per upload, destroyed on session expiry | Run fails with `"CSV cache not initialised — re-upload the file"`; user re-uploads. |
| **MSSQL** (env `AGENT_MSSQL_CONNECTION_STRING`, Phase 2 only) | Source of truth for live data replication | ETL job surfaces `"MSSQL mirror sync failed — using last known snapshot"`; last-known-snapshot queries still work; agent never queries MSSQL directly. |

## Stack

> **Assumed:** The user's intake specified Python + FastAPI + LangGraph + PostgreSQL + Ollama. No deviations from the baseline beyond what the intake dictates; unstated defaults are recorded as `> **Assumed:** …` below.

- **Language:** Python 3.11+ (matches `python=3.11.15` in the environment)
- **Agent framework:** LangGraph (`langgraph` package, StateGraph assembly in `src/graph/agent.py`)
- **LLM provider + model:** Ollama (self-hosted, accessed via `httpx`); default model set by `AGENT_OLLAMA_MODEL` env var (e.g., `llama3.1:8b-instruct-q4_0`); resolves at startup via `Settings.ollama_model()`
- **Backend:** FastAPI (serves API + static frontend at port 8001)
- **Audit/session database + ORM:** PostgreSQL 16 + SQLAlchemy 2.0 (sync)
- **CSV query cache database:** SQLite 3 (per-session file; `sqlite3` stdlib driver, no async)
- **Frontend:** Zero-build static HTML/CSS/JS in `frontend/public/` (no bundler; served at `/app/`)
- **Dependency management:** `uv` + `pyproject.toml`

> **Assumed:** Ollama models are chosen at deploy time by the operator via `AGENT_OLLAMA_MODEL`. The spec does not pin a specific Ollama model name (they change frequently); the code resolves it at startup and surfaces a clear error at boot if the model is unavailable.

### Key Libraries

| Library | Version | Purpose |
|---------|---------|---------|
| `fastapi` | 0.115+ | HTTP API + static file serving |
| `uvicorn` | 0.30+ | ASGI server |
| `langgraph` | 0.2+ | Agent state graph assembly + checkpointing |
| `langchain-core` | 0.3+ | LLM abstractions (compatible with Ollama endpoint via `ChatOllama`) |
| `sqlalchemy` | 2.0+ | ORM (PostgreSQL audit/session DB) + SQLtext execution (SQLite cache) |
| `psycopg2-binary` | 2.9+ | PostgreSQL driver (declared in `[project.dependencies]`, per tech-stack rules) |
| `httpx` | 0.27+ | Ollama HTTP client (no SDK, per baseline rule) |
| `pydantic-settings` | 2.0+ | Env config (`extra="ignore"` required) |
| `structlog` | 24.0+ | Structured logging |
| `pandas` | 2.2+ | CSV ingestion + type inference |
| `openpyxl` | 3.1+ | CSV reading (pandas dependency) |
| `pyodbc` | 5.x | MSSQL bridge connectivity (Phase 2; declared in optional `[dependency-groups.mssql]` per `pyproject.toml` convention) |
| `alembic` | 1.13+ | DB schema migrations |

### Avoid

- **No requests library** — use `httpx` everywhere (consistent, async-capable).
- **No asyncpg / aiosqlite for this project** — the CSV cache is small and synchronous SQLAlchemy is simpler; the audit DB uses synchronous psycopg2 (consistent with the baseline). Defer async migration unless a performance gate demands it.
- **No cloud LLM SDKs** — Ollama is reached via raw `httpx` POST to `/api/chat`, matching the baseline provider-pattern. Adding Anthropic/Gemini/OpenRouter adapters is unnecessary and contradicts the air-gapped requirement.
- **No multiprocessing for CSV ingest** — use pandas `read_csv` in-process; large files (>10 MB) use `chunksize` iterator.
- **No repository pattern** — direct SQLAlchemy queries in nodes and API handlers (per `harness/patterns/project-layout.md` rule 2).

## Deployment Model

The agent runs as a **long-lived FastAPI process** on a police department internal server (Windows Server or Linux). It is started via `uv run python -m src` (or `python -m src` after `uv sync`). Required environment variables are set in a `.env` file stored alongside the app (access-controlled at the filesystem level).

**Phase 1** (air-gapped / no MSSQL):

```bash
# All commands from repo root: C:\Users\PC\zero-shot-hermes-harness
uv run alembic upgrade head
uv run python -m src
# → http://localhost:8001/app/
```

**Phase 2** (with live MSSQL mirror):

```bash
uv run alembic upgrade head
AGENT_MSSQL_CONNECTION_STRING="mssql+pyodbc://..." uv run python -m src
```

The static frontend is served by the same FastAPI process (single-origin pattern, per `harness/patterns/tech-stack.md`). No reverse proxy is required for local deployment; for network-wide access, a reverse proxy (e.g., Nginx) may be placed in front without code changes.

**Ollama management (operator duty, not in-code):**

```bash
# On the Ollama host (same machine or reachable internal network host)
ollama pull llama3.1:8b-instruct-q4_0
ollama serve  # default :11434
```

> **Assumed:** Ollama is pre-installed and managed by the operator; the agent only calls its HTTP API. Model pulls and Ollama lifecycle are outside the agent's scope.
