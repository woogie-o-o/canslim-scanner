# Hero Zone 밀도 리디자인 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 데스크탑에서 휑한 드로워 Hero Zone을, 한줄평을 주인공으로 강화하고 우측에 20일 추세 스파크라인+보조 지표를 채운 2-컬럼 레이아웃으로 리디자인한다.

**Architecture:** 백엔드는 이미 `hist`(가격 히스토리)를 가진 4축 차트 엔드포인트(`_compute_four_axis_payload`)에 경량 `closes`/52주 고저를 얹는다(스캔 페이로드 비대화 회피). 프런트는 드로워가 비동기로 부르는 `loadDpFourAxis`에서 인라인 SVG 스파크라인을 그리고, 거래량/시총은 이미 스캔 행 `d`에 있으므로 `_renderDetailFeatures`에서 채운다. HTML/CSS는 단일 컬럼을 데스크탑 2-컬럼으로 바꾸되 한줄평 포스터와 RSI 온도계는 풀폭 유지.

**Tech Stack:** Flask(Python 3.11), vanilla JS, CSS(theme.css 변수), pytest(`web_app/tests/`), playwright(시각 검증, `py -3`).

**참조 스펙:** `docs/superpowers/specs/2026-06-09-hero-zone-density-design.md`

**불변 제약:**
- element ID 11개 보존: `dp-score, dp-signal, dp-risk-gauge, dp-leader-badge, dp-price, dp-day-chg, dp-thermo-label, dp-thermo-action, dp-rs-value, dp-rs-label, dp-thermo-marker`
- 매수/매도 텍스트 금지 (취득/처분·관심/경계 등 기존 용어 유지)
- 색상은 theme.css 변수 사용 (`var(--success)`, `var(--card)`, `var(--border)`, `var(--text-tertiary)` 등). 시그널색은 기존 포스터 색 로직 재사용.

---

## File Structure

| 파일 | 책임 | 변경 |
|------|------|------|
| `web_app/app.py` | 4축 payload에 `closes`/`wk52_high`/`wk52_low` 추가 + 순수 헬퍼 2개 | Modify |
| `web_app/tests/test_hero_sparkline_payload.py` | 헬퍼 단위 테스트 | Create |
| `web_app/static/app.js` | 스파크라인 SVG 빌더(순수 fn) + `loadDpFourAxis` 배선 + `_renderDetailFeatures` 거래량/시총 | Modify |
| `web_app/static/_spark_test.mjs` | 스파크라인 빌더 node 테스트(임시, 검증 후 삭제) | Create→Delete |
| `web_app/templates/scanner.html` | Hero Zone 마크업 재구성(① 포스터 → ②③ 2-컬럼 → 온도계) | Modify |
| `web_app/static/scanner.css` | 포스터 확대·2-컬럼·차트 패널·반응형 | Modify |

---

## Task 1: 백엔드 — 스파크라인용 closes·52주 고저 페이로드

**Files:**
- Modify: `web_app/app.py` (`_compute_four_axis_payload`, 라인 ~2362~2521 payload 조립부)
- Test: `web_app/tests/test_hero_sparkline_payload.py`

순수 헬퍼로 분리해 네트워크 없이 단위 테스트한다.

- [ ] **Step 1: 실패하는 테스트 작성**

Create `web_app/tests/test_hero_sparkline_payload.py`:

```python
import importlib
app_mod = importlib.import_module("app")

def test_downsample_closes_caps_point_count():
    closes = list(range(100))  # 100 포인트
    out = app_mod._downsample_closes(closes, max_points=24)
    assert len(out) <= 24
    assert out[-1] == 99.0          # 마지막 값(최신가)은 항상 보존
    assert all(isinstance(x, float) for x in out)

def test_downsample_closes_short_input_passthrough():
    closes = [10.0, 11.0, 12.0]
    out = app_mod._downsample_closes(closes, max_points=24)
    assert out == [10.0, 11.0, 12.0]

def test_downsample_closes_empty():
    assert app_mod._downsample_closes([], max_points=24) == []

def test_wk52_high_low():
    closes = [5.0, 1.0, 9.0, 4.0]
    hi, lo = app_mod._wk52_high_low(closes)
    assert hi == 9.0 and lo == 1.0

def test_wk52_high_low_empty_returns_none():
    assert app_mod._wk52_high_low([]) == (None, None)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd web_app && python -m pytest tests/test_hero_sparkline_payload.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute '_downsample_closes'`

