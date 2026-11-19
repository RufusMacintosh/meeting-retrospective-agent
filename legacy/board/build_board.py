"""
把引擎产出的结构化 JSON 注入看板模板，生成自包含的 index.html。
自包含意味着可直接用浏览器打开，无需启动服务；数据更新后重跑本脚本即可。

  python3.11 board/build_board.py
"""

from __future__ import annotations

import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(ROOT, "data", "output")
TRANSCRIPT_DIR = os.path.join(ROOT, "data", "transcripts")
BOARD_DIR = os.path.join(ROOT, "board")


def _load(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _embed(obj) -> str:
    """序列化为可安全内嵌到 <script> 的 JS 字面量。"""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (text.replace("&", "\\u0026")
                .replace("<", "\\u003c")
                .replace(">", "\\u003e"))


def main() -> None:
    board = _load(os.path.join(OUTPUT_DIR, "board.json"))
    histories = _load(os.path.join(OUTPUT_DIR, "matter_histories.json"))

    transcripts = {}
    for path in sorted(glob.glob(os.path.join(TRANSCRIPT_DIR, "*.json"))):
        data = _load(path)
        transcripts[data["meeting_id"]] = data

    with open(os.path.join(BOARD_DIR, "template.html"), "r", encoding="utf-8") as fh:
        html = fh.read()

    html = (html
            .replace("/*__BOARD_DATA__*/", _embed(board))
            .replace("/*__HISTORIES_DATA__*/", _embed(histories))
            .replace("/*__TRANSCRIPTS_DATA__*/", _embed(transcripts)))

    target = os.path.join(BOARD_DIR, "index.html")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(html)

    print(f"看板已生成：{target}")
    print(f"内嵌：{len(board['columns'])} 列 / {len(board['meetings'])} 场会议 / "
          f"{len(histories)} 份事项历史 / {len(transcripts)} 份逐字稿")


if __name__ == "__main__":
    main()
