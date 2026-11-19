"""
主编排：逐字稿 -> 抽取 -> 规范分析 -> 增量入库 -> 复盘/看板输出
"""

from __future__ import annotations

import glob
import json
import os
from typing import Any, Dict, List

from .extractor import Extractor, RuleBasedExtractor
from .models import Meeting
from .quality import analyze_meeting
from .store import MatterStore


class Pipeline:
    """一次项目级增量处理的编排器。可重复调用 ingest_transcript 实现持续更新。"""

    def __init__(self, project_id: str, extractor: Extractor | None = None):
        self.project_id = project_id
        self.extractor: Extractor = extractor or RuleBasedExtractor()
        self.store = MatterStore(project_id)

    def ingest_transcript(self, meeting: Meeting) -> Dict[str, Any]:
        """
        摄入单场会议：
          1) 抽取事项草稿（每条带证据）
          2) 基于草稿做组织/发言规范分析
          3) 增量合并入事项库（去重、状态更新、版本快照）
        """
        drafts = self.extractor.extract(meeting)
        quality = analyze_meeting(meeting, drafts)
        return self.store.ingest_meeting(meeting, drafts, quality)

    def run(self, meetings: List[Meeting]) -> MatterStore:
        """按日期顺序批量摄入（顺序影响"首次发现"与版本历史）。"""
        for meeting in sorted(meetings, key=lambda m: (m.date, m.meeting_id)):
            self.ingest_transcript(meeting)
        return self.store

    def board(self) -> Dict[str, Any]:
        return self.store.get_board_payload()

    def retrospective(self) -> Dict[str, Any]:
        return self.store.get_retrospective().to_dict()


def load_transcripts(directory: str) -> List[Meeting]:
    """从目录加载全部逐字稿 JSON。"""
    meetings: List[Meeting] = []
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        with open(path, "r", encoding="utf-8") as fh:
            meetings.append(Meeting.from_dict(json.load(fh)))
    return meetings
