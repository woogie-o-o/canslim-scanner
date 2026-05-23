# 종목 상세 드로어 시그널 이력 타임라인 설계

**작성일:** 2026-05-22
**대상 파일:** `web_app/history.py`, `web_app/app.py`, `web_app/static/app.js`, `web_app/templates/scanner.html`

## 배경

종목 리스트에서 종목을 클릭하면 상세 드로어가 열린다. 드로어는 현재 2축 사분면 카드, 진입 타이밍 카드, 4축 차트, 컨센서스 카드, 가격 차트를 보여준다. 모두 "지금 이 순간"의 스냅샷이라, 이 종목이 시간에 따라 좋아지고 있는지 나빠지고 있는지는 알 수 없다.

이미 매일 스캔 결과를 저장하는 시스템(`web_app/history.py`)이 있다. 스냅샷 파일은 `web_app/snapshots/scanner_{MARKET}_{YYYY-MM-DD}.json`, 포맷은 `{TICKER: {score, rank}, ...}`, 30일 보관. 하지만 이 데이터는 "어제 대비 점수/순위 변동"(`ScoreDelta`/`RankDelta`)에만 쓰이고, 드로어에서 추세를 직접 보여주는 데는 쓰이지 않는다.

## 목표

- 종목 상세 드로어에 그 종목의 등급(S/A/B/C)·진입 타이밍 변화를 한눈에 보여주는 타임라인을 추가한다.
- 기존 일별 스냅샷 인프라를 재사용한다.
- 백엔드 스코어링 로직·EntryStatus 계산·버킷 구조는 변경하지 않는다 (저장·표시 계층만 수정).

## 비목표

- 종합점수, 버킷, EntryStatus 계산식 변경 — 범위 밖.
- 리스트 페이지(드로어 밖)에 타임라인 노출 — 범위 밖.
- 스냅샷 보관 기간(30일) 변경 — 유지.
- 가격 차트/4축 차트 등 기존 카드 구조 변경 — 유지.

## 설계

### 1. 데이터 흐름

```
드로어 열림(openDetail) → loadSignalHistory(ticker, market)
    → GET /api/signal-history/<ticker>?market=<KR|US>
    → history.load_timeline(ticker, market)
    → [{date, grade, entry}, ...]  (오늘 포함 직전 14일, 달력일)
    → _renderSignalHistory(items) → dp-history-card 2줄 컬러 셀 스트립
```

`loadConsensus`·`loadDpFourAxis`가 이미 별도 lazy fetch로 동작하므로 같은 패턴을 따른다. 상세 응답 본체(`_ticker_detail_cache`)에는 이력을 포함하지 않는다 — 본체를 가볍게 유지하고, 스냅샷 파일 다수를 매 상세 조회마다 읽지 않기 위함.

### 2. 백엔드 — 스냅샷 스키마 확장 (`web_app/history.py`)

`save_snapshot`이 종목별로 저장하는 항목에 `entry` 필드를 추가한다.

기존:
```python
snap = {
    r["Ticker"]: {"score": ..., "rank": ...}
    for i, r in enumerate(sorted_by_score) if r.get("Ticker")
}
```

변경 후 — 각 항목이 `{"score": float, "rank": int, "entry": str|None}`. `entry`는 결과 dict의 `EntryStatus` 값(`STRONG`/`NEUTRAL`/`AVOID`/`GREEN`/`YELLOW`/`RED` 등), 없으면 `None`.

호환성: 구버전 스냅샷 파일은 `entry` 키가 없다. 읽는 쪽(`load_timeline`)이 `.get("entry")`로 접근해 결측 시 `None`을 쓰므로 마이그레이션 불필요. 등급 이력은 기존 `score`에서 소급 계산되지만, 진입 타이밍 이력은 이 변경 배포 후 저장된 날부터 쌓인다.

### 3. 백엔드 — 타임라인 조회 함수 (`web_app/history.py`)