- [ ] **Step 3: 헬퍼 구현**

`web_app/app.py`에 모듈 수준 함수 추가 (다른 `_compute_four_axis_payload` 정의보다 위, 예: 라인 ~2360 직전):

```python
def _downsample_closes(closes, max_points: int = 24):
    """스파크라인용 종가 배열을 max_points 이하로 균등 다운샘플. 최신가는 항상 보존."""
    vals = [float(c) for c in closes if c is not None]
    n = len(vals)
    if n == 0:
        return []
    if n <= max_points:
        return vals
    step = n / max_points
    out = [vals[int(i * step)] for i in range(max_points)]
    out[-1] = vals[-1]  # 마지막(최신) 값 보존
    return out


def _wk52_high_low(closes):
    """종가 배열의 (고가, 저가). 비면 (None, None)."""
    vals = [float(c) for c in closes if c is not None]
    if not vals:
        return (None, None)
    return (max(vals), min(vals))
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd web_app && python -m pytest tests/test_hero_sparkline_payload.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: payload에 필드 추가**

`web_app/app.py` `_compute_four_axis_payload` 내부, `payload` dict가 만들어지고 `hist`가 살아있는 지점(라인 ~2521 `payload = {...}` 직후, heat_signal 블록 앞)에 추가:

```python
        # ── Hero 스파크라인용 경량 데이터 (20~24 포인트) + 52주 고저 ──
        try:
            _closes_full = [float(x) for x in hist["Close"].dropna().tolist()]
            _recent = _closes_full[-60:] if len(_closes_full) > 60 else _closes_full
            payload["closes"] = _downsample_closes(_recent, max_points=24)
            _hi, _lo = _wk52_high_low(_closes_full[-252:])
            payload["wk52_high"] = _hi
            payload["wk52_low"] = _lo
            payload["spark_change_pct"] = (
                round((_recent[-1] / _recent[0] - 1) * 100, 1)
                if len(_recent) >= 2 and _recent[0] else None
            )
        except Exception as _e:
            logging.debug("hero spark payload: %s", _e)
```

- [ ] **Step 6: 페이로드 수동 확인**

Run: `cd web_app && python -c "import app; print(app._downsample_closes(list(range(300)),24)[:3], len(app._downsample_closes(list(range(300)),24)))"`
Expected: `[0.0, 12.0, 24.0] 24` (정확한 값은 stride에 따라 다름, 길이 24·float이면 OK)

- [ ] **Step 7: 커밋**

```bash
git add web_app/app.py web_app/tests/test_hero_sparkline_payload.py
git commit -m "feat(hero): 4축 payload에 스파크라인 closes·52주 고저 추가"
```

---

## Task 2: 프런트 — 스파크라인 SVG 빌더 (순수 함수)

**Files:**
- Modify: `web_app/static/app.js` (유틸 함수 영역, 예: `_fmtMarketCap` 근처 라인 ~5040)
- Test: `web_app/static/_spark_test.mjs` (임시)

- [ ] **Step 1: 실패하는 node 테스트 작성**

Create `web_app/static/_spark_test.mjs`:

```javascript
import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

// app.js에서 buildSparklineSVG 함수 소스만 추출해 평가
const src = fs.readFileSync(new URL('./app.js', import.meta.url), 'utf8');
const m = src.match(/function buildSparklineSVG[\s\S]*?\n}/);
assert(m, 'buildSparklineSVG 함수를 찾지 못함');
const ctx = {}; vm.createContext(ctx);
vm.runInContext(m[0] + '\nthis.buildSparklineSVG = buildSparklineSVG;', ctx);

