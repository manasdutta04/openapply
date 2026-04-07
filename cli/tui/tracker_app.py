from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from rich.text import Text
from sqlalchemy import desc, select

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, ListItem, ListView, Static, TabbedContent, TabPane

from memory.db import Application, Evaluation, Job, Outcome, build_session_factory, create_sqlite_engine, initialize_database


SortBy = Literal["score", "date", "company"]
StatusFilter = Literal["all", "evaluated", "applied", "interview", "top", "skipped"]


@dataclass(slots=True)
class TrackerRow:
    job_id: int
    company: str
    role: str
    score: float | None
    grade: str | None
    status: str
    date: datetime
    report_path: str | None
    url: str


def fetch_rows(sort_by: SortBy) -> list[TrackerRow]:
    engine = create_sqlite_engine()
    initialize_database(engine)
    session_factory = build_session_factory(engine)

    with session_factory() as session:
        jobs = session.scalars(select(Job).order_by(desc(Job.scraped_at))).all()
        rows: list[TrackerRow] = []

        for job in jobs:
            eval_row = session.scalars(
                select(Evaluation).where(Evaluation.job_id == job.id).order_by(desc(Evaluation.id)).limit(1)
            ).first()

            app_row = session.scalars(
                select(Application).where(Application.job_id == job.id).order_by(desc(Application.id)).limit(1)
            ).first()

            effective_status = app_row.outcome if app_row is not None else job.status
            score = eval_row.score_total if eval_row is not None else None
            grade_value = eval_row.grade if eval_row is not None else None
            report_path = eval_row.report_path if eval_row is not None else None

            date_value = job.scraped_at.replace(tzinfo=None) if job.scraped_at.tzinfo else job.scraped_at

            rows.append(
                TrackerRow(
                    job_id=job.id,
                    company=job.company or "Unknown",
                    role=job.role or "Unknown",
                    score=score,
                    grade=grade_value,
                    status=effective_status,
                    date=date_value,
                    report_path=report_path,
                    url=job.url,
                )
            )

    if sort_by == "score":
        rows.sort(key=lambda row: row.score if row.score is not None else -1.0, reverse=True)
    elif sort_by == "company":
        rows.sort(key=lambda row: row.company.lower())
    else:
        rows.sort(key=lambda row: row.date, reverse=True)

    return rows


def apply_filter(rows: list[TrackerRow], tab: StatusFilter, grade: str | None, status: str | None) -> list[TrackerRow]:
    out: list[TrackerRow] = []
    for row in rows:
        if grade and (row.grade or "").upper() != grade.upper():
            continue
        if status and row.status.lower() != status.lower():
            continue

        if tab == "all":
            out.append(row)
        elif tab == "evaluated":
            if row.status in {"evaluated", "new"}:
                out.append(row)
        elif tab == "applied":
            if row.status == "applied":
                out.append(row)
        elif tab == "interview":
            if row.status == "interview":
                out.append(row)
        elif tab == "skipped":
            if row.status == "skipped":
                out.append(row)
        elif tab == "top":
            if (row.score or 0.0) >= 4.0:
                out.append(row)

    return out


