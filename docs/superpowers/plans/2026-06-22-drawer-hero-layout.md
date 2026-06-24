# 드로어 히어로 레이아웃 통합 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 드로어 살까? 섹션을 단일 풀배너(판정 + 확신도 + 이유 + 히어로 스탯)로 통합하고, 언제? 섹션의 중복 타이밍 미니 카드를 제거한다.

**Architecture:** HTML에서 `dp-hero-stats`를 살까? 섹션 하단으로 이동하고 `dp-timing-mini`를 제거. JS의 `_renderEntryVerdict`를 수정해 확신도%를 포스터 우측에 렌더링. CSS에 flex 레이아웃과 확신도 블록 스타일 추가, 타이밍 미니 카드 스타일 제거.

**Tech Stack:** Vanilla JS, HTML, CSS (Pretendard 폰트, CSS 변수)

## Global Constraints

- CSS 변수 시스템 사용 (`var(--text-primary)` 등) — 인라인 color 값 직접 사용 금지
- `dp-price`, `dp-day-chg`, `dp-rs-value`, `dp-rs-label` ID는 JS에서 참조 중 — ID 변경 금지
- `dvp-green` / `dvp-yellow` / `dvp-red` 클래스명은 JS에서 설정 — 변경 금지

---

### Task 1: HTML 재구성

**Files:**
- Modify: `web_app/templates/scanner.html:248-297`

**What this does:** `dp-timing-mini` 제거, `dp-hero-stats` 블록을 살까? 섹션 하단으로 이동.

- [ ] **Step 1: `dp-timing-mini` div 한 줄 제거**

`scanner.html` 264번째 줄 아래 한 줄을 삭제:
```html
<!-- 삭제할 줄 -->
<div id="dp-timing-mini" class="dp-timing-mini" style="display:none;"></div>
```

- [ ] **Step 2: `dp-hero-stats` 블록을 살까? 섹션으로 이동**

`scanner.html`에서 아래 블록(281~295줄)을 잘라내:
```html
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
              <span class="dp-hero-stat-label">RS 등급</span>
              <div id="dp-rs-value" class="dp-hero-stat-value dp-rs-wrap">—</div>
              <span id="dp-rs-label" class="dp-hero-stat-hint">/ 99</span>
            </div>
          </div>
```

그리고 `dp-section-conclusion` 안, `dp-verdict-poster` 바로 아래에 붙여넣어:
```html
    <section id="dp-section-conclusion" class="dp-section-conclusion">
      <div id="dp-verdict-poster" class="dp-verdict-poster" style="display:none;"></div>
      <!-- ↓ 히어로 스탯 이동 위치 -->
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
          <span class="dp-hero-stat-label">RS 등급</span>
          <div id="dp-rs-value" class="dp-hero-stat-value dp-rs-wrap">—</div>
          <span id="dp-rs-label" class="dp-hero-stat-hint">/ 99</span>
        </div>
      </div>
      <!-- 진입 판단 종합 카드 (내부 전용 — UI에 미노출) -->
      <div id="dp-entry-verdict" ...>
```

- [ ] **Step 3: 시각 확인**

앱 실행 후 종목 선택 → 살까? 섹션에 히어로 스탯(현재가/등락률/RS)이 배너 바로 아래 나타나는지 확인.
언제? 섹션 상단에 컬러 미니 카드가 사라졌는지 확인.

- [ ] **Step 4: 커밋**

```bash
git add web_app/templates/scanner.html
git commit -m "refactor(drawer): dp-hero-stats를 살까? 섹션으로 이동, dp-timing-mini 제거"
```

---

### Task 2: JS — 포스터에 확신도% 통합

**Files:**
- Modify: `web_app/static/app.js:3669-3680`

**Interfaces:**
- Consumes: `conv` (0-100 정수), `_pgCls`, `_pvWord`, `_pvReason`, `_pvBg` — 이미 계산된 변수들
- Produces: `dp-verdict-poster` innerHTML — `dvp-main` + `dvp-conf` flex 구조

- [ ] **Step 1: `_renderEntryVerdict` 포스터 렌더 코드 수정**

`app.js` 3669~3674줄 블록을 아래로 교체:

변경 전:
```javascript
  const _vpEl = document.getElementById('dp-verdict-poster');
  if (_vpEl) {
    _vpEl.style.display = '';
    _vpEl.className = `dp-verdict-poster ${_pgCls}`;
    _vpEl.innerHTML = `<div class="dvp-eyebrow">살까? 말까?</div><div class="dvp-word">${_pvWord}</div><div class="dvp-reason">${_pvReason}</div><div class="dvp-bg">${_pvBg}</div>`;
  }
```

변경 후:
```javascript
  const _vpEl = document.getElementById('dp-verdict-poster');
  if (_vpEl) {
    _vpEl.style.display = '';
    _vpEl.className = `dp-verdict-poster ${_pgCls}`;
    _vpEl.innerHTML = `<div class="dvp-main"><div class="dvp-eyebrow">살까? 말까?</div><div class="dvp-word">${_pvWord}</div><div class="dvp-reason">${_pvReason}</div></div><div class="dvp-conf"><div class="dvp-conf-num">${conv}<span class="dvp-conf-pct">%</span></div><div class="dvp-conf-lbl">확신도</div></div><div class="dvp-bg">${_pvBg}</div>`;
  }
```

- [ ] **Step 2: `dp-timing-mini` 렌더 코드 제거**

