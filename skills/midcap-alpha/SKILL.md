---
name: midcap-alpha
description: This skill should be used when the user asks to "미드캡 분석", "midcap scan", "미드캡 스캔", "S&P500 승격 후보", "sp500 promotion", "중형주 분석", "midcap alpha", "기관 매집", "accumulation pattern", "미드캡 리더", "midcap leader". Use this skill whenever the user mentions US mid-cap stock analysis, S&P 500 promotion candidates, or institutional accumulation in mid-cap stocks -- even if they don't explicitly say "midcap alpha". This skill provides quantitative mid-cap specific alpha signals that the standard stock scanner does not cover.
---

# Midcap Alpha

> SEC EDGAR + yfinance 하이브리드 데이터로 미국 미드캡 특화 알파 시그널(S&P500 승격 후보, 기관 매집 패턴, 내부자 순매수, 성장 모멘텀)을 분석하는 스킬

## Workflow

### Step 1: Midcap Universe Construction
**Type**: script

Run the universe builder to construct the target list:

```bash
python3 "${SKILL_DIR}/scripts/midcap_universe.py" --min-cap 3.0 --max-cap 20.0 --top-n 100
```

This script:
- Loads SP400 constituents from `web_app/index_membership.json`
- Filters by market cap range ($3B~$20B) via yfinance
- Includes SP500-boundary stocks (SP400 top 10% by market cap) as promotion candidates
- Outputs `midcap_universe.json` with ticker, sector, market cap

If the user specifies a sector filter (e.g., "반도체 미드캡"), pass `--sector <sector>` to narrow results.

Ask the user before proceeding:

```json
{
  "questions": [{
    "question": "분석할 미드캡 범위를 확인해주세요.",
    "header": "유니버스",
    "options": [
      {"label": "전체 미드캡 (추천)", "description": "SP400 기반 시총 $3B~$20B 전체를 분석해요. 약 100종목."},
      {"label": "특정 섹터만", "description": "관심 섹터를 좁혀서 분석해요. (예: Technology, Healthcare)"},
      {"label": "시총 범위 조정", "description": "기본 $3B~$20B 대신 다른 범위를 지정해요."}
    ],
    "multiSelect": false
  }]
}
```

### Step 2: SEC EDGAR Data Collection
**Type**: script

Run the SEC data collector for the universe:

```bash
python3 "${SKILL_DIR}/scripts/sec_edgar.py" --universe midcap_universe.json --output sec_data.json
```

This script collects two types of SEC filings:
- **13F Institutional Holdings**: Quarterly institutional ownership changes via SEC EDGAR XBRL API. Tracks QoQ increase/decrease in shares held by major institutions.
- **Form 4 Insider Transactions**: Officer/director buy/sell transactions. Aggregates net purchase amount and transaction count over trailing 90 days.

SEC EDGAR API is free with 10 requests/second limit. Results are cached locally (13F: 24h TTL, Form 4: 6h TTL) to minimize repeated calls.

After SEC collection, verify data quality: if >40% of tickers have missing 13F data, warn the user that institutional signals will be proxy-based for those tickers.

### Step 3: Midcap Alpha Signal Computation
**Type**: script

Run the signal engine:

```bash
python3 "${SKILL_DIR}/scripts/midcap_signals.py" --universe midcap_universe.json --sec-data sec_data.json --output midcap_scores.json
```

Four sub-signals are computed per ticker:

| Signal | Weight | Data Source | Description |
|--------|--------|-------------|-------------|
| S&P500 Promotion Readiness | 30% | yfinance + SEC 10-Q | Market cap proximity to SP500 floor + 4Q consecutive profit + liquidity threshold |
| Institutional Accumulation | 30% | SEC 13F + yfinance volume | 13F ownership QoQ increase + volume acceleration + price compression |
| Insider Net Purchase | 20% | SEC Form 4 | Officer/director net buy amount and frequency over 90 days |
| Growth Momentum | 20% | yfinance + existing RS Rating | RS Rating 80+ with quarterly revenue QoQ acceleration |

