# 종목스캐너 시각화 개선안 (월가 퀀트 패널)

> 작성일: 2026-06-04
> 분석: 퀀트 전략가 · 리테일 투자자 · 금융 데이터 시각화 엔지니어 · 리스크 검수자 4인 패널
> 대상: `web_app/templates/scanner.html`, `web_app/static/app.js`, `web_app/static/scanner.css`, `greedzone.py`

---

## 0. 한 줄 진단

지표가 많은 게 문제가 아니라 **위계·맥락·방향성이 없어서** 화면 전체가 똑같은 무게의 노이즈로 평탄화됨.
4명 만장일치 핵심 3가지:

1. **GreedZone 노란 뱃지가 방향 오해의 주범** — 높을수록 "추격매수 위험"인데 색·형식이 "인기 핫한 종목"으로 정반대로 읽힘
2. **"결국 사도 돼/위험해?" 결론이 5개 컬럼에 흩어짐** — 한 칸으로 결론화 필요
3. **raw 숫자에 맥락 부재** — `45pt`, `RSI 62`, `RS 87` → 게이지/구간/등급으로 변환

핵심 원칙: **색 토큰 분리 + 컬럼 축소가 차트 라이브러리보다 먼저.**

---

## 1. 즉시 적용 (CSS 위주, ~1-2시간)

### C1. 주가 등락 색을 위험색에서 분리 ⭐
- **문제**: `--destructive`(빨강) 하나가 *점수낮음·주가상승·위험경고·목표가하락* 4개 의미를 공유 → 빨강을 봐도 무슨 뜻인지 라벨을 읽어야 함 (pre-attentive 실패)
- **위치**: `scanner.css` 의 `.chg-up` / `.chg-down`
- **개선**:
```css
/* theme 변수 추가 */
--price-up:   #E03131;  /* 한국식 상승=빨강, 전용 토큰 */
--price-down: #1971C2;  /* 하락=파랑 */

.chg-up   { color: var(--price-up);   font-weight: 600; }
.chg-down { color: var(--price-down); font-weight: 600; }
```
- **효과**: 빨강의 의미 충돌 1차 해소. 가장 적은 노력 / 큰 효과.

### C2. 등급 뱃지 S/A/B/C 4색 명시 분리
- **문제**: S와 A가 같은 초록 계열이라 한눈에 구분 안 됨
- **위치**: `scanner.css` `.grade-badge` 계열
- **개선**:
```css
.grade-S { background: rgba(22,163,74,0.12);  color: #15803D; border: 1px solid rgba(22,163,74,0.3); }
.grade-A { background: rgba(2,132,199,0.12);  color: #0369A1; border: 1px solid rgba(2,132,199,0.3); }
.grade-B { background: rgba(217,119,6,0.12);  color: #B45309; border: 1px solid rgba(217,119,6,0.3); }
.grade-C { background: rgba(220,38,38,0.12);  color: #B91C1C; border: 1px solid rgba(220,38,38,0.3); }
```

### C3. RSI 미니바 스타일 준비
- **문제**: 테이블 RSI가 맨숫자(`62.3`)뿐 — 과매수/과매도 직관 0
- **위치**: `scanner.css` 신규 클래스
- **개선**:
```css
.rsi-cell { min-width: 70px; }
.rsi-num  { font-size: 13px; font-weight: 700; display: block; text-align: right; }
.rsi-seg-track { height: 3px; background: var(--surface-muted); border-radius: 2px; margin-top: 3px; overflow: hidden; }
.rsi-seg-fill  { height: 100%; border-radius: 2px; transition: width .3s; }
```
- (JS 연결은 J1에서)

### C4. RSI 온도계 마커 색 동적화 준비
- **문제**: `dp-thermo-marker` 테두리가 `#6B7280` 회색 고정 → RSI 70+ 과열에서도 회색이라 배경 그라데이션에 묻힘
- **위치**: `scanner.html` line ~496 / `scanner.css`
- **개선**:
```css
.dp-thermo-marker.zone-fear    { border-color: #1971C2; }
.dp-thermo-marker.zone-neutral { border-color: #6B7280; }
.dp-thermo-marker.zone-greed   { border-color: #E03131; }
```
- (JS 연결은 J3에서)

### C5. 리스크 신호 시각적 강화 (테마주 ⚠ / 위험 필터)
- **문제**: 투기성 테마 경고가 작은 회색 ⚠ 아이콘 → S등급 초록 큰 글씨에 묻힘. 긍정 신호보다 약함
- **위치**: `scanner.css` `.theme-warn`, 퀵필터 `⛔ 위험` 칩
- **개선**: `.theme-warn` 배경/테두리를 경고색으로, `⛔ 위험` 필터 칩을 다른 긍정 칩과 시각적으로 구분(테두리 빨강 등)
```css
.theme-warn { color:#B45309; background:#FEF3C7; border:1px solid #FCD34D; padding:1px 4px; border-radius:3px; font-size:10px; }
.chip[data-filter="laggard"] { border-color:#FCA5A5; color:#B91C1C; }
```

