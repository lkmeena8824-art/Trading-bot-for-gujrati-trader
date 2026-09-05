from __future__ import annotations

import html
from typing import Any

from config import PAYMENT_DETAILS_TEXT, PLANS, RISK_DISCLAIMER
from message_ai import generate_message


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else "N/A"), quote=False)


def _num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def _pct(value: Any) -> str:
    try:
        return f"{float(value):+,.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _net(value: Any) -> str:
    try:
        return f"{float(value):+,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def fmt_morning(data: dict) -> str:
    gift = data.get("gift_nifty")
    try:
        gift_icon = "🟢" if float(gift) >= 0 else "🔴"
    except (TypeError, ValueError):
        gift_icon = "⚪"
    vix = data.get("india_vix")
    return (
        "<b>🔥 GOOD MORNING, ELITE TRADERS!</b>\n\n"
        "<b>📊 GLOBAL CUES</b>\n"
        f"🌍 Dow Jones: <code>{_num(data.get('dow_jones'))}</code> ({_pct(data.get('dow_jones_change'))})\n"
        f"💻 Nasdaq: <code>{_num(data.get('nasdaq'))}</code> ({_pct(data.get('nasdaq_change'))})\n"
        f"{gift_icon} {_esc(data.get('gift_nifty_label', 'Gift Nifty'))}: <code>{_num(gift, 1)}</code> ({_pct(data.get('gift_nifty_change'))})\n"
        f"🛢 Crude: <code>${_num(data.get('crude_oil'))}</code> ({_pct(data.get('crude_oil_change'))})\n\n"
        "<b>📉 VOLATILITY</b>\n"
        f"India VIX: <code>{_num(vix)}</code> ({_pct(data.get('india_vix_change'))})\n\n"
        "<b>🏦 INSTITUTIONAL FLOW — LAST REPORTED</b>\n"
        f"FII/FPI Net: <code>{_net(data.get('fii_net'))}</code>\n"
        f"DII Net: <code>{_net(data.get('dii_net'))}</code>\n"
        f"Date: <code>{_esc(data.get('date', 'N/A'))}</code>\n\n"
        "<b>🎯 TODAY'S FOCUS</b>\n"
        "Sirf A+ Grade setups. Quality over quantity. No forced trade.\n\n"
        f"<i>{_esc(RISK_DISCLAIMER)}</i>"
    )


def fmt_poll_intro() -> str:
    return (
        "<b>🧠 SMART MONEY SENTIMENT POLL</b>\n\n"
        "Aaj ka trap kahan hai? Vote karo — prediction nahi, discipline important hai."
    )


def fmt_premarket() -> str:
    return (
        "<b>🎯 SNIPERS, READY HO JAO!</b>\n\n"
        "Institutional levels mark ho rahe hain. 9:15 ke baad sirf wahi trade execute hoga "
        "jisme Risk:Reward 1:3+ aur multi-timeframe confirmation dono hon.\n\n"
        "Hum 10 trades nahi, 2–3 high-quality setups dhundh rahe hain. <b>Patience is profit.</b>"
    )


def fmt_open_pulse(nifty: dict | None, banknifty: dict | None) -> str:
    def spot(snapshot: dict | None) -> str:
        value = (snapshot or {}).get("spot")
        try:
            return _num(value, 2) if float(value) > 0 else "N/A"
        except (TypeError, ValueError):
            return "N/A"

    return (
        "<b>🔔 MARKET OPEN PULSE</b>\n\n"
        f"NIFTY: <code>{spot(nifty)}</code>\n"
        f"BANKNIFTY: <code>{spot(banknifty)}</code>\n\n"
        "First 15-minute ORB observe ho raha hai. Fake moves se bachein — "
        "confirmed volume breakout ke bina call nahi aayegi."
    )


def fmt_oi(snapshot: dict) -> str:
    return (
        "<b>📊 LIVE OPTION-CHAIN CHECK</b>\n\n"
        f"Underlying: <code>{_esc(snapshot.get('symbol'))}</code>\n"
        f"Spot: <code>{_num(snapshot.get('spot'))}</code>\n"
        f"Expiry: <code>{_esc(snapshot.get('expiry'))}</code>\n"
        f"Source: <code>{_esc(snapshot.get('source'))}</code>\n\n"
        "OI decay ko price action aur volume ke saath confirm kiya ja raha hai."
    )


