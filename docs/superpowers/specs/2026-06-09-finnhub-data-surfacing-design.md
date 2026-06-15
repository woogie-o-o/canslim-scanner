# 설계: Finnhub 무료 tier 데이터 전면 노출

> 작성일: 2026-06-09
> 목적: Finnhub 무료 tier로 가져오고 있으나 UI에 묻혀 있는 데이터를 전용 화면으로 끌어올리고, 안 쓰던 무료 엔드포인트(로고/프로필·내부자 심리·IPO·시장 뉴스)를 신규 배선한다.
> 범위: US 종목 전용. KR은 완전 숨김.

---

## 0. 배경 & 동기

현재 `finnhub_api.py`는 무료 tier에서 6개 엔드포인트를 호출한다 — 내부자 거래, 추천 변화, 실적 서프라이즈, 경쟁사, 뉴스(7일), 실시간 시세, 기본 재무. 호출 결과는 `/api/sentiment/<ticker>` 응답에 `_FH_*` 키로 머지되지만, **상당수가 한줄평 풀에만 녹아 있고 전용 UI가 없다** (`_FH_InsiderNet`, `_FH_EarnStreak`, `_FH_RecChange`, `_FH_Headlines`, `_FH_DaysToEarnings`).

이미 매 상세 패널 호출마다 돈을 주고(=API 콜) 받는 데이터인데 화면에 안 보인다. 추가로, 무료 tier인데 아직 안 쓰는 엔드포인트가 있다(`company_profile2`, `stock_insider_sentiment`, `ipo_calendar`, `general_news`).

## 1. 원칙 (불변)

- **US 종목 전용** — 무료 tier가 KR·ADR(`.HK`/6자리 코드) 전부 403. KR 상세 패널에서는 Finnhub 섹션을 **완전히 렌더하지 않는다** (`_FH_Available` + market gate 이중 방어).
- **lazy + 캐시 유지** — 신규 per-ticker 콜은 `/api/sentiment/<ticker>` 경로에만 추가. 이 경로는 상세 패널 열 때만 호출되고 5분 TTL 캐시가 있어 스캔 루프(수백 종목)와 무관하다. → **60 calls/min rate limit 영향 없음.**
- **README 금지선 준수** — 사실 데이터만 표시. 자동매매·AI 매수추천 텍스트·기대수익률 표기 없음.
- **4 phase 독립 배포** — 각 단계가 그 자체로 완결되어 단독 머지 가능.

## 2. 아키텍처 (3계층, 기존 패턴 그대로)

### 2.1 `finnhub_api.py` — 데이터 계층
기존 `_safe()` / `_cached()` / `_store()` / `_is_us_ticker()` 헬퍼를 그대로 재사용한다.

- `get_sentiment_data(ticker)` **확장**: 기존 6콜에 2콜 추가 (`company_profile2`, `stock_insider_sentiment`) → 총 8콜. 모두 `_safe` 래핑이라 일부 403이어도 나머지 키는 정상 반환.
- `get_market_context()` **신규**: per-ticker가 아닌 시장 전역 데이터. `ipo_calendar` + `general_news` 호출. 별도 TTL 캐시(기본 30분) — 시장 데이터는 종목별보다 변동이 느림.

신규/확장이 반환하는 정규화 필드:

| 소스 엔드포인트 | 정규화 필드 |
|---|---|
| `company_profile2` | `logo`, `ipo`(정확한 상장일), `shareOutstanding`, `finnhubIndustry`, `exchange`, `weburl` |
| `stock_insider_sentiment` | `mspr`(최근 월 -100~100), `mspr_trend`(월별 배열), `mspr_change` |
| `ipo_calendar` | `ipos`: `[{date, symbol, name, price, shares, ...}]` |
| `general_news` | `news`: `[{headline, url, source, datetime, category}]` |

### 2.2 `web_app/app.py` — 라우트 계층
- `/api/sentiment/<ticker>` 페이로드 **확장** (KR은 기존 `kr_not_supported` 분기 유지):
  `_FH_Logo`, `_FH_IpoDate`, `_FH_ShareOut`, `_FH_Industry`, `_FH_Exchange`,
  `_FH_MSPR`, `_FH_MSPRTrend`, `_FH_MSPRChange`
- `/api/market_context` **신규 라우트**: `{ipos: [...], news: [...]}` 반환 + 자체 메모리 캐시(모듈 패턴은 기존 `_sentiment_cache` 복제).

### 2.3 `web_app/static/app.js` — 렌더 계층
배치 결정: **기존 섹션에 통합** (전용 탭 신설 안 함).

- 상세 패널 헤더: 회사 로고 (없으면 티커 이니셜 fallback)
- 수급·심리 영역: 내부자 순매수/매도 칩, MSPR 방향 배지+스파크라인, 어닝 비트 스트릭 배지, 추천 업/다운그레이드 화살표
- 하단: 최근 7일 뉴스 리스트
- 신규 시장 맥락 섹션(시장 탭/매크로 영역): IPO 캘린더 + 시장 뉴스 피드
- **모든 Finnhub 섹션은 `_FH_Available === true` 가드** — false거나 KR이면 DOM 자체를 생성하지 않음.

