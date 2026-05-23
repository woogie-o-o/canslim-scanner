# 종목 상세 드로어 시그널 이력 타임라인 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 종목 상세 드로어에 그 종목의 등급(S/A/B/C)·진입 타이밍 변화를 14일 컬러 셀 스트립으로 보여준다.

**Architecture:** 기존 일별 스냅샷 인프라(`web_app/history.py`)를 재사용한다. `save_snapshot`에 `entry` 필드를 추가하고, 신규 `load_timeline`이 14일 달력일을 순회해 `[{date, grade, entry}]` 배열을 만든다. 신규 라우트 `GET /api/signal-history/<ticker>`가 이를 JSON으로 반환하고, 프론트는 드로어 열림 시 lazy fetch 해서 2줄 스트립으로 렌더한다.

**Tech Stack:** Python 3 / Flask, pytest (테스트는 `web_app/tests/`), 브라우저 글로벌 스크립트 `web_app/static/app.js`, `web_app/templates/scanner.html` 인라인 CSS.

---

## File Structure

- `web_app/history.py` — `_grade_from_score`, `load_timeline` 추가, `save_snapshot`에 `entry` 필드 추가.
- `web_app/app.py` — 신규 라우트 `GET /api/signal-history/<ticker>`.
- `web_app/static/app.js` — `loadSignalHistory`, `_renderSignalHistory` 추가, `openDetail` 연결.
- `web_app/templates/scanner.html` — `dp-history-card` 컨테이너 + CSS.
- `web_app/tests/test_history_timeline.py` — `_grade_from_score`/`load_timeline`/엔드포인트 테스트 (신규).

---

### Task 1: `_grade_from_score` 순수 함수

**Files:**
- Modify: `web_app/history.py`
- Test: `web_app/tests/test_history_timeline.py` (create)

- [ ] **Step 1: Write the failing test**

Create `web_app/tests/test_history_timeline.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import history


def test_grade_from_score_cuts():
    assert history._grade_from_score(75) == "S"
    assert history._grade_from_score(74) == "A"
    assert history._grade_from_score(60) == "A"
    assert history._grade_from_score(59) == "B"
    assert history._grade_from_score(45) == "B"
    assert history._grade_from_score(44) == "C"


def test_grade_from_score_invalid():
    assert history._grade_from_score(None) is None
    assert history._grade_from_score("abc") is None
    assert history._grade_from_score("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest web_app/tests/test_history_timeline.py -v`
Expected: FAIL with `AttributeError: module 'history' has no attribute '_grade_from_score'`

- [ ] **Step 3: Write minimal implementation**

Add to `web_app/history.py` after `_MAX_LOOKBACK_DAYS = 14`:

```python
def _grade_from_score(score) -> str | None:
    """종합점수 → 등급 S/A/B/C. 숫자가 아니면 None. 컷은 프론트 _stockGrade와 정합."""
    if score is None:
        return None
    try:
        n = float(score)
    except (TypeError, ValueError):
        return None
    if n >= 75:
        return "S"
    if n >= 60:
        return "A"
    if n >= 45:
        return "B"
    return "C"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest web_app/tests/test_history_timeline.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add web_app/history.py web_app/tests/test_history_timeline.py
git commit -m "feat: add _grade_from_score to history module"
```

---

### Task 2: `save_snapshot`에 `entry` 필드 추가

**Files:**
- Modify: `web_app/history.py:102-109`
- Test: `web_app/tests/test_history_timeline.py`

- [ ] **Step 1: Write the failing test**

Append to `web_app/tests/test_history_timeline.py`:

```python
import json
import importlib
from datetime import date


def _reload_history_with_dir(tmp_path, monkeypatch):
    import history as h
    importlib.reload(h)
    monkeypatch.setattr(h, "_SNAP_DIR", str(tmp_path))
    return h


def test_save_snapshot_includes_entry(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    results = [
        {"Ticker": "AAA", "TotalScore": 80, "EntryStatus": "STRONG"},
        {"Ticker": "BBB", "TotalScore": 50},
    ]
    h.save_snapshot(results, "US")
    p = os.path.join(str(tmp_path), f"scanner_US_{date.today().isoformat()}.json")
    with open(p, encoding="utf-8") as f:
        snap = json.load(f)
    assert snap["AAA"]["entry"] == "STRONG"
    assert snap["BBB"]["entry"] is None
    assert snap["AAA"]["score"] == 80.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest web_app/tests/test_history_timeline.py::test_save_snapshot_includes_entry -v`
