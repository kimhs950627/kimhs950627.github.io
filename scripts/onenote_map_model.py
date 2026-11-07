#!/usr/bin/env python3
"""onenote_map_model.py — 학문적 내용이 있는 페이지만 골라 모델로 '발행처·문단 배분'을 판단한다.

모델: commandcode `deepseek/deepseek-v4.1-flash` + reasoning_effort low (onenote_decide.call 재사용).

  python3 scripts/onenote_map_model.py                 # 아직 안 한 페이지만
  python3 scripts/onenote_map_model.py --notebook "Books_for_Meds"
  python3 scripts/onenote_map_model.py --page "GI 학회 정리" --reset
  python3 scripts/onenote_map_model.py --limit 20

입력: ~/.local/state/onenote/digest.json · text/<id>.txt · data/onenote-rules.json · data/pages-index.json
출력: ~/.local/state/onenote/mapping-model.json  (페이지별 segments: repo/file/topic/confidence)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from onenote_decide import MODEL, call, parse_json  # noqa: E402

STATE = Path.home() / ".local" / "state" / "onenote"
REPO_DIR = STATE / "repos"
DIGEST = STATE / "digest.json"
TEXT = STATE / "text"
OUT = STATE / "mapping-model.json"
RULES = ROOT / "data" / "onenote-rules.json"
INDEX = ROOT / "data" / "pages-index.json"
MIN_CHARS = 600

SYSTEM = """너는 개인 학습 노트를 발행 사이트로 옮기는 편집자다. 원노트 페이지 하나와 후보 발행 파일 목록이
주어지면, 이 페이지가 학문적으로 발행할 가치가 있는지 판단하고, **문단(소제목) 단위로** 어느 파일로 보낼지 정한다.

규칙:
- repo/file 은 반드시 후보 목록에 있는 값 그대로 쓴다. 새 페이지가 필요하면 repo="NEW", file="" 로 두고
  new_title 과 visibility("public"|"private")를 쓴다.
- 한 페이지에 서로 다른 주제가 섞여 있으면 segments 를 여러 개로 나눈다(예: 내시경 내용과 간수치 내용).
  각 segment 의 headings 에는 그 문단의 소제목 텍스트를 원문 그대로 적는다.
- 개인 재무·투자·행정·연락처·환자 개인정보, 목차/링크 모음만 있는 페이지는 academic=false.
- 원문에 없는 내용을 만들지 않는다. 확신이 없으면 confidence 를 낮게 쓴다.
- 출력은 JSON 하나:
{"academic": true, "segments": [{"headings": ["..."], "repo": "...", "file": "...", "topic": "한 줄",
  "visibility": "public", "confidence": 0.0}], "skip_reason": ""}
