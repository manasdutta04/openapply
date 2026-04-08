from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import httpx
import yaml
from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from agent.portals_config import load_portals_config


ACCENT = "#6366f1"  # Shipwell indigo-ish accent

theme = Theme(
    {
        "accent": f"bold {ACCENT}",
        "muted": "dim",
        "good": "green",
        "warn": "yellow",
        "bad": "red",
        "cmd": "cyan",
        "k": "bold",
    }
)

console = Console(theme=theme, highlight=False, soft_wrap=True)


def panel(title: str, body, *, subtitle: str | None = None) -> Panel:
    return Panel(
        body,
        title=Text(title, style="accent"),
        subtitle=Text(subtitle, style="muted") if subtitle else None,
        border_style="muted",
        box=box.ROUNDED,
        padding=(1, 2),
    )


def ok_mark(ok: bool) -> Text:
    return Text("●", style="good" if ok else "bad")


@dataclass(frozen=True, slots=True)
class BannerStatus:
    config_ok: bool
    cv_ok: bool
    portals_ok: bool
    portals_active: int
    ollama_ok: bool
    ollama_url: str


def _load_config(root: Path) -> dict:
    path = root / "config.yml"
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _check_ollama(config: dict) -> tuple[bool, str]:
    ollama_cfg = config.get("ollama", {}) if isinstance(config.get("ollama"), dict) else {}
    base_url = str(ollama_cfg.get("base_url", "http://localhost:11434")).strip() or "http://localhost:11434"
    try:
        httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=1.2).raise_for_status()
        return True, base_url
    except Exception:
        return False, base_url


def gather_banner_status(project_root: Path) -> BannerStatus:
    config_ok = (project_root / "config.yml").exists()
    cv_ok = (project_root / "cv.md").exists()

    portals_active = 0
    portals_ok = False
    cfg = load_portals_config(project_root)
    if cfg is not None:
        portals_active = len(cfg.active_portals())
        portals_ok = portals_active > 0

    config = _load_config(project_root)
    ollama_ok, ollama_url = _check_ollama(config)

    return BannerStatus(
        config_ok=config_ok,
        cv_ok=cv_ok,
        portals_ok=portals_ok,
        portals_active=portals_active,
        ollama_ok=ollama_ok,
        ollama_url=ollama_url,
    )


def _openapply_logo() -> list[str]:
    # Big ASCII wordmark (safe, widely-supported glyphs).
    return [
        " ██████╗ ██████╗ ███████╗███╗   ██╗ █████╗ ██████╗ ██████╗ ██╗  ██╗   ██╗",
        "██╔═══██╗██╔══██╗██╔════╝████╗  ██║██╔══██╗██╔══██╗██╔══██╗██║  ╚██╗ ██╔╝",
        "██║   ██║██████╔╝█████╗  ██╔██╗ ██║███████║██████╔╝██████╔╝██║   ╚████╔╝ ",
        "██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║██╔══██║██╔═══╝ ██╔═══╝ ██║    ╚██╔╝  ",
        "╚██████╔╝██║     ███████╗██║ ╚████║██║  ██║██║     ██║     ███████╗██║   ",
        " ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝     ╚═╝     ╚══════╝╚═╝   ",
    ]


def _cmd_line(cmd: str, desc: str) -> Text:
    return Text.assemble((cmd, "cmd"), ("  ", "muted"), (desc, "muted"))


def _section(title: str, lines: Iterable[Text]) -> Group:
    header = Text(title, style="k")
    return Group(header, *lines)


def print_banner(project_root: Path, *, version: str) -> None:
    st = gather_banner_status(project_root)

    # Prevent ASCII logo from wrapping; crop on narrow terminals.
    logo = Group(
        *[
            Text(line, style="accent", overflow="crop", no_wrap=True)
            for line in _openapply_logo()
        ]
    )
    left = Group(
        logo,
        Text.assemble(("openapply", "accent"), ("  ", "muted"), (f"v{version}", "muted")),
        Text("local-first job agent", style="muted"),
    )

    getting_started = _section(
        "Getting started",
        [
            _cmd_line("openapply setup", "first-time wizard"),
            _cmd_line("openapply doctor", "health checks"),
            _cmd_line("openapply scan --limit 5 --link-limit 30", "discover jobs (after enabling ≥1 portal)"),
        ],
    )

    agent_tools = _section(
        "Agent tools",
        [
            _cmd_line("openapply pipeline <url-or-jd>", "evaluate + report + CV PDF + cover letter"),
            _cmd_line("openapply apply <url-or-jd>", "interactive apply flow (HITL)"),
            _cmd_line("openapply batch --min-score B --limit 20", "process queue in parallel"),
            _cmd_line("openapply tracker", "dashboard"),
        ],
    )

    extras = _section(
        "Extras",
        [
            _cmd_line("openapply research <job-id>", "company/role research report"),
            _cmd_line("openapply outreach <job-id>", "draft a DM/email"),
            _cmd_line("openapply compare 12,15,22", "compare offers/jobs"),
            _cmd_line("openapply learn <job-id> <outcome>", "log outcomes"),
            _cmd_line("openapply update", "update openapply to latest"),
            _cmd_line("openapply reset", "reset local workspace (destructive)"),
        ],
    )

    status_table = Table.grid(padding=(0, 1))
    status_table.add_column(justify="left")
    status_table.add_column(justify="left")
    status_table.add_row(ok_mark(st.config_ok), Text("config.yml", style="k"))
    status_table.add_row(ok_mark(st.cv_ok), Text("cv.md", style="k"))
    portals_label = Text.assemble(
        ("portals.yml", "k"),
        ("  ", "muted"),
        (f"({st.portals_active} active)", "muted"),
    )
    status_table.add_row(ok_mark(st.portals_ok), portals_label)
    status_table.add_row(ok_mark(st.ollama_ok), Text.assemble(("Ollama", "k"), ("  ", "muted"), (st.ollama_url, "muted")))

    right = Group(
        getting_started,
        Text(""),
        agent_tools,
        Text(""),
        extras,
        Text(""),
        Group(Text("Status", style="k"), status_table),
    )

    # Two-column when there's room; otherwise stack vertically.
    width = console.size.width
    if width < 110:
        content = Group(left, Text(""), right)
    else:
        content = Columns([left, right], equal=False, expand=True, padding=(0, 4))

    title = Text.assemble(("openapply", "accent"), ("  ", "muted"), (f"v{version}", "muted"))
    console.print("")
    console.print(panel(str(title), content))
    console.print("")

