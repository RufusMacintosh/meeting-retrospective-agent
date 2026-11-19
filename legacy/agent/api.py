"""
关键接口（FastAPI）
===================================================================
所有响应统一为 {code, message, data}；code=0 表示成功。
看板主要消费 GET /api/board；核验走 evidence / history 系列接口。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Query

from .models import Meeting
from .pipeline import Pipeline
from . import schemas

_pipeline: Optional[Pipeline] = None


def init_pipeline(pipeline: Pipeline) -> None:
    global _pipeline
    _pipeline = pipeline


def _pl() -> Pipeline:
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="引擎尚未初始化，请先调用 init_pipeline")
    return _pipeline


def ok(data: Any) -> Dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


def create_app() -> FastAPI:
    app = FastAPI(
        title="会议复盘智能体 API",
        version="1.0.0",
        description="增量整理多场会议逐字稿为可核验的项目复盘",
    )

    # ---------------- 健康与元数据 ----------------
    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return ok({"status": "up", "project_id": _pl().project_id})

    @app.get("/api/schemas/{name}")
    def get_schema(name: str) -> Dict[str, Any]:
        if name not in schemas.ALL_SCHEMAS:
            raise HTTPException(status_code=404, detail=f"未知 schema: {name}")
        return ok(schemas.ALL_SCHEMAS[name])

    # ---------------- 会议摄入与规范分析 ----------------
    @app.post("/api/meetings/ingest")
    def ingest(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        """摄入单场会议逐字稿（增量，可重复调用）。"""
        try:
            meeting = Meeting.from_dict(payload)
        except (KeyError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"逐字稿格式错误: {exc}")
        record = _pl().ingest_transcript(meeting)
        return ok(record)

    @app.get("/api/meetings")
    def list_meetings() -> Dict[str, Any]:
        return ok([
            {
                "meeting_id": m.meeting_id,
                "title": m.title,
                "date": m.date,
                "segment_count": len(m.segments),
            }
            for m in _pl().store.meeting_index.values()
        ])

    @app.get("/api/meetings/{meeting_id}/quality")
    def meeting_quality(meeting_id: str) -> Dict[str, Any]:
        q = _pl().store.quality_reports.get(meeting_id)
        if not q:
            raise HTTPException(status_code=404, detail="会议不存在")
        return ok(q.to_dict())

    # ---------------- 复盘 / 看板 ----------------
    @app.get("/api/retrospective")
    def retrospective() -> Dict[str, Any]:
        return ok(_pl().retrospective())

    @app.get("/api/board")
    def board() -> Dict[str, Any]:
        """前端看板直接消费：4 模块 × 4 分类 = 16 列 + 会议规范 + 统计。"""
        return ok(_pl().board())

    # ---------------- 单一事项查询 ----------------
    @app.get("/api/matters")
    def list_matters(
        module: Optional[str] = Query(None),
        category: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
    ) -> Dict[str, Any]:
        items = [
            m.to_dict(include_history=False)
            for m in _pl().store.matters.values()
            if (not module or m.module == module)
            and (not category or m.category == category)
            and (not status or m.status == status)
        ]
        return ok(items)

    @app.get("/api/matters/{matter_id}")
    def get_matter(matter_id: str) -> Dict[str, Any]:
        data = _pl().store.get_matter(matter_id)
        if not data:
            raise HTTPException(status_code=404, detail="事项不存在")
        return ok(data)

    @app.get("/api/matters/{matter_id}/history")
    def get_history(matter_id: str) -> Dict[str, Any]:
        data = _pl().store.get_history(matter_id)
        if data is None:
            raise HTTPException(status_code=404, detail="事项不存在")
        return ok(data)

    @app.get("/api/matters/{matter_id}/history/{version}")
    def get_revision(matter_id: str, version: int) -> Dict[str, Any]:
        data = _pl().store.get_revision(matter_id, version)
        if not data:
            raise HTTPException(status_code=404, detail="版本不存在")
        return ok(data)

    # ---------------- 证据核验 ----------------
    @app.get("/api/matters/{matter_id}/evidence")
    def get_evidence(matter_id: str) -> Dict[str, Any]:
        data = _pl().store.get_evidence(matter_id)
        if data is None:
            raise HTTPException(status_code=404, detail="事项不存在")
        return ok(data)

    @app.get("/api/matters/{matter_id}/evidence/{segment_id}")
    def locate_evidence(matter_id: str, segment_id: str) -> Dict[str, Any]:
        """按段落 id 精确定位回原文（含发言人、时间戳、逐字摘录）。"""
        data = _pl().store.locate(matter_id, segment_id)
        if not data:
            raise HTTPException(status_code=404, detail="证据不存在")
        return ok(data)

    return app


app = create_app()