// 빈 배열 → 빈 문자열
assert.strictEqual(ctx.buildSparklineSVG([], '#22A463'), '');
// 정상 입력 → polyline 포함
const svg = ctx.buildSparklineSVG([1, 2, 3, 4, 5], '#22A463');
assert.ok(svg.includes('<polyline'), 'polyline 없음');
assert.ok(svg.includes('<polygon'), 'area polygon 없음');
assert.ok(svg.includes('#22A463'), '색상 반영 안됨');
console.log('OK buildSparklineSVG');
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd web_app/static && node _spark_test.mjs`
Expected: FAIL — `AssertionError: buildSparklineSVG 함수를 찾지 못함`

- [ ] **Step 3: 빌더 구현**

`web_app/static/app.js`에 추가 (예: 라인 ~5040 `_fmtMarketCap` 근처):

```javascript
// Hero 스파크라인 — closes 배열 → 인라인 SVG (viewBox 300x110)
function buildSparklineSVG(closes, color) {
  if (!Array.isArray(closes) || closes.length < 2) return '';
  const W = 300, H = 110, pad = 6;
  const lo = Math.min(...closes), hi = Math.max(...closes);
  const span = (hi - lo) || 1;
  const n = closes.length;
  const x = i => (i / (n - 1)) * W;
  const y = v => pad + (1 - (v - lo) / span) * (H - pad * 2);
  const pts = closes.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last = closes.length - 1;
  const gid = 'spkg_' + Math.abs(closes.length * 7 + Math.round(hi));
  return (
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:100%;display:block;">`
    + `<defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">`
    + `<stop offset="0" stop-color="${color}" stop-opacity=".22"/>`
    + `<stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`
    + `<polygon points="${pts} ${W},${H} 0,${H}" fill="url(#${gid})"/>`
    + `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>`
    + `<circle cx="${x(last).toFixed(1)}" cy="${y(closes[last]).toFixed(1)}" r="4.5" fill="${color}"/>`
    + `<circle cx="${x(last).toFixed(1)}" cy="${y(closes[last]).toFixed(1)}" r="9" fill="${color}" opacity=".18"/>`
    + `</svg>`
  );
}
```

> 주의: `Math.random()` 금지(결정성). gid는 입력 기반으로 생성.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd web_app/static && node _spark_test.mjs`
Expected: `OK buildSparklineSVG`

- [ ] **Step 5: 임시 테스트 삭제 + 커밋**

```bash
rm web_app/static/_spark_test.mjs
git add web_app/static/app.js
git commit -m "feat(hero): 스파크라인 SVG 빌더 buildSparklineSVG 추가"
```

---

## Task 3: HTML — Hero Zone 마크업 재구성

**Files:**
- Modify: `web_app/templates/scanner.html` (라인 257~310, `dp-hero-zone`)

기존 `dp-hero-summary`(Zone A+B)와 `dp-thermo-compact`(Zone C)는 유지하되, ① 포스터를 키우고 ②③ 2-컬럼 래퍼를 도입한다. **element ID 11개 모두 보존.**

- [ ] **Step 1: dp-hero-summary 내부를 2-컬럼 래퍼로 변경**

`web_app/templates/scanner.html` 라인 262~302의 `<div class="dp-hero-summary"> … </div>`를 아래로 교체 (Zone A/B 기존 ID 유지, 우측 차트 패널 신규 추가):

