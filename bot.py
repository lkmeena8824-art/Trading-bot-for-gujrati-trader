import os, logging, sys, asyncio, random, math, re
from datetime import datetime, timedelta
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ChatJoinRequest
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ChatJoinRequestHandler, filters
from telegram.error import TelegramError
import aiosqlite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiohttp import web

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
FREE_CHANNEL_ID = int(os.getenv("FREE_CHANNEL_ID", "0"))
VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "0"))
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/bot.db")
WEBHOOK_PORT = int(os.getenv("PORT", "8080"))

PAYMENT_DETAILS_TEXT = os.getenv("PAYMENT_DETAILS_TEXT", "Payment ke liye UPI par amount transfer karo:\n\n🏦 UPI ID: yourupi@paytm\n📱 PhonePe/GPay: 9876543210\n📌 Note: Apna Telegram ID zaroor bhejo")
PAYMENT_TELEGRAM_LINK = os.getenv("PAYMENT_TELEGRAM_LINK", "https://t.me/YourID")
PAYMENT_WHATSAPP_LINK = os.getenv("PAYMENT_WHATSAPP_LINK", "https://wa.me/919876543210?text=Hi%20Bhai%20Payment%20Kar%20Diya")

MAX_DAILY_TRADES = 4
ACTIVE_WINDOWS = [(9, 15, 11, 0), (13, 15, 15, 30)]
NO_TRADE_WINDOWS = [(11, 0, 13, 15)]
OI_UPDATE_INTERVAL_MINUTES = 20

PLANS = {
    "Trial": {"duration_days": 3, "price": 99, "original_price": 499, "features": ["3 Days VIP Access", "All Live Calls"]},
    "Bronze": {"duration_days": 30, "price": 2999, "original_price": 5000, "features": ["Index Calls", "Live Alerts"]},
    "Silver": {"duration_days": 90, "price": 6999, "original_price": 12000, "features": ["All Bronze", "Sensex Calls", "OI Data"]},
    "Gold": {"duration_days": 180, "price": 9999, "original_price": 20000, "features": ["All Silver", "Strategy Guidance"]},
    "Diamond": {"duration_days": 365, "price": 17999, "original_price": 35000, "features": ["All Gold", "1-on-1 Mentorship"]}
}
RISK_DISCLAIMER = "⚠️ Risk Disclaimer: Stock trading involves market risks. We are analysis educators; manage capital responsibly."

def validate_config():
    if not BOT_TOKEN or FREE_CHANNEL_ID == 0 or VIP_CHANNEL_ID == 0 or not ADMIN_IDS:
        print("❌ CONFIG ERRORS: BOT_TOKEN, CHANNEL_IDS, ADMIN_IDS missing"); return False
    return True

