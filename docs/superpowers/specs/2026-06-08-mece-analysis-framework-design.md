# MECE 기반 구조적 분석 프레임워크 — 종목스캐너 통합 설계

**작성일:** 2026-06-08
**개정:** v2 — 코드베이스 검증 후 CRITICAL 2건 + MAJOR 3건 수정
**원본:** `주가분석방법.txt` (MECE 기반 Angle-Cut-Number 프레임워크)
**대상:** 디테일 페이지 + 스캔 엔진 + API

## 배경

외부 분석 프레임워크 문서(`주가분석방법.txt`)를 검토한 결과, 우리 스캐너에 세 가지 구조적 공백이 있다:

| 영역 | 현재 상태 | 공백 |
|------|----------|------|
| **Deck 1: 기업 본질** | FinValue + MoatBonus + Bottleneck으로 강함 | 없음 |
| **Deck 2: 주가 vs 기대치** | `_PER`/`_PBR` 원시값만 존재, 맥락 부재 | 과거 대비·동종 대비·선반영 판단 없음 |
| **Deck 3: 촉매·리스크·타이밍** | EntryScore + RSI + Regime 존재 | 시나리오 분기·가격대별 대응 없음 |

## 목표

1. **밸류에이션 맥락화** — "좋은 회사 != 좋은 가격" 판단을 정량 지표로 제공
2. **시나리오 테이블** — 강세/중립/약세 분기를 룰 기반으로 생성, 디테일 페이지에 표시
3. **가격대별 대응 전략** — 진입/추가매수/손절/목표 구간을 숫자로 제시

## 비목표

- AI/LLM 기반 자연어 분석 생성 (룰 기반만)
- 실시간 뉴스/이벤트 캘린더 연동 (데이터 소스 미확보)
- 기존 TotalScore 산출 로직 변경 (표시 계층 추가만)
- 레버리지 상품 별도 분석 (향후 확장)

---

## 엣지 케이스 처리 (전 Phase 공통)

Phase별 로직에서 반복적으로 필요한 방어 규칙을 여기서 한 번에 정의한다.

### 음수/무효 PER 처리

적자기업의 PER은 음수이며, yfinance가 PER을 반환하지 않으면 `safe_get`에 의해 0이 된다.
두 경우 모두 PER 기반 지표는 의미가 없으므로 **PBR 폴백**을 적용한다.

```python
def _valid_per(v) -> float | None:
    """PER이 유효(양수, 비0)하면 반환, 아니면 None."""
    if v is None or v <= 0:
        return None
    return float(v)
```

- `ValPctile`: PER 무효 시 → PBR 백분위로 대체, PBR도 무효 시 → `None` (표시 안 함)
- `SectorRelPE`: PER 무효 종목은 계산 대상에서 제외 + 본인 값도 `None`
- `PriceInLevel`: ValPctile이 `None`이면 해당 비중(40%)을 나머지 두 요소에 재분배

### 애널리스트 커버리지 부재

`analyst_consensus.summarize_upgrades_downgrades`가 `mean_target: 0.0`을 반환하는 경우:

- `PriceInLevel`의 컨센서스 갭(30% 비중)을 0으로 처리하지 않고, **해당 비중을 52주 고가 거리에 이전** (30% + 30% = 60%)
- Phase 3 목표가: `target_analyst`를 `None`으로 설정, `target_52w_high`만 표시

### 52주 데이터 부족 (신규 상장)

`dist_from_52w_high`이 기본값 `1.0`(데이터 없음)인 경우:

- `PriceInLevel` 52주 거리 요소를 제외, ValPctile + 컨센서스로만 산출
- Phase 3 피보나치 구간 비표시, ATR 기반 구간만 제공

### KR vs US 데이터 차이

| 항목 | US | KR |
|------|----|----|
| PER 소스 | yfinance `trailingPE` | naver_quarter `per` / yfinance 폴백 |
| PER 정의 | Trailing 12M | 최신 actual 분기 기준 |
| Peers | `finnhub_api.get_peers()` GICS 기반 | 섹터 내 전체 종목 비교 |
| 애널리스트 | yfinance `upgrades_downgrades` | 동일 (제한적) |

Phase 1 peer 비교 테이블은 US만 Finnhub peer, KR은 섹터 전체 비교로 분기한다.
교차 마켓 비교(US vs KR)는 수행하지 않는다.

---

## Phase 1: 밸류에이션 맥락화 (Deck 2 강화)

### 1-1. 개요

현재 `_PER`/`_PBR` 값은 단일 숫자로만 존재한다. 이것만으로는 "비싼지 싼지" 판단할 수 없다. 다음 세 가지 맥락을 부착한다:

| 지표 | 의미 | 산출 방식 |
|------|------|----------|
| **ValPctile** (자기 대비) | 현재 PER이 과거 대비 어느 위치인가 | 최근 12개월 PER 분포에서 현재값의 백분위 |
| **SectorRelPE** (동종 대비) | 같은 섹터 PER 중앙값 대비 프리미엄/할인 | (종목PER - 섹터중앙PER) / 섹터중앙PER x 100 |
| **PriceInLevel** (선반영 판단) | 좋은 실적이 가격에 얼마나 반영됐는가 | 복합 점수: 가용 요소 가중합 (결측 시 재분배) |

### 1-2. 데이터 소스 매핑 (코드베이스 검증 완료)

| 필요 데이터 | 소스 | 위치 | 상태 |
|------------|------|------|------|
| 현재 PER | `_PER` | `quant_nexus_v20.py:6716` | OK |
| 현재 PBR | `_PBR` | `quant_nexus_v20.py:6715` | OK |
| 과거 PER 시계열 | 스냅샷 누적 + EPS 역산 | 스냅샷: `web_app/history.py:142-148` | **수정 필요** |
| 섹터별 PER 중앙값 | 스캔 결과 내 섹터 집계 | `engine_adapter.py` | 실시간 계산 |
| 52주 고가 거리 | `dist_from_52w_high` | `quant_nexus_v20.py:1903` | OK |
| 애널리스트 목표가 | `mean_target` | `analyst_consensus.py:122` | OK |
| 현재가 | `Price` | `quant_nexus_v20.py:6640` | OK |

### 1-3. 과거 PER 시계열 확보 전략

**문제:** yfinance는 과거 시점의 PER을 직접 제공하지 않는다.
**추가 문제:** 현재 스냅샷(`history.py:142-148`)은 `{score, rank, entry}` 3개 필드만 저장한다.

**해법 B — EPS 역산 (초기 배포 기본)**
- 과거 주가(yfinance `history`) / TTM EPS -> 과거 추정 PER
- 정확도는 떨어지지만 즉시 12개월 추정 가능
- **Phase 1 배포 시 기본 전략**

**해법 A — 스냅샷 누적 (장기 전략)**
- `history.py`의 `save_snapshot()` 수정: `_PER`, `_PBR` 필드 추가 저장
- 12개월 치 누적 후 자연스럽게 히스토리 형성 -> 해법 B를 점진적으로 대체
- 기존 스냅샷은 구 형식 그대로 유지 (하위 호환)

**초기 배포 시 ValPctile 부재 대응:**
- 해법 B로 즉시 추정치 제공
- 추정 불가 시(EPS 미확보 등) ValPctile = `None` -> UI에 "데이터 수집 중" 표시

### 1-4. SectorRelPE — 동종 비교

```python
def compute_sector_rel_pe(stock: dict, sector_peers: list[dict]) -> float | None:
    """섹터 내 PER 중앙값 대비 프리미엄/할인율 (%) 산출."""
    per = _valid_per(stock.get('_PER'))
    if per is None:
        return None

    valid_peers = [_valid_per(s.get('_PER')) for s in sector_peers]
    valid_peers = [p for p in valid_peers if p is not None]

    if len(valid_peers) < 3:  # 비교 표본 부족
        return None

    sector_median = sorted(valid_peers)[len(valid_peers) // 2]
    if sector_median <= 0:
        return None

    return round((per - sector_median) / sector_median * 100, 1)
```

- 양수 -> 섹터 대비 프리미엄 (비쌈)
- 음수 -> 섹터 대비 할인 (저평가 가능성)
- +/-10% 이내 -> 적정 수준

**단일 종목 API 호출 시 SectorRelPE 산출:**
- 스캔 결과는 `_scan_cache`에 캐시되어 있음 (`app.py:1193`)
- `/api/ticker` 호출 시 캐시된 스캔 결과에서 동일 섹터 종목을 조회하여 SectorRelPE 계산
- 캐시가 없으면(콜드 스타트) SectorRelPE = `None` -> UI에 "스캔 대기" 표시

**Finnhub peers 연동 (US만, 향후 확장):**
- `finnhub_api.get_peers(ticker)` 존재 (`finnhub_api.py:250`)
- `app.py:1666`에 `_peers_from_finnhub` 헬퍼도 존재
- Phase 1 초기 배포 범위에서는 섹터 전체 비교만 구현
- Finnhub peer 비교 테이블은 Phase 1.5 확장으로 분리

### 1-5. PriceInLevel — 선반영 복합 판단

기대치 반영 정도를 0~100 점수로 종합. **결측 요소 발생 시 비중을 가용 요소에 재분배:**

