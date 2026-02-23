"""Integration tests for the Telegram adapter, stub, and handlers."""

from __future__ import annotations

import asyncio

import pytest

from foundation.messaging.adapter import CallbackResult, InlineButton
from foundation.testing.telegram_stub import StubAdapter


class TestStubAdapterSendEdit:
    async def test_send_message_returns_id(self) -> None:
        stub = StubAdapter()
        msg_id = await stub.send_message("hello")
        assert msg_id == "1"
        assert len(stub.sent_messages) == 1
        assert stub.sent_messages[0].text == "hello"

    async def test_send_increments_ids(self) -> None:
        stub = StubAdapter()
        id1 = await stub.send_message("first")
        id2 = await stub.send_message("second")
        assert id1 == "1"
        assert id2 == "2"

    async def test_send_with_buttons(self) -> None:
        stub = StubAdapter()
        buttons = [[InlineButton(text="OK", callback_data="ok")]]
        msg_id = await stub.send_message("choose", buttons=buttons)
        assert stub.sent_messages[0].buttons == buttons
        assert msg_id == "1"

    async def test_edit_message(self) -> None:
        stub = StubAdapter()
        msg_id = await stub.send_message("original")
        await stub.edit_message(msg_id, "updated")
        assert stub.sent_messages[0].text == "updated"
        assert stub.sent_messages[0].edited is True

    async def test_edit_nonexistent_raises(self) -> None:
        stub = StubAdapter()
        with pytest.raises(ValueError, match="not found"):
            await stub.edit_message("999", "text")


class TestStubAdapterCallbackInjection:
    async def test_inject_callback(self) -> None:
        stub = StubAdapter()
        msg_id = await stub.send_message(
            "pick one", buttons=[[InlineButton(text="A", callback_data="a")]]
        )

        async def wait_and_check() -> CallbackResult:
            return await stub.wait_for_callback(msg_id, timeout=5)

        async def inject_after_delay() -> None:
            await asyncio.sleep(0.05)
            injected = stub.inject_callback(msg_id, "a")
            assert injected, "inject_callback should find a waiting future"

        result, _ = await asyncio.gather(wait_and_check(), inject_after_delay())
        assert result.data == "a"
        assert result.message_id == msg_id

    async def test_callback_timeout(self) -> None:
        stub = StubAdapter()
        msg_id = await stub.send_message("pick one")
        with pytest.raises(TimeoutError):
            await stub.wait_for_callback(msg_id, timeout=0.05)


class TestStubAdapterReplyInjection:
    async def test_inject_reply(self) -> None:
        stub = StubAdapter()

        async def wait_and_check() -> str:
            return await stub.wait_for_reply(timeout=5)

        async def inject_after_delay() -> None:
            await asyncio.sleep(0.05)
            injected = stub.inject_reply("hello back")
            assert injected, "inject_reply should find a waiting future"

        result, _ = await asyncio.gather(wait_and_check(), inject_after_delay())
        assert result == "hello back"

    async def test_reply_timeout(self) -> None:
        stub = StubAdapter()
        with pytest.raises(TimeoutError):
            await stub.wait_for_reply(timeout=0.05)

    async def test_inject_callback_without_waiter_returns_false(self) -> None:
        stub = StubAdapter()
        assert stub.inject_callback("1", "data") is False

    async def test_inject_reply_without_waiter_returns_false(self) -> None:
        stub = StubAdapter()
        assert stub.inject_reply("nobody waiting") is False


class TestStubAdapterReset:
    async def test_reset_clears_state(self) -> None:
        stub = StubAdapter()
        await stub.send_message("first")
        await stub.send_message("second")
        stub.reset()
        assert len(stub.sent_messages) == 0
        msg_id = await stub.send_message("after reset")
        assert msg_id == "1"  # ID counter reset
