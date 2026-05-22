# 딥테크 스토리 종목 보정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 딥테크 섹터(우주·방산·양자·SMR·수소·자율주행·바이오)의 수주형 적자 기업이 AVOID가 아닌 STORY_STOCK으로 분류되도록 EPS Fail-Safe 면제 및 버킷 라우팅 보정을 추가한다.

**Architecture:** 섹터 화이트리스트 상수와 `_is_deeptech_story()` 게이트 함수를 도입한다. 게이트 통과 시 (1) `quant_nexus_v20.py`의 fail_safe_triggered 계산에서 EPS 원인을 무력화하고, (2) `web_app/one_liner.py:_raw_bucket()`의 score<30 분기에서 AVOID 대신 STORY_STOCK을 반환한다. RS Fail-Safe와 RED grade는 면제 대상 아님.

**Tech Stack:** Python 3, pytest, 기존 코드 컨벤션 유지(타입힌트 선택적, snake_case)

**Spec:** `docs/superpowers/specs/2026-05-22-deeptech-story-correction-design.md`

---

## File Structure

- **Modify** `quant_nexus_v20.py` — 상수 추가, `_is_deeptech_story()` 추가, fail-safe 분기 수정, `_RevenueGrowth` 필드 plumbing
- **Modify** `web_app/one_liner.py` — `_is_deeptech_story()` 동등 함수 추가, `_raw_bucket()` score<30 분기 수정
- **Create** `tests/test_deeptech_story.py` — 게이트 함수와 버킷 라우팅 유닛테스트

`_is_deeptech_story()`를 두 파일에 분리 정의하는 이유: `web_app/one_liner.py`는 scanner row dict만 받고 scanner 클래스에 의존하지 않는 순수 함수 모듈이라 import 경로를 단순화. 두 함수는 동일 로직(상수 공유 위해 one_liner는 `quant_nexus_v20`의 상수 import).

---

## Task 1: 딥테크 섹터 화이트리스트 상수 추가

**Files:**
- Modify: `quant_nexus_v20.py` (상수 영역, `CANSLIM` 딕셔너리 근처 — 약 400줄 부근)

- [ ] **Step 1: 상수 추가**

`quant_nexus_v20.py`에서 `CANSLIM = {...}` 정의 직후(약 line 405 이후 빈 줄)에 추가:

```python
# ════════════════════════════════════════════════════════════
# 딥테크 보정 대상 섹터 (수주·매출 기반 평가가 필요한 미래 산업)
# 이 섹터의 적자 종목은 EPS Fail-Safe를 면제하고 STORY_STOCK으로 라우팅한다.
# 섹터 키는 _SECTORS / 11322·11547 라인의 화면용 라벨과 정확히 일치해야 한다.
# ════════════════════════════════════════════════════════════
_DEEPTECH_SECTORS: set[str] = {
    "드론·우주",
    "위성·발사체",
    "양자컴퓨팅",
    "SMR/원전",
    "수소·연료전지",
    "자율주행",
    "바이오·신약",
}
```

- [ ] **Step 2: 섹터 키 검증**

다음 명령으로 화이트리스트의 모든 키가 실제 섹터 매핑에 존재하는지 확인:

```bash
python -c "
import quant_nexus_v20 as q
src = open('quant_nexus_v20.py', encoding='utf-8').read()
missing = [s for s in q._DEEPTECH_SECTORS if f'\"{s}\"' not in src or src.count(f'\"{s}\"') < 2]
print('MISSING:', missing) if missing else print('OK: all keys present')
"
```

Expected: `OK: all keys present`

키가 누락된 경우 실제 코드의 라벨 표기(가운뎃점 `·` vs `/`)와 정확히 맞춘다. 누락 시 해당 섹터를 화이트리스트에서 제거하거나 코드의 섹터 라벨을 추가한다.

- [ ] **Step 3: Commit**

```bash
git add quant_nexus_v20.py
git commit -m "Add _DEEPTECH_SECTORS whitelist constant"
```

