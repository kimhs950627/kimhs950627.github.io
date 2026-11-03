# 페이지 아카이브

`kimhs950627` 이 깃허브 페이지로 발행한 페이지를 한자리에 모은 색인.
<https://kimhs950627.github.io/>

## 어떻게 동작하나

```
GitHub Actions (매일 06:00 KST)
   └─ scripts/build-pages.py
        ├─ /users/kimhs950627/repos 로 공개 저장소 목록을 받고
        ├─ https://kimhs950627.github.io/<repo>/ 가 실제로 열리는지 확인하고 (404 화면은 제외)
        ├─ 각 페이지의 <title>·meta description 을 읽어
        └─ data/curated.json 의 수동 제목·설명·분류와 합쳐 data/pages.json 을 쓴다
              └─ index.html 이 그 JSON 을 읽어 목록을 그린다
```

새 저장소에 GitHub Pages 를 켜면 **다음 실행에서 자동으로 목록에 붙는다.** 손볼 것은 없다.

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
  { "label": "CT-HMM · HFmrEF 워크북", "url": "https://kimhs950627.github.io/ml_dl_study_material/workbooks/ct-hmm-hfmref/" }
]
```

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
