import asyncio
import websockets
import json
import uuid
import time
from pathlib import Path

class LogisimEmulator:
    """Logisim 仿真器运行管理器 (Headless WebSocket 版)"""

    def __init__(self, config, client):
        self.config = config
        self.client = client
        # HTTP/WS 端口在 config 里，如果没有则默认 9924
        self.port = getattr(self.config.paths, "headless_port", 9924)
        self.uri = f"ws://localhost:{self.port}/ws"
        self.ws = None

    async def connect(self) -> bool:
        """尝试连接到已经运行的 Logisim Headless 后端"""
        try:
            print(f"[Emulator] 正在连接到 Logisim Headless 服务器: {self.uri} ...")
            self.ws = await websockets.connect(self.uri)
            print("[Emulator] WebSocket 连接成功！")
            return True
        except ConnectionRefusedError:
            print(f"[Emulator] CRITICAL: 无法连接到 {self.uri}。请手动运行 Logisim Headless 服务器（如：run_backend.bat）！")
            return False
        except Exception as e:
            print(f"[Emulator] WebSocket 连接异常: {e}")
            return False

    async def send_command(self, action: str, **kwargs) -> dict:
        """发送 JSON 指令并等待响应"""
        if not self.ws:
            print("[Emulator] WebSocket 未连接，无法发送命令！")
            return {"status": "error", "message": "No websocket connection"}

        req_id = str(uuid.uuid4())
        req = {"action": action, "req_id": req_id, **kwargs}
        
        try:
            await self.ws.send(json.dumps(req))
            resp = await self.ws.recv()
            if isinstance(resp, bytes):
                return {"status": "ok", "binary": resp}
            else:
                return json.loads(resp)
        except Exception as e:
            print(f"[Emulator] 发送指令异常: {e}")
            return {"status": "error", "message": str(e)}

    async def launch_and_initialize(self, circ_path: str = None) -> bool:
        """初始化流程：建立连接并加载主电路（如果提供）"""
        if not await self.connect():
            return False

        if circ_path:
            abs_circ = Path(circ_path).absolute()
            print(f"[Emulator] 自动加载电路: {abs_circ}")
            resp = await self.send_command("load_circuit", path=str(abs_circ))
            if resp.get("status") == "ok":
                print("[Emulator] 电路加载成功。")
                return True
            else:
                print(f"[Emulator] 电路加载失败: {resp.get('message')}")
                return False
        return True

    def close(self):
        """清理 WebSocket 连接"""
        if self.ws:
            print("[Emulator] 正在关闭 WebSocket 连接...")
            # create_task 可以在事件循环中异步执行关闭
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.ws.close())
            except:
                pass
            self.ws = None

    def terminate(self):
        self.close()

    def __del__(self):
        self.close()
