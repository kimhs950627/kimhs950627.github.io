#!/usr/bin/env python3
"""onenote_submit.py — 로컬에서 만든 patch 를 비공개 staging(pages-jobs)에 올린다.

적용·배포는 pages-jobs 의 Actions 가 한다. 여기서는 큐에 넣기만 한다(로컬 세션을 짧게 유지).

  python3 scripts/onenote_submit.py /tmp/opencode/onenote-patch.json --page "GI 학회 정리"
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGING = "git@github.com:kimhs950627/pages-jobs.git"


def token() -> str:
    import os

    for k in ("PAGES_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    patch = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not patch.get("updates"):
        print("업데이트가 비어 있다")
        return 1
    a = sys.argv[1:]
    page = a[a.index("--page") + 1] if "--page" in a else patch.get("source", {}).get("page", "")
    patch.setdefault("source", {})["page"] = page
    patch["source"]["submitted"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    slug = "-".join(sorted({u["repo"] for u in patch["updates"]}))[:40]
    name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{slug}.json"

    tmp = Path(tempfile.mkdtemp(prefix="jobs-"))
    try:
        subprocess.run(["git", "clone", "-q", f"https://x-access-token:{token()}@github.com/kimhs950627/pages-jobs.git", str(tmp / "q")], check=True)
        target = tmp / "q" / "jobs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(patch, ensure_ascii=False, indent=1), encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp / "q"), "add", "jobs"], check=True)
        subprocess.run(["git", "-C", str(tmp / "q"), "-c", "user.name=kimhs950627", "-c", "user.email=kimhs950627@gmail.com",
                        "commit", "-q", "-m", f"job: {page or name}"], check=True)
        subprocess.run(["git", "-C", str(tmp / "q"), "push", "-q"], check=True)
        print(f"제출됨: jobs/{name}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
