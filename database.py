import os
import aiosqlite
import logging
from datetime import datetime, timedelta
from config import DATABASE_PATH, PLANS

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, is_banned INTEGER DEFAULT 0, referred_by INTEGER, last_active TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, plan_name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, direction TEXT NOT NULL, entry_price REAL NOT NULL, sl REAL NOT NULL, target1 REAL, target2 REAL, target3 REAL, current_sl REAL, status TEXT DEFAULT 'ACTIVE', strategy TEXT NOT NULL, channel_type TEXT NOT NULL, message_id INTEGER, points_gained REAL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS spam_blacklist (telegram_id INTEGER PRIMARY KEY, reason TEXT);
CREATE TABLE IF NOT EXISTS bot_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, stat_date TEXT NOT NULL UNIQUE, total_trades INTEGER DEFAULT 0, winning_trades INTEGER DEFAULT 0, losing_trades INTEGER DEFAULT 0, total_points REAL DEFAULT 0);
"""

class Database:
    def __init__(self): self._conn = None
    
    async def connect(self):
        os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(DATABASE_PATH)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA); await self._conn.commit()
        try: await self._conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        except: pass
        await self._conn.commit()
        logger.info("DB connected.")
        
    async def close(self):
        if self._conn: await self._conn.close()
        
    async def get_user(self, tid):
        c = await self._conn.execute("SELECT * FROM users WHERE telegram_id=?", (tid,))
        r = await c.fetchone()
        return dict(r) if r else None

    async def upsert_user(self, tid, username=None, fn=None, ref_by=None):
        await self._conn.execute(
            "INSERT INTO users VALUES (?,?,?,0,?,CURRENT_TIMESTAMP) ON CONFLICT(telegram_id) DO UPDATE SET username=COALESCE(?,username), first_name=COALESCE(?,first_name), referred_by=COALESCE(?,referred_by), last_active=CURRENT_TIMESTAMP", 
            (tid, username, fn, ref_by, username, fn, ref_by)
        )
        await self._conn.commit()
        
    async def ban_user(self, tid, reason="Spam"):
        await self._conn.execute("INSERT OR REPLACE INTO spam_blacklist VALUES (?,?)", (tid, reason))
        await self._conn.commit()
        
    async def add_subscription(self, uid, plan):
        p = PLANS[plan]
        now = datetime.now()
        end = now + timedelta(days=p["duration_days"])
        c = await self._conn.execute(
            "INSERT INTO subscriptions (user_id, plan_name, start_date, end_date) VALUES (?,?,?,?)", 
            (uid, plan, now.isoformat(), end.isoformat())
        )
        await self._conn.commit()
        return c.lastrowid
        
    async def extend_subscription(self, uid, extra_days):
        # FIX: Used simple string concatenation instead of nested f-string
        await self._conn.execute(
            f"UPDATE subscriptions SET end_date = datetime(end_date, '+{extra_days} days') WHERE user_id=? AND is_active=1", 
            (extra_days, uid)
        )
        await self._conn.commit()

    async def get_active_sub(self, uid):
        c = await self._conn.execute(
            "SELECT * FROM subscriptions WHERE user_id=? AND is_active=1 AND end_date>?", 
            (uid, datetime.now().isoformat())
        )
        r = await c.fetchone()
        return dict(r) if r else None
        
    async def get_expired_subs(self):
        c = await self._conn.execute(
            "SELECT * FROM subscriptions WHERE is_active=1 AND end_date<=?", 
            (datetime.now().isoformat(),)
        )
        return [dict(r) for r in await c.fetchall()]
        
    async def deactivate_sub(self, sid):
        await self._conn.execute("UPDATE subscriptions SET is_active=0 WHERE id=?", (sid,))
        await self._conn.commit()
        
    async def create_trade(self, sym, dir, e, sl, t1, t2, t3, strat, ch, mid=None):
        c = await self._conn.execute(
            "INSERT INTO trades (symbol,direction,entry_price,sl,target1,target2,target3,current_sl,strategy,channel_type,message_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)", 
            (sym, dir, e, sl, t1, t2, t3, sl, strat, ch, mid)
        )
        await self._conn.commit()
        return c.lastrowid
        
    async def get_trade(self, tid):
        c = await self._conn.execute("SELECT * FROM trades WHERE id=?", (tid,))
        r = await c.fetchone()
        return dict(r) if r else None
        
    async def get_active_trades(self):
        c = await self._conn.execute("SELECT * FROM trades WHERE status='ACTIVE'")
        return [dict(r) for r in await c.fetchall()]
        
    async def update_trade(self, tid, **kw):
        if not kw: return
        kw["updated_at"] = datetime.now().isoformat()
        s = ", ".join(f"{k}=?" for k in kw)
        v = list(kw.values()) + [tid]
        await self._conn.execute(f"UPDATE trades SET {s} WHERE id=?", v)
        await self._conn.commit()
        
    async def get_today_trades(self):
        td = datetime.now().strftime("%Y-%m-%d")
        c = await self._conn.execute("SELECT * FROM trades WHERE DATE(created_at)=?", (td,))
        return [dict(r) for r in await c.fetchall()]
        
    async def get_today_count(self):
        td = datetime.now().strftime("%Y-%m-%d")
        c = await self._conn.execute("SELECT COUNT(*) as c FROM trades WHERE DATE(created_at)=?", (td,))
        r = await c.fetchone()
        return r["c"] if r else 0
        
    async def get_today_free_count(self):
        td = datetime.now().strftime("%Y-%m-%d")
        c = await self._conn.execute("SELECT COUNT(*) as c FROM trades WHERE DATE(created_at)=? AND channel_type='FREE'", (td,))
        r = await c.fetchone()
        return r["c"] if r else 0
        
    async def get_today_vip_count(self):
        td = datetime.now().strftime("%Y-%m-%d")
        c = await self._conn.execute("SELECT COUNT(*) as c FROM trades WHERE DATE(created_at)=? AND channel_type='VIP'", (td,))
        r = await c.fetchone()
        return r["c"] if r else 0
        
    async def update_stats(self, dt, **kw):
        c = await self._conn.execute("SELECT id FROM bot_stats WHERE stat_date=?", (dt,))
        if await c.fetchone():
            s = ", ".join(f"{k}=COALESCE({k},0)+?" for k in kw)
            v = list(kw.values()) + [dt]
            await self._conn.execute(f"UPDATE bot_stats SET {s} WHERE stat_date=?", v)
        else:
            cols = ["stat_date"] + list(kw.keys())
            vals = [dt] + list(kw.values())
            placeholders = ", ".join(["?"] * len(cols))
            await self._conn.execute(f"INSERT INTO bot_stats ({cols}) VALUES ({placeholders})", vals)
        await self._conn.commit()

db = Database()
