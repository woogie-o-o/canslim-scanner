# Contrarian Zone Pro v4 — Backtest Strategy

> `indicator` → `strategy()` 변환본. TradingView Strategy Tester에서 실제 손익·승률·MDD 측정.
> 검증 상태: TradingView 서버 컴파일 통과 (`pine_check` 0 errors / 0 warnings)

---

## 1. 핵심 차이 — 왜 신호가 "부정확"하게 느껴졌나

원본 v4는 필터 **5개 동시 ON 상태**라 다음 모두 만족해야 진입:
1. rawZone (drawdown + smoothed price 동시 σ 초과)
2. zoneMature (3봉 이상 지속)
3. Reversal Bar (첫 양봉/음봉)
4. HTF 추세 (상위TF EMA200 반대)
5. Volume Spike (평균 ×1.3)
6. RSI 극단 (<35 or >65)

→ zone 진입 후 **수 봉이 지난 뒤** 트리거되거나, 강한 zone이어도 거래량/RSI 조건 못 맞춰 누락.

**Backtest 버전은 필터 기본값을 느슨하게** 깔고 (`HTF=OFF`, `Volume=OFF`, `Min Bars=2`) 사용자가 차트별로 토글해서 최적 조합을 찾도록 설계.

## 2. 검증된 Pine v5 Strategy 코드

```pinescript
//@version=5
strategy('Contrarian Zone Pro v4 — Backtest', overlay=true, shorttitle='CZP BT', initial_capital=10000000, default_qty_type=strategy.percent_of_equity, default_qty_value=100, commission_type=strategy.commission.percent, commission_value=0.03, slippage=2, calc_on_every_tick=false, process_orders_on_close=true, max_lines_count=500, max_labels_count=500)

src           = input.source(ohlc4, 'Source', group='Core')
periodLen     = input.int(30, 'High/Low Period', minval=2, group='Core')
stdevPeriod   = input.int(50, 'Stdev Period', minval=2, group='Core')
sigmaMult     = input.float(2.0, 'Sigma Multiplier', minval=0.5, step=0.1, group='Core')

useTrend      = input.bool(false, 'HTF Trend Filter', group='Filters', tooltip='상위TF EMA200 추세 필터 — 가장 강한 필터')
htfTF         = input.timeframe('60', 'HTF Timeframe', group='Filters')
trendLen      = input.int(200, 'HTF EMA Length', minval=2, group='Filters')
useVol        = input.bool(false, 'Volume Confirmation', group='Filters')
volMult       = input.float(1.3, 'Volume Spike x', step=0.1, group='Filters')
useRSI        = input.bool(true, 'RSI Confirmation', group='Filters')
rsiLen        = input.int(14, 'RSI Length', minval=2, group='Filters')
rsiOS         = input.int(35, 'RSI Oversold', group='Filters')
rsiOB         = input.int(65, 'RSI Overbought', group='Filters')
useConfirm    = input.bool(true, 'Reversal Bar Trigger', group='Filters')
minBarsInZone = input.int(2, 'Min Bars in Zone', minval=1, group='Filters')

useLong       = input.bool(true, 'Enable Long (Fear)', group='Direction')
useShort      = input.bool(true, 'Enable Short (Greed)', group='Direction')

atrLen        = input.int(14, 'ATR Length', minval=1, group='Risk')
slMult        = input.float(1.0, 'SL × ATR', step=0.1, group='Risk')
tp1Mult       = input.float(1.5, 'TP1 × ATR (50%)', step=0.1, group='Risk')
tp2Mult       = input.float(3.0, 'TP2 × ATR (50%)', step=0.1, group='Risk')

useDate       = input.bool(false, 'Limit Backtest Date Range', group='Backtest')
startDate     = input.time(timestamp('2024-01-01'), 'Start', group='Backtest')
endDate       = input.time(timestamp('2026-12-31'), 'End', group='Backtest')
inRange       = not useDate or (time >= startDate and time <= endDate)

hh       = ta.highest(src, periodLen)
ll       = ta.lowest(src, periodLen)
FZ1      = (hh - src) / hh
FZ1Limit = ta.wma(FZ1, stdevPeriod) + sigmaMult * ta.stdev(FZ1, stdevPeriod)
FZ2      = ta.wma(src, periodLen)
FZ2Limit = ta.wma(FZ2, stdevPeriod) - sigmaMult * ta.stdev(FZ2, stdevPeriod)
rawFear  = FZ1 > FZ1Limit and FZ2 < FZ2Limit

GZ1      = (ll - src) / ll
GZ1Limit = ta.wma(GZ1, stdevPeriod) - sigmaMult * ta.stdev(GZ1, stdevPeriod)
GZ2      = ta.wma(src, periodLen)
GZ2Limit = ta.wma(GZ2, stdevPeriod) + sigmaMult * ta.stdev(GZ2, stdevPeriod)
rawGreed = GZ1 < GZ1Limit and GZ2 > GZ2Limit

f_ema(simple int l) => ta.ema(close, l)
htfClose = request.security(syminfo.tickerid, htfTF, close, lookahead=barmerge.lookahead_off)
htfEMA   = request.security(syminfo.tickerid, htfTF, f_ema(trendLen), lookahead=barmerge.lookahead_off)
fearTrendOK  = not useTrend or htfClose > htfEMA
greedTrendOK = not useTrend or htfClose < htfEMA

volOK    = not useVol or volume > ta.sma(volume, 20) * volMult
rsi      = ta.rsi(close, rsiLen)
rsiFearOK  = not useRSI or rsi < rsiOS
rsiGreedOK = not useRSI or rsi > rsiOB

fearBars = math.sum(rawFear ? 1 : 0, minBarsInZone)
greedBars = math.sum(rawGreed ? 1 : 0, minBarsInZone)
fearMature  = fearBars >= minBarsInZone
greedMature = greedBars >= minBarsInZone
bullBar = close > open and close > close[1]
bearBar = close < open and close < close[1]

fearTrig  = useConfirm ? (rawFear and fearMature and bullBar) : (rawFear and fearMature)
greedTrig = useConfirm ? (rawGreed and greedMature and bearBar) : (rawGreed and greedMature)

BuySignal  = fearTrig and fearTrendOK and volOK and rsiFearOK and inRange and useLong
SellSignal = greedTrig and greedTrendOK and volOK and rsiGreedOK and inRange and useShort

atr = ta.atr(atrLen)

if BuySignal and strategy.position_size == 0
    slL = close - atr * slMult
    tp1L = close + atr * tp1Mult
    tp2L = close + atr * tp2Mult
    strategy.entry('Long', strategy.long)
    strategy.exit('TP1 L', 'Long', qty_percent=50, limit=tp1L, stop=slL)
    strategy.exit('TP2 L', 'Long', limit=tp2L, stop=slL)

if SellSignal and strategy.position_size == 0
    slS = close + atr * slMult
    tp1S = close - atr * tp1Mult
    tp2S = close - atr * tp2Mult
    strategy.entry('Short', strategy.short)
    strategy.exit('TP1 S', 'Short', qty_percent=50, limit=tp1S, stop=slS)
    strategy.exit('TP2 S', 'Short', limit=tp2S, stop=slS)

if strategy.position_size > 0 and SellSignal
    strategy.close('Long', comment='Greed exit')
if strategy.position_size < 0 and BuySignal
    strategy.close('Short', comment='Fear exit')

fzOpen  = rawFear ? low - ta.tr : na
fzClose = rawFear ? low - 2 * ta.tr : na
plotcandle(fzOpen, fzOpen, fzClose, fzClose, color=#FC6C85, bordercolor=color.red, title='FearZone')
gzOpen  = rawGreed ? high + ta.tr : na
gzClose = rawGreed ? high + 2 * ta.tr : na
plotcandle(gzOpen, gzOpen, gzClose, gzClose, color=#90EE90, bordercolor=color.green, title='GreedZone')

gzMid = rawGreed ? (high + low) / 2 : na
plot(gzMid, title='Greed Mid', style=plot.style_cross, color=#FFEB00, linewidth=4)
fzMid = rawFear ? (high + low) / 2 : na
plot(fzMid, title='Fear Mid', style=plot.style_cross, color=#FFFFFF, linewidth=4)
```

