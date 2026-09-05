"""Fact-guard tests: AI must never change a trade fact or pick a direction."""

import unittest

from ai_guard import (
    collect_allowed_numbers,
    direction_words,
    extract_numbers,
    guard_text,
    missing_required_numbers,
)

TRADE_FACTS = {
    "symbol": "NIFTY",
    "direction": "BUY",
    "entry": 22450.0,
    "entry_low": 22440.0,
    "entry_high": 22460.0,
    "sl": 22410.0,
    "t1": 22490.0,
    "t2": 22530.0,
    "t3": 22570.0,
    "rrr": 3.0,
    "risk_points": 40.0,
}

TRADE_FALLBACK = (
    "<b>SNIPER CALL — NIFTY</b>\n"
    "Direction: BUY\nEntry: 22,440.00 – 22,460.00\nSL: 22,410.00\n"
    "T1: 22,490.00 T2: 22,530.00 T3: 22,570.00\nR:R 1:3.00 Risk 40.00 pts"
)


class NumberExtractionTests(unittest.TestCase):
    def test_extracts_and_normalises(self):
        self.assertIn(22450.0, extract_numbers("Entry at 22,450.00 now"))
        self.assertIn(-12.5, extract_numbers("Points: -12.5"))

    def test_allowed_numbers_include_facts_and_fallback(self):
        allowed = collect_allowed_numbers(TRADE_FACTS, TRADE_FALLBACK)
        self.assertIn(22410.0, allowed)
        self.assertIn(22570.0, allowed)
        self.assertNotIn(22399.0, allowed)


class DirectionTests(unittest.TestCase):
    def test_direction_words_detected(self):
        self.assertIn("buy", direction_words("We BUY here"))
        self.assertIn("short", direction_words("go short now"))

    def test_lowercase_pe_is_not_directional(self):
        # "pe" is an ordinary Hinglish word ("market pe nazar").
        self.assertEqual(direction_words("market pe nazar rakho"), set())
        self.assertIn("PE", direction_words("NIFTY 22400 PE"))


class GuardTests(unittest.TestCase):
    def test_faithful_rephrase_is_accepted(self):
        candidate = (
            "<b>NIFTY sniper call</b>\nDirection BUY. Trigger 22,440.00 – 22,460.00, "
            "SL 22,410.00, targets 22,490.00 / 22,530.00 / 22,570.00. "
            "R:R 1:3.00 with 40.00 points risk. Apna risk budget follow karo."
        )
        result = guard_text(candidate, TRADE_FACTS, TRADE_FALLBACK, kind="trade_call")
        self.assertTrue(result.ok, result.reason)
        self.assertIn("22,410.00", result.text)

    def test_changed_stop_loss_is_rejected(self):
        candidate = (
            "Direction BUY. Entry 22,440.00 – 22,460.00, SL 22,400.00, "
            "targets 22,490.00 / 22,530.00 / 22,570.00, R:R 1:3.00, risk 40.00."
        )
        result = guard_text(candidate, TRADE_FACTS, TRADE_FALLBACK, kind="trade_call")
        self.assertFalse(result.ok)
        self.assertTrue(result.reason.startswith("unknown_number"))
        self.assertEqual(result.text, TRADE_FALLBACK)

    def test_flipped_direction_is_rejected(self):
        candidate = (
            "Direction SELL now. Entry 22,440.00 – 22,460.00, SL 22,410.00, "
            "targets 22,490.00 / 22,530.00 / 22,570.00, R:R 1:3.00, risk 40.00."
        )
        result = guard_text(candidate, TRADE_FACTS, TRADE_FALLBACK, kind="trade_call")
        self.assertFalse(result.ok)
        self.assertTrue(result.reason.startswith("direction_not_allowed"))

    def test_ai_cannot_introduce_a_direction_when_facts_have_none(self):
        facts = {"intent": "tip_request", "calls_today": 0}
        fallback = "Aaj koi confirmed setup nahi mila, isliye koi call nahi."
        result = guard_text("Aaj to buy karo, easy move hai.", facts, fallback, kind="reply_tip_request")
        self.assertFalse(result.ok)
        self.assertEqual(result.text, fallback)

    def test_dropping_a_critical_fact_is_rejected(self):
        candidate = "BUY NIFTY between 22,440.00 and 22,460.00. Risk 40.00 points, R:R 1:3.00."
        result = guard_text(candidate, TRADE_FACTS, TRADE_FALLBACK, kind="trade_call")
        self.assertFalse(result.ok)
        self.assertTrue(result.reason.startswith("missing_facts"))

    def test_missing_required_numbers_lists_keys(self):
        missing = missing_required_numbers("BUY NIFTY at 22,450.00", TRADE_FACTS)
        self.assertIn("sl", missing)
        self.assertIn("t3", missing)

    def test_profit_promises_are_rejected(self):
        candidate = (
            "Guaranteed profit! Direction BUY, entry 22,440.00 – 22,460.00, SL 22,410.00, "
            "targets 22,490.00 / 22,530.00 / 22,570.00, R:R 1:3.00, risk 40.00."
        )
        result = guard_text(candidate, TRADE_FACTS, TRADE_FALLBACK, kind="trade_call")
        self.assertFalse(result.ok)
        self.assertTrue(result.reason.startswith("banned_phrase"))

    def test_unsafe_markup_is_rejected(self):
        facts = {"intent": "greeting"}
        fallback = "Namaste! Aaj discipline pe focus."
        result = guard_text("<script>alert(1)</script>", facts, fallback, kind="reply_greeting")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "disallowed_tag:script")

    def test_unclosed_tag_is_rejected(self):
        facts = {"intent": "greeting"}
        fallback = "Namaste! Aaj discipline pe focus."
        result = guard_text("<b>Namaste ji", facts, fallback, kind="reply_greeting")
        self.assertFalse(result.ok)
        self.assertTrue(result.reason.startswith("unclosed_tag"))

    def test_pnl_numbers_cannot_be_inflated(self):
        facts = {"net_points": -12.5, "count": 1}
        fallback = "Aaj net realized points: -12.50. Loss honestly report kiya gaya hai."
        good = guard_text("Aaj ka net result: -12.50 points. Discipline intact.", facts, fallback, kind="pnl_report")
        self.assertTrue(good.ok, good.reason)
        bad = guard_text("Aaj ka net result: +250.00 points!", facts, fallback, kind="pnl_report")
        self.assertFalse(bad.ok)

    def test_structural_numbers_are_allowed(self):
        facts = {"intent": "timing"}
        fallback = "Windows: 09:15-10:15, 10:45-11:15, 12:45-13:15 IST."
        result = guard_text(
            "Sniper windows 09:15-10:15, 10:45-11:15 aur 12:45-13:15 IST par hi active rehte hain.",
            facts,
            fallback,
            kind="reply_timing",
        )
        self.assertTrue(result.ok, result.reason)

    def test_markdown_fences_are_stripped(self):
        facts = {"intent": "greeting"}
        fallback = "Namaste! Aaj discipline pe focus."
        result = guard_text("```html\n<b>Namaste!</b> Discipline pe focus.\n```", facts, fallback)
        self.assertTrue(result.ok, result.reason)
        self.assertFalse(result.text.startswith("```"))


if __name__ == "__main__":
    unittest.main()
