#!/usr/bin/env python3
"""onenote_fetch.py — Graph(MCP)에서 페이지 본문을 받아 파일로 저장한다.

  python3 scripts/onenote_fetch.py --title "GI 학회 정리" --out /tmp/opencode/gi-full.md
  python3 scripts/onenote_fetch.py --id <pageId> --format html --out /tmp/x.html
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from onenote_recent import call_mcp_raw  # noqa: E402

FMT_CODE = {"text": 0, "html": 1, "summary": 2}


def main() -> int:
    a = sys.argv[1:]
    get = lambda k, d="": a[a.index(k) + 1] if k in a else d
    out = Path(get("--out", "/tmp/opencode/onenote-page.txt"))
    fmt = get("--format", "text")
    if get("--id"):
        raw = call_mcp_raw("getPageContent", {"pageId": get("--id"), "format": fmt})
    else:
        raw = call_mcp_raw("getPageByTitle", {"title": get("--title"), "format": fmt})
    raw = raw.strip()
    if not raw or raw == "null":
        print("빈 결과", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(raw, encoding="utf-8")
    print(json.dumps({"chars": len(raw), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