Expected: FAIL with `KeyError: 'entry'`

- [ ] **Step 3: Write minimal implementation**

Modify `web_app/history.py` `save_snapshot`, the `snap` dict comprehension:

```python
    snap = {
        r["Ticker"]: {
            "score": round(float(r.get("TotalScore", 0) or 0), 1),
            "rank": i + 1,
            "entry": r.get("EntryStatus"),
        }
        for i, r in enumerate(sorted_by_score)
        if r.get("Ticker")
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest web_app/tests/test_history_timeline.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add web_app/history.py web_app/tests/test_history_timeline.py
git commit -m "feat: store EntryStatus in daily snapshot"
```

---

### Task 3: `load_timeline` 조회 함수

**Files:**
- Modify: `web_app/history.py`
- Test: `web_app/tests/test_history_timeline.py`

- [ ] **Step 1: Write the failing test**

Append to `web_app/tests/test_history_timeline.py`:

```python
from datetime import timedelta


def _write_snap(tmp_path, market, day, payload):
    p = os.path.join(str(tmp_path), f"scanner_{market}_{day.isoformat()}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_load_timeline_normal(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    today = date.today()
    _write_snap(tmp_path, "US", today, {"AAA": {"score": 80, "rank": 1, "entry": "STRONG"}})
    _write_snap(tmp_path, "US", today - timedelta(days=1),
                {"AAA": {"score": 50, "rank": 2, "entry": "AVOID"}})
    tl = h.load_timeline("AAA", "US")
    assert len(tl) == 14
    assert tl[-1] == {"date": today.isoformat(), "grade": "S", "entry": "STRONG"}
    assert tl[-2] == {"date": (today - timedelta(days=1)).isoformat(),
                      "grade": "B", "entry": "AVOID"}
    # 날짜 오름차순
    assert tl[0]["date"] < tl[-1]["date"]


def test_load_timeline_empty_day(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    today = date.today()
    _write_snap(tmp_path, "US", today, {"AAA": {"score": 80, "rank": 1, "entry": "STRONG"}})
    tl = h.load_timeline("AAA", "US")
    # 어제는 스냅샷 없음 → 빈 날
    assert tl[-2] == {"date": (today - timedelta(days=1)).isoformat(),
                      "grade": None, "entry": None}


def test_load_timeline_legacy_snapshot(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    today = date.today()
    _write_snap(tmp_path, "US", today, {"AAA": {"score": 80, "rank": 1}})
    tl = h.load_timeline("AAA", "US")
    assert tl[-1] == {"date": today.isoformat(), "grade": "S", "entry": None}


def test_load_timeline_ticker_missing(tmp_path, monkeypatch):
    h = _reload_history_with_dir(tmp_path, monkeypatch)
    today = date.today()
    _write_snap(tmp_path, "US", today, {"BBB": {"score": 80, "rank": 1, "entry": "STRONG"}})
    tl = h.load_timeline("AAA", "US")
    assert all(item["grade"] is None and item["entry"] is None for item in tl)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest web_app/tests/test_history_timeline.py -k load_timeline -v`
Expected: FAIL with `AttributeError: module 'history' has no attribute 'load_timeline'`

- [ ] **Step 3: Write minimal implementation**

Add to `web_app/history.py` after `_grade_from_score`:

```python
def load_timeline(ticker: str, market: str) -> list[dict]:
    """오늘 포함 직전 14 달력일의 등급·진입 이력. 날짜 오름차순."""
    today = date.today()
    out: list[dict] = []
    for back in range(_MAX_LOOKBACK_DAYS - 1, -1, -1):
        d = today - timedelta(days=back)
        snap = _load(market, d)
        rec = snap.get(ticker) if snap else None
        if rec:
            grade = _grade_from_score(rec.get("score"))
            entry = rec.get("entry")
        else:
            grade = None
            entry = None
        out.append({"date": d.isoformat(), "grade": grade, "entry": entry})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest web_app/tests/test_history_timeline.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add web_app/history.py web_app/tests/test_history_timeline.py
git commit -m "feat: add load_timeline for 14-day signal history"
```

---

### Task 4: `/api/signal-history/<ticker>` 엔드포인트

**Files:**
- Modify: `web_app/app.py` (신규 라우트 — `/api/score-history/<ticker>` 라우트 근처 line 1326 부근에 추가)
- Test: `web_app/tests/test_history_timeline.py`

- [ ] **Step 1: Write the failing test**

