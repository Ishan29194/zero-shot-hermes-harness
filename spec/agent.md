# Agent — UP Police Data Analyst

> **Required:** this project uses LangGraph. An incomplete graph specification is a critical blocker.
> **Patterns used:** ReAct loop (#17 — Reasoning Techniques / Tool Use #5) + Planning (#6) + Exception Handling and Recovery (#12) + Resource-Aware Optimization (#16). See `harness/patterns/agentic-ai.md` for the pattern catalogue.
> **Assumed:** See `harness/patterns/project-layout.md` for the `src/graph/` slot; all capacity is in `src/graph/nodes.py`; the graph compiles in `src/graph/agent.py`; `src/graph/edges.py` holds conditional routing functions.

---

## Agent Architecture Pattern

**Chosen: LangGraph ReAct tool-use loop with explicit planning step.**

Rationale: The agent's primary task (NL question → SQL → answer) requires sequenced tool calls — plan (LLM), generate SQL (LLM), execute query (SQLite/PostgreSQL), format answer (LLM) — with conditional branching on query error (retry SQL) and terminal failure (handle_error). A prompt chain is insufficient because the loop must iterate on SQL errors. Multi-agent is unnecessary: all reasoning is by one LLM with structured outputs. The ReAct pattern (reason → act → observe) is the natural fit, enhanced with an explicit planning node to reduce SQL hallucination and a resource-aware budget tracker to keep costs visible in an air-gapped environment.

---

## LLM Provider & Model

| Agent / Node | Provider | Model ID | Rationale |
|-------------|----------|----------|-----------|
| `plan_node` | Ollama | `AGENT_OLLAMA_MODEL` env var (e.g. `llama3.1:8b-instruct-q4_0`) | Lightweight plan; small context (schema text only) |
| `generate_sql_node` | Ollama | `AGENT_OLLAMA_MODEL` env var | Schema + plan → SQL; medium context |
| `format_answer_node` | Ollama | `AGENT_OLLAMA_MODEL` env var | Rows → answer; small-medium context |
| *All nodes* | Ollama | Same model | Single model per deployment to simplify Ollama management in air-gapped env |

**Fallback behaviour:**
- Ollama HTTP returns 502/503/504 or connection refused: the node catches `httpx.HTTPError` / `OllamaHTTPError`, sets `state["error"]`, the conditional edge routes to `handle_error_node`. The graph terminates; `finalize_node` is NOT reached. The API returns 200 with `status: "failed"` and an actionable `error_message` pointing at Ollama connectivity. The agent retries Ollama calls up to **2 times** with exponential back-off (`@retry` decorator in `src/llm/retry.py` pattern, applied per node) before surfacing the error.
- SQLite file missing: `execute_query` catches `OperationalError` (file not found), sets `state["sql_error"] = "CSV cache not found — re-upload the CSV file"`, routes back to `ingest_csv` (if no prior CSV was uploaded this run) or to `handle_error_node` (if ingest already failed once). Phase 1 does not auto-re-prompt for re-upload; the frontend surfaces the error and shows the upload button.
- SQL dialect error: `execute_query` catches `ProgrammingError` / `DataError` (e.g., wrong column name, type mismatch), sets `state["sql_error"]` with the DB error text, routes back to `generate_sql_node` (max 3 total SQL generation attempts including the original). After 3 failures: `handle_error_node`.
- Token budget exhausted: the `format_answer_node` (or any node after `llm_call`) checks `state["tokens_used"] >= state["token_budget"]` BEFORE making the call; if exhausted, sets `state["error"] = "Token budget exceeded"`, routes to `handle_error_node`. The budget is an integer input (`AGENT_TOKEN_BUDGET`, default 8192 for Phase 1; Phase 2 extends this).

**Prompt strategy:**
System/user split per node. Each node builds its own system prompt from a `.md` template loaded at call time via `load_prompt()`. The user message incorporates state. Structured JSON output is requested for the plan node (returns a JSON list of steps); free-form text for `format_answer_node` (the answer format is enforced via instructions in the system prompt, not a JSON schema). No few-shot examples in Phase 1 (eval will add them in Phase 3 if needed). All prompts live in `src/prompts/` as `.md` files.

---

## Tools & Tool Calling

The agent uses **no external MCP or tool-calling framework** — all capability logic is encoded as LangGraph nodes. The "tools" here are the internal node functions; the LLM generates SQL as text output, not via a function-call schema. This avoids the overhead of defining a SQL generation tool schema and matches the data-analyst pattern (SQL is the universal tool).

| Node (tool) | Description | Inputs (from state) | Output (to state) | Side-effects |
|-------------|-------------|-------------------|-------------------|--------------|
| `ingest_csv_node` | Reads CSV, infers schema, loads into per-session SQLite file | `csv_bytes`, `run_id` | `sqlite_path`, `schema_text`, `row_count_csv` | Creates SQLite file on disk; file destroyed on session expiry |
| `plan_node` | Generates a 3–5 step plan of which tables/columns to use | `question`, `schema_text` | `plan_text` | One Ollama call |
| `generate_sql_node` | Generates a single `SELECT` from plan + question + schema | `question`, `schema_text`, `plan_text`, `sql_error` (retry feedback) | `sql_text` | One Ollama call |
| `execute_query_node` | Runs SQL via SQLAlchemy `text()` against SQLite (Phase 1) or PostgreSQL mirror (Phase 2) | `sql_text`, `sqlite_path` / `db_datasource` | `query_rows`, `row_count`, `sql_error` | DB read (no side-effects); parameterised queries only |
| `format_answer_node` | Formats query rows into a plain-text answer with row-count tag | `question`, `query_rows`, `row_count`, `plan_text` | `answer_text` | One Ollama call |
| `handle_error_node` | Marks run as failed, persists error_message, logs | `error` or `sql_error` | `status = "failed"` | DB write (audit row) |
| `finalize_node` | Marks run as completed, persists all audit fields | all state fields | `status = "completed"` | DB write (audit row) |

**Tool selection strategy:** Rule-based routing through conditional edges in the graph (see Graph Topology below). The LLM does not choose which node to call — the graph topology determines that. The LLM's only "action" is generating SQL text as a string within `generate_sql_node`'s output.

**Tool failure handling:**
- Node-level: each node wraps its body in a `try/except`. Fatal errors set `state["error"]` and return; non-fatal errors set the relevant error field (`sql_error`, `sqlite_error`) and return.
- Graph-level: `handle_error_node` catches all terminal failures. See Error Handling & Recovery section.
- SQL errors are the only case where the loop retries: they route back to `generate_sql_node` (max 3 attempts). All other errors terminate immediately.

---

## Agent State

```python
class AgentState(TypedDict, total=False):
    # ── Identity ──────────────────────────────────────────
    run_id: str                 # UUID — set once at graph entry; used for all DB log entries

    # ── Input ─────────────────────────────────────────────
    question: str               # The user's plain-language question (from POST body)
    csv_bytes: bytes | None     # Raw CSV file bytes (if upload; None for pure NL Q&A on live DB)
    instruction: str            # System-level instructions (from settings prompt template)
    token_budget: int           # Max allowed input+output tokens for this run (from settings)

    # ── Data source ───────────────────────────────────────
    sqlite_path: str | None     # Path to the per-session SQLite DB file (Phase 1 CSV cache)
    schema_text: str | None     # Inferred schema summary: "table: col1(TEXT), col2(INTEGER)..." (Phase 1)
                                  # Extended to live-DB schema text in Phase 2
    row_count_csv: int | None   # Total rows ingested from CSV (Phase 1)

    # ── Pipeline data (populated progressively) ───────────
    plan_text: str | None       # The plan generated by plan_node (3-5 step outline)
    sql_text: str | None        # The SQL generated by generate_sql_node (single SELECT)
    query_rows: list[dict] | None  # Rows returned by execute_query_node (list of dicts)
    row_count: int | None       # Row count from the query result
    sql_error: str | None       # SQL / DB error text — non-None routes to generate_sql_node (retry)
    sql_attempts: int           # Counter: how many times generate_sql_node has been called this run

    # ── Output ────────────────────────────────────────────
    answer_text: str | None     # Final formatted answer (plain text, may include <tags>)
    input_tokens: int | None    # Total input tokens consumed across all Ollama calls in this run
    output_tokens: int | None   # Total output tokens consumed
    duration_ms: int | None     # Wall-clock ms from graph entry to finalize

    # ── Control ───────────────────────────────────────────
    error: str | None           # Fatal error string — routes to handle_error_node
    status: str | None          # "pending" → "running" → "completed" | "failed"
    datasource: str             # "sqlite" (Phase 1 CSV) | "mssql_mirror" (Phase 2 live)
```

---

## Nodes / Steps

### `ingest_csv_node`

**Reads from state:** `csv_bytes`, `run_id`, `token_budget`

**Writes to state:** `sqlite_path`, `schema_text`, `row_count_csv`, `sqlite_error`

**LLM call:** No

**External calls:**

| System | Operation | On Failure |
|--------|-----------|------------|
| SQLite (file) | Create `data/sessions/{run_id}.db`, infer schema via `pandas.read_csv`, write to SQLite via `to_sql()` | Set `sqlite_error`, route to `handle_error_node` |

**Behaviour:** If `csv_bytes` is `None` (the user is asking a follow-up question on a pre-loaded session), this node is skipped (graph edge bypass). If `csv_bytes` is present: (1) create the SQLite file under `data/sessions/`, (2) use pandas `read_csv` with `chunksize=10000` for files >10 MB, (3) infer column types (INT, REAL, TEXT — all nullable), (4) write to a single table named `data` via `to_sql("data", if_exists="replace")`, (5) build `schema_text` = `"data: col1(TEXT) | col2(INTEGER) | ..."`, (6) store row count. All pandas type-inference errors are caught — fall back to TEXT for columns that can't be inferred. Max CSV size: 50 MB; larger files get `sqlite_error = "CSV exceeds 50 MB limit"` and route to `handle_error_node` (Phase 2 will handle chunked multi-file ingest).

---

### `plan_node`

**Reads from state:** `question`, `schema_text`, `instruction`

**Writes to state:** `plan_text`

**LLM call:** Yes — one Ollama call; prompt template: `src/prompts/plan.md`

**External calls:** Ollama HTTP `POST /api/chat`

| On Failure | Action |
|------------|--------|
| Ollama unreachable / timeout | Set `state["error"]`, route to `handle_error_node` (retried by LLM retry decorator first, max 2 retries) |
| Ollama returns non-JSON / malformed plan | Set `state["error"] = "Plan generation failed: malformed LLM response"`, route to `handle_error_node` |

**Behaviour:** Calls Ollama with system prompt "You are a data-analysis planner for a police department database. Given a user question and the table schema, produce a JSON array of 3–5 steps identifying which tables and columns to use." The user prompt contains `{schema_text}` and `{question}`. The node parses the LLM's JSON response (using `json.loads`), validates it is a list of strings, and stores it as `plan_text`. If parsing fails: retry once with a stricter system prompt; if still failing, set `error`.

---

### `generate_sql_node`

**Reads from state:** `question`, `schema_text`, `plan_text`, `sql_error`, `sql_attempts`

**Writes to state:** `sql_text`, `sql_attempts`

**LLM call:** Yes — one Ollama call; prompt template: `src/prompts/generate_sql.md`

**External calls:** Ollama HTTP `POST /api/chat`

| On Failure | Action |
|------------|--------|
| Ollama unreachable / timeout | Set `state["error"]`, route to `handle_error_node` |
| Ollama returns non-SQL text or multiple statements | Sanitize: extract the first statement that starts with `SELECT` (case-insensitive); if none found, treat as error |
| SQL dialect error on next execute | `sql_error` set by `execute_query_node`; this node re-invokes with `sql_error` feedback appended; increments `sql_attempts` |

**Behaviour:** Calls Ollama with system prompt: "You are a SQL expert. Given a schema, a plan, and a question, generate a single SELECT statement. Use only the tables and columns listed in the schema. Never use DDL, DML, subqueries against system tables, or multiple statements." Output format: the SQL statement only (no markdown fences, no preamble). If `sql_error` is non-None (retry path), the user message appends: `"PREVIOUS SQL FAILED WITH:\n{sql_error}\n\nFix the SQL and return only the corrected SELECT statement."` The node increments `sql_attempts` on each call. When `sql_attempts >= 3`, it sets `state["error"] = "SQL generation failed after 3 attempts"` and does NOT return `sql_text` (routes to `handle_error_node` on next conditional edge evaluation). Otherwise returns the extracted SQL string.

---

### `execute_query_node`

**Reads from state:** `sql_text`, `sqlite_path`, `datasource`, `run_id`

**Writes to state:** `query_rows`, `row_count`, `sql_error`

**LLM call:** No

**External calls:**

| System | Operation | On Failure |
|--------|-----------|------------|
| SQLite (Phase 1) | `sqlalchemy.create_engine(f"sqlite:///{sqlite_path}").execute(text(sql_text))` | Catch `OperationalError`, `ProgrammingError`, `DataError`; set `sql_error` with DB text |
| PostgreSQL (Phase 2) | `sqlalchemy.create_engine(AGENT_DATABASE_URL).execute(text(sql_text))` | Same as above; additionally catch `sqlalchemy.exc.IntegrityError` (should not occur on SELECT) |

**Behaviour:** Sanitises `sql_text` first: strips markdown fences (```sql ... ```), strips surrounding whitespace, rejects any statement containing DML keywords (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`) — if found, set `sql_error = "Only SELECT queries are permitted"`. Opens a SQLAlchemy engine connection (context manager), executes the sanitised SQL via `text(sql_text)` (parameterised — no string substitution of user input into SQL, only the LLM-generated SQL which is passed as a raw statement). Fetches all rows as `list[dict]` via `RowMapping`. Stores `query_rows` and `row_count = len(rows)`. No write operations; fully read-only. If `row_count > 10000`: truncate `query_rows` to first 10,000 rows, set `state["truncated"] = True` (the `format_answer_node` adds a caveat about truncation in the answer). **Safety:** the SQLite path is always an absolute path under `data/sessions/` (validated against path traversal in `ingest_csv_node`); the engine URL is never user-controllable.

---

### `format_answer_node`

**Reads from state:** `question`, `query_rows`, `row_count`, `plan_text`, `input_tokens`, `output_tokens`, `token_budget`

**Writes to state:** `answer_text`, `input_tokens`, `output_tokens`

**LLM call:** Yes — one Ollama call; prompt template: `src/prompts/format_answer.md`

**External calls:** Ollama HTTP `POST /api/chat`

| On Failure | Action |
|------------|--------|
| Ollama unreachable / timeout | Set `state["error"]`, route to `handle_error_node` |
| Token budget would be exceeded | Pre-check: if `(input_tokens or 0) + (output_tokens or 0) + estimated_output_tokens >= token_budget`, skip LLM call and use a template-based fallback answer (see below) |

**Behaviour:** Calls Ollama with the question, row sample (first 20 rows as text), row count, and plan summary. System prompt instructs the LLM to produce a structured plain-text answer in the following format:

```
<direct-answer>
[One sentence answering the user's question directly]
</direct-answer>

<key-figures>
• Figure 1: [value + description]
• Figure 2: [value + description]
</key-figures>

<caveats>
[Any limitations: sample size, NULL values, time range, truncation, etc.]
</caveats>

<row-count>[N rows returned from query]</row-count>
```

If `query_rows` is empty: the node does NOT call the LLM (zero-token path); it returns a template answer: `"No matching records were found for this question.\n\n<row-count>0</row-count>"`. If the token budget is near-exhausted (pre-check), the node returns a template: `"<direct-answer>[Answer exceeds token budget — please ask a more specific question]</direct-answer>\n\n<row-count>{row_count}</row-count>\n<caveats>Answer was truncated due to token budget limits ({token_budget} tokens). Try narrowing your question.</caveats>"`. These template paths ensure the user always gets a coherent answer even when the LLM is unavailable or the budget is tight.

After the LLM call returns (or template is used), the node updates `input_tokens` and `output_tokens` by adding the current call's token counts (from Ollama response `prompt_eval_count` / `eval_count`). Duration tracking is added at the graph-run level in `runner.py`.

---

### `handle_error_node`

**Reads from state:** `run_id`, `error`, `sql_error`, `status`

**Writes to state:** `status = "failed"`

**LLM call:** No

**External calls:** PostgreSQL — update `runs` row: `status="failed"`, `error_message=(error or sql_error)`, `completed_at=NOW()`

**Behaviour:** This terminal node is reached from any error edge. It does not call the LLM. It opens a DB session, loads the `RunRow` by `run_id`, sets `status="failed"` and `error_message` to `state["error"]` (fatal) or `state["sql_error"]` (SQL error chain exhaustion). Logs the failure with `run_id` context. Returns `{"status": "failed"}`. The graph terminates here.

---

### `finalize_node`

**Reads from state:** all fields

**Writes to state:** `status = "completed"`, `duration_ms`

**LLM call:** No

**External calls:** PostgreSQL — update `runs` row with all final state fields

**Behaviour:** Opens DB session, loads the `RunRow`, populates: `status="completed"`, `output_text=answer_text`, `sql_text=sql_text`, `provider=ollama`, `model=settings.ollama_model()`, `row_count=row_count`, `input_tokens=input_tokens`, `output_tokens=output_tokens`, `duration_ms=duration_ms`, `datasource=datasource`, `completed_at=NOW()`. Logs a structured completion event via `get_logger("finalize")`. Returns `{"status": "completed"}`. Graph terminates.

---

## Graph / Flow Topology

```
START
 │
 ▼
[ingest_csv_node] ──(sqlite_error set)──► [handle_error_node] ──► END
 │  (skipped if csv_bytes is None; edge jumps to plan_node)
 ▼
[plan_node] ──(error set)──► [handle_error_node] ──► END
 ▼
[generate_sql_node] ──(error set)──► [handle_error_node] ──► END
 ▼
[execute_query_node] ──(sql_error set AND sql_attempts < 3)──► [generate_sql_node]  (retry edge)
 │                    ──(sql_error set AND sql_attempts >= 3)──► [handle_error_node]
 ▼
[format_answer_node] ──(error set)──► [handle_error_node] ──► END
 ▼
[finalize_node] ──► END
```

**Conditional edges:**

| Source node | Condition | Target |
|-------------|-----------|--------|
| `START` | `state["csv_bytes"] is not None` | `ingest_csv_node` |
| `START` | `state["csv_bytes"] is None` | `plan_node` |
| `ingest_csv_node` | `state.get("sqlite_error") is not None` | `handle_error_node` |
| `ingest_csv_node` | `state.get("sqlite_error") is None` | `plan_node` |
| `plan_node` | `state.get("error") is not None` | `handle_error_node` |
| `plan_node` | `state.get("error") is None` | `generate_sql_node` |
| `generate_sql_node` | `state.get("error") is not None` | `handle_error_node` |
| `generate_sql_node` | `state.get("error") is None` | `execute_query_node` |
| `execute_query_node` | `state.get("sql_error") is not None` AND `state["sql_attempts"] < 3` | `generate_sql_node` |
| `execute_query_node` | `state.get("sql_error") is not None` AND `state["sql_attempts"] >= 3` | `handle_error_node` |
| `execute_query_node` | `state.get("sql_error") is None` | `format_answer_node` |
| `format_answer_node` | `state.get("error") is not None` | `handle_error_node` |
| `format_answer_node` | `state.get("error") is None` | `finalize_node` |

---

## Memory & Context

| Scope | Mechanism | What is stored |
|-------|-----------|----------------|
| **Within a run** | LangGraph TypedDict state (in-memory, passed to `invoke()`) | All intermediate values: plan, SQL, rows, answer, error |
| **Across runs (audit)** | PostgreSQL `runs` table + `run_sessions` table (Phase 2) | Full run record: question, SQL, answer, timing, tokens, status, error |
| **CSV cache** | SQLite file at `data/sessions/{run_id}.db` | Ingested CSV data; lifetime = session; destroyed on session expiry |
| **Across turns (Phase 2)** | `conversation_history` field in AgentState + DB persistence | Last N Q&A pairs; fed back into `plan_node` as conversation context |

**Context window management:** The Ollama model's own context window is the hard limit (e.g., llama3.1 8B has 8 192 tokens). The agent manages context by:
- Sending `schema_text` (inferred column names + types — typically < 2 000 tokens) to every LLM node, not the raw CSV.
- Sending only the first 20 rows of `query_rows` to `format_answer_node` (as a text sample), not the full result set.
- Tracking `input_tokens` + `output_tokens` cumulatively in `state`; aborting before the Ollama call if the budget would be exceeded (using `prompt_eval_count` from the previous call to estimate the next one).
- Phase 2 adds `conversation_history` as the last N turns (rolled summary for turns > 10 back), keeping total prompt tokens under budget.

---

## Human-in-the-Loop Checkpoints

> **Assumed:** No human-in-the-loop checkpoints are required for Phase 1 or Phase 2. The system operates under a single shared deployment on an internal network; access control is at the network level. The audit trail (every SQL + every answer) provides the accountability surface. Human review of generated SQL before execution is deliberately not required — it would break the agent's purpose (self-service Q&A). HITL may be added in a future phase targeting high-stakes queries (e.g., queries that return fewer than 5 rows but are flagged as critical incident searches), following the pattern in `harness/patterns/agentic-ai.md` §13.

---

## Error Handling & Recovery

**Node-level:** Every node wraps its body in a `try/except` block. Expected exceptions (HTTP errors from Ollama, SQLAlchemy DB errors, pandas CSV parse errors) are caught explicitly; unexpected exceptions are also caught and surfaced as `state["error"]`. No exception propagates out of a node function.

**Graph-level (`handle_error_node`):** Reads `run_id`, `error`, and/or `sql_error` from state; persists a `RunRow` with `status="failed"` and the error message; logs with `get_logger("handle_error")`. Terminates the graph.

**Partial failure:**
- Ollama LLM call fails mid-pipeline (e.g., in `format_answer_node`): the run is marked `failed`; the SQL and rows are already persisted in the audit row (stored earlier in the run by `execute_query_node` via partial state — see `runner.py`); the user can see what SQL was executed and what rows were returned, even though the answer text is absent.
- Ollama is down from the start (in `plan_node`): no DB writes occur until `finalize_node`; the run is marked `failed`; no partial audit row is created (clean failure).
- SQLite file is deleted mid-run after ingest (rare, e.g., disk clean): `execute_query_node` catches `OperationalError`, routes back to `generate_sql_node` with `sql_error`; after 3 retries, `handle_error_node`. No data loss; the CSV upload record is kept in the PostgreSQL run row.

**Resume / retry strategy:** A failed run cannot be resumed in-place (graph execution is stateless between runs). The user re-submits the question (and re-uploads the CSV if needed). The audit trail preserves the SQL and rows from the failed run for inspection. Phase 2 adds per-session checkpointing via PostgreSQL so that a session can resume from the last successful step.

---

## Observability

| Signal | What | Where |
|--------|------|-------|
| **Trace** | One structlog `log_span` per run (`agent_run`); one sub-span per node (`node:ingest_csv`, `node:plan`, etc.) | stdout (structlog JSON) |
| **LLM calls** | Per-call: `prompt_tokens`, `completion_tokens`, `latency_ms`, `model`, `provider` (always "ollama") | Structured log line `llm_call` per node + stored in PostgreSQL `runs` row |
| **SQL execution** | `sql_text` (full SQL), `row_count`, `sql_error`, `datasource`, `sql_attempts` | PostgreSQL `runs` row (full audit) |
| **Run outcome** | `status`, `duration_ms`, `input_tokens`, `output_tokens`, `error_message` | PostgreSQL `runs` row + structured log |
| **Token budget** | Cumulative `input_tokens + output_tokens` per run | In state; logged at finalize; Phase 2 adds per-session budget counter |

All log events include `run_id` as a structured field (`log.bind(run_id=run_id)`). No PII, secrets, or full answer text are written to logs (only a truncated first-100-char excerpt of `answer_text` is logged for traceability).

---

## Concurrency Model

- **Run isolation:** Runs are serialised at the DB session level (one DB session per run in `create_db_session()` context manager). Concurrent runs on separate `run_id` values are safe — each has its own SQLite file path and its own DB row. The SQLite file path includes `run_id`, preventing collisions. **No queue is needed in Phase 1** (expected throughput is low: one operator = one query at a time, typical response takes 5–30 seconds). Phase 2 may add a lightweight asyncio queue if MSSQL mirror sync is long-running.
- **Parallel nodes within a run:** No parallel nodes in Phase 1 (sequential ReAct chain is sufficient latency). Phase 2 may parallelise `execute_accelerated_query` + `execute_fallback_query` (MV vs live) with `AND`-merge semantics if both complete within the token budget.
- **Checkpointing:** No checkpointing in Phase 1 (runs are short: <60 seconds). Phase 2 adds `PostgresSaver` checkpointing when human-in-the-loop or long-running ETL jobs are introduced. The Phase 1 graph is compiled without a checkpointer (`checkpointer=None`).

---

## Graph Assembly (`src/graph/agent.py`)

> **≤ 60 lines in the real implementation.** Pseudocode below shows the wiring contract.

```python
from langgraph.graph import StateGraph, END
from src.graph.state import AgentState
from src.graph.nodes import (
    ingest_csv_node,
    plan_node,
    generate_sql_node,
    execute_query_node,
    format_answer_node,
    handle_error_node,
    finalize_node,
)


def _route_from_start(state: AgentState) -> str:
    return "ingest_csv" if state.get("csv_bytes") is not None else "plan"


def _route_from_ingest(state: AgentState) -> str:
    return "handle_error" if state.get("sqlite_error") else "plan"


def _route_from_plan(state: AgentState) -> str:
    return "handle_error" if state.get("error") else "generate_sql"


def _route_from_generate_sql(state: AgentState) -> str:
    return "handle_error" if state.get("error") else "execute_query"


def _route_from_execute(state: AgentState) -> str:
    if state.get("sql_error"):
        return (
            "handle_error"
            if state.get("sql_attempts", 0) >= 3
            else "generate_sql"
        )
    return "format_answer"


def _route_from_format_answer(state: AgentState) -> str:
    return "handle_error" if state.get("error") else "finalize"


def build_agent() -> StateGraph:
    g = StateGraph(AgentState)

    g.add_node("ingest_csv", ingest_csv_node)
    g.add_node("plan", plan_node)
    g.add_node("generate_sql", generate_sql_node)
    g.add_node("execute_query", execute_query_node)
    g.add_node("format_answer", format_answer_node)
    g.add_node("handle_error", handle_error_node)
    g.add_node("finalize", finalize_node)

    g.set_conditional_entry_point(_route_from_start)

    g.add_conditional_edges("ingest_csv", _route_from_ingest)
    g.add_edge("plan", "generate_sql")
    g.add_conditional_edges("generate_sql", _route_from_generate_sql)
    g.add_conditional_edges("execute_query", _route_from_execute)
    g.add_conditional_edges("format_answer", _route_from_format_answer)
    g.add_edge("handle_error", END)
    g.add_edge("finalize", END)

    return g.compile(checkpointer=None)  # Phase 1: no checkpointing


# Module-level compiled graph — singleton, created once at import time.
agentic_ai: StateGraph = build_agent()
```

**Assembly constraints:**
- The compiled graph is a **module-level singleton** in `src/graph/agent.py`. It is compiled once at import time and reused by all `run_agent()` calls (see `src/graph/runner.py`).
- The graph is **stateless between runs**: `runner.py` creates a fresh `AgentState` dict per invocation and passes it to `agentic_ai.invoke(initial)`.
- `checkpointer=None` in Phase 1; Phase 2 adds `PostgresSaver` when human-in-the-loop and session-resume features require it.
- No dynamic node addition at runtime — the graph shape is fixed after compilation.
