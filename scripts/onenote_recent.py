#!/usr/bin/env python3
"""onenote_recent.py — OneNote 에서 '언제 무엇이 바뀌었는지'만 뽑는다.

MCP 서버(onenote-mcp.mjs)의 recentPages 도구를 JSON-RPC 로 직접 호출하므로
에이전트 세션·도구 왕복 없이 셸에서 결정적으로 돌릴 수 있다.

  python3 scripts/onenote_recent.py                 # state 파일의 lastSync 이후
  python3 scripts/onenote_recent.py --since 2026-10-01T00:00:00Z
  python3 scripts/onenote_recent.py --days 3        # 최근 3일

표준출력: {"cutoff": ..., "count": N, "pages": [{id,title,section,modified,webUrl}]}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "onenote-state.json"
MCP_DIR = Path("/mnt/c/Users/pc/Downloads/ms_onenote_with_commandcode/onenote-mcp")
KST = timezone(timedelta(hours=9))


def cutoff_from_state() -> str:
    if STATE.exists():
        try:
            last = json.loads(STATE.read_text(encoding="utf-8")).get("lastSync")
            if last:
                return last
        except json.JSONDecodeError:
            pass
    return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z")


def call_mcp_raw(tool: str, arguments: dict, timeout: int = 180) -> str:
    """도구 결과의 원문 텍스트를 그대로 돌려준다 (페이지 본문 등)."""
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "hub", "version": "1"}},
    }
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    payload = "\n".join(json.dumps(m) for m in [init, {"jsonrpc": "2.0", "method": "notifications/initialized"}, call]) + "\n"
    proc = subprocess.run(
        ["node.exe", "onenote-mcp.mjs"], input=payload, capture_output=True, text=True, cwd=str(MCP_DIR), timeout=timeout
    )
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            if "error" in msg:
                raise RuntimeError(msg["error"])
            return msg["result"]["content"][0]["text"]
    raise RuntimeError(f"MCP 응답 없음: {proc.stdout[-300:]} {proc.stderr[-300:]}")


def call_mcp(tool: str, arguments: dict, timeout: int = 180) -> dict:
    """MCP stdio 서버에 initialize + tools/call 을 보내고 결과 JSON 을 받는다."""
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "hub", "version": "1"}},
    }
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    payload = "\n".join(json.dumps(m) for m in [init, {"jsonrpc": "2.0", "method": "notifications/initialized"}, call]) + "\n"

    proc = subprocess.run(
        ["node.exe", "onenote-mcp.mjs"],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(MCP_DIR),
        timeout=timeout,
    )
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            if "error" in msg:
                raise RuntimeError(msg["error"])
            text = msg["result"]["content"][0]["text"]
            return json.loads(text)
    raise RuntimeError(f"MCP 응답 없음: {proc.stdout[-300:]} {proc.stderr[-300:]}")


def from_manifest(since: str) -> dict:
    """로컬 COM 이 만들어 둔 manifest 를 먼저 쓴다(레이트리밋 없음)."""
    m = ROOT / "data" / "onenote-manifest.json"
    rows = json.loads(m.read_text(encoding="utf-8-sig"))
    pages = [
        {"id": r["id"], "title": r["page"], "section": r["section"], "modified": r["modified"],
         "webUrl": None, "source": "local"}
        for r in rows
        if r.get("modified") and r["modified"] > since
    ]
    pages.sort(key=lambda p: p["modified"], reverse=True)
    return {"cutoff": since, "count": len(pages), "pages": pages, "source": "local"}


def main() -> int:
    args = sys.argv[1:]
    since = None
    if "--since" in args:
        since = args[args.index("--since") + 1]
    elif "--days" in args:
        days = int(args[args.index("--days") + 1])
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    else:
        since = cutoff_from_state()

    manifest = ROOT / "data" / "onenote-manifest.json"
    if "--mcp" not in args and manifest.exists():
        result = from_manifest(since)
    else:
        try:
            result = call_mcp("recentPages", {"since": since, "limit": 40})
            result["source"] = "mcp"
        except Exception as err:  # noqa: BLE001
            print(json.dumps({"error": str(err)}, ensure_ascii=False), file=sys.stderr)
            return 1

    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
