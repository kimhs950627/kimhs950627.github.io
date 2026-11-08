> **실행 위치 (2026-11-08 변경)**: 이 루틴은 이제 **GitHub Actions** 가 실행한다.
> `kimhs950627/pages-jobs` 의 `원노트 동기화(onenote-sync.yml)`, 매일 **04:30 KST**(cron `30 19 * * *`).
> kimaki 루틴 131 은 삭제됨. 이 문서는 절차·규칙의 SSOT 로 남는다(수동 실행·복구용).
> 흐름: 감지 → 매핑 → 병합 → [그림] → 문장 → `jobs/*.json` 커밋 → `apply.yml`(반영·색인·웹훅).
> 상태는 `pages-jobs/state/` 에 커밋되어 다음 실행이 이어받는다.

---
title: 원노트 → 발행물 동기화
description: >
  Managed by a kimaki routine. Do not move or delete this file
  without also updating the routine (kimaki routine list / kimaki routine edit).
---

# 원노트 변경을 발행 페이지에 반영한다 (매일 새벽)

작업 폴더: `/home/kimhs950627/projects/ghpages-hub` · 스킬: `pages-publish`

이 job 은 다섯 가지를 반드시 끝낸다.

- **a. 수정 내역 catch** — **바뀐 섹션만** 고른 뒤 그 안의 페이지만 ETag 로 확인(§1). 페이지 시각은 안 믿는다
- **b. 매핑 결정** — Command Code API `deepseek/deepseek-v4.1-flash` + `reasoning_effort: low`
- **c. repo 전달** — 비공개 staging `pages-jobs` 에 job(+plan) 커밋
- **d. 페이지 생성·수정 + 허브 리스팅** — GitHub Actions 가 적용, 이어서 `kimhs950627.github.io` 색인 갱신
- **e. 웹훅 noti** — Actions 가 `notify.py send` 1회
- **f. 그림 반입** — 원노트 그림을 내려받아 해당 단원 본문에 넣는다(§4-1). 텍스트만 옮기고 끝내지 않는다.

## 0. 모델 고정

판단은 **`deepseek/deepseek-v4.1-flash` + `reasoning_effort: low`** 만 쓴다(`onenote_decide.call`).
키: `$COMMANDCODE_API_KEY` 또는 `~/.config/pfarm/commandcode.env`. 다른 모델·subagent 금지.
API 가 죽으면 kimaki 세션(같은 모델)으로 폴백하고 그 사실을 한 줄 남긴다.

## 1. a — 변경 감지 (전수 스캔 금지)

```bash
python3 scripts/onenote_changes.py --since 1d --save-text   # 매일
python3 scripts/onenote_changes.py --full                   # 주 1회 안전망(일요일)
```

- **페이지** `lastModifiedDateTime` 은 못 믿는다(실측: GI 학회 정리 실제 11-03 수정, Graph 페이지 값 08-31).
  MCP `recentPages` 는 이 필드로 거르므로 0건이 나온다 — 쓰지 않는다.
- **섹션** `lastModifiedDateTime` 은 최신이다(같은 섹션 11-03, 다른 섹션 11-04·11-05). 그래서
  ① 노트북→섹션 시각(6+21콜, 수 초)으로 후보 섹션을 고르고 ② 그 섹션 페이지만 **ETag 조건부 GET** 한다.
  변경 없으면 304(본문 0바이트). 실측: 18쪽 첫 49초 → 재실행 36초·변경 0건.
- 페이지 delta(`/me/onenote/pages/delta`)는 이 계정에서 400/404 — 대안 없음.
- `--save-text` 가 본문 캐시와 digest.json 을 함께 갱신한다.
- 실패(400/5xx)는 `failed` 로 남기고 성공으로 보고하지 않는다.

## 2. 요약(digest) 갱신 — 매핑의 근거

```bash
python3 scripts/onenote_digest.py --notebook "Books_for_Meds"
```

본문은 `~/.local/state/onenote/text/<id>.txt`(로컬, **공개 저장소 금지**), 요약은
`~/.local/state/onenote/digest.json`(제목·섹션·글자수·소제목·키워드·도입부·해시).

