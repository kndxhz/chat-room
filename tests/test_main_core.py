from __future__ import annotations

import asyncio
import io
import socket
from pathlib import Path

import main as chat_main

from conftest import FakeWebSocket


def test_parse_reply_meta_decodes_payload(main_env):
    meta = chat_main.parse_reply_meta(
        "[[reply:%E5%BC%A0%E4%B8%89|%E4%BD%A0%E5%A5%BD|abc123]]\n正文"
    )

    assert meta == {
        "reply_to": "张三",
        "preview": "你好",
        "target_msgid": "abc123",
        "body": "正文",
    }


def test_parse_reply_meta_and_history_filters(main_env):
    assert chat_main.parse_reply_meta("plain text") is None

    chat_main.save_message("Alice", "hello")
    chat_main.save_message("Bob", "kick Alice")
    chat_main.save_message("Carol", "world")

    recent = chat_main.get_recent_raw_messages()
    assert len(recent) == 2
    assert any("hello" in msg for msg in recent)
    assert any("world" in msg for msg in recent)
    assert all("kick" not in msg for msg in recent)


def test_username_validation(main_env):
    assert chat_main.is_valid_username("Alice_123")
    assert chat_main.is_valid_username("张三")
    assert not chat_main.is_valid_username("bad name")
    assert not chat_main.is_valid_username("")


def test_message_storage_helpers(main_env):
    msg_id, raw_message = chat_main.save_message("Alice", "hello\nworld")

    assert raw_message.startswith("Alice：[[msg:")
    assert chat_main.get_recent_raw_messages() == [raw_message]

    stored = chat_main.get_message_by_msg_id(msg_id)
    assert stored is not None
    assert stored["msg_id"] == msg_id
    assert stored["sender"] == "Alice"
    assert stored["message"] == raw_message

    chat_main.clear_history_messages()
    assert chat_main.get_recent_raw_messages() == []


def test_connection_storage_helpers(main_env):
    client_a = FakeWebSocket("10.0.0.8", 1111)
    client_a.name = "Alice"
    client_b = FakeWebSocket("10.0.0.9", 2222)
    client_b.name = "Bob"
    chat_main.connected_clients.update({client_a, client_b})

    chat_main.sync_connections_to_db()

    assert chat_main.get_nickname_by_ip("10.0.0.8") == "Alice"
    assert chat_main.get_nickname_by_ip("10.0.0.9") == "Bob"
    assert chat_main.get_connection_rows() == [
        ("10.0.0.8", "Alice"),
        ("10.0.0.9", "Bob"),
    ]


