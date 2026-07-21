# Roadmap — UP Police Data Analyst Agent

> **App:** UP Police Data Analyst
> **Stack:** Python 3.11 · FastAPI · LangGraph · PostgreSQL 16 · SQLite (CSV cache) · Ollama (self-hosted LLM) · uv
> **Access:** Web UI (zero-build static at `/app`) + REST API
> **Environment:** On-premises / air-gapped; no external API calls; Ollama serves the LLM locally
> **Primary users:** Police data analysts, crime investigation officers, and administrative staff who need to query CSV-uploaded police data or MSSQL live database using plain-language questions

---

## What This Agent Does

The UP Police Data Analyst is a LangGraph-backed AI agent that gives police personnel self-service, natural-language access to police operations data. In Phase 1, a user uploads a CSV file (e.g., FIR register, arrest records, incident logs) and then asks plain-language questions about it — the agent generates and executes SQL against an in-process SQLite cache of the uploaded data, interprets the result, and returns a plain-text answer with a row count and any caveats. From Phase 2 onward, the agent also connects to a live MSSQL production database (via a lightweight PostgreSQL mirror), accelerating queries through a read-optimized materialised-view layer. The complete system is designed to run entirely on-premises or on an air-gapped network: no LLM API keys leave the host, all data stays local, and all writes go to the local PostgreSQL audit/session store.

The agent follows a **plan-first reasoning** loop: for each question it generates an explicit plan before touching any SQL, iteratively fixes SQL errors when the database rejects them, and surfaces only the final answer text (plus audit metadata) to the user — never raw SQL or schema details by default.

## Who Uses It

| Role | What they need |
|------|----------------|
| Police data analyst | Quick counts, trends, and outlier reports across incident/FIR CSVs without running SQL by hand |
| Investigation officer | Instant answers from the live incidents DB without a DBA or MIS request |
| Administrative supervisor | Audit trail of every question asked, every SQL executed, and every answer returned |

## Core Problem Being Solved

Currently, police data analysis requires either: (a) manually running SQL or exporting data to Excel, a slow and error-prone process; or (b) filing MIS requests that take days. The agent closes this gap by letting any authorised user ask a natural-language question and get a verified, auditable answer in seconds — first against uploaded CSV data, then against the live MSSQL mirror — without needing SQL skills or external connectivity.

## Success Criteria

- [ ] A user can upload a CSV (≤ 50 MB) through the Web UI and receive a schema summary within 60 seconds
- [ ] The same user can ask a natural-language question about that CSV and receive a correct, plain-text answer with the underlying row count shown
- [ ] If the generated SQL has an error, the agent retries and fixes it without user intervention (max 3 attempts)
- [ ] Every run — question, SQL, answer, timing — is recorded in PostgreSQL with a full audit trail
- [ ] Token usage and estimated cost are tracked per run and shown in the audit log
- [ ] The agent does not require external internet access to function (Ollama, PostgreSQL, MSSQL bridge all run locally)
- [ ] Phase 2: a live MSSQL connection produces the same NL→answer cycle with a row-count match against the source

## What This Agent Does NOT Do (Out of Scope)

- Write or modify data in the MSSQL production database (read-only mirror)
- Handle multi-turn conversation context beyond the current session history window (deferred to Phase 2 polish)
- Provide chart or graph visualisations in Phase 1 (`<chart>` placeholder in answer; chart rendering deferred)
- Authenticate individual users with roles/permissions beyond a single shared deployment (assumed deployed on an internal network with network-level access control)
- Load CSV files larger than 50 MB (handled via chunked upload in Phase 2; gated at 50 MB in Phase 1)
- Write to the CSV cache or modify the source data
- Connect to cloud-hosted LLM providers (Ollama only; cloud fallback is out of scope)

## Key Constraints

- **Air-gapped / on-prem:** the complete stack must run without external network access; all secrets (PostgreSQL credentials, Ollama endpoint, MSSQL connection string) are in `.env`
- **MSSQL is read-only:** a lightweight PostgreSQL mirror replicates the live schema; the agent only ever queries
- **Primary DB stays light:** PostgreSQL holds the audit/session DB only; the CSV data cache lives in a per-session SQLite file that is created fresh per upload and destroyed when the session expires
- **LLM is self-hosted:** Ollama endpoint configured via env var; no API keys leave the host
- **Token budget tracking:** every LLM call logs input/output token counts against a per-session budget counter; budget exhaustion surfaces a plain error
- **Answer-text-only UI:** answers are plain text with structural markers (`<warning>`, `<table-summary>`, `<row-count>`); no HTML rendering of SQL results in the answer box

