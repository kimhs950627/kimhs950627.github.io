#!/usr/bin/env python3
"""onenote_html.py — 발행 페이지의 '단원 블록'을 뽑아내는 공용 파서.

이 저장소들의 페이지는 두 모양이 섞여 있다.
1) `<section aria-labelledby="x"><h2 id="x">…</h2> … </section>`  (lab-followup, primary-care 등)
2) `<main>` 안에 `<h2 id="x">…</h2>` 만 나열                    (endoscopy-study egd 등)

두 경우 모두 (key, block_html) 목록으로 돌려주고, 같은 key 로 교체할 수 있게 한다.
"""
from __future__ import annotations

import re

SEC = re.compile(r"<section\b[^>]*>.*?</section>", re.S)


def h2id(block: str) -> str:
    m = (re.search(r'<h2[^>]*\bid="([^"]+)"', block)
         or re.search(r'aria-labelledby="([^"]+)"', block)
         or re.search(r'<section[^>]*\bid="([^"]+)"', block))
    return m.group(1) if m else ""


def page_blocks(html: str) -> tuple[list[tuple[str, str]], str]:
    """(blocks, mode). mode = 'section' | 'h2'."""
    secs = SEC.findall(html)
    if secs and all(b.count("<section") == 1 for b in secs):
        blocks = [(k, b) for b in secs if (k := h2id(b))]
        if blocks:
            return blocks, "section"

    m = re.search(r"<main[^>]*>(.*)</main>", html, re.S)
    body = m.group(1) if m else html
    parts = re.split(r'(?=<h2[^>]*\bid=")', body)
    blocks = []
    for part in parts:
        hm = re.match(r'<h2[^>]*\bid="([^"]+)"', part)
        if hm and len(part.strip()) > 60:
            blocks.append((hm.group(1), part.strip()))
    return blocks, "h2"
