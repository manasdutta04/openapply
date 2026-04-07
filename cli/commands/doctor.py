from __future__ import annotations

import importlib
from pathlib import Path

import httpx
import typer
import yaml
from rich.panel import Panel
from sqlalchemy import select

from agent.portals_config import load_portals_config
from memory.db import Job, Portal, build_session_factory, create_sqlite_engine, initialize_database

from cli.ui import console, panel

_PORTALS_FIX = (
    "Run `openapply portal` to enable at least one portal (or edit portals.yml manually). "
    "Tip: start with a single company board to keep scans fast."
)

def _check_ollama(config: dict) -> tuple[bool, str]:
    ollama_cfg = config.get("ollama", {}) if isinstance(config.get("ollama"), dict) else {}
    base_url = str(ollama_cfg.get("base_url", "http://localhost:11434")).strip()
    if not base_url:
        return False, "Missing ollama.base_url in config.yml"
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
        resp.raise_for_status()
        return True, f"Ollama reachable ({base_url})"
    except Exception as exc:
        return False, f"Ollama not reachable at {base_url}: {exc}"


def _check_playwright() -> tuple[bool, str]:
    try:
        importlib.import_module("playwright.async_api")
        return True, "Playwright import OK (run: python -m playwright install chromium if browser missing)"
    except Exception as exc:
        return False, f"Playwright not installed/usable: {exc}"


def _check_db(project_root: Path) -> tuple[bool, str]:
    try:
        engine = create_sqlite_engine()
        initialize_database(engine)
        session_factory = build_session_factory(engine)
        with session_factory() as session:
            _ = session.scalar(select(Job.id).limit(1))
        return True, f"SQLite OK ({(project_root / 'data' / 'openapply.db').as_posix()})"
    except Exception as exc:
        return False, f"DB error: {exc}"


def _check_portals(project_root: Path) -> tuple[bool, str]:
    cfg = load_portals_config(project_root)
    if cfg is not None:
        active = cfg.active_portals()
        if active:
            return True, f"portals.yml OK ({len(active)} active)"
        return False, f"portals.yml found but has 0 active portals. {_PORTALS_FIX}"

    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)
    with session_factory() as session:
        active = session.scalars(select(Portal).where(Portal.active.is_(True))).all()
        if active:
            return True, f"DB portals OK ({len(active)} active)"

    return False, "No active portals found (create portals.yml or insert rows into DB table: portals)"


def command() -> None:
    """Health checks for OpenApply setup and dependencies."""
    project_root = Path.cwd()
    config_path = project_root / "config.yml"
    cv_path = project_root / "cv.md"

    checks: list[tuple[str, bool, str]] = []

    if not config_path.exists():
        checks.append(("config.yml", False, "Missing config.yml (run: openapply setup)"))
        config = {}
    else:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        checks.append(("config.yml", True, "Found config.yml"))

    if not cv_path.exists():
        checks.append(("cv.md", False, "Missing cv.md (run: openapply setup)"))
    else:
        checks.append(("cv.md", True, "Found cv.md"))

    ok, msg = _check_ollama(config if isinstance(config, dict) else {})
    checks.append(("Ollama", ok, msg))

    ok, msg = _check_playwright()
    checks.append(("Playwright", ok, msg))

    ok, msg = _check_db(project_root)
    checks.append(("Database", ok, msg))

    ok, msg = _check_portals(project_root)
    checks.append(("Portals", ok, msg))

    pipeline_path = project_root / "data" / "pipeline.md"
    checks.append(("Queue", pipeline_path.exists(), "data/pipeline.md" if pipeline_path.exists() else "Missing data/pipeline.md (run scan --auto or create it)"))

    failed = [c for c in checks if not c[1]]
    lines = []
    for name, ok, msg in checks:
        mark = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
        lines.append(f"- {mark} [bold]{name}[/bold]: {msg}")

    console.print(panel("openapply doctor", "\n".join(lines)))
    if failed:
        raise typer.Exit(code=1)

