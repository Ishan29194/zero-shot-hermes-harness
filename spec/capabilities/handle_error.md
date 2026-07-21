# Capability: Handle Error

## What It Does
Acts as the graph's terminal error node — reads the terminal error from state, persists it to the PostgreSQL audit `runs` row, logs a structured failure event, and terminates the graph cleanly.

## Inputs

| Input | Type | Source | Required |
|-------|------|--------|----------|
| `run_id` | `str` | Graph entry point | Yes |
| `error` | `str \| None` | Terminal fatal error set by any upstream node | Yes (one of `error` or `sql_error` is non-None) |
| `sql_error` | `str \| None` | SQL error text from `execute_query_node` (retry exhaustion path) | Yes (alternative to `error`) |
| `status` | `str \| None` | Prior status in state (`"running"`) | Yes |

## Outputs

| Output | Type | Destination |
|--------|------|-------------|
| `status` | `"failed"` | `AgentState` — terminal value; returned to `runner.py` for API response |
| (DB side-effect) | — | PostgreSQL `runs` row updated: `status="failed"`, `error_message=...`, `completed_at=NOW()` |

## External Calls

| System | Operation | On Failure |
|--------|-----------|------------|
| PostgreSQL | `UPDATE runs SET status='failed', error_message=..., completed_at=NOW() WHERE id=run_id` | Catch `SQLAlchemyError`; log and surface `"Audit DB write failed — run may not be recorded"` |

## Business Rules

- The error message surfaced to the user is taken from `state["error"]` (fatal error, e.g., Ollama unreachable) if it is present; otherwise `state["sql_error"]` is used (SQL retry exhaustion). If both are `None` (should not occur), the node uses `"Unknown error — run terminated unexpectedly"`.
- The error message is **user-facing** — it must be plain language, not a stack trace or raw exception class name. For Ollama errors: `"Ollama is unreachable — check that Ollama is running and AGENT_OLLAMA_BASE_URL is set correctly in .env."` For SQL errors: `"Could not answer this question after 3 SQL attempts. The question may be ambiguous or the data may not contain the information requested."`
- The node does NOT attempt to retry or recover — it is a terminal sink. Any retry logic lives upstream (the `execute_query_node` → `generate_sql_node` edge).
- On DB update failure (PostgreSQL unreachable during the write), the node logs the failure but does not set another `error` in state (to avoid a second routing loop). The run row remains with its prior `status="running"` (an operator-visible inconsistency that requires manual reconciliation).

## Success Criteria

- [ ] When `state["error"]` is set by `plan_node` ("Ollama unreachable"), the `runs` row shows `status="failed"` and `error_message` matches the user-facing Ollama error text
- [ ] When `sql_error` is set by `execute_query_node` *and* `sql_attempts >= 3`, the `runs` row shows `status="failed"` with the SQL error text as `error_message`
- [ ] When the PostgreSQL UPDATE itself fails (simulated by closing the DB mid-run), the node logs the failure and returns without infinite recursion (assert that `runner.py` completes without raising)
- [ ] The node is reached in < 500 ms after the upstream failure (not a long-running node — no LLM calls)
