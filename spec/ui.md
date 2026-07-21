# UI

> **Tech:** Zero-build static HTML/CSS/JS in `frontend/public/`. No bundler, no Node runtime. Served by FastAPI at `/app/` (single-origin per `harness/patterns/tech-stack.md`). See `harness/patterns/ui-ux.md` for baseline frontend conventions.
> **Assumed:** The deployment is on an internal police network; no user-facing login page. Users access the UI directly via the internal URL. Error states must be actionable and reference `.env` or operator documentation (never expose stack traces).

---

## UI Type

**Web dashboard** — single-page application (SPA) with vanilla JS; no routing library. The page has three logical zones: an upload/question form (top), the answer display (centre), and an audit sidebar (right). On narrow viewports the sidebar collapses below the answer area.

---

## Views / Screens

### Screen: Main Workspace (the only screen — `/app/`)

**Purpose:** Upload a CSV (Phase 1) or select a live data source (Phase 2), ask plain-language questions, read answers, and inspect the audit trail.

**Key elements (Phase 1 — real):**

- **Header bar:** Title `"UP Police Data Analyst"`, version label, and a status indicator showing Ollama connectivity (green = connected, red = unreachable, with an actionable hint: *"Check Ollama is running on AGENT_OLLAMA_BASE_URL"*).
- **Data source section (top):**
  - Dropzone + file picker for CSV upload. Shows file name, row count after ingest, and a "Remove / re-upload" button.
  - Schema summary box: lists column names and inferred types (TEXT / INTEGER / REAL) after ingest. Hidden when no CSV is loaded.
  - Phase 2 stub: a "Data source" toggle showing `"Uploaded CSV"` (selected) and `"Live DB (MSSQL Mirror)"` (disabled, greyed, labelled *"Phase 2"*).
- **Question form (centre, below data source):**
  - Text input / `<textarea>` (max 5 000 chars, client-side enforced) for the natural-language question.
  - "Ask" button; on submit the button shows a **progress indicator** (animated bar or spinner) with a **live timer** ("Question processed in 4.2 s...").
  - Character counter: "N / 5 000 chars".
- **Answer display (centre, below question form):**
  - Rendered as structured text, NOT raw JSON. The answer parser looks for `<direct-answer>`, `<key-figures>`, `<caveats>`, `<row-count>` tags and renders them as visually distinct blocks (bold header for direct answer, bullet list for figures, italic preface for caveats, pill/badge for row-count).
  - If `answer_text` is `null` (run failed): an error block with `error_message` in red and a "Retry" button (re-submits the same question).
  - Phase 1 stub: a **chart placeholder** showing `<chart type="bar" metric="COUNT(*)" />` as a styled grey box with the label *"Chart rendering — Phase 2"* — it must be visually distinct from a broken image or empty space.
- **Audit sidebar (right, or below on narrow screens):**
  - Scrollable list of past Q&A pairs for the current session (last 20 runs by default). Each item shows: question (truncated to 80 chars), row count badge, time elapsed, and a "View SQL" toggle (expands to show the executed SQL in a code block).
  - "Clear history" button (clears the client-side display; does not delete DB records — labelled as a client-side action).
  - Phase 1 stub: a "Session history" expander labelled *"Full history saved in database — Phase 2"* that currently shows a count only.
- **Budget indicator (footer of sidebar):**
  - Phase 1: shows current session's `input_tokens + output_tokens` vs. the session token budget (default 8 192). Shows a warning when > 80% of budget used (amber), and an error state when exceeded (red + message).
  - Phase 2 stub: the same indicator is live and accurate.

**Key elements (Phase 2 — enabled when live DB is active):**

- Data source toggle: `"Uploaded CSV"` vs `"Live DB (MSSQL Mirror)"`. Selecting "Live DB" reveals a table-browser widget showing available tables/columns from the mirror (read-only). The question form is the same regardless of data source.
- Mirror status indicator: Shows `"Mirror up-to-date as of <timestamp>"` with a refresh button (triggers `POST /mirror/refresh`, operator-only in practice).
- History panel: Up to 100 prior Q&A pairs from the current session; clicking a prior question re-submits it (shows the saved answer).
- Chart rendering: The `<chart>` placeholder is replaced by a simple text-based bar chart (CSS bars, no canvas/SVG) showing the top-N result values.

**Actions available:**

