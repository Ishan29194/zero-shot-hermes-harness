# Capability: Generate Plan

## What It Does
Calls the LLM to produce a short, structured analysis plan (3–5 steps) from the user's question and the inferred data schema, so that the subsequent SQL-generation step has explicit guidance on which tables and columns to use.

## Inputs

| Input | Type | Source | Required |
|-------|------|--------|----------|
| `question` | `str` | User's plain-language question (from `POST /runs` body) | Yes |
| `schema_text` | `str` | Inferred by `ingest_csv_node`; shown in UI | Yes |
| `instruction` | `str` | System prompt template (`src/prompts/plan.md`) | Yes |

## Outputs

| Output | Type | Destination |
|--------|------|-------------|
| `plan_text` | `str` | `AgentState` — consumed by `generate_sql_node` |
| `error` | `str \| None` | `AgentState` — on fatal LLM failure, routes to `handle_error_node` |

## External Calls

| System | Operation | On Failure |
|--------|-----------|------------|
| Ollama HTTP `POST /api/chat` | Generate a JSON-array plan from question + schema | Retry max 2× with back-off; set `state["error"]` and route to `handle_error_node` if still failing |

## Business Rules

- The plan is a JSON array of 3–5 strings — each string is a single sentence identifying a table name, column name, or filter condition.
- Only columns present in `schema_text` may appear in the plan (the LLM is instructed to validate against schema — the agent does not enforce this programmatically in Phase 1; `execute_query_node` will catch invalid column names at query time).
- The plan generation call is capped at `AGENT_LLM_MAX_TOKENS` for output (default 512 tokens, sufficient for 5 short plan steps).
- If the LLM returns non-JSON text, the node retries once with a stricter system prompt ("Respond with ONLY a JSON array, no other text.").
- On second JSON-parse failure: set `state["error"] = "Plan generation produced malformed output after retry"`, route to `handle_error_node`.

## Success Criteria

- [ ] For the question "How many records are there?" on a schema with a single table `data` (5 columns), the plan is `["Use the data table", "Apply COUNT(*) aggregation", "Return the total count"]` (order and wording may vary but contains 3 steps and references the `data` table)
- [ ] The node completes without an LLM call when Ollama is stubbed (tests use `no_keys` fixture)
- [ ] On malformed LLM output (simulated via test double), the node retries once then sets `state["error"]` (test asserts `state["error"]` is set)