---

## Phases of Development

> Phase 1 is the **smallest first-time-right user-testable win**: real CSV upload + NL Q&A against SQLite, all testable the first time. No stubs on the tested path. Later phases wire live-DB, charting, history, and MSSQL acceleration.

---

### Phase 1 — CSV Upload + Natural-Language Q&A (SQLite)

- **Goal:** A user uploads a CSV file through the Web UI, the agent ingests it into a per-session SQLite database, the user asks plain-language questions, and receives correct, auditable plain-text answers — end-to-end, real data, real Ollama LLM, first-time-right.
- **Independent slices (parallel build units):**
  - `slice-data` (backend) — CSV ingestion + SQLite schema inference + safe query runner; owns `src/tools/csv_tools.py`, `src/graph/nodes.py` (new nodes), `spec/capabilities/upload_csv.md`, `spec/capabilities/generate_sql.md`, `spec/capabilities/execute_query.md`. Dependencies: none (slice-ui reads the session store, not this slice directly).
  - `slice-agent` (backend) — LangGraph graph assembly (plan → generate SQL → execute → format answer → handle error), new state fields, audit persistence; owns `src/graph/state.py`, `src/graph/nodes.py` (new node funcs), `src/graph/edges.py`, `src/graph/agent.py`, `src/graph/runner.py`, `src/prompts/`. Dependencies: `slice-data` (for tool signatures shared in the graph).
  - `slice-api` (backend) — POST /runs endpoint + CSV upload endpoint extended, RunRequest/RunResult extended; owns `src/api/runs.py`, `src/domain/run.py`. Dependencies: `slice-agent` (graph invocation signature).
  - `slice-ui` (frontend) — Upload form + question box + answer display + progress+timer + audit log sidebar; owns `frontend/public/`. Dependencies: none (calls REST API; no direct DB access).
- **Key surfaces / files:**
  | Slice | Surfaces |
  |-------|----------|
  | slice-data | `src/tools/csv_tools.py`, `spec/capabilities/upload_csv.md` |
  | slice-agent | `src/graph/state.py`, `src/graph/nodes.py`, `src/graph/edges.py`, `src/graph/agent.py`, `src/prompts/` |
  | slice-api | `src/api/runs.py`, `src/domain/run.py`, `spec/api.md` |
  | slice-ui | `frontend/public/index.html`, `frontend/public/styles.css`, `frontend/public/app.js` |
- **Gate command:** `uv run pytest tests/integration/test_phase1_csv_qa.py -v` (must pass against real Ollama at `AGENT_OLLAMA_BASE_URL` + SQLite CSV cache DB; asserts on answer content and row count, not just status code)
- **How the user tests it (handoff seed):**
  1. `cd C:\Users\PC\zero-shot-hermes-harness && uv run python -m src` — server boots on `http://localhost:8001`
  2. Open `http://localhost:8001/app/` — see the upload form labelled *"Step 1: Upload CSV (≤ 50 MB)"* and an answer box labelled *"Answer (pending upload)"* (stub text: *"Upload a CSV to start asking questions"*).
  3. Upload a small CSVs (e.g., 10-row FIR test fixture committed to `tests/fixtures/`).
  4. UI shows schema summary (columns + types inferred) and a question input appears.
  5. Type a question: *"How many records are there total?"* — answer must state the correct row count within 30 seconds.
  6. Sidebar shows: question → SQL → answer with timing and token usage. (Chart rendering stub shows `<chart>` placeholder — labelled *"Coming in Phase 2"*.)
  7. Structured log line appears for the run (observability gate).
- **Real vs stub surfaces (Phase 1):**
  - **Real:** CSV upload + SQLite ingestion, NL→SQL generation + execution, NL answer text, audit log row, progress indicator, token/cost counter, error retry-on-SQL-error.
  - **Stub (labelled):** Chart/graph rendering (shows `<chart type="bar" metric="COUNT(*)" />` placeholder, labelled *"Phase 2"*); live-MSSQL mode button (disabled, labelled *"Phase 2"*); query history beyond current session (shows *"Full history — Phase 2"*); user authentication (not implemented; labelled *"Deployment-time concern"*).

