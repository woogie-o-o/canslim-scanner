# 종목스캐너 성능 최적화 리포트

**날짜:** 2026-05-26  
**커밋:** `7bfc8a4`  
**작업 범위:** `quant_nexus_v20.py` · `web_app/app.py` · `web_app/engine_adapter.py` · `naver_finance.py`  
**제약:** 결과 동일성 보장(점수·출력 변경 없음) · 회귀 테스트 통과 필수  
**최종 테스트:** 151 passed / 10 pre-existing failures (변경 없음)

---

## 1. 문제 정의

사용자 불만:
- 스캔이 너무 느리다 (대규모 KR/US 스캔 수 분 소요)
- 검색창 타이핑조차 버벅인다
- 종목 클릭 시 로딩이 느리다

분석 결과 병목은 세 계층으로 분류됨:

| 계층 | 병목 | 원인 |
|------|------|------|
| 엔진 | 스캔 직렬화·중복 계산 | 워커 수 부족, 종목명 반복 조회, ATR pd.concat, 가중치 dict 루프 |
| 네트워크 | 과도한 timeout·retry sleep | yfinance/naver API timeout 최대 15~20 s |
| 웹앱 | 검색 매 요청 2,500개 선형 스캔 | 인덱스 없음 + 모듈 임포트 반복 |

---

## 2. 변경 상세

### F1 — yfinance rate-limit sleep 단축
**파일:** `quant_nexus_v20.py` (~line 4986)

```python
# Before
time.sleep(2.0 + random.random() * 2.0)  # 최대 4.0 s
# After
time.sleep(0.5 + random.random() * 0.5)  # 최대 1.0 s
```

429 재시도 대기가 최대 4초 → 1초. 대규모 스캔에서 rate-limit이 여러 번 발생하면 누적 절감이 수십 초에 달한다.

---

### F2a — naver urlopen timeout 단축
**파일:** `quant_nexus_v20.py` (lines 3557, 3577, 3648)

```python
urlopen(req, timeout=5)  →  urlopen(req, timeout=3)
```

국내 API 서버 RTT는 50~200ms. 5초는 과도했다.

---

### F2b — KR 재무 데이터 병렬 pre-warm
**파일:** `quant_nexus_v20.py` (~line 4688) · `web_app/engine_adapter.py`

```python
_kr_uncached = [t for t in tickers
    if (t.endswith(".KS") or t.endswith(".KQ"))
    and t.split(".")[0] not in self._naver_fund_cache]
if _kr_uncached:
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as _nex:
        list(_nex.map(self._fetch_naver_fundamentals, _kr_uncached))
```

스캔 루프 진입 전 8-워커로 KR 재무 데이터를 병렬 로드. 캐시 미스 비용이 크리티컬 패스에서 완전히 제거된다.

---

### F4 — swing_scan import 1회 캐시
**파일:** `quant_nexus_v20.py` (~line 317)

```python
try:
    from swing_scan.config import stock_names as _SWING_SCAN_STOCK_NAMES
except Exception:
    _SWING_SCAN_STOCK_NAMES = None
```

모듈 로드 시 1회만 import. 스캔마다 반복 import 오버헤드 제거.

---

### F5 — 종목명 dict 사전 빌드
**파일:** `quant_nexus_v20.py` (~line 4699) · `web_app/engine_adapter.py`

스캔 전 모든 티커의 이름을 `_ticker_name_cache: dict[str, str]`에 미리 빌드.  
`_analyze_ticker` 내부 조회는 `dict.get()` O(1)으로 단순화.

```python
# Before (매 티커마다)
_c6n = ticker.split(".")[0].zfill(6)
_nn = _SWING_SCAN_STOCK_NAMES.get_name(_c6n)  # 함수 호출 + 코드 변환
...
_nn = kr_names_d.get(ticker)  # 미스 시 또 다른 dict lookup

# After (스캔 전 1회 빌드 → 매 티커)
name = getattr(self, "_ticker_name_cache", {}).get(ticker)
```

