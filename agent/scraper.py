from __future__ import annotations

import importlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

class ScraperError(RuntimeError):
    """Raised when scraping or form interaction fails."""


class JobScraper:
    """Playwright-based scraper for JD extraction and form prefill (HITL-safe)."""

    _TITLE_SELECTORS: tuple[str, ...] = (
        "h1[data-qa='job-title']",
        "h1[data-testid='job-title']",
        "h1.posting-headline",
        "h1.job-title",
        "h1",
    )

    _COMPANY_SELECTORS: tuple[str, ...] = (
        "[data-qa='company-name']",
        "[data-testid='company-name']",
        ".posting-company a",
        ".company",
        "meta[property='og:site_name']",
    )

    _DESCRIPTION_SELECTORS: tuple[str, ...] = (
        "#content",
        "#job-description",
        "[data-qa='job-description']",
        "[data-testid='job-description']",
        ".job-description",
        ".posting-description",
        "main",
    )

    _REQUIREMENTS_HINTS: tuple[str, ...] = (
        "requirements",
        "qualifications",
        "what you'll need",
        "what you will need",
        "must have",
        "who you are",
    )

    async def scrape_jd(self, url: str) -> dict[str, Any]:
        """Extract core JD attributes from a job URL."""
        if not url.strip():
            raise ValueError("URL is required for scrape_jd().")

        async_playwright, playwright_error = self._load_playwright_sdk()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(800)

                portal = self._detect_portal(url)
                title = await self._first_text(page, self._TITLE_SELECTORS)
                company = await self._extract_company(page)
                description = await self._extract_description(page)
                requirements = self._extract_requirements(description)

                return {
                    "url": url,
                    "portal": portal,
                    "title": title,
                    "company": company,
                    "description": description,
                    "requirements": requirements,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
            except playwright_error as exc:
                raise ScraperError(f"Failed to scrape JD from {url}: {exc}") from exc
            finally:
                await browser.close()

    async def fill_form(
        self,
        url: str,
        evaluation: dict[str, Any],
        cv_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Fill application fields and always return for human review; never submits."""
        if not url.strip():
            raise ValueError("URL is required for fill_form().")

        async_playwright, playwright_error = self._load_playwright_sdk()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            page = await browser.new_page()
            filled_fields: list[dict[str, Any]] = []

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(1200)

                portal = self._detect_portal(url)
                fields = await self._collect_form_fields(page)

                for field in fields:
                    value = self._suggest_field_value(field, cv_data=cv_data, evaluation=evaluation)
                    status = "skipped"

                    if value is not None:
                        try:
                            await self._apply_value(page, field, value)
                            status = "filled"
                        except playwright_error:
                            status = "failed"

                    filled_fields.append(
                        {
                            "name": field["name"],
                            "label": field["label"],
                            "type": field["type"],
                            "selector": field["selector"],
                            "value": value,
                            "status": status,
                        }
                    )

                return {
                    "url": url,
                    "portal": portal,
                    "requires_review": True,
                    "auto_submitted": False,
                    "human_reviewed": False,
                    "filled_fields": filled_fields,
                    "message": (
                        "Fields were drafted for review only. "
                        "No submit action was executed."
                    ),
                }
            except playwright_error as exc:
                raise ScraperError(f"Failed to fill form at {url}: {exc}") from exc
            finally:
                await browser.close()

    @staticmethod
    def _detect_portal(url: str) -> str:
        host = (urlparse(url).netloc or "").lower()
        if "greenhouse" in host:
            return "greenhouse"
        if "ashby" in host:
            return "ashby"
        if "lever" in host:
            return "lever"
        if "linkedin" in host:
            return "linkedin"
        if "workable" in host:
            return "workable"
        return "custom"

    async def _first_text(self, page: Any, selectors: tuple[str, ...]) -> str:
        for selector in selectors:
            try:
                if selector.startswith("meta"):
                    content = await page.get_attribute(selector, "content")
                    if content and content.strip():
                        return self._clean_text(content)
                    continue

                locator = page.locator(selector).first
                if await locator.count() > 0:
                    text = await locator.inner_text(timeout=2000)
                    if text and text.strip():
                        return self._clean_text(text)
            except Exception:
                continue
        return ""

    async def _extract_company(self, page: Any) -> str:
        company = await self._first_text(page, self._COMPANY_SELECTORS)
        if company:
            return company

        title = await page.title()
        chunks = [part.strip() for part in re.split(r"[\-|@]", title) if part.strip()]
        if len(chunks) >= 2:
            return chunks[-1]
        return ""

    async def _extract_description(self, page: Any) -> str:
        for selector in self._DESCRIPTION_SELECTORS:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                text = await locator.inner_text(timeout=3000)
                cleaned = self._clean_text(text)
                if len(cleaned) >= 200:
                    return cleaned
            except Exception:
                continue

        body_text = await page.locator("body").inner_text(timeout=4000)
        return self._clean_text(body_text)

    def _extract_requirements(self, description: str) -> list[str]:
        if not description:
            return []

        lines = [line.strip(" -\t") for line in description.splitlines() if line.strip()]
        req_lines: list[str] = []
        in_requirements = False

        for line in lines:
            lowered = line.lower()
            if any(hint in lowered for hint in self._REQUIREMENTS_HINTS):
                in_requirements = True
                continue

            if in_requirements and re.match(r"^[A-Z][A-Za-z ]{2,35}$", line):
                # Likely next section header.
                break

            if in_requirements:
                req_lines.append(line)

        if req_lines:
            return req_lines[:20]

        fallback = [line for line in lines if any(k in line.lower() for k in ("years", "experience", "required"))]
        return fallback[:12]

    async def _collect_form_fields(self, page: Any) -> list[dict[str, str]]:
        js = """
() => {
  const nodes = Array.from(document.querySelectorAll('input, textarea, select'));
  return nodes
    .filter((el) => !el.disabled && el.type !== 'hidden')
    .map((el, idx) => {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute('type') || tag).toLowerCase();
      const name = el.getAttribute('name') || el.getAttribute('id') || `field_${idx}`;
      const id = el.getAttribute('id') || '';
      const placeholder = el.getAttribute('placeholder') || '';
      const aria = el.getAttribute('aria-label') || '';
      let label = '';
      if (id) {
        const labelNode = document.querySelector(`label[for="${id}"]`);
        if (labelNode) label = (labelNode.textContent || '').trim();
      }
      if (!label) {
        const parentLabel = el.closest('label');
        if (parentLabel) label = (parentLabel.textContent || '').trim();
      }
      return {
        name,
        type,
        tag,
        label,
        placeholder,
        aria,
        selector: id ? `#${id}` : `[name="${name}"]`
      };
    });
}
"""
        raw_fields = await page.evaluate(js)
        fields: list[dict[str, str]] = []

        for field in raw_fields:
            item = {
                "name": str(field.get("name", "")).strip(),
                "type": str(field.get("type", "text")).strip().lower(),
                "tag": str(field.get("tag", "input")).strip().lower(),
                "label": str(field.get("label", "")).strip(),
                "placeholder": str(field.get("placeholder", "")).strip(),
                "aria": str(field.get("aria", "")).strip(),
                "selector": str(field.get("selector", "")).strip(),
            }
            if not item["name"] or not item["selector"]:
                continue
            fields.append(item)

        return fields

    def _suggest_field_value(
        self,
        field: dict[str, str],
        cv_data: dict[str, Any],
        evaluation: dict[str, Any],
    ) -> str | bool | None:
        probe = " ".join(
            [
                field.get("name", ""),
                field.get("label", ""),
                field.get("placeholder", ""),
                field.get("aria", ""),
            ]
        ).lower()

        profile = cv_data.get("profile", {}) if isinstance(cv_data.get("profile"), dict) else {}
        summary = str(cv_data.get("summary", "")).strip()

        if any(k in probe for k in ("first name", "firstname", "given name")):
            full_name = str(profile.get("name", "")).strip()
            return full_name.split(" ")[0] if full_name else None

        if any(k in probe for k in ("last name", "lastname", "family name", "surname")):
            full_name = str(profile.get("name", "")).strip()
            parts = [p for p in full_name.split(" ") if p]
            return parts[-1] if len(parts) >= 2 else None

        if any(k in probe for k in ("full name", "name")):
            return str(profile.get("name", "")).strip() or None

        if "email" in probe:
            return str(profile.get("email", "")).strip() or None

        if any(k in probe for k in ("phone", "mobile", "telephone")):
            return str(profile.get("phone", "")).strip() or None

        if any(k in probe for k in ("location", "city", "country", "address")):
            return str(profile.get("location", "")).strip() or None

        if "linkedin" in probe:
            return str(profile.get("linkedin", "")).strip() or None

        if "github" in probe:
            return str(profile.get("github", "")).strip() or None

        if any(k in probe for k in ("website", "portfolio")):
            return str(profile.get("website", "")).strip() or None

        if any(k in probe for k in ("cover", "why", "motivation", "about")):
            verdict = str(evaluation.get("recommendation", "")).strip().lower()
            if verdict:
                return (
                    "I am excited to apply because this role aligns strongly with my background. "
                    f"Based on my evaluation, my current fit is '{verdict}', and I can contribute quickly."
                )
            return summary or None

        if field.get("type") in {"checkbox", "radio"}:
            if any(k in probe for k in ("terms", "privacy", "consent", "agree")):
                return True
            return None

        if field.get("type") in {"file"}:
            # File upload path must be selected by the user during review.
            return None

        if field.get("type") in {"select", "textarea", "text", "search", "url"}:
            return summary[:600] if summary else None

        return None

    async def _apply_value(self, page: Any, field: dict[str, str], value: str | bool) -> None:
        selector = field["selector"]
        field_type = field.get("type", "text")

        if field_type == "checkbox":
            if isinstance(value, bool):
                locator = page.locator(selector).first
                checked = await locator.is_checked()
                if value and not checked:
                    await locator.check()
                if not value and checked:
                    await locator.uncheck()
            return

        if field_type == "radio":
            if isinstance(value, bool) and value:
                await page.locator(selector).first.check()
            return

        if field_type == "select":
            if isinstance(value, str) and value.strip():
                locator = page.locator(selector).first
                try:
                    await locator.select_option(label=value)
                except Exception:
                    # Fallback: keep default selection if exact label is unavailable.
                    pass
            return

        if isinstance(value, str):
            await page.locator(selector).first.fill(value)

    @staticmethod
    def _clean_text(text: str) -> str:
        compact = text.replace("\u00a0", " ")
        compact = re.sub(r"\r\n?", "\n", compact)
        compact = re.sub(r"\n{3,}", "\n\n", compact)
        compact = re.sub(r"[ \t]{2,}", " ", compact)
        return compact.strip()

    @staticmethod
    def _load_playwright_sdk() -> tuple[Any, type[Exception]]:
        try:
            module = importlib.import_module("playwright.async_api")
        except ImportError as exc:
            raise ScraperError(
                "The 'playwright' package is not installed. Install dependencies first: pip install -e ."
            ) from exc

        async_playwright = getattr(module, "async_playwright", None)
        playwright_error = getattr(module, "Error", Exception)

        if async_playwright is None:
            raise ScraperError("Installed playwright package does not expose async_playwright.")

        if not isinstance(playwright_error, type) or not issubclass(playwright_error, Exception):
            playwright_error = Exception

        return async_playwright, playwright_error
