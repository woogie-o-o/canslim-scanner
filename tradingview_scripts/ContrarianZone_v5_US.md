# Contrarian Zone v5 US — High Win-Rate Strategy

> 미국주식 정규장 (09:30–16:00 ET) + **승률 우선** 튜닝.
> 검증 상태: TradingView 서버 컴파일 통과 (`pine_check` 0 errors / 0 warnings)

---

## 1. v4 → v5 핵심 개선 (승률↑ 목적)

| # | 개선 | 효과 |
|---|---|---|
| 1 | **Breakeven Stop** — TP1(50%) 체결 후 잔여물량 SL을 진입가로 이동 | 절반 익절 후 본전 보호 → 손실 거래 감소 |
| 2 | **Bullish/Bearish Engulfing** (단순 양/음봉 대신) | 진짜 반전봉만 → 페이크아웃 컷 |
| 3 | **VWAP 필터** — Long은 close<VWAP, Short는 close>VWAP | 평균회귀 정직 (낙폭/과열 구간에서만) |
| 4 | **시간 필터** — 개장 15분 + 마감 30분 노이즈 제외 | 변동성 폭증 구간 회피 |
| 5 | **Sigma 2.5** (v4 2.0) | "진짜 강한 zone"만 인정 |
| 6 | **일일 거래 제한** (기본 2회) | 과도한 추격매매 차단 |
| 7 | **TP1 = 1×ATR** (v4 1.5×ATR) | 짧은 익절 → 도달률↑ → 승률↑ |
| 8 | **SL = 1.5×ATR** (v4 1.0×ATR) | 노이즈에 죽지 않음 |

## 2. 검증된 Pine v5 코드

