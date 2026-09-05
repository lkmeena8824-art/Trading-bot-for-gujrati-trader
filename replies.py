"""Contextual, human-style Telegram replies.

Design:

* Intent detection is a small deterministic classifier (keywords, Hinglish
  included). No network call is needed, so replies work in zero-cost mode.
* The reply text is composed from templates that already know the member
  (community memory) and the day (market memory), which is what makes them feel
  human instead of canned.
* AI, when enabled, only rephrases that draft and is fact-guarded, so a reply
  can never turn into a BUY/SELL recommendation or a fabricated number.
* Every "what should I buy?" style question is answered with education and
  channel process, never with a direction. That rule lives in the templates and
  is double-checked by :mod:`ai_guard` because the reply facts never contain a
  direction.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

import config
import community
import market_memory
from database import db
from message_ai import generate_message
from quality_control import describe_slot, is_market_session_open, market_day

logger = logging.getLogger(__name__)

_last_reply_at: dict[int, float] = {}

INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greeting", re.compile(r"\b(hi|hii+|hello|hey|namaste|namaskar|kem cho|good\s*(morning|evening|afternoon))\b", re.I)),
    ("thanks", re.compile(r"\b(thanks|thank\s*you|thx|dhanyawad|shukriya|aabhar)\b", re.I)),
    ("payment", re.compile(r"\b(payment|paid|upi|paytm|phonepe|gpay|screenshot|invoice|refund)\b", re.I)),
    ("vip", re.compile(r"\b(vip|plan|plans|subscription|subscribe|membership|price|kitna|charges|fees)\b", re.I)),
    ("tip_request", re.compile(r"(kya\s*(lein|le|karu|karein)|tip|call\s*do|target\s*bat|kaunsa|konsa|batao|entry\s*bata|position\s*batao|kharid|bech|buy\s*karu|sell\s*karu)", re.I)),
    ("loss", re.compile(r"\b(loss|nuksan|sl\s*hit|stop\s*loss\s*hit|gaya|blown|down|khatam)\b", re.I)),
    ("profit", re.compile(r"\b(profit|target\s*hit|booked|kama|jeet|win|paisa\s*ban)\b", re.I)),
    ("poll", re.compile(r"\b(poll|vote|voted|sentiment|survey)\b", re.I)),
    ("timing", re.compile(r"\b(kab|when|time|timing|window|slot|schedule|kitne\s*baje)\b", re.I)),
    ("market", re.compile(r"\b(market|nifty|banknifty|sensex|trend|view|analysis|oi|option\s*chain|vix)\b", re.I)),
    ("complaint", re.compile(r"\b(fraud|scam|fake|bakwas|useless|refund\s*do|cheat)\b", re.I)),
]

TIP_REFUSAL = (
    "Direction main personally nahi bata sakta — channel ka rule simple hai: "
    "engine confirm kare tabhi call jaati hai, warna nahi."
)


def detect_intent(text: str) -> str:
    """Classify a member message. Deterministic, keyword based, free."""
    cleaned = (text or "").strip()
    if not cleaned:
        return "unknown"
    for intent, pattern in INTENT_PATTERNS:
        if pattern.search(cleaned):
            return intent
    if cleaned.endswith("?"):
        return "market"
    return "unknown"


def _variant(options: list[str], *seed_parts: Any) -> str:
    """Pick a template deterministically so replies vary per member/day.

    A stable digest is used instead of ``hash()`` so the same member gets the
    same wording on a restart, while different members still see variety.
    """
    seed = "|".join(str(part) for part in seed_parts).encode("utf-8")
    index = int(hashlib.sha1(seed).hexdigest(), 16) % len(options)
    return options[index]


def _name(profile: dict, fallback: str = "Bhai") -> str:
    name = (profile.get("first_name") or "").strip()
    return name.split(" ")[0] if name else fallback


def build_facts(
    intent: str,
    profile: dict,
    market: dict,
    crowd: dict,
    *,
    user_id: int = 0,
) -> dict[str, Any]:
    """Fact bundle for the reply. Deliberately free of trade instructions."""
    return {
        "intent": intent,
        "persona": config.AI_PERSONA_NAME,
        "member": {
            "name": _name(profile, ""),
            "known": bool(profile.get("known")),
            "poll_votes": int(profile.get("poll_votes") or 0),
            "regular": bool(profile.get("regular")),
            "voted_today": bool(profile.get("voted_today")),
            "usual_mood": profile.get("usual_mood", "unknown"),
        },
        "market_state": {
            "market_day": market.get("market_day"),
            "phase": market.get("phase"),
            "engine_read": market.get("engine_read"),
            "calls_today": market.get("calls_today", 0),
            "no_trade_today": market.get("no_trade_today", False),
            "net_points_today": market.get("net_points_today", 0),
            "session_open": is_market_session_open(),
            "slot": describe_slot(),
        },
        "community": {
            "voters_today": crowd.get("voters_today", 0),
            "top_option": crowd.get("top_option", ""),
            "active_members_7d": crowd.get("active_members_7d", 0),
        },
        "rules": [
            "no direction advice",
            "no new numbers",
            "honest about risk",
        ],
        "_seed": user_id,
    }


def compose_reply(facts: dict[str, Any]) -> str:
    """Deterministic human-style Hinglish reply (the always-free fallback)."""
    intent = facts.get("intent", "unknown")
    member = facts.get("member", {})
    market = facts.get("market_state", {})
    crowd = facts.get("community", {})
    name = member.get("name") or "Bhai"
    seed = (facts.get("_seed"), market.get("market_day"), intent)
    calls = int(market.get("calls_today") or 0)
    session_line = (
        "Market abhi live hai" if market.get("session_open") else "Market abhi band hai"
    )
    memory_line = (
        f"Tere {member.get('poll_votes')} poll votes yaad hain, tu regular hai 🙏"
        if member.get("regular")
        else "Poll me vote karta reh, community ka mood wahi se banta hai."
    )

    if intent == "greeting":
        return _variant(
            [
                f"Arre {name}! 👋 {session_line}. Aaj ke liye engine ka status: {market.get('engine_read')}. {memory_line}",
                f"Hello {name}! 🙏 {session_line}. Abhi tak {calls} sniper call admit hui hai. Patience wala din hai.",
                f"{name}, welcome back! {session_line}. Setup mile to hi call jaayegi, warna nahi. {memory_line}",
            ],
            *seed,
        )

    if intent == "thanks":
        return _variant(
            [
                f"Bas process ka kaam hai {name} 🙏 Tu discipline maintain kar, baaki engine dekh lega.",
                f"Thank you {name}! Risk management tere haath me hai — wahi asli edge hai.",
                f"Khush raho {name} 😊 Aise hi capital ko respect karte raho.",
            ],
            *seed,
        )

    if intent == "tip_request":
        return (
            f"{name}, {TIP_REFUSAL}\n\n"
            f"Aaj ka engine read: {market.get('engine_read')}. Abhi tak {calls} call admit hui hai "
            f"({market.get('slot')} slot state). Jab multi-confirmation pass hota hai, "
            "call apne aap channel me aa jaati hai — entry, SL aur targets ke saath. "
            "Tab tak apna risk plan ready rakh."
        )

    if intent == "loss":
        return (
            f"{name}, honestly bolun — SL lagna process ka hissa hai, failure nahi. "
            "Revenge trade sabse mehnga hota hai. "
            f"Aaj ka record: {calls} call(s), engine read {market.get('engine_read')}. "
            "Risk per trade fix rakh aur agla A+ setup ka wait kar."
        )

    if intent == "profit":
        return (
            f"Shabaash {name} 👏 Par ek trade se judge mat kar — consistency important hai. "
            f"Aaj {calls} call track hui hai. Partial booking aur SL discipline hamesha follow karte rehna."
        )

    if intent == "payment":
        return (
            f"{name}, payment ke baad screenshot admin ko bhej de — activation manual verify hota hai. "
            "Details /plans me hain. Kisi random link ya DM par paisa mat bhejna."
        )

    if intent == "vip":
        return (
            f"{name}, saare plans aur pricing /plans command me hain. "
            "VIP ka matlab zyada calls nahi — same quality-first process, bas early aur detailed updates. "
            "Koi profit guarantee nahi milegi, sirf transparent process."
        )

    if intent == "poll":
        voters = int(crowd.get("voters_today") or 0)
        crowd_line = (
            f"Aaj {voters} logon ne vote kiya, crowd choice: {crowd.get('top_option') or 'mixed'}."
            if voters
            else "Aaj abhi tak poll par vote nahi aaya."
        )
        return (
            f"{name}, {crowd_line} Yaad rakh — poll sirf sentiment hai, trade decision nahi. "
            f"{memory_line}"
        )

    if intent == "timing":
        return (
            f"{name}, sniper windows fix hain: 09:15–10:15, 10:45–11:15 aur 12:45–13:15 IST. "
            "In windows me bhi call tabhi jaati hai jab confirmation mile. "
            f"{session_line}."
        )

    if intent == "complaint":
        return (
            f"{name}, tera concern valid hai. Yahan koi profit guarantee nahi di jaati — "
            "har call ka entry, SL, target aur result channel me publicly record hota hai, "
            "loss bhi wahi report hota hai. Koi issue ho to admin ko DM kar."
        )

    if intent == "market":
        return (
            f"{name}, aaj ka engine read: {market.get('engine_read')}. "
            f"{session_line}, abhi tak {calls} call admit hui hai. "
            "Main personally view nahi deta — levels, volume aur OI confirmation ka kaam engine karta hai, "
            "aur confirm hote hi poori detail channel me aati hai."
        )

    return _variant(
        [
            f"{name}, samajh gaya 🙏 Detailed help ke liye /plans dekh le ya admin ko DM kar.",
            f"{name}, main sirf process aur updates par baat karta hoon. Call aate hi channel me post hoti hai.",
            f"Noted {name}! Market state: {market.get('engine_read')}. Baaki koi doubt ho to poochh le.",
        ],
        *seed,
    )


async def should_reply(user_id: int) -> bool:
    """Cooldown + daily cap so the bot never spams a member."""
    if not config.REPLY_ENABLED:
        return False
    now = time.monotonic()
    last = _last_reply_at.get(user_id, 0.0)
    if now - last < config.REPLY_COOLDOWN_SECONDS:
        return False
    try:
        count = await db.register_reply(user_id, market_day())
    except Exception:
        logger.exception("Reply accounting failed; replying once anyway")
        count = 1
    if count > config.REPLY_MAX_PER_USER_PER_DAY:
        return False
    _last_reply_at[user_id] = now
    return True


def reset_cooldowns() -> None:
    _last_reply_at.clear()


async def build_reply(
    user_id: int,
    text: str,
    *,
    username: str | None = None,
    first_name: str | None = None,
) -> tuple[str, str]:
    """Return ``(intent, reply_text)``. AI-polished only when it stays safe."""
    intent = detect_intent(text)
    await community.note_message(user_id, intent, username=username, first_name=first_name)
    profile = await community.member_profile(user_id)
    if first_name and not profile.get("first_name"):
        profile["first_name"] = first_name
    market = await market_memory.reply_context()
    crowd = await community.snapshot()

    facts = build_facts(intent, profile, market, crowd, user_id=user_id)
    fallback = compose_reply(facts)
    reply = await generate_message(
        f"reply_{intent}",
        facts,
        fallback,
        context={"style": "one short human paragraph", "no_advice": True},
    )
    return intent, reply
