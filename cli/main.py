from __future__ import annotations

import typer
from rich.console import Console

from cli.commands import setup

app = typer.Typer(
    help=(
        "Open Apply CLI.\n\n"
        "Examples:\n"
        "  openapply setup\n"
        "  openapply --help\n"
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()


@app.callback()
def main() -> None:
    """Open Apply command registry."""
    return None


@app.command(
    "setup",
    help=(
        "Run first-time setup wizard.\n\n"
        "Example:\n"
        "  openapply setup"
    ),
)
def setup_command() -> None:
    setup.command()


if __name__ == "__main__":
    app()
