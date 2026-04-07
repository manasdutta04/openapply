from __future__ import annotations

import typer
from rich.console import Console

from cli.commands import apply, batch, compare, doctor, learn, outreach, pipeline, research, scan, setup, tracker

app = typer.Typer(
    help=(
        "Open Apply CLI.\n\n"
        "Examples:\n"
        "  openapply setup\n"
        "  openapply apply <url-or-jd-text>\n"
        "  openapply scan [--auto]\n"
        "  openapply batch [--min-score B] [--limit 20]\n"
        "  openapply learn <job-id> <outcome>\n"
        "  openapply tracker\n"
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


@app.command(
    "doctor",
    help="Run setup and dependency health checks.",
)
def doctor_command() -> None:
    doctor.command()


@app.command(
    "pipeline",
    help=(
        "Auto pipeline: evaluate + generate report + CV PDF + cover letter.\n\n"
        "Examples:\n"
        "  openapply pipeline https://boards.greenhouse.io/company/jobs/123\n"
        "  openapply pipeline \"Senior Backend Engineer ...\""
    ),
)
def pipeline_command(target: str = typer.Argument(..., help="Job URL or raw JD text.")) -> None:
    pipeline.command(target)

@app.command(
    "research",
    help="Generate a company/role research report for a job ID.",
)
def research_command(job_id: int = typer.Argument(..., help="Job ID.")) -> None:
    research.command(job_id)


@app.command(
    "outreach",
    help="Draft a LinkedIn DM or email for a job ID.",
)
def outreach_command(
    job_id: int = typer.Argument(..., help="Job ID."),
    channel: str = typer.Option("linkedin", "--channel", help="linkedin|email"),
) -> None:
    outreach.command(job_id, channel=channel)


@app.command(
    "compare",
    help="Compare 2+ jobs by ID and recommend priority.",
)
def compare_command(job_ids: str = typer.Argument(..., help="Comma-separated job IDs, e.g. '12,15,22'")) -> None:
    compare.command(job_ids)


@app.command(
    "apply",
    help=(
        "Evaluate and process one job URL or JD text.\n\n"
        "Examples:\n"
        "  openapply apply https://boards.greenhouse.io/company/jobs/123\n"
        "  openapply apply \"Senior Backend Engineer ...\""
    ),
)
def apply_command(target: str = typer.Argument(..., help="Job URL or raw JD text.")) -> None:
    apply.command(target)


@app.command(
    "tracker",
    help=(
        "Interactive dashboard for applications and outcomes.\n\n"
        "Examples:\n"
        "  openapply tracker\n"
        "  openapply tracker --grade B --status applied --sort-by score"
    ),
)
def tracker_command(
    grade: str | None = typer.Option(None, "--grade", help="Filter by grade (A/B/C/D/F)."),
    status: str | None = typer.Option(None, "--status", help="Filter by status."),
    sort_by: str = typer.Option("date", "--sort-by", help="Sort by score/date/company."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Print rows and exit (for smoke tests/CI)."),
) -> None:
    tracker.command(
        grade=grade,
        status=status,
        sort_by=sort_by,  # type: ignore[arg-type]
        non_interactive=non_interactive,
    )


@app.command(
    "scan",
    help=(
        "Discover jobs across configured portals.\n\n"
        "Examples:\n"
        "  openapply scan\n"
        "  openapply scan --auto"
    ),
)
def scan_command(
    auto: bool = typer.Option(False, "--auto", help="Evaluate discovered jobs and queue B+ matches."),
    limit: int = typer.Option(8, "--limit", min=1, help="Max jobs to scrape per portal."),
    link_limit: int = typer.Option(30, "--link-limit", min=1, help="Max listing links to consider per portal."),
) -> None:
    scan.command(auto=auto, limit=limit, link_limit=link_limit)


@app.command(
    "batch",
    help=(
        "Process pipeline queue in parallel.\n\n"
        "Examples:\n"
        "  openapply batch\n"
        "  openapply batch --min-score B --limit 20"
    ),
)
def batch_command(
    min_score: str = typer.Option("B", "--min-score", help="Minimum grade to generate CV."),
    limit: int = typer.Option(20, "--limit", min=1, help="Max pending URLs to process."),
) -> None:
    batch.command(min_score=min_score, limit=limit)


@app.command(
    "learn",
    help=(
        "Log outcome and update scoring weights.\n\n"
        "Examples:\n"
        "  openapply learn 42 interview\n"
        "  openapply learn 42 rejected --notes \"Lost to stronger domain fit\""
    ),
)
def learn_command(
    job_id: int = typer.Argument(..., help="Job ID."),
    outcome: str = typer.Argument(..., help="interview|rejected|offer|ghosted"),
    notes: str = typer.Option("", "--notes", help="Optional outcome note."),
) -> None:
    learn.command(job_id=job_id, outcome=outcome, notes=notes)


if __name__ == "__main__":
    app()
