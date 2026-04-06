from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session, sessionmaker

from memory.db import DEFAULT_SCORING_WEIGHTS, Evaluation, Job

from .ollama_client import OllamaClient, OllamaClientError


SCORE_KEYS: tuple[str, ...] = (
    "role_match",
    "skills_alignment",
    "seniority_fit",
    "compensation",
    "geographic",
    "company_stage",
    "product_market_fit",
    "growth_trajectory",
    "interview_likelihood",
    "timeline",
)


@dataclass(slots=True)
class EvaluationResult:
    job_id: int | None
    evaluation_id: int | None
    score_role_match: float
    score_skills_alignment: float
    score_seniority_fit: float
    score_compensation: float
    score_geographic: float
    score_company_stage: float
    score_product_market_fit: float
    score_growth_trajectory: float
    score_interview_likelihood: float
    score_timeline: float
    total: float
    grade: str
    summary: str
    top_strengths: list[str]
    key_gaps: list[str]
    recommendation: str
    report_path: Path
    evaluated_at: datetime
    model_used: str

    @property
    def weighted_total(self) -> float:
        return self.total

    @property
    def scores(self) -> dict[str, float]:
        return {
            "role_match": self.score_role_match,
            "skills_alignment": self.score_skills_alignment,
            "seniority_fit": self.score_seniority_fit,
            "compensation": self.score_compensation,
            "geographic": self.score_geographic,
            "company_stage": self.score_company_stage,
            "product_market_fit": self.score_product_market_fit,
            "growth_trajectory": self.score_growth_trajectory,
            "interview_likelihood": self.score_interview_likelihood,
            "timeline": self.score_timeline,
        }


