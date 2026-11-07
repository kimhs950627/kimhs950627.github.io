#!/usr/bin/env python3
"""onenote_elaborate.py — 원노트 내용을 기존 페이지에 '정리해 넣는다'(덧붙이기 금지).

기존 `onenote_patch_build.py` 는 문단을 앵커 아래에 **삽입**했다. 그러면 페이지 끝에 원노트 복사본이
쌓인다(실제: lab-followup/liver.html 아래 '원노트' 구역 6,167자). 이 스크립트는 대신:

1. 대상 페이지의 **단원(section) HTML** 을 읽고
2. 원노트 문단을 그 단원들에 **elaborate**(기존 문장 유지 + 새 사실을 제자리에 통합, 중복 금지)
3. 단원 HTML 을 통째로 교체하고, 흡수된 '원노트' 부록은 삭제한다.
4. 검증: 기존 사실(숫자·약물명·고유명사)이 90% 이상 남아 있는지 확인. 미달이면 그 단원은 건드리지 않는다.

  python3 scripts/onenote_elaborate.py --file lab-followup/liver.html
  python3 scripts/onenote_elaborate.py --all --min-confidence 0.6
  python3 scripts/onenote_elaborate.py --file lab-followup/liver.html --out /tmp/opencode/elab.json
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from onenote_decide import MODEL, call, parse_json  # noqa: E402
from onenote_html import page_blocks  # noqa: E402

STATE = Path.home() / ".local" / "state" / "onenote"
REPOS = STATE / "repos"
TEXT = STATE / "text"
MAPPING = STATE / "mapping-model.json"
DIGEST = STATE / "digest.json"
TEXT_OUT = Path("/tmp/opencode/elaborate.json")

SYSTEM = """너는 개인 임상 노트를 이미 발행된 참고서 페이지에 **통합 편집**하는 편집자다.

입력: (1) 기존 페이지의 단원 HTML 목록 (2) 원노트 새 내용.

임무: 새 내용의 사실을 **기존 단원 안의 제자리**에 녹여 넣는다. 페이지 끝에 붙이지 않는다.

규칙:
- 기존 단원의 모든 사실(수치·약물명·기준·주의)을 **삭제하지 않는다**. 표현은 다듬어도 된다.
- 새 내용이 기존 문장과 같은 말이면 중복해서 쓰지 말고, 빠진 디테일만 보강한다.
- 새 주제가 기존 어느 단원에도 속하지 않으면 그때만 새 단원을 만들지 말고 가장 가까운 단원 끝에 소제목(h3)으로 넣는다.
- 태그는 기존 단원에서 쓰인 것만(h2/h3/p/ul/ol/li/table/thead/tbody/tr/th/td/strong/em/dl/dt/dd/div.tbl-wrap).
  class 는 기존에 있던 것만. style·script 금지.
- 단원마다 <h2 id="..."><span class="num">NN — Label</span>제목</h2> 머리부분은 **그대로** 유지한다.
- 확신 없는 내용은 쓰지 않는다. 원문에 없는 수치를 만들지 않는다.
- 출력은 JSON 하나:
{"sections": {"<section id>": "<단원 전체 HTML>", ...}, "absorbed_appendix": true, "summary": "무엇을 어디에 넣었는지 2~3줄"}
변경이 없는 단원은 sections 에 넣지 않는다(생략 = 그대로 둠).
"""

FACT = re.compile(r"\d+(?:\.\d+)?|[A-Za-z]{4,}|[가-힣]{4,}")
STOP = {"에서", "으로", "한다", "있다", "경우", "대한", "위해", "따라", "그리고", "또는", "때문", "정도",
        "이상", "이하", "보고", "권고", "사용", "치료", "환자", "검사", "정리", "내용", "방법"}


def facts(html: str) -> collections.Counter:
    text = re.sub(r"<[^>]+>", " ", html)
    return collections.Counter(w for w in FACT.findall(text) if w not in STOP)


def coverage(before: str, after: str) -> float:
    b, a = facts(before), facts(after)
    if not b:
        return 1.0
    kept = sum(min(c, a.get(w, 0)) for w, c in b.items())
    return kept / sum(b.values())


def sections(html: str) -> list[tuple[str, str]]:
    """section 태그는 aria-labelledby 로만 두고 id 는 안의 h2 에 있는 경우가 많다."""
    out = []
    for m in re.finditer(r"<section\b[^>]*>.*?</section>", html, re.S):
        block = m.group(0)
        if block.count("<section") > 1:
            continue
        head = re.search(r'<h2[^>]*\bid="([^"]+)"', block) or re.search(r'aria-labelledby="([^"]+)"', block)
        if head:
            out.append((head.group(1), block))
    return out


def safe(pid: str) -> str:
    return re.sub(r"[^A-Za-z0-9!._-]", "_", pid)


def slice_source(text: str, page_headings: list[str], seg_headings: list[str]) -> str:
    """페이지 하나가 여러 발행 파일로 갈라질 때, 그 segment 의 소제목 구간만 잘라낸다.

    자르지 않으면 같은 원문이 대상 파일 여러 개에 통째로 들어가 페이지끼리 내용이 중복된다
    (2026-11-08 실측: '간담췌 파트 술기' 1쪽 → abdomen-technique·abdomen-findings·thyroid·
    hernia·ct-mr 5개 파일).

    경계는 '다른 segment 의 소제목'으로만 끊는다. 어느 segment 에도 안 잡힌 소제목은
    앞 구간의 연속으로 본다(그래야 소제목 목록이 잘려 있어도 본문이 사라지지 않는다).
    """
    want = {h.strip() for h in (seg_headings or []) if h and h.strip()}
    if not want:
        return text
    page_heads = {h.strip() for h in page_headings if h and h.strip()} | want

    def match(line: str) -> str | None:
        s = line.strip().lstrip("#").strip()
        if not s:
            return None
        if s in page_heads:
            return "__seg__" if s in want else "__other__"
        for h in page_heads:
            if len(h) >= 6 and (s.startswith(h) or h.startswith(s)):
                return "__seg__" if h in want else "__other__"
        return None

    kept: list[str] = []
    current = None
    # 첫 소제목 앞의 머리 문단은 '첫 소제목을 가진 segment' 것으로 본다(그렇지 않으면 사라진다)
    for line in text.splitlines():
        who = match(line)
        if who is not None:
            current = who
            break
    for line in text.splitlines():
        who = match(line)
        if who is not None:
            current = who
        if current == "__seg__":
            kept.append(line)
    body = "\n".join(kept).strip()
    return body or text


def build(repo: str, rel: str, source_text: str, page_title: str) -> dict | None:
    path = REPOS / repo / rel
    if not path.exists():
        print(f"SKIP {repo}/{rel}: 파일 없음", flush=True)
        return None
    html = path.read_text(encoding="utf-8")
    # 페이지에 이미 '원노트' 부록으로 쌓여 있으면, 그것을 흡수 대상 원문으로 쓴다(이번 요구의 핵심).
    region = re.search(r"<!--\s*onenote:start[^>]*-->(.*?)<!--\s*onenote:end\s*-->", html, re.S)
    if region and len(region.group(1)) > 200:
        source_text = region.group(1)
    secs, mode = page_blocks(html)
    secs = [(sid, s) for sid, s in secs if sid != "onenote"]
    if not secs:
        print(f"SKIP {repo}/{rel}: 단원 없음", flush=True)
        return None
    listing = "\n\n".join(s for _, s in secs)
    prompt = f"""대상: {repo}/{rel} · 페이지: {page_title}