def fmt_trade(signal: dict, status: str = "ACTIVE") -> str:
    direction = signal.get("direction", "BUY")
    side = "🟢 BUY / LONG" if direction == "BUY" else "🔴 SELL / SHORT"
    return (
        f"<b>🚨 SNIPER CALL ACTIVATED — {_esc(signal.get('symbol'))} 🚨</b>\n\n"
        f"<b>📈 Direction:</b> {side}\n"
        f"<b>🎯 Underlying Trigger Zone:</b> <code>{_num(signal.get('entry_low'))} – {_num(signal.get('entry_high'))}</code>\n"
        f"<b>💵 Option LTP:</b> <code>{_num(signal.get('option_ltp'))}</code>\n"
        f"<b>🛑 Strict SL:</b> <code>{_num(signal.get('sl'))}</code> "
        f"(Risk: <code>{_num(signal.get('risk_points'))}</code> pts)\n\n"
        "<b>💰 MANAGEMENT TARGETS</b>\n"
        f"T1: <code>{_num(signal.get('t1'))}</code> (1:1) → move SL to cost\n"
        f"T2: <code>{_num(signal.get('t2'))}</code> (1:2) → book 50%\n"
        f"T3: <code>{_num(signal.get('t3'))}</code> (1:3) → manage remaining 50%\n\n"
        f"<b>🧠 Logic:</b> <i>{_esc(signal.get('logic'))}</i>\n"
        f"<b>📊 R:R:</b> <code>1:{_num(signal.get('rrr'), 2)}</code>\n"
        f"<b>📌 Status:</b> {_esc(status)}\n\n"
        "<b>⚠️ Risk Management:</b> Use only your predefined risk budget; never over-leverage.\n\n"
        f"<i>{_esc(RISK_DISCLAIMER)}</i>"
    )


def fmt_update(trade: dict, event: str, new_sl: float | None = None, points: float | None = None) -> str:
    symbol = _esc(trade.get("symbol"))
    prefix = f"<b>{symbol}</b>"
    points_text = _num(points) if points is not None else "N/A"
    if event == "BREAKEVEN":
        return (
            f"<b>🛡️ RISK REMOVED — {prefix}</b>\n\n"
            f"1:1 achieved. SL moved to cost: <code>{_num(new_sl)}</code>\n"
            "No profit booking yet. Runner is protected."
        )
    if event == "T2_HIT":
        return (
            f"<b>✅ T2 HIT — {prefix}</b>\n\n"
            f"1:2 achieved. Book 50%. Realized points: <code>{points_text}</code>\n"
            "Remaining 50% is managed toward T3 with a trailing stop."
        )
    if event == "T3_HIT":
        return (
            f"<b>🏆 T3 HIT — {prefix}</b>\n\n"
            f"1:3 achieved. Remaining position closed. Total realized points: <code>{points_text}</code>\n"
            "Quality over quantity — trade complete."
        )
    if event == "SL_HIT":
        return (
            f"<b>❌ SL HIT — {prefix}</b>\n\n"
            f"Realized points: <code>{points_text}</code>\n"
            "Loss is reported honestly. Discipline maintained; no revenge trade."
        )
    return f"<b>📌 TRADE UPDATE — {prefix}</b>\n\nStatus: <code>{_esc(event)}</code>"


def fmt_no_trade(reason: str = "market choppy") -> str:
    return (
        "<b>🛡️ NO TRADE ZONE</b>\n\n"
        f"Market { _esc(reason) }. Aaj capital protect karna hi sabse badi jeet hai.\n\n"
        "No forced call. Kal fresh setup ke saath wapas."
    )


