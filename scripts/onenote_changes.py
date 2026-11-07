#!/usr/bin/env python3
"""onenote_changes.py — '바뀐 섹션만' 골라 그 안에서 ETag 로 바뀐 페이지만 확인한다.

실측 근거(2026-11-08)
- **페이지** `lastModifiedDateTime` 은 데스크톱 편집을 반영하지 않는다.
  GI 학회 정리(실제 11-03 수정)의 페이지 값은 `2026-08-31T15:00:04Z`.
- **섹션** `lastModifiedDateTime` 은 반영한다. 같은 섹션이 `2026-11-03T22:54:07Z`.
  (다른 섹션들도 11-04·11-05 로 최신)
- 그래서 ① 섹션 시각으로 후보 섹션을 고르고 ② 그 섹션 페이지에만 ETag 조건부 GET 을 보낸다.
  변경 없으면 304(본문 0바이트). 페이지 delta 는 이 계정에서 400/404 라 쓸 수 없다.

  python3 scripts/onenote_changes.py                 # 워터마크 이후 바뀐 섹션만
  python3 scripts/onenote_changes.py --since 2d      # 최근 2일
  python3 scripts/onenote_changes.py --full          # 섹션 무시하고 전수(주 1회 안전망)
  python3 scripts/onenote_changes.py --save-text     # 바뀐 페이지 본문을 digest/text 에 반영

출력: JSON {sections_changed, pages_checked, changed[], failed[]}
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from onenote_delta import GRAPH, access_token  # noqa: E402
from onenote_etag import STATE as TEXT_STATE, html_to_text, update_digest  # noqa: E402

NOTEBOOKS = ["Books_for_Meds", "Medical Deep Learning", "hyunsoo의 전자 필기장"]
STATE = Path.home() / ".local" / "state" / "onenote" / "sections.json"
TEXT = Path.home() / ".local" / "state" / "onenote" / "text"


def call(url: str, token: str, etag: str = "") -> tuple[int, dict, bytes]:
    headers = {"Authorization": f"Bearer {token}"}
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def jget(url: str, token: str) -> dict:
    status, _, body = call(url, token)
    if status != 200:
        raise RuntimeError(f"{status} {url[:70]} {body[:120]!r}")
    return json.loads(body)


def load() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"sections": {}}


def save(doc: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def parse_since(value: str) -> datetime:
    m = re.fullmatch(r"(\d+)([hdw])", value or "")
    if not m:
        return datetime.now(timezone.utc) - timedelta(days=1)
    n, unit = int(m.group(1)), m.group(2)
    return datetime.now(timezone.utc) - timedelta(**{"h": {"hours": n}, "d": {"days": n}, "w": {"weeks": n}}[unit])


def main() -> int:
    args = sys.argv[1:]
    get = lambda k, d="": args[args.index(k) + 1] if k in args else d
    full = "--full" in args
    save_text = "--save-text" in args
    since = parse_since(get("--since", "1d"))
    only_nb = get("--notebook")

    token = access_token()
    state = load()
    sec_state = state.setdefault("sections", {})

    started = time.time()
    changed: list[dict] = []
    failed: list[dict] = []
    checked = 0
    sections_scanned = sections_changed = 0

    notebooks = jget(f"{GRAPH}/me/onenote/notebooks?$select=id,displayName&$top=50", token)["value"]
    for nb in notebooks:
        if nb["displayName"] not in (NOTEBOOKS if not only_nb else [only_nb]):
            continue
        sections = jget(
            f"{GRAPH}/me/onenote/notebooks/{nb['id']}/sections?$select=id,displayName,lastModifiedDateTime&$top=100",
            token)["value"]
        for sec in sections:
            sections_scanned += 1
            sid = sec["id"]
            lm = sec.get("lastModifiedDateTime") or ""
            prev = sec_state.get(sid) or {}
            prev_lm = prev.get("lastModifiedDateTime") or ""
            is_new = not prev
            touched = is_new or lm > prev_lm or (lm and lm >= since.isoformat())
            if not (full or touched):
                continue
            sections_changed += 1
            entry = sec_state.setdefault(sid, {})
            entry.update({"displayName": sec["displayName"], "notebook": nb["displayName"],
                          "lastModifiedDateTime": lm})
            pages_etags = entry.setdefault("pages", {})
            try:
                listing = jget(f"{GRAPH}/me/onenote/sections/{sid}/pages?$select=id,title&$top=100", token)["value"]
            except RuntimeError as err:
                failed.append({"section": sec["displayName"], "error": str(err)[:80]})
                continue
            for page in listing:
                pid, title = page["id"], page["title"]
                prev_etag = (pages_etags.get(pid) or {}).get("etag", "")
                status, headers, body = call(f"{GRAPH}/me/onenote/pages/{pid}/content", token, prev_etag)
                checked += 1
                etag = headers.get("ETag") or headers.get("etag") or prev_etag
                if status == 304:
                    continue
                if status != 200:
                    failed.append({"title": title, "status": status})
                    continue
                pages_etags[pid] = {"etag": etag, "title": title}
                rec = {"id": pid, "title": title, "section": sec["displayName"],
                       "notebook": nb["displayName"], "sectionModified": lm, "bytes": len(body)}
                if save_text:
                    TEXT.mkdir(parents=True, exist_ok=True)
                    plain = html_to_text(body)
                    safe = re.sub(r"[^A-Za-z0-9!._-]", "_", pid)
                    (TEXT / f"{safe}.txt").write_text(plain, encoding="utf-8")
                    update_digest(pid, title, sec["displayName"], nb["displayName"], plain)
                    rec["chars"] = len(plain)
                changed.append(rec)

    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save(state)
    print(json.dumps({
        "sections_scanned": sections_scanned, "sections_changed": sections_changed,
        "pages_checked": checked, "changed": changed, "failed": failed,
        "seconds": round(time.time() - started, 1), "mode": "full" if full else "incremental",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
