from __future__ import annotations

import shutil
from pathlib import Path

import typer

from cli.ui import console, panel


def _rm_path(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except Exception:
        return False


def command(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
    keep_cv: bool = typer.Option(False, "--keep-cv", help="Keep cv.md."),
    keep_portals: bool = typer.Option(False, "--keep-portals", help="Keep portals.yml."),
    keep_config: bool = typer.Option(False, "--keep-config", help="Keep config.yml."),
) -> None:
    """Reset local OpenApply workspace files (destructive)."""
    root = Path.cwd()

    targets: list[tuple[str, Path, bool]] = [
        ("config.yml", root / "config.yml", keep_config),
        ("cv.md", root / "cv.md", keep_cv),
        ("portals.yml", root / "portals.yml", keep_portals),
        ("data/", root / "data", False),
        ("output/", root / "output", False),
        ("reports/", root / "reports", False),
    ]

    lines = []
    for label, path, keep in targets:
        if keep:
            lines.append(f"- keep: {label}")
        else:
            lines.append(f"- delete: {label}")

    console.print(panel("Reset", "\n".join(lines), subtitle="This deletes local data in the current folder"))

    if not yes:
        ok = typer.confirm("This will delete files/directories. Continue?", default=False)
        if not ok:
            console.print(panel("Reset", "[muted]Cancelled.[/muted]"))
            raise typer.Exit(code=0)

    deleted = 0
    failed: list[str] = []
    for label, path, keep in targets:
        if keep:
            continue
        if not path.exists():
            continue
        if _rm_path(path):
            deleted += 1
        else:
            failed.append(label)

    msg = f"Deleted {deleted} item(s)."
    if failed:
        msg += "\nFailed:\n" + "\n".join(f"- {x}" for x in failed)
    msg += "\n\nNext: openapply setup"
    console.print(panel("Done", msg))

