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
    prompt_path = project_root / "agent" / "prompts" / "outreach.md"
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    bundled = files("agent").joinpath("prompts/outreach.md")
    return bundled.read_text(encoding="utf-8")


async def _run_outreach(job_id: int, channel: str) -> Path:
    project_root = Path.cwd()
    config_path = project_root / "config.yml"
    if not config_path.exists():
        raise typer.BadParameter("config.yml not found. Run 'openapply setup' first.")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    profile = config.get("profile", {}) if isinstance(config.get("profile"), dict) else {}

    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    evaluation_payload: dict[str, Any] = {}
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise typer.BadParameter(f"Job id {job_id} not found.")
        evaluation = session.scalars(
            select(Evaluation).where(Evaluation.job_id == job_id).order_by(desc(Evaluation.id)).limit(1)
        ).first()
        if evaluation is not None:
            evaluation_payload = {
                "score_total": evaluation.score_total,
                "grade": evaluation.grade,
                "notes": evaluation.notes,
            }

    prompt = _load_prompt(project_root).format(
        profile_json=json.dumps(profile, ensure_ascii=True, indent=2),
        company_name=job.company or "Unknown",
        role_name=job.role or "Unknown",
        jd_content=(job.jd_extracted or job.jd_raw or ""),
        evaluation_json=json.dumps(evaluation_payload, ensure_ascii=True, indent=2),
    )

    client = OllamaClient(config_path=config_path, profile="generate")
    payload = await client.complete_json(
        system_prompt="You write outreach messages. Return JSON only.",
        user_prompt=prompt,
    )

    output_dir = project_root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    out = output_dir / f"outreach-{job_id:03d}-{stamp}-{channel}.md"

    subject = str(payload.get("subject", "")).strip()
    message = str(payload.get("message", "")).strip()
    lines = []
    lines.append(f"# Outreach ({channel})")
    lines.append("")
    if subject:
        lines.append(f"**Subject:** {subject}")
        lines.append("")
    lines.append(message)
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def command(
    job_id: int = typer.Argument(..., help="Job ID to draft outreach for."),
    channel: str = typer.Option("linkedin", "--channel", help="linkedin|email"),
) -> None:
    """Draft a LinkedIn DM or email based on job + profile."""
    channel_norm = channel.strip().lower()
    if channel_norm not in {"linkedin", "email"}:
        raise typer.BadParameter("--channel must be linkedin or email")
    try:
        import asyncio

        path = asyncio.run(_run_outreach(job_id, channel_norm))
        console.print(Panel.fit(f"Saved outreach draft:\n{path.as_posix()}", border_style="blue"))
    except OllamaClientError as exc:
        console.print("[red]Ollama is unavailable or misconfigured.[/red]")
        console.print(f"[dim]Details: {exc}[/dim]")
        raise typer.Exit(code=1) from exc

