#!/usr/bin/env python3
"""onenote_remerge.py — 이미 페이지에 '덧붙여진' 원노트 내용을 단원 안으로 흡수한다(소급 정리).

대상: `<!-- onenote:start -->` 부록이 있거나 `onenote-insert:` 로 본문에 끼워넣은 흔적이 있는 페이지.
동작: 삽입된 원문을 모아 → 기존 단원 HTML 과 함께 모델에 주고 → 단원을 통합 편집본으로 교체하고
      부록/삽입 블록을 제거한다. 이후에는 `onenote_elaborate.py` 만 쓰면 된다.

  python3 scripts/onenote_remerge.py                 # 15개 대상 전부
  python3 scripts/onenote_remerge.py --file endoscopy-study/egd/00-총론.html
  python3 scripts/onenote_remerge.py --dry-run       # 대상 목록만
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
from onenote_elaborate import SYSTEM, coverage  # noqa: E402
from onenote_html import page_blocks  # noqa: E402

REPOS = Path.home() / ".local" / "state" / "onenote" / "repos"
OUT = Path("/tmp/opencode/remerge.json")
REPOS_WANTED = ["endoscopy-study", "lab-followup", "ward-nights", "primary-care-protocol", "ultrasound-protocol-study"]


def targets() -> list[tuple[str, str]]:
    out = []
    for repo in REPOS_WANTED:
        d = REPOS / repo
        if not d.exists():
            continue
        for f in sorted(d.rglob("*.html")):
            if "node_modules" in f.parts:
                continue
            t = f.read_text(encoding="utf-8", errors="ignore")
            if "onenote:start" in t or "onenote-insert:" in t:
                out.append((repo, str(f.relative_to(d))))
    return out


def inserted_source(html: str) -> str:
    parts = []
    for m in re.finditer(r"<!--\s*onenote:start[^>]*-->(.*?)<!--\s*onenote:end\s*-->", html, re.S):
        parts.append(m.group(1))
    for m in re.finditer(r'<div class="onenote-insert"[^>]*>(.*?)</div>', html, re.S):
        parts.append(m.group(1))
    return "\n\n".join(p.strip() for p in parts if len(p.strip()) > 80)


def _related(block: str, src: str) -> bool:
    """블록과 원문의 키워드가 2개 이상 겹치면 관련 있다고 본다."""
    import re as _re

    words = set(_re.findall(r"[가-힣]{2,}|[A-Za-z]{4,}", src.lower()))
    hit = sum(1 for w in set(_re.findall(r"[가-힣]{2,}|[A-Za-z]{4,}", block.lower())) if w in words)
    return hit >= 3


def build(repo: str, rel: str) -> dict | None:
    path = REPOS / repo / rel
    html = path.read_text(encoding="utf-8")
    src = inserted_source(html)
    if len(src) < 150:
        print(f"SKIP {repo}/{rel}: 삽입 원문이 없음", flush=True)
        return None
    secs, mode = page_blocks(html)
    secs = [(sid, s) for sid, s in secs if sid != "onenote"]
    if not secs:
        print(f"SKIP {repo}/{rel}: 단원 구분 없음", flush=True)
        return None
    listing = "\n\n".join(s for _, s in secs)
    prompt = f"""대상: {repo}/{rel}

[기존 단원 HTML — 이 구조와 사실을 유지한다]
{listing[:24000]}

[현재 페이지에 덧붙여져 있는 원노트 원문 — 이 내용을 위 단원 안으로 흡수하라]
<<<
{src[:14000]}
>>>
"""
    try:
        out = parse_json(call(prompt, max_tokens=16000, system=SYSTEM))
    except Exception as err:  # noqa: BLE001
        # 큰 페이지는 한 번에 못 만든다 → 관련 단원만 골라 단원별로 통합한다.
        print(f"  · {repo}/{rel}: 일괄 실패({err}) → 단원별 재시도", flush=True)
        out = {"sections": {}, "summary": "단원별 통합"}
        for sid, block in secs:
            if not _related(block, src):
                continue
            one = f"""대상: {repo}/{rel} · 단원: {sid}

[기존 단원 HTML]
{block[:12000]}

[원노트 원문 — 이 단원에 흡수]
<<<
{src[:6000]}
>>>
"""
            try:
                got = parse_json(call(one, max_tokens=9000, system=SYSTEM))
            except Exception:  # noqa: BLE001
                continue
            cand = (got.get("sections") or {}).get(sid)
            if not cand:
                continue
            if coverage(block, cand) < 0.9:
                continue
            out["sections"][sid] = cand
            time.sleep(0.3)
        if not out["sections"]:
            print(f"FAIL {repo}/{rel}: {err}", flush=True)
            return None

    merged, rejected = {}, []
    for sid, new_html in (out.get("sections") or {}).items():
        old = dict(secs).get(sid)
        if not old:
            continue
        if mode == "section" and not new_html.strip().startswith("<section"):
            continue
        if mode == "h2" and not new_html.strip().startswith("<h2"):
            continue
        cov = coverage(old, new_html)
        if cov < 0.9:
            rejected.append(f"{sid} {cov:.0%}")
            print(f"  ! {sid}: 보존율 {cov:.0%} → 제외", flush=True)
            continue
        merged[sid] = new_html
    if not merged:
        return None
    # 페이지 전체 사실 보존 확인
    old_page = html
    new_page = html
    for sid, block in merged.items():
        old_block = dict(secs)[sid]
        new_page = new_page.replace(old_block, block, 1)
    page_cov = coverage(re.sub(r"<[^>]+>", " ", old_page), re.sub(r"<[^>]+>", " ", new_page))
    return {
        "repo": repo,
        "file": rel,
        "sections": merged,
        "remove_sections": ["onenote"],
        "strip_onenote_inserts": True,
        "source": {"page": rel, "model": MODEL, "summary": out.get("summary", ""),
                   "rejected": rejected, "page_coverage": round(page_cov, 3)},
        "commit_message": f"docs(onenote): {rel} — 덧붙인 원노트 내용을 단원에 통합",
    }


def main() -> int:
    args = sys.argv[1:]
    only = args[args.index("--file") + 1] if "--file" in args else ""
    lst = targets()
    if only:
        lst = [t for t in lst if f"{t[0]}/{t[1]}" == only]
    print(f"대상 {len(lst)}개", flush=True)
    if "--dry-run" in args:
        for repo, rel in lst:
            print(" ", repo, rel)
        return 0

    updates = []
    for repo, rel in lst:
        upd = build(repo, rel)
        if upd:
            updates.append(upd)
            print(f"+ {repo}/{rel}: {sorted(upd['sections'])} (페이지 보존율 {upd['source']['page_coverage']:.0%})", flush=True)
        time.sleep(0.4)

    doc = {"updates": updates, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "updates": len(updates)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
