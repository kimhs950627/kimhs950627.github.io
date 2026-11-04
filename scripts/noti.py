#!/usr/bin/env python3
"""noti.py — 새벽 작업 결과를 모아 두었다가 아침에 한 번에 보낸다.

  python3 scripts/noti.py add "발행 동기화: 검사 11 · 변경 0"
  python3 scripts/noti.py add-plan     # 신규 페이지 제안 요약(제목·상태·문서 링크)
  python3 scripts/noti.py flush        # 큐를 비우고 웹훅으로 전송(길면 여러 메시지)

큐 파일: data/noti-queue.jsonl (한 줄에 {"at","kind","text"})
웹훅: DISCORD_WEBHOOK_URL 환경변수 → 없으면 ~/.config/kimaki/discord-webhook.env
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "data" / "noti-queue.jsonl"
KST = timezone(timedelta(hours=9))


def webhook_url() -> str | None:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        return url
    for path, key in (
        (Path.home() / ".config" / "kimaki" / "discord-webhook.env", "DISCORD_WEBHOOK_URL"),
        (Path.home() / ".config" / "kimaki-hub" / "secrets.env", "DISCORD_WEBHOOK"),
    ):
        if path.exists():
            for line in path.read_text().splitlines():
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
    return None


def add(text: str, kind: str = "sync") -> int:
    QUEUE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": datetime.now(KST).isoformat(timespec="minutes"), "kind": kind, "text": text}, ensure_ascii=False) + "\n")
    print(f"queued: {text}")
    return 0


PLAN = ROOT / "docs" / "onenote-page-plan.md"


def plan_summary() -> str:
    """신규 페이지 제안을 '결정할 수 있는 형태'로 요약한다(제목·우선순위·상태·링크)."""
    if not PLAN.exists():
        return "신규 페이지 제안 문서가 없다"
    text = PLAN.read_text(encoding="utf-8")
    published = set()
    curated = ROOT / "data" / "curated.json"
    if curated.exists():
        try:
            published = set(json.loads(curated.read_text(encoding="utf-8")).get("pages", {}))
        except json.JSONDecodeError:
            pass

    items = []
    for line in text.splitlines():
        if not line.startswith("### "):
            continue
        head = line[4:].strip()
        if head.startswith("기존 페이지 확장"):
            continue
        mark = "✅발행됨" if any(k in head for k in published) else "⬜대기"
        items.append(f"• {head} — {mark}")
    if not items:
        return "제안 항목을 찾지 못했다"

    body = ["**🗂 신규 페이지 제안 — 승인할 번호를 회신**", ""]
    body += items
    body += ["", f"전문: {PLAN}"]
    rendered = rendered_link(PLAN)
    if rendered:
        body.append(f"문서: {rendered}")
    body.append("회신 예: `2,3 승인` / `보류` (번호 없이 승인 시 우선순위대로 진행)")
    return "\n".join(body)


def rendered_link(path: Path) -> str | None:
    """md-view 로 렌더링 링크를 만든다(실패하면 None)."""
    import subprocess

    exe = Path.home() / ".local" / "bin" / "md-view"
    if not exe.exists():
        return None
    try:
        out = subprocess.run([str(exe), str(path)], capture_output=True, text=True, timeout=120)
        url = (out.stdout or "").strip().splitlines()
        return url[-1].strip() if url and url[-1].startswith("http") else None
    except Exception:  # noqa: BLE001
        return None


def pack(lines: list[str], limit: int) -> list[str]:
    """문단 단위로 잘라 여러 메시지로 만든다(내용 손실 없이)."""
    chunks, cur = [], []
    size = 0
    for line in lines:
        extra = len(line) + 1
        if size + extra > limit and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += extra
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def post(url: str, body: str) -> bool:
    req = urllib.request.Request(
        url,
        data=json.dumps({"content": body[:1990]}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "pages-hub/1.0 (+https://kimhs950627.github.io/)"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status in (200, 204)
    except Exception as err:  # noqa: BLE001
        print(f"전송 실패: {err}", file=sys.stderr)
        return False


def flush() -> int:
    if not QUEUE.exists() or not QUEUE.read_text(encoding="utf-8").strip():
        print("큐가 비어 있다 — 보내지 않는다")
        return 0
    items = [json.loads(line) for line in QUEUE.read_text(encoding="utf-8").splitlines() if line.strip()]
    url = webhook_url()
    if not url:
        print("웹훅 URL 없음 — 큐를 그대로 둔다", file=sys.stderr)
        return 2

    day = datetime.now(KST).strftime("%m-%d")
    lines = [f"**🌙 새벽 작업 결과 ({day})**", ""]
    for it in items:
        lines.append(f"• {it['text']}")

    for body in pack(lines, 1900):
        if not post(url, body):
            print("전송 실패", file=sys.stderr)
            return 1
    QUEUE.write_text("", encoding="utf-8")
    print(f"sent {len(items)}건")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    if argv[0] == "add" and len(argv) > 1:
        return add(" ".join(argv[1:]), os.environ.get("NOTI_KIND", "sync"))
    if argv[0] == "add-onenote" and len(argv) > 1:
        return add(" ".join(argv[1:]), "onenote")
    if argv[0] == "add-plan":
        return add(plan_summary(), "plan")
    if argv[0] == "flush":
        return flush()
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