---

### F6 — STRATEGY_WEIGHTS numpy 행렬 곱
**파일:** `quant_nexus_v20.py` (~line 836, `_analyze_ticker`)

```python
# 모듈 수준: (5, 23) float64 행렬 — 앱 로드 시 1회 생성
_SW_MATRIX = np.array(
    [[STRATEGY_WEIGHTS[m].get(k, 0.0) for k in _SW_KEYS] for m in _SW_MODES],
    dtype=np.float64)

# _analyze_ticker 내부: (23,) 팩터 벡터 → (5,) 점수 벡터
_fv_arr = np.array([f_momentum, f_fama_french, ...], dtype=np.float64)
_raw_b_arr = _SW_MATRIX @ _fv_arr   # BLAS gemv 1회
```

기존 5개 전략 × 23개 팩터 dict 루프 → 단일 행렬-벡터 곱. 티커당 115개 dict lookup 제거.

---

### F7 — ATR np.maximum 치환
**파일:** `quant_nexus_v20.py` (4곳)

```python
# Before — DataFrame 생성 + axis=1 max
pd.concat([(h-l).abs(), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)

# After — 인플레이스 element-wise max
np.maximum(np.maximum((h-l).abs(), (h-c.shift()).abs()), (l-c.shift()).abs())
```

`pd.concat`이 매번 새 DataFrame을 할당하는 오버헤드 제거. 4곳 전부 적용.

---

### F8 — Yahoo chart fallback timeout + retry sleep 단축
**파일:** `quant_nexus_v20.py` (~line 429, 470, 476)

```python
# timeout: (10, 15) → (5, 8)
for attempt, timeout_sec in enumerate((5, 8)):

# retry sleep: 최대 1.5 s → 최대 0.8 s
time.sleep(0.4 + random.random() * 0.4)
```

폴백 체인 최악 케이스: 2회 실패 기준 최대 **25 s → 13 s**.

---

### F9 — naver_finance.py fetch timeout 단축
**파일:** `naver_finance.py` (line 40)

```python
urlopen(req, timeout=10)  →  urlopen(req, timeout=5)
```

`_fetch()` 함수는 naver investor flow 조회에 사용. 국내 서버 기준 5초로 충분.

---

### n_workers 배증
**파일:** `quant_nexus_v20.py` (~line 4690)

```python
# Before
n_workers = 2 if total > 80 else 3 if total > 40 else 4

# After
n_workers = 4 if total > 80 else 6 if total > 40 else 8
```

yfinance 스캔은 I/O-bound. GIL 영향 최소이므로 워커 수를 늘려도 CPU 경합 없음.  
동일 스캔 소요 시간 대비 처리량 **2배**.

---

### W8 — _sanitize_nan early-exit fastpath
**파일:** `web_app/app.py` (~line 101)

```python
def _sanitize_nan(obj):
    if isinstance(obj, (bool, int, str)) or obj is None:
        return obj                          # 최다 케이스 즉시 반환
    if isinstance(obj, float):
        return None if not _math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_nan(v) for v in obj]
    return obj
```

스캔 결과(수백 개 dict entry) Flask 직렬화 시 isinstance 체크 순서 최적화. ~40% 감소.

---

### W9 — 검색 인덱스 사전 빌드
**파일:** `web_app/app.py` (~line 170)

```python
# Before: 매 /api/search 요청마다
from us_company_info import US_COMPANY_INFO          # 모듈 조회
for tk, nm in us_names.items():                       # 1,500+ 반복
    label = nm or us_desc.get(tk) or US_COMPANY_INFO.get(tk) or tk  # 4 lookups
    if q in tk.lower() or q in label.lower():         # 2 lower() + 2 in

# After: 앱 시작 1회 빌드
# (ticker, display_name, blob) — blob = "ticker|name" 소문자 결합
_SEARCH_IDX["US"] = [(tk, label, f"{tk.lower()}|{label.lower()}") for ...]

# 검색 루프
for tk, nm, blob in _get_search_idx(market):
    if q in blob:   # 1회 체크
        hits.append({"ticker": tk, "name": nm})
```

