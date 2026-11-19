"""
事项抽取器（可插拔）
===================================================================
Extractor 是统一接口，生产环境可替换为 LLM 实现；本仓库提供可离线运行的
RuleBasedExtractor 作为基线。两者都必须满足同一约束：

  * 每条事项必须携带 EvidenceRef（segment_id + 原文摘录），可回原文核验；
  * 抽不到的字段置 None，并写入 missing_fields，绝不臆造；
  * 输出为 Matter 草稿，matter_id 为临时 id，由 Store 在合并阶段赋予稳定 id。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from .models import EvidenceRef, Meeting, Matter, MissingInfo
from . import rules


def make_title(text: str, max_len: int = 34) -> str:
    """从发言中提炼短标题：取首个分句（过短则拼接次句），去语气词，不改写事实。"""
    t = re.sub(r"^\s*(那个|这个|嗯|所以|然后|就是说|另外|还有|OK|好的)[，,、：:\s]*", "", (text or "").strip())
    parts = [p.strip() for p in re.split(r"[。！？；\n]", t) if p.strip()]
    if not parts:
        return (text or "").strip()[:max_len]
    first = parts[0]
    # "听下来方向很清楚"这类过渡句信息量低，拼接下一句以保留事实
    if len(first) < 12 and len(parts) > 1:
        first = first + "。" + parts[1]
    if len(first) > max_len:
        first = first[:max_len] + "…"
    return first


class Extractor(ABC):
    """抽取器统一接口。"""

    @abstractmethod
    def extract(self, meeting: Meeting) -> List[Matter]:
        """从单场会议抽取事项草稿列表。"""


class RuleBasedExtractor(Extractor):
    """
    规则基线抽取器：基于关键词信号 + 模式匹配。
    优点是可解释、离线可跑；局限是语义泛化弱。生产建议换用 LLMExtractor。
    """

    def extract(self, meeting: Meeting) -> List[Matter]:
        items: List[Matter] = []
        for seg in meeting.segments:
            text = (seg.text or "").strip()
            if _word_count(text) < 8:
                continue  # 过滤纯附和

            category, cat_conf = rules.classify_category(text)
            module, mod_conf = rules.classify_module(text)
            # 只有出现明确类别信号（非兜底低置信）才抽为事项
            if cat_conf < 0.5:
                continue

            owner = rules.extract_owner(text, seg.speaker, meeting.participants)
            due = rules.extract_due(text)
            priority = rules.extract_priority(text)
            confidence = round(min(cat_conf, mod_conf) if mod_conf < 1.0 else cat_conf, 2)

            evidence = EvidenceRef(
                meeting_id=meeting.meeting_id,
                meeting_title=meeting.title,
                segment_id=seg.segment_id,
                speaker=seg.speaker,
                timestamp=seg.timestamp,
                order=seg.order,
                quote=text[:300],
            )

            missing: List[MissingInfo] = []
            if not owner:
                missing.append(MissingInfo(
                    field="owner",
                    reason="原文未出现明确责任人（无 @/由X负责/我来 等表达）",
                    evidence=evidence,
                ))
            if not due:
                missing.append(MissingInfo(
                    field="due_date",
                    reason="原文未出现明确截止时间（保留相对表述也不会臆造绝对日期）",
                    evidence=evidence,
                ))
            if mod_conf < 0.5:
                missing.append(MissingInfo(
                    field="module",
                    reason="原文缺少模块关键词，已回退到 product_dev，需人工确认",
                    evidence=evidence,
                ))

            items.append(Matter(
                matter_id=f"draft:{meeting.meeting_id}:{seg.segment_id}",
                project_id=meeting.project_id,
                module=module,
                category=category,
                title=make_title(text),
                description=text,
                status="open",
                owner=owner,
                due_date=due,
                priority=priority,
                confidence=confidence,
                evidence=[evidence],
                missing_fields=missing,
                first_seen_meeting=meeting.meeting_id,
                first_seen_date=meeting.date,
                last_seen_meeting=meeting.meeting_id,
                last_updated_date=meeting.date,
            ))
        return items


LLM_EXTRACT_SYSTEM_PROMPT = """你是会议复盘助手。请从给定的会议发言段落中抽取"事项"。
严格约束：
1. 只能依据原文，不得臆造；抽不到的字段填 null。
2. 每条事项必须给出 evidence：segment_id、speaker、timestamp、quote（逐字摘录）。
3. module 只能是 user_insights / product_dev / marketing / sales_conversion。
4. category 只能是 progress / blocker / action / feedback。
5. 输出 JSON 数组，每项字段：title, description, module, category, owner, due_date, priority, status, confidence, evidence(object), missing_fields(string[])。
6. 相对时间（如下周三）原样保留在 due_date，不要推断成具体日期。
"""


class LLMExtractor(Extractor):
    """
    LLM 抽取器（生产环境接口预留）。
    需要外部环境注入一个 callable：call_llm(system_prompt, user_prompt) -> str(JSON)。
    未注入时明确报错，不静默回退，以免误以为使用了 LLM。
    """

    def __init__(self, call_llm, max_retries: int = 2):
        self.call_llm = call_llm
        self.max_retries = max_retries

    def extract(self, meeting: Meeting) -> List[Matter]:
        payload = json.dumps({
            "meeting_id": meeting.meeting_id,
            "project_id": meeting.project_id,
            "date": meeting.date,
            "agenda": meeting.agenda,
            "participants": meeting.participants,
            "segments": [s.to_dict() for s in meeting.segments],
        }, ensure_ascii=False)
        user_prompt = f"会议逐字稿如下，请抽取事项：\n{payload}"

        last_error: Optional[Exception] = None
        for _ in range(self.max_retries + 1):
            try:
                raw = self.call_llm(LLM_EXTRACT_SYSTEM_PROMPT, user_prompt)
                data = json.loads(_strip_code_fence(raw))
                return self._to_matters(data, meeting)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        raise RuntimeError(f"LLM 抽取失败（已重试 {self.max_retries} 次）: {last_error}")

    def _to_matters(self, data: Any, meeting: Meeting) -> List[Matter]:
        if isinstance(data, dict):
            data = data.get("items", [])
        result: List[Matter] = []
        for i, raw in enumerate(data):
            ev = raw.get("evidence") or {}
            seg_id = ev.get("segment_id")
            if not seg_id:
                continue  # 无证据的条目丢弃
            seg = next((s for s in meeting.segments if s.segment_id == seg_id), None)
            if seg is None:
                continue
            evidence = EvidenceRef(
                meeting_id=meeting.meeting_id,
                meeting_title=meeting.title,
                segment_id=seg_id,
                speaker=ev.get("speaker") or seg.speaker,
                timestamp=ev.get("timestamp") or seg.timestamp,
                order=seg.order,
                quote=ev.get("quote") or seg.text[:300],
            )
            missing = [MissingInfo(field=f, reason="LLM 标记为缺失", evidence=evidence, needs_confirmation=True)
                       for f in (raw.get("missing_fields") or [])]
            result.append(Matter(
                matter_id=f"draft:{meeting.meeting_id}:{seg_id}:{i}",
                project_id=meeting.project_id,
                module=raw.get("module") if raw.get("module") in rules.PROJECT_MODULES else "product_dev",
                category=raw.get("category") if raw.get("category") in rules.ITEM_CATEGORIES else "progress",
                title=raw.get("title") or make_title(seg.text),
                description=raw.get("description") or seg.text,
                status=raw.get("status") if raw.get("status") in rules.ITEM_STATUSES else "open",
                owner=raw.get("owner"),
                due_date=raw.get("due_date"),
                priority=raw.get("priority") if raw.get("priority") in ("low", "normal", "high") else "normal",
                confidence=float(raw.get("confidence", 0.8)),
                evidence=[evidence],
                missing_fields=missing,
                first_seen_meeting=meeting.meeting_id,
                first_seen_date=meeting.date,
                last_seen_meeting=meeting.meeting_id,
                last_updated_date=meeting.date,
            ))
        return result


def _strip_code_fence(raw: str) -> str:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s


def _word_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))
