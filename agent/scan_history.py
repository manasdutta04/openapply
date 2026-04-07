from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .url_utils import normalized_role_company


@dataclass(slots=True)
class HistoryKeys:
    urls: set[str]
    role_company: set[str]


def scan_history_paths(project_root: str | Path) -> tuple[Path, Path]:
    root = Path(project_root)
    return root / "data" / "scan-history.tsv", root / "data" / "scan-history.md"


def ensure_scan_history_files(project_root: str | Path) -> None:
    tsv_path, _ = scan_history_paths(project_root)
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    if not tsv_path.exists():
        tsv_path.write_text("date\tportal\tcompany\trole\turl\taction\n", encoding="utf-8")


def parse_scan_history_keys(project_root: str | Path) -> HistoryKeys:
    tsv_path, md_path = scan_history_paths(project_root)
    urls: set[str] = set()
    role_company: set[str] = set()

    if tsv_path.exists():
        for line in tsv_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lower().startswith("date\t"):
                continue
            parts = [p.strip() for p in line.split("\t")]
            if len(parts) < 6:
                continue
            company = parts[2]
            role = parts[3]
            url = parts[4]
            if url:
                urls.add(url)
            role_company.add(normalized_role_company(company, role))

    if md_path.exists():
        for line in md_path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("|"):
                continue
            if "| Date |" in line or "|------" in line:
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 7:
                continue
            company = parts[3]
            role = parts[4]
            url = parts[5]
            if url:
                urls.add(url)
            role_company.add(normalized_role_company(company, role))

    if not tsv_path.exists() and not md_path.exists():
        ensure_scan_history_files(project_root)

    return HistoryKeys(urls=urls, role_company=role_company)


def append_scan_history_row(
    project_root: str | Path,
    portal: str,
    company: str,
    role: str,
    url: str,
    action: str,
    now: datetime | None = None,
) -> None:
    ensure_scan_history_files(project_root)
    tsv_path, md_path = scan_history_paths(project_root)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")

    with tsv_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp}\t{portal}\t{company}\t{role}\t{url}\t{action}\n")

    if md_path.exists():
        with md_path.open("a", encoding="utf-8") as handle:
            handle.write(f"| {stamp} | {portal} | {company} | {role} | {url} | {action} |\n")

