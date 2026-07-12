import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FREE_CHANNEL_ID = int(os.getenv("FREE_CHANNEL_ID", "0"))
VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "0"))
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/bot.db")
WEBHOOK_PORT = int(os.getenv("PORT", "8080"))

PAYMENT_DETAILS_TEXT = os.getenv("PAYMENT_DETAILS_TEXT", "Payment ke liye UPI par amount transfer karo:\n\n🏦 UPI ID: yourupi@paytm\n📱 PhonePe/GPay: 9876543210\n📌 Note: Apna Telegram ID zaroor bhejo")
PAYMENT_TELEGRAM_LINK = os.getenv("PAYMENT_TELEGRAM_LINK", "https://t.me/YourID")
PAYMENT_WHATSAPP_LINK = os.getenv("PAYMENT_WHATSAPP_LINK", "https://wa.me/919876543210?text=Hi%20Bhai%20Payment%20Kar%20Diya")
REFERRAL_REWARD_DAYS = 7

MAX_DAILY_FREE_TRADES = 3
MAX_DAILY_VIP_TRADES = 1
MAX_DAILY_TRADES = MAX_DAILY_FREE_TRADES + MAX_DAILY_VIP_TRADES

ACTIVE_WINDOWS = [(9, 15, 11, 30), (13, 15, 15, 45)]
NO_TRADE_WINDOWS = [(11, 30, 13, 15)]
OI_UPDATE_INTERVAL_MINUTES = 20

PLANS = {
    "Bronze": {"duration_days": 30, "price": 2999, "original_price": 5000, "features": ["Daily Index Calls", "Live Alerts"]},
    "Silver": {"duration_days": 90, "price": 6999, "original_price": 12000, "features": ["All Bronze", "Sensex Calls", "OI Data"]},
    "Gold": {"duration_days": 180, "price": 9999, "original_price": 20000, "features": ["All Silver", "Strategy Guidance"]},
    "Diamond": {"duration_days": 365, "price": 17999, "original_price": 35000, "features": ["All Gold", "1-on-1 Mentorship"]}
}
RISK_DISCLAIMER = "⚠️ Risk Disclaimer: Stock trading involves market risks. We are analysis educators; manage capital responsibly."

HOLIDAYS = [
    "2024-01-26", "2024-03-25", "2024-03-29", "2024-04-11", "2024-04-17", 
    "2024-05-01", "2024-06-17", "2024-07-17", "2024-08-15", "2024-10-02",
    "2024-11-01", "2024-11-15", "2024-12-25", "2025-01-26", "2025-03-14",
    "2025-03-31", "2025-04-14", "2025-04-18", "2025-05-01", "2025-06-07"
]

def validate_config():
    if not BOT_TOKEN or FREE_CHANNEL_ID == 0 or VIP_CHANNEL_ID == 0 or not ADMIN_IDS:
        print("❌ CONFIG ERRORS: BOT_TOKEN, CHANNEL_IDS, ADMIN_IDS missing"); return False
    return True
