# 원노트 인벤토리와 신규 페이지 제안

`data/onenote-inventory.json` — Books_for_Meds 161쪽(섹션 20개)의 제목·섹션·글자수·소제목·키워드·도입부.
본문 XML 은 로컬(`/tmp/opencode/onenote-html`, 140MB)에만 있고 저장소에는 올리지 않는다.

## 지금 상태

- 161쪽 중 **내용이 있는 쪽은 44개**(300자 초과), 나머지는 제목만 있는 빈 페이지(틀만 잡아 둔 상태).
- 총 5.9만 자. 큰 덩어리는 학회 정리(3개), 외래 프로토콜(4개), 당직(2개), 근골격·통증(3개).

## 이미 발행 중인 페이지 (중복 금지)

- `ktirads-study` — 갑상선 결절(K-TIRADS)
- `endoscopy-study` — 상부위내시경 워크북
- `ksecho-research` — 심초음파 연구·도플러 프로토콜
- `fundoscopy-study` — 안저
- `neurology-outpatient-reference` — 신경과 외래(비공개)
- `ml_dl_study_material`, `ai-research-copilot`

## 제안: 새로 만들 페이지 5개 + 기존 확장 2개

### 1. `lab-followup` (P0) — "검진에서 흔한 lab 이상, 다음에 뭘 할까"

사용자 예시(혈뇨·단백뇨·간기능)를 한 페이지로 묶는 안.

- 혈뇨·단백뇨·eGFR — `Nephro 학회 정리` (3,066자)
- 간기능·MASLD·바이러스간염 — `GI 학회 정리` (6,741자)
- TSH·갑상선기능 — `Endo 학회 정리` (10,966자)
- 전해질·CBC·요검사 이상 — 해당 없음(새로 쓸 자리)

구성: 항목별 ①흔한 원인 ②재검 시점 ③추가 검사 ④의뢰 기준. 기존 페이지와 겹치지 않음.

### 2. `primary-care-protocol` (P0) — 외래 프로토콜 묶음

- 고혈압 `[의사용] 고혈압 외래 Protocol v1.0` (1,693자)
- 고지혈증 `[의사용] 고지혈증 외래 Protocol v1.0` (1,970자)
- 당뇨 `외래 당뇨 protocol` (3,922자)
- 소화기 `일차의료 소화기 약속처방 프로토콜` (빈 페이지)
- 환자 설명문 `[환자용] 혈압·콜레스테롤·혈당 안내문 v1.2` (812자)

구성: 질환별 ①진단 기준 ②약물 시작·조정 ③모니터링 주기 ④환자에게 건네는 1장. **처방 내용이므로 발행 전 검토 필요.**

### 3. `ward-nights` (P1) — 당직·입원 케어

- `혈당 관리` (4,246자), `내과병동 당직 어시스트 텍스트` (692자)

구성: 야간 호출 빈도순(고혈당/저혈당 → 섬망 → 전해질 → 발열). 지금은 2쪽뿐이라 골격만 만들고 이후 채우는 방식.

### 4. `msk-us` (P1) — 근골격 초음파·통증

- 어깨·무릎·허리: `근골격계 초음파 공부`(대부분 빈 페이지), `근골격계 공부 - HD`(참관 951자)
- 통증 기전: `통증의학 정리` (1,569 + 1,091자)
- 술기: TPI·hydrodissection (122자)

구성: 부위별 ①정상 해부 ②병변 소견 ③주사 위치. **내용이 가장 적어 신규 작성 비중이 큼.**

### 5. `aesthetics-clinic` (P2, 비공개 권장)

- 보톡스·필러·레이저·스킨부스터·IVNT·줄기세포 (14쪽, 합 6,000자 내외)

가격표·보험 항목이 섞여 있어 **비공개(Cloudflare Access)** 발행을 권한다.

### 기존 페이지 확장 (새 저장소 만들지 않음)

- `ksecho-research` ← `Echo_Theory_in_A_nutshell`(Routine Views 1,471자), `Echo_Measure`
- `ktirads-study` ← `갑상선초음파 공부`(Views 1,320자), `경동맥초음파 공부`
- `endoscopy-study` ← `EGD protocol (personal)`(3,371자), `검진 protocol/DDx`

## 우선순위 제안

1. `lab-followup` — 사용자 예시와 일치, 기존 발행물과 안 겹침, 재료 3덩어리 확보
2. `primary-care-protocol` — 재료는 충분하나 처방 검토가 필요
3. `ward-nights` — 골격만 먼저
4. `msk-us` — 신규 작성 비중 큼
5. `aesthetics-clinic` — 비공개로

## 다음 단계 (승인 후)

- 승인된 항목마다 저장소 생성 → 챕터 골격 → 원노트 본문을 `onenote` 마커 구간으로 주입 → 발행
  (`pages-publish` 스킬: private 은 Cloudflare + Access, public 은 GitHub Pages)
- 각 페이지에 §0 질문(동기화 여부·구간)을 적용해 `data/onenote-map.json` 에 등록
