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


def insert_into_section(text: str, update: dict) -> tuple[str, str]:
    """기존 장의 특정 단원(앵커) 안에 써넣는다. 앵커는 id 또는 제목 텍스트로 지정한다."""
    anchor_id = update.get("anchor_id") or ""
    anchor_heading = update.get("anchor_heading") or ""
    tag = anchor_id or anchor_heading
    if not tag:
        return text, "앵커 없음"

    start = f"<!-- onenote-insert:{tag}:start -->"
    end = f"<!-- onenote-insert:{tag}:end -->"
    block = f'{start}\n{update["html"]}\n{end}'

    if start in text and end in text:
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
        return pattern.sub(lambda _m: block, text, count=1), "갱신"

    if anchor_id:
        m = re.search(rf'<h2[^>]*id="{re.escape(anchor_id)}"[^>]*>.*?</h2>', text, re.S)
    else:
        m = re.search(rf'<h2[^>]*>(?:(?!</h2>).)*{re.escape(anchor_heading)}.*?</h2>', text, re.S)
    if not m:
        return text, f"단원 못 찾음: {tag}"

    rest = text[m.end():]
    nxt = re.search(r"<h2[\s>]", rest)
    cut = m.end() + (nxt.start() if nxt else len(rest))
    wrapper = f'\n<div class="onenote-insert" data-anchor="{tag}">\n{block}\n</div>\n'
    return text[:cut].rstrip() + wrapper + text[cut:], "삽입"


def apply_one(update: dict, check_only: bool) -> str:
    repo = update["repo"]
    rel = update["file"]
    region = update.get("region", "onenote")
    html = update.get("html", "")
    anchored = bool(update.get("anchor_id") or update.get("anchor_heading"))

    tmp = Path(tempfile.mkdtemp(prefix="onenote-"))
    try:
        clone(repo, tmp / "repo")
        target = tmp / "repo" / rel
        if not target.exists():
            if not (update.get("create") and html):
                return f"{repo}/{rel}: 파일 없음"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(html, encoding="utf-8")
            added = [rel]
            link = update.get("index_link") or {}
            if link.get("file") and link.get("html"):
                ip = tmp / "repo" / link["file"]
                if ip.exists():
                    it = ip.read_text(encoding="utf-8")
                    marker = link.get("before") or "</main>"
                    if marker in it:
                        ip.write_text(it.replace(marker, link["html"].rstrip() + "\n" + marker, 1), encoding="utf-8")
                        added.append(link["file"])
            if check_only:
                return f"{repo}/{rel}: 새 페이지 생성 예정"
            msg = update.get("commit_message") or "chore: 원노트 새 페이지"
            subprocess.run(["git", "-C", str(tmp / "repo"), "add", *added], check=True)
            subprocess.run(
                ["git", "-C", str(tmp / "repo"), "-c", "user.name=kimhs950627", "-c", "user.email=kimhs950627@gmail.com",
                 "commit", "-q", "-m", msg],
                check=True,
            )
            push = subprocess.run(["git", "-C", str(tmp / "repo"), "push", "-q"], capture_output=True, text=True)
            if push.returncode != 0:
                return f"{repo}/{rel}: push 실패 — {push.stderr.strip()[:200]}"
            return f"{repo}/{rel}: 새 페이지 생성·push 완료"
        text = target.read_text(encoding="utf-8")

        if update.get("sections"):
            # elaborate 모드: 단원 블록(section 또는 h2 구간)을 통째로 교체한다(덧붙이기 아님).
            from onenote_html import page_blocks

            want = update["sections"]
            drop = set(update.get("remove_sections") or [])
            blocks, mode = page_blocks(text)
            new = text
            replaced: list[str] = []
            for key, block in blocks:
                if key in want:
                    new = new.replace(block, want[key].strip(), 1)
                    replaced.append(key)
                elif key in drop:
                    new = new.replace(block, "", 1)
            if update.get("strip_onenote_inserts"):
                new = re.sub(r'\n?[ \t]*<div class="onenote-insert"[^>]*>.*?</div>[ \t]*\n?', "\n", new, flags=re.S)
                new = re.sub(r"<!--\s*onenote-insert:[^>]*-->\n?", "", new)
            for pat in update.get("remove_regex") or []:
                new = re.sub(pat, "\n", new, flags=re.S)
            new = re.sub(r"\n{3,}", "\n\n", new)
            if not replaced and not update.get("remove_regex"):
                return f"{repo}/{rel}: 교체할 단원 없음 — 건드리지 않음"
            html = "elaborate"
        elif update.get("remove"):
            pat = region_pattern(region)
            if not pat.search(text):
                return f"{repo}/{rel}: 삭제할 '{region}' 구간 없음"
            sec = re.search(rf'<section[^>]*id="{re.escape(region)}"[^>]*>.*?</section>\s*\n?', text, re.S)
            new = text.replace(sec.group(0), "", 1) if sec else pat.sub("", text)
        elif anchored:
            new, how = insert_into_section(text, update)
            if new == text:
                return f"{repo}/{rel}: {how} — 건드리지 않음"
        else:
            pat = region_pattern(region)
            if not pat.search(text):
                if not update.get("create_region", True):
                    return f"{repo}/{rel}: '{region}' 마커 없음 — 건드리지 않음"
                section = (
                    f'<section id="{region}" class="note-sync">\n'
                    f'  <h2>{update.get("heading") or "원노트"}</h2>\n'
                    f'  <!-- {region}:start -->\n{html}\n  <!-- {region}:end -->\n'
                    f'</section>\n\n'
                )
                anchor = "</main>" if "</main>" in text else ("</body>" if "</body>" in text else None)
                if not anchor:
                    return f"{repo}/{rel}: 삽입 위치(</main> 또는 </body>)가 없음 — 건드리지 않음"
                new = text.replace(anchor, section + anchor, 1)
            else:
                new = pat.sub(lambda m: f"{m.group(1)}\n{html}\n{m.group(3)}", text)

        if (not html and not update.get("sections") and not update.get("create")
                and not update.get("remove") and not update.get("remove_regex")):
            # 빈 html 로 region 을 지워버리는 사고 방지(2026-11-07: 구버전 스크립트가 부록을 비웠다)
            return f"{repo}/{rel}: 빈 html — 건드리지 않음"
        if new == text:
            return f"{repo}/{rel}: 변경 없음"
        if check_only:
            return f"{repo}/{rel}: 변경 예정 ({len(new) - len(text):+d}자)"

        target.write_text(new, encoding="utf-8")
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
