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


def build_idf(entries: list[dict]) -> dict[str, float]:
    """흔한 단어(학회, 정리, 공부 …)의 가중치를 낮춘다."""
    import math

    df: dict[str, int] = {}
    for e in entries:
        toks = tokens(e["title"]) | tokens(" ".join(e.get("headings", []))) | set(e.get("keywords", []))
        for t in toks:
            df[t] = df.get(t, 0) + 1
    n = max(len(entries), 1)
    return {t: math.log((n + 1) / (c + 1)) + 0.2 for t, c in df.items()}


STOP = {"정리", "공부", "내용", "학회", "학회내용", "노트", "참고", "방법", "개요", "총론",
        "study", "note", "notes", "reference", "guide", "intro", "정리본", "자료", "시험", "대비"}


def score(query: dict[str, set[str]], entry: dict, idf: dict[str, float]) -> float:
    """같은 단어를 여러 필드에서 중복 계산하지 않는다. 흔한 단어는 STOP 으로 뺀다."""
    t = tokens(entry["title"]) - STOP
    h = tokens(" ".join(entry.get("headings", []))) - STOP
    k = set(entry.get("keywords", [])) - STOP

    total = 0.0
    denom = 1.0
    for q, qweight in ((query["title"], 3.0), (query["section"], 1.5), (query["body"], 1.0)):
        q = q - STOP
        denom += qweight * sum(idf.get(x, 0.2) for x in q)
        for tok in q:
            bonus = 3.0 if tok in t else (2.0 if tok in h else (1.0 if tok in k else 0.0))
            if bonus:
                total += qweight * bonus * idf.get(tok, 0.2)
    return round(total / denom, 3)


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
        "body": tokens(re.sub(r"\]\]>|<[^>]+>", " ", arg("--excerpt"))[:3000]),
    }
    idf = build_idf(data["pages"])
    ranked = sorted(
        ({**e, "score": score(query, e, idf)} for e in data["pages"] if score(query, e, idf) > 0),
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
