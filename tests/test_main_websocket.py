from __future__ import annotations

import asyncio

import main as chat_main

from conftest import FakeWebSocket


def test_handler_rejects_invalid_and_duplicate_names(main_env):
    async def scenario():
        other = FakeWebSocket("10.0.0.2", 2002)
        other.name = "Alice"
        chat_main.connected_clients.add(other)

        invalid = FakeWebSocket("10.0.0.3", 2003, ["/set-name bad name"])
        await chat_main.handler(invalid)
        assert any(
            message.startswith(chat_main.SYSTEM_ALERT_PREFIX)
            for message in invalid.sent
        )

        duplicate = FakeWebSocket("10.0.0.4", 2004, ["/set-name Alice"])
        await chat_main.handler(duplicate)
        assert any(
            message.startswith(chat_main.SYSTEM_ALERT_PREFIX)
            for message in duplicate.sent
        )

    asyncio.run(scenario())


def test_handler_command_branches(main_env, monkeypatch):
    async def scenario():
        chat_main.connected_clients.clear()
        peer = FakeWebSocket("10.0.0.6", 2006)
        peer.name = "Peer"
        chat_main.connected_clients.add(peer)

        main_env.ban_file.write_text("1.2.3.4\n", encoding="utf-8")
        monkeypatch.setattr(chat_main.random, "randint", lambda a, b: 12345)

        close_calls = []

        async def fake_close_websocket_by_ip(ip):
            close_calls.append(ip)

        monkeypatch.setattr(
            chat_main, "close_websocket_by_ip", fake_close_websocket_by_ip
        )
        monkeypatch.setattr(chat_main, "get_host_ip", lambda: "9.9.9.9")
        monkeypatch.setattr(
            chat_main,
            "update_cloudflare_dns",
            lambda ip: close_calls.append(f"dns:{ip}"),
        )

        command_client = FakeWebSocket(
            "10.0.0.5",
            2005,
            [
                "/change-key",
                "/list",
                "/del missing.txt",
                "/del-all-files",
                "/ban 1.2.3.4 12345",
                "/unban 1.2.3.4 12345",
                "/clear",
            ],
        )
        await chat_main.handler(command_client)

        assert main_env.key_file.read_text(encoding="utf-8").strip()
        assert any(
            message.startswith(chat_main.SYSTEM_PREFIX + "当前在线的客户端:")
            for message in command_client.sent
        )
        assert any(
            message.startswith(chat_main.SYSTEM_PREFIX + "文件不存在: missing.txt")
            for message in command_client.sent
        )
        assert any(
            message == f"{chat_main.SYSTEM_PREFIX}已删除所有文件"
            for message in command_client.sent
        )
        assert any(
            message == f"{chat_main.SYSTEM_PREFIX}1.2.3.4 被 ban"
            for message in command_client.sent
        )
        assert main_env.ban_file.read_text(encoding="utf-8").strip() == ""
        assert "dns:9.9.9.9" not in close_calls

    asyncio.run(scenario())


def test_handler_kick_command_uses_name_and_key(main_env):
    async def scenario():
        chat_main.connected_clients.clear()

        target = FakeWebSocket("10.0.0.9", 2009)
        target.name = "Alice"
        chat_main.connected_clients.add(target)

        admin = FakeWebSocket(
            "10.0.0.5",
            2005,
            ["/kick Alice name 12345"],
        )
        await chat_main.handler(admin)

        assert target.closed is True
        assert any(
            message == f"{chat_main.SYSTEM_ALERT_PREFIX}请重新设定昵称"
            for message in target.sent
        )
        assert any(
            message == f"{chat_main.SYSTEM_PREFIX}Alice 被 kick"
            for message in admin.sent
        )

    asyncio.run(scenario())


def test_handler_broadcasts_message_and_persists_history(main_env):
    async def scenario():
        chat_main.connected_clients.clear()
        receiver = FakeWebSocket("10.0.0.8", 2008)
        receiver.name = "Receiver"
        chat_main.connected_clients.add(receiver)

        sender = FakeWebSocket("10.0.0.7", 2007, ["/set-name Alice", "hello world"])
        await chat_main.handler(sender)

        stored = chat_main.get_recent_raw_messages()
        assert stored
        assert stored[-1].startswith("Alice：[[msg:")
        assert any(
            "Alice：[[msg:" in message and "hello world" in message
            for message in sender.sent
        )
        assert any(
            "Alice：[[msg:" in message and "hello world" in message
            for message in receiver.sent
        )

    asyncio.run(scenario())


def test_websocket_roundtrip_with_handler(main_env):
    async def scenario():
        chat_main.connected_clients.clear()
        peer = FakeWebSocket("10.0.0.8", 2008)
        peer.name = "Peer"
        chat_main.connected_clients.add(peer)

        client = FakeWebSocket("10.0.0.7", 2007, ["/set-name Alice", "hello from ws"])
        await chat_main.handler(client)

        assert any(
            message.startswith(chat_main.SYSTEM_PREFIX + "----以上是历史记录----")
            for message in client.sent
        )
        assert any(
            "Alice：[[msg:" in message and "hello from ws" in message
            for message in peer.sent
        )
        assert any(
            "Alice：[[msg:" in message and "hello from ws" in message
            for message in client.sent
        )

    asyncio.run(scenario())