---

## Task 2: `_RevenueGrowth` 필드 plumbing

**Files:**
- Modify: `quant_nexus_v20.py:5937` 부근 (row dict 생성부)

게이트가 매출 YoY를 참조해야 하므로 yfinance `info["revenueGrowth"]`를 row dict에 노출한다. 이미 line 2541 부근에서 읽고 있으므로 외부 호출 없이 추가 가능.

- [ ] **Step 1: row dict에 `_RevenueGrowth` 추가**

`quant_nexus_v20.py`에서 `"_MarketCap": safe_get(info.get("marketCap"), 0),` (line 5937 부근) 다음 줄에 삽입:

```python
                "_RevenueGrowth":   safe_get(info.get("revenueGrowth"), 0.0),
```

(들여쓰기는 인접 라인과 동일하게 맞춘다 — 16 spaces 추정. 실제 라인의 들여쓰기 그대로 복사.)

- [ ] **Step 2: 필드 존재 확인 (수동 스모크)**

scanner를 짧게 돌려 `_RevenueGrowth` 키가 결과 dict에 있는지 확인하거나, 코드 검사로 충분하면 다음:

```bash
python -c "
src = open('quant_nexus_v20.py', encoding='utf-8').read()
assert '_RevenueGrowth' in src, 'field not added'
print('OK')
"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add quant_nexus_v20.py
git commit -m "Plumb revenueGrowth into scanner row as _RevenueGrowth"
```

---

## Task 3: `_is_deeptech_story()` 게이트 함수 (quant_nexus_v20)

**Files:**
- Modify: `quant_nexus_v20.py` (상수 `_DEEPTECH_SECTORS` 정의 직후 모듈 레벨)
- Test: `tests/test_deeptech_story.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_deeptech_story.py` 생성:

```python
"""Tests for deeptech story correction gate."""
import pytest
from quant_nexus_v20 import _is_deeptech_story


def _row(sector="드론·우주", rev_growth=0.10, market_cap=200_000_000_000):
    return {
        "Sector": sector,
        "_RevenueGrowth": rev_growth,
        "_MarketCap": market_cap,
    }


def test_deeptech_story_pass_with_growing_revenue_and_large_cap():
    assert _is_deeptech_story("099320.KQ", _row()) is True


def test_deeptech_story_fail_when_sector_not_in_whitelist():
    assert _is_deeptech_story("005930.KS", _row(sector="반도체")) is False


def test_deeptech_story_fail_when_revenue_not_growing():
    assert _is_deeptech_story("099320.KQ", _row(rev_growth=-0.05)) is False


def test_deeptech_story_fail_when_revenue_flat():
    assert _is_deeptech_story("099320.KQ", _row(rev_growth=0.0)) is False


def test_deeptech_story_fail_when_market_cap_too_small():
    # 1000억 미만 = 작은 좌비주
    assert _is_deeptech_story("X.KQ", _row(market_cap=50_000_000_000)) is False


def test_deeptech_story_fail_on_missing_sector():
    row = _row()
    row["Sector"] = ""
    assert _is_deeptech_story("X", row) is False


def test_deeptech_story_fail_on_missing_revenue_growth():
    row = _row()
    row.pop("_RevenueGrowth", None)
    assert _is_deeptech_story("X", row) is False


def test_deeptech_story_fail_on_missing_market_cap():
    row = _row()
    row.pop("_MarketCap", None)
    assert _is_deeptech_story("X", row) is False
```

- [ ] **Step 2: 테스트 실행, 실패 확인**

```bash
python -m pytest tests/test_deeptech_story.py -v
```

Expected: ImportError 또는 8 tests FAIL with `cannot import name '_is_deeptech_story'`.

- [ ] **Step 3: 함수 구현**

`quant_nexus_v20.py`의 `_DEEPTECH_SECTORS` 정의 직후 모듈 레벨에 추가:

```python
def _is_deeptech_story(ticker: str, row: dict) -> bool:
    """딥테크 보정 대상 여부.

    True 조건 (모두 충족):
      - row["Sector"] ∈ _DEEPTECH_SECTORS
      - row["_RevenueGrowth"] > 0 (매출 YoY 증가)
      - row["_MarketCap"] > 1000억원 (= 1e11)

    데이터 결측 시 보수적으로 False.
    """
    sector = row.get("Sector") or ""
    if sector not in _DEEPTECH_SECTORS:
        return False
    rev_growth = row.get("_RevenueGrowth")
    if rev_growth is None or rev_growth <= 0:
        return False
    mcap = row.get("_MarketCap") or 0
    if mcap <= 1e11:
        return False
    return True
```

- [ ] **Step 4: 테스트 실행, 통과 확인**

```bash
python -m pytest tests/test_deeptech_story.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add quant_nexus_v20.py tests/test_deeptech_story.py
git commit -m "Add _is_deeptech_story gate function with tests"
```

---

## Task 4: EPS Fail-Safe 면제 적용 (quant_nexus_v20)

**Files:**
- Modify: `quant_nexus_v20.py:5180-5207` (fail-safe 계산 블록)

지주사 면제 패턴과 동일한 위치에 deeptech 분기를 추가한다. EPS 원인만 무력화 — RS Fail-Safe는 그대로 적용.

- [ ] **Step 1: fail-safe 분기 수정**

`quant_nexus_v20.py:5182-5185`:

기존:
```python
            fail_safe_triggered = (
                (earn["fail_safe_eps"] and not _is_holdco)
                or rs["fail_safe_rs"]
            )
```

다음으로 교체:
```python
            # 딥테크 수주형 적자 기업도 EPS Fail-Safe 면제 (지주사와 동일 패턴).
            # row dict는 아직 생성 전이므로 게이트가 요구하는 3개 필드를 인라인으로 구성.
            # 섹터 lookup은 line 4132 패턴(self._ticker_sector_map) 재사용.
            _deeptech_row = {
                "Sector": self._ticker_sector_map.get(ticker, "") if hasattr(self, "_ticker_sector_map") else "",
                "_RevenueGrowth": safe_get(info.get("revenueGrowth"), 0.0),
                "_MarketCap": safe_get(info.get("marketCap"), 0),
            }
            _is_deeptech = _is_deeptech_story(ticker, _deeptech_row)
            fail_safe_triggered = (
                (earn["fail_safe_eps"] and not _is_holdco and not _is_deeptech)
                or rs["fail_safe_rs"]
            )
```

- [ ] **Step 2: 스모크 실행 (구문 오류 체크)**

```bash
python -c "import ast; ast.parse(open('quant_nexus_v20.py', encoding='utf-8').read()); print('OK')"
```

Expected: `OK`

- [ ] **Step 3: 기존 테스트 회귀 확인**

```bash
python -m pytest tests/ -v
```

Expected: 모든 테스트 pass (특히 `test_entry_filter`, `test_one_liner_consistency`).

- [ ] **Step 4: Commit**

```bash
git add quant_nexus_v20.py
git commit -m "Exempt deeptech story stocks from EPS Fail-Safe"
```

---

## Task 5: STORY_STOCK 라우팅 (one_liner.py)

**Files:**
- Modify: `web_app/one_liner.py` (모듈 상단 import 영역, 그리고 `:3635-3637`)
- Test: `tests/test_deeptech_story.py` (확장)

- [ ] **Step 1: 추가 실패 테스트 작성**

`tests/test_deeptech_story.py` 끝에 추가:

```python
from web_app.one_liner import _raw_bucket


def _row_for_bucket(score=20, sector="드론·우주", rev_growth=0.10,
                     market_cap=200_000_000_000, signal="", grade=""):
    return {
        "Ticker": "099320.KQ",
        "TotalScore": score,
        "Sector": sector,
        "_RevenueGrowth": rev_growth,
        "_MarketCap": market_cap,
        "Signal": signal,
        "Grade": grade,
        # _raw_bucket이 참조하는 기본 키들 (결측 시 _num에서 0 처리)
        "_PER": 0, "_ROE": -10, "_EPSGrowth": -20,
        "RSI": 50, "Mom12M": 5, "_Mom3M": 0, "Drawdown": -5,
        "_OperatingMargin": -5,
    }


def test_deeptech_story_routes_to_story_stock_when_low_score():
    row = _row_for_bucket(score=20)
    assert _raw_bucket(row) == "STORY_STOCK"


def test_non_deeptech_low_score_still_avoid():
    row = _row_for_bucket(score=20, sector="반도체")
    assert _raw_bucket(row) == "AVOID"


def test_deeptech_with_red_grade_still_avoid():
    row = _row_for_bucket(score=20, grade="RED")
    assert _raw_bucket(row) == "AVOID"


def test_deeptech_with_explicit_avoid_signal_still_avoid():
    row = _row_for_bucket(score=20, signal="AVOID")
    assert _raw_bucket(row) == "AVOID"
```

- [ ] **Step 2: 테스트 실행, 실패 확인**

```bash
python -m pytest tests/test_deeptech_story.py -v
```

Expected: 새 4개 테스트가 FAIL. 첫 번째는 `AVOID != STORY_STOCK`으로 실패해야 함.

- [ ] **Step 3: one_liner.py에 헬퍼와 라우팅 추가**

`web_app/one_liner.py` 모듈 상단(다른 import들 근처)에 추가:

```python
try:
    from quant_nexus_v20 import _DEEPTECH_SECTORS
except Exception:
    _DEEPTECH_SECTORS = {
        "드론·우주", "위성·발사체", "양자컴퓨팅", "SMR/원전",
        "수소·연료전지", "자율주행", "바이오·신약",
    }


def _is_deeptech_story(row: dict) -> bool:
    """딥테크 스토리 종목 여부. quant_nexus_v20 동등 로직."""
    sector = row.get("Sector") or ""
    if sector not in _DEEPTECH_SECTORS:
        return False
    rev_growth = row.get("_RevenueGrowth")
    if rev_growth is None or rev_growth <= 0:
        return False
    mcap = row.get("_MarketCap") or 0
    if mcap <= 1e11:
        return False
    return True
```

`_raw_bucket()` 내부의 `:3636` 분기 수정:

기존:
```python
    if "AVOID" in signal or (score and score < 30):
        return "AVOID"
```

다음으로 교체:
```python
    if "AVOID" in signal or (score and score < 30):
        # RED grade / 명시적 AVOID signal은 면제 없이 AVOID
        grade = (d.get("Grade") or "").upper()
        if "AVOID" in signal or grade == "RED":
            return "AVOID"
        # 딥테크 수주형 종목은 STORY_STOCK으로 라우팅
        if _is_deeptech_story(d):
            return "STORY_STOCK"
        return "AVOID"
```

**참고:** `signal` 변수는 이 분기에 이미 in-scope임 (line 3636에서 사용 중). `score` 또한 동일.

- [ ] **Step 4: 테스트 실행, 통과 확인**

```bash
python -m pytest tests/test_deeptech_story.py -v
```

Expected: 모든 테스트(원래 8개 + 새 4개 = 12) PASS.

- [ ] **Step 5: 회귀 테스트**

```bash
python -m pytest tests/ -v
```

Expected: 전체 테스트 PASS. 특히 `test_one_liner_consistency.py`가 영향받을 수 있으므로 주의.

- [ ] **Step 6: Commit**

```bash
git add web_app/one_liner.py tests/test_deeptech_story.py
git commit -m "Route deeptech story stocks to STORY_STOCK bucket instead of AVOID"
```

---

## Task 6: 통합 스모크 테스트 (쎄트렉아이)

**Files:**
- Test: `tests/test_deeptech_story.py` (확장)

- [ ] **Step 1: 통합 시나리오 테스트 추가**

`tests/test_deeptech_story.py` 끝에 추가:

```python
def test_satrec_initiative_scenario():
    """쎄트렉아이(099320) 시나리오: 적자지만 위성 수주 성장 → STORY_STOCK."""
    row = {
        "Ticker": "099320.KQ",
        "Sector": "위성·발사체",
        "TotalScore": 25,           # 낮은 퀄리티 점수
        "_RevenueGrowth": 0.30,     # 매출 30% 성장
        "_MarketCap": 1_500_000_000_000,  # 1.5조원
        "Signal": "",
        "Grade": "YELLOW",
        "_PER": 0, "_ROE": -8, "_EPSGrowth": -15,
        "RSI": 55, "Mom12M": 40, "_Mom3M": 5, "Drawdown": -10,
        "_OperatingMargin": -3,
    }
    assert _raw_bucket(row) == "STORY_STOCK"


def test_paper_space_themed_smallcap_stays_avoid():
    """이름만 우주테마인 좌비주(매출 정체, 소형주) → 여전히 AVOID."""
    row = {
        "Ticker": "X.KQ",
        "Sector": "드론·우주",
        "TotalScore": 20,
        "_RevenueGrowth": -0.05,    # 매출 감소
        "_MarketCap": 30_000_000_000,  # 300억 (가드레일 미달)
        "Signal": "",
        "Grade": "YELLOW",
        "_PER": 0, "_ROE": -20, "_EPSGrowth": -50,
        "RSI": 30, "Mom12M": -40, "_Mom3M": -15, "Drawdown": -50,
        "_OperatingMargin": -25,
    }
    assert _raw_bucket(row) == "AVOID"


def test_profitable_deeptech_unaffected():
    """흑자 딥테크 종목은 게이트와 무관하게 정상 분류."""
    row = {
        "Ticker": "012450.KS",   # 한화에어로스페이스 예시
        "Sector": "드론·우주",
        "TotalScore": 75,
        "_RevenueGrowth": 0.25,
        "_MarketCap": 10_000_000_000_000,
        "Signal": "",
        "Grade": "GREEN",
        "_PER": 18, "_ROE": 14, "_EPSGrowth": 30,
        "RSI": 65, "Mom12M": 50, "_Mom3M": 8, "Drawdown": -5,
        "_OperatingMargin": 8,
    }
    result = _raw_bucket(row)
    # AVOID/FALLING_KNIFE가 아니어야 함 (score 75는 score<30 분기 안 탐)
    assert result not in ("AVOID", "STORY_STOCK")
```

- [ ] **Step 2: 테스트 실행**

```bash
python -m pytest tests/test_deeptech_story.py -v
```

Expected: 모든 테스트 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_deeptech_story.py
git commit -m "Add integration smoke tests for deeptech story correction"
```

---

## Task 7: 최종 회귀 및 한줄평 일관성 확인

- [ ] **Step 1: 전체 테스트 스위트 실행**

```bash
python -m pytest tests/ -v
```

Expected: 모든 테스트 PASS.

- [ ] **Step 2: 구문/import 무결성 확인**

```bash
python -c "import quant_nexus_v20; from web_app import one_liner; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: (선택) 실제 스캐너 짧은 실행으로 쎄트렉아이 결과 확인**

가능한 환경이면 `python quant_nexus_v20.py` 실행 후 위성·발사체 섹터에서 099320.KQ가 STORY_STOCK 또는 비-AVOID 버킷에 들어가는지 시각 확인.

- [ ] **Step 4: 변경사항이 모두 커밋되었는지 확인**

```bash
git status
git log --oneline -10
```

Expected: working tree clean, 최근 커밋에 deeptech 관련 6개 커밋 포함.

---

## 완료 조건

- 모든 새 테스트 PASS (Task 3, 5, 6에서 추가)
- 기존 테스트 회귀 없음
- 쎄트렉아이(099320.KQ) 시나리오 테스트에서 STORY_STOCK 반환
- 매출 정체 소형 테마주 시나리오 테스트에서 AVOID 유지
- RED grade 종목은 면제 없이 AVOID 유지
