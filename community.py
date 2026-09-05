"""Poll-answer tracking and community memory.

What this module remembers is *engagement*, never market truth:

* who voted in the daily sentiment poll and for which option,
* how often a member participates and what their usual leaning is,
* how many members are active, and what the crowd picked today.

Community sentiment is explicitly **not** a trading input. The signal engine
never reads this module; it only feeds tone and context for human-style replies
and recap wording. Storage failures degrade to empty memory so Telegram
behaviour is never blocked.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import config
from database import db
from quality_control import market_day

logger = logging.getLogger(__name__)

# Maps the four standing poll options to a short, neutral mood label used for
# wording only.
OPTION_MOODS = {
    "🟢 Aggressive Long": "bullish",
    "🔴 Defensive Short": "bearish",
    "🟡 Sideways Trap": "rangebound",
    "🛡️ No Trade": "cautious",
}

EMPTY_SNAPSHOT: dict[str, Any] = {
    "market_day": "",
    "members": 0,
    "active_members_7d": 0,
    "voters_today": 0,
    "tally": {},
    "top_option": "",
    "top_votes": 0,
    "top_mood": "unknown",
    "participation_note": "Poll data abhi available nahi hai.",
}


def mood_for_option(option_text: str | None) -> str:
    if not option_text:
        return "unknown"
    return OPTION_MOODS.get(option_text, "unknown")


async def remember_poll(poll_id: str, question: str, options: list[str], chat_id: int, message_id: int | None) -> None:
    if not config.POLL_TRACKING_ENABLED:
        return
    try:
        await db.record_poll(
            poll_id=poll_id,
            question=question,
            options=options,
            market_day=market_day(),
            chat_id=chat_id,
            message_id=message_id,
        )
    except Exception:
        logger.exception("Could not persist poll %s", poll_id)


async def record_answer(
    poll_id: str,
    user_id: int,
    option_ids: list[int],
    username: str | None = None,
    first_name: str | None = None,
) -> dict[str, Any]:
    """Store one poll answer and update that member's community memory."""
    if not config.POLL_TRACKING_ENABLED:
        return {}
    try:
        poll = await db.get_poll(poll_id) or {}
        try:
            options = json.loads(poll.get("options") or "[]")
        except (TypeError, ValueError):
            options = []
        option_texts = [options[i] for i in option_ids if 0 <= i < len(options)]
        day = poll.get("market_day") or market_day()
        await db.record_poll_answer(
            poll_id=poll_id,
            user_id=user_id,
            option_ids=option_ids,
            option_texts=option_texts,
            username=username,
            first_name=first_name,
            market_day=day,
        )
        if config.COMMUNITY_MEMORY_ENABLED:
            await db.note_community_vote(
                user_id=user_id,
                option_text=option_texts[0] if option_texts else None,
                market_day=day,
                username=username,
                first_name=first_name,
            )
        return {
            "poll_id": poll_id,
            "user_id": user_id,
            "options": option_texts,
            "mood": mood_for_option(option_texts[0] if option_texts else None),
            "retracted": not option_ids,
            "market_day": day,
        }
    except Exception:
        logger.exception("Could not record poll answer for user %s", user_id)
        return {}


async def note_message(user_id: int, intent: str | None, username: str | None = None, first_name: str | None = None) -> dict:
    if not config.COMMUNITY_MEMORY_ENABLED:
        return {}
    try:
        return await db.note_community_message(
            user_id=user_id,
            intent=intent,
            market_day=market_day(),
            username=username,
            first_name=first_name,
        )
    except Exception:
        logger.exception("Could not update community memory for %s", user_id)
        return {}


async def member_profile(user_id: int) -> dict[str, Any]:
    """Compact, wording-safe profile of one community member."""
    if not config.COMMUNITY_MEMORY_ENABLED:
        return {"known": False, "poll_votes": 0, "usual_mood": "unknown"}
    try:
        member = await db.get_community_member(user_id) or {}
    except Exception:
        logger.exception("Community memory read failed for %s", user_id)
        member = {}
    if not member:
        return {"known": False, "poll_votes": 0, "usual_mood": "unknown", "messages": 0}
    try:
        tally = json.loads(member.get("sentiment_tally") or "{}")
    except (TypeError, ValueError):
        tally = {}
    usual_option = max(tally.items(), key=lambda item: item[1])[0] if tally else None
    votes = int(member.get("poll_votes") or 0)
    return {
        "known": True,
        "first_name": member.get("first_name") or "",
        "poll_votes": votes,
        "messages": int(member.get("messages") or 0),
        "last_option": member.get("last_option") or "",
        "last_vote_day": member.get("last_vote_day") or "",
        "last_intent": member.get("last_intent") or "",
        "usual_mood": mood_for_option(usual_option),
        "regular": votes >= 3,
        "voted_today": member.get("last_vote_day") == market_day(),
    }


async def snapshot(day: str | None = None) -> dict[str, Any]:
    """Today's community picture: participation counts and crowd choice."""
    if not config.COMMUNITY_MEMORY_ENABLED:
        return dict(EMPTY_SNAPSHOT, market_day=day or market_day())
    try:
        stats = await db.community_stats(day or market_day())
    except Exception:
        logger.exception("Community stats unavailable")
        return dict(EMPTY_SNAPSHOT, market_day=day or market_day())
    stats["top_mood"] = mood_for_option(stats.get("top_option"))
    voters = int(stats.get("voters_today") or 0)
    if voters == 0:
        stats["participation_note"] = "Aaj poll par abhi koi vote nahi aaya."
    else:
        stats["participation_note"] = (
            f"Aaj {voters} members ne poll me vote kiya; crowd ka jhukav "
            f"{stats.get('top_option') or 'mixed'} par hai."
        )
    return stats


def describe_snapshot(stats: dict[str, Any]) -> str:
    """Deterministic one-liner for channel recaps (no market prediction)."""
    voters = int(stats.get("voters_today") or 0)
    if voters <= 0:
        return "🧠 Community poll: aaj koi vote record nahi hua."
    top = stats.get("top_option") or "Mixed"
    return (
        f"🧠 Community poll: {voters} votes | Crowd choice: {top}. "
        "Yeh sirf sentiment hai, trade decision nahi."
    )
