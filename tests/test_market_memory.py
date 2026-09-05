"""Daily market state memory tests."""

import os
import tempfile
import unittest
from dataclasses import dataclass

import database as database_module
import market_memory
from quality_control import market_day


@dataclass
class FakeDecision:
    signal: dict | None = None
    reasons: tuple = ()
    no_trade_zone: bool = False
    data_unavailable: bool = False


class MarketMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self._original = database_module.DATABASE_PATH
        database_module.DATABASE_PATH = self.path
        self.db = database_module.db
        await self.db.connect()

    async def asyncTearDown(self):
        await self.db.close()
        database_module.DATABASE_PATH = self._original
        os.unlink(self.path)

    async def test_premarket_and_open_state_is_stored(self):
        await market_memory.remember_premarket(
            {"india_vix": 13.4, "gift_nifty": 22500.0, "fii_net": -1200.5, "source": "YAHOO_FINANCE_FREE"}
        )
        await market_memory.remember_open({"spot": 22480.0}, {"spot": 48200.0})
        state = await market_memory.today()
        self.assertEqual(state["phase"], "OPEN")
        self.assertAlmostEqual(state["india_vix"], 13.4)
        self.assertAlmostEqual(state["spot_open"], 22480.0)
        self.assertAlmostEqual(state["data"]["fii_net"], -1200.5)

    async def test_engine_decision_defines_bias_not_ai(self):
        await market_memory.remember_decision("FIRST", FakeDecision(signal={"direction": "BUY"}))
        self.assertEqual((await market_memory.today())["bias"], "BUY")

        await market_memory.remember_decision("SECOND", FakeDecision(no_trade_zone=True, reasons=("range too tight",)))
        state = await market_memory.today()
        self.assertEqual(state["bias"], "NO_TRADE_ZONE")
        self.assertEqual(state["data"]["last_reason"], "range too tight")

        await market_memory.remember_decision("THIRD", FakeDecision(data_unavailable=True))
        self.assertEqual((await market_memory.today())["bias"], "DATA_UNAVAILABLE")

    async def test_trade_and_close_facts(self):
        await market_memory.remember_trade_posted({"symbol": "NIFTY", "slot": "FIRST"}, 7)
        self.assertEqual((await market_memory.today())["trades_taken"], 1)

        trades = [
            {"symbol": "NIFTY", "status": "T3_HIT", "realized_points": 120.0},
            {"symbol": "NIFTY", "status": "SL_HIT", "realized_points": -40.0},
        ]
        await market_memory.remember_close(trades, {"spot": 22600.0}, {"spot": 48500.0})
        state = await market_memory.today()
        self.assertEqual(state["phase"], "CLOSED")
        self.assertEqual(state["wins"], 1)
        self.assertEqual(state["losses"], 1)
        self.assertAlmostEqual(state["net_points"], 80.0)
        self.assertAlmostEqual(state["spot_close"], 22600.0)

    async def test_no_trade_day_memory(self):
        await market_memory.remember_no_trade("NO_TRADE_ZONE", "choppy range")
        state = await market_memory.today()
        self.assertEqual(state["no_trade"], 1)
        self.assertIn("No-trade", state["headline"])

    async def test_reply_context_is_fact_only(self):
        await market_memory.remember_decision("FIRST", FakeDecision(signal={"direction": "SELL"}))
        await market_memory.remember_trade_posted({"symbol": "NIFTY", "slot": "FIRST"}, 1)
        context = await market_memory.reply_context()
        self.assertEqual(context["market_day"], market_day())
        self.assertEqual(context["calls_today"], 1)
        # The context describes the engine read in words; it never hands the
        # reply layer a raw BUY/SELL instruction to repeat as advice.
        self.assertIn("sell-side pressure", context["engine_read"])
        self.assertNotIn("target", context)
        self.assertNotIn("entry", context)

    async def test_history_is_kept_per_day(self):
        await self.db.upsert_market_memory("2026-01-05", phase="CLOSED", net_points=25.0, trades_taken=2)
        await self.db.upsert_market_memory("2026-01-06", phase="CLOSED", net_points=-10.0, trades_taken=1)
        history = await market_memory.recent(5)
        days = [row["market_day"] for row in history]
        self.assertIn("2026-01-05", days)
        self.assertIn("2026-01-06", days)

    async def test_describe_is_deterministic_text(self):
        await market_memory.remember_decision("FIRST", FakeDecision(reasons=("no volume",)))
        text = market_memory.describe(await market_memory.today())
        self.assertIn("Calls today: 0", text)
        self.assertIn("no confirmed A+ setup", text)


if __name__ == "__main__":
    unittest.main()
