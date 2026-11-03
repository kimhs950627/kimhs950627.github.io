#!/usr/bin/env python3
"""onenote_apply.py — 원노트에서 뽑은 내용을 발행 중인 HTML 에 반영하고 다시 발행한다.

원노트 읽기(OneNote MCP)는 에이전트 세션에서만 가능하므로, 이 스크립트는 **이미 뽑아 둔 HTML**을 받아
저장소 파일에 넣고 커밋·push 한 뒤, 필요하면 Cloudflare Pages 로 재배포한다.

  python3 scripts/onenote_apply.py patch.json            # 반영 + push + (비공개면) 재배포
  python3 scripts/onenote_apply.py --check patch.json    # 무엇이 바뀔지만 확인

patch.json
{
  "updates": [
    {
      "repo": "ktirads-study",
      "file": "04-4-등급별-악성률과-생검-기준-한-표.html",
      "region": "onenote",          # <!-- onenote:start onenote --> … <!-- onenote:end onenote -->
      "html": "<p>…</p>"
    }
  ],
  "commit_message": "chore: 원노트 반영"
}

규칙
- 대상 파일에 마커가 없으면 **그 파일은 건드리지 않고 실패로 보고**한다(사람이 마커를 넣어야 한다).
- 변경이 없으면 커밋하지 않는다.
- public 저장소는 push 만으로 GitHub Pages 가 갱신되고, private 저장소는 publish.py 로 Cloudflare 에 재배포한다.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER = "kimhs950627"
sys.path.insert(0, str(ROOT / "scripts"))


def token() -> str:
    import os

    for key in ("PAGES_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()


def clone(repo: str, dest: Path) -> None:
    subprocess.run(
        ["git", "clone", "-q", f"https://x-access-token:{token()}@github.com/{OWNER}/{repo}.git", str(dest)],
        check=True,
    )


def region_pattern(region: str) -> re.Pattern:
    return re.compile(
        rf"(<!--\s*{re.escape(region)}:start[^>]*-->)(.*?)(<!--\s*{re.escape(region)}:end\s*-->)",
        re.S,
    )


def apply_one(update: dict, check_only: bool) -> str:
    repo = update["repo"]
    rel = update["file"]
    region = update.get("region", "onenote")
    html = update["html"]

    tmp = Path(tempfile.mkdtemp(prefix="onenote-"))
    try:
        clone(repo, tmp / "repo")
        target = tmp / "repo" / rel
        if not target.exists():
            return f"{repo}/{rel}: 파일 없음"
        text = target.read_text(encoding="utf-8")
        pat = region_pattern(region)
        if not pat.search(text):
            if not update.get("create_region", True):
                return f"{repo}/{rel}: '{region}' 마커 없음 — 건드리지 않음"
            block = (
                f'<section id="{region}" class="note-sync">\n'
                f'  <h2>{update.get("heading") or "원노트"}</h2>\n'
                f'  <!-- {region}:start -->\n{html}\n  <!-- {region}:end -->\n'
                f'</section>\n\n'
            )
            anchor = "</main>" if "</main>" in text else ("</body>" if "</body>" in text else None)
            if not anchor:
                return f"{repo}/{rel}: 삽입 위치(</main> 또는 </body>)가 없음 — 건드리지 않음"
            new = text.replace(anchor, block + anchor, 1)
        else:
            new = pat.sub(lambda m: f"{m.group(1)}\n{html}\n{m.group(3)}", text)
        if new == text:
            return f"{repo}/{rel}: 변경 없음"
        if check_only:
            return f"{repo}/{rel}: 변경 예정 ({len(html)}자)"

        target.write_text(new, encoding="utf-8")
        # 원노트에서 뽑은 그림을 같은 커밋에 넣는다
        added = [rel]
        for asset in update.get("assets", []):
            apath = tmp / "repo" / asset["path"]
            apath.parent.mkdir(parents=True, exist_ok=True)
            apath.write_bytes(base64.b64decode(asset["base64"]))
            added.append(asset["path"])
        msg = update.get("commit_message") or "chore: 원노트 변경 반영"
        subprocess.run(["git", "-C", str(tmp / "repo"), "add", *added], check=True)
        subprocess.run(
            ["git", "-C", str(tmp / "repo"), "-c", "user.name=kimhs950627", "-c", "user.email=kimhs950627@gmail.com",
             "commit", "-q", "-m", msg],
            check=True,
        )
        push = subprocess.run(["git", "-C", str(tmp / "repo"), "push", "-q"], capture_output=True, text=True)
        if push.returncode != 0:
            return f"{repo}/{rel}: push 실패 — {push.stderr.strip()[:200]}"
        return f"{repo}/{rel}: 반영·push 완료"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    argv = [a for a in sys.argv[1:] if a != "--check"]
    check_only = "--check" in sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    patch = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    updates = patch.get("updates", [])
    if not updates:
        print("반영할 항목이 없다")
        return 0

    failures = 0
    touched_private = set()
    for upd in updates:
        result = apply_one(upd, check_only)
        print(" ", result)
        if "실패" in result or "없음" in result or "마커" in result:
            failures += 1
        if not check_only and "완료" in result:
            try:
                from publish import repo_info  # 같은 폴더의 publish.py 재사용

                if repo_info(upd["repo"])["private"]:
                    touched_private.add(upd["repo"])
            except Exception:  # noqa: BLE001
                pass

    if touched_private and not check_only:
        import publish

        for repo in sorted(touched_private):
            try:
                publish.publish(repo, quiet=True)
                print(f"  {repo}: Cloudflare Pages 재배포 완료")
            except Exception as err:  # noqa: BLE001
                print(f"  {repo}: 재배포 실패 — {err}", file=sys.stderr)
                failures += 1

    print(f"결과: {len(updates)}건 중 실패 {failures}건")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
