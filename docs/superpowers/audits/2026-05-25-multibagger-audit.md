# 월스트리트 패널 감사 — 멀티배거 파인더

**날짜:** 2026-05-25
**범위:** 풀스택 (게이트 F1~F8, 점수 Q1~Q6, yfinance 보강, 캐시/동시성, DIFF 백테스트, 라우트)
**산출물:** 리포트 전용 (코드 변경 없음 — 각 항목 별 사용자 승인 필요)
**심각도:** P0 = 기능 무력화 · P1 = 결과 왜곡/사용자 오인 · P2 = 코드 위생/명료성

---

## P0 — Critical (즉시 처리 권장)

### P0-1. DIFF 백테스트가 구조적으로 작동 불가
**파일:** `web_app/multibagger_backtest.py:29-44`, `web_app/multibagger_blueprint.py:99-101`

**증상:** `/api/multibagger/diff` 의 모든 응답이 `classify="UNKNOWN"`, `fail_gates=[]`.

**근거:**
- `_extract_baggers()` 는 `{ticker, start_close, end_close, multiple}` 만 적재. `snapshot_at_start` 키를 절대 만들지 않음.
- `multibagger_blueprint.py:99` `snap = b.get("snapshot_at_start") or {}` → 항상 빈 dict → `if not snap: ... "classify":"UNKNOWN"` 분기로 직행.
- 결과: stats.pass_n / watch_n / miss_n 모두 0, top_fail_gates 비어있음 → "5년 10배 종목 중 PASS는 몇 %" 라는 DIFF 탭의 핵심 가치 명제가 0% 달성.

**재현:**
```bash
python -m multibagger_backtest   # baggers_us.json 생성
curl -s localhost:5000/api/multibagger/diff | python -m json.tool
# baggers[].classify == "UNKNOWN" 가 100%
```

**수정 방안(권고):**
1. `_fetch_history` 시점에 yfinance `Ticker(sym).info` / `income_stmt` / `balance_sheet` / `cashflow` 를 함께 페치(또는 `start` 시점 직후 분기 보고서로). 시점 정합성을 위해 가장 오래된 fiscal year row 를 선택.
2. `multibagger_enrich.enrich_one` 의 시그니처를 `start_date` 옵션 받는 형태로 일반화하고 `_fetch_history` 에서 그 결과를 `Fundamentals(...)` → asdict → `snapshot_at_start` 에 적재.
3. 실패 시 `snapshot_at_start: null` 로 명시(`get(...) or {}` 의 None-vs-{} 양가성 제거).

---

## P1 — High (결과 왜곡 / 사용자 오인 위험)

### P1-1. `compose_score` 의 연산자 우선순위 — 무해하지만 명세와 다른 식
**파일:** `web_app/multibagger.py:268`

**현재:** `return min(100.0, core * 0.7 + bonus * 0.3 / 35 * 100)`

**연산자 우선순위 전개:** `core*0.7 + ((bonus*0.3)/35)*100` = `core*0.7 + bonus*(30/35)` ≈ `core*0.7 + bonus*0.857`. bonus 만점 35 → 30 기여. 합산 만점 100 — 수치는 맞음.

**문제:** 코드가 "Core×0.7 + Bonus×0.3 (normalized to 100)" 명세를 표현하는 가장 헷갈리는 방법. 리뷰어/외부 검증에서 버그로 오해받기 쉽고, 임계치 튜닝 시 의도 잃기 쉬움.

**수정 방안:** `bonus_pct = min(100, bonus / 35 * 100); return min(100.0, core*0.7 + bonus_pct*0.3)` 로 명시.

---

### P1-2. `_roic` 의 미국식 21% 세율 하드코딩 — 비미국 티커 모두 왜곡
**파일:** `web_app/multibagger_enrich.py:102-103`

**증상:** ROIC = EBIT × (1 − 0.21) / Invested. 미국 외 법인(아일랜드 12.5%, 한국 22%, 일본 30%+) 의 실효세율과 불일치 → F3 게이트(`F3_ROIC_MIN=0.10`) 통과/미통과가 잘못 결정됨.

