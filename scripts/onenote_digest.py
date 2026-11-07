#!/usr/bin/env python3
"""onenote_digest.py — 전 노트북 페이지를 훑어 '내용 요약 JSON'을 만든다.

본문 전문은 로컬 상태 폴더에만 두고(공개 저장소 금지), 요약(제목·섹션·글자수·소제목·키워드·도입부)을
`~/.local/state/onenote/digest.json` 에 남긴다. 이 요약이 매핑 제안의 근거다.

  python3 scripts/onenote_digest.py                  # 3개 노트북 전수(이미 받은 본문은 재사용)
  python3 scripts/onenote_digest.py --notebook "Books_for_Meds"
  python3 scripts/onenote_digest.py --force          # 본문 다시 받기
  python3 scripts/onenote_digest.py --report         # 요약만 다시 계산(네트워크 없음)

본문 캐시: ~/.local/state/onenote/text/<id>.txt
요약:      ~/.local/state/onenote/digest.json
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from onenote_graph import NOTEBOOKS, digest as body_hash, fetch_page, list_pages  # noqa: E402
from onenote_recent import call_mcp_raw  # noqa: E402

STATE_DIR = Path.home() / ".local" / "state" / "onenote"
TEXT_DIR = STATE_DIR / "text"
DIGEST = STATE_DIR / "digest.json"

WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
STOP = {"정리", "공부", "내용", "학회", "노트", "참고", "방법", "개요", "총론", "자료", "시험", "대비",
        "study", "note", "notes", "reference", "guide", "intro", "page", "one", "the", "and", "for"}


def safe(page_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9!._-]", "_", page_id)


def section_map(notebook: str) -> dict[str, str]:
    """섹션 id → 섹션 이름."""
    raw = call_mcp_raw("listSections", {"notebookName": notebook}) or ""
    out: dict[str, str] = {}
    name = ""
    for line in raw.splitlines():
        stripped = line.strip()
        m = re.match(r"\*\*(.+?)\*\*", stripped)
        if m:
            name = m.group(1).strip()
            continue
        m = re.search(r"ID:\s*(\S+)", stripped)
        if m and name:
            out[m.group(1)] = name
    return out


def summarize(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    heads = [ln for ln in lines if 2 <= len(ln) <= 80 and not ln.startswith(("📄", "📅"))][:12]
    freq: dict[str, int] = {}
    for w in WORD.findall(text):
        w = w.lower()
        if w in STOP:
            continue
        freq[w] = freq.get(w, 0) + 1
    keywords = [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:15]]
    return {
        "chars": len(text),
        "lines": len(lines),
        "headings": heads,
        "keywords": keywords,
        "sample": re.sub(r"\s+", " ", text)[:300],
        "hash": body_hash(text),
    }


def load_digest() -> dict:
    if DIGEST.exists():
        try:
            return json.loads(DIGEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"generated": None, "count": 0, "pages": {}}


def save_digest(doc: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    doc["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["count"] = len(doc["pages"])
    tmp = DIGEST.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(DIGEST)


def main() -> int:
    a = sys.argv[1:]
    get = lambda k, d="": a[a.index(k) + 1] if k in a else d
    force = "--force" in a
    report_only = "--report" in a
    notebook = get("--notebook")
    notebooks = [notebook] if notebook else NOTEBOOKS

    doc = load_digest()
    pages = doc.setdefault("pages", {})
    TEXT_DIR.mkdir(parents=True, exist_ok=True)

    if report_only:
        for pid, meta in pages.items():
            f = TEXT_DIR / f"{safe(pid)}.txt"
            if f.exists():
                meta.update(summarize(f.read_text(encoding="utf-8")))
        save_digest(doc)
        print(json.dumps({"count": len(pages), "generated": doc["generated"]}, ensure_ascii=False))
        return 0

    scanned = failed = 0
    for nb in notebooks:
        secs = section_map(nb)
        for item in list_pages(nb):
            pid = item["id"]
            f = TEXT_DIR / f"{safe(pid)}.txt"
            text = ""
            if f.exists() and not force:
                text = f.read_text(encoding="utf-8")
            else:
                text, reason = fetch_page(pid)
                if not text:
                    failed += 1
                    print(f"FAIL {item['title'][:40]} :: {reason}", flush=True)
                    pages.setdefault(pid, {})["failed"] = reason
                    continue
                f.write_text(text, encoding="utf-8")
                time.sleep(1.2)
            entry = {
                "title": item["title"],
                "notebook": nb,
                "section": secs.get(item.get("sectionid", ""), ""),
            }
            entry.update(summarize(text))
            entry.pop("failed", None)
            pages[pid] = entry
            scanned += 1
            if scanned % 10 == 0:
                save_digest(doc)
                print(f"{scanned} pages (last: {item['title'][:40]} {entry['chars']}자)", flush=True)

    save_digest(doc)
    print(json.dumps({"scanned": scanned, "failed": failed, "total": len(pages)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