3675~3680줄 블록 전체 삭제:
```javascript
  const _tmEl = document.getElementById('dp-timing-mini');
  if (_tmEl) {
    _tmEl.style.display = '';
    _tmEl.className = `dp-timing-mini ${_pgCls}`;
    _tmEl.innerHTML = `<div class="dtm-main"><div class="dtm-eyebrow">타이밍은?</div><div class="dtm-word">${_tmWord}</div><div class="dtm-sub">${_tmSub}</div></div><div class="dtm-conf"><div class="dtm-conf-num">${conv}<span class="dtm-conf-pct">%</span></div><div class="dtm-conf-lbl">확신도</div></div>`;
  }
```

- [ ] **Step 3: 사용되지 않는 변수 정리**

`_tmWord`, `_tmSub` 변수 할당 코드도 제거. 각 conv 분기(conv >= 72, conv >= 42, else)에서 아래 줄들을 삭제:

conv >= 72 분기:
```javascript
    _tmWord = _pick(['🟢 지금 담아', '🟢 풀매각', '🟢 줍줍 ㄱㄱ', '🟢 슈팅각']);
    _tmSub = '담기 딱 좋음';
```

conv >= 42 분기:
```javascript
    _tmWord = _pick(['🟡 존버각', '🟡 눈팅 중', '🟡 기다려봐', '🟡 관망각']);
    _tmSub = '아직 눈팅 중';
```

else 분기:
```javascript
    _tmWord = _pick(['🔴 손절각', '🔴 탈출각', '🔴 손 빼셈', '🔴 패스각']);
    _tmSub = '손 빼셈';
```

그리고 상단 변수 선언도 수정:
```javascript
// 변경 전
let _pgCls, _pvWord, _pvReason, _pvBg, _tmWord, _tmSub;
// 변경 후
let _pgCls, _pvWord, _pvReason, _pvBg;
```

- [ ] **Step 4: 시각 확인**

종목 선택 → 배너 우측에 확신도%(예: 73%) 블록이 나타나는지 확인.
초록/노랑/빨강 세 케이스 모두 확인 (확신도 72이상 / 42~71 / 41이하).

- [ ] **Step 5: 커밋**

```bash
git add web_app/static/app.js
git commit -m "feat(drawer): 판단 포스터에 확신도% 통합, dp-timing-mini 렌더 제거"
```

---

### Task 3: CSS — 포스터 flex 레이아웃 + 확신도 블록 스타일

**Files:**
- Modify: `web_app/static/scanner.css:2480-2600`

**What this does:** `dp-verdict-poster`에 flex 적용, `.dvp-main` / `.dvp-conf` 스타일 추가, `dp-timing-mini` 관련 스타일 전체 제거.

- [ ] **Step 1: `dp-verdict-poster`에 flex 레이아웃 추가**

`scanner.css` 2480줄 `.dp-verdict-poster` 블록에 아래 속성 추가:

```css
.dp-verdict-poster {
  margin: 16px 20px 0;
  border-radius: 16px;
  padding: 22px 24px;
  color: white;
  position: relative;
  overflow: hidden;
  isolation: isolate;
  /* ↓ 추가 */
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
```

- [ ] **Step 2: `.dvp-main` 래퍼 스타일 추가**

`.dvp-eyebrow` 룰 바로 위에 삽입:

```css
.dvp-main {
  flex: 1;
  min-width: 0;
}
```

- [ ] **Step 3: `.dvp-conf` 확신도 블록 스타일 추가**

`.dvp-main` 바로 아래에 삽입:

```css
.dvp-conf {
  flex-shrink: 0;
  background: rgba(255,255,255,.18);
  border-radius: 12px;
  padding: 8px 14px;
  text-align: center;
}
.dvp-conf-num {
  font-size: 28px;
  font-weight: 900;
  color: white;
  line-height: 1;
}
.dvp-conf-pct {
  font-size: 14px;
  font-weight: 700;
}
.dvp-conf-lbl {
  font-size: 10px;
  color: rgba(255,255,255,.8);
  margin-top: 2px;
}
```

- [ ] **Step 4: `dp-hero-stats` 경계선 스타일 조정**

현재 `.dp-hero-stats`가 타이밍 섹션 안에 있어 border-top이 없음. 살까? 섹션으로 이동했으므로 상단 구분선 추가. `scanner.css`에서 `.dp-hero-stats` 룰을 찾아 추가:

```css
.dp-section-conclusion .dp-hero-stats {
  border-top: 1px solid var(--border);
}
```

- [ ] **Step 5: `dp-timing-mini` 관련 스타일 제거**

`scanner.css`에서 아래 룰 전체 삭제 (2536~약2600줄):

```
/* ── 언제? 탭 — 미니 판단 카드 ── */
.dp-timing-mini { ... }
.dtm-main { ... }
.dp-timing-mini::before { ... }
.dp-timing-mini.dvp-green { ... }
.dp-timing-mini.dvp-yellow { ... }
.dp-timing-mini.dvp-red { ... }
.dtm-eyebrow { ... }
.dtm-word { ... }
.dtm-sub { ... }
.dtm-conf { ... }
.dtm-conf-num { ... }
.dtm-conf-pct { ... }
.dtm-conf-lbl { ... }
```

- [ ] **Step 6: 전체 시각 확인**

앱 실행 후:
- [ ] 살까? 배너: 왼쪽에 word+reason, 우측에 확신도% 블록 정렬 확인
- [ ] 배너 아래 현재가/등락률/RS 스탯 행 표시 확인
- [ ] 언제? 섹션 상단에 컬러 카드 없음 확인
- [ ] dvp-green / dvp-yellow / dvp-red 세 상태 모두 레이아웃 확인

- [ ] **Step 7: 커밋**

```bash
git add web_app/static/scanner.css
git commit -m "style(drawer): 판단 포스터 flex 레이아웃, dvp-conf 스타일, dp-timing-mini 스타일 제거"
```
