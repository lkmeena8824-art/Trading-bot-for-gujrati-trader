"""Fact guard for AI-generated Telegram text.

The single most important Phase 2 rule:

    AI may rephrase. AI may never decide BUY/SELL and may never change entry,
    SL, targets, RRR, OI or P&L facts.

This module enforces that mechanically instead of trusting the prompt:

1. **Number integrity** — every number in the AI text must already exist in the
   supplied facts or in the deterministic fallback text (small structural
   numbers such as 1:2 / 1:3 / 50% are whitelisted). A hallucinated or shifted
   price is therefore impossible to publish.
2. **Direction integrity** — directional words (buy/sell/long/short/CE/PE and
   common Hinglish equivalents) are only allowed when the same direction is
   already present in the facts or the fallback. AI can never introduce or flip
   a trade direction.
3. **Required facts** — for trade-critical messages the key numbers must still
   be present, so AI cannot quietly drop the SL or a target.
4. **Promise/hype guard** — guaranteed-profit style claims are rejected.
5. **Markup safety** — only Telegram-safe HTML tags, balanced, length-capped.

Any failure returns the deterministic fallback. Failing closed is always the
correct behaviour here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

logger = logging.getLogger(__name__)

NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
TAG_RE = re.compile(r"</?([a-zA-Z0-9]+)[^>]*>")
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "s", "code", "pre", "a", "br"}

# Structural numbers that carry no price meaning (ratios, percentages, slots,
# clock references that the templates already use).
SAFE_NUMBERS = {
    0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 9.0, 10.0, 12.0, 14.0, 15.0, 20.0, 24.0,
    25.0, 30.0, 45.0, 50.0, 60.0, 75.0, 100.0,
}

# "call"/"calls" alone means "published alert" in this channel, so only the
# explicit option-leg wording counts as directional.
BUY_WORDS = {
    "buy", "buying", "long", "longs", "kharid", "kharido", "khareed",
    "khareedo", "call option", "call options",
}
SELL_WORDS = {
    "sell", "selling", "short", "shorts", "bech", "becho", "bechna",
    "put option", "put options",
}
DIRECTION_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted((BUY_WORDS | SELL_WORDS), key=len, reverse=True)).replace(" ", r"\s+") + r")\b",
    re.IGNORECASE,
)
# "pe"/"ce" are ordinary Hinglish words in lower case ("market pe nazar"), so
# only the upper-case option-leg tokens count as directional.
OPTION_LEG_RE = re.compile(r"\b(CE|PE)\b")

BANNED_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"guarantee",
        r"guaranteed",
        r"sure\s*-?\s*shot",
        r"100\s*%\s*(profit|accurate|accuracy|win)",
        r"no\s+risk",
        r"risk\s*-?\s*free",
        r"loss\s+nahi\s+ho",
        r"confirm(ed)?\s+profit",
        r"paisa\s+double",
        r"double\s+your\s+money",
        r"jackpot",
        r"insider",
        r"assured\s+return",
        r"pakka\s+profit",
    )
]

# Numbers that must survive rephrasing for trade-critical messages.
CRITICAL_FACT_KEYS = (
    "entry", "entry_low", "entry_high", "sl", "current_sl", "new_sl",
    "t1", "t2", "t3", "target1", "target2", "target3",
    "rrr", "risk_points", "points", "realized_points", "net_points",
    "oi", "oi_change", "pnl", "net_pnl",
)
FACT_CRITICAL_KINDS = {
    "trade_call", "trade_update", "pnl_report", "oi_update", "closing_summary",
}


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    text: str
    reason: str = ""


def _to_float(token: str) -> float | None:
    try:
        return float(token.replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def extract_numbers(text: str) -> list[float]:
    values = []
    for match in NUMBER_RE.finditer(text or ""):
        value = _to_float(match.group())
        if value is not None:
            values.append(value)
    return values


def _walk(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk(item)
    else:
        yield value


def collect_allowed_numbers(facts: Any, *reference_texts: str) -> set[float]:
    """All numbers the AI is permitted to mention."""
    allowed: set[float] = set(SAFE_NUMBERS)
    for value in _walk(facts):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            allowed.add(float(value))
            allowed.add(round(float(value), 2))
            allowed.add(round(float(value), 1))
            allowed.add(float(int(value)))
        elif isinstance(value, str):
            allowed.update(extract_numbers(value))
    for text in reference_texts:
        allowed.update(extract_numbers(text or ""))
    expanded = set()
    for value in allowed:
        expanded.add(value)
        expanded.add(round(value, 2))
        expanded.add(round(value, 1))
        expanded.add(float(round(value)))
        expanded.add(abs(value))
    return expanded


def _number_allowed(value: float, allowed: set[float]) -> bool:
    if value in allowed:
        return True
    tolerance = max(0.011, abs(value) * 1e-6)
    return any(abs(value - candidate) <= tolerance for candidate in allowed)


def direction_words(text: str) -> set[str]:
    found = set()
    for match in DIRECTION_WORD_RE.finditer(text or ""):
        found.add(re.sub(r"\s+", " ", match.group().strip().lower()))
    for match in OPTION_LEG_RE.finditer(text or ""):
        found.add(match.group())
    return found


def _direction_family(word: str) -> str:
    if word == "CE":
        return "BUY"
    if word == "PE":
        return "SELL"
    if word in BUY_WORDS:
        return "BUY"
    if word in SELL_WORDS:
        return "SELL"
    return "UNKNOWN"


def allowed_direction_families(facts: Any, *reference_texts: str) -> set[str]:
    families: set[str] = set()
    for value in _walk(facts):
        if isinstance(value, str):
            for word in direction_words(value):
                families.add(_direction_family(word))
            upper = value.strip().upper()
            if upper in {"BUY", "SELL"}:
                families.add(upper)
    for text in reference_texts:
        for word in direction_words(text):
            families.add(_direction_family(word))
    families.discard("UNKNOWN")
    return families


def _html_ok(text: str) -> tuple[bool, str]:
    stack: list[str] = []
    for match in TAG_RE.finditer(text):
        raw = match.group()
        tag = match.group(1).lower()
        if tag not in ALLOWED_TAGS:
            return False, f"disallowed_tag:{tag}"
        if tag == "br":
            continue
        if raw.startswith("</"):
            if not stack or stack.pop() != tag:
                return False, f"unbalanced_tag:{tag}"
        elif not raw.endswith("/>"):
            stack.append(tag)
    if stack:
        return False, f"unclosed_tag:{stack[-1]}"
    return True, ""


def strip_wrappers(text: str) -> str:
    """Remove markdown fences and stray leading labels some models emit."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    cleaned = re.sub(r"^(output|message|response)\s*[:\-]\s*", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def missing_required_numbers(text: str, facts: Any, reference: str | None = None) -> list[str]:
    """Critical fact numbers that disappeared from the AI text.

    Only facts the deterministic draft actually published are required; a value
    the template never showed (for example a mid-point entry when the draft
    prints a trigger zone) must not force a rejection.
    """
    if not isinstance(facts, dict):
        return []
    present = extract_numbers(text)
    published = extract_numbers(reference) if reference is not None else None

    def _seen(target: float, pool: list[float]) -> bool:
        return any(abs(target - number) <= max(0.011, abs(target) * 1e-6) for number in pool)

    missing = []
    for key in CRITICAL_FACT_KEYS:
        if key not in facts:
            continue
        value = facts[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        target = float(value)
        if published is not None and not _seen(target, published):
            continue
        if not _seen(target, present):
            missing.append(key)
    return missing


def guard_text(
    candidate: str,
    facts: Any,
    fallback: str,
    *,
    kind: str = "generic",
    max_length: int = 3900,
) -> GuardResult:
    """Validate AI text against the facts; return safe text plus a reason."""
    text = strip_wrappers(candidate)
    if not text:
        return GuardResult(False, fallback, "empty")
    if len(text) > max_length:
        return GuardResult(False, fallback, "too_long")

    for pattern in BANNED_PATTERNS:
        if pattern.search(text):
            return GuardResult(False, fallback, f"banned_phrase:{pattern.pattern}")

    html_ok, html_reason = _html_ok(text)
    if not html_ok:
        return GuardResult(False, fallback, html_reason)

    allowed = collect_allowed_numbers(facts, fallback)
    for number in extract_numbers(text):
        if not _number_allowed(number, allowed):
            return GuardResult(False, fallback, f"unknown_number:{number}")

    permitted_families = allowed_direction_families(facts, fallback)
    for word in direction_words(text):
        family = _direction_family(word)
        if family not in permitted_families:
            return GuardResult(False, fallback, f"direction_not_allowed:{word}")

    if kind in FACT_CRITICAL_KINDS:
        missing = missing_required_numbers(text, facts, fallback)
        if missing:
            return GuardResult(False, fallback, "missing_facts:" + ",".join(missing))

    return GuardResult(True, text, "ok")