신규 함수 `load_timeline(ticker: str, market: str) -> list[dict]`:

- 윈도우: 오늘(`date.today()`) 포함 **직전 14일**의 달력일.
- 각 달력일 `d`에 대해 `_load(market, d)` (기존 함수 재사용)로 스냅샷을 읽는다.
- 스냅샷 파일이 없으면(주말 등 스캔 미실행) → `{date, grade: None, entry: None}` ("빈 날").
- 스냅샷은 있으나 해당 `ticker`가 없으면 → `{date, grade: None, entry: None}` (빈 날과 동일 처리).
- 스냅샷에 ticker가 있으면 → `grade`는 저장된 `score`로 계산, `entry`는 `.get("entry")`.
- 반환 배열은 날짜 오름차순(과거 → 오늘).
- 항목 형식: `{"date": "YYYY-MM-DD", "grade": "S"|"A"|"B"|"C"|None, "entry": str|None}`.

등급 계산 — 신규 모듈 내 순수 함수 `_grade_from_score(score)`:

| 등급 | 조건 |
|---|---|
| S | score ≥ 75 |
| A | 60 ≤ score < 75 |
| B | 45 ≤ score < 60 |
| C | score < 45 |

`score`가 `None`이거나 숫자가 아니면 `None` 반환. 컷은 프론트 `_stockGrade`(app.js)와 정합 — 동일 임계값(75/60/45).

### 4. 백엔드 — 엔드포인트 (`web_app/app.py`)

신규 라우트 `GET /api/signal-history/<ticker>`:

- `market` 쿼리 파라미터 필수. 누락 또는 `KR`/`US` 외 값이면 `400`.
- `history.load_timeline(ticker, market)` 호출 → `{"ticker": ..., "market": ..., "timeline": [...]}` JSON 반환(`200`).
- 예외 발생 시 `500`과 함께 빈 timeline — 프론트가 카드를 숨기도록.

### 5. 프론트 — 드로어 카드 (`web_app/templates/scanner.html`)

- 신규 카드 컨테이너 `dp-history-card`를 사분면 카드(`dp-quadrant-card`) 아래에 배치.
- 카드 내부: 제목 "시그널 이력", 2줄 스트립 호스트 `dp-history-strip`, 양끝 날짜 라벨 영역.
- 신규 CSS — `.history-strip`(2줄 grid/flex), `.history-cell`(셀 사각형), 등급 줄은 Task 5에서 정의한 `.grade-S/A/B/C` 색을, 진입 줄은 `.entry-green/yellow/red` 색을 재사용. 빈 칸 클래스 `.history-cell-empty`는 회색(`var(--surface-page)` + 옅은 테두리). 모바일 좁은 화면에서 셀 크기 축소 `@media (max-width: 640px)`.

### 6. 프론트 — 렌더링 (`web_app/static/app.js`)

- `openDetail(ticker)` 안에서 기존 lazy 호출들과 함께 `loadSignalHistory(ticker, market)` 호출. `market`은 `openDetail`이 이미 알고 있는 현재 시장 값을 사용.
- `loadSignalHistory(ticker, market)`: `fetch('/api/signal-history/' + encodeURIComponent(ticker) + '?market=' + encodeURIComponent(market))` → 성공 시 `_renderSignalHistory(timeline)`, 실패 시 `dp-history-card`를 숨김(`display:none`) — 다른 카드 로딩을 방해하지 않음. 기존 `loadConsensus` 에러 처리 패턴과 동일.
- `_renderSignalHistory(items)`:
  - `items`가 비었거나 전부 빈 날이면 카드에 "이력 데이터가 아직 없어요" 안내 텍스트만, 스트립 미표시.
  - 그 외에는 2줄 스트립 렌더. 등급 줄: 각 항목 `grade`가 있으면 `grade-{G}` 색 셀, 없으면 `.history-cell-empty`. 진입 줄: `entry`를 색 클래스로 매핑(`STRONG/GREEN→green`, `NEUTRAL/YELLOW→yellow`, `AVOID/RED→red`, 그 외/결측→empty).
  - 각 셀 `title`(툴팁): `날짜 · 등급 · 진입 라벨`. 진입 라벨은 Task 2에서 만든 `_ENTRY_LABEL` 매핑(진입적기/눌림대기/부적합) 재사용.
  - 스트립 아래 양끝에 첫 날짜·마지막 날짜를 `M/D` 형식으로 표시.
  - 모든 동적 텍스트(날짜, 라벨)는 `esc()`로 이스케이프.

