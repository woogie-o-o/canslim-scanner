# 드로어 히어로 레이아웃 통합 설계

**날짜:** 2026-06-22
**범위:** detail drawer 상단 영역 (살까? + 언제? 섹션 상단부)

---

## 배경

드로어 상단에 살까? / 타이밍 / 히어로 스탯 세 요소가 분산되어 있어 시각적으로 정리되지 않은 느낌. 사용자가 옵션 A(풀배너 통합)를 선택함.

## 선택된 디자인: 옵션 A — 풀배너 통합

### Before (현재)
- **살까? 섹션** (`dp-section-conclusion`): `dp-verdict-poster` (word + reason만 포함)
- **언제? 섹션** (`dp-section-timing`): `dp-timing-mini` (미니 카드) + `dp-hero-stats` (현재가/등락률/RS) 별도

### After (변경 후)
- **살까? 섹션** (`dp-section-conclusion`):
  - `dp-verdict-poster` — 배너 안에 eyebrow + big word + reason 텍스트 + 확신도% (우측 블록) 통합
  - `dp-hero-stats` — 배너 바로 아래, 가로 3열 (현재가 / 등락률 / RS 등급)
- **언제? 섹션** (`dp-section-timing`): 종합점수 + 4축 차트 (미니 카드 없음)

## 변경 파일 및 작업

### 1. `web_app/templates/scanner.html`
- `dp-hero-stats` div를 `dp-section-timing`에서 `dp-section-conclusion` 하단으로 이동 (dp-fa-reasons 위)
- `dp-timing-mini` div 제거

### 2. `web_app/static/app.js`
- `_renderEntryVerdict()`: `dp-verdict-poster` innerHTML에 확신도 블록 우측 추가
  - 기존: `<div class="dvp-eyebrow">살까? 말까?</div><div class="dvp-word">…</div><div class="dvp-reason">…</div><div class="dvp-bg">…</div>`
  - 변경: 전체를 flex row로 감싸고 우측에 `<div class="dvp-conf"><div class="dvp-conf-num">${conv}<span>%</span></div><div class="dvp-conf-lbl">확신도</div></div>` 추가
- `dp-timing-mini` 렌더 코드 제거 (불필요)

### 3. `web_app/static/scanner.css`
- `.dp-verdict-poster`: `display: flex; align-items: flex-start; justify-content: space-between; gap: 16px;` 추가
- `.dvp-main`: 기존 컨텐츠(eyebrow/word/reason)를 감싸는 래퍼 추가, `flex: 1; min-width: 0;`
- `.dvp-conf`: 우측 확신도 블록 스타일 — `background: rgba(255,255,255,.18); border-radius: 12px; padding: 8px 14px; text-align: center; flex-shrink: 0;`
- `.dvp-conf-num`: `font-size: 28px; font-weight: 900; color: white; line-height: 1;`
- `.dvp-conf-lbl`: `font-size: 10px; color: rgba(255,255,255,.8);`
- `.dp-timing-mini` 관련 스타일 제거

## 성공 기준

- 드로어 열면 살까? 섹션에서 판단 배너 + 히어로 스탯이 한 덩어리로 보임
- 확신도%가 배너 우측에 표시됨
- 언제? 섹션에 미니 타이밍 카드가 없음
- 빨간/노란 케이스(dvp-red, dvp-yellow)도 동일하게 동작

## 범위 외

- 언제? 섹션 내 4축 차트, 쎄모미터, 종합 점수 영역: 변경 없음
- `dp-timing-mini` CSS 삭제 (dead code 정리)