```html
      <div class="dp-hero-summary">
        <!-- 좌: 판결 + 수치 -->
        <div class="dp-hero-left">
          <!-- Zone A: 종합 판결 -->
          <div class="dp-verdict-zone">
            <div class="dp-verdict-score-wrap">
              <span id="dp-score" class="dp-score-num">—</span>
              <span class="dp-score-unit">점</span>
            </div>
            <div class="dp-verdict-right">
              <div class="dp-verdict-label">종합 점수</div>
              <div id="dp-signal" class="dp-signal-badge">—</div>
              <div class="dp-verdict-badges">
                <div id="dp-risk-gauge" class="dp-risk-pill"></div>
                <div id="dp-leader-badge" class="dp-leader-pill"></div>
              </div>
            </div>
          </div>
          <!-- Zone B: 4개 수치 그리드 -->
          <div class="dp-hero-stats">
            <div class="dp-hero-stat-cell">
              <span class="dp-hero-stat-label">현재가</span>
              <span id="dp-price" class="dp-hero-stat-value">—</span>
            </div>
            <div class="dp-hero-stat-cell">
              <span class="dp-hero-stat-label">등락률</span>
              <span id="dp-day-chg" class="dp-hero-stat-value">—</span>
            </div>
            <div class="dp-hero-stat-cell">
              <span class="dp-hero-stat-label">RSI</span>
              <span id="dp-thermo-label" class="dp-hero-stat-value">—</span>
              <span id="dp-thermo-action" class="dp-hero-stat-hint">—</span>
            </div>
            <div class="dp-hero-stat-cell">
              <span class="dp-hero-stat-label">RS 등급</span>
              <div id="dp-rs-value" class="dp-hero-stat-value dp-rs-wrap">—</div>
              <span id="dp-rs-label" class="dp-hero-stat-hint">/ 99</span>
            </div>
          </div>
        </div>

        <!-- 우: 추세 차트 패널 (신규) -->
        <div class="dp-hero-right" id="dp-spark-panel" style="display:none;">
          <div class="dp-spark-head">
            <span class="dp-spark-eyebrow">최근 추세</span>
            <span id="dp-spark-change" class="dp-spark-change"></span>
          </div>
          <div class="dp-spark-chart">
            <div id="dp-spark"></div>
            <span id="dp-spark-last" class="dp-spark-last"></span>
          </div>
          <div class="dp-spark-facts">
            <div class="dp-spark-fact dp-spark-fact--wide">
              <span class="dp-spark-fact-label">52주 위치</span>
              <div class="dp-wk52-track"><div id="dp-wk52-bar" class="dp-wk52-fill"></div></div>
              <span id="dp-wk52-label" class="dp-spark-fact-val">—</span>
            </div>
            <div class="dp-spark-fact">
              <span class="dp-spark-fact-label">거래량</span>
              <span id="dp-volratio" class="dp-spark-fact-val">—</span>
            </div>
            <div class="dp-spark-fact">
              <span class="dp-spark-fact-label">시총</span>
              <span id="dp-mktcap" class="dp-spark-fact-val">—</span>
            </div>
          </div>
        </div>
      </div><!-- /dp-hero-summary -->
```

- [ ] **Step 2: 한줄평 포스터에 eyebrow 라벨 래핑 (선택, 키포인트 강조)**

라인 259 `<div id="dp-fa-haiku" class="dp-oneliner-poster" style="display:none;"></div>` 는 JS가 textContent를 채우므로 구조 변경 없이 CSS로 확대(Task 4)한다. 변경 없음 — 확인만.

- [ ] **Step 3: 브라우저 콘솔 오류 없는지 임시 확인 (서버 기동)**

Run: `cd web_app && python app.py` (백그라운드) 후 `curl -s http://localhost:5000/ -o NUL -w "%{http_code}\n"`
Expected: `200` (템플릿 렌더 오류 없음). 확인 후 서버 종료.

- [ ] **Step 4: 커밋**

```bash
git add web_app/templates/scanner.html
git commit -m "feat(hero): Hero Zone 2-컬럼 마크업 + 추세 차트 패널 골격"
```

---

## Task 4: CSS — 포스터 확대 · 2-컬럼 · 차트 패널 · 반응형

**Files:**
- Modify: `web_app/static/scanner.css` (Hero Zone 영역 라인 ~1843~1985, 모바일 ~3324~3360)

- [ ] **Step 1: 한줄평 포스터 확대 (키포인트)**

`.dp-oneliner-poster`(라인 ~2096) 의 `font-size`를 확대. 기존 `font-size: clamp(28px, 5.5vw, 46px);` 를 아래로:

```css
  font-size: clamp(30px, 5.5vw, 46px);
  line-height: 1.25;
  letter-spacing: -0.03em;
```

(데스크탑에서 46px 상한 유지, 하한·자간만 강화. 색/그림자는 기존 시그널색 로직 유지.)

- [ ] **Step 2: hero-summary 2-컬럼화 + 신규 패널 스타일 추가**

