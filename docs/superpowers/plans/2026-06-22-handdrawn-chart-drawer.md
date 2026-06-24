# 드로어 핸드드로잉 차트 통합 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 종목 드로어(scanner.html)에서 SVG 스파크라인을 제거하고, 핸드드로잉 4축 차트 이미지를 헤더 직후에 전체 폭으로 표시한다.

**Architecture:** BG warm을 `chart:1`(차트 포함)으로 교체해 상위 20개 종목은 캐시 히트로 즉시 표시되게 한다. `loadDpFourAxis`는 단일 fetch로 차트+텍스트를 함께 받아 렌더링하며, 차트 영역은 로딩 중 펄스 스켈레톤을 보여준다.

**Tech Stack:** Flask(Python), vanilla JS, CSS custom properties, matplotlib(서버 렌더링)

## Global Constraints

- CSS 변수: `var(--border)`, `var(--radius)` 등 기존 theme.css 변수만 사용
- JS: vanilla JS, ES2020 이하, no import/export
- 차트 렌더 크기: `width_px=1200, height_px=560, dpi=100` (변경 금지)
- 캐시 키 포맷: `{ticker}:{market}:{timeframe}:c1` (기존 포맷 유지)
- 기존 `loadFourAxis()` (detail.html용) 함수는 건드리지 않는다

---

## 파일 변경 맵

| 파일 | 작업 |
|------|------|
| `web_app/static/scanner.css` | 추가: `.dp-hd-chart-wrap`, `.dp-hd-skeleton`, `#dp-hd-chart` 스타일 |
| `web_app/templates/scanner.html` | 제거: `dp-spark-panel`, RSI stat 셀, `dp-hero-right` / 추가: `dp-hd-chart-wrap` |
| `web_app/app.py` | 수정: `_warm_four_axis` — c0→c1, `want_chart=False`→`True` |
| `web_app/static/app.js` | 수정: `loadDpFourAxis` — chart:0 제거, 스파크라인 블록 제거, 차트 이미지 렌더 추가 |

---

### Task 1: CSS — 스켈레톤 & 차트 스타일 추가

**Files:**
- Modify: `web_app/static/scanner.css` (파일 끝에 추가)

**Interfaces:**
- Produces: `.dp-hd-chart-wrap`, `.dp-hd-skeleton`, `#dp-hd-chart` 클래스/ID — Task 2 HTML이 사용

- [ ] **Step 1: `scanner.css` 파일 끝에 다음 스타일 블록 추가**

`web_app/static/scanner.css` 파일 맨 끝에 아래 내용을 추가한다:

```css
/* ── 드로어 핸드드로잉 차트 ─────────────────────────────── */
.dp-hd-chart-wrap {
  position: relative;
  width: 100%;
  aspect-ratio: 12 / 5.6;
  overflow: hidden;
  background: var(--surface);
}
.dp-hd-skeleton {
  position: absolute;
  inset: 0;
  background: var(--border);
  animation: dp-hd-pulse 1.2s ease-in-out infinite;
}
@keyframes dp-hd-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}
#dp-hd-chart {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0;
  border-bottom: 1px solid var(--border);
  transition: opacity 150ms ease;
}
#dp-hd-chart.is-loaded {
  opacity: 0.92;
}
```

- [ ] **Step 2: 브라우저에서 시각 확인 (Flask 실행 중이면)**

아직 HTML에 요소가 없으므로 CSS 오류 여부만 확인. 다음 단계에서 HTML 추가 후 시각 확인 가능.

- [ ] **Step 3: 커밋**

```bash
git add web_app/static/scanner.css
git commit -m "style(drawer): 핸드드로잉 차트 스켈레톤 & 이미지 CSS 추가"
```

---

### Task 2: HTML — 스파크라인 제거 & 차트 래퍼 삽입

**Files:**
- Modify: `web_app/templates/scanner.html`

