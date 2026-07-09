import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from config import FREE_CHANNEL_ID, VIP_CHANNEL_ID, RISK_DISCLAIMER
from database import db
from utils.formatters import *

logger = logging.getLogger(__name__)

async def _safe_send(bot: Bot, chat_id: int, text: str, parse_mode: str = "HTML", reply_markup=None) -> int | None:
    for attempt in range(3):
        try:
            msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup, disable_web_page_preview=True)
            return msg.message_id
        except TelegramError as e:
            if "Flood" in str(e) and attempt < 2:
                import asyncio; await asyncio.sleep(2**attempt)
            else: return None

class Broadcaster:
    def __init__(self, bot: Bot): self.bot = bot

    async def post_trade_call(self, symbol: str, direction: str, entry_price: float, sl: float, target1: float, target2: float, target3: float, strategy: str, channel_type: str, **kwargs) -> int:
        text = format_trade_call(symbol, direction, entry_price, sl, target1, target2, target3, strategy, channel_type=channel_type)
        chat_id = VIP_CHANNEL_ID if channel_type == "VIP" else FREE_CHANNEL_ID
        msg_id = await _safe_send(self.bot, chat_id, text)
        tid = await db.create_trade(symbol, direction, entry_price, sl, target1, target2, target3, strategy, channel_type, msg_id)
        return tid

    async def post_trade_update(self, trade_id: int, update_type: str, new_sl: float = None, points: float = None):
        trade = await db.get_trade(trade_id)
        if not trade: return
        await db.update_trade(trade_id, status=update_type, current_sl=new_sl if new_sl else trade["current_sl"], points_gained=points if points else 0)
        text = format_trade_update(trade, update_type, new_sl, points)
        chat_id = VIP_CHANNEL_ID if trade["channel_type"]=="VIP" else FREE_CHANNEL_ID
        await _safe_send(self.bot, chat_id, text)
        if update_type in ("T2_HIT", "T3_HIT") and trade["channel_type"]=="VIP":
            fomo_text = format_fomo_post(trade, update_type, points)
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 JOIN VIP FOR JACKPOT CALLS", url="https://t.me/+4oN8IsDUF1FhNjY1")]])
            await _safe_send(self.bot, FREE_CHANNEL_ID, fomo_text, reply_markup=btn)

    async def post_morning_setup(self, data: dict):
        await _safe_send(self.bot, FREE_CHANNEL_ID, format_morning_setup(data))

    async def post_oi_update(self, data: dict):
        text = format_oi_data(data)
        await _safe_send(self.bot, FREE_CHANNEL_ID, text)
        await _safe_send(self.bot, VIP_CHANNEL_ID, text)

    async def post_marketing(self, post_type: str):
        await _safe_send(self.bot, FREE_CHANNEL_ID, format_marketing_post(post_type))

    async def post_evening_summary(self, trades: list[dict]):
        text = format_evening_summary(trades)
        await _safe_send(self.bot, FREE_CHANNEL_ID, text)
        if trades: await _safe_send(self.bot, VIP_CHANNEL_ID, text)
