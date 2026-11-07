#!/usr/bin/env python3
"""onenote_etag.py — ETag 조건부 GET 으로 '바뀐 페이지만' 골라낸다.

왜 타임스탬프가 아니라 ETag 인가(2026-11-07 실측)
- `lastModifiedDateTime` 은 **데스크톱 편집을 반영하지 않는다**. GI 학회 정리(실제 11-03 수정)의
  Graph 값은 `2026-08-31T15:00:04Z` 였다. → '전날 수정분' 필터는 변경을 통째로 놓친다.
- 페이지 delta(`/me/onenote/pages/delta`)는 이 계정에서 400(20112), 섹션 단위 delta 는 404.
- 반면 `GET .../pages/{id}/content` 는 ETag 를 주고, 같은 ETag 로 `If-None-Match` 를 보내면 **304** 를 준다.
  → 본문을 안 받고 바뀐 것만 골라낼 수 있다.

사용
  python3 scripts/onenote_etag.py                      # 설정된 노트북 전수(조건부 GET)
  python3 scripts/onenote_etag.py --notebook "Books_for_Meds" --section "외래진료"
  python3 scripts/onenote_etag.py --save-text          # 바뀐 페이지 본문을 로컬 캐시에 저장

상태: ~/.local/state/onenote/etags.json  {pageId: {etag, title, section, checked}}
출력: JSON {changed:[...], unchanged:N, failed:[...]}
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from onenote_delta import GRAPH, access_token  # noqa: E402

NOTEBOOKS = ["Books_for_Meds", "Medical Deep Learning", "hyunsoo의 전자 필기장"]
STATE = Path.home() / ".local" / "state" / "onenote" / "etags.json"
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


def html_to_text(raw: bytes) -> str:
    """Graph content(HTML) → 요약용 평문."""
    import re

    html = raw.decode("utf-8", "ignore")
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def update_digest(pid: str, title: str, section: str, notebook: str, plain: str) -> None:
    """`onenote_digest.py` 와 같은 모양으로 요약을 갱신한다(추가 설치 없이)."""
    import re

    from onenote_digest import summarize  # 같은 폴더

    path = Path.home() / ".local" / "state" / "onenote" / "digest.json"
    doc = {"pages": {}}
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    entry = {"title": title, "notebook": notebook, "section": section}
    entry.update(summarize(plain))
    doc.setdefault("pages", {})[pid] = entry
    doc["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["count"] = len(doc["pages"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def load() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"pages": {}}


def save(doc: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def main() -> int:
    args = sys.argv[1:]
    get = lambda k, d="": args[args.index(k) + 1] if k in args else d
    only_nb = get("--notebook")
    only_sec = get("--section")
    save_text = "--save-text" in args
    limit = int(get("--limit", "0"))

    token = access_token()
    state = load()
    pages_state = state.setdefault("pages", {})
    changed, unchanged, failed = [], 0, []

    notebooks = jget(f"{GRAPH}/me/onenote/notebooks?$select=id,displayName&$top=50", token)["value"]
    for nb in notebooks:
        if nb["displayName"] not in (NOTEBOOKS if not only_nb else [only_nb]):
            continue
        sections = jget(f"{GRAPH}/me/onenote/notebooks/{nb['id']}/sections?$select=id,displayName&$top=100", token)["value"]
        for sec in sections:
            if only_sec and only_sec not in sec["displayName"]:
                continue
            listing = jget(f"{GRAPH}/me/onenote/sections/{sec['id']}/pages?$select=id,title&$top=100", token)["value"]
            for page in listing:
                pid, title = page["id"], page["title"]
                prev = pages_state.get(pid, {})
                status, headers, body = call(f"{GRAPH}/me/onenote/pages/{pid}/content", token, prev.get("etag", ""))
                etag = headers.get("ETag") or headers.get("etag") or ""
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                if status == 304:
                    unchanged += 1
                    pages_state.setdefault(pid, {}).update({"etag": etag or prev.get("etag", ""), "checked": now})
                    continue
                if status != 200:
                    failed.append({"title": title, "status": status})
                    continue
                pages_state[pid] = {"etag": etag, "title": title, "section": sec["displayName"],
                                    "notebook": nb["displayName"], "checked": now}
                changed.append({"id": pid, "title": title, "section": sec["displayName"],
                                "notebook": nb["displayName"], "bytes": len(body)})
                if save_text:
                    TEXT.mkdir(parents=True, exist_ok=True)
                    safe = "".join(c if c.isalnum() or c in "!._-" else "_" for c in pid)
                    plain = html_to_text(body)
                    (TEXT / f"{safe}.txt").write_text(plain, encoding="utf-8")
                    update_digest(pid, title, sec["displayName"], nb["displayName"], plain)
                time.sleep(0.3)
                if limit and len(changed) >= limit:
                    break
            if limit and len(changed) >= limit:
                break
        if limit and len(changed) >= limit:
            break

    state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save(state)
    print(json.dumps({"changed": changed, "unchanged": unchanged, "failed": failed,
                      "state": str(STATE)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
