import asyncio
import websockets
import socket
import requests
from flask import Flask, request, send_from_directory, jsonify
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)  # 允许跨域
connected_clients = set()
UPLOAD_FOLDER = "./files"
CONNECT_FILE = "./connect.txt"


CLOUDFLARE_API_TOKEN = ""
ZONE_ID = ""  # 替换为你的 Zone ID
RECORD_ID = ""  # 替换为你的 Record ID
DOMAIN = ""




os.makedirs(UPLOAD_FOLDER, exist_ok=True)


@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "没有找到文件"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "文件名为空"}), 400
    file_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(file_path)
    return jsonify({"message": f"文件 {file.filename} 上传成功"}), 200


@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    try:
        return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)
    except FileNotFoundError:
        return jsonify({"error": "文件未找到"}), 404


@app.route("/file_list", methods=["GET"])
def file_list():
    files = os.listdir(UPLOAD_FOLDER)

    return jsonify(files), 200


def update_connect_file():
    """更新 connect.txt 文件，记录所有连接的客户端信息"""
    with open(CONNECT_FILE, "w") as f:
        for client in connected_clients:
            name = getattr(client, "name", client.remote_address[0])
            ip = client.remote_address[0]
            f.write(f"{ip} {name}\n")


async def broadcast_connection_list():
    """向所有客户端广播当前连接的客户端列表"""
    for client in connected_clients:
        name = getattr(client, "name", client.remote_address[0])
        message = f"{name} 进入了房间"
        await asyncio.gather(
            *[c.send(message) for c in connected_clients if c != client]
        )


async def broadcast_exit_message(client):
    """广播客户端退出消息"""
    name = getattr(client, "name", client.remote_address[0])
    message = f"{name} 退出了房间"
    await asyncio.gather(*[c.send(message) for c in connected_clients if c != client])


async def handler(websocket):
    # 注册新客户端
    connected_clients.add(websocket)
    try:
        # 获取客户端 IP
        ip = websocket.remote_address[0]
        websocket.name = ip  # 默认名称为 IP 地址
        update_connect_file()
        await broadcast_connection_list()

        async for message in websocket:
            print(f"收到消息: {message}")
            if message.startswith("set-name"):
                old_name = getattr(websocket, "name", websocket.remote_address[0])
                new_name = message.split(" ", 1)[1].strip()
                websocket.name = new_name
                update_connect_file()
                # Broadcast the name change to other clients
                message = f"{old_name} 已改名为 {new_name}"
                await asyncio.gather(
                    *[client.send(message) for client in connected_clients if client != websocket]
                )
                await websocket.send(f"你已成功改名为 {new_name}")

            # elif message.startswith("kick"):
            #     ip_to_kick = message.split(" ", 1)[1].strip()
            #     for client in connected_clients:
            #         if client.remote_address[0] == ip_to_kick:
            #             await client.close()
            #             print(f"已踢出 IP: {ip_to_kick}")
            #             break
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
                with open(CONNECT_FILE, "r") as f:
                    content = f.read()
                await websocket.send("当前在线的客户端:\n" + content)
            elif message == "clear":
                break
            else:
                name = getattr(websocket, "name", websocket.remote_address[0])
                formatted_message = f"{name}：{message}"
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
        await broadcast_connection_list()


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
    # 程序启动时清空 connect.txt 文件
    with open(CONNECT_FILE, "w") as f:
        f.write("")
    update_cloudflare_dns(get_host_ip())
    loop = asyncio.get_event_loop()
    # 同时运行 Flask 和 WebSocket 服务器
    loop.run_in_executor(None, run_flask_app)
    loop.run_until_complete(run_websocket_server())
