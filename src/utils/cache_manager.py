"""
CacheManager — 轻量级任务缓存与续演管理器

设计目标：
- cache/ 目录存放所有中间产物的 JSON 序列化。
- 每个 TaskRecord 以 task_id 为 key 独立缓存。
- 解析结果 (ParsingResult) 整体缓存，下次启动自动恢复。
- 截图等二进制资产已保存在 output/ 中，JSON 中仅记录路径。
"""

import json
import shutil
from pathlib import Path
from typing import Optional

from ..core.models import TaskRecord, ParsingResult


CACHE_DIR = Path("cache")
PARSING_CACHE_FILE = CACHE_DIR / "parsing_result.json"
TASKS_CACHE_DIR = CACHE_DIR / "tasks"
DESIGN_SUBS_FILE = CACHE_DIR / "design_subtasks.json"


class CacheManager:
    """管理 cache/ 目录下的所有缓存，支持跨运行续演。"""

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self.tasks_dir = cache_dir / "tasks"
        self.parsing_file = cache_dir / "parsing_result.json"
        self.design_subs_file = cache_dir / "design_subtasks.json"

    def initialize(self):
        """确保缓存目录存在。"""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def clear(self):
        """彻底清除所有缓存（用于全新运行）。"""
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
        self.initialize()
        print("[Cache] 缓存已清空，将全新运行。")

    # ------------------------------------------------------------------ #
    # ParsingResult
    # ------------------------------------------------------------------ #
    def save_parsing_result(self, result: ParsingResult):
        """序列化并保存 ParsingResult。"""
        self.parsing_file.write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        print(f"[Cache] 解析结果已保存: {self.parsing_file}")

    def load_parsing_result(self) -> Optional[ParsingResult]:
        """从缓存加载 ParsingResult，若不存在返回 None。"""
        if not self.parsing_file.exists():
            return None
        try:
            data = json.loads(self.parsing_file.read_text(encoding="utf-8"))
            result = ParsingResult(**data)
            print(f"[Cache] ✅ 命中解析缓存，跳过 ParsingAgent。")
            return result
        except Exception as e:
            print(f"[Cache] 解析缓存损坏，将重新运行 ParsingAgent: {e}")
            return None

    # ------------------------------------------------------------------ #
    # TaskRecord
    # ------------------------------------------------------------------ #
    def _task_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.json"

    def save_task(self, task: TaskRecord):
        """序列化并保存单条 TaskRecord。"""
        self._task_path(task.task_id).write_text(
            task.model_dump_json(indent=2), encoding="utf-8"
        )

    def load_task(self, task_id: str) -> Optional[TaskRecord]:
        """加载单条 TaskRecord，若不存在或损坏返回 None。"""
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            return TaskRecord(**json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[Cache] 任务缓存 {task_id} 损坏: {e}")
            return None

    def is_task_done(self, task_id: str) -> bool:
        """检查某任务是否已完成（status == finished）。"""
        cached = self.load_task(task_id)
        return cached is not None and cached.status == "finished"

    def get_task_if_done(self, task: TaskRecord) -> Optional[TaskRecord]:
        """
        若缓存中对应 task_id 已完成，则返回缓存版本；否则返回 None。
        调用处可据此决定是否跳过。
        """
        cached = self.load_task(task.task_id)
        if cached and cached.status == "finished":
            print(f"[Cache] ✅ 命中缓存，跳过: {cached.task_name}")
            return cached
        return None

    # ------------------------------------------------------------------ #
    # Design sub-tasks mapping: design_task_id -> [sub_task_ids]
    # ------------------------------------------------------------------ #
    def save_design_subtasks(self, parent_id: str, sub_tasks: list[TaskRecord]):
        """保存某设计任务拆解出的子任务映射。"""
        existing = {}
        if self.design_subs_file.exists():
            try:
                existing = json.loads(self.design_subs_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        existing[parent_id] = [t.model_dump() for t in sub_tasks]
        self.design_subs_file.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 也把每个子任务单独保存一下方便查询
        for t in sub_tasks:
            self.save_task(t)

    def load_design_subtasks(self, parent_id: str) -> Optional[list[TaskRecord]]:
        """加载某设计任务的子任务列表，若不存在返回 None。"""
        if not self.design_subs_file.exists():
            return None
        try:
            data = json.loads(self.design_subs_file.read_text(encoding="utf-8"))
            if parent_id not in data:
                return None
            result = [TaskRecord(**item) for item in data[parent_id]]
            print(f"[Cache] ✅ 命中子任务缓存 ({len(result)} 条)，跳过重新拆解。")
            return result
        except Exception as e:
            print(f"[Cache] 子任务缓存 {parent_id} 损坏: {e}")
            return None
