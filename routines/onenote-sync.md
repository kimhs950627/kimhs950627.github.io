---
title: 원노트 → 발행물 동기화
description: >
  Managed by a kimaki routine. Do not move or delete this file
  without also updating the routine (kimaki routine list / kimaki routine edit).
---

# 원노트 변경을 발행된 페이지에 반영한다

작업 폴더: `/home/kimhs950627/projects/ghpages-hub` · 스킬: `pages-publish`

## 0. 원칙

- **Graph(MCP) 대신 로컬 원노트 COM 을 쓴다.** MCP 는 429 레이트리밋에 걸리고 `lastModifiedDateTime` 이
  낡아 최근 수정을 놓친다(실측: GI 학회 정리 2026-11-03 수정이 MCP 에선 9월로 보였다).
- **변경 감지는 코드, 판단은 모델, 적용은 스크립트.** 본문은 파일로만 다루고 컨텍스트에 싣지 않는다.
- 확신이 없으면 **큐에 제안만** 남기고 파일을 건드리지 않는다.

## 1. 변경 감지 (토큰 0)

```bash
python3 scripts/onenote_sync_local.py --commit     # manifest + state 갱신, 바뀐 페이지 출력
```

출력에서 `changed` 목록을 본다. 300자 미만 페이지는 내용이 없으므로 건너뛴다.

## 2. 본문 확보 (파일로만)

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w scripts/onenote-export.ps1)" \
  -Mode exportall -Notebook "Books_for_Meds" -Out "$(wslpath -w /tmp/opencode/onenote-html)"
python3 scripts/onenote_inventory_local.py --manifest data/onenote-manifest.json \
  --raw /tmp/opencode/onenote-html --out data/onenote-inventory.json
```

`data/onenote-inventory.json` 에 페이지별 제목·섹션·글자수·소제목·키워드·도입부가 있다.
**본문 전문은 읽지 않는다.** 필요한 페이지만 `data/onenote-raw/<pageid>.xml` 에서 발췌한다.

## 2-1. 그림이 있는 페이지

원노트 페이지 161쪽 중 111쪽에 그림이 있다. 적용할 페이지에 `<one:Image>` 가 있으면 **그 페이지만**
`piAll` 로 다시 뽑아 그림을 꺼낸다.

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w scripts/onenote-export.ps1)" \
  -Mode exportall -Notebook "Books_for_Meds" -OnlyPage "<페이지 제목>" -Out "$(wslpath -w /tmp/opencode/onenote-img)"
python3 scripts/onenote_images.py --xml /tmp/opencode/onenote-img/<id>.xml --prefix <repo>-<장> --out /tmp/opencode/assets.json
```

`assets.json` 의 `assets` 배열을 `onenote-patch.json` 의 `assets` 로 넣으면 그림도 같은 커밋에 들어간다.
`tags` 는 본문에 넣을 `<img>` 문자열이다. 그림은 **원본 페이지의 것만** 쓰고, 12장까지만 넣는다(`--max`).

## 3. 어느 페이지로 갈지 후보 좁히기

```bash
python3 scripts/onenote_route.py --title "<페이지 제목>" --section "<섹션>" --excerpt "<도입부 300자>"
```

상위 6개 후보와 `marker` 유무가 나온다. 판단 규칙:

- 1위 점수가 2위의 **1.5배 이상**이고, 후보 파일이 주제상 명백하면 → 적용
- 비슷하거나(1위/2위 < 1.5), 주제가 어긋나면 → **제안만** 큐에 남긴다
- 마커가 없어도 `create_region` 이 기본 true 라 새 구간을 만든다. 파일 끝(`</main>`/`</body>`)에 추가된다

## 4. 반영

```bash
cat > /tmp/opencode/onenote-patch.json <<'JSON'
{"updates":[{"repo":"…","file":"…","region":"onenote","heading":"원노트 — …","html":"…"}]}
JSON
python3 scripts/onenote_apply.py --check /tmp/opencode/onenote-patch.json
python3 scripts/onenote_apply.py /tmp/opencode/onenote-patch.json
```

`onenote_apply.py` 가 ①마커 구간 교체(없으면 새 구간) ②커밋·push ③비공개 저장소면 Cloudflare 재배포까지 한다.
public 은 push 만으로 GitHub Pages 가 갱신된다.

## 5. 마무리

```bash
python3 scripts/pages_index.py            # 새 구간이 색인에 반영되게
GITHUB_TOKEN=$(gh auth token) python3 scripts/build-pages.py
```

변경이 있으면 `data/` 를 커밋·push. 결과는 알림 큐에 넣고 **직접 보고하지 않는다**(07:00 에 한 번에 간다).

```bash
python3 scripts/noti.py add-onenote "원노트 반영: <repo>/<file> · <페이지 제목>"
```

마지막 답변은 `기록됨` 한 줄만. 바뀐 게 없으면 아무 것도 넣지 않는다.

## 금지

- 마커 밖의 내용을 고치지 않는다.
- 원노트에 없는 내용을 지어내지 않는다.
- 본문 전문을 컨텍스트에 붙여 넣지 않는다(파일로 처리).
- 처방·용량이 들어간 내용은 적용 전에 사용자 확인을 받는다.
