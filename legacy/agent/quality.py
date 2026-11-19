"""
会议组织与发言规范分析
===================================================================
输入单场会议逐字稿 + 该会议抽取到的事项草稿，输出可核验的 MeetingQuality。

覆盖的规范维度：
  1. 议程推进   —— 每项议程是否被讨论、覆盖度
  2. 结论明确度 —— 结论信号 / 责任人 / 截止时间的明确比例
  3. 跑题       —— 与议程及会议主题几乎无重叠的长发言
  4. 打断       —— 显式打断词 或 极短时间内不同发言人切换
  5. 发言失衡   —— 单人字数占比过高
  6. 事项未闭环 —— 状态未达 done/cancelled 的事项数

所有问题都带 evidence_index（issue -> segment_id[]），可回原文核验。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    Meeting,
    Matter,
    MeetingQuality,
    TopicDeviation,
    Interruption,
    SpeakerStat,
)
from . import rules


def parse_ts(value: Optional[str]) -> Optional[int]:
    """把时间戳解析为秒数（取区间起点），无法解析返回 None。"""
    if not value:
        return None
    head = re.split(r"[-–~—到至]", value)[0].strip()
    parts = head.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 3:
        h, m, s = nums
        return h * 3600 + m * 60 + s
    if len(nums) == 2:
        m, s = nums
        return m * 60 + s
    if len(nums) == 1:
        return nums[0]
    return None


def _word_count(text: str) -> int:
    return len(re.sub(r"\s+", "", text or ""))


CONTROL_SIGNALS = ["回到", "拉回", "别跑", "跑题", "时间到", "提醒一下", "下一个议程", "按顺序", "收个尾"]

# 跑题阈值：中文 2-gram 重叠率低于该值视为离群发言。
# 经验值，可按项目词表调整；配置化以便审计与调优。
OFF_TOPIC_THRESHOLD = 0.10


def _topic_terms(meeting: Meeting) -> set:
    """
    会议主题词 = 标题/议程的 2-gram
              ∪ 在至少 2 个不同发言段落中复现的 2-gram（会议内共识主题）。
    这样"虽不在议程上、但全场都在讨论"的内容不会被误判为跑题。
    """
    terms: set = set()
    for source in [meeting.title] + list(meeting.agenda):
        terms |= rules.ngrams(source)
    df: Dict[str, int] = {}
    for seg in meeting.segments:
        for gram in rules.ngrams(seg.text):
            df[gram] = df.get(gram, 0) + 1
    for gram, count in df.items():
        if count >= 2:
            terms.add(gram)
    return terms


def analyze_agenda(meeting: Meeting) -> Tuple[List[Dict[str, Any]], float]:
    """议程推进分析：返回 (每项议程明细, 覆盖度)。"""
    if not meeting.agenda:
        return [], 1.0

    details: List[Dict[str, Any]] = []
    covered = 0
    seg_grams = [(s, rules.ngrams(s.text)) for s in meeting.segments]

    for idx, item in enumerate(meeting.agenda, 1):
        a_grams = rules.ngrams(item)
        matched: List[str] = []
        if a_grams:
            for seg, sg in seg_grams:
                if not sg:
                    continue
                overlap = len(a_grams & sg) / len(a_grams)
                if overlap >= 0.34:
                    matched.append(seg.segment_id)
        is_covered = len(matched) > 0
        covered += int(is_covered)
        details.append({
            "agenda_index": idx,
            "agenda": item,
            "covered": is_covered,
            "discussed_segments": matched,
            "note": "" if is_covered else "本场会议未见对应讨论（依据转写文本关键词匹配）",
        })
    return details, round(covered / len(meeting.agenda), 2)


def analyze_off_topic(meeting: Meeting) -> List[TopicDeviation]:
    """跑题检测：长发言且与会议主题词几乎无重叠。"""
    terms = _topic_terms(meeting)
    result: List[TopicDeviation] = []
    if not terms:
        return result
    for seg in meeting.segments:
        text = seg.text or ""
        if rules.keyword_hits(text, rules.INTERRUPTION_SIGNALS):
            continue  # 插话交由打断检测处理，不重复判为跑题
        if rules.keyword_hits(text, CONTROL_SIGNALS):
            continue  # 主持人控场发言不算跑题
        if _word_count(text) < 15:
            continue            # 短附和不判定跑题
        sg = rules.ngrams(text)
        if not sg:
            continue
        overlap = len(sg & terms) / len(sg)
        if overlap < OFF_TOPIC_THRESHOLD:
            result.append(TopicDeviation(
                segment_id=seg.segment_id,
                speaker=seg.speaker,
                timestamp=seg.timestamp,
                quote=text[:120],
                reason=f"与议程/主题词重叠率仅 {overlap:.1%}，疑似跑题",
            ))
    return result


def analyze_interruptions(meeting: Meeting) -> List[Interruption]:
    """打断检测：显式信号词 + 短时间切换（间隔 < 3 秒且发言人不同）。"""
    result: List[Interruption] = []
    segs = meeting.segments

    # 1) 显式信号词
    for seg in segs:
        hits = rules.keyword_hits(seg.text, rules.INTERRUPTION_SIGNALS)
        if hits:
            prev = None
            for s in segs:
                if s.segment_id == seg.segment_id:
                    break
                prev = s
            result.append(Interruption(
                at_segment_id=seg.segment_id,
                interrupter=seg.speaker,
                interrupted=prev.speaker if prev else "未知",
                timestamp=seg.timestamp,
                quote=(seg.text or "")[:120],
                signal="|".join(hits),
            ))

    # 2) 极短间隔 + 发言人切换（避免与显式信号重复）
    explicit_ids = {i.at_segment_id for i in result}
    for prev, cur in zip(segs, segs[1:]):
        if cur.segment_id in explicit_ids:
            continue
        if prev.speaker == cur.speaker:
            continue
        t1, t2 = parse_ts(prev.timestamp), parse_ts(cur.timestamp)
        if t1 is None or t2 is None:
            continue
        if 0 <= t2 - t1 < 3:
            result.append(Interruption(
                at_segment_id=cur.segment_id,
                interrupter=cur.speaker,
                interrupted=prev.speaker,
                timestamp=cur.timestamp,
                quote=(cur.text or "")[:120],
                signal=f"发言人在 {t2 - t1}s 内切换",
            ))
    return result


def analyze_speakers(meeting: Meeting) -> Tuple[List[SpeakerStat], float]:
    """发言参与度与均衡度。balance=1 表示完全均衡。"""
    counts: Dict[str, Dict[str, int]] = {}
    for seg in meeting.segments:
        entry = counts.setdefault(seg.speaker, {"segment_count": 0, "word_count": 0})
        entry["segment_count"] += 1
        entry["word_count"] += _word_count(seg.text)

    total_words = sum(e["word_count"] for e in counts.values()) or 1
    n = len(counts)
    stats = [
        SpeakerStat(
            speaker=name,
            segment_count=e["segment_count"],
            word_count=e["word_count"],
            share=round(e["word_count"] / total_words, 3),
        )
        for name, e in counts.items()
    ]
    stats.sort(key=lambda s: s.share, reverse=True)

    if n <= 1:
        balance = 1.0
    else:
        ideal = 1.0 / n
        max_share = stats[0].share if stats else 1.0
        balance = round(max(0.0, 1.0 - (max_share - ideal) / (1.0 - ideal)), 3)
    return stats, balance


def analyze_decision_clarity(meeting: Meeting, draft_items: List[Matter]) -> Dict[str, Any]:
    """结论 / 责任人 / 截止时间明确度。"""
    actions = [m for m in draft_items if m.category == "action"]
    owner_ok = sum(1 for m in actions if m.owner)
    due_ok = sum(1 for m in actions if m.due_date)
    decision_segments = [
        s.segment_id for s in meeting.segments
        if rules.keyword_hits(s.text, rules.DECISION_SIGNALS)
    ]
    action_count = len(actions)
    owner_rate = round(owner_ok / action_count, 2) if action_count else 1.0
    due_rate = round(due_ok / action_count, 2) if action_count else 1.0
    clarity = round(0.4 * owner_rate + 0.3 * due_rate
                    + 0.3 * min(1.0, len(decision_segments) / 3), 2)
    return {
        "action_count": action_count,
        "owner_defined_count": owner_ok,
        "owner_defined_rate": owner_rate,
        "due_defined_count": due_ok,
        "due_defined_rate": due_rate,
        "decision_signal_count": len(decision_segments),
        "decision_segments": decision_segments,
        "clarity_score": clarity,
        "note": "明确度基于行动项字段填充率与结论信号词统计，仅反映文本显性表达",
    }


def analyze_meeting(meeting: Meeting, draft_items: Optional[List[Matter]] = None) -> MeetingQuality:
    """主入口：汇总全部规范维度。"""
    draft_items = draft_items or []
    agenda_progress, agenda_coverage = analyze_agenda(meeting)
    off_topic = analyze_off_topic(meeting)
    interruptions = analyze_interruptions(meeting)
    speaker_stats, speaker_balance = analyze_speakers(meeting)
    clarity = analyze_decision_clarity(meeting, draft_items)
    open_items = [m for m in draft_items if m.status not in ("done", "cancelled")]
    open_item_count = len(open_items)

    issues: List[str] = []
    evidence_index: Dict[str, List[str]] = {}

    def add_issue(text: str, seg_ids: List[str]) -> None:
        issues.append(text)
        # 用问题序号作为稳定 key，同时保证可回原文
        evidence_index[f"issue-{len(issues):02d}"] = seg_ids

    if meeting.agenda and agenda_coverage < 1.0:
        uncovered = [d["agenda"] for d in agenda_progress if not d["covered"]]
        seg_ids = [sid for d in agenda_progress if not d["covered"] for sid in d["discussed_segments"]]
        add_issue(f"议程覆盖度 {agenda_coverage:.0%}，未充分讨论：{'；'.join(uncovered)}", seg_ids or ["(无对应发言)"])

    if clarity["action_count"] and clarity["owner_defined_rate"] < 0.7:
        add_issue(
            f"责任人明确率仅 {clarity['owner_defined_rate']:.0%}"
            f"（{clarity['action_count'] - clarity['owner_defined_count']}/{clarity['action_count']} 项行动未指定责任人）",
            [s.segment_id for s in meeting.segments if m_owner_missing(s, draft_items)],
        )

    if clarity["action_count"] and clarity["due_defined_rate"] < 0.7:
        add_issue(
            f"截止时间明确率仅 {clarity['due_defined_rate']:.0%}"
            f"（{clarity['action_count'] - clarity['due_defined_count']}/{clarity['action_count']} 项行动无截止时间）",
            [s.segment_id for s in meeting.segments if m_due_missing(s, draft_items)],
        )

    if off_topic:
        add_issue(f"检测到 {len(off_topic)} 处疑似跑题发言（{', '.join(d.segment_id for d in off_topic[:5])}）",
                  [d.segment_id for d in off_topic])

    if interruptions:
        add_issue(f"检测到 {len(interruptions)} 次打断（{', '.join(i.at_segment_id for i in interruptions[:5])}）",
                  [i.at_segment_id for i in interruptions])

    if speaker_balance < 0.5 and speaker_stats:
        top = speaker_stats[0]
        add_issue(f"发言明显失衡：{top.speaker} 占总发言量 {top.share:.0%}",
                  [s.segment_id for s in meeting.segments if s.speaker == top.speaker][:12])

    if open_item_count:
        add_issue(f"{open_item_count} 项事项在本场会议后仍未闭环（非已完成/已取消）",
                  [m.evidence[0].segment_id for m in open_items if m.evidence])

    if not issues:
        add_issue("未检测到显著的组织或发言规范问题", [])

    return MeetingQuality(
        meeting_id=meeting.meeting_id,
        meeting_title=meeting.title,
        date=meeting.date,
        agenda_progress=agenda_progress,
        agenda_coverage=agenda_coverage,
        decision_clarity=clarity,
        off_topic=off_topic,
        interruptions=interruptions,
        speaker_stats=speaker_stats,
        speaker_balance=speaker_balance,
        open_item_count=open_item_count,
        issues=issues,
        evidence_index=evidence_index,
    )


def _seg_has_due(seg) -> bool:
    return bool(rules.extract_due(seg.text))


def m_owner_missing(seg, draft_items: List[Matter]) -> bool:
    """该发言对应的事项是否缺责任人（用于证据定位）。"""
    for m in draft_items:
        if any(e.segment_id == seg.segment_id for e in m.evidence):
            return not bool(m.owner)
    return False


def m_due_missing(seg, draft_items: List[Matter]) -> bool:
    """该发言对应的事项是否缺截止时间（用于证据定位）。"""
    for m in draft_items:
        if any(e.segment_id == seg.segment_id for e in m.evidence):
            return m.category == "action" and not bool(m.due_date)
    return False
