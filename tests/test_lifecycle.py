import pytest
import asyncio
import os
import psutil
from pathlib import Path
from src.utils.sim_runner import LogisimEmulator
from src.utils.config_loader import ConfigManager

@pytest.mark.asyncio
async def test_emulator_lifecycle_and_vision_ready():
    """
    专门测试仿真器的开启与关闭流程。
    标准：直到左上角识别到 'Logisim' 文字才算启动成功。
    """
    config = ConfigManager.load_config(Path("config/config.toml"))
    emulator = LogisimEmulator(config)
    
    print("\n[Lifecycle] 正在启动并进行视觉初始化验证...")
    # 启动并等待视觉就绪
    success = await emulator.launch_and_initialize()
    
    assert success is True, "视觉初始化失败：未在规定时间内识别到左上角的 'Logisim' 标志"
    
    win = emulator.main_window
    print(f"[Lifecycle] 视觉确认窗口: {win.title}，位置: ({win.left}, {win.top})")
    
    pid = emulator.process.pid
    print(f"[Lifecycle] 存活进程 PID: {pid}")
    
    # 获取进程对象用于后续存活检查
    p = psutil.Process(pid)
    assert p.is_running(), "进程未正常运行"
    
    print("[Lifecycle] 验证完成。现在让对象自然析构（依靠 __del__ 自动关闭）...")
    
    # 模拟对象超出作用域（此处使用 del 模拟真实场景下的自动关闭）
    del emulator
    
    # 给系统一点清理时间
    await asyncio.sleep(2)
    
    # 检查进程是否已消失
    # 注意：taskkill /F /T 会清理整个树，这里我们检查原 PID 是否还在
    exists = False
    for proc in psutil.process_iter(['pid']):
        if proc.info['pid'] == pid:
            exists = True
            break
            
    assert not exists, f"生命周期管理失败：进程 {pid} 在对象销毁后依然残留"
    print("[Lifecycle] 成功：进程已随 object.__del__ 自动销毁。")