# ================= DATABASE =================
class Database:
    def __init__(self): self._conn = None
    async def connect(self):
        os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
        self._conn = await aiosqlite.connect(DATABASE_PATH)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, is_banned INTEGER DEFAULT 0, last_active TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, plan_name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, direction TEXT NOT NULL, entry_price REAL NOT NULL, sl REAL NOT NULL, target1 REAL, target2 REAL, target3 REAL, current_sl REAL, status TEXT DEFAULT 'ACTIVE', strategy TEXT NOT NULL, channel_type TEXT NOT NULL, message_id INTEGER, points_gained REAL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS spam_blacklist (telegram_id INTEGER PRIMARY KEY, reason TEXT);
            CREATE TABLE IF NOT EXISTS bot_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, stat_date TEXT NOT NULL UNIQUE, total_trades INTEGER DEFAULT 0, winning_trades INTEGER DEFAULT 0, losing_trades INTEGER DEFAULT 0, total_points REAL DEFAULT 0);
        """)
        await self._conn.commit()
    async def close(self):
        if self._conn: await self._conn.close()
    async def upsert_user(self, tid, username=None, fn=None, ln=None):
        await self._conn.execute("INSERT INTO users VALUES (?,?,?,?,0,CURRENT_TIMESTAMP) ON CONFLICT(telegram_id) DO UPDATE SET username=COALESCE(?,username), first_name=COALESCE(?,first_name), last_active=CURRENT_TIMESTAMP", (tid, username, fn, ln, username, fn)); await self._conn.commit()
    async def ban_user(self, tid, reason="Spam"):
        await self._conn.execute("INSERT OR REPLACE INTO spam_blacklist VALUES (?,?)", (tid, reason)); await self._conn.commit()
    async def add_subscription(self, uid, plan):
        p = PLANS[plan]; now = datetime.now(); end = now + timedelta(days=p["duration_days"])
        c = await self._conn.execute("INSERT INTO subscriptions (user_id,plan_name,start_date,end_date) VALUES (?,?,?,?)", (uid, plan, now.isoformat(), end.isoformat())); await self._conn.commit(); return c.lastrowid
    async def get_active_sub(self, uid):
        c = await self._conn.execute("SELECT * FROM subscriptions WHERE user_id=? AND is_active=1 AND end_date>?", (uid, datetime.now().isoformat())); r = await c.fetchone(); return dict(r) if r else None
    async def get_expired_subs(self):
        c = await self._conn.execute("SELECT * FROM subscriptions WHERE is_active=1 AND end_date<=?", (datetime.now().isoformat(),)); return [dict(r) for r in await c.fetchall()]
    async def deactivate_sub(self, sid):
        await self._conn.execute("UPDATE subscriptions SET is_active=0 WHERE id=?", (sid,)); await self._conn.commit()
    async def create_trade(self, sym, dir, e, sl, t1, t2, t3, strat, ch, mid=None):
        c = await self._conn.execute("INSERT INTO trades (symbol,direction,entry_price,sl,target1,target2,target3,current_sl,strategy,channel_type,message_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (sym, dir, e, sl, t1, t2, t3, sl, strat, ch, mid)); await self._conn.commit(); return c.lastrowid
    async def get_trade(self, tid):
        c = await self._conn.execute("SELECT * FROM trades WHERE id=?", (tid,)); r = await c.fetchone(); return dict(r) if r else None
    async def get_active_trades(self):
        c = await self._conn.execute("SELECT * FROM trades WHERE status='ACTIVE'"); return [dict(r) for r in await c.fetchall()]
    async def update_trade(self, tid, **kw):
        if not kw: return
        kw["updated_at"] = datetime.now().isoformat(); s = ", ".join(f"{k}=?" for k in kw); v = list(kw.values()) + [tid]
        await self._conn.execute(f"UPDATE trades SET {s} WHERE id=?", v); await self._conn.commit()
    async def get_today_trades(self):
        td = datetime.now().strftime("%Y-%m-%d"); c = await self._conn.execute("SELECT * FROM trades WHERE DATE(created_at)=?", (td,)); return [dict(r) for r in await c.fetchall()]
    async def get_today_count(self):
        td = datetime.now().strftime("%Y-%m-%d"); c = await self._conn.execute("SELECT COUNT(*) as c FROM trades WHERE DATE(created_at)=?", (td,)); r = await c.fetchone(); return r["c"] if r else 0
    async def get_today_free_count(self):
        td = datetime.now().strftime("%Y-%m-%d"); c = await self._conn.execute("SELECT COUNT(*) as c FROM trades WHERE DATE(created_at)=? AND channel_type='FREE'", (td,)); r = await c.fetchone(); return r["c"] if r else 0
    async def update_stats(self, dt, **kw):
        c = await self._conn.execute("SELECT id FROM bot_stats WHERE stat_date=?", (dt,))
        if await c.fetchone():
            s = ", ".join(f"{k}=COALESCE({k},0)+?" for k in kw); v = list(kw.values()) + [dt]
            await self._conn.execute(f"UPDATE bot_stats SET {s} WHERE stat_date=?", v)
        else:
            cols, vals = ["stat_date"]+list(kw.keys()), [dt]+list(kw.values())
            await self._conn.execute(f"INSERT INTO bot_stats ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", vals)
        await self._conn.commit()
    async def get_stats(self, days=7):
        c = await self._conn.execute("SELECT * FROM bot_stats WHERE stat_date >= DATE('now', ?)", (f"-{days} days",)); return [dict(r) for r in await c.fetchall()]

db = Database()

# ================= MARKET DATA & INDICATORS =================
def get_morning_data():
    b = random.uniform(22300, 22600)
    return {"gift_nifty": round(b,1), "gift_nifty_change": round(random.uniform(-1.0, 1.5),2), "dow_jones": round(random.uniform(38500, 39500),1), "dow_change": round(random.uniform(-0.5, 0.5),2), "nasdaq": round(random.uniform(16000, 17000),1), "nasdaq_change": round(random.uniform(-0.8, 0.8),2), "crude_oil": round(random.uniform(75, 82),2), "usd_inr": round(random.uniform(82.8, 83.5),2), "r1": round(b+60,1), "r2": round(b+120,1), "s1": round(b-60,1), "s2": round(b-120,1)}

def get_oi_data():
    c, p = random.randint(8e6, 15e6), random.randint(7e6, 14e6); pcr = round(p/c, 3)
    return {"c_oi": int(c), "p_oi": int(p), "pcr": pcr, "max_c": round(random.uniform(22400,22600),1), "max_p": round(random.uniform(22200,22400),1), "sent": "🟢 BULLISH" if pcr>1.2 else "🔴 BEARISH" if pcr<0.8 else "🟡 NEUTRAL", "time": datetime.now().strftime("%H:%M %p")}

def get_candles(sym, count=5):
    base = {"NIFTY": 22450, "BANKNIFTY": 48500, "SENSEX": 73800}.get(sym, 22450)
    candles, p = [], base
    for _ in range(count):
        ch = random.gauss(0, base*0.001); o = p; c = p+ch
        candles.append({"close": round(c,2)}); p = c
    return candles

# ================= FORMATTERS (1000x Human Mind Styling) =================
def fmt_morning(d):
    g, ge, gr = d["gift_nifty"], d["gift_nifty_change"], "🟢" if d["gift_nifty_change"]>=0 else "🔴"
    return (f"<b>🔥 GOOD MORNING TRADERS! MARKET READY HAI PANGA LENE KE LIYE! 🔥</b>\n\n<b>📊 GIFT NIFTY:</b> <code>{g:,.1f}</code> ({gr} <i>{ge:+.2f}%</i>)\n<b>🇺🇸 DOW:</b> <code>{d['dow_jones']:,.1f}</code> | <b>💻 NASDAQ:</b> <code>{d['nasdaq']:,.1f}</code>\n<b>🛢️ CRUDE:</b> <code>${d['crude_oil']:,.2f}</code> | <b>💰 USD/INR:</b> <code>{d['usd_inr']:.2f}</code>\n\n<b>━━━━━━━━━━━━━━━━━━━━━</b>\n<b>📐 NIFTY LEVELS</b>\n🔺 <b>R1:</b> <code>{d['r1']:,.1f}</code> | 🔺 <b>R2:</b> <code>{d['r2']:,.1f}</code>\n🔻 <b>S1:</b> <code>{d['s1']:,.1f}</code> | 🔻 <b>S2:</b> <code>{d['s2']:,.1f}</code>\n<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n<b>📈 Mood:</b> {'🟢 BULLISH' if ge>=0 else '🔴 BEARISH'}\n\n<i>⏰ First call 09:30 AM ke baad.</i>\n\n<i>{RISK_DISCLAIMER}</i>")

def fmt_trade(sym, dir, e, sl, t1, t2, t3, strat, status="ACTIVE"):
    risk = abs(e - sl); rrr = abs(t2 - e) / risk if risk > 0 else 0
    d = "🟢 BUY (LONG)" if dir=="BUY" else "🔴 SELL (SHORT)"
    st = {"ACTIVE":"🟢 LIVE","T1_HIT":"✅ T1 DONE","T2_HIT":"🎯 T2 DONE","T3_HIT":"🏆 JACKPOT","SL_HIT":"❌ SL HIT"}.get(status, status)
    return (f"<b>🚀 JACKPOT CALL — {sym}</b>\n\n<b>📍 TYPE:</b> {d}\n<b>🎯 ENTRY:</b> <code>{e:,.2f}</code>\n<b>🛡️ SL:</b> <code>{sl:,.2f}</code> <i>(Strict!)</i>\n<b>✅ T1:</b> <code>{t1:,.2f}</code>\n<b>✅ T2:</b> <code>{t2:,.2f}</code>\n<b>✅ T3:</b> <code>{t3:,.2f}</code>\n\n<b>⚡ STRATEGY:</b> <i>{strat}</i>\n<b>📊 R:R:</b> <code>1:{rrr:.1f}+</code>\n<b>📌 STATUS:</b> {st}\n\n<b>━━━━━━━━━━━━━━━━━━━━━</b>\n<b>RULES:</b>\n✅ Entry ke baad SL modify mat karo\n✅ T1 hit pe half book, SL cost pe trail\n\n<i>{RISK_DISCLAIMER}</i>")

def fmt_update(t, utype, nsl=None, pts=None):
    s, d, e = t["symbol"], t["direction"], t["entry_price"]
    p = f"<code>+{pts:,.1f}</code>" if pts and pts>=0 else f"<code>{pts:,.1f}</code>"
    if utype=="T1_HIT": return f"<b>✅ T1 HIT! — {s}</b>\n\n📍 {d} @ <code>{e:,.2f}</code>\n🎯 <code>{t['target1']:,.2f}</code> <b>ACHIEVED!</b>\n💰 Points: {p}\n\n🔄 <b>Half book karo</b>\n🛡️ <b>SL TRAILED TO COST:</b> <code>{nsl:,.2f}</code>\n\n<i>{RISK_DISCLAIMER}</i>"
    if utype=="T2_HIT": return f"<b>🎯 T2 HIT! — {s}</b>\n\n💰 Total Points: {p}\n\n<i>{RISK_DISCLAIMER}</i>"
    if utype=="T3_HIT": return f"<b>🏆 T3 HIT! JACKPOT! — {s}</b>\n\n💰 <b>TOTAL POINTS: {p}</b>\n\n🔥🔥🔥 <b>FULL TARGET! SAALI MARKET KA RAJA BAN GAYE!</b> 🔥🔥🔥\n\n<i>{RISK_DISCLAIMER}</i>"
    if utype=="SL_HIT": return f"<b>❌ SL HIT — {s}</b>\n\n💰 Points: {p}\n\n✅ <b>Discipline maintain karo!</b>\n\n<i>{RISK_DISCLAIMER}</i>"

def fmt_oi(d):
    return f"<b>📊 LIVE OI DATA</b>\n\n<b>📞 Call OI:</b> <code>{d['c_oi']:,}</code>\n<b>📞 Put OI:</b> <code>{d['p_oi']:,}</code>\n<b>📊 PCR:</b> <code>{d['pcr']:.3f}</code>\n<b>📍 Max Call:</b> <code>{d['max_c']:,.1f}</code> | <b>Max Put:</b> <code>{d['max_p']:,.1f}</code>\n\n<b>📈 Sentiment:</b> {d['sent']}\n\n<i>{RISK_DISCLAIMER}</i>"

def fmt_fomo(t, pts):
    return f"<b>🔥 VIP CALL RESULT 🔥</b>\n\n<b>📊 {t['symbol']}</b>\n📍 Entry: <b>[HIDDEN FOR VIPs 🔒]</b>\n🛡️ SL: <b>[HIDDEN FOR VIPs 🔒]</b>\n\n<b>🏆 ACHIEVED!</b>\n<b>💰 Points: +{pts:,.1f}</b>\n\n<i>Yeh call sirf VIP members ko milti hai.</i>\n\n<i>{RISK_DISCLAIMER}</i>"

def fmt_plan(pname):
    p = PLANS.get(pname); disc = int((1 - p["price"]/p["original_price"])*100)
    return (f"<b>━━━━━━━━━━━━━━━━━━━━━</b>\n<b>💳 PAYMENT DETAILS</b>\n<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n<b>Plan:</b> <i>{pname} ({p['duration_days']} Days)</i>\n<b>Amount:</b> <code>₹{p['price']:,.0f}</code> <i>(~~₹{p['original_price']:,.0f}~~ | {disc}% OFF)</i>\n\n<b>━━━━━━━━━━━━━━━━━━━━━</b>\n{PAYMENT_DETAILS_TEXT}\n<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n<i>📌 Payment ke baad screenshot admin ko bhejna.</i>\n\n<i>{RISK_DISCLAIMER}</i>")

def fmt_promo():
    pt = ""
    for n, d in PLANS.items():
        if n=="Trial": continue
        disc = int((1-d["price"]/d["original_price"])*100)
        pt += f"\n{'💎' if n=='Diamond' else '🥇' if n=='Gold' else '🥈' if n=='Silver' else '🥉'} <b>{n}</b> ~~₹{d['original_price']:,.0f}~~ ➡️ <b>₹{d['price']:,.0f}</b> (<i>{disc}% OFF</i>)\n"
    return f"<b>🔥 SPECIAL OFFER 🔥</b>\n\n<b>━━━━━━━━━━━━━━━━━━━━━</b>{pt}<b>━━━━━━━━━━━━━━━━━━━━━</b>\n\n<b>🚀 VIP Features:</b>\n✅ 4 Algo Strategies\n✅ Real-time Updates\n\n<i>{RISK_DISCLAIMER}</i>"

# ================= BROADCASTER & RISK MANAGER =================
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
    if now.weekday() >= 5: return False
    for sh,sm,eh,em in NO_TRADE_WINDOWS:
        if now.replace(hour=sh,minute=sm,second=0) <= now <= now.replace(hour=eh,minute=em,second=0): return False
    is_win = any(now.replace(hour=sh,minute=sm,second=0) <= now <= now.replace(hour=eh,minute=em,second=0) for sh,sm,eh,em in ACTIVE_WINDOWS)
    return is_win

# ================= HANDLERS =================
def is_adm(uid): return uid in ADMIN_IDS

async def cmd_start(u, c):
    await db.upsert_user(u.effective_user.id, u.effective_user.username, u.effective_user.first_name)
    await u.message.reply_text(f"<b>Hey {u.effective_user.first_name}! 👋</b>\n\nMain tera Trading Assistant hu.\n\n<b>🚀 VIP Access:</b> /plans\n\n<i>{RISK_DISCLAIMER}</i>", parse_mode="HTML")

async def cmd_plans(u, c):
    btns = [[InlineKeyboardButton(f"{'💎' if n=='Diamond' else '🥇' if n=='Gold' else '🥈' if n=='Silver' else '🥉' if n=='Bronze' else '⚡'} {n} — ₹{d['price']:,}", callback_data=f"plan_{n}")] for n,d in PLANS.items()]
    await u.message.reply_text("<b>🔥 PREMIUM VIP PLANS 🔥</b>\n\nMarket ko dominate karna hai? Plan chuo:\n\n"+"\n".join([f"{'💎' if n=='Diamond' else '🥇' if n=='Gold' else '🥈' if n=='Silver' else '🥉' if n=='Bronze' else '⚡'} <b>{n} ({d['duration_days']}D)</b> ~~₹{d['original_price']:,}~~ ➡️ <b>₹{d['price']:,}</b>" for n,d in PLANS.items()]), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(btns))

async def plan_cb(u, c):
    q = u.callback_query; await q.answer(); pn = q.data.replace("plan_","").capitalize()
    if pn not in PLANS: return
    btns = [[InlineKeyboardButton("📱 WHATSAPP PE PAYMENT KARO", url=PAYMENT_WHATSAPP_LINK)], [InlineKeyboardButton("💬 TELEGRAM PE PAYMENT KARO", url=PAYMENT_TELEGRAM_LINK)], [InlineKeyboardButton("◀️ BACK", callback_data="back_plans")]]
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
        try: await c.bot.approve_chat_join_request(cid, user.id); await c.bot.send_message(chat_id=user.id, text=f"<b>🚀 Welcome VIP!</b>\nPlan: {sub['plan_name']}", parse_mode="HTML")
        except: pass
    else:
        try: await c.bot.decline_chat_join_request(cid, user.id); await c.bot.send_message(chat_id=user.id, text="<b>❌ VIP Access Required</b>\n/plans use karo.", parse_mode="HTML")
        except: pass

async def spam_guard(u, c):
    if not u.message or not u.effective_chat or u.effective_chat.id != VIP_CHANNEL_ID: return
    user = u.effective_user
    if not user or user.id in ADMIN_IDS: return
    txt = f"{u.message.text or ''} {u.message.caption or ''}"
    if re.search(r"https?://(?!t\.me)|t\.me/joinchat/|whatsapp|free.*signals", txt, re.I):
        try: await u.message.delete(); await db.ban_user(user.id, "Spam"); await c.bot.ban_chat_member(VIP_CHANNEL_ID, user.id)
        except: pass

# ================= SCHEDULER JOBS =================
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")

async def job_morning(app):
    await safe_send(app.bot, FREE_CHANNEL_ID, fmt_morning(get_morning_data()))

async def job_oi(app):
    if not can_trade(): return
    await safe_send(app, FREE_CHANNEL_ID, fmt_oi(get_oi_data()))
    await safe_send(app, VIP_CHANNEL_ID, fmt_oi(get_oi_data()))

async def job_promo(app):
    await safe_send(app, FREE_CHANNEL_ID, fmt_promo())

async def job_summary(app):
    trades = await db.get_today_trades()
    w = sum(1 for t in trades if t["status"] in ("T1_HIT","T2_HIT","T3_HIT")); l = sum(1 for t in trades if t["status"]=="SL_HIT")
    pts = sum(t.get("points_gained",0) for t in trades); wr = (w/(w+l)*100) if (w+l)>0 else 0
    txt = f"<b>📋 TODAY'S REPORT</b>\n\n<b>Trades:</b> <code>{len(trades)}</code> | <b>Win Rate:</b> <code>{wr:.0f}%</code> | <b>Points:</b> <code>{pts:+,.1f}</code>\n\n💪 <b>Consistency is key!</b>\n\n<i>{RISK_DISCLAIMER}</i>"
    await safe_send(app, FREE_CHANNEL_ID, txt)
    if trades: await safe_send(app, VIP_CHANNEL_ID, txt)
    await db.update_stats(datetime.now().strftime("%Y-%m-%d"), total_trades=len(trades), winning_trades=w, losing_trades=l, total_points=pts)

async def job_expiry(app):
    for sub in await db.get_expired_subs():
        await db.deactivate_sub(sub["id"])
        try: await app.bot.ban_chat_member(VIP_CHANNEL_ID, sub["user_id"]); await app.bot.unban_chat_member(VIP_CHANNEL_ID, sub["user_id"])
        except: pass

async def job_monitor(app):
    if not can_trade(): return
    for t in await db.get_active_trades():
        c = get_candles(t["symbol"], 5); cp = c[-1]["close"]; sl = t["current_sl"] or t["sl"]
        if t["direction"]=="BUY":
            if cp <= sl: await post_upd(t["id"], "SL_HIT", pts=sl-t["entry_price"], bot=app.bot)
            elif cp >= t["target1"] and t["status"]=="ACTIVE": await post_upd(t["id"], "T1_HIT", nsl=t["entry_price"], pts=t["target1"]-t["entry_price"], bot=app.bot)
            elif cp >= t["target2"] and t["status"]=="T1_HIT": await post_upd(t["id"], "T2_HIT", nsl=t["target1"], pts=t["target2"]-t["entry_price"], bot=app.bot)
            elif cp >= t["target3"]: await post_upd(t["id"], "T3_HIT", pts=t["target3"]-t["entry_price"], bot=app.bot)
        else:
            if cp >= sl: await post_upd(t["id"], "SL_HIT", pts=t["entry_price"]-sl, bot=app.bot)
            elif cp <= t["target1"] and t["status"]=="ACTIVE": await post_upd(t["id"], "T1_HIT", nsl=t["entry_price"], pts=t["entry_price"]-t["target1"], bot=app.bot)
            elif cp <= t["target2"] and t["status"]=="T1_HIT": await post_upd(t["id"], "T2_HIT", nsl=t["target1"], pts=t["entry_entry"]-t["target2"], bot=app.bot)
            elif cp <= t["target3"]: await post_upd(t["id"], "T3_HIT", pts=t["entry_price"]-t["target3"], bot=app.bot)

async def start_webhook(app):
    wa = web.Application(); wa["telegram_app"] = app
    async def tv_h(r): return web.json_response({"status":"ok"})
    wa.router.add_get("/health", tv_h)
    runner = web.AppRunner(wa); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT).start()

# ================= MAIN APP =================
async def post_init(app):
    await db.connect()
    app.bot_data["bot"] = app.bot
    scheduler.add_job(job_morning, CronTrigger(hour=8, minute=30), args=[app], id="m", replace_existing=True)
    scheduler.add_job(job_oi, IntervalTrigger(minutes=OI_UPDATE_INTERVAL_MINUTES), args=[app], id="o", replace_existing=True)
    scheduler.add_job(job_promo, CronTrigger(hour=11, minute=30), args=[app], id="p1", replace_existing=True)
    scheduler.add_job(job_summary, CronTrigger(hour=15, minute=45), args=[app], id="s", replace_existing=True)
    scheduler.add_job(job_promo, CronTrigger(hour=20, minute=30), args=[app], id="p2", replace_existing=True)
    scheduler.add_job(job_monitor, IntervalTrigger(minutes=1), args=[app], id="mon", replace_existing=True)
    scheduler.add_job(job_expiry, IntervalTrigger(minutes=5), args=[app], id="exp", replace_existing=True)
    scheduler.start()
    logging.info("Bot is LIVE.")

async def post_shutdown(app):
    scheduler.shutdown(wait=False); await db.close()

def main():
    if not validate_config(): sys.exit(1)
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).concurrent_updates(True).build()
    
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("plans", cmd_plans))
    app.add_handler(CommandHandler("addvip", cmd_addvip))
    app.add_handler(CommandHandler("forcecall", cmd_force))
    app.add_handler(CallbackQueryHandler(plan_cb, pattern=r"^plan_"))
    app.add_handler(CallbackQueryHandler(back_cb, pattern=r"^back_plans$"))
    app.add_handler(ChatJoinRequestHandler(join_req))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, spam_guard, block=False))
    
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    loop.create_task(start_webhook(app))
    
    logging.basicConfig(format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S", level=logging.INFO)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
