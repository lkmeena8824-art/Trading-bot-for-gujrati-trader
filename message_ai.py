"""Optional AI wording layer.

The project is zero-cost by default: AI_ENABLED is false and deterministic
message templates are always used. If an operator explicitly supplies an
OpenAI key and enables the flag, the API can polish wording but cannot alter
trade facts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp

from config import AI_ENABLED, AI_MIN_INTERVAL_SECONDS, OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger(__name__)
_last_call = 0.0
_lock = asyncio.Lock()


async def optional_ai_message(kind: str, facts: dict, fallback: str) -> str:
    global _last_call
    if not AI_ENABLED or not OPENAI_API_KEY:
        return fallback
    # A hosted model is intentionally opt-in and rate-limited. A failure never
    # blocks the deterministic message.
    async with _lock:
        wait_for = AI_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call)
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        _last_call = time.monotonic()

        system = (
            "You write concise Telegram trading messages. Return only Telegram HTML text. "
            "Use exactly the supplied facts; never invent or change prices, direction, "
            "OI, P&L, targets, or risk. Be energetic but professional, honest about losses, "
            "and never promise profit."
        )
        user = json.dumps({"kind": kind, "facts": facts}, ensure_ascii=False)
        payload = {
            "model": OPENAI_MODEL,
            "temperature": 0.3,
            "max_tokens": 350,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                    json=payload,
                ) as response:
                    if response.status != 200:
                        logger.warning("AI provider returned HTTP %s", response.status)
                        return fallback
                    body = await response.json()
            text = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return text[:3900] if text else fallback
        except Exception as exc:
            logger.warning("AI wording failed; using free template: %s", exc)
            return fallback
