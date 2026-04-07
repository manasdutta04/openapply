from __future__ import annotations

import asyncio
import importlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml


PromptProfile = Literal["evaluate", "generate"]


class OllamaClientError(RuntimeError):
    """Raised for configuration and runtime failures when talking to Ollama."""


@dataclass(slots=True)
class OllamaSettings:
    base_url: str
    evaluate_model: str
    generate_model: str
    stream: bool = True


class OllamaClient:
    """Model-agnostic client wrapper with retries and JSON utilities."""

    def __init__(
        self,
        config_path: str | Path = "config.yml",
        profile: PromptProfile = "evaluate",
        model_override: str | None = None,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.75,
    ) -> None:
        self._config_path = Path(config_path)
        self._settings = self._load_settings(self._config_path)
        self._profile = profile
        self._model_override = model_override
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        async_client_cls, response_error_cls = self._load_ollama_sdk()
        self._response_error_cls = response_error_cls
        self._client = async_client_cls(host=self._settings.base_url)

    @property
    def profile(self) -> PromptProfile:
        return self._profile

    def set_profile(self, profile: PromptProfile) -> None:
        self._profile = profile

    def set_model_override(self, model: str | None) -> None:
        self._model_override = model

    def selected_model(self) -> str:
        return self._select_model()

    async def complete(self, system_prompt: str, user_prompt: str, stream: bool = True) -> str:
        """Generate a text completion and aggregate streamed chunks into one string."""
        effective_stream = stream if stream is not None else self._settings.stream
        model = self._select_model()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        if effective_stream:
            return await self._complete_streaming(model=model, messages=messages)

        response = await self._request_with_retry(model=model, messages=messages, stream=False)
        return self._extract_message_content(response)

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Generate a completion and parse it as strict JSON."""
        raw = await self.complete(system_prompt=system_prompt, user_prompt=user_prompt, stream=False)
        normalized = self._strip_json_fences(raw)

        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise OllamaClientError(
                "Model response was not valid JSON. Verify the prompt requests JSON-only output."
            ) from exc

        if not isinstance(parsed, dict):
            raise OllamaClientError("Expected a JSON object response.")

        return parsed

    async def _complete_streaming(self, model: str, messages: list[dict[str, str]]) -> str:
        stream_iter = await self._request_with_retry(model=model, messages=messages, stream=True)
        chunks: list[str] = []

        async for chunk in stream_iter:
            content = self._extract_message_content(chunk)
            if content:
                chunks.append(content)

        return "".join(chunks).strip()

    async def _request_with_retry(
        self,
        model: str,
        messages: list[dict[str, str]],
        stream: bool,
    ) -> Any:
        last_error: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return await self._client.chat(model=model, messages=messages, stream=stream)
            except (httpx.HTTPError, ConnectionError, TimeoutError, self._response_error_cls) as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    break

                jitter = random.uniform(0.0, 0.25)
                delay = (self._backoff_base_seconds * (2 ** (attempt - 1))) + jitter
                await asyncio.sleep(delay)
            except Exception as exc:  # pragma: no cover - unexpected SDK failures
                raise OllamaClientError(f"Unexpected Ollama client failure: {exc}") from exc

        message = (
            "Could not reach Ollama. Ensure the Ollama service is running and your model is installed. "
            f"Last error: {last_error}"
        )
        raise OllamaClientError(message)

    def _select_model(self) -> str:
        if self._model_override:
            return self._model_override

        if self._profile == "evaluate":
            return self._settings.evaluate_model

        return self._settings.generate_model

    @staticmethod
    def _extract_message_content(payload: Any) -> str:
        if isinstance(payload, str):
            return payload

        # Handle ChatResponse object from Ollama SDK
        if hasattr(payload, "message") and hasattr(payload.message, "content"):
            content = payload.message.content
            if isinstance(content, str):
                return content

        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content

            content = payload.get("content")
            if isinstance(content, str):
                return content

        return ""

    @staticmethod
    def _strip_json_fences(text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        return stripped

    @staticmethod
    def _load_settings(config_path: Path) -> OllamaSettings:
        if not config_path.exists():
            raise OllamaClientError(
                f"Config not found at {config_path}. Run 'openapply setup' to create config.yml."
            )

        with config_path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}

        ollama_cfg = config.get("ollama")
        if not isinstance(ollama_cfg, dict):
            raise OllamaClientError("Missing 'ollama' section in config.yml.")

        base_url = str(ollama_cfg.get("base_url", "http://localhost:11434")).strip()
        evaluate_model = str(ollama_cfg.get("evaluate_model", "")).strip()
        generate_model = str(ollama_cfg.get("generate_model", "")).strip()
        stream = bool(ollama_cfg.get("stream", True))

        if not evaluate_model or not generate_model:
            raise OllamaClientError(
                "Both ollama.evaluate_model and ollama.generate_model must be configured in config.yml."
            )

        return OllamaSettings(
            base_url=base_url,
            evaluate_model=evaluate_model,
            generate_model=generate_model,
            stream=stream,
        )

    @staticmethod
    def _load_ollama_sdk() -> tuple[Any, type[Exception]]:
        try:
            module = importlib.import_module("ollama")
        except ImportError as exc:
            raise OllamaClientError(
                "The 'ollama' package is not installed. Install dependencies first: pip install -e ."
            ) from exc

        async_client_cls = getattr(module, "AsyncClient", None)
        if async_client_cls is None:
            raise OllamaClientError("Installed ollama package does not expose AsyncClient.")

        response_error_cls = getattr(module, "ResponseError", Exception)
        if not isinstance(response_error_cls, type) or not issubclass(response_error_cls, Exception):
            response_error_cls = Exception

        return async_client_cls, response_error_cls
