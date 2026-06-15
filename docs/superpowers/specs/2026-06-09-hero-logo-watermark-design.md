# 설계: Hero Zone 로고 워터마크

> 작성일: 2026-06-09
> 목적: 종목 상세 드로워의 "요약"(Hero Zone) 섹션에 회사 로고를 은은한 배경 워터마크로 깔아 브랜드 인지성과 시각적 완성도를 높인다.
> 범위: US 종목 + 로고가 있는 경우만. KR·로고 없는 종목은 변화 없음.

---

## 0. 배경

드로워 헤더에 회사 로고(흰 타일)를 노출하는 작업을 마친 뒤, "로고를 배경으로도 활용"하자는 요청. 비주얼 컴패니언으로 3방향(워터마크 / 블러 풀블리드 / 색 추출 그라데이션)을 비교해 **워터마크** 방향을 선택했고, 위치·크기·농도 변형 중 **우상단 코너(작게·기울임)**을 확정했다.

## 1. 확정 시각 스펙

| 속성 | 값 |
|---|---|
| 위치 | Hero Zone(`#dp-hero-zone`) 우상단 코너, 일부 화면 밖으로 블리드 |
| 크기 | 150 × 150 px |
| 투명도 | 0.07 (7%) |
| 회전 | -8° |
| z축 | 콘텐츠 뒤 (`z-index:0`), 클릭 불가(`pointer-events:none`) |
| 클리핑 | Hero Zone 경계 밖은 `overflow:hidden`으로 잘림 |

## 2. 범위 / 정책

- **US 종목 + 로고 존재 시에만** 표시.
- 판별: 티커가 `.` 미포함 && 전체 숫자 아님 (`_is_us_ticker`와 동일 정책, 헤더·목록 로고와 일관).
- 로고 URL 404 → `img.onerror`로 워터마크 제거(빈 배경).
- KR·로고 없는 종목 → 워터마크 미생성(기존 Hero Zone 그대로).

## 3. 로고 출처

티커에서 **Finnhub 정적 URL 패턴**으로 직접 구성한다 — 목록 행 로고와 동일 방식:
`https://static2.finnhub.io/file/publicdatany/finnhubimage/stock_logo/{TICKER}.png`

→ 드로워를 **여는 즉시** 표시된다(센티먼트 lazy-load `/api/sentiment` 완료를 기다리지 않음). API 호출 0.

## 4. 아키텍처 (3곳, 소규모)

### 4.1 `web_app/static/app.js`
- 신규 함수 `_renderHeroWatermark(d)`:
  - `#dp-hero-zone`의 **첫 자식**으로 `<img class="dp-hero-watermark" id="dp-hero-wm">`를 주입/갱신.
  - US·로고 패턴이면 `img.src` 설정, 아니면 기존 워터마크 제거.
  - `img.onerror = () => img.remove()` (로고 없는 종목 graceful).
- 호출: `_populatePanelDetail(d)` (app.js ~line 2920, 드로워 populate 시점·`d.Ticker` 확보) 안에서 호출.

### 4.2 `web_app/static/scanner.css`
- `.dp-hero-zone`에 `position:relative; overflow:hidden;` 추가(기존 `padding/background/border-bottom` 유지).
- `.dp-hero-zone > *:not(.dp-hero-watermark) { position:relative; z-index:1; }` — 실제 콘텐츠를 워터마크 위로(absolute 형제가 static 콘텐츠를 덮는 것 방지).
- `.dp-hero-watermark { position:absolute; top:-22px; right:-18px; width:150px; height:150px; object-fit:contain; opacity:.07; transform:rotate(-8deg); pointer-events:none; z-index:0; }`

## 5. 데이터 흐름

```
종목 클릭 → openDetail → _populatePanelDetail(d)
   → _renderHeroWatermark(d)
      → US·로고 패턴? → #dp-hero-zone 첫 자식에 <img> 주입 (즉시)
                       → onerror 시 제거
      → 아니면 기존 워터마크 제거
```

## 6. 엣지 케이스 / 리스크

- **overflow:hidden 부작용**: Hero Zone 안에 의도적으로 넘치는 요소(툴팁/드롭다운 등)가 있으면 잘릴 수 있음 → 시각 확인 필수. (현재 Hero Zone은 패딩 박스 + 정적 콘텐츠라 위험 낮음.)
- **다크모드 농도**: 7%가 다크 배경에서 묻히거나 밝은 로고가 튀면 구현 단계에서 미세조정(필요 시 `opacity` 또는 `mix-blend-mode` 검토).
- **종목 전환 시 잔상**: 같은 `id="dp-hero-wm"` 재사용 → 매 populate마다 `src` 갱신/제거로 이전 종목 로고가 남지 않도록.
- **캡처/클론**: 상세 캡처(`captureDetail`)가 Hero Zone을 클론할 때 워터마크 포함 여부 — 포함되어도 무방(배경 장식). 특별 처리 불필요.

## 7. 테스트

- `node --check web_app/static/app.js` (문법).
- 시각 확인: US 로고 종목(워터마크 표시) / KR 종목(없음) / 로고 없는 US 티커(onerror 제거) / 종목 전환 시 잔상 없음 / 기존 Hero 콘텐츠 안 잘림.

## 8. 안 하는 것 (YAGNI)

- 블러 풀블리드, 로고 색 추출 그라데이션(이번 범위 밖 — 선택되지 않음).
- 헤더(60px)에 워터마크(공간 부족).
- 로고 색 동적 추출/테마링(별도 주제).
