# Finnhub 데이터 노출 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finnhub 무료 tier 데이터를 종목스캐너 UI에 전면 노출한다 — 뉴스 헤드라인 리스트, 회사 로고·프로필, 내부자 심리 MSPR, IPO 캘린더·시장 뉴스.

**Architecture:** 3계층(데이터 `finnhub_api.py` → 라우트 `web_app/app.py` → 렌더 `web_app/static/app.js`). 데이터 계층은 순수 파서 함수로 분리해 네트워크 없이 단위테스트. 라우트는 기존 lazy `/api/sentiment` 페이로드를 확장하고 시장 데이터용 신규 라우트 1개 추가. 프론트는 US 종목에서만(`_FH_Available` 가드) 동적 DOM 주입으로 렌더(두 템플릿 동시 수정 회피). KR은 완전 숨김.

**Tech Stack:** Python 3 / Flask, `finnhub-python`, 바닐라 JS, `unittest`(pytest 폴백).

---

## 배경: 현재 코드 실측 (계획의 전제)

- `finnhub_api.py` — `get_sentiment_data(ticker)`가 6콜(insider/rec/earnings/quote/news/...)을 `_safe()` 래핑으로 호출. 헬퍼 `_safe`/`_cached`/`_store`/`_is_us_ticker`/`_client` 존재. `_CACHE_TTL=3600`.
- `web_app/app.py` — `/api/sentiment/<ticker>` (line ~1586) lazy 라우트가 결과를 `out["_FH_*"]`로 머지, 5분 TTL `_sentiment_cache`. KR은 `kr_not_supported`로 조기 반환(line ~1597).
- `web_app/static/app.js`:
  - `_renderInvestorCard(d)` (line ~3098) — `dp-investor-card`/`dp-investor-grid`에 칩 렌더. **이미** 내부자(FH1)·추천변화(FH3)·실적서프라이즈+스트릭(FH2)·다음실적 D-day(FH5)·뉴스 건수를 렌더함. → 이 칩들은 신규 작업 아님.
  - `_loadSentiment(ticker, market, seq)` (line ~2840) — US만, `/api/sentiment` fetch 후 `Object.assign(_lastDetailData, data)` → `_renderInvestorCard` 재호출.
  - `_FH_Headlines`(제목/url/source/datetime 배열)는 페이로드에 있으나 **리스트 UI 없음** → P1의 실제 작업.
  - `buildSparklineSVG(...)` 헬퍼 존재(직전 세션 추가) → P3 MSPR 추세에 재사용.
- 템플릿 2개(`web_app/templates/scanner.html`, `web_app/templates/detail.html`) 모두 `dp-investor-card`/`dp-ticker` 보유, 같은 `app.js` 공유.
- 테스트: `tests/` 디렉터리, `unittest` + pytest 폴백, 네트워크 호출 없음(`test_dcf_target.py` 패턴).

## 무료 엔드포인트 실측 선행 (각 백엔드 Phase 첫 작업)

P2/P3/P4는 미검증 엔드포인트를 쓴다. UI 작업 전에 `tools/test_finnhub.py`로 실측한다. **403이면 그 Phase 중단·보고.** 단위테스트는 목 응답이라 키 없이도 통과한다.

---

## Phase 1 — 뉴스 헤드라인 리스트 (FH4)

> 백엔드 변경 없음. `_FH_Headlines`는 이미 페이로드에 존재. app.js 렌더만.

### Task 1: 뉴스 리스트 렌더 함수 + 호출 배선

**Files:**
- Modify: `web_app/static/app.js` (`_loadSentiment` ~2840, 신규 함수 추가)

- [ ] **Step 1: `_renderFhNews(d)` 함수 추가**

`_renderInvestorCard` 함수 정의 바로 위(`// ── 투자자 동향 카드 ──` 주석 직전)에 추가. 컨테이너가 없으면 `dp-investor-card` 뒤에 동적 생성 → 두 템플릿 수정 회피.

```javascript
// ── Finnhub 뉴스 헤드라인 리스트 (US, 최근 7일) ──────────────────────────
function _renderFhNews(d) {
  // KR/비가용이면 기존 컨테이너 제거 후 종료
  const existing = document.getElementById('dp-fh-news');
  if (!d || !d._FH_Available || !Array.isArray(d._FH_Headlines) || d._FH_Headlines.length === 0) {
    if (existing) existing.remove();
    return;
  }
  // 컨테이너 확보 (없으면 투자자 카드 뒤에 주입)
  let wrap = existing;
  if (!wrap) {
    const anchor = document.getElementById('dp-investor-card');
    if (!anchor || !anchor.parentNode) return;
    wrap = document.createElement('div');
    wrap.id = 'dp-fh-news';
    wrap.style.cssText = 'padding:8px 24px 12px;';
    anchor.parentNode.insertBefore(wrap, anchor.nextSibling);
  }
  const rows = d._FH_Headlines.slice(0, 5).map(n => {
    const title = esc(n.title || '');
    const src = esc(n.source || '');
    const url = n.url || '';
    const safeUrl = /^https?:\/\//i.test(url) ? url : '';
    const titleHtml = safeUrl
      ? `<a href="${esc(safeUrl)}" target="_blank" rel="noopener noreferrer" style="color:var(--text-primary); text-decoration:none;">${title}</a>`
      : title;
    return `<div style="padding:9px 0; border-bottom:1px solid var(--border);">
      <div style="font-size:13px; line-height:1.45; color:var(--text-primary);">${titleHtml}</div>
      ${src ? `<div style="font-size:10.5px; color:var(--text-tertiary); margin-top:3px;">${src}</div>` : ''}
    </div>`;
  }).join('');
  wrap.innerHTML = `
    <div style="font-size:12px; font-weight:700; color:var(--text-secondary); padding:6px 0 2px; letter-spacing:0.01em;">최근 뉴스 · 7일</div>
    ${rows}`;
}
```