`.dp-hero-summary`(라인 ~1853) 규칙을 교체하고 그 아래 신규 규칙 추가:

```css
.dp-hero-summary {
  display: grid;
  grid-template-columns: 1fr 1.1fr;
  gap: 28px;
  align-items: stretch;
  margin-bottom: 0;
}
.dp-hero-left { min-width: 0; }
.dp-hero-right {
  min-width: 0;
  display: flex;
  flex-direction: column;
  border-left: 1px solid var(--border);
  padding-left: 24px;
}
.dp-spark-head { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.dp-spark-eyebrow {
  font-size: 10px; letter-spacing: 0.08em; color: var(--text-tertiary);
  font-weight: 700; text-transform: uppercase;
}
.dp-spark-change {
  font-size: 11px; font-weight: 800; color: var(--success);
  background: color-mix(in srgb, var(--success) 12%, transparent);
  padding: 2px 7px; border-radius: 6px;
}
.dp-spark-change.is-down {
  color: var(--destructive);
  background: color-mix(in srgb, var(--destructive) 12%, transparent);
}
.dp-spark-chart { position: relative; flex: 1; min-height: 96px; }
.dp-spark-chart > #dp-spark { position: absolute; inset: 0; }
.dp-spark-last {
  position: absolute; right: 0; top: 0;
  font-size: 12px; font-weight: 800; color: var(--success);
}
.dp-spark-facts { display: flex; margin-top: 8px; border-top: 1px solid var(--border); padding-top: 11px; }
.dp-spark-fact { flex: 1; padding-left: 14px; border-left: 1px solid var(--border); }
.dp-spark-fact:first-child { padding-left: 0; border-left: none; }
.dp-spark-fact--wide { flex: 1.2; }
.dp-spark-fact-label {
  display: block; font-size: 9px; letter-spacing: 0.06em;
  color: var(--text-tertiary); font-weight: 700; text-transform: uppercase;
}
.dp-spark-fact-val { font-size: 15px; font-weight: 700; color: var(--text-primary); }
.dp-wk52-track { height: 4px; background: var(--surface-muted, #eef0f2); border-radius: 9px; margin: 7px 12px 6px 0; position: relative; }
.dp-wk52-fill { position: absolute; left: 0; top: 0; height: 4px; background: var(--success); border-radius: 9px; }
```

- [ ] **Step 3: 모바일(≤768px) 세로 스택**

`@media (max-width: 768px)` 블록(라인 ~3324, 판결문 레이아웃 모바일) 안에 추가:

```css
  .dp-hero-summary { grid-template-columns: 1fr; gap: 18px; }
  .dp-hero-right {
    border-left: none; padding-left: 0;
    border-top: 1px solid var(--border); padding-top: 16px;
  }
```

(모바일에서는 ① 포스터 → ② 판결+수치 → ③ 차트 패널 순으로 세로 스택)

- [ ] **Step 4: color-mix 폴백 확인**

Run: `cd web_app/static && node -e "const c=require('fs').readFileSync('scanner.css','utf8'); console.log(/color-mix/.test(c)?'uses color-mix':'no')"`
타깃 브라우저가 구형이면 `color-mix` 대신 기존 패턴(`rgba(34,164,99,.12)`)으로 치환. 본 앱은 Apple 디자인(모던 Safari/Chrome 타깃)이므로 `color-mix` 허용.
Expected: `uses color-mix`

- [ ] **Step 5: 커밋**

```bash
git add web_app/static/scanner.css
git commit -m "feat(hero): 포스터 확대 + 2-컬럼/차트패널 CSS + 반응형 스택"
```

---

## Task 5: JS 배선 — 스파크라인·52주·거래량·시총 채우기

**Files:**
- Modify: `web_app/static/app.js` (`loadDpFourAxis` 성공 핸들러 라인 ~3961, `_renderDetailFeatures` 라인 ~3336)

- [ ] **Step 1: 4축 성공 핸들러에 스파크라인·52주 배선**

`web_app/static/app.js` `loadDpFourAxis` 내 차트 src 설정(라인 3961) 직후에 추가:

```javascript
    // ── Hero 스파크라인 + 52주 위치 ─────────────────────────────
    try {
      const panel = document.getElementById('dp-spark-panel');
      const sparkEl = document.getElementById('dp-spark');
      const closes = Array.isArray(d.closes) ? d.closes : [];
      if (panel && sparkEl && closes.length >= 2) {
        const _rec = _stockMap[ticker];
        const _sig = (_rec && _rec.Signal) || '';
        const up = closes[closes.length - 1] >= closes[0];
        const col = up ? 'var(--success)' : 'var(--destructive)';
        sparkEl.innerHTML = buildSparklineSVG(closes, up ? '#22A463' : '#DC2626');
        const lastEl = document.getElementById('dp-spark-last');
        if (lastEl) { lastEl.textContent = fmtPrice(closes[closes.length - 1]); lastEl.style.color = up ? '#22A463' : '#DC2626'; }
        const chgEl = document.getElementById('dp-spark-change');
        if (chgEl && d.spark_change_pct != null) {
          const s = d.spark_change_pct >= 0 ? '▲ ' : '▼ ';
          chgEl.textContent = s + Math.abs(d.spark_change_pct).toFixed(1) + '%';
          chgEl.classList.toggle('is-down', d.spark_change_pct < 0);
        }
        // 52주 위치 = (현재 - 저) / (고 - 저)
        const bar = document.getElementById('dp-wk52-bar');
        const wlbl = document.getElementById('dp-wk52-label');
        if (bar && wlbl && d.wk52_high != null && d.wk52_low != null && d.wk52_high > d.wk52_low) {
          const cur = closes[closes.length - 1];
          const posPct = Math.max(0, Math.min(100, ((cur - d.wk52_low) / (d.wk52_high - d.wk52_low)) * 100));
          bar.style.width = posPct.toFixed(0) + '%';
          wlbl.textContent = '상위 ' + posPct.toFixed(0) + '%';
        }
        panel.style.display = '';
      }
    } catch (e) { console.warn('hero spark render failed:', e); }
```

- [ ] **Step 2: 거래량/시총 배선 (`_renderDetailFeatures`)**

`web_app/static/app.js` `_renderDetailFeatures(d)` 함수 끝(라인 ~3404 `}` 직전, RS 블록 뒤)에 추가:

```javascript
  // ── 거래량 배수 · 시총 (Hero 우측 패널) ──────────────────────
  const volEl = document.getElementById('dp-volratio');
  if (volEl) volEl.textContent = (d.VolRatio != null) ? (Number(d.VolRatio).toFixed(1) + '×') : '—';
  const mcEl = document.getElementById('dp-mktcap');
  if (mcEl) {
    const mc = (d.MarketCap != null) ? d.MarketCap : d._MarketCap;
    mcEl.textContent = (mc != null) ? _fmtMarketCap(mc) : '—';
  }
```

> `_fmtMarketCap`는 기존 함수(app.js:1604에서 사용). 없으면 같은 포맷터 재사용.

- [ ] **Step 3: 구문 체크**

Run: `cd web_app && node --check static/app.js`
Expected: 출력 없음(통과). 오류 시 수정.

- [ ] **Step 4: 커밋**

```bash
git add web_app/static/app.js
git commit -m "feat(hero): 스파크라인·52주·거래량·시총 드로워 배선"
```

---

## Task 6: 시각 검증 (playwright 하니스 — 데스크탑 + 모바일)

**Files:**
- Create(임시): `web_app/_hero_verify.py`, `web_app/_hero_verify.html`

실제 서버 스캔 없이, 실제 `scanner.css`+`theme.css`+신규 마크업에 NBIX 샘플 데이터를 채워 렌더 검증한다. (`py -3`에 playwright 설치됨)

- [ ] **Step 1: 검증용 하니스 HTML 작성**

