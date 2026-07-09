import logging
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
from config import ADMIN_IDS, PLANS, VIP_CHANNEL_ID
from utils.formatters import format_stats_message
logger = logging.getLogger(__name__)

def is_admin(uid): return uid in ADMIN_IDS

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db = context.bot_data.get("db")
    if db: await db.upsert_user(u.id, u.username, u.first_name, u.last_name)
    await update.message.reply_text(
        "<b>Hey {}! 👋</b>\n\nMain tera Trading Assistant hu. Nifty, Bank Nifty aur Sensex pe high-accuracy algo calls deta hu.\n\n<b>📋 Kya milta hai:</b>\n• Free Channel pe daily 2 premium calls\n• OI Data aur Market Sentiment\n• Morning/Evening analysis\n\n<b>🚀 VIP Access:</b> /plans\n<b>📊 Free Channel:</b> <a href='https://t.me/+JeNuvtWpz64wOWY1'>JOIN KARO</a>\n\n<i>⚠️ Risk Disclaimer: Stock trading involves market risks.</i>".format(u.first_name), 
        parse_mode="HTML", disable_web_page_preview=True
    )

async def cmd_addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 2:
        await update.message.reply_text("<b>Usage:</b> <code>/addvip TELEGRAM_ID PLAN_NAME</code>", parse_mode="HTML"); return
    try:
        uid, pn = int(context.args[0]), context.args[1].capitalize()
        if pn not in PLANS: await update.message.reply_text(f"Invalid plan. Use: {', '.join(PLANS.keys())}"); return
        db = context.bot_data["db"]; await db.add_subscription(uid, pn)
        await update.message.reply_text(f"✅ <b>VIP Granted!</b>\nUser: <code>{uid}</code>\nPlan: {pn}", parse_mode="HTML")
        try:
            await context.bot.send_message(chat_id=uid, text="<b>🎉 VIP ACTIVATED!</b>\n\nPlan: <b>{}</b>\n\nAb VIP Channel join karo — auto-approve hoga:\n<a href='https://t.me/+4oN8IsDUF1FhNjY1'>JOIN VIP</a>".format(pn), parse_mode="HTML", disable_web_page_preview=True)
        except: pass
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    db = context.bot_data["db"]; stats = await db.get_stats(days=7)
    await update.message.reply_text(format_stats_message(stats), parse_mode="HTML")

async def cmd_forcecall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if len(context.args) < 7:
        await update.message.reply_text("<code>/forcecall NIFTY BUY 22450 22410 22510 22570 22640 ORB VIP</code>", parse_mode="HTML"); return
    try:
        sym, dir = context.args[0].upper(), context.args[1].upper()
        e, sl, t1, t2, t3 = float(context.args[2]), float(context.args[3]), float(context.args[4]), float(context.args[5]), float(context.args[6])
        strat = context.args[7] if len(context.args)>7 else "MANUAL"
        ch = context.args[8].upper() if len(context.args)>8 else "VIP"
        b = context.bot_data["broadcaster"]; tid = await b.post_trade_call(sym, dir, e, sl, t1, t2, t3, strat, ch)
        await update.message.reply_text(f"✅ Call Posted! ID: {tid} | {ch}")
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

async def cmd_getchatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = update.effective_chat
    await update.message.reply_text(f"<code>{c.id}</code>\nType: {c.type}", parse_mode="HTML")

def register_admin_handlers(app):
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("addvip", cmd_addvip))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("forcecall", cmd_forcecall))
    app.add_handler(CommandHandler("getchatid", cmd_getchatid))
