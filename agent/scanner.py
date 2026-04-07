from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from memory.db import Job, Portal

from .scan_history import append_scan_history_row, parse_scan_history_keys
from .portals_config import PortalSpec, load_portals_config
from .ollama_client import OllamaClient, OllamaClientError
from .scraper import JobScraper, ScraperError
from .url_utils import attach_query, normalized_role_company


@dataclass(slots=True)
class DiscoveredJob:
    portal_name: str
    portal_type: str
    url: str
    company: str
    role: str
    description: str


@dataclass(slots=True)
class ScanResult:
    discovered: list[DiscoveredJob]
    inserted_job_ids: list[int]
    skipped_duplicates: int


class JobScanner:
    """Autonomous scanner for active job portals with dedup and persistence."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        ollama_client: OllamaClient,
        scraper: JobScraper,
        project_root: str | Path = ".",
        prompt_path: str | Path = "agent/prompts/scan_query.md",
    ) -> None:
        self._session_factory = session_factory
        self._ollama_client = ollama_client
        self._scraper = scraper
        self._project_root = Path(project_root)
        self._prompt_path = Path(prompt_path)

    async def scan(self, max_links_per_portal: int = 30, max_jobs_per_portal: int = 8) -> ScanResult:
        portals = self._load_active_portals()
        if not portals:
            return ScanResult(discovered=[], inserted_job_ids=[], skipped_duplicates=0)

        existing_urls, existing_role_company = self._load_existing_job_keys()
        history = parse_scan_history_keys(self._project_root)
        history_urls, history_role_company = history.urls, history.role_company

        discovered: list[DiscoveredJob] = []
        inserted_job_ids: list[int] = []
        skipped_duplicates = 0

        for portal in portals:
            queries = await self._queries_for_portal(portal)
            listing_links = await self._discover_links(portal, queries=queries, limit=max_links_per_portal)

            processed_portal_jobs = 0
            for job_url in listing_links:
                if processed_portal_jobs >= max_jobs_per_portal:
                    break

                if job_url in existing_urls or job_url in history_urls:
                    skipped_duplicates += 1
                    append_scan_history_row(self._project_root, portal.name, "", "", job_url, "duplicate")
                    continue

                try:
                    jd = await self._scraper.scrape_jd(job_url)
                except ScraperError:
                    append_scan_history_row(self._project_root, portal.name, "", "", job_url, "error")
                    continue

                company = str(jd.get("company", "")).strip() or self._extract_company_from_url(job_url) or "Unknown"
                role = str(jd.get("title", "")).strip() or "Unknown"
                description = str(jd.get("description", "")).strip()
                normalized_key = normalized_role_company(company, role)

                if normalized_key in existing_role_company or normalized_key in history_role_company:
                    skipped_duplicates += 1
                    append_scan_history_row(self._project_root, portal.name, company, role, job_url, "duplicate")
                    continue

                inserted_id = self._insert_job(
                    url=job_url,
                    company=company,
                    role=role,
                    jd_text=description,
                )

                if inserted_id is None:
                    skipped_duplicates += 1
                    append_scan_history_row(self._project_root, portal.name, company, role, job_url, "duplicate")
                    continue

                discovered.append(
                    DiscoveredJob(
                        portal_name=portal.name,
                        portal_type=portal.type,
                        url=job_url,
                        company=company,
                        role=role,
                        description=description,
                    )
                )
                inserted_job_ids.append(inserted_id)
                processed_portal_jobs += 1

                existing_urls.add(job_url)
                existing_role_company.add(normalized_key)
                append_scan_history_row(self._project_root, portal.name, company, role, job_url, "new")

        return ScanResult(
            discovered=discovered,
            inserted_job_ids=inserted_job_ids,
            skipped_duplicates=skipped_duplicates,
        )

    def _load_active_portals(self) -> list[PortalSpec]:
        cfg = load_portals_config(self._project_root)
        if cfg is not None:
            return cfg.active_portals()

        with self._session_factory() as session:
            rows = session.scalars(select(Portal).where(Portal.active.is_(True))).all()

        return [
            PortalSpec(
                name=row.name,
                type=row.type,
                url=row.url,
                active=bool(row.active),
                query_param="q",
                search_queries=None,
            )
            for row in rows
        ]

    def _load_existing_job_keys(self) -> tuple[set[str], set[str]]:
        urls: set[str] = set()
        role_company: set[str] = set()

        with self._session_factory() as session:
            rows = session.scalars(select(Job)).all()
            for row in rows:
                urls.add(row.url)
                role_company.add(normalized_role_company(row.company or "", row.role or ""))

        return urls, role_company

    async def _queries_for_portal(self, portal: PortalSpec) -> list[str]:
        # portals.yml can provide curated search queries per portal.
        # Semantics:
        # - search_queries == None: generate via LLM prompt
        # - search_queries == []: do not add query variants (scan only portal.url)
        # - search_queries == [..]: use exactly those queries (plus portal.url)
        if portal.search_queries is not None:
            return portal.search_queries

        prompt_file = self._project_root / self._prompt_path
        if prompt_file.exists():
            prompt_template = prompt_file.read_text(encoding="utf-8")
        else:
            bundled_prompt = files("agent").joinpath(f"prompts/{self._prompt_path.name}")
            if not bundled_prompt.is_file():
                return []
            prompt_template = bundled_prompt.read_text(encoding="utf-8")

        with self._session_factory() as session:
            targets = self._load_targets_from_config()
            recent_jobs = session.scalars(select(Job).order_by(Job.scraped_at.desc()).limit(20)).all()

        history = [
            {"company": row.company, "role": row.role, "url": row.url}
            for row in recent_jobs
        ]

        prompt = prompt_template.format(
            targets_json=json.dumps(targets, ensure_ascii=True, indent=2),
            history_json=json.dumps(history, ensure_ascii=True, indent=2),
            portal_json=json.dumps(
                {"name": portal.name, "url": portal.url, "type": portal.type},
                ensure_ascii=True,
                indent=2,
            ),
        )

        try:
            payload = await self._ollama_client.complete_json(
                system_prompt="You generate concise and portal-friendly search queries. Return JSON only.",
                user_prompt=prompt,
            )
        except OllamaClientError:
            return []

        raw_queries = payload.get("queries", [])
        if not isinstance(raw_queries, list):
            return []

        result: list[str] = []
        for item in raw_queries:
            if not isinstance(item, dict):
                continue
            query = str(item.get("query", "")).strip()
            if query:
                result.append(query)

        return result[:8]

    async def _discover_links(self, portal: PortalSpec, queries: list[str], limit: int) -> list[str]:
        targets = [portal.url]
        for query in queries[:4]:
            targets.append(attach_query(portal.url, query_param=portal.query_param, query=query))

        links: list[str] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for target in targets:
                try:
                    response = await client.get(target)
                except httpx.HTTPError:
                    continue

                html = response.text
                extracted = self._extract_links(base_url=str(response.url), html=html)
                for link in extracted:
                    if link in seen:
                        continue
                    seen.add(link)
                    links.append(link)
                    if len(links) >= limit:
                        return links

        return links

    @staticmethod
    def _extract_links(base_url: str, html: str) -> list[str]:
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
        links: list[str] = []
        blocked_ext = (
            ".css", ".js", ".json", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg",
            ".ico", ".webp", ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip",
        )

        for href in hrefs:
            absolute = urljoin(base_url, href)
            normalized = absolute.split("#", 1)[0]

            if not normalized.startswith("http"):
                continue
            lowered = normalized.lower()
            parsed = urlparse(normalized)
            path = parsed.path.lower()

            if "/assets/" in path or path.endswith(blocked_ext):
                continue
            if "cdn.greenhouse.io" in parsed.netloc.lower():
                continue

            if any(tag in lowered for tag in ("/job", "/jobs", "greenhouse", "lever", "ashby", "workable")):
                links.append(normalized)

        return links

    def _insert_job(self, url: str, company: str, role: str, jd_text: str) -> int | None:
        with self._session_factory() as session:
            row = Job(
                url=url,
                company=company,
                role=role,
                jd_raw=jd_text,
                jd_extracted=jd_text,
                scraped_at=datetime.now(timezone.utc),
                status="new",
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                # Another scan pass or process may have inserted this URL already.
                return None
            session.refresh(row)
            return row.id

    @staticmethod
    def _extract_company_from_url(url: str) -> str | None:
        """Extract company name from job URL domain."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        
        # Extract company name from known job portal domains
        mapping = {
            "boards.greenhouse.io": lambda path: path.split("/")[1] if path.startswith("/") else None,
            "jobs.lever.co": lambda path: path.split("/")[1] if path.startswith("/") else None,
            "jobs.ashbyhq.com": lambda path: path.split("/")[1] if path.startswith("/") else None,
        }
        
        if domain in mapping:
            company = mapping[domain](parsed.path)
            if company:
                return company.capitalize()
        
        return None

    def _load_targets_from_config(self) -> dict[str, Any]:
        config_path = self._project_root / "config.yml"
        if not config_path.exists():
            return {"roles": [], "locations": [], "remote_only": False}

        import yaml

        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        targets = payload.get("targets", {}) if isinstance(payload, dict) else {}
        return targets if isinstance(targets, dict) else {"roles": [], "locations": [], "remote_only": False}
