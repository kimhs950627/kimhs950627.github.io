#!/usr/bin/env python3
"""onenote_route.py — 바뀐 원노트 페이지 하나에 대해 '어느 html 의 어느 구간'인지 후보를 좁힌다.

  python3 scripts/onenote_route.py --title "위내시경 질환 Study" --section "검진 protocol, DDx" \
      --excerpt "...본문 앞부분..."

표준출력: {"candidates":[{repo,file,title,score,marker}], "best": {...} | null}
모델은 이 후보 안에서만 고르면 된다 — 저장소 전체를 읽지 않는다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data" / "pages-index.json"
WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")


def tokens(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(text or "")}


def score(query: dict[str, set[str]], entry: dict) -> float:
    t = tokens(entry["title"])
    h = tokens(" ".join(entry.get("headings", [])))
    k = set(entry.get("keywords", []))
    q_title, q_section, q_body = query["title"], query["section"], query["body"]
    s = 0.0
    s += 4 * len(q_title & t) + 2 * len(q_title & h) + 1.5 * len(q_title & k)
    s += 2 * len(q_section & t) + 1.5 * len(q_section & h) + len(q_section & k)
    s += 1.5 * len(q_body & t) + 1.5 * len(q_body & h) + len(q_body & k)
    denom = 1 + (len(q_title) * 2 + len(q_section) + len(q_body) * 0.2)
    return round(s / denom, 3)


def main() -> int:
    argv = sys.argv[1:]

    def arg(name: str) -> str:
        return argv[argv.index(name) + 1] if name in argv else ""

    if not INDEX.exists():
        print(json.dumps({"error": "pages-index.json 없음 — scripts/pages_index.py 먼저 실행"}))
        return 1

    data = json.loads(INDEX.read_text(encoding="utf-8"))
    query = {
        "title": tokens(arg("--title")),
        "section": tokens(arg("--section")),
        "body": tokens(arg("--excerpt")[:3000]),
    }
    ranked = sorted(
        ({**e, "score": score(query, e)} for e in data["pages"]),
        key=lambda e: -e["score"],
    )[:6]
    out = {
        "candidates": [
            {"repo": e["repo"], "file": e["file"], "title": e["title"], "score": e["score"], "marker": e["marker"]}
            for e in ranked
        ],
        "best": None,
    }
    if ranked:
        top = ranked[0]
        out["best"] = {"repo": top["repo"], "file": top["file"], "score": top["score"], "marker": top["marker"]}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