```python
def compute_price_in_level(
    val_pctile: float | None,       # 0~100 PER 백분위
    dist_from_52w_high: float | None,  # 0~1
    consensus_gap: float | None,    # 현재가/목표가 비율 (0~1+)
) -> float | None:
    """선반영 복합 점수 (0~100). 가용 요소만으로 가중합."""
    components = []

    if val_pctile is not None:
        components.append(('val', 40, val_pctile))

    if dist_from_52w_high is not None and dist_from_52w_high < 1.0:
        # 고가 근접 = 선반영. dist 0 = 신고가(100점), dist 0.5 = 50점
        score_52w = max(0, min(100, (1 - dist_from_52w_high) * 100))
        components.append(('52w', 30, score_52w))

    if consensus_gap is not None and consensus_gap > 0:
        # 현재가/목표가 비율. 1.0 = 목표가 도달(100점), 0.5 = 절반(50점)
        score_con = max(0, min(100, consensus_gap * 100))
        components.append(('con', 30, score_con))

    if not components:
        return None

    total_weight = sum(w for _, w, _ in components)
    weighted_sum = sum(w * s for _, w, s in components)
    return round(weighted_sum / total_weight, 1)
```

해석:
- **0~30**: 저반영 (기대치 낮음, 서프라이즈 여력)
- **30~60**: 적정 반영
- **60~100**: 과반영 (추격매수 위험)

### 1-6. 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `web_app/valuation_context.py` | **신규** — 밸류에이션 맥락 계산 순수 함수 모듈 |
| `web_app/history.py` | **수정** — `save_snapshot()`에 `_PER`, `_PBR` 필드 추가 저장 |
| `web_app/engine_adapter.py` | **수정** — 스캔 완료 후 `_attach_valuation_context()` 호출 |
| `web_app/app.py` | **수정** — `/api/ticker` 응답에 ValPctile, SectorRelPE, PriceInLevel 부착 |
| `web_app/static/app.js` | **수정** — 디테일 페이지에 밸류에이션 맥락 카드 렌더링 |
| `web_app/templates/detail.html` | **수정** — 밸류에이션/시나리오/가격 카드 컨테이너 3개 추가 |
| `web_app/static/scanner.css` | **수정** — 밸류에이션 카드 스타일 |
| `web_app/tests/test_valuation_context.py` | **신규** — 순수 함수 단위 테스트 |

### 1-7. 디테일 페이지 UI — 밸류에이션 맥락 카드

기존 `_renderQuadrant` 아래에 새 섹션 삽입:

```
+-------------------------------------------------+
|  밸류에이션 맥락          [참고용 시뮬레이션]      |
|                                                   |
|  PER 12.3  > 과거 1년 중 하위 35% (저평가 구간)    |
|  --------*--------------------------              |
|  0%       35%                 100%                |
|                                                   |
|  섹터 대비  -18%  (할인)                           |
|  선반영도   42/100  (적정)                         |
|                                                   |
|  +- 동종 비교 -------------------------+          |
|  |  AAPL  PER 28.5                     |          |
|  |  MSFT  PER 32.1                     |          |
|  |> GOOG  PER 12.3  < 현재 종목        |          |
|  |  META  PER 24.7                     |          |
|  +-------------------------------------+          |
+-------------------------------------------------+
```

ValPctile이 `None`인 경우: 게이지 바 대신 "데이터 수집 중 (약 2주 후 활성화)" 텍스트 표시.

---

## Phase 2: 시나리오 시그널 강도 테이블

### 2-1. 개요

종목 분석의 핵심 출력물: **강세/중립/약세 시나리오를 시그널 강도로 분기**하여 디테일 페이지에 표시. LLM이 아닌 **룰 기반 점수 조합**으로 산출.

**명칭 주의:** "확률"이라는 용어 대신 **"시그널 강도"**를 사용한다. 이 점수는 통계적 예측이 아닌 현재 시그널 조합의 방향성 합산이며, 사용자 오해를 방지하기 위해 UI에도 "시그널 강도"로 표기한다.

### 2-2. 시나리오 분류 로직

기존 시그널을 조합하여 3-way 강도를 산출한다:

**입력 시그널 (코드베이스 검증 완료):**

| 시그널 | 출처 | 실제 범위 | 의미 |
|--------|------|----------|------|
| TotalScore | 스캔 엔진 (`quant_nexus_v20.py:6650`) | 0~100 | 종합 퀄리티 |
| EntryScore | quant_nexus (`quant_nexus_v20.py:6689`) | **0~100** | 진입 타이밍 적합도 |
| RSI | quant_nexus (`quant_nexus_v20.py:6646`) | 0~100 | 과매수/과매도 |
| Regime | quant_nexus (`quant_nexus_v20.py:6649`) | enum (아래 참조) | ADX+SMA 레짐 |
| PriceInLevel | Phase 1 | 0~100 | 기대치 선반영도 |
| SectorRelPE | Phase 1 | % | 동종 대비 밸류에이션 |

