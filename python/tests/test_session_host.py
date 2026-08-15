"""Host restart and read-only Session API tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aiohttp import ClientSession

from harness.agent import Message, Role, SessionId, UserInputAccepted
from harness.host import HarnessHost, HarnessHostConfig


class SessionHostTests(unittest.IsolatedAsyncioTestCase):
    """Exercise durable Session recovery through the assembled Host."""

    async def test_restart_recovers_history_and_route_is_read_only(self) -> None:
        """A second Host loads the same Session and rejects another Session ID."""
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "sessions.sqlite"
            first = HarnessHost(HarnessHostConfig(port=0, session_db=database))
            await first.start()
            first.invocations.log.append(
                UserInputAccepted(SessionId("default-turn"), (Message(Role.USER, "hello"),))
            )
            async with ClientSession() as client:
                response = await client.get(f"{first.base_url}/api/v1/sessions/default")
                self.assertEqual(response.status, 200)
                initial = await response.json()
                missing = await client.get(f"{first.base_url}/api/v1/sessions/other")
                self.assertEqual(missing.status, 404)
            await first.close()

            second = HarnessHost(HarnessHostConfig(port=0, session_db=database))
            await second.start()
            try:
                async with ClientSession() as client:
                    response = await client.get(f"{second.base_url}/api/v1/sessions/default")
                    self.assertEqual(response.status, 200)
                    restored = await response.json()
                self.assertEqual(restored, initial)
                self.assertEqual(restored["transcript"], [{"sequence": 1, "kind": "user", "content": "hello"}])
            finally:
                await second.close()


if __name__ == "__main__":
    unittest.main()