## 3. 데이터 흐름

```
[상세 패널]
종목 클릭 → /api/ticker (즉시 paint)
         → /api/sentiment/<ticker> (lazy, 5분 캐시)
            → _FH_* 머지 → 로고·칩·MSPR·뉴스 렌더 (US만)

[시장 맥락]
탭 열기 → /api/market_context (30분 캐시)
        → IPO 캘린더 + 시장 뉴스 렌더
```

## 4. 단계 (Phase)

| Phase | 내용 | 백엔드 변경 | 난이도 | 비고 |
|---|---|---|---|---|
| **P1** | FH1~4 노출 (내부자·어닝스트릭·추천·뉴스) | **없음** | 하 | `_FH_*` 이미 페이로드에 존재. app.js 렌더만. 리스크 0. |
| **P2** | FH6 로고·프로필 | `company_profile2` 배선 | 중 | 헤더 로고 + 정확 IPO일·상장주식수·산업 |
| **P3** | FH7 내부자 심리 MSPR | `stock_insider_sentiment` 배선 | 중 | 월별 MSPR 스파크라인 + 방향 배지 |
| **P4** | FH8/9 시장 맥락 | `/api/market_context` 신규 | 중 | IPO 캘린더 + `general_news` 피드 |

각 phase는 독립적으로 배포 가능하며 P1이 ROI가 가장 높다(백엔드 변경 0).

## 5. 정직한 제약 명시

- **실적 D-Day(FH5)는 추정값 유지.** 정확한 다음 실적일은 `earnings_calendar` symbol-filter가 필요한데 이는 **유료(403)**다. 현재 `_FH_DaysToEarnings`는 마지막 분기 + 120일 cadence 추정이라 오차가 클 수 있다. → UI에 **"추정" 라벨을 명시**해 오인을 방지한다. 무료로는 정확도 개선 불가.
- **엔드포인트 무료 여부 실측 선행.** `company_profile2`/`stock_insider_sentiment`/`ipo_calendar`/`general_news`는 무료 tier로 알려져 있으나, 기존 모듈 docstring이 일부 엔드포인트를 403으로 기록한 전례가 있다. → **각 phase의 첫 작업은 `tools/test_finnhub.py`에 해당 엔드포인트 스모크 호출을 추가해 실측 확인.** 403이면 그 phase는 그 자리에서 중단하고 보고한다(UI 작업 전에).

## 6. 에러 처리 & 엣지 케이스

- 신규 콜 전부 `_safe()` 래핑 → 한 콜이 실패해도 해당 키만 빠지고 나머지는 정상.
- 로고 URL이 비면 → 티커 이니셜 원형 배지 fallback.
- MSPR 데이터가 없는 소형주 → MSPR 섹션 미표시(빈 카드 안 띄움).
- `ipo_calendar`/`general_news` 빈 응답 → "데이터 없음" 상태 표시.
- US-only: `_is_us_ticker()` + `/api/sentiment`의 market gate 이중 방어(이미 존재).

## 7. 테스트 전략

- **단위(`finnhub_api`)**: 신규 파서가 목(mock) 응답을 정규화 dict로 변환하는지 검증 (`company_profile2`/`insider_sentiment`/`ipo_calendar`/`general_news` 각각). `tests/` 디렉터리에 기존 패턴 존재.
- **graceful degradation**: 403/빈 응답/네트워크 실패 시 나머지 키가 정상 반환되는지.
- **US-only 가드**: KR 종목 응답에 `_FH_*`가 없고, 프론트가 FH 섹션을 미생성하는지(수동/스냅샷).

## 8. 안 하는 것 (YAGNI / 금지)

- `social_sentiment`(레딧/트위터) — **유료(403)**. 제외.
- **FH11 점수 엔진 통합** — 내부자 심리를 4축 점수에 가산하는 것은 회귀 위험이 크고 백테스트가 필요하다. 이번 spec 범위 밖, **별도 spec**으로 분리.
- 정확 실적 캘린더(symbol-filter) — 유료.
- 자동매매·AI 매수추천 텍스트·기대수익률 표기 — README 금지선.

---

## 9. 영향 받는 파일 요약

| 파일 | 변경 |
|---|---|
| `finnhub_api.py` | `get_sentiment_data` 확장(P2,P3), `get_market_context` 신규(P4) |
| `web_app/app.py` | `/api/sentiment` 페이로드 확장(P2,P3), `/api/market_context` 신규(P4) |
| `web_app/static/app.js` | 상세 패널 렌더(P1~P3), 시장 맥락 섹션(P4) |
| `tools/test_finnhub.py` | 각 phase 엔드포인트 스모크 추가 |
| `tests/` | 신규 파서 단위테스트 |
