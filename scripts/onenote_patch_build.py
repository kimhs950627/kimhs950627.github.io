#!/usr/bin/env python3
"""onenote_patch_build.py — 모델 매핑(mapping-model.json)을 실제 HTML 패치로 바꾼다.

  python3 scripts/onenote_patch_build.py                 # confidence>=0.6, 기존 파일 대상
  python3 scripts/onenote_patch_build.py --page "GI 학회 정리"
  python3 scripts/onenote_patch_build.py --min-confidence 0.5

동작
1. 매핑의 각 segment(headings)로 원문을 잘라낸다.
2. 대상 파일의 단원(h2 id)과 주제를 비교해 앵커를 고른다. 앵커가 없으면 region 삽입으로 둔다.
3. Command Code API(deepseek-v4.1-flash, low)로 원문 → HTML 조각 변환.
4. /tmp/opencode/patch-existing.json 생성 (제출용).

출력 JSON: {"updates":[{repo,file,anchor_id|region,heading,html,source:{page,headings}}], ...}
"""
from __future__ import annotations

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

STATE = Path.home() / ".local" / "state" / "onenote"
REPOS = STATE / "repos"
TEXT = STATE / "text"
MAPPING = STATE / "mapping-model.json"
DIGEST = STATE / "digest.json"
OUT = Path("/tmp/opencode/patch-existing.json")

SYSTEM = """너는 개인 학습 노트를 정적 HTML 페이지에 옮기는 편집자다. 원노트 문단 하나와 대상 파일의 단원(h2) 목록이
주어지면, 그 내용을 손실 없이 HTML 조각으로 바꾸고 **어느 단원에 넣을지** 고른다.

규칙:
- 원문에 있는 내용만 쓴다. 요약·창작·추측 금지. 없는 수치는 "확인 필요"로 남긴다.
- 태그는 h3, h4, p, ul, ol, li, table, thead, tbody, tr, th, td, strong, em, dt, dd, dl 만 쓴다.
- <html>, <body>, <section>, <script>, style 속성, class 속성 금지(기존 페이지 CSS 를 건드리지 않는다).
- 원문의 표·목록 구조를 최대한 보존한다. 제목 줄은 h3 로 만든다.
- anchor_id 는 주어진 단원 id 중 주제가 가장 맞는 것 하나. 맞는 단원이 없으면 "".
- 문장은 원문을 다듬되 의미를 바꾸지 않는다. 개인정보·연락처·금액은 옮기지 않는다.
- 출력은 JSON 하나: {"anchor_id": "...", "html": "...", "summary": "한 줄"}
"""


def safe(page_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9!._-]", "_", page_id)


def norm(text: str) -> str:
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text or "")


def find_line(lines: list[str], heading: str) -> int:
    key = norm(heading)[:20]
    if not key:
        return -1
    for i, line in enumerate(lines):
        if key in norm(line):
            return i
    return -1


def slice_segments(text: str, segments: list[dict]) -> list[tuple[dict, str]]:
    """여러 segment 의 시작 소제목 위치를 찾아 '그 사이' 구간으로 잘라낸다.

    소제목 휴리스틱으로 끝을 찾으면 표·설명 줄을 제목으로 오인해 잘린다(2026-11-07 실제 발생:
    'm/c reason : MASLD' 에서 잘려 38자만 남음). 그래서 다음 segment 의 시작을 경계로 쓴다.
    """
    lines = text.splitlines()
    starts: list[tuple[int, dict]] = []
    for seg in segments:
        pos = -1
        for h in seg.get("headings") or []:
            pos = find_line(lines, h)
            if pos >= 0:
                break
        if pos >= 0:
            starts.append((pos, seg))
    starts.sort(key=lambda x: x[0])
    out: list[tuple[dict, str]] = []
    for idx, (pos, seg) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[pos:end]).strip()
        if chunk:
            out.append((seg, chunk))
    return out