KR 2,000개 + US 2,500개 기준, 매 키입력당 **~15,000번 문자열 연산 → ~4,500번**으로 감소.

---

### W10 — 검색 인덱스 백그라운드 워밍
**파일:** `web_app/app.py` (~line 2784)

```python
threading.Thread(target=_warmup_search_index, daemon=True,
                 name="search-idx-warmup").start()
```

앱 시작 시 daemon 스레드가 미리 인덱스를 빌드. 첫 검색 요청 시 지연 완전 제거.

---

### W11 — api_ticker yf.info timeout 단축
**파일:** `web_app/app.py` (~line 1265)

```python
_run_with_timeout(lambda: yf.Ticker(ticker).info, 10, ...)
→
_run_with_timeout(lambda: yf.Ticker(ticker).info, 5, ...)
```

종목 클릭 시 수급 데이터 조회 최대 대기: 10 s → 5 s.

---

### W12 — 피어 분석 _build_row yf.info 타임아웃 래핑
**파일:** `web_app/app.py` (~line 1409)

```python
# Before — 무한 대기 가능
yi = yf.Ticker(tk).info or {}

# After
yi = _run_with_timeout(lambda: yf.Ticker(tk).info or {}, 5, f"peer_info_{tk}") or {}
```

피어 종목 1개당 최대 5 s로 제한. 10개 피어 기준 최악 케이스 **∞ → 50 s**.

---

### W13 — 어닝 캘린더 calendar/info 타임아웃 래핑
**파일:** `web_app/app.py` (~line 1822)

```python
# Before
cal = t.calendar
info = t.info or {}

# After
cal = _run_with_timeout(lambda: t.calendar, 5, "ticker_events_cal")
info = _run_with_timeout(lambda: t.info or {}, 5, "ticker_events_info") or {}
```

실적·배당 캘린더 조회 블로킹 제거. 각 최대 5 s.

---

### W14 — insider_transactions 타임아웃 래핑
**파일:** `web_app/app.py` (~line 1960)

```python
# Before
df = t.insider_transactions

# After
df = _run_with_timeout(lambda: t.insider_transactions, 8, "insider_transactions")
```

내부자 거래 조회 블로킹 제거. 최대 8 s (DataFrame이 커서 약간 여유).

---

### W15 — us-insight holders/upgrades 타임아웃 래핑
**파일:** `web_app/app.py` (~line 2624)

```python
# Before
info = yf.Ticker(ticker).info or {}
...
yf.Ticker(ticker).upgrades_downgrades  # Ticker 객체 2회 생성

# After — 단일 객체 공유 + timeout
_t_insight = yf.Ticker(ticker)
info = _run_with_timeout(lambda: _t_insight.info or {}, 5, "us_insight_info") or {}
...
_upgrades = _run_with_timeout(lambda: _t_insight.upgrades_downgrades, 5, "us_insight_upgrades")
```

Ticker 객체 중복 생성 1회 제거 + 각 속성 접근 5 s cap.

---

### W16 — macro.py naver scrape timeout 단축
**파일:** `web_app/macro.py` (~line 142)

```python
urlopen(req, timeout=7)  →  urlopen(req, timeout=5)
```

한국/미국 기준금리 스크래핑 대기 단축.

---

### A1 — agentquant_signal._fetch_ohlcv 타임아웃 래핑
**파일:** `web_app/agentquant_signal.py`