**Interfaces:**
- Consumes: `.dp-hd-chart-wrap`, `.dp-hd-skeleton`, `#dp-hd-chart` (Task 1에서 정의)
- Produces: `id="dp-hd-chart-wrap"`, `id="dp-hd-skeleton"`, `id="dp-hd-chart"` — Task 4 JS가 사용

**현재 구조 (scanner.html 262~337번째 줄 기준):**

```html
<div class="dp-hero-flex">
  <div class="dp-hero-left">
    ...
    <div class="dp-hero-stats">
      <div class="dp-hero-stat-cell">현재가...</div>
      <div class="dp-hero-stat-cell">등락률...</div>
      <div class="dp-hero-stat-cell">RSI...</div>       ← 제거 대상
      <div class="dp-hero-stat-cell">RS 등급...</div>
    </div>
  </div>
  <div class="dp-hero-right">
    <div id="dp-spark-panel" ...>...</div>              ← 제거 대상
  </div>
</div>
```

- [ ] **Step 1: `dp-hd-chart-wrap` 블록을 `dp-hero-flex` 위에 삽입**

`scanner.html` 261번째 줄 — `<div id="dp-timing-mini" ...>` 바로 다음, `<div class="dp-hero-flex">` 바로 앞에 삽입:

```html
      <div id="dp-timing-mini" class="dp-timing-mini" style="display:none;"></div>
      <!-- 핸드드로잉 4축 차트 -->
      <div id="dp-hd-chart-wrap" class="dp-hd-chart-wrap">
        <div id="dp-hd-skeleton" class="dp-hd-skeleton"></div>
        <img id="dp-hd-chart" src="" alt="차트 분석" />
      </div>
      <div class="dp-hero-flex">
```

- [ ] **Step 2: RSI stat 셀 제거**

`scanner.html` 287~291번째 줄의 RSI 셀을 제거한다:

제거 전:
```html
            <div class="dp-hero-stat-cell">
              <span class="dp-hero-stat-label">RSI</span>
              <span id="dp-thermo-label" class="dp-hero-stat-value">—</span>
              <span id="dp-thermo-action" class="dp-hero-stat-hint">—</span>
            </div>
```

제거 후: 해당 블록 없음 (현재가 / 등락률 / RS 등급 3개만 남음)

- [ ] **Step 3: `dp-hero-right` + `dp-spark-panel` 블록 제거**

`scanner.html` 299~336번째 줄 전체를 제거한다:

```html
        <div class="dp-hero-right">
      <div id="dp-spark-panel" class="dp-spark-panel--hidden">
        ... (스파크라인 전체 내용) ...
      </div><!-- /dp-spark-panel -->
        </div><!-- /dp-hero-right -->
```

`dp-hero-flex` 닫는 태그(`</div><!-- /dp-hero-flex -->`)는 유지한다.

- [ ] **Step 4: Flask 서버 실행 후 드로어 열어서 레이아웃 확인**

```bash
python web_app/app.py
```

브라우저에서 아무 종목 클릭 → 드로어 최상단에 회색 스켈레톤 박스가 보여야 함.
아직 JS가 차트를 로드하지 않으므로 스켈레톤이 계속 표시되는 것이 정상.

- [ ] **Step 5: 커밋**

```bash
git add web_app/templates/scanner.html
git commit -m "feat(drawer): 스파크라인 제거 및 핸드드로잉 차트 래퍼 삽입"
```

---

### Task 3: 백엔드 — BG warm을 chart:1으로 전환

**Files:**
- Modify: `web_app/app.py:3015-3039`

**Interfaces:**
- Produces: `c1` 캐시 키로 차트 포함 페이로드 워밍 — Task 4 JS fetch가 소비

- [ ] **Step 1: `_warm_four_axis` 함수 수정**

`app.py` 3018~3031번째 줄을 아래와 같이 수정:

변경 전:
```python
def _warm_four_axis(ticker: str, market: str, timeframe: str = "default") -> None:
    """BG 선제 4축 캐시 채우기 — 클릭(드로어) 시 분석 즉시 표시.

    드로어는 차트를 쓰지 않으므로 no-chart(c0) 페이로드를 워밍한다.
    """
    cache_key = f"{ticker}:{market}:{timeframe}:c0"
    ...
        payload, err = _compute_four_axis_payload(ticker, market, want_chart=False)
```

변경 후:
```python
def _warm_four_axis(ticker: str, market: str, timeframe: str = "default") -> None:
    """BG 선제 4축 캐시 채우기 — 클릭(드로어) 시 차트+분석 즉시 표시.

    드로어가 핸드드로잉 차트를 표시하므로 c1(차트 포함) 페이로드를 워밍한다.
    """
    cache_key = f"{ticker}:{market}:{timeframe}:c1"
    ...
        payload, err = _compute_four_axis_payload(ticker, market, want_chart=True)
```

- [ ] **Step 2: 변경 확인 — 로그로 검증**

Flask 서버 재시작 후 스캔 실행. 로그에서 확인:

```
INFO - 4axis pre-warm: KR/005930 OK
```

`c1`으로 캐싱되는지 디버그 로그에서 확인:
```python
# 임시 확인용 — 커밋 전 삭제 불필요 (logging.info가 이미 있음)
logging.info("4axis pre-warm: %s/%s OK", market, ticker)
```

- [ ] **Step 3: 커밋**

```bash
git add web_app/app.py
git commit -m "perf(warm): BG 4축 워밍을 chart:1(차트 포함)으로 전환"
```

---

### Task 4: JS — `loadDpFourAxis` 차트 이미지 렌더링 추가

**Files:**
- Modify: `web_app/static/app.js:4272-4435`

**Interfaces:**
- Consumes: `id="dp-hd-chart-wrap"`, `id="dp-hd-skeleton"`, `id="dp-hd-chart"` (Task 2 HTML)
- Consumes: `.is-loaded` CSS 클래스 (Task 1 CSS)
- Consumes: `/api/four_axis/{ticker}?market=...` — `chart:1` 기본값 응답에 `d.chart` (base64 PNG) 포함

- [ ] **Step 1: fetch URL에서 `chart: '0'` 파라미터 제거**

`app.js` 4288번째 줄:

변경 전:
```js
    const p = new URLSearchParams({ market: currentMarket, chart: '0' });
```

변경 후:
```js
    const p = new URLSearchParams({ market: currentMarket });
```

- [ ] **Step 2: 스파크라인 렌더링 블록 제거, 차트 이미지 렌더링으로 교체**

`app.js` 4298~4333번째 줄의 스파크라인 블록 전체를 제거하고 아래로 교체:

제거 블록 (4298~4333):
```js
    // ── Hero 스파크라인 + 52주 위치 ─────────────────────────────
    try {
      const panel = document.getElementById('dp-spark-panel');
      ... (스파크라인 전체 렌더링 코드) ...
    } catch (e) { console.warn('hero spark render failed:', e); }
```

교체 코드 (같은 위치에 삽입):
```js
    // ── 핸드드로잉 차트 이미지 렌더 ─────────────────────────────
    try {
      const skeleton = document.getElementById('dp-hd-skeleton');
      const chartImg = document.getElementById('dp-hd-chart');
      if (chartImg && d.chart) {
        chartImg.onload = () => {
          if (skeleton) skeleton.style.display = 'none';
          chartImg.classList.add('is-loaded');
        };
        chartImg.onerror = () => {
          if (skeleton) skeleton.style.display = 'none';
        };
        chartImg.src = 'data:image/png;base64,' + d.chart;
      } else if (skeleton) {
        skeleton.style.display = 'none';
      }
    } catch (e) { console.warn('handdrawn chart render failed:', e); }
```

- [ ] **Step 3: 드로어 열릴 때마다 스켈레톤 초기화 로직 추가**

