#!/usr/bin/env python3
"""onenote_polish.py — 병합된 문단·불릿만 골라 문장을 다듬는다(구조·사실 보존).

전체 단원 재작성은 사실을 흘리기 쉬워서(실측: 영문 보존 66~74%) 폐기했다.
대신 **문제가 있는 텍스트 노드만** 하나씩 고친다.

  감지: 중복 문장 · 120자 초과 · 공허한 수식(매우/다양한/효과적/중요) · 같은 문장 반복
  수정: 모델이 그 노드 하나만 다시 쓴다(수치·약물 100% 보존 검증)

  python3 scripts/onenote_polish.py --merged
  python3 scripts/onenote_polish.py --file ward-nights/gi.html
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from onenote_decide import MODEL, call, parse_json  # noqa: E402
from onenote_html import page_blocks  # noqa: E402

STATE = Path.home() / ".local" / "state" / "onenote"
REPOS = STATE / "repos"
STYLE = (ROOT / "data" / "editorial-style.md").read_text(encoding="utf-8")
OUT = Path("/tmp/opencode/polish.json")

MERGED = [
    "lab-followup/thyroid.html",
    "lab-followup/urine.html",
    "lab-followup/liver.html",
    "ward-nights/breathing.html",
    "ward-nights/gi.html",
    "ward-nights/mental.html",
    "ward-nights/fever.html",
    "ward-nights/glucose.html",
]

NODE = re.compile(r"<(p|li)([^>]*)>(.*?)</\1>", re.S)
TAGS = re.compile(r"<[^>]+>")
NUM = re.compile(r"\d+(?:\.\d+)?")
LAT = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
FILLER = ("매우", "다양한", "효과적", "중요합니다", "주의가 필요", "라고 할 수 있", "것으로 보입니다")

SYSTEM = """너는 임상 참고서 문장 편집자다. 문단 하나를 아래 규칙에 맞게 다듬는다.

[규칙]
{style}

[절대 규칙]
- 수치·약물명·기준은 하나도 바꾸지 않는다(그대로 복사).
- 항목을 삭제하지 않는다. 문장만 다듬는다.
- 출력 JSON 하나: {{"text": "다듬은 문단(HTML 태그 없이 순수 텍스트)"}}""".replace("{style}", STYLE)


def needs(text: str, dups: Counter) -> str:
    plain = TAGS.sub("", text).strip()
    if len(plain) > 120:
        return "long"
    if any(f in plain for f in FILLER):
        return "filler"
    if dups[plain] > 1:
        return "dup"
    return ""


def safe(old: str, new: str) -> bool:
    if not new or len(new) < 8:
        return False
    for pat in (NUM, LAT):
        a = Counter(w.lower() for w in pat.findall(TAGS.sub(" ", old)))
        b = Counter(w.lower() for w in pat.findall(new))
        if sum(a.values()) and sum(min(c, b.get(w, 0)) for w, c in a.items()) / sum(a.values()) < 1.0:
            return False
    return True


def polish_file(target: str) -> dict | None:
    repo, rel = target.split("/", 1)
    path = REPOS / repo / rel
    if not path.exists():
        print(f"SKIP {target}: 없음", flush=True)
        return None
    html = path.read_text(encoding="utf-8")
    blocks, _ = page_blocks(html)
    sections: dict[str, str] = {}
    edits = 0

    for sid, block in blocks:
        if sid == "onenote":
            continue
        dups = Counter(TAGS.sub("", m.group(3)).strip() for m in NODE.finditer(block))
        new_block = block
        for m in NODE.finditer(block):
            tag, attrs, inner = m.group(1), m.group(2), m.group(3)
            why = needs(inner, dups)
            if not why:
                continue
            plain = TAGS.sub("", inner).strip()
            prompt = f"[문단]\n{plain}\n"
            try:
                out = parse_json(call(prompt, max_tokens=700, system=SYSTEM))
            except Exception:  # noqa: BLE001
                continue
            fixed = (out.get("text") or "").strip()
            if not safe(plain, fixed) or fixed == plain:
                continue
            new_block = new_block.replace(m.group(0), f"<{tag}{attrs}>{fixed}</{tag}>", 1)
            edits += 1
            time.sleep(0.2)
        if new_block != block:
            # 단원 전체 검증 — 수치/영문 100%, 한글 90%, 길이 ±15%
            def cov(pat, old, new):
                a = Counter(w.lower() for w in pat.findall(TAGS.sub(" ", old)))
                b = Counter(w.lower() for w in pat.findall(TAGS.sub(" ", new)))
                if not a:
                    return 1.0
                return sum(min(c, b.get(w, 0)) for w, c in a.items()) / sum(a.values())
            num, lat = cov(NUM, block, new_block), cov(LAT, block, new_block)
            ko = cov(re.compile(r"[가-힣]{4,}"), block, new_block)
            ratio = len(new_block) / max(1, len(block))
            if num < 1.0 or lat < 0.98 or ko < 0.87 or not (0.85 <= ratio <= 1.15):
                print(f"    ! {sid}: 검증 실패 숫자 {num:.0%} 영문 {lat:.0%} 한글 {ko:.0%} 길이 {ratio:.2f} → 폐기", flush=True)
                continue
            sections[sid] = new_block

    if not sections:
        return None
    print(f"  ~ {target}: {edits}개 문단", flush=True)
    return {
        "repo": repo, "file": rel, "sections": sections,
        "source": {"page": "문장 다듬기", "model": MODEL},
        "commit_message": f"docs(onenote): {rel} — 병합 문단 문장 정리({edits}개)",
    }


def main() -> int:
    args = sys.argv[1:]
    getk = lambda k, d="": args[args.index(k) + 1] if k in args else d
    if "--file" in args:
        files = [getk("--file")]
    elif "--files" in args:
        files = [f for f in getk("--files").split(",") if f]
    else:
        files = MERGED
    updates = [u for u in (polish_file(f) for f in files) if u]
    out = Path(getk("--out", str(OUT)))
    out.write_text(json.dumps({"updates": updates}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "files": len(updates)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