**Regime 실제 enum 값 (quant_nexus_v20.py:2409-2463):**
- `STRONG_BULL` : ADX>25 + 가격>SMA20>SMA50
- `BULL` : ADX>20 + 가격>SMA50
- `SIDEWAYS_BULL` : 가격>SMA50 but ADX<20
- `SIDEWAYS` : 횡보
- `BEAR` : 가격<SMA50
- `STRONG_BEAR` : 가격<SMA20<SMA50

**시나리오 점수 산출:**

```python
def compute_scenario_scores(stock: dict) -> dict:
    """강세/중립/약세 시그널 강도 산출. 정규화 후 비율로 변환."""

    bull_score = 0
    bear_score = 0
    contributions = []  # 핵심 변수 추출용

    # 1. 퀄리티 축 (TotalScore, 0~100)
    ts = stock.get('TotalScore', 50)
    if ts >= 70:
        bull_score += 25
        contributions.append({'name': '높은 퀄리티 (S/A등급)', 'impact': +25})
    elif ts >= 55:
        bull_score += 15
        contributions.append({'name': '양호한 퀄리티 (B+등급)', 'impact': +15})
    elif ts < 40:
        bear_score += 20
        contributions.append({'name': '낮은 퀄리티 (C등급)', 'impact': -20})

    # 2. 타이밍 축 (EntryScore, 0~100 -- NOT 0~10)
    es = stock.get('EntryScore', 50)
    if es >= 50:
        bull_score += 20
        contributions.append({'name': '진입 적기 시그널', 'impact': +20})
    elif es <= 25:
        bear_score += 15
        contributions.append({'name': '진입 부적합 시그널', 'impact': -15})

    # 3. 모멘텀/과열 (RSI, 0~100)
    rsi = stock.get('RSI', 50)
    if rsi > 75:
        bear_score += 15
        contributions.append({'name': '과매수 구간 (RSI > 75)', 'impact': -15})
    elif rsi < 30:
        bull_score += 15
        contributions.append({'name': '과매도 반등 기대 (RSI < 30)', 'impact': +15})

    # 4. 시장 레짐 (실제 enum 매핑)
    regime = stock.get('Regime', '')
    regime_map_bull = {'STRONG_BULL': 15, 'BULL': 10, 'SIDEWAYS_BULL': 5}
    regime_map_bear = {'STRONG_BEAR': 20, 'BEAR': 15}
    r_bull = regime_map_bull.get(regime, 0)
    r_bear = regime_map_bear.get(regime, 0)
    if r_bull:
        bull_score += r_bull
        contributions.append({'name': f'상승 추세 ({regime})', 'impact': +r_bull})
    if r_bear:
        bear_score += r_bear
        contributions.append({'name': f'하락 추세 ({regime})', 'impact': -r_bear})

    # 5. 선반영도 (Phase 1, 0~100) -- Phase 1 미배포 시 폴백 50
    pil = stock.get('PriceInLevel', None)
    if pil is not None:
        if pil >= 70:
            bear_score += 20
            contributions.append({'name': '기대치 과반영', 'impact': -20})
        elif pil <= 30:
            bull_score += 15
            contributions.append({'name': '기대치 저반영', 'impact': +15})

    # 6. 밸류에이션 상대위치 (Phase 1) -- Phase 1 미배포 시 스킵
    srpe = stock.get('SectorRelPE', None)
    if srpe is not None:
        if srpe < -20:
            bull_score += 10
            contributions.append({'name': '섹터 대비 저평가', 'impact': +10})
        elif srpe > 30:
            bear_score += 10
            contributions.append({'name': '섹터 대비 고평가', 'impact': -10})

    # 정규화 -> 비율 -> 클램핑 -> 재정규화 (합계 100% 보장)
    total = bull_score + bear_score + 30  # 30 = 중립 베이스
    raw_bull = bull_score / total * 100
    raw_bear = bear_score / total * 100
    raw_neutral = 100 - raw_bull - raw_bear

    # 클램핑 (과신 방지)
    b = max(10, min(70, raw_bull))
    n = max(15, min(60, raw_neutral))
    e = max(10, min(70, raw_bear))

    # 재정규화 -> 합계 100% 보장
    s = b + n + e
    bull_final = round(b / s * 100)
    bear_final = round(e / s * 100)
    neutral_final = 100 - bull_final - bear_final  # 나머지 할당으로 정확히 100%

    # 핵심 변수 상위 3개 추출 (절대 영향력 기준)
    top_vars = sorted(contributions, key=lambda x: abs(x['impact']), reverse=True)[:3]

    return {
        'bull': bull_final,
        'neutral': neutral_final,
        'bear': bear_final,
        'key_variables': top_vars,
        'contributions': contributions,  # 전체 기여 목록 (디버깅/고급 표시용)
    }
```

