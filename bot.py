import os, logging, sys, asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder
from config import validate_config, BOT_TOKEN, OI_UPDATE_INTERVAL_MINUTES
from database import db
from engine import get_all_handlers, get_scheduler_jobs, start_webhook, scheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logging.basicConfig(format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S", level=logging.INFO)
logger = logging.getLogger("MAIN")

async def post_init(app):
    await db.connect()
    
    # --- STATE RECOVERY ---
    active = await db.get_active_trades()
    if active: logger.warning(f"RECOVERY: Found {len(active)} active trades from previous session.")
    
    # --- SCHEDULER SETUP ---
    jobs = get_scheduler_jobs()
    scheduler.add_job(jobs[0], CronTrigger(hour=8, minute=30), args=[app], id="morning", replace_existing=True)
    scheduler.add_job(jobs[1], IntervalTrigger(minutes=OI_UPDATE_INTERVAL_MINUTES), args=[app], id="oi", replace_existing=True)
    scheduler.add_job(jobs[2], CronTrigger(hour=11, minute=30), args=[app], id="promo1", replace_existing=True)
    scheduler.add_job(jobs[3], CronTrigger(hour=15, minute=45), args=[app], id="summary", replace_existing=True)
    scheduler.add_job(jobs[4], IntervalTrigger(minutes=5), args=[app], id="expiry", replace_existing=True)
    scheduler.add_job(jobs[5], IntervalTrigger(minutes=1), args=[app], id="monitor", replace_existing=True)
    
    # FIX: Night promo me 'app' pass nahi ho raha tha, isliye args=[app] add kiya
    scheduler.add_job(jobs[2], CronTrigger(hour=20, minute=30), args=[app], id="promo2", replace_existing=True) 
    
    scheduler.start()
    logger.info("✅ Bot is LIVE. All systems operational.")

async def post_shutdown(app):
    scheduler.shutdown(wait=False)
    await db.close()

def main():
    if not validate_config(): sys.exit(1)
    
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).concurrent_updates(True).build()
    
    for handler in get_all_handlers():
        app.add_handler(handler)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(start_webhook(app))
    
    logger.info("Starting bot engine...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
