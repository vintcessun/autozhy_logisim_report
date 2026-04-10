import subprocess
import time
import psutil
from pathlib import Path

def trace():
    print("[Tracer] Launching Logisim-ITA.exe...")
    exe_path = Path("3rd/Logisim-ITA.exe").absolute()
    process = subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
    
    parent_pid = process.pid
    tracked_pids = set()
    start_time = time.time()
    
    print(f"[Tracer] Parent PID: {parent_pid}. Sniffing children for 10 seconds...")
    
    while time.time() - start_time < 10:
        try:
            p = psutil.Process(parent_pid)
            for child in p.children(recursive=True):
                if child.pid not in tracked_pids:
                    print(f"  >>> Found Child! PID: {child.pid}, Name: {child.name()}")
                    tracked_pids.add(child.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # If parent dies, children might still be alive as orphans
            pass
        time.sleep(0.1)
    
    print(f"[Tracer] Final Captured List: {list(tracked_pids)}")
    print("[Tracer] Cleaning up tracked PIDs surgically...")
    for pid in tracked_pids:
        try:
            psutil.Process(pid).kill()
            print(f"  >>> Killed child PID {pid}")
        except: pass
    
    try:
        psutil.Process(parent_pid).kill()
    except: pass
    print("[Tracer] Done.")

if __name__ == "__main__":
    trace()
