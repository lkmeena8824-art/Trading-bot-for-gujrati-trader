from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime
from typing import Any

from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import (
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

from config import (
    ADMIN_IDS,
    AI_DAILY_CALL_BUDGET,
    AUTO_TRADE_CHANNEL,
    REPLY_ENABLED,
    REPLY_IN_GROUPS,
    ENABLE_BTST,
    FREE_CHANNEL_ID,
    MAX_DAILY_TRADES,
    MARKET_CLOSE_TIME,
    MARKET_OPEN_TIME,
    PAYMENT_DETAILS_TEXT,
    PAYMENT_TELEGRAM_LINK,
    PAYMENT_WHATSAPP_LINK,
    PLANS,
    RISK_DISCLAIMER,
    SIGNAL_SYMBOL,
    VIP_CHANNEL_ID,
    WEBHOOK_PORT,
    WEBHOOK_SECRET,
)
from database import db
from market_data import fetch_candles, get_real_candles, market_data
from messaging import (
    fmt_closing_summary,
    fmt_fomo,
    fmt_morning,
    fmt_no_trade,
    fmt_oi,
    fmt_open_pulse,
    fmt_pnl,
    fmt_poll_intro,
    fmt_premarket,
    fmt_plan,
    fmt_trade,
    fmt_update,
)
import community
import market_memory
import replies
from message_ai import ai_stats, generate_message
from quality_control import (
    is_market_day,
    is_market_session_open,
    is_trade_window,
    market_day,
    slot_for_time,
)
from signal_engine import build_signal

logger = logging.getLogger(__name__)

# Compatibility variable retained, but the persistent DB slot reservation is
# now the real anti-duplicate control.
LAST_AUTO_POST_TIME = 0.0


def can_trade() -> bool:
    """Compatibility wrapper: true only during one of the three trade slots."""
    return is_trade_window()


def is_adm(uid: int) -> bool:
    return uid in ADMIN_IDS


def _channel_id(channel_type: str) -> int:
    return VIP_CHANNEL_ID if channel_type.upper() == "VIP" else FREE_CHANNEL_ID


async def safe_send(bot, cid: int, text: str, rm=None) -> int | None:
    """Send Telegram HTML safely with RetryAfter/network retries."""
    if not cid:
        logger.error("Telegram channel ID is missing")
        return None
    for attempt in range(3):
        try:
            message = await bot.send_message(
                chat_id=cid,
                text=text,
                parse_mode="HTML",
                reply_markup=rm,
                disable_web_page_preview=True,
            )
            return message.message_id
        except RetryAfter as exc:
            if attempt >= 2:
                logger.error("Telegram flood limit exhausted: %s", exc)
                return None
            await asyncio.sleep(float(exc.retry_after))
        except (TimedOut, NetworkError) as exc:
            if attempt >= 2:
                logger.error("Telegram network delivery failed: %s", exc)
                return None
            await asyncio.sleep(2**attempt)
        except TelegramError as exc:
            logger.error("Telegram delivery failed: %s", exc)
            return None
    return None


async def post_call(
    sym: str,
    direction: str,
    entry: float,
    sl: float,
    t1: float,
    t2: float,
    t3: float,
    strategy: str,
    channel_type: str,
    bot,
    *,
    logic: str | None = None,
    underlying: str | None = None,
    option_type: str | None = None,
    option_strike: float | None = None,
    option_ltp: float | None = None,
    slot: str | None = None,
    source: str = "AUTO",
    enforce_limit: bool = True,
    entry_low: float | None = None,
    entry_high: float | None = None,
    risk_points: float | None = None,
    rrr: float | None = None,
) -> int | None:
    """Create, deliver and activate one trade exactly once.

    Normal automated calls are admitted atomically by the database. Manual
    admin overrides remain available for backward compatibility and are clearly
    labelled in the persisted source field.
    """
    direction = direction.upper()
    channel_type = channel_type.upper()
    risk_points = risk_points if risk_points is not None else abs(entry - sl)
    rrr = rrr if rrr is not None else (abs(t3 - entry) / risk_points if risk_points else 0.0)
    signal = {
        "symbol": sym,
        "underlying": underlying or sym,
        "direction": direction,
        "option_type": option_type,
        "option_strike": option_strike,
        "option_ltp": option_ltp,
        "entry": entry,
        "entry_low": entry_low if entry_low is not None else entry,
        "entry_high": entry_high if entry_high is not None else entry,
        "sl": sl,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "risk_points": risk_points,
        "rrr": rrr,
        "strategy": strategy,
        "logic": logic or strategy,
    }
    if enforce_limit and not slot:
        logger.info("Trade rejected: no active trade slot")
        return None

    trade_id = await db.create_trade_if_allowed(
        sym=sym,
        direction=direction,
        entry=entry,
        sl=sl,
        t1=t1,
        t2=t2,
        t3=t3,
        strategy=strategy,
        channel=channel_type,
        market_day=market_day(),
        slot=slot,
        max_daily_trades=MAX_DAILY_TRADES,
        underlying=underlying or sym,
        option_type=option_type,
        option_strike=option_strike,
        option_ltp=option_ltp,
        entry_low=signal["entry_low"],
        entry_high=signal["entry_high"],
        logic=signal["logic"],
        source=source,
        rrr=rrr,
        risk_points=risk_points,
        enforce_limit=enforce_limit,
    )
    if trade_id is None:
        logger.info("Trade rejected by daily/slot quality gate: %s", sym)
        return None

    message_id = await safe_send(bot, _channel_id(channel_type), fmt_trade(signal), None)
    if message_id is None:
        await db.update_trade(trade_id, status="DELIVERY_FAILED", last_event="DELIVERY_FAILED")
        await db.record_trade_event(trade_id, "DELIVERY_FAILED", metadata="telegram_send_failed")
        return None

    await db.update_trade(trade_id, status="ACTIVE", message_id=message_id, last_event="POSTED")
    await db.record_trade_event(trade_id, "POSTED", price=entry, metadata=source)
    logger.info("SNIPER CALL %s posted as trade %s in slot %s", sym, trade_id, slot)
    return trade_id


async def post_upd(tid: int, event: str, new_sl: float | None = None, points: float | None = None, bot=None) -> None:
    trade = await db.get_trade(tid)
    if not trade:
        return
    event = event.upper()
    status_map = {
        "BREAKEVEN": "BREAKEVEN",
        "T2_HIT": "PARTIAL_BOOKED",
        "T3_HIT": "T3_HIT",
        "SL_HIT": "SL_HIT",
    }
    status = status_map.get(event, event)
    fields: dict[str, Any] = {
        "status": status,
        "last_event": event,
        "points_gained": points if points is not None else trade.get("points_gained", 0),
        "realized_points": points if points is not None else trade.get("realized_points", 0),
    }
    if new_sl is not None:
        fields["current_sl"] = new_sl
    if event == "BREAKEVEN":
        fields["breakeven_moved"] = 1
    elif event == "T2_HIT":
        fields["partial_booked"] = 1
        fields["remaining_quantity"] = 0.5
    elif event in {"T3_HIT", "SL_HIT"}:
        fields["remaining_quantity"] = 0
        fields["closed_at"] = datetime.utcnow().isoformat()
        fields["exit_price"] = new_sl if event == "SL_HIT" else (trade.get("target3") or trade.get("entry_price"))
    await db.update_trade(tid, **fields)
    await db.record_trade_event(tid, event, price=new_sl, points=points)

    if bot is not None:
        await safe_send(
            bot,
            _channel_id(trade.get("channel_type", "FREE")),
            fmt_update(trade, event, new_sl, points),
        )
        if event in {"T2_HIT", "T3_HIT"} and trade.get("channel_type") == "VIP":
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("🚀 JOIN VIP FOR QUALITY CALLS", url="https://t.me/+4oN8IsDUF1FhNjY1")]]
            )
            await safe_send(bot, FREE_CHANNEL_ID, fmt_fomo(trade, points or 0), rm=keyboard)


