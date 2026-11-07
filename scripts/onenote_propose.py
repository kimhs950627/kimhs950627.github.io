#!/usr/bin/env python3
"""onenote_propose.py — 전수 요약(digest)을 근거로 '어디로 보낼지' 매핑 제안을 만든다.

  python3 scripts/onenote_propose.py                # 제안 생성 + 요약 출력
  python3 scripts/onenote_propose.py --verbose      # 페이지별 상세

입력: ~/.local/state/onenote/{digest.json,text/}  · data/onenote-rules.json · data/pages-index.json
출력: ~/.local/state/onenote/proposal.json

원칙
- 판단은 결정적(키워드 점수)이다. 모델은 애매한 것만 나중에 부른다.
- 한 페이지에 여러 주제가 섞이면(예: GI 학회 정리 = 내시경 + 간수치) **문단(소제목) 단위로 쪼개**
  서로 다른 repo 로 보낸다. 통째로 한 곳에 밀어넣지 않는다.
- 확신이 없으면 action="hold" 로 남기고 파일을 건드리지 않는다.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "data" / "onenote-rules.json"
INDEX = ROOT / "data" / "pages-index.json"
STATE = Path.home() / ".local" / "state" / "onenote"
DIGEST = STATE / "digest.json"
TEXT = STATE / "text"
OUT = STATE / "proposal.json"

WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{2,}")


def tokens(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(text or "")}


def safe(page_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9!._-]", "_", page_id)


def group_scores(text: str, groups: dict) -> dict[str, int]:
    low = text.lower()
    scores = {}
    for name, g in groups.items():
        hits = 0
        for kw in g["keywords"]:
            if kw in low:
                hits += 1
        if hits:
            scores[name] = hits
    return scores


def split_chunks(text: str) -> list[dict]:
    """소제목 줄을 경계로 문단을 나눈다. 제목 줄 = 60자 이하, 문장부호로 끝나지 않는 줄."""
    lines = text.splitlines()
    chunks: list[dict] = []
    label = "(도입)"
    buf: list[str] = []
    for line in lines:
        s = line.strip()
        is_head = bool(s) and len(s) <= 60 and not s.endswith((".", "다", "요", "음", "함", ":", ")", "]")) \
            and not s.startswith(("📄", "📅")) and "]]>" not in s
        if is_head and len(" ".join(buf)) > 200:
            chunks.append({"label": label, "text": "\n".join(buf).strip()})
            label = s
            buf = [s]
        else:
            if is_head and not buf:
                label = s
            buf.append(line)
    if buf:
        chunks.append({"label": label, "text": "\n".join(buf).strip()})
    return [c for c in chunks if len(c["text"]) >= 80]


def best_file(chunk_text: str, group: dict, entries: list[dict]) -> tuple[str, str, int]:
    """그룹의 repo 안에서 가장 잘 맞는 파일을 고른다. (repo, file, score)"""
    pool = [e for e in entries if e["repo"] in group["repos"]]
    if not pool:
        return group["repos"][0], "", 0
    ct = tokens(chunk_text)
    best = (group["repos"][0], "", 0)
    for e in pool:
        et = tokens(e["title"]) | tokens(" ".join(e.get("headings", [])))
        hit = len(ct & et)
        if hit > best[2]:
            best = (e["repo"], e["file"], hit)
    return best


def main() -> int:
    a = sys.argv[1:]
    verbose = "--verbose" in a
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    digest = json.loads(DIGEST.read_text(encoding="utf-8"))
    entries = json.loads(INDEX.read_text(encoding="utf-8"))["pages"]
    groups = rules["groups"]
    section_map = rules["section_map"]
    mixed_at = rules.get("mixed_threshold_chars", 5000)

    proposal = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "pages": []}
    for pid, meta in digest["pages"].items():
        if meta.get("notebook") in rules["notebook_skip"]:
            continue
        section = meta.get("section") or ""
        mapped = section_map.get(section, "")
        f = TEXT / f"{safe(pid)}.txt"
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")

        page_scores = group_scores(meta["title"] + " " + " ".join(meta.get("headings", [])) + " " + meta.get("sample", ""), groups)
        page_group = max(page_scores, key=page_scores.get) if page_scores else ""
        if mapped == "SKIP":
            proposal["pages"].append({"title": meta["title"], "section": section, "chars": meta["chars"],
                                      "action": "skip", "reason": "섹션 규칙 SKIP"})
            continue
        if page_group and page_scores.get(page_group, 0) < 2:
            page_group = mapped if mapped and mapped != "SKIP" else page_group
        elif not page_group:
            page_group = mapped if mapped and mapped != "SKIP" else ""

        chunks = split_chunks(text) if meta["chars"] >= mixed_at else [{"label": "(전체)", "text": text}]
        routed = []
        for ch in chunks:
            sc = group_scores(ch["text"], groups)
            g = max(sc, key=sc.get) if sc and max(sc.values()) >= 2 else page_group
            if not g:
                routed.append({"label": ch["label"], "group": "", "chars": len(ch["text"])})
                continue
            repo, file, hit = best_file(ch["text"], groups[g], entries)
            routed.append({"label": ch["label"], "group": g, "chars": len(ch["text"]),
                           "repo": repo, "file": file, "match": hit})

        used = {r["group"] for r in routed if r.get("group")}
        if not used:
            action = "hold"
        elif len(used) == 1:
            action = "create" if groups[next(iter(used))].get("create") else "update"
        else:
            action = "split"
        item = {"title": meta["title"], "section": section, "notebook": meta["notebook"],
                "chars": meta["chars"], "action": action, "chunks": routed}
        proposal["pages"].append(item)
        if verbose and action != "skip":
            print(f"[{action}] {meta['title'][:40]} ({meta['chars']}자) → {sorted(used)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proposal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    counts = {}
    for p in proposal["pages"]:
        counts[p["action"]] = counts.get(p["action"], 0) + 1
    print(json.dumps({"out": str(OUT), "pages": len(proposal["pages"]), "actions": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
