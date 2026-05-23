# Mobile Responsive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PC 레이아웃을 그대로 유지하면서 768px 이하 모바일에서 종목 카드 리스트·상세 패널 개선·비교/설정 페이지 패딩 수정을 적용한다.

**Architecture:** 모든 변경은 CSS `@media (max-width: 768px)` 안에서만 이뤄지므로 데스크탑은 전혀 영향 없음. 종목 테이블 옆에 `#mobile-stock-list` 컨테이너를 추가해 모바일에서는 테이블을 숨기고 카드 리스트를 노출. `app.js`에 `renderMobileCard()` 함수를 추가하고 `renderStockTable` / `setStockListMsg` / `showScanLoading` 세 곳에서 모바일 목록도 함께 업데이트.

**Tech Stack:** Vanilla CSS (media queries), Vanilla JS, Flask/Jinja2 HTML templates

---

## File Map

| 파일 | 변경 내용 |
|------|-----------|
| `web_app/templates/scanner.html` | 모바일 카드 CSS 추가, `#mobile-stock-list` div 추가, 상세 패널 모바일 CSS 개선 |
| `web_app/static/app.js` | `renderMobileCard()` 추가, `renderStockTable` / `setStockListMsg` / `showScanLoading` 수정 |
| `web_app/templates/compare.html` | 모바일 패딩/레이아웃 CSS 추가 |
| `web_app/templates/settings.html` | 모바일 topbar CSS 추가 |

---

## Task 1: 모바일 카드 CSS + HTML 컨테이너 (scanner.html)

**Files:**
- Modify: `web_app/templates/scanner.html` — `</style>` 닫기 태그 직전에 CSS 추가, `</main>` 직전에 div 추가

- [ ] **Step 1: 기존 `@media (max-width: 768px)` 블록 안의 `.stock-table-wrap` 관련 규칙 바로 아래에 모바일 카드 CSS 추가**

`scanner.html`의 `@media (max-width: 768px)` 블록(line ~1700) 안 `.stock-table-wrap` 규칙 다음에 아래를 추가:

```css
  /* ── Mobile Card List ─────────────────────────────────── */
  .mobile-stock-list {
    display: flex;
    flex-direction: column;
    background: var(--card);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    overflow: hidden;
  }
  .stock-card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
    transition: background var(--duration-fast);
    -webkit-tap-highlight-color: transparent;
  }
  .stock-card:last-child { border-bottom: none; }
  .stock-card:active { background: var(--surface-subtle); }
  .stock-card-row1 {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .stock-card-rank {
    font-size: 12px;
    font-weight: 700;
    color: var(--text-tertiary);
    width: 20px;
    text-align: center;
    flex-shrink: 0;
  }
  .stock-card-name {
    flex: 1;
    min-width: 0;
  }
  .stock-card-name-main {
    display: block;
    font-size: 15px;
    font-weight: 700;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .stock-card-ticker {
    font-size: 11px;
    color: var(--text-tertiary);
  }
  .stock-card-score {
    font-size: 22px;
    font-weight: 800;
    flex-shrink: 0;
  }
  .stock-card-chg {
    font-size: 13px;
    font-weight: 700;
    flex-shrink: 0;
    font-variant-numeric: tabular-nums;
    min-width: 54px;
    text-align: right;
  }
  .stock-card-row2 {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
  }
  .stock-card-oneliner {
    font-size: 12px;
    padding: 3px 8px;
    max-width: 100%;
    box-shadow: 1px 1px 0 #1a1a1a;
  }
  .mobile-stock-list-msg {
    padding: 40px 16px;
    text-align: center;
    color: var(--text-tertiary);
    font-size: 14px;
  }
```

- [ ] **Step 2: 데스크탑에서 `#mobile-stock-list` 숨기고, 모바일에서 `.stock-table-wrap` 숨기기**

`scanner.html`의 CSS에서 기존 `.mobile-stock-list` 정의가 없으므로 데스크탑 기본 스타일로 추가 (`:root` 영역이나 `/* ── Main (Stock Table) ──` 섹션 근처):

```css
.mobile-stock-list { display: none; }
```

그리고 `@media (max-width: 768px)` 블록 안 기존 `.stock-table-wrap` 규칙을 확인 — 이미 있으면 그 옆에 아래 추가:

```css
  .stock-table-wrap { display: none; }
```

- [ ] **Step 3: HTML에 `#mobile-stock-list` 컨테이너 추가**

`scanner.html`에서 아래 부분을 찾아:
```html
      </div>
    </main>
  </div>
```
이 중 `</main>` 바로 위 `</div>` (`.stock-table-wrap` 닫기) 뒤에 추가:

```html
      <div id="mobile-stock-list" class="mobile-stock-list"></div>
```

