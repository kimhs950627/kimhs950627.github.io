#!/usr/bin/env python3
"""onenote_leftover.py — 모델이 흡수하지 못한 '남은 원문'을 가장 관련 있는 단원 안으로 옮긴다.

모델 재작성 없이 **원문 그대로** 옮기므로 내용 손실이 없다. 페이지 끝 부록만 사라진다.
대상: remerge/absorb 후 흡수율 80% 미만인 페이지.

  python3 scripts/onenote_leftover.py /tmp/opencode/remerge.json --min-coverage 0.8
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from onenote_elaborate import coverage  # noqa: E402
from onenote_html import page_blocks  # noqa: E402
from onenote_remerge import REPOS, inserted_source  # noqa: E402


def chunks(src: str) -> list[str]:
    parts = re.split(r"(?=<h3\b)", src)
    out = [p.strip() for p in parts if len(p.strip()) > 40]
    if len(out) <= 1:  # h3 가 없으면 문단 단위
        out = [p.strip() for p in re.split(r"\n\s*\n", src) if len(p.strip()) > 60]
    return out


def score(chunk: str, block: str) -> int:
    a = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{4,}", chunk.lower()))
    b = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{4,}", block.lower()))
    return len(a & b)


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
    args = sys.argv[1:]
    patch = Path(args[0] if args and not args[0].startswith("-") else "/tmp/opencode/remerge.json")
    min_cov = float(args[args.index("--min-coverage") + 1]) if "--min-coverage" in args else 0.8
    doc = json.loads(patch.read_text(encoding="utf-8"))

    fixed = 0
    for u in doc["updates"]:
        html = (REPOS / u["repo"] / u["file"]).read_text(encoding="utf-8")
        src = inserted_source(html)
        if not src:
            continue
        cov = coverage(src, render(html, u["sections"], set(u.get("remove_sections") or []), True))
        if cov >= min_cov:
            continue
        secs, _ = page_blocks(html)
        secs = [(k, b) for k, b in secs if k != "onenote"]
        placed = 0
        for ch in chunks(src):
            body = re.sub(r"<[^>]+>", " ", ch)
            if len(body.strip()) < 40:
                continue
            # 이미 그 내용이 페이지에 있으면 건너뛴다(중복 방지)
            if coverage(ch, render(html, u["sections"], set(u.get("remove_sections") or []), True)) >= 0.7:
                continue
            ranked = sorted(secs, key=lambda kb: -score(ch, kb[1]))
            if not ranked:
                continue
            sid, block = ranked[0]
            cur = u["sections"].get(sid, block)
            if sid not in u["sections"]:
                u["sections"][sid] = cur
            # 블록 끝(닫는 태그 앞)에 원문 조각을 붙인다
            m = re.search(r"</section>\s*$", cur.strip(), re.S)
            if m:
                u["sections"][sid] = cur.strip()[: m.start()] + "\n" + ch + "\n" + cur.strip()[m.start():]
            else:
                u["sections"][sid] = cur.rstrip() + "\n" + ch
            placed += 1
        u["remove_sections"] = ["onenote"]
        u["strip_onenote_inserts"] = True
        cov2 = coverage(src, render(html, u["sections"], {"onenote"}, True))
        u["source"] = {**(u.get("source") or {}), "source_coverage": round(cov2, 3), "leftover_chunks": placed}
        print(f"{u['repo']}/{u['file']}: {cov:.0%} → {cov2:.0%} (조각 {placed}개를 단원 안으로)", flush=True)
        fixed += 1

    patch.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"fixed": fixed, "total": len(doc["updates"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
