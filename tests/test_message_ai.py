"""AI message generation: deterministic fallback is always the safety net."""

import unittest
from unittest import mock

import config
import message_ai
from ai_provider import AIResponse

FACTS = {"symbol": "NIFTY", "net_points": -12.5, "count": 1}
FALLBACK = "<b>Aaj ka result</b>\nNet realized points: -12.50. Loss honestly report kiya gaya hai."


class _StubProvider:
    """Records calls so tests can prove zero-cost mode never dials out."""

    def __init__(self, response: AIResponse):
        self.response = response
        self.calls = 0
        self.name = "stub"
        self.model = "stub-model"
        self.available = True

    async def generate(self, system, user, *, temperature=None, max_tokens=None):
        self.calls += 1
        self.system = system
        self.user = user
        return self.response


def _stub(text: str = "", ok: bool = True, error: str = "") -> _StubProvider:
    return _StubProvider(AIResponse(ok=ok, text=text, provider="stub", model="stub-model", error=error))


class ZeroCostModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        message_ai.reset_ai_state()

    async def test_disabled_ai_returns_fallback_without_calling_provider(self):
        provider = _stub("some AI text")
        with mock.patch.object(config, "AI_ENABLED", False), \
             mock.patch.object(message_ai, "get_provider", return_value=provider):
            text = await message_ai.generate_message("pnl_report", FACTS, FALLBACK)
        self.assertEqual(text, FALLBACK)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(message_ai.ai_stats()["skipped_disabled"], 1)

    async def test_enabled_but_unavailable_provider_uses_fallback(self):
        provider = _stub("some AI text")
        provider.available = False
        with mock.patch.object(config, "AI_ENABLED", True), \
             mock.patch.object(message_ai, "get_provider", return_value=provider):
            text = await message_ai.generate_message("pnl_report", FACTS, FALLBACK)
        self.assertEqual(text, FALLBACK)
        self.assertEqual(provider.calls, 0)

    async def test_backward_compatible_entry_point(self):
        with mock.patch.object(config, "AI_ENABLED", False):
            text = await message_ai.optional_ai_message("morning_brief", {"x": 1}, "template text")
        self.assertEqual(text, "template text")


class AIEnabledTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        message_ai.reset_ai_state()
        self.patches = [
            mock.patch.object(config, "AI_ENABLED", True),
            mock.patch.object(config, "AI_MIN_INTERVAL_SECONDS", 0),
            mock.patch.object(config, "AI_DAILY_CALL_BUDGET", 50),
        ]
        for patch in self.patches:
            patch.start()

    async def asyncTearDown(self):
        for patch in self.patches:
            patch.stop()
        message_ai.reset_ai_state()

    async def test_safe_rephrase_is_published(self):
        provider = _stub("Aaj ka net result -12.50 points raha. Process discipline intact.")
        with mock.patch.object(message_ai, "get_provider", return_value=provider):
            text = await message_ai.generate_message("pnl_report", FACTS, FALLBACK)
        self.assertIn("-12.50", text)
        self.assertNotEqual(text, FALLBACK)
        self.assertEqual(message_ai.ai_stats()["published_ai"], 1)

    async def test_hallucinated_number_falls_back(self):
        provider = _stub("Aaj ka net result +999.00 points raha!")
        with mock.patch.object(message_ai, "get_provider", return_value=provider):
            text = await message_ai.generate_message("pnl_report", FACTS, FALLBACK)
        self.assertEqual(text, FALLBACK)
        self.assertEqual(message_ai.ai_stats()["guard_rejected"], 1)

    async def test_direction_advice_falls_back(self):
        facts = {"intent": "tip_request"}
        fallback = "Direction main nahi bataunga; engine confirm kare tabhi call jaayegi."
        provider = _stub("Bhai aaj to sell karo, gap down pakka hai.")
        with mock.patch.object(message_ai, "get_provider", return_value=provider):
            text = await message_ai.generate_message("reply_tip_request", facts, fallback)
        self.assertEqual(text, fallback)

    async def test_provider_error_falls_back(self):
        provider = _stub("", ok=False, error="timeout")
        with mock.patch.object(message_ai, "get_provider", return_value=provider):
            text = await message_ai.generate_message("pnl_report", FACTS, FALLBACK)
        self.assertEqual(text, FALLBACK)
        self.assertEqual(message_ai.ai_stats()["provider_error"], 1)

    async def test_daily_budget_stops_spending(self):
        provider = _stub("Aaj ka net result -12.50 points raha.")
        with mock.patch.object(config, "AI_DAILY_CALL_BUDGET", 1), \
             mock.patch.object(message_ai, "get_provider", return_value=provider):
            first = await message_ai.generate_message("pnl_report", FACTS, FALLBACK)
            second = await message_ai.generate_message("pnl_report", FACTS, FALLBACK)
        self.assertNotEqual(first, FALLBACK)
        self.assertEqual(second, FALLBACK)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(message_ai.ai_stats()["skipped_budget"], 1)

    async def test_prompt_carries_draft_and_rules(self):
        provider = _stub("Aaj ka net result -12.50 points raha.")
        with mock.patch.object(message_ai, "get_provider", return_value=provider):
            await message_ai.generate_message("pnl_report", FACTS, FALLBACK)
        self.assertIn("NEVER decide or suggest BUY or SELL", provider.system)
        self.assertIn("deterministic_draft", provider.user)


if __name__ == "__main__":
    unittest.main()