결과:
```html
      <div class="stock-table-wrap">
        <table class="stock-table">
          ...
        </table>
      </div>
      <div id="mobile-stock-list" class="mobile-stock-list"></div>
    </main>
```

- [ ] **Step 4: 커밋**

```
git add web_app/templates/scanner.html
git commit -m "feat(mobile): add mobile card list CSS and HTML container"
```

---

## Task 2: app.js — renderMobileCard + 세 함수 업데이트

**Files:**
- Modify: `web_app/static/app.js` — `renderStockRow` 함수 바로 뒤에 `renderMobileCard` 추가, `renderStockTable` / `setStockListMsg` / `showScanLoading` 수정

- [ ] **Step 1: `renderMobileCard` 함수 추가 (app.js line ~831 이후, `showStockPopup` 함수 앞)**

```javascript
function renderMobileCard(stock, rank) {
  const score    = Math.round(stock.TotalScore || 0);
  const sc       = scoreClass(score);
  const dayChg   = stock.DayChg || 0;
  const chgPct   = (dayChg * 100).toFixed(2);
  const chgClass = dayChg > 0 ? 'chg-up' : dayChg < 0 ? 'chg-down' : 'chg-flat';
  const chgSign  = dayChg > 0 ? '+' : '';
  const starred  = _watchlist.has(stock.Ticker);
  const olTag    = stock.OneLinerTag || '';
  const olText   = stock.OneLiner   || '';

  const olHtml = olText
    ? `<div class="stock-oneliner stock-card-oneliner" data-tag="${esc(olTag)}">${esc(olText)}</div>`
    : '';

  return `
<div class="stock-card" onclick="openDetail('${esc(stock.Ticker)}')">
  <div class="stock-card-row1">
    <span class="stock-card-rank">${rank}</span>
    <button class="star-btn${starred ? ' starred' : ''}"
            onclick="toggleWatchlist('${esc(stock.Ticker)}', event)"
            title="${starred ? '워치리스트에서 제거' : '워치리스트에 추가'}">${starred ? '★' : '☆'}</button>
    <div class="stock-card-name">
      <span class="stock-card-name-main">${esc(stock.Name || stock.Ticker)}</span>
      <span class="stock-card-ticker">${esc(stock.Ticker)} · ${esc(stock.Sector || '')}</span>
    </div>
    <span class="stock-card-score ${sc}">${score}</span>
    <span class="stock-card-chg ${chgClass}">${chgSign}${chgPct}%</span>
  </div>
  ${olHtml}
  <div class="stock-card-row2">
    ${_renderSignalHtml(stock.Signal, stock)}
  </div>
</div>`;
}

function _updateMobileList(view, emptyMsg) {
  const el = document.getElementById('mobile-stock-list');
  if (!el) return;
  if (!view || view.length === 0) {
    el.innerHTML = `<div class="mobile-stock-list-msg">${emptyMsg || '결과 없음'}</div>`;
    return;
  }
  el.innerHTML = view.map((s, i) => renderMobileCard(s, i + 1)).join('');
}
```

- [ ] **Step 2: `renderStockTable` 함수 끝 부분에 모바일 목록 업데이트 추가**

`renderStockTable` 함수 (line ~703)에서 마지막 줄:
```javascript
  tbody.innerHTML = view.map((s, i) => renderStockRow(s, i + 1)).join('');
```
이 줄 바로 다음에 추가:
```javascript
  _updateMobileList(view);
```

그리고 빈 결과 분기 (line ~727):
```javascript
    tbody.innerHTML = `<tr><td colspan="${_colCount()}" class="state-msg">${esc(_currentFilter === 'all' ? '결과 없음' : emptyMsg)}</td></tr>`;
    return;
```
이 줄 앞에 추가:
```javascript
    _updateMobileList([], _currentFilter === 'all' ? '결과 없음' : emptyMsg);
```

- [ ] **Step 3: `setStockListMsg` 함수에 모바일 업데이트 추가**

```javascript
function setStockListMsg(msg) {
  const tbody = document.getElementById('stock-list');
  if (tbody) tbody.innerHTML = `<tr><td colspan="${_colCount()}" class="state-msg">${esc(msg)}</td></tr>`;
  _updateMobileList([], msg);  // ← 추가
}
```

- [ ] **Step 4: `showScanLoading` 함수에 모바일 로딩 메시지 추가**

`showScanLoading` 함수 (line ~930) 안에서 `tbody.innerHTML = ...` 설정 직후에 추가:
```javascript
  const mEl = document.getElementById('mobile-stock-list');
  if (mEl) mEl.innerHTML = `<div class="mobile-stock-list-msg">종목 분석 중...</div>`;
```

- [ ] **Step 5: 커밋**

```
git add web_app/static/app.js
git commit -m "feat(mobile): add renderMobileCard and sync mobile list with table updates"
```

---

## Task 3: 상세 패널 모바일 개선 (scanner.html CSS)

