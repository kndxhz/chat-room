from __future__ import annotations

import asyncio

import main as chat_main
import websockets

from conftest import FakeWebSocket


async def read_until(websocket, predicate, *, limit=10, timeout=1):
    messages = []
    for _ in range(limit):
        message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
        messages.append(message)
        if predicate(message, messages):
            return messages
    raise AssertionError(f"未在 {limit} 条消息内匹配到目标消息: {messages}")


async def drain_history(websocket):
    return await read_until(
        websocket,
        lambda message, _: message
        == f"{chat_main.SYSTEM_PREFIX}----以上是历史记录----",
    )


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
            message == f"{chat_main.SYSTEM_PREFIX}1.2.3.4 被封禁"
            for message in command_client.sent
        )
        assert any(
            message == f"{chat_main.SYSTEM_PREFIX}已清空聊天记录"
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
        assert target.close_code == 4001
        assert target.close_reason == "请重新设定昵称"
        assert any(
            message == f"{chat_main.SYSTEM_PREFIX}Alice 被踢出"
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


def test_ws_client_roundtrip_for_text_and_image_messages(main_env):
    async def scenario():
        chat_main.connected_clients.clear()

        async with websockets.serve(chat_main.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            uri = f"ws://127.0.0.1:{port}"

            async with (
                websockets.connect(uri) as receiver,
                websockets.connect(uri) as sender,
            ):
                await drain_history(receiver)
                await drain_history(sender)

                await receiver.send("/set-name Bob")
                await sender.send("/set-name Alice")
                await asyncio.sleep(0.05)

                await sender.send("hello from ws client")
                sender_messages = await read_until(
                    sender, lambda message, _: "hello from ws client" in message
                )
                receiver_messages = await read_until(
                    receiver, lambda message, _: "hello from ws client" in message
                )

                await sender.send("![img](http://example.com/pic.png)")
                image_messages = await read_until(
                    receiver,
                    lambda message, _: "![img](http://example.com/pic.png)" in message,
                )

        assert any("Alice：[[msg:" in message for message in sender_messages)
        assert any("Alice：[[msg:" in message for message in receiver_messages)
        assert any("![img](http://example.com/pic.png)" in message for message in image_messages)
        stored = chat_main.get_recent_raw_messages()
        assert any("hello from ws client" in item for item in stored)
        assert any("![img](http://example.com/pic.png)" in item for item in stored)

    asyncio.run(scenario())


def test_ws_client_commands_cover_real_command_flow(main_env, monkeypatch):
    async def scenario():
        chat_main.connected_clients.clear()
        monkeypatch.setattr(chat_main.random, "randint", lambda a, b: 12345)

        dns_updates = []
        monkeypatch.setattr(
            chat_main,
            "update_cloudflare_dns",
            lambda ip: dns_updates.append(ip),
        )
        monkeypatch.setattr(chat_main, "get_host_ip", lambda: "9.9.9.9")

        (main_env.upload_dir / "delete.txt").write_text("x", encoding="utf-8")
        (main_env.upload_dir / "keep.txt").write_text("y", encoding="utf-8")

        async with websockets.serve(chat_main.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            uri = f"ws://127.0.0.1:{port}"

            async with (
                websockets.connect(uri) as target,
                websockets.connect(uri) as admin,
            ):
                await drain_history(target)
                await drain_history(admin)

                await target.send("/set-name Alice")
                await admin.send("/set-name Admin")
                await asyncio.sleep(0.05)

                await admin.send("/change-key")
                await read_until(
                    admin,
                    lambda message, _: message
                    == f"{chat_main.SYSTEM_PREFIX}已更新秘钥",
                )
                assert main_env.key_file.read_text(encoding="utf-8").strip() == "12345"

                await admin.send("/list")
                list_messages = await read_until(
                    admin,
                    lambda message, _: message.startswith(
                        f"{chat_main.SYSTEM_PREFIX}当前在线的客户端:"
                    ),
                )
                assert any("Alice" in message for message in list_messages)

                await admin.send("/del delete.txt")
                await asyncio.sleep(0.05)
                assert not (main_env.upload_dir / "delete.txt").exists()

                await admin.send("/del missing.txt")
                await read_until(
                    admin,
                    lambda message, _: message
                    == f"{chat_main.SYSTEM_PREFIX}文件不存在: missing.txt",
                )

                await admin.send("/del-all-files")
                await read_until(
                    admin,
                    lambda message, _: message
                    == f"{chat_main.SYSTEM_PREFIX}已删除所有文件",
                )
                assert list(main_env.upload_dir.iterdir()) == []

                await admin.send("/update-dns")
                await read_until(
                    admin,
                    lambda message, _: message
                    == f"{chat_main.SYSTEM_PREFIX}已更新 DNS 记录",
                )
                assert dns_updates == ["9.9.9.9"]

                await admin.send("/ban 8.8.8.8 12345")
                await read_until(
                    admin,
                    lambda message, _: message
                    == f"{chat_main.SYSTEM_PREFIX}8.8.8.8 被封禁",
                )
                assert "8.8.8.8" in main_env.ban_file.read_text(encoding="utf-8")

                await admin.send("/unban 8.8.8.8 12345")
                await asyncio.sleep(0.05)
                assert "8.8.8.8" not in main_env.ban_file.read_text(encoding="utf-8")

                await admin.send("message before clear")
                await read_until(
                    admin, lambda message, _: "message before clear" in message
                )
                assert any(
                    "message before clear" in item
                    for item in chat_main.get_recent_raw_messages()
                )

                await admin.send("/clear")
                await read_until(
                    admin,
                    lambda message, _: message
                    == f"{chat_main.SYSTEM_PREFIX}已清空聊天记录",
                )
                assert chat_main.get_recent_raw_messages() == []

                await admin.send("/kick Alice name 12345")
                await read_until(
                    admin,
                    lambda message, _: message
                    == f"{chat_main.SYSTEM_PREFIX}Alice 被踢出",
                )
                await asyncio.wait_for(target.wait_closed(), timeout=1)
                assert target.close_code == 4001
                assert target.close_reason == "请重新设定昵称"

    asyncio.run(scenario())
