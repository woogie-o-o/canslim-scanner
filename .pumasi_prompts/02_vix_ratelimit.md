`C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너\web_app\engine_adapter.py` 의 `_fetch_vol_index` 메서드만 수정. **다른 함수/메서드 변경 금지.**

## 현재 동작 (79~115번째 줄 부근)
```python
@staticmethod
def _fetch_vol_index(market: str) -> float:
    now = time.time()
    with _VIX_CACHE_LOCK:
        cached = _VIX_CACHE["value"]
        cached_ts = _VIX_CACHE["ts"]
        if cached is not None and (now - cached_ts) < _VIX_TTL_SEC:
            return float(cached)

    import yfinance as _yf
    for attempt in range(2):
        try:
            v = _yf.Ticker("^VIX").history(period="5d")
            if not v.empty:
                val = float(v["Close"].iloc[-1])
                with _VIX_CACHE_LOCK:
                    _VIX_CACHE["value"] = val
                    _VIX_CACHE["ts"] = time.time()
                return val
        except Exception as e:
            if attempt == 0:
                time.sleep(2.0)
                continue
            logging.warning("[Adapter] vol index fetch failed (%s): %s", market, e)
    if cached is not None:
        return float(cached)
    return 20.0
```

## 새 동작 요구사항
1. 재시도 3회 (attempt 0/1/2)
2. 지수 + 지터 백오프:
   - attempt 0: 즉시
   - attempt 1: random.uniform(3.0, 5.0) 초 sleep 후 재시도
   - attempt 2: random.uniform(8.0, 12.0) 초 sleep 후 재시도
3. 예외 메시지에 "rate" 또는 "Too Many" 또는 "429" 가 포함되면:
   - `logging.warning("[Adapter] vol index rate-limited (%s, attempt %d): %s", market, attempt, e)` 만 출력
   - 일반 예외는 기존대로 `logging.warning("[Adapter] vol index fetch failed (%s): %s", market, e)`
4. 모든 재시도 실패 + 캐시도 없으면 20.0 반환 (기존 동작 유지)
5. **stale 캐시 사용 로직 추가**: TTL(300초) 만료됐지만 cached_ts가 15분(900초) 이내이고 새 페치가 실패하면:
   - 마지막 attempt 후 `logging.info("[VIX] stale cache used (%.1fmin old)", (now - cached_ts)/60.0)` 출력
   - 그 stale 값 반환

## 시그니처/캐시 변경 금지
- `def _fetch_vol_index(market: str) -> float:` static method 유지
- 모듈 상단 `_VIX_CACHE`, `_VIX_CACHE_LOCK`, `_VIX_TTL_SEC` 변경 금지
- 다른 import 추가는 `import random` 만 허용 (`import time` 은 이미 있음)

## 추가 import
파일 상단의 import 블록에 `import random` 추가 (아직 없다면).

## 검증
```bash
python -m py_compile "C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너\web_app\engine_adapter.py"
```

## 금지사항
- 다른 메서드/함수/클래스 변경 금지
- 새 외부 의존성 추가 금지 (random, time 만 사용)
- 캐시 자료구조 변경 금지
