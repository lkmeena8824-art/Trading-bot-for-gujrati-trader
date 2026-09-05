"""Poll-answer tracking and community memory tests (real SQLite, temp file)."""

import os
import tempfile
import unittest

import community
import database as database_module
from quality_control import market_day


class CommunityMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self._original = database_module.DATABASE_PATH
        database_module.DATABASE_PATH = self.path
        self.db = database_module.db
        await self.db.connect()
        self.day = market_day()

    async def asyncTearDown(self):
        await self.db.close()
        database_module.DATABASE_PATH = self._original
        os.unlink(self.path)

    async def _seed_poll(self, poll_id="poll-1"):
        await community.remember_poll(
            poll_id=poll_id,
            question="🧠 AAJ KA SMART MONEY SENTIMENT?",
            options=["🟢 Aggressive Long", "🔴 Defensive Short", "🟡 Sideways Trap", "🛡️ No Trade"],
            chat_id=-1001,
            message_id=55,
        )

    async def test_poll_answer_is_attributed_and_tallied(self):
        await self._seed_poll()
        result = await community.record_answer("poll-1", 101, [0], username="a", first_name="Amit")
        self.assertEqual(result["options"], ["🟢 Aggressive Long"])
        self.assertEqual(result["mood"], "bullish")

        await community.record_answer("poll-1", 102, [3], first_name="Bhavesh")
        await community.record_answer("poll-1", 103, [0], first_name="Chirag")

        tally = await self.db.get_poll_tally("poll-1")
        self.assertEqual(tally["🟢 Aggressive Long"], 2)
        self.assertEqual(tally["🛡️ No Trade"], 1)

    async def test_changed_vote_replaces_previous_answer(self):
        await self._seed_poll()
        await community.record_answer("poll-1", 101, [0], first_name="Amit")
        await community.record_answer("poll-1", 101, [1], first_name="Amit")
        answers = await self.db.get_poll_answers("poll-1")
        self.assertEqual(len(answers), 1)
        tally = await self.db.get_poll_tally("poll-1")
        self.assertEqual(tally, {"🔴 Defensive Short": 1})

    async def test_retracted_vote_is_excluded(self):
        await self._seed_poll()
        await community.record_answer("poll-1", 101, [0], first_name="Amit")
        await community.record_answer("poll-1", 101, [], first_name="Amit")
        self.assertEqual(await self.db.get_poll_answers("poll-1"), [])

    async def test_member_profile_builds_usual_mood(self):
        await self._seed_poll("poll-a")
        await self._seed_poll("poll-b")
        await self._seed_poll("poll-c")
        await community.record_answer("poll-a", 200, [0], first_name="Dhruv")
        await community.record_answer("poll-b", 200, [0], first_name="Dhruv")
        await community.record_answer("poll-c", 200, [2], first_name="Dhruv")

        profile = await community.member_profile(200)
        self.assertTrue(profile["known"])
        self.assertEqual(profile["usual_mood"], "bullish")
        self.assertEqual(profile["first_name"], "Dhruv")
        self.assertTrue(profile["voted_today"])

    async def test_unknown_member_profile_is_safe(self):
        profile = await community.member_profile(999999)
        self.assertFalse(profile["known"])
        self.assertEqual(profile["poll_votes"], 0)

    async def test_snapshot_reports_crowd_choice(self):
        await self._seed_poll()
        await community.record_answer("poll-1", 301, [1], first_name="E")
        await community.record_answer("poll-1", 302, [1], first_name="F")
        await community.record_answer("poll-1", 303, [2], first_name="G")

        snapshot = await community.snapshot()
        self.assertEqual(snapshot["voters_today"], 3)
        self.assertEqual(snapshot["top_option"], "🔴 Defensive Short")
        self.assertEqual(snapshot["top_mood"], "bearish")
        self.assertIn("3", community.describe_snapshot(snapshot))

    async def test_empty_snapshot_is_honest(self):
        snapshot = await community.snapshot()
        self.assertEqual(snapshot["voters_today"], 0)
        self.assertIn("koi vote", community.describe_snapshot(snapshot))

    async def test_message_memory_counts_and_reply_budget(self):
        await community.note_message(400, "greeting", first_name="Hetal")
        await community.note_message(400, "market", first_name="Hetal")
        profile = await community.member_profile(400)
        self.assertEqual(profile["messages"], 2)
        self.assertEqual(profile["last_intent"], "market")

        first = await self.db.register_reply(400, self.day)
        second = await self.db.register_reply(400, self.day)
        self.assertEqual((first, second), (1, 2))
        self.assertEqual(await self.db.register_reply(400, "1999-01-01"), 1)


if __name__ == "__main__":
    unittest.main()
