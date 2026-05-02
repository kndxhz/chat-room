import asyncio
import os
import sys
from pathlib import Path


def _bootstrap_local_site_packages():
    project_root = Path(__file__).resolve().parent
    bundled_python = project_root / "python-3.13.2-embed-amd64"
    candidate_paths = [
        bundled_python,
        bundled_python / "Lib" / "site-packages",
        bundled_python / "site-packages",
    ]
    for candidate in candidate_paths:
        candidate_str = str(candidate)
        if candidate.exists() and candidate_str not in sys.path:
            sys.path.append(candidate_str)


_bootstrap_local_site_packages()

import websockets
import socket
import requests
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import datetime
import random
import time
import logging
import keyboard
import pyperclip
import mimetypes
import re
import sqlite3
import threading
from urllib.parse import unquote
from dotenv import load_dotenv

load_dotenv()

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "./files")
IMAGE_FOLDER = os.environ.get("IMAGE_FOLDER", "./html/img")
CONNECT_FILE = os.environ.get("CONNECT_FILE", "./connect.txt")
HISTORY_FILE = os.environ.get("HISTORY_FILE", "./history.txt")
KEY_FILE = os.environ.get("KEY_FILE", "./key.txt")
BAN_FILE = os.environ.get("BAN_FILE", "./ban.txt")
LOG_FILE = os.environ.get("LOG_FILE", "./chat-room.log")
DB_FILE = os.environ.get("DB_FILE", "./chat.db")


CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
ZONE_ID = os.environ.get("ZONE_ID", "")
RECORD_ID = os.environ.get("RECORD_ID", "")
DOMAIN = os.environ.get("DOMAIN", "")
LAST_ALT_PRESS_TIME = 0
DOUBLE_CLICK_THRESHOLD = 0.5

BING_INFO = ""
SYSTEM_PREFIX = os.environ.get("SYSTEM_PREFIX", "[SYSTEM]")  # 系统消息前缀
SYSTEM_ALERT_PREFIX = os.environ.get("SYSTEM_ALERT_PREFIX", "[ALERT]")  # 系统警告前缀

connected_clients = set()


