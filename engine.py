import os, logging, asyncio, random, re, time
from datetime import datetime
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ChatJoinRequest
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, ChatJoinRequestHandler, filters
from telegram.error import TelegramError
import yfinance as yf
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from config import *
from database import db

logger = logging.getLogger(__name__)

# Anti-spam cooldown for scanner (seconds)
LAST_AUTO_POST_TIME = 0 

# ================= 1. MARKET DATA & SCANNER =================
def get_morning_data():
    try:
        n = yf.Ticker("^NSEI").history(period="2d")
        if len(n) >= 1:
            c = round(n['Close'].iloc[-1], 1); p = round(n['Close'].iloc[-2], 1) if len(n)>=2 else c
            nc = round(((c-p)/p)*100, 2)
            d = yf.Ticker("^DJI").history(period="2d"); dc = round(d['Close'].iloc[-1], 1); dp = round(d['Close'].iloc[-2], 1); dc_ch = round(((dc-dp)/dp)*100, 2)
            nq = yf.Ticker("^IXIC").history(period="2d"); nqc = round(nq['Close'].iloc[-1], 1); nqp = round(nq['Close'].iloc[-2], 1); nq_ch = round(((nqc-nqp)/nqp)*100, 2)
            cr = yf.Ticker("CL=F").history(period="1d"); cr_c = round(cr['Close'].iloc[-1], 2) if len(cr)>0 else 78.5
            return {"gift_nifty": c, "gift_nifty_change": nc, "dow_jones": dc, "dow_change": dc_ch, "nasdaq": nqc, "nasdaq_change": nq_ch, "crude_oil": cr_c, "usd_inr": 83.12, "r1": round(c+60,1), "r2": round(c+120,1), "s1": round(c-60,1), "s2": round(c-120,1)}
    except Exception as e: logger.error(f"Morning data fail: {e}")
    return None

def get_real_candles(sym, count=20):
    tickers = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}
    try:
        data = yf.download(tickers.get(sym, "^NSEI"), period="5d", interval="5m", progress=False)
        if len(data) >= count: return data.tail(count)
    except: pass
    return None

def auto_scan_algo():
    data = get_real_candles("NIFTY", 20)
    if data is None or data.empty: return None
    try:
        closes = data['Close'].tolist()
        ema9 = closes[-2]; ema21 = closes[-3] 
        curr = closes[-1]; prev = closes[-2]
        
        if ema9 <= ema21 and curr > prev and (curr - ema21) > 10:
            sl = round(data['Low'].tail(10).min(), 2)
            risk = curr - sl
            if risk <= 0: return None
            return {"symbol": "NIFTY", "direction": "BUY", "entry": round(curr, 2), "sl": sl, "t1": round(curr+risk*1.5,2), "t2": round(curr+risk*3,2), "t3": round(curr+risk*5,2), "strategy": "AUTO_EMA"}
        
        if ema9 >= ema21 and curr < prev and (ema21 - curr) > 10:
            sl = round(data['High'].tail(10).max(), 2)
            risk = sl - curr
            if risk <= 0: return None
            return {"symbol": "NIFTY", "direction": "SELL", "entry": round(curr, 2), "sl": sl, "t1": round(curr-risk*1.5,2), "t2": round(curr-risk*3,2), "t3": round(curr-risk*5,2), "strategy": "AUTO_EMA"}
    except: pass
    return None

