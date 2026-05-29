# Multibagger Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Flask 종목스캐너에 `/multibagger` 독립 페이지를 추가해 US 종목 중 잠재적 멀티배거 후보를 PASS/WATCH/DIFF 3레이어로 식별·랭킹·회고검증한다.

**Architecture:** 기존 US 스캔 pickle 캐시 위에 어댑터 1개를 얹는다. 신규 모듈 `multibagger.py`(게이트·점수), `multibagger_enrich.py`(yfinance 보강), `multibagger_rates.py`(FRED 10Y), `multibagger_backtest.py`(DIFF 5y 10배 명단)로 책임 분리. Flask 라우트 5개 추가, 백그라운드 워머 루프 1개 추가. 모든 yfinance/FRED는 mock-친화 인터페이스로 분리.

**Tech Stack:** Python 3.11 / Flask / yfinance / pandas / pytest. 기존 `cache_v19/` pickle 패턴과 `_us_warmup_loop` 패턴 재사용.

**Spec source:** `docs/superpowers/specs/2026-05-25-multibagger-finder-design.md`

---

## File Structure

| 파일 | 책임 | 신규/수정 |
|---|---|---|
| `web_app/multibagger.py` | 게이트 평가, 점수화, 분류 (PASS/WATCH), 임계 상수 | 신규 |
| `web_app/multibagger_enrich.py` | yfinance 호출로 펀더멘털/가격 시계열 보강 | 신규 |
| `web_app/multibagger_rates.py` | FRED DGS10 fetcher + 24h pickle 캐시 | 신규 |
| `web_app/multibagger_backtest.py` | 5년 10배 명단 추출 배치 (수동) | 신규 |
| `web_app/app.py` | 라우트 5개 + 워머 루프 wiring | 수정 |
| `web_app/templates/multibagger.html` | 페이지 마크업 | 신규 |
| `web_app/static/multibagger.css` | 페이지 스타일 (theme.css 변수 사용) | 신규 |
| `web_app/static/multibagger.js` | Layer 탭, 고급 패널, 폴링 | 신규 |
| `web_app/tests/test_multibagger_gates.py` | F1~F8 게이트 평가 | 신규 |
| `web_app/tests/test_multibagger_scoring.py` | Q1~Q6 정규화, Bonus, tie-break | 신규 |
| `web_app/tests/test_multibagger_classify.py` | PASS/WATCH/MISS 분기 | 신규 |
| `web_app/tests/test_multibagger_rates.py` | FRED fetch + 캐시 만료 | 신규 |
| `web_app/tests/test_multibagger_enrich.py` | yfinance 보강 (mock) | 신규 |
| `web_app/tests/test_multibagger_api.py` | Flask test client 응답·헤더 | 신규 |
| `web_app/tests/test_multibagger_backtest.py` | 5y 계산·상폐 skip·결측 플래그 | 신규 |

`multibagger.py` 분리 원칙: 순수 함수만(I/O 없음). 보강·캐시·라우트는 별도 모듈로.

---

## Task 1: 모듈 스캐폴딩 + 임계 상수

**Files:**
- Create: `web_app/multibagger.py`
- Create: `web_app/tests/test_multibagger_gates.py`

- [ ] **Step 1: 스캐폴딩 작성**

`web_app/multibagger.py`:

```python
"""멀티배거 파인더 — 순수 평가/점수 함수."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# F1~F8 디폴트 임계
DEFAULTS = {
    "F1_MCAP_MIN": 200_000_000,
    "F1_MCAP_MAX": 2_000_000_000,
    "F3_ROIC_MIN": 0.10,
    "F4_FCF_YIELD_MIN": 0.05,
    "F4_PB_MAX": 3.0,
    "F5_REVENUE_YOY_MIN": 0.05,
    "F7_ICR_MIN": 3.0,
    "F7_DEBT_EBITDA_MAX": 3.0,
    "F7_ICR_MIN_HIRATE": 4.0,
    "F7_DEBT_EBITDA_MAX_HIRATE": 2.5,
    "F7_HIRATE_DGS10_PCT": 4.0,
    "F8_FROM_52W_HIGH_MIN": -0.50,
    "F8_FROM_52W_HIGH_MAX": -0.10,
    "F8_1M_RETURN_MAX": 0.30,
}

CORE_GATES_REQUIRED = ("F1", "F2", "F8")  # WATCH도 필수 통과
CORE_GATES_OPTIONAL = ("F3", "F4", "F5", "F6", "F7")  # WATCH는 1~2개 부족 허용
ALL_GATES = CORE_GATES_REQUIRED + CORE_GATES_OPTIONAL


@dataclass
class Fundamentals:
    """게이트 평가에 필요한 모든 입력. 결측은 None."""
    market_cap: Optional[float] = None
    ebitda: Optional[float] = None
    fcf: Optional[float] = None
    roic: Optional[float] = None
    roic_prev: Optional[float] = None
    fcf_yield: Optional[float] = None
    pb: Optional[float] = None
    revenue_yoy: Optional[float] = None
    revenue_yoy_prev: Optional[float] = None  # B4용 (1년 전 YoY)
    ebitda_yoy: Optional[float] = None
    fcf_yoy: Optional[float] = None
    assets_yoy: Optional[float] = None
    icr: Optional[float] = None
    debt_ebitda: Optional[float] = None
    from_52w_high: Optional[float] = None  # 음수 (예: -0.20 = 20% 빠짐)
    return_1m: Optional[float] = None
    sector: Optional[str] = None
    insider_net_buy_3m: Optional[float] = None
    buyback_yield_ttm: Optional[float] = None
    capex_yoy: Optional[float] = None  # F6 N/A 판정용
    dgs10_pct: Optional[float] = None
```

- [ ] **Step 2: 테스트 파일 스캐폴딩**

`web_app/tests/test_multibagger_gates.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger as mb


def test_defaults_present():
    assert mb.DEFAULTS["F1_MCAP_MIN"] == 200_000_000
    assert mb.DEFAULTS["F1_MCAP_MAX"] == 2_000_000_000


def test_fundamentals_all_optional():
    f = mb.Fundamentals()
    assert f.market_cap is None
```

- [ ] **Step 3: 테스트 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_gates.py -v`
Expected: 2 passed.

- [ ] **Step 4: 커밋**

```bash
git add web_app/multibagger.py web_app/tests/test_multibagger_gates.py
git commit -m "feat(multibagger): scaffold module + threshold defaults"
```

---

## Task 2: F1·F2·F8 (필수 게이트)

**Files:**
- Modify: `web_app/multibagger.py` (append `evaluate_gate_*` 함수)
- Modify: `web_app/tests/test_multibagger_gates.py`

- [ ] **Step 1: 실패 테스트 작성**

`web_app/tests/test_multibagger_gates.py` 추가:

```python
def test_f1_size_band_pass():
    f = mb.Fundamentals(market_cap=1_000_000_000)
    assert mb.eval_f1(f, mb.DEFAULTS) is True

def test_f1_size_too_small():
    f = mb.Fundamentals(market_cap=100_000_000)
    assert mb.eval_f1(f, mb.DEFAULTS) is False

def test_f1_size_too_large():
    f = mb.Fundamentals(market_cap=5_000_000_000)
    assert mb.eval_f1(f, mb.DEFAULTS) is False

def test_f1_missing():
    f = mb.Fundamentals(market_cap=None)
    assert mb.eval_f1(f, mb.DEFAULTS) is None

def test_f2_profitability():
    assert mb.eval_f2(mb.Fundamentals(ebitda=1.0, fcf=1.0), mb.DEFAULTS) is True
    assert mb.eval_f2(mb.Fundamentals(ebitda=-1.0, fcf=1.0), mb.DEFAULTS) is False
    assert mb.eval_f2(mb.Fundamentals(ebitda=1.0, fcf=None), mb.DEFAULTS) is None

def test_f8_entry():
    ok = mb.Fundamentals(from_52w_high=-0.20, return_1m=0.10)
    assert mb.eval_f8(ok, mb.DEFAULTS) is True
    too_high = mb.Fundamentals(from_52w_high=-0.05, return_1m=0.10)
    assert mb.eval_f8(too_high, mb.DEFAULTS) is False
    too_deep = mb.Fundamentals(from_52w_high=-0.60, return_1m=0.10)
    assert mb.eval_f8(too_deep, mb.DEFAULTS) is False
    overheated = mb.Fundamentals(from_52w_high=-0.20, return_1m=0.40)
    assert mb.eval_f8(overheated, mb.DEFAULTS) is False
```

- [ ] **Step 2: 테스트 실행 → 실패 확인**

Run: `python -m pytest web_app/tests/test_multibagger_gates.py -v`
Expected: NameError on `eval_f1`.

- [ ] **Step 3: 구현 추가**

`web_app/multibagger.py` 끝에:

```python
def _missing(*vals) -> bool:
    return any(v is None for v in vals)


def eval_f1(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.market_cap):
        return None
    return t["F1_MCAP_MIN"] <= f.market_cap <= t["F1_MCAP_MAX"]


def eval_f2(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.ebitda, f.fcf):
        return None
    return f.ebitda > 0 and f.fcf > 0


def eval_f8(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.from_52w_high, f.return_1m):
        return None
    band_ok = t["F8_FROM_52W_HIGH_MIN"] <= f.from_52w_high <= t["F8_FROM_52W_HIGH_MAX"]
    momentum_ok = f.return_1m <= t["F8_1M_RETURN_MAX"]
    return band_ok and momentum_ok
