# 종목 등급 · 진입 타이밍 라벨 분리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 리스트의 명령형 시그널 라벨을 종합점수 파생 등급(S/A/B/C)으로 바꾸고, 진입 타이밍 신호등을 라벨 배지로 승격해 "종목 퀄리티"와 "진입 타이밍" 두 축이 충돌 없이 한눈에 보이게 한다.

**Architecture:** 표시 계층(app.js / scanner.html)만 수정한다. 백엔드 스코어링·버킷·EntryStatus 계산식은 변경하지 않는다. 새 순수 함수 `_stockGrade(totalScore)`가 `TotalScore`를 등급 문자로 변환하고, 기존 `_entryLight()`는 아이콘 전용에서 아이콘+라벨 배지로 바뀐다. STORY_STOCK 버킷은 `OneLinerTag` 필드로 판별한다.

**Tech Stack:** Vanilla JS (브라우저 전역 스크립트), Flask 템플릿 inline CSS. JS 테스트 프레임워크 없음 — `_stockGrade` 단위 테스트는 Node 기반 standalone assert 스크립트로 작성한다.

---

## File Structure

- `web_app/static/app.js` — 신규 `_stockGrade()`, 수정 `_entryLight()` / `_renderSignalHtml()` / `_renderEntryCard()`
- `web_app/templates/scanner.html` — 등급 배지·진입 타이밍 배지·스토리 칩 CSS, 필터 칩 라벨
- `tests/test_stock_grade.js` — `_stockGrade()` 단위 테스트 (신규)

배경 사실 (조사 완료):
- `_stockGrade`가 쓸 점수 필드는 `stock.TotalScore` (숫자 또는 결측).
- STORY_STOCK 판별 필드는 `stock.OneLinerTag` (`one_liner.annotate`가 채움, 값이 `'STORY_STOCK'`).
- `_ENTRY_ICON` (app.js:123) 은 이미 STRONG/NEUTRAL/AVOID/GREEN/YELLOW/RED → 🟢🟡🔴 매핑 보유.
- `_entryLight(stock)` (app.js:362-379) 은 `<span class="entry-light" title="...">${ico}${aqBadge}</span>` 반환.
- `_renderSignalHtml(signal, stock)` (app.js:381-396) 은 `_entryLight()` + `signal-badge` 를 `signal-row` 안에 조립.
- 필터 칩은 scanner.html:1976 `data-filter="strong"` = "🔥 강력매수".
- `.signal-row`/`.entry-light`/`.signal-badge` CSS 는 scanner.html:1044-1060, 546-555.

---

### Task 1: `_stockGrade()` 순수 함수 + 단위 테스트

**Files:**
- Modify: `web_app/static/app.js` (`_signalTier` 함수 바로 뒤, app.js:92 다음 줄)
- Test: `tests/test_stock_grade.js`

- [ ] **Step 1: Write the failing test**

`tests/test_stock_grade.js` 생성:

```javascript
// _stockGrade() 단위 테스트 — JS 프레임워크가 없어 standalone Node 스크립트로 작성.
// app.js 에서 _stockGrade 함수 소스를 정규식으로 추출해 격리 평가한다 (순수 함수, DOM 의존 없음).
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const src = fs.readFileSync(
  path.join(__dirname, '..', 'web_app', 'static', 'app.js'), 'utf8');

const m = src.match(/function _stockGrade\s*\([\s\S]*?\n\}/);
if (!m) { console.error('FAIL: _stockGrade 함수를 app.js 에서 찾지 못함'); process.exit(1); }
const _stockGrade = new Function(m[0] + '\nreturn _stockGrade;')();

const cases = [
  [75, 'S'], [80, 'S'], [100, 'S'],
  [74, 'A'], [60, 'A'],
  [59, 'B'], [45, 'B'],
  [44, 'C'], [0, 'C'],
  [null, null], [undefined, null], ['', null], [NaN, null], ['abc', null],
];
let pass = 0;
for (const [input, expected] of cases) {
  const got = _stockGrade(input);
  assert.strictEqual(got, expected,
    `_stockGrade(${JSON.stringify(input)}) = ${JSON.stringify(got)}, 기대값 ${JSON.stringify(expected)}`);
  pass++;
}
console.log(`PASS: _stockGrade ${pass}/${cases.length} cases`);
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node tests/test_stock_grade.js`
Expected: FAIL — `_stockGrade 함수를 app.js 에서 찾지 못함` (아직 함수 없음)

- [ ] **Step 3: Write minimal implementation**

`web_app/static/app.js` 의 `_signalTier` 함수가 끝나는 줄(`}` at app.js:92) 바로 다음에 추가:

```javascript

// 종합점수(TotalScore) → 종목 등급 S/A/B/C. 숫자가 아니면 null.
function _stockGrade(totalScore) {
  const n = Number(totalScore);
  if (totalScore == null || totalScore === '' || Number.isNaN(n)) return null;
  if (n >= 75) return 'S';
  if (n >= 60) return 'A';
  if (n >= 45) return 'B';
  return 'C';
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node tests/test_stock_grade.js`
Expected: `PASS: _stockGrade 14/14 cases`

- [ ] **Step 5: Commit**

```bash
git add web_app/static/app.js tests/test_stock_grade.js
git commit -m "feat: add _stockGrade pure function for S/A/B/C stock grading"
```

---

### Task 2: `_entryLight()` 를 아이콘+라벨 배지로 승격

**Files:**
- Modify: `web_app/static/app.js:362-379` (`_entryLight` 함수 전체)

- [ ] **Step 1: 진입 타이밍 라벨 매핑 상수 추가**

`web_app/static/app.js` 의 `_ENTRY_ICON` 정의(app.js:123-124) 바로 다음 줄에 추가:

```javascript
const _ENTRY_LABEL = { STRONG: '진입적기', NEUTRAL: '눌림대기', AVOID: '부적합',
                       GREEN: '진입적기', YELLOW: '눌림대기', RED: '부적합' };
```

- [ ] **Step 2: `_entryLight()` 를 라벨 배지로 교체**

`web_app/static/app.js:362-379` 의 `_entryLight` 함수 전체를 아래로 교체:

```javascript
function _entryLight(stock) {
  if (!stock || !stock.EntryStatus) return '';
  const st = stock.EntryStatus;
  const ico = _ENTRY_ICON[st] || '⚪';
  const lbl = _ENTRY_LABEL[st] || '';
  const cls = _ENTRY_COLOR[st] || 'neutral';
  const phr = stock.EntryPhrase || '';
  const sc  = stock.EntryScore != null ? `진입 타이밍 ${stock.EntryScore}/100` : '';
  let tip = phr ? `${phr}${sc ? ' (' + sc + ')' : ''}` : sc;
  let aqBadge = '';
  if (stock.AQ_Verdict || stock.EntryScore_aq != null) {
    const vc = stock.AQ_VerdictCode;
    const col = vc === 'BUY' ? '#16A34A' : vc === 'ACCUMULATE' ? '#F59E0B' : vc === 'AVOID' ? '#DC2626' : '#6b7280';
    const reg = stock.AQ_Regime ? ` · ${stock.AQ_Regime}` : '';
    const aqSc = stock.EntryScore_aq != null ? ` AQ${Math.round(stock.EntryScore_aq)}` : '';
    tip += ` | AgentQuant: ${stock.AQ_Verdict || '—'}${reg}${aqSc}`;
    aqBadge = `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${col};margin-left:2px;vertical-align:middle;" title="${esc(stock.AQ_Verdict||'')}"></span>`;
  }
  return `<span class="entry-badge entry-${cls}" title="${esc(tip)}">${ico}${lbl ? `<span class="entry-badge-label">${esc(lbl)}</span>` : ''}${aqBadge}</span>`;
}
```

변경점: 클래스 `entry-light` → `entry-badge entry-<색상>`, 아이콘 뒤에 라벨 `<span>` 추가.

- [ ] **Step 3: 수동 확인 (테스트 프레임워크 없음)**

Run: `node -e "const s=require('fs').readFileSync('web_app/static/app.js','utf8'); new Function(s.match(/function _entryLight[\s\S]*?\n\}/)[0]); console.log('syntax OK');"`
Expected: `syntax OK` (구문 오류 없음 확인). 실제 렌더 검증은 Task 5 CSS 후 브라우저에서 수행.

- [ ] **Step 4: Commit**

```bash
git add web_app/static/app.js
git commit -m "feat: promote entry light dot to icon+label entry timing badge"
```

---

### Task 3: 등급 배지 / 스토리 칩을 `_renderSignalHtml()` 에 통합

**Files:**
- Modify: `web_app/static/app.js:381-396` (`_renderSignalHtml` 함수)

- [ ] **Step 1: `_renderSignalHtml()` 에 등급 배지 분기 추가**

`web_app/static/app.js:381-396` 의 `_renderSignalHtml` 함수 전체를 아래로 교체:

```javascript
function _renderSignalHtml(signal, stock) {
  const { base, tags } = _splitSignal(signal);
  const tr = _trKo(base || '—');
  // 종목 퀄리티 축: STORY_STOCK 버킷은 "스토리" 칩, 그 외는 TotalScore 파생 등급.
  // 등급/칩을 못 만들면 기존 시그널 라벨로 폴백.
  let qualityHtml;
  if (stock && stock.OneLinerTag === 'STORY_STOCK') {
    qualityHtml = `<span class="story-chip" title="스토리 종목 — 등급 척도 비적용">스토리</span>`;
  } else {
    const g = stock ? _stockGrade(stock.TotalScore) : null;
    qualityHtml = g
      ? `<span class="grade-badge grade-${g}" title="종합점수 ${Math.round(Number(stock.TotalScore))} 기준 등급">${g}</span>`
      : `<span class="signal-badge" style="color:${signalColor(base)};background:${signalBg(base)}">${esc(tr)}</span>`;
  }
  let h = `<div class="signal-row">${_entryLight(stock)}${qualityHtml}</div>`;
  if (tags.length) {
    h += '<div class="signal-tags">';
    for (const t of tags) {
      const clean = t.replace(/[\u{1F525}\u{1F514}]/gu, '').trim();
      const label = _TAG_KO[clean] || clean;
      const cls = /BREAKOUT|EPS|VOL/.test(t) ? 'sig-tag-hot' : /LOW|LIQ/.test(t) ? 'sig-tag-warn' : 'sig-tag-info';
      h += `<span class="sig-tag ${cls}">${esc(label)}</span>`;
    }
    h += '</div>';
  }
  return h;
}
```

- [ ] **Step 2: 구문 확인**

Run: `node -e "require('fs').readFileSync('web_app/static/app.js','utf8'); console.log('read OK');" && node --check web_app/static/app.js`
Expected: `read OK` 그리고 `node --check` 통과 (오류 출력 없음). 만약 `node --check` 가 브라우저 전역(`document` 등)으로 실패하지 않고 순수 구문만 검사하므로 통과해야 함.

- [ ] **Step 3: Commit**

```bash
git add web_app/static/app.js
git commit -m "feat: render S/A/B/C grade badge and story chip in signal column"
```

---

### Task 4: 필터 칩 라벨 리네이밍

**Files:**
- Modify: `web_app/templates/scanner.html:1976` (`data-filter="strong"` 칩)

- [ ] **Step 1: "강력매수" 칩 라벨을 "S·A등급" 으로 변경**

`web_app/templates/scanner.html:1976` 한 줄을 아래로 교체 (필터 로직·`data-filter` 값은 그대로):

```html
        <button class="chip" data-filter="strong" title="시그널이 돌파(BREAKOUT)·강한 주도주(STRONG LEADER)·강모멘텀(MOMENTUM) 중 하나인 종목 — 종목 퀄리티 상위권">🔥 S·A등급</button>
