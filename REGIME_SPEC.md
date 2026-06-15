# 선제적 레짐 파악(Regime Detection) 모듈 — 구현 스펙 v1

> 목표: 기존 종목 스캐너에 **확률적 레짐 분류기 + 오더플로우 불균형(OFI) + 크로스마켓 리드래그**를 추가하고,
> 그 결과를 기존 4축·병목해자 점수에 곱해 **선제적 진입 점수**를 산출한다.
> 원칙: **최소 침습 · 기존 패턴 재사용 · 의존성 격리 · graceful fallback · 과도한 엔지니어링 회피**.

작성일: 2026-06-09 · 대상 브랜치: `main`

---

## 0. 확정된 설계 결정 (인터뷰 + 환경스캔)

| 항목 | 결정 | 근거 |
|------|------|------|
| 데이터 입자 | **일봉 OHLCV** | 시스템 전체가 daily 기반 (yfinance/naver/finnhub). OFI는 일봉 프록시로 근사 |
| 레짐 엔진 | **정식 `hmmlearn` GaussianHMM** (+ sklearn 보조) | 통계적 정밀성. 설치 완료(3.13-64), 배포 Linux 호환 |
| 범위 | **4개 모듈 전부** | 분류기 → OFI → 리드래그 → 동적가중 통합 |
| 실행 환경 | 로컬 dev = Python 3.13-64 (풀스택 OK) · 로컬 앱 런타임 = 3.11-32 · 배포 = Linux x86_64 | **32-bit에서 scipy/sklearn 휠 없음** → import 실패 시 numpy 폴백 필수 |

### 핵심 비용 설계 (스캔 성능 보호)
- **레짐 분류기 = 시장/섹터 레벨, 일 1회 캐시** (종목별 HMM 금지). 수백 종목마다 HMM을 돌리지 않는다.
  종목의 레짐 승수는 그 종목이 속한 **섹터/시장 지수의 레짐 전환확률**에서 온다 (스펙 원문 "시장과 섹터의 상태"와 일치).
- **OFI = 종목별이지만 경량** (이미 보유한 일봉에서 산출, 신규 네트워크 호출 0).
- **리드래그 = 테마 레벨, 일 1회 캐시**.

---

## 1. 통합 아키텍처 (어디에 꽂는가)

### 1.1 통합 지점 — `web_app/engine_adapter.py`
`ScanAdapter.scan_sector()`(L606–613)와 `scan_all()`(L644–651)의 **`_attach_*` 체인**이 단일 통합 지점이다.
모든 신규 모듈은 기존 `_attach_bottleneck` 패턴(멱등·try/except·setdefault 폴백·never throw)을 그대로 복제한다.

```
현재 체인:                          신규 추가 후:
_attach_sector_residual            _attach_sector_residual
apply_speculative_correction       apply_speculative_correction
_annotate_micro_outlier            _annotate_micro_outlier
_attach_bottleneck                 _attach_bottleneck
_attach_index_membership           _attach_index_membership
_attach_midcap_alpha               _attach_midcap_alpha
_attach_valuation_context          _attach_valuation_context
                              ★    _attach_regime(results, market)      # 모듈1: 섹터/시장 레짐 전환확률
                              ★    _attach_order_flow(results)          # 모듈2: OFI 프록시
                              ★    _attach_leadlag(results, market)     # 모듈3: 크로스마켓(KR만)
                              ★    _apply_regime_weighting(results)     # 모듈4: 동적 가중 → RegimeEntryScore
sort by TotalScore                 sort by (RegimeEntryScore if REGIME_RANK else TotalScore)
```

### 1.2 기존 레짐 레이어와의 관계 (중복 금지)
- `macro_gate.get_regime()` → VIX 임계 기반 시장 Risk-On/Neutral/Risk-Off (단순·시장 1개).
  **유지**. 신규 HMM은 이를 **대체하지 않고 보강** — VIX를 HMM feature 중 하나로 흡수하고, 섹터별 확률 레짐을 추가.
