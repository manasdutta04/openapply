from __future__ import annotations

import asyncio
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.table import Table

from agent.cv_builder import CVBuilderError
from agent.ollama_client import OllamaClientError
from agent.scraper import JobScraper, ScraperError
from cli.flows.offer_pipeline import PipelineResult, log_application, run_offer_pipeline
from memory.db import build_session_factory, create_sqlite_engine, initialize_database

console = Console()

async def _run_apply(target: str) -> None:
    project_root = Path.cwd()
    config_path = project_root / "config.yml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    profile = config.get("profile", {}) if isinstance(config, dict) and isinstance(config.get("profile"), dict) else {}

    pipeline: PipelineResult = await run_offer_pipeline(target, interactive=True, allow_form_draft=True)

    choice = typer.prompt("Apply now? [y/N/later]", default="N").strip().lower()
    if choice not in {"y", "yes"}:
        console.print("[bold]Apply postponed.[/bold]")
        return

    console.print("[bold]Drafting application form values (HITL)...[/bold]")
    if not target.strip().lower().startswith("http"):
        console.print("[yellow]Input was JD text, not URL. Cannot open a live form to fill.[/yellow]")
        engine = create_sqlite_engine()
        initialize_database(engine)
        session_factory = build_session_factory(engine)
        application_id = log_application(
            job_id=pipeline.job.id,
            cv_id=pipeline.cv.cv_id,
            human_reviewed=False,
            session_factory=session_factory,
        )
        console.print(f"Application draft logged with ID: [green]{application_id}[/green]")
        return

    scraper = JobScraper()
    fill_result = await scraper.fill_form(
        target,
        evaluation={
            "recommendation": pipeline.evaluation.recommendation,
            "grade": pipeline.evaluation.grade,
            "score_total": pipeline.evaluation.weighted_total,
        },
        cv_data={"profile": profile, "summary": pipeline.evaluation.summary},
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

    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    application_id = log_application(
        job_id=pipeline.job.id,
        cv_id=pipeline.cv.cv_id,
        human_reviewed=reviewed,
        session_factory=session_factory,
    )

    console.print("[bold]Logged application to DB[/bold]")
    console.print(f"Application ID: [green]{application_id}[/green]")


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
