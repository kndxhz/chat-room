from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main as chat_main


@dataclass
class TestEnvironment:
    module: object
    root: Path
    upload_dir: Path
    image_dir: Path
    connect_file: Path
    history_file: Path
    key_file: Path
    ban_file: Path
    db_file: Path


class FakeWebSocket:
    def __init__(self, ip: str = "127.0.0.1", port: int = 10000, incoming=None):
        self.remote_address = (ip, port)
        self.name = ip
        self.sent: list[str] = []
        self.closed = False
        self.close_code = None
        self.close_reason = None
        self._incoming = list(incoming or [])

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)

    async def send(self, message):
        self.sent.append(message)

    async def close(self, code=None, reason=None):
        self.closed = True
        self.close_code = code
        self.close_reason = reason


@pytest.fixture()
def tmp_path():
    base_dir = Path.home() / ".codex" / "memories" / "chat-room-test-tmp"
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def main_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))

    upload_dir = tmp_path / "files"
    image_dir = tmp_path / "html" / "img"
    upload_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    connect_file = tmp_path / "connect.txt"
    history_file = tmp_path / "history.txt"
    key_file = tmp_path / "key.txt"
    ban_file = tmp_path / "ban.txt"
    db_file = tmp_path / "chat.db"

    for path in [connect_file, history_file, key_file, ban_file]:
        path.write_text("", encoding="utf-8")

    monkeypatch.setattr(chat_main, "UPLOAD_FOLDER", str(upload_dir), raising=False)
    monkeypatch.setattr(chat_main, "IMAGE_FOLDER", str(image_dir), raising=False)
    monkeypatch.setattr(chat_main, "CONNECT_FILE", str(connect_file), raising=False)
    monkeypatch.setattr(chat_main, "HISTORY_FILE", str(history_file), raising=False)
    monkeypatch.setattr(chat_main, "KEY_FILE", str(key_file), raising=False)
    monkeypatch.setattr(chat_main, "BAN_FILE", str(ban_file), raising=False)
    monkeypatch.setattr(chat_main, "DB_FILE", str(db_file), raising=False)
    monkeypatch.setattr(chat_main, "connected_clients", set(), raising=False)
    monkeypatch.setattr(chat_main, "key", "12345", raising=False)
    monkeypatch.setattr(chat_main, "BING_INFO", "", raising=False)

    chat_main.init_db()
    chat_main.clear_history_messages()
    chat_main.clear_connections_table()

    return TestEnvironment(
        module=chat_main,
        root=tmp_path,
        upload_dir=upload_dir,
        image_dir=image_dir,
        connect_file=connect_file,
        history_file=history_file,
        key_file=key_file,
        ban_file=ban_file,
        db_file=db_file,
    )
