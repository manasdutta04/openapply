from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class PipelineState:
    pending: list[str]
    processed: list[str]


def ensure_pipeline_file(project_root: Path) -> Path:
    path = project_root / "data" / "pipeline.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path

    path.write_text(
        "# Open Apply - Processing Queue\n\n"
        "Add job URLs here, one per line.\n"
        "Run: openapply batch\n\n"
        "## Pending\n"
        "\n"
        "## Processed\n"
        "(auto-moved here after processing)\n",
        encoding="utf-8",
    )
    return path


def load_pipeline(path: Path) -> PipelineState:
    text = path.read_text(encoding="utf-8")
    pending: list[str] = []
    processed: list[str] = []

    section: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower() == "## pending":
            section = "pending"
            continue
        if stripped.lower() == "## processed":
            section = "processed"
            continue
        if not stripped.startswith("- "):
            continue

        url = stripped[2:].strip()
        if not url:
            continue

        if section == "pending":
            pending.append(url)
        elif section == "processed":
            processed.append(url)

    return PipelineState(pending=pending, processed=processed)


def save_pipeline(path: Path, state: PipelineState) -> None:
    lines: list[str] = []
    lines.append("# Open Apply - Processing Queue")
    lines.append("")
    lines.append("Add job URLs here, one per line.")
    lines.append("Run: openapply batch")
    lines.append("")
    lines.append("## Pending")
    if state.pending:
        for url in state.pending:
            lines.append(f"- {url}")
    else:
        lines.append("(empty)")

    lines.append("")
    lines.append("## Processed")
    if state.processed:
        for url in state.processed:
            lines.append(f"- {url}")
    else:
        lines.append("(auto-moved here after processing)")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def append_pending(project_root: Path, urls: list[str]) -> int:
    path = ensure_pipeline_file(project_root)
    state = load_pipeline(path)
    existing = set(state.pending) | set(state.processed)

    additions = [url for url in urls if url not in existing]
    if not additions:
        return 0

    state.pending = dedupe_keep_order(state.pending + additions)
    save_pipeline(path, state)
    return len(additions)

