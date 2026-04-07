from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml


PORTAL_TYPES: Final[set[str]] = {"greenhouse", "ashby", "lever", "linkedin", "custom", "workable", "wellfound"}


@dataclass(slots=True)
class PortalSpec:
    name: str
    type: str
    url: str
    active: bool = True
    query_param: str = "q"
    search_queries: list[str] | None = None


@dataclass(slots=True)
class PortalsConfig:
    tracked_companies: list[PortalSpec]
    job_boards: list[PortalSpec]
    negative_keywords: list[str]

    def active_portals(self) -> list[PortalSpec]:
        portals = [*self.tracked_companies, *self.job_boards]
        return [p for p in portals if p.active]


def load_portals_config(project_root: str | Path = ".", filename: str = "portals.yml") -> PortalsConfig | None:
    root = Path(project_root)
    path = root / filename
    if not path.exists():
        return None

    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return None

    tracked = _load_portal_list(payload.get("tracked_companies"))
    boards = _load_portal_list(payload.get("job_boards"))
    negative = payload.get("negative_keywords", [])
    negative_keywords = (
        [str(item).strip() for item in negative if str(item).strip()]
        if isinstance(negative, list)
        else []
    )

    return PortalsConfig(
        tracked_companies=tracked,
        job_boards=boards,
        negative_keywords=negative_keywords,
    )


def _load_portal_list(value: Any) -> list[PortalSpec]:
    if not isinstance(value, list):
        return []

    portals: list[PortalSpec] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        ptype = str(item.get("type", "custom")).strip().lower()
        if not name or not url:
            continue
        if ptype not in PORTAL_TYPES:
            ptype = "custom"
        active = bool(item.get("active", True))
        query_param = str(item.get("query_param", "q")).strip() or "q"
        raw_queries = item.get("search_queries")
        search_queries: list[str] | None
        if raw_queries is None:
            search_queries = None
        elif isinstance(raw_queries, list):
            search_queries = [str(q).strip() for q in raw_queries if str(q).strip()]
        else:
            search_queries = []

        portals.append(
            PortalSpec(
                name=name,
                type=ptype,
                url=url,
                active=active,
                query_param=query_param,
                search_queries=search_queries,
            )
        )

    return portals

