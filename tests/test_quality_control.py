import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from quality_control import is_market_session_open, slot_for_time


IST = ZoneInfo("Asia/Kolkata")


class QualityControlTests(unittest.TestCase):
    def test_three_slots(self):
        self.assertEqual(slot_for_time(datetime(2026, 1, 5, 9, 30, tzinfo=IST)), "FIRST")
        self.assertEqual(slot_for_time(datetime(2026, 1, 5, 11, 0, tzinfo=IST)), "SECOND")
        self.assertEqual(slot_for_time(datetime(2026, 1, 5, 13, 0, tzinfo=IST)), "THIRD")
        self.assertIsNone(slot_for_time(datetime(2026, 1, 5, 14, 0, tzinfo=IST)))

    def test_weekend_is_not_a_trade_day(self):
        saturday = datetime(2026, 1, 3, 9, 30, tzinfo=IST)
        self.assertIsNone(slot_for_time(saturday))
        self.assertFalse(is_market_session_open(saturday))

    def test_market_session(self):
        self.assertTrue(is_market_session_open(datetime(2026, 1, 5, 15, 0, tzinfo=IST)))
        self.assertFalse(is_market_session_open(datetime(2026, 1, 5, 15, 31, tzinfo=IST)))
