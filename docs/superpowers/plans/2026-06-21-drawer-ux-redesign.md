# 드로워 UX 재설계 — 질문 기반 탭 구조 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 드로워 섹션 탭을 데이터 타입 이름(결론/타이밍/회사/상세)에서 질문(살까?/언제?/얼마나?/뭔데?)으로 바꾸고, 각 탭 첫 줄에 "사세요 / 기다리세요 / 보류예요" 또는 "지금이에요 / 조금 기다려요 / 아직이에요" 한 단어 답을 표시한다.

**Architecture:** 백엔드 변경 없음. scanner.html에 HTML 골격(verdict-word, timing-word, split-plan 영역), app.js에 두 개의 짧은 렌더 함수, scanner.css에 스타일 추가. 분할매수 플랜을 기존 `#dp-entry-verdict` 카드에서 분리해 얼마나? 섹션으로 이동.

**Tech Stack:** Vanilla JS, HTML, CSS (기존 프로젝트 패턴 준수)

## Global Constraints

- 백엔드(Python) 파일 일절 수정 금지
- 기존 id/class 변경 금지 — 새 id만 추가
- 스크롤 기반 섹션 nav 구조 유지 (탭 전환 구조로 변경 안 함)
- 이모지는 기존 버튼에 있는 것만 유지 또는 변경 — 추가 금지

---

### Task 1: 섹션 Nav 버튼 텍스트 변경

**Files:**
- Modify: `web_app/templates/scanner.html:274-278`

**Interfaces:**
- Produces: 버튼 텍스트가 질문형으로 바뀐 nav

- [ ] **Step 1: scanner.html 섹션 nav 버튼 4개 텍스트 수정**

현재 (line 274-278):
```html
<button class="dp-section-btn active" onclick="dpScrollTo('dp-section-conclusion',this)">🎯 결론</button>
<button class="dp-section-btn" onclick="dpScrollTo('dp-section-timing',this)">📈 타이밍</button>
<button class="dp-section-btn" onclick="dpScrollTo('dp-section-company',this)">🏢 회사</button>
<button class="dp-section-btn" onclick="dpScrollTo('dp-section-detail',this)">📊 상세</button>
<button class="dp-section-btn" onclick="dpScrollTo('dp-group-ref',this)">📎 참고</button>
```

변경 후:
```html
<button class="dp-section-btn active" onclick="dpScrollTo('dp-section-conclusion',this)">🎯 살까?</button>
<button class="dp-section-btn" onclick="dpScrollTo('dp-section-timing',this)">📈 언제?</button>
<button class="dp-section-btn" onclick="dpScrollTo('dp-section-company',this)">🏢 뭔데?</button>
<button class="dp-section-btn" onclick="dpScrollTo('dp-section-detail',this)">📊 얼마나?</button>
<button class="dp-section-btn" onclick="dpScrollTo('dp-group-ref',this)">📎 참고</button>
```

- [ ] **Step 2: 브라우저에서 드로워 열어 버튼 텍스트 확인**

드로워 상단 nav가 `🎯 살까? | 📈 언제? | 🏢 뭔데? | 📊 얼마나? | 📎 참고` 로 표시되면 OK.

- [ ] **Step 3: 커밋**

```bash
git add web_app/templates/scanner.html
git commit -m "feat(drawer): rename section nav to question-based labels (살까?/언제?/뭔데?/얼마나?)"
```

---

### Task 2: 살까? 탭 — verdict-word 영역 추가 (HTML + CSS)

**Files:**
- Modify: `web_app/templates/scanner.html` — `#dp-section-conclusion` 내부 상단
- Modify: `web_app/static/scanner.css` — `.dp-verdict-word` 스타일 추가

**Interfaces:**
- Produces: `<div id="dp-verdict-word">` — app.js에서 채울 빈 컨테이너

- [ ] **Step 1: scanner.html `dp-section-conclusion` 최상단에 verdict-word div 삽입**

