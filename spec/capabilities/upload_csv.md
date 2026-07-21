# Capability: Upload CSV

## What It Does
Accepts a CSV multipart upload from the user, stores the file as a per-session SQLite database, and returns a schema summary (column names + inferred types + row count) ready for query.

## Inputs

| Input | Type | Source | Required |
|-------|------|--------|----------|
| `csv_bytes` | `bytes` | Multipart file upload from frontend | Yes (when user uploads) |
| `run_id` | `str` (UUID) | `runner.py` — set at graph entry | Yes |
| `token_budget` | `int` | `AGENT_TOKEN_BUDGET` env var (default 8 192) | No (defaulted) |
| `data_dir` | `str` | `settings.data_dir` (default `"data/sessions"`) | No (defaulted) |

## Outputs

| Output | Type | Destination |
|--------|------|-------------|
| `sqlite_path` | `str` (absolute path) | `AgentState` — used by `execute_query_node` |
| `schema_text` | `str` | `AgentState` — used by `plan_node`, shown in UI |
| `row_count_csv` | `int` | `AgentState` — shown in UI |
| `sqlite_error` | `str \| None` | `AgentState` — on failure, routes to `handle_error_node` |

## External Calls

| System | Operation | On Failure |
|--------|-----------|------------|
| SQLite (filesystem) | Create `data/sessions/{run_id}.db`, then `df.to_sql("data", if_exists="replace")` | Set `sqlite_error` in state; route to `handle_error_node` |
| pandas `read_csv` | Parse CSV bytes with `chunksize=10 000` for files > 10 MB | Catch `pandas.errors.ParserError`; set `sqlite_error`; route to `handle_error_node` |

## Business Rules

- Maximum CSV file size: **50 MB**. Files exceeding this limit produce `sqlite_error = "CSV file exceeds 50 MB limit"` and terminate the run with `status="failed"`.
- All columns are **nullable** — no column is marked `NOT NULL` during inference.
- Type inference order (first match wins): `INTEGER` (parsable as int, no NaN), `REAL` (parsable as float), `TEXT` (fallback for all other values).
- The SQLite table is always named `data` (single-table schema for Phase 1).
- `sqlite_path` is always an absolute path under `data/sessions/`; any path-traversal in the `run_id` is rejected before filesystem access.
- The file is **not deleted** at the end of the run in Phase 1 (cleanup is operator-responsible; Phase 2 adds TTL expiry).
- CSV files with `#` comment lines, BOM-encoded UTF-8, and Windows-style line endings (`\r\n`) are all handled by pandas defaults.

## Success Criteria

- [ ] Uploading a 10-row, 5-column CSV (text fixture `tests/fixtures/fir_sample.csv`) produces a SQLite file and `schema_text = "data: id(TEXT) | date(TEXT) | district(TEXT) | incident_type(TEXT) | count(INTEGER)"`
- [ ] `row_count_csv` equals the physical number of data rows in the CSV (excluding header)
- [ ] A >10 MB CSV is processed without `MemoryError` (uses `chunksize=10 000`)
- [ ] A 51 MB CSV returns `sqlite_error` with the 50 MB limit message; the API returns HTTP 200 with `status="failed"`
- [ ] The SQLite file is readable by `execute_query_node` immediately after ingest (no close/re-open race condition)
