#!/usr/bin/env python3
"""onenote_graph.py — Graph(웹) 기준으로 원노트 변경을 감지한다.

타임스탬프는 믿지 않는다(실측: 웹 편집분이 Graph 는 9-01, 로컬 앱은 11-03 15:52 로 서로 다르게 나옴).
본문 텍스트를 해시로 비교해 '내용이 실제로 바뀐 페이지'만 뽑는다.

  python3 scripts/onenote_graph.py --notebook Books_for_Meds      # 전체(느림, 레이트리밋 주의)
  python3 scripts/onenote_graph.py --notebook Books_for_Meds --limit 30   # 하루 30쪽씩 순환
  python3 scripts/onenote_graph.py --page-id <id>                # 한 쪽만

state: data/onenote-hashes.json  {"pages": {id: {"hash","title","section"}}, "lastRun": ISO}
표준출력: {"scanned":N,"changed":[{id,title,section,modified}]}
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from onenote_recent import call_mcp_raw  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "onenote-hashes.json"


def norm(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def digest(text: str) -> str:
    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()[:32]


def fetch_page(page_id: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            raw = call_mcp_raw("getPageContent", {"pageId": page_id, "format": "text"}, timeout=240)
            if raw and raw.strip() and raw.strip() != "null":
                return raw
        except Exception:  # noqa: BLE001
            pass
        time.sleep(20 * (attempt + 1))
    return ""


def list_pages(notebook: str) -> list[dict]:
    sections_raw = call_mcp_raw("listSections", {"notebookName": notebook})
    ids = re.findall(r"ID:\s*([^\s]+)", sections_raw or "")
    pages: list[dict] = []
    for sid in ids:
        raw = call_mcp_raw("listPages", {"sectionId": sid, "limit": 200})
        for block in re.split(r"\n(?=\d+\.\s\*\*)", raw or ""):
            name = re.search(r"\*\*(.+?)\*\*", block)
            pid = re.search(r"ID:\s*([^\s]+)", block)
            if name and pid:
                pages.append({"id": pid.group(1), "title": name.group(1).strip()})
        time.sleep(1.0)
    return pages


def main() -> int:
    a = sys.argv[1:]
    get = lambda k, d="": a[a.index(k) + 1] if k in a else d
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"pages": {}}
    known: dict = state.get("pages", {})
    changed: list[dict] = []

    if get("--page-id"):
        targets = [{"id": get("--page-id"), "title": get("--title", "")}]
    else:
        targets = list_pages(get("--notebook", "Books_for_Meds"))
        limit = int(get("--limit", "0"))
        if limit:
            targets.sort(key=lambda p: p["id"])
            offset = int(state.get("cursor", 0)) % max(len(targets), 1)
            targets = (targets + targets[:offset])[offset:offset + limit]
            state["cursor"] = (offset + limit) % max(len(list(targets)) or 1, 1)

    for item in targets:
        text = fetch_page(item["id"])
        if not text:
            continue
        h = digest(text)
        prev = known.get(item["id"], {}).get("hash")
        if prev != h:
            changed.append({"id": item["id"], "title": item["title"],
                            "section": known.get(item["id"], {}).get("section", ""), "modified": ""})
        known[item["id"]] = {"hash": h, "title": item["title"],
                             "section": known.get(item["id"], {}).get("section", "")}
        time.sleep(1.2)

    state["pages"] = known
    state["lastRun"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"scanned": len(targets), "changed": changed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
