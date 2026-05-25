# 멀티배거 파인더 메뉴 설계

**Date**: 2026-05-25
**Status**: Implemented
**Scope**: Flask 종목스캐너에 독립 페이지 `/multibagger` 추가. US 종목 중 잠재적 멀티배거 후보를 PASS / WATCH 두 레이어로 식별하고, 과거 5년 10배 종목과 회고 검증(DIFF) 제공.
**Inspiration source**: Yartseva, *The Alchemy of Multibagger Stocks* (CAFE Working Paper 33) — 영감이며 충실 매핑 대상은 아님. 실제 베거 식별 효과 우선.

---

## 1. 결정된 컨텍스트

- **Form factor**: 독립 페이지 `/multibagger` (scanner.html과 분리)
- **Universe**: US 단독 (NYSE/NASDAQ)
- **Scoring 철학**: 하이브리드 — 하드필터 게이트(F1~F8) + 정규화 점수(Core 6 + Bonus 4)로 랭킹
- **데이터 범위**: 풀스펙. 기존 스캔 캐시 + yfinance 보강(자산·EBITDA YoY, 52w 가격) + FRED 10Y
- **Threshold UI**: 디폴트 고정, "고급" 패널 펴치기로 튜닝
- **출력 모양**: 3 레이어 — PASS / WATCH / DIFF(회고 검증)

## 2. 아키텍처

기존 US 스캔 캐시(`cache_v19/`) 위에 어댑터 1개 얹는 방식.

```
web_app/
  multibagger.py            ← 후보 산출 + 보강 + 게이트/점수 핵심
  multibagger_rates.py      ← FRED 10Y fetch (24h TTL)
  multibagger_backtest.py   ← DIFF: 5년 10배 명단 추출 (수동 트리거)
  templates/multibagger.html
  static/multibagger.css
  static/multibagger.js
  tests/test_multibagger_*.py

cache_v19/
  multibagger_us.pkl        ← PASS/WATCH 결과, 12h TTL
  multibagger_rates.pkl     ← DGS10, 24h TTL
  baggers_us.pkl            ← 5년 10배 명단, 수동 갱신
```

**데이터 흐름**

```
사용자 → GET /multibagger
       → GET /api/multibagger
         ├─ 캐시 fresh → 즉시 반환
         └─ 캐시 stale → BG 워커 트리거 + stale 즉시 반환 + X-Warming 헤더

BG 워커 (_multibagger_warmup_loop):
  1. _scan_results_cache[("US","BALANCED","")]에서 베이스
  2. F1+F2 1차 하드필터 → 후보 풀 (예상 200~500)
  3. 8-worker ThreadPool 보강:
     income_stmt / balance_sheet / cashflow → YoY들
     history(1y) → 52w high/low, 1M·3M return
     ROIC 자체 계산
     insider transactions, buyback (TTM)
  4. multibagger_rates.pkl에서 DGS10
  5. 게이트 평가 → 점수 → tie-break → 분류
  6. pkl + 인메모리 캐시 저장
```

## 3. 베거 식별 지표 셋

### 1차 하드필터 (PASS 자격, F1~F8)

| # | 지표 | 임계값 | 인포그래픽 5요소 |
|---|---|---|---|
| F1 | 시총 sweet spot | $200M ≤ MarketCap ≤ $2B | 규모 |
| F2 | 수익성 체력 | EBITDA > 0 AND FCF > 0 | 수익성 |
| F3 | 자본효율 | ROIC ≥ 10% OR ROIC YoY 개선 | 수익성(자본 낭비 X) |
| F4 | 밸류에이션 | FCF yield ≥ 5% OR P/B ≤ 3.0 | 밸류에이션 |
| F5 | 성장의 질 | Revenue YoY ≥ 5% AND EBITDA YoY ≥ Revenue YoY | 투자(질) |
| F6 | 자본배분 효율 | EBITDA YoY ≥ Assets YoY | 투자(인포그래픽 핵심) |
| F7 | 고금리 생존력 | ICR ≥ 3.0 AND Debt/EBITDA ≤ 3.0 | 금리 |
| F8 | 진입 시점 | 52w high 대비 −10% ~ −50% AND 1M return ≤ +30% | 과열 회피 |

**F7 자동 강화**: DGS10 ≥ 4.0%일 때 ICR≥4.0, Debt/EBITDA≤2.5로 임계 상향.

### 2차 보너스 (랭킹 가점, B1~B4)

| # | 조건 | 가점 |
|---|---|---|
| B1 | sector ∈ {Healthcare, Technology, Consumer Discretionary} | +10 |
| B2 | 최근 3M net insider buy > 0 | +10 |
| B3 | TTM buyback yield > 0 | +5 |
| B4 | 직전 YoY > 1년 전 YoY (매출 가속도) | +10 |

### 분기 룰

- **PASS**: F1~F8 모두 통과
- **WATCH**: F1·F2·F8 필수 통과, F3~F7 중 1~2개 부족
- **DIFF**: 별도 데이터셋 (섹션 5)

**결측 처리**: Core 요소 결측은 가중치 0 + 재정규화. 결측 3개 이상 → 결과 제외. 결측 게이트 → WATCH 강등.

## 4. 점수화

```
Score = CoreScore × 0.7 + BonusScore × 0.3   (cap 100)
CoreScore = mean(Q1..Q6 0-100 정규화)
BonusScore = sum(B1..B4)
```