## 데이터 흐름 요약

```
[저장]  스캔 완료 → save_snapshot(results, market)
         → snapshots/scanner_{MARKET}_{날짜}.json: {TICKER: {score, rank, entry}}

[조회]  드로어 열림 → /api/signal-history/<ticker>?market=...
         → load_timeline: 14일 달력일 순회 → [{date, grade, entry}]
         → _renderSignalHistory → 2줄 컬러 셀 스트립
```

## 엣지 케이스

| 케이스 | 처리 |
|---|---|
| 스냅샷이 하나도 없음 | 카드에 "이력 데이터가 아직 없어요" 안내, 스트립 미표시 |
| 스캔 미실행일(주말 등) | 해당 날짜 회색 빈 칸 (`grade:null, entry:null`) |
| 스냅샷에 해당 ticker 없음 | 빈 날과 동일 — 회색 칸 |
| 구버전 스냅샷 (entry 키 없음) | `entry:null` → 진입 줄 회색 칸, 등급 줄은 정상 |
| score 결측/비숫자 | `grade:null` → 등급 줄 회색 칸 |
| `market` 쿼리 누락/잘못된 값 | 엔드포인트 400 |
| fetch 실패 / 500 | 프론트가 `dp-history-card` 숨김 |
| 등급 컷 경계값 (정확히 60) | A등급 (조건 `>= 60`) |

## 테스트

**`history.load_timeline` / `_grade_from_score` 단위 테스트 (pytest):**
- `_grade_from_score`: 75→S, 74→A, 60→A, 59→B, 45→B, 44→C, None→None, 비숫자→None.
- `load_timeline` — 임시 스냅샷 디렉토리 fixture로:
  - 정상 이력: 며칠치 스냅샷 → 날짜 오름차순 `{date, grade, entry}` 배열.
  - 빈 날: 중간 날짜 스냅샷 누락 → 그 날 `grade:null, entry:null`.
  - 구버전 스냅샷(entry 키 없음) → `entry:null`, `grade`는 정상.
  - ticker가 모든 스냅샷에 없음 → 전부 회색(`grade:null, entry:null`).
  - 윈도우 경계: 14일치 정확히 반환, 오늘 포함.

**엔드포인트 테스트 (Flask test client):**
- 정상: `GET /api/signal-history/AAPL?market=US` → 200, `timeline` 배열 포함.
- `market` 누락 → 400.
- 잘못된 `market` 값 → 400.

## 영향 범위

- 변경 파일: `web_app/history.py`(스키마 확장 + `load_timeline`/`_grade_from_score` 추가), `web_app/app.py`(신규 라우트), `web_app/static/app.js`(`loadSignalHistory`/`_renderSignalHistory` + `openDetail` 연결), `web_app/templates/scanner.html`(`dp-history-card` + CSS).
- 신규 함수: `_grade_from_score`, `load_timeline` (history.py), `loadSignalHistory`, `_renderSignalHistory` (app.js).
- 기존 함수 수정: `save_snapshot` (entry 필드 추가), `openDetail` (호출 추가).
- 데이터 마이그레이션: 불필요 (구버전 스냅샷은 읽기 시 `.get`으로 호환).
- 스코어링/EntryStatus 계산 백엔드 변경: 없음.
