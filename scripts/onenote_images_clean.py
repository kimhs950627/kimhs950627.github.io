#!/usr/bin/env python3
"""onenote_images_clean.py — 잘못 들어간 그림(figure.fig)을 걷어낸다.

이전 배치에서 캡션이 기계 문자열(URL·'동영상으로'·'그림 이미지')이거나 주제가 틀린 그림을 제거한다.
`--all` 은 해당 파일의 그림을 전부 제거(예: 혈당 페이지에 들어간 내시경/갑상선 그림).

  python3 scripts/onenote_images_clean.py --file ward-nights/glucose.html --all
  python3 scripts/onenote_images_clean.py --file ultrasound-protocol-study/reporting.html
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from onenote_html import page_blocks  # noqa: E402

REPOS = Path.home() / ".local" / "state" / "onenote" / "repos"
FIG = re.compile(r"\n?\s*<figure class=\"fig\">.*?</figure>\s*\n?", re.S)
CAP = re.compile(r"<figcaption>(.*?)</figcaption>", re.S)


def junk(caption: str) -> bool:
    c = re.sub(r"\s+", " ", caption).strip()
    if not c:
        return True
    if any(k in c for k in ("http", "graph.microsoft", "동영상으로", "원문에", "그림 주제", "그림 이미지", "화면을종료")):
        return True
    if re.match(r"^(참고자료|간담췌|Endo|위내시경|DDX)\S* 그림 \d+$", c):
        return True
    digits = len(re.findall(r"[0-9]", c))
    if digits >= max(4, len(c) // 3):
        return True
    if re.fullmatch(r"[A-Za-z0-9 :.,()/%+\-]+", c) and digits:
        return True
    return False


def main() -> int:
    args = sys.argv[1:]
    getk = lambda k, d="": args[args.index(k) + 1] if k in args else d
    repo, rel = getk("--file").split("/", 1)
    drop_all = "--all" in args
    path = REPOS / repo / rel
    html = path.read_text(encoding="utf-8")
    figs = list(FIG.finditer(html))
    patterns = []
    for m in figs:
        capm = CAP.search(m.group(0))
        if drop_all or junk(capm.group(1) if capm else ""):
            patterns.append(re.escape(m.group(0).strip()))
    if not patterns:
        print(json.dumps({"file": f"{repo}/{rel}", "removed": 0}, ensure_ascii=False))
        return 0
    # 긴 패턴부터 지운다
    patterns.sort(key=len, reverse=True)
    patch = {"updates": [{
        "repo": repo, "file": rel, "sections": {"__noop__": ""},
        "remove_regex": patterns,
        "source": {"page": "그림 정리", "model": "none"},
        "commit_message": f"fix(onenote): {rel} — 잘못 들어간 그림 {len(patterns)}개 제거",
    }], "generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    removed = len(patterns)
    out = Path(getk("--out", "/tmp/opencode/images-clean.json"))
    out.write_text(json.dumps(patch, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"file": f"{repo}/{rel}", "removed": removed, "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