```

- [ ] **Step 4: 테스트 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_gates.py -v`
Expected: all passed.

- [ ] **Step 5: 커밋**

```bash
git add -u
git commit -m "feat(multibagger): F1/F2/F8 required gates"
```

---

## Task 3: F3·F4·F5·F6 (퀄리티 게이트)

**Files:**
- Modify: `web_app/multibagger.py`
- Modify: `web_app/tests/test_multibagger_gates.py`

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_f3_roic_absolute():
    assert mb.eval_f3(mb.Fundamentals(roic=0.15), mb.DEFAULTS) is True
    assert mb.eval_f3(mb.Fundamentals(roic=0.05), mb.DEFAULTS) is False

def test_f3_roic_improving():
    f = mb.Fundamentals(roic=0.08, roic_prev=0.05)
    assert mb.eval_f3(f, mb.DEFAULTS) is True  # 절대 미달이지만 개선

def test_f3_missing():
    assert mb.eval_f3(mb.Fundamentals(), mb.DEFAULTS) is None

def test_f4_valuation_either():
    assert mb.eval_f4(mb.Fundamentals(fcf_yield=0.08, pb=5.0), mb.DEFAULTS) is True  # FCF만
    assert mb.eval_f4(mb.Fundamentals(fcf_yield=0.02, pb=2.0), mb.DEFAULTS) is True  # PB만
    assert mb.eval_f4(mb.Fundamentals(fcf_yield=0.02, pb=5.0), mb.DEFAULTS) is False
    assert mb.eval_f4(mb.Fundamentals(fcf_yield=None, pb=None), mb.DEFAULTS) is None

def test_f5_growth_quality():
    ok = mb.Fundamentals(revenue_yoy=0.10, ebitda_yoy=0.15)
    assert mb.eval_f5(ok, mb.DEFAULTS) is True
    slow = mb.Fundamentals(revenue_yoy=0.03, ebitda_yoy=0.10)
    assert mb.eval_f5(slow, mb.DEFAULTS) is False  # rev<5%
    margin_drop = mb.Fundamentals(revenue_yoy=0.10, ebitda_yoy=0.05)
    assert mb.eval_f5(margin_drop, mb.DEFAULTS) is False

def test_f6_capital_allocation():
    ok = mb.Fundamentals(ebitda_yoy=0.20, assets_yoy=0.10)
    assert mb.eval_f6(ok, mb.DEFAULTS) is True
    waste = mb.Fundamentals(ebitda_yoy=0.05, assets_yoy=0.20)
    assert mb.eval_f6(waste, mb.DEFAULTS) is False
```

- [ ] **Step 2: 테스트 실행 → 실패**

Run: `python -m pytest web_app/tests/test_multibagger_gates.py -v`
Expected: NameError.

- [ ] **Step 3: 구현**

`web_app/multibagger.py` 끝에:

```python
def eval_f3(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.roic):
        return None
    if f.roic >= t["F3_ROIC_MIN"]:
        return True
    if f.roic_prev is not None and f.roic > f.roic_prev:
        return True
    return False


def eval_f4(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.fcf_yield) and _missing(f.pb):
        return None
    fcf_ok = f.fcf_yield is not None and f.fcf_yield >= t["F4_FCF_YIELD_MIN"]
    pb_ok = f.pb is not None and f.pb <= t["F4_PB_MAX"]
    return fcf_ok or pb_ok


def eval_f5(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.revenue_yoy, f.ebitda_yoy):
        return None
    return f.revenue_yoy >= t["F5_REVENUE_YOY_MIN"] and f.ebitda_yoy >= f.revenue_yoy


def eval_f6(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.ebitda_yoy, f.assets_yoy):
        return None
    return f.ebitda_yoy >= f.assets_yoy
```

- [ ] **Step 4: 테스트 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_gates.py -v`

- [ ] **Step 5: 커밋**

```bash
git add -u
git commit -m "feat(multibagger): F3/F4/F5/F6 quality gates"
```

---

## Task 4: F7 (금리 연동 강화)

**Files:**
- Modify: `web_app/multibagger.py`
- Modify: `web_app/tests/test_multibagger_gates.py`

- [ ] **Step 1: 실패 테스트**

```python
def test_f7_normal_rates():
    f = mb.Fundamentals(icr=5.0, debt_ebitda=2.0, dgs10_pct=3.0)
    assert mb.eval_f7(f, mb.DEFAULTS) is True

def test_f7_normal_icr_fail():
    f = mb.Fundamentals(icr=2.0, debt_ebitda=2.0, dgs10_pct=3.0)
    assert mb.eval_f7(f, mb.DEFAULTS) is False

def test_f7_hirate_strengthens():
    # 금리 4.5% → ICR≥4.0 D/E≤2.5 로 강화
    borderline = mb.Fundamentals(icr=3.5, debt_ebitda=2.7, dgs10_pct=4.5)
    assert mb.eval_f7(borderline, mb.DEFAULTS) is False  # 평시엔 통과지만 고금리에선 탈락

    strong = mb.Fundamentals(icr=4.5, debt_ebitda=2.0, dgs10_pct=4.5)
    assert mb.eval_f7(strong, mb.DEFAULTS) is True

def test_f7_dgs10_missing_uses_normal():
    f = mb.Fundamentals(icr=3.5, debt_ebitda=2.7, dgs10_pct=None)
    assert mb.eval_f7(f, mb.DEFAULTS) is True  # 평시 임계로 평가

def test_f7_inputs_missing():
    assert mb.eval_f7(mb.Fundamentals(dgs10_pct=3.0), mb.DEFAULTS) is None
```

- [ ] **Step 2: 실행 → 실패**

- [ ] **Step 3: 구현**

```python
def eval_f7(f: Fundamentals, t: dict) -> Optional[bool]:
    if _missing(f.icr, f.debt_ebitda):
        return None
    hirate = f.dgs10_pct is not None and f.dgs10_pct >= t["F7_HIRATE_DGS10_PCT"]
    icr_min = t["F7_ICR_MIN_HIRATE"] if hirate else t["F7_ICR_MIN"]
    de_max = t["F7_DEBT_EBITDA_MAX_HIRATE"] if hirate else t["F7_DEBT_EBITDA_MAX"]
    return f.icr >= icr_min and f.debt_ebitda <= de_max
```

- [ ] **Step 4: 실행 → 통과**

- [ ] **Step 5: 커밋**

```bash
git add -u
git commit -m "feat(multibagger): F7 high-rate adaptive gate"
```

---

## Task 5: 게이트 일괄 평가 + 분류 (PASS/WATCH/MISS)

**Files:**
- Modify: `web_app/multibagger.py`
- Create: `web_app/tests/test_multibagger_classify.py`

- [ ] **Step 1: 실패 테스트**

`web_app/tests/test_multibagger_classify.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger as mb


def _passing_fundamentals():
    return mb.Fundamentals(
        market_cap=1_000_000_000,
        ebitda=1e8, fcf=5e7,
        roic=0.15, fcf_yield=0.08, pb=2.0,
        revenue_yoy=0.10, ebitda_yoy=0.15, assets_yoy=0.08,
        icr=5.0, debt_ebitda=2.0,
        from_52w_high=-0.20, return_1m=0.10,
        dgs10_pct=3.5,
    )


def test_classify_pass():
    res = mb.classify(_passing_fundamentals(), mb.DEFAULTS)
    assert res.layer == "PASS"
    assert res.gates_passed == set(mb.ALL_GATES)
    assert res.gates_failed == set()


def test_classify_watch_one_optional_fail():
    f = _passing_fundamentals()
    f.roic = 0.05  # F3 fail
    f.roic_prev = 0.05
    res = mb.classify(f, mb.DEFAULTS)
    assert res.layer == "WATCH"
    assert "F3" in res.gates_failed


def test_classify_miss_required_fail():
    f = _passing_fundamentals()
    f.market_cap = 5_000_000_000  # F1 fail
    res = mb.classify(f, mb.DEFAULTS)
    assert res.layer == "MISS"


def test_classify_miss_too_many_optional_fail():
    f = _passing_fundamentals()
    f.roic = 0.05; f.roic_prev = 0.05  # F3
    f.fcf_yield = 0.02; f.pb = 5.0     # F4
    f.icr = 2.0                         # F7
    res = mb.classify(f, mb.DEFAULTS)
    assert res.layer == "MISS"  # 3개 부족


def test_classify_excludes_when_3_missing_optional():
    f = mb.Fundamentals(
        market_cap=1e9, ebitda=1e8, fcf=5e7,
        from_52w_high=-0.20, return_1m=0.10,
        # F3·F4·F5·F6 입력 결측 → 4개 N/A
    )
    res = mb.classify(f, mb.DEFAULTS)
    assert res.layer == "EXCLUDED"
```

- [ ] **Step 2: 실행 → 실패**

- [ ] **Step 3: 구현**

`web_app/multibagger.py` 끝에:

```python
GATE_EVALUATORS = {
    "F1": eval_f1, "F2": eval_f2, "F3": eval_f3, "F4": eval_f4,
    "F5": eval_f5, "F6": eval_f6, "F7": eval_f7, "F8": eval_f8,
}


@dataclass
class GateResult:
    layer: str  # "PASS" | "WATCH" | "MISS" | "EXCLUDED"
    gates_passed: set = field(default_factory=set)
    gates_failed: set = field(default_factory=set)
    gates_missing: set = field(default_factory=set)


def evaluate_all_gates(f: Fundamentals, t: dict) -> dict:
    return {g: GATE_EVALUATORS[g](f, t) for g in ALL_GATES}


def classify(f: Fundamentals, t: dict) -> GateResult:
    res = GateResult(layer="MISS")
    by_gate = evaluate_all_gates(f, t)
    for g, v in by_gate.items():
        if v is True:
            res.gates_passed.add(g)
        elif v is False:
            res.gates_failed.add(g)
        else:
            res.gates_missing.add(g)

    # 결측 3개+ → 제외
    missing_optional = res.gates_missing & set(CORE_GATES_OPTIONAL)
    if len(missing_optional) >= 3:
        res.layer = "EXCLUDED"
        return res

    # 필수 게이트 미통과 (실패 또는 결측) → MISS
    for g in CORE_GATES_REQUIRED:
        if g not in res.gates_passed:
            res.layer = "MISS"
            return res

    # 옵셔널 부족(실패+결측) 개수
    optional_short = (res.gates_failed | res.gates_missing) & set(CORE_GATES_OPTIONAL)
    if len(optional_short) == 0:
        res.layer = "PASS"
    elif len(optional_short) <= 2:
        res.layer = "WATCH"
    else:
        res.layer = "MISS"
    return res
```

- [ ] **Step 4: 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_classify.py -v`

- [ ] **Step 5: 커밋**

```bash
git add -u
git commit -m "feat(multibagger): classify PASS/WATCH/MISS/EXCLUDED"
```

---

## Task 6: 점수화 (Q1~Q6 + Bonus + tie-break)

**Files:**
- Modify: `web_app/multibagger.py`
- Create: `web_app/tests/test_multibagger_scoring.py`

- [ ] **Step 1: 실패 테스트**

`web_app/tests/test_multibagger_scoring.py`:

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger as mb


def test_q1_roic_normalization():
    assert mb.score_q1(mb.Fundamentals(roic=0.30)) == 100
    assert mb.score_q1(mb.Fundamentals(roic=0.10)) == 0
    assert mb.score_q1(mb.Fundamentals(roic=0.20)) == 50
    assert mb.score_q1(mb.Fundamentals(roic=0.05)) == 0  # clamp
    assert mb.score_q1(mb.Fundamentals(roic=None)) is None


def test_q2_max_of_fcf_or_bm():
    # FCF 강함
    s1 = mb.score_q2(mb.Fundamentals(fcf_yield=0.15, pb=10.0))
    assert s1 == 100
    # PB 강함 (B/M = 1.0)
    s2 = mb.score_q2(mb.Fundamentals(fcf_yield=0.02, pb=1.0))
    assert s2 == 100
    # 둘다 결측
    assert mb.score_q2(mb.Fundamentals()) is None


def test_q3_q4_growth_normalization():
    # Q3: EBITDA YoY − Revenue YoY
    s = mb.score_q3(mb.Fundamentals(ebitda_yoy=0.20, revenue_yoy=0.10))  # +10pp
    assert s == 100
    s = mb.score_q3(mb.Fundamentals(ebitda_yoy=0.10, revenue_yoy=0.10))  # 0pp
    assert s == 50

    s = mb.score_q4(mb.Fundamentals(ebitda_yoy=0.20, assets_yoy=0.05))  # +15pp
    assert s == 100


def test_q5_min_of_icr_or_de():
    # ICR 약점
    f = mb.Fundamentals(icr=3.0, debt_ebitda=0.0)
    assert mb.score_q5(f) == 0  # ICR 약점이 binding


def test_q6_revenue_acceleration():
    assert mb.score_q6(mb.Fundamentals(revenue_yoy=0.30)) == 100
    assert mb.score_q6(mb.Fundamentals(revenue_yoy=0.05)) == 0


def test_bonus_sum():
    f = mb.Fundamentals(
        sector="Healthcare",
        insider_net_buy_3m=1.0,
        buyback_yield_ttm=0.02,
        revenue_yoy=0.20, revenue_yoy_prev=0.10,
    )
    assert mb.score_bonus(f) == 35  # all four


def test_compose_score():
    f = mb.Fundamentals(
        roic=0.20, fcf_yield=0.10, pb=2.0,
        ebitda_yoy=0.20, revenue_yoy=0.10, assets_yoy=0.05,
        icr=10.0, debt_ebitda=1.0,
        sector="Healthcare", buyback_yield_ttm=0.01,
    )
    s = mb.compose_score(f)
    assert 0 <= s <= 100


def test_tie_break_prefers_q4():
    a = mb.Fundamentals(ebitda_yoy=0.30, assets_yoy=0.05)  # Q4 강
    b = mb.Fundamentals(ebitda_yoy=0.10, assets_yoy=0.10)  # Q4 약
    assert mb.tie_break_key(a)[0] > mb.tie_break_key(b)[0]
```

- [ ] **Step 2: 실행 → 실패**

- [ ] **Step 3: 구현**

`web_app/multibagger.py` 끝에:

```python
def _clamp01(x: float, lo: float, hi: float) -> float:
    if x <= lo:
        return 0.0
    if x >= hi:
        return 100.0
    return (x - lo) / (hi - lo) * 100.0


def score_q1(f: Fundamentals) -> Optional[float]:
    if f.roic is None:
        return None
    return _clamp01(f.roic, 0.10, 0.30)


def score_q2(f: Fundamentals) -> Optional[float]:
    parts = []
    if f.fcf_yield is not None:
        parts.append(_clamp01(f.fcf_yield, 0.05, 0.15))
    if f.pb is not None and f.pb > 0:
        bm = 1.0 / f.pb
        parts.append(_clamp01(bm, 0.33, 1.0))
    if not parts:
        return None
    return max(parts)


def score_q3(f: Fundamentals) -> Optional[float]:
    if f.ebitda_yoy is None or f.revenue_yoy is None:
        return None
    diff = f.ebitda_yoy - f.revenue_yoy
    return _clamp01(diff, -0.05, 0.10) * 0.5 + 50 - _clamp01(diff, -0.05, 0.0) * 0.5 \
        if diff < 0 else _clamp01(diff, 0.0, 0.10) * 0.5 + 50


def score_q4(f: Fundamentals) -> Optional[float]:
    if f.ebitda_yoy is None or f.assets_yoy is None:
        return None
    diff = f.ebitda_yoy - f.assets_yoy
    if diff < 0:
        return _clamp01(diff, -0.10, 0.0) * 0.5
    return 50 + _clamp01(diff, 0.0, 0.15) * 0.5


def score_q5(f: Fundamentals) -> Optional[float]:
    parts = []
    if f.icr is not None:
        parts.append(_clamp01(f.icr, 3.0, 10.0))
    if f.debt_ebitda is not None:
        parts.append(_clamp01(-f.debt_ebitda, -3.0, 0.0))
    if not parts:
        return None
    return min(parts)


def score_q6(f: Fundamentals) -> Optional[float]:
    if f.revenue_yoy is None:
        return None
    return _clamp01(f.revenue_yoy, 0.05, 0.30)


BAGGER_SECTORS = {"Healthcare", "Technology", "Consumer Discretionary"}


def score_bonus(f: Fundamentals) -> float:
    b = 0.0
    if f.sector and f.sector in BAGGER_SECTORS:
        b += 10
    if f.insider_net_buy_3m is not None and f.insider_net_buy_3m > 0:
        b += 10
    if f.buyback_yield_ttm is not None and f.buyback_yield_ttm > 0:
        b += 5
    if (f.revenue_yoy is not None and f.revenue_yoy_prev is not None
            and f.revenue_yoy > f.revenue_yoy_prev):
        b += 10
    return b


_Q_FUNCS = (score_q1, score_q2, score_q3, score_q4, score_q5, score_q6)


def compose_score(f: Fundamentals) -> float:
    vals = [fn(f) for fn in _Q_FUNCS]
    vals = [v for v in vals if v is not None]
    if not vals:
        core = 0.0
    else:
        core = sum(vals) / len(vals)
    bonus = score_bonus(f)
    return min(100.0, core * 0.7 + bonus * 0.3 / 35 * 100)
    # bonus 0~35 → 0~100 스케일 후 0.3 가중


def tie_break_key(f: Fundamentals) -> tuple:
    """동점 시 비교용. 내림차순 정렬 가정 (큰 게 우선)."""
    return (
        score_q4(f) or 0,
        f.roic or 0,
        score_q2(f) or 0,
        -(f.market_cap or 1e18),  # 작을수록 우선이므로 음수
    )
```

수정 — Q3 식이 복잡함. 단순화:

`score_q3`를 다음으로 교체:

```python
def score_q3(f: Fundamentals) -> Optional[float]:
    if f.ebitda_yoy is None or f.revenue_yoy is None:
        return None
    diff = f.ebitda_yoy - f.revenue_yoy
    if diff >= 0.10:
        return 100.0
    if diff <= -0.05:
        return 0.0
    if diff >= 0:
        return 50 + (diff / 0.10) * 50
    return 50 + (diff / 0.05) * 50  # diff 음수 → 0~50
```

- [ ] **Step 4: 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_scoring.py -v`

- [ ] **Step 5: 커밋**

```bash
git add -u
git commit -m "feat(multibagger): Q1-Q6 scoring + bonus + tie-break"
```

---

## Task 7: FRED 금리 fetcher

**Files:**
- Create: `web_app/multibagger_rates.py`
- Create: `web_app/tests/test_multibagger_rates.py`

- [ ] **Step 1: 실패 테스트**

`web_app/tests/test_multibagger_rates.py`:

```python
import os, sys, time, pickle
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger_rates as mr


