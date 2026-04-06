from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import and_, desc, func, select

from memory.db import (
    Application,
    Evaluation,
    Job,
    Outcome,
    ScoringWeight,
    build_session_factory,
    create_sqlite_engine,
    initialize_database,
)

console = Console()

OutcomeType = Literal["interview", "rejected", "offer", "ghosted"]


@dataclass(slots=True)
class Adjustment:
    dimension: str
    old_weight: float
    new_weight: float


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        n = len(weights)
        return {k: 1.0 / n for k in weights}
    return {k: v / total for k, v in weights.items()}


def _top_bottom_dimensions(evaluation: Evaluation) -> tuple[list[str], list[str]]:
    score_map = {
        "role_match": float(evaluation.score_role_match),
        "skills_alignment": float(evaluation.score_skills),
        "seniority_fit": float(evaluation.score_seniority),
        "compensation": float(evaluation.score_compensation),
        "geographic": float(evaluation.score_geographic),
        "company_stage": float(evaluation.score_company_stage),
        "product_market_fit": float(evaluation.score_pmf),
        "growth_trajectory": float(evaluation.score_growth),
        "interview_likelihood": float(evaluation.score_interview_likelihood),
        "timeline": float(evaluation.score_timeline),
    }
    ranked = sorted(score_map.items(), key=lambda item: item[1], reverse=True)
    top = [ranked[0][0], ranked[1][0]]
    bottom = [ranked[-1][0], ranked[-2][0]]
    return top, bottom


def _collect_weight_adjustments(
    current_weights: dict[str, float],
    evaluation: Evaluation,
    outcome: OutcomeType,
    bonus_company_stage: bool,
) -> dict[str, float]:
    updated = dict(current_weights)
    top_dims, bottom_dims = _top_bottom_dimensions(evaluation)

    if outcome in {"interview", "offer"}:
        base_delta = 0.015 if outcome == "offer" else 0.01
        for dim in top_dims:
            updated[dim] = max(0.01, updated[dim] + base_delta)

        if bonus_company_stage:
            updated["company_stage"] = max(0.01, updated["company_stage"] + 0.01)
    else:
        base_delta = 0.01
        for dim in bottom_dims:
            updated[dim] = max(0.01, updated[dim] - base_delta)

    return _normalize_weights(updated)


def _latest_application_for_job(job_id: int, session) -> Application | None:
    return session.scalars(
        select(Application).where(Application.job_id == job_id).order_by(desc(Application.id)).limit(1)
    ).first()


def _latest_evaluation_for_job(job_id: int, session) -> Evaluation | None:
    return session.scalars(
        select(Evaluation).where(Evaluation.job_id == job_id).order_by(desc(Evaluation.id)).limit(1)
    ).first()


def _last_month_b_grade_interviews_for_company_stage(session) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=30)

    stmt = (
        select(func.count(Application.id))
        .join(Job, Application.job_id == Job.id)
        .join(Evaluation, and_(Evaluation.job_id == Job.id, Evaluation.grade == "B"))
        .where(
            and_(
                Application.outcome == "interview",
                Application.applied_at >= since,
                Evaluation.score_company_stage >= 3.5,
            )
        )
    )
    value = session.scalar(stmt)
    return int(value or 0)


def command(
    job_id: int = typer.Argument(..., help="Job ID to log learning outcome for."),
    outcome: OutcomeType = typer.Argument(..., help="Outcome: interview|rejected|offer|ghosted"),
    notes: str = typer.Option("", "--notes", help="Optional notes to store with the outcome."),
) -> None:
    """Log outcome and update scoring weights.

    Examples:
      openapply learn 42 interview
      openapply learn 42 rejected --notes "Lost to candidate with stronger domain background"
      openapply learn 42 offer
    """
    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise typer.BadParameter(f"Job id {job_id} not found.")

        evaluation = _latest_evaluation_for_job(job_id, session)
        if evaluation is None:
            raise typer.BadParameter("No evaluation exists for this job yet. Run apply/scan first.")

        app_row = _latest_application_for_job(job_id, session)
        if app_row is None:
            app_row = Application(
                job_id=job_id,
                cv_id=None,
                auto_applied=False,
                human_reviewed=True,
                outcome=outcome,
                response_received_at=datetime.now(timezone.utc),
            )
            session.add(app_row)
            session.flush()
        else:
            app_row.outcome = outcome
            app_row.response_received_at = datetime.now(timezone.utc)
            session.add(app_row)

        session.add(
            Outcome(
                application_id=app_row.id,
                outcome_type=outcome,
                notes=notes or None,
                logged_at=datetime.now(timezone.utc),
            )
        )

        if outcome == "offer":
            job.status = "offer"
        elif outcome == "interview":
            job.status = "interview"
        elif outcome == "rejected":
            job.status = "rejected"
        session.add(job)

        weight_rows = session.scalars(select(ScoringWeight)).all()
        if not weight_rows:
            raise typer.BadParameter("No scoring weights found. Run setup to initialize database.")

        current_weights = {row.dimension: float(row.weight) for row in weight_rows}

        bonus_company_stage = bool(
            outcome in {"interview", "offer"}
            and evaluation.grade == "B"
            and float(evaluation.score_company_stage) >= 3.5
        )

        updated_weights = _collect_weight_adjustments(
            current_weights=current_weights,
            evaluation=evaluation,
            outcome=outcome,
            bonus_company_stage=bonus_company_stage,
        )

        adjustments: list[Adjustment] = []
        for row in weight_rows:
            old_value = float(row.weight)
            new_value = float(updated_weights.get(row.dimension, old_value))
            row.weight = new_value
            row.last_updated = datetime.now(timezone.utc)
            session.add(row)
            if abs(new_value - old_value) >= 0.0001:
                adjustments.append(
                    Adjustment(
                        dimension=row.dimension,
                        old_weight=old_value,
                        new_weight=new_value,
                    )
                )

        session.commit()

        summary = Table(title="Learning Update")
        summary.add_column("Dimension", style="cyan")
        summary.add_column("Old", justify="right")
        summary.add_column("New", justify="right", style="green")
        summary.add_column("Delta", justify="right")

        for item in sorted(adjustments, key=lambda a: abs(a.new_weight - a.old_weight), reverse=True)[:6]:
            delta = item.new_weight - item.old_weight
            summary.add_row(
                item.dimension,
                f"{item.old_weight:.4f}",
                f"{item.new_weight:.4f}",
                f"{delta:+.4f}",
            )

        console.print(summary)

        if bonus_company_stage:
            count = _last_month_b_grade_interviews_for_company_stage(session)
            console.print(
                Panel.fit(
                    (
                        "You've had "
                        f"{count} interviews from B-grade roles with strong company-stage fit "
                        "in the last month. Adjusting company_stage weight up."
                    ),
                    border_style="blue",
                )
            )

        console.print(
            Panel.fit(
                f"Outcome logged for job {job_id}: {outcome}. Scoring weights updated.",
                border_style="green",
            )
        )