| Action | Effect |
|--------|--------|
| Upload CSV | Triggers `POST /runs` with multipart body; UI shows loading state; on success shows schema and activates the question input |
| Submit question | Triggers `POST /runs` with JSON body; polls `GET /runs/{run_id}` every 2 s until terminal status; renders answer |
| View SQL (toggle) | Expands the SQL text in an expandable code block in the sidebar |
| Clear session | Removes the SQLite cache file client-side reference; does not call an API (CSV data is only referenced by `run_id`; a new upload replaces it) |
| Retry (on failure) | Re-submits the same question using the same session/csv_path |

---

## Error States

| State | UI rendering | User action |
|-------|-------------|-------------|
| **Ollama unreachable** | Red banner at top: *"LLM service (Ollama) is unreachable. Check that Ollama is running and AGENT_OLLAMA_BASE_URL is set correctly in .env."* | Retry button; if repeated, contact operator |
| **CSV upload > 50 MB** | Inline error below the file picker: *"File exceeds the 50 MB limit. Please split the CSV and upload separately."* | Re-upload a smaller file |
| **CSV parse error** | Inline error with the pandas error message: *"Could not read this file: [error]. Please ensure it is a valid CSV."* | Re-upload |
| **SQL error (exhausted retries)** | Red answer block: *"Could not answer this question after 3 SQL attempts. The question may be ambiguous or the data may not contain the information requested."* + full `sql_error` text in a collapsible detail | Re-word the question or try a different CSV |
| **Token budget exceeded** | Amber banner + answer: *"This session's token budget has been reached. Please start a new session or ask a more specific question."* | Start a new session (re-upload CSV or switch data source) |
| **Health check failed (startup)** | Startup overlay (shown while `GET /health` is polling): if it fails after 3 retries, a full-page error: *"Server is starting or unreachable. Wait a moment and refresh [F5]."* | Refresh; if still failing, check the server terminal |
| **Server 500** | Full-page error: *"An unexpected error occurred. The run was not saved. Please try again."* + a "Copy error details" button (copies the console error to clipboard) | Retry; if recurring, contact operator |

**Progress and timer contract:**
- The progress indicator starts at "Planning…" when `status: "running"` is first received.
- It transitions to "Generating SQL…" on the second node's typical duration, then to "Running query…", then "Formatting answer…".
- If the run takes > 15 seconds, a reassuring message appears: "This may take a moment for large datasets."
- The timer shows elapsed wall-clock seconds (updated every 500 ms during polling) and freezes at the final `duration_ms` when the run completes.

**Stub-labelling contract (Phase 1):**
- Every stub surface must have a `title` or `aria-label` attribute beginning with `"STUB: "` followed by the phase where it ships, e.g., `<div title="STUB: Phase 2 – Chart rendering" aria-label="STUB: Phase 2 – Chart rendering">`.
- Stub surfaces must have a visible label on the page (not just in `title`/`aria-label`).
- Stubs must never render as blank space — always show a styled placeholder (grey background, italic text) so users know something is coming.

---

## Tech Stack

**Zero-build static frontend** (per `harness/patterns/tech-stack.md`):
- Single HTML file: `frontend/public/index.html` — semantic HTML5, no `<div>` soup; `<main>`, `<aside>`, `<header>` regions.
- Single CSS file: `frontend/public/styles.css` — CSS custom properties for theme colours; mobile-first responsive layout (flexbox/grid, no media-query breakpoints below 1 200 px).
- Single JS file: `frontend/public/app.js` — vanilla ES modules or a single IIFE; no build step; `fetch()` calls to the `/runs` API; no external CDN dependencies (all assets served from the same origin).
- No Tailwind, no CSS framework, no icon library (SVG inline icons only, hand-authored for the small icon set: upload, question, chart-stub, warning, error).
- Responsive layout: single-column on < 640 px; sidebar stacks below the answer area.
- Accessibility: `<label>` elements for all `<input>` and `<textarea>`; `role="alert"` on error banners; focus management moves to the answer area on run completion.

> **Assumed:** The frontend does NOT adopt a JS framework (no React, Vite, Next.js). The project's UI needs (upload form, question box, answer display, history sidebar) fit comfortably within vanilla HTML/CSS/JS. No client-side routing is needed. If Phase 2 grows to require component reuse or complex state management, a JS framework may be adopted — that decision is deferred to Phase 2 and would require updating the gates to cover the production build step (per `tech-stack.md`).
