---
title: 원노트 → 발행물 동기화
description: >
  Managed by a kimaki routine. Do not move or delete this file
  without also updating the routine (kimaki routine list / kimaki routine edit).
---

# 원노트 변경을 발행 페이지에 반영한다 (매일 새벽)

작업 폴더: `/home/kimhs950627/projects/ghpages-hub` · 스킬: `pages-publish`

이 job 은 다섯 가지를 반드시 끝낸다.

- **a. 수정 내역 catch** — 3개 노트북 전수, 본문 해시 비교(시각은 안 믿는다)
- **b. 매핑 결정** — Command Code API `deepseek/deepseek-v4.1-flash` + `reasoning_effort: low`
- **c. repo 전달** — 비공개 staging `pages-jobs` 에 job(+plan) 커밋
- **d. 페이지 생성·수정 + 허브 리스팅** — GitHub Actions 가 적용, 이어서 `kimhs950627.github.io` 색인 갱신
- **e. 웹훅 noti** — Actions 가 `notify.py send` 1회

## 0. 모델 고정

판단은 **`deepseek/deepseek-v4.1-flash` + `reasoning_effort: low`** 만 쓴다(`onenote_decide.call`).
키: `$COMMANDCODE_API_KEY` 또는 `~/.config/pfarm/commandcode.env`. 다른 모델·subagent 금지.
API 가 죽으면 kimaki 세션(같은 모델)으로 폴백하고 그 사실을 한 줄 남긴다.

## 1. a — 변경 감지

```bash
python3 scripts/onenote_graph.py          # 3개 노트북 · 120쪽/일 순환 + pinned 매일
python3 scripts/onenote_graph.py --full   # 전수(30분+)
```

- `changed` = 내용이 실제로 바뀐 페이지. `firstSeen` 은 첫 기준선일 뿐이다.
- `failed` 는 Graph 오류 — **성공으로 보고하지 않는다**.
- Graph 오류 문구(`Failed to get page content…`)는 절대 본문으로 쓰지 않는다(해시하면 영구 unchanged).
  실패하면 제목 조회로 폴백하고, 그래도 실패하면 `failed` 에 남긴다.

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
python3 scripts/onenote_elaborate.py --all     # 단원 단위 교체
```

- 대상 페이지의 **단원(section) HTML** 과 원노트 내용을 함께 모델에 주고, 새 사실을 **기존 단원의 제자리**에
  녹여 넣은 단원 HTML 을 받는다. 페이지 아래에 원노트 복사본을 쌓는 방식은 금지.
- 페이지에 이미 `onenote` 부록(마커 구간)이 쌓여 있으면 그것을 흡수 대상 원문으로 쓰고, 흡수 후
  **그 단원을 삭제**한다.
- 검증: 기존 사실(숫자·약물명·고유명사) **보존율 90%** 미만인 단원은 적용하지 않는다(모델이 내용을 흘렸을 때 방어).
- 출력 `updates[] = {repo,file,sections:{단원id: 새 HTML}, remove_sections:["onenote"]}`.
- 신규 주제가 어느 단원에도 안 맞으면 그때만 h3 소절로 가장 가까운 단원에 넣는다.
- 모델은 **GitHub Actions 에서 호출하는 것을 우선**한다(키는 secret). Actions 로 돌릴 수 없을 때만 로컬.

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