app = Flask(__name__)
CORS(app)  # 允许跨域
logger = logging.getLogger("chat-room")
db_lock = threading.Lock()
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_formatter = logging.Formatter("%(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(log_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False


def _format_log_value(value):
    value_text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    value_text = value_text.replace("\n", "\\n")
    if len(value_text) > 240:
        value_text = value_text[:237] + "..."
    value_text = value_text.replace("|", "/")
    return value_text


def _format_log_event(level, action, field_items):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level_name = logging.getLevelName(level)
    parts = [timestamp, level_name, action]
    for key, value in field_items:
        if value is None or value == "":
            continue
        value_text = _format_log_value(value)
        parts.append(f"{key}={value_text}")
    return " ".join(parts)


LOG_FIELD_WHITELIST = {
    "接收消息": ["sender", "message"],
    "接收回复": ["sender", "reply_to", "message"],
    "入房改名": ["transition"],
    "用户改名": ["transition"],
    "进入房间": ["user"],
    "退出房间": ["user"],
    "下载文件": ["user", "file"],
    "昵称格式不合法": ["name"],
    "昵称重复": ["name"],
    "踢出用户": ["user"],
    "封禁IP": ["ip"],
    "解除封禁": ["ip"],
    "删除文件": ["file"],
    "文件不存在": ["file"],
    "更新 DNS 成功": ["domain"],
    "更新 DNS 失败": ["code", "error"],
    "WebSocket 启动": ["url"],
    "必应图片信息": ["title"],
}


def log_event(level, action, **fields):
    allowed_keys = LOG_FIELD_WHITELIST.get(action)
    if allowed_keys is None:
        allowed_keys = ["error"] if level >= logging.ERROR else []

    ordered_items = []
    for key in allowed_keys:
        if key in fields:
            ordered_items.append((key, fields.get(key)))
    logger.log(level, _format_log_event(level, action, ordered_items))


def parse_reply_meta(content):
    source = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    token_match = re.match(
        r"^\s*\[\[reply:([^|\]]*)\|([^|\]]*)(?:\|([^\]]*))?\]\]\s*\n?",
        source,
        flags=re.IGNORECASE,
    )
    if not token_match:
        return None

    reply_to = unquote(token_match.group(1) or "").strip()
    preview = unquote(token_match.group(2) or "").strip()
    target_msgid = unquote(token_match.group(3) or "").strip()
    body = source[token_match.end() :].lstrip("\n")

    return {
        "reply_to": reply_to,
        "preview": preview,
        "target_msgid": target_msgid,
        "body": body,
    }


def parse_hidden_command(message):
    normalized = (message or "").strip()
    if normalized.startswith("/"):
        normalized = normalized[1:].strip()
    if not normalized:
        return None, []
    parts = normalized.split()
    return parts[0].lower(), parts[1:]


def write_new_key():
    new_key = str(random.randint(10000, 99999))
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(new_key)
    return new_key


os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(IMAGE_FOLDER, exist_ok=True)


def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='connections'"
            )
            has_connections = cursor.fetchone() is not None
            if has_connections:
                cursor.execute("PRAGMA table_info(connections)")
                columns = [row[1] for row in cursor.fetchall()]
                # 旧结构是 ip 主键，不支持同 IP 多连接；检测到则迁移重建
                if "session_id" not in columns:
                    cursor.execute("DROP TABLE IF EXISTS connections")

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS connections (
                    session_id TEXT PRIMARY KEY,
                    ip TEXT NOT NULL,
                    name TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id TEXT UNIQUE NOT NULL,
                    sender TEXT NOT NULL,
                    content TEXT NOT NULL,
                    raw_message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()


def sync_connections_to_db():
    now_text = datetime.datetime.now().isoformat(timespec="seconds")
    rows = []
    for client in connected_clients:
        ip = client.remote_address[0]
        port = client.remote_address[1]
        session_id = f"{ip}:{port}"
        name = getattr(client, "name", ip)
        rows.append((session_id, ip, name, now_text))

    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM connections")
            if rows:
                cursor.executemany(
                    "INSERT INTO connections(session_id, ip, name, updated_at) VALUES (?, ?, ?, ?)",
                    rows,
                )
            conn.commit()
        finally:
            conn.close()


def get_nickname_by_ip(ip):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM connections WHERE ip = ? ORDER BY updated_at DESC LIMIT 1",
                (ip,),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]
        finally:
            conn.close()
    return ip


def get_connection_rows():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT ip, name FROM connections ORDER BY updated_at DESC, name ASC"
            )
            return cursor.fetchall()
        finally:
            conn.close()


def generate_message_id():
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S%f") + str(
        random.randint(1000, 9999)
    )


def save_message(sender, content):
    msg_id = generate_message_id()
    raw_message = f"{sender}：[[msg:{msg_id}]]\n{content}"
    created_at = datetime.datetime.now().isoformat(timespec="seconds")
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO messages(msg_id, sender, content, raw_message, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (msg_id, sender, content, raw_message, created_at),
            )
            conn.commit()
        finally:
            conn.close()
    return msg_id, raw_message


def get_recent_raw_messages(limit=100):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT raw_message FROM messages ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
    messages = [row[0] for row in rows][::-1]
    return [m for m in messages if "kick" not in m]


def get_message_by_msg_id(msg_id):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT msg_id, raw_message, sender, created_at FROM messages WHERE msg_id = ?",
                (msg_id,),
            )
            row = cursor.fetchone()
        finally:
            conn.close()
    if not row:
        return None
    return {
        "msg_id": row[0],
        "message": row[1],
        "sender": row[2],
        "created_at": row[3],
    }


def clear_history_messages():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages")
            conn.commit()
        finally:
            conn.close()


def clear_connections_table():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM connections")
            conn.commit()
        finally:
            conn.close()