```

- [ ] **Step 2: 확인**

Run: `node -e "const s=require('fs').readFileSync('web_app/templates/scanner.html','utf8'); console.log(s.includes('🔥 S·A등급') ? 'PASS' : 'FAIL');"`
Expected: `PASS`

- [ ] **Step 3: Commit**

```bash
git add web_app/templates/scanner.html
git commit -m "feat: relabel strong-buy filter chip to S/A grade"
```

---

### Task 5: 등급 배지 · 진입 타이밍 배지 · 스토리 칩 CSS

**Files:**
- Modify: `web_app/templates/scanner.html` (`.entry-light` 블록, scanner.html:1052-1060 부근)

- [ ] **Step 1: `.entry-light` CSS 를 새 배지 스타일로 교체/확장**

`web_app/templates/scanner.html:1052-1060` 의 `.entry-light { ... }` 와 `.signal-row .signal-badge { max-width: 100%; }` 사이에, `.entry-light` 규칙은 유지하고(타 위치 호환) 그 다음에 신규 규칙을 삽입한다. `.entry-light` 블록(1052-1059) 바로 뒤, `.signal-row .signal-badge` 줄(1060) 앞에 추가:

```css
.entry-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 22px;
  padding: 0 7px;
  border-radius: 100px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  flex-shrink: 0;
}
.entry-badge-label { font-size: 11px; }
.entry-badge.entry-green  { color: var(--success);     background: rgba(0,192,115,0.10); }
.entry-badge.entry-yellow { color: var(--warning);     background: rgba(255,146,0,0.10); }
.entry-badge.entry-red    { color: var(--destructive); background: rgba(240,68,82,0.10); }
.entry-badge.entry-neutral{ color: var(--text-secondary); background: var(--surface-subtle); }

