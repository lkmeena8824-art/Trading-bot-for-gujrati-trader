"""Engine integration tests: Telegram behaviour must survive Phase 2."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import config
import database as database_module
import engine
import message_ai
from messaging import fmt_morning
from telegram.ext import CommandHandler, MessageHandler, PollAnswerHandler


class HandlerRegistryTests(unittest.TestCase):
    def test_existing_handlers_are_preserved(self):
        commands = set()
        for handler in engine.get_all_handlers():
            if isinstance(handler, CommandHandler):
                commands.update(handler.commands)
        self.assertTrue({"start", "plans", "refer", "addvip", "forcecall"}.issubset(commands))
        self.assertIn("aistatus", commands)

    def test_poll_answers_are_tracked(self):
        self.assertTrue(any(isinstance(h, PollAnswerHandler) for h in engine.get_all_handlers()))

    def test_contextual_replies_run_in_their_own_group(self):
        groups = engine.get_handler_groups()
        self.assertTrue(all(group == 0 for _, group in groups[:-1]))
        handler, group = groups[-1]
        self.assertEqual(group, 1)
        self.assertIsInstance(handler, MessageHandler)
        self.assertIs(handler.callback, engine.contextual_reply)

    def test_scheduler_registry_is_unchanged(self):
        jobs = engine.get_scheduler_jobs()
        expected = {
            "morning", "poll", "premarket", "open_pulse", "scanner", "no_trade",
            "btst", "oi", "closing", "pnl", "promo", "expiry", "monitor",
        }
        self.assertEqual(expected, set(jobs))


class JobBehaviourTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self._original = database_module.DATABASE_PATH
        database_module.DATABASE_PATH = self.path
        self.db = database_module.db
        await self.db.connect()
        message_ai.reset_ai_state()
        self.sent: list[tuple[int, str]] = []

        async def fake_send(bot, cid, text, rm=None):
            self.sent.append((cid, text))
            return 1

        self.send_patch = mock.patch.object(engine, "safe_send", side_effect=fake_send)
        self.send_patch.start()
        self.ai_patch = mock.patch.object(config, "AI_ENABLED", False)
        self.ai_patch.start()

    async def asyncTearDown(self):
        self.ai_patch.stop()
        self.send_patch.stop()
        await self.db.close()
        database_module.DATABASE_PATH = self._original
        os.unlink(self.path)

    async def test_morning_job_posts_template_and_stores_memory(self):
        data = {"india_vix": 13.2, "gift_nifty": 22500.0, "date": "2026-01-05", "source": "YAHOO_FINANCE_FREE"}
        app = SimpleNamespace(bot=object())
        with mock.patch.object(engine.market_data, "morning_data", new=mock.AsyncMock(return_value=data)):
            await engine.job_morning(app)

        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1], fmt_morning(data))  # zero-cost text is byte-identical
        state = await self.db.get_market_memory()
        self.assertEqual(state["phase"], "PRE_OPEN")
        self.assertAlmostEqual(state["india_vix"], 13.2)

    async def test_poll_job_persists_poll_and_answers_are_attributed(self):
        poll = SimpleNamespace(id="poll-xyz")
        message = SimpleNamespace(poll=poll, message_id=42)
        bot = SimpleNamespace(send_poll=mock.AsyncMock(return_value=message))
        app = SimpleNamespace(bot=bot)

        await engine.job_hype_poll(app)
        bot.send_poll.assert_awaited_once()
        stored = await self.db.get_poll("poll-xyz")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["message_id"], 42)

        update = SimpleNamespace(
            poll_answer=SimpleNamespace(
                poll_id="poll-xyz",
                option_ids=[1],
                user=SimpleNamespace(id=900, username="u", first_name="Utsav"),
            )
        )
        await engine.on_poll_answer(update, None)
        tally = await self.db.get_poll_tally("poll-xyz")
        self.assertEqual(tally, {"🔴 Defensive Short": 1})

    async def test_contextual_reply_answers_a_private_message(self):
        replies_sent = []

        async def fake_reply_text(text, **kwargs):
            replies_sent.append(text)

        message = SimpleNamespace(
            text="bhai aaj kya lein?",
            caption=None,
            reply_to_message=None,
            reply_text=fake_reply_text,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=901, username="u", first_name="Nikhil", is_bot=False),
            effective_chat=SimpleNamespace(id=901, type="private"),
        )
        context = SimpleNamespace(bot=SimpleNamespace(username="bot", id=5))

        engine.replies.reset_cooldowns()
        await engine.contextual_reply(update, context)
        self.assertEqual(len(replies_sent), 1)
        self.assertIn("Nikhil", replies_sent[0])
        self.assertIn("nahi bata sakta", replies_sent[0])

    async def test_group_messages_are_ignored_unless_enabled(self):
        replies_sent = []

        async def fake_reply_text(text, **kwargs):
            replies_sent.append(text)

        message = SimpleNamespace(
            text="hello", caption=None, reply_to_message=None, reply_text=fake_reply_text
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=902, username="u", first_name="Gopi", is_bot=False),
            effective_chat=SimpleNamespace(id=-100123, type="supergroup"),
        )
        context = SimpleNamespace(bot=SimpleNamespace(username="bot", id=5))

        engine.replies.reset_cooldowns()
        with mock.patch.object(config, "REPLY_IN_GROUPS", False):
            await engine.contextual_reply(update, context)
        self.assertEqual(replies_sent, [])

    async def test_commands_are_never_answered_by_the_reply_layer(self):
        replies_sent = []

        async def fake_reply_text(text, **kwargs):
            replies_sent.append(text)

        message = SimpleNamespace(
            text="/plans", caption=None, reply_to_message=None, reply_text=fake_reply_text
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=903, username="u", first_name="Jay", is_bot=False),
            effective_chat=SimpleNamespace(id=903, type="private"),
        )
        context = SimpleNamespace(bot=SimpleNamespace(username="bot", id=5))

        engine.replies.reset_cooldowns()
        await engine.contextual_reply(update, context)
        self.assertEqual(replies_sent, [])


if __name__ == "__main__":
    unittest.main()
