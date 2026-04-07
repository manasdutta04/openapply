from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import desc, select

from agent.evaluator import JobEvaluator
from agent.ollama_client import OllamaClient, OllamaClientError
from agent.portals_config import load_portals_config
from agent.scanner import JobScanner
from agent.scraper import JobScraper
from memory.db import Evaluation, Job, Portal, build_session_factory, create_sqlite_engine, initialize_database
from cli.pipeline_queue import append_pending

console = Console()

_PORTALS_REQUIRED_MESSAGE = (
    "[yellow]No active portals enabled in portals.yml.[/yellow]\n"
    "Edit portals.yml and set at least one entry to [bold]active: true[/bold], then re-run:\n"
    "  openapply doctor\n"
    "  openapply scan\n"
)

def _show_summary(new_jobs: list[Job]) -> None:
    count = len(new_jobs)
    console.print(f"Found [bold green]{count}[/bold green] new matches.")

    top = new_jobs[:5]
    if not top:
        return

    table = Table(title="Top 5 New Matches")
    table.add_column("Job ID", justify="right", style="cyan")
    table.add_column("Company", style="green")
    table.add_column("Role")
    table.add_column("URL")

    for row in top:
        table.add_row(str(row.id), row.company or "Unknown", row.role or "Unknown", row.url)

    console.print(table)


async def _auto_route_b_plus(
    project_root: Path,
    session_factory,
    new_jobs: list[Job],
) -> tuple[int, int]:
    cv_path = project_root / "cv.md"
    if not cv_path.exists():
        console.print("[yellow]cv.md not found; skipping --auto evaluation routing.[/yellow]")
        return 0, 0

    cv_content = cv_path.read_text(encoding="utf-8")
    evaluator = JobEvaluator(
        session_factory=session_factory,
        ollama_client=OllamaClient(config_path=project_root / "config.yml", profile="evaluate"),
        project_root=project_root,
    )

    passed_urls: list[str] = []
    evaluated = 0

    for job in new_jobs:
        if not (job.jd_extracted or job.jd_raw):
            continue

        result = await evaluator.evaluate_job(job.id, cv_content)
        evaluated += 1
        if result.grade in {"A", "B"}:
            passed_urls.append(job.url)

    queued = append_pending(project_root, passed_urls)
    return evaluated, queued


async def _run_scan(auto: bool, max_jobs_per_portal: int, max_links_per_portal: int) -> None:
    project_root = Path.cwd()
    config_path = project_root / "config.yml"

    if not config_path.exists():
        raise typer.BadParameter("config.yml not found. Run 'openapply setup' first.")

    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    portals_cfg = load_portals_config(project_root)
    if portals_cfg is not None:
        if not portals_cfg.active_portals():
            console.print(_PORTALS_REQUIRED_MESSAGE)
            return
    else:
        with session_factory() as session:
            portal_count = session.scalars(select(Portal).where(Portal.active.is_(True))).all()
            if not portal_count:
                console.print(
                    "[yellow]No active portals configured.[/yellow] "
                    "Create portals.yml (copy from portals.example.yml) or insert rows in DB table: portals."
                )
                return

    scanner = JobScanner(
        session_factory=session_factory,
        ollama_client=OllamaClient(config_path=config_path, profile="generate"),
        scraper=JobScraper(),
        project_root=project_root,
    )

    console.print("[bold]Scanning active portals...[/bold]")
    result = await scanner.scan(
        max_links_per_portal=max_links_per_portal,
        max_jobs_per_portal=max_jobs_per_portal,
    )

    with session_factory() as session:
        discovered_urls = {item.url for item in result.discovered}
        new_jobs = session.scalars(
            select(Job).where(Job.url.in_(discovered_urls)).order_by(desc(Job.scraped_at))
        ).all() if discovered_urls else []

    _show_summary(new_jobs)
    if result.skipped_duplicates:
        console.print(f"Skipped duplicates: [yellow]{result.skipped_duplicates}[/yellow]")

    if auto:
        console.print("[bold]Auto mode:[/bold] evaluating new jobs and routing B+ to pipeline...")
        try:
            evaluated_count, queued_count = await _auto_route_b_plus(project_root, session_factory, new_jobs)
        except OllamaClientError as exc:
            console.print("[yellow]Ollama unavailable; skipping auto routing.[/yellow]")
            console.print(f"[dim]Details: {exc}[/dim]")
            return

        console.print(
            Panel.fit(
                f"Evaluated: {evaluated_count} | Added to pipeline: {queued_count}",
                border_style="blue",
            )
        )


def command(
    auto: bool = typer.Option(
        False,
        "--auto",
        help="Evaluate discovered jobs and add B+ matches to data/pipeline.md.",
    ),
    limit: int = typer.Option(8, "--limit", min=1, help="Max jobs to scrape per portal."),
    link_limit: int = typer.Option(30, "--link-limit", min=1, help="Max listing links to consider per portal."),
) -> None:
    """Discover jobs across active portals.

    Examples:
      openapply scan
      openapply scan --auto
    """
    try:
        asyncio.run(_run_scan(auto=auto, max_jobs_per_portal=limit, max_links_per_portal=link_limit))
    except OllamaClientError as exc:
        console.print("[red]Ollama is unavailable or misconfigured.[/red]")
        console.print(f"[dim]Details: {exc}[/dim]")
        raise typer.Exit(code=1) from exc
