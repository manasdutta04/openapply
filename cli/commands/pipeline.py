from __future__ import annotations

import typer
from rich.console import Console

from agent.ollama_client import OllamaClientError
from cli.flows.offer_pipeline import run_offer_pipeline

console = Console()


async def _run_pipeline(target: str) -> None:
    await run_offer_pipeline(target, interactive=False, allow_form_draft=False)


def command(
    target: str = typer.Argument(..., help="Job URL or raw job description text."),
) -> None:
    """Run auto-pipeline: evaluate + generate report + CV PDF + cover letter."""
    try:
        import asyncio

        asyncio.run(_run_pipeline(target))
    except OllamaClientError as exc:
        console.print("[red]Ollama is unavailable or misconfigured.[/red]")
        console.print(f"[dim]Details: {exc}[/dim]")
        raise typer.Exit(code=1) from exc

