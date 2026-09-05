import os
from datetime import time
from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FREE_CHANNEL_ID = _int_env("FREE_CHANNEL_ID", 0)
VIP_CHANNEL_ID = _int_env("VIP_CHANNEL_ID", 0)
ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
]
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/bot.db")
WEBHOOK_PORT = _int_env("PORT", 8080)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# The bot remains zero-cost by default. AI is strictly opt-in because hosted
# LLM APIs are not free. Deterministic templates are the default fallback.
AI_ENABLED = _bool_env("AI_ENABLED", False)
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
AI_MIN_INTERVAL_SECONDS = _int_env("AI_MIN_INTERVAL_SECONDS", 15)

PAYMENT_DETAILS_TEXT = os.getenv(
    "PAYMENT_DETAILS_TEXT",
    "Payment ke liye UPI par amount transfer karo:\n\n"
    "🏦 UPI ID: yourupi@paytm\n"
    "📱 PhonePe/GPay: 9876543210\n"
    "📌 Note: Apna Telegram ID zaroor bhejo",
)
PAYMENT_TELEGRAM_LINK = os.getenv("PAYMENT_TELEGRAM_LINK", "https://t.me/YourID")
PAYMENT_WHATSAPP_LINK = os.getenv(
    "PAYMENT_WHATSAPP_LINK",
    "https://wa.me/919876543210?text=Hi%20Bhai%20Payment%20Kar%20Diya",
)
REFERRAL_REWARD_DAYS = 7

# One global automated trade cap. The old Free/VIP constants remain available
# for compatibility, but they must never increase the global cap.
MAX_DAILY_TRADES = _int_env("MAX_DAILY_TRADES", 3)
MAX_DAILY_FREE_TRADES = MAX_DAILY_TRADES
MAX_DAILY_VIP_TRADES = MAX_DAILY_TRADES

# A slot is an opportunity, not a forced trade. The signal engine must still
# pass every confirmation gate before a call is admitted.
TRADE_WINDOWS = {
    "FIRST": (time(9, 15), time(10, 15)),
    "SECOND": (time(10, 45), time(11, 15)),
    "THIRD": (time(12, 45), time(13, 15)),
}
ACTIVE_WINDOWS = [
    (start.hour, start.minute, end.hour, end.minute)
    for start, end in TRADE_WINDOWS.values()
]
NO_TRADE_WINDOWS = [(11, 15, 12, 45)]
NO_TRADE_CHECK_TIME = time(12, 0)
MARKET_OPEN_TIME = time(9, 15)
MARKET_CLOSE_TIME = time(15, 30)

# Free/public data is deliberately treated as best-effort. Stale or missing
# data causes a safe rejection rather than a guessed call.
MARKET_TIMEZONE = os.getenv("MARKET_TIMEZONE", "Asia/Kolkata")
DATA_PROVIDER = os.getenv("DATA_PROVIDER", "free").lower()
DATA_TIMEOUT_SECONDS = _int_env("DATA_TIMEOUT_SECONDS", 12)
DATA_MAX_AGE_SECONDS = _int_env("DATA_MAX_AGE_SECONDS", 180)
OPTION_CHAIN_MIN_INTERVAL_SECONDS = _int_env("OPTION_CHAIN_MIN_INTERVAL_SECONDS", 60)
CANDLE_MIN_INTERVAL_SECONDS = _int_env("CANDLE_MIN_INTERVAL_SECONDS", 45)
OI_UPDATE_INTERVAL_MINUTES = _int_env("OI_UPDATE_INTERVAL_MINUTES", 20)
SIGNAL_SYMBOL = os.getenv("SIGNAL_SYMBOL", "NIFTY").upper()
TRADE_ASSET_MODE = os.getenv("TRADE_ASSET_MODE", "UNDERLYING").upper()

# Signal gates. These values are intentionally configurable so they can be
# evaluated with historical replay instead of being silently hard-coded.
MIN_RRR = _float_env("MIN_RRR", 3.0)
BREAKOUT_VOLUME_MULTIPLIER = _float_env("BREAKOUT_VOLUME_MULTIPLIER", 1.25)
OI_DECAY_EDGE = _float_env("OI_DECAY_EDGE", 1.05)
NO_TRADE_RANGE_PCT = _float_env("NO_TRADE_RANGE_PCT", 0.004)
NO_TRADE_TREND_PCT = _float_env("NO_TRADE_TREND_PCT", 0.0015)
TRAIL_ATR_MULTIPLIER = _float_env("TRAIL_ATR_MULTIPLIER", 1.2)

# Current project uses a free/public option-chain endpoint and yfinance as a
# fallback for candles. A licensed feed can be added later without changing
# the signal engine through the provider interface.
NSE_OPTION_CHAIN_URL = os.getenv(
    "NSE_OPTION_CHAIN_URL",
    "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}",
)

PLANS = {
    "Bronze": {
        "duration_days": 30,
        "price": 2999,
        "original_price": 5000,
        "features": ["Daily Index Calls", "Live Alerts"],
    },
    "Silver": {
        "duration_days": 90,
        "price": 6999,
        "original_price": 12000,
        "features": ["All Bronze", "Sensex Calls", "OI Data"],
    },
    "Gold": {
        "duration_days": 180,
        "price": 9999,
        "original_price": 20000,
        "features": ["All Silver", "Strategy Guidance"],
    },
    "Diamond": {
        "duration_days": 365,
        "price": 17999,
        "original_price": 35000,
        "features": ["All Gold", "1-on-1 Mentorship"],
    },
}

RISK_DISCLAIMER = (
    "⚠️ Risk Disclaimer: Stock trading involves market risks. "
    "We are analysis educators; manage capital responsibly."
)

# The list is intentionally configurable. For accurate exchange holidays, add
# dates through MARKET_HOLIDAYS in the deployment environment or update this
# list when the official calendar is published.
HOLIDAYS = {
    "2024-01-26", "2024-03-25", "2024-03-29", "2024-04-11", "2024-04-17",
    "2024-05-01", "2024-06-17", "2024-07-17", "2024-08-15", "2024-10-02",
    "2024-11-01", "2024-11-15", "2024-12-25", "2025-01-26", "2025-03-14",
    "2025-03-31", "2025-04-14", "2025-04-18", "2025-05-01", "2025-06-07",
}
HOLIDAYS.update(x.strip() for x in os.getenv("MARKET_HOLIDAYS", "").split(",") if x.strip())

# Existing automation remains available, but BTST is off by default because it
# would violate the three-slot quality policy when run after the final slot.
ENABLE_BTST = _bool_env("ENABLE_BTST", False)
AUTO_TRADE_CHANNEL = os.getenv("AUTO_TRADE_CHANNEL", "FREE").upper()


def validate_config() -> bool:
    errors = []
    if not BOT_TOKEN:
        errors.append("BOT_TOKEN")
    if FREE_CHANNEL_ID == 0:
        errors.append("FREE_CHANNEL_ID")
    if VIP_CHANNEL_ID == 0:
        errors.append("VIP_CHANNEL_ID")
    if not ADMIN_IDS:
        errors.append("ADMIN_IDS")
    if MAX_DAILY_TRADES < 1 or MAX_DAILY_TRADES > 3:
        errors.append("MAX_DAILY_TRADES must be between 1 and 3")

    if errors:
        print("❌ CONFIG ERRORS: " + ", ".join(errors))
        return False
    return True
