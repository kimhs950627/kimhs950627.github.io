#!/usr/bin/env python3
"""onenote_images_pull.py — 원노트 그림을 주제에 맞는 단원 본문에 넣는다.

설계(2026-11-08 재작성)
- 한 원노트 페이지의 그림을 **대상 파일·단원 하나에만** 배치한다(복사 배치 금지).
- 배치 결정: 그림 앞 문맥(앞 700자) vs 후보 단원 텍스트의 키워드 점수.
  점수가 낮거나(<6) 2등과 차이가 작으면(<2) **넣지 않는다** — 엉뚱한 페이지(TIRADS 그림이 혈당 페이지로) 방지.
- 캡션: 원노트 alt/문맥에서 **사람이 읽을 수 있는 문장**만 쓴다. `<img ...>`, GUID, 'Microsoft' 같은 쓰레기는 버리고
  앞 문맥의 마지막 문장(8~90자)을 쓴다.
- 페이지당 그림 상한(기본 40), 파일당 상한(기본 12) — 복부초음파처럼 그림이 많은 페이지를 6장에서 끊지 않는다.
- 그림 파일은 repo `assets/onenote/<page-slug>-NN.<ext>`, HTML 에서는 파일 깊이에 맞는 상대경로.

  python3 scripts/onenote_images_pull.py --page "간담췌 파트 술기"
  python3 scripts/onenote_images_pull.py --all --max-per-page 40 --max-per-file 12
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
HEXLIKE = re.compile(r"[0-9a-f]{16,}", re.I)
HEAD = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)


def get(url: str, token: str, binary: bool = False, tries: int = 4):
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                body = res.read()
                return (body, res.headers.get("Content-Type", "")) if binary else body.decode("utf-8", "ignore")
        except urllib.error.HTTPError as err:
            if err.code == 429:
                wait = 15 * (attempt + 1)
                print(f"    · 429 → {wait}s 대기", flush=True)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError(f"429 반복: {url[:60]}")


def strip_tags(raw: str) -> str:
    text = re.sub(r"<[^>]*>", " ", raw)
    text = re.sub(r'[A-Za-z-]+="[^"]*"', " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def tidy(text: str, limit: int = 80) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    # 원노트는 글자 사이에 공백을 넣는 경우가 있다('조 직 검 사') — 토큰이 대부분 1글자일 때만 붙인다
    tokens = t.split()
    if len(tokens) >= 5 and sum(len(x) for x in tokens) / len(tokens) <= 1.3:
        t = re.sub(r"(?<=[가-힣])\s+(?=[가-힣])", "", t)
    t = re.sub(r"^[0-9]{1,2}[.)]?\s*", "", t)
    t = t.strip(" .·-—:;/\\")
    if len(t) > limit:
        cut = t[:limit]
        t = cut[: cut.rfind(" ")] if " " in cut else cut
    return t.strip()


def slug(text: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣]+", "-", text).strip("-").lower()
    return s[:36] or "img"


def bad_caption(text: str) -> bool:
    if not text or len(text) < 6:
        return True
    if "<" in text or ">" in text:
        return True
    if HEXLIKE.search(text):
        return True
    if "Microsoft" in text or "graph.microsoft" in text or "http" in text:
        return True
    if "동영상으로" in text or "화면을종료" in text:
        return True
    digits = len(re.findall(r"[0-9]", text))
    if digits >= max(4, len(text) // 3):
        return True
    if re.match(r"^[A-Za-z0-9 :.,()/%+-]+$", text) and digits:
        return True
    if len(re.findall(r"[A-Za-z0-9]", text)) > 0 and len(re.findall(r"[가-힣]", text)) == 0 and len(text) < 8:
        return True
    return False


def caption_for(html: str, pos: int) -> str:
    """그림 직전 문맥에서 읽히는 문장을 캡션으로. 없으면 직전 소제목."""
    pre = strip_tags(html[max(0, pos - 900):pos])
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n{2,}", pre) if p.strip()]
    for part in reversed(parts):
        cand = tidy(part, 90)
        if not bad_caption(cand) and 8 <= len(cand) <= 90:
            return cand
    heads = HEAD.findall(html[:pos])
    if heads:
        cand = tidy(strip_tags(heads[-1]), 60)
        if not bad_caption(cand):
            return cand
    return ""


def headings(html: str) -> list[tuple[int, str]]:
    """(시작 위치, 제목) 목록 — 그림이 어느 소제목 아래에 있는지 판단하는 데 쓴다."""
    out = []
    for m in HEAD.finditer(html):
        text = tidy(strip_tags(m.group(1)), 60)
        if text:
            out.append((m.start(), text))
    return out


def assign_by_model(page: str, heads: list[tuple[int, str]], cand: list[tuple[str, str, str, str]]) -> dict[int, int]:
    """소제목 → 후보 인덱스 배정을 모델에게 맡긴다(어휘 점수는 한국어에서 너무 약하다)."""
    try:
        from onenote_decide import call, parse_json
    except Exception:  # noqa: BLE001
        return {}
    if not heads or not cand:
        return {}
    lines = []
    for i, (repo, rel, sid, block) in enumerate(cand):
        title = tidy(strip_tags(re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S).group(1)), 40) if "<h2" in block else sid
        snippet = tidy(strip_tags(block), 60)
        lines.append(f"{i}) {rel} | {sid} | {title} — {snippet}")
    head_lines = [f"{k}) {t}" for k, (_, t) in enumerate(heads)]
    prompt = f"""원노트 페이지 '{page}' 의 소제목 목록과, 그림을 넣을 수 있는 발행 페이지 단원 목록이다.
