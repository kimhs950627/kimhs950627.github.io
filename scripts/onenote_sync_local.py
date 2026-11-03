#!/usr/bin/env python3
"""onenote_sync_local.py — COM(로컬 원노트 앱)으로 계층·수정시각을 받아 Graph 레이트리밋 없이 상태를 갱신한다.

  python3 scripts/onenote_sync_local.py            # manifest 갱신 + 바뀐 목록 출력
  python3 scripts/onenote_sync_local.py --commit   # state 파일까지 갱신

state: data/onenote-state.json  {"lastSync": ISO, "pages": {id: {title, section, modified}}}
"""
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "onenote-state.json"
MANIFEST = ROOT / "data" / "onenote-manifest.json"
PS1 = ROOT / "scripts" / "onenote-export.ps1"


def wslpath(p: Path) -> str:
    return subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True).stdout.strip()


def main() -> int:
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", wslpath(PS1),
         "-Mode", "manifest", "-Out", wslpath(MANIFEST)],
        capture_output=True, text=True, timeout=600,
    )
    if not MANIFEST.exists():
        print(out.stdout[-300:], out.stderr[-300:], file=sys.stderr)
        return 1
    rows = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"lastSync": None, "pages": {}}
    known = state.get("pages", {})
    changed = [r for r in rows if known.get(r["id"], {}).get("modified") != r["modified"]]
    changed.sort(key=lambda r: r["modified"], reverse=True)
    print(f"pages={len(rows)} changed={len(changed)}")
    for r in changed[:25]:
        print(f"  {r['modified'][:16]} | {r['section'][:24]} | {r['page'][:40]}")
    if "--commit" in sys.argv:
        state["pages"] = {r["id"]: {"title": r["page"], "section": r["section"], "modified": r["modified"]} for r in rows}
        state["lastSync"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("state updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
