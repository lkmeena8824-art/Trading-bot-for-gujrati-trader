from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

from config import DATABASE_PATH, MARKET_TIMEZONE, PLANS

logger = logging.getLogger(__name__)
MARKET_TZ = ZoneInfo(MARKET_TIMEZONE)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    is_banned INTEGER DEFAULT 0,
    referred_by INTEGER,
    last_active TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    sl REAL NOT NULL,
    target1 REAL,
    target2 REAL,
    target3 REAL,
    current_sl REAL,
    status TEXT DEFAULT 'ACTIVE',
    strategy TEXT NOT NULL,
    channel_type TEXT NOT NULL,
    message_id INTEGER,
    points_gained REAL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    underlying TEXT,
    option_type TEXT,
    option_strike REAL,
    option_ltp REAL,
    entry_low REAL,
    entry_high REAL,
    logic TEXT,
    source TEXT DEFAULT 'AUTO',
    slot TEXT,
    market_day TEXT,
    rrr REAL,
    risk_points REAL,
    breakeven_moved INTEGER DEFAULT 0,
    partial_booked INTEGER DEFAULT 0,
    quantity REAL DEFAULT 1,
    remaining_quantity REAL DEFAULT 1,
    realized_points REAL DEFAULT 0,
    realized_pnl REAL DEFAULT 0,
    exit_price REAL,
    closed_at TEXT,
    last_event TEXT
);
CREATE TABLE IF NOT EXISTS spam_blacklist (
    telegram_id INTEGER PRIMARY KEY,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS bot_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stat_date TEXT NOT NULL UNIQUE,
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    total_points REAL DEFAULT 0,
    net_pnl REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS daily_sessions (
    market_day TEXT PRIMARY KEY,
    trade_count INTEGER DEFAULT 0,
    state TEXT DEFAULT 'NORMAL',
    no_trade_posted INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS trade_slots (
    market_day TEXT NOT NULL,
    slot TEXT NOT NULL,
    trade_id INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (market_day, slot)
);
CREATE TABLE IF NOT EXISTS trade_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    price REAL,
    quantity REAL,
    points REAL,
    metadata TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS message_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    status TEXT NOT NULL,
    attempts INTEGER DEFAULT 0,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS polls (
    poll_id TEXT PRIMARY KEY,
    question TEXT,
    options TEXT DEFAULT '[]',
    market_day TEXT,
    chat_id INTEGER,
    message_id INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS poll_answers (
    poll_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    option_ids TEXT DEFAULT '[]',
    option_texts TEXT DEFAULT '[]',
    market_day TEXT,
    retracted INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (poll_id, user_id)
);
CREATE TABLE IF NOT EXISTS community_memory (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
    poll_votes INTEGER DEFAULT 0,
    last_vote_day TEXT,
    last_option TEXT,
    sentiment_tally TEXT DEFAULT '{}',
    messages INTEGER DEFAULT 0,
    last_message_day TEXT,
    last_intent TEXT,
    reply_count INTEGER DEFAULT 0,
    reply_day TEXT,
    last_reply_at TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS market_memory (
    market_day TEXT PRIMARY KEY,
    phase TEXT DEFAULT 'PRE_OPEN',
    bias TEXT DEFAULT 'UNKNOWN',
    spot_open REAL,
    spot_close REAL,
    india_vix REAL,
    trades_taken INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    net_points REAL DEFAULT 0,
    no_trade INTEGER DEFAULT 0,
    headline TEXT,
    data TEXT DEFAULT '{}',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_day TEXT,
    provider TEXT,
    model TEXT,
    kind TEXT,
    status TEXT,
    latency_ms INTEGER DEFAULT 0,
    error TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _now_utc() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _market_day() -> str:
        return datetime.now(MARKET_TZ).date().isoformat()

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(DATABASE_PATH)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(SCHEMA)
        await self._migrate_existing_tables()
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_market_day ON trades(market_day)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        await self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_events_trade_id ON trade_events(trade_id)")
        await self._conn.commit()
        logger.info("DB connected at %s", DATABASE_PATH)

    async def _migrate_existing_tables(self) -> None:
        """Additive migrations for databases created by the original bot."""
        if not self._conn:
            return
        columns = {
            "underlying": "TEXT",
            "option_type": "TEXT",
            "option_strike": "REAL",
            "option_ltp": "REAL",
            "entry_low": "REAL",
            "entry_high": "REAL",
            "logic": "TEXT",
            "source": "TEXT DEFAULT 'AUTO'",
            "slot": "TEXT",
            "market_day": "TEXT",
            "rrr": "REAL",
            "risk_points": "REAL",
            "breakeven_moved": "INTEGER DEFAULT 0",
            "partial_booked": "INTEGER DEFAULT 0",
            "quantity": "REAL DEFAULT 1",
            "remaining_quantity": "REAL DEFAULT 1",
            "realized_points": "REAL DEFAULT 0",
            "realized_pnl": "REAL DEFAULT 0",
            "exit_price": "REAL",
            "closed_at": "TEXT",
            "last_event": "TEXT",
        }
        cursor = await self._conn.execute("PRAGMA table_info(trades)")
        existing = {row[1] for row in await cursor.fetchall()}
        for column, declaration in columns.items():
            if column not in existing:
                await self._conn.execute(f"ALTER TABLE trades ADD COLUMN {column} {declaration}")

        # Backfill market_day for legacy rows using the legacy timestamp where
        # possible. New rows always receive an explicit IST market day.
        await self._conn.execute(
            "UPDATE trades SET market_day=substr(created_at,1,10) WHERE market_day IS NULL"
        )
        await self._conn.execute(
            "UPDATE trades SET underlying=symbol WHERE underlying IS NULL"
        )
        await self._conn.execute(
            "UPDATE trades SET source='LEGACY' WHERE source IS NULL"
        )

        # The original bot_stats table did not contain net_pnl.
        stats_cursor = await self._conn.execute("PRAGMA table_info(bot_stats)")
        stats_columns = {row[1] for row in await stats_cursor.fetchall()}
        if "net_pnl" not in stats_columns:
            await self._conn.execute("ALTER TABLE bot_stats ADD COLUMN net_pnl REAL DEFAULT 0")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database is not connected")
        return self._conn

    async def get_user(self, tid: int) -> dict | None:
        conn = self._require_conn()
        cursor = await conn.execute("SELECT * FROM users WHERE telegram_id=?", (tid,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def upsert_user(self, tid: int, username: str | None = None, fn: str | None = None, ref_by: int | None = None) -> None:
        conn = self._require_conn()
        await conn.execute(
            """INSERT INTO users (telegram_id,username,first_name,is_banned,referred_by,last_active)
               VALUES (?,?,?,0,?,CURRENT_TIMESTAMP)
               ON CONFLICT(telegram_id) DO UPDATE SET
                 username=COALESCE(excluded.username,users.username),
                 first_name=COALESCE(excluded.first_name,users.first_name),
                 referred_by=COALESCE(excluded.referred_by,users.referred_by),
                 last_active=CURRENT_TIMESTAMP""",
            (tid, username, fn, ref_by),
        )
        await conn.commit()

    async def ban_user(self, tid: int, reason: str = "Spam") -> None:
        conn = self._require_conn()
        await conn.execute(
            "INSERT OR REPLACE INTO spam_blacklist (telegram_id,reason) VALUES (?,?)",
            (tid, reason),
        )
        await conn.commit()

    async def add_subscription(self, uid: int, plan: str) -> int:
        conn = self._require_conn()
        plan_data = PLANS[plan]
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=plan_data["duration_days"])
        cursor = await conn.execute(
            "INSERT INTO subscriptions (user_id,plan_name,start_date,end_date) VALUES (?,?,?,?)",
            (uid, plan, now.isoformat(), end.isoformat()),
        )
        await conn.commit()
        return int(cursor.lastrowid)

    async def extend_subscription(self, uid: int, extra_days: int) -> None:
        conn = self._require_conn()
        extra_days = max(0, int(extra_days))
        await conn.execute(
            "UPDATE subscriptions SET end_date=datetime(end_date, ?) WHERE user_id=? AND is_active=1",
            (f"+{extra_days} days", uid),
        )
        await conn.commit()

    async def get_active_sub(self, uid: int) -> dict | None:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM subscriptions WHERE user_id=? AND is_active=1 AND end_date>? ORDER BY end_date DESC",
            (uid, datetime.now(timezone.utc).isoformat()),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_expired_subs(self) -> list[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM subscriptions WHERE is_active=1 AND end_date<=?",
            (datetime.now(timezone.utc).isoformat(),),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def deactivate_sub(self, sid: int) -> None:
        conn = self._require_conn()
        await conn.execute("UPDATE subscriptions SET is_active=0 WHERE id=?", (sid,))
        await conn.commit()

    async def create_trade_if_allowed(
        self,
        *,
        sym: str,
        direction: str,
        entry: float,
        sl: float,
        t1: float,
        t2: float,
        t3: float,
        strategy: str,
        channel: str,
        market_day: str,
        slot: str | None,
        max_daily_trades: int,
        message_id: int | None = None,
        underlying: str | None = None,
        option_type: str | None = None,
        option_strike: float | None = None,
        option_ltp: float | None = None,
        entry_low: float | None = None,
        entry_high: float | None = None,
        logic: str | None = None,
        source: str = "AUTO",
        rrr: float | None = None,
        risk_points: float | None = None,
        enforce_limit: bool = True,
    ) -> int | None:
        """Atomically reserve a trade count/slot and insert a pending trade."""
        conn = self._require_conn()
        async with self._lock:
            try:
                await conn.execute("BEGIN IMMEDIATE")
                if enforce_limit:
                    cursor = await conn.execute(
                        """SELECT COUNT(*) FROM trades
                           WHERE market_day=? AND status NOT IN ('CANCELLED')""",
                        (market_day,),
                    )
                    count = int((await cursor.fetchone())[0])
                    if count >= max_daily_trades:
                        await conn.rollback()
                        return None
                    if not slot:
                        await conn.rollback()
                        return None
                    cursor = await conn.execute(
                        "SELECT trade_id FROM trade_slots WHERE market_day=? AND slot=?",
                        (market_day, slot),
                    )
                    if await cursor.fetchone():
                        await conn.rollback()
                        return None

                now = self._now_utc()
                cursor = await conn.execute(
                    """INSERT INTO trades (
                        symbol,direction,entry_price,sl,target1,target2,target3,current_sl,
                        status,strategy,channel_type,message_id,points_gained,created_at,updated_at,
                        underlying,option_type,option_strike,option_ltp,entry_low,entry_high,logic,source,slot,market_day,
                        rrr,risk_points,quantity,remaining_quantity,last_event
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        sym, direction, entry, sl, t1, t2, t3, sl, "PENDING", strategy,
                        channel, message_id, 0, now, now, underlying or sym, option_type,
                        option_strike, option_ltp, entry_low, entry_high, logic, source, slot, market_day, rrr,
                        risk_points, 1, 1, "CREATED",
                    ),
                )
                trade_id = int(cursor.lastrowid)
                if enforce_limit and slot:
                    await conn.execute(
                        "INSERT INTO trade_slots (market_day,slot,trade_id) VALUES (?,?,?)",
                        (market_day, slot, trade_id),
                    )
                await conn.execute(
                    """INSERT INTO daily_sessions (market_day,trade_count,state,updated_at)
                       VALUES (?,1,'NORMAL',?)
                       ON CONFLICT(market_day) DO UPDATE SET
                         trade_count=daily_sessions.trade_count+1,
                         updated_at=excluded.updated_at""",
                    (market_day, now),
                )
                await conn.execute(
                    "INSERT INTO trade_events (trade_id,event_type,metadata,created_at) VALUES (?,?,?,?)",
                    (trade_id, "CREATED", source, now),
                )
                await conn.commit()
                return trade_id
            except Exception:
                await conn.rollback()
                raise

    async def create_trade(self, sym, dir, e, sl, t1, t2, t3, strat, ch, mid=None, **kwargs) -> int:
        """Backward-compatible manual insert used by older integrations."""
        trade_id = await self.create_trade_if_allowed(
            sym=sym,
            direction=dir,
            entry=e,
            sl=sl,
            t1=t1,
            t2=t2,
            t3=t3,
            strategy=strat,
            channel=ch,
            market_day=kwargs.get("market_day", self._market_day()),
            slot=None,
            max_daily_trades=999999,
            message_id=mid,
            underlying=kwargs.get("underlying", sym),
            option_type=kwargs.get("option_type"),
            option_strike=kwargs.get("option_strike"),
            option_ltp=kwargs.get("option_ltp"),
            logic=kwargs.get("logic"),
            source=kwargs.get("source", "LEGACY"),
            enforce_limit=False,
        )
        if trade_id is None:
            raise RuntimeError("Could not create trade")
        return trade_id

    async def get_trade(self, tid: int) -> dict | None:
        conn = self._require_conn()
        cursor = await conn.execute("SELECT * FROM trades WHERE id=?", (tid,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_active_trades(self) -> list[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM trades WHERE status IN ('ACTIVE','BREAKEVEN','PARTIAL_BOOKED','RUNNER_ACTIVE') ORDER BY id"
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def update_trade(self, tid: int, **values) -> None:
        conn = self._require_conn()
        allowed = {
            "status", "message_id", "current_sl", "points_gained", "realized_points",
            "realized_pnl", "remaining_quantity", "breakeven_moved", "partial_booked",
            "exit_price", "closed_at", "last_event", "updated_at",
        }
        safe_values = {key: value for key, value in values.items() if key in allowed}
        if not safe_values:
            return
        safe_values["updated_at"] = self._now_utc()
        assignments = ", ".join(f"{key}=?" for key in safe_values)
        params = list(safe_values.values()) + [tid]
        await conn.execute(f"UPDATE trades SET {assignments} WHERE id=?", params)
        await conn.commit()

    async def record_trade_event(
        self,
        trade_id: int,
        event_type: str,
        price: float | None = None,
        quantity: float | None = None,
        points: float | None = None,
        metadata: str | None = None,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            "INSERT INTO trade_events (trade_id,event_type,price,quantity,points,metadata,created_at) VALUES (?,?,?,?,?,?,?)",
            (trade_id, event_type, price, quantity, points, metadata, self._now_utc()),
        )
        await conn.commit()

    async def get_today_trades(self, day: str | None = None) -> list[dict]:
        conn = self._require_conn()
        day = day or self._market_day()
        cursor = await conn.execute(
            "SELECT * FROM trades WHERE market_day=? OR (market_day IS NULL AND substr(created_at,1,10)=?) ORDER BY id",
            (day, day),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_today_count(self, day: str | None = None) -> int:
        trades = await self.get_today_trades(day)
        return len(trades)

    async def get_today_free_count(self, day: str | None = None) -> int:
        return sum(1 for trade in await self.get_today_trades(day) if trade["channel_type"] == "FREE")

    async def get_today_vip_count(self, day: str | None = None) -> int:
        return sum(1 for trade in await self.get_today_trades(day) if trade["channel_type"] == "VIP")

    async def get_daily_state(self, day: str | None = None) -> dict:
        conn = self._require_conn()
        day = day or self._market_day()
        cursor = await conn.execute("SELECT * FROM daily_sessions WHERE market_day=?", (day,))
        row = await cursor.fetchone()
        return dict(row) if row else {"market_day": day, "trade_count": 0, "state": "NORMAL", "no_trade_posted": 0}

    async def mark_no_trade(self, day: str | None = None, state: str = "NO_TRADE_ZONE") -> bool:
        conn = self._require_conn()
        day = day or self._market_day()
        async with self._lock:
            cursor = await conn.execute(
                "SELECT no_trade_posted FROM daily_sessions WHERE market_day=?",
                (day,),
            )
            row = await cursor.fetchone()
            if row and row[0]:
                return False
            await conn.execute(
                """INSERT INTO daily_sessions (market_day,trade_count,state,no_trade_posted,updated_at)
                   VALUES (?,0,?,1,?)
                   ON CONFLICT(market_day) DO UPDATE SET
                     state=excluded.state,no_trade_posted=1,updated_at=excluded.updated_at""",
                (day, state, self._now_utc()),
            )
            await conn.commit()
            return True

    async def update_stats(self, dt: str, **values) -> None:
        conn = self._require_conn()
        columns = {
            "total_trades", "winning_trades", "losing_trades", "total_points", "net_pnl"
        }
        values = {key: value for key, value in values.items() if key in columns}
        if not values:
            return
        values.setdefault("total_trades", 0)
        values.setdefault("winning_trades", 0)
        values.setdefault("losing_trades", 0)
        values.setdefault("total_points", 0)
        values.setdefault("net_pnl", 0)
        await conn.execute(
            """INSERT INTO bot_stats (stat_date,total_trades,winning_trades,losing_trades,total_points,net_pnl)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(stat_date) DO UPDATE SET
                 total_trades=excluded.total_trades,
                 winning_trades=excluded.winning_trades,
                 losing_trades=excluded.losing_trades,
                 total_points=excluded.total_points,
                 net_pnl=excluded.net_pnl""",
            (
                dt,
                values["total_trades"],
                values["winning_trades"],
                values["losing_trades"],
                values["total_points"],
                values["net_pnl"],
            ),
        )
        await conn.commit()

    # ================= PHASE 2: POLLS, COMMUNITY AND MARKET MEMORY =========
    # These tables only store engagement/state facts. Nothing written here can
    # influence a trade decision; the signal engine remains the sole authority
    # for direction, entry, SL, targets, RRR, OI reading and P&L.

    async def record_poll(
        self,
        poll_id: str,
        question: str,
        options: list[str],
        market_day: str | None = None,
        chat_id: int | None = None,
        message_id: int | None = None,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """INSERT INTO polls (poll_id,question,options,market_day,chat_id,message_id,created_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(poll_id) DO UPDATE SET
                 question=excluded.question,
                 options=excluded.options,
                 market_day=excluded.market_day,
                 chat_id=excluded.chat_id,
                 message_id=excluded.message_id""",
            (
                str(poll_id),
                question,
                json.dumps(list(options or []), ensure_ascii=False),
                market_day or self._market_day(),
                chat_id,
                message_id,
                self._now_utc(),
            ),
        )
        await conn.commit()

    async def get_poll(self, poll_id: str) -> dict | None:
        conn = self._require_conn()
        cursor = await conn.execute("SELECT * FROM polls WHERE poll_id=?", (str(poll_id),))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_latest_poll(self, market_day: str | None = None) -> dict | None:
        conn = self._require_conn()
        day = market_day or self._market_day()
        cursor = await conn.execute(
            "SELECT * FROM polls WHERE market_day=? ORDER BY created_at DESC LIMIT 1",
            (day,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def record_poll_answer(
        self,
        poll_id: str,
        user_id: int,
        option_ids: list[int],
        option_texts: list[str] | None = None,
        username: str | None = None,
        first_name: str | None = None,
        market_day: str | None = None,
    ) -> None:
        conn = self._require_conn()
        day = market_day or self._market_day()
        retracted = 0 if option_ids else 1
        await conn.execute(
            """INSERT INTO poll_answers
                 (poll_id,user_id,username,first_name,option_ids,option_texts,market_day,retracted,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(poll_id,user_id) DO UPDATE SET
                 username=COALESCE(excluded.username,poll_answers.username),
                 first_name=COALESCE(excluded.first_name,poll_answers.first_name),
                 option_ids=excluded.option_ids,
                 option_texts=excluded.option_texts,
                 retracted=excluded.retracted,
                 updated_at=excluded.updated_at""",
            (
                str(poll_id),
                int(user_id),
                username,
                first_name,
                json.dumps(list(option_ids or [])),
                json.dumps(list(option_texts or []), ensure_ascii=False),
                day,
                retracted,
                self._now_utc(),
                self._now_utc(),
            ),
        )
        await conn.commit()

    async def get_poll_answers(self, poll_id: str) -> list[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM poll_answers WHERE poll_id=? AND retracted=0 ORDER BY updated_at",
            (str(poll_id),),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_poll_tally(self, poll_id: str) -> dict[str, int]:
        tally: dict[str, int] = {}
        for answer in await self.get_poll_answers(poll_id):
            try:
                options = json.loads(answer.get("option_texts") or "[]")
            except (TypeError, ValueError):
                options = []
            for option in options:
                tally[str(option)] = tally.get(str(option), 0) + 1
        return tally

    async def upsert_community_member(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
    ) -> None:
        conn = self._require_conn()
        now = self._now_utc()
        await conn.execute(
            """INSERT INTO community_memory (user_id,username,first_name,first_seen,last_seen)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 username=COALESCE(excluded.username,community_memory.username),
                 first_name=COALESCE(excluded.first_name,community_memory.first_name),
                 last_seen=excluded.last_seen""",
            (int(user_id), username, first_name, now, now),
        )
        await conn.commit()

    async def get_community_member(self, user_id: int) -> dict | None:
        conn = self._require_conn()
        cursor = await conn.execute("SELECT * FROM community_memory WHERE user_id=?", (int(user_id),))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def note_community_vote(
        self,
        user_id: int,
        option_text: str | None,
        market_day: str | None = None,
        username: str | None = None,
        first_name: str | None = None,
    ) -> dict:
        await self.upsert_community_member(user_id, username, first_name)
        conn = self._require_conn()
        day = market_day or self._market_day()
        member = await self.get_community_member(user_id) or {}
        try:
            tally = json.loads(member.get("sentiment_tally") or "{}")
        except (TypeError, ValueError):
            tally = {}
        if option_text:
            tally[str(option_text)] = int(tally.get(str(option_text), 0)) + 1
        already_voted_today = member.get("last_vote_day") == day
        votes = int(member.get("poll_votes") or 0) + (0 if already_voted_today else 1)
        await conn.execute(
            """UPDATE community_memory
                  SET poll_votes=?, last_vote_day=?, last_option=?, sentiment_tally=?, last_seen=?
                WHERE user_id=?""",
            (votes, day, option_text, json.dumps(tally, ensure_ascii=False), self._now_utc(), int(user_id)),
        )
        await conn.commit()
        return await self.get_community_member(user_id) or {}

    async def note_community_message(
        self,
        user_id: int,
        intent: str | None = None,
        market_day: str | None = None,
        username: str | None = None,
        first_name: str | None = None,
    ) -> dict:
        await self.upsert_community_member(user_id, username, first_name)
        conn = self._require_conn()
        day = market_day or self._market_day()
        await conn.execute(
            """UPDATE community_memory
                  SET messages=COALESCE(messages,0)+1,
                      last_message_day=?,
                      last_intent=COALESCE(?,last_intent),
                      last_seen=?
                WHERE user_id=?""",
            (day, intent, self._now_utc(), int(user_id)),
        )
        await conn.commit()
        return await self.get_community_member(user_id) or {}

    async def register_reply(self, user_id: int, market_day: str | None = None) -> int:
        """Count a contextual reply for the day and return the new count."""
        conn = self._require_conn()
        day = market_day or self._market_day()
        async with self._lock:
            member = await self.get_community_member(user_id)
            if member is None:
                await self.upsert_community_member(user_id)
                member = await self.get_community_member(user_id) or {}
            count = int(member.get("reply_count") or 0) if member.get("reply_day") == day else 0
            count += 1
            await conn.execute(
                "UPDATE community_memory SET reply_count=?, reply_day=?, last_reply_at=? WHERE user_id=?",
                (count, day, self._now_utc(), int(user_id)),
            )
            await conn.commit()
            return count

    async def community_stats(self, market_day: str | None = None) -> dict:
        conn = self._require_conn()
        day = market_day or self._market_day()
        cursor = await conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM poll_answers WHERE market_day=? AND retracted=0",
            (day,),
        )
        voters = int((await cursor.fetchone())[0] or 0)
        cursor = await conn.execute("SELECT COUNT(*) FROM community_memory")
        members = int((await cursor.fetchone())[0] or 0)
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM community_memory WHERE last_seen>=datetime('now','-7 days')"
        )
        active = int((await cursor.fetchone())[0] or 0)
        poll = await self.get_latest_poll(day)
        tally = await self.get_poll_tally(poll["poll_id"]) if poll else {}
        top_option, top_votes = ("", 0)
        if tally:
            top_option, top_votes = max(tally.items(), key=lambda item: item[1])
        return {
            "market_day": day,
            "members": members,
            "active_members_7d": active,
            "voters_today": voters,
            "tally": tally,
            "top_option": top_option,
            "top_votes": top_votes,
        }

    async def upsert_market_memory(self, market_day: str | None = None, **values) -> dict:
        """Merge today's market-state facts. Unknown keys go to the JSON blob."""
        conn = self._require_conn()
        day = market_day or self._market_day()
        columns = {
            "phase", "bias", "spot_open", "spot_close", "india_vix",
            "trades_taken", "wins", "losses", "net_points", "no_trade", "headline",
        }
        extra = {key: value for key, value in values.items() if key not in columns}
        known = {key: value for key, value in values.items() if key in columns}

        async with self._lock:
            cursor = await conn.execute("SELECT * FROM market_memory WHERE market_day=?", (day,))
            row = await cursor.fetchone()
            if row is None:
                await conn.execute(
                    "INSERT INTO market_memory (market_day,created_at,updated_at) VALUES (?,?,?)",
                    (day, self._now_utc(), self._now_utc()),
                )
                existing_data = {}
            else:
                try:
                    existing_data = json.loads(dict(row).get("data") or "{}")
                except (TypeError, ValueError):
                    existing_data = {}
            if extra:
                existing_data.update(extra)
            assignments = [f"{key}=?" for key in known]
            params: list[Any] = list(known.values())
            assignments.append("data=?")
            params.append(json.dumps(existing_data, ensure_ascii=False, default=str))
            assignments.append("updated_at=?")
            params.append(self._now_utc())
            params.append(day)
            await conn.execute(
                f"UPDATE market_memory SET {','.join(assignments)} WHERE market_day=?",
                params,
            )
            await conn.commit()
        return await self.get_market_memory(day)

    async def get_market_memory(self, market_day: str | None = None) -> dict:
        conn = self._require_conn()
        day = market_day or self._market_day()
        cursor = await conn.execute("SELECT * FROM market_memory WHERE market_day=?", (day,))
        row = await cursor.fetchone()
        if not row:
            return {"market_day": day, "phase": "UNKNOWN", "bias": "UNKNOWN", "data": {}}
        record = dict(row)
        try:
            record["data"] = json.loads(record.get("data") or "{}")
        except (TypeError, ValueError):
            record["data"] = {}
        return record

    async def recent_market_memory(self, limit: int = 5) -> list[dict]:
        conn = self._require_conn()
        cursor = await conn.execute(
            "SELECT * FROM market_memory ORDER BY market_day DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        records = []
        for row in await cursor.fetchall():
            record = dict(row)
            try:
                record["data"] = json.loads(record.get("data") or "{}")
            except (TypeError, ValueError):
                record["data"] = {}
            records.append(record)
        return records

    async def log_ai_usage(
        self,
        market_day: str | None = None,
        provider: str = "none",
        model: str = "",
        kind: str = "",
        status: str = "",
        latency_ms: int = 0,
        error: str = "",
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """INSERT INTO ai_usage (market_day,provider,model,kind,status,latency_ms,error,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                market_day or self._market_day(),
                provider,
                model,
                kind,
                status,
                int(latency_ms or 0),
                (error or "")[:300],
                self._now_utc(),
            ),
        )
        await conn.commit()

    async def ai_call_count(self, market_day: str | None = None) -> int:
        conn = self._require_conn()
        day = market_day or self._market_day()
        cursor = await conn.execute("SELECT COUNT(*) FROM ai_usage WHERE market_day=?", (day,))
        return int((await cursor.fetchone())[0] or 0)


# One shared database instance preserves the original bot API.
db = Database()
