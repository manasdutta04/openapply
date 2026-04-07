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
    prompt_path = project_root / "agent" / "prompts" / "deep_research.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    bundled = files("agent").joinpath("prompts/deep_research.md")
    return bundled.read_text(encoding="utf-8")


def _render_markdown(job: Job, payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Open Apply Research")
    lines.append("")
    lines.append(f"- Job ID: {job.id}")
    lines.append(f"- Company: {job.company or 'Unknown'}")
    lines.append(f"- Role: {job.role or 'Unknown'}")
    lines.append(f"- URL: {job.url}")
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append(f"- verdict: **{payload.get('verdict', 'maybe')}**")
    lines.append(f"- confidence: {payload.get('confidence', 0.0)}")
    lines.append("")
    lines.append("## Highlights")
    lines.append("")
    for item in payload.get("highlights", []) or []:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Red Flags")
    lines.append("")
    for item in payload.get("red_flags", []) or []:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## Questions to Ask")
    lines.append("")
    for item in payload.get("questions_to_ask_recruiter", []) or []:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def _run_research(job_id: int) -> Path:
    project_root = Path.cwd()
    config_path = project_root / "config.yml"
    if not config_path.exists():
        raise typer.BadParameter("config.yml not found. Run 'openapply setup' first.")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    signals: dict[str, Any] = {}

    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise typer.BadParameter(f"Job id {job_id} not found.")
        evaluation = session.scalars(
            select(Evaluation).where(Evaluation.job_id == job_id).order_by(desc(Evaluation.id)).limit(1)
        ).first()
        if evaluation is not None:
            signals["evaluation"] = {
                "score_total": evaluation.score_total,
                "grade": evaluation.grade,
                "notes": evaluation.notes,
            }

    client = OllamaClient(config_path=config_path, profile="generate")
    prompt = _load_prompt(project_root).format(
        company_name=job.company or "Unknown",
        role_name=job.role or "Unknown",
        jd_content=(job.jd_extracted or job.jd_raw or ""),
        signals_json=json.dumps(signals, ensure_ascii=True, indent=2),
    )
    payload = await client.complete_json(
        system_prompt="You are Open Apply's company researcher. Return JSON only.",
        user_prompt=prompt,
    )

    reports_dir = project_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = reports_dir / f"research-{job_id:03d}-{stamp}.md"
    out.write_text(_render_markdown(job, payload), encoding="utf-8")
    return out


def command(job_id: int = typer.Argument(..., help="Job ID to research.")) -> None:
    """Generate a company/role research report (local-only)."""
    try:
        import asyncio

        path = asyncio.run(_run_research(job_id))
        console.print(Panel.fit(f"Saved research report:\n{path.as_posix()}", border_style="blue"))
    except OllamaClientError as exc:
        console.print("[red]Ollama is unavailable or misconfigured.[/red]")
        console.print(f"[dim]Details: {exc}[/dim]")
        raise typer.Exit(code=1) from exc