**Phase 1 미배포 시 폴백:** PriceInLevel과 SectorRelPE가 `None`이면 해당 시그널을 건너뛴다. Phase 2는 Phase 1 없이도 4개 시그널(TotalScore, EntryScore, RSI, Regime)만으로 독립 동작한다.

### 2-3. 시나리오별 조건·대응 생성

각 시나리오에 **트리거 조건**과 **대응 지침**을 룰 기반으로 매핑. 실제 종목 데이터에서 해당하는 조건만 필터링하여 표시:

```python
def generate_active_triggers(stock: dict) -> dict:
    """현재 종목 데이터에서 실제로 충족되는 조건만 추출."""
    es = stock.get('EntryScore', 50)  # 0~100
    triggers = {
        'bull': [],
        'neutral': [],
        'bear': [],
    }

    # 강세 트리거
    if stock.get('TotalScore', 0) >= 70:
        triggers['bull'].append('높은 종합 퀄리티 (S/A등급)')
    if es >= 50:
        triggers['bull'].append('진입 적기 시그널')
    if stock.get('PriceInLevel') is not None and stock['PriceInLevel'] <= 30:
        triggers['bull'].append('기대치 저반영 상태')
    if stock.get('Regime', '') in ('STRONG_BULL', 'BULL', 'SIDEWAYS_BULL'):
        triggers['bull'].append('상승 추세')

    # 약세 트리거
    if stock.get('RSI', 50) > 75:
        triggers['bear'].append('과매수 구간')
    if stock.get('PriceInLevel') is not None and stock['PriceInLevel'] >= 70:
        triggers['bear'].append('기대치 과반영')
    if stock.get('Regime', '') in ('BEAR', 'STRONG_BEAR'):
        triggers['bear'].append('하락 추세')
    if stock.get('SectorRelPE') is not None and stock['SectorRelPE'] > 30:
        triggers['bear'].append('섹터 대비 고평가')

    # 중립 트리거 (강세/약세 어느 쪽도 압도적이지 않을 때)
    ts = stock.get('TotalScore', 50)
    if 50 <= ts < 70:
        triggers['neutral'].append('양호하지만 돌출 아님')
    pil = stock.get('PriceInLevel')
    if pil is not None and 30 < pil < 60:
        triggers['neutral'].append('적정 수준 반영')

    return triggers

SCENARIO_RESPONSE = {
    'bull': '눌림목 분할 매수',
    'neutral': '추격 매수 금지, 조정 시 재검토',
    'bear': '비중 축소 또는 손절',
}
```

### 2-4. 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `web_app/scenario_engine.py` | **신규** — 시나리오 강도 계산 + 조건/대응 매핑 순수 함수 |
| `web_app/app.py` | **수정** — `/api/ticker` 응답에 `Scenarios` 객체 부착 |
| `web_app/static/app.js` | **수정** — 디테일 페이지에 시나리오 테이블 렌더링 |
| `web_app/templates/detail.html` | **수정** — 시나리오 카드 컨테이너 (Phase 1에서 일괄 추가) |
| `web_app/static/scanner.css` | **수정** — 시나리오 테이블 스타일 |
| `web_app/tests/test_scenario_engine.py` | **신규** — 순수 함수 단위 테스트 |

### 2-5. 디테일 페이지 UI — 시나리오 테이블

밸류에이션 카드 아래에 배치:

```
+-------------------------------------------------+
|  시나리오 시그널 강도      [참고용 시뮬레이션]      |
|                                                   |
|  ============--------------------  ========----  |
|  강세 35%               중립 45%     약세 20%     |
|                                                   |
| +----------+--------------------+--------------+ |
| | 시나리오  | 충족 조건           | 대응         | |
| +----------+--------------------+--------------+ |
| | 강세 35% | 높은 퀄리티(S등급)  | 눌림목       | |
| |          | 진입 적기 시그널    | 분할 매수    | |
| |          | 기대치 저반영       |              | |
| +----------+--------------------+--------------+ |
| | 중립 45% | 양호하나 선반영 부담| 추격 매수    | |
| |          |                    | 금지          | |
| +----------+--------------------+--------------+ |
| | 약세 20% | 과매수 구간        | 비중 축소    | |
| |          | 하락 추세          | 또는 손절    | |
| +----------+--------------------+--------------+ |
|                                                   |
|  핵심 변수: (1) 높은 퀄리티 (+25)                  |
|            (2) 진입 적기 시그널 (+20)              |
|            (3) 과매수 구간 (-15)                   |
+-------------------------------------------------+
```

핵심 변수는 `compute_scenario_scores`의 `key_variables`에서 자동 추출된다.

