# Entry Threshold Backtest (EG-006)

- Generated: 2026-05-25T22:17:50
- Tickers: 20 (AAPL, MSFT, GOOGL, AMZN, NVDA, TSLA, META, AMD, AVGO, ORCL...)
- Period: 1y, Hold: 5 days
- Total samples: 184

## Bucket Performance

| Bucket | n | WinRate(%) | Sharpe (annu.) | MaxDD(%) | MeanRet(%) | 표본 충분 |
|---|---:|---:|---:|---:|---:|:---:|
| 음수 갭(추격) | 108 | 46.3 | 0.46 | -28.67 | 0.337 | ✓ |
| 진입적기 (r<0.5) | 54 | 46.3 | -0.69 | -28.03 | -0.358 | ✓ |
| 분할진입 (0.5≤r<1.0) | 10 | 60.0 | -0.46 | -17.88 | -0.329 | ⚠ 표본 부족 |
| 풀백대기 (r≥1.0) | 12 | 16.67 | -4.11 | -30.23 | -2.843 | ⚠ 표본 부족 |

## 권고

- `r<0.5` (진입적기) 버킷의 Sharpe/WR 가 가장 높으면 현재 임계값 유지.
- `lt_1_0` 와 `ge_1_0` 간 격차가 크지 않으면 임계값을 1.5 ATR 로 늦춰도 무방.
- 음수 갭(추격) 버킷이 양수 Sharpe 면 추격이 오히려 유효 → 라벨 재설계 필요.

## Sample Sufficiency

표본 ≥30 인 버킷: 음수 갭(추격), 진입적기 (r<0.5)