- [ ] **Step 2: `_loadSentiment`에서 호출**

`web_app/static/app.js` line ~2851 — `_renderInvestorCard(_lastDetailData);` 다음 줄에 추가:

```javascript
    _renderInvestorCard(_lastDetailData);
    _renderFhNews(_lastDetailData);   // ← 추가
```

- [ ] **Step 3: 수동 검증 (US)**

앱 실행 후 US 종목(예: NVDA) 상세 패널 열기. 투자자 카드 아래 "최근 뉴스 · 7일" 리스트가 뜨고 헤드라인 클릭 시 새 탭으로 열리는지 확인.
Run: `python -m web_app.app` (또는 프로젝트 표준 실행) → 브라우저에서 NVDA 상세 열기
Expected: 뉴스 5건 리스트 노출, 링크 동작.

- [ ] **Step 4: 수동 검증 (KR 숨김)**

KR 종목(예: 005930) 상세 패널 열기.
Expected: `dp-fh-news` 컨테이너 없음(`_FH_Available` false → 미생성). 투자자 카드엔 외인/기관만.

- [ ] **Step 5: Commit**

```bash
git add web_app/static/app.js
git commit -m "feat(finnhub): 상세 패널 뉴스 헤드라인 리스트 노출 (FH4)"
```

### Task 1b: 다음 실적 D-Day "추정" 라벨 (오인 방지)

> 현재 `_FH_DaysToEarnings`는 마지막 분기 + 120일 cadence **추정**(정확값은 유료). 사용자가 확정 일정으로 오인하지 않도록 라벨 명시. 백엔드 변경 없음.

**Files:**
- Modify: `web_app/static/app.js` (`_renderInvestorCard` ~3147 "다음 실적" 블록)

- [ ] **Step 1: 라벨에 "추정" 표기**

`_renderInvestorCard`의 `// 신규: 다음 실적 D-day` 블록에서 `label: '다음 실적',`을 다음으로 교체:

```javascript
        label: '다음 실적(추정)',
```

그리고 같은 push의 `sub` 줄을 다음으로 교체(추정일임을 명시):

```javascript
        sub: d._FH_NextEarnings ? `~${d._FH_NextEarnings} 추정` : '추정',
```

- [ ] **Step 2: 수동 검증**

US 종목(실적 60일 이내) 상세 → 칩 라벨이 "다음 실적(추정)", 보조 텍스트에 "추정" 포함.

- [ ] **Step 3: Commit**

```bash
git add web_app/static/app.js
git commit -m "fix(finnhub): 다음 실적 D-Day '추정' 라벨 명시 (오인 방지)"
```

---

## Phase 2 — 회사 로고·프로필 (FH6)

> `company_profile2` 신규 배선 → 헤더 로고 + 정확한 IPO일·상장주식수·산업.

### Task 2: 엔드포인트 실측 스모크

**Files:**
- Modify: `tools/test_finnhub.py`

- [ ] **Step 1: 스모크 호출 추가**

`tools/test_finnhub.py` 끝의 `print("\nDone.")` 직전에 추가:

```python
# 5) Company profile2 (무료 여부 실측)
print("\n=== Company Profile2 (AAPL) ===")
try:
    p = fc.company_profile2(symbol="AAPL")
    print(f"  name={p.get('name')} ipo={p.get('ipo')} "
          f"shareOut={p.get('shareOutstanding')} industry={p.get('finnhubIndustry')} "
          f"logo={'Y' if p.get('logo') else 'N'}")
except Exception as e:
    print(f"  Error: {e}")
```

- [ ] **Step 2: 실행해서 무료 여부 확인**

Run: `python tools/test_finnhub.py`
Expected: profile2 줄이 `name=Apple Inc ipo=1980-12-12 ...` 출력(성공). **403/Error면 여기서 중단하고 사용자에게 보고** — Phase 2 진행 불가.

- [ ] **Step 3: Commit**

```bash
git add tools/test_finnhub.py
git commit -m "test(finnhub): company_profile2 스모크 추가"
```

### Task 3: `_parse_profile2` 순수 파서 + 단위테스트

**Files:**
- Modify: `finnhub_api.py`
- Test: `tests/test_finnhub_parsers.py` (신규)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_finnhub_parsers.py` 생성:

```python
"""finnhub_api 순수 파서 단위테스트 — 네트워크 호출 없음."""
from __future__ import annotations

import os
import sys
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from finnhub_api import _parse_profile2  # noqa: E402