Append to `web_app/tests/test_history_timeline.py`:

```python
def test_endpoint_ok(monkeypatch):
    import app as flask_app
    monkeypatch.setattr("history.load_timeline",
                        lambda t, m: [{"date": "2026-05-22", "grade": "S", "entry": "STRONG"}])
    client = flask_app.app.test_client()
    resp = client.get("/api/signal-history/AAPL?market=US")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ticker"] == "AAPL"
    assert data["market"] == "US"
    assert isinstance(data["timeline"], list)


def test_endpoint_missing_market(monkeypatch):
    import app as flask_app
    client = flask_app.app.test_client()
    assert client.get("/api/signal-history/AAPL").status_code == 400


def test_endpoint_bad_market(monkeypatch):
    import app as flask_app
    client = flask_app.app.test_client()
    assert client.get("/api/signal-history/AAPL?market=XX").status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest web_app/tests/test_history_timeline.py -k endpoint -v`
Expected: FAIL — 404 instead of 200/400 (route not defined)

- [ ] **Step 3: Write minimal implementation**

Add to `web_app/app.py` near the existing `/api/score-history/<ticker>` route (~line 1326). Confirm `import history` is available in scope (it is imported in `_run_scan`; add a module-level `import history` near the top if not already present):

```python
@app.route("/api/signal-history/<ticker>")
def api_signal_history(ticker):
    market = request.args.get("market")
    if market not in ("KR", "US"):
        return jsonify({"error": "market must be KR or US"}), 400
    try:
        import history
        timeline = history.load_timeline(ticker, market)
    except Exception as e:
        logging.warning("signal-history failed (%s): %s", ticker, e)
        return jsonify({"ticker": ticker, "market": market, "timeline": []}), 500
    return jsonify({"ticker": ticker, "market": market, "timeline": timeline})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest web_app/tests/test_history_timeline.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add web_app/app.py web_app/tests/test_history_timeline.py
git commit -m "feat: add /api/signal-history endpoint"
```

---

### Task 5: `dp-history-card` HTML + CSS

**Files:**
- Modify: `web_app/templates/scanner.html`

- [ ] **Step 1: Add the card container**

In `web_app/templates/scanner.html`, find the 사분면 카드 `dp-quadrant-card` in the detail drawer and add immediately after its closing `</div>`:

```html
<div class="dp-card" id="dp-history-card">
  <div class="dp-card-title">시그널 이력</div>
  <div id="dp-history-strip"></div>
  <div class="history-axis"><span id="dp-history-start"></span><span id="dp-history-end"></span></div>
</div>
```