---

## 2. 중간 작업 (JS 렌더 수정, ~반나절)

### J1. GreedZone 뱃지 → 맥락 있는 게이지 ⭐⭐ (사용자가 헷갈린 바로 그것)
- **문제**: `🟡 45pt` — 분모 없음, 노란색이 "괜찮은 중간"으로 오독, 높을수록 위험인데 좋아보임
- **근거**(`greedzone.py:118-132`): 점수↑ = 저점 대비 더 오래·더 깊게 과열 = **추격매수 경고**
- **위치**: `app.js:1527`(모바일 카드) + 테이블 행 greed-badge 생성부
- **개선**:
```javascript
if (stock.GreedZone) {
  const gz = stock.GreedZoneScore || 0;
  const gzW = Math.min(100, gz);
  const gzColor = gz >= 70 ? '#DC2626' : gz >= 40 ? '#D97706' : '#F59E0B';
  greedHtml = `<span class="greed-badge-v2" title="저점 대비 과열 ${gz}/100 · ${stock.GreedZoneDays||0}일 연속${stock.GreedZoneEntry?' · 오늘 진입!':''} — 추격매수 주의">
    <span class="gz-label">⚠ 과열 ${gz}<span style="opacity:.6">/100</span></span>
    <span class="gz-bar-track"><span class="gz-bar-fill" style="width:${gzW}%;background:${gzColor}"></span></span>
  </span>`;
}
```
```css
.greed-badge-v2 { display:inline-flex; flex-direction:column; font-size:10px; font-weight:700; color:#B45309; padding:2px 6px; border-radius:4px; background:#FEF3C7; border:1px solid #FCD34D; }
.gz-bar-track { height:2px; background:rgba(0,0,0,0.1); border-radius:1px; width:40px; margin-top:2px; }
.gz-bar-fill  { height:100%; border-radius:1px; display:block; }
```
- **핵심 변경**: `🟡` → `⚠`, `45pt` → `과열 45/100`, 40↑ 주황 / 70↑ 빨강으로 위험 강도 색 반영

### J2. 테이블 RSI 숫자 → 숫자 + 색 미니바
- **위치**: `app.js` `renderStockRow`의 RSI `<td>` (line ~1493)
- **개선**:
```javascript
const rsiColor = stock.RSI>70?'var(--destructive)':stock.RSI<30?'#1971C2':'var(--success)';
// <td class="right rsi-cell">
//   <span class="rsi-num">${rsi}</span>
//   <div class="rsi-seg-track"><div class="rsi-seg-fill"
//        style="width:${Math.min(100,Math.max(0,stock.RSI))}%;background:${rsiColor}"></div></div>
// </td>
```

### J3. RSI 온도계 마커 동적 색상 연결
- **위치**: `app.js:3115` 부근 (`dp-thermo-marker` 설정부)
- **개선**:
```javascript
const marker = document.getElementById('dp-thermo-marker');
if (marker) {
  const rsiVal = d.RSI ?? 50;
  marker.className = 'dp-thermo-marker ' +
    (rsiVal < 30 ? 'zone-fear' : rsiVal > 70 ? 'zone-greed' : 'zone-neutral');
}
```

### J4. 컬럼 축소 + 토글 (14 → 7컬럼)
- **문제**: 14컬럼 평탄화. `설명/평균거래량/시총/증권사목표가`는 스크리닝 1차 판단에 불필요
- **1차 노출 권장**: `# | 종목 | 점수바 | 진입등급 | 등락 | RSI미니바 | 핵심이유`
- **이동 대상**: 설명(이미 `showStockPopup`에 있음)·평균거래량·시총·목표가 → 호버 팝업/상세 드로어
- **위치**: `app.js` `renderStockTable` / `scanner.html` 테이블 헤더
- **개선**: 각 td/th에 `data-col` 부여 후 localStorage 토글
```javascript
function _initColVisibility() {
  const hidden = JSON.parse(localStorage.getItem('sc_hidden_cols') || '["desc-col","_AvgVol20","_MarketCap"]');
  hidden.forEach(col => document.querySelectorAll(`[data-col="${col}"]`).forEach(el => el.style.display='none'));
}
```

### J5. 모바일 카드 정보 보강
- **문제**: `renderMobileCard`(app.js:1513~)에 RSI·거래량·핵심이유 없음 → 데스크탑과 의사결정 근거 불일치. GreedZone 뱃지가 종목명 inline이라 긴 이름서 줄바꿈 깨짐
- **개선**: 카드에 row3(RSI 색숫자 + 거래량 급증 🔥) 추가, greed 뱃지를 별도 라인으로 분리

---

## 3. 큰 작업 (차트/구조 변경, 하루~이틀)

