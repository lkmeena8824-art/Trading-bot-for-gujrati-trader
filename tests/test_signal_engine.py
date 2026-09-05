import unittest
from datetime import datetime, timedelta, timezone

from market_data import Candle
from signal_engine import atr, ema, rsi, vwap


class IndicatorTests(unittest.TestCase):
    def setUp(self):
        start = datetime(2026, 1, 5, 9, 15, tzinfo=timezone.utc)
        self.candles = [
            Candle(start + timedelta(minutes=5 * i), 100 + i, 101 + i, 99 + i, 100 + i, 1000 + i * 10)
            for i in range(30)
        ]

    def test_indicators_are_numeric(self):
        self.assertGreater(ema([c.close for c in self.candles], 9), 0)
        self.assertGreater(atr(self.candles), 0)
        self.assertGreater(vwap(self.candles), 0)
        self.assertGreaterEqual(rsi([c.close for c in self.candles]), 50)

    def test_vwap_uses_price_and_volume(self):
        value = vwap(self.candles)
        self.assertGreater(value, self.candles[0].close)
        self.assertLess(value, self.candles[-1].close)
