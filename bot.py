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
    active = await db.get_active_trades()
    if active: logger.warning(f"RECOVERY: Found {len(active)} active trades.")
    
    jobs = get_scheduler_jobs()
    
    # EXACT TIMES AS PER YOUR REQUEST
    scheduler.add_job(jobs[0], CronTrigger(hour=8, minute=0), args=[app], id="morning", replace_existing=True)    # 8:00 AM
    scheduler.add_job(jobs[1], CronTrigger(hour=8, minute=45), args=[app], id="poll", replace_existing=True)      # 8:45 AM
    scheduler.add_job(jobs[2], IntervalTrigger(minutes=5), args=[app], id="scanner", replace_existing=True)      # 9:15-11:30 & 1:15-3:45 (Scanner checks can_trade internally)
    scheduler.add_job(jobs[3], CronTrigger(hour=15, minute=10), args=[app], id="btst", replace_existing=True)       # 3:10 PM
    scheduler.add_job(jobs[4], IntervalTrigger(minutes=OI_UPDATE_INTERVAL_MINUTES), args=[app], id="oi", replace_existing=True)
    scheduler.add_job(jobs[5], CronTrigger(hour=15, minute=45), args=[app], id="summary", replace_existing=True)   # 3:45 PM Results + Promo
    scheduler.add_job(jobs[5], CronTrigger(hour=20, minute=30), args=[app], id="promo2", replace_existing=True)   # 8:30 PM Night Promo
    scheduler.add_job(jobs[6], IntervalTrigger(minutes=5), args=[app], id="expiry", replace_existing=True)
    scheduler.add_job(jobs[8], IntervalTrigger(minutes=1), args=[app], id="monitor", replace_existing=True)
    
    scheduler.start()
    logger.info("✅ FULL AUTOBOT IS LIVE.")

async def post_shutdown(app):
    scheduler.shutdown(wait=False)
    await db.close()

def main():
    if not validate_config(): sys.exit(1)
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).concurrent_updates(True).build()
    for handler in get_all_handlers(): app.add_handler(handler)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(start_webhook(app))
    
    logger.info("Starting Full Autobot Engine...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