### B1. '결론 셀' 통합 ⭐⭐ (리테일이 가장 원하는 것)
- **문제**: "지금 사도 돼/위험해?" 답이 점수·등급·타이밍·RSI·핵심이유·GreedZone 6곳에 분산
- **개선**: 시그널 컬럼을 **결론 셀**로 재설계
```
┌────────────────────┐
│ 🟢 지금 진입 근접     │  ← 큰 글씨 결론 (초록/노랑/빨강)
│ S등급 · RS 87        │  ← 근거 서브텍스트
│ ⚠ 과열·테마주        │  ← 리스크 하단 배지
└────────────────────┘
```
- 퀄리티(등급)와 타이밍(진입)을 **명시적으로 분리 라벨**("등급:" / "타이밍:")해 색 혼동 제거

### B2. RS Rating 도넛 게이지 (상세 패널)
- **문제**: `dp-rs-value`가 숫자만. RS는 핵심 알파인데 맥락 없음
- **개선**: SVG 도넛 호(외부 라이브러리 불필요), `_renderRsRating(d)` 신규 함수
```javascript
function _renderRsRating(d) {
  const rsEl = document.getElementById('dp-rs-value');
  const rs = d.RSRating ?? null; if (!rsEl || rs==null) return;
  const pct=Math.min(100,Math.max(0,rs)), r=28,cx=34,cy=34,sw=6, circ=2*Math.PI*r, dash=(pct/100)*circ;
  const col = rs>=80?'#16A34A':rs>=50?'#F59E0B':'#DC2626';
  rsEl.innerHTML = `<svg width="68" height="68" viewBox="0 0 68 68">
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--surface-muted)" stroke-width="${sw}"/>
    <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${col}" stroke-width="${sw}"
      stroke-dasharray="${dash.toFixed(1)} ${circ.toFixed(1)}" stroke-linecap="round" transform="rotate(-90 ${cx} ${cy})"/>
    <text x="${cx}" y="${cy}" text-anchor="middle" dominant-baseline="central" font-size="16" font-weight="800" fill="${col}">${Math.round(rs)}</text>
  </svg>`;
}
```

### B3. 테이블 행 미니 사분면 dot (퀄리티 × 타이밍)
- **문제**: 상세창의 4축/사분면 차트가 좋은데 목록에선 안 보임 → 클릭 전 판단 불가
- **개선**: 점수 컬럼에 24×24 SVG dot로 (좋은회사 × 좋은타이밍) 위치 즉시 표시. `_renderQuadrant` 로직 재사용
```javascript
const es=stock.EntryScore??50, ts=stock.TotalScore??0;
const qx=Math.round((Math.min(100,es)/100)*20), qy=Math.round((1-Math.min(100,ts)/100)*20);
const qCol=(ts>=60&&es>=50)?'#16A34A':(ts>=60)?'#D97706':(es>=50)?'#2563EB':'#DC2626';
// 24x24 SVG: 사분면 격자 + circle(qx+2, qy+2, r=4, fill=qCol)
```

---

## 4. 리스크 검수자 별도 권고 (법적/윤리)

> "리스크 정보는 스크롤 밑에 숨기고 초록 S등급은 28px로 박은 레이아웃 — 투자자 보호 의지가 없는 거냐"

- **R1. 면책을 상세 드로어에도 배치** — 현재 상단 1곳뿐, 스크롤하면 사라짐. RSI 온도계 아래 "투자조언 아님" 9px 텍스트는 사실상 안 보임
- **R2. 드로다운 리스크 카드 승격** — MDD -28%, 수면하 234일 등 핵심 위험이 상세창 하단 12px 테이블에 숨음 → Hero 카드 레벨로 끌어올리기
- **R3. 거짓 정밀성 완화** — `45pt` `RS 87`처럼 두 자리 정수는 실제보다 정밀해 보임. 구간/밴드(낮음·중간·높음) 병기 권장
- **R4. 과열 중복 경고 정리** — RSI 70+ 과매수와 GreedZone 과열은 상관 높은 같은 팩터. 둘을 독립 신호처럼 병렬 표시하면 정보량 1인데 시각 무게 2배

---

## 5. 권장 실행 순서

1. **C1 (등락 색 분리)** — 5분, 즉시 체감
2. **J1 (GreedZone 게이지)** — 당신이 헷갈린 문제 직접 해결
3. **C2·C3·J2·J3 (등급색 + RSI 미니바/마커)** — 색·게이지 일괄
4. **J4 (컬럼 축소)** — 노이즈 제거
5. **B1 (결론 셀)** — 가장 큰 UX 임팩트, 마지막에 제대로
6. **R1·R2 (면책·리스크 승격)** — 출시 전 필수

> 각 스니펫의 line 번호는 분석 시점 근사치 — 적용 전 해당 함수(`renderStockRow`, `renderMobileCard`, `_renderQuadrant`, thermo 설정부)를 grep으로 재확인할 것.
