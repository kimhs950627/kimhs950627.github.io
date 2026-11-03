---
title: 원노트 → 발행물 동기화
description: >
  Managed by a kimaki routine. Do not move or delete this file
  without also updating the routine (kimaki routine list / kimaki routine edit).
---

# 원노트 변경을 발행된 페이지에 반영한다

작업 폴더: `/home/kimhs950627/projects/ghpages-hub`
스킬: `pages-publish` (필요하면 로드)

## 절차

1. `data/onenote-map.json` 을 읽는다. `enabled: true` 인 항목이 없으면 **아무 것도 하지 않고 종료**한다
   (사용자에게 알리지 않는다).
2. 활성 항목마다 OneNote MCP 로 해당 노트북 → 섹션 → 페이지를 읽는다
   (`onenote.searchPages` 또는 `getPageByTitle`, `format: "html"`).
3. 페이지 내용을 발행 파일에 넣을 HTML 조각으로 만든다.
   - 원문의 표·목록·수치를 그대로 살린다. 요약하거나 윤문하지 않는다.
   - 제목 계층은 발행 파일의 기존 태그 수준에 맞춘다(h2/h3).
4. `/tmp/opencode/onenote-patch.json` 을 만든다.

   ```json
   {"updates":[{"repo":"…","file":"…","region":"onenote","html":"…"}]}
   ```

5. `python3 scripts/onenote_apply.py --check /tmp/opencode/onenote-patch.json` 으로 먼저 확인한다.
   "마커 없음" 이 나오면 그 항목은 건너뛰고 사용자에게 마커가 필요하다고 알린다.
6. 문제 없으면 `python3 scripts/onenote_apply.py /tmp/opencode/onenote-patch.json` 실행.
   public 저장소는 push 로 GitHub Pages 가 갱신되고, private 저장소는 스크립트가 Cloudflare 에 재배포한다.
7. `GITHUB_TOKEN=$(gh auth token) python3 scripts/build-pages.py` → 변경 있으면 커밋·push.
8. 결과를 한 줄씩 보고한다: 어떤 파일이 바뀌었고, 어디에 발행됐는지. 바뀐 게 없으면 보고하지 않고 끝낸다.

## 허용/금지

- 원노트 페이지에 붙은 **그림**은 `assets/img/onenote/` 에 내려받아 본문에서 참조해도 된다(원본 페이지의 그림만).
- 마커 밖의 내용을 고치지 않는다.
- 원노트에 없는 내용을 지어내지 않는다.
- 커밋 메시지에 PHI 를 넣지 않는다.