| Q | 산출 | 정규화 |
|---|---|---|
| Q1 자본효율 | ROIC | clamp(10%, 30%) → 0-100 |
| Q2 밸류에이션 | max(FCF yield 점수, B/M 점수) | FCF 5%→0, 15%→100 / B/M 0.33→0, 1.0→100 |
| Q3 성장 질 | EBITDA YoY − Revenue YoY | 0pp→50, +10pp→100, −5pp→0 |
| Q4 자본배분 | EBITDA YoY − Assets YoY | 0pp→50, +15pp→100, −10pp→0 |
| Q5 재무 안정성 | min(ICR 점수, Debt/EBITDA 점수) | ICR 3→0, 10→100 / D/E 3→0, 0→100 |
| Q6 매출 가속도 | Revenue YoY (latest) | 5%→0, 30%→100 |

**Tie-break**: Q4 → Q1 → Q2 → 시총 작은 순.

**Score 해설 한 줄**: 기여 큰 요소 2 + 깎인 요소 1 자동 추출 → "ROIC 18%·자본배분 우수, 다만 진입가 부담".

## 5. DIFF (5년 10배 회고 검증)

**대상**: 2021-01-01 → 현재. 5y_return ≥ 10x.

**배치 스크립트** (`web_app/multibagger_backtest.py`)
1. cache_v19/sectors_us.pkl 유니버스
2. yfinance Ticker.history 5년치 (8-worker, 종목당 timeout 15s)
3. 200거래일 미만 → 상폐 추정, skip
4. 10x 종목 추출
5. start 시점 펀더멘털 스냅샷 시도. 결측 시 `prices_only` 플래그
6. `cache_v19/baggers_us.pkl` 저장 (git 커밋 가능 크기)

**실행**: 수동 트리거 (`python -m web_app.multibagger_backtest`). 자동 워밍업 X. 분기 1회 갱신 권장.

**렌더링**: 각 베거에 대해 start 시점 펀더멘털로 F1~F8 평가 → PASS/WATCH/MISS 분류. 페이지에 "지난 5년 N개 베거 중 X PASS / Y WATCH / Z MISS" 통계 + 종목별 표 + 자주 탈락한 게이트 통계.

**서바이버십 편향**: 상장 종목만 잡힘 → UI 상단 배너에 명시. "회수율" 표현 회피.

## 6. API · 캐시

| 경로 | 응답 |
|---|---|
| GET `/multibagger` | HTML |
| GET `/api/multibagger?layer=pass\|watch\|all` | `{pass:[], watch:[], meta:{}}` |
| GET `/api/multibagger/diff` | `{baggers:[], stats:{...}}` |
| GET `/api/multibagger/thresholds` | `{F1:{min,max}, ...}` |
| GET `/api/multibagger/ticker/<sym>` | `{symbol, score, gates, bonus}` |

**TTL**: multibagger_us 12h / rates 24h / baggers 없음(수동).

**SWR 헤더**: `X-Cache-Age-Min`, `X-As-Of`, `X-Warming-In-Progress` (기존 패턴).

**워커**: `_multibagger_warmup_loop`, 1h 인터벌, 12h TTL 미스 시만 빌드. `_multibagger_build_lock` 동시성 방지.

**고급 패널 임계 오버라이드**: URL `?t=<base64>` → 캐시 키 해시 포함, 비표준 임계는 1h 인메모리만.

## 7. UI

**페이지 구성**
- Disclaimer Band (서바이버십 편향)
- Header: As-Of, Universe N, Hit N, DGS10, "고급 임계 펴치기"
- Layer Tabs: PASS / WATCH / DIFF
- Table:
  - Rank | Ticker | Score | 시총 | ROIC | FCF Yld 또는 P/B | EBITDA YoY−Rev YoY | 52w 위치 | Bonus 배지 | 한줄평
  - 컬럼 정렬, ticker 클릭 시 /detail/<ticker>
- WATCH 추가: `부족 게이트` 배지 (F3·F5 식)
- DIFF 추가: 5y Return / 분류 / 탈락 게이트

**점수 분해 위젯**: detail.html에 임베드. `/api/multibagger/ticker/<sym>` → 게이트 ✓/✗ 리스트 + Core 6 막대.

## 8. 에러 처리

| 상황 | 동작 |
|---|---|
| 캐시 미존재 | 빈 결과 + warming:true + JS 30s 폴링 |
| 워커 빌드 중 | stale + X-Warming-In-Progress:true |
| 종목 보강 실패 | 결과 제외, enrich_failed_n 카운터 |
| FRED 실패 | F7 자동상향 비활성, meta.rates: stale |
| baggers pkl 없음 | DIFF 탭에 "관리자 빌드 필요" 안내 |
| 베이스 스캔 캐시 없음 | 빌드 abort, 1h 후 재시도 |

## 9. 테스트

| 파일 | 범위 |
|---|---|
| test_multibagger_gates.py | F1~F8 평가 (통과·탈락·N/A) |
| test_multibagger_scoring.py | Q1~Q6 정규화, Bonus, tie-break |
| test_multibagger_classify.py | PASS/WATCH/MISS 분기 |
| test_multibagger_api.py | Flask test client, 응답 형태·헤더 |
| test_multibagger_backtest.py | 5y 계산·상폐 skip·스냅샷 결측 (yfinance mocked) |

**원칙**: 모든 yfinance/FRED는 fixture mock. CI 결정적.

## 10. 비범위 (Out of scope)

- KR 종목 (향후 별 마일스톤)
- 자동 임계 학습/ML 튜닝
- 알림/푸시
- 백테스트 다구간(3y/7y)
- 워크리스트 통합 (필요 시 detail 페이지 통해)

## 11. 한계 명시 (UI 카피에도 반영)

- 워킹페이퍼 기반 영감 — 확정 공식 아님
- 서바이버십 편향 (상폐 종목 누락)
- 통계적 예측 신호일 뿐 인과 X
- 룰 통과 ≠ 10배 보장