각 소제목의 내용이 가장 잘 맞는 단원 하나를 골라라. 맞는 단원이 없으면 -1.

[소제목]
{chr(10).join(head_lines)}

[단원 후보]
{chr(10).join(lines)}

출력 JSON 하나: {{"map": {{"0": 12, "1": -1, ...}}}}  (키=소제목 번호, 값=단원 번호)"""
    try:
        out = parse_json(call(prompt, max_tokens=2000, system="너는 의학 참고서 편집자다. JSON 만 출력한다."))
    except Exception:  # noqa: BLE001
        return {}
    res = {}
    for k, v in (out.get("map") or {}).items():
        try:
            ki, vi = int(k), int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= ki < len(heads) and 0 <= vi < len(cand):
            res[ki] = vi
    return res


def place_one(page: str, ctx: str, cand: list[tuple[str, str, str, str]], token_unused: str = "") -> tuple[int, str]:
    """그림 하나를 단원 하나에 배정하고 캡션을 만든다(모델). 실패하면 (-1, "")."""
    try:
        from onenote_decide import call, parse_json
    except Exception:  # noqa: BLE001
        return -1, ""
    lines = []
    for i, (repo, rel, sid, block) in enumerate(cand):
        title = tidy(strip_tags(re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S).group(1)), 30) if "<h2" in block else sid
        lines.append(f"{i}) {rel} | {sid} | {title} — {tidy(strip_tags(block), 70)}")
    prompt = f"""원노트 그림이 들어갈 자리를 고른다. 페이지 '{page}'.

[그림 주변 원문]
{ctx[:900]}

[후보 단원]
{chr(10).join(lines)}