- `four_axis_analyzer.FourAxisAnalyzer` → 종목 4축(추세/모멘텀/변동성/볼륨) 각 0–5점. OHLCV·VWAP·OBV·ATR 보유.
  → **모듈2(OFI)의 풀버전은 여기에 함께 산출**(df가 손에 있음). 디테일 카드에 OFI 배지 노출.
- `bottleneck_screen.bottleneck_entry_signal(regime=...)` → 이미 `regime` 인자를 받음. 신규 레짐 라벨을 여기에 전달.

### 1.3 신규 파일 매니페스트 (모두 프로젝트 루트, 기존 모듈과 동일 위치)
| 파일 | 역할 | 의존성 |
|------|------|--------|
| `regime_classifier.py` | HMM/GMM 레짐 분류기 + 전환신호 (모듈1) | hmmlearn/sklearn(옵셔널) → numpy 폴백 |
| `order_flow.py` | 일봉 OFI·스마트머니 프록시 (모듈2) | numpy/pandas only |
| `cross_market_lead.py` | US→KR 리드래그 전이 (모듈3) | yfinance(기존), numpy |
| `regime_integration.py` | `_attach_*`/`_apply_regime_weighting` 어댑터 함수 (모듈4) | 위 3개 |
| `tests/test_regime_classifier.py` 외 | 단위 테스트 | pytest |
| `regime_cache/` | 일 1회 레짐/리드래그 캐시 (gitignore) | — |

> `engine_adapter.py`에는 **import + 체인에 4줄 추가**만. 로직은 전부 신규 파일에 격리.

---

## 2. 모듈1 — 확률적 레짐 분류기 (`regime_classifier.py`)

연구 스펙(`.kkirikkiri/research/hmm_regime_spec.md`) 채택. 요약:

### 2.1 공개 API
```python
def classify_regime(ohlcv: pd.DataFrame, *, config: dict = REGIME_CONFIG) -> RegimeResult
# RegimeResult: { state: str,                # 'low_vol_uptrend'|'high_vol_downtrend'|'range_chop'
#                 probs: {regime: float},    # 필터드 posterior P(state_t|obs_1..t)
#                 p_next: {regime: float},   # transmat 1-step forecast α_t·A
#                 transition_signal: dict,   # {early_long: bool, early_exit: bool, strength: 0..1, fresh: 0..1}
#                 model_status: str }        # 'hmm'|'two_state'|'rule_based'(fallback)

def get_market_regime(market: str) -> RegimeResult     # ^KS11/^KQ11/^GSPC, 일 1회 캐시
def get_sector_regime(sector_key: str, market: str) -> RegimeResult  # 섹터 지수/대표 바스켓, 캐시
```

### 2.2 모델 (연구 스펙 §2,§6)
- `hmmlearn.GaussianHMM`, **n_states=3**, `covariance_type="diag"`, `n_iter=150`, `min_covar=1e-3`, `random_state=42`, 멀티스타트 8회 best-score.
- **Feature(6, 저공선성)**: `ret`, `rvol_20`, `volratio(rvol10/rvol20)`, `skew_20`, `dd_60`, `eff_20(efficiency ratio)`.
- **표준화**: expanding z-score(min_periods=252), `eff/skew`는 winsorize. **rolling/full-sample 금지**(누수).
- **라벨 고정**: fit 후 상태별 `mean(mom), mean(rvol_20)` 정렬 → uptrend/downtrend/chop 결정적 매핑(label-switching 해소).

### 2.3 선행 전환신호 (연구 스펙 §3) — MA보다 앞선다
`early_long = (hard_state ∈ {bear,chop}) AND P_bull≥0.40 AND ΔP_bull>0 (2일 연속) AND (P_bull↑0.50 cross OR p_next[bull]≥0.55)`.
대칭 `early_exit`. 히스테리시스 0.45. 임계 디폴트: `arm=0.40, enter=0.50, exit=0.50, forecast=0.55, rising_days=2`.

