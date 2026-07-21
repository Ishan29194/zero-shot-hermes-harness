# Capabilities Index

## What Is a Capability?

A capability is a single, discrete node-level action or behavior the agent performs (corresponding to a node in the LangGraph graph). Each capability file documents one node: what it reads from state, the LLM or DB calls it makes, the state it writes, and its failure behaviour.

## Capabilities in This Project

| Capability | File |
|-----------|------|
| Upload CSV — ingest a CSV file into a per-session SQLite database | [upload_csv.md](upload_csv.md) |
| Generate Plan — produce a structured analysis plan via the LLM | [generate_plan.md](generate_plan.md) |
| Generate SQL — produce a safe SELECT statement via the LLM with SQL-error retry loop | [generate_sql.md](generate_sql.md) |
| Execute Query — run the SQL against SQLite (Phase 1) or PostgreSQL mirror (Phase 2) | [execute_query.md](execute_query.md) |
| Format Answer — convert query results into structured plain-text via the LLM | [format_answer.md](format_answer.md) |
| Handle Error — terminal error sink; persist failure to audit DB | [handle_error.md](handle_error.md) |

## How to Add a New Capability

Run `/zero-shot-build [description]` on the existing spec. The spec-writer sub-agent will:
1. Create a new file in this directory (`<name>.md`, no number prefix)
2. Update this index
3. Flag any dependencies on existing capabilities
4. Self-review that it fits the architecture and data model before returning
