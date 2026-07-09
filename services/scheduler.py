import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from config import OI_UPDATE_INTERVAL_MINUTES
from database import db

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

async def job_morning(app):
    try:
        data = await app.bot_data["market_data"].get_morning_summary()
        await app.bot_data["broadcaster"].post_morning_setup(data)
    except Exception as e: logger.error(f"Morning err: {e}")

async def job_oi(app):
    now = datetime.now()
    if not ((now.hour==9 and now.minute>=15) or (10<=now.hour<=14) or (now.hour==15 and now.minute<=30)): return
    try:
        data = await app.bot_data["market_data"].get_oi_data()
        await app.bot_data["broadcaster"].post_oi_update(data)
    except Exception as e: logger.error(f"OI err: {e}")

async def job_promo(app, ptype):
    try: await app.bot_data["broadcaster"].post_marketing(ptype)
    except Exception as e: logger.error(f"Promo err: {e}")

async def job_summary(app):
    try:
        trades = await db.get_today_trades()
        await app.bot_data["broadcaster"].post_evening_summary(trades)
        w = sum(1 for t in trades if t["status"] in ("T1_HIT","T2_HIT","T3_HIT"))
        l = sum(1 for t in trades if t["status"]=="SL_HIT")
        await db.update_daily_stats(datetime.now().strftime("%Y-%m-%d"), total_trades=len(trades), winning_trades=w, losing_trades=l, total_points=sum(t.get("points_gained",0) for t in trades))
    except Exception as e: logger.error(f"Summary err: {e}")

async def job_expiry(app):
    try: await app.bot_data["expiry_engine"].check_and_expire()
    except Exception as e: logger.error(f"Expiry err: {e}")

async def job_monitor(app):
    now = datetime.now()
    if not ((now.hour==9 and now.minute>=15) or (10<=now.hour<=14) or (now.hour==15 and now.minute<=30)): return
    try:
        active = await db.get_active_trades()
        if not active: return
        md = app.bot_data["market_data"]
        br = app.bot_data["broadcaster"]
        for t in active:
            c = await md.get_mock_candles(t["symbol"], 5)
            if not c: continue
            cp = c[-1]["close"]
            sl = t["current_sl"] or t["sl"]
            if t["direction"]=="BUY":
                if cp <= sl: await br.post_trade_update(t["id"], "SL_HIT", points=sl-t["entry_price"])
                elif cp >= t["target1"] and t["status"]=="ACTIVE": await br.post_trade_update(t["id"], "T1_HIT", new_sl=t["entry_price"], points=t["target1"]-t["entry_price"])
                elif cp >= t["target2"] and t["status"]=="T1_HIT": await br.post_trade_update(t["id"], "T2_HIT", new_sl=t["target1"], points=t["target2"]-t["entry_price"])
                elif cp >= t["target3"]: await br.post_trade_update(t["id"], "T3_HIT", points=t["target3"]-t["entry_price"])
            else:
                if cp >= sl: await br.post_trade_update(t["id"], "SL_HIT", points=t["entry_price"]-sl)
                elif cp <= t["target1"] and t["status"]=="ACTIVE": await br.post_trade_update(t["id"], "T1_HIT", new_sl=t["entry_price"], points=t["entry_price"]-t["target1"])
                elif cp <= t["target2"] and t["status"]=="T1_HIT": await br.post_trade_update(t["id"], "T2_HIT", new_sl=t["target1"], points=t["entry_price"]-t["target2"])
                elif cp <= t["target3"]: await br.post_trade_update(t["id"], "T3_HIT", points=t["entry_price"]-t["target3"])
    except Exception as e: logger.error(f"Monitor err: {e}")

def setup_scheduler(app):
    scheduler.add_job(job_morning, CronTrigger(hour=8, minute=30), args=[app], id="morning", replace_existing=True)
    scheduler.add_job(job_oi, IntervalTrigger(minutes=OI_UPDATE_INTERVAL_MINUTES), args=[app], id="oi", replace_existing=True)
    scheduler.add_job(job_promo, CronTrigger(hour=11, minute=30), args=[app, "mid_day"], id="promo1", replace_existing=True)
    scheduler.add_job(job_summary, CronTrigger(hour=15, minute=45), args=[app], id="summary", replace_existing=True)
    scheduler.add_job(job_promo, CronTrigger(hour=20, minute=30), args=[app, "night"], id="promo2", replace_existing=True)
    scheduler.add_job(job_monitor, IntervalTrigger(minutes=1), args=[app], id="monitor", replace_existing=True)
    scheduler.add_job(job_expiry, IntervalTrigger(minutes=5), args=[app], id="expiry", replace_existing=True)
    scheduler.start()
    logger.info("Scheduler started")
