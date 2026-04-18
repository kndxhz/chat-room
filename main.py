import asyncio
import websockets
import socket
import requests
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import os
import datetime
import random
import time
import logging
import keyboard
import pyperclip
import mimetypes
import re

connected_clients = set()
UPLOAD_FOLDER = "./files"
IMAGE_FOLDER = "./html/img"
CONNECT_FILE = "./connect.txt"
HISTORY_FILE = "./history.txt"
KEY_FILE = "./key.txt"
BAN_FILE = "./ban.txt"
LOG_FILE = "./chat-room.log"


CLOUDFLARE_API_TOKEN = "CLOUDFLARE_API_TOKEN"
ZONE_ID = "ZONE_ID"  # 替换为你的 Zone ID
RECORD_ID = "RECORD_ID"  # 替换为你的 Record ID
DOMAIN = "im.kndxhz.cn"
LAST_ALT_PRESS_TIME = 0
DOUBLE_CLICK_THRESHOLD = 0.3  # 双击时间阈值（秒）

BING_INFO = ""
SYSTEM_PREFIX = "[SYSTEM]"
SYSTEM_ALERT_PREFIX = "[SYSTEM:ALERT]"

app = Flask(__name__)
CORS(app)  # 允许跨域
logger = logging.getLogger("chat-room")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    log_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(log_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False


def log_event(level, action, **fields):
    parts = [action]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        value_text = str(value).replace("\n", "\\n")
        if len(value_text) > 80:
            value_text = value_text[:77] + "..."
        if key == "transition":
            parts.append(value_text)
        else:
            parts.append(f"{key}={value_text}")
    logger.log(level, " | ".join(parts))


os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(IMAGE_FOLDER, exist_ok=True)


@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "没有找到文件"}), 400
    file = request.files["file"]
    file.filename = file.filename.replace(" ", "")
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
        nickname = client_ip
        try:
            with open(CONNECT_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith(client_ip):
                        parts = line.split(" ", 1)
                        if len(parts) == 2:
                            nickname = parts[1].strip()
                        break
        except FileNotFoundError:
            pass  # 文件不存在就用 IP

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
        if response.status_code == 200:
            data = response.json()
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


def update_connect_file():
    """更新 connect.txt 文件，记录所有连接的客户端信息"""
    with open(CONNECT_FILE, "w", encoding="utf-8") as f:
        for client in connected_clients:
            name = getattr(client, "name", client.remote_address[0])
            ip = client.remote_address[0]
            f.write(f"{ip} {name}\n")


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

    if event.name == "alt" and event.event_type == "down":
        current_time = time.time()
        time_since_last_alt = current_time - LAST_ALT_PRESS_TIME

        # 如果两次Alt按下时间间隔小于阈值，则认为是双击
        if time_since_last_alt < DOUBLE_CLICK_THRESHOLD:
            with open(KEY_FILE, "r", encoding="utf-8") as f:
                pyperclip.copy(f.read())

        LAST_ALT_PRESS_TIME = current_time


async def broadcast_connection_list(client):
    # for client in connected_clients:
    #     name = getattr(client, "name", client.remote_address[0])
    #     message = f"{name} 进入了房间"
    #     await asyncio.gather(
    #         *[c.send(message) for c in connected_clients if c != client]
    #     )
    log_event(logging.INFO, "进入房间", ip=client.remote_address[0])
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    tem_ = []
    for i in lines:
        if "kick" in i:
            continue
        tem_.append(i.replace("#换行", "\n"))
    lines = tem_
    if lines:
        lines = lines[-100:]  # 只发送最后100行历史记录

    await asyncio.gather(*[client.send(line) for line in lines])


async def broadcast_exit_message(client):
    # name = getattr(client, "name", client.remote_address[0])
    # message = f"{name} 退出了房间"
    # await asyncio.gather(*[c.send(message) for c in connected_clients if c != client])
    log_event(logging.INFO, "退出房间", ip=client.remote_address[0])


async def close_websocket_by_ip(ip):
    for websocket in connected_clients:
        if websocket.remote_address[0] == ip:
            await websocket.close()


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
            log_event(
                logging.INFO,
                "接收消息",
                ip=websocket.remote_address[0],
                message=message,
            )
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
                elif new_name in [
                    line.split(" ", 1)[1].strip()
                    for line in open(CONNECT_FILE, "r", encoding="utf-8").readlines()
                ]:
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
            elif message.startswith("change-key"):
                key = str(random.randint(10000, 99999))
                with open(KEY_FILE, "w", encoding="utf-8") as f:
                    f.write("")
                    f.write(key)
                log_event(logging.INFO, "更新秘钥")
                await send_system_message(websocket, "已更新秘钥")

            elif "kick" in message and key not in message:
                pass
            elif message.startswith("ban") and key in message:
                try:
                    ip = message.split(" ")[1]
                    if ip.count(".") != 3:
                        await send_system_message(websocket, "ip格式错误")
                        return
                    log_event(logging.WARNING, "封禁IP", ip=ip)
                    with open(BAN_FILE, "a", encoding="utf-8") as f:
                        f.write(ip + "\n")
                    await send_system_message(websocket, f"{ip} 被 ban")
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
                    await send_system_message(websocket, f"ban 失败: {e}")
            elif message.startswith("unban") and key in message:
                try:
                    ip = message.split(" ")[1]
                    log_event(logging.INFO, "解除封禁", ip=ip)
                    with open(BAN_FILE, "w+", encoding="utf-8") as f:
                        lines = f.readlines()
                        lines = [line for line in lines if line.strip() != ip]
                        f.writelines(lines)

                except Exception as e:
                    log_event(logging.ERROR, "解除封禁失败", error=e)
                    await send_system_message(websocket, f"unban 失败: {e}")

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
                with open(CONNECT_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
                log_event(
                    logging.INFO, "列出在线客户端", ip=websocket.remote_address[0]
                )
                await send_system_message(websocket, "当前在线的客户端:\n" + content)
            elif message == "/clear":
                log_event(logging.INFO, "清空聊天记录", ip=websocket.remote_address[0])
            else:
                name = getattr(websocket, "name", websocket.remote_address[0])
                formatted_message = f"{name}：{message}"
                with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                    f.write(formatted_message.replace("\n", "#换行") + "\n")
                # 将消息广播给所有连接的客户端
                await asyncio.gather(
                    *[
                        client.send(formatted_message)
                        for client in connected_clients
                        if client != websocket
                    ]
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
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.logger.setLevel(logging.WARNING)
    files_to_clear = [CONNECT_FILE, HISTORY_FILE, KEY_FILE, LOG_FILE]
    for file_path in files_to_clear:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("")
    key = str(random.randint(10000, 99999))
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key)
    log_event(logging.INFO, "生成秘钥", key=key)
    update_cloudflare_dns(get_host_ip())
    download_bing_pic()
    log_event(logging.INFO, "必应图片信息", info=BING_INFO.replace("\n", "|"))
    keyboard.hook(on_alt_press)
    loop = asyncio.get_event_loop()
    # 同时运行 Flask 和 WebSocket 服务器
    loop.run_in_executor(None, run_flask_app)
    loop.run_until_complete(run_websocket_server())
