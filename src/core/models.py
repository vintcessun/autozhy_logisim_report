from pydantic import BaseModel, Field
from typing import Literal, List, Optional
import uuid

class TaskRecord(BaseModel):
    """
    智能体之间通过 TaskRecord 对象进行状态传递。
    符合 SPECIFICATIONS.md Section 2 定义。
    """
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_name: str
    task_type: Literal["verification", "design", "challenge"]
    source_circ: List[str] = []  # 源码绝对路径
    reference_circ: Optional[str] = None  # 参考设计电路路径
    status: Literal["pending", "executing", "finished", "failed"] = "pending"
    assets: List[str] = []       # 关联的截图路径 (相对于 .assets/)
    analysis_raw: str = ""       # 原始分析文本（原子场景描述）
    section_text: str = ""       # 阶段一提取的原始 PDF 段落（供报告智能体引用）
    target_subcircuit: Optional[str] = None  # 明确指定的目标子电路名
    logic_check_pass: bool = False  # 拓扑可达性状态
    
    # 扩展字段：实验目的与环境
    experiment_objective: str = ""
    experiment_environment: str = ""
    thinking_questions: List[str] = []

class ParsingResult(BaseModel):
    """
    内容解析智能体的结构化输出结果。
    """
    verification_tasks: List[TaskRecord] = []
    design_tasks: List[TaskRecord] = []
    reference_reports: List[str] = []  # 参考报告 (PDF/Docx) 的路径列表
    instruction_docs: List[str] = []   # 教师指导书 + Word 模板
    raw_experiments: List[dict] = []   # 阶段一原始实验分类列表（含 section_text，供下游全量参考）

class LogicalNode(BaseModel):
    """CircuitSchema 中的逻辑节点"""
    id: str
    type: str
    label: Optional[str] = None
    bit_width: Optional[int] = None
    is_input: Optional[bool] = None
    radix: Optional[str] = None

class Connectivity(BaseModel):
    """CircuitSchema 中的连接关系"""
    source: str
    target: str
    semantic: str = "data_bus"

class CircuitSchema(BaseModel):
    """
    电路结构 JSON。
    符合 PIPELINE.md Section 2.1 定义。
    """
    circuit_metadata: dict = {
        "filename": "unknown.circ",
        "has_clock": False
    }
    logical_nodes: List[LogicalNode] = []
    connectivity: List[Connectivity] = []