def test_parse_csv_last_valid(monkeypatch):
    csv = "DATE,DGS10\n2026-05-22,4.32\n2026-05-23,.\n2026-05-24,4.35\n"
    assert mr._parse_last_valid(csv) == 4.35


def test_parse_csv_handles_all_missing():
    assert mr._parse_last_valid("DATE,DGS10\n2026-05-23,.\n") is None


def test_cache_hit(tmp_path, monkeypatch):
    cache_file = tmp_path / "rates_us.pkl"
    cache_file.write_bytes(pickle.dumps({"_ts": time.time(), "dgs10_pct": 4.2}))
    monkeypatch.setattr(mr, "CACHE_PATH", str(cache_file))
    assert mr.get_dgs10() == 4.2


def test_cache_expired_triggers_fetch(tmp_path, monkeypatch):
    cache_file = tmp_path / "rates_us.pkl"
    cache_file.write_bytes(pickle.dumps({"_ts": time.time() - 48*3600, "dgs10_pct": 3.0}))
    monkeypatch.setattr(mr, "CACHE_PATH", str(cache_file))
    called = {"n": 0}
    def fake_fetch():
        called["n"] += 1
        return 4.5
    monkeypatch.setattr(mr, "_fetch_remote", fake_fetch)
    assert mr.get_dgs10() == 4.5
    assert called["n"] == 1
```

- [ ] **Step 2: 실행 → 실패**

- [ ] **Step 3: 구현**

`web_app/multibagger_rates.py`:

```python
"""FRED DGS10 (10년 국채금리) fetcher + 24h pickle 캐시."""
from __future__ import annotations

import os
import pickle
import time
import urllib.request
from typing import Optional

CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache_v19", "rates_us.pkl")
CACHE_TTL_SEC = 24 * 3600
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"


def _parse_last_valid(csv_text: str) -> Optional[float]:
    last = None
    for line in csv_text.strip().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        v = parts[1].strip()
        if v and v != ".":
            try:
                last = float(v)
            except ValueError:
                continue
    return last


def _fetch_remote() -> Optional[float]:
    try:
        with urllib.request.urlopen(FRED_URL, timeout=5) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        return _parse_last_valid(data)
    except Exception:
        return None


def get_dgs10() -> Optional[float]:
    """캐시 fresh면 반환, 만료면 fetch 후 갱신. 모두 실패 시 last cached 또는 None."""
    cached = None
    try:
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, "rb") as f:
                cached = pickle.load(f)
    except Exception:
        cached = None

    if cached and (time.time() - cached.get("_ts", 0)) < CACHE_TTL_SEC:
        return cached.get("dgs10_pct")

    fresh = _fetch_remote()
    if fresh is None:
        return cached.get("dgs10_pct") if cached else None

    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "wb") as f:
            pickle.dump({"_ts": time.time(), "dgs10_pct": fresh}, f)
    except Exception:
        pass
    return fresh
```

- [ ] **Step 4: 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_rates.py -v`

- [ ] **Step 5: 커밋**

```bash
git add web_app/multibagger_rates.py web_app/tests/test_multibagger_rates.py
git commit -m "feat(multibagger): FRED DGS10 fetcher with 24h cache"
```

---

## Task 8: yfinance 보강 모듈

**Files:**
- Create: `web_app/multibagger_enrich.py`
- Create: `web_app/tests/test_multibagger_enrich.py`

- [ ] **Step 1: 실패 테스트**

`web_app/tests/test_multibagger_enrich.py`:

```python
import os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger_enrich as me
import multibagger as mb


class FakeTicker:
    def __init__(self, info=None, hist=None, income=None, balance=None, cash=None):
        self.info = info or {}
        self.fast_info = {}
        self._hist = hist
        self._income = income
        self._balance = balance
        self._cash = cash

    def history(self, period=None, **_kw):
        return self._hist if self._hist is not None else pd.DataFrame()

    @property
    def income_stmt(self):
        return self._income if self._income is not None else pd.DataFrame()

    @property
    def balance_sheet(self):
        return self._balance if self._balance is not None else pd.DataFrame()

    @property
    def cashflow(self):
        return self._cash if self._cash is not None else pd.DataFrame()

    def get_insider_transactions(self):
        return pd.DataFrame()


def test_extract_yoy_from_income():
    df = pd.DataFrame(
        {"2024-12-31": [200, 100], "2023-12-31": [150, 80]},
        index=["TotalRevenue", "EBITDA"],
    )
    rev_yoy = me._yoy(df, "TotalRevenue")
    ebitda_yoy = me._yoy(df, "EBITDA")
    assert abs(rev_yoy - (200/150 - 1)) < 1e-6
    assert abs(ebitda_yoy - (100/80 - 1)) < 1e-6


def test_extract_yoy_handles_missing_row():
    df = pd.DataFrame({"2024-12-31": [200], "2023-12-31": [150]}, index=["TotalRevenue"])
    assert me._yoy(df, "EBITDA") is None


def test_extract_52w_high_distance():
    hist = pd.DataFrame({"Close": [100, 110, 120, 90, 100]})
    distance, ret_1m = me._price_signals(hist)
    assert abs(distance - (100/120 - 1)) < 1e-6


def test_enrich_one_returns_fundamentals(monkeypatch):
    info = {
        "marketCap": 1e9, "freeCashflow": 5e7, "ebitda": 1e8,
        "priceToBook": 2.0, "sector": "Healthcare",
    }
    income = pd.DataFrame(
        {"2024-12-31": [1000, 200, 50], "2023-12-31": [800, 150, 40]},
        index=["TotalRevenue", "EBITDA", "InterestExpense"],
    )
    balance = pd.DataFrame(
        {"2024-12-31": [2000, 300], "2023-12-31": [1800, 280]},
        index=["TotalAssets", "TotalDebt"],
    )
    cash = pd.DataFrame(
        {"2024-12-31": [50, -100, -20]}, index=["FreeCashFlow", "CapitalExpenditure", "RepurchaseOfCapitalStock"]
    )
    hist = pd.DataFrame({"Close": list(range(80, 130))})
    fake = FakeTicker(info=info, hist=hist, income=income, balance=balance, cash=cash)

    monkeypatch.setattr(me, "_get_ticker", lambda sym: fake)
    f = me.enrich_one("FOO", dgs10_pct=4.5)
    assert isinstance(f, mb.Fundamentals)
    assert f.market_cap == 1e9
    assert f.ebitda == 1e8
    assert f.dgs10_pct == 4.5
    assert f.revenue_yoy is not None and f.revenue_yoy > 0


def test_enrich_one_exception_returns_none(monkeypatch):
    def boom(_):
        raise RuntimeError("yf down")
    monkeypatch.setattr(me, "_get_ticker", boom)
    assert me.enrich_one("FOO", dgs10_pct=None) is None
```

- [ ] **Step 2: 실행 → 실패**

- [ ] **Step 3: 구현**

`web_app/multibagger_enrich.py`:

```python
"""yfinance 보강 — Ticker → Fundamentals 변환."""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

import multibagger as mb


def _get_ticker(symbol: str):
    """test에서 monkeypatch로 대체."""
    import yfinance as yf
    return yf.Ticker(symbol)


def _yoy(df: pd.DataFrame, row: str) -> Optional[float]:
    if df is None or df.empty or row not in df.index:
        return None
    try:
        series = df.loc[row].dropna()
        if len(series) < 2:
            return None
        cols = list(df.columns)
        latest_val = df.loc[row, cols[0]]
        prev_val = df.loc[row, cols[1]]
        if prev_val is None or pd.isna(prev_val) or prev_val == 0:
            return None
        return float(latest_val) / float(prev_val) - 1.0
    except Exception:
        return None


def _latest_val(df: pd.DataFrame, row: str) -> Optional[float]:
    if df is None or df.empty or row not in df.index:
        return None
    try:
        v = df.loc[row].dropna()
        return float(v.iloc[0]) if len(v) else None
    except Exception:
        return None


def _price_signals(hist: pd.DataFrame) -> tuple[Optional[float], Optional[float]]:
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None, None
    closes = hist["Close"].dropna()
    if len(closes) < 21:
        return None, None
    last = float(closes.iloc[-1])
    high_52w = float(closes.max())
    distance = last / high_52w - 1.0 if high_52w > 0 else None
    one_month_idx = max(0, len(closes) - 21)
    return distance, last / float(closes.iloc[one_month_idx]) - 1.0


def _roic(info: dict, income: pd.DataFrame, balance: pd.DataFrame) -> Optional[float]:
    try:
        ebit = _latest_val(income, "EBIT") or _latest_val(income, "OperatingIncome")
        debt = _latest_val(balance, "TotalDebt") or 0.0
        equity = _latest_val(balance, "StockholdersEquity") or _latest_val(balance, "TotalEquityGrossMinorityInterest")
        if not ebit or not equity:
            return None
        invested = float(equity) + float(debt)
        if invested <= 0:
            return None
        tax_rate = 0.21
        return float(ebit) * (1 - tax_rate) / invested
    except Exception:
        return None


def _insider_net_3m(t) -> Optional[float]:
    try:
        df = t.get_insider_transactions()
        if df is None or df.empty:
            return 0.0
        if "Value" in df.columns and "Transaction" in df.columns:
            # 90일 필터는 yfinance 응답에 날짜가 있을 때만
            return float(df["Value"].fillna(0).sum())
        return 0.0
    except Exception:
        return None


def enrich_one(symbol: str, dgs10_pct: Optional[float]) -> Optional[mb.Fundamentals]:
    try:
        t = _get_ticker(symbol)
        info = getattr(t, "info", {}) or {}
        income = t.income_stmt if hasattr(t, "income_stmt") else pd.DataFrame()
        balance = t.balance_sheet if hasattr(t, "balance_sheet") else pd.DataFrame()
        cash = t.cashflow if hasattr(t, "cashflow") else pd.DataFrame()
        hist = t.history(period="1y")

        mcap = info.get("marketCap")
        fcf = info.get("freeCashflow") or _latest_val(cash, "FreeCashFlow")
        ebitda = info.get("ebitda") or _latest_val(income, "EBITDA")
        pb = info.get("priceToBook")
        sector = info.get("sector")

        rev_yoy = _yoy(income, "TotalRevenue")
        ebitda_yoy = _yoy(income, "EBITDA")
        fcf_yoy = _yoy(cash, "FreeCashFlow")
        assets_yoy = _yoy(balance, "TotalAssets")
        capex_yoy = _yoy(cash, "CapitalExpenditure")

        interest = _latest_val(income, "InterestExpense")
        icr = (ebitda / abs(interest)) if (ebitda and interest) else None
        debt = _latest_val(balance, "TotalDebt")
        debt_ebitda = (debt / ebitda) if (debt and ebitda and ebitda > 0) else None

        fcf_yield = (fcf / mcap) if (fcf and mcap) else None
        roic = _roic(info, income, balance)
        distance, ret_1m = _price_signals(hist)

        buyback_raw = _latest_val(cash, "RepurchaseOfCapitalStock")
        buyback_yield = (abs(buyback_raw) / mcap) if (buyback_raw and mcap) else None

        return mb.Fundamentals(
            market_cap=mcap, ebitda=ebitda, fcf=fcf,
            roic=roic, fcf_yield=fcf_yield, pb=pb,
            revenue_yoy=rev_yoy, ebitda_yoy=ebitda_yoy, fcf_yoy=fcf_yoy,
            assets_yoy=assets_yoy, capex_yoy=capex_yoy,
            icr=icr, debt_ebitda=debt_ebitda,
            from_52w_high=distance, return_1m=ret_1m,
            sector=sector,
            insider_net_buy_3m=_insider_net_3m(t),
            buyback_yield_ttm=buyback_yield,
            dgs10_pct=dgs10_pct,
        )
    except Exception as e:
        logging.warning("multibagger enrich failed for %s: %s", symbol, e)
        return None
```