---

### Phase 2 — Live MSSQL Mirror + Accelerated Queries + Session History

- **Goal:** The agent connects to a live PostgreSQL mirror of an MSSQL production database, accelerates repeated patterns via materialised views, and persists a full query/answer history per user session — enabling analysts to work directly against live data and review past findings.
- **Independent slices (parallel build units):**
  - `slice-mssql-bridge` (backend) — ETL job that ingests from MSSQL into a local PostgreSQL schema mirror; queue-based incremental refresh; owns `src/tools/mssql_ingest.py`, `src/db/models.py` additions, `alembic/versions/0002_mssql_mirror.py`. Dependencies: none.
  - `slice-acceleration` (backend) — Materialised-view definitions + refresh trigger; query planner that prefers MV over raw table scan for recognised patterns; owns `src/tools/query_planner.py`, `alembic/versions/0003_acceleration.py`. Dependencies: `slice-mssql-bridge` (knows the mirrored schema).
  - `slice-history` (backend) — Conversation history persistence, retrieval (last N turns), session scoping; extends AgentState with `conversation_history`; owns `src/graph/state.py`, `src/tools/history_tools.py`, `src/api/runs.py` (history endpoints). Dependencies: none (uses existing PostgreSQL session DB).
  - `slice-session-ui` (frontend) — History panel (past Q&A pairs), MSSQL connection indicator, live-DB toggle, chart rendering (simple text-based bar chart), budget warning banner; owns `frontend/public/index.html`, `frontend/public/styles.css`, `frontend/public/app.js`. Dependencies: `slice-history` (needs the history endpoint response shape).
- **Key surfaces / files:**
  | Slice | Surfaces |
  |-------|----------|
  | slice-mssql-bridge | `src/tools/mssql_ingest.py`, `src/db/models.py`, `alembic/` |
  | slice-acceleration | `src/tools/query_planner.py`, `src/prompts/plan.md` |
  | slice-history | `src/tools/history_tools.py`, `src/graph/state.py`, `src/api/runs.py` |
  | slice-session-ui | `frontend/public/` |
- **Gate command:** `uv run pytest tests/integration/test_phase2_mssql_history.py -v` (must pass against real Ollama + PostgreSQL (both `DATABASE_URL` and `AGENT_MSSQL_CONNECTION_STRING` set; `MSSQL_INGEST_ENABLED=true` for the ingest test subset); asserts content of live-DB Q&A answers and confirms history retrieval returns prior turns)
- **How the user tests it (handoff seed):**
  1. `cd C:\Users\PC\zero-shot-hermes-harness && AGENT_MSSQL_CONNECTION_STRING="mssql+pyodbc://..." MSSQL_INGEST_ENABLED=true uv run python -m src`
  2. Open `http://localhost:8001/app/` — the *"Data source"* toggle now shows *"Live DB (MSSQL Mirror)"* enabled; green indicator shows *"Mirror: up-to-date as of <timestamp>"*.
  3. Ask the same question as Phase 1 — answer now references live data; row-count must match a ground-truth SQL Server CMS query.
  4. Ask a second question — history panel shows both Q&A pairs.
  5. Budget banner shows cumulative tokens used vs. session limit.

---

### Phase 3 (Optional) — Polish + Hand-off

- **Goal:** Final drift audit, README end-to-end verified from a clean clone, edge-case hardening, hand-off to operations.
- **Independent slices (parallel build units):**
  - `slice-polish-backend` (backend) — Retry/backoff hardening on Ollama, OOM-safe CSV loader (streaming for >10 MB), Alembic migratability check on a fresh PostgreSQL container. No new capability; robustness only.
  - `slice-polish-frontend` (frontend) — Responsive layout fixes, accessible labels, loading states, budget-exhausted state.
  - `slice-docs` (backend) — README verified command-by-command from clean clone; drift audit runs green; `.env.example` complete and current.
- **Gate command:** `uv run pytest tests/ -v --tb=short` (all unit + integration + e2e green; real Ollama + PostgreSQL; `uv run alembic upgrade head` succeeds on a throwaway PostgreSQL container)
- **How the user tests it:** Re-run the two golden paths (CSV upload; live-DB Q&A) from a clean clone per README; confirm every step runs without manual env editing beyond filling `.env`.
