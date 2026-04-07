from __future__ import annotations

import typer
from cli.tui.tracker_app import TrackerApp, SortBy

def command(
    grade: str | None = typer.Option(None, "--grade", help="Filter by grade (A/B/C/D/F)."),
    status: str | None = typer.Option(None, "--status", help="Filter by status."),
    sort_by: SortBy = typer.Option("date", "--sort-by", help="Sort by: score, date, company."),
) -> None:
    """Interactive tracker dashboard.

    Examples:
      openapply tracker
      openapply tracker --grade B --status applied --sort-by score
      openapply tracker --date-from 2026-01-01 --date-to 2026-04-30
    """
    app = TrackerApp(sort_by=sort_by, grade=grade, status=status)
    app.run()
