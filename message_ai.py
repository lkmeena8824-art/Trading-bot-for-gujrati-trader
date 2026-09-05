"""AI message generation with a deterministic fallback.

Contract enforced here (Phase 2):

* Deterministic templates are the source of truth. AI only ever *rephrases*
  them, and only when ``AI_ENABLED=true`` plus a provider key is configured.
* Every AI answer passes through :mod:`ai_guard`. If it invents a number,
  introduces or flips a direction, drops a critical fact, promises profit or
  emits unsafe markup, the deterministic text is published instead.
* Any provider error, timeout, budget exhaustion or rate-limit falls back
  silently. The bot never blocks on AI and never fails because of AI.
* With ``AI_ENABLED=false`` (the default) not a single paid call is made, so
  the zero-cost mode is byte-for-byte the previous behaviour.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import config
from ai_guard import guard_text
from ai_provider import AIResponse, get_provider

logger = logging.getLogger(__name__)

SYSTEM_RULES = (
    "You are the wording assistant for an Indian index-options alert channel. "
    "You NEVER make trading decisions. You NEVER decide or suggest BUY or SELL. "
    "You NEVER invent, round, shift or remove any number: entry, stop loss, targets, "
    "risk-reward, open interest, points or P&L must appear exactly as supplied. "
    "You rewrite the supplied deterministic draft so it sounds like a real human trader "
    "talking to his community. Keep every fact identical. "
    "Never promise profit, never guarantee accuracy, be honest about losses, "
    "and keep risk discipline in the tone. "
    "Return Telegram-safe HTML only (b, i, u, s, code, pre, a). No markdown fences."
)

STYLE_HINTS = {
    "hinglish": "Write in natural Hinglish (Roman script Hindi mixed with English), warm and direct.",
    "english": "Write in clear, simple Indian English.",
    "gujarati": "Write in natural Gujarati-flavoured Hinglish (Roman script), warm and direct.",
}


@dataclass
class _AIState:
    last_call_monotonic: float = 0.0
    day_key: str = ""
    calls_today: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stats: dict[str, int] = field(default_factory=lambda: {
        "requested": 0,
        "skipped_disabled": 0,
        "skipped_budget": 0,
        "provider_ok": 0,
        "provider_error": 0,
        "guard_rejected": 0,
        "published_ai": 0,
        "published_fallback": 0,
    })


_state = _AIState()


def reset_ai_state() -> None:
    """Test/ops helper: clear rate-limit, budget counters and statistics."""
    global _state
    _state = _AIState()


def ai_stats() -> dict[str, Any]:
    from ai_provider import provider_status

    data = dict(_state.stats)
    data.update(provider_status())
    data["calls_today"] = _state.calls_today
    data["day_key"] = _state.day_key
    return data


def _market_day() -> str:
    from quality_control import market_day

    return market_day()


def _budget_available() -> bool:
    day = _market_day()
    if _state.day_key != day:
        _state.day_key = day
        _state.calls_today = 0
    budget = int(config.AI_DAILY_CALL_BUDGET)
    if budget <= 0:
        return False
    return _state.calls_today < budget


def ai_available() -> bool:
    """True only when a real, funded provider call is possible right now."""
    if not config.AI_ENABLED:
        return False
    return get_provider().available


def build_prompt(kind: str, facts: Any, fallback: str, context: dict | None = None) -> tuple[str, str]:
    style = STYLE_HINTS.get(config.AI_STYLE_LANGUAGE, STYLE_HINTS["hinglish"])
    system = (
        f"{SYSTEM_RULES}\n"
        f"Persona name: {config.AI_PERSONA_NAME}. {style}\n"
        "Hard limit: only numbers already present in the draft or facts may appear."
    )
    payload = {
        "kind": kind,
        "facts": facts,
        "deterministic_draft": fallback,
        "context": context or {},
        "instructions": [
            "Rewrite the deterministic draft in a human, community-friendly voice.",
            "Keep every number, direction and status exactly as given.",
            "Do not add new trade advice, predictions or targets.",
            "Keep it under 1200 characters unless the draft is longer.",
        ],
    }
    return system, json.dumps(payload, ensure_ascii=False, default=str)


async def _log_usage(kind: str, response: AIResponse, outcome: str) -> None:
    try:
        from database import db

        await db.log_ai_usage(
            market_day=_market_day(),
            provider=response.provider,
            model=response.model,
            kind=kind,
            status=outcome,
            latency_ms=response.latency_ms,
            error=response.error,
        )
    except Exception:  # observability must never break delivery
        logger.debug("AI usage logging skipped", exc_info=True)


async def generate_message(
    kind: str,
    facts: Any,
    fallback: str,
    *,
    context: dict | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str:
    """Return AI-polished text when it is safe, otherwise the free template."""
    _state.stats["requested"] += 1
    fallback = (fallback or "").strip()

    if not ai_available():
        _state.stats["skipped_disabled"] += 1
        _state.stats["published_fallback"] += 1
        return fallback

    async with _state.lock:
        if not _budget_available():
            _state.stats["skipped_budget"] += 1
            _state.stats["published_fallback"] += 1
            logger.info("AI daily budget exhausted; using deterministic template")
            return fallback

        wait_for = config.AI_MIN_INTERVAL_SECONDS - (time.monotonic() - _state.last_call_monotonic)
        if wait_for > 0:
            await asyncio.sleep(min(wait_for, float(config.AI_MIN_INTERVAL_SECONDS)))
        _state.last_call_monotonic = time.monotonic()
        _state.calls_today += 1

        provider = get_provider()
        system, user = build_prompt(kind, facts, fallback, context)
        response = await provider.generate(
            system,
            user,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    if not response.ok:
        _state.stats["provider_error"] += 1
        _state.stats["published_fallback"] += 1
        await _log_usage(kind, response, "provider_error")
        logger.warning("AI wording failed (%s); using free template", response.error)
        return fallback

    _state.stats["provider_ok"] += 1
    result = guard_text(response.text, facts, fallback, kind=kind)
    if not result.ok:
        _state.stats["guard_rejected"] += 1
        _state.stats["published_fallback"] += 1
        await _log_usage(kind, response, f"guard_rejected:{result.reason}")
        logger.warning("AI text rejected by fact guard (%s); using free template", result.reason)
        return fallback

    _state.stats["published_ai"] += 1
    await _log_usage(kind, response, "published")
    return result.text


async def optional_ai_message(kind: str, facts: dict, fallback: str) -> str:
    """Backward-compatible entry point used by the existing messaging layer."""
    return await generate_message(kind, facts, fallback)
