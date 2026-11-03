#!/usr/bin/env python3
"""onenote_inventory.py — Books_for_Meds 전체를 훑어 '각 페이지에 무엇이 있는지' JSON 으로 만든다.

본문은 data/onenote-raw/ 에 파일로 떨어뜨리고, 표준출력·JSON 에는 요약만 남긴다(토큰 절약).
한 번 띄운 MCP 프로세스로 여러 호출을 처리하고, 429(too many requests)를 만나면 쉬었다가 재시도한다.

  python3 scripts/onenote_inventory.py --notebook "Books_for_Meds"
  python3 scripts/onenote_inventory.py --section-id <id>      # 한 섹션만
  python3 scripts/onenote_inventory.py --refresh              # 캐시 무시하고 다시 받기
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = Path("/mnt/c/Users/pc/Downloads/ms_onenote_with_commandcode/onenote-mcp")
RAW = ROOT / "data" / "onenote-raw"
INV = ROOT / "data" / "onenote-inventory.json"
WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")


class Mcp:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            ["node.exe", "onenote-mcp.mjs"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=str(MCP_DIR),
        )
        self.next_id = 1
        self.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "inventory", "version": "1"},
            },
            _method="initialize",
        )
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def call(self, tool: str, args: dict, _method: str | None = None, retries: int = 6) -> str:
        for attempt in range(retries):
            self.next_id += 1
            rid = self.next_id
            body = (
                {"jsonrpc": "2.0", "id": rid, "method": _method, "params": args}
                if _method
                else {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": tool, "arguments": args}}
            )
            self._send(body)
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("MCP 프로세스가 종료됨")
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") != rid:
                    continue
                break
            if "error" in msg:
                raise RuntimeError(msg["error"])
            result = msg.get("result", {})
            text = ""
            for part in result.get("content", []):
                if part.get("type") == "text":
                    text += part.get("text", "")
            if result.get("isError"):
                if "too many requests" in text.lower():
                    wait = 60 * (attempt + 1)
                    print(f"    … 제한, {wait}s 대기", flush=True)
                    time.sleep(wait)
                    continue
                raise RuntimeError(text[:200])
            return text
        raise RuntimeError("재시도 한도 초과")


def keywords(text: str, limit: int = 20) -> list[str]:
    counts: dict[str, int] = {}
    for w in WORD.findall(text):
        w = w.lower()
        counts[w] = counts.get(w, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]


def main() -> int:
    argv = sys.argv[1:]
    refresh = "--refresh" in argv

    def arg(name: str, default: str = "") -> str:
        return argv[argv.index(name) + 1] if name in argv else default

    RAW.mkdir(parents=True, exist_ok=True)
    inventory = json.loads(INV.read_text(encoding="utf-8")) if INV.exists() else {"notebook": "Books_for_Meds", "pages": {}}

    mcp = Mcp()
    if arg("--section-id"):
        sections = [{"id": arg("--section-id"), "displayName": arg("--section-name", "section")}]
    else:
        notebook = arg("--notebook", "Books_for_Meds")
        notebooks = mcp.call("listNotebooks", {})
        nb_id = None
        match = re.search(r"\*\*" + re.escape(notebook) + r"\*\*\s*\n\s*ID:\s*(\S+)", notebooks)
        if match:
            nb_id = match.group(1)
        raw = mcp.call("listSections", {"notebookId": nb_id} if nb_id else {})
        sections = [
            {"id": m.group(2), "displayName": m.group(1)}
            for m in re.finditer(r"\*\*(.+?)\*\*\s*\n\s*ID:\s*(\S+)", raw)
        ]

    print(f"{len(sections)} sections", flush=True)
    total = 0
    for section in sections:
        sid, sname = section["id"], section["displayName"]
        try:
            listing = mcp.call("listPages", {"sectionId": sid, "limit": 200})
        except Exception as err:  # noqa: BLE001
            print(f"  ! {sname}: {err}", flush=True)
            continue
        pages = [
            {"id": m.group(2), "title": m.group(1)}
            for m in re.finditer(r"\*\*(.+?)\*\*\s*\n\s*ID:\s*(\S+)", listing)
        ]
        print(f"  {sname}: {len(pages)} pages", flush=True)
        for page in pages:
            pid, title = page["id"], page["title"]
            entry = inventory["pages"].get(pid, {})
            if not refresh and entry.get("title") == title and (RAW / f"{pid}.md").exists():
                continue
            try:
                body = mcp.call("getPageContent", {"pageId": pid, "format": "text"})
            except Exception as err:  # noqa: BLE001
                print(f"    ! {title[:30]}: {err}", flush=True)
                continue
            (RAW / f"{pid}.md").write_text(body, encoding="utf-8")
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            inventory["pages"][pid] = {
                "title": title,
                "section": sname,
                "modified": entry.get("modified") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "chars": len(body),
                "headings": [ln for ln in lines if len(ln) < 70][:8],
                "keywords": keywords(body),
                "sample": body[:160].replace("\n", " "),
            }
            total += 1
            if total % 10 == 0:
                INV.write_text(json.dumps(inventory, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                print(f"    ... {total} fetched", flush=True)
            time.sleep(1.2)

    INV.write_text(json.dumps(inventory, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"완료: {len(inventory['pages'])} pages, 이번에 {total}개 갱신", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