(If the drawer cards use a class other than `dp-card`/`dp-card-title`, match the existing sibling card's classes — inspect `dp-quadrant-card` markup and mirror it.)

- [ ] **Step 2: Add CSS**

In the `<style>` block, after the `.grade-badge` rules added in the grade/timing feature:

```css
.history-strip { display: flex; flex-direction: column; gap: 4px; }
.history-row { display: flex; gap: 3px; }
.history-cell { flex: 1; height: 16px; border-radius: 3px; min-width: 6px; }
.history-cell-empty { background: var(--surface-page); border: 1px solid rgba(0,0,0,0.08); }
.history-cell.grade-S { background: #7C3AED; }
.history-cell.grade-A { background: #2563EB; }
.history-cell.grade-B { background: #16A34A; }
.history-cell.grade-C { background: #9CA3AF; }
.history-cell.entry-green { background: #16A34A; }
.history-cell.entry-yellow { background: #EAB308; }
.history-cell.entry-red { background: #DC2626; }
.history-axis { display: flex; justify-content: space-between; font-size: 11px; color: var(--text-muted); margin-top: 4px; }
.history-empty-msg { font-size: 12px; color: var(--text-muted); }
@media (max-width: 640px) { .history-cell { height: 12px; min-width: 4px; } }
```

- [ ] **Step 3: Verify in browser**

Start the dev server, open the scanner, click a stock to open the drawer. Confirm the `시그널 이력` card placeholder appears below the 사분면 카드 (strip empty until Task 6 wires data).

- [ ] **Step 4: Commit**

```bash
git add web_app/templates/scanner.html
git commit -m "feat: add signal history card markup and styles"
```

---

### Task 6: `loadSignalHistory` / `_renderSignalHistory` + `openDetail` 연결

**Files:**
- Modify: `web_app/static/app.js` (`openDetail` line ~1601, lazy calls ~1619-1621; reference `loadConsensus` at ~3007)

- [ ] **Step 1: Add the fetch + render functions**

Add to `web_app/static/app.js` near `loadConsensus` (~line 3007):

```javascript
// 진입 상태 → 색 클래스 매핑
function _entryColorClass(entry) {
  if (entry === 'STRONG' || entry === 'GREEN') return 'entry-green';
  if (entry === 'NEUTRAL' || entry === 'YELLOW') return 'entry-yellow';
  if (entry === 'AVOID' || entry === 'RED') return 'entry-red';
  return 'history-cell-empty';
}

function loadSignalHistory(ticker, market) {
  var card = document.getElementById('dp-history-card');
  fetch('/api/signal-history/' + encodeURIComponent(ticker) + '?market=' + encodeURIComponent(market))
    .then(function (r) { if (!r.ok) throw new Error('http ' + r.status); return r.json(); })
    .then(function (d) { _renderSignalHistory(d.timeline || []); })
    .catch(function () { if (card) card.style.display = 'none'; });
}

function _renderSignalHistory(items) {
  var card = document.getElementById('dp-history-card');
  var strip = document.getElementById('dp-history-strip');
  var startEl = document.getElementById('dp-history-start');
  var endEl = document.getElementById('dp-history-end');
  if (!card || !strip) return;
  card.style.display = '';
  var hasData = items.some(function (it) { return it.grade || it.entry; });
  if (!items.length || !hasData) {
    strip.innerHTML = '<div class="history-empty-msg">이력 데이터가 아직 없어요</div>';
    if (startEl) startEl.textContent = '';
    if (endEl) endEl.textContent = '';
    return;
  }
  var gradeRow = '', entryRow = '';
  items.forEach(function (it) {
    var gCls = it.grade ? 'grade-' + it.grade : 'history-cell-empty';
    var eCls = _entryColorClass(it.entry);
    var entryLabel = _ENTRY_LABEL[it.entry] || '-';
    var tip = esc(it.date + ' · ' + (it.grade || '-') + '등급 · ' + entryLabel);
    gradeRow += '<div class="history-cell ' + gCls + '" title="' + tip + '"></div>';
    entryRow += '<div class="history-cell ' + eCls + '" title="' + tip + '"></div>';
  });
  strip.className = 'history-strip';
  strip.innerHTML = '<div class="history-row">' + gradeRow + '</div>' +
                    '<div class="history-row">' + entryRow + '</div>';
  function md(iso) { var p = iso.split('-'); return Number(p[1]) + '/' + Number(p[2]); }
  if (startEl) startEl.textContent = esc(md(items[0].date));
  if (endEl) endEl.textContent = esc(md(items[items.length - 1].date));
}
```

(If `_ENTRY_LABEL` keys differ from EntryStatus values, the `entryLabel` lookup falls back to `'-'` safely. Verify `_ENTRY_LABEL` keys include `STRONG/NEUTRAL/AVOID`; if it is keyed by `GREEN/YELLOW/RED`, add the missing aliases or normalize before lookup.)

- [ ] **Step 2: Wire into `openDetail`**

In `openDetail(ticker)`, alongside the lazy calls at ~lines 1619-1621 (`loadDpFourAxis`, `_loadAqSignal`, `loadConsensus`), add:

```javascript
  loadSignalHistory(ticker, currentMarket);
```

- [ ] **Step 3: Verify in browser**

Start the dev server. Open the scanner, click a stock. Confirm: (a) the 2-row colored strip renders with 14 cells per row; (b) empty days show gray; (c) tooltips show `날짜 · 등급 · 진입라벨`; (d) start/end M/D labels appear; (e) a ticker with no history shows "이력 데이터가 아직 없어요".

- [ ] **Step 4: Commit**

```bash
git add web_app/static/app.js
git commit -m "feat: render signal history timeline in detail drawer"
```

---

## Self-Review Notes

- 스펙의 모든 섹션(스키마 확장, `load_timeline`, 엔드포인트, 카드, 렌더링)이 Task 1-6에 매핑됨.
- `/api/signal-history/`는 기존 `/api/score-history/`와 이름이 다름 — 충돌 없음.
- 14일 윈도우 경계: `range(13, -1, -1)`로 오늘 포함 14일, 오름차순.
- 등급 컷(75/60/45)은 프론트 `_stockGrade`와 정합.
- 엣지 케이스(빈 날, 구버전 스냅샷, ticker 누락, market 누락/오류, fetch 실패)는 Task 3/4/6 테스트·코드에 반영됨.
