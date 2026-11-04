---
title: 원노트 → 발행물 동기화
description: >
  Managed by a kimaki routine. Do not move or delete this file
  without also updating the routine (kimaki routine list / kimaki routine edit).
---

# 원노트 변경을 발행된 페이지에 반영한다

작업 폴더: `/home/kimhs950627/projects/ghpages-hub` · 스킬: `pages-publish`

## 0-0. 모델 고정 (필수)

판단(어느 파일·어느 구간)은 **`deepseek/deepseek-v4.1-flash` + `reasoning_effort: low`** 만 쓴다.
`scripts/onenote_decide.py` 가 **Command Code API**(`https://api.commandcode.ai/provider/v1/chat/completions`)를
직접 호출하며, CLI 는 쓰지 않는다. 키는 `$COMMANDCODE_API_KEY` 또는 `~/.config/pfarm/commandcode.env`.

- 다른 모델·subagent 로 우회하지 않는다. 모델을 바꿔야 하면 사용자에게 먼저 묻는다.
- API 가 실패(403·타임아웃)하면 같은 모델의 kimaki 세션으로 판단하고, 그 사실을 답변에 한 줄 남긴다.

## 0. 원칙

- **소스는 Graph(웹) 1순위.** 데스크톱 COM 은 캐시가 덜 동기화돼 본문이 잘려 있다(실측: 같은 페이지
  로컬 8,243자 vs Graph 62,759자 — 진정내시경 파트가 로컬에 없음). COM 은 오프라인 폴백으로만 쓴다.
- **타임스탬프는 믿지 않는다.** 같은 편집이 Graph 는 9-01, 로컬은 11-03 15:52 로 나왔다. 변경 감지는
  `scripts/onenote_graph.py` 의 **본문 해시 비교**로만 한다.

- **Graph(MCP) 대신 로컬 원노트 COM 을 쓴다.** MCP 는 429 레이트리밋에 걸리고 `lastModifiedDateTime` 이
  낡아 최근 수정을 놓친다(실측: GI 학회 정리 2026-11-03 수정이 MCP 에선 9월로 보였다).
- **변경 감지는 코드, 판단은 모델, 적용은 스크립트.** 본문은 파일로만 다루고 컨텍스트에 싣지 않는다.
- 확신이 없으면 **큐에 제안만** 남기고 파일을 건드리지 않는다.

## 1. 변경 감지 (본문 해시, Graph)

```bash
python3 scripts/onenote_graph.py --notebook Books_for_Meds --limit 40   # 하루 40쪽씩 순환
python3 scripts/onenote_graph.py --page-id <id>                         # 특정 페이지만
```

`changed` 에 실제로 내용이 바뀐 페이지만 나온다. 처음 한 바퀴는 전부 changed 로 뜨는 게 정상이다
(해시가 없으니까). 300자 미만은 건너뛴다. Graph 가 429 면 로컬 COM 으로 폴백하고 그 사실을 남긴다.

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

## 3. 후보 좁히기 (토큰 0)

```bash
python3 scripts/onenote_route.py --title "<페이지 제목>" --section "<섹션>" \
  --excerpt "$(head -c 400 data/onenote-raw/<id>.txt)" > /tmp/opencode/cand.json
```

## 3-1. 판단 (API, 모델 고정)

```bash
python3 scripts/onenote_decide.py --title "<페이지 제목>" --section "<섹션>" \
  --text data/onenote-raw/<id>.txt --candidates /tmp/opencode/cand.json \
  --out /tmp/opencode/onenote-patch.json --min-confidence 0.7
```

- 종료코드 **0** = patch 생성 → 4단계로.
- 종료코드 **3** = 확신 부족(`/tmp/opencode/onenote-proposal.json` 에 후보·이유 저장) → 큐에 알림만 넣고 끝낸다.
- 후보 밖 파일을 고르면 코드가 거부한다.

## 3-2. 기존 단원에 써넣기 (단순 append 금지)

새 섹션을 페이지 끝에 붙이지 말고, **기존 장의 해당 단원 안**에 넣는다. patch 의 update 에
`region` 대신 앵커를 쓴다.

```json
{"repo": "endoscopy-study", "file": "egd/00-총론.html", "anchor_id": "s-진정-sedation", "html": "…"}
{"repo": "lab-followup", "file": "liver.html", "anchor_heading": "지방간을 진단받았을 때", "html": "…"}
```

- `anchor_id` 는 그 페이지의 `<h2 id="…">`, `anchor_heading` 은 제목 텍스트.
- 삽입 위치는 앵커 단원의 끝(다음 `<h2>` 직전). 두 번 돌려도 중복되지 않는다(마커로 교체).
- 어느 단원인지 애매하면 붙이지 말고 제안만 남긴다.

## 3-3. 제출 전 확인 (필수)

Actions 는 **제출 시점의 hub `master` HEAD** 에 있는 스크립트를 쓴다. 허브 스크립트를 고쳤으면
**먼저 push 하고 나서** job 을 제출한다. 안 그러면 앵커가 무시되고 파일 끝에 부록으로 붙는다(실제 발생).

## 4. 큐에 제출 (적용은 Actions 가 한다)

```bash
python3 scripts/onenote_submit.py /tmp/opencode/onenote-patch.json --page "<페이지 제목>"
```

- 비공개 staging `kimhs950627/pages-jobs` 의 `jobs/` 에 들어가고, 그 저장소의 **Actions 가 적용·배포**한다
  (대상 repo 커밋·push → private 이면 Cloudflare 재배포 → 소비한 job 삭제 → `status/` 기록).
- 로컬에서는 **적용하지 않는다.** 세션이 짧아지고, PC 가 꺼져도 Actions 가 이어받는다.
- 원노트 본문이 공개 저장소에 남지 않도록 staging 은 private 이다.

## 5. 마무리

**보고는 웹훅이 한다. 이 스레드에 결과를 쓰지 않는다.**

- 적용·배포 후 `pages-jobs` 의 Actions 가 `notify.py send` 로 **Discord 웹훅** 1회 발송:
  원노트 페이지명 · 원노트 수정일시 · 대상 `repo/file` · GitHub Pages/Cloudflare Pages 구분 · URL · 성공/실패.
- 로컬 세션 답변은 `기록됨 (제출 N건)` 한 줄만. 상세 설명·목록을 여기에 쓰지 않는다.
- 제출할 게 없으면 아무 것도 넣지 않고, 답변도 한 줄로 끝낸다.

job JSON 의 `source` 에 **`page` 와 `modified`(원노트 수정일시)를 반드시 넣는다** — 웹훅 본문이 이 값을 쓴다.

## 금지

- 마커 밖의 내용을 고치지 않는다.
- 원노트에 없는 내용을 지어내지 않는다.
- 본문 전문을 컨텍스트에 붙여 넣지 않는다(파일로 처리).
- 처방·용량이 들어간 내용은 적용 전에 사용자 확인을 받는다.