# ================= 2. FORMATTERS =================
def fmt_morning(d):
    g, ge, gr = d["gift_nifty"], d["gift_nifty_change"], "🟢" if d["gift_nifty_change"]>=0 else "🔴"
    return (f"<b>☀️ GOOD MORNING TRADERS!</b>\n\n"
            f"<b>📊 NIFTY:</b> <code>{g:,.1f}</code> ({gr} <i>{ge:+.2f}%</i>)\n"
            f"<b>🇺🇸 DOW:</b> <code>{d['dow_jones']:,.1f}</code> (<i>{d['dow_change']:+.2f}%</i>)\n"
            f"<b>💻 NASDAQ:</b> <code>{d['nasdaq']:,.1f}</code>\n"
            f"<b>🛢️ CRUDE:</b> <code>${d['crude_oil']:,.2f}</code>\n\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"<b>📐 NIFTY LEVELS</b>\n"
            f"🔺 R1: <code>{d['r1']:,.1f}</code> | R2: <code>{d['r2']:,.1f}</code>\n"
            f"🔻 S1: <code>{d['s1']:,.1f}</code> | S2: <code>{d['s2']:,.1f}</code>\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"📈 <b>Mood: {'🟢 BULLISH' if ge>=0 else '🔴 BEARISH'}</b>\n"
            f"⏰ 8:45 AM pe Poll aayega.\n\n<i>{RISK_DISCLAIMER}</i>")

def fmt_trade(sym, dir, e, sl, t1, t2, t3, strat, status="ACTIVE"):
    risk = abs(e - sl); rrr = abs(t2 - e) / risk if risk > 0 else 0
    d = "🟢 BUY (LONG)" if dir=="BUY" else "🔴 SELL (SHORT)"
    st = {"ACTIVE":"🟢 LIVE","T1_HIT":"✅ T1 DONE","T2_HIT":"🎯 T2 DONE","T3_HIT":"🏆 JACKPOT","SL_HIT":"❌ SL HIT"}.get(status, status)
    return (f"<b>🚀 JACKPOT CALL — {sym}</b>\n\n"
            f"<b>📍 TYPE:</b> {d}\n<b>🎯 ENTRY:</b> <code>{e:,.2f}</code>\n"
            f"<b>🛡️ SL:</b> <code>{sl:,.2f}</code>\n"
            f"<b>✅ T1:</b> <code>{t1:,.2f}</code> | <b>T2:</b> <code>{t2:,.2f}</code> | <b>T3:</b> <code>{t3:,.2f}</code>\n\n"
            f"<b>⚡ STRATEGY:</b> <i>{strat}</i> | <b>📊 R:R:</b> <code>1:{rrr:.1f}</code>\n"
            f"<b>📌 STATUS:</b> {st}\n\n<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
            f"✅ T1 pe half book, SL cost pe trail.\n\n<i>{RISK_DISCLAIMER}</i>")

def fmt_update(t, utype, nsl=None, pts=None):
    s, d, e = t["symbol"], t["direction"], t["entry_price"]
    p = f"<code>+{pts:,.1f}</code>" if pts and pts>=0 else f"<code>{pts:,.1f}</code>"
    if utype=="T1_HIT": return f"<b>✅ T1 HIT! — {s}</b>\n\n📍 {d} @ <code>{e:,.2f}</code>\n💰 Points: {p}\n\n🔄 <b>Half book karo</b>\n🛡️ <b>SL TRAILED TO COST:</b> <code>{nsl:,.2f}</code>\n\n<i>{RISK_DISCLAIMER}</i>"
    if utype=="T2_HIT": return f"<b>🎯 T2 HIT! — {s}</b>\n\n💰 Total Points: {p}\n\n<i>{RISK_DISCLAIMER}</i>"
    if utype=="T3_HIT": return f"<b>🏆 T3 HIT! JACKPOT! — {s}</b>\n\n💰 <b>TOTAL POINTS: {p}</b>\n\n🔥🔥🔥 <b>SAALI MARKET KA RAJA BAN GAYE!</b> 🔥🔥🔥\n\n<i>{RISK_DISCLAIMER}</i>"
    if utype=="SL_HIT": return f"<b>❌ SL HIT — {s}</b>\n\n💰 Points: {p}\n\n✅ <b>Discipline maintain karo!</b>\n\n<i>{RISK_DISCLAIMER}</i>"

def fmt_fomo(t, pts):
    return (f"<b>🔥 VIP JACKPOT RESULT 🔥</b>\n\n<b>📊 {t['symbol']}</b> | {t['strategy']}\n"
            f"📍 Entry: <b>[HIDDEN FOR VIPs 🔒]</b>\n🛡️ SL: <b>[HIDDEN FOR VIPs 🔒]</b>\n\n"
            f"<b>🏆 ACHIEVED! Points: +{pts:,.1f}</b>\n\n<i>Yeh call sirf VIP members ko milti hai.</i>\n\n<i>{RISK_DISCLAIMER}</i>")

