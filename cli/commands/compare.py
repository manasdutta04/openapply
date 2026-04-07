from __future__ import annotations

import json
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from sqlalchemy import desc, select

from agent.ollama_client import OllamaClient, OllamaClientError
from memory.db import Evaluation, Job, build_session_factory, create_sqlite_engine, initialize_database

console = Console()


def _load_prompt(project_root: Path) -> str:
    prompt_path = project_root / "agent" / "prompts" / "compare_offers.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    bundled = files("agent").joinpath("prompts/compare_offers.md")
    return bundled.read_text(encoding="utf-8")


def _render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Open Apply Offer Comparison")
    lines.append("")
    lines.append("## Ranking")
    lines.append("")
    for item in payload.get("ranking", []) or []:
        lines.append(f"- **#{item.get('rank')}** job_id={item.get('job_id')}: {item.get('why')}")
    lines.append("")
    top = payload.get("top_pick", {}) if isinstance(payload.get("top_pick"), dict) else {}
    if top:
        lines.append("## Top Pick")
        lines.append("")
        lines.append(f"- job_id={top.get('job_id')}: {top.get('why')}")
        lines.append("")
    notes = payload.get("notes", []) or []
    if notes:
        lines.append("## Notes")
        lines.append("")
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def _run_compare(job_ids: list[int]) -> Path:
    project_root = Path.cwd()
    config_path = project_root / "config.yml"
    if not config_path.exists():
        raise typer.BadParameter("config.yml not found. Run 'openapply setup' first.")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    targets = config.get("targets", {}) if isinstance(config.get("targets"), dict) else {}

    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    offers: list[dict[str, Any]] = []
    with session_factory() as session:
        for job_id in job_ids:
            job = session.get(Job, job_id)
            if job is None:
                continue
            evaluation = session.scalars(
                select(Evaluation).where(Evaluation.job_id == job_id).order_by(desc(Evaluation.id)).limit(1)
            ).first()
            offers.append(
                {
                    "job_id": job_id,
                    "company": job.company,
                    "role": job.role,
                    "url": job.url,
                    "score_total": evaluation.score_total if evaluation else None,
                    "grade": evaluation.grade if evaluation else None,
                    "evaluation_notes": evaluation.notes if evaluation else None,
                }
            )

    if len(offers) < 2:
        raise typer.BadParameter("Need at least 2 valid job IDs with stored jobs.")

    prompt = _load_prompt(project_root).format(
        targets_json=json.dumps(targets, ensure_ascii=True, indent=2),
        offers_json=json.dumps(offers, ensure_ascii=True, indent=2),
    )

    client = OllamaClient(config_path=config_path, profile="generate")
    payload = await client.complete_json(
        system_prompt="You compare job offers for a candidate. Return JSON only.",
        user_prompt=prompt,
    )

    reports_dir = project_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = reports_dir / f"compare-{stamp}.md"
    out.write_text(_render_markdown(payload), encoding="utf-8")
    return out


def command(
    job_ids: str = typer.Argument(..., help="Comma-separated job IDs, e.g. '12,15,22'"),
) -> None:
    """Compare 2+ jobs and recommend priority order."""
    ids: list[int] = []
    for token in job_ids.split(","):
        token = token.strip()
        if token.isdigit():
            ids.append(int(token))
    ids = list(dict.fromkeys(ids))
    if len(ids) < 2:
        raise typer.BadParameter("Provide at least 2 comma-separated numeric job IDs.")

    try:
        import asyncio

        path = asyncio.run(_run_compare(ids))
        console.print(Panel.fit(f"Saved comparison report:\n{path.as_posix()}", border_style="blue"))
    except OllamaClientError as exc:
        console.print("[red]Ollama is unavailable or misconfigured.[/red]")
        console.print(f"[dim]Details: {exc}[/dim]")
        raise typer.Exit(code=1) from exc

