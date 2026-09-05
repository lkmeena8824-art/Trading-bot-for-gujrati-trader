"""Contextual human-style reply tests."""

import os
import tempfile
import unittest
from unittest import mock

import ai_guard
import community
import config
import database as database_module
import market_memory
import message_ai
import replies


class IntentTests(unittest.TestCase):
    def test_intents(self):
        cases = {
            "Hii bhai": "greeting",
            "Thank you so much": "thanks",
            "payment kar diya screenshot bhej du?": "payment",
            "VIP plan ka price kya hai": "vip",
            "bhai aaj kya lein batao": "tip_request",
            "mera sl hit ho gaya loss ho gaya": "loss",
            "target hit profit book kiya": "profit",
            "poll me vote kiya maine": "poll",
            "call kab aayegi": "timing",
            "nifty ka trend kya lag raha": "market",
            "ye sab fake hai": "complaint",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(replies.detect_intent(text), expected)

    def test_blank_and_unknown(self):
        self.assertEqual(replies.detect_intent(""), "unknown")
        self.assertEqual(replies.detect_intent("asdfgh zxcvb"), "unknown")


class DeterministicReplyTests(unittest.TestCase):
    def setUp(self):
        self.market = {
            "market_day": "2026-01-05",
            "phase": "IN_SESSION",
            "engine_read": "no confirmed A+ setup",
            "calls_today": 0,
            "no_trade_today": False,
            "net_points_today": 0,
        }
        self.crowd = {"voters_today": 12, "top_option": "🛡️ No Trade", "active_members_7d": 40}
        self.profile = {"known": True, "first_name": "Amit", "poll_votes": 5, "regular": True, "usual_mood": "cautious"}

    def _facts(self, intent):
        return replies.build_facts(intent, self.profile, self.market, self.crowd, user_id=101)

    def test_reply_never_gives_a_direction(self):
        for intent, _ in replies.INTENT_PATTERNS:
            with self.subTest(intent=intent):
                text = replies.compose_reply(self._facts(intent))
                self.assertEqual(ai_guard.direction_words(text), set(), text)

    def test_tip_request_refuses_and_explains_process(self):
        text = replies.compose_reply(self._facts("tip_request"))
        self.assertIn("nahi bata sakta", text)
        self.assertIn("engine", text.lower())

    def test_reply_uses_member_and_market_memory(self):
        text = replies.compose_reply(self._facts("greeting"))
        self.assertIn("Amit", text)
        self.assertTrue(
            "no confirmed A+ setup" in text or "sniper call" in text or "Patience" in text,
            text,
        )

    def test_poll_reply_uses_community_memory(self):
        text = replies.compose_reply(self._facts("poll"))
        self.assertIn("12", text)
        self.assertIn("🛡️ No Trade", text)

    def test_replies_vary_between_members_but_are_deterministic(self):
        first = replies.compose_reply(
            replies.build_facts("greeting", self.profile, self.market, self.crowd, user_id=1)
        )
        second = replies.compose_reply(
            replies.build_facts("greeting", self.profile, self.market, self.crowd, user_id=4)
        )
        again = replies.compose_reply(
            replies.build_facts("greeting", self.profile, self.market, self.crowd, user_id=1)
        )
        self.assertEqual(first, again)
        self.assertNotEqual(first, second)

    def test_facts_contain_no_trade_instruction(self):
        facts = self._facts("market")
        flat = str(facts).lower()
        for forbidden in ("entry", "stop loss", "target", "buy", "sell"):
            self.assertNotIn(forbidden, flat)


class ReplyPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self._original = database_module.DATABASE_PATH
        database_module.DATABASE_PATH = self.path
        self.db = database_module.db
        await self.db.connect()
        message_ai.reset_ai_state()
        replies.reset_cooldowns()

    async def asyncTearDown(self):
        await self.db.close()
        database_module.DATABASE_PATH = self._original
        os.unlink(self.path)
        replies.reset_cooldowns()

    async def test_zero_cost_reply_is_the_deterministic_template(self):
        with mock.patch.object(config, "AI_ENABLED", False):
            intent, text = await replies.build_reply(501, "bhai aaj kya lein?", first_name="Ravi")
        self.assertEqual(intent, "tip_request")
        self.assertIn("Ravi", text)
        self.assertEqual(ai_guard.direction_words(text), set())

    async def test_memory_is_updated_by_a_reply(self):
        with mock.patch.object(config, "AI_ENABLED", False):
            await replies.build_reply(502, "hello bhai", first_name="Sneha")
        profile = await community.member_profile(502)
        self.assertEqual(profile["messages"], 1)
        self.assertEqual(profile["last_intent"], "greeting")

    async def test_reply_mentions_today_market_memory(self):
        await market_memory.remember_trade_posted({"symbol": "NIFTY", "slot": "FIRST"}, 1)
        with mock.patch.object(config, "AI_ENABLED", False):
            _, text = await replies.build_reply(503, "nifty ka view kya hai", first_name="Kiran")
        self.assertIn("1 call", text)

    async def test_ai_polish_is_guarded(self):
        class _Provider:
            name = "stub"
            model = "stub"
            available = True

            async def generate(self, system, user, *, temperature=None, max_tokens=None):
                from ai_provider import AIResponse

                return AIResponse(ok=True, text="Bhai aaj BUY kar le, easy paisa.", provider="stub", model="stub")

        with mock.patch.object(config, "AI_ENABLED", True), \
             mock.patch.object(config, "AI_MIN_INTERVAL_SECONDS", 0), \
             mock.patch.object(message_ai, "get_provider", return_value=_Provider()):
            _, text = await replies.build_reply(504, "kya lein aaj?", first_name="Manish")
        self.assertNotIn("BUY", text)
        self.assertIn("nahi bata sakta", text)

    async def test_cooldown_and_daily_cap(self):
        with mock.patch.object(config, "REPLY_COOLDOWN_SECONDS", 0), \
             mock.patch.object(config, "REPLY_MAX_PER_USER_PER_DAY", 2):
            self.assertTrue(await replies.should_reply(505))
            self.assertTrue(await replies.should_reply(505))
            self.assertFalse(await replies.should_reply(505))

        replies.reset_cooldowns()
        with mock.patch.object(config, "REPLY_COOLDOWN_SECONDS", 300), \
             mock.patch.object(config, "REPLY_MAX_PER_USER_PER_DAY", 10):
            self.assertTrue(await replies.should_reply(506))
            self.assertFalse(await replies.should_reply(506))

    async def test_replies_can_be_switched_off(self):
        with mock.patch.object(config, "REPLY_ENABLED", False):
            self.assertFalse(await replies.should_reply(507))


if __name__ == "__main__":
    unittest.main()