# ===================== SCHEDULER JOBS =====================
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")


async def job_morning(app) -> None:
    data = await market_data.morning_data()
    fallback = fmt_morning(data)
    # Daily market memory starts here: pre-open cues are stored as engine facts.
    await market_memory.remember_premarket(data)
    context = {"market_memory": await market_memory.reply_context()}
    text = await generate_message("morning_brief", data, fallback, context=context)
    await safe_send(app.bot, FREE_CHANNEL_ID, text)


POLL_QUESTION = "🧠 AAJ KA SMART MONEY SENTIMENT?"
POLL_OPTIONS = [
    "🟢 Aggressive Long",
    "🔴 Defensive Short",
    "🟡 Sideways Trap",
    "🛡️ No Trade",
]


async def job_hype_poll(app) -> None:
    await safe_send(app.bot, FREE_CHANNEL_ID, fmt_poll_intro())
    try:
        message = await app.bot.send_poll(
            chat_id=FREE_CHANNEL_ID,
            question=POLL_QUESTION,
            options=POLL_OPTIONS,
            is_anonymous=False,
        )
    except TelegramError as exc:
        logger.error("Poll failed: %s", exc)
        return

    poll = getattr(message, "poll", None)
    if poll is not None:
        # Remember the poll so every incoming answer can be attributed.
        await community.remember_poll(
            poll_id=str(poll.id),
            question=POLL_QUESTION,
            options=list(POLL_OPTIONS),
            chat_id=FREE_CHANNEL_ID,
            message_id=getattr(message, "message_id", None),
        )