현재 (line 284-290):
```html
<section id="dp-section-conclusion" class="dp-section-conclusion">
  <div id="dp-fa-haiku" class="dp-oneliner-poster" style="display:none;"></div>
  <div id="dp-news-bar" class="dp-news-bar" style="display:none;">
```

변경 후:
```html
<section id="dp-section-conclusion" class="dp-section-conclusion">
  <div id="dp-verdict-word" class="dp-verdict-word" style="display:none;"></div>
  <div id="dp-fa-haiku" class="dp-oneliner-poster" style="display:none;"></div>
  <div id="dp-news-bar" class="dp-news-bar" style="display:none;">
```

- [ ] **Step 2: scanner.css에 `.dp-verdict-word` 스타일 추가**

기존 `#dp-fa-reasons` 스타일 블록 아래에 추가:
```css
/* ── 살까? 탭 — verdict word ── */
.dp-verdict-word {
  text-align: center;
  padding: 20px 20px 8px;
}
.dp-verdict-word .dvw-text {
  font-size: 36px;
  font-weight: 900;
  letter-spacing: -0.5px;
  line-height: 1;
}
.dp-verdict-word .dvw-sub {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 4px;
}
.dvw-buy   { color: #16A34A; }
.dvw-wait  { color: #D97706; }
.dvw-hold  { color: #DC2626; }
```

- [ ] **Step 3: 브라우저에서 요소 존재 확인 (아직 비어있어야 함)**

DevTools → `document.getElementById('dp-verdict-word')` 가 null이 아니면 OK.

- [ ] **Step 4: 커밋**

```bash
git add web_app/templates/scanner.html web_app/static/scanner.css
git commit -m "feat(drawer): add verdict-word container to 살까? section"
```

---

### Task 3: 살까? 탭 — app.js에서 verdict-word 렌더

**Files:**
- Modify: `web_app/static/app.js` — `_renderEntryVerdict` 함수 (line ~3629 이후 `// ── 렌더 ──` 블록)

**Interfaces:**
- Consumes: `conv` (0-100, 기존 계산값), `color` (기존), `label` (기존)
- Produces: `#dp-verdict-word` 채움

- [ ] **Step 1: `_renderEntryVerdict` 함수의 `// ── 렌더 ──` 블록 직전에 verdict-word 렌더 추가**

현재 (line 3629-3640):
```js
  // ── 렌더 ──
  card.style.display = '';
  card.style.borderLeft = `3px solid ${color}`;
  card.innerHTML = `
    <div class="ev-head">
      <span class="ev-icon">${icon}</span>
      <span class="ev-label" style="color:${color}">${label}</span>
      <span class="ev-conf" style="background:${color}18;color:${color}">확신도 ${conv}%</span>
    </div>
    ${pills ? `<div class="ev-pills">${pills}</div>` : ''}
    ${splitHtml}
  `;
}
```

변경 후:
```js
  // ── verdict-word 렌더 (살까? 탭 첫 줄) ──
  const _vwEl = document.getElementById('dp-verdict-word');
  if (_vwEl) {
    let _vwText, _vwCls, _vwSub;
    if (conv >= 72)      { _vwText = '사세요';    _vwCls = 'dvw-buy';  _vwSub = `확신도 ${conv}% — 진입 유리`; }
    else if (conv >= 42) { _vwText = '기다리세요'; _vwCls = 'dvw-wait'; _vwSub = `확신도 ${conv}% — 조건 부족`; }
    else                 { _vwText = '보류예요';   _vwCls = 'dvw-hold'; _vwSub = `확신도 ${conv}% — 진입 부적합`; }
    _vwEl.style.display = '';
    _vwEl.innerHTML = `<div class="dvw-text ${_vwCls}">${_vwText}</div><div class="dvw-sub">${_vwSub}</div>`;
  }

  // ── 렌더 ──
  card.style.display = '';
  card.style.borderLeft = `3px solid ${color}`;
  card.innerHTML = `
    <div class="ev-head">
      <span class="ev-icon">${icon}</span>
      <span class="ev-label" style="color:${color}">${label}</span>
      <span class="ev-conf" style="background:${color}18;color:${color}">확신도 ${conv}%</span>
    </div>
    ${pills ? `<div class="ev-pills">${pills}</div>` : ''}
    ${splitHtml}
  `;
}
```

