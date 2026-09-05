"""Provider abstraction tests (OpenAI + Gemini), without any network call."""

import unittest
from unittest import mock

import config
from ai_provider import (
    AIResponse,
    DisabledProvider,
    GeminiProvider,
    OpenAIProvider,
    build_provider,
    get_provider,
    provider_status,
    reset_provider_cache,
)


class ProviderSelectionTests(unittest.TestCase):
    def setUp(self):
        reset_provider_cache()

    def tearDown(self):
        reset_provider_cache()

    def test_zero_cost_mode_selects_disabled_provider(self):
        with mock.patch.object(config, "AI_ENABLED", False), \
             mock.patch.object(config, "AI_PROVIDER", "openai"), \
             mock.patch.object(config, "OPENAI_API_KEY", "sk-test"):
            provider = get_provider()
        self.assertIsInstance(provider, DisabledProvider)
        self.assertFalse(provider.available)

    def test_openai_selected_when_enabled_with_key(self):
        with mock.patch.object(config, "AI_ENABLED", True), \
             mock.patch.object(config, "AI_PROVIDER", "openai"), \
             mock.patch.object(config, "OPENAI_API_KEY", "sk-test"):
            provider = get_provider()
        self.assertIsInstance(provider, OpenAIProvider)
        self.assertTrue(provider.available)

    def test_gemini_selected_when_enabled_with_key(self):
        with mock.patch.object(config, "AI_ENABLED", True), \
             mock.patch.object(config, "AI_PROVIDER", "gemini"), \
             mock.patch.object(config, "GEMINI_API_KEY", "g-test"):
            provider = get_provider()
        self.assertIsInstance(provider, GeminiProvider)

    def test_auto_prefers_openai_then_gemini(self):
        with mock.patch.object(config, "AI_ENABLED", True), \
             mock.patch.object(config, "AI_PROVIDER", "auto"), \
             mock.patch.object(config, "OPENAI_API_KEY", ""), \
             mock.patch.object(config, "GEMINI_API_KEY", "g-test"):
            self.assertEqual(config.active_ai_provider(), "gemini")
        with mock.patch.object(config, "AI_ENABLED", True), \
             mock.patch.object(config, "AI_PROVIDER", "auto"), \
             mock.patch.object(config, "OPENAI_API_KEY", "sk-test"), \
             mock.patch.object(config, "GEMINI_API_KEY", "g-test"):
            self.assertEqual(config.active_ai_provider(), "openai")

    def test_enabled_without_any_key_stays_zero_cost(self):
        with mock.patch.object(config, "AI_ENABLED", True), \
             mock.patch.object(config, "AI_PROVIDER", "openai"), \
             mock.patch.object(config, "OPENAI_API_KEY", ""):
            self.assertEqual(config.active_ai_provider(), "none")
            self.assertFalse(provider_status()["available"])


class RequestShapeTests(unittest.TestCase):
    def test_openai_request_and_parse(self):
        provider = OpenAIProvider(model="gpt-4o-mini", api_key="sk-test", base_url="https://api.openai.com/v1")
        url, headers, payload = provider.build_request("SYSTEM", "USER", 0.3, 200)
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["content"], "USER")
        self.assertEqual(payload["max_tokens"], 200)
        parsed = provider.parse_response({"choices": [{"message": {"content": "hello"}}]})
        self.assertEqual(parsed, "hello")

    def test_gemini_request_and_parse(self):
        provider = GeminiProvider(
            model="gemini-1.5-flash",
            api_key="g-test",
            base_url="https://generativelanguage.googleapis.com/v1beta",
        )
        url, headers, payload = provider.build_request("SYSTEM", "USER", 0.3, 200)
        self.assertTrue(url.endswith("/models/gemini-1.5-flash:generateContent"))
        self.assertEqual(headers["x-goog-api-key"], "g-test")
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "USER")
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 200)
        parsed = provider.parse_response(
            {"candidates": [{"content": {"parts": [{"text": "hel"}, {"text": "lo"}]}}]}
        )
        self.assertEqual(parsed, "hello")

    def test_disabled_provider_never_builds_requests(self):
        provider = build_provider("none")
        self.assertIsInstance(provider, DisabledProvider)
        with self.assertRaises(RuntimeError):
            provider.build_request("s", "u", 0.1, 10)


class GenerateTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_generation(self):
        provider = OpenAIProvider(model="m", api_key="sk-test", base_url="http://x")
        with mock.patch.object(
            OpenAIProvider,
            "_post_json",
            new=mock.AsyncMock(return_value={"choices": [{"message": {"content": " hi "}}]}),
        ):
            response = await provider.generate("s", "u")
        self.assertTrue(response.ok)
        self.assertEqual(response.text, "hi")
        self.assertEqual(response.provider, "openai")

    async def test_http_failure_is_soft(self):
        provider = GeminiProvider(model="m", api_key="g", base_url="http://x")
        with mock.patch.object(
            GeminiProvider, "_post_json", new=mock.AsyncMock(side_effect=RuntimeError("http_500:boom"))
        ):
            response = await provider.generate("s", "u")
        self.assertFalse(response.ok)
        self.assertIn("http_500", response.error)

    async def test_missing_key_short_circuits(self):
        provider = OpenAIProvider(model="m", api_key="", base_url="http://x")
        response = await provider.generate("s", "u")
        self.assertEqual(response, AIResponse(ok=False, provider="openai", model="m", error="missing_api_key"))


if __name__ == "__main__":
    unittest.main()