**재현:** 같은 EBIT/equity 를 가진 일본 ADR 과 미국 종목이 동일 ROIC 로 평가됨.

**수정 방안:** `info.get("taxRate")` 또는 income_stmt 의 `TaxRateForCalcs` / `(TaxProvision/PretaxIncome)` 사용. 결측 시 sector median, 최후 fallback 21%.

---

### P1-3. `_price_signals` 가 1년 시계를 "52주 고가" 로 사용 — 신규 상장종목 왜곡
**파일:** `web_app/multibagger_enrich.py:78-89`

**증상:** `hist=t.history(period="1y")` 결과의 `closes.max()` 를 52주 고가로 사용. 상장 6개월된 종목은 6개월 고가가 곧 52주 고가가 됨 → F8 `from_52w_high` 가 실제 52주 대비 하락폭이 아닌 "보유 데이터 기간 내 하락폭" 으로 평가됨.

**파급:** F8 통과 기준이 `-50% <= from_52w_high <= -10%` 인데, 신규 상장 후 강한 랠리 종목이 부당하게 미통과.

**수정 방안:**
- `len(closes) < 252` (≈영업일 1년) 면 `from_52w_high = None` 으로 명시 → F8 결측 처리.
- 또는 `hist = t.history(period="2y")` 후 마지막 252봉으로 정확히 자르기.

---

### P1-4. `_insider_net_3m` 가 이름과 달리 기간 필터 없음
**파일:** `web_app/multibagger_enrich.py:108-123`

**증상:** 함수명/도큐멘트 모두 "3개월" 인데 실제 코드는 `df["Value"] * sign` 의 전체 합. yfinance `get_insider_transactions()` 는 통상 6개월 데이터 반환 → 실제로는 "최근 6개월 net 매수" 가 Bonus B 에 반영됨.

**파급:** Bonus 가 +10pt (insider_net_buy > 0). 윈도우가 두 배라 양/음 상쇄 시기 다름 → 종목별 비교 공정성 훼손.

**수정 방안:** `df = df[df["Start Date"] >= now - 90d]` 적용 후 합산. yfinance 컬럼명은 `"Start Date"` 또는 `"Date"` — `df.columns` 검사 후 동적 매핑.

---

### P1-5. `_insider_net_3m` 의 부호 판정이 "sale" 한 단어에만 의존
**파일:** `web_app/multibagger_enrich.py:118-120`

**증상:** `"Conversion"`, `"Exercise"`, `"Gift"`, `"Tax Withholding"` 등 매도가 아닌 이벤트도 모두 + 부호로 처리되어 net 매수 과대평가.

**재현:** AAPL 등 옵션 행사가 잦은 대형주 — `Exercise of Derivative` 트랜잭션이 Value 양수로 표기되어 buy 로 분류.

**수정 방안:** 명시적 화이트리스트 (`Purchase`, `Acquisition`) / 블랙리스트 (`Sale`, `Disposition`) 양방향 매칭 + 미매칭 항목은 0 (무시).

---

### P1-6. `tie_break_key` docstring 과 정렬 동작이 반대
**파일:** `web_app/multibagger.py:271-278`, `multibagger.py:382-383`

**현재:** tie_break_key 가 `-(f.market_cap or 1e18)` 반환 → 이후 `_sort_key` 에서 `tuple(-x for x in tie_break_key(...))` 로 다시 negate → 시가총액은 양수로 복귀, 오름차순 정렬이라 **작은 시총 우선**.

**평가:** 멀티배거 전략 관점에선 소형주 우선이 합리. 다만 docstring `"내림차순 정렬 가정 (큰 게 우선)"` 과 모순 — q4/roic/q2 는 정상적으로 "큰 게 우선" 인데 market_cap 만 반대로 동작. 의도와 동작이 맞아도 코드의 자명성 실패.

**수정 방안:** tie_break_key 에서 market_cap 도 양수로 반환하고 docstring 을 `"market_cap 만 작은 게 우선 — 멀티배거 전략"` 으로 명시. 또는 dual-key 정렬로 분리.

