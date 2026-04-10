import google.generativeai as genai
from ..utils.sim_runner import LogisimEmulator
from ..utils.tars_bridge import TarsBridge

class VerificationAgent:
    """
    验证性实验智能体。
    职责：操控 Logisim GUI 进行仿真测试并截图。
    """

    def __init__(self, config):
        self.config = config
        self.gui = ScreenControl()
        self.emulator = None
        self.bridge = TarsBridge(config.ollama)

    async def run(self, task: TaskRecord, circ_path: Path) -> TaskRecord:
        """执行验证流程 (V3.0: 语境化视觉操控)"""
        # 1. 确保仿真器已启动并锁定主窗口句柄
        if not self.emulator:
            self.emulator = LogisimEmulator(self.config)
            
        success = await self.emulator.launch_and_initialize()
        if not success:
            task.status = "failed"
            task.analysis_raw = "FAILED: Could not find or lock Logisim main window."
            return task

        win = self.emulator.main_window

        # 2. 获取物理屏幕锁，开始 GUI 操控
        async with ScreenLockContext(screen_lock):
            # 将窗口置顶
            self.gui.force_focus(win)
            
            save_dir = Path("output") / f"{task.task_name}.assets"
            save_dir.mkdir(parents=True, exist_ok=True)
            
            # --- 阶段 1: 初始画面捕获 ---
            stage1_path = save_dir / "init_state.png"
            pyautogui.screenshot(str(stage1_path), region=(win.left, win.top, win.width, win.height))
            
            # --- 阶段 2: 视觉语义操控 ---
            # 从任务描述中提取动作指令
            instruction = f"Looking at this Logisim window, {task.analysis_raw}"
            print(f"[Verification] Action: {instruction}")
            
            success_action = await self.bridge.perform_visual_action(instruction, stage1_path)
            
            if success_action:
                await asyncio.sleep(2) # 等待动作生效渲染
                
                # --- 阶段 3: 结果捕获与存证 ---
                result_path = save_dir / f"verified_{task.task_id}.png"
                pyautogui.screenshot(str(result_path), region=(win.left, win.top, win.width, win.height))
                
                task.assets.append(str(result_path.relative_to(Path("output"))))
                task.status = "finished"
                task.analysis_raw = f"SUCCESS: Visual action performed and verified."
            else:
                task.status = "failed"
                task.analysis_raw = "FAILED: UI-TARS could not execute the visual instruction."
            
        return task
            
        return task

    def close(self):
        """释放资源"""
        if self.emulator:
            self.emulator.terminate()