def file_h2(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    html = path.read_text(encoding="utf-8", errors="ignore")
    return [(m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip())
            for m in re.finditer(r'<h2[^>]*id="([^"]+)"[^>]*>(.*?)</h2>', html, re.S)]


def pick_anchor(path: Path, topic: str, segment_text: str) -> tuple[str, str]:
    """대상 파일의 h2 중 주제와 가장 맞는 것을 고른다.

    토큰 겹침만 쓰면 '진정 (sedation)' 처럼 짧은 제목이 임계값(3)을 못 넘어 앵커를 놓친다.
    제목 문자열이 주제/본문에 그대로 등장하면 강한 신호로 본다.
    """
    h2s = file_h2(path)
    if not h2s:
        return "", ""
    body = (topic + " " + segment_text[:4000]).lower()
    hay = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", body))
    best = ("", "", 0)
    for hid, htext in h2s:
        ht = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", htext.lower()))
        score = len(hay & ht)
        key = norm(htext).lower()
        if len(key) >= 3 and key in norm(body).lower():
            score += 4
        if score > best[2]:
            best = (hid, htext, score)
    return (best[0], best[1]) if best[2] >= 2 else ("", "")


def main() -> int:
    a = sys.argv[1:]
    get = lambda k, d="": a[a.index(k) + 1] if k in a else d
    only_page = get("--page")
    min_conf = float(get("--min-confidence", "0.6"))

    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    digest = json.loads(DIGEST.read_text(encoding="utf-8"))["pages"]
    by_title = {m["title"]: (pid, m) for pid, m in digest.items()}

    updates: list[dict] = []
    skipped: list[str] = []
    for pid, dec in mapping["pages"].items():
        if not dec.get("academic"):
            continue
        page = dec.get("page") or digest.get(pid, {}).get("title", "")
        if only_page and page != only_page:
            continue
        f = TEXT / f"{safe(pid)}.txt"
        if not f.exists():
            skipped.append(f"{page}: 본문 캐시 없음")
            continue
        full = f.read_text(encoding="utf-8", errors="ignore")
        candidates = [s for s in (dec.get("segments") or [])
                      if s.get("repo") != "NEW" and s.get("file") and float(s.get("confidence", 0)) >= min_conf]
        for seg in (dec.get("segments") or []):
            if seg.get("repo") == "NEW" or not seg.get("file"):
                continue
            if float(seg.get("confidence", 0)) < min_conf:
                skipped.append(f"{page}: 낮은 확신 {seg.get('confidence')} → {seg.get('new_title') or seg.get('file')}")
        for seg, chunk in slice_segments(full, candidates):
            repo, rel = seg.get("repo"), seg.get("file")
            conf = float(seg.get("confidence", 0))
            if len(chunk) < 80:
                skipped.append(f"{page}/{rel}: 문단 추출 실패")
                continue
            path = REPOS / repo / rel
            h2s = file_h2(path)
            anchor_id, anchor_text = pick_anchor(path, seg.get("topic", ""), chunk)
            listing = "\n".join(f"- {hid} : {htext}" for hid, htext in h2s[:40]) or "(단원 없음)"
            prompt = f"""대상 페이지: {repo}/{rel}
원노트 페이지: {page}
문단 소제목: {", ".join(seg.get("headings") or [])}
주제: {seg.get("topic", "")}

대상 파일의 단원(h2) 후보:
{listing}

원문:
<<<
{chunk[:14000]}
>>>
"""
            try:
                out = parse_json(call(prompt, max_tokens=6000, system=SYSTEM))
            except Exception as err:  # noqa: BLE001
                skipped.append(f"{page}/{rel}: API 실패 {err}")
                continue
            html = (out.get("html") or "").strip()
            model_anchor = (out.get("anchor_id") or "").strip()
            if model_anchor and any(model_anchor == hid for hid, _ in h2s):
                anchor_id = model_anchor
            if html.startswith("<h2"):
                html = "<h3" + html[3:]
            if len(html) < 80:
                skipped.append(f"{page}/{rel}: 변환 결과 없음")
                continue
            upd = {
                "repo": repo,
                "file": rel,
                "html": html,
                "source": {"page": page, "headings": seg.get("headings"), "topic": seg.get("topic"),
                           "confidence": conf, "model": MODEL},
                "commit_message": f"chore(onenote): {page} — {seg.get('topic','')[:60]}",
            }
            if anchor_id:
                upd["anchor_id"] = anchor_id
            else:
                upd["region"] = "onenote"
                upd["heading"] = f"원노트 — {seg.get('topic') or page}"
            updates.append(upd)
            print(f"+ {repo}/{rel} ← {page} :: {(seg.get('topic') or '')[:40]} ({len(html)}자, anchor={anchor_id or 'region'})", flush=True)
            time.sleep(0.5)

    patch = {"updates": updates, "source": {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                            "model": MODEL, "pages": len({u['source']['page'] for u in updates})}}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(patch, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(OUT), "updates": len(updates), "skipped": skipped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