def fmt_closing_summary(nifty: dict | None, banknifty: dict | None, trades: list[dict]) -> str:
    def spot(snapshot: dict | None) -> str:
        value = (snapshot or {}).get("spot")
        try:
            return _num(value, 2) if float(value) > 0 else "N/A"
        except (TypeError, ValueError):
            return "N/A"

    count = len(trades)
    return (
        "<b>🔔 CLOSING BELL SUMMARY</b>\n\n"
        f"NIFTY: <code>{spot(nifty)}</code>\n"
        f"BANKNIFTY: <code>{spot(banknifty)}</code>\n\n"
        "<b>🏦 INSTITUTIONAL FOOTPRINT</b>\n"
        "Levels, VWAP behavior, volume and option-chain positioning were reviewed.\n"
        f"Today's accepted sniper trades: <code>{count}</code>\n\n"
        "Kal ke levels raat ko update honge. No guarantee — only disciplined analysis."
    )


def fmt_pnl(trades: list[dict]) -> str:
    total_points = sum(float(trade.get("realized_points") or trade.get("points_gained") or 0) for trade in trades)
    closed = [trade for trade in trades if trade.get("status") in {"SL_HIT", "T3_HIT", "CLOSED_TARGET", "CLOSED_STOP"}]
    wins = sum(1 for trade in closed if float(trade.get("realized_points") or trade.get("points_gained") or 0) > 0)
    lines = ["<b>🔥 TODAY'S RESULT: QUALITY OVER QUANTITY 🔥</b>", ""]
    if not trades:
        lines.append("No trade taken. Capital protected — this is a valid result.")
    else:
        for index, trade in enumerate(trades, 1):
            points = float(trade.get("realized_points") or trade.get("points_gained") or 0)
            status = _esc(trade.get("status", "OPEN"))
            lines.append(
                f"<b>Trade {index}</b>: {_esc(trade.get('symbol'))} | "
                f"<code>{status}</code> | Points: <code>{points:+,.2f}</code>"
            )
        lines.extend(
            [
                "",
                f"📊 Net realized points: <code>{total_points:+,.2f}</code>",
                f"✅ Closed winners: <code>{wins}/{len(closed) if closed else 0}</code>",
                "",
                "A small SL is part of the plan. No revenge trade. Honest process, repeatable discipline.",
            ]
        )
    lines.extend(["", "🚀 Kal phir sirf A+ setup. Patience is profit.", "", f"<i>{_esc(RISK_DISCLAIMER)}</i>"])
    return "\n".join(lines)


def fmt_plan(pname: str) -> str:
    plan = PLANS.get(pname, {})
    discount = int((1 - plan.get("price", 0) / plan.get("original_price", 1)) * 100)
    emoji = {"Bronze": "👑", "Silver": "🥈", "Gold": "🥇", "Diamond": "💎"}.get(pname, "⭐")
    return (
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        f"{emoji} <b>SELECTED: {_esc(pname.upper())}</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<b>Duration:</b> <i>{plan.get('duration_days')} Days</i>\n"
        f"<b>Price:</b> <code>₹{plan.get('price', 0):,}</code> <i>({discount}% OFF)</i>\n\n"
        f"{_esc(PAYMENT_DETAILS_TEXT)}\n\n"
        "<i>📌 Payment ke baad screenshot bhejna.</i>\n\n"
        f"<i>{_esc(RISK_DISCLAIMER)}</i>"
    )


def fmt_fomo(trade: dict, points: float) -> str:
    return (
        "<b>🔥 VIP RESULT UPDATE 🔥</b>\n\n"
        f"<b>📊 {_esc(trade.get('symbol'))}</b> | {_esc(trade.get('strategy'))}\n"
        "Entry and SL are hidden for VIP members.\n\n"
        f"<b>🏆 Realized points: {points:+,.1f}</b>\n\n"
        f"<i>{_esc(RISK_DISCLAIMER)}</i>"
    )


async def ai_or_fallback(kind: str, facts: dict, fallback: str, context: dict | None = None) -> str:
    """Compatibility wrapper around the Phase 2 AI layer.

    The deterministic template is always the fallback, so callers keep their
    zero-cost behaviour when AI is disabled or when the fact guard rejects the
    AI wording.
    """
    return await generate_message(kind, facts, fallback, context=context)