---

## Phase 3: 가격대별 대응 전략

### 3-1. 개요

"어떤 가격에서 진입하고, 틀리면 어디서 나올 것인가"를 숫자로 제시.

**기존 기능과의 관계:** `app.py:2507-2532`에 이미 ATR 기반 DCA 분할매수 가이드(`dca_plan`)와 `detail.html`의 `dca-plan-wrap` 컨테이너가 존재한다. Phase 3는 이 기존 기능을 **확장·교체**한다:

| 항목 | 기존 `dca_plan` | Phase 3 `price_levels` |
|------|-----------------|----------------------|
| 매수 구간 | ATR 0x/1x/2x/3x (4단계) | ATR 기반 + 피보나치 병행 |
| 비율 배분 | 30/30/25/15% 고정 | 시나리오 연동 동적 판단 |
| 손절 기준 | 없음 | ATR 4배 기반 |
| 보유자/비보유자 분기 | 없음 | 있음 |
| 시나리오 연동 | 없음 | Phase 2 강세/약세에 따라 행동 분기 |

**마이그레이션:** 기존 `dca-plan-wrap` 컨테이너를 재사용하고, `dca_plan` 렌더링 코드를 Phase 3 코드로 교체한다. 기존 백엔드 `dca_plan` 계산 로직은 유지하되, 프론트에서 Phase 3 UI로 대체한다.

### 3-2. 데이터 소스 매핑 (코드베이스 검증 완료)

| 필요 데이터 | 소스 | 위치 | 상태 |
|------------|------|------|------|
| 현재가 | `Price` | `quant_nexus_v20.py:6640` | OK |
| 52주 고가 | `high_52w` | `quant_nexus_v20.py:1918` | OK |
| 52주 저가 | `low_52w` | quant_nexus 내부 | **확인 필요 — 없으면 yfinance history에서 파생** |
| ATR | `EntryPlan` 서브필드 | `app.py:2509` `result.volatility.details.atr` | OK — **별도 top-level 필드로 추출** |
| ATR % | `EntryPlan` 서브필드 | `app.py:2510` `result.volatility.details.atr_pct` | OK |
| 애널리스트 목표가 | `mean_target` | `analyst_consensus.py:122` | OK |
| SMA20/SMA50 | Regime 계산 내부 | `quant_nexus_v20.py:2409` | API 미노출 — **선택사항, Regime으로 대체** |

**ATR 필드 분리:** `/api/ticker` 응답 구성 시 top-level 필드로 추출:
```python
result['ATR'] = result.get('volatility', {}).get('details', {}).get('atr', 0)
result['ATR_pct'] = result.get('volatility', {}).get('details', {}).get('atr_pct', 0)
```

### 3-3. 가격 수준 산출

```python
def compute_price_levels(stock: dict) -> dict | None:
    """진입/추가매수/손절/목표 가격 수준 산출."""
    price = stock.get('Price', 0)
    if price <= 0:
        return None

    high_52w = stock.get('high_52w', price)
    low_52w = stock.get('low_52w', None)
    atr = stock.get('ATR', 0) or (price * 0.02)  # ATR 미확보 시 2% 폴백
    target_price = stock.get('AnalystTargetPrice', 0)

    result = {
        # 분할 매수 구간 (기존 dca_plan과 동일한 ATR 배수 사용)
        'entry_1': round(price - atr * 1, 2),       # ATR 1배 하락
        'entry_2': round(price - atr * 2, 2),       # ATR 2배 하락
        'entry_3': round(price - atr * 3, 2),       # ATR 3배 하락

        # 손절 기준
        'stop_loss': round(price - atr * 4, 2),     # ATR 4배 -- 추세 이탈 판정

        # 목표가
        'target_analyst': round(target_price, 2) if target_price > 0 else None,
        'target_52w_high': round(high_52w, 2),

        # 메타
        'price': price,
        'atr': round(atr, 2),
        'atr_pct': round(atr / price * 100, 1),
    }

    # 피보나치 되돌림 지지선 (52주 저가 확보 시만)
    if low_52w is not None and low_52w > 0:
        range_52w = high_52w - low_52w
        if range_52w > 0:
            result['fib_382'] = round(high_52w - range_52w * 0.382, 2)
            result['fib_500'] = round(high_52w - range_52w * 0.500, 2)
            result['fib_618'] = round(high_52w - range_52w * 0.618, 2)

    return result
```

**ATR 배수 근거:** 기존 `dca_plan`의 0x/1x/2x/3x 구간과 정합성 유지 (entry 1~3). 손절은 4x로 추가 (기존에는 없던 기능). 스윙~중기 투자자 기준.

### 3-4. 보유자/비보유자 분기

원본 문서의 핵심: **신규 진입자와 기존 보유자의 대응이 다르다.**

