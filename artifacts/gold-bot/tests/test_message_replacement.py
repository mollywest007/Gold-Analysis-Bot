import asyncio
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import message_replacement


class MessageReplacementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.temp_dir.name, "replaceable.json")
        self.bot = SimpleNamespace(delete_message=AsyncMock())
        self.state_patch = patch.object(message_replacement, "STATE_PATH", self.state_path)
        self.state_patch.start()

    async def asyncTearDown(self):
        self.state_patch.stop()
        self.temp_dir.cleanup()

    async def test_replaces_previous_messages_for_one_panel(self):
        await message_replacement.remember_message(
            123, "analyze", SimpleNamespace(message_id=10)
        )
        await message_replacement.remember_message(
            123, "analyze", SimpleNamespace(message_id=11)
        )
        await message_replacement.remember_message(
            123, "signal", SimpleNamespace(message_id=20)
        )

        await message_replacement.clear_previous(self.bot, 123, "analyze")

        self.assertEqual(
            self.bot.delete_message.await_args_list[0].kwargs,
            {"chat_id": 123, "message_id": 10},
        )
        self.assertEqual(
            self.bot.delete_message.await_args_list[1].kwargs,
            {"chat_id": 123, "message_id": 11},
        )
        with open(self.state_path) as file:
            state = json.load(file)
        self.assertNotIn("analyze", state["chats"]["123"])
        self.assertEqual(state["chats"]["123"]["signal"], [20])

    async def test_inline_refresh_keeps_current_message(self):
        await message_replacement.remember_message(
            123, "analyze", SimpleNamespace(message_id=10)
        )
        await message_replacement.remember_message(
            123, "analyze", SimpleNamespace(message_id=11)
        )

        await message_replacement.clear_previous(
            self.bot, 123, "analyze", keep_message_id=11
        )

        self.bot.delete_message.assert_awaited_once_with(
            chat_id=123, message_id=10
        )
        with open(self.state_path) as file:
            state = json.load(file)
        self.assertEqual(state["chats"]["123"]["analyze"], [11])

    async def test_state_survives_module_restart(self):
        await message_replacement.remember_message(
            123, "news", SimpleNamespace(message_id=77)
        )

        reloaded = __import__(
            "src.message_replacement", fromlist=["clear_previous"]
        )
        with patch.object(reloaded, "STATE_PATH", self.state_path):
            await reloaded.clear_previous(self.bot, 123, "news")

        self.bot.delete_message.assert_awaited_once_with(
            chat_id=123, message_id=77
        )


if __name__ == "__main__":
    unittest.main()