async def on_poll_answer(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track who voted for what and update community memory."""
    answer = update.poll_answer
    if answer is None:
        return
    user = getattr(answer, "user", None)
    if user is None:
        return
    result = await community.record_answer(
        poll_id=str(answer.poll_id),
        user_id=user.id,
        option_ids=list(answer.option_ids or []),
        username=user.username,
        first_name=user.first_name,
    )
    if result:
        logger.info(
            "Poll answer recorded: user=%s options=%s retracted=%s",
            user.id,
            result.get("options"),
            result.get("retracted"),
        )



async def job_premarket(app) -> None:
    fallback = fmt_premarket()
    context = {"market_memory": await market_memory.reply_context(), "community": await community.snapshot()}
    text = await generate_message("premarket_hype", {"phase": "PRE_MARKET"}, fallback, context=context)
    await safe_send(app.bot, FREE_CHANNEL_ID, text)


async def _spot_dict(symbol: str) -> dict:
    candles = await fetch_candles(symbol, "5m", "5d", 5)
    spot = candles[-1].close if candles else 0.0
    return {"symbol": symbol, "spot": spot, "source": "FREE_YFINANCE"}


async def job_open_pulse(app) -> None:
    nifty, banknifty = await asyncio.gather(_spot_dict("NIFTY"), _spot_dict("BANKNIFTY"))
    fallback = fmt_open_pulse(nifty, banknifty)
    await market_memory.remember_open(nifty, banknifty)
    text = await generate_message(
        "open_pulse",
        {"nifty": nifty, "banknifty": banknifty},
        fallback,
    )
    await safe_send(app.bot, FREE_CHANNEL_ID, text)


async def _select_channel() -> str:
    # Keep existing Free/VIP behavior configurable while the global DB gate
    # enforces the same maximum of three for every channel.
    return "VIP" if AUTO_TRADE_CHANNEL == "VIP" else "FREE"


async def job_auto_scanner(app) -> None:
    slot = slot_for_time()
    if not is_market_day() or not slot:
        return
    snapshot = await market_data.snapshot(SIGNAL_SYMBOL)
    decision = build_signal(snapshot, slot)
    # The engine's verdict (and only the engine's verdict) enters daily memory.
    await market_memory.remember_decision(slot, decision)
    if not decision.signal:
        logger.info("No signal in %s: %s", slot, "; ".join(decision.reasons))
        return
    channel = await _select_channel()
    signal = decision.signal
    trade_id = await post_call(
        signal["symbol"],
        signal["direction"],
        signal["entry"],
        signal["sl"],
        signal["t1"],
        signal["t2"],
        signal["t3"],
        signal["strategy"],
        channel,
        app.bot,
        logic=signal["logic"],
        underlying=signal["underlying"],
        option_type=signal["option_type"],
        option_strike=signal["option_strike"],
        option_ltp=signal["option_ltp"],
        slot=slot,
        source="AUTO_SNIPER",
        entry_low=signal["entry_low"],
        entry_high=signal["entry_high"],
        risk_points=signal["risk_points"],
        rrr=signal["rrr"],
    )
    if trade_id:
        await market_memory.remember_trade_posted({**signal, "slot": slot}, trade_id)


async def job_no_trade_check(app) -> None:
    if not is_market_day() or (await db.get_today_count()) > 0:
        return
    snapshot = await market_data.snapshot(SIGNAL_SYMBOL)
    decision = build_signal(snapshot)
    await market_memory.remember_decision(slot_for_time(), decision)
    if decision.data_unavailable:
        logger.info("No-trade checkpoint skipped because data is unavailable")
        return
    reason = decision.reasons[0] if decision.reasons else "no A+ setup confirmed"
    state = "NO_TRADE_ZONE" if decision.no_trade_zone else "NO_CONFIRMED_SETUP"
    if await db.mark_no_trade(state=state):
        await market_memory.remember_no_trade(state, reason)
        fallback = fmt_no_trade(reason)
        text = await generate_message(
            "no_trade",
            {"reason": reason, "state": state},
            fallback,
            context={"market_memory": await market_memory.reply_context()},
        )
        await safe_send(app.bot, FREE_CHANNEL_ID, text)


async def job_btst(app) -> None:
    # Retained for compatibility, but never bypasses the new three-slot policy.
    if not ENABLE_BTST:
        return
    logger.info("BTST is enabled but is disabled outside the configured sniper slots")


async def job_oi(app) -> None:
    if not is_market_session_open():
        return
    chain = await market_data.option_chain(SIGNAL_SYMBOL)
    if not chain:
        return
    text = fmt_oi(
        {
            "symbol": chain.symbol,
            "spot": chain.spot,
            "expiry": chain.expiry,
            "source": chain.source,
        }
    )
    await safe_send(app.bot, FREE_CHANNEL_ID, text)
    await safe_send(app.bot, VIP_CHANNEL_ID, text)


async def job_closing(app) -> None:
    nifty, banknifty = await asyncio.gather(
        _spot_dict("NIFTY"),
        _spot_dict("BANKNIFTY"),
    )
    trades = await db.get_today_trades()
    crowd = await community.snapshot()
    await market_memory.remember_close(trades, nifty, banknifty)
    fallback = fmt_closing_summary(nifty, banknifty, trades) + "\n\n" + community.describe_snapshot(crowd)
    text = await generate_message(
        "closing_summary",
        {
            "nifty": nifty,
            "banknifty": banknifty,
            "trades_count": len(trades),
            "community": crowd,
        },
        fallback,
        context={"market_memory": await market_memory.reply_context()},
    )
    await safe_send(app.bot, FREE_CHANNEL_ID, text)
    if trades:
        await safe_send(app.bot, VIP_CHANNEL_ID, text)


async def job_pnl(app) -> None:
    trades = await db.get_today_trades()
    fallback = fmt_pnl(trades)
    total_points = sum(
        float(trade.get("realized_points") or trade.get("points_gained") or 0) for trade in trades
    )
    text = await generate_message(
        "pnl_report",
        {
            "trades": [
                {
                    "symbol": trade.get("symbol"),
                    "status": trade.get("status"),
                    "points": float(trade.get("realized_points") or trade.get("points_gained") or 0),
                }
                for trade in trades
            ],
            "net_points": round(total_points, 2),
            "count": len(trades),
        },
        fallback,
        context={"market_memory": await market_memory.reply_context()},
    )
    await safe_send(app.bot, FREE_CHANNEL_ID, text)
    if trades:
        await safe_send(app.bot, VIP_CHANNEL_ID, text)

    winners = sum(
        1 for trade in trades
        if trade.get("status") in {"T3_HIT", "CLOSED_TARGET"}
    )
    losers = sum(1 for trade in trades if trade.get("status") in {"SL_HIT", "CLOSED_STOP"})
    total_points = sum(float(trade.get("realized_points") or trade.get("points_gained") or 0) for trade in trades)
    await db.update_stats(
        market_day(),
        total_trades=len(trades),
        winning_trades=winners,
        losing_trades=losers,
        total_points=total_points,
        net_pnl=total_points,
    )


async def job_promo(app) -> None:
    prices = "\n".join(
        f"{'💎' if name == 'Diamond' else '🥇' if name == 'Gold' else '🥈' if name == 'Silver' else '🥉'} "
        f"<b>{name}</b> ~~₹{data['original_price']:,}~~ ➡️ <b>₹{data['price']:,}</b>"
        for name, data in PLANS.items()
    )
    await safe_send(app.bot, FREE_CHANNEL_ID, f"<b>🔥 SPECIAL OFFER 🔥</b>\n\n{prices}\n\n<i>{RISK_DISCLAIMER}</i>")


async def job_expiry(app) -> None:
    for subscription in await db.get_expired_subs():
        await db.deactivate_sub(subscription["id"])
        try:
            await app.bot.ban_chat_member(VIP_CHANNEL_ID, subscription["user_id"])
            await app.bot.unban_chat_member(VIP_CHANNEL_ID, subscription["user_id"])
        except TelegramError:
            logger.info("Could not remove expired user %s", subscription["user_id"])


def _signed_points(trade: dict, price: float, quantity: float = 1.0) -> float:
    entry = float(trade.get("entry_price") or 0)
    if trade.get("direction") == "BUY":
        return (price - entry) * quantity
    return (entry - price) * quantity


async def _manage_trade(trade: dict, candles: list, bot) -> None:
    if not candles:
        return
    current = candles[-1].close
    direction = trade.get("direction")
    entry = float(trade.get("entry_price") or 0)
    initial_risk = float(trade.get("risk_points") or abs(entry - float(trade.get("sl") or entry)))
    if entry <= 0 or initial_risk <= 0:
        return
    current_sl = float(trade.get("current_sl") or trade.get("sl") or entry)
    status = trade.get("status")

    if direction == "BUY":
        stop_hit = current <= current_sl
        reached_1r = current >= entry + initial_risk
        reached_2r = current >= float(trade.get("target2") or entry + initial_risk * 2)
        reached_3r = current >= float(trade.get("target3") or entry + initial_risk * 3)
    else:
        stop_hit = current >= current_sl
        reached_1r = current <= entry - initial_risk
        reached_2r = current <= float(trade.get("target2") or entry - initial_risk * 2)
        reached_3r = current <= float(trade.get("target3") or entry - initial_risk * 3)

    # A stop is checked first to avoid reporting a target on an adverse close.
    if status in {"ACTIVE", "BREAKEVEN"} and stop_hit:
        await post_upd(trade["id"], "SL_HIT", new_sl=current_sl, points=_signed_points(trade, current), bot=bot)
        return

    if status == "ACTIVE" and reached_1r:
        await post_upd(trade["id"], "BREAKEVEN", new_sl=entry, points=0.0, bot=bot)
        return

    if status == "BREAKEVEN":
        if reached_2r:
            partial = _signed_points(trade, float(trade.get("target2") or current), 0.5)
            await post_upd(trade["id"], "T2_HIT", new_sl=entry, points=partial, bot=bot)
            return
        if stop_hit:
            await post_upd(trade["id"], "SL_HIT", new_sl=current_sl, points=0.0, bot=bot)
            return

    if status == "PARTIAL_BOOKED":
        partial = _signed_points(trade, float(trade.get("target2") or entry), 0.5)
        if reached_3r:
            final = partial + _signed_points(trade, float(trade.get("target3") or current), 0.5)
            await post_upd(trade["id"], "T3_HIT", new_sl=float(trade.get("target3") or current), points=final, bot=bot)
            return
        if stop_hit:
            runner = _signed_points(trade, current, 0.5)
            await post_upd(trade["id"], "SL_HIT", new_sl=current_sl, points=partial + runner, bot=bot)
            return

        # ATR-like trailing approximation from recent candle range. The stop
        # can only tighten, never loosen.
        recent = candles[-14:]
        average_range = sum(c.high - c.low for c in recent) / len(recent) if recent else initial_risk
        trail_distance = max(average_range * 1.2, initial_risk * 0.25)
        candidate = current - trail_distance if direction == "BUY" else current + trail_distance
        tightened = max(current_sl, candidate) if direction == "BUY" else min(current_sl, candidate)
        if abs(tightened - current_sl) >= max(0.01, initial_risk * 0.05):
            await db.update_trade(trade["id"], current_sl=round(tightened, 2), last_event="TRAIL_UPDATED")
            await db.record_trade_event(trade["id"], "TRAIL_UPDATED", price=tightened)


async def job_monitor(app) -> None:
    if not is_market_session_open():
        return
    for trade in await db.get_active_trades():
        try:
            underlying = trade.get("underlying") or trade.get("symbol") or SIGNAL_SYMBOL
            candles = await fetch_candles(underlying, "5m", "5d", 30)
            await _manage_trade(trade, candles, app.bot)
        except Exception as exc:
            logger.exception("Trade monitor error for %s: %s", trade.get("id"), exc)


# ===================== TELEGRAM HANDLERS =====================
async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    ref_by = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            ref_by = int(context.args[0].split("_", 1)[1])
        except ValueError:
            ref_by = None
    await db.upsert_user(user.id, user.username, user.first_name, ref_by)
    await update.message.reply_text(
        f"<b>Hey {user.first_name}! 👋</b>\n\n"
        "Elite Sniper mode active. A+ setups only.\n\n"
        "<b>🚀 VIP:</b> /plans | <b>🔗 Earn:</b> /refer\n\n"
        f"<i>{RISK_DISCLAIMER}</i>",
        parse_mode="HTML",
    )


async def cmd_refer(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    bot_user = await update.bot.get_me()
    link = f"https://t.me/{bot_user.username}?start=ref_{uid}"
    await update.message.reply_text(
        f"<b>🔗 EARN VIA REFERRALS!</b>\n\n<code>{link}</code>\n\n<i>Share kar aur kamao!</i>",
        parse_mode="HTML",
    )


async def cmd_plans(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "<b>🔥 EXCLUSIVE VIP ACCESS 🔥</b>\n\n"
        "Quality alerts, transparent updates and disciplined risk management.\n\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
        "👑 <b>BRONZE (30D)</b> ~~₹5,000~~ ➡️ <b>₹2,999</b>\n"
        "🥈 <b>SILVER (90D)</b> ~~₹12,000~~ ➡️ <b>₹6,999</b>\n"
        "🥇 <b>GOLD (6M)</b> ~~₹20,000~~ ➡️ <b>₹9,999</b>\n"
        "💎 <b>DIAMOND (1Y)</b> ~~₹35,000~~ ➡️ <b>₹17,999</b>\n"
        "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<i>⚡ Maximum three quality trade decisions per day; no forced calls.</i>\n\n"
        f"<i>{RISK_DISCLAIMER}</i>"
    )
    buttons = [
        [InlineKeyboardButton(f"{('👑' if name == 'Bronze' else '🥈' if name == 'Silver' else '🥇' if name == 'Gold' else '💎')} {name} — ₹{data['price']:,}", callback_data=f"plan_{name}")]
        for name, data in PLANS.items()
    ]
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def plan_cb(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    plan_name = query.data.replace("plan_", "").capitalize()
    if plan_name not in PLANS:
        return
    buttons = [
        [InlineKeyboardButton("📱 PAY VIA WHATSAPP", url=PAYMENT_WHATSAPP_LINK)],
        [InlineKeyboardButton("💬 PAY VIA TELEGRAM", url=PAYMENT_TELEGRAM_LINK)],
        [InlineKeyboardButton("◀️ BACK", callback_data="back_plans")],
    ]
    await query.edit_message_text(
        fmt_plan(plan_name),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def back_cb(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    fake_update = type("FakeUpdate", (), {"message": query.message, "effective_user": query.from_user})()
    await cmd_plans(fake_update, context)


async def cmd_addvip(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_adm(update.effective_user.id):
        return
    if len(context.args) < 2:
        await update.message.reply_text("<code>/addvip ID PLAN</code>", parse_mode="HTML")
        return
    try:
        user_id, plan_name = int(context.args[0]), context.args[1].capitalize()
        if plan_name not in PLANS:
            return
        await db.add_subscription(user_id, plan_name)
        await update.message.reply_text("✅ <b>VIP Granted!</b>", parse_mode="HTML")
        try:
            await update.bot.send_message(
                chat_id=user_id,
                text=f"<b>🎉 VIP ACTIVATED!</b>\nPlan: <b>{plan_name}</b>\n\n<a href='https://t.me/+4oN8IsDUF1FhNjY1'>JOIN VIP</a>",
                parse_mode="HTML",
            )
        except TelegramError:
            pass
    except (TypeError, ValueError, KeyError) as exc:
        await update.message.reply_text(f"❌ {exc}")


async def cmd_force(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_adm(update.effective_user.id):
        return
    if len(context.args) < 7:
        await update.message.reply_text(
            "<code>/forcecall NIFTY BUY 22450 22410 22500 22600 22750 ORB VIP</code>",
            parse_mode="HTML",
        )
        return
    try:
        symbol = context.args[0].upper()
        direction = context.args[1].upper()
        entry, sl, t1, t2, t3 = (float(value) for value in context.args[2:7])
        strategy = context.args[7] if len(context.args) > 7 else "MANUAL"
        channel = context.args[8].upper() if len(context.args) > 8 else "VIP"
        trade_id = await post_call(
            symbol,
            direction,
            entry,
            sl,
            t1,
            t2,
            t3,
            strategy,
            channel,
            update.bot,
            source="MANUAL_OVERRIDE",
            slot=slot_for_time(),
            enforce_limit=True,
            logic="Admin manual override; still subject to the three-trade and slot policy.",
        )
        await update.message.reply_text(
            f"{'✅ Posted! ID: ' + str(trade_id) if trade_id else '⛔ Rejected by the three-trade/slot quality policy.'}"
        )
    except (TypeError, ValueError) as exc:
        await update.message.reply_text(f"❌ {exc}")


async def join_req(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    request = update.chat_join_request
    user = request.from_user
    if request.chat.id != VIP_CHANNEL_ID:
        return
    await db.upsert_user(user.id, user.username, user.first_name)
    subscription = await db.get_active_sub(user.id)
    if subscription:
        try:
            await context.bot.approve_chat_join_request(request.chat.id, user.id)
            await context.bot.send_message(chat_id=user.id, text="<b>🚀 Welcome VIP!</b>", parse_mode="HTML")
        except TelegramError:
            pass
    else:
        try:
            await context.bot.decline_chat_join_request(request.chat.id, user.id)
            await context.bot.send_message(chat_id=user.id, text="<b>❌ VIP Required</b>\n/plans", parse_mode="HTML")
        except TelegramError:
            pass


async def spam_guard(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or update.effective_chat.id != VIP_CHANNEL_ID:
        return
    user = update.effective_user
    if not user or user.id in ADMIN_IDS:
        return
    text = f"{update.message.text or ''} {update.message.caption or ''}"
    if re.search(r"https?://(?!t\\.me)|t\\.me/joinchat/|whatsapp|free.*signals", text, re.I):
        try:
            await update.message.delete()
            await db.ban_user(user.id, "Spam")
            await context.bot.ban_chat_member(VIP_CHANNEL_ID, user.id)
        except TelegramError:
            pass


async def contextual_reply(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Human-style contextual reply built from community + market memory.

    Works fully in zero-cost mode: the template layer is deterministic and AI,
    when enabled, only rephrases it under the fact guard. The reply never
    contains a BUY/SELL recommendation or any number that is not already a
    stored fact.
    """
    if not REPLY_ENABLED:
        return
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or user.is_bot:
        return
    text = (message.text or message.caption or "").strip()
    if not text or text.startswith("/"):
        return

    is_private = chat.type == "private"
    if not is_private:
        if not REPLY_IN_GROUPS:
            return
        bot_username = (context.bot.username or "").lower()
        mentioned = bool(bot_username) and f"@{bot_username}" in text.lower()
        replied_to_bot = bool(
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        if not (mentioned or replied_to_bot):
            return

    if not await replies.should_reply(user.id):
        return

    try:
        intent, reply_text = await replies.build_reply(
            user.id,
            text,
            username=user.username,
            first_name=user.first_name,
        )
    except Exception:
        logger.exception("Contextual reply generation failed")
        return

    try:
        await message.reply_text(reply_text, parse_mode="HTML", disable_web_page_preview=True)
        logger.info("Contextual reply sent to %s (intent=%s)", user.id, intent)
    except TelegramError as exc:
        logger.warning("Contextual reply delivery failed: %s", exc)


async def cmd_aistatus(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin view of the AI layer, memory sizes and zero-cost state."""
    if not is_adm(update.effective_user.id):
        return
    stats = ai_stats()
    crowd = await community.snapshot()
    state = await market_memory.today()
    lines = [
        "<b>🤖 AI + MEMORY STATUS</b>",
        "",
        f"AI enabled: <code>{stats.get('ai_enabled')}</code>",
        f"Configured provider: <code>{stats.get('configured_provider')}</code>",
        f"Active provider: <code>{stats.get('active_provider')}</code> "
        f"({'available' if stats.get('available') else 'unavailable'})",
        f"Model: <code>{stats.get('model') or 'n/a'}</code>",
        f"Zero-cost mode: <code>{stats.get('zero_cost_mode')}</code>",
        f"Calls today: <code>{stats.get('calls_today')}</code> / <code>{AI_DAILY_CALL_BUDGET}</code>",
        f"AI published: <code>{stats.get('published_ai')}</code> | "
        f"Fallback: <code>{stats.get('published_fallback')}</code> | "
        f"Guard rejects: <code>{stats.get('guard_rejected')}</code>",
        "",
        "<b>🧠 MEMORY</b>",
        f"Market day: <code>{state.get('market_day')}</code>",
        f"State: <code>{market_memory.describe(state)}</code>",
        f"Community members: <code>{crowd.get('members', 0)}</code> | "
        f"Voters today: <code>{crowd.get('voters_today', 0)}</code>",
        f"Crowd choice: <code>{crowd.get('top_option') or 'n/a'}</code>",
        "",
        "<i>AI never decides direction, entry, SL, targets, RRR, OI or P&amp;L.</i>",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ===================== WEBHOOK SERVER =====================
webhook_runner: web.AppRunner | None = None


def _authorized(request: web.Request) -> bool:
    if not WEBHOOK_SECRET:
        return True  # backward compatibility; configure secret in production
    supplied = request.headers.get("X-Webhook-Secret", "")
    return hmac.compare_digest(supplied, WEBHOOK_SECRET)


async def start_webhook(app) -> None:
    global webhook_runner
    web_app = web.Application()
    web_app["telegram_app"] = app

    async def tv_handler(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
            symbol = body.get("ticker", SIGNAL_SYMBOL).split(":")[-1].replace("FUT", "").strip().upper()
            if symbol not in {"NIFTY", "BANKNIFTY", "SENSEX"}:
                symbol = SIGNAL_SYMBOL
            requested = str(body.get("action", "")).lower()
            requested_direction = "BUY" if requested in {"buy", "long"} else "SELL" if requested in {"sell", "short"} else None
            if not requested_direction or not is_trade_window():
                return web.json_response({"status": "rejected_quality_or_window"})

            snapshot = await market_data.snapshot(symbol)
            decision = build_signal(snapshot, slot_for_time())
            signal = decision.signal
            if not signal or signal["direction"] != requested_direction:
                return web.json_response({"status": "rejected_multi_confirmation", "reasons": decision.reasons})
            trade_id = await post_call(
                signal["symbol"], signal["direction"], signal["entry"], signal["sl"],
                signal["t1"], signal["t2"], signal["t3"], signal["strategy"],
                await _select_channel(), app.bot, logic=signal["logic"], underlying=signal["underlying"],
                option_type=signal["option_type"], option_strike=signal["option_strike"], option_ltp=signal["option_ltp"],
                slot=slot_for_time(), source="TRADINGVIEW_CONFIRMED",
                entry_low=signal["entry_low"], entry_high=signal["entry_high"],
                risk_points=signal["risk_points"], rrr=signal["rrr"],
            )
            if not trade_id:
                return web.json_response({"status": "daily_limit_or_slot_used"})
            return web.json_response({"status": "posted", "id": trade_id})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            logger.exception("TradingView webhook failure")
            return web.json_response({"error": "internal webhook failure"}, status=500)

    async def pay_handler(request: web.Request) -> web.Response:
        if not _authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            body = await request.json()
            user_id = int(body.get("user_id", 0))
            plan = str(body.get("plan", "")).capitalize()
            if not user_id or plan not in PLANS:
                return web.json_response({"error": "invalid"}, status=400)
            await db.add_subscription(user_id, plan)
            try:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=f"<b>🎉 VIP ACTIVATED!</b>\nPlan: <b>{plan}</b>\n\n<a href='https://t.me/+4oN8IsDUF1FhNjY1'>JOIN VIP</a>",
                    parse_mode="HTML",
                )
            except TelegramError:
                pass
            return web.json_response({"status": "activated"})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception:
            logger.exception("Payment webhook failure")
            return web.json_response({"error": "internal webhook failure"}, status=500)

    async def health_handler(request: web.Request) -> web.Response:
        return web.json_response({"status": "healthy", "quality_mode": "multi_confirmation"})

    web_app.router.add_post("/webhook/tradingview", tv_handler)
    web_app.router.add_post("/webhook/payment", pay_handler)
    web_app.router.add_get("/health", health_handler)
    webhook_runner = web.AppRunner(web_app)
    await webhook_runner.setup()
    await web.TCPSite(webhook_runner, "0.0.0.0", WEBHOOK_PORT).start()
    logger.info("Webhook server listening on 0.0.0.0:%s", WEBHOOK_PORT)


async def stop_webhook() -> None:
    global webhook_runner
    if webhook_runner:
        await webhook_runner.cleanup()
        webhook_runner = None


def get_all_handlers():
    return [
        CommandHandler("start", cmd_start),
        CommandHandler("plans", cmd_plans),
        CommandHandler("refer", cmd_refer),
        CommandHandler("addvip", cmd_addvip),
        CommandHandler("forcecall", cmd_force),
        CommandHandler("aistatus", cmd_aistatus),
        CallbackQueryHandler(plan_cb, pattern=r"^plan_"),
        CallbackQueryHandler(back_cb, pattern=r"^back_plans$"),
        ChatJoinRequestHandler(join_req),
        PollAnswerHandler(on_poll_answer),
        MessageHandler(filters.TEXT | filters.CAPTION, spam_guard, block=False),
    ]


def get_handler_groups() -> list[tuple[Any, int]]:
    """Handlers with explicit PTB groups.

    Group 0 keeps the original behaviour (commands, callbacks, spam guard).
    Contextual replies live in group 1 so they can run *in addition to* the
    spam guard instead of competing with it inside the same group.
    """
    handlers: list[tuple[Any, int]] = [(handler, 0) for handler in get_all_handlers()]
    handlers.append(
        (MessageHandler(filters.TEXT & ~filters.COMMAND, contextual_reply, block=False), 1)
    )
    return handlers


def get_scheduler_jobs() -> dict[str, Any]:
    """Named scheduler registry avoids the original positional-index bug."""
    return {
        "morning": job_morning,
        "poll": job_hype_poll,
        "premarket": job_premarket,
        "open_pulse": job_open_pulse,
        "scanner": job_auto_scanner,
        "no_trade": job_no_trade_check,
        "btst": job_btst,
        "oi": job_oi,
        "closing": job_closing,
        "pnl": job_pnl,
        "promo": job_promo,
        "expiry": job_expiry,
        "monitor": job_monitor,
    }
