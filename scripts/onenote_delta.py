#!/usr/bin/env python3
"""onenote_delta.py — Graph **delta** 로 '바뀐 페이지만' 받아온다 (전날 수정분만 가져오기).

왜 delta 인가
- `lastModifiedDateTime` 필터(`?$filter=lastModifiedDateTime ge ...`)는 Graph OneNote 에서
  지원되지 않거나(400) 값이 부실하다. 반면 `/me/onenote/pages/delta` 는 **변경분만** 돌려주고
  `@odata.deltaLink` 를 주므로, 그 토큰만 저장하면 다음 실행은 "그 이후 변경"만 받는다.
- 즉 매일 전수 633쪽을 훑을 필요가 없다. 첫 1회만 전체, 이후는 변경분.

사용
  python3 scripts/onenote_delta.py                 # deltaLink 있으면 증분, 없으면 전체 시작
  python3 scripts/onenote_delta.py --reset         # 토큰 버리고 처음부터
  python3 scripts/onenote_delta.py --max 200       # 한 번에 받을 최대 페이지 수

토큰
- `--cache PATH` (기본: onenote-mcp 의 .msal-cache.json) 에서 refresh token 을 읽어
  OAuth refresh_token grant 로 access token 을 새로 받는다. 액세스 토큰은 저장하지 않는다.
- CI 에서는 `ONENOTE_REFRESH_TOKEN` 환경변수(또는 secret)로 넘긴다.
- deltaLink 는 `--state PATH` (기본 ~/.local/state/onenote/delta.json) 에 저장한다.

출력: JSON (stdout) — changed 페이지 목록(id,title,lastModifiedDateTime,sectionId,parentNotebook)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "14d82eec-204b-4c2f-b7e8-296a70dab67e")
TENANT = os.environ.get("AZURE_TENANT", "consumers")
SCOPES = "Notes.Read Notes.ReadWrite Notes.Create User.Read offline_access openid profile"
CACHE = Path(os.environ.get(
    "ONENOTE_MSAL_CACHE",
    "/mnt/c/Users/pc/Downloads/ms_onenote_with_commandcode/onenote-mcp/.msal-cache.json",
))
STATE = Path(os.environ.get("ONENOTE_DELTA_STATE", str(Path.home() / ".local/state/onenote/delta.json")))
GRAPH = "https://graph.microsoft.com/v1.0"


TOKEN_FILE = Path.home() / ".local" / "state" / "onenote" / "refresh_token"


def refresh_token() -> str:
    env = os.environ.get("ONENOTE_REFRESH_TOKEN")
    if env:
        return env.strip()
    if TOKEN_FILE.exists():  # 회전된 최신 토큰(로컬 or CI 가 남긴 것)
        saved = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if saved:
            return saved
    if not CACHE.exists():
        sys.exit(f"refresh token 없음: {CACHE}")
    blob = json.loads(CACHE.read_text(encoding="utf-8-sig"))

    def walk(o):
        if isinstance(o, dict):
            if "secret" in o:
                yield o
            else:
                for v in o.values():
                    yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    best = None
    for e in walk(blob):
        secret = str(e.get("secret") or "")
        # refresh token 은 target 이 비어 있고 길이가 짧다(실측 417자). id/access 는 target·길이로 걸러진다.
        if not e.get("target") and 200 < len(secret) < 1200:
            best = secret
    if not best:
        sys.exit("refresh token 항목을 찾지 못함")
    return best


def access_token() -> str:
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token(),
        "scope": SCOPES,
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            body = json.loads(res.read())
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:400]
        sys.exit(f"token 실패 {err.code}: {detail}")
    rotated = body.get("refresh_token")
    if rotated:  # MSAL 은 매 refresh 마다 토큰을 회전시킨다 → 저장해 두지 않으면 다음 실행이 죽는다
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(rotated, encoding="utf-8")
        TOKEN_FILE.chmod(0o600)
        out = os.environ.get("ONENOTE_REFRESH_TOKEN_OUT")
        if out:
            Path(out).write_text(rotated, encoding="utf-8")
            Path(out).chmod(0o600)
    return body["access_token"]


def get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as err:
        sys.exit(f"graph 실패 {err.code} {url[:80]}: {err.read().decode('utf-8','replace')[:300]}")


def main() -> int:
    args = sys.argv[1:]
    reset = "--reset" in args
    cap = int(args[args.index("--max") + 1]) if "--max" in args else 0

    state = {"deltaLink": ""}
    if STATE.exists() and not reset:
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    link = state.get("deltaLink") or ""
    url = link or f"{GRAPH}/me/onenote/pages/delta?$select=id,title,lastModifiedDateTime,createdDateTime,parentSection,parentNotebook&$top=100"
    token = access_token()

    changed: list[dict] = []
    while True:
        doc = get(url, token)
        for page in doc.get("value", []):
            if page.get("@removed"):
                changed.append({"id": page.get("id"), "removed": True})
                continue
            changed.append({
                "id": page.get("id"),
                "title": page.get("title"),
                "lastModifiedDateTime": page.get("lastModifiedDateTime"),
                "createdDateTime": page.get("createdDateTime"),
                "sectionId": (page.get("parentSection") or {}).get("id"),
                "section": (page.get("parentSection") or {}).get("displayName"),
                "notebook": (page.get("parentNotebook") or {}).get("displayName"),
            })
        nxt = doc.get("@odata.nextLink")
        if nxt and not (cap and len(changed) >= cap):
            url = nxt
            time.sleep(0.4)
            continue
        final = doc.get("@odata.deltaLink") or link
        break

    STATE.parent.mkdir(parents=True, exist_ok=True)
    if final and not (cap and len(changed) >= cap):
        STATE.write_text(json.dumps({"deltaLink": final,
                                     "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                                    ensure_ascii=False, indent=1), encoding="utf-8")

    dates = sorted([c["lastModifiedDateTime"] for c in changed if c.get("lastModifiedDateTime")] or [])
    print(json.dumps({
        "mode": "delta" if link else "initial",
        "changed": len(changed),
        "oldest": dates[0] if dates else "",
        "newest": dates[-1] if dates else "",
        "state": str(STATE),
        "pages": changed,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
