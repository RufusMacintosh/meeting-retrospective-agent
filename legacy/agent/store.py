"""
事项存储与状态引擎
===================================================================
职责：
  * 跨会议事项去重合并（稳定 matter_id）
  * 状态迁移与字段更新
  * 不可变版本快照（历史版本追踪 / 单一事项历史查询）
  * 冲突与缺失的显式标注
  * 复盘 / 看板聚合输出

设计原则：任何自动写入都必须有证据支撑；冲突不自动裁决，只列候选。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    CATEGORY_LABELS,
    ITEM_CATEGORIES,
    MODULE_LABELS,
    PROJECT_MODULES,
    Conflict,
    ConflictCandidate,
    EvidenceRef,
    FieldChange,
    Matter,
    MatterRevision,
    Meeting,
    MeetingQuality,
    Retrospective,
)
from . import rules


class MatterStore:
    """项目级事项存储（内存实现，可替换为数据库）。"""

    def __init__(self, project_id: str, merge_threshold: float = rules.MERGE_THRESHOLD):
        self.project_id = project_id
        self.merge_threshold = merge_threshold
        self.matters: Dict[str, Matter] = {}
        self.meeting_index: Dict[str, Meeting] = {}
        self.quality_reports: Dict[str, MeetingQuality] = {}
        self.ingest_log: List[Dict[str, Any]] = []
        self._seq = 0

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _next_id(self) -> str:
        self._seq += 1
        return f"M-{self._seq:04d}"

    @staticmethod
    def _diff(before: Dict[str, Any], after: Dict[str, Any]) -> List[FieldChange]:
        changes: List[FieldChange] = []
        for key in after:
            if before.get(key) != after.get(key):
                changes.append(FieldChange(field=key, old_value=before.get(key), new_value=after.get(key)))
        return changes

    def _append_revision(self, matter: Matter, meeting: Meeting,
                         change_type: str, note: str,
                         changes: List[FieldChange], raised_by: Optional[str]) -> None:
        version = len(matter.history) + 1
        matter.history.append(MatterRevision(
            version=version,
            meeting_id=meeting.meeting_id,
            meeting_date=meeting.date,
            change_type=change_type,
            note=note,
            snapshot=matter.snapshot(),
            changes=changes,
            raised_by=raised_by,
        ))
        matter.revised_count = version - 1

    def _resolve_missing(self, matter: Matter, field: str) -> None:
        matter.missing_fields = [m for m in matter.missing_fields if m.field != field]

    def _record_conflict(self, matter: Matter, field: str,
                         old_value: Any, new_value: Any,
                         new_evidence: EvidenceRef, meeting: Meeting) -> None:
        """同字段取值冲突：只登记候选，不裁决。"""
        existing = next((c for c in matter.conflicts if c.field == field), None)
        if existing is None:
            old_ev = matter.evidence[0] if matter.evidence else new_evidence
            existing = Conflict(field=field, note="同一字段出现互斥取值，需人工确认；系统不自动裁决")
            matter.conflicts.append(existing)
            existing.candidates.append(ConflictCandidate(
                value=old_value, evidence=old_ev, meeting_id=matter.first_seen_meeting))
        # 避免同一值重复登记
        if any(c.value == new_value for c in existing.candidates):
            return
        existing.candidates.append(ConflictCandidate(
            value=new_value, evidence=new_evidence, meeting_id=meeting.meeting_id))

    def _find_best_match(self, draft: Matter) -> Tuple[Optional[Matter], float]:
        best: Optional[Matter] = None
        best_score = 0.0
        for matter in self.matters.values():
            score = rules.match_score(
                matter.title, matter.module,
                draft.title, draft.module,
                draft.description,
            )
            if score > best_score:
                best, best_score = matter, score
        if best_score >= self.merge_threshold:
            return best, best_score
        return None, best_score

    def _merge(self, target: Matter, draft: Matter, meeting: Meeting, score: float) -> str:
        """把草稿合并进既有事项，返回变更类型。"""
        before = target.snapshot()
        incoming_ev = draft.evidence[0]

        # ---- owner：空则填充，冲突则登记 ----
        if draft.owner:
            if not target.owner:
                target.owner = draft.owner
                self._resolve_missing(target, "owner")
            elif target.owner != draft.owner:
                self._record_conflict(target, "owner", target.owner, draft.owner, incoming_ev, meeting)

        # ---- due_date：空则填充，冲突则登记 ----
        if draft.due_date:
            if not target.due_date:
                target.due_date = draft.due_date
                self._resolve_missing(target, "due_date")
            elif target.due_date != draft.due_date:
                self._record_conflict(target, "due_date", target.due_date, draft.due_date, incoming_ev, meeting)

        # ---- category 演化（如 行动 -> 进度）----
        category_changed = draft.category != target.category
        if category_changed and draft.confidence >= target.confidence:
            target.category = draft.category

        # ---- 状态推断 ----
        new_status, signals = rules.infer_status(draft.description, target.status)
        status_changed = bool(new_status and new_status != target.status)
        if status_changed:
            target.status = new_status

        # ---- 优先级：仅升级，不降级 ----
        rank = {"low": 0, "normal": 1, "high": 2}
        if rank.get(draft.priority, 1) > rank.get(target.priority, 1):
            target.priority = draft.priority

        # ---- 证据去重后追加 ----
        known = {e.segment_id + "@" + e.meeting_id for e in target.evidence}
        key = incoming_ev.segment_id + "@" + incoming_ev.meeting_id
        if key not in known:
            target.evidence.append(incoming_ev)

        # ---- 合并来源登记 ----
        if draft.matter_id not in target.merged_from:
            target.merged_from.append(draft.matter_id)

        # ---- 置信度取较高者 ----
        target.confidence = round(max(target.confidence, draft.confidence), 2)
        target.last_seen_meeting = meeting.meeting_id
        target.last_updated_date = meeting.date

        after = target.snapshot()
        changes = self._diff(before, after)

        if status_changed:
            change_type = "status_changed"
            note = f"依据「{'/'.join(signals)}」将状态更新为 {new_status}"
        elif meeting.meeting_id != target.first_seen_meeting:
            change_type = "merged"
            note = f"与跨会议事项匹配（相似度 {score:.2f}）并合并证据"
        elif changes:
            change_type = "updated"
            note = "同一会议内补充信息"
        else:
            change_type = "merged"
            note = f"匹配到既有事项（相似度 {score:.2f}），仅追加证据"

        self._append_revision(target, meeting, change_type, note, changes, incoming_ev.speaker)
        return change_type

    def _create(self, draft: Matter, meeting: Meeting) -> Matter:
        matter = Matter(
            matter_id=self._next_id(),
            project_id=draft.project_id,
            module=draft.module,
            category=draft.category,
            title=draft.title,
            description=draft.description,
            status=draft.status,
            owner=draft.owner,
            due_date=draft.due_date,
            priority=draft.priority,
            confidence=draft.confidence,
            tags=list(draft.tags),
            evidence=list(draft.evidence),
            conflicts=list(draft.conflicts),
            missing_fields=list(draft.missing_fields),
            first_seen_meeting=draft.first_seen_meeting,
            first_seen_date=draft.first_seen_date,
            last_seen_meeting=meeting.meeting_id,
            last_updated_date=meeting.date,
            merged_from=[draft.matter_id],
        )
        self._append_revision(
            matter, meeting, "created", "首次抽取该事项",
            self._diff({}, matter.snapshot()),
            matter.evidence[0].speaker if matter.evidence else None,
        )
        self.matters[matter.matter_id] = matter
        return matter

    # ------------------------------------------------------------------
    # 对外：摄入
    # ------------------------------------------------------------------
    def ingest_meeting(self, meeting: Meeting, drafts: List[Matter],
                       quality: MeetingQuality) -> Dict[str, Any]:
        """把一场会议的抽取结果增量并入项目复盘。"""
        self.meeting_index[meeting.meeting_id] = meeting
        self.quality_reports[meeting.meeting_id] = quality

        created, merged = 0, 0
        changed: List[Dict[str, str]] = []
        for draft in drafts:
            target, score = self._find_best_match(draft)
            if target is None:
                matter = self._create(draft, meeting)
                created += 1
                changed.append({"matter_id": matter.matter_id, "action": "created", "title": matter.title})
            else:
                change_type = self._merge(target, draft, meeting, score)
                merged += 1
                changed.append({
                    "matter_id": target.matter_id,
                    "action": change_type,
                    "title": target.title,
                    "match_score": round(score, 3),
                })

        record = {
            "meeting_id": meeting.meeting_id,
            "date": meeting.date,
            "drafts": len(drafts),
            "created": created,
            "merged": merged,
            "changes": changed,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        self.ingest_log.append(record)
        return record

    # ------------------------------------------------------------------
    # 对外：查询
    # ------------------------------------------------------------------
    def get_matter(self, matter_id: str, include_history: bool = True) -> Optional[Dict[str, Any]]:
        matter = self.matters.get(matter_id)
        return matter.to_dict(include_history=include_history) if matter else None

    def get_history(self, matter_id: str) -> Optional[List[Dict[str, Any]]]:
        matter = self.matters.get(matter_id)
        return [r.to_dict() for r in matter.history] if matter else None

    def get_revision(self, matter_id: str, version: int) -> Optional[Dict[str, Any]]:
        matter = self.matters.get(matter_id)
        if not matter:
            return None
        for r in matter.history:
            if r.version == version:
                return r.to_dict()
        return None

    def get_evidence(self, matter_id: str) -> Optional[List[Dict[str, Any]]]:
        matter = self.matters.get(matter_id)
        return [e.to_dict() for e in matter.evidence] if matter else None

    def locate(self, matter_id: str, segment_id: str) -> Optional[Dict[str, Any]]:
        """按事项 + 段落 id 精确定位回原文。"""
        matter = self.matters.get(matter_id)
        if not matter:
            return None
        for ev in matter.evidence:
            if ev.segment_id == segment_id:
                return {
                    "matter_id": matter_id,
                    **ev.to_dict(),
                    "meeting_title": ev.meeting_title,
                }
        return None

    def get_retrospective(self) -> Retrospective:
        modules: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
            m: {c: [] for c in ITEM_CATEGORIES} for m in PROJECT_MODULES
        }
        for matter in sorted(self.matters.values(), key=lambda x: x.matter_id):
            modules[matter.module][matter.category].append(matter.to_dict(include_history=False))

        stats = self._stats()
        return Retrospective(
            project_id=self.project_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            source_meeting_ids=list(self.meeting_index.keys()),
            modules=modules,
            stats=stats,
        )

    def get_board_payload(self) -> Dict[str, Any]:
        columns: List[Dict[str, Any]] = []
        for module in PROJECT_MODULES:
            for category in ITEM_CATEGORIES:
                items = [
                    m.to_dict(include_history=False)
                    for m in self.matters.values()
                    if m.module == module and m.category == category
                ]
                columns.append({
                    "module": module,
                    "category": category,
                    "label": f"{MODULE_LABELS[module]} · {CATEGORY_LABELS[category]}",
                    "items": items,
                })
        return {
            "project_id": self.project_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "columns": columns,
            "meetings": [q.to_dict() for q in self.quality_reports.values()],
            "stats": self._stats(),
        }

    def _stats(self) -> Dict[str, Any]:
        by_status = {s: 0 for s in rules.ITEM_STATUSES}
        by_module = {m: 0 for m in PROJECT_MODULES}
        conflict_count = missing_count = evidence_count = 0
        for m in self.matters.values():
            by_status[m.status] = by_status.get(m.status, 0) + 1
            by_module[m.module] = by_module.get(m.module, 0) + 1
            conflict_count += len(m.conflicts)
            missing_count += len(m.missing_fields)
            evidence_count += len(m.evidence)
        quality_issues = sum(len(q.issues) for q in self.quality_reports.values())
        return {
            "matter_count": len(self.matters),
            "meeting_count": len(self.meeting_index),
            "by_status": by_status,
            "by_module": by_module,
            "conflict_count": conflict_count,
            "missing_field_count": missing_count,
            "evidence_count": evidence_count,
            "merge_count": sum(max(0, len(m.merged_from) - 1) for m in self.matters.values()),
            "quality_issue_count": quality_issues,
            "closed_rate": round(
                (by_status.get("done", 0) + by_status.get("cancelled", 0)) / len(self.matters), 2
            ) if self.matters else 1.0,
        }