`loadDpFourAxis` 함수 상단 — 기존 초기화 블록(4280~4283번째 줄) 바로 다음에 삽입:

기존:
```js
  header.style.display = 'none';
  obsDiv.style.display = 'none';
  errDiv.style.display = 'none';
  loading.style.display = 'block';
```

추가 (바로 아래):
```js
  // 차트 영역 초기화 — 이전 종목 차트가 남아있지 않도록
  const _prevImg = document.getElementById('dp-hd-chart');
  const _prevSkel = document.getElementById('dp-hd-skeleton');
  if (_prevImg) { _prevImg.src = ''; _prevImg.classList.remove('is-loaded'); }
  if (_prevSkel) _prevSkel.style.display = '';
```

- [ ] **Step 4: Flask 서버 재시작 후 종목 클릭으로 종단 테스트**

```bash
python web_app/app.py
```

체크리스트:
- [ ] 드로어 열리면 헤더 직후 스켈레톤 박스 표시
- [ ] 1~3초 후 핸드드로잉 차트 이미지로 교체 (fade-in 150ms)
- [ ] 스캔 후 상위 종목 클릭 시 차트가 거의 즉시 표시 (BG warm 캐시 히트)
- [ ] 다른 종목 클릭 시 이전 차트가 사라지고 스켈레톤 재표시
- [ ] 타이밍 분석 텍스트(별점, 페이즈, 관찰) 정상 렌더링 유지
- [ ] 에러 종목 클릭 시 스켈레톤 숨겨지고 에러 메시지 표시

- [ ] **Step 5: 커밋**

```bash
git add web_app/static/app.js
git commit -m "feat(drawer): loadDpFourAxis — 핸드드로잉 차트 이미지 렌더링 추가"
```

---

### Task 5: 정리 — dp-spark 관련 CSS 제거

**Files:**
- Modify: `web_app/static/scanner.css`

**Note:** `dp-spark-panel` HTML이 제거되었으므로 관련 CSS도 정리한다. 기능에 영향 없지만 번들 크기 감소.

- [ ] **Step 1: scanner.css에서 spark 관련 스타일 블록 제거**

아래 선택자들로 시작하는 블록을 제거한다 (scanner.css에서 grep으로 확인 후):
- `#dp-spark-panel`
- `.dp-spark-panel--hidden`
- `.dp-spark-panel--visible`
- `.dp-spark-skeleton`
- `.dp-spark-skel-line`
- `.dp-spark-skel-chart`
- `.dp-spark-skel-facts`
- `.dp-spark-head`
- `.dp-spark-eyebrow`
- `.dp-spark-change`
- `.dp-spark-chart`
- `.dp-spark-last`
- `.dp-spark-facts`
- `.dp-spark-fact`
- `.dp-wk52-track`
- `.dp-wk52-fill`
- `.dp-hero-right`

확인 명령:
```bash
grep -n "dp-spark\|dp-hero-right\|dp-wk52" web_app/static/scanner.css
```

- [ ] **Step 2: 브라우저에서 드로어 재확인 — 스타일 제거 후 레이아웃 깨짐 없는지 확인**

- [ ] **Step 3: 커밋**

```bash
git add web_app/static/scanner.css
git commit -m "style(cleanup): 드로어 스파크라인 관련 CSS 제거"
```

---

## 완료 기준

- 드로어 열면 헤더(`[종목명] [닫기]`) 직후 핸드드로잉 차트가 표시됨
- 로딩 중 펄스 스켈레톤 표시, 완료 시 150ms fade-in
- 스캔 상위 종목은 BG warm으로 거의 즉시 표시
- 기존 타이밍 분석 텍스트 (별점, 페이즈, 4축 scores) 정상 동작
- SVG 스파크라인, 52주 고가 바, 거래량/시총 stat 제거 완료
- `loadFourAxis()` (detail.html용) 동작 변경 없음
