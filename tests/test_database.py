import os
import tempfile
import unittest

import database as database_module


class DatabaseLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_three_trade_cap_and_unique_slots(self):
        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        original = database_module.DATABASE_PATH
        database_module.DATABASE_PATH = path
        db = database_module.Database()
        try:
            await db.connect()
            kwargs = dict(
                sym="NIFTY CE",
                direction="BUY",
                entry=100,
                sl=90,
                t1=110,
                t2=120,
                t3=130,
                strategy="TEST",
                channel="FREE",
                market_day="2026-01-05",
                max_daily_trades=3,
                source="TEST",
                enforce_limit=True,
            )
            ids = []
            for slot in ("FIRST", "SECOND", "THIRD", "FOURTH"):
                ids.append(await db.create_trade_if_allowed(slot=slot, **kwargs))
            self.assertEqual(ids[:3], [1, 2, 3])
            self.assertIsNone(ids[3])
            self.assertEqual(await db.get_today_count("2026-01-05"), 3)
        finally:
            await db.close()
            database_module.DATABASE_PATH = original
            os.unlink(path)