def fmt_plan(pname):
    p = PLANS.get(pname); disc = int((1 - p["price"]/p["original_price"])*100)
    emoji = "👑" if pname=="Bronze" else "🥈" if pname=="Silver" else "🥇" if pname=="Gold" else "💎"
    return (f"<b>━━━━━━━━━━━━━━━━━━━━━</b>\n{emoji} <b>SELECTED: {pname.upper()}</b>\n<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"<b>Duration:</b> <i>{p['duration_days']} Days</b>\n<b>Price:</b> <code>₹{p['price']:,}</code> <i>({disc}% OFF)</i>\n\n"
            f"<b>━━━━━━━━━━━━━━━━━━━━━</b>\n{PAYMENT_DETAILS_TEXT}\n<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"<i>📌 Payment ke baad screenshot bhejna.</i>\n\n<i>{RISK_DISCLAIMER}</i>")

# ================= 3. BROADCASTER & RISK =================
async def safe_send(bot, cid, text, rm=None):
    for i in range(3):
        try: return (await bot.send_message(chat_id=cid, text=text, parse_mode="HTML", reply_markup=rm, disable_web_page_preview=True)).message_id
        except TelegramError as e:
            if "Flood" in str(e) and i<2: await asyncio.sleep(2**i) 
            else: return None

async def post_call(sym, dir, e, sl, t1, t2, t3, strat, ch, bot):
    cid = VIP_CHANNEL_ID if ch=="VIP" else FREE_CHANNEL_ID
    mid = await safe_send(bot, cid, fmt_trade(sym, dir, e, sl, t1, t2, t3, strat))
    return await db.create_trade(sym, dir, e, sl, t1, t2, t3, strat, ch, mid)

async def post_upd(tid, utype, nsl=None, pts=None, bot=None):
    t = await db.get_trade(tid)
    if not t: return
    await db.update_trade(tid, status=utype, current_sl=nsl if nsl else t["current_sl"], points_gained=pts if pts else 0)
    cid = VIP_CHANNEL_ID if t["channel_type"]=="VIP" else FREE_CHANNEL_ID
    await safe_send(bot, cid, fmt_update(t, utype, nsl, pts))
    if utype in ("T2_HIT","T3_HIT") and t["channel_type"]=="VIP":
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 JOIN VIP FOR JACKPOT CALLS", url="https://t.me/+4oN8IsDUF1FhNjY1")]])
        await safe_send(bot, FREE_CHANNEL_ID, fmt_fomo(t, pts), rm=btn)

def can_trade():
    now = datetime.now()
    if now.weekday() >= 5 or now.strftime("%Y-%m-%d") in HOLIDAYS: return False
    for sh,sm,eh,em in NO_TRADE_WINDOWS:
        if now.replace(hour=sh,minute=sm,second=0) <= now <= now.replace(hour=eh,minute=em,second=0): return False
    return any(now.replace(hour=sh,minute=sm,second=0) <= now <= now.replace(hour=eh,minute=em,second=0) for sh,sm,eh,em in ACTIVE_WINDOWS)

# ================= 4. HANDLERS =================
def is_adm(uid): return uid in ADMIN_IDS

async def cmd_start(u, c):
    ref_by = None
    if c.args and c.args[0].startswith("ref_"):
        try: ref_by = int(c.args[0].split("_")[1])
        except: pass
    await db.upsert_user(u.effective_user.id, u.effective_user.username, u.effective_user.first_name, ref_by)
    await u.message.reply_text(f"<b>Hey {u.effective_user.first_name}! 👋</b>\n\nStrictly Quality Calls. Auto Algo Active.\n\n<b>🚀 VIP:</b> /plans | <b>🔗 Earn:</b> /refer\n\n<i>{RISK_DISCLAIMER}</i>", parse_mode="HTML")

async def cmd_refer(u, c):
    uid = u.effective_user.id
    link = f"https://t.me/{(await u.bot.get_me()).username}?start=ref_{uid}"
    await u.message.reply_text(f"<b>🔗 EARN VIA REFERRALS!</b>\n\n<code>{link}</code>\n\n<i>Share kar aur kamao!</i>", parse_mode="HTML")

async def cmd_plans(u, c):
    text = ("<b>🔥 EXCLUSIVE VIP ACCESS 🔥</b>\n\nMarket me log panic me hain, tu jackpot pick karna chahta hai?\n\n"
            "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
            "👑 <b>BRONZE (30D)</b> ~~₹5,000~~ ➡️ <b>₹2,999</b>\n"
            "🥈 <b>SILVER (90D)</b> ~~₹12,000~~ ➡️ <b>₹6,999</b>\n"
            "🥇 <b>GOLD (6M)</b> ~~₹20,000~~ ➡️ <b>₹9,999</b>\n"
            "💎 <b>DIAMOND (1Y)</b> ~~₹35,000~~ ➡️ <b>₹17,999</b>\n"
            "<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            "<i>⚡ Strictly 1 Premium Call/Day. Quality > Quantity.</i>\n\n"
            f"<i>{RISK_DISCLAIMER}</i>")
    btns = [[InlineKeyboardButton(f"{'👑' if n=='Bronze' else '🥈' if n=='Silver' else '🥇' if n=='Gold' else '💎'} {n} — ₹{d['price']:,}", callback_data=f"plan_{n}")] for n,d in PLANS.items()]
    await u.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))