---

### P1-7. `_pre_filter_F1_F2` 가 base scan 결측 시 후보 누락 — 침묵 실패
**파일:** `web_app/multibagger.py:285-301`

**증상:** base scan row 에 `market_cap` / `ebitda` / `fcf` 중 하나라도 없으면 `continue` 로 폐기. 그러나 멀티배거 enrichment 가 바로 그 값들을 채우는 책임 → 사전 필터에서 폐기 시 yfinance 보강 기회를 영구히 잃음.

**파급:** base scan(quant_nexus) 가 EBITDA 미산출 종목 → 멀티배거 후보군에서 영구 제외.

**수정 방안:** F1 (market_cap) 만 사전 필터로 두고, F2 (ebitda>0, fcf>0) 는 enrichment 이후 `classify` 단계에 위임. 또는 enrichment 후 재필터 패스 추가.

---

### P1-8. `multibagger_warmup_loop` — 락 누수 시나리오
**파일:** `web_app/multibagger_blueprint.py:127-140`

**증상:** `_multibagger_build_lock.acquire(blocking=False)` 성공 후 `_rebuild_multibagger_us` 가 `KeyboardInterrupt` 같은 BaseException 으로 죽으면 `try/finally` 가 해당 예외도 잡지만, 그 사이 `_maybe_trigger_multibagger_build` 가 들어오면 `acquire` 실패 → 영구 락. (현재 `try/finally` 가 release 보장하므로 보통은 OK, 다만 `flask_app._multibagger_build_lock` 자체를 다른 곳에서 acquire 한 채 죽으면 복구 불가.)

**수정 방안:** `time.monotonic()` 기반 락 보유 시간 모니터 + 60분 초과 시 강제 release 경고 로그 + 헬스체크 엔드포인트.

---

### P1-9. DGS10 fetch timeout=5s, 재시도 없음
**파일:** `web_app/multibagger_rates.py:34`

**증상:** FRED 5초 안에 응답 못 하면 즉시 fallback. F7 의 hirate 분기(`F7_HIRATE_DGS10_PCT=4.0`) 가 동적 임계치인데 데이터 없으면 보수적 분기(저금리 가정) 로 떨어짐.

**수정 방안:** 3회 backoff 재시도 (1s/2s/4s), 최종 실패 시 캐시 fallback (현행).

---

## P2 — Medium (코드 위생 / 장기 부채)

### P2-1. `_yoy` 가 fiscal year column 정렬 가정
**파일:** `web_app/multibagger_enrich.py:51-65`

**현재:** `cols[0]` = 최신, `cols[1]` = 직전. yfinance 의 `income_stmt` 는 통상 컬럼이 최신→과거 순이지만 명시적 sort 없음. 라이브러리 업데이트나 비표준 reporter 의 종목에서 순서 뒤집힐 수 있음.

**수정 방안:** `cols_sorted = sorted(df.columns, reverse=True)` 후 인덱싱.

---

### P2-2. `compose_score` — vals 전부 None 일 때 core=0 → bonus 단독으로 점수 형성
**파일:** `web_app/multibagger.py:262-268`

**증상:** Q1~Q6 모두 결측 → core=0, bonus 만점이면 score=30. 그러나 이 상황은 `classify` 단계에서 보통 `EXCLUDED` (optional 3개 이상 결측) — 그래도 PASS/WATCH 진입한 종목 중 일부에서 발생 가능.

**수정 방안:** `if not vals: return 0.0` 또는 명세 합의 후 minimum vals 임계(예: 3개 미만이면 score=None) 로 응답에서 제외.

---

### P2-3. 캐시 `clear() + update()` non-atomic
**파일:** `web_app/multibagger_blueprint.py:191-192`

**증상:** rebuild 워커가 `_multibagger_results_cache.clear()` 직후 다른 스레드가 read → 빈 dict 관찰 → "warming" 응답 반환. 사용자 입장에선 갱신 직전 짧은 순간 데이터 사라짐.

