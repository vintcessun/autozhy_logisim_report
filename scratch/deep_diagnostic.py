import subprocess
import time
import os
import psutil
from pathlib import Path

def diagnostic_launch():
    print("=== Logisim 启动深度诊断 ===")
    exe_path = Path("3rd/Logisim-ITA.exe").absolute()
    print(f"检查文件是否存在: {exe_path.exists()}")
    
    # 记录启动前的进程快照
    pre_processes = {p.pid for p in psutil.process_iter(['pid'])}
    
    print("\n[1] 尝试物理启动进程并捕获流...")
    try:
        # 使用 PIPE 捕获输出
        proc = subprocess.Popen(
            [str(exe_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=exe_path.parent # 切换到 binary 所在目录启动
        )
        
        # 等待 15 秒观察状态
        for i in range(15):
            print(f"正在观察... {i+1}s (PID: {proc.pid}, Status: {proc.poll() if proc else 'Dead'})")
            
            # 检查是否有新相关的进程（如 java.exe）出现
            for p in psutil.process_iter(['pid', 'name', 'cmdline']):
                if p.pid not in pre_processes:
                    if any(k in (p.info['name'] or '').lower() for k in ['java', 'logisim']):
                        print(f"发现了关联进程: PID={p.pid}, Name={p.info['name']}, Cmd={p.info['cmdline']}")
            
            time.sleep(1)
            
        # 捕获可能的错误
        stdout, stderr = proc.communicate(timeout=1)
        print(f"\n[STDOUT]:\n{stdout}")
        print(f"\n[STDERR]:\n{stderr}")
        
    except Exception as e:
        print(f"启动异常: {e}")
    finally:
        if 'proc' in locals():
            proc.terminate()

if __name__ == "__main__":
    diagnostic_launch()