Each sub-signal produces a 0~100 score. Weighted sum produces `MidcapAlphaScore` (0~100).

**Signal orthogonalization**: Because sub-signals share underlying data (price, volume), pairwise correlation is computed. If correlation > 0.7 between any two signals, the redundant signal's weight is halved to prevent overconfidence from signal stacking.

**Regime circuit breaker**: If VIX > 25 (fetched via yfinance `^VIX`), all midcap-specific signal weights are dampened by 50%. This mirrors the existing Bear market cap pattern in `quant_nexus_v20.py`.

**Concentration limit**: Maximum 3 tickers per GICS sector in the final top-N list. Prevents single-theme concentration risk.

All signals carry `"basis": "proxy"` metadata tag. Output includes disclaimer: "무료 데이터 기반 참고 지표. 실시간 매매 판단에 부적합."

### Step 4: LLM Deep Briefing
**Type**: prompt

For the top 10~15 candidates by MidcapAlphaScore, read `midcap_scores.json` and generate a structured briefing per ticker:

```
## {Ticker} — {Company Name} ({Sector})
MidcapAlphaScore: {score}/100

### Why This Stock
{1-2 sentence reason this ticker surfaced as a midcap alpha candidate}

### Signal Breakdown
- Promotion Readiness: {score}/100 — {brief explanation}
- Institutional Accumulation: {score}/100 — {brief explanation}
- Insider Net Purchase: {score}/100 — {brief explanation}
- Growth Momentum: {score}/100 — {brief explanation}

### Catalysts & Timeline
{Upcoming events: index rebalancing dates, earnings, insider filing deadlines}

### Risks
{Key risks specific to this ticker's midcap alpha thesis}
```

Use Korean labels where appropriate:
- "승격 임박" for Promotion Readiness > 70
- "매집 초기" for Institutional Accumulation > 60
- "내부자 확신" for Insider Net Purchase > 65
- "성장 가속" for Growth Momentum > 70

### Step 5: Result Output
**Type**: generate

Produce final output in two formats:

1. **Console summary table**: Ticker | Sector | MidcapAlphaScore | Top Signal | One-line Reason
2. **Detailed JSON**: `midcap_alpha_report.json` with full signal breakdowns, saved to the project directory

Append disclaimer at the end of all outputs:
> 이 분석은 SEC EDGAR 공시 및 yfinance 무료 데이터 기반의 참고 지표입니다. S&P 지수위원회의 편입 결정에는 정량 기준 외 정성적 재량이 포함되며, 기관 매집 패턴은 프록시 추정입니다. 투자 판단의 근거로 단독 사용하지 마세요.

## References
- **`references/sp500-criteria.md`** -- S&P 500 편입 정량 기준 정리 (시총, 수익성, 유동성, 섹터 균형)
- **`references/signal-weights.md`** -- 4축 시그널 가중치 설계 근거 및 레짐별 조정 로직

## Scripts
- **`scripts/midcap_universe.py`** -- SP400 기반 미드캡 유니버스 구성 (시총 필터, 섹터 매핑)
- **`scripts/sec_edgar.py`** -- SEC EDGAR API로 13F 기관 보유 + Form 4 내부자 거래 수집
- **`scripts/midcap_signals.py`** -- 4축 알파 시그널 연산 + 복합 스코어링

## Settings
| Setting | Default | How to Change |
|---------|---------|---------------|
| Market cap range | $3B~$20B | AskUserQuestion at Step 1, or `--min-cap`/`--max-cap` args |
| Target count | Top 100 | `--top-n` arg in midcap_universe.py |
| Signal weights | Promotion 30% / Institutional 30% / Insider 20% / Growth 20% | Edit `references/signal-weights.md` |
| Cache TTL | 13F: 24h, Form4: 6h, Price: 1h | Constants in sec_edgar.py |
| VIX circuit breaker | VIX > 25 triggers 50% dampening | Constant in midcap_signals.py |
| Sector concentration | Max 3 per sector | Constant in midcap_signals.py |
