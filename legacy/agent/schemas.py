"""
对外 JSON Schema（JSON Schema Draft 2020-12）
===================================================================
前端看板、后端服务、其他智能体都以这些 Schema 作为契约。
字段一旦发布即为稳定接口；新增字段只允许追加，不允许改名/删除。
"""

from __future__ import annotations

from typing import Any, Dict

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"

MODULES = ["user_insights", "product_dev", "marketing", "sales_conversion"]
CATEGORIES = ["progress", "blocker", "action", "feedback"]
STATUSES = ["open", "in_progress", "done", "blocked", "cancelled"]
CHANGE_TYPES = ["created", "updated", "status_changed", "merged", "confirmed"]


def _ref(name: str) -> Dict[str, Any]:
    return {"$ref": f"#/$defs/{name}"}


# ---------------------------------------------------------------------------
# 可复用的 $defs
# ---------------------------------------------------------------------------
DEFS: Dict[str, Any] = {
    "Segment": {
        "type": "object",
        "required": ["segment_id", "speaker", "text"],
        "properties": {
            "segment_id": {"type": "string", "description": "会议内唯一段落 id"},
            "speaker": {"type": "string", "description": "发言人（转写软件已区分）"},
            "text": {"type": "string", "description": "转写原文，禁止改写"},
            "timestamp": {"type": ["string", "null"], "description": "时间戳，如 00:12:34 或区间"},
            "order": {"type": "integer", "minimum": 0, "description": "段落序号，定位兜底"},
        },
        "additionalProperties": False,
    },
    "Transcript": {
        "type": "object",
        "required": ["meeting_id", "project_id", "date", "segments"],
        "properties": {
            "meeting_id": {"type": "string"},
            "project_id": {"type": "string"},
            "title": {"type": "string"},
            "date": {"type": "string", "format": "date"},
            "participants": {"type": "array", "items": {"type": "string"}},
            "agenda": {"type": "array", "items": {"type": "string"}, "description": "会议议程（若有）"},
            "segments": {"type": "array", "items": _ref("Segment")},
            "source": {"type": ["string", "null"], "description": "转写软件来源"},
        },
        "additionalProperties": False,
    },
    "EvidenceRef": {
        "type": "object",
        "required": ["meeting_id", "segment_id", "speaker", "quote"],
        "properties": {
            "meeting_id": {"type": "string"},
            "meeting_title": {"type": "string"},
            "segment_id": {"type": "string"},
            "speaker": {"type": "string"},
            "timestamp": {"type": ["string", "null"]},
            "order": {"type": "integer"},
            "quote": {"type": "string", "description": "原文摘录，逐字保留以便核验"},
        },
        "additionalProperties": False,
    },
    "FieldChange": {
        "type": "object",
        "required": ["field", "old_value", "new_value"],
        "properties": {
            "field": {"type": "string"},
            "old_value": {},
            "new_value": {},
        },
        "additionalProperties": False,
    },
    "MatterRevision": {
        "type": "object",
        "required": ["version", "meeting_id", "change_type", "snapshot"],
        "properties": {
            "version": {"type": "integer", "minimum": 1},
            "meeting_id": {"type": "string"},
            "meeting_date": {"type": "string"},
            "change_type": {"type": "string", "enum": CHANGE_TYPES},
            "note": {"type": "string"},
            "snapshot": {"type": "object", "description": "该版本业务字段完整快照"},
            "changes": {"type": "array", "items": _ref("FieldChange")},
            "raised_by": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    },
    "ConflictCandidate": {
        "type": "object",
        "required": ["value", "evidence", "meeting_id"],
        "properties": {
            "value": {},
            "evidence": _ref("EvidenceRef"),
            "meeting_id": {"type": "string"},
        },
        "additionalProperties": False,
    },
    "Conflict": {
        "type": "object",
        "required": ["field", "candidates"],
        "properties": {
            "field": {"type": "string"},
            "candidates": {"type": "array", "items": _ref("ConflictCandidate")},
            "note": {"type": "string"},
            "resolved": {"type": "boolean"},
            "resolution": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    },
    "MissingInfo": {
        "type": "object",
        "required": ["field", "reason"],
        "properties": {
            "field": {"type": "string"},
            "reason": {"type": "string"},
            "evidence": {"oneOf": [_ref("EvidenceRef"), {"type": "null"}]},
            "needs_confirmation": {"type": "boolean"},
        },
        "additionalProperties": False,
    },
    "Matter": {
        "type": "object",
        "required": ["matter_id", "project_id", "module", "category", "title", "status"],
        "properties": {
            "matter_id": {"type": "string", "description": "跨会议稳定 id"},
            "project_id": {"type": "string"},
            "module": {"type": "string", "enum": MODULES},
            "category": {"type": "string", "enum": CATEGORIES},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "status": {"type": "string", "enum": STATUSES},
            "owner": {"type": ["string", "null"], "description": "责任人；缺失为 null 并在 missing_fields 标注"},
            "due_date": {"type": ["string", "null"], "description": "截止时间；缺失为 null 并标注"},
            "priority": {"type": "string", "enum": ["low", "normal", "high"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "tags": {"type": "array", "items": {"type": "string"}},
            "evidence": {"type": "array", "items": _ref("EvidenceRef")},
            "history": {"type": "array", "items": _ref("MatterRevision")},
            "conflicts": {"type": "array", "items": _ref("Conflict")},
            "missing_fields": {"type": "array", "items": _ref("MissingInfo")},
            "first_seen_meeting": {"type": "string"},
            "first_seen_date": {"type": "string"},
            "last_seen_meeting": {"type": "string"},
            "last_updated_date": {"type": "string"},
            "merged_from": {"type": "array", "items": {"type": "string"}},
            "revised_count": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": False,
    },
    "MeetingQuality": {
        "type": "object",
        "required": ["meeting_id", "agenda_coverage", "speaker_balance", "issues"],
        "properties": {
            "meeting_id": {"type": "string"},
            "meeting_title": {"type": "string"},
            "date": {"type": "string"},
            "agenda_progress": {"type": "array", "items": {"type": "object"}},
            "agenda_coverage": {"type": "number", "minimum": 0, "maximum": 1},
            "decision_clarity": {"type": "object"},
            "off_topic": {"type": "array", "items": {"type": "object"}},
            "interruptions": {"type": "array", "items": {"type": "object"}},
            "speaker_stats": {"type": "array", "items": {"type": "object"}},
            "speaker_balance": {"type": "number", "minimum": 0, "maximum": 1},
            "open_item_count": {"type": "integer"},
            "issues": {"type": "array", "items": {"type": "string"}},
            "evidence_index": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
        },
        "additionalProperties": False,
    },
}

# ---------------------------------------------------------------------------
# 顶层 Schema
# ---------------------------------------------------------------------------
TRANSCRIPT_SCHEMA: Dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://example.com/schemas/transcript.json",
    "title": "MeetingTranscript",
    "description": "输入：语音转文字软件生成、可区分发言人的单场会议逐字稿",
    **{k: v for k, v in DEFS["Transcript"].items() if k != "$ref"},
    "$defs": {"Segment": DEFS["Segment"]},
}

MATTER_SCHEMA: Dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://example.com/schemas/matter.json",
    "title": "Matter",
    "description": "核心实体：跨会议去重重合并后的项目事项",
    **{k: v for k, v in DEFS["Matter"].items()},
    "$defs": {
        "EvidenceRef": DEFS["EvidenceRef"],
        "FieldChange": DEFS["FieldChange"],
        "MatterRevision": DEFS["MatterRevision"],
        "ConflictCandidate": DEFS["ConflictCandidate"],
        "Conflict": DEFS["Conflict"],
        "MissingInfo": DEFS["MissingInfo"],
    },
}

RETROSPECTIVE_SCHEMA: Dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://example.com/schemas/retrospective.json",
    "title": "Retrospective",
    "description": "输出：持续更新的项目复盘（四模块 × 四分类）",
    "type": "object",
    "required": ["project_id", "generated_at", "source_meeting_ids", "modules", "stats"],
    "properties": {
        "project_id": {"type": "string"},
        "generated_at": {"type": "string", "description": "ISO 8601 时间"},
        "source_meeting_ids": {"type": "array", "items": {"type": "string"}},
        "modules": {
            "type": "object",
            "description": "module -> category -> Matter[]",
            "properties": {
                m: {
                    "type": "object",
                    "properties": {c: {"type": "array", "items": _ref("Matter")} for c in CATEGORIES},
                }
                for m in MODULES
            },
        },
        "stats": {"type": "object"},
    },
    "$defs": {
        "Matter": DEFS["Matter"],
        "EvidenceRef": DEFS["EvidenceRef"],
        "FieldChange": DEFS["FieldChange"],
        "MatterRevision": DEFS["MatterRevision"],
        "ConflictCandidate": DEFS["ConflictCandidate"],
        "Conflict": DEFS["Conflict"],
        "MissingInfo": DEFS["MissingInfo"],
    },
}

BOARD_SCHEMA: Dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://example.com/schemas/board.json",
    "title": "BoardPayload",
    "description": "输出：前端看板直接消费的聚合载荷（GET /api/board 的响应体）",
    "type": "object",
    "required": ["project_id", "generated_at", "columns", "meetings", "stats"],
    "properties": {
        "project_id": {"type": "string"},
        "generated_at": {"type": "string"},
        "columns": {
            "type": "array",
            "description": "4 模块 × 4 分类 = 16 列看板",
            "items": {
                "type": "object",
                "required": ["module", "category", "label", "items"],
                "properties": {
                    "module": {"type": "string", "enum": MODULES},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "label": {"type": "string"},
                    "items": {"type": "array", "items": _ref("Matter")},
                },
            },
        },
        "meetings": {"type": "array", "items": _ref("MeetingQuality")},
        "stats": {"type": "object"},
    },
    "$defs": {
        "Matter": DEFS["Matter"],
        "MeetingQuality": DEFS["MeetingQuality"],
        "EvidenceRef": DEFS["EvidenceRef"],
        "FieldChange": DEFS["FieldChange"],
        "MatterRevision": DEFS["MatterRevision"],
        "ConflictCandidate": DEFS["ConflictCandidate"],
        "Conflict": DEFS["Conflict"],
        "MissingInfo": DEFS["MissingInfo"],
    },
}

QUALITY_SCHEMA: Dict[str, Any] = {
    "$schema": SCHEMA_DIALECT,
    "$id": "https://example.com/schemas/quality.json",
    "title": "MeetingQuality",
    "description": "输出：单场会议组织与发言规范分析（GET /api/meetings/{id}/quality）",
    **{k: v for k, v in DEFS["MeetingQuality"].items()},
}

ALL_SCHEMAS = {
    "transcript.schema.json": TRANSCRIPT_SCHEMA,
    "matter.schema.json": MATTER_SCHEMA,
    "retrospective.schema.json": RETROSPECTIVE_SCHEMA,
    "board.schema.json": BOARD_SCHEMA,
    "quality.schema.json": QUALITY_SCHEMA,
}
