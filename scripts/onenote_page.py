#!/usr/bin/env python3
"""onenote_page.py — 페이지 하나를 받아 파일로 저장하고 요약만 출력한다.

컨텍스트를 태우지 않으려고 본문은 파일로 떨어뜨리고, 표준출력에는 크기·제목 구조만 쓴다.

  python3 scripts/onenote_page.py --title "GI 학회 정리" --out /tmp/opencode/gi.md
  python3 scripts/onenote_page.py --title "GI 학회 정리" --out /tmp/opencode/gi.html --format html
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from onenote_recent import call_mcp_raw  # noqa: E402


def main() -> int:
    argv = sys.argv[1:]

    def arg(name: str, default: str = "") -> str:
        return argv[argv.index(name) + 1] if name in argv else default

    title = arg("--title")
    fmt = arg("--format", "text")
    out = Path(arg("--out", "/tmp/opencode/onenote-page.txt"))
    if not title:
        print(__doc__)
        return 1

    try:
        text = call_mcp_raw("getPageByTitle", {"title": title, "format": fmt})
    except Exception as err:  # noqa: BLE001
        print(f"실패: {err}", file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")

    heads = [ln.strip() for ln in text.splitlines() if ln.strip() and len(ln.strip()) < 80][:25]
    print(json.dumps({"out": str(out), "chars": len(text), "lines": text.count("\n") + 1, "head": heads[:12]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
