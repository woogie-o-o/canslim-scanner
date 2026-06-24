# 드로어 핸드드로잉 차트 통합 설계

**날짜**: 2026-06-22
**상태**: 승인됨

---

## 목표

종목 클릭 시 열리는 드로어(scanner.html)에 `handdrawn_renderer.py`가 생성하는 4축 핸드드로잉 차트를 삽입하고, 기존 SVG 스파크라인을 제거한다.

---

## 레이아웃

```
[종목명]                    [닫기]
┌──────────────────────────────────┐
│      핸드드로잉 차트              │
│  (aspect-ratio 12/5.6 고정)      │
└──────────────────────────────────┘
[가격]  [등락률]  [RS등급]
[RSI 공포·탐욕 바]
[📈 타이밍 분석 텍스트]
[회사 정보, 경쟁사 비교 등...]
```

- 차트는 드로어 헤더(`dp-about-box`) 직후, 히어로 stats 이전에 위치
- 기존 `dp-spark-panel` (SVG 스파크라인 + 52주 고가 바 + 거래량/시총) 제거
- 히어로 stats에서 RSI 제거 (차트 3번째 패널에 이미 표시됨)
- 남는 stats: 가격, 등락률, RS등급 3개

---

## 차트 스타일

- `width: 100%`, `aspect-ratio: 12 / 5.6` — 레이아웃 점프 방지
- `opacity: 0.92`, `border-radius: var(--radius)`
- `border-bottom: 1px solid var(--border)`
- 로드 완료 시 fade-in **150ms** (Toss 기준 빠른 트랜지션)
- 상단은 헤더에 밀착 (패딩 없음), 하단만 `border-radius` 적용

---

## 스켈레톤 로딩

- 차트 영역과 동일한 `aspect-ratio: 12 / 5.6` 박스
- 배경: `var(--border)` 색상
- 애니메이션: opacity 펄스 `1.2s ease-in-out infinite`
- 차트 완료 시: 스켈레톤 숨기기 → 이미지 fade-in

---

## 데이터 흐름 (접근법 C)

### 백엔드 — `app.py`

`_warm_four_axis()` 함수에서 `want_chart=False` → `want_chart=True`로 변경:

- 스캔 완료 후 상위 20개 종목을 `c1` (차트 포함) 캐시로 워밍
- 캐시 키: `{ticker}:{market}:{timeframe}:c1`
- 기존 `c0` 워밍 루프를 `c1`으로 교체

### 프론트엔드 — `app.js`

`loadDpFourAxis(ticker)` 함수 수정:

1. fetch URL에서 `chart: '0'` 파라미터 제거 (기본값 `chart: '1'`)
2. 응답 `d.chart` (base64 PNG) → `dp-hd-chart` img.src 설정
3. 이미지 로드 완료(`onload`) 시 스켈레톤 숨기고 img fade-in
4. 에러 시 스켈레톤 숨기고 `dp-fouraxis-error` 표시

기존 텍스트 분석 렌더링(phase, stars, observation, 4축 scores) 로직은 변경 없음.

### HTML — `scanner.html`

**제거:**
- `dp-spark-panel` 블록 전체
- `dp-hero-right` div (내용이 비므로)
- 히어로 stats에서 RSI(`dp-thermo-label`) 셀

**추가:**
```html
<!-- dp-hero-card 내부, dp-hero-flex 바로 위 (스크롤 영역 최상단) -->
<div id="dp-hd-chart-wrap" class="dp-hd-chart-wrap">
  <div id="dp-hd-skeleton" class="dp-hd-skeleton"></div>
  <img id="dp-hd-chart" src="" alt="차트 분석" style="display:none;" />
</div>
```

### CSS — `scanner.css`

```css
.dp-hd-chart-wrap {
  position: relative;
  width: 100%;
  aspect-ratio: 12 / 5.6;
  overflow: hidden;
}

.dp-hd-skeleton {
  position: absolute;
  inset: 0;
  background: var(--border);
  animation: dp-hd-pulse 1.2s ease-in-out infinite;
}

@keyframes dp-hd-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

#dp-hd-chart {
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0.92;
  border-bottom: 1px solid var(--border);
  transition: opacity 150ms ease;
}
```

---

## 제거 대상 정리

| 대상 | 위치 | 판단 |
|------|------|------|
| `dp-spark-panel` (SVG 스파크라인) | scanner.html | 차트로 대체 — 제거 |
| 52주 고가 대비 바 | scanner.html | 차트 1패널에서 확인 가능 — 제거 |
| 거래량 비율 stat | scanner.html | 차트 2패널에서 확인 가능 — 제거 |
| 시총 stat | scanner.html | 하단 회사 섹션에서 확인 가능 — 제거 |
| RSI stat (히어로) | scanner.html | 차트 3패널에서 확인 가능 — 제거 |
| `loadFourAxis()` chart:0 파라미터 | app.js | c1으로 통합 — 제거 |

---

## 영향 범위

- `scanner.html` — HTML 구조 변경
- `web_app/static/scanner.css` — 스켈레톤 CSS 추가
- `web_app/static/app.js` — `loadDpFourAxis` 수정
- `web_app/app.py` — `_warm_four_axis` 수정
- `handdrawn_renderer.py`, `four_axis_analyzer.py` — 변경 없음
- `detail.html` — 변경 없음 (별도 페이지, 현재 미사용)