class ActionPalette(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss", "Close", show=False)]

    class Picked(Message):
        def __init__(self, action_id: str) -> None:
            super().__init__()
            self.action_id = action_id

    def __init__(self, actions: list[tuple[str, str]]) -> None:
        super().__init__()
        self._actions = actions

    def compose(self) -> ComposeResult:
        yield Container(
            Static("Command palette", classes="title"),
            Input(placeholder="Type to filter actions…", id="q"),
            ListView(id="list"),
            id="palette",
        )

    def on_mount(self) -> None:
        self.query_one("#q", Input).focus()
        self._render("")

    @on(Input.Changed, "#q")
    def _changed(self, event: Input.Changed) -> None:
        self._render(event.value)

    def _render(self, query: str) -> None:
        lv = self.query_one("#list", ListView)
        lv.clear()
        q = query.strip().lower()
        for action_id, label in self._actions:
            if q and q not in label.lower():
                continue
            lv.append(ListItem(Static(label), id=action_id))

    @on(ListView.Selected)
    def _selected(self, event: ListView.Selected) -> None:
        if event.item is None or event.item.id is None:
            return
        self.dismiss(event.item.id)


class OutcomeModal(ModalScreen[tuple[str, str] | None]):
    BINDINGS = [Binding("escape", "dismiss", "Close", show=False)]

    def compose(self) -> ComposeResult:
        yield Container(
            Static("Log outcome", classes="title"),
            Static("Outcome: interview / rejected / offer / ghosted"),
            Input(placeholder="Outcome (interview)", id="outcome"),
            Input(placeholder="Notes (optional)", id="notes"),
            Static("Press Enter to save, Esc to cancel.", classes="hint"),
            id="outcome_modal",
        )

    def on_mount(self) -> None:
        self.query_one("#outcome", Input).value = "interview"
        self.query_one("#outcome", Input).focus()

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        outcome = self.query_one("#outcome", Input).value.strip().lower()
        notes = self.query_one("#notes", Input).value.strip()
        if outcome not in {"interview", "rejected", "offer", "ghosted"}:
            return
        self.dismiss((outcome, notes))


class TrackerApp(App):
    CSS = """
    #body { height: 1fr; }
    #list { height: 1fr; }
    #preview { height: 1fr; overflow: auto; }
    #palette, #outcome_modal { width: 80%; max-width: 90; padding: 1; border: heavy $accent; background: $panel; }
    .title { content-align: center middle; padding: 0 0 1 0; }
    .hint { color: $text-muted; padding-top: 1; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+p", "palette", "Palette"),
        Binding("enter", "open_report", "Open report"),
        Binding("a", "apply", "Apply"),
        Binding("l", "log_outcome", "Log outcome"),
        Binding("?", "help", "Help"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, *, sort_by: SortBy = "date", grade: str | None = None, status: str | None = None) -> None:
        super().__init__()
        self._sort_by: SortBy = sort_by
        self._grade = grade
        self._status = status
        self._tab: StatusFilter = "all"
        self._rows_all: list[TrackerRow] = []
        self._rows_view: list[TrackerRow] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="body"):
            with TabbedContent(id="tabs"):
                yield TabPane("All", id="all")
                yield TabPane("Evaluated", id="evaluated")
                yield TabPane("Applied", id="applied")
                yield TabPane("Interview", id="interview")
                yield TabPane("Top ≥4.0", id="top")
                yield TabPane("Skipped", id="skipped")

            with Horizontal():
                yield ListView(id="list")
                yield Static("", id="preview")
        yield Footer()

    def on_mount(self) -> None:
        self.action_reload()

    def action_reload(self) -> None:
        self._rows_all = fetch_rows(self._sort_by)
        self._apply_view()

    @on(TabbedContent.TabActivated)
    def _tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab_id = (event.tab.id or "all").strip().lower()
        if tab_id in {"all", "evaluated", "applied", "interview", "top", "skipped"}:
            self._tab = tab_id  # type: ignore[assignment]
            self._apply_view()

    @on(ListView.Selected)
    def _selected(self, event: ListView.Selected) -> None:
        idx = self._selected_index()
        if idx is None:
            self.query_one("#preview", Static).update("")
            return
        self._render_preview(self._rows_view[idx])

    def _apply_view(self) -> None:
        self._rows_view = apply_filter(self._rows_all, self._tab, self._grade, self._status)
        lv = self.query_one("#list", ListView)
        lv.clear()
        for row in self._rows_view:
            score = f"{row.score:.2f}" if row.score is not None else "-"
            grade = row.grade or "-"
            label = f"{row.job_id:>4}  {row.company} — {row.role}   {score} {grade}   {row.status}"
            lv.append(ListItem(Static(label), id=str(row.job_id)))
        if self._rows_view:
            lv.index = 0
            self._render_preview(self._rows_view[0])
        else:
            self.query_one("#preview", Static).update("No rows match current filters.")

    def _selected_index(self) -> int | None:
        lv = self.query_one("#list", ListView)
        if lv.index is None:
            return None
        if lv.index < 0 or lv.index >= len(self._rows_view):
            return None
        return int(lv.index)

    def _render_preview(self, row: TrackerRow) -> None:
        parts: list[Text] = []
        header = Text.assemble(
            ("Job ", "bold"),
            (str(row.job_id), "bold cyan"),
            ("  ", ""),
            (row.company, "bold green"),
            (" — ", ""),
            (row.role, "bold"),
        )
        parts.append(header)
        meta = Text(
            f"{row.date.strftime('%Y-%m-%d')}  status={row.status}  score={(row.score or 0.0):.2f}  grade={row.grade or '-'}"
        )
        parts.append(meta)
        parts.append(Text(""))
        parts.append(Text(f"URL: {row.url}"))

        if row.report_path:
            path = Path(row.report_path)
            parts.append(Text(f"Report: {path.as_posix()}"))
            if path.exists():
                raw = path.read_text(encoding="utf-8")
                snippet = raw[:6000]
                parts.append(Text(""))
                parts.append(Text(snippet))
            else:
                parts.append(Text("Report file missing on disk."))
        else:
            parts.append(Text("No report yet."))

        preview = Text("\n").join(parts)
        self.query_one("#preview", Static).update(preview)

    async def action_palette(self) -> None:
        actions = [
            ("open_report", "Open report in preview (Enter)"),
            ("apply", "Run apply flow for selected (A)"),
            ("log_outcome", "Log outcome for selected (L)"),
            ("reload", "Reload from DB (R)"),
            ("quit", "Quit (Q)"),
        ]
        picked = await self.push_screen(ActionPalette(actions))
        if picked:
            if picked == "open_report":
                self.action_open_report()
            elif picked == "apply":
                await self.action_apply()
            elif picked == "log_outcome":
                await self.action_log_outcome()
            elif picked == "reload":
                self.action_reload()
            elif picked == "quit":
                self.action_quit()

    def action_open_report(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        self._render_preview(self._rows_view[idx])

    async def action_apply(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        row = self._rows_view[idx]
        if not row.url.startswith("http"):
            return
        # Run the existing CLI command in-process. This will block the UI while running.
        from cli.commands import apply as apply_command

        self.exit(message=f"Running apply flow for {row.url}…")
        apply_command.command(row.url)

    async def action_log_outcome(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        row = self._rows_view[idx]
        result = await self.push_screen(OutcomeModal())
        if result is None:
            return
        outcome, notes = result
        self._persist_outcome(job_id=row.job_id, outcome=outcome, notes=notes)
        self.action_reload()

    def _persist_outcome(self, job_id: int, outcome: str, notes: str) -> None:
        engine = create_sqlite_engine()
        initialize_database(engine)
        session_factory = build_session_factory(engine)
        with session_factory() as session:
            app_row = session.scalars(
                select(Application).where(Application.job_id == job_id).order_by(desc(Application.id)).limit(1)
            ).first()
            if app_row is None:
                app_row = Application(job_id=job_id, cv_id=None, auto_applied=False, human_reviewed=True, outcome=outcome)
                session.add(app_row)
                session.flush()
            else:
                app_row.outcome = outcome
                session.add(app_row)

            session.add(
                Outcome(
                    application_id=app_row.id,
                    outcome_type=outcome,
                    notes=notes or None,
                )
            )

            job = session.get(Job, job_id)
            if job is not None:
                if outcome in {"interview", "offer", "rejected", "ghosted"}:
                    job.status = outcome if outcome != "ghosted" else job.status
                session.add(job)

            session.commit()

    def action_help(self) -> None:
        self.notify(
            "Keys: ↑/↓ navigate • Enter preview • Ctrl+P palette • A apply • L log outcome • R reload • Q quit",
            timeout=6,
        )