**Files:**
- Modify: `web_app/templates/scanner.html` — `@media (max-width: 768px)` 블록 안 상세 패널 섹션

- [ ] **Step 1: 현재 `@media (max-width: 768px)` 블록 안 `.dp-left` 규칙 찾기**

현재 코드 (line ~1731):
```css
  .dp-left {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid var(--border);
    max-height: 280px;
    overflow-y: auto;
  }
```

이를 아래로 교체:
```css
  .dp-left {
    width: 100%;
    border-right: none;
    border-bottom: 1px solid var(--border);
    /* max-height 제거 — 자연 높이로 표시 */
    overflow-y: visible;
  }
  /* 모바일에서 불필요한 좌측 카드 숨김 */
  #dp-quadrant-card { display: none !important; }
```

- [ ] **Step 2: 탭 네비게이션 sticky 추가**

`@media (max-width: 768px)` 블록 안에 추가:
```css
  /* 탭바 sticky — dp-scroll 내에서 항상 상단 고정 */
  .dp-tab-nav {
    position: sticky;
    top: 0;
    z-index: 10;
    overflow-x: auto;
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
    flex-shrink: 0;
  }
  .dp-tab-nav::-webkit-scrollbar { display: none; }
  .dp-tab-btn { padding: 0 14px; font-size: 13px; white-space: nowrap; height: 40px; }
```

- [ ] **Step 3: 4축 지표 그리드 2컬럼으로 변경**

`@media (max-width: 768px)` 블록 안에 추가:
```css
  /* 4축 그리드 2열로 */
  #dp-fouraxis-header > div:last-child {
    grid-template-columns: repeat(2, 1fr) !important;
  }
  #dp-fouraxis-header { padding: 10px 14px 6px; }
  #dp-fouraxis-chart-wrap { padding: 8px 14px; }
  #dp-fouraxis-obs { margin: 0 12px 12px; padding: 12px; }
```

- [ ] **Step 4: 상세 패널 헤더 모바일 개선**

캡처 버튼이 작은 화면에서 밀리는 문제 수정. `@media (max-width: 480px)` 블록 안에 추가:
```css
  .dp-header button[onclick="captureDetail()"] { display: none; }
```

- [ ] **Step 5: 커밋**

```
git add web_app/templates/scanner.html
git commit -m "feat(mobile): sticky tabs, remove dp-left max-height, 2-col 4-axis grid"
```

---

## Task 4: compare.html + settings.html 모바일 패딩 수정

**Files:**
- Modify: `web_app/templates/compare.html` — `</style>` 직전에 media query 추가
- Modify: `web_app/templates/settings.html` — `</style>` 직전에 media query 추가

- [ ] **Step 1: compare.html에 모바일 CSS 추가**

`compare.html`의 `<style>` 블록 닫기 전에 추가:

```css
@media (max-width: 768px) {
  .compare-shell { padding: 12px; }
  .compare-topbar {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    margin-bottom: 12px;
  }
  .compare-title { font-size: 20px; }
  .compare-sub { font-size: 12px; }
  .compare-grid { grid-template-columns: 1fr; }
  .compare-back { height: 34px; font-size: 13px; }
}
```

- [ ] **Step 2: settings.html에 모바일 CSS 추가**

`settings.html`의 `<style>` 블록 닫기 전에 추가:

```css
@media (max-width: 768px) {
  .app-shell { grid-template-rows: 52px 1fr; }
  .topbar { padding: 0 12px; gap: 8px; }
  .topbar-logo { font-size: 15px; }
  .settings-content { padding: 12px; }
}
```

- [ ] **Step 3: settings.html에서 `settings-content` 클래스 확인**

`settings.html`을 열어 `.settings-content` CSS 확인. 만약 `padding: 32px` 같은 고정값이 있다면 위의 override가 제대로 적용되는지 확인 (specificity 충돌 시 `!important` 추가).

- [ ] **Step 4: 커밋**

```
git add web_app/templates/compare.html web_app/templates/settings.html
git commit -m "feat(mobile): responsive padding for compare and settings pages"
```

---

## Self-Review 체크리스트

- [x] **Spec coverage**: 테이블→카드(Task 2), 탭 sticky(Task 3), dp-left 개선(Task 3), compare/settings(Task 4) — 모두 커버
- [x] **Placeholder 없음**: 모든 단계에 실제 코드 포함
- [x] **Type consistency**: `renderMobileCard`, `_updateMobileList` 이름이 모든 태스크에서 일치
- [x] **PC 미영향**: 모든 CSS 변경은 `@media (max-width: 768px)` 내부, app.js 변경은 `#mobile-stock-list` 요소가 없으면 no-op
- [x] **로딩 상태**: Task 2 Step 4에서 `showScanLoading` 도 처리

---

*Plan saved: 2026-05-23*
