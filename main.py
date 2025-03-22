import asyncio
import websockets
import socket
import requests

connected_clients = set()
banned_ips = set()
CLOUDFLARE_API_TOKEN = ""
ZONE_ID = ""  # 替换为你的 Zone ID
RECORD_ID = ""  # 替换为你的 Record ID
DOMAIN = ""


async def handler(websocket):
    # 注册新客户端
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            print(f"收到消息: {message}")
            if message.startswith("set-name"):
                name = message.split(" ", 1)[1].strip()
                websocket.name = name
            elif message.startswith("kick"):
                ip_to_kick = message.split(" ", 1)[1].strip()
                for client in connected_clients:
                    if client.remote_address[0] == ip_to_kick:
                        await client.close()
                        print(f"已踢出 IP: {ip_to_kick}")
                        break
            else:
                name = getattr(websocket, "name", websocket.remote_address[0])
            formatted_message = f"{name} {message}"
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
    response = requests.put(url, json=data, headers=headers)
    if response.status_code == 200:
        print(f"成功更新 DNS 记录: {DOMAIN} -> {ip}")
    else:
        print(f"更新 DNS 记录失败: {response.status_code}, {response.text}")


def get_host_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("1.1.1.1", 0))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip


async def main():
    update_cloudflare_dns(get_host_ip())
    async with websockets.serve(handler, "0.0.0.0", 8765):
        print("WebSocket 服务器已启动,地址: ws://0.0.0.0:8765")
        await asyncio.Future()  # 永远运行


if __name__ == "__main__":
    asyncio.run(main())