"""


def safe(page_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9!._-]", "_", page_id)


def catalog(rules: dict, entries: list[dict]) -> str:
    """대상 repo 를 실제로 클론해 파일·단원(앵커 id 포함) 목록을 만든다.

    pages-index 만 쓰면 lab-followup·primary-care-protocol·ward-nights 처럼 아직 색인에 안 들어온
    repo 가 후보에서 빠져 모델이 엉뚱하게 NEW 를 고른다(2026-11-07 실제 발생).
    """
    lines = []
    for group, g in rules["groups"].items():
        for repo in g["repos"]:
            for e in repo_entries(repo):
                heads = "; ".join(f"{h[0]}|{h[1][:36]}" for h in e["h2"][:14])
                lines.append(f"- {repo} :: {e['file']} — {e['title'][:60]} | 단원: {heads}")
        if g.get("create"):
            lines.append(f"- NEW :: (새 페이지) — {group} 주제. 이 그룹에 맞는 내용이면 repo=\"NEW\" 로 제안")
    return "\n".join(lines)


def ensure_repo(repo: str) -> Path | None:
    import subprocess

    d = REPO_DIR / repo
    if d.exists():
        return d
    try:
        subprocess.run(["git", "clone", "-q", "--depth", "1",
                        f"https://github.com/kimhs950627/{repo}.git", str(d)], check=True, timeout=180)
        return d
    except Exception:  # noqa: BLE001
        return None


def repo_entries(repo: str) -> list[dict]:
    d = ensure_repo(repo)
    if not d:
        return []
    out = []
    for f in sorted(d.rglob("*.html")):
        if "node_modules" in f.parts or f.name.startswith("404"):
            continue
        html = f.read_text(encoding="utf-8", errors="ignore")
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        h2 = [(m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip())
              for m in re.finditer(r'<h2[^>]*id="([^"]+)"[^>]*>(.*?)</h2>', html, re.S)]
        out.append({
            "repo": repo,
            "file": str(f.relative_to(d)),
            "title": (title.group(1).strip() if title else f.stem),
            "headings": [h[1] for h in h2],
            "h2": h2,
        })
    return out


def load(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def save(doc: dict) -> None:
    doc["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    doc["count"] = len(doc["pages"])
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(OUT)


def main() -> int:
    a = sys.argv[1:]
    get = lambda k, d="": a[a.index(k) + 1] if k in a else d
    only_page = get("--page")
    only_notebook = get("--notebook")
    limit = int(get("--limit", "0"))
    reset = "--reset" in a

    rules = load(RULES, {})
    digest = load(DIGEST, {"pages": {}})
    entries = load(INDEX, {"pages": []})["pages"]
    cat = catalog(rules, entries)
    doc = load(OUT, {"pages": {}})
    if reset and only_page:
        for pid, meta in digest["pages"].items():
            if meta.get("title") == only_page:
                doc["pages"].pop(pid, None)

    todo = []
    for pid, meta in digest["pages"].items():
        if meta.get("notebook") in rules.get("notebook_skip", []):
            continue
        if rules.get("section_map", {}).get(meta.get("section") or "") == "SKIP":
            continue
        if meta.get("chars", 0) < MIN_CHARS:
            continue
        if only_notebook and meta.get("notebook") != only_notebook:
            continue
        if only_page and meta.get("title") != only_page:
            continue
        if pid in doc["pages"] and not reset:
            continue
        if not (TEXT / f"{safe(pid)}.txt").exists():
            continue
        todo.append((pid, meta))
    if limit:
        todo = todo[:limit]
    print(f"대상 {len(todo)}쪽 (digest {len(digest['pages'])}쪽 중)", flush=True)

    done = failed = 0
    for pid, meta in todo:
        text = (TEXT / f"{safe(pid)}.txt").read_text(encoding="utf-8", errors="ignore")
        text = re.sub(r"\]\]>", "\n", text)[:16000]
        prompt = f"""원노트 페이지
제목: {meta['title']}
노트북: {meta.get('notebook')} / 섹션: {meta.get('section')}
글자수: {meta.get('chars')}

후보 발행 파일:
{cat}

본문:
<<<
{text}
>>>
"""
        try:
            decision = parse_json(call(prompt, max_tokens=3000, system=SYSTEM))
        except Exception as err:  # noqa: BLE001
            failed += 1
            print(f"FAIL {meta['title'][:40]} :: {err}", flush=True)
            time.sleep(3)
            continue
        decision["page"] = meta["title"]
        decision["section"] = meta.get("section")
        decision["model"] = MODEL
        doc["pages"][pid] = decision
        done += 1
        segs = decision.get("segments") or []
        tag = "skip" if not decision.get("academic") else ",".join(sorted({s.get("repo", "?") for s in segs})) or "?"
        print(f"[{done}] {meta['title'][:36]} → {tag} ({len(segs)}seg)", flush=True)
        if done % 5 == 0:
            save(doc)
        time.sleep(0.5)

    save(doc)
    print(json.dumps({"done": done, "failed": failed, "total": len(doc["pages"]), "out": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
