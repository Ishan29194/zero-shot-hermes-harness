# Capability: Format Answer

## What It Does
Converts the structured query result (rows, row count, plan) into a plain-text, structured-format answer for the user — using the LLM when budget permits, or a template fallback when it does not.

## Inputs

| Input | Type | Source | Required |
|-------|------|--------|----------|
| `question` | `str` | User's question (state) | Yes |
| `query_rows` | `list[dict]` | `execute_query_node` output | Yes |
| `row_count` | `int` | `execute_query_node` output | Yes |
| `plan_text` | `str \| None` | `plan_node` output (provides context for summarisation) | No |
| `input_tokens` / `output_tokens` | `int \| None` | Cumulative counters in state | No |
| `token_budget` | `int` | Env var `AGENT_TOKEN_BUDGET` (default 8 192) | No |
| `AGENT_LLM_MAX_TOKENS` | `int` | Env var (default 512 for answer node) | No |

## Outputs

| Output | Type | Destination |
|--------|------|-------------|
| `answer_text` | `str` | Returned to user via API; persisted to `runs.output_text` |
| `input_tokens` | `int` | `AgentState` — cumulative across all LLM calls in run |
| `output_tokens` | `int` | `AgentState` — cumulative across all LLM calls in run |
| `error` | `str \| None` | `AgentState` — on Ollama failure, routes to `handle_error_node` |

## External Calls

| System | Operation | On Failure |
|--------|-----------|------------|
| Ollama HTTP `POST /api/chat` | Generate structured plain-text answer from rows | Retried by LLM retry decorator (max 2 HTTP-level retries); on final LLM failure set `state["error"]`, route to `handle_error_node` |

## Business Rules

- **Output format:** The answer is structured XML-tagged plain text. Tags are `<direct-answer>`, `<key-figures>`, `<caveats>`, `<row-count>`. The frontend parser renders these tags as distinct visual blocks. No other tags are produced by the LLM; if the LLM outputs additional tags, the frontend treats them as plain text.
- **Empty results path (zero-LLM):** If `query_rows` is empty (`row_count == 0`), the node does NOT call the LLM. It returns a template answer: `"<direct-answer>\nNo matching records were found for this question.\n</direct-answer>\n\n<row-count>0</row-count>"`. This avoids a wasted LLM call and ensures a coherent answer in all cases.
- **Token budget pre-check (graceful degradation):** Before calling the LLM, the node estimates the next call's total prompt tokens. If `input_tokens + output_tokens + estimated_output_tokens >= token_budget`, the LLM call is skipped and a template answer is returned: `"<direct-answer>\n[Answer exceeds token budget — please ask a more specific question]\n</direct-answer>\n\n<row-count>{row_count}</row-count>\n<caveats>Answer was truncated due to token budget limits ({token_budget} tokens). Try narrowing your question.</caveats>"`. This ensures users always get an actionable answer even under budget pressure.
- **LLM prompt content:** The prompt sent to Ollama includes: (a) the original question, (b) a text representation of the first 20 rows of `query_rows` (or all rows if fewer than 20), (c) `row_count`, and (d) `plan_text` for context. The prompt is bounded to fit within the model's context window — long `query_rows` are truncated at the row level.
- **Row sample truncation for LLM prompt:** If `query_rows` has `N > 20` rows, only the first 20 rows are sent to the LLM (the LLM produces a summary, not a per-row listing). `row_count` in the prompt reflects the true count. The `caveats` block always includes a sentence like "Based on the first 20 of {row_count} rows shown to the summarisation step."
- **Token accounting:** After the Ollama call returns, the node reads `prompt_eval_count` (input tokens) and `eval_count` (output tokens) from the Ollama response and adds them to the state's cumulative counters. These values are authoritative (from Ollama's own token counter) — not estimated.
- **No markdown in answer:** The LLM's system prompt instructs it to not use markdown formatting (no `**bold**`, no `# headings`, no bulleted lists without the `<key-figures>` tags). Bullet points in `<key-figures>` use `• ` as the bullet character.

## Success Criteria

- [ ] For a query returning `[{"count": 1247}]`, the answer contains `<row-count>1247</row-count>` and a one-sentence direct answer stating "There are 1 247 records"
- [ ] When `query_rows` is empty, `answer_text` is the template string (no Ollama call is made; verify via mock that the LLM client's `complete` method is never called)
- [ ] When `token_budget` is set to 100 and the current cumulative token usage is 90, the LLM call is skipped and the budget-exceeded template answer is returned
- [ ] The LLM's provided system prompt for `format_answer` includes the text of the first 20 rows and the row count (assert via prompt-inspection in the test double)
- [ ] After a successful Ollama call, `input_tokens` and `output_tokens` in state equal the sum of all node LLM calls in the run (assert via stage-instrumented test)
