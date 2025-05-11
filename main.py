import asyncio
import websockets
import socket
import requests
from flask import Flask, request, send_from_directory, jsonify
from flask_cors import CORS
import os
import datetime
import random
import time
import logging
import keyboard
import pyperclip

app = Flask(__name__)
CORS(app)  # 允许跨域
connected_clients = set()
UPLOAD_FOLDER = "./files"
CONNECT_FILE = "./connect.txt"
HISTORY_FILE = "./history.txt"
KEY_FILE = "./key.txt"
BAN_FILE = "./ban.txt"


CLOUDFLARE_API_TOKEN = ""
ZONE_ID = ""  # 替换为你的 Zone ID
RECORD_ID = ""  # 替换为你的 Record ID
DOMAIN = ""
LAST_ALT_PRESS_TIME = 0
DOUBLE_CLICK_THRESHOLD = 0.3  # 双击时间阈值（秒）


os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "没有找到文件"}), 400
    file = request.files["file"]
    file.filename = file.filename.replace(" ", "")
    if file.filename == "":
        return jsonify({"error": "文件名为空"}), 400

    file_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(file_path)
    return jsonify({"message": f"文件 {file.filename} 上传成功"}), 200


@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    try:
        client_ip = request.remote_addr
        with open(CONNECT_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            nickname = next(
                (
                    line.split(" ", 1)[1].strip()
                    for line in lines
                    if line.startswith(client_ip)
                ),
                client_ip,
            )
        print(f"{nickname} 下载了 {filename}")
        return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({"error": "文件未找到"}), 404


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
    print(f"{client.remote_address[0]} 进入了房间")
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    tem_ = []
    for i in lines:
        if "kick" in i:
            continue

        tem_.append(i.replace("#换行", "\n"))
    lines = tem_
    # print(lines)
    if lines:
        lines = lines[-100:]  # 只发送最后100行历史记录
        lines.append("----以上是历史记录----\n")
        await asyncio.gather(*[client.send(line) for line in lines])


async def broadcast_exit_message(client):

    # name = getattr(client, "name", client.remote_address[0])
    # message = f"{name} 退出了房间"
    # await asyncio.gather(*[c.send(message) for c in connected_clients if c != client])
    print(f"{client.remote_address[0]} 退出了房间")


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
            print(f"{ip} 尝试连接，但被禁止")
            await websocket.send("你已被封禁")
            await websocket.close()
            return
        websocket.name = ip  # 默认名称为 IP 地址
        update_connect_file()
        await broadcast_connection_list(websocket)  # 广播连接消息

        async for message in websocket:
            print(f"收到消息: {message} {websocket.remote_address[0]}")
            if message.startswith("set-name"):
                # 获取当前时间
                current_time = datetime.datetime.now()
                # 计算时间差
                time_diff = current_time - connection_time
                old_name = getattr(websocket, "name", websocket.remote_address[0])
                new_name = message[9:]

                # 检查昵称是否重复
                with open(CONNECT_FILE, "r", encoding="utf-8") as f:
                    names = [line.split(" ", 1)[1].strip() for line in f.readlines()]
                if new_name in names:
                    print(f"昵称 {new_name} 已被占用")
                    await websocket.send(f"repeated_nicknames")

                elif time_diff.total_seconds() < 3:
                    print(f"{new_name} {websocket.remote_address[0]} 改名差小于3秒")
                    websocket.name = new_name
                    update_connect_file()

                    websocket.name = new_name

                elif " " in new_name:
                    print(f"昵称 {new_name} 包含空格")
                    await websocket.send(f"nickname_space")

                else:
                    update_connect_file()
                    # Broadcast the name change to other clients
                    message = f"{old_name} 已改名为 {new_name}"
                    await asyncio.gather(
                        *[
                            client.send(message)
                            for client in connected_clients
                            if client != websocket
                        ]
                    )
                    await websocket.send(f"你已成功改名为 {new_name}")

                update_connect_file()
            elif message.startswith("change-key"):
                key = str(random.randint(10000, 99999))
                with open(KEY_FILE, "w", encoding="utf-8") as f:
                    f.write("")
                    f.write(key)
                print(f"秘钥：{key}")
                await websocket.send("已更新秘钥")

            elif "kick" in message and key not in message:
                pass
            elif message.startswith("ban") and key in message:
                try:
                    ip = message.split(" ")[1]
                    if ip.count(".") != 3:
                        await websocket.send("ip格式错误")
                        return
                    print(f"{ip} 被 ban")
                    with open(BAN_FILE, "a", encoding="utf-8") as f:
                        f.write(ip + "\n")
                    await websocket.send(f"{ip} 被 ban")
                    await close_websocket_by_ip(ip)
                    await asyncio.gather(
                        *[
                            client.send(f"{ip} 已被封禁")
                            for client in connected_clients
                            if client != websocket
                        ]
                    )
                except Exception as e:
                    print(f"ban 失败: {e}")
                    await websocket.send(f"ban 失败: {e}")
            elif message.startswith("unban") and key in message:
                try:
                    ip = message.split(" ")[1]
                    print(f"{ip} 被unban")
                    with open(BAN_FILE, "w+", encoding="utf-8") as f:
                        lines = f.readlines()
                        lines = [line for line in lines if line.strip() != ip]
                        f.writelines(lines)

                except Exception as e:
                    print(f"unban 失败: {e}")
                    await websocket.send(f"unban 失败: {e}")

            elif message == "del-all-files":
                try:
                    for filename in os.listdir(UPLOAD_FOLDER):
                        file_path = os.path.join(UPLOAD_FOLDER, filename)
                        os.remove(file_path)
                        print("已删除所有文件")
                except Exception as e:
                    print(f"删除所有文件失败: {e}")
                    await websocket.send(f"删除所有文件失败: {e}")
                await websocket.send("已删除所有文件")
            elif message == "update-dns":
                update_cloudflare_dns(get_host_ip())
                await websocket.send("已更新 DNS 记录")
            elif message.startswith("del"):
                try:
                    filename = message.split(" ", 1)[1].strip()
                    file_path = os.path.join(UPLOAD_FOLDER, filename)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        print(f"已删除文件: {filename}")
                    else:
                        print(f"文件不存在: {filename}")
                        await websocket.send(f"文件不存在: {filename}")
                except Exception as e:
                    print(f"删除文件失败: {e}")
                    await websocket.send(f"删除文件失败: {e}")

            elif message == "list":
                update_connect_file()
                with open(CONNECT_FILE, "r", encoding="utf-8") as f:
                    content = f.read()
                await websocket.send("当前在线的客户端:\n" + content)
            elif message == "clear":
                print(f"{websocket.remote_address[0]} 清空了聊天记录")
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
        print("WebSocket 服务器已启动，地址: ws://0.0.0.0:8765")
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
            print(f"成功更新 DNS 记录: {DOMAIN} -> {ip}")
        else:
            print(f"更新 DNS 记录失败: {response.status_code}, {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"更新 DNS 记录失败: {e}")


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
    files_to_clear = [CONNECT_FILE, HISTORY_FILE, KEY_FILE]
    for file_path in files_to_clear:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("")
    key = str(random.randint(10000, 99999))
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key)
    print(f"秘钥：{key}")
    update_cloudflare_dns(get_host_ip())
    keyboard.hook(on_alt_press)
    loop = asyncio.get_event_loop()
    # 同时运行 Flask 和 WebSocket 服务器
    loop.run_in_executor(None, run_flask_app)
    loop.run_until_complete(run_websocket_server())
