#!/usr/bin/env python3
"""onenote_graph.py — Graph(웹) 기준으로 원노트 변경을 감지한다.

타임스탬프는 믿지 않는다(실측: 웹 편집분이 Graph 는 9-01, 로컬 앱은 11-03 15:52 로 서로 다르게 나옴).
본문 텍스트를 해시로 비교해 '내용이 실제로 바뀐 페이지'만 뽑는다.

  python3 scripts/onenote_graph.py                     # 3개 노트북 · 하루 120쪽 순환(+pinned)
  python3 scripts/onenote_graph.py --full              # 전수(느림, 레이트리밋 주의)
  python3 scripts/onenote_graph.py --notebook Books_for_Meds --limit 30
  python3 scripts/onenote_graph.py --page-id <id>      # 한 쪽만

state: data/onenote-hashes.json  {"pages": {id: {"hash","title","section","notebook"}}, "lastRun": ISO}
표준출력: {"scanned":N,"changed":[{id,title,notebook,section,firstSeen,chars}],"failed":[...]}
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
NOTEBOOKS = ["Books_for_Meds", "Medical Deep Learning", "hyunsoo의 전자 필기장"]
DEFAULT_LIMIT = 120  # 하루 순환 창(전체 633쪽 → 약 6일 1바퀴). --full 로 전수, --limit 0 과 같다.


def norm(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def digest(text: str) -> str:
    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()[:32]


ERROR_MARKERS = ("Failed to get page content", "Something failed", "cannot share any more information")


def fetch_page(page_id: str, retries: int = 3) -> tuple[str, str]:
    """본문을 가져온다. Graph 오류 문구를 본문으로 착각하지 않는다(예전 버그: 오류 문자열을 해시 → 영원히 unchanged).

    반환: (본문, 빈 문자열이면 성공 / 실패 사유)
    """
    reason = "no-response"
    for attempt in range(retries):
        try:
            raw = call_mcp_raw("getPageContent", {"pageId": page_id, "format": "text"}, timeout=240)
            text = (raw or "").strip()
            if text and text != "null":
                if any(marker in text[:300] for marker in ERROR_MARKERS):
                    reason = "graph-error"
                else:
                    return text, ""
            else:
                reason = "empty"
        except Exception as err:  # noqa: BLE001
            reason = f"error:{type(err).__name__}"
        time.sleep(20 * (attempt + 1))
    return "", reason


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
    failed: list[dict] = []

    if get("--page-id"):
        targets = [{"id": get("--page-id"), "title": get("--title", ""), "notebook": get("--notebook", "")}]
    else:
        notebook = get("--notebook")
        notebooks = [notebook] if notebook else NOTEBOOKS
        targets = []
        for nb in notebooks:
            for item in list_pages(nb):
                item["notebook"] = nb
                targets.append(item)
        limit = 0 if "--full" in a else int(get("--limit", str(DEFAULT_LIMIT)))
        if limit:
            targets.sort(key=lambda p: p["id"])
            total = len(targets)
            offset = int(state.get("cursor", 0)) % max(total, 1)
            window = (targets + targets)[offset:offset + limit]
            targets = window
            state["cursor"] = (offset + limit) % max(total, 1)
        # pinned: 매핑된 발행 대상 페이지는 순환 창과 무관하게 매번 확인한다
        seen = {t["id"] for t in targets}
        for pid, meta in known.items():
            if pid in seen or not meta.get("pinned"):
                continue
            targets.append({"id": pid, "title": meta.get("title", ""), "notebook": meta.get("notebook", "")})

    def save() -> None:
        state["pages"] = known
        state["lastRun"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(STATE)

    for item in targets:
        text, reason = fetch_page(item["id"])
        if not text:
            failed.append({"id": item["id"], "title": item["title"], "reason": reason})
            continue
        h = digest(text)
        prev = known.get(item["id"], {}).get("hash")
        if prev != h:
            changed.append({"id": item["id"], "title": item["title"], "notebook": item.get("notebook", ""),
                            "section": known.get(item["id"], {}).get("section", ""), "modified": "",
                            "firstSeen": prev is None, "chars": len(text)})
        entry = dict(known.get(item["id"], {}))
        entry.update({"hash": h, "title": item["title"] or entry.get("title", ""),
                      "notebook": item.get("notebook") or entry.get("notebook", "")})
        known[item["id"]] = entry
        save()  # 중간 저장 — 긴 실행이 끊겨도 진행분을 잃지 않는다
        time.sleep(1.2)

    save()
    print(json.dumps({"scanned": len(targets), "changed": changed, "failed": failed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