```python
def generate_action_plan(
    stock: dict,
    price_levels: dict,
    scenarios: dict,
) -> dict:
    """보유 상태별 행동 지침 생성."""
    bull_pct = scenarios['bull']
    bear_pct = scenarios['bear']
    pil = stock.get('PriceInLevel', 50)  # Phase 1 미배포 시 중립 기본값

    plan = {
        'new_investor': {'action': '', 'details': []},
        'holder': {'action': '', 'details': []},
    }

    # 신규 진입자
    if bull_pct >= 40 and pil <= 50:
        plan['new_investor']['action'] = '분할 매수 고려'
        plan['new_investor']['details'] = [
            f"1차: {price_levels['entry_1']} (ATR 1배 하락)",
            f"2차: {price_levels['entry_2']} (ATR 2배 하락)",
            f"3차: {price_levels['entry_3']} (ATR 3배 하락)",
            f"손절: {price_levels['stop_loss']} 이탈 시",
        ]
    elif bull_pct >= 30:
        plan['new_investor']['action'] = '관망 (조정 대기)'
        fib = price_levels.get('fib_382')
        if fib:
            plan['new_investor']['details'].append(
                f"피보나치 38.2% ({fib}) 도달 시 재검토")
        plan['new_investor']['details'].append("추격 매수 지양")
    else:
        plan['new_investor']['action'] = '진입 보류'
        plan['new_investor']['details'] = [
            "하락 시그널이 상승 시그널을 초과",
            "추세 전환 확인 후 재검토",
        ]

    # 기존 보유자
    if bear_pct >= 40:
        plan['holder']['action'] = '비중 축소 고려'
        plan['holder']['details'] = [
            f"손절: {price_levels['stop_loss']} 이탈 시",
            "부분 이익 실현 고려",
        ]
    elif bull_pct >= 40:
        plan['holder']['action'] = '보유 유지'
        target = price_levels.get('target_analyst') or price_levels.get('target_52w_high')
        plan['holder']['details'] = [
            f"목표가: {target}",
            f"추가 매수 고려: {price_levels['entry_2']} 구간",
        ]
    else:
        plan['holder']['action'] = '보유 유지 (추가매수 보류)'
        plan['holder']['details'] = [
            "현 비중 유지, 추가 투입 지양",
            f"손절: {price_levels['stop_loss']} 이탈 시",
        ]

    return plan
```

**어투 주의:** "매수", "매도" 대신 "고려", "지양" 등 완화된 표현 사용. 투자 자문이 아님을 명확히.

### 3-5. 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `web_app/price_levels.py` | **신규** — 가격 수준 산출 + 행동 지침 생성 순수 함수 |
| `web_app/app.py` | **수정** — `/api/ticker`에 ATR top-level 노출 + PriceLevels/ActionPlan 부착 |
| `web_app/static/app.js` | **수정** — 기존 `dca-plan-wrap` 렌더링을 Phase 3 UI로 교체 |
| `web_app/templates/detail.html` | **수정** — 가격 카드 컨테이너 (Phase 1에서 일괄 추가) |
| `web_app/static/scanner.css` | **수정** — 가격 수준 시각화 스타일 |
| `web_app/tests/test_price_levels.py` | **신규** — 순수 함수 단위 테스트 |

### 3-6. 디테일 페이지 UI — 가격 대응 카드

기존 `dca-plan-wrap` 위치를 확장 교체:

```
+-------------------------------------------------+
|  가격대별 대응 전략        [참고용 시뮬레이션]      |
|                                                   |
|  -- 가격 맵 ----------------------------------   |
|                                                   |
|  $185.20  -- 52주 고가 ------------ 목표         |
|  $178.50  -- 애널리스트 평균 목표가               |
|  $165.30  -- 현재가 *                             |
|  $157.00  -- 1차 (ATR 1x)                        |
|  $148.70  -- 2차 (ATR 2x)                        |
|  $140.40  -- 3차 (ATR 3x)       fib 38.2%       |
|  $132.10  -- 손절 (ATR 4x)                       |
|  $120.80  -- 52주 저가                            |
|                                                   |
|  ATR: $8.30 (5.0%)  변동성: 보통                  |
|                                                   |
|  +- 신규 진입자 ----------+- 기존 보유자 --------+|
|  | 분할 매수 고려          | 보유 유지            ||
|  |                        |                      ||
|  | 1차: $157.00           | 목표: $178.50        ||
|  | 2차: $148.70           | 추가매수: $148.70    ||
|  | 3차: $140.40           | 손절: $132.10 이탈시 ||
|  | 손절: $132.10 이탈시    |                      ||
|  +------------------------+----------------------+|
+-------------------------------------------------+
```

---