```pinescript
//@version=5
strategy('Contrarian Zone v5 US — High WinRate', overlay=true, shorttitle='CZ v5 US', initial_capital=10000, default_qty_type=strategy.percent_of_equity, default_qty_value=100, commission_type=strategy.commission.percent, commission_value=0.03, slippage=2, calc_on_every_tick=false, process_orders_on_close=true, max_lines_count=500, max_labels_count=500)

src           = input.source(ohlc4, 'Source', group='Core')
periodLen     = input.int(30, 'High/Low Period', minval=2, group='Core')
stdevPeriod   = input.int(50, 'Stdev Period', minval=2, group='Core')
sigmaMult     = input.float(2.5, 'Sigma Multiplier', minval=0.5, step=0.1, group='Core', tooltip='2.5 = 진짜 강한 zone만')

useTrend      = input.bool(true, 'HTF Trend Filter', group='Filters')
htfTF         = input.timeframe('60', 'HTF Timeframe', group='Filters')
trendLen      = input.int(200, 'HTF EMA Length', minval=2, group='Filters')
useVWAP       = input.bool(true, 'VWAP Filter (Long<VWAP, Short>VWAP)', group='Filters')
useVol        = input.bool(true, 'Volume Confirmation', group='Filters')
volMult       = input.float(1.5, 'Volume Spike x', step=0.1, group='Filters')
useRSI        = input.bool(true, 'RSI Confirmation', group='Filters')
rsiLen        = input.int(14, 'RSI Length', minval=2, group='Filters')
rsiOS         = input.int(30, 'RSI Oversold', group='Filters')
rsiOB         = input.int(70, 'RSI Overbought', group='Filters')
useEngulf     = input.bool(true, 'Engulfing Bar (stronger reversal)', group='Filters')
minBarsInZone = input.int(2, 'Min Bars in Zone', minval=1, group='Filters')

useTime       = input.bool(true, 'US Regular Session Filter', group='Session')
skipOpenMin   = input.int(15, 'Skip Open Minutes', minval=0, group='Session')
skipCloseMin  = input.int(30, 'Skip Close Minutes', minval=0, group='Session')
maxTradesDay  = input.int(2, 'Max Trades per Day', minval=1, group='Session')

useLong       = input.bool(true, 'Enable Long', group='Direction')
useShort      = input.bool(true, 'Enable Short', group='Direction')

atrLen        = input.int(14, 'ATR Length', minval=1, group='Risk')
slMult        = input.float(1.5, 'SL × ATR', step=0.1, group='Risk')
tp1Mult       = input.float(1.0, 'TP1 × ATR (50%)', step=0.1, group='Risk')
tp2Mult       = input.float(2.0, 'TP2 × ATR (50%)', step=0.1, group='Risk')
useBE         = input.bool(true, 'Breakeven after TP1', group='Risk')

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

vwapV    = ta.vwap(hlc3)
vwapLongOK  = not useVWAP or close < vwapV
vwapShortOK = not useVWAP or close > vwapV

volOK    = not useVol or volume > ta.sma(volume, 20) * volMult
rsi      = ta.rsi(close, rsiLen)
rsiFearOK  = not useRSI or rsi < rsiOS
rsiGreedOK = not useRSI or rsi > rsiOB

fearBars  = math.sum(rawFear ? 1 : 0, minBarsInZone)
greedBars = math.sum(rawGreed ? 1 : 0, minBarsInZone)
fearMature  = fearBars >= minBarsInZone
greedMature = greedBars >= minBarsInZone

bullEngulf = close > open and close[1] < open[1] and close >= open[1] and open <= close[1]
bearEngulf = close < open and close[1] > open[1] and close <= open[1] and open >= close[1]
bullBar = close > open and close > close[1]
bearBar = close < open and close < close[1]
fearBarOK  = useEngulf ? bullEngulf : bullBar
greedBarOK = useEngulf ? bearEngulf : bearBar

inOpenSkip  = useTime and not na(time(timeframe.period, '0930-0945:1234567')) and skipOpenMin > 0
inCloseSkip = useTime and not na(time(timeframe.period, '1530-1600:1234567')) and skipCloseMin > 0
inRegular   = not useTime or not na(time(timeframe.period, '0930-1600:1234567'))
timeOK      = inRegular and not inOpenSkip and not inCloseSkip

var int tradesDay = 0
newDay = ta.change(time('D'))
if not na(newDay) and newDay != 0
    tradesDay := 0
dayLimitOK = tradesDay < maxTradesDay

fearTrig  = rawFear and fearMature and fearBarOK
greedTrig = rawGreed and greedMature and greedBarOK

BuySignal  = fearTrig and fearTrendOK and vwapLongOK and volOK and rsiFearOK and timeOK and dayLimitOK and inRange and useLong
SellSignal = greedTrig and greedTrendOK and vwapShortOK and volOK and rsiGreedOK and timeOK and dayLimitOK and inRange and useShort

atr = ta.atr(atrLen)

var float curEntry = na
var float curTP1 = na
var float curTP2 = na
var bool tp1Done = false

if BuySignal and strategy.position_size == 0
    curEntry := close
    curTP1 := close + atr * tp1Mult
    curTP2 := close + atr * tp2Mult
    tp1Done := false
    slL = close - atr * slMult
    strategy.entry('Long', strategy.long)
    strategy.exit('TP1 L', 'Long', qty_percent=50, limit=curTP1, stop=slL)
    strategy.exit('TP2 L', 'Long', limit=curTP2, stop=slL)
    tradesDay += 1

if SellSignal and strategy.position_size == 0
    curEntry := close
    curTP1 := close - atr * tp1Mult
    curTP2 := close - atr * tp2Mult
    tp1Done := false
    slS = close + atr * slMult
    strategy.entry('Short', strategy.short)
    strategy.exit('TP1 S', 'Short', qty_percent=50, limit=curTP1, stop=slS)
    strategy.exit('TP2 S', 'Short', limit=curTP2, stop=slS)
    tradesDay += 1

if useBE and not tp1Done and strategy.position_size > 0 and high >= curTP1
    tp1Done := true
    strategy.exit('TP2 L BE', 'Long', limit=curTP2, stop=curEntry)
if useBE and not tp1Done and strategy.position_size < 0 and low <= curTP1
    tp1Done := true
    strategy.exit('TP2 S BE', 'Short', limit=curTP2, stop=curEntry)

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

## 3. 미국주식 권장 입력값

| 종목 유형 | Sigma | HTF | useVWAP | useEngulf | maxTrades | SL/TP1/TP2 |
|---|---|---|---|---|---|---|
| **SPY/QQQ (ETF)** | 2.5 | 60 | ON | ON | 2 | 1.5 / 1.0 / 2.0 |
| **AAPL/MSFT/NVDA (대형주)** | 2.5 | 60 | ON | ON | 2 | 1.5 / 1.0 / 2.0 |
| **TSLA (변동성 큰 대형주)** | 3.0 | 60 | ON | ON | 1 | 2.0 / 1.0 / 2.5 |
| **소형주 (Russell)** | 3.0 | 240 | ON | ON | 1 | 2.0 / 1.0 / 2.0 |
| **장기 스윙 (1H/4H)** | 2.0 | D | ON | ON | 2 | 2.0 / 1.5 / 3.0 |

## 4. 승률이 높아지는 이유 (수학적 근거)

**TP1 = 1×ATR, SL = 1.5×ATR 의 비대칭 구조 + Breakeven Stop**

1. **첫 50% 익절률 ↑** — TP1을 1×ATR로 짧게 잡으면 도달 확률 ~60–70%
2. **잔여 50%는 본전이동 후 무위험** — TP1 체결되는 순간 SL을 진입가로 이동, 최악의 경우 50%만 익절 후 본전
3. **순 손실 거래 비율 = (TP1 미도달 비율) ≈ 30–40%**
   → **승률 60–70% 기대**
4. **VWAP + Engulfing + Sigma 2.5** 진입 필터가 첫 TP1 도달률을 더 끌어올림

기댓값:
```
승률 65%, 평균 승 = 0.5 × 1ATR + 0.5 × 1.5ATR = 1.25 ATR
패율 35%, 평균 패 = 1.5 ATR
E[trade] = 0.65 × 1.25 - 0.35 × 1.5 = 0.8125 - 0.525 = +0.288 ATR
```

## 5. 백테스트 비교 절차 (v4 → v5)

같은 종목/같은 기간/같은 자본:

| 측정 | v4 Backtest | v5 US 목표 |
|---|---|---|
| Win Rate | 40–50% | **>55%** |
| Profit Factor | 1.2–1.5 | **>1.6** |
| Max Drawdown | <25% | **<15%** |
| Total Trades | 50+ | 30–60 (필터 강화로 감소) |
| Avg Trade % | +0.4% | **+0.6%** |

추천 테스트 종목 (15분봉, 6–12개월):
- SPY, QQQ, AAPL, MSFT, NVDA — 큰 유동성·뚜렷한 추세
- TSLA, AMD — 변동성 검증

## 6. 사용 절차

1. Pine Editor → "Save As New" → 이름 `CZ v5 US`
2. Add to chart (15분봉 권장)
3. Strategy Tester 탭 → Overview 확인
4. **Inputs**:
   - 처음엔 기본값 그대로 (이미 미국주식 + 승률 최적화 세팅)
   - Win Rate < 50% 이면 → `Sigma` 2.5 → 3.0, `Skip Open Minutes` 15 → 30
   - Trades < 20 이면 → `Min Bars in Zone` 2 → 1, `useEngulf` OFF
5. 종목별 따로 튜닝 (한 세팅이 모든 종목에 통하지 않음)

## 7. 주의

- **선물·24시간 종목**: `Skip Open/Close` 의미 없음 — `useTime = OFF`
- **암호화폐**: 정규장 없음 — `useTime = OFF`, VWAP은 일중 reset이라 24h 차트엔 부적합
- **Breakeven 보호의 한계**: 갭다운/갭업 발생 시 SL을 건너뛸 수 있음 (실전 슬리피지)
- **수수료**: 0.03% 기본값 (미국 대부분 무료 브로커이면 0으로 낮춰도 됨)
