#!/usr/bin/env python3
"""noti.py — 새벽 작업 결과를 모아 두었다가 아침에 한 번에 보낸다.

  python3 scripts/noti.py add "발행 동기화: 검사 11 · 변경 0"
  python3 scripts/noti.py flush        # 큐를 비우고 웹훅으로 한 번에 전송

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
    body = "\n".join(lines)[:1900]

    req = urllib.request.Request(
        url,
        data=json.dumps({"content": body}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "pages-hub/1.0 (+https://kimhs950627.github.io/)"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            ok = res.status in (200, 204)
    except Exception as err:  # noqa: BLE001
        print(f"전송 실패: {err}", file=sys.stderr)
        return 1
    if not ok:
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
    if argv[0] == "flush":
        return flush()
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