@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "没有找到文件"}), 400
    file = request.files["file"]
    file.filename = (file.filename or "").replace(" ", "")
    if file.filename == "":
        return jsonify({"error": "文件名为空"}), 400

    # 根据参数决定保存目录
    is_img = request.args.get("is_img") == "1"
    folder = IMAGE_FOLDER if is_img else UPLOAD_FOLDER
    file_path = os.path.join(folder, file.filename)
    file.save(file_path)
    return jsonify({"message": f"文件 {file.filename} 上传成功"}), 200


@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    try:
        client_ip = request.remote_addr

        # 读取昵称
        nickname = get_nickname_by_ip(client_ip)

        file_path = os.path.join(UPLOAD_FOLDER, filename)
        if not os.path.isfile(file_path):
            return jsonify({"error": "文件未找到"}), 404

        # 只在首次完整请求时打印（非 Range 请求）
        if request.headers.get("Range") is None:
            mime_type, _ = mimetypes.guess_type(filename)
            if mime_type and not mime_type.startswith("image/"):
                log_event(
                    logging.INFO, "下载文件", ip=client_ip, user=nickname, file=filename
                )

        return send_file(
            file_path,
            as_attachment=True,
            conditional=True,  # 支持断点续传
            download_name=filename,
            max_age=0,
            last_modified=True,
        )
    except Exception as e:
        log_event(logging.ERROR, "下载出错", error=e)
        return jsonify({"error": "服务器内部错误"}), 500


@app.route("/get_bing_info", methods=["GET"])
def get_bing_info():
    global BING_INFO
    return jsonify({"info": BING_INFO}), 200


def download_bing_pic():
    try:
        response = requests.get(
            "https://cn.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN"
        )
        if response.status_code != 200:
            return

        data = response.json()
        if not data.get("images"):
            return

        url = "https://cn.bing.com/" + data["images"][0]["url"]
        response = requests.get(url)

        with open("./html/img/bing.jpg", "wb") as f:
            f.write(response.content)

        title = data["images"][0]["title"]
        localtion = data["images"][0]["copyright"]
        pat = re.compile(
            r"(?P<place>[^()]+)"  # 非括号字符：地点描述
            r"\s*"  # 可选空白
            r"\("  # 左括号
            r"(?P<copyright>[^)]+)"  # 非右括号字符：版权信息
            r"\)"  # 右括号
        )

        m = pat.search(localtion)
        place = ""
        copyr = ""
        if m:
            place = m.group("place").strip()  # 伊斯特本码头, 东萨塞克斯郡, 英格兰
            copyr = m.group("copyright").strip()  # © Tolga_TEZCAN/Getty Images

        global BING_INFO
        BING_INFO = f"{title}\n{place}\n{copyr}"

    except Exception as e:
        log_event(logging.ERROR, "必应壁纸下载失败", error=e)


@app.route("/file_list", methods=["GET"])
def file_list():
    files = os.listdir(UPLOAD_FOLDER)

    return jsonify(files), 200


@app.route("/get_message_by_id/<msg_id>", methods=["GET"])
def get_message_by_id(msg_id):
    data = get_message_by_msg_id(msg_id)
    if not data:
        return jsonify({"found": False}), 404
    return jsonify({"found": True, **data}), 200


def update_connect_file():
    """同步在线连接到数据库"""
    sync_connections_to_db()


async def send_system_message(client, message):
    await client.send(f"{SYSTEM_PREFIX}{message}")


async def send_system_alert(client, message):
    await client.send(f"{SYSTEM_ALERT_PREFIX}{message}")


def is_valid_username(name):
    if not name or not isinstance(name, str):
        return False
    pattern = r"^[a-zA-Z0-9_\u4e00-\u9fff]+$"
    return re.match(pattern, name) is not None


def get_online_user_names():
    names = []
    for client in connected_clients:
        names.append(getattr(client, "name", client.remote_address[0]))
    return sorted(set(names))


@app.route("/get_online_user", methods=["GET"])
def get_online_user():
    return jsonify({"users": get_online_user_names()}), 200