async def plan_cb(u, c):
    q = u.callback_query; await q.answer(); pn = q.data.replace("plan_","").capitalize()
    if pn not in PLANS: return
    btns = [[InlineKeyboardButton("📱 PAY VIA WHATSAPP", url=PAYMENT_WHATSAPP_LINK)], [InlineKeyboardButton("💬 PAY VIA TELEGRAM", url=PAYMENT_TELEGRAM_LINK)], [InlineKeyboardButton("◀️ BACK", callback_data="back_plans")]]
    await q.edit_message_text(fmt_plan(pn), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))

async def back_cb(u, c):
    q = u.callback_query; await q.answer()
    fake = type('o',(object,),{'message':q.message,'effective_user':q.from_user})()
    await cmd_plans(fake, c)

async def cmd_addvip(u, c):
    if not is_adm(u.effective_user.id): return
    if len(c.args)<2: return await u.message.reply_text("<code>/addvip ID PLAN</code>", parse_mode="HTML")
    try:
        uid, pn = int(c.args[0]), c.args[1].capitalize()
        if pn not in PLANS: return
        await db.add_subscription(uid, pn)
        await u.message.reply_text(f"✅ <b>VIP Granted!</b>", parse_mode="HTML")
        try: await u.bot.send_message(chat_id=uid, text=f"<b>🎉 VIP ACTIVATED!</b>\nPlan: <b>{pn}</b>\n\n<a href='https://t.me/+4oN8IsDUF1FhNjY1'>JOIN VIP</a>", parse_mode="HTML", disable_web_page_preview=True)
        except: pass
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def cmd_force(u, c):
    if not is_adm(u.effective_user.id): return
    if len(c.args)<7: return await u.message.reply_text("<code>/forcecall NIFTY BUY 22450 22410 22510 22570 22640 ORB VIP</code>", parse_mode="HTML")
    try:
        s,d = c.args[0].upper(), c.args[1].upper(); e,sl,t1,t2,t3 = float(c.args[2]),float(c.args[3]),float(c.args[4]),float(c.args[5]),float(c.args[6])
        st = c.args[7] if len(c.args)>7 else "MANUAL"; ch = c.args[8].upper() if len(c.args)>8 else "VIP"
        tid = await post_call(s,d,e,sl,t1,t2,t3,st,ch,u.bot)
        await u.message.reply_text(f"✅ Posted! ID: {tid}")
    except Exception as e: await u.message.reply_text(f"❌ {e}")

