"""Runs API — POST /runs executes the agent; GET /runs/{id} fetches a run."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Form, Request
from sqlalchemy.orm import Session

from src.api._common import api_error, ok
from src.db.models import RunRow
from src.db.session import get_session
from src.domain.run import RunResult
from src.graph.runner import run_agent

router = APIRouter()


def _to_result(run: RunRow) -> RunResult:
    return RunResult(
        run_id=run.id,
        status=run.status,
        question=run.question or "",
        answer_text=run.output_text,
        sql_text=run.sql_text,
        row_count=run.row_count,
        provider=run.provider,
        model=run.model,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        duration_ms=run.duration_ms,
        error_message=run.error_message,
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.post("/runs")
async def create_run(
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    content_type = request.headers.get("content-type") or ""

    if content_type.startswith("multipart/form-data") or content_type.startswith("application/x-www-form-urlencoded"):
        form = await request.form()
        question = (form.get("question") or "").strip()
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        raw = body.get("question") or body.get("text") or ""
        question = str(raw).strip()
    if not question:
        raise api_error("validation_error", "question is required", 422)

    run_id = run_agent(question, None)
    run = session.get(RunRow, run_id)
    if run is None:
        raise api_error("run_not_found", f"run {run_id} vanished", 500)
    return ok(_to_result(run).model_dump())


@router.get("/runs/{run_id}")
def get_run(run_id: str, session: Session = Depends(get_session)) -> dict:
    run = session.get(RunRow, run_id)
    if run is None:
        raise api_error("run_not_found", f"no run with id {run_id}", 404)
    return ok(_to_result(run).model_dump())


@router.get("/runs")
def list_runs(
    limit: int = 50,
    offset: int = 0,
    session: Session = Depends(get_session),
) -> dict:
    limit = max(1, min(limit, 200))
    rows = (
        session.query(RunRow)
        .order_by(RunRow.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    total = session.query(RunRow).count()
    return ok({"items": [_to_result(r).model_dump() for r in rows], "total": total})
