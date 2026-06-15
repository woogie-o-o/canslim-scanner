# 인수인계 문서 — 드로워 UX/UI 리디자인

## 현재 세션에서 완료된 작업

### 1. 드로워 구조 변경 (scanner.html)
- **2-column (dp-left/dp-right) 제거** -> single-column flow
- 새 섹션 순서: `dp-hero-zone` -> `dp-chart-section` -> `dp-analysis-section` -> `dp-group-company` -> `dp-group-ref`
- `dp-section-nav` 5개 탭(요약/차트/분석/기업/참고) 추가
- `dp-hero-summary` 래퍼로 score + stats 묶음 (현재 stacked 레이아웃)
- 한줄평 포스터(`dp-fa-haiku`)를 hero zone 맨 위로 이동
- 기업/참고 정보를 collapsible `dp-card-group`으로 분리

### 2. CSS 스타일링 (scanner.css)
- Section nav: underline 탭 스타일 (pill에서 변경)
- Tab nav: border 없는 subtle pill 스타일
- Score: 52px, SF Pro Display, "종합 점수" 라벨
- Signal badge: 26px pill
- Stats: border 없는 flex 가로 스트립 (표 느낌 제거 중)
- 섹션 배경 교차: hero(card) -> chart(surface-page) -> analysis(card)
- Chart section: border-top 제거, 배경 교차로 구분
- Analysis section: card bg + border-top
- 모바일 반응형: stats 2x2 wrap, 패널 풀스크린
- Detail panel: 28px radius, 부드러운 shadow

### 3. 매수/매도 텍스트 제거
**HTML (scanner.html):**
- 필터 칩 title: "SELL/AVOID" -> "위험 등급"
- 면책 문구: "매수 추천이 아닙니다" -> "투자 조언이 아닙니다"
- 내부자 카드: 매수/매도 -> 취득/처분

**JS (app.js):**
- 인사이더 라벨: buy:'매수' -> '취득', sell:'매도' -> '처분'
- 순매수/순매도 -> 순취득/순처분
- 시그널 번역 맵: BUY->'관심', SELL->'경계', ACCUMULATE->'주목'
- Kalman fallback: '매도/회피' -> '경계'
- 추천 변화: '매수 N 매도 N' -> '긍정 N 부정 N'
- 기술지표 설명: '매수/매도 흐름' -> '유입/유출 흐름'
- ORB 신호 설명: '매수 신호' -> '진입 신호'
- 컨센서스 opinion 색상: includes('매수') -> /긍정|관심|강한/.test()

**유지한 것 (변경 불필요):**
- 과매수/과매도 (RSI 기술용어)
- 공매도 (데이터 라벨)
- 트리비아 텍스트 (역사적 사실)
- 내부 JS enum 키 ('buy'/'sell' as code identifiers)

### 4. 컨센서스 개선
- yfinance 타임아웃 5초 -> 8초 (app.py)
- 에러 로깅 console.debug -> console.warn (app.js)

---

## 미완료 — 다음 세션에서 구현 필요

### 핵심: Hero Zone "한줄평 아래" 리디자인

**사용자 피드백**: "한줄평 밑에 표로 되어있는게 좀 뭔가 아마츄어같애"

**3인 디자이너 합의안 (Apple/Robinhood/Toss):**

Zone A/B/C 3단 역피라미드 구조:

```
[한줄평 포스터]

Zone A — 결론 (점수 + 시그널)
┌──────────────────────────────────────────────┐
│  87점  │  STRONG MOMENTUM  [리스크:낮음]      │
│  (60px,│  [리더뱃지]                          │
│  bold) │                                     │
└──────────────────────────────────────────────┘

Zone B — 근거 (4-stat grid)
┌───────────┬───────────┬───────────┬──────────┐
│ 현재가     │ 등락률     │ RSI       │ RS 등급   │
│ $182.54   │ +2.3%     │ 54        │ 88       │
└───────────┴───────────┴───────────┴──────────┘

Zone C — 위치 (RSI 온도계)
──●──────────────────────────────────────────
극도공포  공포  중립  탐욕  극도탐욕
```

**구체적 디자인 사양:**
- Zone A: `display:flex`, 점수(왼쪽) | 수직구분선 | 시그널스택(오른쪽)
- 점수: 60px, font-weight 800, letter-spacing -0.04em, color transition 0.3s
- 시그널뱃지: pill -> rectangular badge (border-radius 6px, uppercase)
- Zone B: `display:grid; grid-template-columns:repeat(4,1fr)`, 셀 간 border 구분선
- Zone C: 온도계 track 8px, inset shadow, marker spring ease
- 모바일(768px): Zone B를 2x2 grid로

**유지할 element ID (11개):**
dp-score, dp-signal, dp-risk-gauge, dp-leader-badge, dp-price, dp-day-chg, dp-thermo-label, dp-thermo-action, dp-rs-value, dp-rs-label, dp-thermo-marker

---

## 파일 위치

| 파일 | 역할 |
|------|------|
| `web_app/templates/scanner.html` | 드로워 HTML 구조 (라인 257~320 dp-hero-zone) |
| `web_app/static/scanner.css` | 드로워 스타일 (라인 1837~1910 hero zone) |
| `web_app/static/app.js` | JS 로직 (element ID로 DOM 조작) |
| `web_app/app.py` | 서버 API (라인 2268 consensus endpoint) |

## 주의사항
- 매수/매도 관련 텍스트 절대 사용 금지 (사용자 반복 지시)
- element ID 변경 금지 (app.js에서 참조)
- CSS 변수는 theme.css 시스템 사용 (var(--card), var(--brand) 등)
- git status: 3개 파일 수정됨 (scanner.html, scanner.css, app.js) + app.py — 아직 커밋 안됨
