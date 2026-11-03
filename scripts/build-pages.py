#!/usr/bin/env python3
"""깃허브 페이지 목록을 모아 data/pages.json 을 만든다.

- data/curated.json 에 손으로 적은 제목·설명·분류가 있으면 그것을 쓴다.
- 없으면 그 페이지 자신의 <title>·meta description 을 쓰고 "그 밖"에 넣는다.
- 저장소의 페이지가 404(GitHub Pages 404 화면)면 목록에서 뺀다.

실행: GITHUB_TOKEN=$(gh auth token) python3 scripts/build-pages.py
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OWNER = os.environ.get("PAGES_OWNER", "kimhs950627")
ROOT = Path(__file__).resolve().parent.parent
CURATED = ROOT / "data" / "curated.json"
OUT = ROOT / "data" / "pages.json"
UA = f"{OWNER}-github-pages-hub"
TIMEOUT = 20


def get(url: str, token: str | None = None) -> tuple[int, str]:
    safe_url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")
    req = urllib.request.Request(safe_url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return res.status, res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as err:
        return err.code, ""
    except Exception as err:  # noqa: BLE001 — 네트워크 실패는 '페이지 없음'으로 처리
        print(f"  ! {url} — {err}", file=sys.stderr)
        return 0, ""


def list_repos(token: str | None) -> list[dict]:
    repos: list[dict] = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{OWNER}/repos?per_page=100&page={page}&type=owner&sort=pushed"
        status, body = get(url, token)
        if status != 200:
            print(f"저장소 목록을 못 받음 (HTTP {status})", file=sys.stderr)
            break
        chunk = json.loads(body)
        repos.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return repos


TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
DESC_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]*content=["\'](.*?)["\']',
    re.I | re.S,
)
DESC_RE_ALT = re.compile(
    r'<meta[^>]+content=["\'](.*?)["\'][^>]*(?:name|property)=["\'](?:description|og:description)["\']',
    re.I | re.S,
)


def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


_TREE_CACHE: dict[str, list[str]] = {}


def repo_tree(repo: str, token: str | None) -> list[str]:
    """저장소의 파일 경로 목록. 실패하면 빈 목록."""
    if repo in _TREE_CACHE:
        return _TREE_CACHE[repo]
    status, body = get(f"https://api.github.com/repos/{OWNER}/{repo}", token)
    if status != 200:
        _TREE_CACHE[repo] = []
        return []
    branch = json.loads(body).get("default_branch", "main")
    status, body = get(f"https://api.github.com/repos/{OWNER}/{repo}/git/trees/{branch}?recursive=1", token)
    paths = [t["path"] for t in json.loads(body).get("tree", [])] if status == 200 else []
    _TREE_CACHE[repo] = paths
    return paths


def resolve_workbook(url: str, token: str | None) -> tuple[str, str]:
    """워크북 링크를 '검토본 우선, 없으면 초안'으로 바꾼다.

    <repo>/workbooks/<slug>/ 처럼 디렉터리 URL 이면 그 아래 reviewed/*.html 을 먼저 찾고,
    없으면 draft/*.html, 둘 다 없으면 원래 URL 을 그대로 쓴다.
    """
    m = re.match(rf"^https://{re.escape(OWNER)}\.github\.io/([^/]+)/(.*)$", url)
    if not m:
        return url, ""
    repo, sub = m.group(1), m.group(2).strip("/")
    prefix = f"{sub}/" if sub else ""
    paths = repo_tree(repo, token)
    if not paths:
        return url, ""
    for status_ in ("reviewed", "draft"):
        hits = sorted(
            p for p in paths if p.startswith(f"{prefix}{status_}/") and p.endswith(".html")
        )
        if hits:
            return f"https://{OWNER}.github.io/{repo}/{hits[0]}", status_
    return url, ""


def parse_page(body: str) -> tuple[str, str]:
    head = body[:20000]
    title = clean(TITLE_RE.search(head).group(1)) if TITLE_RE.search(head) else ""
    desc = DESC_RE.search(head) or DESC_RE_ALT.search(head)
    return title, clean(desc.group(1)) if desc else ""


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    curated = json.loads(CURATED.read_text(encoding="utf-8"))
    known = curated.get("pages", {})
    order = curated.get("category_order", [])
    labels = {c["id"]: c["label"] for c in order}

    site_root = f"https://{OWNER}.github.io"
    found: list[dict] = []

    for repo in list_repos(token):
        name = repo["name"]
        if name.lower() == f"{OWNER.lower()}.github.io":
            continue
        url = f"{site_root}/{name}/"
        status, body = get(url, token)
        if status != 200:
            continue
        if "Page not found" in body[:4000] and "github.io" in body[:4000]:
            print(f"  - {name}: 페이지 없음(404 화면)", file=sys.stderr)
            continue
        title, desc = parse_page(body)
        meta = known.get(name, {})
        links = []
        for link in meta.get("links", []):
            target, status_ = link["url"], ""
            if link.get("kind") == "workbook":
                target, status_ = resolve_workbook(link["url"], token)
            code, _ = get(target, token)
            if code == 200:
                links.append({"label": link["label"], "url": target, "status": status_})
            else:
                print(f"  ! {name}: 쓰러진 바로가기 {target} (HTTP {code})", file=sys.stderr)
        found.append(
            {
                "repo": name,
                "url": url,
                "host": f"{OWNER}.github.io/{name}",
                "title": meta.get("title") or title or name,
                "note": meta.get("note") or desc or clean(repo.get("description") or ""),
                "category": meta.get("category") or "other",
                "order": meta.get("order", 9999),
                "links": links,
                "updated": (repo.get("pushed_at") or "")[:10],
            }
        )
        time.sleep(0.2)

    groups = []
    for cat in order:
        items = [p for p in found if p["category"] == cat["id"]]
        if not items:
            continue
        items.sort(key=lambda p: (p["order"], p["title"]))
        groups.append({"id": cat["id"], "label": cat["label"], "pages": items})

    # curated 에 없는 분류가 새로 생기면 마지막에 붙인다
    leftover = [p for p in found if p["category"] not in labels]
    if leftover:
        leftover.sort(key=lambda p: p["title"])
        groups.append({"id": "uncategorized", "label": "미분류", "pages": leftover})

    payload = {
        "owner": OWNER,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total": len(found),
        "link_total": sum(len(p["links"]) for p in found),
        "groups": groups,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(found)}개 페이지 · 바로가기 {payload['link_total']}개 → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
