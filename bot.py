import os, logging, sys, asyncio
from handlers.subscription import register_subscription_handlers
from telegram import Update
from telegram.ext import ApplicationBuilder
from config import validate_config, BOT_TOKEN
from database import db
from handlers.admin import register_admin_handlers
from handlers.subscription import register_subscription_handlers
from handlers.join_request import register_join_request_handler
from handlers.spam_guard import register_spam_guard
from services.broadcaster import Broadcaster
from services.expiry_engine import ExpiryEngine
from services.webhook_server import start_webhook_server
from services.market_data import MarketDataService
from services.scheduler import setup_scheduler
from utils.risk_manager import RiskManager

logging.basicConfig(format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S", level=logging.INFO)
logger = logging.getLogger("MAIN")

async def post_init(app):
    await db.connect()
    app.bot_data["db"] = db
    app.bot_data["broadcaster"] = Broadcaster(app.bot)
    app.bot_data["risk_manager"] = RiskManager(db)
    app.bot_data["market_data"] = MarketDataService(db)
    app.bot_data["expiry_engine"] = ExpiryEngine(app.bot, db)
    setup_scheduler(app)
    logger.info("Bot is LIVE.")

async def post_shutdown(app):
    from services.scheduler import scheduler
    scheduler.shutdown(wait=False)
    await db.close()

def main():
    if not validate_config(): sys.exit(1)
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).concurrent_updates(True).build()
    register_admin_handlers(app)
        register_subscription_handlers(app)
    register_subscription_handlers(app)
    register_join_request_handler(app)
    register_spam_guard(app)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(start_webhook_server(app))
    
    logger.info(f"Starting... Port: {os.getenv('PORT', '8080')}")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