`strength` = max(P_bull−0.5, 0)·2 (전환 확신도). `fresh` = 전환 cross 이후 경과일 기반 감쇠(0일=1.0 → rising_days 경과 후 0) → **모듈4 "전환 초기일수록 가중↑"의 입력**.

### 2.4 누수 방지 (연구 스펙 §4) — 필수
- **forward-only filtering**: `.predict()`(Viterbi 전구간)·`.predict_proba()`(forward-backward smoothing) **라이브 사용 금지**.
  확장-예측 루프(`predict_proba(X[:t+1])`의 마지막 행만 사용) 또는 `_do_forward_pass` 활용. tail만 증분 계산·캐시.
- 파라미터는 과거만으로 fit(rolling 756bar), 추론은 frozen 모델로 daily. 재적합 월 1회(21bar).

### 2.5 폴백 체인 (연구 스펙 §5) — 절대 크래시 금지
```
hmmlearn import 실패(32-bit) → rule_based 즉시
fit 미수렴/퇴화          → 시드 증가 → 2-state HMM → rule_based
rule_based: rvol_20 백분위 + dd_60 + eff_20 임계로 3레짐 근사. model_status='rule_based' 플래그.
min_fit_bars=500 미만     → rule_based
```
`import hmmlearn`은 try/except로 감싸 모듈 상단에서 `_HAS_HMM` 플래그화.

---

## 3. 모듈2 — OFI/스마트머니 프록시 (`order_flow.py`)

일봉만으로 매수/매도 압력 불균형 + 은밀한 매집 변곡점을 근사. numpy/pandas only.

### 3.1 공개 API
```python
def compute_ofi(ohlcv: pd.DataFrame, *, window: int = 20) -> dict
# { ofi: -1..1,            # 누적 압력 불균형 (양수=매수우위)
#   smart_money: 0..1,     # 스마트머니 체결강도(종가위치·거래량가중)
#   accumulation: bool,    # 횡보 밴드 내 은밀 매집 변곡점 캡처
#   vwap_pressure: -1..1,  # 일중추정 VWAP 대비 종가 위치 가중
#   reasons: [str] }

def ofi_from_row(row: dict) -> dict   # 스캔 경량 프록시 (row의 기존 필드 사용, df 없을 때)
```

### 3.2 일봉 프록시 공식
- **종가위치(CLV, close-location-value)**: `clv = ((C-L)-(H-C))/(H-L)` ∈ [-1,1]. 일중 매수/매도 압력 방향.
- **거래량 가중 압력**: `mfv = clv × Volume`. 20일 누적 / 20일 거래량합 → `ofi` 정규화.
- **VWAP 프록시**: 일봉 typical price `(H+L+C)/3` rolling-VWAP(20) 대비 종가 위치 → `vwap_pressure` (four_axis의 `_vwap_rolling` 재사용).
- **스마트머니 체결강도**: 종가가 고가권 마감(`clv>0.5`)인 날의 거래량 비중 z-score. 기관성 매집은 종가 끌어올림 경향.
- **은밀 매집 변곡점**(핵심): 가격은 밴드 횡보(`ATR%`/`BB width` 낮음 + 가격 레인지 ≤ N%)인데 `ofi`·OBV 기울기는 우상향 → `accumulation=True`.
  공식: `range_bound = (rolling high/low 폭 ≤ 8%) AND (BBwidth 하위 40%)`; `stealth = OBV_slope>0 AND ofi>0.15`; `accumulation = range_bound AND stealth`.

### 3.3 통합
- **풀버전**: `four_axis_analyzer`가 OHLCV를 가진 시점에 호출 → 디테일 카드 OFI 배지/매집 표식. (네트워크 0)
- **경량**: 스캔 `_attach_order_flow`에서 `ofi_from_row`로 row의 `_VolRatio`·RSI·종가위치 파생 → `OFIScore` 필드.

