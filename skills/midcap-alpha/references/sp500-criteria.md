# S&P 500 Index Inclusion Criteria

S&P 지수위원회의 편입 기준을 정량적으로 정리한 참고 문서.

## Quantitative Requirements (2026 estimates)

| Criterion | Threshold | Data Source | Verifiable? |
|-----------|-----------|-------------|-------------|
| Market capitalization | >= $18.0B (annually adjusted) | yfinance `marketCap` | Yes |
| Quarterly profitability | 4 consecutive quarters GAAP net income > 0 | SEC 10-Q, yfinance `trailingEps` | Partial |
| Public float | >= 50% of shares outstanding | yfinance `floatShares` | Yes |
| Liquidity | Annual trading volume >= 1.0x float-adjusted shares | yfinance `averageVolume` | Yes |
| Domicile | U.S. company (incorporated or HQ) | yfinance `country` | Yes |
| Listing | NYSE, NASDAQ, or Cboe BZX | Exchange data | Yes |

## Qualitative Factors (not quantifiable)

- Sector balance within the index
- Committee discretion on "adequate representation"
- Avoiding over-concentration in any single sector
- Timing relative to quarterly rebalancing schedule (3rd Friday of March/June/September/December)

## Signal Design Implications

- **Market cap proximity** is the strongest predictor — 80%+ of inclusions have market cap clearly above minimum
- **Profitability streak** is a hard gate — a single quarterly loss resets the clock
- **Liquidity** is rarely the binding constraint for midcap leaders
- **Committee discretion** cannot be modeled — label output as "필요조건 충족도" not "승격 확률"

## Historical Inclusion Patterns

- Average excess return around inclusion announcement: +5~8% (academic consensus)
- Most alpha is captured in the 1-2 weeks before official announcement
- Passive fund buying creates mechanical demand ~5 trading days before effective date
- SP400 → SP500 promotions typically announced 3-5 business days before effective date
