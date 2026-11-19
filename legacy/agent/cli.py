"""
命令行入口
===================================================================
  python3.11 -m agent.cli run            # 处理示例逐字稿，生成结构化输出
  python3.11 -m agent.cli serve --port 8000  # 启动 API 服务
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from . import schemas
from .pipeline import Pipeline, load_transcripts


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INPUT = os.path.join(PROJECT_ROOT, "data", "transcripts")
DEFAULT_OUTPUT = os.path.join(PROJECT_ROOT, "data", "output")


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def cmd_run(args: argparse.Namespace) -> None:
    meetings = load_transcripts(args.input)
    if not meetings:
        raise SystemExit(f"未在 {args.input} 找到任何逐字稿 JSON")

    pipeline = Pipeline(args.project_id)
    for meeting in sorted(meetings, key=lambda m: (m.date, m.meeting_id)):
        record = pipeline.ingest_transcript(meeting)
        print(
            f"[{meeting.meeting_id}] {meeting.date} 草稿 {record['drafts']} 条 → "
            f"新建 {record['created']} / 合并 {record['merged']}"
        )

    out = args.output
    store = pipeline.store
    _write_json(os.path.join(out, "retrospective.json"), pipeline.retrospective())
    _write_json(os.path.join(out, "board.json"), pipeline.board())
    _write_json(
        os.path.join(out, "quality_reports.json"),
        [q.to_dict() for q in store.quality_reports.values()],
    )
    _write_json(os.path.join(out, "ingest_log.json"), store.ingest_log)

    # 单一事项历史：按 matter_id 组织，支持任意事项 / 任意版本回溯
    histories = {
        mid: store.get_history(mid) for mid in sorted(store.matters)
    }
    _write_json(os.path.join(out, "matter_histories.json"), histories)

    # 对外契约 Schema
    for filename, schema in schemas.ALL_SCHEMAS.items():
        _write_json(os.path.join(out, "schemas", filename), schema)

    stats = store._stats()
    _write_json(os.path.join(out, "manifest.json"), {
        "project_id": args.project_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "outputs": {
            "retrospective": "retrospective.json",
            "board": "board.json",
            "quality_reports": "quality_reports.json",
            "ingest_log": "ingest_log.json",
            "matter_histories": "matter_histories.json",
            "schemas": "schemas/",
        },
        "stats": stats,
    })
    print(f"\n完成：{stats['matter_count']} 个事项，{stats['meeting_count']} 场会议，"
          f"{stats['conflict_count']} 处冲突，{stats['missing_field_count']} 处缺失标注。")
    print(f"输出目录：{out}")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    meetings = load_transcripts(args.input)
    pipeline = Pipeline(args.project_id)
    pipeline.run(meetings)

    from .api import app, init_pipeline
    init_pipeline(pipeline)
    print(f"API 已就绪，启动于 http://0.0.0.0:{args.port}")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(description="会议复盘智能体")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="处理逐字稿并生成结构化输出")
    p_run.add_argument("--input", default=DEFAULT_INPUT)
    p_run.add_argument("--output", default=DEFAULT_OUTPUT)
    p_run.add_argument("--project-id", default="proj-demo")
    p_run.set_defaults(func=cmd_run)

    p_serve = sub.add_parser("serve", help="启动 API 服务")
    p_serve.add_argument("--input", default=DEFAULT_INPUT)
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--project-id", default="proj-demo")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
