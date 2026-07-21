# API

> **Style:** REST (FastAPI), single-origin (frontend served from same host at `/app/`). See `harness/patterns/tech-stack.md` — zero-build static frontend served at `/app/`, port 8001.
> **Assumed:** Authentication is network-level (internal LAN / VPN). No application-level auth in Phase 1 or Phase 2.

---

## Base URL

```
http://localhost:8001   (FastAPI default; overridable via PORT env)
```

Static frontend: `http://localhost:8001/app/`
API prefix: `http://localhost:8001/runs` (flat, no version prefix for Phase 1; `/v1/` prefix deferred pending external API consumers)

---

## Response Envelope

Every API response follows the baseline envelope from `src/api/_common.py`:

```jsonc
// Success (HTTP 200):
{ "ok": true,  "data": { ... } }

// Error (HTTP 4xx/5xx):
{ "ok": false, "error": { "code": "string", "message": "string" } }
```

Agent-run endpoint is a **special case**: it always returns HTTP 200 (even on agent failure). The `status` field in the response body indicates success or failure. This prevents the frontend from treating an LLM-or-DB failure as a transport error and hiding the error message from the user.

---

## Endpoints

### `GET /health`

**Purpose:** Check that the server, database, and Ollama are reachable.

**Response:**
```jsonc
{
  "ok": true,
  "data": {
    "status": "healthy",
    "database": "connected",        // or "disconnected"
    "ollama": "connected",          // or "disconnected" / "stub"
    "ollama_model": "llama3.1:8b-instruct-q4_0",
    "version": "0.1.0"
  }
}
```

**Error cases:**

| Status | Condition |
|--------|-----------|
| 200 | All services reachable (database + Ollama) — `ollama` may be `"stub"` if no key is present (Phase 1 fallback) |
| 503 | Database or Ollama unreachable; `data.database` or `data.ollama` = `"disconnected"` |

> **Assumed:** `GET /health` does NOT echo any secret values (API keys, DB passwords). It reports presence/absence of each service only.

---

### `POST /runs`

**Purpose:** Submit a natural-language question (with optional CSV upload) and receive the agent's answer.

**Request (application/json or multipart/form-data):**

Two supported content types:

**A. JSON body (no CSV upload; re-uses an existing session or queries live DB in Phase 2):**

```jsonc
{
  "question": "How many FIRs were registered in Lucknow in June 2024?",
  "session_id": "optional-uuid-of-existing-session"   // Phase 2: continue a session
}
```

**B. Multipart form-data (CSV upload + question):**

```
csv_upload: <file>          // CSV file, ≤ 50 MB, text/csv or application/octet-stream
question: "How many records are there in this file?"
```

**Request fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `question` | `string` (1–5 000 chars) | Yes | The user's plain-language question |
| `csv_upload` | `UploadFile` | No | Multipart CSV; absent = use existing session / live DB |
| `session_id` | `UUID string` | No | Phase 2 only: continue a prior session |

**Response (HTTP 200, always — even on run failure):**

```jsonc
{
  "ok": true,
  "data": {
    "run_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "completed",            // "running" | "completed" | "failed"
    "question": "How many records?",
    "answer_text": "<direct-answer>\nThere are 1 247 records.\n</direct-answer>\n\n<row-count>1247</row-count>",
    "sql_text": "SELECT COUNT(*) FROM data",
    "row_count": 1247,
    "datasource": "sqlite",
    "provider": "ollama",
    "model": "llama3.1:8b-instruct-q4_0",
    "input_tokens": 612,
    "output_tokens": 89,
    "output_tokens": 89,
    "duration_ms": 8200,
    "error_message": null,
    "created_at": "2024-07-21T10:30:00Z",
    "completed_at": "2024-07-21T10:30:08Z"
  }
}
```

**`status="running"` polling flow (Phase 1):** The initial response after submission may have `status: "running"`. The client polls `GET /runs/{run_id}` until `status` is `"completed"` or `"failed"`. Phase 1 does not use server-sent events or websockets.

**Failed run response:**

```jsonc
{
  "ok": true,
  "data": {
    "run_id": "...",
    "status": "failed",
    "error_message": "Ollama is unreachable — check that Ollama is running and AGENT_OLLAMA_BASE_URL is correct",
    "sql_text": null,
    "answer_text": null
    // ... other fields null or set to partial values
  }
}
```

**Error cases:**

| Status | Condition |
|--------|-----------|
| 400 | `question` is blank or exceeds 5 000 chars; CSV file > 50 MB; CSV is not text/CSV |
| 413 | CSV file > 50 MB |
| 422 | Malformed multipart; missing `question` field |
| 500 | Database write failed (run row could not be created); bug in `runner.py`; surfaced as `error_message` in a 200 response instead (agentic envelope) |

> **Assumed:** No authentication headers are required in Phase 1 — the API is reachable from the local deployment network only. Phase 2 adds optional token-based auth if required.

---

### `GET /runs/{run_id}`

**Purpose:** Poll for a running run or retrieve a completed run's full audit record.

**Response (200):** Same shape as `POST /runs` response body.

**Error cases:**

| Status | Condition |
|--------|-----------|
| 404 | `run_id` not found in database |
| 500 | Database query failed |

---

### `GET /runs`

**Purpose:** List recent runs (Phase 2: filtered by `session_id`).

**Query params (Phase 1 minimal):**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max results (1–200) |
| `offset` | int | 0 | Pagination offset |
| `session_id` | UUID | — | Phase 2 only: filter by session |

**Response (200):**
```jsonc
{
  "ok": true,
  "data": {
    "items": [ { /* RunResult */ }, ... ],
    "total": 1247
  }
}
```

**Error cases:**

| Status | Condition |
|--------|-----------|
| 422 | `limit > 200` or negative |

---

### `GET /health` (already documented above)

**Purpose:** Service health check. Returns database + Ollama connectivity status.

---

## Content-Type Summary

| Endpoint | Accepts | Returns |
|----------|---------|---------|
| `GET /health` | — | `application/json` |
| `POST /runs` | `application/json` OR `multipart/form-data` | `application/json` |
| `GET /runs` | — | `application/json` |
| `GET /runs/{run_id}` | — | `application/json` |
| `GET /app/` (static frontend) | — | `text/html` |

---

## Phase 2 Additions (deferred; documented here for completeness)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/sessions` | Create a named conversation session (groups related runs) |
| `GET` | `/sessions/{session_id}` | Retrieve session status + data source + last activity |
| `DELETE` | `/sessions/{session_id}` | End a session (triggers SQLite cache cleanup) |
| `POST` | `/mirror/refresh` | Trigger an on-demand MSSQL mirror refresh (operator action) |
| `GET` | `/mirror/status` | Check mirror staleness (last refresh timestamp, row counts) |
| `POST` | `/budget` | Set or reset the token budget for a session |

These endpoints are stubbed in the Phase 1 frontend (disabled, labelled *"Phase 2"*) but not implemented in Phase 1.
