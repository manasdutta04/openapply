from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, sessionmaker

from memory.db import CV, Evaluation, Job

from .cv_builder import CVBuilder
from .evaluator import JobEvaluator
from .scraper import JobScraper


ProgressCallback = Callable[["BatchTaskResult"], Awaitable[None] | None]


@dataclass(slots=True)
class BatchTaskResult:
    url: str
    status: str
    job_id: int | None = None
    evaluation_id: int | None = None
    cv_id: int | None = None
    grade: str | None = None
    score: float | None = None
    error: str | None = None


@dataclass(slots=True)
class BatchRunResult:
    total: int
    processed: int
    succeeded: int
    filtered: int
    skipped: int
    failed: int
    results: list[BatchTaskResult]


class BatchProcessor:
    """Parallel scrape -> evaluate -> CV generation pipeline for URL queues."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        scraper: JobScraper,
        evaluator: JobEvaluator,
        cv_builder: CVBuilder,
        concurrency: int = 3,
    ) -> None:
        self._session_factory = session_factory
        self._scraper = scraper
        self._evaluator = evaluator
        self._cv_builder = cv_builder
        self._concurrency = max(1, concurrency)

    async def process_urls(
        self,
        urls: list[str],
        cv_content: str,
        min_grade: str = "B",
        progress_callback: ProgressCallback | None = None,
    ) -> BatchRunResult:
        queue: asyncio.Queue[str] = asyncio.Queue()
        for url in urls:
            queue.put_nowait(url)

        results: list[BatchTaskResult] = []
        results_lock = asyncio.Lock()

        async def worker() -> None:
            while True:
                try:
                    url = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

                result = await self._process_one(url=url, cv_content=cv_content, min_grade=min_grade)

                async with results_lock:
                    results.append(result)

                if progress_callback is not None:
                    maybe = progress_callback(result)
                    if asyncio.iscoroutine(maybe):
                        await maybe

                queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(self._concurrency)]
        await asyncio.gather(*workers)

        succeeded = sum(1 for row in results if row.status == "succeeded")
        filtered = sum(1 for row in results if row.status == "filtered")
        skipped = sum(1 for row in results if row.status == "skipped")
        failed = sum(1 for row in results if row.status == "failed")

        return BatchRunResult(
            total=len(urls),
            processed=len(results),
            succeeded=succeeded,
            filtered=filtered,
            skipped=skipped,
            failed=failed,
            results=results,
        )

    async def _process_one(self, url: str, cv_content: str, min_grade: str) -> BatchTaskResult:
        try:
            resume_state = self._resume_state(url)
            if resume_state is not None:
                return resume_state

            jd = await self._scraper.scrape_jd(url)
            job = self._upsert_job(
                url=url,
                company=str(jd.get("company", "")).strip() or None,
                role=str(jd.get("title", "")).strip() or None,
                description=str(jd.get("description", "")).strip(),
            )

            eval_result = await self._evaluator.evaluate_job(job.id, cv_content)
            if self._grade_rank(eval_result.grade) < self._grade_rank(min_grade):
                self._mark_job_skipped(job.id)
                return BatchTaskResult(
                    url=url,
                    status="filtered",
                    job_id=job.id,
                    evaluation_id=eval_result.evaluation_id,
                    grade=eval_result.grade,
                    score=eval_result.weighted_total,
                )

            if eval_result.evaluation_id is None:
                raise RuntimeError("Evaluation did not persist an evaluation_id.")

            cv_result = await self._cv_builder.build_for_job(job.id, eval_result.evaluation_id)
            return BatchTaskResult(
                url=url,
                status="succeeded",
                job_id=job.id,
                evaluation_id=eval_result.evaluation_id,
                cv_id=cv_result.cv_id,
                grade=eval_result.grade,
                score=eval_result.weighted_total,
            )
        except Exception as exc:
            return BatchTaskResult(url=url, status="failed", error=str(exc))

    def _resume_state(self, url: str) -> BatchTaskResult | None:
        with self._session_factory() as session:
            job = session.scalars(select(Job).where(Job.url == url)).first()
            if job is None:
                return None

            latest_eval = session.scalars(
                select(Evaluation).where(Evaluation.job_id == job.id).order_by(desc(Evaluation.id)).limit(1)
            ).first()
            latest_cv = session.scalars(
                select(CV).where(CV.job_id == job.id).order_by(desc(CV.id)).limit(1)
            ).first()

            if latest_eval is None:
                return None

            if latest_cv is not None:
                return BatchTaskResult(
                    url=url,
                    status="skipped",
                    job_id=job.id,
                    evaluation_id=latest_eval.id,
                    cv_id=latest_cv.id,
                    grade=latest_eval.grade,
                    score=latest_eval.score_total,
                )

            return None

    def _upsert_job(self, url: str, company: str | None, role: str | None, description: str) -> Job:
        with self._session_factory() as session:
            job = session.scalars(select(Job).where(Job.url == url)).first()
            if job is None:
                job = Job(
                    url=url,
                    company=company,
                    role=role,
                    jd_raw=description,
                    jd_extracted=description,
                    scraped_at=datetime.now(timezone.utc),
                    status="new",
                )
                session.add(job)
            else:
                job.company = company or job.company
                job.role = role or job.role
                job.jd_raw = description or job.jd_raw
                job.jd_extracted = description or job.jd_extracted
                job.scraped_at = datetime.now(timezone.utc)
                session.add(job)

            session.commit()
            session.refresh(job)
            return job

    def _mark_job_skipped(self, job_id: int) -> None:
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.status = "skipped"
            session.add(job)
            session.commit()

    @staticmethod
    def _grade_rank(grade: str) -> int:
        order = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}
        return order.get(grade.upper().strip(), 1)