---

## 4. 모듈3 — 크로스마켓 리드래그 (`cross_market_lead.py`)

연구 스펙(`.kkirikkiri/research/leadlag_spec.md`) 채택. US 섹터 ETF 마감 → 다음 KR 세션 테마 nudge.

### 4.1 공개 API
```python
def compute_leadlag() -> dict   # { kr_theme: {transfer: 0..1, target_kr_date, fired: bool, direction} }, 일 1회 캐시
def leadlag_for_theme(theme: str) -> dict   # 단일 테마 조회 (없으면 neutral 0.5)
```

### 4.2 핵심 (연구 스펙 §3,§5)
- US 바스켓: `SPY,QQQ,SMH,SOXX,XLK,LIT,XLE,ITA,XBI,URA,ARKK,^VIX` (1배치, `macro.py`/`etf.py` 재사용).
- 신호: `rs_z`(섹터 vs SPY) + `vol_z` + `cnh`(종가위치) → `strength=0.5+0.30·clip(rs_z/2)+0.15·clip(vol_z/2)+0.05·(cnh-0.5)·2`.
- **VIX 게이트**: 하락=1.0, 완만상승=0.6, 급등=0.3, panic(>30)×0.5.
- **데실 발화**: `|rs_z|≥1.3`(상/하위 10%)에서만 발화 + 매크로 테마는 이중확인(`vol_z≥1 OR cnh≥0.7`). 그 외 neutral 0.5.
- **US→KR 매핑**(테마명은 `theme_stocks.txt` 헤더와 일치): 반도체/HBM←SMH/SOXX, AI반도체/인프라←SMH/XLK, AISW/플랫폼←XLK/QQQ, EV/모빌리티←LIT/XLE, 방산/우주←ITA, 바이오←XBI, 원자력/SMR←URA. ARKK→투기테마 글로벌 승수.
- **캘린더**: US T(05:00 KST 마감) → KR 같은 날 개장. US 금→KR 월. KR 휴일 roll-forward. 1세션 hard decay.
- **never throw**: fetch 실패·비발화 시 전부 neutral 0.5. 스캐너를 게이팅하지 않고 nudge만.

> KR 스캔에만 적용(US→KR 방향). US 스캔에서는 `_attach_leadlag` no-op.

---

## 5. 모듈4 — 스캐너 통합 + 동적 가중치 (`regime_integration.py`)

### 5.1 최종 진입 점수 공식
기본 점수(기존)에 레짐 전환확률을 **곱한다**. 전환 초기 국면일수록 승수↑.