## 3. 백테스트 절차

1. 위 코드 복사 → Pine Editor → "Save As New" → "CZP Backtest"
2. **Add to chart** → 차트 하단 **Strategy Tester** 탭 열기
3. **Overview** 탭에서 Net Profit / Win Rate / Profit Factor / Max Drawdown 확인
4. **Settings** → **Inputs**에서 필터 토글해 가며 비교

## 4. 필터별 정확도 비교 절차

같은 종목/같은 기간에서 아래 4개 조합을 차례로 돌려서 비교:

| 시나리오 | RSI | Volume | HTF Trend | Reversal Bar | 결과로 보는 것 |
|---|---|---|---|---|---|
| **A. Baseline** | OFF | OFF | OFF | OFF | 원 신호 빈도·정확도 (raw zone만) |
| **B. RSI만** | ON | OFF | OFF | OFF | RSI 필터 효과 |
| **C. RSI+Reversal** (권장 기본) | ON | OFF | OFF | ON | 노이즈 컷 |
| **D. 전부 ON** | ON | ON | ON | ON | 가장 깐깐 — 신호 수 급감 |

핵심 지표:
- **Net Profit ↑ + Win Rate ↑ + Trades ≥30** = 신뢰 가능한 조합
- Trades < 10 → 표본 부족, 의미 없음

## 5. 종목별 권장 시작값

| 종목 유형 | useTrend | useVol | useRSI | useConfirm | Min Bars | Sigma |
|---|---|---|---|---|---|---|
| **추세 강한 코인 (BTC/ETH)** | ON | OFF | ON | ON | 3 | 2.0 |
| **횡보 코인** | OFF | OFF | ON | OFF | 2 | 1.5 |
| **국내 주식 (KOSPI)** | ON | ON | ON | ON | 3 | 2.0 |
| **미국 우량주** | ON | OFF | ON | ON | 2 | 2.0 |
| **변동성 높은 소형주** | OFF | ON | ON | ON | 4 | 2.5 |

## 6. 결과 해석 가이드

| 지표 | 좋은 값 | 의미 |
|---|---|---|
| Net Profit % | > 50% (1년) | 절대 수익률 |
| Profit Factor | > 1.5 | 총익절 ÷ 총손절 |
| Win Rate | > 45% | 컨트래리언이라 50% 안돼도 OK |
| Avg Trade | > 0.5% | 거래당 평균수익 |
| Max Drawdown | < 20% | 최대 자본 감소 |
| Total Trades | > 30 | 통계 유의성 확보 |

**Win Rate 낮아도 Profit Factor 높으면 OK** — TP2가 SL보다 3배 크니까 30% 승률에도 PF 1.5+ 가능.

## 7. 주의 — 백테스트 함정

- **15분봉 1년치만** 봐도 충분 (TradingView 무료 = 약 5000봉)
- **수수료(0.03%) + 슬리피지(2 tick)** 반영됨 — 실전 가깝게
- **`calc_on_every_tick=false`** = 봉 마감 기준 신호 (실전과 동일)
- **lookahead 미사용** = 미래참조 없음 (정직한 결과)
- 한 종목에서 좋다고 다른 종목도 좋은 건 아님 → 종목별 따로 튜닝
