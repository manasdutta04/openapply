from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import desc, select

from memory.db import Application, Evaluation, Job, build_session_factory, create_sqlite_engine, initialize_database


SortBy = Literal["score", "date", "company"]
StatusFilter = Literal["all", "evaluated", "applied", "interview", "top", "skipped"]


@dataclass(slots=True)
class TrackerRow:
    job_id: int
    company: str
    role: str
    score: float | None
    grade: str | None
    status: str
    date: datetime
    report_path: str | None
    url: str


def fetch_rows(sort_by: SortBy) -> list[TrackerRow]:
    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        jobs = session.scalars(select(Job).order_by(desc(Job.scraped_at))).all()
        rows: list[TrackerRow] = []

        for job in jobs:
            eval_row = session.scalars(
                select(Evaluation).where(Evaluation.job_id == job.id).order_by(desc(Evaluation.id)).limit(1)
            ).first()

            app_row = session.scalars(
                select(Application).where(Application.job_id == job.id).order_by(desc(Application.id)).limit(1)
            ).first()

            effective_status = app_row.outcome if app_row is not None else job.status
            score = eval_row.score_total if eval_row is not None else None
            grade_value = eval_row.grade if eval_row is not None else None
            report_path = eval_row.report_path if eval_row is not None else None

            date_value = job.scraped_at.replace(tzinfo=None) if job.scraped_at.tzinfo else job.scraped_at

            rows.append(
                TrackerRow(
                    job_id=job.id,
                    company=job.company or "Unknown",
                    role=job.role or "Unknown",
                    score=score,
                    grade=grade_value,
                    status=effective_status,
                    date=date_value,
                    report_path=report_path,
                    url=job.url,
                )
            )

    if sort_by == "score":
        rows.sort(key=lambda row: row.score if row.score is not None else -1.0, reverse=True)
    elif sort_by == "company":
        rows.sort(key=lambda row: row.company.lower())
    else:
        rows.sort(key=lambda row: row.date, reverse=True)

    return rows


def apply_filter(rows: list[TrackerRow], tab: StatusFilter, grade: str | None, status: str | None) -> list[TrackerRow]:
    out: list[TrackerRow] = []
    for row in rows:
        if grade and (row.grade or "").upper() != grade.upper():
            continue
        if status and row.status.lower() != status.lower():
            continue

        if tab == "all":
            out.append(row)
        elif tab == "evaluated":
            if row.status in {"evaluated", "new"}:
                out.append(row)
        elif tab == "applied":
            if row.status == "applied":
                out.append(row)
        elif tab == "interview":
            if row.status == "interview":
                out.append(row)
        elif tab == "skipped":
            if row.status == "skipped":
                out.append(row)
        elif tab == "top":
            if (row.score or 0.0) >= 4.0:
                out.append(row)

    return out

