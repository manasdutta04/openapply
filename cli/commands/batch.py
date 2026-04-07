from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from agent.batch import BatchProcessor, BatchTaskResult
from agent.cv_builder import CVBuilder
from agent.evaluator import JobEvaluator
from agent.ollama_client import OllamaClient, OllamaClientError
from agent.scraper import JobScraper
from memory.db import build_session_factory, create_sqlite_engine, initialize_database
from cli.pipeline_queue import PipelineState, dedupe_keep_order, ensure_pipeline_file, load_pipeline, save_pipeline

console = Console()

async def _run_batch(min_grade: str, limit: int | None) -> None:
    project_root = Path.cwd()
    config_path = project_root / "config.yml"
    cv_path = project_root / "cv.md"

    if not config_path.exists():
        raise typer.BadParameter("config.yml not found. Run 'openapply setup' first.")
    if not cv_path.exists():
        raise typer.BadParameter("cv.md not found. Run 'openapply setup' first.")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    concurrency = (
        int(config.get("batch", {}).get("concurrency", 3))
        if isinstance(config.get("batch"), dict)
        else 3
    )

    pipeline_path = ensure_pipeline_file(project_root)
    state = load_pipeline(pipeline_path)

    pending = dedupe_keep_order(state.pending)
    if limit is not None:
        pending = pending[: max(0, limit)]

    if not pending:
        console.print("[yellow]Pipeline is empty. Add URLs under Pending in data/pipeline.md[/yellow]")
        return

    console.print(f"Queue size: [bold cyan]{len(pending)}[/bold cyan] (concurrency={concurrency})")

    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    evaluator = JobEvaluator(
        session_factory=session_factory,
        ollama_client=OllamaClient(config_path=config_path, profile="evaluate"),
        project_root=project_root,
    )
    cv_builder = CVBuilder(
        session_factory=session_factory,
        ollama_client=OllamaClient(config_path=config_path, profile="generate"),
        project_root=project_root,
    )
    processor = BatchProcessor(
        session_factory=session_factory,
        scraper=JobScraper(),
        evaluator=evaluator,
        cv_builder=cv_builder,
        concurrency=concurrency,
    )

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )

    completed_results: list[BatchTaskResult] = []

    with progress:
        task_id = progress.add_task("Processing queue", total=len(pending))

        async def on_progress(result: BatchTaskResult) -> None:
            completed_results.append(result)
            progress.advance(task_id, 1)
            progress.update(task_id, description=f"Processing queue ({result.status})")

        run_result = await processor.process_urls(
            urls=pending,
            cv_content=cv_path.read_text(encoding="utf-8"),
            min_grade=min_grade,
            progress_callback=on_progress,
        )

    processed_now = [row.url for row in completed_results]
    state.pending = [url for url in state.pending if url not in set(processed_now)]
    state.processed = dedupe_keep_order(state.processed + processed_now)
    save_pipeline(pipeline_path, state)

    summary = Table(title="Batch Results Summary")
    summary.add_column("Metric", style="cyan")
    summary.add_column("Value", justify="right", style="green")
    summary.add_row("Total queued", str(run_result.total))
    summary.add_row("Processed", str(run_result.processed))
    summary.add_row("Succeeded", str(run_result.succeeded))
    summary.add_row("Filtered", str(run_result.filtered))
    summary.add_row("Skipped (resumable)", str(run_result.skipped))
    summary.add_row("Failed", str(run_result.failed))
    console.print(summary)

    if run_result.failed:
        failures = Table(title="Failed Items")
        failures.add_column("URL", style="yellow")
        failures.add_column("Error", style="red")
        for row in run_result.results:
            if row.status == "failed":
                failures.add_row(row.url, row.error or "Unknown error")
        console.print(failures)

    console.print(
        Panel.fit(
            "Batch complete. Queue updated in data/pipeline.md",
            border_style="blue",
        )
    )


def command(
    min_score: str = typer.Option("B", "--min-score", help="Minimum grade to generate CV (A/B/C/D/F)."),
    limit: int = typer.Option(20, "--limit", min=1, help="Max pending URLs to process."),
) -> None:
    """Process pipeline queue in parallel.

    Examples:
      openapply batch
      openapply batch --min-score B --limit 20
      openapply batch --min-score C --limit 50
    """
    grade = min_score.strip().upper()
    if grade not in {"A", "B", "C", "D", "F"}:
        raise typer.BadParameter("--min-score must be one of: A, B, C, D, F")

    try:
        asyncio.run(_run_batch(min_grade=grade, limit=limit))
    except OllamaClientError as exc:
        console.print("[red]Ollama is unavailable or misconfigured.[/red]")
        console.print(f"[dim]Details: {exc}[/dim]")
        raise typer.Exit(code=1) from exc
