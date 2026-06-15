# Signal Weight Design Rationale

4축 미드캡 알파 시그널의 가중치 설계 근거.

## Default Weights

| Signal | Weight | Rationale |
|--------|--------|-----------|
| S&P500 Promotion Readiness | 30% | Academically validated alpha (+5~8% excess return). Clearest quantitative criteria among the four signals. |
| Institutional Accumulation | 30% | Combines SEC 13F filing frequency (fundamental) with volume/price patterns (technical). Dual-source confirmation increases reliability. |
| Insider Net Purchase | 20% | SEC Form 4 is real-time and authoritative, but single-insider noise is high. Lower weight reflects noise level. |
| Growth Momentum | 20% | RS + revenue acceleration captures secular growth trend. Lower weight because it partially overlaps with existing scanner's RS Rating. |

## Regime-Based Adjustment

| Market Regime | Adjustment | Rationale |
|---------------|------------|-----------|
| VIX <= 25 (Normal) | No change | Standard weights apply |
| VIX > 25 (Elevated) | All signals dampened 50% | Midcap-specific alpha is unreliable in high-volatility regimes. Systematic risk dominates idiosyncratic signals. |

## Orthogonalization Rules

Signals sharing underlying data (price, volume, Finnhub) may exhibit spurious correlation.

- If two signal scores differ by < 10 points AND both > 50: reduce the lighter-weighted signal by 30%
- Renormalize weights to sum to 1.0 after adjustment
- This prevents overconfidence from "all signals agree" when they derive from the same data

## Concentration Limit

- Maximum 3 tickers per GICS sector in final ranked output
- Prevents single-theme concentration (e.g., 5 AI midcaps all scoring high simultaneously)
- Applied after scoring, before LLM briefing generation

## Future Iteration Targets

- Backtest IC (Information Coefficient) for each signal axis against 20-day forward returns
- If any signal's IC < 0.01 across 6 months, consider removing or replacing it
- Add sector-specific weight overrides (e.g., biotech may need different promotion criteria)
