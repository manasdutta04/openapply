from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from cli.tracker_store import SortBy, apply_filter, fetch_rows

console = Console()

def command(
    grade: str | None = typer.Option(None, "--grade", help="Filter by grade (A/B/C/D/F)."),
    status: str | None = typer.Option(None, "--status", help="Filter by status."),
    sort_by: SortBy = typer.Option("date", "--sort-by", help="Sort by: score, date, company."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Print rows and exit (for smoke tests/CI)."),
) -> None:
    """Interactive tracker dashboard.

    Examples:
      openapply tracker
      openapply tracker --grade B --status applied --sort-by score
      openapply tracker --date-from 2026-01-01 --date-to 2026-04-30
    """
    if non_interactive:
        rows = fetch_rows(sort_by)
        rows = apply_filter(rows, "all", grade, status)

        table = Table(title="Open Apply Tracker (non-interactive)")
        table.add_column("Job ID", justify="right", style="cyan")
        table.add_column("Company", style="green")
        table.add_column("Role")
        table.add_column("Score", justify="right")
        table.add_column("Grade", justify="center")
        table.add_column("Status")
        table.add_column("Date")

        for row in rows[:50]:
            table.add_row(
                str(row.job_id),
                row.company,
                row.role,
                f"{row.score:.2f}" if row.score is not None else "-",
                row.grade or "-",
                row.status,
                row.date.strftime("%Y-%m-%d"),
            )

        console.print(table)
        return

    try:
        from cli.tui.tracker_app import TrackerApp
    except ModuleNotFoundError as exc:
        raise typer.BadParameter(
            "Interactive tracker requires the 'textual' dependency. "
            "Install it by re-installing OpenApply (pip install -e .) or: pip install textual"
        ) from exc

    app = TrackerApp(sort_by=sort_by, grade=grade, status=status)
    app.run()