Create `web_app/_hero_verify.html` — `<link>`로 `static/theme.css`·`static/scanner.css` 로드, `detail-overlay > detail-panel.open > dp-scroll > dp-section-nav + dp-hero-zone` 구조에 Task 3의 마크업을 넣고 샘플값(92, S 강세, Risk 48, 163.12, −0.46%, RSI 탐욕, RS 82, 한줄평, 스파크라인 SVG, 52주 78%, 거래량 1.4×, 시총 $16.2B)을 인라인으로 채운다. `#dp-spark-panel`은 `style="display:flex"`로 강제 표시.

> 참조: 직전 세션에서 동일 패턴의 하니스를 사용함. 포스터/Zone A/B/온도계 마크업은 `scanner.html` 라인 257~310에서 복사하고 값만 채운다.

- [ ] **Step 2: 스크린샷 스크립트 작성**

Create `web_app/_hero_verify.py`:

```python
import pathlib
from playwright.sync_api import sync_playwright
HERE = pathlib.Path(__file__).resolve().parent
URL = (HERE / "_hero_verify.html").as_uri()
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, w in [("desktop", 1200), ("mobile", 390)]:
        ctx = b.new_context(viewport={"width": w, "height": 1000}, device_scale_factor=2)
        pg = ctx.new_page(); pg.goto(URL); pg.wait_for_timeout(400)
        pg.locator("#dp-hero-zone").screenshot(path=str(HERE / f"_hero_{name}.png"))
        print(name, "OK")
    b.close()
```

- [ ] **Step 3: 렌더 + 스크린샷**

Run: `cd web_app && py -3 _hero_verify.py`
Expected: `desktop OK` / `mobile OK`, `_hero_desktop.png`·`_hero_mobile.png` 생성

- [ ] **Step 4: 스크린샷 육안 검증 (Read 도구로 이미지 확인)**

체크리스트:
- 데스크탑: ① 한줄평 포스터 크게(46px) 상단 풀폭 → ② 좌 92점+2×2 수치 / ③ 우 스파크라인(글로우+▲%)·52주바·거래량·시총, 폭이 꽉 참(휑하지 않음)
- 모바일: 세로 스택(포스터 → 판결+수치 → 차트), 2×2 수치 유지
- RSI 온도계 풀폭 정상
- 매수/매도 텍스트 없음

미흡 시 Task 4 CSS를 수정하고 Step 3 재실행(반복).

- [ ] **Step 5: 임시 파일 삭제**

```bash
rm web_app/_hero_verify.py web_app/_hero_verify.html web_app/_hero_desktop.png web_app/_hero_mobile.png
```

- [ ] **Step 6: 전체 회귀 — 백엔드 테스트**

Run: `cd web_app && python -m pytest tests/ -q`
Expected: 기존 + 신규 테스트 통과(실패 0). 네트워크 의존 테스트가 환경상 실패하면 신규 `test_hero_sparkline_payload.py`만이라도 통과 확인.

- [ ] **Step 7: 최종 커밋**

```bash
git add -A
git commit -m "test(hero): 시각 검증 완료 — 데스크탑/모바일 렌더 확인"
```

---

## Self-Review 체크 (계획 작성자 수행 완료)

- **스펙 커버리지:** ① 포스터 확대(T3·T4) / ② 판결+수치 보존(T3) / ③ 스파크라인·52주·거래량·시총(T1·T2·T5) / 데스크탑 2-컬럼(T4) / 모바일 스택(T4) / 온도계 유지(T3 보존) / 데이터 백엔드(T1) — 전부 매핑됨.
- **플레이스홀더:** 각 코드 스텝에 실제 코드/명령/기대출력 포함. "적절히 처리" 류 없음.
- **타입/이름 일관성:** `buildSparklineSVG`(T2 정의 → T5 호출), payload 키 `closes`/`wk52_high`/`wk52_low`/`spark_change_pct`(T1 생성 → T5 소비), element ID(`dp-spark`,`dp-spark-change`,`dp-spark-last`,`dp-wk52-bar`,`dp-wk52-label`,`dp-volratio`,`dp-mktcap`)가 T3 마크업 ↔ T5 배선 ↔ T4 스타일에서 일치.
- **데이터 의존:** `d.VolRatio`/`d.MarketCap`(있음), `d.closes` 등(T1에서 신설). 정합.