async def join_req(u, c):
    jr = u.chat_join_request; user = jr.from_user; cid = jr.chat.id
    if cid != VIP_CHANNEL_ID: return
    await db.upsert_user(user.id, user.username, user.first_name)
    sub = await db.get_active_sub(user.id)
    if sub:
        try: await c.bot.approve_chat_join_request(cid, user.id); await c.bot.send_message(chat_id=user.id, text=f"<b>🚀 Welcome VIP!</b>", parse_mode="HTML")
        except: pass
    else:
        try: await c.bot.decline_chat_join_request(cid, user.id); await c.bot.send_message(chat_id=user.id, text="<b>❌ VIP Required</b>\n/plans", parse_mode="HTML")
        except: pass

async def spam_guard(u, c):
    if not u.message or not u.effective_chat or u.effective_chat.id != VIP_CHANNEL_ID: return
    user = u.effective_user
    if not user or user.id in ADMIN_IDS: return
    txt = f"{u.message.text or ''} {u.message.caption or ''}"
    if re.search(r"https?://(?!t\.me)|t\.me/joinchat/|whatsapp|free.*signals", txt, re.I):
        try: await u.message.delete(); await db.ban_user(user.id, "Spam"); await c.bot.ban_chat_member(VIP_CHANNEL_ID, user.id)
        except: pass

# ================= 5. SCHEDULER JOBS (STRICT LIMITS) =================
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

async def job_morning(app):
    data = get_morning_data()
    if data: await safe_send(app.bot, FREE_CHANNEL_ID, fmt_morning(data))

async def job_hype_poll(app):
    hype = ("<b>⚡ MARKET KHULNE WALA HAI! SIR LAGAO DHAKKA! ⚡</b>\n\n"
            "Smart money position li hai. Algo scanning live hai.\n"
            "Agar 1:3+ ka setup milega toh immediate fire karenge!\n\n<b>👇 Neeche batayo, aaj kiski side me ho?</b>")
    await safe_send(app.bot, FREE_CHANNEL_ID, hype)
    try: await app.bot.send_poll(chat_id=FREE_CHANNEL_ID, question="🔥 AAJ KA MARKET MOOD?", options=["🟢 BULLISH", "🔴 BEARISH", "🟡 SIDEWAYS"], is_anonymous=False)
    except: pass

async def job_auto_scanner(app):
    global LAST_AUTO_POST_TIME
    if not can_trade(): return
    
    # ANTI-SPAM: Agar last 15 min me post hua hai toh skip
    if time.time() - LAST_AUTO_POST_TIME < 900: return 

    total = await db.get_today_count()
    if total >= MAX_DAILY_TRADES: return # Hard limit hit

    # STRICT ALLOCATION LOGIC
    free_count = await db.get_today_free_count()
    vip_count = await db.get_today_vip_count()

    ch = None
    if free_count < MAX_DAILY_FREE_TRADES:
        ch = "FREE"
    elif vip_count < MAX_DAILY_VIP_TRADES:
        ch = "VIP"
    else:
        return # Daily specific quota full. STOP SCANNING.

    signal = auto_scan_algo()
    if signal:
        await post_call(signal["symbol"], signal["direction"], signal["entry"], signal["sl"], 
                       signal["t1"], signal["t2"], signal["t3"], signal["strategy"], ch, app.bot)
        LAST_AUTO_POST_TIME = time.time() # Update cooldown
        logger.info(f"AUTO-TRADE: {signal['symbol']} -> {ch} (Free:{free_count}/{MAX_DAILY_FREE_TRADES}, VIP:{vip_count}/{MAX_DAILY_VIP_TRADES})")

async def job_btst(app):
    data = get_real_candles("NIFTY", 50)
    if data is None or data.empty: return
    try:
        c = round(data['Close'].iloc[-1], 2); l = round(data['Low'].min(), 2); sl = round(l - 20, 2)
        await post_call("NIFTY", "BUY", c, sl, round(c+50,2), round(c+100,2), round(c+150,2), "BTST_EOD", "VIP", app.bot)
    except: pass

