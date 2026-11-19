"""
处理规则集合（纯函数，无副作用）
===================================================================
这里集中定义所有"判断口径"，便于审计、调参与单测。规则分为：

  A. 分类规则   —— 模块（四模块）/ 类别（进度·卡点·行动·反馈）
  B. 状态规则   —— 由后续发言推断事项状态迁移
  C. 抽取规则   —— 责任人 / 截止时间 / 优先级
  D. 合并规则   —— 跨会议事项去重相似度与匹配打分
  E. 文本工具   —— 归一化、n-gram、关键词命中

所有规则均为"证据驱动"：只有文本中确实出现相应信号才返回值，
抽取不到即返回 None，由调用方写入 missing_fields，绝不猜测填充。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from .models import (
    PROJECT_MODULES,
    ITEM_CATEGORIES,
    ITEM_STATUSES,  # re-export，供其他模块通过 rules.* 引用
)

# ---------------------------------------------------------------------------
# A. 分类词典
# ---------------------------------------------------------------------------
MODULE_KEYWORDS: Dict[str, List[str]] = {
    "user_insights": [
        "用户", "客户", "调研", "访谈", "画像", "痛点", "需求", "体验", "用研",
        "受访者", "留存", "NPS", "反馈", "抱怨", "使用习惯", "场景", "人群",
    ],
    "product_dev": [
        "开发", "技术", "接口", "API", "版本", "上线", "发布", "PRD", "需求文档",
        "设计稿", "测试", "bug", "修复", "迭代", "架构", "性能", "前端", "后端",
        "数据库", "埋点", "SDK", "联调", "重构", "灰度", "部署", "原型",
    ],
    "marketing": [
        "营销", "市场", "推广", "活动", "内容", "文案", "渠道", "投放", "品牌",
        "公众号", "小红书", "抖音", "传播", "曝光", "展会", "白皮书", "获客",
        "选题", "海报", "直播", "投放预算", "KOL",
    ],
    "sales_conversion": [
        "销售", "转化", "线索", "商机", "合同", "报价", "签约", "客单价", "漏斗",
        "成交", "回款", "试用", "Demo", "定价", "渠道代理", "续约", "客户成功",
        "同类客户", "方案", "投标", "转化率", "成单",
    ],
}

CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "blocker": [
        "卡在", "卡点", "阻塞", "阻碍", "风险", "问题", "困难", "无法", "不能",
        "延期", "延迟", "缺少", "不足", "瓶颈", "依赖", "待定", "争议", "没谈拢",
        "不确定", "卡住", "推不动", "没人", "不足", "缺人", "预算不够",
    ],
    "progress": [
        "完成", "已完成", "搞定", "上线", "发布", "交付", "推进到", "进展",
        "已解决", "落地", "通过", "达成", "已经", "跑通", "上线了", "收尾",
    ],
    "feedback": [
        "反馈", "建议", "意见", "投诉", "评价", "觉得", "反映", "提到", "抱怨",
        "体验不好", "希望", "期待", "用户说", "客户说", "吐槽",
    ],
    "action": [
        "负责", "跟进", "推进", "安排", "计划", "我来", "咱们", "需要", "尽快",
        "落实", "输出", "组织", "对接", "拉群", "约", "启动", "下一步", "会做",
        "牵头", "主导", "整理", "梳理", "评估", "补一下",
    ],
}

# 类别优先级：当多类得分相同时的决定顺序
CATEGORY_PRIORITY = ["blocker", "progress", "feedback", "action"]

STATUS_SIGNALS: Dict[str, List[str]] = {
    "done": [
        "已完成", "完成了", "搞定了", "已上线", "发布了", "已发布", "已解决",
        "已交付", "落地了", "通过了", "已确认", "闭环了", "收尾了", "跑通了",
    ],
    "blocked": [
        "卡住了", "被阻塞", "受阻", "延期", "推迟", "暂停", "无法推进",
        "推不动", "还在等", "依赖", "有风险", "暂缓", "搁置",
    ],
    "in_progress": [
        "进行中", "正在做", "推进中", "在做", "进行", "已启动", "启动了",
        "在跟", "跟进中", "处理中",
    ],
    "cancelled": [
        "取消", "不做了", "砍掉", "放弃", "停止推进", "不再推进", "终止",
    ],
}

# 结论信号（用于规范分析中的"结论明确度"）
DECISION_SIGNALS = ["决定", "结论", "确定", "达成共识", "就这么定", "拍板", "定下来", "对齐一下结论"]

# 打断信号
INTERRUPTION_SIGNALS = [
    "等一下", "插一句", "打断", "抱歉打断", "我补充一下", "先别", "sorry",
    "我插个话", "稍等", "你说那个", "我纠正一下",
]

STOPWORDS = set("的 了 和 是 在 我 我们 你 你们 他 她 它 这 那个 一个 一下 就 都 也 啊 吧 呢 吗 把 被 给 对 与 及 或".split())


# ---------------------------------------------------------------------------
# E. 文本工具
# ---------------------------------------------------------------------------
def normalize(text: str) -> str:
    """归一化：去空白与标点，转小写，便于相似度比较。"""
    text = (text or "").strip().lower()
    return re.sub(r"[\s，。！？、；：""''（）()《》\[\]{}<>,.!?;:'\"\\-]+", "", text)


def ngrams(text: str, n: int = 2) -> set:
    """中文按字符 n-gram，避免引入分词依赖。"""
    t = normalize(text)
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def keyword_hits(text: str, keywords: List[str]) -> List[str]:
    """返回文本中命中的关键词（原文匹配，不做同义词扩展以免臆造）。"""
    return [kw for kw in keywords if kw.lower() in (text or "").lower()]


def title_similarity(a: str, b: str) -> float:
    """标题相似度：字符序列相似度 与 2-gram Jaccard 的加权融合。"""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ga, gb = ngrams(a), ngrams(b)
    jac = len(ga & gb) / len(ga | gb) if (ga | gb) else 0.0
    return round(0.6 * seq + 0.4 * jac, 4)


# ---------------------------------------------------------------------------
# A. 分类规则
# ---------------------------------------------------------------------------
def classify_module(text: str) -> Tuple[str, float]:
    """返回 (模块, 置信度)。无关键词命中时回退 product_dev 并给低置信度。"""
    scores = {m: len(keyword_hits(text, kws)) for m, kws in MODULE_KEYWORDS.items()}
    best = max(scores, key=lambda m: scores[m])
    if scores[best] == 0:
        # 兜底：不臆造具体模块，回退通用产品开发模块并降低置信度，由上层标注
        return "product_dev", 0.35
    total = sum(scores.values()) or 1
    confidence = round(0.5 + 0.5 * (scores[best] / total), 2)
    return best, min(confidence, 1.0)


def classify_category(text: str) -> Tuple[str, float]:
    """返回 (类别, 置信度)。按命中数打分，平局按 CATEGORY_PRIORITY。"""
    scores = {c: len(keyword_hits(text, kws)) for c, kws in CATEGORY_KEYWORDS.items()}
    best_score = max(scores.values())
    if best_score == 0:
        # 无明确信号时归为"进度"但给低置信度（上层会标注需确认）
        return "progress", 0.35
    winners = [c for c in CATEGORY_PRIORITY if scores[c] == best_score]
    best = winners[0]
    total = sum(scores.values()) or 1
    confidence = round(0.5 + 0.5 * (best_score / total), 2)
    return best, min(confidence, 1.0)


# ---------------------------------------------------------------------------
# B. 状态规则
# ---------------------------------------------------------------------------
def infer_status(text: str, current: str = "open") -> Tuple[Optional[str], List[str]]:
    """
    从后续发言推断状态变化。
    返回 (新状态或None, 命中的信号词)。无信号时返回 None，表示沿用原状态。
    多个状态信号同时出现时，按 done > cancelled > blocked > in_progress 取"最强"变化。
    """
    priority = ["done", "cancelled", "blocked", "in_progress"]
    hits: Dict[str, List[str]] = {s: keyword_hits(text, kws) for s, kws in STATUS_SIGNALS.items()}
    for status in priority:
        if hits[status]:
            return status, hits[status]
    return None, []


# ---------------------------------------------------------------------------
# C. 抽取规则
# ---------------------------------------------------------------------------
OWNER_PATTERNS = [
    r"@([\u4e00-\u9fa5A-Za-z0-9_]{2,10})",                       # @张三
    r"由([\u4e00-\u9fa5]{2,6})(负责|跟进|牵头|主导|输出|组织)",     # 由张三负责
    r"([\u4e00-\u9fa5]{2,6})(来|去)(负责|跟进|牵头|主导|梳理|整理|做)",  # 张三来跟进
    r"负责人(?:是|为)?([\u4e00-\u9fa5]{2,6})",                    # 负责人是张三
    r"([\u4e00-\u9fa5]{2,6})(负责|牵头|主导)这个",                  # 张三负责这个
]

DUE_PATTERNS = [
    r"\d{4}-\d{1,2}-\d{1,2}",
    r"\d{1,2}月\d{1,2}[日号]",
    r"(本|下|下下)?周[一二三四五六日天](前|之前|下班前)?",
    r"(本|下)?月底(前)?",
    r"(本|下)?季度末(前)?",
    r"(\d{1,2})天内",
    r"(今天|明天|后天|大后天)(前|之前|下班前)",
    r"周[一二三四五六日天]",
    r"\d{1,2}号前",
]


def extract_owner(text: str, speaker: str, participants: List[str]) -> Optional[str]:
    """
    抽取责任人。

    关键约束：输入逐字稿已给出 participants，用它校验人名，
    防止把"数据源清单我来梳理"中的"数据源清单我"误当成人名。
    抽不到返回 None（上层标注缺失），绝不臆造。
    """
    content = text or ""

    # 1) @姓名
    m = re.search(OWNER_PATTERNS[0], content)
    if m and (not participants or m.group(1).strip() in participants):
        return m.group(1).strip()

    # 2) 显式指派："由X负责/牵头"、"负责人是X"
    for pattern in (OWNER_PATTERNS[1], OWNER_PATTERNS[3], OWNER_PATTERNS[4]):
        m = re.search(pattern, content)
        if m:
            candidate = m.group(1).strip()
            if participants:
                if candidate in participants:
                    return candidate
            elif len(candidate) <= 4 and candidate not in {"这个", "那个", "我们", "你们", "他们", "大家", "各位"}:
                return candidate

    # 3) "X来/去 负责|跟进|梳理..."：必须命中参与者名单才采纳
    m = re.search(OWNER_PATTERNS[2], content)
    if m and participants and m.group(1).strip() in participants:
        return m.group(1).strip()

    # 4) 第一人称承诺：责任人即发言人
    if re.search(r"我来|我负责|我牵头|我梳理|我整理|我去做|我输出", content):
        return speaker
    return None


def extract_due(text: str) -> Optional[str]:
    """抽取截止时间。保留原文相对表述（如"下周三前"），不推断绝对日期以免臆造。"""
    for pattern in DUE_PATTERNS:
        m = re.search(pattern, text or "")
        if m:
            return m.group(0).strip()
    return None


def extract_priority(text: str) -> str:
    """抽取优先级。"""
    if any(k in (text or "") for k in ["紧急", "尽快", "马上", "最高优先", "P0", "立刻"]):
        return "high"
    if any(k in (text or "") for k in ["不急", "有空", "低优先", "P2", "后续再说"]):
        return "low"
    return "normal"


# ---------------------------------------------------------------------------
# D. 合并规则
# ---------------------------------------------------------------------------
def match_score(existing_title: str, existing_module: str,
                cand_title: str, cand_module: str,
                cand_text: str = "") -> float:
    """
    计算候选事项与既有事项的匹配打分 0~1。
    模块不同直接判 0（避免跨模块误合并）。
    模块相同时以标题相似度为主，辅以上下文关键词重叠。
    """
    if existing_module != cand_module:
        return 0.0
    na, nb = normalize(existing_title), normalize(cand_title)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ga, gb = ngrams(existing_title), ngrams(cand_title)
    inter = len(ga & gb)
    jac = inter / len(ga | gb) if (ga | gb) else 0.0
    anchor = min(1.0, inter / 8.0)
    # 最长公共子串：中文事项标题里连续 6 字以上相同，基本可判定同一事项
    lcs = SequenceMatcher(None, na, nb).find_longest_match(0, len(na), 0, len(nb)).size
    lcs_anchor = min(1.0, lcs / 6.0)
    core_overlap = len(ga & ngrams(cand_text)) / len(ga) if ga else 0.0
    score = 0.25 * seq + 0.10 * jac + 0.30 * anchor + 0.25 * lcs_anchor + 0.10 * core_overlap
    return round(min(1.0, score), 4)


MERGE_THRESHOLD = 0.58   # 匹配分 >= 该值判定为同一事项


def should_merge(score: float, threshold: float = MERGE_THRESHOLD) -> bool:
    return score >= threshold
