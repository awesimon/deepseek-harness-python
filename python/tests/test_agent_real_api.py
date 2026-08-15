"""Optional bounded DeepSeek API smoke test for the assembled Host."""

from __future__ import annotations

import os
import unittest

from aiohttp import ClientSession

from harness.agent import DeepSeekHTTPConfig
from harness.host import HarnessHost, HarnessHostConfig

_API_KEY = os.environ.get("DEEPSEEK_API_KEY")


@unittest.skipUnless(_API_KEY, "DEEPSEEK_API_KEY is not configured")
class DeepSeekRealAPITests(unittest.IsolatedAsyncioTestCase):
    """Run one no-Tool Turn only when real-API credentials are explicit."""

    async def test_bounded_host_invocation(self) -> None:
        """The production adapter completes one Turn without exposing its key."""
        assert _API_KEY is not None
        provider = DeepSeekHTTPConfig(
            "deepseek",
            os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            _API_KEY,
            request_timeout=60,
        )
        self.assertNotIn(_API_KEY, repr(provider))
        async with HarnessHost(
            HarnessHostConfig(port=0, deepseek=provider, max_steps=1)
        ) as host, ClientSession() as client:
            response = await client.post(
                f"{host.base_url}/api/v1/agent/invocations/real-api-smoke",
                json={"input": "Reply with one short greeting."},
            )
            body = await response.text()
        self.assertEqual(response.status, 200, body)
        self.assertNotIn(_API_KEY, body)


if __name__ == "__main__":
    unittest.main()
