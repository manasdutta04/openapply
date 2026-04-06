from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, select_autoescape
from sqlalchemy.orm import Session, sessionmaker

from memory.db import CV, Evaluation, Job

from .ollama_client import OllamaClient, OllamaClientError


DEFAULT_ARCHETYPES: tuple[str, ...] = (
    "Software Engineer",
    "AI/ML Engineer",
    "Product Manager",
    "Data Engineer",
    "DevOps/Platform",
    "Fullstack Developer",
)


@dataclass(slots=True)
class CVBuildResult:
    cv_id: int
    job_id: int
    evaluation_id: int | None
    cv_path: Path
    pdf_path: Path
    archetype: str
    language: str
    page_format: str
    keywords_injected: list[str]
    generated_at: datetime


@dataclass(slots=True)
class ExperienceItem:
    company: str
    title: str
    period: str
    bullets: list[str]


@dataclass(slots=True)
class ParsedCV:
    header: dict[str, str]
    summary: str
    experience: list[ExperienceItem]
    projects: list[dict[str, Any]]
    education: list[dict[str, str]]
    skills: list[str]


class CVBuilderError(RuntimeError):
    """Raised when CV build/render operations fail."""


class CVBuilder:
    """Tailors CV content to a JD and renders ATS-safe PDF output."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        ollama_client: OllamaClient,
        project_root: str | Path = ".",
        config_path: str | Path = "config.yml",
        template_path: str | Path = "templates/cv.html",
        prompt_path: str | Path = "agent/prompts/tailor_cv.md",
    ) -> None:
        self._session_factory = session_factory
        self._ollama_client = ollama_client
        self._project_root = Path(project_root)
        self._config_path = Path(config_path)
        self._template_path = Path(template_path)
        self._prompt_path = Path(prompt_path)

    async def build_for_job(
        self,
        job_id: int,
        evaluation_id: int,
        cv_markdown_path: str | Path = "cv.md",
    ) -> CVBuildResult:
        config = self._load_config()

        with self._session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise ValueError(f"Job with id={job_id} was not found.")

            evaluation = session.get(Evaluation, evaluation_id)
            if evaluation is None:
                raise ValueError(f"Evaluation with id={evaluation_id} was not found.")

            jd_content = (job.jd_extracted or job.jd_raw or "").strip()
            if not jd_content:
                raise ValueError("Job description is empty. Scrape or import JD before CV build.")

            cv_base_path = self._project_root / cv_markdown_path
            if not cv_base_path.exists():
                raise FileNotFoundError(f"Base CV not found: {cv_base_path}")

            base_cv_markdown = cv_base_path.read_text(encoding="utf-8")
            parsed_cv = self._parse_cv_markdown(base_cv_markdown)

            archetypes = self._load_archetypes(config)
            tailoring_plan = await self._tailor_cv(
                base_cv_markdown=base_cv_markdown,
                jd_content=jd_content,
                evaluation=evaluation,
                archetypes=archetypes,
            )

            keywords = self._extract_keywords(tailoring_plan, jd_content)
            language = self._detect_language(jd_content, config)
            page_format = self._detect_page_format(jd_content, config)
            archetype = self._select_archetype(tailoring_plan, config, archetypes)

            tailored_cv = self._apply_tailoring(parsed_cv=parsed_cv, keywords=keywords)
            html = self._render_html(
                cv=tailored_cv,
                page_format=page_format,
                language=language,
                archetype=archetype,
            )

            generated_at = datetime.now()
            output_dir = self._project_root / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            slug_base = self._output_slug(company=job.company, role=job.role, dt=generated_at)

            cv_path = output_dir / f"{slug_base}.md"
            pdf_path = output_dir / f"{slug_base}.pdf"

            cv_path.write_text(self._render_tailored_markdown(tailored_cv), encoding="utf-8")
            await self._render_pdf(html=html, target_path=pdf_path, page_format=page_format)

            cv_row = CV(
                job_id=job.id,
                evaluation_id=evaluation.id,
                cv_path=cv_path.as_posix(),
                pdf_path=pdf_path.as_posix(),
                keywords_injected=json.dumps(keywords, ensure_ascii=True),
                archetype_used=archetype,
                generated_at=generated_at,
            )
            session.add(cv_row)
            session.commit()
            session.refresh(cv_row)

            return CVBuildResult(
                cv_id=cv_row.id,
                job_id=job.id,
                evaluation_id=evaluation.id,
                cv_path=cv_path,
                pdf_path=pdf_path,
                archetype=archetype,
                language=language,
                page_format=page_format,
                keywords_injected=keywords,
                generated_at=generated_at,
            )

    def _load_config(self) -> dict[str, Any]:
        config_path = self._project_root / self._config_path
        if not config_path.exists():
            raise FileNotFoundError(
                f"Config not found at {config_path}. Run 'openapply setup' first."
            )

        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    def _load_archetypes(self, config: dict[str, Any]) -> list[str]:
        cv_cfg = config.get("cv", {}) if isinstance(config.get("cv"), dict) else {}
        raw = cv_cfg.get("archetypes")
        if isinstance(raw, list):
            cleaned = [str(item).strip() for item in raw if str(item).strip()]
            if cleaned:
                return cleaned
        return list(DEFAULT_ARCHETYPES)

    async def _tailor_cv(
        self,
        base_cv_markdown: str,
        jd_content: str,
        evaluation: Evaluation,
        archetypes: list[str],
    ) -> dict[str, Any]:
        prompt_file = self._project_root / self._prompt_path
        if prompt_file.exists():
            prompt_template = prompt_file.read_text(encoding="utf-8")
        else:
            bundled_prompt = files("agent").joinpath(f"prompts/{self._prompt_path.name}")
            if not bundled_prompt.is_file():
                raise FileNotFoundError(f"Missing prompt file: {prompt_file}")
            prompt_template = bundled_prompt.read_text(encoding="utf-8")

        evaluation_payload = {
            "score_total": evaluation.score_total,
            "grade": evaluation.grade,
            "score_role_match": evaluation.score_role_match,
            "score_skills": evaluation.score_skills,
            "score_seniority": evaluation.score_seniority,
            "score_compensation": evaluation.score_compensation,
            "score_geographic": evaluation.score_geographic,
            "score_company_stage": evaluation.score_company_stage,
            "score_pmf": evaluation.score_pmf,
            "score_growth": evaluation.score_growth,
            "score_interview_likelihood": evaluation.score_interview_likelihood,
            "score_timeline": evaluation.score_timeline,
            "notes": evaluation.notes,
        }

        prompt = prompt_template.format(
            cv_content=base_cv_markdown,
            jd_content=jd_content,
            evaluation_json=json.dumps(evaluation_payload, ensure_ascii=True, indent=2),
            archetypes=", ".join(archetypes),
        )

        try:
            return await self._ollama_client.complete_json(
                system_prompt=(
                    "You are Open Apply's CV tailoring model. Return only valid JSON as requested."
                ),
                user_prompt=prompt,
            )
        except OllamaClientError:
            raise
        except Exception as exc:
            raise OllamaClientError(f"CV tailoring failed: {exc}") from exc

    @staticmethod
    def _extract_keywords(plan: dict[str, Any], jd_content: str) -> list[str]:
        raw = plan.get("keywords", [])
        model_keywords = raw if isinstance(raw, list) else []

        keywords: list[str] = []
        lowered_set: set[str] = set()

        for item in model_keywords:
            token = str(item).strip()
            if not token:
                continue
            norm = token.lower()
            if norm in lowered_set:
                continue
            keywords.append(token)
            lowered_set.add(norm)
            if len(keywords) >= 20:
                break

        if len(keywords) < 15:
            for token in CVBuilder._extract_keywords_from_jd(jd_content):
                norm = token.lower()
                if norm in lowered_set:
                    continue
                keywords.append(token)
                lowered_set.add(norm)
                if len(keywords) >= 20:
                    break

        return keywords

    @staticmethod
    def _extract_keywords_from_jd(jd_content: str) -> list[str]:
        stop_words = {
            "the", "and", "for", "with", "you", "your", "our", "are", "this", "that",
            "will", "from", "have", "has", "all", "any", "not", "but", "job", "role",
            "team", "work", "years", "year", "experience", "required", "preferred", "skills",
            "ability", "strong", "using", "build", "develop", "engineer", "engineering",
        }
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+.#-]{2,}", jd_content)
        frequencies: dict[str, int] = {}
        original_case: dict[str, str] = {}

        for token in tokens:
            norm = token.lower()
            if norm in stop_words:
                continue
            if norm.isdigit():
                continue
            frequencies[norm] = frequencies.get(norm, 0) + 1
            if norm not in original_case:
                original_case[norm] = token

        sorted_tokens = sorted(frequencies.items(), key=lambda item: item[1], reverse=True)
        return [original_case[norm] for norm, _ in sorted_tokens]

    def _select_archetype(
        self,
        plan: dict[str, Any],
        config: dict[str, Any],
        archetypes: list[str],
    ) -> str:
        suggestion = str(plan.get("archetype", "")).strip()
        if suggestion in archetypes:
            return suggestion

        cv_cfg = config.get("cv", {}) if isinstance(config.get("cv"), dict) else {}
        default_archetype = str(cv_cfg.get("default_archetype", "")).strip()
        if default_archetype in archetypes:
            return default_archetype

        return archetypes[0]

    def _detect_language(self, jd_content: str, config: dict[str, Any]) -> str:
        cv_cfg = config.get("cv", {}) if isinstance(config.get("cv"), dict) else {}
        configured = str(cv_cfg.get("language", "")).strip().lower()
        if configured:
            return configured

        sample = jd_content.lower()
        spanish_markers = (" requisitos ", " experiencia ", "trabajo", "equipo", "remoto")
        for marker in spanish_markers:
            if marker in f" {sample} ":
                return "es"

        return "en"

    def _detect_page_format(self, jd_content: str, config: dict[str, Any]) -> str:
        cv_cfg = config.get("cv", {}) if isinstance(config.get("cv"), dict) else {}
        configured = str(cv_cfg.get("page_format", "")).strip().lower()
        if configured in {"a4", "letter"}:
            return "A4" if configured == "a4" else "Letter"

        text = jd_content.lower()
        us_markers = ("united states", "usa", "u.s.", "new york", "california", "texas")
        if any(marker in text for marker in us_markers):
            return "Letter"

        return "A4"

    def _apply_tailoring(self, parsed_cv: ParsedCV, keywords: list[str]) -> ParsedCV:
        summary = parsed_cv.summary
        if keywords:
            top_summary_keywords = [k for k in keywords[:3] if k.lower() not in summary.lower()]
            if top_summary_keywords:
                summary_suffix = ", ".join(top_summary_keywords)
                if summary and not summary.endswith("."):
                    summary += "."
                summary = (summary + " " if summary else "") + f"Focused on {summary_suffix}."

        experience = []
        keyword_cycle = keywords[:]
        for index, item in enumerate(parsed_cv.experience):
            reordered_bullets = self._reorder_bullets_by_keywords(item.bullets, keywords)

            if reordered_bullets and keyword_cycle:
                kw = keyword_cycle[index % len(keyword_cycle)]
                first = reordered_bullets[0]
                if kw.lower() not in first.lower():
                    sep = ";" if first.endswith(".") else " -"
                    reordered_bullets[0] = f"{first.rstrip('.')} {sep} using {kw}."

            experience.append(
                ExperienceItem(
                    company=item.company,
                    title=item.title,
                    period=item.period,
                    bullets=reordered_bullets,
                )
            )

        return ParsedCV(
            header=parsed_cv.header,
            summary=summary,
            experience=experience,
            projects=parsed_cv.projects,
            education=parsed_cv.education,
            skills=parsed_cv.skills,
        )

    @staticmethod
    def _reorder_bullets_by_keywords(bullets: list[str], keywords: list[str]) -> list[str]:
        if not bullets or not keywords:
            return bullets[:]

        def score(text: str) -> int:
            lowered = text.lower()
            return sum(1 for keyword in keywords if keyword.lower() in lowered)

        return sorted(bullets, key=lambda b: score(b), reverse=True)

    def _render_html(self, cv: ParsedCV, page_format: str, language: str, archetype: str) -> str:
        template_full_path = self._project_root / self._template_path
        env = Environment(
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        if template_full_path.exists():
            template = env.from_string(template_full_path.read_text(encoding="utf-8"))
        else:
            bundled_template = files("agent").joinpath(f"templates/{self._template_path.name}")
            if not bundled_template.is_file():
                raise FileNotFoundError(f"CV template not found: {template_full_path}")
            template = env.from_string(bundled_template.read_text(encoding="utf-8"))

        return template.render(
            page_format=page_format,
            language=language,
            archetype=archetype,
            header=cv.header,
            summary=cv.summary,
            experience=cv.experience,
            projects=cv.projects,
            education=cv.education,
            skills=cv.skills,
        )

    async def _render_pdf(self, html: str, target_path: Path, page_format: str) -> None:
        async_playwright = self._load_playwright_sdk()
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            await page.emulate_media(media="print")
            await page.pdf(
                path=target_path.as_posix(),
                format=page_format,
                print_background=True,
                margin={"top": "0.5in", "right": "0.5in", "bottom": "0.5in", "left": "0.5in"},
            )
            await browser.close()

    @staticmethod
    def _load_playwright_sdk() -> Any:
        try:
            module = importlib.import_module("playwright.async_api")
        except ImportError as exc:
            raise CVBuilderError(
                "The 'playwright' package is not installed. Install dependencies first: pip install -e ."
            ) from exc

        async_playwright = getattr(module, "async_playwright", None)
        if async_playwright is None:
            raise CVBuilderError("Installed playwright package does not expose async_playwright.")
        return async_playwright

    @staticmethod
    def _output_slug(company: str | None, role: str | None, dt: datetime) -> str:
        company_slug = CVBuilder._slugify(company or "company")
        role_slug = CVBuilder._slugify(role or "role")
        return f"{company_slug}-{role_slug}-{dt.strftime('%Y%m%d')}"

    @staticmethod
    def _slugify(value: str) -> str:
        lowered = value.lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
        return normalized or "na"

    @staticmethod
    def _parse_cv_markdown(markdown: str) -> ParsedCV:
        lines = markdown.splitlines()

        header: dict[str, str] = {
            "name": "",
            "email": "",
            "phone": "",
            "location": "",
            "linkedin": "",
            "github": "",
            "website": "",
        }
        summary_lines: list[str] = []
        experience: list[ExperienceItem] = []
        projects: list[dict[str, Any]] = []
        education: list[dict[str, str]] = []
        skills: list[str] = []

        current_section = ""
        current_exp: ExperienceItem | None = None
        current_project: dict[str, Any] | None = None

        if lines and lines[0].startswith("# "):
            header["name"] = lines[0][2:].strip()

        for raw_line in lines[1:]:
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("## "):
                if current_exp is not None:
                    experience.append(current_exp)
                    current_exp = None
                if current_project is not None:
                    projects.append(current_project)
                    current_project = None
                current_section = line[3:].strip().lower()
                continue

            if line.startswith("### "):
                title_line = line[4:].strip()
                if current_section == "experience":
                    if current_exp is not None:
                        experience.append(current_exp)
                    current_exp = ExperienceItem(company=title_line, title="", period="", bullets=[])
                elif current_section == "projects":
                    if current_project is not None:
                        projects.append(current_project)
                    current_project = {"name": title_line, "description": "", "bullets": []}
                elif current_section == "education":
                    education.append({"institution": title_line, "details": ""})
                continue

            if current_section == "summary":
                summary_lines.append(line)
                continue

            if current_section == "header":
                key, _, value = line.partition(":")
                if value:
                    header[key.strip().lower()] = value.strip()
                continue

            if current_section == "experience" and current_exp is not None:
                if line.startswith("- "):
                    current_exp.bullets.append(line[2:].strip())
                elif not current_exp.title:
                    current_exp.title = line
                elif not current_exp.period:
                    current_exp.period = line
                else:
                    current_exp.bullets.append(line)
                continue

            if current_section == "projects" and current_project is not None:
                if line.startswith("- "):
                    current_project.setdefault("bullets", []).append(line[2:].strip())
                elif not current_project.get("description"):
                    current_project["description"] = line
                continue

            if current_section == "education" and education:
                last = education[-1]
                last["details"] = (last.get("details", "") + " " + line).strip()
                continue

            if current_section == "skills":
                if line.startswith("- "):
                    skills.append(line[2:].strip())
                else:
                    skills.extend([token.strip() for token in line.split(",") if token.strip()])
                continue

            # Generic contact extraction fallback.
            if "@" in line and not header.get("email"):
                header["email"] = line
            if "linkedin.com" in line and not header.get("linkedin"):
                header["linkedin"] = line
            if "github.com" in line and not header.get("github"):
                header["github"] = line

        if current_exp is not None:
            experience.append(current_exp)
        if current_project is not None:
            projects.append(current_project)

        return ParsedCV(
            header=header,
            summary=" ".join(summary_lines).strip(),
            experience=experience,
            projects=projects,
            education=education,
            skills=skills,
        )

    @staticmethod
    def _render_tailored_markdown(cv: ParsedCV) -> str:
        lines: list[str] = []
        lines.append(f"# {cv.header.get('name') or 'Candidate'}")
        lines.append("")

        lines.append("## Header")
        for key in ("email", "phone", "location", "linkedin", "github", "website"):
            value = cv.header.get(key, "").strip()
            if value:
                lines.append(f"{key}: {value}")
        lines.append("")

        lines.append("## Summary")
        lines.append(cv.summary)
        lines.append("")

        lines.append("## Experience")
        for item in cv.experience:
            lines.append(f"### {item.company}")
            if item.title:
                lines.append(item.title)
            if item.period:
                lines.append(item.period)
            for bullet in item.bullets:
                lines.append(f"- {bullet}")
            lines.append("")

        if cv.projects:
            lines.append("## Projects")
            for proj in cv.projects:
                lines.append(f"### {proj.get('name', '')}")
                desc = str(proj.get("description", "")).strip()
                if desc:
                    lines.append(desc)
                for bullet in proj.get("bullets", []):
                    lines.append(f"- {bullet}")
                lines.append("")

        if cv.education:
            lines.append("## Education")
            for edu in cv.education:
                lines.append(f"### {edu.get('institution', '')}")
                details = str(edu.get("details", "")).strip()
                if details:
                    lines.append(details)
                lines.append("")

        if cv.skills:
            lines.append("## Skills")
            lines.append(", ".join(cv.skills))
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