def test_flask_routes(main_env, monkeypatch):
    client = chat_main.app.test_client()

    response = client.post("/upload")
    assert response.status_code == 400

    response = client.post(
        "/upload",
        data={"file": (io.BytesIO(b""), "")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": "文件名为空"}

    response = client.post(
        "/upload",
        data={"file": (io.BytesIO(b"hello"), "a b.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert (main_env.upload_dir / "ab.txt").exists()

    response = client.get("/file_list")
    assert response.status_code == 200
    assert "ab.txt" in response.get_json()

    response = client.post(
        "/upload?is_img=1",
        data={"file": (io.BytesIO(b"img"), "pic.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert (main_env.image_dir / "pic.png").exists()

    recorded = []

    def fake_log_event(*args, **kwargs):
        recorded.append((args, kwargs))

    monkeypatch.setattr(chat_main, "log_event", fake_log_event)
    monkeypatch.setattr(
        chat_main,
        "send_file",
        lambda *args, **kwargs: chat_main.app.response_class(
            b"downloaded", mimetype="application/octet-stream"
        ),
    )

    response = client.get("/download/ab.txt")
    assert response.data == b"downloaded"
    assert recorded

    chat_main.BING_INFO = "title\nplace\ncopy"
    response = client.get("/get_bing_info")
    assert response.status_code == 200
    assert response.get_json() == {"info": "title\nplace\ncopy"}

    response = client.get("/get_message_by_id/unknown")
    assert response.status_code == 404
    assert response.get_json() == {"found": False}

    msg_id, raw_message = chat_main.save_message("Alice", "hello")
    response = client.get(f"/get_message_by_id/{msg_id}")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["found"] is True
    assert payload["msg_id"] == msg_id
    assert payload["message"] == raw_message

    chat_main.connected_clients.clear()
    client_a = FakeWebSocket("10.0.0.8", 1111)
    client_a.name = "Alice"
    chat_main.connected_clients.add(client_a)
    response = client.get("/get_online_user")
    assert response.status_code == 200
    assert response.get_json() == {"users": ["Alice"]}


def test_download_file_missing_and_bing_failure(main_env, monkeypatch):
    client = chat_main.app.test_client()

    response = client.get("/download/missing.txt")
    assert response.status_code == 404
    assert response.get_json() == {"error": "文件未找到"}

    monkeypatch.setattr(
        chat_main.requests,
        "get",
        lambda *args, **kwargs: type("R", (), {"status_code": 500})(),
    )
    before = chat_main.BING_INFO
    chat_main.download_bing_pic()
    assert chat_main.BING_INFO == before


def test_connection_helpers_and_broadcasts(main_env):
    client_a = FakeWebSocket("10.0.0.1", 1111)
    client_a.name = "Alice"
    client_b = FakeWebSocket("10.0.0.1", 2222)
    client_b.name = "Bob"
    chat_main.connected_clients.update({client_a, client_b})

    assert chat_main.get_online_user_names() == ["Alice", "Bob"]

    asyncio.run(chat_main.close_websocket_by_ip("10.0.0.1"))
    assert client_a.closed is True
    assert client_b.closed is True

    chat_main.clear_history_messages()
    chat_main.save_message("Alice", "first")
    chat_main.save_message("Bob", "second")

    receiver = FakeWebSocket("10.0.0.3", 3333)

    async def scenario():
        await chat_main.broadcast_connection_list(receiver)

    asyncio.run(scenario())
    assert receiver.sent[-1] == f"{chat_main.SYSTEM_PREFIX}----以上是历史记录----"
    assert any("first" in item for item in receiver.sent)
    assert any("second" in item for item in receiver.sent)


def test_update_cloudflare_dns_and_host_ip(main_env, monkeypatch):
    captured = {}

    class DummyResponse:
        status_code = 200
        text = "ok"

    def fake_put(url, json=None, headers=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return DummyResponse()

    monkeypatch.setattr(chat_main.requests, "put", fake_put)
    chat_main.update_cloudflare_dns("1.2.3.4")

    assert captured["url"].endswith(
        "/zones/40dc857bc7c8f78ec2ace5369ff54ae8/dns_records/6d9640e6162fbd9bf5b0d54f69642dfd"
    )
    assert captured["json"]["content"] == "1.2.3.4"
    assert captured["headers"]["Authorization"].startswith("Bearer ")

    class DummySocket:
        def __init__(self):
            self.connected = None

        def connect(self, address):
            self.connected = address

        def getsockname(self):
            return ("192.168.1.77", 54321)

        def close(self):
            pass

    monkeypatch.setattr(
        chat_main.socket, "socket", lambda *args, **kwargs: DummySocket()
    )
    assert chat_main.get_host_ip() == "192.168.1.77"


def test_download_bing_pic_writes_local_file(main_env, monkeypatch):
    main_env.root.mkdir(parents=True, exist_ok=True)
    (main_env.root / "html" / "img").mkdir(parents=True, exist_ok=True)

    class FirstResponse:
        status_code = 200

        def json(self):
            return {
                "images": [
                    {
                        "url": "/test.jpg",
                        "title": "测试标题",
                        "copyright": "地点（版权）",
                    }
                ]
            }

    class SecondResponse:
        content = b"binary-image"

    def fake_get(url):
        if "HPImageArchive" in url:
            return FirstResponse()
        return SecondResponse()

    monkeypatch.setattr(chat_main.requests, "get", fake_get)

    chat_main.download_bing_pic()

    assert Path("html/img/bing.jpg").read_bytes() == b"binary-image"
    assert "测试标题" in chat_main.BING_INFO


def test_send_system_helpers(main_env):
    websocket = FakeWebSocket()

    asyncio.run(chat_main.send_system_message(websocket, "hello"))
    asyncio.run(chat_main.send_system_alert(websocket, "danger"))

    assert websocket.sent == [
        f"{chat_main.SYSTEM_PREFIX}hello",
        f"{chat_main.SYSTEM_ALERT_PREFIX}danger",
    ]
