#!/usr/bin/env python3
"""track.py — 저장소·페이지의 변경을 기록하고, 색인에 없는 페이지를 찾아낸다.

  python3 scripts/track.py                # 스캔해서 data/activity.json · data/repos-state.json 갱신
  python3 scripts/track.py --apply        # 공개인데 색인에 없는 페이지를 data/curated.json 에 자동 등재
  python3 scripts/track.py --noti         # 요약을 data/noti-queue.jsonl 에 넣는다(아침 알림이 발송)
  python3 scripts/track.py --publish-private  # 비공개 후보를 Cloudflare Pages + Access(소유자만)로 발행

하는 일(전부 LLM 없이 결정적으로):
  1) 이 계정의 저장소 전부(비공개 포함)를 GitHub API 로 훑어 마지막 커밋·공개 상태·Pages 상태를 읽는다.
  2) 지난 스냅숏(data/repos-state.json)과 비교해 새 저장소·새 커밋·공개/비공개 전환·Pages 켜짐/꺼짐을 남긴다.
     스냅숏에는 비공개 저장소 이름이 들어가므로 .gitignore 로 빼고 워크플로 캐시에만 둔다.
     공개로 내보내는 data/activity.json 에는 비공개 저장소의 이름·커밋 메시지를 넣지 않는다.
  3) 색인(data/pages.json)·손으로 적은 목록(data/curated.json)에 없는 저장소 중 실제 페이지가 살아 있는 것을
     '미등록 후보'로 모은다. 공개는 --apply 로 curated.json 에 자동 등재하고,
     비공개는 --publish-private 로 publish.py 를 불러 Cloudflare Pages + Access(소유자 이메일만)로 발행한다 —
     색인에는 제목·설명만 실리고 내용은 소유자 로그인 없이는 열리지 않는다.
  4) 변경 이력은 data/activity.json 에 최근 window_days 일만 남긴다(사이트 '최근 변경' 절이 읽는다).

환경: PAGES_TOKEN · GH_TOKEN · GITHUB_TOKEN 중 먼저 있는 것. 없으면 `gh auth token`.
"""

from __future__ import annotations

import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OWNER = os.environ.get("PAGES_OWNER", "kimhs950627")
ROOT = Path(__file__).resolve().parent.parent
CURATED = ROOT / "data" / "curated.json"
PAGES = ROOT / "data" / "pages.json"
STATE = ROOT / "data" / "repos-state.json"
ACTIVITY = ROOT / "data" / "activity.json"
IGNORE = ROOT / "data" / "track-ignore.json"
KST = timezone(timedelta(hours=9))
WINDOW_DAYS = 21
UA = f"{OWNER}-pages-hub-track"

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
DESC_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]*content=["\'](.*?)["\']',
    re.I | re.S,
)


def token() -> str:
    for key in ("PAGES_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()


def api(path: str, tok: str, auth: bool = True) -> tuple[int, object]:
    url = path if path.startswith("https://") else f"https://api.github.com{path}"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    if auth and tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=25) as res:
            raw = res.read()
            return res.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        return err.code, {}
    except Exception as err:  # noqa: BLE001 — 네트워크 실패는 '모름'
        print(f"  ! {url} — {err}", file=sys.stderr)
        return 0, {}