- [ ] **Step 2: 브라우저에서 살까? 섹션 확인**

종목 클릭 → 드로워 열림 → "살까?" 섹션 상단에 "사세요" / "기다리세요" / "보류예요" 중 하나가 큰 글씨로 표시되면 OK.

- [ ] **Step 3: 커밋**

```bash
git add web_app/static/app.js
git commit -m "feat(drawer): render verdict-word (사세요/기다리세요/보류예요) in 살까? section"
```

---

### Task 4: 언제? 탭 — timing-word 영역 추가 및 렌더

**Files:**
- Modify: `web_app/templates/scanner.html` — `#dp-section-timing` 상단 (line ~292)
- Modify: `web_app/static/scanner.css` — `.dp-timing-word` 스타일
- Modify: `web_app/static/app.js` — `loadDpFourAxis` 내 `_stars` 계산 직후

**Interfaces:**
- Consumes: `_stars` (1-5, line ~4275 기준으로 이미 계산됨)
- Produces: `#dp-timing-word` 채움

- [ ] **Step 1: scanner.html `dp-section-timing` 최상단에 timing-word div 삽입**

현재 (line 292-294):
```html
<section id="dp-section-timing" class="dp-section-timing">
  <div class="dp-hero-flex">
```

변경 후:
```html
<section id="dp-section-timing" class="dp-section-timing">
  <div id="dp-timing-word" class="dp-timing-word" style="display:none;"></div>
  <div class="dp-hero-flex">
```

- [ ] **Step 2: scanner.css에 `.dp-timing-word` 스타일 추가**

`.dp-verdict-word` 블록 바로 아래에 추가:
```css
/* ── 언제? 탭 — timing word ── */
.dp-timing-word {
  text-align: center;
  padding: 16px 20px 4px;
}
.dp-timing-word .dtw-text {
  font-size: 32px;
  font-weight: 900;
  letter-spacing: -0.5px;
  line-height: 1;
}
.dp-timing-word .dtw-sub {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 4px;
}
.dtw-now   { color: #16A34A; }
.dtw-soon  { color: #D97706; }
.dtw-wait  { color: #DC2626; }
```

- [ ] **Step 3: app.js `loadDpFourAxis` — `_stars` 설정 직후 timing-word 렌더 추가**

현재 (line ~4283):
```js
    set('dp-fa-stars', '★'.repeat(_stars) + '☆'.repeat(5 - _stars));
    const _starMeaningTbl = {
```

변경 후:
```js
    set('dp-fa-stars', '★'.repeat(_stars) + '☆'.repeat(5 - _stars));

    // ── timing-word 렌더 (언제? 탭 첫 줄) ──
    const _twEl = document.getElementById('dp-timing-word');
    if (_twEl) {
      let _twText, _twCls, _twSub;
      if (_stars >= 4)      { _twText = '지금이에요';    _twCls = 'dtw-now';  _twSub = '진입 타이밍 충족'; }
      else if (_stars === 3) { _twText = '조금 기다려요'; _twCls = 'dtw-soon'; _twSub = '추가 확인 필요'; }
      else                   { _twText = '아직이에요';   _twCls = 'dtw-wait'; _twSub = '타이밍 미충족'; }
      _twEl.style.display = '';
      _twEl.innerHTML = `<div class="dtw-text ${_twCls}">${_twText}</div><div class="dtw-sub">${_twSub}</div>`;
    }

    const _starMeaningTbl = {
```

