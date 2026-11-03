#!/usr/bin/env python3
"""onenote_inventory_local.py — COM 으로 내보낸 페이지 XML 로 색인 JSON 을 만든다.

  python3 scripts/onenote_inventory_local.py --manifest /tmp/opencode/onenote-manifest.json \
      --raw /tmp/opencode/onenote-html --out data/onenote-inventory.json
"""
import json, re, sys
from pathlib import Path

ONE_T = re.compile(r"<one:T[^>]*>(.*?)</one:T>", re.S)
TAGS = re.compile(r"<[^>]+>")
WORD = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")


def args(name, default=""):
    a = sys.argv[1:]
    return a[a.index(name) + 1] if name in a else default


def text_of(xml: str) -> str:
    parts = [TAGS.sub("", m).strip() for m in ONE_T.findall(xml)]
    return "\n".join(p for p in parts if p)


def main():
    manifest = json.loads(Path(args("--manifest")).read_text(encoding="utf-8"))
    raw = Path(args("--raw"))
    out = Path(args("--out", "data/onenote-inventory.json"))

    pages = []
    for row in manifest:
        safe = re.sub(r"[^A-Za-z0-9!._-]", "_", row["id"])
        f = raw / f"{safe}.xml"
        if not f.exists():
            continue
        body = text_of(f.read_text(encoding="utf-8", errors="ignore"))
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        counts = {}
        for w in WORD.findall(body):
            w = w.lower()
            counts[w] = counts.get(w, 0) + 1
        pages.append({
            "page": row["page"],
            "section": row["section"],
            "modified": row.get("modified", "")[:19],
            "chars": len(body),
            "lines": len(lines),
            "headings": [ln for ln in lines if len(ln) <= 40][:10],
            "keywords": [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:18]],
            "sample": " ".join(lines[:4])[:220],
        })
    pages.sort(key=lambda p: (p["section"], -p["chars"]))
    out.write_text(json.dumps({"generated": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                               "count": len(pages), "pages": pages}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(pages)} pages → {out}")


if __name__ == "__main__":
    main()