규칙:
- 그림의 주제(장기·소견·계측법)와 가장 잘 맞는 단원 **하나**를 고른다. 애매하면 -1.
- caption 은 원문에 있는 내용만으로 8~60자 한국어 한 줄. 기계 로그·숫자 나열·파일명은 쓰지 않는다.
출력 JSON: {{"i": 3, "caption": "췌장 미부 횡스캔"}}"""
    try:
        out = parse_json(call(prompt, max_tokens=600, system="너는 초음파 참고서 편집자다. JSON 만 출력한다."))
    except Exception:  # noqa: BLE001
        return -1, ""
    try:
        idx = int(out.get("i", -1))
    except (TypeError, ValueError):
        return -1, ""
    cap = tidy(str(out.get("caption") or ""), 60)
    if bad_caption(cap):
        cap = ""
    if not (0 <= idx < len(cand)):
        return -1, cap
    return idx, cap


def score(ctx: str, block: str) -> int:
    a = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", ctx.lower()))
    b = set(re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", block.lower()))
    a -= {"그림", "사진", "초음파", "검사"}
    return len(a & b)


def pull(page_id: str, title: str, targets: list[tuple[str, str]], max_n: int, max_file: int,
         token: str, patch: dict) -> None:
    html = get(f"{GRAPH}/me/onenote/pages/{page_id}/content", token)
    hits = list(IMG.finditer(html))
    if not hits:
        print(f"  {title[:30]}: 그림 없음", flush=True)
        return

    cand: list[tuple[str, str, str, str]] = []
    for repo, rel in targets:
        path = REPOS / repo / rel
        if not path.exists():
            continue
        prefix = "../" * rel.count("/")
        blocks, _ = page_blocks(path.read_text(encoding="utf-8"))
        for sid, block in blocks:
            if sid == "onenote":
                continue
            fixed = block.replace('src="assets/onenote/', f'src="{prefix}assets/onenote/')
            cand.append((repo, rel, sid, fixed))
    if not cand:
        print(f"  {title[:30]}: 쓸 단원 없음", flush=True)
        return

    heads = headings(html)
    mapping = assign_by_model(title, heads, cand)
    if mapping:
        print(f"    · 소제목 {len(heads)}개 중 {len(mapping)}개를 단원에 배정", flush=True)

    picks: dict[tuple[str, str], dict[str, str]] = {}
    assets: dict[tuple[str, str], list[dict]] = {}
    per_file: dict[tuple[str, str], int] = {}
    placed = skipped = 0

    hits = [h for h in hits if re.search(r'src="https?://', h.group(0))]
    if not hits:
        print(f"  {title[:30]}: 실제 그림 리소스 없음", flush=True)
        return
    for i, m in enumerate(hits[:max_n], 1):
        tag = m.group(0)
        sm = re.search(r'src="([^"]+)"', tag)
        if not sm:
            continue
        src = sm.group(1)
        if not src.startswith("http"):
            src = "https://graph.microsoft.com" + (src if src.startswith("/") else "/" + src)
        ctx = strip_tags(html[max(0, m.start() - 700):m.start() + 300])
        # 그림이 속한 소제목(모델 배정) 우선, 없으면 그림 단위 모델 배정, 그다음 어휘 점수
        hidx = -1
        for k, (pos, _) in enumerate(heads):
            if pos < m.start():
                hidx = k
            else:
                break
        idx, cap0 = place_one(title, ctx, cand)
        if idx < 0:
            ranked = sorted(cand, key=lambda c: -score(ctx, c[3]))
            if ranked and score(ctx, ranked[0][3]) >= 3:
                best = ranked[0]
            else:
                skipped += 1
                continue
        else:
            best = cand[idx]
        repo, rel = best[0], best[1]
        if per_file.get((repo, rel), 0) >= max_file:
            skipped += 1
            continue
        sid, block = best[2], best[3]
        cap = cap0 if not bad_caption(cap0) else ""
        if not cap:
            cand_cap = caption_for(html, m.start())
            cap = cand_cap if not bad_caption(cand_cap) else ""
        if not cap and hidx >= 0:
            cap = tidy(heads[hidx][1], 60)
        cur = picks.get((repo, rel), {}).get(sid, block)
        if cap and cap in cur:
            skipped += 1
            continue
        if cap and f"{slug(title)}-" in cur and cap in cur:
            skipped += 1
            continue
        try:
            data, ctype = get(src, token, binary=True)
        except Exception as err:  # noqa: BLE001
            print(f"    ! {title[:20]} 이미지 {i}: {err}", flush=True)
            continue
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}.get(
            ctype.split(";")[0], "png")
        relp = f"assets/onenote/{slug(title)}-{i:02d}.{ext}"
        prefix = "../" * rel.count("/")
        alt = cap or f"{title} 그림 {i}"
        assets.setdefault((repo, rel), []).append({"path": relp, "base64": base64.b64encode(data).decode()})
        fig = (f'\n      <figure class="fig">\n'
               f'        <img src="{prefix}{relp}" alt="{alt}" loading="lazy">\n'
               f'        <figcaption>{alt}</figcaption>\n'
               f'      </figure>\n')
        if cur.rstrip().endswith("</section>"):
            body = cur.rstrip()[: -len("</section>")].rstrip()
            picks.setdefault((repo, rel), {})[sid] = body + "\n" + fig + "    </section>"
        else:
            picks.setdefault((repo, rel), {})[sid] = cur.rstrip() + "\n" + fig
        per_file[(repo, rel)] = per_file.get((repo, rel), 0) + 1
        placed += 1
        time.sleep(1.1)

    for (repo, rel), secs in picks.items():
        n = len(assets.get((repo, rel), []))
        patch["updates"].append({
            "repo": repo,
            "file": rel,
            "sections": secs,
            "assets": assets.get((repo, rel), []),
            "source": {"page": title, "images": n, "model": "graph"},
            "commit_message": f"docs(onenote): {rel} — 원노트 그림 {n}개 추가",
        })
        print(f"  + {title[:26]} → {repo}/{rel}: {n}장", flush=True)
    print(f"    (배치 {placed}, 건너뜀 {skipped})", flush=True)


def main() -> int:
    args = sys.argv[1:]
    getk = lambda k, d="": args[args.index(k) + 1] if k in args else d
    only_page = getk("--page")
    only_file = getk("--file")
    max_n = int(getk("--max-per-page", "40"))
    max_file = int(getk("--max-per-file", "12"))
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
        pull(pid, page or targets[0][1], targets, max_n, max_file, token, patch)

    out = Path(getk("--out", str(OUT)))
    out.write_text(json.dumps(patch, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "updates": len(patch["updates"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
