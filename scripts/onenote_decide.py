#!/usr/bin/env python3
"""onenote_decide.py — 바뀐 원노트 페이지를 '어느 파일의 onenote 구간'에 넣을지 Command Code API 로 판단한다.

CLI 없이 HTTP 로 부른다(모델 고정: deepseek/deepseek-v4.1-flash, reasoning_effort=low).

  python3 scripts/onenote_decide.py --title "GI 학회 정리" --section "외래진료 공부" \\
      --text /tmp/opencode/page.txt --candidates /tmp/opencode/cand.json \\
      --out /tmp/opencode/patch.json [--min-confidence 0.7]

종료코드: 0 = patch 생성, 3 = 확신 부족(제안만), 1 = 오류
API 키: $COMMANDCODE_API_KEY 또는 ~/.config/pfarm/commandcode.env
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.commandcode.ai/provider/v1/chat/completions"
MODEL = "deepseek/deepseek-v4.1-flash"

SYSTEM = """너는 개인 학습 페이지의 편집자다. 원노트 페이지 하나가 주어지면, 후보 HTML 목록 중
가장 알맞은 파일 하나를 고르고, 그 페이지 내용을 HTML 조각으로 옮긴다.

규칙:
- repo/file 은 반드시 후보 목록에 있는 값 그대로 쓴다. 없으면 confidence 0.
- html 은 원노트에 있는 내용만 쓴다. 요약·창작·추측 금지. 없는 수치는 쓰지 말고 "확인 필요"로 남긴다.
- h3, p, ul, ol, table, strong 만 쓴다. <html>, <body>, <script>, style 속성 금지. 인라인 class도 쓰지 않는다.
- 원문의 표·목록 구조를 최대한 보존한다. 문장은 원문을 다듬되 의미를 바꾸지 않는다.
- 출력은 JSON 하나뿐이다:
  {"repo": "...", "file": "...", "confidence": 0.0~1.0, "reason": "한 문장", "html": "..."}
"""


def api_key() -> str:
    key = os.environ.get("COMMANDCODE_API_KEY")
    if key:
        return key
    env = Path.home() / ".config" / "pfarm" / "commandcode.env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("COMMANDCODE_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("COMMANDCODE_API_KEY 없음")


def call(prompt: str, max_tokens: int = 8000) -> str:
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "reasoning_effort": "low",
    }
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
            "User-Agent": "commandcode/1.88.2",
            "x-commandcode-version": "1.88.2",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as res:
        data = json.loads(res.read())
    return data["choices"][0]["message"]["content"]


def parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def main() -> int:
    a = sys.argv[1:]
    get = lambda k, d="": a[a.index(k) + 1] if k in a else d

    title, section = get("--title"), get("--section")
    text_path, cand_path = get("--text"), get("--candidates")
    out = Path(get("--out", "/tmp/opencode/onenote-patch.json"))
    min_conf = float(get("--min-confidence", "0.7"))
    if not (title and text_path and cand_path):
        print(__doc__)
        return 1

    page = Path(text_path).read_text(encoding="utf-8", errors="ignore")
    page = re.sub(r"\]\]>", "\n", page)
    page = page[:20000]
    cand = json.loads(Path(cand_path).read_text(encoding="utf-8"))
    cands = cand.get("candidates", cand if isinstance(cand, list) else [])
    if not cands:
        print("후보 없음", file=sys.stderr)
        return 1

    listing = "\n".join(
        f"- {c['repo']} :: {c['file']} — {c.get('title','')} (marker={c.get('marker')}, score={c.get('score')})"
        for c in cands
    )
    prompt = f"""원노트 페이지
제목: {title}
섹션: {section}

후보 파일:
{listing}

본문:
<<<
{page}
>>>
"""
    try:
        raw = call(prompt)
        decision = parse_json(raw)
    except Exception as err:  # noqa: BLE001
        print(f"API 실패: {err}", file=sys.stderr)
        return 1

    repo, file = decision.get("repo"), decision.get("file")
    conf = float(decision.get("confidence", 0))
    allowed = {(c["repo"], c["file"]) for c in cands}
    if (repo, file) not in allowed:
        print(f"후보 밖 선택({repo}/{file}) — 무시", file=sys.stderr)
        return 3
    if conf < min_conf or not decision.get("html"):
        Path("/tmp/opencode/onenote-proposal.json").write_text(
            json.dumps({"page": title, "section": section, "decision": decision, "candidates": cands},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"확신 부족({conf}) — 제안만 저장", file=sys.stderr)
        return 3

    patch = {
        "updates": [{
            "repo": repo,
            "file": file,
            "region": "onenote",
            "heading": f"원노트 — {title}",
            "html": decision["html"],
        }],
        "source": {"page": title, "section": section, "model": MODEL, "confidence": conf,
                   "reason": decision.get("reason", "")},
    }
    out.write_text(json.dumps(patch, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"repo": repo, "file": file, "confidence": conf, "chars": len(decision["html"]),
                      "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