.grade-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 800;
  flex-shrink: 0;
}
.grade-badge.grade-S { color: #fff; background: #7C3AED; }
.grade-badge.grade-A { color: #fff; background: #16A34A; }
.grade-badge.grade-B { color: #fff; background: #2563EB; }
.grade-badge.grade-C { color: #fff; background: #9CA3AF; }

.story-chip {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 100px;
  font-size: 11px;
  font-weight: 700;
  color: #B45309;
  background: rgba(217,119,6,0.12);
  white-space: nowrap;
  flex-shrink: 0;
}

@media (max-width: 640px) {
  .entry-badge-label { display: none; }
  .signal-row { gap: 3px; }
}
```

(모바일 좁은 화면에서는 진입 배지 라벨을 숨겨 아이콘만 — 등급 배지 + 진입 아이콘 2개가 한 줄에 들어가도록.)

- [ ] **Step 2: 확인**

Run: `node -e "const s=require('fs').readFileSync('web_app/templates/scanner.html','utf8'); console.log(['.entry-badge','.grade-badge.grade-S','.story-chip'].every(c=>s.includes(c))?'PASS':'FAIL');"`
Expected: `PASS`

- [ ] **Step 3: 브라우저 확인**

Flask 앱 실행 후 `http://localhost:5000` 리스트에서 한 행에 "등급 배지 + 진입 배지"(예: `A 🟢진입적기`)가 함께 보이는지, STORY_STOCK 종목은 "스토리" 칩이 보이는지, 모바일 폭(≤640px)에서 진입 라벨이 숨고 아이콘만 남는지 확인.

- [ ] **Step 4: Commit**

```bash
git add web_app/templates/scanner.html
git commit -m "feat: add CSS for grade badge, entry timing badge, and story chip"
```

---

### Task 6: 세부 페이지 진입 타이밍 카드 어휘 통일

**Files:**
- Modify: `web_app/static/app.js:173-204` (`_renderEntryCard` 함수 내 헤드라인 표시부)

배경: 백엔드 `_compute_entry_status` 의 label("진입 강함"/"관망"/"진입 부적합")은 변경하지 않는다(비목표). 프론트에서 진입 타이밍 카드 헤드라인이 리스트 배지와 같은 어휘(진입적기/눌림대기/부적합)를 쓰도록 매핑만 한다. `_renderQuadrant` 의 사분면 라벨("좋은 회사 · 나쁜 타이밍" 등)은 구조·문구 유지.

- [ ] **Step 1: `_renderEntryCard()` 헤드라인에 어휘 통일 매핑 적용**

`web_app/static/app.js:184-190` 영역 — 현재 코드:

```javascript
  const _pp = phrase.split(' · ');
  const _headline = plan.headline_action || _pp[0] || phrase;
  const _reason = plan.one_reason || _pp.slice(1).filter(Boolean).slice(0, 2).join(' · ');
  setText('dp-entry-phrase', _headline);
```

이것을 아래로 교체 (`_ENTRY_LABEL` 은 Task 2 에서 추가됨):

```javascript
  const _pp = phrase.split(' · ');
  let _headline = plan.headline_action || _pp[0] || phrase;
  // 진입 타이밍 어휘를 리스트 배지(진입적기/눌림대기/부적합)와 통일.
  const _entryLbl = _ENTRY_LABEL[st];
  if (_entryLbl) _headline = `${ico} ${_entryLbl}`;
  const _reason = plan.one_reason || _pp.slice(1).filter(Boolean).slice(0, 2).join(' · ');
  setText('dp-entry-phrase', _headline);
```

- [ ] **Step 2: 구문 확인**

Run: `node --check web_app/static/app.js`
Expected: 오류 출력 없음 (통과)

- [ ] **Step 3: 브라우저 확인**

리스트에서 종목 클릭 → 세부 패널의 진입 타이밍 카드 헤드라인이 "🟢 진입적기" / "🟡 눌림대기" / "🔴 부적합" 형태로 리스트 배지와 같은 어휘를 쓰는지 확인. EntryStatus 결측 종목은 기존 헤드라인 유지되는지 확인.

- [ ] **Step 4: Commit**

```bash
git add web_app/static/app.js
git commit -m "feat: unify entry timing vocabulary on detail page with list badges"
```

---

## Self-Review Notes

- **스펙 커버리지:** 등급 배지(Task 1·3·5), 진입 타이밍 배지(Task 2·5), 필터 칩 정리(Task 4), 세부 페이지 어휘 통일(Task 6), STORY_STOCK 칩(Task 3·5), 모바일 반응형(Task 5) — 스펙 4개 설계 절 + 엣지케이스 모두 매핑됨.
- **엣지케이스:** TotalScore 결측/비숫자 → `_stockGrade` 가 null → `_renderSignalHtml` 이 기존 signal-badge 폴백(Task 3). EntryStatus 결측 → `_entryLight` 가 빈 문자열 반환(기존 동작 유지). 경계값 60 → `n >= 60` 으로 A등급(Task 1 테스트가 검증).
- **타입 일관성:** `_stockGrade` 는 `'S'|'A'|'B'|'C'|null` 반환, `_ENTRY_LABEL` 은 Task 2 에서 정의되어 Task 6 에서 재사용. 클래스명 `grade-S/A/B/C`, `entry-green/yellow/red/neutral`, `story-chip` 이 app.js 와 scanner.html 양쪽에서 일치.
- **백엔드 무변경:** 모든 Task 가 app.js / scanner.html 만 수정. `quant_nexus_v20.py`·`one_liner.py`·`app.py` 미변경.
