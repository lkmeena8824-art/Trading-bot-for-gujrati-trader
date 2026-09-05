"""Provider abstraction for optional AI wording (OpenAI and Gemini).

Design rules for this layer:

* It is a *text* layer only. A provider receives already-computed facts and
  returns wording. It never decides direction, never edits numbers, and its
  output is validated by ``ai_guard`` before anything reaches Telegram.
* Every provider fails soft. A network error, bad key, HTTP error or malformed
  response returns ``AIResponse(ok=False)`` so the caller can use the free
  deterministic template.
* Nothing here runs unless ``AI_ENABLED`` is true and a key is configured, so
  the zero-cost mode never performs a paid call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIResponse:
    ok: bool
    text: str = ""
    provider: str = "none"
    model: str = ""
    error: str = ""
    latency_ms: int = 0


@dataclass
class AIProvider(ABC):
    """Common interface every AI backend implements."""

    name: str = "base"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout: int = 8
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @abstractmethod
    def build_request(self, system: str, user: str, temperature: float, max_tokens: int) -> tuple[str, dict, dict]:
        """Return (url, headers, json_payload)."""

    @abstractmethod
    def parse_response(self, body: Any) -> str:
        """Extract plain text from a provider-specific response body."""

    async def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AIResponse:
        if not self.available:
            return AIResponse(ok=False, provider=self.name, model=self.model, error="missing_api_key")

        temperature = config.AI_TEMPERATURE if temperature is None else temperature
        max_tokens = config.AI_MAX_TOKENS if max_tokens is None else max_tokens
        url, headers, payload = self.build_request(system, user, temperature, max_tokens)
        started = time.monotonic()
        try:
            body = await self._post_json(url, headers, payload)
        except asyncio.TimeoutError:
            return AIResponse(ok=False, provider=self.name, model=self.model, error="timeout")
        except Exception as exc:  # network/DNS/TLS/etc. never break the bot
            logger.warning("AI provider %s failed: %s", self.name, exc)
            return AIResponse(ok=False, provider=self.name, model=self.model, error=str(exc)[:200])

        latency_ms = int((time.monotonic() - started) * 1000)
        if body is None:
            return AIResponse(ok=False, provider=self.name, model=self.model, error="empty_body", latency_ms=latency_ms)
        try:
            text = (self.parse_response(body) or "").strip()
        except Exception as exc:
            return AIResponse(ok=False, provider=self.name, model=self.model, error=f"parse:{exc}"[:200], latency_ms=latency_ms)
        if not text:
            return AIResponse(ok=False, provider=self.name, model=self.model, error="empty_text", latency_ms=latency_ms)
        return AIResponse(ok=True, text=text, provider=self.name, model=self.model, latency_ms=latency_ms)

    async def _post_json(self, url: str, headers: dict, payload: dict) -> Any:
        """Perform the HTTP call. Split out so tests can stub the transport."""
        import aiohttp  # imported lazily: zero-cost mode never needs it here

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status != 200:
                    detail = (await response.text())[:200]
                    raise RuntimeError(f"http_{response.status}:{detail}")
                return await response.json()


@dataclass
class DisabledProvider(AIProvider):
    """Explicit no-op provider used whenever AI must not run (zero-cost mode)."""

    name: str = "none"

    @property
    def available(self) -> bool:
        return False

    def build_request(self, system: str, user: str, temperature: float, max_tokens: int):
        raise RuntimeError("disabled provider cannot build a request")

    def parse_response(self, body: Any) -> str:
        return ""


@dataclass
class OpenAIProvider(AIProvider):
    name: str = "openai"

    def build_request(self, system: str, user: str, temperature: float, max_tokens: int):
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return url, headers, payload

    def parse_response(self, body: Any) -> str:
        choices = (body or {}).get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("message") or {}).get("content", "") or ""


@dataclass
class GeminiProvider(AIProvider):
    name: str = "gemini"

    def build_request(self, system: str, user: str, temperature: float, max_tokens: int):
        url = f"{self.base_url}/models/{self.model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        return url, headers, payload

    def parse_response(self, body: Any) -> str:
        candidates = (body or {}).get("candidates") or []
        if not candidates:
            return ""
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        return "".join(part.get("text", "") for part in parts)


_PROVIDER_CACHE: dict[str, AIProvider] = {}


def build_provider(name: str) -> AIProvider:
    """Construct a provider instance from the current configuration."""
    name = (name or "none").strip().lower()
    timeout = max(2, int(config.AI_TIMEOUT_SECONDS))
    if name == "openai":
        return OpenAIProvider(
            model=config.OPENAI_MODEL,
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
            timeout=timeout,
        )
    if name == "gemini":
        return GeminiProvider(
            model=config.GEMINI_MODEL,
            api_key=config.GEMINI_API_KEY,
            base_url=config.GEMINI_BASE_URL,
            timeout=timeout,
        )
    return DisabledProvider()


def get_provider(name: str | None = None) -> AIProvider:
    """Return the provider that should be used for the next AI call.

    ``config.active_ai_provider()`` resolves AI_ENABLED, AI_PROVIDER=auto and
    missing keys, so an unconfigured deployment always gets DisabledProvider
    and therefore always uses the free deterministic templates.
    """
    resolved = (name or config.active_ai_provider()).strip().lower()
    cache_key = "|".join(
        [
            resolved,
            config.OPENAI_MODEL,
            config.GEMINI_MODEL,
            str(bool(config.OPENAI_API_KEY)),
            str(bool(config.GEMINI_API_KEY)),
            config.OPENAI_BASE_URL,
            config.GEMINI_BASE_URL,
            str(config.AI_TIMEOUT_SECONDS),
        ]
    )
    provider = _PROVIDER_CACHE.get(cache_key)
    if provider is None:
        provider = build_provider(resolved)
        _PROVIDER_CACHE[cache_key] = provider
    return provider


def reset_provider_cache() -> None:
    _PROVIDER_CACHE.clear()


def provider_status() -> dict[str, Any]:
    provider = get_provider()
    return {
        "ai_enabled": bool(config.AI_ENABLED),
        "configured_provider": config.AI_PROVIDER,
        "active_provider": provider.name,
        "model": provider.model,
        "available": provider.available,
        "zero_cost_mode": not provider.available,
    }