async def job_oi(app):
    if not can_trade(): return
    c, p = random.randint(8e6, 15e6), random.randint(7e6, 14e6); pcr = round(p/c, 3)
    sent = "🟢 BULLISH" if pcr>1.2 else "🔴 BEARISH" if pcr<0.8 else "🟡 NEUTRAL"
    txt = f"<b>📊 LIVE OI DATA</b>\n\n<b>PCR:</b> <code>{pcr:.3f}</code>\n<b>📈 Sentiment:</b> {sent}\n\n<i>{RISK_DISCLAIMER}</i>"
    await safe_send(app.bot, FREE_CHANNEL_ID, txt); await safe_send(app.bot, VIP_CHANNEL_ID, txt)

async def job_promo(app):
    pt = "\n".join([f"{'💎' if n=='Diamond' else '🥇' if n=='Gold' else '🥈' if n=='Silver' else '🥉'} <b>{n}</b> ~~₹{d['original_price']:,}~~ ➡️ <b>₹{d['price']:,}</b>" for n,d in PLANS.items()])
    await safe_send(app.bot, FREE_CHANNEL_ID, f"<b>🔥 SPECIAL OFFER 🔥</b>\n\n{pt}\n\n<i>{RISK_DISCLAIMER}</i>")

async def job_summary(app):
    trades = await db.get_today_trades()
    w = sum(1 for t in trades if t["status"] in ("T1_HIT","T2_HIT","T3_HIT")); l = sum(1 for t in trades if t["status"]=="SL_HIT")
    pts = sum(t.get("points_gained",0) for t in trades); wr = (w/(w+l)*100) if (w+l)>0 else 0
    txt = f"<b>📋 TODAY'S REPORT</b>\n\n<b>Trades:</b> <code>{len(trades)}</code> | <b>Win Rate:</b> <code>{wr:.0f}%</code> | <b>Points:</b> <code>{pts:+,.1f}</code>\n\n💪 <b>Consistency is key!</b>\n\n<i>{RISK_DISCLAIMER}</i>"
    await safe_send(app.bot, FREE_CHANNEL_ID, txt)
    if trades: await safe_send(app.bot, VIP_CHANNEL_ID, txt)
    await db.update_stats(datetime.now().strftime("%Y-%m-%d"), total_trades=len(trades), winning_trades=w, losing_trades=l, total_points=pts)

async def job_expiry(app):
    for sub in await db.get_expired_subs():
        await db.deactivate_sub(sub["id"])
        try: await app.bot.ban_chat_member(VIP_CHANNEL_ID, sub["user_id"]); await app.bot.unban_chat_member(VIP_CHANNEL_ID, sub["user_id"])
        except: pass

async def job_monitor(app):
    if not can_trade(): return
    for t in await db.get_active_trades():
        data = get_real_candles(t["symbol"], 5)
        if data is None or data.empty: continue
        try:
            cp = round(data['Close'].iloc[-1], 2); sl = t["current_sl"] or t["sl"]
            if t["direction"]=="BUY":
                if cp <= sl: await post_upd(t["id"], "SL_HIT", pts=sl-t["entry_price"], bot=app.bot)
                elif cp >= t["target1"] and t["status"]=="ACTIVE": await post_upd(t["id"], "T1_HIT", nsl=t["entry_price"], pts=t["target1"]-t["entry_price"], bot=app.bot)
                elif cp >= t["target2"] and t["status"]=="T1_HIT": await post_upd(t["id"], "T2_HIT", nsl=t["target1"], pts=t["target2"]-t["entry_price"], bot=app.bot)
                elif cp >= t["target3"]: await post_upd(t["id"], "T3_HIT", pts=t["target3"]-t["entry_price"], bot=app.bot)
            else:
                if cp >= sl: await post_upd(t["id"], "SL_HIT", pts=t["entry_price"]-sl, bot=app.bot)
                elif cp <= t["target1"] and t["status"]=="ACTIVE": await post_upd(t["id"], "T1_HIT", nsl=t["entry_price"], pts=t["entry_price"]-t["target1"], bot=app.bot)
                elif cp <= t["target2"] and t["status"]=="T1_HIT": await post_upd(t["id"], "T2_HIT", nsl=t["target1"], pts=t["entry_price"]-t["target2"], bot=app.bot)
                elif cp <= t["target3"]: await post_upd(t["id"], "T3_HIT", pts=t["entry_price"]-t["target3"], bot=app.bot)
        except Exception as e: logger.error(f"Monitor err: {e}")

