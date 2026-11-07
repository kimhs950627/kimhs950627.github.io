# 페이지 아카이브

`kimhs950627` 이 깃허브 페이지로 발행한 페이지를 한자리에 모은 색인.
<https://kimhs950627.github.io/>

## 어떻게 동작하나

```
GitHub Actions (매일)
  04:00  sync.yml          발행 상태 재판정 (공개=GitHub Pages / 비공개=Cloudflare Pages+Access)
  05:00  track.yml         scripts/track.py
          ├─ 저장소 전부(비공개 포함)를 훑어 마지막 커밋·공개 범위·Pages 상태를 지난 스냅숏과 비교
          ├─ 새 저장소·새 커밋·공개 전환·Pages 켜짐/꺼짐을 data/activity.json 에 남긴다 (21일 보관)
          ├─ 색인에 없는 페이지: 공개면 curated.json 에 자동 등재
          └─ 비공개면 publish.py 로 Cloudflare Pages + Access 발행 (소유자 이메일만 열람)
  06:00  update-pages.yml  data/pages.json 재생성
  07:00  noti-digest.yml   밤사이 결과를 디스코드로 한 번에

index.html + scripts/app.js
   └─ data/pages.json · data/activity.json 을 읽어 '갈래 탭'(최근 변경 · 임상 학습 · 연구 설계 · 도구 · 여행)으로 그린다
```

새 저장소에 GitHub Pages 를 켜면 **다음 실행에서 자동으로 목록에 붙는다.** 손볼 것은 없다.

## 비공개 저장소

페이지 소스(`index.html`, `docs/index.html`)가 있는 비공개 저장소는 색인에 **잠금 상태로** 실린다.
내용은 Cloudflare Pages 에 배포되고 Cloudflare Access 정책이 `kimhs950627@gmail.com` 만 허용하므로,
계정으로 로그인하지 않으면 제목·설명만 보이고 본문은 열리지 않는다.

```bash
python3 scripts/track.py --publish-private   # 비공개 후보를 지금 발행 (평소에는 track.yml 이 한다)
python3 scripts/publish.py approve <email>   # 다른 사람에게 열어 줄 때
```

색인에 싣고 싶지 않은 저장소는 `data/track-ignore.json` 의 `repos` 에 적는다.
전체 스냅숏(`data/repos-state.json`)에는 비공개 저장소 이름이 들어가므로 `.gitignore` 로 빼고
Actions 캐시에만 둔다. 공개되는 `data/activity.json` 에는 비공개 저장소 이름·커밋 메시지를 넣지 않는다.

## 문구·분류 고치기

`data/curated.json` 만 고치면 된다. 여기 없는 저장소는 자동으로 `그 밖`에 들어가고,
제목과 설명은 그 페이지 자신의 `<title>`·`meta description` 을 쓴다.

```json
"endoscopy-study": {
  "category": "study",
  "order": 10,
  "title": "내시경 워크북 — EGD · CFS",
  "note": "상부위장관내시경과 대장내시경 학습 워크북."
}
```

분류(`category`)는 `category_order` 에 있는 id 중 하나를 쓴다. `order` 는 그룹 안 정렬 순서다.

그 페이지 안의 워크북·장 단위로 바로 걸고 싶으면 `links` 를 붙인다. 빌드할 때 실제로 열리는지(200) 확인하고 죽은 링크는 뺀다.

```json
"links": [
  { "label": "CT-HMM · HFmrEF 워크북", "url": "https://kimhs950627.github.io/ml_dl_study_material/workbooks/ct-hmm-hfmref/", "kind": "workbook" }
]
```

`kind: "workbook"` 을 달면 빌드할 때 그 디렉터리의 `reviewed/*.html` 을 먼저 찾아 그리로 걸고,
없으면 `draft/*.html`, 둘 다 없으면 원래 URL 을 쓴다. 칩에는 `검토본` / `초안` 표시가 붙는다.

## 직접 돌려보기

```bash
GITHUB_TOKEN=$(gh auth token) python3 scripts/build-pages.py
python3 -m http.server 8901 --directory .
# http://127.0.0.1:8901/
```

## 구성

| 경로 | 역할 |
|---|---|
| `index.html` | 페이지 골격 (문구는 데이터에서 온다) |
| `styles/tokens.css` | 색·타입·간격·모션 토큰 |
| `styles/site.css` | 레이아웃 |
| `scripts/app.js` | `data/pages.json` → 목록 렌더 |
| `scripts/build-pages.py` | 페이지 수집·생성 |
| `data/curated.json` | 손으로 고치는 제목·설명·분류 |
| `data/pages.json` | 자동 생성물 (직접 고치지 않는다) |
| `.github/workflows/update-pages.yml` | 매일 갱신 |

## 디자인

따뜻한 종이색(`#f5f0e6`) 위 잉크색 본문에 벽돌색 악센트 하나. 제목은 Gowun Batang,
본문은 IBM Plex Sans KR, 숫자·주소는 IBM Plex Mono. 비대칭 2단(왼쪽 고정 색인 + 오른쪽 목록).
`prefers-reduced-motion` 분기 있음.
