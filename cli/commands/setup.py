from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path
from typing import Any

import httpx
import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from memory.db import create_sqlite_engine, initialize_database

console = Console()


def _workspace_root() -> Path:
    return Path.cwd()


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _save_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def _fetch_ollama_models(base_url: str) -> list[str]:
    endpoint = f"{base_url.rstrip('/')}/api/tags"
    response = httpx.get(endpoint, timeout=8)
    response.raise_for_status()
    body = response.json()
    models = body.get("models", []) if isinstance(body, dict) else []
    names: list[str] = []

    for model in models:
        if not isinstance(model, dict):
            continue
        name = str(model.get("name", "")).strip()
        if name:
            names.append(name)

    return names


def _pick_model(models: list[str], prompt_text: str, default: str) -> str:
    if not models:
        return typer.prompt(prompt_text, default=default).strip()

    table = Table(title="Available Ollama Models")
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Model", style="green")
    for idx, model in enumerate(models, start=1):
        table.add_row(str(idx), model)
    console.print(table)

    choice = typer.prompt(
        f"{prompt_text} (enter number or model name)",
        default=default,
    ).strip()

    if choice.isdigit():
        selected = int(choice)
        if 1 <= selected <= len(models):
            return models[selected - 1]

    return choice or default


def _setup_cv(cv_path: Path, config: dict[str, Any]) -> None:
    if cv_path.exists():
        console.print(f"[green]Found existing CV:[/green] {cv_path.as_posix()}")
        return

    console.print("[yellow]cv.md not found.[/yellow] Choose how to create it:")
    console.print("1) Paste CV markdown")
    console.print("2) Paste LinkedIn URL")
    console.print("3) Describe experience (AI-ready notes)")

    mode = typer.prompt("Select option", default="1").strip()

    if mode == "1":
        edited = typer.edit("# Your Name\n\n## Summary\nPaste your CV markdown here.\n")
        content = edited if edited and edited.strip() else "# Your Name\n\n## Summary\n"
        cv_path.write_text(content.strip() + "\n", encoding="utf-8")
        console.print("[green]Created cv.md from pasted markdown.[/green]")
        return

    if mode == "2":
        linkedin_url = typer.prompt("Paste LinkedIn URL", default="").strip()
        profile = config.setdefault("profile", {})
        if isinstance(profile, dict):
            profile["linkedin"] = linkedin_url

        template = (
            "# Your Name\n\n"
            "## Header\n"
            f"linkedin: {linkedin_url}\n\n"
            "## Summary\n"
            "Profile imported from LinkedIn URL. Expand this into a full CV before applying.\n\n"
            "## Experience\n"
            "### Company Name\n"
            "Role Title\n"
            "Dates\n"
            "- Achievement and impact bullet\n"
        )
        cv_path.write_text(template, encoding="utf-8")
        console.print("[green]Created starter cv.md from LinkedIn URL.[/green]")
        return

    notes = typer.edit(
        "Describe your experience, major projects, tech stack, and target roles here.\n"
    )
    notes_text = notes.strip() if notes else ""
    cv_path.write_text(
        "# Your Name\n\n"
        "## Summary\n"
        "Draft generated from your notes. Refine before applying.\n\n"
        "## Experience\n"
        "### Background Notes\n"
        +
        (notes_text or "- Add your experience details here.")
        + "\n",
        encoding="utf-8",
    )
    console.print("[green]Created cv.md from experience notes.[/green]")


def _setup_targets(config: dict[str, Any]) -> None:
    targets = config.setdefault("targets", {})
    if not isinstance(targets, dict):
        targets = {}
        config["targets"] = targets

    roles_input = typer.prompt(
        "Target roles (comma-separated)",
        default=", ".join(targets.get("roles", ["Software Engineer"]))
        if isinstance(targets.get("roles"), list)
        else "Software Engineer",
    )
    roles = [r.strip() for r in roles_input.split(",") if r.strip()]
    if roles:
        targets["roles"] = roles

    salary_min = typer.prompt("Salary min", default=str(targets.get("salary_min", 0))).strip()
    salary_max = typer.prompt("Salary max", default=str(targets.get("salary_max", 0))).strip()
    currency = typer.prompt("Currency", default=str(targets.get("currency", "USD"))).strip() or "USD"

    remote_only = typer.confirm("Remote only?", default=bool(targets.get("remote_only", False)))
    locations_input = typer.prompt(
        "Preferred locations (comma-separated, blank for none)",
        default=", ".join(targets.get("locations", []))
        if isinstance(targets.get("locations"), list)
        else "",
    )

    targets["salary_min"] = int(salary_min) if salary_min.isdigit() else 0
    targets["salary_max"] = int(salary_max) if salary_max.isdigit() else 0
    targets["currency"] = currency
    targets["remote_only"] = remote_only
    targets["locations"] = [loc.strip() for loc in locations_input.split(",") if loc.strip()]


