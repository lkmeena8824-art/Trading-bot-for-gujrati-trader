import aiosqlite
import json
import logging
from datetime import datetime, timedelta
from config import DATABASE_PATH, PLANS

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT, is_banned INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, last_active TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, plan_name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, is_active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, direction TEXT NOT NULL, entry_price REAL NOT NULL, sl REAL NOT NULL, target1 REAL, target2 REAL, target3 REAL, current_sl REAL, status TEXT DEFAULT 'ACTIVE', strategy TEXT NOT NULL, channel_type TEXT NOT NULL, message_id INTEGER, points_gained REAL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS opening_ranges (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, date TEXT NOT NULL, range_high REAL NOT NULL, range_low REAL NOT NULL, UNIQUE(symbol, date));
CREATE TABLE IF NOT EXISTS spam_blacklist (telegram_id INTEGER PRIMARY KEY, reason TEXT, banned_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS bot_stats (id INTEGER PRIMARY KEY AUTOINCREMENT, stat_date TEXT NOT NULL UNIQUE, total_trades INTEGER DEFAULT 0, winning_trades INTEGER DEFAULT 0, losing_trades INTEGER DEFAULT 0, total_points REAL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_sub_active ON subscriptions(is_active);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
"""

class Database:
    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._conn = None

    async def connect(self):
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        logger.info(f"DB connected: {self.db_path}")

    async def close(self):
        if self._conn: await self._conn.close()

    async def upsert_user(self, telegram_id: int, username: str = None, first_name: str = None, last_name: str = None):
        await self._conn.execute("INSERT INTO users (telegram_id, username, first_name, last_name, last_active) VALUES (?, ?, ?, ?, ?) ON CONFLICT(telegram_id) DO UPDATE SET username=COALESCE(?, username), first_name=COALESCE(?, first_name), last_active=CURRENT_TIMESTAMP", (telegram_id, username, first_name, last_name, datetime.now().isoformat(), username, first_name))
        await self._conn.commit()

    async def ban_user(self, telegram_id: int, reason: str = "Spam"):
        await self._conn.execute("UPDATE users SET is_banned = 1 WHERE telegram_id = ?", (reason, telegram_id))
        await self._conn.execute("INSERT OR REPLACE INTO spam_blacklist (telegram_id, reason) VALUES (?, ?)", (telegram_id, reason))
        await self._conn.commit()

    async def is_banned(self, telegram_id: int) -> bool:
        cursor = await self._conn.execute("SELECT 1 FROM spam_blacklist WHERE telegram_id = ?", (telegram_id,))
        return await cursor.fetchone() is not None

    async def add_subscription(self, user_id: int, plan_name: str) -> int:
        plan = PLANS.get(plan_name)
        if not plan: raise ValueError(f"Invalid plan: {plan_name}")
        now = datetime.now()
        end = now + timedelta(days=plan["duration_days"])
        cursor = await self._conn.execute("INSERT INTO subscriptions (user_id, plan_name, start_date, end_date) VALUES (?, ?, ?, ?)", (user_id, plan_name, now.isoformat(), end.isoformat()))
        await self._conn.commit()
        return cursor.lastrowid

    async def get_active_subscription(self, user_id: int) -> dict | None:
        cursor = await self._conn.execute("SELECT * FROM subscriptions WHERE user_id = ? AND is_active = 1 AND end_date > ? ORDER BY end_date DESC LIMIT 1", (user_id, datetime.now().isoformat()))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_expired_subscriptions(self) -> list[dict]:
        cursor = await self._conn.execute("SELECT * FROM subscriptions WHERE is_active = 1 AND end_date <= ?", (datetime.now().isoformat(),))
        return [dict(row) for row in await cursor.fetchall()]

    async def deactivate_subscription(self, sub_id: int):
        await self._conn.execute("UPDATE subscriptions SET is_active = 0 WHERE id = ?", (sub_id,))
        await self._conn.commit()

    async def create_trade(self, symbol: str, direction: str, entry_price: float, sl: float, target1: float, target2: float, target3: float, strategy: str, channel_type: str, message_id: int = None) -> int:
        cursor = await self._conn.execute("INSERT INTO trades (symbol, direction, entry_price, sl, target1, target2, target3, current_sl, strategy, channel_type, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (symbol, direction, entry_price, sl, target1, target2, target3, sl, strategy, channel_type, message_id))
        await self._conn.commit()
        return cursor.lastrowid

    async def get_trade(self, trade_id: int) -> dict | None:
        cursor = await self._conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_active_trades(self) -> list[dict]:
        cursor = await self._conn.execute("SELECT * FROM trades WHERE status = 'ACTIVE'")
        return [dict(row) for row in await cursor.fetchall()]

    async def update_trade(self, trade_id: int, **kwargs):
        if not kwargs: return
        kwargs["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [trade_id]
        await self._conn.execute(f"UPDATE trades SET {set_clause} WHERE id = ?", values)
        await self._conn.commit()

    async def get_today_trades(self, channel_type: str = None) -> list[dict]:
        today = datetime.now().strftime("%Y-%m-%d")
        query, params = "SELECT * FROM trades WHERE DATE(created_at) = ?", [today]
        if channel_type: query += " AND channel_type = ?"; params.append(channel_type)
        cursor = await self._conn.execute(query, params)
        return [dict(row) for row in await cursor.fetchall()]

    async def get_today_trade_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        cursor = await self._conn.execute("SELECT COUNT(*) as cnt FROM trades WHERE DATE(created_at) = ?", (today,))
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def get_today_free_trade_count(self) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        cursor = await self._conn.execute("SELECT COUNT(*) as cnt FROM trades WHERE DATE(created_at) = ? AND channel_type = 'FREE'", (today,))
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def set_opening_range(self, symbol: str, date_str: str, range_high: float, range_low: float):
        await self._conn.execute("INSERT OR REPLACE INTO opening_ranges (symbol, date, range_high, range_low) VALUES (?, ?, ?, ?)", (symbol, date_str, range_high, range_low))
        await self._conn.commit()

    async def update_daily_stats(self, stat_date: str, **kwargs):
        cursor = await self._conn.execute("SELECT id FROM bot_stats WHERE stat_date = ?", (stat_date,))
        if await cursor.fetchone():
            set_clause = ", ".join(f"{k} = COALESCE({k}, 0) + ?" for k in kwargs)
            values = list(kwargs.values()) + [stat_date]
            await self._conn.execute(f"UPDATE bot_stats SET {set_clause} WHERE stat_date = ?", values)
        else:
            cols, vals = ["stat_date"] + list(kwargs.keys()), [stat_date] + list(kwargs.values())
            await self._conn.execute(f"INSERT INTO bot_stats ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})", vals)
        await self._conn.commit()

    async def get_stats(self, days: int = 30) -> list[dict]:
        cursor = await self._conn.execute("SELECT * FROM bot_stats WHERE stat_date >= DATE('now', ?) ORDER BY stat_date DESC", (f"-{days} days",))
        return [dict(row) for row in await cursor.fetchall()]

db = Database()