class JobEvaluator:
    """Evaluate job descriptions against CV content using a 10-dimension framework."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        ollama_client: OllamaClient,
        project_root: str | Path = ".",
        prompt_path: str | Path = "agent/prompts/evaluate.md",
        config_path: str | Path = "config.yml",
        cv_path: str | Path = "cv.md",
    ) -> None:
        self._session_factory = session_factory
        self._ollama_client = ollama_client
        self._project_root = Path(project_root)
        self._prompt_path = Path(prompt_path)
        self._config_path = Path(config_path)
        self._cv_path = Path(cv_path)

    async def evaluate_job(self, job_id: int, cv_content: str | None = None) -> EvaluationResult:
        """Evaluate and persist results for a job already stored in SQLite."""
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"Job with id={job_id} was not found.")

            jd_content = (job.jd_extracted or job.jd_raw or "").strip()
            if not jd_content:
                raise ValueError("Job description is empty. Scrape or import JD before evaluation.")

            cv_text = cv_content or self._load_cv_content()
            weights = self._load_scoring_weights_from_config()
            payload = await self._evaluate_with_llm(cv_content=cv_text, jd_content=jd_content)

            result = self._build_result(
                payload=payload,
                weights=weights,
                company=job.company,
                job_id=job.id,
                model_used=self._model_used_label(),
            )

            report = self._render_markdown_report(
                result=result,
                weights=weights,
                company=job.company,
                role=job.role,
                url=job.url,
            )
            self._write_report(result.report_path, report)
            self._persist_evaluation(session=session, job=job, result=result)
            return result

    async def evaluate_text(
        self,
        cv_content: str | None,
        jd_content: str,
        company: str | None = None,
        role: str | None = None,
        url: str | None = None,
    ) -> EvaluationResult:
        """Evaluate raw JD text (no DB persistence of evaluation row)."""
        cv_text = cv_content or self._load_cv_content()
        weights = self._load_scoring_weights_from_config()
        payload = await self._evaluate_with_llm(cv_content=cv_text, jd_content=jd_content)

        result = self._build_result(
            payload=payload,
            weights=weights,
            company=company,
            job_id=None,
            model_used=self._model_used_label(),
        )

        report = self._render_markdown_report(
            result=result,
            weights=weights,
            company=company,
            role=role,
            url=url,
        )
        self._write_report(result.report_path, report)
        return result

    async def _evaluate_with_llm(self, cv_content: str, jd_content: str) -> dict[str, Any]:
        prompt_template = self._load_prompt_template()
        prompt_body = prompt_template.format(cv_content=cv_content, jd_content=jd_content)

        system_prompt = (
            "You are Open Apply's job-fit evaluator. "
            "Follow instructions exactly and return only valid JSON."
        )

        try:
            payload = await self._ollama_client.complete_json(system_prompt=system_prompt, user_prompt=prompt_body)
        except OllamaClientError:
            raise
        except Exception as exc:
            raise OllamaClientError(f"Evaluation call failed: {exc}") from exc

        if not isinstance(payload, dict):
            raise ValueError("Evaluator response must be a JSON object.")

        return payload

    def _load_prompt_template(self) -> str:
        prompt_file = self._project_root / self._prompt_path
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8")

        bundled_prompt = files("agent").joinpath(f"prompts/{self._prompt_path.name}")
        if bundled_prompt.is_file():
            return bundled_prompt.read_text(encoding="utf-8")

        raise FileNotFoundError(f"Missing prompt file: {prompt_file}")

    def _load_cv_content(self) -> str:
        cv_file = self._project_root / self._cv_path
        if not cv_file.exists():
            raise FileNotFoundError(f"Base CV not found at {cv_file}. Run setup or create cv.md first.")
        return cv_file.read_text(encoding="utf-8")

    def _load_scoring_weights_from_config(self) -> dict[str, float]:
        config_file = self._project_root / self._config_path
        if not config_file.exists():
            return self._normalize_weights(dict(DEFAULT_SCORING_WEIGHTS))

        payload = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
        scoring = payload.get("scoring", {}) if isinstance(payload, dict) else {}
        raw_weights = scoring.get("weights", {}) if isinstance(scoring, dict) else {}

        merged = dict(DEFAULT_SCORING_WEIGHTS)
        if isinstance(raw_weights, dict):
            for key in SCORE_KEYS:
                value = raw_weights.get(key)
                if isinstance(value, (int, float)) and float(value) >= 0:
                    merged[key] = float(value)

        return self._normalize_weights(merged)

    @staticmethod
    def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
        total = sum(weights.values())
        if total <= 0:
            equal = 1.0 / len(weights)
            return {k: equal for k in weights}
        return {k: float(v) / total for k, v in weights.items()}

    def _build_result(
        self,
        payload: dict[str, Any],
        weights: dict[str, float],
        company: str | None,
        job_id: int | None,
        model_used: str,
    ) -> EvaluationResult:
        scores = self._normalize_scores(payload)
        total = self._calculate_weighted_total(scores=scores, weights=weights)

        if scores["role_match"] < 2.0 or scores["skills_alignment"] < 2.0:
            total = min(total, 2.5)

        total = round(total, 2)
        grade = self._grade_from_score(total)
        summary = str(payload.get("summary", "")).strip() or "No model summary was provided."
        top_strengths = self._safe_string_list(payload.get("top_strengths"), limit=3)
        key_gaps = self._safe_string_list(payload.get("key_gaps"), limit=3)

        recommendation = str(payload.get("recommendation", "maybe")).strip().lower()
        if recommendation not in {"apply", "skip", "maybe"}:
            recommendation = "maybe"

        evaluated_at = datetime.now(timezone.utc)
        report_path = self._build_report_path(job_id=job_id, company=company, evaluated_at=evaluated_at)

        return EvaluationResult(
            job_id=job_id,
            evaluation_id=None,
            score_role_match=scores["role_match"],
            score_skills_alignment=scores["skills_alignment"],
            score_seniority_fit=scores["seniority_fit"],
            score_compensation=scores["compensation"],
            score_geographic=scores["geographic"],
            score_company_stage=scores["company_stage"],
            score_product_market_fit=scores["product_market_fit"],
            score_growth_trajectory=scores["growth_trajectory"],
            score_interview_likelihood=scores["interview_likelihood"],
            score_timeline=scores["timeline"],
            total=total,
            grade=grade,
            summary=summary,
            top_strengths=top_strengths,
            key_gaps=key_gaps,
            recommendation=recommendation,
            report_path=report_path,
            evaluated_at=evaluated_at,
            model_used=model_used,
        )

    def _normalize_scores(self, payload: dict[str, Any]) -> dict[str, float]:
        raw_scores = payload.get("scores")
        if not isinstance(raw_scores, dict):
            raise ValueError("Model response missing 'scores' object.")

        normalized: dict[str, float] = {}
        for key in SCORE_KEYS:
            value = raw_scores.get(key)
            if not isinstance(value, (int, float)):
                raise ValueError(f"Model response missing numeric score for '{key}'.")
            clamped = max(1.0, min(5.0, float(value)))
            normalized[key] = round(clamped, 1)

        return normalized

    @staticmethod
    def _calculate_weighted_total(scores: dict[str, float], weights: dict[str, float]) -> float:
        return sum(scores[k] * weights[k] for k in SCORE_KEYS)

    @staticmethod
    def _grade_from_score(score: float) -> str:
        if score >= 4.5:
            return "A"
        if score >= 4.0:
            return "B"
        if score >= 3.0:
            return "C"
        if score >= 2.0:
            return "D"
        return "F"

    @staticmethod
    def _safe_string_list(value: Any, limit: int = 3) -> list[str]:
        if not isinstance(value, list):
            return []

        cleaned: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                cleaned.append(item.strip())
            if len(cleaned) >= limit:
                break
        return cleaned

    def _build_report_path(self, job_id: int | None, company: str | None, evaluated_at: datetime) -> Path:
        job_token = f"{(job_id or 0):03d}"
        company_slug = self._slugify(company or "unknown-company")
        date_token = evaluated_at.strftime("%Y%m%d-%H%M%S")

        reports_dir = self._project_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        return reports_dir / f"{job_token}-{company_slug}-{date_token}.md"

    @staticmethod
    def _slugify(value: str) -> str:
        lowered = value.lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
        return normalized or "na"

    @staticmethod
    def _write_report(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _persist_evaluation(self, session: Session, job: Job, result: EvaluationResult) -> None:
        notes_payload = {
            "summary": result.summary,
            "top_strengths": result.top_strengths,
            "key_gaps": result.key_gaps,
            "recommendation": result.recommendation,
        }

        row = Evaluation(
            job_id=job.id,
            score_total=result.total,
            grade=result.grade,
            score_role_match=result.score_role_match,
            score_skills=result.score_skills_alignment,
            score_seniority=result.score_seniority_fit,
            score_compensation=result.score_compensation,
            score_geographic=result.score_geographic,
            score_company_stage=result.score_company_stage,
            score_pmf=result.score_product_market_fit,
            score_growth=result.score_growth_trajectory,
            score_interview_likelihood=result.score_interview_likelihood,
            score_timeline=result.score_timeline,
            report_path=result.report_path.as_posix(),
            evaluated_at=result.evaluated_at,
            model_used=result.model_used,
            notes=json.dumps(notes_payload, ensure_ascii=True),
        )

        job.status = "evaluated"
        session.add(row)
        session.add(job)
        session.commit()
        session.refresh(row)
        result.evaluation_id = row.id

    def _render_markdown_report(
        self,
        result: EvaluationResult,
        weights: dict[str, float],
        company: str | None,
        role: str | None,
        url: str | None,
    ) -> str:
        lines: list[str] = []
        lines.append("# Open Apply Evaluation Report")
        lines.append("")
        lines.append(f"- Evaluated At: {result.evaluated_at.isoformat()}")
        lines.append(f"- Company: {company or 'Unknown'}")
        lines.append(f"- Role: {role or 'Unknown'}")
        lines.append(f"- URL: {url or 'N/A'}")
        lines.append(f"- Model Used: {result.model_used}")
        lines.append("")
        lines.append("## Overall Verdict")
        lines.append("")
        lines.append(f"- Total Score: {result.total:.2f}/5.00")
        lines.append(f"- Grade: {result.grade}")
        lines.append(f"- Recommendation: {result.recommendation}")
        lines.append(f"- Summary: {result.summary}")
        lines.append("")
        lines.append("## Dimension Scores")
        lines.append("")
        lines.append("| Dimension | Score | Weight | Weighted Contribution |")
        lines.append("|-----------|-------|--------|-----------------------|")

        for key in SCORE_KEYS:
            score = result.scores[key]
            weight = weights[key]
            lines.append(f"| {key} | {score:.1f} | {weight:.2f} | {(score * weight):.2f} |")

        lines.append("")
        lines.append("## Top Strengths")
        lines.append("")
        for item in result.top_strengths or ["None provided"]:
            lines.append(f"- {item}")

        lines.append("")
        lines.append("## Key Gaps")
        lines.append("")
        for item in result.key_gaps or ["None provided"]:
            lines.append(f"- {item}")

        lines.append("")
        lines.append("## Notes")
        lines.append("")
        lines.append("- Gate Rule: Final score is capped at 2.5 if role_match < 2.0 or skills_alignment < 2.0.")
        lines.append("- HITL: AI analyzes, human decides before applying.")

        return "\n".join(lines).strip() + "\n"

    def _model_used_label(self) -> str:
        return self._ollama_client.selected_model()
