from __future__ import annotations

import re
import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any

import typer
import yaml

from agent.portals_config import PORTAL_TYPES, load_portals_config
from cli.ui import console, panel


def _project_root() -> Path:
    return Path.cwd()


def _ensure_portals_file(root: Path) -> Path:
    path = root / "portals.yml"
    if path.exists():
        return path

    example = root / "portals.example.yml"
    if example.exists():
        shutil.copyfile(example, path)
        return path

    bundled = files("cli").joinpath("assets/portals.example.yml")
    path.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _save_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def _all_portals(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ("tracked_companies", "job_boards"):
        value = payload.get(key, [])
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    out.append(item)
    return out


def _dedupe_by_url(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        url = str(item.get("url", "")).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(item)
    return out


def _detect_type(url: str) -> str:
    u = url.strip().lower()
    if "boards.greenhouse.io" in u:
        return "greenhouse"
    if "jobs.lever.co" in u:
        return "lever"
    if "jobs.ashbyhq.com" in u:
        return "ashby"
    return "custom"


def _infer_name(url: str) -> str:
    u = url.strip().rstrip("/")
    # Take last path segment as a decent default.
    seg = u.split("/")[-1] if "/" in u else u
    seg = re.sub(r"[^a-zA-Z0-9._-]+", " ", seg).strip()
    return seg or "My Portal"


def _render_status(root: Path) -> str:
    cfg = load_portals_config(root)
    if cfg is None:
        return "portals.yml not found yet."
    active = cfg.active_portals()
    lines = [f"Active portals: {len(active)}"]
    for p in active[:10]:
        lines.append(f"- {p.name} ({p.type}) {p.url}")
    if len(active) > 10:
        lines.append(f"- …and {len(active) - 10} more")
    return "\n".join(lines)


def _enable_from_catalog(path: Path) -> int:
    payload = _load_yaml(path)
    portals = _all_portals(payload)
    if not portals:
        console.print(panel("Portals", "[warn]No portals found in portals.yml[/warn]"))
        return 0

    # Present a numbered list.
    lines: list[str] = []
    for idx, item in enumerate(portals, start=1):
        name = str(item.get("name", "")).strip() or f"Portal {idx}"
        ptype = str(item.get("type", "custom")).strip().lower()
        url = str(item.get("url", "")).strip()
        active = bool(item.get("active", False))
        mark = "ON " if active else "off"
        lines.append(f"{idx:>2}. [{mark}] {name} ({ptype}) — {url}")
    console.print(panel("Catalog", "\n".join(lines), subtitle="Choose one or more portal numbers to enable"))

    raw = typer.prompt("Enable portals (comma-separated numbers)", default="1").strip()
    picks: set[int] = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit():
            picks.add(int(tok))

    enabled = 0
    for idx in picks:
        if 1 <= idx <= len(portals):
            if not bool(portals[idx - 1].get("active", False)):
                portals[idx - 1]["active"] = True
                enabled += 1

    # Write back, preserving top-level lists.
    tracked = payload.get("tracked_companies", [])
    boards = payload.get("job_boards", [])
    if isinstance(tracked, list):
        payload["tracked_companies"] = _dedupe_by_url([i for i in tracked if isinstance(i, dict)])
    if isinstance(boards, list):
        payload["job_boards"] = _dedupe_by_url([i for i in boards if isinstance(i, dict)])
    _save_yaml(path, payload)
    return enabled


def _add_by_url(path: Path) -> None:
    url = typer.prompt("Paste company jobs URL (Greenhouse/Lever/Ashby)", default="").strip()
    if not url:
        raise typer.BadParameter("URL is required.")

    ptype = _detect_type(url)
    if ptype not in PORTAL_TYPES:
        ptype = "custom"

    default_name = _infer_name(url)
    name = typer.prompt("Portal name", default=default_name).strip() or default_name

    payload = _load_yaml(path)
    tracked = payload.get("tracked_companies")
    if not isinstance(tracked, list):
        tracked = []
        payload["tracked_companies"] = tracked

    tracked.append(
        {
            "name": name,
            "type": ptype,
            "url": url,
            "active": True,
        }
    )

    payload["tracked_companies"] = _dedupe_by_url([i for i in tracked if isinstance(i, dict)])
    _save_yaml(path, payload)


def _disable_active(path: Path) -> int:
    payload = _load_yaml(path)
    portals = _all_portals(payload)
    active_items = [p for p in portals if bool(p.get("active", False))]
    if not active_items:
        console.print(panel("Portals", "[muted]No active portals to disable.[/muted]"))
        return 0

    lines: list[str] = []
    for idx, item in enumerate(active_items, start=1):
        name = str(item.get("name", "")).strip() or f"Portal {idx}"
        ptype = str(item.get("type", "custom")).strip().lower()
        url = str(item.get("url", "")).strip()
        lines.append(f"{idx:>2}. {name} ({ptype}) — {url}")
    console.print(panel("Disable", "\n".join(lines)))

    raw = typer.prompt("Disable which? (comma-separated numbers)", default="").strip()
    picks: set[int] = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit():
            picks.add(int(tok))

    disabled = 0
    for idx in picks:
        if 1 <= idx <= len(active_items):
            if bool(active_items[idx - 1].get("active", False)):
                active_items[idx - 1]["active"] = False
                disabled += 1

    _save_yaml(path, payload)
    return disabled


def command() -> None:
    """Manage portals from the CLI (no manual YAML edits required)."""
    root = _project_root()
    path = _ensure_portals_file(root)

    console.print(panel("Portals", f"Config file: {path.as_posix()}"))

    choice = typer.prompt(
        "Action (enable/add/disable/status)",
        default="enable",
    ).strip().lower()

    if choice in {"status", "s"}:
        console.print(panel("Status", _render_status(root)))
        return

    if choice in {"enable", "e"}:
        enabled = _enable_from_catalog(path)
        console.print(panel("Saved", f"Enabled {enabled} portal(s).\nNext: openapply doctor → openapply scan"))
        return

    if choice in {"add", "a"}:
        _add_by_url(path)
        console.print(panel("Saved", "Added 1 portal (active: true).\nNext: openapply doctor → openapply scan"))
        return

    if choice in {"disable", "d"}:
        disabled = _disable_active(path)
        console.print(panel("Saved", f"Disabled {disabled} portal(s)."))
        return

    raise typer.BadParameter("Unknown action. Use enable/add/disable/status.")