def _setup_ollama(config: dict[str, Any]) -> None:
    ollama_cfg = config.setdefault("ollama", {})
    if not isinstance(ollama_cfg, dict):
        ollama_cfg = {}
        config["ollama"] = ollama_cfg

    base_url = str(ollama_cfg.get("base_url", "http://localhost:11434")).strip() or "http://localhost:11434"
    ollama_cfg["base_url"] = base_url

    console.print("[bold]Checking Ollama availability...[/bold]")
    models: list[str] = []
    try:
        models = _fetch_ollama_models(base_url)
        console.print(f"[green]Ollama is online.[/green] Found {len(models)} model(s).")
    except Exception as exc:
        console.print(
            "[yellow]Ollama appears offline or unreachable.[/yellow] "
            "You can still finish setup and update models later with config command."
        )
        console.print(f"[dim]Details: {exc}[/dim]")

    default_eval = str(ollama_cfg.get("evaluate_model", "llama3.1:8b"))
    default_gen = str(ollama_cfg.get("generate_model", "qwen2.5:14b"))

    ollama_cfg["evaluate_model"] = _pick_model(models, "Select evaluation model", default_eval)
    ollama_cfg["generate_model"] = _pick_model(models, "Select generation model", default_gen)
    ollama_cfg["stream"] = bool(ollama_cfg.get("stream", True))


def _initialize_db(project_root: Path) -> None:
    engine = create_sqlite_engine()
    initialize_database(engine)
    console.print(f"[green]Database initialized:[/green] {(project_root / 'data' / 'openapply.db').as_posix()}")


def run_setup() -> None:
    """Interactive first-run wizard."""
    root = _workspace_root()
    config_path = root / "config.yml"
    cv_path = root / "cv.md"
    portals_path = root / "portals.yml"

    console.print(Panel.fit("Open Apply Setup Wizard", border_style="cyan"))

    if not config_path.exists():
        config_example = root / "config.example.yml"
        if config_example.exists():
            shutil.copyfile(config_example, config_path)
            console.print("[green]Copied config.example.yml -> config.yml[/green]")
        else:
            try:
                bundled = files("cli").joinpath("assets/config.example.yml")
                config_path.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
                console.print("[green]Created config.yml from packaged defaults.[/green]")
            except Exception as exc:
                raise typer.BadParameter(
                    "No config template found. Reinstall package or create config.yml manually."
                ) from exc

    if not portals_path.exists():
        portals_example = root / "portals.example.yml"
        if portals_example.exists():
            # Do not overwrite a user-edited portals.yml (only bootstrap when missing).
            shutil.copyfile(portals_example, portals_path)
            console.print("[green]Copied portals.example.yml -> portals.yml[/green]")
        else:
            try:
                bundled = files("cli").joinpath("assets/portals.example.yml")
                portals_path.write_text(bundled.read_text(encoding="utf-8"), encoding="utf-8")
                console.print("[green]Created portals.yml from packaged defaults.[/green]")
            except Exception:
                # portals.yml is optional; scanning can still work via DB portal rows.
                pass

    config = _load_yaml(config_path)
    _setup_ollama(config)
    _setup_cv(cv_path, config)
    _setup_targets(config)

    _save_yaml(config_path, config)
    _initialize_db(root)

    console.print("")
    console.print("[bold green]You're ready. Run:[/bold green] [cyan]openapply scan[/cyan]")


def command() -> None:
    """First-run setup wizard.

    Examples:
      openapply setup
    """
    run_setup()