## 3. b — 매핑 결정 (학문적 내용만)

```bash
python3 scripts/onenote_map_model.py      # 아직 안 한 페이지만, >=600자, SKIP 섹션 제외
```

- 결과 `~/.local/state/onenote/mapping-model.json`: 페이지 → `segments[]`(headings, repo, file, topic, confidence).
- **한 페이지가 여러 repo 로 갈라질 수 있다** — GI 학회 정리처럼 내시경 파트는 `endoscopy-study`,
  간수치 파트는 `lab-followup`. 통째로 한 곳에 넣지 않는다.
- 후보 카탈로그는 대상 repo 를 실제 클론해 만든다(앵커 id 포함). `data/pages-index.json` 만 쓰면
  색인에 없는 repo(lab-followup 등)가 빠져 모델이 NEW 를 잘못 고른다.
- confidence < 0.6 은 적용하지 않고 큐에 제안만 남긴다.
- 발행 규칙(공개/비공개/skip)은 `data/onenote-rules.json` 이 SSOT다.

## 4. 기존 페이지 — **덧붙이지 말고 통합 편집(elaborate)**

```bash
python3 scripts/onenote_elaborate.py --all     # 단원(section/h2 블록) 단위 교체
python3 scripts/onenote_remerge.py             # (1회성) 이미 덧붙여진 페이지 소급 정리
```

- 대상 페이지의 **단원(section) HTML** 과 원노트 내용을 함께 모델에 주고, 새 사실을 **기존 단원의 제자리**에
  녹여 넣은 단원 HTML 을 받는다. 페이지 아래에 원노트 복사본을 쌓는 방식은 금지.
- 페이지에 이미 `onenote` 부록(마커 구간)이 쌓여 있으면 그것을 흡수 대상 원문으로 쓰고, 흡수 후
  **그 단원을 삭제**한다.
- 검증: 기존 사실(숫자·약물명·고유명사) **보존율 90%** 미만인 단원은 적용하지 않는다(모델이 내용을 흘렸을 때 방어).
- 출력 `updates[] = {repo,file,sections:{단원id: 새 HTML}, remove_sections:["onenote"]}`.
- 신규 주제가 어느 단원에도 안 맞으면 그때만 h3 소절로 가장 가까운 단원에 넣는다.
- 모델은 **GitHub Actions 에서 호출하는 것을 우선**한다(키는 secret). Actions 로 돌릴 수 없을 때만 로컬.
- 페이지 구조가 두 가지다: `<section aria-labelledby>` 형과 `<main>` 안 `<h2 id>` 나열 형.
  `scripts/onenote_html.py` 의 `page_blocks()` 가 둘 다 처리한다(교체 단위 = 블록 전체).
- 단원이 아예 없는 스텁 페이지(예: endoscopy-study/cfs/index.html)는 통합 대상이 아니라 **새 페이지**로 만든다(§5).

## 4-0. 문장 다듬기 — 병합문은 그대로 내보내지 않는다

```bash
python3 scripts/onenote_polish.py --merged        # 문단 단위(중복·장문·공허한 수식만)
python3 scripts/onenote_rewrite.py --file <repo/file>   # 단원 재작성(사실 보존 검증 통과 시만)
```

- 편집 규칙 SSOT: **`data/editorial-style.md`** (design-craft 의 AI 티 제거 원칙을 임상 문서에 옮긴 것).
  Actions 는 같은 파일을 **비공개 `pages-jobs/skills/editorial-style.md`** 에서 읽는다.
- 검증: 수치 100% · 영문 98% · 한글 87% 이상 보존, 길이 ±15% 일 때만 적용. 미달이면 원문 유지.
- 전체 단원 재작성은 사실 손실이 커서(실측 영문 66~74%) **검증 통과분만** 반영한다.
- Actions 실행: `pages-jobs` 의 **문장 편집(edit.yml)** 워크플로(`mode=polish|rewrite`, `files=` 입력).

## 4-1. 그림(이미지) 반입 — 본문 단원 안에 넣는다