## 전체 신규 파일 구조

```
web_app/
  valuation_context.py    # Phase 1 -- 밸류에이션 맥락 순수 함수
  scenario_engine.py      # Phase 2 -- 시나리오 강도 + 조건/대응 매핑
  price_levels.py         # Phase 3 -- 가격 수준 + 행동 지침

  tests/                  # 디렉토리 신규 생성 필요
    test_valuation_context.py
    test_scenario_engine.py
    test_price_levels.py
```

설계 원칙:
- **순수 함수만** -- 네트워크/IO 없음. 입력 dict -> 출력 dict.
- **기존 엔진 불변** -- TotalScore, FinValue, EntryScore 산출 로직 변경 없음.
- **점진적 배포** -- Phase별 독립 배포 가능. Phase 2는 Phase 1 없이도 동작 (폴백).
- **완화된 어투** -- "매수/매도" 대신 "고려/지양". 각 카드 헤더에 "[참고용 시뮬레이션]" 배지.

## 수정 파일 요약 (전 Phase)

| 파일 | Phase | 변경 유형 |
|------|-------|----------|
| `web_app/valuation_context.py` | 1 | 신규 |
| `web_app/scenario_engine.py` | 2 | 신규 |
| `web_app/price_levels.py` | 3 | 신규 |
| `web_app/history.py` | 1 | 수정 -- 스냅샷에 `_PER`, `_PBR` 추가 |
| `web_app/engine_adapter.py` | 1 | 수정 -- `_attach_valuation_context()` 추가 |
| `web_app/app.py` | 1, 2, 3 | 수정 -- API 응답 확장 + ATR 필드 분리 |
| `web_app/static/app.js` | 1, 2, 3 | 수정 -- 디테일 3개 섹션 + DCA 교체 |
| `web_app/templates/detail.html` | 1 | 수정 -- 카드 컨테이너 3개 일괄 추가 |
| `web_app/static/scanner.css` | 1, 2, 3 | 수정 -- 새 컴포넌트 스타일 |
| `web_app/tests/test_valuation_context.py` | 1 | 신규 |
| `web_app/tests/test_scenario_engine.py` | 2 | 신규 |
| `web_app/tests/test_price_levels.py` | 3 | 신규 |

## 의존 관계

```
Phase 1 (밸류에이션 맥락) -- 독립 배포 가능
    | PriceInLevel, SectorRelPE (Optional)
    v
Phase 2 (시나리오 테이블) -- Phase 1 없이도 동작 (4개 시그널 폴백)
    | Scenarios
    v
Phase 3 (가격대별 대응) -- Phase 2 없이도 가격 수준만 제공 가능
```

**각 Phase는 상위 Phase 미배포 시에도 독립 동작 가능하도록 폴백을 내장한다.**

## 디스클레이머

**위치:** 각 카드 헤더에 `[참고용 시뮬레이션]` 배지 + 디테일 페이지 하단에 전체 문구.

> 본 분석은 과거 데이터 기반 룰 엔진 산출물이며, 투자 권유가 아닙니다.
> 시그널 강도는 통계적 예측이 아닌 현재 시그널 조합의 방향성 합산입니다.
> 실제 투자 결정은 본인의 판단과 책임 하에 이루어져야 합니다.

---

## 부록: v1 -> v2 검증에서 수정된 항목

| # | 심각도 | 문제 | v2 수정 내용 |
|---|--------|------|-------------|
| 1 | CRITICAL | EntryScore 0~10 가정 (실제 0~100) | 임계값 50/25로 수정, 기본값 50 |
| 2 | CRITICAL | `WEAK_BEAR` 미존재 enum | `STRONG_BEAR: 20, BEAR: 15, SIDEWAYS_BULL: 5` 매핑 |
| 3 | MAJOR | 스냅샷에 PER/PBR 미저장 | `history.py` 수정 파일 추가, 해법 B 기본 전략으로 격상 |
| 4 | MAJOR | 기존 `dca_plan` 중복 미언급 | 관계 명시 (확장·교체), ATR 배수 정합 |
| 5 | MAJOR | 확률 클램핑 후 합계 != 100% | 재정규화 단계 추가 |
| 6 | MINOR | 음수 PER/None 미처리 | 엣지 케이스 섹션 추가, `_valid_per` 함수 |
| 7 | MINOR | 커버리지 부재 종목 미처리 | 비중 재분배 로직 명시 |
| 8 | MINOR | detail.html Phase 2/3 누락 | Phase 1에서 3개 컨테이너 일괄 추가 |
| 9 | MINOR | "확률" 표현 오해 소지 | "시그널 강도"로 명칭 변경 |
| 10 | MINOR | 행동 지침 투자 자문 오해 | "고려/지양" 완화 어투 + 카드별 배지 |