- [ ] **Step 4: 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_enrich.py -v`

- [ ] **Step 5: 커밋**

```bash
git add web_app/multibagger_enrich.py web_app/tests/test_multibagger_enrich.py
git commit -m "feat(multibagger): yfinance enrichment with mock-friendly seam"
```

---

## Task 9: 오케스트레이터 (베이스 캐시 → 후보 풀 → 결과 빌드)

**Files:**
- Modify: `web_app/multibagger.py` (append `build_results`)
- Modify: `web_app/tests/test_multibagger_classify.py`

- [ ] **Step 1: 실패 테스트**

`web_app/tests/test_multibagger_classify.py`에 추가:

```python
def test_build_results_pre_filters_by_size_and_profit(monkeypatch):
    base = [
        {"Ticker": "SMALL", "market_cap": 1e9, "ebitda": 1e8, "fcf": 5e7},
        {"Ticker": "BIG", "market_cap": 1e11, "ebitda": 1e10, "fcf": 1e9},
        {"Ticker": "LOSS", "market_cap": 1e9, "ebitda": -1e8, "fcf": -5e7},
    ]
    # 보강은 결정적 stub
    def fake_enrich(sym, dgs10_pct):
        return mb.Fundamentals(
            market_cap=1e9, ebitda=1e8, fcf=5e7,
            roic=0.15, fcf_yield=0.08, pb=2.0,
            revenue_yoy=0.10, ebitda_yoy=0.15, assets_yoy=0.08,
            icr=5.0, debt_ebitda=2.0,
            from_52w_high=-0.20, return_1m=0.10,
            dgs10_pct=3.5, sector="Healthcare",
        )
    res = mb.build_results(base, dgs10_pct=3.5, enrich_fn=fake_enrich, max_workers=2)
    tickers = {r["ticker"] for r in res["pass"] + res["watch"]}
    assert "SMALL" in tickers
    assert "BIG" not in tickers
    assert "LOSS" not in tickers


def test_build_results_sorts_pass_by_score():
    # 두 종목 점수 차이 검증
    def enrich_factory(roic):
        def _e(sym, dgs10_pct):
            return mb.Fundamentals(
                market_cap=1e9, ebitda=1e8, fcf=5e7,
                roic=roic, fcf_yield=0.08, pb=2.0,
                revenue_yoy=0.10, ebitda_yoy=0.15, assets_yoy=0.08,
                icr=5.0, debt_ebitda=2.0,
                from_52w_high=-0.20, return_1m=0.10,
                dgs10_pct=3.5, sector="Healthcare",
            )
        return _e

    base_a = [{"Ticker": "A", "market_cap": 1e9, "ebitda": 1e8, "fcf": 5e7}]
    base_b = [{"Ticker": "B", "market_cap": 1e9, "ebitda": 1e8, "fcf": 5e7}]
    res_a = mb.build_results(base_a, 3.5, enrich_factory(0.25), max_workers=1)
    res_b = mb.build_results(base_b, 3.5, enrich_factory(0.12), max_workers=1)
    assert res_a["pass"][0]["score"] > res_b["pass"][0]["score"]
```

- [ ] **Step 2: 실행 → 실패**

- [ ] **Step 3: 구현**

`web_app/multibagger.py` 끝에:

```python
def _pre_filter_F1_F2(base: list, t: dict) -> list:
    out = []
    for row in base:
        mc = row.get("market_cap") or row.get("MarketCap") or row.get("marketCap")
        eb = row.get("ebitda") or row.get("EBITDA")
        fc = row.get("fcf") or row.get("FCF") or row.get("freeCashflow")
        if mc is None or eb is None or fc is None:
            continue
        if not (t["F1_MCAP_MIN"] <= mc <= t["F1_MCAP_MAX"]):
            continue
        if eb <= 0 or fc <= 0:
            continue
        ticker = row.get("Ticker") or row.get("ticker") or row.get("symbol")
        if not ticker:
            continue
        out.append(ticker)
    return out


def _row_summary(ticker: str, f: Fundamentals, cls: GateResult, score: float) -> dict:
    return {
        "ticker": ticker,
        "score": round(score, 1),
        "market_cap": f.market_cap,
        "roic": f.roic,
        "fcf_yield": f.fcf_yield,
        "pb": f.pb,
        "ebitda_yoy": f.ebitda_yoy,
        "revenue_yoy": f.revenue_yoy,
        "assets_yoy": f.assets_yoy,
        "from_52w_high": f.from_52w_high,
        "sector": f.sector,
        "layer": cls.layer,
        "gates_passed": sorted(cls.gates_passed),
        "gates_failed": sorted(cls.gates_failed),
        "gates_missing": sorted(cls.gates_missing),
    }