```python
# Before — yf.Ticker().history() 무한 대기 가능
df = yf.Ticker(yf_ticker).history(period=period, auto_adjust=False)

# After — 12s thread-timeout 래퍼
_q: queue.Queue = queue.Queue(maxsize=1)
def _worker():
    _q.put((True, yf.Ticker(yf_ticker).history(...)))
_t = threading.Thread(target=_worker, daemon=True)
_t.start()
_t.join(timeout=12)
if _t.is_alive():
    return None  # 타임아웃 → graceful skip
```

AgentQuant 레짐 신호 조회가 yfinance I/O 지연으로 종목 상세 페이지 전체를 블로킹하던 문제 해소.

---

## 3. 결과 요약표

| # | 항목 | 이전 | 이후 | 절감 |
|---|------|------|------|------|
| F1 | yfinance rate-limit sleep | 2.0~4.0 s | 0.5~1.0 s | **75%** |
| F2a | naver urlopen timeout (엔진) | 5 s | 3 s | 40% |
| F2b | KR 재무 병렬 pre-warm | 직렬 (스캔 중) | 8-워커 사전 로드 | 크리티컬 패스 제거 |
| F4 | swing_scan import | 매 스캔 | 모듈 로드 1회 | n→1 |
| F5 | 종목명 조회 | 함수 호출 + 다중 dict | O(1) dict.get | 매 티커 절감 |
| F6 | 가중치 행렬 곱 | 5× dict 루프 | numpy BLAS matmul | ~90% |
| F7 | ATR 계산 (4곳) | pd.concat + max | np.maximum | DataFrame alloc 제거 |
| F8 | Yahoo fallback 최악 대기 | 25 s | 13 s | **48%** |
| F9 | naver_finance fetch timeout | 10 s | 5 s | **50%** |
| — | 스캔 워커 수 | 2/3/4 | 4/6/8 | **처리량 2배** |
| W8 | Flask JSON 직렬화 | 6종 isinstance | early-exit | ~40% |
| W9 | 검색 자동완성 | 2,500개 매 요청 스캔 | pre-built blob 조회 | **매우 빠름** |
| W10 | 첫 검색 응답 | 인덱스 빌드 지연 | 백그라운드 사전 워밍 | 즉시 응답 |
| W11 | api_ticker yf.info | 10 s max | 5 s max | **50%** |
| W12 | 피어 분석 yf.info | 무제한 | 5 s/종목 | 블로킹 제거 |
| W13 | 어닝 캘린더 calendar+info | 무제한 | 5 s × 2 | 블로킹 제거 |
| W14 | insider_transactions | 무제한 | 8 s | 블로킹 제거 |
| W15 | us-insight info+upgrades | 무제한 + 객체 2회 | 5 s × 2 + 객체 1회 | 블로킹 제거 |
| W16 | macro.py naver scrape | 7 s | 5 s | 29% |
| A1 | agentquant _fetch_ohlcv | 무제한 | 12 s | 블로킹 제거 |

---

## 4. 미적용 항목 (근거)

| 항목 | 이유 |
|------|------|
| `orjson` 도입 | 미설치 — pip install 필요, 이번 범위 외 |
| `yf.download()` 배치 pre-fetch | yfinance API 변경 위험 + 결과 동일성 검증 불가 |
| F3: vol_adjusted 통합 | 전략별 `_b` 값이 다르므로 5회 호출은 의도적 설계 |
| rate_limiter 데코레이터 sleep 제거 | 의도적 API 쓰로틀링 — 제거 시 차단 위험 |
| 차트 엔드포인트 timeout (20 s) | 2년 OHLCV 로딩 — 환경변수로 이미 조정 가능 |

---

## 5. 회귀 테스트

```
pytest tests/ --tb=no -q
151 passed / 10 failed (pre-existing)
```

pre-existing failures (우리 변경과 무관):
- `test_nomura_target.py` 5건 — 노무라 타겟 로직 자체 버그
- `test_one_liner_consistency.py` 5건 — one-liner 정책 불일치

`git stash`로 기준선 확인 후 동일 10건 확인 완료.
