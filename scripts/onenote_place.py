#!/usr/bin/env python3
"""onenote_place.py — 원노트 내용 조각을 '기존 장의 어느 단원'에 넣을지 Command Code API 로 고른다.

  python3 scripts/onenote_place.py --chunks chunks.json --catalog catalog.json --out plan.json

chunks.json  : [{"key":"gerd","label":"GERD/NERD/LA","text":"…"}]
catalog.json : {"pages":[{"repo":"…","file":"…","title":"…","sections":[{"id":"…","title":"…"}]}]}
plan.json    : {"moves":[{"key":"gerd","repo":"…","file":"…","anchor_id":"…","reason":"…"}], "drop":[...]}

모델: deepseek/deepseek-v4.1-flash (reasoning_effort low) — CLI 없이 API 직접 호출.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from onenote_decide import call, parse_json  # noqa: E402

SYSTEM = """너는 내시경 학습 워크북의 편집자다. 원노트에서 가져온 내용 조각이 여러 개 있고,
워크북의 기존 단원(장 + 앵커 id) 목록이 있다. 각 조각을 가장 알맞은 단원 하나에 배정한다.

규칙:
- repo/file/anchor_id 는 반드시 후보 목록에 있는 값 그대로 쓴다.
- 조각의 주제가 후보 단원 중 어디에도 맞지 않으면 "drop" 에 넣는다.
- 같은 단원에 여러 조각이 가도 된다. 순서는 원문 흐름을 유지한다.
- 출력은 JSON 하나: {"moves":[{"key":"...","repo":"...","file":"...","anchor_id":"...","reason":"한 문장"}],"drop":["key",...]}
"""


def norm_keys(text: str) -> set[str]:
    return set(re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", text.lower()))


def flat(text: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text)


def is_duplicate(chunk_text: str, sections: list[dict]) -> dict | None:
    """보수적 중복 판정 — 확실히 겹칠 때만 True 쪽으로 기운다.

    ① 40자 이상 연속 일치(정규화 후) ② 내용 토큰의 90% 이상이 한 단원에 이미 존재.
    둘 다 아니면 중복으로 보지 않는다(내용 소실 방지).
    """
    key = flat(chunk_text)
    toks = norm_keys(chunk_text)
    for sec in sections:
        body = sec.get("text", "")
        if len(key) >= 40:
            fb = flat(body)
            for i in range(0, len(key) - 40 + 1, 10):
                if key[i:i + 40] in fb:
                    return {"anchor_id": sec["id"], "file": sec.get("file", ""), "why": "문장 일치"}
        if toks:
            overlap = len(toks & norm_keys(body)) / len(toks)
            if overlap >= 0.9:
                return {"anchor_id": sec["id"], "file": sec.get("file", ""), "why": f"토큰 {overlap:.0%} 일치"}
    return None


def main() -> int:
    a = sys.argv[1:]
    get = lambda k, d="": a[a.index(k) + 1] if k in a else d
    chunks = json.loads(Path(get("--chunks")).read_text(encoding="utf-8"))
    catalog = json.loads(Path(get("--catalog")).read_text(encoding="utf-8"))
    out = Path(get("--out", "/tmp/opencode/place.json"))
    batch = int(get("--batch", "12"))

    cat_lines = []
    for page in catalog["pages"]:
        cat_lines.append(f"## {page['repo']}/{page['file']} — {page['title']}")
        for sec in page["sections"]:
            cat_lines.append(f"  - {sec['id']} :: {sec['title']}")
    cat_text = "\n".join(cat_lines)

    moves, dropped, failed = [], [], []
    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        body = "\n\n".join(f"### key={c['key']} ({c['label']})\n{c['text'][:900]}" for c in part)
        prompt = f"후보 단원:\n{cat_text}\n\n내용 조각:\n{body}"
        try:
            raw = call(prompt, system=SYSTEM, max_tokens=6000)
            plan = parse_json(raw)
        except Exception as err:  # noqa: BLE001
            failed.extend(c["key"] for c in part)
            print(f"  batch {i//batch} 실패: {err}", file=sys.stderr)
            continue
        moves.extend(plan.get("moves", []))
        dropped.extend(plan.get("drop", []))

    # 중복 판정: 후보 페이지 전체 본문과 비교해 확실히 겹치는 조각만 뺀다
    all_sections = []
    for p in catalog["pages"]:
        for s in p["sections"]:
            all_sections.append({**s, "file": p["file"]})
    by_key = {c["key"]: c for c in chunks}
    checked, duplicates = [], []
    for mv in moves:
        chunk = by_key.get(mv["key"])
        if not chunk:
            continue
        dup = is_duplicate(chunk["text"], all_sections)
        if dup:
            duplicates.append({"key": mv["key"], "matched": dup, "label": chunk["label"][:60]})
        else:
            checked.append(mv)
    moves = checked

    allowed = {(p["repo"], p["file"], s["id"]) for p in catalog["pages"] for s in p["sections"]}
    moves = [m for m in moves if (m.get("repo"), m.get("file"), m.get("anchor_id")) in allowed]
    plan = {"moves": moves, "drop": dropped, "failed": failed, "duplicates": duplicates}
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"moves": len(moves), "duplicates": len(duplicates), "drop": len(dropped),
                      "failed": failed, "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
