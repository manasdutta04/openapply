from __future__ import annotations

import asyncio
import difflib
import json
import re
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import desc, select

from agent.cv_builder import CVBuilder, CVBuilderError
from agent.evaluator import JobEvaluator
from agent.ollama_client import OllamaClient, OllamaClientError
from agent.scraper import JobScraper, ScraperError
from memory.db import Application, Evaluation, Job, build_session_factory, create_sqlite_engine, initialize_database

console = Console()


def _is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value.strip(), re.IGNORECASE))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _ensure_job(
    source: str,
    jd_payload: dict[str, Any] | None,
    session_factory,
) -> Job:
    with session_factory() as session:
        if jd_payload is not None:
            job = session.scalars(select(Job).where(Job.url == source)).first()
            if job is None:
                job = Job(
                    url=source,
                    company=str(jd_payload.get("company", "")).strip() or None,
                    role=str(jd_payload.get("title", "")).strip() or None,
                    jd_raw=str(jd_payload.get("description", "")).strip(),
                    jd_extracted=str(jd_payload.get("description", "")).strip(),
                    scraped_at=datetime.now(timezone.utc),
                    status="new",
                )
                session.add(job)
            else:
                job.company = str(jd_payload.get("company", "")).strip() or job.company
                job.role = str(jd_payload.get("title", "")).strip() or job.role
                job.jd_raw = str(jd_payload.get("description", "")).strip() or job.jd_raw
                job.jd_extracted = str(jd_payload.get("description", "")).strip() or job.jd_extracted
                job.scraped_at = datetime.now(timezone.utc)

            session.commit()
            session.refresh(job)
            return job

        synthetic_url = f"inline://{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        title_guess, company_guess = _infer_title_company_from_text(source)

        job = Job(
            url=synthetic_url,
            company=company_guess,
            role=title_guess,
            jd_raw=source,
            jd_extracted=source,
            scraped_at=datetime.now(timezone.utc),
            status="new",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def _infer_title_company_from_text(jd_text: str) -> tuple[str | None, str | None]:
    lines = [line.strip() for line in jd_text.splitlines() if line.strip()]
    role = lines[0] if lines else None
    company = None
    for line in lines[:8]:
        if line.lower().startswith("company") and ":" in line:
            company = line.split(":", 1)[1].strip() or None
            break
    return role, company


def _show_scorecard(result) -> None:
    table = Table(title="Job Evaluation")
    table.add_column("Dimension", style="cyan")
    table.add_column("Score", justify="right", style="green")

    for key, value in result.scores.items():
        table.add_row(key, f"{value:.1f}")

    console.print(table)
    console.print(
        Panel.fit(
            f"Total: [bold]{result.weighted_total:.2f}[/bold] | "
            f"Grade: [bold]{result.grade}[/bold] | "
            f"Recommendation: [bold]{result.recommendation}[/bold]",
            border_style="blue",
        )
    )


def _latest_evaluation_id(job_id: int, session_factory) -> int | None:
    with session_factory() as session:
        row = session.scalars(
            select(Evaluation).where(Evaluation.job_id == job_id).order_by(desc(Evaluation.id)).limit(1)
        ).first()
        return row.id if row is not None else None


def _render_cv_diff(base_cv_path: Path, tailored_cv_path: Path) -> str:
    base_lines = base_cv_path.read_text(encoding="utf-8").splitlines()
    tailored_lines = tailored_cv_path.read_text(encoding="utf-8").splitlines()
    diff = list(
        difflib.unified_diff(
            base_lines,
            tailored_lines,
            fromfile=base_cv_path.name,
            tofile=tailored_cv_path.name,
            n=1,
            lineterm="",
        )
    )
    if not diff:
        return "No textual CV differences detected."

    preview = diff[:220]
    return "\n".join(preview)


async def _generate_cover_letter(
    project_root: Path,
    ollama_client: OllamaClient,
    profile: dict[str, Any],
    cv_content: str,
    jd_content: str,
    evaluation_payload: dict[str, Any],
    role: str | None,
    company: str | None,
) -> Path:
    prompt_path = project_root / "agent" / "prompts" / "cover_letter.md"
    if prompt_path.exists():
        prompt_template = prompt_path.read_text(encoding="utf-8")
    else:
        bundled_prompt = files("agent").joinpath("prompts/cover_letter.md")
        if not bundled_prompt.is_file():
            raise FileNotFoundError(f"Missing prompt file: {prompt_path}")
        prompt_template = bundled_prompt.read_text(encoding="utf-8")

    prompt = prompt_template.format(
        profile_json=json.dumps(profile, ensure_ascii=True, indent=2),
        cv_content=cv_content,
        jd_content=jd_content,
        evaluation_json=json.dumps(evaluation_payload, ensure_ascii=True, indent=2),
        tailoring_json=json.dumps({}, ensure_ascii=True),
    )

    payload = await ollama_client.complete_json(
        system_prompt="You are Open Apply's cover letter writer. Return JSON only.",
        user_prompt=prompt,
    )

    body = str(payload.get("body", "")).strip()
    greeting = str(payload.get("greeting", "Dear Hiring Team,")).strip()
    closing = str(payload.get("closing", "Sincerely,")).strip()
    subject = str(payload.get("subject", f"Application for {role or 'the role'}")).strip()

    slug_role = _slugify(role or "role")
    slug_company = _slugify(company or "company")
    stamp = datetime.now().strftime("%Y%m%d")
    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{slug_company}-{slug_role}-{stamp}-cover-letter.md"

    text = (
        f"# {subject}\n\n"
        f"{greeting}\n\n"
        f"{body}\n\n"
        f"{closing}\n"
    )
    path.write_text(text, encoding="utf-8")
    return path


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "na"


def _log_application(job_id: int, cv_id: int | None, human_reviewed: bool, session_factory) -> int:
    with session_factory() as session:
        app = Application(
            job_id=job_id,
            cv_id=cv_id,
            applied_at=datetime.now(timezone.utc),
            auto_applied=False,
            human_reviewed=human_reviewed,
            outcome="pending",
        )
        session.add(app)

        job = session.get(Job, job_id)
        if job is not None:
            job.status = "applied" if human_reviewed else "evaluated"
            session.add(job)

        session.commit()
        session.refresh(app)
        return app.id


async def _run_apply(target: str) -> None:
    project_root = Path.cwd()
    config_path = project_root / "config.yml"
    cv_path = project_root / "cv.md"

    if not config_path.exists():
        raise typer.BadParameter("config.yml not found. Run 'openapply setup' first.")
    if not cv_path.exists():
        raise typer.BadParameter("cv.md not found. Run 'openapply setup' first.")

    config = _load_yaml(config_path)
    profile = config.get("profile", {}) if isinstance(config.get("profile"), dict) else {}
    cv_content = cv_path.read_text(encoding="utf-8")

    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    evaluate_client = OllamaClient(config_path=config_path, profile="evaluate")
    generate_client = OllamaClient(config_path=config_path, profile="generate")

    scraper = JobScraper()
    evaluator = JobEvaluator(session_factory=session_factory, ollama_client=evaluate_client, project_root=project_root)
    cv_builder = CVBuilder(session_factory=session_factory, ollama_client=generate_client, project_root=project_root)

    jd_payload: dict[str, Any] | None = None
    source_for_job = target

    if _is_url(target):
        console.print("[bold]1/8[/bold] Scraping job description...")
        jd_payload = await scraper.scrape_jd(target)
        source_for_job = target
    else:
        console.print("[bold]1/8[/bold] Parsing pasted JD text...")
        source_for_job = target

    job = _ensure_job(source=source_for_job, jd_payload=jd_payload, session_factory=session_factory)

    console.print("[bold]2/8[/bold] Evaluating fit (10 dimensions)...")
    evaluation_result = await evaluator.evaluate_job(job.id, cv_content)
    _show_scorecard(evaluation_result)

    if evaluation_result.weighted_total < 3.0:
        proceed = typer.confirm(
            "Score is below 3.0. Continue anyway?",
            default=False,
        )
        if not proceed:
            with session_factory() as session:
                db_job = session.get(Job, job.id)
                if db_job is not None:
                    db_job.status = "skipped"
                    session.add(db_job)
                    session.commit()
            console.print("[yellow]Apply flow canceled by user. Job marked as skipped.[/yellow]")
            return

    evaluation_id = evaluation_result.evaluation_id or _latest_evaluation_id(job.id, session_factory)
    if evaluation_id is None:
        raise RuntimeError("Evaluation was not persisted; cannot continue apply flow.")

    console.print("[bold]3/8[/bold] Generating tailored CV PDF...")
    cv_result = await cv_builder.build_for_job(job.id, evaluation_id)

    console.print("[bold]4/8[/bold] Generating cover letter...")
    jd_for_cover = jd_payload.get("description", "") if jd_payload else target
    cover_path = await _generate_cover_letter(
        project_root=project_root,
        ollama_client=generate_client,
        profile=profile,
        cv_content=cv_content,
        jd_content=jd_for_cover,
        evaluation_payload={
            "score": evaluation_result.weighted_total,
            "grade": evaluation_result.grade,
            "summary": evaluation_result.summary,
            "top_strengths": evaluation_result.top_strengths,
            "key_gaps": evaluation_result.key_gaps,
            "recommendation": evaluation_result.recommendation,
        },
        role=job.role,
        company=job.company,
    )

    console.print("[bold]5/8[/bold] Showing CV diff vs base cv.md...")
    diff_text = _render_cv_diff(base_cv_path=cv_path, tailored_cv_path=cv_result.cv_path)
    console.print(Panel(diff_text, title="CV Diff Preview", border_style="magenta"))

    console.print("[bold]6/8[/bold] Review generated artifacts")
    console.print(f"- Tailored CV markdown: {cv_result.cv_path.as_posix()}")
    console.print(f"- Tailored CV PDF: {cv_result.pdf_path.as_posix()}")
    console.print(f"- Cover letter: {cover_path.as_posix()}")
    console.print(f"- Evaluation report: {evaluation_result.report_path.as_posix()}")

    choice = typer.prompt("Apply now? [y/N/later]", default="N").strip().lower()
    if choice in {"y", "yes"}:
        console.print("[bold]7/8[/bold] Drafting application form values (HITL)...")

        if _is_url(target):
            fill_result = await scraper.fill_form(
                target,
                evaluation={
                    "recommendation": evaluation_result.recommendation,
                    "grade": evaluation_result.grade,
                    "score_total": evaluation_result.weighted_total,
                },
                cv_data={
                    "profile": profile,
                    "summary": evaluation_result.summary,
                },
            )

            table = Table(title="Drafted Application Fields")
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Status", style="yellow")
            table.add_column("Value", style="white")

            for field in fill_result.get("filled_fields", []):
                table.add_row(
                    str(field.get("name", "")),
                    str(field.get("type", "")),
                    str(field.get("status", "")),
                    str(field.get("value", ""))[:80],
                )
            console.print(table)

            reviewed = typer.confirm(
                "Review is required. Mark as human-reviewed after you validate in browser?",
                default=False,
            )
            application_id = _log_application(job_id=job.id, cv_id=cv_result.cv_id, human_reviewed=reviewed, session_factory=session_factory)

            console.print("[bold]8/8[/bold] Logged application to DB")
            console.print(f"Application ID: [green]{application_id}[/green]")
            if reviewed:
                console.print("[green]Application marked as reviewed/applied.[/green]")
            else:
                console.print("[yellow]Application saved as pending review.[/yellow]")
            return

        console.print("[yellow]Input was JD text, not URL. Cannot open a live form to fill.[/yellow]")
        application_id = _log_application(job_id=job.id, cv_id=cv_result.cv_id, human_reviewed=False, session_factory=session_factory)
        console.print(f"Application draft logged with ID: [green]{application_id}[/green]")
        return

    console.print("[bold]7/8[/bold] Apply postponed.")
    if choice == "later":
        console.print("Saved artifacts. You can return later to apply manually.")
    else:
        console.print("No application form actions were taken.")


def command(
    target: str = typer.Argument(
        ...,
        help="Job URL or raw job description text.",
    ),
) -> None:
    """Run full apply flow for one job.

    Examples:
      openapply apply https://boards.greenhouse.io/company/jobs/123
            openapply apply "Senior Backend Engineer ..."
    """
    try:
        asyncio.run(_run_apply(target))
    except OllamaClientError as exc:
        console.print(
            "[red]Ollama is unavailable or misconfigured.[/red] "
            "The command can continue once Ollama is running and models are configured."
        )
        console.print(f"[dim]Details: {exc}[/dim]")
        raise typer.Exit(code=1) from exc
    except ScraperError as exc:
        console.print(f"[red]Scraper error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CVBuilderError as exc:
        console.print(f"[red]CV builder error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