- [ ] **Step 4: 브라우저에서 언제? 섹션 확인**

드로워 → "언제?" 버튼 클릭 → 섹션 최상단에 "지금이에요" / "조금 기다려요" / "아직이에요" 중 하나가 큰 글씨로 표시되면 OK.

- [ ] **Step 5: 커밋**

```bash
git add web_app/templates/scanner.html web_app/static/scanner.css web_app/static/app.js
git commit -m "feat(drawer): render timing-word (지금이에요/조금 기다려요/아직이에요) in 언제? section"
```

---

### Task 5: 얼마나? 탭 — 분할매수 플랜 이동

**Files:**
- Modify: `web_app/templates/scanner.html` — `#dp-section-detail` 최상단에 `#dp-split-plan` 추가
- Modify: `web_app/static/app.js` — `_renderEntryVerdict` 에서 splitHtml을 `#dp-split-plan` 에 주입, `#dp-entry-verdict` 카드에서 제거

**Interfaces:**
- Consumes: `splitHtml` (기존 계산값, Task 2/3에서 변경 없음)
- Produces: `#dp-split-plan` — 분할매수 플랜이 얼마나? 섹션에 표시

- [ ] **Step 1: scanner.html `dp-section-detail` 최상단에 split-plan 컨테이너 삽입**

현재 (line ~517):
```html
<div class="dp-card-group" id="dp-section-detail">
  <button class="dp-card-group-toggle" onclick="toggleCardGroup(this)">
    <span>📊 상세 분석</span>
```

변경 후:
```html
<div class="dp-card-group" id="dp-section-detail">
  <div id="dp-split-plan" style="padding:4px 20px 0;"></div>
  <button class="dp-card-group-toggle" onclick="toggleCardGroup(this)">
    <span>📊 상세 분석</span>
```

- [ ] **Step 2: app.js `_renderEntryVerdict` — splitHtml을 `#dp-split-plan`에 분리 렌더**

현재 (line ~3638-3640):
```js
    ${pills ? `<div class="ev-pills">${pills}</div>` : ''}
    ${splitHtml}
  `;
}
```

변경 후:
```js
    ${pills ? `<div class="ev-pills">${pills}</div>` : ''}
  `;

  // 분할매수 플랜은 얼마나? 섹션으로 분리
  const _spEl = document.getElementById('dp-split-plan');
  if (_spEl) _spEl.innerHTML = splitHtml;
}
```

- [ ] **Step 3: 브라우저에서 분할매수 플랜 위치 확인**

- 살까? 섹션 `dp-entry-verdict` 카드에 분할매수 플랜이 **없어야** 함
- 얼마나? 섹션 상단에 "📊 분할매수 플랜 ATR 기반 3회" 카드가 표시되면 OK

- [ ] **Step 4: 커밋**

```bash
git add web_app/templates/scanner.html web_app/static/app.js
git commit -m "feat(drawer): move split-plan to 얼마나? section"
```

---

## 완료 기준 체크리스트

- [ ] 드로워 상단 nav: 살까? / 언제? / 뭔데? / 얼마나? / 참고
- [ ] 살까? 섹션 첫 줄: "사세요" (초록) / "기다리세요" (노랑) / "보류예요" (빨강) 중 하나
- [ ] 언제? 섹션 첫 줄: "지금이에요" (초록) / "조금 기다려요" (노랑) / "아직이에요" (빨강) 중 하나
- [ ] 분할매수 플랜이 얼마나? 섹션에 표시됨
- [ ] 살까? 섹션 verdict 카드(dp-entry-verdict)에 분할매수 플랜 없음
- [ ] 뭔데? 섹션(기존 회사) 내용 변경 없음
- [ ] 기존 기능 깨짐 없음 (공포탐욕, RSI, 4축 그리드, BF Score 등)
