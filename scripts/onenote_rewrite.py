#!/usr/bin/env python3
"""onenote_rewrite.py — 병합된 단원의 **문장·구조**를 편집 규칙에 맞춰 다시 쓴다.

`onenote_elaborate.py` 는 사실을 합치는 데 집중하느라 문장이 어수선해진다(중복 불릿, 긴 문단,
"매우 중요" 같은 공허한 수식). 이 스크립트는 사실은 그대로 두고 **표현과 구조만** 고친다.

  python3 scripts/onenote_rewrite.py --file lab-followup/thyroid.html
  python3 scripts/onenote_rewrite.py --merged          # 오늘 병합한 페이지 기본 목록

검증: 기존 사실 보존율 ≥ 95% 그리고 길이가 25% 넘게 줄지 않을 때만 적용한다.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from onenote_decide import MODEL, call, parse_json  # noqa: E402
from collections import Counter  # noqa: E402
from onenote_html import page_blocks  # noqa: E402

STATE = Path.home() / ".local" / "state" / "onenote"
REPOS = STATE / "repos"
STYLE = (ROOT / "data" / "editorial-style.md").read_text(encoding="utf-8")
OUT = Path("/tmp/opencode/rewrite.json")

MERGED = [
    "lab-followup/thyroid.html",
    "lab-followup/urine.html",
    "lab-followup/liver.html",
    "ward-nights/breathing.html",
    "ward-nights/gi.html",
    "ward-nights/mental.html",
    "ward-nights/fever.html",
    "ward-nights/glucose.html",
    "endoscopy-study/egd/02-식도-악성.html",
    "endoscopy-study/egd/04-위-악성.html",
]

SYSTEM = """너는 임상 참고서 편집자다. 주어진 단원 HTML 을 아래 편집 규칙에 맞게 다시 쓴다.

[절대 규칙 — 감축 금지]
- 원문의 **모든 항목·수치·약물명·기준**을 그대로 남긴다. 불릿 개수를 줄이지 않는다.
- 길이는 원문의 90~120% 를 유지한다. 요약해서 짧게 만들지 않는다.
- 삭제는 (a) 같은 문장의 반복 (b) 의미 없는 수식어 두 경우만 허용한다.
- 확신이 없으면 원문 문장을 그대로 둔다.

[편집 규칙]
{style}

출력은 JSON 하나: {{"html": "<단원 전체 HTML>", "summary": "무엇을 고쳤는지 한 줄"}}
태그·클래스·h2 머리부분은 원문 그대로 유지한다.
""".replace("{style}", STYLE)


FACT_NUM = re.compile(r"\d+(?:\.\d+)?")
FACT_LAT = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
FACT_KO = re.compile(r"[가-힣]{4,}")


def _cov(pat, old: str, new: str) -> float:
    a = Counter(w.lower() for w in pat.findall(re.sub(r"<[^>]+>", " ", old)))
    b = Counter(w.lower() for w in pat.findall(re.sub(r"<[^>]+>", " ", new)))
    if not a:
        return 1.0
    return sum(min(c, b.get(w, 0)) for w, c in a.items()) / sum(a.values())


def rewrite_block(sid: str, block: str) -> str | None:
    prompt = f"""단원 id: {sid}

[원문]
{block[:14000]}
"""
    try:
        out = parse_json(call(prompt, max_tokens=12000, system=SYSTEM))
    except Exception as err:  # noqa: BLE001
        print(f"    FAIL {sid}: {err}", flush=True)
        return None
    new = (out.get("html") or "").strip()
    if not new:
        return None
    head_old = re.search(r"<h2[^>]*>.*?</h2>", block, re.S)
    if head_old and head_old.group(0).split("</span>")[0][:30] not in new:
        print(f"    ! {sid}: h2 머리 변경 → 폐기", flush=True)
        return None
    num, lat, ko = _cov(FACT_NUM, block, new), _cov(FACT_LAT, block, new), _cov(FACT_KO, block, new)
    if num < 0.98 or lat < 0.95 or ko < 0.85:
        print(f"    ! {sid}: 보존 숫자 {num:.0%}·영문 {lat:.0%}·한글 {ko:.0%} → 폐기", flush=True)
        return None
    if len(new) < len(block) * 0.9:
        print(f"    ! {sid}: 길이 {len(new)}/{len(block)} → 폐기", flush=True)
        return None
    return new


def main() -> int:
    args = sys.argv[1:]
    getk = lambda k, d="": args[args.index(k) + 1] if k in args else d
    files = [getk("--file")] if "--file" in args else ([] if "--merged" not in args else MERGED)
    if "--files" in args:
        files = [f for f in getk("--files").split(",") if f]

    updates = []
    for target in files:
        repo, rel = target.split("/", 1)
        path = REPOS / repo / rel
        if not path.exists():
            print(f"SKIP {target}: 없음", flush=True)
            continue
        blocks, _ = page_blocks(path.read_text(encoding="utf-8"))
        sections: dict[str, str] = {}
        for sid, block in blocks:
            if sid == "onenote" or len(block) < 400:
                continue
            new = rewrite_block(sid, block)
            if new and new != block:
                sections[sid] = new
                print(f"  ~ {target} · {sid}", flush=True)
        if sections:
            updates.append({
                "repo": repo, "file": rel, "sections": sections,
                "source": {"page": "문장 편집", "model": MODEL},
                "commit_message": f"docs(onenote): {rel} — 병합 문장·구조 편집",
            })
        time.sleep(0.3)

    out = Path(getk("--out", str(OUT)))
    out.write_text(json.dumps({"updates": updates}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "files": len(updates),
                      "sections": sum(len(u["sections"]) for u in updates)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