[기존 단원 HTML]
{listing[:24000]}

[원노트 새 내용 — 이 내용을 위 단원들에 통합하라]
<<<
{source_text[:14000]}
>>>
"""
    try:
        out = parse_json(call(prompt, max_tokens=16000, system=SYSTEM))
    except Exception as err:  # noqa: BLE001
        print(f"FAIL {repo}/{rel}: {err}", flush=True)
        return None

    merged: dict[str, str] = {}
    rejected: list[str] = []
    for sid, new_html in (out.get("sections") or {}).items():
        old = dict(secs).get(sid)
        if not old:
            print(f"  ! 알 수 없는 단원 {sid} — 무시", flush=True)
            continue
        if mode == "section" and not new_html.strip().startswith("<section"):
            print(f"  ! {sid}: section 태그 없음 — 무시", flush=True)
            continue
        if mode == "h2" and not new_html.strip().startswith("<h2"):
            print(f"  ! {sid}: h2 시작 아님 — 무시", flush=True)
            continue
        cov = coverage(old, new_html)
        # 새 내용이 실제로 늘었는지 + 기존 사실 보존 확인
        if cov < 0.9:
            rejected.append(f"{sid} (보존율 {cov:.0%})")
            print(f"  ! {sid}: 기존 사실 보존 {cov:.0%} < 90% → 건드리지 않음", flush=True)
            continue
        merged[sid] = new_html
    if not merged:
        return None
    return {
        "repo": repo,
        "file": rel,
        "sections": merged,
        "remove_sections": ["onenote"] if out.get("absorbed_appendix") else [],
        "source": {"page": page_title, "model": MODEL, "summary": out.get("summary", ""),
                   "rejected": rejected, "coverage": {sid: round(coverage(dict(secs)[sid], merged[sid]), 3)
                                                      for sid in merged}},
        "commit_message": f"docs(onenote): {page_title[:40]} — 원노트 내용을 단원에 통합",
    }


def main() -> int:
    args = sys.argv[1:]
    get = lambda k, d="": args[args.index(k) + 1] if k in args else d
    only = get("--file")
    min_conf = float(get("--min-confidence", "0.6"))
    out_path = Path(get("--out", str(TEXT_OUT)))
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    digest = json.loads(DIGEST.read_text(encoding="utf-8")) if DIGEST.exists() else {"pages": {}}
    updates: list[dict] = []

    seen: set[tuple[str, str]] = set()
    for pid, dec in mapping["pages"].items():
        if not dec.get("academic"):
            continue
        for seg in dec.get("segments") or []:
            repo, rel = seg.get("repo"), seg.get("file")
            if repo in (None, "NEW") or not rel or float(seg.get("confidence", 0)) < min_conf:
                continue
            if only and f"{repo}/{rel}" != only:
                continue
            if (repo, rel) in seen:
                continue
            f = TEXT / f"{safe(pid)}.txt"
            if not f.exists():
                continue
            seen.add((repo, rel))
            src = f.read_text(encoding="utf-8", errors="ignore")[:14000]
            heads: list[str] = []
            for other in dec.get("segments") or []:  # 경계는 segment 소제목으로만 세운다(digest 소제목은 잘려 있음)
                heads += [h for h in (other.get("headings") or []) if h not in heads]
            multi = len({(s2.get("repo"), s2.get("file")) for s2 in dec.get("segments") or []
                         if s2.get("repo") not in (None, "NEW")}) > 1
            if multi:  # 한 페이지가 여러 파일로 갈라질 때만 자른다(단일 대상이면 전문을 넣는다)
                src = slice_source(src, heads, seg.get("headings") or [])[:14000]
            upd = build(repo, rel, src, dec.get("page", ""))
            if upd:
                updates.append(upd)
                print(f"+ {repo}/{rel}: {sorted(upd['sections'])}", flush=True)
            time.sleep(0.4)

    doc = {"updates": updates, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out_path), "updates": len(updates)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