def build_results(base_rows: list, dgs10_pct: Optional[float],
                  enrich_fn, max_workers: int = 8,
                  thresholds: Optional[dict] = None) -> dict:
    """베이스 스캔 결과 → PASS/WATCH 분류 + 점수 랭킹."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    t = thresholds or DEFAULTS
    candidates = _pre_filter_F1_F2(base_rows, t)

    pass_rows, watch_rows = [], []
    enrich_failed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(enrich_fn, sym, dgs10_pct): sym for sym in candidates}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                f = fut.result()
            except Exception:
                enrich_failed += 1
                continue
            if f is None:
                enrich_failed += 1
                continue
            cls = classify(f, t)
            if cls.layer not in ("PASS", "WATCH"):
                continue
            score = compose_score(f)
            row = _row_summary(sym, f, cls, score)
            (pass_rows if cls.layer == "PASS" else watch_rows).append(row)

    def _sort_key(r):
        return (-r["score"],) + tuple(-x for x in tie_break_key(_blank_for_sort(r)))

    def _blank_for_sort(r):
        return Fundamentals(
            ebitda_yoy=r.get("ebitda_yoy"), assets_yoy=r.get("assets_yoy"),
            roic=r.get("roic"), fcf_yield=r.get("fcf_yield"), pb=r.get("pb"),
            market_cap=r.get("market_cap"),
        )

    pass_rows.sort(key=_sort_key)
    watch_rows.sort(key=lambda r: (len(r["gates_failed"]) + len(r["gates_missing"]), -r["score"]))

    return {
        "pass": pass_rows,
        "watch": watch_rows,
        "meta": {
            "universe_n": len(base_rows),
            "candidates_n": len(candidates),
            "pass_n": len(pass_rows),
            "watch_n": len(watch_rows),
            "enrich_failed_n": enrich_failed,
            "dgs10_pct": dgs10_pct,
        },
    }
```

- [ ] **Step 4: 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_classify.py -v`

- [ ] **Step 5: 커밋**

```bash
git add -u
git commit -m "feat(multibagger): orchestrator build_results with pre-filter + parallel enrich"
```

---

## Task 10: Flask 라우트 추가 (/multibagger, /api/multibagger, /thresholds, /ticker)

**Files:**
- Modify: `web_app/app.py` (라우트 추가 + 워커 wiring 자리만)
- Create: `web_app/templates/multibagger.html` (최소 마크업)
- Create: `web_app/tests/test_multibagger_api.py`

- [ ] **Step 1: 실패 테스트**

`web_app/tests/test_multibagger_api.py`:

```python
import os, sys, json, pickle, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as flask_app


def test_multibagger_page_renders():
    client = flask_app.app.test_client()
    resp = client.get("/multibagger")
    assert resp.status_code == 200
    assert b"multibagger" in resp.data.lower() or b"\xeb\xa9\x80\xed\x8b\xb0" in resp.data  # "멀티" UTF-8


def test_api_multibagger_returns_warming_when_no_cache(monkeypatch):
    # 캐시 비어있을 때
    monkeypatch.setattr(flask_app, "_multibagger_results_cache", {})
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"].get("warming") is True or resp.headers.get("X-Warming-In-Progress") == "true"


def test_api_thresholds():
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger/thresholds")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "F1_MCAP_MIN" in body


def test_api_ticker_returns_404_when_unknown(monkeypatch):
    monkeypatch.setattr(flask_app, "_multibagger_results_cache", {"data": {"pass": [], "watch": []}, "_ts": time.time()})
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger/ticker/UNKNOWNSYM")
    assert resp.status_code == 404
```

- [ ] **Step 2: 실행 → 실패**

- [ ] **Step 3: 템플릿 최소판 작성**

`web_app/templates/multibagger.html`:

```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>멀티배거 파인더 — (.)(.)분석기</title>
<link rel="stylesheet" href="/static/theme.css?v={{ v_theme_css }}">
<link rel="stylesheet" href="/static/multibagger.css?v={{ v_multibagger_css }}">
</head>
<body>
<div class="multibagger-shell">
  <header class="mb-header">
    <h1>멀티배거 파인더 <span class="mb-sub">US</span></h1>
    <div class="mb-meta" id="mb-meta"></div>
    <button id="mb-advanced-toggle">고급 임계 펴치기</button>
    <section id="mb-advanced" hidden></section>
  </header>
  <nav class="mb-tabs">
    <button data-layer="pass" class="active">PASS</button>
    <button data-layer="watch">WATCH</button>
    <button data-layer="diff">DIFF (회고)</button>
  </nav>
  <main>
    <div class="mb-disclaimer">
      서바이버십 편향 주의: 현재 상장 종목 기준. 워킹페이퍼 영감 기반이며 10배 보장 아님.
    </div>
    <table id="mb-table"></table>
  </main>
</div>
<script src="/static/multibagger.js?v={{ v_multibagger_js }}"></script>
</body>
</html>
```

- [ ] **Step 4: `web_app/app.py` 수정 — 라우트 추가**

`web_app/app.py` 적절한 위치(다른 `@app.route` 옆)에 추가:

```python
# ── 멀티배거 파인더 ─────────────────────────────────────────
_multibagger_results_cache = {}  # {"_ts": ..., "data": {"pass":[], "watch":[], "meta":{}}}
_multibagger_build_lock = threading.Lock()
_MULTIBAGGER_TTL_SEC = 12 * 3600


@app.route("/multibagger")
def multibagger_page():
    static_dir = os.path.join(app.root_path, "static")
    def _v(name):
        p = os.path.join(static_dir, name)
        try:
            return int(os.path.getmtime(p))
        except OSError:
            return 0
    return render_template(
        "multibagger.html",
        v_theme_css=_v("theme.css"),
        v_multibagger_css=_v("multibagger.css"),
        v_multibagger_js=_v("multibagger.js"),
    )


@app.route("/api/multibagger")
def api_multibagger():
    import multibagger as mb
    cached = _multibagger_results_cache
    if cached and (time.time() - cached.get("_ts", 0)) < _MULTIBAGGER_TTL_SEC:
        resp = jsonify(cached["data"])
        resp.headers["X-Warming-In-Progress"] = "false"
        return resp
    # 캐시 미스/만료
    _maybe_trigger_multibagger_build()
    body = cached.get("data") if cached else {
        "pass": [], "watch": [], "meta": {"warming": True}
    }
    resp = jsonify(body)
    resp.headers["X-Warming-In-Progress"] = "true"
    return resp


@app.route("/api/multibagger/thresholds")
def api_multibagger_thresholds():
    import multibagger as mb
    return jsonify(mb.DEFAULTS)


@app.route("/api/multibagger/ticker/<sym>")
def api_multibagger_ticker(sym):
    cached = _multibagger_results_cache
    if not cached:
        return jsonify({"error": "cache empty"}), 404
    sym_up = sym.upper()
    for row in cached.get("data", {}).get("pass", []) + cached.get("data", {}).get("watch", []):
        if row["ticker"].upper() == sym_up:
            return jsonify(row)
    return jsonify({"error": "not found"}), 404


def _maybe_trigger_multibagger_build():
    if not _multibagger_build_lock.acquire(blocking=False):
        return
    def _worker():
        try:
            _rebuild_multibagger_us()
        finally:
            _multibagger_build_lock.release()
    threading.Thread(target=_worker, daemon=True).start()


def _rebuild_multibagger_us():
    import multibagger as mb
    import multibagger_enrich as me
    import multibagger_rates as mr

    base = None
    with _scan_results_cache_lock:
        cached_base = _scan_results_cache.get(("US", "BALANCED", ""))
        if cached_base:
            base = cached_base.get("data")
    if not base:
        logging.info("multibagger: base US scan cache empty, aborting build")
        return

    dgs10 = mr.get_dgs10()
    result = mb.build_results(base, dgs10_pct=dgs10, enrich_fn=me.enrich_one, max_workers=8)
    _multibagger_results_cache.clear()
    _multibagger_results_cache.update({"_ts": time.time(), "data": result})
    logging.info("multibagger: built %d PASS / %d WATCH (universe %d, candidates %d)",
                 result["meta"]["pass_n"], result["meta"]["watch_n"],
                 result["meta"]["universe_n"], result["meta"]["candidates_n"])
```

- [ ] **Step 5: 빈 정적 파일 생성** (404 방지)

`web_app/static/multibagger.css` (빈):
```css
/* multibagger styles - Task 12에서 채움 */
```

`web_app/static/multibagger.js` (빈):
```javascript
/* multibagger logic - Task 12에서 채움 */
```

- [ ] **Step 6: 테스트 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_api.py -v`

- [ ] **Step 7: 커밋**

```bash
git add web_app/app.py web_app/templates/multibagger.html web_app/static/multibagger.css web_app/static/multibagger.js web_app/tests/test_multibagger_api.py
git commit -m "feat(multibagger): /multibagger page + API routes + cache"
```

---

## Task 11: 백그라운드 워머 루프

**Files:**
- Modify: `web_app/app.py`

- [ ] **Step 1: 워머 루프 추가**

`web_app/app.py`, `_us_warmup_loop` 근처에 추가:

```python
def _multibagger_warmup_loop(interval_sec: int = 3600):
    while True:
        try:
            cached = _multibagger_results_cache
            stale = (not cached) or (time.time() - cached.get("_ts", 0)) >= _MULTIBAGGER_TTL_SEC
            if stale and _multibagger_build_lock.acquire(blocking=False):
                try:
                    _rebuild_multibagger_us()
                finally:
                    _multibagger_build_lock.release()
        except Exception as e:
            logging.warning("multibagger warmup loop error: %s", e)
        time.sleep(interval_sec)


_multibagger_warmup_started = False


def _start_multibagger_warmup_once():
    global _multibagger_warmup_started
    if _multibagger_warmup_started:
        return
    _multibagger_warmup_started = True
    threading.Thread(target=_multibagger_warmup_loop, daemon=True).start()
    logging.info("multibagger warmup loop started")
```

- [ ] **Step 2: 시작 hook에 결합**

기존 `_start_us_warmup_once()` 호출 자리 근처(보통 모듈 로드 끝)에서 추가 호출:

```python
_start_multibagger_warmup_once()
```

(정확한 위치는 `grep -n "_start_us_warmup_once" web_app/app.py` 로 찾아 같은 자리에)

- [ ] **Step 3: 임포트 시 에러 없음 검증**

Run: `python -c "import sys; sys.path.insert(0,'web_app'); import app"`
Expected: 에러 없음 (워머는 daemon thread라 즉시 종료해도 됨).

- [ ] **Step 4: API 테스트 재실행 (회귀 확인)**

Run: `python -m pytest web_app/tests/test_multibagger_api.py -v`

- [ ] **Step 5: 커밋**

```bash
git add -u
git commit -m "feat(multibagger): background warmup loop wired"
```

---

## Task 12: UI — CSS + JS (Layer 탭, 폴링, 고급 패널)

**Files:**
- Modify: `web_app/static/multibagger.css`
- Modify: `web_app/static/multibagger.js`

- [ ] **Step 1: CSS 작성**

`web_app/static/multibagger.css`:

```css
.multibagger-shell {
  max-width: 1280px;
  margin: 0 auto;
  padding: 24px;
  font-family: 'Pretendard', system-ui, sans-serif;
  color: var(--text-primary);
}
.mb-header { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; }
.mb-header h1 { font-size: 24px; font-weight: 800; }
.mb-sub { font-size: 12px; padding: 2px 6px; background: var(--surface-2); border-radius: 4px; margin-left: 6px; }
.mb-meta { font-size: 12px; color: var(--text-secondary); }
.mb-disclaimer { font-size: 12px; background: rgba(245,158,11,0.10); border: 1px solid rgba(245,158,11,0.30); padding: 8px 12px; border-radius: 6px; margin: 12px 0; }
.mb-tabs { display: flex; gap: 4px; margin: 16px 0; }
.mb-tabs button { padding: 8px 16px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; }
.mb-tabs button.active { background: var(--accent); color: white; }
#mb-table { width: 100%; border-collapse: collapse; font-size: 13px; }
#mb-table th, #mb-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: left; }
#mb-table th { background: var(--surface-2); font-weight: 600; }
#mb-table tr:hover { background: rgba(0,0,0,0.03); }
.mb-badge { display: inline-block; padding: 2px 6px; font-size: 11px; border-radius: 3px; background: var(--surface-2); margin-right: 4px; }
.mb-badge.fail { background: rgba(239,68,68,0.15); color: rgb(185,28,28); }
#mb-advanced { width: 100%; margin-top: 8px; padding: 12px; background: var(--surface-2); border-radius: 6px; }
#mb-advanced[hidden] { display: none; }
```

- [ ] **Step 2: JS 작성**

`web_app/static/multibagger.js`:

```javascript
(function () {
  const state = { layer: "pass", data: null, polling: false };

  function fmtPct(v) { return v == null ? "—" : (v * 100).toFixed(1) + "%"; }
  function fmtMcap(v) {
    if (v == null) return "—";
    if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(0) + "M";
    return v.toString();
  }

  function renderTable() {
    const tbl = document.getElementById("mb-table");
    if (!state.data) { tbl.innerHTML = "<tr><td>로딩 중…</td></tr>"; return; }
    const rows = state.data[state.layer] || [];
    if (state.layer === "diff") { tbl.innerHTML = "<tr><td>DIFF 데이터 준비 안 됨(관리자 빌드 필요)</td></tr>"; return; }
    if (!rows.length) { tbl.innerHTML = "<tr><td>해당 레이어에 종목 없음</td></tr>"; return; }
    const isWatch = state.layer === "watch";
    let html = "<thead><tr><th>#</th><th>Ticker</th><th>Score</th><th>시총</th><th>ROIC</th><th>FCF Yld / P/B</th><th>EBITDA YoY−Rev YoY</th><th>52w↓</th><th>섹터</th>" + (isWatch ? "<th>부족</th>" : "") + "</tr></thead><tbody>";
    rows.forEach((r, i) => {
      const valGap = (r.ebitda_yoy != null && r.revenue_yoy != null) ? fmtPct(r.ebitda_yoy - r.revenue_yoy) : "—";
      const valuation = r.fcf_yield != null ? fmtPct(r.fcf_yield) + " / " + (r.pb != null ? r.pb.toFixed(1) : "—") : (r.pb != null ? "PB " + r.pb.toFixed(1) : "—");
      const shortGates = isWatch ? "<td>" + (r.gates_failed.concat(r.gates_missing)).map(g => `<span class="mb-badge fail">${g}</span>`).join("") + "</td>" : "";
      html += `<tr onclick="location.href='/detail/${r.ticker}'" style="cursor:pointer">
        <td>${i+1}</td><td><b>${r.ticker}</b></td><td>${r.score.toFixed(1)}</td>
        <td>${fmtMcap(r.market_cap)}</td><td>${fmtPct(r.roic)}</td><td>${valuation}</td>
        <td>${valGap}</td><td>${fmtPct(r.from_52w_high)}</td><td>${r.sector || "—"}</td>${shortGates}
      </tr>`;
    });
    html += "</tbody>";
    tbl.innerHTML = html;
  }

  function renderMeta() {
    if (!state.data) return;
    const m = state.data.meta || {};
    document.getElementById("mb-meta").textContent =
      `Universe ${m.universe_n||0} · Candidates ${m.candidates_n||0} · PASS ${m.pass_n||0} · WATCH ${m.watch_n||0}` +
      (m.dgs10_pct ? ` · DGS10 ${m.dgs10_pct.toFixed(2)}%` : "");
  }

  async function load() {
    const resp = await fetch("/api/multibagger");
    state.data = await resp.json();
    renderMeta(); renderTable();
    if (resp.headers.get("X-Warming-In-Progress") === "true" && !state.polling) {
      state.polling = true;
      setTimeout(() => { state.polling = false; load(); }, 30000);
    }
  }

  document.querySelectorAll(".mb-tabs button").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mb-tabs button").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.layer = btn.dataset.layer;
      renderTable();
    });
  });

  document.getElementById("mb-advanced-toggle").addEventListener("click", async () => {
    const panel = document.getElementById("mb-advanced");
    if (panel.hasAttribute("hidden")) {
      const resp = await fetch("/api/multibagger/thresholds");
      const th = await resp.json();
      panel.innerHTML = "<pre>" + JSON.stringify(th, null, 2) + "</pre><small>현재 임계값. 튜닝 UI는 추후 기능.</small>";
      panel.removeAttribute("hidden");
    } else {
      panel.setAttribute("hidden", "");
    }
  });

  load();
})();
```

- [ ] **Step 3: 수동 스모크 테스트**

Run (별도 터미널):
```bash
cd web_app && python app.py
```
브라우저 http://localhost:5000/multibagger 열기 → 페이지 렌더, "로딩 중…" 또는 "데이터 보강 중" 표시 확인. 약 1~5분 후 PASS/WATCH 테이블 채워지는지 확인.

- [ ] **Step 4: 커밋**

```bash
git add -u
git commit -m "feat(multibagger): UI — layer tabs, polling, advanced panel"
```

---

## Task 13: DIFF — 5년 베거 명단 배치 스크립트

**Files:**
- Create: `web_app/multibagger_backtest.py`
- Create: `web_app/tests/test_multibagger_backtest.py`

- [ ] **Step 1: 실패 테스트**

`web_app/tests/test_multibagger_backtest.py`:

```python
import os, sys
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multibagger_backtest as mbk


def test_compute_multiple_basic():
    closes = pd.Series([10.0, 12.0, 15.0, 100.0])
    assert mbk._compute_multiple(closes) == 10.0


def test_compute_multiple_too_few_rows():
    closes = pd.Series([100.0] * 50)
    assert mbk._compute_multiple(closes) is None  # <200 거래일


def test_extract_baggers_filters_by_threshold():
    by_symbol = {
        "TENX": pd.DataFrame({"Close": [10.0] + [100.0]*250}),
        "FLAT": pd.DataFrame({"Close": [50.0]*251}),
        "DELISTED": pd.DataFrame({"Close": [10.0]*100}),  # 200 미만
    }
    result = mbk._extract_baggers(by_symbol, multiple=10.0)
    tickers = {b["ticker"] for b in result}
    assert "TENX" in tickers
    assert "FLAT" not in tickers
    assert "DELISTED" not in tickers
```

- [ ] **Step 2: 실행 → 실패**

- [ ] **Step 3: 구현**

`web_app/multibagger_backtest.py`:

```python
"""DIFF — 5년 10배 종목 명단 배치 추출. CLI: python -m multibagger_backtest"""
from __future__ import annotations

import os
import pickle
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import pandas as pd

CACHE_PATH = os.path.join(os.path.dirname(__file__), "cache_v19", "baggers_us.pkl")
MIN_TRADING_DAYS = 200


def _compute_multiple(closes: pd.Series) -> Optional[float]:
    if closes is None or len(closes) < MIN_TRADING_DAYS:
        return None
    start = float(closes.iloc[0])
    end = float(closes.iloc[-1])
    if start <= 0:
        return None
    return end / start


def _extract_baggers(by_symbol: dict, multiple: float) -> list:
    out = []
    for sym, df in by_symbol.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        mult = _compute_multiple(df["Close"])
        if mult is None or mult < multiple:
            continue
        out.append({
            "ticker": sym,
            "start_close": float(df["Close"].iloc[0]),
            "end_close": float(df["Close"].iloc[-1]),
            "multiple": round(mult, 2),
        })
    out.sort(key=lambda r: -r["multiple"])
    return out


def _fetch_history(sym: str, start: str) -> Optional[pd.DataFrame]:
    try:
        import yfinance as yf
        return yf.Ticker(sym).history(start=start, timeout=15)
    except Exception as e:
        logging.warning("history fetch failed %s: %s", sym, e)
        return None


def build_bagger_list_us(start: str = "2021-01-01", multiple: float = 10.0,
                        universe: Optional[list] = None, max_workers: int = 8) -> list:
    if universe is None:
        try:
            with open(os.path.join(os.path.dirname(__file__), "cache_v19", "sectors_us.pkl"), "rb") as f:
                sectors = pickle.load(f)
            universe = sorted({t for ts in sectors.values() for t in ts})
        except Exception:
            universe = []

    by_symbol = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_history, sym, start): sym for sym in universe}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                by_symbol[sym] = fut.result()
            except Exception:
                continue

    baggers = _extract_baggers(by_symbol, multiple)
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "wb") as f:
            import time
            pickle.dump({"_ts": time.time(), "baggers": baggers}, f)
    except Exception as e:
        logging.warning("baggers pkl save failed: %s", e)
    logging.info("baggers extracted: %d", len(baggers))
    return baggers


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_bagger_list_us()
```

- [ ] **Step 4: 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_backtest.py -v`

- [ ] **Step 5: 커밋**

```bash
git add web_app/multibagger_backtest.py web_app/tests/test_multibagger_backtest.py
git commit -m "feat(multibagger): backtest batch — 5y 10x extraction"
```

---

## Task 14: /api/multibagger/diff 라우트

**Files:**
- Modify: `web_app/app.py`
- Modify: `web_app/static/multibagger.js`
- Modify: `web_app/tests/test_multibagger_api.py`

- [ ] **Step 1: 실패 테스트 추가**

`web_app/tests/test_multibagger_api.py`에 추가:

```python
def test_api_diff_missing_pkl_returns_empty(monkeypatch, tmp_path):
    fake_path = str(tmp_path / "no_such.pkl")
    monkeypatch.setattr(flask_app, "_MULTIBAGGER_BAGGERS_PATH", fake_path)
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger/diff")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["baggers"] == []
    assert body["stats"]["available"] is False


def test_api_diff_loads_and_classifies(monkeypatch, tmp_path):
    fake_path = str(tmp_path / "baggers_us.pkl")
    import pickle, time
    pickle.dump({
        "_ts": time.time(),
        "baggers": [
            {"ticker": "TENX", "start_close": 10, "end_close": 100, "multiple": 10.0,
             "snapshot_at_start": {
                 "market_cap": 1e9, "ebitda": 1e8, "fcf": 5e7,
                 "roic": 0.15, "fcf_yield": 0.08, "pb": 2.0,
                 "revenue_yoy": 0.10, "ebitda_yoy": 0.15, "assets_yoy": 0.08,
                 "icr": 5.0, "debt_ebitda": 2.0,
                 "from_52w_high": -0.20, "return_1m": 0.10,
             }},
        ],
    }, open(fake_path, "wb"))
    monkeypatch.setattr(flask_app, "_MULTIBAGGER_BAGGERS_PATH", fake_path)
    client = flask_app.app.test_client()
    resp = client.get("/api/multibagger/diff")
    body = resp.get_json()
    assert body["stats"]["pass_n"] + body["stats"]["watch_n"] + body["stats"]["miss_n"] == 1
```

- [ ] **Step 2: 실행 → 실패**

- [ ] **Step 3: 구현**

`web_app/app.py`에 추가:

```python
_MULTIBAGGER_BAGGERS_PATH = os.path.join(app.root_path, "cache_v19", "baggers_us.pkl")


@app.route("/api/multibagger/diff")
def api_multibagger_diff():
    import multibagger as mb
    path = _MULTIBAGGER_BAGGERS_PATH
    if not os.path.exists(path):
        return jsonify({"baggers": [], "stats": {"available": False}})
    try:
        with open(path, "rb") as f:
            blob = pickle.load(f)
    except Exception:
        return jsonify({"baggers": [], "stats": {"available": False}})

    items = blob.get("baggers", [])
    out = []
    pass_n = watch_n = miss_n = 0
    fail_counter = {}
    for b in items:
        snap = b.get("snapshot_at_start") or {}
        if not snap:
            out.append({**b, "classify": "UNKNOWN", "fail_gates": []})
            continue
        f = mb.Fundamentals(**{k: snap.get(k) for k in mb.Fundamentals.__dataclass_fields__})
        cls = mb.classify(f, mb.DEFAULTS)
        if cls.layer == "PASS": pass_n += 1
        elif cls.layer == "WATCH": watch_n += 1
        else: miss_n += 1
        for g in cls.gates_failed | cls.gates_missing:
            fail_counter[g] = fail_counter.get(g, 0) + 1
        out.append({**b, "classify": cls.layer, "fail_gates": sorted(cls.gates_failed | cls.gates_missing)})

    return jsonify({
        "baggers": out,
        "stats": {
            "available": True,
            "pass_n": pass_n, "watch_n": watch_n, "miss_n": miss_n,
            "top_fail_gates": sorted(fail_counter.items(), key=lambda x: -x[1])[:5],
        },
    })
```

- [ ] **Step 4: JS에 DIFF 탭 렌더 추가**

`web_app/static/multibagger.js`의 `renderTable()` 함수에서 DIFF 분기를:

```javascript
if (state.layer === "diff") {
  if (!state.diffData) {
    fetch("/api/multibagger/diff").then(r => r.json()).then(d => { state.diffData = d; renderTable(); });
    tbl.innerHTML = "<tr><td>DIFF 데이터 로딩 중…</td></tr>";
    return;
  }
  if (!state.diffData.stats.available) {
    tbl.innerHTML = '<tr><td>DIFF 데이터 준비 안 됨. <code>python -m web_app.multibagger_backtest</code> 실행 필요.</td></tr>';
    return;
  }
  const items = state.diffData.baggers;
  let html = "<thead><tr><th>#</th><th>Ticker</th><th>5y Multiple</th><th>분류</th><th>탈락 게이트</th></tr></thead><tbody>";
  items.forEach((r, i) => {
    const failBadges = (r.fail_gates || []).map(g => `<span class="mb-badge fail">${g}</span>`).join("");
    html += `<tr><td>${i+1}</td><td><b>${r.ticker}</b></td><td>${r.multiple}×</td><td>${r.classify}</td><td>${failBadges}</td></tr>`;
  });
  tbl.innerHTML = html + "</tbody>";
  return;
}
```

- [ ] **Step 5: 실행 → 통과**

Run: `python -m pytest web_app/tests/test_multibagger_api.py -v`

- [ ] **Step 6: 커밋**

```bash
git add -u
git commit -m "feat(multibagger): /api/multibagger/diff + DIFF tab rendering"
```

---

## Task 15: detail 페이지에 멀티배거 위젯 임베드

**Files:**
- Modify: `web_app/templates/detail.html`

- [ ] **Step 1: 위젯 마크업 추가**

`web_app/templates/detail.html` 적절한 위치(다른 위젯들 옆)에 추가:

```html
<section id="multibagger-widget" data-ticker="{{ ticker }}">
  <h3>멀티배거 평가</h3>
  <div id="mb-widget-body">로딩 중…</div>
</section>
<script>
(function () {
  const sym = document.getElementById("multibagger-widget").dataset.ticker;
  fetch("/api/multibagger/ticker/" + encodeURIComponent(sym))
    .then(r => r.ok ? r.json() : null)
    .then(d => {
      const body = document.getElementById("mb-widget-body");
      if (!d) { body.innerHTML = "<small>이 종목은 멀티배거 후보 풀에 없음 (시총·수익성 사전필터 탈락)</small>"; return; }
      const gates = ["F1","F2","F3","F4","F5","F6","F7","F8"].map(g => {
        const ok = d.gates_passed.includes(g);
        return `<span class="mb-badge${ok ? '' : ' fail'}">${g} ${ok ? '✓' : '✗'}</span>`;
      }).join(" ");
      body.innerHTML = `<div>레이어: <b>${d.layer}</b> · 점수: <b>${d.score.toFixed(1)}</b></div><div style="margin-top:8px">${gates}</div>`;
    });
})();
</script>
```

- [ ] **Step 2: 수동 검증**

`/multibagger`에서 PASS 종목 하나 클릭 → `/detail/<sym>` 진입 → 위젯이 레이어·점수·게이트 배지 표시.

- [ ] **Step 3: 커밋**

```bash
git add web_app/templates/detail.html
git commit -m "feat(multibagger): embed evaluation widget on detail page"
```

---

## Task 16: 전체 회귀 + 문서화 마감

**Files:**
- Modify: `docs/superpowers/specs/2026-05-25-multibagger-finder-design.md` (Status 갱신)

- [ ] **Step 1: 전체 테스트**

Run: `python -m pytest web_app/tests/ -v`
Expected: all passed.

- [ ] **Step 2: 수동 스모크**

서버 재기동 후:
- `/` (기존 스캐너) 정상
- `/multibagger` 로딩 → 5분 내 PASS/WATCH 채워짐
- 탭 전환 동작
- 고급 패널 펴치기 → 임계 JSON 노출
- DIFF 탭 → "준비 안 됨" 안내 (배치 안 돌렸으면)
- DIFF 배치 실행: `cd web_app && python -m multibagger_backtest`
- 재로드 후 DIFF 탭 → 종목 + 분류 표시
- PASS 종목 클릭 → detail 페이지 위젯 표시

- [ ] **Step 3: Status 갱신**

`docs/superpowers/specs/2026-05-25-multibagger-finder-design.md` 헤더 `Status: Design (pending user review)` → `Status: Implemented`.

- [ ] **Step 4: 커밋**

```bash
git add -u
git commit -m "docs(multibagger): mark spec as implemented"
```

---

## Self-Review (실행 전 체크)

- ✅ Spec coverage: 섹션 1~11 모두 1개 이상 Task에서 다뤄짐
  - 아키텍처(섹션 2) → Task 1, 9, 10, 11
  - 지표 셋(섹션 3) → Task 2~5
  - 점수화(섹션 4) → Task 6
  - DIFF(섹션 5) → Task 13, 14
  - API·캐시(섹션 6) → Task 10, 11, 14
  - UI(섹션 7) → Task 10, 12, 15
  - 에러 처리(섹션 8) → Task 10(warming), 14(diff 결측), 11(루프 예외)
  - 테스트(섹션 9) → 모든 Task에 단위 테스트 포함
  - 비범위(섹션 10) → 정확히 미포함
  - 한계 명시(섹션 11) → Task 10 템플릿 disclaimer
- ✅ Placeholder scan: TBD/TODO 없음, 모든 step에 실제 코드 포함
- ✅ Type consistency: `Fundamentals` 필드/`GateResult`/`DEFAULTS` 키 전 Task 일관 사용
- ✅ Tasks의 함수 시그니처가 후속 Task에서 동일하게 호출됨 (`enrich_one`, `build_results`, `classify`, `compose_score` 등)
