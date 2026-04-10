import subprocess
import time
import pygetwindow as gw
from pathlib import Path

def debug_windows():
    print("--- WINDOW ENUMERATION DEBUG (UTF-8) ---")
    logisim_exe = Path("3rd/Logisim-ITA.exe").absolute()
    print(f"Executing: {logisim_exe}")
    
    # 使用 Popen 启动
    proc = subprocess.Popen([str(logisim_exe)])
    
    titles_log = []
    
    try:
        # 等待更长时间，每 2 秒采样一次，持续 60 秒
        for i in range(30):
            print(f"Sample {i+1}/30...")
            titles = gw.getAllTitles()
            for t in titles:
                if t and t not in titles_log:
                    # 避免控制台编码错误，直接打印安全的字符或忽略错误
                    try:
                        print(f"  + {t.encode('ascii', 'ignore').decode('ascii')}")
                    except:
                        pass
                    titles_log.append(t)
            
            # 检查是否有包含关键字的窗口
            for t in titles:
                if "Logisim" in t:
                    print(f"!!! FOUND LOGISIM WINDOW: {t}")
            
            time.sleep(2)
            
    finally:
        print("\nDebug finished. Results saved to debug/window_titles.txt")
        Path("debug").mkdir(exist_ok=True)
        with open("debug/window_titles.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(titles_log))
        proc.terminate()

if __name__ == "__main__":
    debug_windows()
