#!/usr/bin/env python3
"""pages_index.py — 발행 중인 저장소의 HTML 을 훑어 '어느 페이지에 무엇이 있는지' 색인을 만든다.

라우팅(원노트 페이지 → 어느 html)의 근거 자료다. 모델에게 저장소 전체를 주지 않고
이 색인의 상위 후보만 준다.

  python3 scripts/pages_index.py            # data/pages-index.json 생성
  python3 scripts/pages_index.py --repo ktirads-study   # 한 저장소만
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "data" / "pages-index.json"
OWNER = "kimhs950627"

TAG = re.compile(r"<[^>]+>")
WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")


def gh_token() -> str:
    import os

    for key in ("PAGES_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()


def keywords(text: str, limit: int = 28) -> list[str]:
    counts: dict[str, int] = {}
    for w in WORD.findall(text):
        w = w.lower()
        counts[w] = counts.get(w, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]


def index_repo(repo: str, workdir: Path) -> list[dict]:
    dest = workdir / repo
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"https://x-access-token:{gh_token()}@github.com/{OWNER}/{repo}.git", str(dest)],
        check=True,
    )
    rows = []
    for path in sorted(dest.rglob("*.html")):
        rel = path.relative_to(dest).as_posix()
        if rel.startswith(("node_modules/", "assets/")) or "/assets/" in rel:
            continue
        raw = path.read_text(encoding="utf-8", errors="ignore")
        title_m = re.search(r"<title>(.*?)</title>", raw, re.S | re.I)
        heads = re.findall(r"<h[1-3][^>]*>(.*?)</h[1-3]>", raw, re.S | re.I)
        heads = [TAG.sub("", h).strip() for h in heads][:12]
        body = TAG.sub(" ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I))
        rows.append({
            "repo": repo,
            "file": rel,
            "title": (title_m.group(1).strip() if title_m else (heads[0] if heads else rel)),
            "headings": [h for h in heads if h][:8],
            "keywords": keywords(body),
            "marker": "onenote:start" in raw,
            "bytes": len(raw),
        })
    return rows


def main() -> int:
    argv = sys.argv[1:]
    curated = json.loads((ROOT / "data" / "curated.json").read_text(encoding="utf-8"))
    repos = [r for r, p in curated["pages"].items() if p.get("url") and not p.get("manual")]
    if "--repo" in argv:
        repos = [argv[argv.index("--repo") + 1]]

    tmp = Path(tempfile.mkdtemp(prefix="pages-index-"))
    rows: list[dict] = []
    try:
        for repo in repos:
            try:
                rows.extend(index_repo(repo, tmp))
                print(f"  ok {repo}")
            except Exception as err:  # noqa: BLE001
                print(f"  ! {repo}: {err}", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    INDEX.write_text(json.dumps({"generated": __import__("datetime").datetime.now().isoformat(timespec="seconds"), "pages": rows}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(rows)}개 html → {INDEX.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
