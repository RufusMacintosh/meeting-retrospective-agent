"""
会议复盘智能体 —— 核心数据模型
===================================================================
所有模型均为可序列化 dataclass，作为「引擎内部表示」与「对外 JSON」的
唯一事实来源（single source of truth）。对外输出的 JSON Schema 见 schemas.py。

设计要点：
  1. EvidenceRef 是证据链的最小单元，任何结论型字段都必须可回溯到原文。
  2. Matter（事项）是跨会议去重合并、状态更新、版本追踪的核心实体，
     拥有稳定的 matter_id，不随会议轮次变化。
  3. MatterRevision 记录每一次变更的完整字段快照，支持单一事项历史查询
     与任意版本回溯。
  4. Conflict / MissingInfo 显式标注"信息冲突 / 信息缺失"，杜绝臆造。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 受控枚举（用常量而非自由字符串，保证看板消费方字段稳定）
# ---------------------------------------------------------------------------
PROJECT_MODULES: List[str] = [
    "user_insights",    # 用户洞察
    "product_dev",      # 产品开发
    "marketing",        # 市场营销
    "sales_conversion", # 销售转化
]

ITEM_CATEGORIES: List[str] = [
    "progress",   # 进度
    "blocker",    # 卡点
    "action",     # 行动
    "feedback",   # 反馈
]

ITEM_STATUSES: List[str] = [
    "open",         # 待办 / 新提出
    "in_progress",  # 进行中
    "done",         # 已完成 / 已闭环
    "blocked",      # 被阻塞
    "cancelled",    # 已取消 / 不再推进
]

MODULE_LABELS: Dict[str, str] = {
    "user_insights": "用户洞察",
    "product_dev": "产品开发",
    "marketing": "市场营销",
    "sales_conversion": "销售转化",
}

CATEGORY_LABELS: Dict[str, str] = {
    "progress": "进度",
    "blocker": "卡点",
    "action": "行动",
    "feedback": "反馈",
}

STATUS_LABELS: Dict[str, str] = {
    "open": "待办",
    "in_progress": "进行中",
    "done": "已完成",
    "blocked": "被阻塞",
    "cancelled": "已取消",
}


def _clean(value: Any) -> Any:
    """递归清理：dataclass -> dict，None 保留（缺失信息必须显式）。"""
    if hasattr(value, "__dataclass_fields__"):
        return {k: _clean(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# 输入层：逐字稿
# ---------------------------------------------------------------------------
@dataclass
class Segment:
    """一个最小发言单元。语音转文字软件通常已给出说话人与时间信息。"""
    segment_id: str
    speaker: str
    text: str
    timestamp: Optional[str] = None       # 形如 "00:12:34" 或 "00:12:34-00:13:10"
    order: int = 0                        # 段落在会议内的序号（可定位信息）

    def to_dict(self) -> Dict[str, Any]:
        return _clean(self)


@dataclass
class Meeting:
    """单场会议逐字稿。"""
    meeting_id: str
    project_id: str
    title: str
    date: str                              # ISO 日期 YYYY-MM-DD
    participants: List[str] = field(default_factory=list)
    agenda: List[str] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    source: Optional[str] = None           # 转写软件来源，便于溯源

    def to_dict(self) -> Dict[str, Any]:
        return _clean(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Meeting":
        return Meeting(
            meeting_id=data["meeting_id"],
            project_id=data["project_id"],
            title=data.get("title", ""),
            date=data.get("date", ""),
            participants=list(data.get("participants", [])),
            agenda=list(data.get("agenda", [])),
            segments=[
                Segment(
                    segment_id=s["segment_id"],
                    speaker=s.get("speaker", "未知"),
                    text=s.get("text", ""),
                    timestamp=s.get("timestamp"),
                    order=int(s.get("order", i)),
                )
                for i, s in enumerate(data.get("segments", []))
            ],
            source=data.get("source"),
        )


# ---------------------------------------------------------------------------
# 证据与版本
# ---------------------------------------------------------------------------
@dataclass
class EvidenceRef:
    """指向原始逐字稿的可核验引用。"""
    meeting_id: str
    meeting_title: str
    segment_id: str
    speaker: str
    timestamp: Optional[str]
    order: int
    quote: str                             # 原文摘录，不做改写

    def to_dict(self) -> Dict[str, Any]:
        return _clean(self)


@dataclass
class FieldChange:
    """单个字段的变更记录。"""
    field: str
    old_value: Any
    new_value: Any


@dataclass
class MatterRevision:
    """事项的一个不可变版本快照。"""
    version: int
    meeting_id: str
    meeting_date: str
    change_type: str                       # created / updated / status_changed / merged / confirmed
    note: str
    snapshot: Dict[str, Any]               # 该版本下事项全部业务字段的快照
    changes: List[FieldChange] = field(default_factory=list)
    raised_by: Optional[str] = None        # 触发本次变更的发言人

    def to_dict(self) -> Dict[str, Any]:
        return _clean(self)


@dataclass
class ConflictCandidate:
    """冲突选项中的一条候选值及其证据。"""
    value: Any
    evidence: EvidenceRef
    meeting_id: str


@dataclass
class Conflict:
    """同一字段出现互斥取值时的显式标注，不做自动裁决。"""
    field: str
    candidates: List[ConflictCandidate] = field(default_factory=list)
    note: str = "存在冲突，需人工确认"
    resolved: bool = False
    resolution: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _clean(self)


@dataclass
class MissingInfo:
    """字段信息缺失的显式标注。"""
    field: str
    reason: str
    evidence: Optional[EvidenceRef] = None
    needs_confirmation: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return _clean(self)


# ---------------------------------------------------------------------------
# 核心实体：事项
# ---------------------------------------------------------------------------
@dataclass
class Matter:
    """
    跨会议稳定存在的事项实体。

    matter_id 在首次创建时生成，后续会议无论合并或更新都沿用同一 id，
    因此前端可用它作为看板卡片的稳定 key，并据此查询历史。
    """
    matter_id: str
    project_id: str
    module: str                            # PROJECT_MODULES 之一
    category: str                          # ITEM_CATEGORIES 之一
    title: str
    description: str
    status: str = "open"
    owner: Optional[str] = None
    due_date: Optional[str] = None
    priority: str = "normal"               # low / normal / high
    confidence: float = 1.0                # 抽取置信度 0~1
    tags: List[str] = field(default_factory=list)

    evidence: List[EvidenceRef] = field(default_factory=list)
    history: List[MatterRevision] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    missing_fields: List[MissingInfo] = field(default_factory=list)

    first_seen_meeting: str = ""
    first_seen_date: str = ""
    last_seen_meeting: str = ""
    last_updated_date: str = ""
    merged_from: List[str] = field(default_factory=list)  # 被合并进来的原始 item id
    revised_count: int = 0

    def snapshot(self) -> Dict[str, Any]:
        """返回业务字段快照（不含 history/evidence 等元数据），用于版本对比。"""
        return _clean({
            "title": self.title,
            "description": self.description,
            "module": self.module,
            "category": self.category,
            "status": self.status,
            "owner": self.owner,
            "due_date": self.due_date,
            "priority": self.priority,
            "tags": list(self.tags),
        })

    def to_dict(self, include_history: bool = True) -> Dict[str, Any]:
        data = _clean(self)
        if not include_history:
            data.pop("history", None)
        return data


# ---------------------------------------------------------------------------
# 输出层：会议规范分析 / 复盘 / 看板
# ---------------------------------------------------------------------------
@dataclass
class TopicDeviation:
    """跑题片段。"""
    segment_id: str
    speaker: str
    timestamp: Optional[str]
    quote: str
    reason: str


@dataclass
class Interruption:
    """打断事件。"""
    at_segment_id: str
    interrupter: str
    interrupted: str
    timestamp: Optional[str]
    quote: str
    signal: str


@dataclass
class SpeakerStat:
    """发言人参与度统计。"""
    speaker: str
    segment_count: int
    word_count: int
    share: float                           # 发言字数占比 0~1


@dataclass
class MeetingQuality:
    """单场会议的「组织与发言规范」分析。"""
    meeting_id: str
    meeting_title: str
    date: str
    agenda_progress: List[Dict[str, Any]]  # 每项议程的覆盖情况
    agenda_coverage: float                 # 议程覆盖度 0~1
    decision_clarity: Dict[str, Any]       # 结论/责任人/截止 明确度
    off_topic: List[TopicDeviation]
    interruptions: List[Interruption]
    speaker_stats: List[SpeakerStat]
    speaker_balance: float                 # 发言均衡度 0~1（1 为完全均衡）
    open_item_count: int
    issues: List[str]                      # 人类可读的问题清单
    evidence_index: Dict[str, List[str]]   # issue -> [segment_id]

    def to_dict(self) -> Dict[str, Any]:
        return _clean(self)


@dataclass
class Retrospective:
    """持续更新的项目复盘（按四模块组织）。"""
    project_id: str
    generated_at: str
    source_meeting_ids: List[str]
    modules: Dict[str, Dict[str, List[Dict[str, Any]]]]  # module -> category -> [matter dict]
    stats: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return _clean(self)