def fetch_page(url: str, tok: str) -> tuple[int, str, str]:
    """(HTTP 상태, 제목, 설명)."""
    safe = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")
    req = urllib.request.Request(safe, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            body = res.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        return err.code, "", ""
    except Exception:  # noqa: BLE001
        return 0, "", ""
    head = body[:20000]
    m = TITLE_RE.search(head)
    d = DESC_RE.search(head)
    clean = lambda s: re.sub(r"\s+", " ", html.unescape(s or "")).strip()  # noqa: E731
    return 200, clean(m.group(1)) if m else "", clean(d.group(1)) if d else ""


def list_repos(tok: str) -> list[dict]:
    """저장소 전부(비공개 포함). /users/{owner}/repos 는 공개만 주므로 /user/repos 를 먼저 쓴다."""
    repos: list[dict] = []
    page = 1
    while True:
        status, chunk = api(
            f"/user/repos?per_page=100&page={page}&affiliation=owner&sort=pushed", tok
        )
        if status != 200 or not isinstance(chunk, list):
            print(f"저장소 목록을 못 받음 (HTTP {status}) — 공개 목록으로 대체", file=sys.stderr)
            return list_public_repos(tok)
        repos.extend(r for r in chunk if (r.get("owner") or {}).get("login", "").lower() == OWNER.lower())
        if len(chunk) < 100:
            break
        page += 1
    return repos


def list_public_repos(tok: str) -> list[dict]:
    repos: list[dict] = []
    page = 1
    while True:
        status, chunk = api(
            f"/users/{OWNER}/repos?per_page=100&page={page}&type=owner&sort=pushed", tok
        )
        if status != 200 or not isinstance(chunk, list):
            print(f"저장소 목록을 못 받음 (HTTP {status})", file=sys.stderr)
            break
        repos.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return repos


def scan(tok: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for repo in list_repos(tok):
        name = repo["name"]
        branch = repo.get("default_branch") or "main"
        status, commits = api(f"/repos/{OWNER}/{name}/commits?per_page=1&sha={branch}", tok)
        latest = {}
        if status == 200 and isinstance(commits, list) and commits:
            c = commits[0]
            latest = {
                "sha": c.get("sha", ""),
                "message": (c.get("commit", {}).get("message") or "").splitlines()[0][:120],
                "at": (c.get("commit", {}).get("author", {}).get("date") or "")[:19],
            }
        pages_status, pages = api(f"/repos/{OWNER}/{name}/pages", tok)
        out[name] = {
            "private": bool(repo.get("private")),
            "archived": bool(repo.get("archived")),
            "fork": bool(repo.get("fork")),
            "branch": branch,
            "pushed_at": (repo.get("pushed_at") or "")[:19],
            "description": (repo.get("description") or "").strip(),
            "commit": latest,
            "pages": pages.get("html_url") if pages_status == 200 and isinstance(pages, dict) else "",
        }
    return out


def listed_repos() -> set[str]:
    listed: set[str] = set()
    if PAGES.exists():
        try:
            data = json.loads(PAGES.read_text(encoding="utf-8"))
            listed |= {p["repo"] for g in data.get("groups", []) for p in g.get("pages", [])}
        except (json.JSONDecodeError, KeyError) as err:
            print(f"  ! data/pages.json 을 못 읽음 — {err}", file=sys.stderr)
    if CURATED.exists():
        try:
            listed |= set(json.loads(CURATED.read_text(encoding="utf-8")).get("pages", {}))
        except json.JSONDecodeError as err:
            print(f"  ! data/curated.json 을 못 읽음 — {err}", file=sys.stderr)
    return listed


def ignored() -> set[str]:
    if not IGNORE.exists():
        return set()
    try:
        data = json.loads(IGNORE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    return set(data.get("repos", []))


def diff(prev: dict[str, dict], cur: dict[str, dict], today: str) -> list[dict]:
    changes: list[dict] = []
    hub = f"{OWNER.lower()}.github.io"

    def add(repo: str, kind: str, label: str, text: str, url: str, sha: str = "") -> None:
        entry = {"at": today, "repo": repo, "kind": kind, "label": label, "text": text, "url": url}
        if sha:
            entry["sha"] = sha
        changes.append(entry)

    for name, now in cur.items():
        if name.lower() == hub:
            continue
        url = now["pages"] or f"https://github.com/{OWNER}/{name}"
        before = prev.get(name)
        if before is None:
            add(name, "new-repo", "새 저장소", now["description"] or name, url)
            if now["pages"]:
                add(name, "pages-on", "페이지 공개", "GitHub Pages 가 켜졌다", now["pages"])
            continue
        old_sha = (before.get("commit") or {}).get("sha")
        new_sha = (now.get("commit") or {}).get("sha")
        if old_sha and new_sha and old_sha != new_sha:
            add(
                name,
                "push",
                "수정",
                (now.get("commit") or {}).get("message") or "(메시지 없음)",
                url,
                sha=new_sha,
            )
        if before.get("private") != now["private"]:
            word = "비공개 → 공개" if now["private"] is False else "공개 → 비공개"
            add(name, "visibility", "공개 범위", word, url)
        if before.get("pages") != now["pages"]:
            if now["pages"]:
                add(name, "pages-on", "페이지 공개", "GitHub Pages 가 켜졌다", now["pages"])
            else:
                add(name, "pages-off", "페이지 중단", "GitHub Pages 가 꺼졌다", url)
        if before.get("archived") != now["archived"]:
            add(name, "archive", "보관", "저장소를 보관 처리함" if now["archived"] else "보관 해제", url)
    for name, before in prev.items():
        if name not in cur and name.lower() != hub:
            add(name, "deleted", "삭제", "저장소가 사라졌다(이름 변경·삭제)", f"https://github.com/{OWNER}/{name}")
    return changes


def keep_history(prev_activity: dict, fresh: list[dict], today: str) -> list[dict]:
    cutoff = (datetime.fromisoformat(today) - timedelta(days=WINDOW_DAYS)).date().isoformat()
    seen: set[tuple[str, str, str]] = set()
    merged: list[dict] = []

    def key(c: dict) -> tuple[str, str, str]:
        detail = c.get("sha") or (c.get("text", "") if c.get("kind") != "push" else "")
        return (c.get("repo", ""), c.get("kind", ""), c.get("at", "") + detail)

    for c in [*fresh, *prev_activity.get("changes", [])]:
        if c.get("at", "") < cutoff:
            continue
        k = key(c)
        if k in seen:
            continue
        seen.add(k)
        merged.append(c)
    merged.sort(key=lambda c: (c.get("at", ""), c.get("repo", "")), reverse=True)
    return merged[:200]


def unlisted(cur: dict[str, dict], listed: set[str], ignore: set[str], tok: str) -> list[dict]:
    rows: list[dict] = []
    for name, info in sorted(cur.items()):
        if name.lower() == f"{OWNER.lower()}.github.io" or name in listed or name in ignore:
            continue
        if info["pages"]:
            code, title, desc = fetch_page(info["pages"], tok)
            if code != 200:
                continue
            rows.append(
                {
                    "repo": name,
                    "url": info["pages"],
                    "private": info["private"],
                    "title": title or name,
                    "note": desc or info["description"] or "",
                    "reason": "비공개 저장소 — publish.py publish 로 Cloudflare 발행 후 curated.json 에 url 을 넣는다"
                    if info["private"]
                    else "공개 페이지 — 공개 저장소는 다음 빌드에서 자동 색인된다",
                }
            )
            continue
        # Pages 가 꺼져 있어도 사이트 소스(index.html·docs/index.html)가 있으면 후보로 알린다
        status, tree = api(
            f"/repos/{OWNER}/{name}/git/trees/{info['branch']}?recursive=1", tok
        )
        paths = {t.get("path") for t in tree.get("tree", [])} if status == 200 else set()
        source = next((p for p in ("index.html", "docs/index.html", "index.md", "docs/index.md") if p in paths), "")
        if not source:
            continue
        rows.append(
            {
                "repo": name,
                "url": f"https://github.com/{OWNER}/{name}",
                "private": info["private"],
                "title": info["description"] or name,
                "note": f"사이트 소스 {source} 가 있는데 GitHub Pages 가 꺼져 있다",
                "reason": "비공개 저장소 — publish.py publish 로 Cloudflare 발행"
                if info["private"]
                else "Pages 가 꺼져 있다 — 저장소 설정에서 Pages 를 켜면 다음 빌드에서 자동 색인된다",
            }
        )
    return rows


def apply_public(rows: list[dict]) -> list[str]:
    """공개 페이지는 curated.json '그 밖'에 자동 등재한다(비공개는 발행 절차가 필요해 건드리지 않는다)."""
    if not rows:
        return []
    data = json.loads(CURATED.read_text(encoding="utf-8")) if CURATED.exists() else {"pages": {}}
    pages = data.setdefault("pages", {})
    added: list[str] = []
    for row in rows:
        if row["private"] or row["repo"] in pages:
            continue
        pages[row["repo"]] = {
            "category": "other",
            "order": 9000,
            "title": row["title"],
            "note": row["note"],
            "url": row["url"],
            "auto": True,
        }
        added.append(row["repo"])
    if added:
        CURATED.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return added



def load_publisher():
    path = ROOT / "scripts" / "publish.py"
    spec = importlib.util.spec_from_file_location("hub_publish", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def publish_private(rows: list[dict]) -> tuple[list[str], list[str]]:
    """비공개 후보를 Cloudflare Pages + Access(소유자 이메일만 허용)로 발행한다.

    publish.py 가 클론·배포·Access 앱 생성·curated.json 등재까지 한 번에 한다.
    실패해도 나머지 후보는 계속 진행한다.
    """
    pending = [r for r in rows if r["private"]]
    if not pending:
        return [], []
    try:
        pub = load_publisher()
    except Exception as err:  # noqa: BLE001
        return [], [f"publish.py 로드 실패 — {err}"]
    done: list[str] = []
    failed: list[str] = []
    for row in pending:
        try:
            url = pub.publish(row["repo"], quiet=True)
            done.append(row["repo"])
            print(f"  비공개 발행 {row['repo']} → {url}")
        except Exception as err:  # noqa: BLE001
            failed.append(f"{row['repo']} — {err}")
            print(f"  ! 비공개 발행 실패 {row['repo']}: {err}", file=sys.stderr)
    return done, failed


def main() -> int:
    args = sys.argv[1:]
    tok = token()
    today = datetime.now(KST).date().isoformat()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    cur = scan(tok)
    prev_state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    prev_repos = prev_state.get("repos", {})
    baseline = not prev_repos

    fresh = [] if baseline else diff(prev_repos, cur, today)
    # 기준선이 없어도(첫 실행·캐시 만료) 이미 쌓인 이력은 지우지 않는다.
    prev_activity = json.loads(ACTIVITY.read_text(encoding="utf-8")) if ACTIVITY.exists() else {}
    changes = keep_history(prev_activity, fresh, today)

    listed = listed_repos()
    rows = unlisted(cur, listed, ignored(), tok)
    added = apply_public(rows) if "--apply" in args else []
    if added:
        rows = [r for r in rows if r["repo"] not in added]
    published, pub_failed = ([], [])
    if "--publish-private" in args:
        published, pub_failed = publish_private(rows)
        if published:
            rows = [r for r in rows if r["repo"] not in published]
    if added:
        fresh = [
            {
                "at": today,
                "repo": name,
                "kind": "listed",
                "label": "색인 등재",
                "text": "자동으로 색인에 실렸다",
                "url": f"https://{OWNER}.github.io/{name}/",
            }
            for name in added
        ] + fresh
        changes = keep_history(prev_activity, fresh, today)

    # 이 저장소는 공개다 — 비공개 저장소의 이름·커밋 메시지는 activity.json 에 넣지 않는다.
    # 전체 스냅숏(data/repos-state.json)은 .gitignore 로 빼고 워크플로 캐시에만 둔다.
    private_names = {n for n, i in cur.items() if i["private"]} | {
        n for n, i in prev_repos.items() if i.get("private")
    }
    public_changes = [c for c in changes if c["repo"] not in private_names]
    public_rows = [r for r in rows if not r["private"]]
    private_rows = [r for r in rows if r["private"]]

    activity = {
        "generated_at": now_iso,
        "window_days": WINDOW_DAYS,
        "baseline": baseline,
        "summary": {
            "repos": len(cur),
            "listed": len([n for n in cur if n in listed]),
            "changed": len([c for c in fresh if c["repo"] not in private_names]),
            "unlisted": len(public_rows),
        },
        "changes": public_changes,
        "unlisted": public_rows,
    }
    ACTIVITY.write_text(json.dumps(activity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATE.write_text(
        json.dumps({"generated_at": now_iso, "repos": cur}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # 색인에 실린 항목의 변경도 '새로 실림'으로 남긴다
    if added:
        print(f"자동 등재: {', '.join(added)}")

    if "--noti" in args:
        s = activity["summary"]
        text = (
            f"색인 추적: 저장소 {s['repos']} · 실린 페이지 {s['listed']} · "
            f"오늘 변경 {s['changed']} · 미등록 {s['unlisted']}"
        )
        if added:
            text += f" (자동 등재 {len(added)}: {', '.join(added)})"
        if published:
            text += f" (비공개 발행 {len(published)}: {', '.join(published)})"
        if pub_failed:
            text += "\n비공개 발행 실패: " + "; ".join(pub_failed)
        if private_rows:
            text += "\n비공개 미등록 후보: " + ", ".join(r["repo"] for r in private_rows)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "noti.py"), "add", text], check=False)

    print(
        f"저장소 {activity['summary']['repos']} · 색인 {activity['summary']['listed']} · "
        f"변경 {activity['summary']['changed']} · 미등록 {activity['summary']['unlisted']}"
        f"{' · 기준선 저장(첫 실행)' if baseline else ''}"
    )
    for row in rows:
        print(f"  - {row['repo']} {'(비공개)' if row['private'] else ''} {row['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
