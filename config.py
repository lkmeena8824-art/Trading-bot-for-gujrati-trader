import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FREE_CHANNEL_ID = int(os.getenv("FREE_CHANNEL_ID", "0"))
VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "0"))
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# ── Database ──
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/bot.db")

# ── Webhook Server ──
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("PORT", os.getenv("WEBHOOK_PORT", "8080")))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change_me")

# ── Custom Payment Settings (Tumhara Jugaad) ──
PAYMENT_DETAILS_TEXT = os.getenv("PAYMENT_DETAILS_TEXT", "Payment ke liye neeche given details par amount transfer karo:\n\n🏦 UPI ID: yourUPI@paytm\n📱 PhonePe/Google Pay: 9876543210\n📋 Note: Apna Telegram ID zaroor likhna")
PAYMENT_TELEGRAM_LINK = os.getenv("PAYMENT_TELEGRAM_LINK", "https://t.me/YourPaymentID")
PAYMENT_WHATSAPP_LINK = os.getenv("PAYMENT_WHATSAPP_LINK", "https://wa.me/919876543210?text=Hi%20Bhai%2C%20Payment%20kar%20diya")

# ── Data Provider ──
DATA_MODE = os.getenv("DATA_MODE", "mock")

# ── Trading Rules ──
MAX_DAILY_TRADES = 4
ACTIVE_WINDOWS = [(9, 15, 11, 0), (13, 15, 15, 30)]
NO_TRADE_WINDOWS = [(11, 0, 13, 15)]
MIN_RRR = 2.0

# ── Algo Strategy Parameters ──
RSI_PERIOD = 14
RSI_OVERBOUGHT = 60
RSI_OVERSOLD = 40
EMA_FAST = 9
EMA_SLOW = 21
ADX_PERIOD = 14
ADX_THRESHOLD = 25
BB_PERIOD = 20
BB_STD_DEV = 2
VOLUME_SMA_PERIOD = 20

# ── Subscription Plans ──
PLANS = {
    "Trial": {
        "duration_days": 3,
        "price": 99,
        "original_price": 499,
        "features": ["3 Days Full VIP Access", "All Live Algo Calls", "Basic Support"]
    },
    "Bronze": {
        "duration_days": 30,
        "price": 2999,
        "original_price": 5000,
        "features": ["Daily Index Calls (Nifty + Bank Nifty)", "Live Alert Notifications", "Basic Support"]
    },
    "Silver": {
        "duration_days": 90,
        "price": 6999,
        "original_price": 12000,
        "features": ["All Bronze Features", "Sensex Hero-Zero Contracts", "OI Data Access", "Priority Support"]
    },
    "Gold": {
        "duration_days": 180,
        "price": 9999,
        "original_price": 20000,
        "features": ["All Silver Features", "Complete VIP Call Access", "Strategy Guidance Sessions", "Dedicated Support"]
    },
    "Diamond": {
        "duration_days": 365,
        "price": 17999,
        "original_price": 35000,
        "features": ["All Gold Features", "1-on-1 Mentorship", "Live Handholding During Market", "Personalized Risk Management"]
    }
}

# ── Scheduled Post Times ──
OI_UPDATE_INTERVAL_MINUTES = 20

# ── Risk Disclaimer ──
RISK_DISCLAIMER = "⚠️ Risk Disclaimer: Stock trading involves market risks. We are analysis educators; manage capital responsibly."

def validate_config():
    errors = []
    if not BOT_TOKEN: errors.append("BOT_TOKEN missing")
    if FREE_CHANNEL_ID == 0: errors.append("FREE_CHANNEL_ID missing")
    if VIP_CHANNEL_ID == 0: errors.append("VIP_CHANNEL_ID missing")
    if not ADMIN_IDS: errors.append("ADMIN_IDS missing")
    if errors:
        print("❌ CONFIG ERRORS:"); 
        for e in errors: print(f"   → {e}")
        return False
    return True
