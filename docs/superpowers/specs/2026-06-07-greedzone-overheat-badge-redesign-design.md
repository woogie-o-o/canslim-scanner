# GreedZone "과열" 배지 리디자인

**날짜:** 2026-06-07
**대상:** 스캐너 목록의 GreedZone 과열 배지 (`_greedBadge`, `.greed-badge-v2`)
**방향:** B안 — 틴트 + 세그먼트 게이지 (브레인스토밍에서 선택)

## 목적

스캐너 목록에 표시되는 GreedZone "과열" 배지의 **가시성**과 **심미성**을 높이고, **라이트·다크 테마 모두에서 자연스럽게** 보이도록 한다. 의미(저점 대비 과열 = 추격매수 위험, 역추세 경고)는 그대로 유지한다.

## 현재 상태와 문제점

현재 배지(`app.js:_greedBadge`, `scanner.css:.greed-badge-v2`)는 다음과 같다:

```
⚠ 과열 55/100
▔▔▔▔        ← 2px 게이지바
```

- **너무 작다** — 10px 글씨 + 2px 게이지바라 한눈에 안 들어옴
- **다크 테마 미대응** — 배경/글자색이 하드코딩(`#FEF3C7` / `#B45309`)이라 다크 모드에서 붕 뜸
- **행 높이 불안정** — 2단 세로(inline-flex column) 구조라 목록 행 높이를 들쭉날쭉하게 만듦
- **위험도 비교가 색에만 의존** — 숫자 25/55/88을 색만으로 구분 → 빠른 비교 어려움

## 새 디자인 (B안)

한 줄 구성: `🔥` + **5단 도트 게이지** + **점수 숫자**. 은은한 틴트 배경 알약(pill).

```
🔥 ●●●◦◦ 55        (경고 / mid)
```

### 시각 규칙

- **강도 티어** (현재 임계값 유지):
  - `gz >= 70` → **hi (고위험)** : 빨강 계열
  - `gz >= 40` → **mid (경고)** : 주황 계열
  - `gz <  40` → **lo (주의)** : 노랑/주황 계열
- **도트 게이지**: 5칸, 20점당 1칸 채움.
  - 채운 칸 수 `on = gz <= 0 ? 0 : clamp(ceil(gz / 20), 1, 5)`
  - 예: 25→2칸, 55→3칸, 88→5칸
  - 채운 칸은 `currentColor` 불투명, 빈 칸은 `opacity:.20`
- **아이콘**: `🔥` (기존 `⚠`에서 변경 — "과열=열" 은유에 직결, 가시성↑)
- **'과열' 글자**: 표시하지 않음 (🔥 + 게이지 + 툴팁으로 의미 전달, 폭 절약)
- **툴팁(title)**: 기존 그대로 유지 —
  `저점 대비 과열 {gz}/100 · {days}일 연속[ · 오늘 신규 진입!] — 추격매수 주의 (역추세 경고)`

### 테마 대응

라이트/다크에서 각각 틴트 배경 + 글자색을 분리 지정한다. 다크는 앱의 `.dark` 클래스 하위 규칙으로 처리.

| 티어 | 라이트 (배경 / 글자) | 다크 (배경 / 글자) |
|------|----------------------|--------------------|
| lo   | `rgba(217,119,6,.14)` / `#B45309` | `rgba(245,158,11,.18)` / `#FBBF24` |
| mid  | `rgba(234,88,12,.16)` / `#C2410C` | `rgba(249,115,22,.20)` / `#FB923C` |
| hi   | `rgba(220,38,38,.16)` / `#DC2626` | `rgba(248,113,113,.20)` / `#F87171` |

## 구현 범위

### 1. `web_app/static/app.js` — `_greedBadge()` 재작성

기존 2단(라벨 + 게이지바) HTML 대신 한 줄 pill을 반환한다.

```js
function _greedBadge(stock) {
  if (!stock || !stock.GreedZone) return '';
  const gz   = stock.GreedZoneScore || 0;
  const tier = gz >= 70 ? 'hi' : gz >= 40 ? 'mid' : 'lo';
  const on   = gz <= 0 ? 0 : Math.min(5, Math.max(1, Math.ceil(gz / 20)));
  let segs = '';
  for (let i = 0; i < 5; i++) segs += `<i class="${i < on ? 'on' : ''}"></i>`;
  const tip = `저점 대비 과열 ${gz}/100 · ${stock.GreedZoneDays || 0}일 연속`
            + `${stock.GreedZoneEntry ? ' · 오늘 신규 진입!' : ''} — 추격매수 주의 (역추세 경고)`;
  return ` <span class="gz-badge ${tier}" title="${esc(tip)}">`
       + `🔥<span class="gz-seg">${segs}</span>`
       + `<span class="gz-val">${gz}</span>`
       + `</span>`;
}
```

호출부(목록 렌더)는 변경 없음 — 함수 시그니처/반환 형식(앞 공백 포함 HTML 문자열) 동일.

### 2. `web_app/static/scanner.css` — 배지 스타일 교체

기존 `.greed-badge-v2`, `.gz-label`, `.gz-denom`, `.gz-bar-track`, `.gz-bar-fill` 블록(727~755행)을 제거하고 새 `.gz-badge` 규칙으로 대체한다.

```css
.gz-badge{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;margin-left:4px;
  border-radius:7px;font-size:11px;font-weight:800;font-variant-numeric:tabular-nums;
  line-height:1;white-space:nowrap;vertical-align:middle;cursor:help}
.gz-badge .gz-seg{display:inline-flex;gap:2px}
.gz-badge .gz-seg i{width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.20}
.gz-badge .gz-seg i.on{opacity:1}
.gz-badge.lo{background:rgba(217,119,6,.14);color:#B45309}
.gz-badge.mid{background:rgba(234,88,12,.16);color:#C2410C}
.gz-badge.hi{background:rgba(220,38,38,.16);color:#DC2626}
.dark .gz-badge.lo{background:rgba(245,158,11,.18);color:#FBBF24}
.dark .gz-badge.mid{background:rgba(249,115,22,.20);color:#FB923C}
.dark .gz-badge.hi{background:rgba(248,113,113,.20);color:#F87171}
```

## 엣지 케이스

- `GreedZone`이 false/없음 → 배지 미표시 (기존과 동일, 가드 유지)
- `GreedZoneScore`가 0 또는 누락 → 숫자 `0`, 도트 0칸, lo 티어
- `GreedZoneScore`가 100 초과 가능성 → 도트는 5칸으로 clamp, 숫자는 원값 표시
- 툴팁 텍스트는 `esc()`로 이스케이프 유지

## 테스트 / 검증

- `_greedBadge` 전용 단위 테스트는 현재 없음. 선택적으로 node 평가 방식(`test_entry_label_js.py` 패턴)으로 티어/도트수 계산을 검증하는 테스트를 추가할 수 있음 (gz 0/25/55/88/100 → tier·on 칸수).
- 시각 검증: 라이트·다크 테마에서 강도 3종(주의/경고/고위험) 목록 행 렌더 육안 확인.

## 비목표 (YAGNI)

- GreedZone 점수 산출 로직(`greedzone.py`)은 손대지 않음 — 표시만 개선
- 다른 "과열" 표기(RSI 카드, Kalman OVERHEATED)는 이번 범위 밖
- 애니메이션/펄스 효과는 넣지 않음 (정적 배지로 충분)
