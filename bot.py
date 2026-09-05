import asyncio
import logging
import sys

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram.ext import ApplicationBuilder

from config import BOT_TOKEN, OI_UPDATE_INTERVAL_MINUTES, validate_config
from database import db
from engine import get_all_handlers, get_scheduler_jobs, scheduler, start_webhook, stop_webhook

logging.basicConfig(
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger("MAIN")


async def post_init(app) -> None:
    await db.connect()
    active = await db.get_active_trades()
    if active:
        logger.warning("RECOVERY: Found %s non-terminal trade(s).", len(active))

    await start_webhook(app)
    jobs = get_scheduler_jobs()

    # All times are Asia/Kolkata through the scheduler timezone. Jobs are
    # named instead of indexed so a new job cannot silently miswire expiry or
    # summaries, which happened in the original version.
    scheduler.add_job(jobs["morning"], CronTrigger(hour=8, minute=0), args=[app], id="morning", replace_existing=True)
    scheduler.add_job(jobs["poll"], CronTrigger(hour=8, minute=30), args=[app], id="poll", replace_existing=True)
    scheduler.add_job(jobs["premarket"], CronTrigger(hour=9, minute=0), args=[app], id="premarket", replace_existing=True)
    scheduler.add_job(jobs["open_pulse"], CronTrigger(hour=9, minute=15), args=[app], id="open_pulse", replace_existing=True)
    scheduler.add_job(jobs["scanner"], IntervalTrigger(minutes=1), args=[app], id="scanner", replace_existing=True)
    scheduler.add_job(jobs["no_trade"], CronTrigger(hour=12, minute=0), args=[app], id="no_trade", replace_existing=True)
    scheduler.add_job(jobs["btst"], CronTrigger(hour=15, minute=10), args=[app], id="btst", replace_existing=True)
    scheduler.add_job(jobs["oi"], IntervalTrigger(minutes=OI_UPDATE_INTERVAL_MINUTES), args=[app], id="oi", replace_existing=True)
    scheduler.add_job(jobs["closing"], CronTrigger(hour=15, minute=15), args=[app], id="closing", replace_existing=True)
    scheduler.add_job(jobs["pnl"], CronTrigger(hour=15, minute=30), args=[app], id="pnl", replace_existing=True)
    scheduler.add_job(jobs["promo"], CronTrigger(hour=15, minute=45), args=[app], id="promo", replace_existing=True)
    scheduler.add_job(jobs["promo"], CronTrigger(hour=20, minute=30), args=[app], id="promo2", replace_existing=True)
    scheduler.add_job(jobs["expiry"], IntervalTrigger(minutes=60), args=[app], id="expiry", replace_existing=True)
    scheduler.add_job(jobs["monitor"], IntervalTrigger(minutes=1), args=[app], id="monitor", replace_existing=True)

    scheduler.start()
    logger.info("✅ ELITE SNIPER AUTOBOT IS LIVE (free-data, multi-confirmation mode).")


async def post_shutdown(app) -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
    await stop_webhook()
    await db.close()


def main() -> None:
    if not validate_config():
        sys.exit(1)
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .concurrent_updates(True)
        .build()
    )
    for handler in get_all_handlers():
        app.add_handler(handler)

    logger.info("Starting Elite Sniper Engine...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
