#!/usr/bin/env python3
"""pages_catalog.py — 후보 페이지의 단원 목록(id·제목·본문)을 만든다.

  python3 scripts/pages_catalog.py --repo endoscopy-study --files egd/01-식도-양성.html egd/03-위-양성.html \
      --out /tmp/opencode/catalog.json

본문 텍스트는 중복 판정에 쓴다(로컬에만 두고 발행물에는 영향 없음).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

OWNER = "kimhs950627"


def token() -> str:
    import os

    for k in ("PAGES_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()


def plain(html: str) -> str:
    txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()


def main() -> int:
    a = sys.argv[1:]
    get = lambda k, d="": a[a.index(k) + 1] if k in a else d
    repo = get("--repo")
    files: list[str] = []
    if "--files" in a:
        for tok in a[a.index("--files") + 1:]:
            if tok.startswith("--"):
                break
            files.append(tok)
    if not files:
        files = [x for x in a if x.endswith(".html")]
    out = Path(get("--out", "/tmp/opencode/catalog.json"))

    tmp = Path(tempfile.mkdtemp(prefix="catalog-"))
    subprocess.run(["git", "clone", "-q", "--depth", "1",
                    f"https://x-access-token:{token()}@github.com/{OWNER}/{repo}.git", str(tmp / "r")], check=True)

    pages = []
    for rel in files:
        f = tmp / "r" / rel
        if not f.exists():
            continue
        html = f.read_text(encoding="utf-8")
        sections = []
        heads = list(re.finditer(r'<h2[^>]*id="([^"]+)"[^>]*>(.*?)</h2>', html, re.S))
        for i, m in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(html)
            sections.append({"id": m.group(1), "title": plain(m.group(2))[:60],
                             "text": plain(html[m.end():end])[:4000]})
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        pages.append({"repo": repo, "file": rel, "title": plain(title.group(1)) if title else rel,
                      "sections": sections, "text": plain(html)[:20000]})

    out.write_text(json.dumps({"pages": pages}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"pages": len(pages), "sections": sum(len(p["sections"]) for p in pages), "out": str(out)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
