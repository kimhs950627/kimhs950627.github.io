#!/usr/bin/env python3
"""onenote_images_pull.py — 원노트 페이지의 그림을 내려받아 대상 페이지 단원에 넣는다.

동작
1. 원노트 페이지 HTML(Graph) 에서 <img src=...> 를 찾는다.
2. 앞뒤 문맥(각 200자)을 캡션·배치 근거로 쓴다.
3. 대상 repo 의 단원(블록) 중 문맥 키워드가 가장 많은 곳 끝에 <figure> 로 붙인다.
4. 파일은 repo `assets/onenote/<slug>-NN.<ext>` 로 저장(apply 의 assets 로 전달).

  python3 scripts/onenote_images_pull.py --page "EGD protocol (personal)" --file endoscopy-study/egd/00-총론.html
  python3 scripts/onenote_images_pull.py --all --max-per-page 8
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from onenote_delta import GRAPH, access_token  # noqa: E402
from onenote_html import page_blocks  # noqa: E402

STATE = Path.home() / ".local" / "state" / "onenote"
REPOS = STATE / "repos"
MAPPING = STATE / "mapping-model.json"
OUT = Path("/tmp/opencode/images.json")
IMG = re.compile(r"<img\b[^>]*>", re.I)


def get(url: str, token: str, binary: bool = False, tries: int = 4):
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                return res.read() if binary else res.read().decode("utf-8", "ignore"), res.headers.get("Content-Type", "")
        except urllib.error.HTTPError as err:
            if err.code == 429:
                wait = 15 * (attempt + 1)
                print(f"    · 429 → {wait}s 대기", flush=True)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"429 반복: {url[:60]}")


def slug(text: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "-", text).strip("-").lower()
    return s[:40] or "img"


def strip_tags(raw: str) -> str:
    text = re.sub(r"<[^>]*>", " ", raw)
    text = re.sub(r'[A-Za-z-]+="[^"]*"', " ", text)  # 남은 속성 조각
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def context(html: str, pos: int, span: int = 220) -> str:
    return strip_tags(html[max(0, pos - span):pos] + " " + html[pos:pos + span])


def tidy(text: str, limit: int = 70) -> str:
    """원노트 alt 는 글자마다 공백이 들어간 경우가 많다('조 직 검 사') → 붙이고 자른다."""
    t = re.sub(r"\s+", " ", text).strip()
    if re.search(r"[가-힣] [가-힣] [가-힣]", t):
        t = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", t)
    t = t.strip(" .·-—")
    if len(t) > limit:
        cut = t[:limit]
        t = cut[: cut.rfind(" ")] if " " in cut else cut
    return t


def img_alt(tag: str) -> str:
    m = re.search(r'alt="([^"]*)"', tag)
    if not m:
        return ""
    val = tidy(m.group(1))
    return val if len(val) >= 6 and "Microsoft" not in val else ""


def caption_for(ctx: str) -> str:
    ctx = re.sub(r"\s+", " ", ctx).strip()
    cut = re.split(r"(?<=[.·])\s|—|\n", ctx)
    line = cut[0] if cut else ctx
    return tidy(line)


def score(ctx: str, block: str) -> int:
    a = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{4,}", ctx.lower()))
    b = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{4,}", block.lower()))
    return len(a & b)


def pull(page_id: str, title: str, targets: list[tuple[str, str]], max_n: int, token: str, patch: dict) -> None:
    """한 원노트 페이지의 그림을 **가장 잘 맞는 파일 하나씩**에만 배치한다(중복 배치 금지)."""
    html, _ = get(f"{GRAPH}/me/onenote/pages/{page_id}/content", token)
    hits = list(IMG.finditer(html))
    if not hits:
        print(f"  {title[:30]}: 그림 없음", flush=True)
        return

    cand: list[tuple[str, str, str, str]] = []  # repo, rel, sid, block
    for repo, rel in targets:
        path = REPOS / repo / rel
        if not path.exists():
            continue
        blocks, _ = page_blocks(path.read_text(encoding="utf-8"))
        for sid, block in blocks:
            if sid != "onenote":
                cand.append((repo, rel, sid, block))
    if not cand:
        print(f"  {title[:30]}: 쓸 단원 없음", flush=True)
        return

    picks: dict[tuple[str, str], dict[str, str]] = {}
    assets: dict[tuple[str, str], list[dict]] = {}
    placed = 0
    for i, m in enumerate(hits[:max_n], 1):
        tag = m.group(0)
        sm = re.search(r'src="([^"]+)"', tag)
        if not sm:
            continue
        src = sm.group(1)
        if not src.startswith("http"):
            src = "https://graph.microsoft.com" + (src if src.startswith("/") else "/" + src)
        ctx = context(html, m.start())
        repo, rel, sid, block = max(cand, key=lambda c: score(ctx, c[3]))
        alt = img_alt(tag)
        cap = alt if 6 <= len(alt) <= 60 else caption_for(ctx)
        cur = picks.get((repo, rel), {}).get(sid, block)
        if cap and cap in cur:
            continue
        if f"{slug(title)}-{i:02d}." in cur:
            continue  # 이미 이 페이지에 들어간 그림
        try:
            data, ctype = get(src, token, binary=True)
        except Exception as err:  # noqa: BLE001
            print(f"    ! {title[:20]} 이미지 {i}: {err}", flush=True)
            continue
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}.get(ctype.split(";")[0], "png")
        relp = f"assets/onenote/{slug(title)}-{i:02d}.{ext}"
        assets.setdefault((repo, rel), []).append({"path": relp, "base64": base64.b64encode(data).decode()})
        updir = Path(relp).parent.as_posix()
        fig = (f'\n      <figure class="fig">\n'
               f'        <img src="{updir}/{Path(relp).name}" alt="{cap}" loading="lazy">\n'
               f'        <figcaption>{cap}</figcaption>\n'
               f'      </figure>\n')
        picks.setdefault((repo, rel), {})[sid] = cur.rstrip() + "\n" + fig
        placed += 1
        time.sleep(1.2)

    for (repo, rel), secs in picks.items():
        patch["updates"].append({
            "repo": repo,
            "file": rel,
            "sections": secs,
            "assets": assets.get((repo, rel), []),
            "source": {"page": title, "images": len(assets.get((repo, rel), [])), "model": "graph"},
            "commit_message": f"docs(onenote): {rel} — 원노트 그림 {len(assets.get((repo, rel), []))}개 추가",
        })
        print(f"  + {title[:28]} → {repo}/{rel}: 그림 {len(assets.get((repo, rel), []))}개", flush=True)


def main() -> int:
    args = sys.argv[1:]
    getk = lambda k, d="": args[args.index(k) + 1] if k in args else d
    only_page = getk("--page")
    only_file = getk("--file")
    max_n = int(getk("--max-per-page", "8"))
    token = access_token()
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))

    patch = {"updates": [], "generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for pid, dec in mapping["pages"].items():
        page = dec.get("page") or ""
        if only_page and page != only_page:
            continue
        targets: list[tuple[str, str]] = []
        for seg in dec.get("segments") or []:
            repo, rel = seg.get("repo"), seg.get("file")
            if repo in (None, "NEW") or not rel or float(seg.get("confidence", 0)) < 0.6:
                continue
            if only_file and f"{repo}/{rel}" != only_file:
                continue
            if (repo, rel) not in targets:
                targets.append((repo, rel))
        if not targets:
            continue
        pull(pid, page or targets[0][1], targets, max_n, token, patch)

    out = Path(getk("--out", str(OUT)))
    out.write_text(json.dumps(patch, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "updates": len(patch["updates"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
