#!/usr/bin/env python3
"""onenote_absorb.py — 재머지에서 흡수율이 낮았던 페이지만 다시, '빠진 사실'을 알려주며 흡수시킨다.

1차(`onenote_remerge.py`)는 페이지 전체를 한 번에 통합해 일부 사실을 흘렸다(흡수율 31~76%).
여기서는 ① 원문 사실 중 페이지에 없는 것만 뽑아 목록으로 보여주고 ② 가장 관련 있는 단원에 넣게 한다.
2회 반복해도 80% 미만이면 부록을 남긴다(내용 손실 금지).

  python3 scripts/onenote_absorb.py /tmp/opencode/remerge.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from onenote_decide import MODEL, call, parse_json  # noqa: E402
from onenote_elaborate import coverage  # noqa: E402
from onenote_html import page_blocks  # noqa: E402
from onenote_remerge import REPOS, inserted_source  # noqa: E402

SYSTEM = """너는 임상 참고서 편집자다. 기존 단원 HTML 과 '아직 페이지에 없는 사실 목록'이 주어지면,
그 사실들을 **기존 단원의 제자리**에 녹여 넣은 단원 HTML 전체를 돌려준다.

규칙:
- 기존 단원의 내용·구조·태그·class 를 유지한다(삭제 금지).
- 주어진 '빠진 사실'을 모두 반영한다. 중복 서술은 하지 않는다.
- 태그는 기존에 쓰인 것만. style·script 금지.
- 출력은 JSON 하나: {"html": "<단원 전체 HTML>", "summary": "한 줄"}
"""

STOP = {"에서", "으로", "한다", "있다", "경우", "대한", "위해", "따라", "그리고", "또는", "때문", "정도",
        "이상", "이하", "보고", "권고", "사용", "치료", "환자", "검사", "정리", "내용", "방법"}
FACT = re.compile(r"\d+(?:\.\d+)?|[A-Za-z]{4,}|[가-힣]{4,}")


def facts(text: str) -> Counter:
    return Counter(w for w in FACT.findall(re.sub(r"<[^>]+>", " ", text)) if w not in STOP)


def missing(src: str, page: str, limit: int = 40) -> list[str]:
    have = facts(page)
    out = []
    for w, c in facts(src).items():
        if have.get(w, 0) < c and w not in out:
            out.append(w)
    return out[:limit]


def render(html: str, sections: dict[str, str], drop: set[str], strip: bool) -> str:
    blocks, _ = page_blocks(html)
    new = html
    for k, b in blocks:
        if k in sections:
            new = new.replace(b, sections[k].strip(), 1)
        elif k in drop:
            new = new.replace(b, "", 1)
    if strip:
        new = re.sub(r'\n?[ \t]*<div class="onenote-insert"[^>]*>.*?</div>[ \t]*\n?', "\n", new, flags=re.S)
    return new


def main() -> int:
    patch = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/remerge.json")
    doc = json.loads(patch.read_text(encoding="utf-8"))
    changed = 0
    for u in doc["updates"]:
        html = (REPOS / u["repo"] / u["file"]).read_text(encoding="utf-8")
        src = inserted_source(html)
        secs, _ = page_blocks(html)
        secs = [(k, b) for k, b in secs if k != "onenote"]
        drop = set(u.get("remove_sections") or [])
        for attempt in range(2):
            page = render(html, u["sections"], drop, True)
            cov = coverage(src, page)
            if cov >= 0.8:
                break
            gaps = missing(src, page)
            if not gaps:
                break
            # 가장 관련 있는 단원 2개에만 넣는다
            ranked = sorted(secs, key=lambda kb: -len(set(re.findall(r"[가-힣]{2,}|[A-Za-z]{4,}", kb[1].lower()))
                                                     & set(gaps)))[:2]
            for sid, block in ranked:
                block = u["sections"].get(sid, block)
                prompt = f"""대상: {u['repo']}/{u['file']} · 단원: {sid}

[기존 단원 HTML]
{block[:12000]}

[아직 페이지에 없는 사실]
{', '.join(gaps)}

[원문 발췌 — 문맥 확인용]
<<<
{src[:8000]}
>>>
"""
                try:
                    out = parse_json(call(prompt, max_tokens=9000, system=SYSTEM))
                except Exception:  # noqa: BLE001
                    continue
                cand = (out.get("html") or "").strip()
                if not cand or coverage(block, cand) < 0.9:
                    continue
                u["sections"][sid] = cand
                time.sleep(0.3)
        page = render(html, u["sections"], drop, True)
        cov = coverage(src, page)
        u["source"]["source_coverage"] = round(cov, 3)
        if cov < 0.8:
            u["remove_sections"] = []
            u["strip_onenote_inserts"] = False
        else:
            u["remove_sections"] = ["onenote"]
            u["strip_onenote_inserts"] = True
            changed += 1
        print(f"{u['repo']}/{u['file']}: 흡수율 {cov:.0%} {'(부록 제거)' if u['remove_sections'] else '(부록 유지)'}", flush=True)
    patch.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"updated": changed, "total": len(doc["updates"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
