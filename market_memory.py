"""Daily market state memory.

The bot keeps a small, factual diary of each Indian market day:

* pre-open cues (India VIX, global proxies) as reported by the free feed,
* the opening spot prints,
* what the *signal engine* decided (bias, no-trade zone, rejection reason),
* how many sniper calls were admitted and how the day closed.

Everything stored here is produced by the deterministic engine or by the market
data feed. AI can read this memory to sound informed, but it can never write to
it and it can never change a stored number: replies are still fact-guarded.
"""

from __future__ import annotations

import logging
from typing import Any

import config
from database import db
from quality_control import market_day

logger = logging.getLogger(__name__)

BIAS_LABELS = {
    "BUY": "buy-side pressure confirmed by the engine",
    "SELL": "sell-side pressure confirmed by the engine",
    "NO_TRADE_ZONE": "choppy / no-trade zone",
    "NO_CONFIRMED_SETUP": "no confirmed A+ setup",
    "DATA_UNAVAILABLE": "free data unavailable, engine failed closed",
    "UNKNOWN": "not established yet",
}


async def _merge(**values: Any) -> dict:
    try:
        return await db.upsert_market_memory(market_day(), **values)
    except Exception:
        logger.exception("Market memory write skipped")
        return {}


async def remember_premarket(morning: dict) -> dict:
    return await _merge(
        phase="PRE_OPEN",
        india_vix=morning.get("india_vix"),
        gift_nifty=morning.get("gift_nifty"),
        gift_nifty_change=morning.get("gift_nifty_change"),
        dow_jones_change=morning.get("dow_jones_change"),
        nasdaq_change=morning.get("nasdaq_change"),
        crude_oil_change=morning.get("crude_oil_change"),
        fii_net=morning.get("fii_net"),
        dii_net=morning.get("dii_net"),
        data_source=morning.get("source"),
    )


async def remember_open(nifty: dict | None, banknifty: dict | None) -> dict:
    spot = float((nifty or {}).get("spot") or 0) or None
    return await _merge(
        phase="OPEN",
        spot_open=spot,
        banknifty_open=float((banknifty or {}).get("spot") or 0) or None,
    )


async def remember_decision(slot: str | None, decision: Any) -> dict:
    """Record the engine's own verdict for the slot. Engine facts only."""
    signal = getattr(decision, "signal", None)
    reasons = list(getattr(decision, "reasons", ()) or ())
    if signal:
        bias = str(signal.get("direction") or "UNKNOWN").upper()
    elif getattr(decision, "no_trade_zone", False):
        bias = "NO_TRADE_ZONE"
    elif getattr(decision, "data_unavailable", False):
        bias = "DATA_UNAVAILABLE"
    else:
        bias = "NO_CONFIRMED_SETUP"
    return await _merge(
        bias=bias,
        last_slot=slot or "OUTSIDE_WINDOW",
        last_reason=reasons[0] if reasons else "",
        last_reasons=reasons[:3],
    )


async def remember_trade_posted(signal: dict, trade_id: int | None) -> dict:
    state = {}
    try:
        state = await db.get_market_memory(market_day())
    except Exception:
        logger.exception("Market memory read skipped")
    taken = int(state.get("trades_taken") or 0) + 1
    return await _merge(
        phase="IN_SESSION",
        trades_taken=taken,
        last_trade_id=trade_id,
        last_trade_symbol=signal.get("symbol"),
        last_trade_slot=signal.get("slot"),
    )


async def remember_no_trade(state: str, reason: str) -> dict:
    return await _merge(
        no_trade=1,
        bias=state or "NO_CONFIRMED_SETUP",
        last_reason=reason,
        headline="No-trade day so far: capital protection mode.",
    )


async def remember_close(trades: list[dict], nifty: dict | None, banknifty: dict | None) -> dict:
    wins = sum(1 for trade in trades if trade.get("status") in {"T3_HIT", "CLOSED_TARGET"})
    losses = sum(1 for trade in trades if trade.get("status") in {"SL_HIT", "CLOSED_STOP"})
    net_points = sum(
        float(trade.get("realized_points") or trade.get("points_gained") or 0) for trade in trades
    )
    if not trades:
        headline = "No sniper call was admitted; capital protected."
    else:
        headline = f"{len(trades)} sniper call(s) tracked, net {net_points:+.2f} points."
    return await _merge(
        phase="CLOSED",
        spot_close=float((nifty or {}).get("spot") or 0) or None,
        banknifty_close=float((banknifty or {}).get("spot") or 0) or None,
        trades_taken=len(trades),
        wins=wins,
        losses=losses,
        net_points=round(net_points, 2),
        no_trade=0 if trades else 1,
        headline=headline,
    )


async def today() -> dict:
    try:
        return await db.get_market_memory(market_day())
    except Exception:
        logger.exception("Market memory unavailable")
        return {"market_day": market_day(), "phase": "UNKNOWN", "bias": "UNKNOWN", "data": {}}


async def recent(limit: int | None = None) -> list[dict]:
    try:
        return await db.recent_market_memory(limit or config.MARKET_MEMORY_DAYS)
    except Exception:
        logger.exception("Market memory history unavailable")
        return []


def describe(state: dict) -> str:
    """Deterministic Hinglish description of one stored market day."""
    if not state:
        return "Aaj ka market state abhi record nahi hua."
    phase = str(state.get("phase") or "UNKNOWN")
    bias = str(state.get("bias") or "UNKNOWN").upper()
    bias_text = BIAS_LABELS.get(bias, BIAS_LABELS["UNKNOWN"])
    trades = int(state.get("trades_taken") or 0)
    parts = [f"Phase: {phase}", f"Engine read: {bias_text}", f"Calls today: {trades}"]
    if state.get("no_trade"):
        parts.append("No-trade discipline active")
    if state.get("net_points") not in (None, 0):
        parts.append(f"Net points: {float(state['net_points']):+.2f}")
    return " | ".join(parts)


async def reply_context() -> dict[str, Any]:
    """Compact, fact-only context handed to the reply layer.

    Only stored engine facts are exposed. There is no forecast, no target and
    no direction advice here, so a reply can never leak a trade decision.
    """
    state = await today()
    history = await recent()
    closed_days = [day for day in history if day.get("phase") == "CLOSED"]
    return {
        "market_day": state.get("market_day"),
        "phase": state.get("phase", "UNKNOWN"),
        "engine_read": BIAS_LABELS.get(str(state.get("bias") or "UNKNOWN").upper(), BIAS_LABELS["UNKNOWN"]),
        "calls_today": int(state.get("trades_taken") or 0),
        "no_trade_today": bool(state.get("no_trade")),
        "net_points_today": float(state.get("net_points") or 0),
        "last_reason": (state.get("data") or {}).get("last_reason", ""),
        "days_remembered": len(history),
        "closed_days_remembered": len(closed_days),
        "summary": describe(state),
    }