# ================= 6. WEBHOOK SERVER =================
async def start_webhook(app):
    wa = web.Application(); wa["telegram_app"] = app
    async def tv_handler(request):
        try:
            body = await request.json()
            if not can_trade(): return web.json_response({"status": "risk_blocked"})
            sym = body.get("ticker","NIFTY").split(":")[-1].replace("FUT","").strip().upper()
            d = "BUY" if body.get("action","").lower() in ("buy","long") else "SELL" if body.get("action","").lower() in ("sell","short") else None
            if not d or float(body.get("price",0))<=0: return web.json_response({"status": "invalid"})
            
            # Apply strict limits to webhooks too
            total = await db.get_today_count()
            if total >= MAX_DAILY_TRADES: return web.json_response({"status": "daily_limit_reached"})
            
            free_count = await db.get_today_free_count()
            vip_count = await db.get_today_vip_count()
            ch = "FREE" if free_count < MAX_DAILY_FREE_TRADES else "VIP" if vip_count < MAX_DAILY_VIP_TRADES else None
            if not ch: return web.json_response({"status": "quota_full"})
            
            p = float(body.get("price",0)); rp = p*0.003
            sl = round(p-rp,2) if d=="BUY" else round(p+rp,2)
            t1 = round(p+rp,2) if d=="BUY" else round(p-rp,2)
            t2 = round(p+rp*2,2) if d=="BUY" else round(p-rp*2,2)
            t3 = round(p+rp*3,2) if d=="BUY" else round(p-rp*3,2)
            tid = await post_call(sym, d, p, sl, t1, t2, t3, "WEBHOOK", ch, app.bot)
            return web.json_response({"status": "posted", "id": tid, "channel": ch})
        except Exception as e: return web.json_response({"error": str(e)}, status=500)

    async def pay_handler(request):
        try:
            body = await request.json(); uid = int(body.get("user_id", 0)); plan = body.get("plan", "").capitalize()
            if not uid or plan not in PLANS: return web.json_response({"error": "Invalid"})
            await db.add_subscription(uid, plan)
            try: await app.bot.send_message(chat_id=uid, text=f"<b>🎉 VIP ACTIVATED!</b>\nPlan: <b>{plan}</b>\n\n<a href='https://t.me/+4oN8IsDUF1FhNjY1'>JOIN VIP</a>", parse_mode="HTML", disable_web_page_preview=True)
            except: pass
            return web.json_response({"status": "activated"})
        except Exception as e: return web.json_response({"error": str(e)}, status=500)

    wa.router.add_post("/webhook/tradingview", tv_handler)
    wa.router.add_post("/webhook/payment", pay_handler)
    wa.router.add_get("/health", lambda r: web.json_response({"status": "healthy"}))
    runner = web.AppRunner(wa); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT).start()

def get_all_handlers():
    return [
        CommandHandler("start", cmd_start), CommandHandler("plans", cmd_plans), CommandHandler("refer", cmd_refer),
        CommandHandler("addvip", cmd_addvip), CommandHandler("forcecall", cmd_force),
        CallbackQueryHandler(plan_cb, pattern=r"^plan_"), CallbackQueryHandler(back_cb, pattern=r"^back_plans$"),
        ChatJoinRequestHandler(join_req),
        MessageHandler(filters.TEXT | filters.CAPTION, spam_guard, block=False)
    ]

def get_scheduler_jobs(): return [job_morning, job_hype_poll, job_auto_scanner, job_btst, job_oi, job_promo, job_summary, job_expiry, job_monitor]