```bash
python3 scripts/onenote_images_pull.py --max-per-page 6 --out /tmp/opencode/images.json
```

- 원노트 페이지 HTML(Graph)의 `<img>` 리소스를 access token 으로 내려받아 repo `assets/onenote/<page-slug>-NN.<ext>` 로 저장한다.
- 배치: 그림 앞뒤 문맥(220자)과 **모든 대상 파일의 단원 텍스트**를 키워드 점수로 비교해 **가장 맞는 단원 하나에만** 넣는다.
  같은 그림을 여러 페이지에 복사하지 않는다.
- 캡션: `<img alt>` 가 6~60자면 그것을, 아니면 문맥 첫 구절. 원노트 alt 는 글자마다 공백이 있으므로 붙여서 쓴다.
- job 에는 `assets[] = {path, base64}` 로 실어 보내고 Actions 가 커밋한다(그림 파일을 로컬에만 두고 끝내지 않는다).
- 어느 단원에도 맞지 않으면 넣지 않는다. 페이지 끝에 몰아넣지 않는다.
- 원노트 API 는 429 를 자주 준다 → 1.2초 간격 + 429 면 15·30·45초 백오프.
- 환자 식별 정보가 보이는 그림은 공개 repo 에 넣지 않는다(§5 공개/비공개 판단).

## 5. 신규 페이지

`mapping-model.json` 의 `repo: "NEW"` 세그먼트는 새 페이지로 만든다.

- **무조건 `design-craft`(+`site-craft`)로 디자인**한다. 기존 시리즈가 있으면 그 토큰·CSS를 재사용해 톤을 맞춘다.
- **공개/비공개 판단**
  - 환자정보·검사 원본·사례, 금융·수입, 저작권 있는 자료 → **비공개 Cloudflare Pages** (`publish.py --private`)
  - 그 외 학습·임상 참고자료 → 공개 GitHub Pages
- 새 repo 는 `gh repo create` 후 `styles/` 복사, `create: true` + `index_link` 로 job 에 넣는다.
- `design-lint.py` 0 HIGH, `page-check.sh` 0 HIGH/0 MED 를 통과해야 제출한다.

## 6. c — 제출 (적용은 Actions)

```bash
python3 scripts/onenote_submit.py /tmp/opencode/patch-existing.json --page "<대표 페이지>"
```

- `kimhs950627/pages-jobs`(비공개) `jobs/` 에 JSON 커밋. 로컬에서는 저장소를 고치지 않는다.
- 허브 스크립트를 고쳤으면 **먼저 push** 한다(Actions 는 hub master 의 스크립트를 쓴다).
- 결정 기록도 같은 repo `plan/<UTC>.json` 에 남긴다(페이지·문단·repo/file·confidence·모델).

## 7. d — Actions 가 적용하고 허브에 리스팅

`pages-jobs` 의 `apply.yml` 이 순서대로 한다.

1. job 마다 `onenote_apply.py` — 기존 파일은 앵커/구간 교체, `create: true` 는 새 파일 생성(+index 링크)
2. 대상 repo 커밋·push (public → GitHub Pages 자동, private → Cloudflare 재배포)
3. **허브 색인 갱신** — `kimhs950627.github.io` 를 checkout 해 `build-pages.py` 실행 후 커밋·push
4. 소비한 job 삭제 + `status/<ts>.txt` 기록

## 8. e — 웹훅 보고

Actions 가 `notify.py send` 로 Discord 웹훅 1회: 페이지명 · 원노트 수정일시 · 대상 repo/file ·
GitHub Pages/Cloudflare 구분 · URL · 성공/실패. 이 스레드에는 `기록됨 (제출 N건)` 한 줄만 쓴다.

## 금지

- 마커·앵커 밖의 내용을 고치지 않는다.
- 원노트에 없는 내용을 지어내지 않는다.
- 본문 전문을 컨텍스트에 붙이지 않는다(파일로 처리).
- 처방·용량이 들어간 내용은 적용 전에 사용자 확인을 받는다.
- 개인 재무·행정·환자 사례는 발행하지 않는다(skip).