def on_alt_press(event):
    global LAST_ALT_PRESS_TIME

    event_name = str(getattr(event, "name", "") or "").lower()
    if event_name in {"alt", "left alt", "alt left"} and event.event_type == "down":
        current_time = time.time()
        time_since_last_alt = current_time - LAST_ALT_PRESS_TIME

        # 如果两次Alt按下时间间隔小于阈值，则认为是双击
        if time_since_last_alt < DOUBLE_CLICK_THRESHOLD:
            with open(KEY_FILE, "r", encoding="utf-8") as f:
                pyperclip.copy(f.read().strip())

        LAST_ALT_PRESS_TIME = current_time


async def broadcast_connection_list(client):
    # for client in connected_clients:
    #     name = getattr(client, "name", client.remote_address[0])
    #     message = f"{name} 进入了房间"
    #     await asyncio.gather(
    #         *[c.send(message) for c in connected_clients if c != client]
    #     )
    user_name = getattr(client, "name", client.remote_address[0])
    log_event(logging.INFO, "进入房间", user=user_name)
    lines = get_recent_raw_messages(100)
    if lines:
        lines = lines[-100:]  # 只发送最后100行历史记录
    lines.append(SYSTEM_PREFIX + "----以上是历史记录----")
    await asyncio.gather(*[client.send(line) for line in lines])


async def broadcast_exit_message(client):
    # name = getattr(client, "name", client.remote_address[0])
    # message = f"{name} 退出了房间"
    # await asyncio.gather(*[c.send(message) for c in connected_clients if c != client])
    user_name = getattr(client, "name", client.remote_address[0])
    log_event(logging.INFO, "退出房间", user=user_name)


async def close_websocket_by_ip(ip):
    for websocket in connected_clients:
        if websocket.remote_address[0] == ip:
            await websocket.close()


async def close_websocket_by_name(name, notice=None):
    closed = False
    for websocket in connected_clients.copy():
        if getattr(websocket, "name", websocket.remote_address[0]) == name:
            close_reason = notice or "你已被踢出"
            await websocket.close(code=4001, reason=close_reason)
            closed = True
    return closed