class TestParseProfile2(unittest.TestCase):
    def test_full_profile(self) -> None:
        raw = {
            "name": "Apple Inc", "ipo": "1980-12-12",
            "shareOutstanding": 15000.0, "finnhubIndustry": "Technology",
            "exchange": "NASDAQ NMS - GLOBAL MARKET",
            "weburl": "https://www.apple.com/", "logo": "https://x/aapl.png",
        }
        out = _parse_profile2(raw)
        self.assertEqual(out["logo"], "https://x/aapl.png")
        self.assertEqual(out["ipo"], "1980-12-12")
        self.assertEqual(out["share_outstanding"], 15000.0)
        self.assertEqual(out["industry"], "Technology")
        self.assertEqual(out["exchange"], "NASDAQ NMS - GLOBAL MARKET")

    def test_empty_input(self) -> None:
        self.assertEqual(_parse_profile2(None), {})
        self.assertEqual(_parse_profile2({}), {})

    def test_partial_fields_only_present_keys(self) -> None:
        out = _parse_profile2({"name": "X", "logo": "https://x/x.png"})
        self.assertEqual(out["logo"], "https://x/x.png")
        self.assertNotIn("ipo", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_finnhub_parsers.py -v` (또는 `python tests/test_finnhub_parsers.py`)
Expected: FAIL — `ImportError: cannot import name '_parse_profile2'`.

- [ ] **Step 3: `_parse_profile2` 구현**

`finnhub_api.py`의 `get_basic_financials` 함수 정의 위(또는 `_safe` 아래)에 추가:

```python
def _parse_profile2(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """company_profile2 응답 → 정규화 dict. 존재하는 키만 포함."""
    if not raw or not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    if raw.get("logo"):
        out["logo"] = raw["logo"]
    if raw.get("ipo"):
        out["ipo"] = raw["ipo"]
    so = raw.get("shareOutstanding")
    if so is not None:
        out["share_outstanding"] = so
    if raw.get("finnhubIndustry"):
        out["industry"] = raw["finnhubIndustry"]
    if raw.get("exchange"):
        out["exchange"] = raw["exchange"]
    if raw.get("weburl"):
        out["weburl"] = raw["weburl"]
    return out
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_finnhub_parsers.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add finnhub_api.py tests/test_finnhub_parsers.py
git commit -m "feat(finnhub): _parse_profile2 파서 + 단위테스트"
```

### Task 4: `get_sentiment_data`에 profile2 병합

**Files:**
- Modify: `finnhub_api.py` (`get_sentiment_data` ~110-247)

- [ ] **Step 1: profile2 호출·병합 코드 추가**

`get_sentiment_data` 안, `_store(cache_key, result)` 직전에 추가:

```python
    # 7) Company profile2 (로고·정확 IPO일·상장주식수·산업)
    prof = _safe("profile2", ticker, lambda: fc.company_profile2(symbol=ticker))
    parsed_prof = _parse_profile2(prof)
    result["logo"] = parsed_prof.get("logo", "")
    result["ipo_date"] = parsed_prof.get("ipo", "")
    result["share_outstanding"] = parsed_prof.get("share_outstanding")
    result["industry"] = parsed_prof.get("industry", "")
    result["exchange"] = parsed_prof.get("exchange", "")
```

- [ ] **Step 2: 회귀 없는지 기존 테스트 실행**

Run: `python -m pytest tests/test_finnhub_parsers.py -v`
Expected: PASS (변경 없음 — import-time assert 등 깨지지 않음).

- [ ] **Step 3: Commit**

```bash
git add finnhub_api.py
git commit -m "feat(finnhub): get_sentiment_data에 company_profile2 병합"
```

### Task 5: `/api/sentiment` 페이로드 확장

**Files:**
- Modify: `web_app/app.py` (~1654, `out["_FH_*"]` 블록)

- [ ] **Step 1: `_FH_*` 키 추가**

`web_app/app.py`에서 `out["_FH_CurrentPrice"] = fh.get("current_price", 0)` 다음 줄에 추가(같은 `if fh.get("available"):` 블록 안, `out["_FH_Available"] = True` 위):

```python
                out["_FH_Logo"] = fh.get("logo", "")
                out["_FH_IpoDate"] = fh.get("ipo_date", "")
                out["_FH_ShareOut"] = fh.get("share_outstanding")
                out["_FH_Industry"] = fh.get("industry", "")
                out["_FH_Exchange"] = fh.get("exchange", "")
```

- [ ] **Step 2: 수동 검증**

Run: 앱 실행 → 브라우저 콘솔/네트워크 탭에서 `/api/sentiment/NVDA?market=US` 응답 확인.
Expected: 응답 JSON에 `_FH_Logo`(https URL), `_FH_IpoDate` 포함.

- [ ] **Step 3: Commit**

```bash
git add web_app/app.py
git commit -m "feat(finnhub): /api/sentiment에 로고·프로필 키 추가"
```

### Task 6: 헤더 로고 렌더 (app.js)

**Files:**
- Modify: `web_app/static/app.js` (`_loadSentiment` ~2851)

- [ ] **Step 1: `_renderFhLogo(d)` 함수 추가**

`_renderFhNews` 함수 바로 아래에 추가:

```javascript
// ── Finnhub 회사 로고 (US, 헤더 dp-ticker 좌측) ──────────────────────────
function _renderFhLogo(d) {
  const tickerEl = document.getElementById('dp-ticker');
  let img = document.getElementById('dp-fh-logo');
  const url = d && d._FH_Available ? (d._FH_Logo || '') : '';
  const safe = /^https?:\/\//i.test(url) ? url : '';
  if (!safe) { if (img) img.remove(); return; }
  if (!tickerEl || !tickerEl.parentNode) return;
  if (!img) {
    img = document.createElement('img');
    img.id = 'dp-fh-logo';
    img.style.cssText = 'width:22px; height:22px; border-radius:5px; object-fit:contain; vertical-align:middle; margin-right:8px; background:var(--surface-2);';
    img.onerror = () => img.remove();  // 로고 깨지면 제거 (이니셜 fallback = 기존 텍스트)
    tickerEl.parentNode.insertBefore(img, tickerEl);
  }
  img.src = safe;
}
```

- [ ] **Step 2: `_loadSentiment`에서 호출**

`_renderFhNews(_lastDetailData);` 다음 줄에 추가:

```javascript
    _renderFhNews(_lastDetailData);
    _renderFhLogo(_lastDetailData);   // ← 추가
```

- [ ] **Step 3: 수동 검증**

US 종목 상세 열기 → `dp-ticker` 좌측에 로고 노출. 로고 없는 종목/깨진 URL → 이니셜(기존 텍스트)만, 에러 없음. KR 종목 → 로고 없음.

- [ ] **Step 4: Commit**

```bash
git add web_app/static/app.js
git commit -m "feat(finnhub): 상세 헤더 회사 로고 노출 (FH6)"
```

---

## Phase 3 — 내부자 심리 MSPR (FH7)

> `stock_insider_sentiment` 신규 배선 → 월별 MSPR 칩 + 추세 스파크라인.

### Task 7: 엔드포인트 실측 스모크

**Files:**
- Modify: `tools/test_finnhub.py`

- [ ] **Step 1: 스모크 호출 추가**

`print("\nDone.")` 직전에 추가:

```python
# 6) Insider sentiment MSPR (무료 여부 실측)
print("\n=== Insider Sentiment (AAPL) ===")
try:
    s = fc.stock_insider_sentiment("AAPL", "2025-01-01", "2026-06-01")
    rows = s.get("data") or []
    print(f"  rows={len(rows)}")
    for r in rows[-2:]:
        print(f"  {r.get('year')}-{r.get('month')}: mspr={r.get('mspr')} change={r.get('change')}")
except Exception as e:
    print(f"  Error: {e}")
```

- [ ] **Step 2: 실행해서 무료 여부 확인**

Run: `python tools/test_finnhub.py`
Expected: `rows=N` + mspr 값 출력. **403/Error면 중단·보고** — Phase 3 진행 불가.

- [ ] **Step 3: Commit**

```bash
git add tools/test_finnhub.py
git commit -m "test(finnhub): stock_insider_sentiment 스모크 추가"
```

### Task 8: `_parse_insider_sentiment` 파서 + 단위테스트

**Files:**
- Modify: `finnhub_api.py`
- Test: `tests/test_finnhub_parsers.py`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_finnhub_parsers.py`의 import 줄을 수정하고 클래스 추가:

```python
from finnhub_api import _parse_profile2, _parse_insider_sentiment  # noqa: E402
```

`if __name__` 블록 위에 추가:

```python
class TestParseInsiderSentiment(unittest.TestCase):
    def test_latest_and_trend(self) -> None:
        raw = {"data": [
            {"year": 2026, "month": 1, "mspr": -10.0, "change": -100},
            {"year": 2026, "month": 2, "mspr": 5.0, "change": 50},
            {"year": 2026, "month": 3, "mspr": 20.0, "change": 200},
        ]}
        out = _parse_insider_sentiment(raw)
        self.assertEqual(out["mspr"], 20.0)            # 최신 월
        self.assertEqual(out["mspr_trend"], [-10.0, 5.0, 20.0])  # 시간순
        self.assertEqual(out["mspr_change"], 15.0)     # 20 - 5

    def test_empty(self) -> None:
        self.assertEqual(_parse_insider_sentiment(None), {})
        self.assertEqual(_parse_insider_sentiment({"data": []}), {})

    def test_single_month_no_change(self) -> None:
        out = _parse_insider_sentiment({"data": [{"year": 2026, "month": 3, "mspr": 7.0}]})
        self.assertEqual(out["mspr"], 7.0)
        self.assertEqual(out["mspr_trend"], [7.0])
        self.assertEqual(out["mspr_change"], 0.0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_finnhub_parsers.py::TestParseInsiderSentiment -v`
Expected: FAIL — `ImportError: cannot import name '_parse_insider_sentiment'`.

- [ ] **Step 3: 파서 구현**

`finnhub_api.py`의 `_parse_profile2` 아래에 추가:

```python
def _parse_insider_sentiment(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """stock_insider_sentiment 응답 → 최신 MSPR + 월별 추세.

    MSPR(Monthly Share Purchase Ratio): -100~100. 양수=순매수 우위.
    """
    if not raw or not isinstance(raw, dict):
        return {}
    rows = raw.get("data") or []
    if not rows:
        return {}
    # 연-월 오름차순 정렬 (시간순 추세)
    rows = sorted(rows, key=lambda r: (r.get("year", 0), r.get("month", 0)))
    trend = [float(r.get("mspr") or 0.0) for r in rows]
    latest = trend[-1]
    prev = trend[-2] if len(trend) >= 2 else latest
    return {
        "mspr": latest,
        "mspr_trend": trend,
        "mspr_change": round(latest - prev, 4),
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_finnhub_parsers.py -v`
Expected: PASS (모든 클래스).

- [ ] **Step 5: Commit**

```bash
git add finnhub_api.py tests/test_finnhub_parsers.py
git commit -m "feat(finnhub): _parse_insider_sentiment 파서 + 단위테스트"
```

### Task 9: `get_sentiment_data`에 MSPR 병합 + 라우트 키

**Files:**
- Modify: `finnhub_api.py` (`get_sentiment_data`)
- Modify: `web_app/app.py` (`/api/sentiment` 블록)

- [ ] **Step 1: MSPR 호출·병합**

`get_sentiment_data` 안, Task 4에서 추가한 profile2 블록 다음(여전히 `_store` 직전)에 추가:

```python
    # 8) Insider sentiment MSPR (최근 ~18개월)
    twelve_mo_ago = (today - timedelta(days=540)).strftime("%Y-%m-%d")
    ins_sent = _safe("insider_sentiment", ticker,
                     lambda: fc.stock_insider_sentiment(ticker, twelve_mo_ago, today_s))
    parsed_ms = _parse_insider_sentiment(ins_sent)
    result["mspr"] = parsed_ms.get("mspr")
    result["mspr_trend"] = parsed_ms.get("mspr_trend", [])
    result["mspr_change"] = parsed_ms.get("mspr_change", 0.0)
```

- [ ] **Step 2: 라우트 페이로드 확장**

`web_app/app.py`에서 Task 5의 `_FH_Exchange` 줄 다음에 추가:

```python
                out["_FH_MSPR"] = fh.get("mspr")
                out["_FH_MSPRTrend"] = fh.get("mspr_trend", [])
                out["_FH_MSPRChange"] = fh.get("mspr_change", 0.0)
```

- [ ] **Step 3: 수동 검증**

Run: 앱 실행 → `/api/sentiment/NVDA?market=US` 응답 확인.
Expected: `_FH_MSPR`(숫자 or null), `_FH_MSPRTrend`(배열).

- [ ] **Step 4: Commit**

```bash
git add finnhub_api.py web_app/app.py
git commit -m "feat(finnhub): MSPR 내부자 심리 병합 + /api/sentiment 키"
```

### Task 10: MSPR 칩 + 스파크라인 렌더

**Files:**
- Modify: `web_app/static/app.js` (`_renderInvestorCard` ~3119 US 블록)

- [ ] **Step 1: `buildSparklineSVG` 시그니처 확인**

Run: `grep -n "function buildSparklineSVG" web_app/static/app.js`
Expected: 시그니처 확인(예: `buildSparklineSVG(values, opts)`). 인자 형태를 Step 2에 맞춘다. 시그니처가 다르면 Step 2의 호출부를 실제 시그니처로 교정.

- [ ] **Step 2: MSPR 칩을 US Finnhub 블록에 추가**

`web_app/static/app.js`의 `_renderInvestorCard`에서 US `if (d._FH_Available) {` 블록 안, `// 신규: 뉴스 buzz` 직전에 추가:

```javascript
    // 신규: 내부자 심리 MSPR (-100~100, 양수=순매수 우위)
    const mspr = d._FH_MSPR;
    if (mspr != null) {
      const trend = Array.isArray(d._FH_MSPRTrend) ? d._FH_MSPRTrend : [];
      let spark = '';
      if (trend.length >= 2 && typeof buildSparklineSVG === 'function') {
        spark = buildSparklineSVG(trend, { width: 56, height: 16,
          color: mspr >= 0 ? 'var(--success)' : 'var(--destructive)' });
      }
      items.push({
        label: '내부자 심리',
        value: `${mspr >= 0 ? '+' : ''}${mspr.toFixed(0)}`,
        sub: spark || (mspr >= 20 ? '강한 매수세' : mspr <= -20 ? '강한 매도세' : '중립'),
        color: mspr >= 0 ? 'var(--success)' : 'var(--destructive)',
        subIsHtml: !!spark,
      });
    }
```

- [ ] **Step 3: `subIsHtml` 지원하도록 렌더 루프 수정**

같은 함수 끝, `grid.innerHTML = items.map(...)` 안의 `it.sub` 렌더 부분을 찾아 `esc(it.sub)`를 조건부로 교체. 현재:

```javascript
${it.sub ? `<small ...>${esc(it.sub)}</small>` : ''}
```

를 다음으로 교체:

```javascript
${it.sub ? `<small style="display:block; font-size:10.5px; color:var(--text-tertiary); font-weight:600; margin-top:2px; text-align:right;">${it.subIsHtml ? it.sub : esc(it.sub)}</small>` : ''}
```

(주의: `buildSparklineSVG` 출력은 신뢰된 내부 생성 SVG 문자열이므로 esc 제외. 외부 입력 아님.)

- [ ] **Step 4: 수동 검증**

US 종목 상세 → 투자자 카드에 "내부자 심리 +N" 칩 + 미니 스파크라인. MSPR 없는 종목 → 칩 미표시. KR → US 블록 자체 skip.

- [ ] **Step 5: Commit**

```bash
git add web_app/static/app.js
git commit -m "feat(finnhub): 내부자 심리 MSPR 칩 + 스파크라인 (FH7)"
```

---

## Phase 4 — 시장 맥락: IPO 캘린더 + 시장 뉴스 (FH8/9)

> per-ticker 아님. 신규 `get_market_context()` + `/api/market_context` + 시장 영역 섹션.

### Task 11: 엔드포인트 실측 스모크

**Files:**
- Modify: `tools/test_finnhub.py`

- [ ] **Step 1: 스모크 호출 추가**

`print("\nDone.")` 직전에 추가:

```python
# 7) IPO calendar + general news (무료 여부 실측)
print("\n=== IPO Calendar (다음 30일) ===")
try:
    from datetime import datetime as _dt, timedelta as _td
    _t = _dt.now()
    ic = fc.ipo_calendar(_from=_t.strftime("%Y-%m-%d"),
                         to=(_t + _td(days=30)).strftime("%Y-%m-%d"))
    print(f"  ipos={len(ic.get('ipoCalendar') or [])}")
except Exception as e:
    print(f"  Error: {e}")
print("\n=== General News ===")
try:
    gn = fc.general_news("general", min_id=0)
    print(f"  news={len(gn or [])}; first={ (gn or [{}])[0].get('headline','')[:50] }")
except Exception as e:
    print(f"  Error: {e}")
```

- [ ] **Step 2: 실행해서 무료 여부 확인**

Run: `python tools/test_finnhub.py`
Expected: `ipos=N`, `news=N`. **둘 중 하나라도 403이면 해당 부분 제외하고 보고** (IPO·뉴스는 독립적이라 한쪽만 살릴 수 있음).

- [ ] **Step 3: Commit**

```bash
git add tools/test_finnhub.py
git commit -m "test(finnhub): ipo_calendar·general_news 스모크 추가"
```

### Task 12: `_parse_ipo_calendar` + `_parse_general_news` 파서 + 테스트

**Files:**
- Modify: `finnhub_api.py`
- Test: `tests/test_finnhub_parsers.py`

- [ ] **Step 1: 실패하는 테스트 추가**

import 줄 갱신:

```python
from finnhub_api import (  # noqa: E402
    _parse_profile2, _parse_insider_sentiment,
    _parse_ipo_calendar, _parse_general_news,
)
```

`if __name__` 위에 추가:

```python
class TestParseIpoCalendar(unittest.TestCase):
    def test_basic(self) -> None:
        raw = {"ipoCalendar": [
            {"date": "2026-06-10", "symbol": "ABC", "name": "Abc Inc",
             "price": "15.00-17.00", "numberOfShares": 1000000, "exchange": "NASDAQ"},
        ]}
        out = _parse_ipo_calendar(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["symbol"], "ABC")
        self.assertEqual(out[0]["date"], "2026-06-10")
        self.assertEqual(out[0]["price"], "15.00-17.00")

    def test_empty(self) -> None:
        self.assertEqual(_parse_ipo_calendar(None), [])
        self.assertEqual(_parse_ipo_calendar({"ipoCalendar": []}), [])


class TestParseGeneralNews(unittest.TestCase):
    def test_basic_limit_and_fields(self) -> None:
        raw = [
            {"headline": f"H{i}", "url": f"https://x/{i}", "source": "CNBC",
             "datetime": 1700000000 + i, "category": "top news"}
            for i in range(30)
        ]
        out = _parse_general_news(raw, limit=10)
        self.assertEqual(len(out), 10)
        self.assertEqual(out[0]["headline"], "H0")
        self.assertEqual(out[0]["source"], "CNBC")

    def test_empty(self) -> None:
        self.assertEqual(_parse_general_news(None), [])
        self.assertEqual(_parse_general_news([]), [])

    def test_skips_entries_without_headline(self) -> None:
        out = _parse_general_news([{"url": "https://x/1"}, {"headline": "ok", "url": "https://x/2"}])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["headline"], "ok")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_finnhub_parsers.py -v`
Expected: FAIL — ImportError(`_parse_ipo_calendar`).

- [ ] **Step 3: 파서 구현**

`finnhub_api.py`의 `_parse_insider_sentiment` 아래에 추가:

```python
def _parse_ipo_calendar(raw: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """ipo_calendar 응답 → 정규화 리스트."""
    if not raw or not isinstance(raw, dict):
        return []
    rows = raw.get("ipoCalendar") or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not r.get("symbol"):
            continue
        out.append({
            "date": r.get("date", ""),
            "symbol": r.get("symbol", ""),
            "name": r.get("name", ""),
            "price": r.get("price", ""),
            "shares": r.get("numberOfShares"),
            "exchange": r.get("exchange", ""),
        })
    return out


def _parse_general_news(raw: Optional[List[Dict[str, Any]]],
                        limit: int = 15) -> List[Dict[str, Any]]:
    """general_news 응답(list) → 정규화 리스트. headline 없는 항목 제외."""
    if not raw or not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for n in raw:
        h = n.get("headline")
        if not h:
            continue
        out.append({
            "headline": h[:160],
            "url": n.get("url", ""),
            "source": n.get("source", ""),
            "datetime": n.get("datetime", 0),
            "category": n.get("category", ""),
        })
        if len(out) >= limit:
            break
    return out
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_finnhub_parsers.py -v`
Expected: PASS (전체).

- [ ] **Step 5: Commit**

```bash
git add finnhub_api.py tests/test_finnhub_parsers.py
git commit -m "feat(finnhub): ipo_calendar·general_news 파서 + 단위테스트"
```

### Task 13: `get_market_context()` 함수 + 캐시

**Files:**
- Modify: `finnhub_api.py`

- [ ] **Step 1: 함수 추가**

`finnhub_api.py` 맨 아래에 추가:

```python
def get_market_context() -> Dict[str, Any]:
    """시장 전역 데이터 — IPO 캘린더(향후 30일) + 일반 시장 뉴스.

    per-ticker 아님. 자체 1시간 캐시(_CACHE_TTL) 사용.
    """
    if not is_available():
        return {"available": False, "ipos": [], "news": []}
    cache_key = "fh_market_context"
    cached = _cached(cache_key)
    if cached is not None:
        return cached
    fc = _client()
    today = datetime.now()
    today_s = today.strftime("%Y-%m-%d")
    in_30 = (today + timedelta(days=30)).strftime("%Y-%m-%d")

    ipo_raw = _safe("ipo_calendar", "_market",
                    lambda: fc.ipo_calendar(_from=today_s, to=in_30))
    news_raw = _safe("general_news", "_market",
                     lambda: fc.general_news("general", min_id=0))

    result = {
        "available": True,
        "ipos": _parse_ipo_calendar(ipo_raw),
        "news": _parse_general_news(news_raw),
    }
    _store(cache_key, result)
    return result
```

- [ ] **Step 2: 기존 테스트 회귀 확인**

Run: `python -m pytest tests/test_finnhub_parsers.py -v`
Expected: PASS (변경 없음 — 새 함수는 파서 재사용).

- [ ] **Step 3: Commit**

```bash
git add finnhub_api.py
git commit -m "feat(finnhub): get_market_context (IPO 캘린더 + 시장 뉴스)"
```

### Task 14: `/api/market_context` 라우트

**Files:**
- Modify: `web_app/app.py` (`/api/sentiment` 라우트 근처)

- [ ] **Step 1: 라우트 + 캐시 추가**

`web_app/app.py`의 `api_sentiment` 함수 정의 위(또는 `_sentiment_cache` 선언 근처)에 캐시 변수와 라우트 추가:

```python
_market_ctx_cache: dict = {"_ts": 0, "data": None}
_market_ctx_lock = threading.Lock()
_MARKET_CTX_TTL_SEC = 1800  # 30분


@app.route("/api/market_context")
def api_market_context():
    """GET /api/market_context → Finnhub IPO 캘린더 + 시장 뉴스 (US, 30분 캐시)."""
    _now = time.time()
    with _market_ctx_lock:
        c = _market_ctx_cache
        if c["data"] is not None and (_now - c["_ts"]) < _MARKET_CTX_TTL_SEC:
            return jsonify(c["data"])
    try:
        from finnhub_api import get_market_context, is_available as fh_ok
        if not fh_ok():
            return jsonify({"ok": False, "available": False, "ipos": [], "news": []})
        ctx = get_market_context()
        out = {"ok": True, "available": ctx.get("available", False),
               "ipos": ctx.get("ipos", []), "news": ctx.get("news", [])}
    except Exception as e:
        logging.debug("market_context failed: %s", e)
        out = {"ok": False, "available": False, "ipos": [], "news": []}
    with _market_ctx_lock:
        _market_ctx_cache["data"] = out
        _market_ctx_cache["_ts"] = _now
    return jsonify(out)
```

- [ ] **Step 2: 수동 검증**

Run: 앱 실행 → 브라우저에서 `/api/market_context` 직접 열기.
Expected: `{"ok":true,"available":true,"ipos":[...],"news":[...]}`.

- [ ] **Step 3: Commit**

```bash
git add web_app/app.py
git commit -m "feat(finnhub): /api/market_context 라우트 + 30분 캐시"
```

### Task 15: 시장 맥락 섹션 렌더 (app.js)

**Files:**
- Modify: `web_app/static/app.js`
- Modify: `web_app/templates/scanner.html` (섹션 컨테이너 1개)

- [ ] **Step 1: 컨테이너 배치 위치 확인**

Run: `grep -n "매크로\|macro\|etf-tab\|id=\"market" web_app/templates/scanner.html`
Expected: 매크로/ETF 영역 위치 파악. 그 영역 끝에 컨테이너를 둔다.

- [ ] **Step 2: 컨테이너 추가 (scanner.html)**

Step 1에서 찾은 매크로/ETF 섹션 닫는 태그 직후에 추가:

```html
<div id="market-context" style="display:none; margin-top:12px;">
  <div style="font-size:13px; font-weight:700; color:var(--text-secondary); margin:8px 0;">🆕 IPO 캘린더 · 시장 뉴스</div>
  <div id="market-context-body"></div>
</div>
```

- [ ] **Step 3: 로더 + 렌더 함수 추가 (app.js)**

`app.js` 적당한 최상위 위치(예: `_renderFhNews` 근처)에 추가:

```javascript
// ── 시장 맥락: IPO 캘린더 + 시장 뉴스 (US) ──────────────────────────────
let _marketCtxLoaded = false;
async function loadMarketContext() {
  if (_marketCtxLoaded) return;
  const wrap = document.getElementById('market-context');
  const body = document.getElementById('market-context-body');
  if (!wrap || !body) return;
  try {
    const res = await fetch('/api/market_context');
    const d = await res.json();
    if (!d.available || (!(d.ipos || []).length && !(d.news || []).length)) {
      wrap.style.display = 'none'; return;
    }
    const ipoRows = (d.ipos || []).slice(0, 8).map(i => `
      <div style="display:flex; justify-content:space-between; gap:8px; padding:7px 0; border-bottom:1px solid var(--border); font-size:12.5px;">
        <span style="color:var(--text-primary); font-weight:600;">${esc(i.symbol)} <span style="color:var(--text-tertiary); font-weight:400;">${esc(i.name || '')}</span></span>
        <span style="color:var(--text-secondary); white-space:nowrap;">${esc(i.date)}${i.price ? ` · $${esc(i.price)}` : ''}</span>
      </div>`).join('');
    const newsRows = (d.news || []).slice(0, 8).map(n => {
      const u = /^https?:\/\//i.test(n.url || '') ? n.url : '';
      const t = esc(n.headline || '');
      return `<div style="padding:7px 0; border-bottom:1px solid var(--border); font-size:12.5px; line-height:1.4;">
        ${u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer" style="color:var(--text-primary); text-decoration:none;">${t}</a>` : t}
        ${n.source ? `<span style="color:var(--text-tertiary); font-size:10.5px; margin-left:6px;">${esc(n.source)}</span>` : ''}
      </div>`;
    }).join('');
    body.innerHTML = `
      ${ipoRows ? `<div style="font-size:11.5px; font-weight:700; color:var(--text-tertiary); margin:4px 0;">예정 IPO</div>${ipoRows}` : ''}
      ${newsRows ? `<div style="font-size:11.5px; font-weight:700; color:var(--text-tertiary); margin:10px 0 4px;">시장 뉴스</div>${newsRows}` : ''}`;
    wrap.style.display = '';
    _marketCtxLoaded = true;
  } catch (e) {
    console.debug('market_context 로드 실패:', e);
    wrap.style.display = 'none';
  }
}
```

- [ ] **Step 4: 로더 호출 배선**

매크로/ETF 영역이 처음 표시되는 지점(또는 페이지 로드 후 idle)에서 `loadMarketContext()`를 호출. 가장 안전한 위치는 페이지 초기화 끝. `grep -n "DOMContentLoaded\|window.addEventListener('load'" web_app/static/app.js`로 초기화 훅을 찾아 그 안에 추가:

```javascript
  setTimeout(loadMarketContext, 1200);  // 첫 paint 방해 안 하도록 지연
```

- [ ] **Step 5: 수동 검증**

앱 실행 → 매크로/ETF 영역에 "IPO 캘린더 · 시장 뉴스" 섹션이 1.2초 후 채워짐. 데이터 없거나 키 미설정 → 섹션 숨김(에러 없음).

- [ ] **Step 6: Commit**

```bash
git add web_app/static/app.js web_app/templates/scanner.html
git commit -m "feat(finnhub): 시장 맥락 섹션 — IPO 캘린더 + 시장 뉴스 (FH8/9)"
```

---

## 최종 검증 (전체 Phase 완료 후)

- [ ] **전체 단위테스트**

Run: `python -m pytest tests/test_finnhub_parsers.py -v`
Expected: 전체 PASS.

- [ ] **회귀: 기존 테스트**

Run: `python -m pytest tests/ -q`
Expected: 신규 변경으로 기존 테스트 깨지지 않음.

- [ ] **수동 통합 (US/KR)**

US 종목: 로고·MSPR 칩·스파크라인·뉴스 리스트 모두 노출. KR 종목: Finnhub 섹션 전부 미표시(외인/기관만). 시장 맥락 섹션 노출.

---

## 안 하는 것 (이 계획 범위 밖)

- `social_sentiment`(유료), FH11 점수 엔진 통합(별도 spec), 정확 실적 캘린더(유료), 자동매매·매수추천·기대수익률 텍스트(README 금지).
- 실적 D-Day는 기존 추정 로직 유지(정확값 무료 불가). 별도 "추정" 라벨 개선은 선택 — 본 계획 미포함.