**수정 방안:** new dict 생성 후 atomic 치환:
```python
new_state = {"_ts": time.time(), "data": result}
flask_app._multibagger_results_cache = new_state  # rebind
```
단 module-level rebind 는 import 한 곳마다 영향 — `lock` 안에서 swap 또는 RWlock 사용.

---

### P2-4. `_rebuild_multibagger_us` 가 base scan empty 시 silent abort
**파일:** `web_app/multibagger_blueprint.py:182-184`

**증상:** `logging.info("...aborting build")` 만 남김. API 응답에 사유 미반영 → UI 는 영원히 warming 상태처럼 보임.

**수정 방안:** `meta` 에 `"abort_reason": "base_cache_empty"` 와 마지막 시도 timestamp 노출.

---

### P2-5. `eval_f4` 의 결측 판정이 양쪽 모두 None 일 때만 None
**파일:** `web_app/multibagger.py:90-95`

**현재:** `if _missing(f.fcf_yield) and _missing(f.pb): return None`. 한쪽만 결측이면 다른 쪽으로 판정. 합리적이지만 missing 카운트가 0 으로 잡혀 EXCLUDED 분류 회피 → "데이터 충분도" 측면에서 너그러움.

**검토 권고:** 멀티배거 명세상 의도된 동작인지 확인. 의도면 docstring 으로 명시.

---

### P2-6. `score_q4` 의 비대칭 스케일
**파일:** `web_app/multibagger.py:214-220`

**현재:** diff<0 → `_clamp01(diff, -0.10, 0.0) * 0.5` (0~50). diff>=0 → `50 + _clamp01(diff, 0, 0.15)*0.5` (50~100). `_clamp01` 가 0~100 반환하므로 ×0.5 = 0~50 — 산식 자체는 맞으나 `score_q3` 과 변환 방식 다름 → 일관성 결함. 리뷰에서 버그로 오인 우려.

**수정 방안:** Q3, Q4 모두 같은 헬퍼 `_two_sided_score(diff, neg_floor, pos_ceiling)` 로 통일.

---

### P2-7. 테스트 커버리지 — 통합/회귀 시나리오 부족
**파일:** `web_app/tests/test_multibagger_*.py` (7개)

**관찰(파일명 기준):** classify / gates / scoring / enrich / backtest / rates / api 단위 테스트는 존재. 빠진 것:
- `compose_score` 의 bonus-only 케이스 (P2-2).
- `_pre_filter_F1_F2` 침묵 누락 회귀 테스트 (P1-7).
- DIFF 라우트의 snapshot 결측 시 응답 계약 테스트 (P0-1 회귀 방지).
- 동시성: rebuild 중 read 가 빈 dict 보지 않음 보장.

**수정 방안:** 위 4개 케이스 추가.

---

## 부록 — 검토했으나 결함 아님

| 항목 | 결론 |
|---|---|
| `get_dgs10` fetch 실패 시 `_ts` 미갱신 → "영구 stale" 우려 | 캐시는 여전히 stale(>= TTL) 로 판정되므로 다음 호출에서 재시도. 정상 동작. |
| `multibagger_rates` JSON 마이그레이션 | pickle.load RCE 회피 — 잘 처리됨. |
| `multibagger_backtest` JSON 마이그레이션 | 동일 — 잘 처리됨. |
| `_multibagger_warmup_started` double-check | thread-safe singleton 패턴 정상. |

---

## 권고 처리 순서

1. **P0-1 즉시** — DIFF 탭의 사용자 가치가 0. snapshot_at_start 적재 또는 명세 변경.
2. **P1-2, P1-3, P1-4, P1-5** — 결과 정확도 직격. 한 묶음으로 enrichment 모듈 패치.
3. **P1-1, P1-6, P1-7** — 정합성/명료성. 코드 리뷰 1회로 함께 처리.
4. **P1-8, P1-9** — 운영 안정성. 별도 PR.
5. **P2-*** — 다음 분기 정리 작업으로.

---

**감사 종료.** 각 항목별 수정 적용 여부 / 우선순위 / 단위 결정 후 별도 승인 시 코드 변경 진행.