async def handler(websocket):
    global key
    # 注册新客户端
    connected_clients.add(websocket)
    connection_time = datetime.datetime.now()
    try:
        # 获取客户端 IP
        ip = websocket.remote_address[0]
        if ip in open(BAN_FILE, "r", encoding="utf-8").read():
            log_event(logging.WARNING, "连接被拒", ip=ip)
            await send_system_message(websocket, "你已被封禁")
            await websocket.close()
            return
        websocket.name = ip  # 默认名称为 IP 地址
        update_connect_file()
        await broadcast_connection_list(websocket)  # 广播连接消息

        async for message in websocket:
            hidden_command, hidden_args = parse_hidden_command(message)

            if message.startswith("/set-name"):
                # 获取当前时间
                current_time = datetime.datetime.now()
                # 计算时间差
                time_diff = current_time - connection_time
                old_name = getattr(websocket, "name", websocket.remote_address[0])
                new_name = message[10:]

                # 检查昵称格式是否合法
                if not is_valid_username(new_name):
                    log_event(
                        logging.WARNING,
                        "昵称格式不合法",
                        ip=websocket.remote_address[0],
                        name=new_name,
                    )
                    await send_system_alert(
                        websocket, "昵称只能包含英文、中文、数字和下划线！"
                    )

                # 检查昵称是否重复
                elif new_name != old_name and new_name in get_online_user_names():
                    log_event(
                        logging.WARNING,
                        "昵称重复",
                        ip=websocket.remote_address[0],
                        name=new_name,
                    )
                    await send_system_alert(websocket, "昵称重复，请重新输入！")

                elif time_diff.total_seconds() < 3:
                    log_event(
                        logging.INFO,
                        "入房改名",
                        ip=websocket.remote_address[0],
                        transition=f"{old_name} -> {new_name}",
                    )
                    websocket.name = new_name
                    update_connect_file()

                else:
                    update_connect_file()
                    # Broadcast the name change to other clients
                    message = f"{old_name} 已改名为 {new_name}"
                    websocket.name = new_name
                    log_event(
                        logging.INFO,
                        "用户改名",
                        ip=websocket.remote_address[0],
                        transition=f"{old_name} -> {new_name}",
                    )
                    await asyncio.gather(
                        *[
                            client.send(message)
                            for client in connected_clients
                            if client != websocket
                        ]
                    )
                    await send_system_message(websocket, f"你已成功改名为 {new_name}")

                update_connect_file()
            elif hidden_command == "change-key":
                key = write_new_key()
                log_event(logging.INFO, "更新秘钥")
                await send_system_message(websocket, "已更新秘钥")
            elif hidden_command == "kick":
                try:
                    if len(hidden_args) < 3:
                        await send_system_message(
                            websocket, "用法: /kick 昵称 原因 秘钥"
                        )
                        continue
                    name = hidden_args[0]
                    command_key = hidden_args[-1]
                    reason = " ".join(hidden_args[1:-1]).strip()
                    if command_key != key:
                        await send_system_message(websocket, "秘钥错误")
                        continue
                    if not reason:
                        await send_system_message(
                            websocket, "用法: /kick 昵称 原因 秘钥"
                        )
                        continue
                    notice = (
                        "请重新设定昵称"
                        if reason == "name"
                        else f"你已被踢出！\n原因：{reason}"
                    )
                    closed = await close_websocket_by_name(name, notice=notice)
                    if not closed:
                        await send_system_message(websocket, f"用户不存在: {name}")
                        continue
                    log_event(logging.WARNING, "踢出用户", user=name)
                    await send_system_message(websocket, f"{name} 被踢出")
                except Exception as e:
                    log_event(logging.ERROR, "踢出失败", error=e)
                    await send_system_message(websocket, f"踢出失败: {e}")
            elif hidden_command == "ban":
                try:
                    if len(hidden_args) != 2:
                        await send_system_message(websocket, "用法: /ban ip 秘钥")
                        continue
                    ip = hidden_args[0]
                    command_key = hidden_args[1]
                    if command_key != key:
                        await send_system_message(websocket, "秘钥错误")
                        continue
                    if ip.count(".") != 3:
                        await send_system_message(websocket, "ip格式错误")
                        continue
                    log_event(logging.WARNING, "封禁IP", ip=ip)
                    with open(BAN_FILE, "a", encoding="utf-8") as f:
                        f.write(ip + "\n")
                    await send_system_message(websocket, f"{ip} 被封禁")
                    await close_websocket_by_ip(ip)
                    await asyncio.gather(
                        *[
                            client.send(f"{ip} 已被封禁")
                            for client in connected_clients
                            if client != websocket
                        ]
                    )
                except Exception as e:
                    log_event(logging.ERROR, "封禁失败", error=e)
                    await send_system_message(websocket, f"封禁失败: {e}")
            elif hidden_command == "unban":
                try:
                    if len(hidden_args) != 2:
                        await send_system_message(websocket, "用法: /unban ip 秘钥")
                        continue
                    ip = hidden_args[0]
                    command_key = hidden_args[1]
                    if command_key != key:
                        await send_system_message(websocket, "秘钥错误")
                        continue
                    log_event(logging.INFO, "解除封禁", ip=ip)
                    with open(BAN_FILE, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    lines = [line for line in lines if line.strip() != ip]
                    with open(BAN_FILE, "w", encoding="utf-8") as f:
                        f.writelines(lines)

                except Exception as e:
                    log_event(logging.ERROR, "解除封禁失败", error=e)
                    await send_system_message(websocket, f"解除封禁失败: {e}")

            elif message == "/del-all-files":
                try:
                    for filename in os.listdir(UPLOAD_FOLDER):
                        file_path = os.path.join(UPLOAD_FOLDER, filename)
                        os.remove(file_path)
                    log_event(logging.INFO, "删除所有文件")
                except Exception as e:
                    log_event(logging.ERROR, "删除所有文件失败", error=e)
                    await send_system_message(websocket, f"删除所有文件失败: {e}")
                await send_system_message(websocket, "已删除所有文件")
            elif message == "/update-dns":
                update_cloudflare_dns(get_host_ip())
                await send_system_message(websocket, "已更新 DNS 记录")
            elif message.startswith("/del "):
                try:
                    filename = message.split(" ", 1)[1].strip()
                    file_path = os.path.join(UPLOAD_FOLDER, filename)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        log_event(logging.INFO, "删除文件", file=filename)
                    else:
                        log_event(logging.WARNING, "文件不存在", file=filename)
                        await send_system_message(websocket, f"文件不存在: {filename}")
                except Exception as e:
                    log_event(logging.ERROR, "删除文件失败", error=e)
                    await send_system_message(websocket, f"删除文件失败: {e}")

            elif message == "/list":
                update_connect_file()
                rows = get_connection_rows()
                content = "\n".join([f"{ip} {name}" for ip, name in rows])
                log_event(
                    logging.INFO, "列出在线客户端", ip=websocket.remote_address[0]
                )
                await send_system_message(websocket, "当前在线的客户端:\n" + content)
            elif message == "/clear":
                log_event(logging.INFO, "清空聊天记录", ip=websocket.remote_address[0])
                clear_history_messages()
                await send_system_message(websocket, "已清空聊天记录")
            else:
                name = getattr(websocket, "name", websocket.remote_address[0])
                _, formatted_message = save_message(name, message)
                reply_meta = parse_reply_meta(message)
                if reply_meta:
                    log_event(
                        logging.INFO,
                        "接收回复",
                        sender=name,
                        reply_to=reply_meta.get("reply_to", ""),
                        message=reply_meta.get("body", ""),
                    )
                else:
                    log_event(
                        logging.INFO,
                        "接收消息",
                        sender=name,
                        message=message,
                    )
                # 将消息广播给所有连接的客户端
                await asyncio.gather(
                    *[client.send(formatted_message) for client in connected_clients]
                )
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        # 注销客户端
        connected_clients.remove(websocket)
        update_connect_file()
        await broadcast_exit_message(websocket)


async def run_websocket_server():
    async with websockets.serve(handler, "0.0.0.0", 8765):
        log_event(logging.INFO, "WebSocket 启动", url="ws://0.0.0.0:8765")
        await asyncio.Future()  # 保持运行


def update_cloudflare_dns(ip):
    url = (
        f"https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records/{RECORD_ID}"
    )
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    data = {
        "type": "A",
        "name": DOMAIN,
        "content": ip,
        "ttl": 1,
        "proxied": False,  # 不启用 Cloudflare 代理
    }
    try:
        response = requests.put(url, json=data, headers=headers)
        if response.status_code == 200:
            log_event(logging.INFO, "更新 DNS 成功", domain=DOMAIN, ip=ip)
        else:
            log_event(
                logging.ERROR,
                "更新 DNS 失败",
                code=response.status_code,
                body=response.text,
            )
    except requests.exceptions.RequestException as e:
        log_event(logging.ERROR, "更新 DNS 失败", error=e)


def get_host_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 0))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip


def run_flask_app():
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    try:
        os.remove(DB_FILE)
    except FileNotFoundError:
        log_event(logging.INFO, "数据库文件不存在，无需删除")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.logger.setLevel(logging.WARNING)
    init_db()

    create_files = (CONNECT_FILE, HISTORY_FILE, KEY_FILE, BAN_FILE)
    for file in create_files:
        if not os.path.exists(file):
            with open(file, "w", encoding="utf-8") as f:
                pass
    create_folders = (UPLOAD_FOLDER, IMAGE_FOLDER)
    for folder in create_folders:
        os.makedirs(folder, exist_ok=True)

    key = write_new_key()
    log_event(logging.INFO, "生成秘钥")
    print(f"当前秘钥: {key}")
    update_cloudflare_dns(get_host_ip())
    download_bing_pic()
    log_event(logging.INFO, "必应图片信息", title=BING_INFO.replace("\n", "|"))
    keyboard.hook(on_alt_press)
    loop = asyncio.get_event_loop()
    # 同时运行 Flask 和 WebSocket 服务器
    loop.run_in_executor(None, run_flask_app)
    loop.run_until_complete(run_websocket_server())