```
base        = TotalScore                      # 기존 4축+엔진 종합 (0..100)
# 1) 레짐 전환 승수 (모듈1) — 핵심
regime_mult = 1 + W_REGIME · sig.strength · (0.5 + 0.5·sig.fresh) · dir_sign
              # early_long 이고 fresh=1(갓 전환)이면 최대 (1 + W_REGIME)
              # early_exit 이면 dir_sign<0 → 감점.  중립/무신호면 1.0
# 2) OFI 보너스 (모듈2) — 매집 변곡점 가산
of_mult     = 1 + W_OFI · (OFIScore - 0.5)·2 · (1.3 if accumulation else 1.0)
# 3) 리드래그 nudge (모듈3) — KR만
ll_mult     = 1 + W_LEADLAG · (transfer - 0.5)·2          # transfer=0.5 → 1.0 (무영향)

RegimeEntryScore = clip(base · regime_mult · of_mult · ll_mult, 0, 100)
```
디폴트 가중: `W_REGIME=0.30, W_OFI=0.12, W_LEADLAG=0.08`. (regime이 지배적 — 스펙 #4 의도)
`fresh`가 "전환 초기일수록 높은 가중"을 직접 구현: 갓 전환(fresh≈1)이면 승수 풀가중, 전환 성숙(fresh→0)이면 절반.

### 5.2 적용 방식 (최소 침습 · 역가역성)
- 신규 필드만 부착: `Regime, RegimeProbs, RegimeSignal, OFIScore, Accumulation, LeadLag, RegimeEntryScore, RegimeReasons`.
- **기존 `TotalScore`·`EntryScore`는 변경하지 않음**. 정렬 키만 env로 토글:
  `REGIME_RANK=1`이면 `RegimeEntryScore`로 정렬, 아니면 기존 `TotalScore` 정렬(디폴트 보존).
- 즉 **켜기 전엔 기존 동작 100% 동일**. A/B 비교·롤백 안전.

### 5.3 `bottleneck_entry_signal` 연동
신규 레짐 라벨(`low_vol_uptrend` 등)을 `_attach_bottleneck`의 `regime=` 인자에 매핑 전달(현재 `r.get("Regime")`).

---

## 6. 폴백 · 안전 (전 모듈 공통)
1. **import 격리**: 모든 신규 import는 try/except. hmmlearn 없으면 rule_based, 그래도 모듈은 동작.
2. **never throw into scan**: 모든 `_attach_*`는 예외를 삼키고 setdefault 폴백(기존 `_attach_bottleneck` 패턴 동일).
3. **캐시**: 레짐/리드래그는 `regime_cache/`에 일자 키로 1회 계산. 스캔 성능 영향 최소.
4. **env 킬스위치**: `REGIME_DISABLE=1`이면 4개 attach 전부 no-op.
5. **배포**: `requirements.txt`에 `scikit-learn>=1.3,<1.9`, `hmmlearn>=0.3.0` 추가(Linux 휠 존재). 로컬 32-bit 앱은 폴백 경로로 무중단.

---

## 7. 검증 계획
- **단위 테스트(pytest, 3.13-64)**: 
  - 레짐: 합성 시계열(상승/하락/횡보 3구간 접합)에서 라벨 정확도, 라벨 안정성(재적합 invariance), 누수 테스트(미래 데이터가 과거 신호 불변), 폴백 경로(hmmlearn mock 제거).
  - OFI: 매집 합성 케이스(횡보+OBV상승)에서 `accumulation=True`, 분산 케이스 False.
  - 리드래그: 데실 게이트·VIX 게이트·캘린더 roll 단위 테스트.
  - 통합: 무신호 시 `RegimeEntryScore==TotalScore`(승수 1.0) 불변식.
- **예측력(IC)**: 기존 `web_app/score_eval.py`·`bottleneck_ic.py` 하네스 패턴으로 `RegimeEntryScore` forward-return IC를 기존 `TotalScore` 대비 측정.
- **코드 리뷰**: codex CLI로 신규 4파일 리뷰(백그라운드).

## 8. 작업 순서 (체크리스트는 TaskList와 동기화)
1. `regime_classifier.py` + 테스트 (모듈1)
2. `order_flow.py` + 테스트 (모듈2)
3. `cross_market_lead.py` + 테스트 (모듈3)
4. `regime_integration.py` + `engine_adapter.py` 4줄 통합 + four_axis OFI 배지 (모듈4)
5. requirements.txt·.gitignore 갱신, IC 점검, codex 리뷰, 문서(리뷰·교훈)

---

## 부록 A. 디폴트 설정 (코드 직행)
- `REGIME_CONFIG`: 연구 스펙 §6 dict 그대로.
- `LEADLAG_CONFIG`: 연구 스펙 §5 dict 그대로.
- 가중치: `W_REGIME=0.30, W_OFI=0.12, W_LEADLAG=0.08` (env override 가능).
- env 플래그: `REGIME_RANK`(정렬 토글), `REGIME_DISABLE`(킬), `REGIME_REFIT_BARS=21`.
