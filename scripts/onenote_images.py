#!/usr/bin/env python3
"""onenote_images.py — 페이지 XML 에서 그림을 꺼내 onenote_apply 용 assets 항목으로 만든다.

  python3 scripts/onenote_images.py --xml data/onenote-raw/<id>.xml --prefix egd/la --out /tmp/opencode/assets.json

출력: {"assets":[{"path":"assets/onenote/<명>.png","base64":"..."}], "tags":["<img src=\"../assets/onenote/x.png\" alt=\"\">"]}
"""
import base64, json, re, sys
from pathlib import Path

IMG = re.compile(r'<one:Image[^>]*>(.*?)</one:Image>', re.S)
SIZE = re.compile(r'<one:Size[^>]*width="([\d.]+)"[^>]*height="([\d.]+)"')
DATA = re.compile(r'<one:Data>(.*?)</one:Data>', re.S)
FMT = re.compile(r'format="([^"]+)"')
EXT = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "gif": "gif", "bmp": "bmp"}


def main() -> int:
    a = sys.argv[1:]
    get = lambda k, d="": a[a.index(k) + 1] if k in a else d
    xml = Path(get("--xml")).read_text(encoding="utf-8", errors="ignore")
    prefix = get("--prefix", "onenote")
    out = Path(get("--out", "/tmp/opencode/onenote-assets.json"))
    rel_dir = get("--dir", "assets/onenote")

    max_n = int(get("--max", "12"))
    assets, tags = [], []
    blocks = []
    for block in IMG.findall(xml):
        m = SIZE.search(block)
        w, h = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)
        blocks.append((w * h, block))
    blocks.sort(key=lambda b: -b[0])

    for i, (_, block) in enumerate(blocks[:max_n], 1):
        m = DATA.search(block)
        if not m:
            continue
        fmt = (FMT.search(block).group(1) if FMT.search(block) else "png").lower()
        ext = EXT.get(fmt, "png")
        name = f"{prefix.replace('/', '-')}-{i}.{ext}"
        b64 = re.sub(r"\s+", "", m.group(1))
        assets.append({"path": f"{rel_dir}/{name}", "base64": b64})
        tags.append(f'<img src="/{rel_dir}/{name}" alt="원노트 그림 {i}" loading="lazy">')

    out.write_text(json.dumps({"assets": assets, "tags": tags}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"count": len(assets), "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
