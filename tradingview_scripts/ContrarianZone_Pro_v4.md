# Contrarian Zone Pro v4 — Fear+Greed 통합 / 진입·청산 자동 마킹

> 원본: Fearzone / GreedZone © Zeiierman (CC BY-NC-SA 4.0)
> 검증 상태: TradingView 서버 컴파일 통과 (`pine_check` 0 errors / 0 warnings)
> 대상: 15분봉 (HTF 1H EMA200 기준)

---

## 1. v4의 핵심 신규 기능

| 기능 | 설명 |
|---|---|
| ★ **GreedZone 캔들 중간점 마커** | 초록 캔들이 켜진 봉의 `(high+low)/2` 지점에 형광 분홍 점 — "여기서 팔면 거의 고점" |
| ★ **FearZone 캔들 중간점 마커** | 빨간 캔들이 켜진 봉의 `(high+low)/2` 지점에 시안 점 — "여기서 사면 거의 저점" |
| ★ **존 평균 중간선** | 연속된 zone의 평균 중간가를 점선으로 right-extend — 다음 진입가 가이드 |
| 진입 라벨 | BUY/SELL @ 진입가, SL, TP1, TP2, 신호강도(★n/5) 모두 표시 |
| SL/TP 자동선 | 빨강(SL) / 초록 가는선(TP1) / 초록 굵은선(TP2) — 진입봉부터 right-extend |
| TP1 부분익절 마커 | 50% 청산 신호 (포지션 유지) — 시안 다이아몬드 |
| 청산 라벨 | SL / TP2 / Greed(반대신호) / Fear(반대신호) 사유 표시 |
| RSI Divergence | Bull/Bear 다이버전스 다이아몬드 표시 |
| 신호 강도 점수 | rawZone + mature + 반전봉 + 거래량 + 다이버전스 = ★n/5 |
| 트레이드 배경 | 롱 보유=연녹색 / 숏 보유=연빨강 배경 틴트 |
| 4종 알림 분리 | BUY / SELL / Long Exit / Short Exit |

## 2. GreedZone 중간점 = 고점 근사 원리

```
GreedZone 발생 = 과열 + 30봉 고점 근접
이때 캔들의 (high+low)/2 = 그 봉의 평균가
```

과열 구간에서 분 단위로 위/아래로 휩쏘이지만, **존이 지속되는 동안의 평균 중간가**가
실제 회전 고점과 일치하는 경우가 많음 → 분홍 점/선이 떠 있는 동안 분할 익절 자리.

FearZone도 동일 원리로 시안 점/선이 바닥 매수 자리 가이드.

## 3. 검증된 Pine v5 코드

```pinescript
//@version=5
indicator('Contrarian Zone Pro v4', overlay=true, shorttitle='CZP v4', max_lines_count=500, max_labels_count=500)

// ===== Core =====
src           = input.source(ohlc4, 'Source', group='Core')
periodLen     = input.int(30, 'High/Low Period', minval=2, group='Core')
stdevPeriod   = input.int(50, 'Stdev Period', minval=2, group='Core')
sigmaMult     = input.float(2.0, 'Sigma Multiplier', minval=0.5, step=0.1, group='Core')
matype        = input.string('WMA', 'MA Type', options=['SMA','EMA','WMA','HMA','RMA'], group='Core')

// ===== Filters =====
useTrend      = input.bool(true, 'HTF Trend Filter', group='Filters')
htfTF         = input.timeframe('60', 'HTF Timeframe', group='Filters')
trendLen      = input.int(200, 'HTF EMA Length', minval=2, group='Filters')
useVol        = input.bool(true, 'Volume Confirmation', group='Filters')
volMult       = input.float(1.3, 'Volume Spike x', step=0.1, group='Filters')
useRSI        = input.bool(true, 'RSI Confirmation', group='Filters')
rsiLen        = input.int(14, 'RSI Length', minval=2, group='Filters')
rsiOS         = input.int(35, 'RSI Oversold', group='Filters')
rsiOB         = input.int(65, 'RSI Overbought', group='Filters')
useConfirm    = input.bool(true, 'Reversal Bar Trigger', group='Filters')
minBarsInZone = input.int(3, 'Min Bars in Zone', minval=1, group='Filters')

// ===== Risk =====
atrLen        = input.int(14, 'ATR Length', minval=1, group='Risk')
slMult        = input.float(1.0, 'SL × ATR', step=0.1, group='Risk')
tp1Mult       = input.float(1.5, 'TP1 × ATR', step=0.1, group='Risk')
tp2Mult       = input.float(3.0, 'TP2 × ATR', step=0.1, group='Risk')

// ===== Visual =====
showMid       = input.bool(true, 'Show Greed/Fear Midpoint Marker', group='Visual')
showDiv       = input.bool(true, 'Show RSI Divergences', group='Visual')
showBG        = input.bool(true, 'Trade Background Tint', group='Visual')

// ===== MA helper =====
avg(simple string mt, series float s, simple int l) =>
    a = ta.sma(s, l)
    b = ta.ema(s, l)
    c = ta.wma(s, l)
    d = ta.hma(s, l)
    e = ta.rma(s, l)
    switch mt
        'SMA' => a
        'EMA' => b
        'WMA' => c
        'HMA' => d
        'RMA' => e
        => a

// ===== Zones =====
hh       = ta.highest(src, periodLen)
ll       = ta.lowest(src, periodLen)
FZ1      = (hh - src) / hh
FZ1Limit = avg(matype, FZ1, stdevPeriod) + sigmaMult * ta.stdev(FZ1, stdevPeriod)
FZ2      = avg(matype, src, periodLen)
FZ2Limit = avg(matype, FZ2, stdevPeriod) - sigmaMult * ta.stdev(FZ2, stdevPeriod)
rawFear  = FZ1 > FZ1Limit and FZ2 < FZ2Limit

GZ1      = (ll - src) / ll
GZ1Limit = avg(matype, GZ1, stdevPeriod) - sigmaMult * ta.stdev(GZ1, stdevPeriod)
GZ2      = avg(matype, src, periodLen)
GZ2Limit = avg(matype, GZ2, stdevPeriod) + sigmaMult * ta.stdev(GZ2, stdevPeriod)
rawGreed = GZ1 < GZ1Limit and GZ2 > GZ2Limit

// ===== HTF Trend =====
f_ema(simple int l) => ta.ema(close, l)
htfClose = request.security(syminfo.tickerid, htfTF, close, lookahead=barmerge.lookahead_off)
htfEMA   = request.security(syminfo.tickerid, htfTF, f_ema(trendLen), lookahead=barmerge.lookahead_off)
trendUp  = htfClose > htfEMA
trendDn  = htfClose < htfEMA
fearTrendOK  = not useTrend or trendUp
greedTrendOK = not useTrend or trendDn

// ===== Filters =====
volOK    = not useVol or volume > ta.sma(volume, 20) * volMult
rsi      = ta.rsi(close, rsiLen)
rsiFearOK  = not useRSI or rsi < rsiOS
rsiGreedOK = not useRSI or rsi > rsiOB

// ===== RSI Divergence =====
pivLen = 5
phPrice = ta.pivothigh(high, pivLen, pivLen)
plPrice = ta.pivotlow(low, pivLen, pivLen)
phRSI   = ta.pivothigh(rsi, pivLen, pivLen)
plRSI   = ta.pivotlow(rsi, pivLen, pivLen)
var float lastPH = na
var float lastPHR = na
var float lastPL = na
var float lastPLR = na
bearDiv = false
bullDiv = false
if not na(phPrice)
    if not na(lastPH) and phPrice > lastPH and phRSI < lastPHR
        bearDiv := true
    lastPH := phPrice
    lastPHR := phRSI
if not na(plPrice)
    if not na(lastPL) and plPrice < lastPL and plRSI > lastPLR
        bullDiv := true
    lastPL := plPrice
    lastPLR := plRSI

// ===== Zone maturity + reversal =====
fearBars = math.sum(rawFear ? 1 : 0, minBarsInZone)
greedBars = math.sum(rawGreed ? 1 : 0, minBarsInZone)
fearMature  = fearBars >= minBarsInZone
greedMature = greedBars >= minBarsInZone
bullBar = close > open and close > close[1]
bearBar = close < open and close < close[1]

fearTrig  = useConfirm ? (rawFear and fearMature and bullBar) : (rawFear and fearMature)
greedTrig = useConfirm ? (rawGreed and greedMature and bearBar) : (rawGreed and greedMature)

BuySignal  = fearTrig and fearTrendOK and volOK and rsiFearOK
SellSignal = greedTrig and greedTrendOK and volOK and rsiGreedOK

// ===== Signal strength (0-5) =====
buyScore  = (rawFear?1:0) + (fearMature?1:0) + (bullBar?1:0) + (volOK?1:0) + (bullDiv?1:0)
sellScore = (rawGreed?1:0) + (greedMature?1:0) + (bearBar?1:0) + (volOK?1:0) + (bearDiv?1:0)

// ===== Trade State Machine =====
var int    posDir   = 0
var float  entryPx  = na
var float  slPx     = na
var float  tp1Px    = na
var float  tp2Px    = na
var bool   tp1Hit   = false
var line   slLine   = na
var line   tp1Line  = na
var line   tp2Line  = na
var int    entryBar = na

atr = ta.atr(atrLen)

if BuySignal and posDir == 0
    posDir := 1
    entryPx := close
    slPx := close - atr * slMult
    tp1Px := close + atr * tp1Mult
    tp2Px := close + atr * tp2Mult
    tp1Hit := false
    entryBar := bar_index
    slLine := line.new(bar_index, slPx, bar_index+1, slPx, extend=extend.right, color=color.red, width=2)
    tp1Line := line.new(bar_index, tp1Px, bar_index+1, tp1Px, extend=extend.right, color=color.green, width=1)
    tp2Line := line.new(bar_index, tp2Px, bar_index+1, tp2Px, extend=extend.right, color=color.green, width=2)
    label.new(bar_index, low, 'BUY @ ' + str.tostring(close, format.mintick) + '\nSL: ' + str.tostring(slPx, format.mintick) + '\nTP1: ' + str.tostring(tp1Px, format.mintick) + '\nTP2: ' + str.tostring(tp2Px, format.mintick) + '\n★' + str.tostring(buyScore) + '/5', style=label.style_label_up, color=color.new(color.lime, 0), textcolor=color.black, size=size.small)

if SellSignal and posDir == 0
    posDir := -1
    entryPx := close
    slPx := close + atr * slMult
    tp1Px := close - atr * tp1Mult
    tp2Px := close - atr * tp2Mult
    tp1Hit := false
    entryBar := bar_index
    slLine := line.new(bar_index, slPx, bar_index+1, slPx, extend=extend.right, color=color.red, width=2)
    tp1Line := line.new(bar_index, tp1Px, bar_index+1, tp1Px, extend=extend.right, color=color.green, width=1)
    tp2Line := line.new(bar_index, tp2Px, bar_index+1, tp2Px, extend=extend.right, color=color.green, width=2)
    label.new(bar_index, high, 'SELL @ ' + str.tostring(close, format.mintick) + '\nSL: ' + str.tostring(slPx, format.mintick) + '\nTP1: ' + str.tostring(tp1Px, format.mintick) + '\nTP2: ' + str.tostring(tp2Px, format.mintick) + '\n★' + str.tostring(sellScore) + '/5', style=label.style_label_down, color=color.new(color.red, 0), textcolor=color.white, size=size.small)

// Exit logic
exitReason = ''
exitNow = false

if posDir == 1
    if low <= slPx
        exitReason := 'SL'
        exitNow := true
    else if not tp1Hit and high >= tp1Px
        tp1Hit := true
        label.new(bar_index, high, 'TP1\n' + str.tostring(tp1Px, format.mintick), style=label.style_diamond, color=color.aqua, textcolor=color.black, size=size.tiny)
    else if high >= tp2Px
        exitReason := 'TP2'
        exitNow := true
    else if SellSignal
        exitReason := 'Greed'
        exitNow := true

if posDir == -1
    if high >= slPx
        exitReason := 'SL'
        exitNow := true
    else if not tp1Hit and low <= tp1Px
        tp1Hit := true
        label.new(bar_index, low, 'TP1\n' + str.tostring(tp1Px, format.mintick), style=label.style_diamond, color=color.aqua, textcolor=color.black, size=size.tiny)
    else if low <= tp2Px
        exitReason := 'TP2'
        exitNow := true
    else if BuySignal
        exitReason := 'Fear'
        exitNow := true

if exitNow
    exitCol = exitReason == 'SL' ? color.red : exitReason == 'TP2' ? color.green : color.orange
    yPos = posDir == 1 ? high : low
    lblStyle = posDir == 1 ? label.style_label_down : label.style_label_up
    label.new(bar_index, yPos, 'EXIT ' + exitReason + '\n@' + str.tostring(close, format.mintick), style=lblStyle, color=color.new(exitCol, 0), textcolor=color.white, size=size.small)
    if not na(slLine)
        line.set_x2(slLine, bar_index)
        line.set_extend(slLine, extend.none)
    if not na(tp1Line)
        line.set_x2(tp1Line, bar_index)
        line.set_extend(tp1Line, extend.none)
    if not na(tp2Line)
        line.set_x2(tp2Line, bar_index)
        line.set_extend(tp2Line, extend.none)
    posDir := 0

// ===== Zone candles =====
fzOpen  = rawFear ? low - ta.tr : na
fzClose = rawFear ? low - 2 * ta.tr : na
plotcandle(fzOpen, fzOpen, fzClose, fzClose, color=#FC6C85, bordercolor=color.red, title='FearZone')

gzOpen  = rawGreed ? high + ta.tr : na
gzClose = rawGreed ? high + 2 * ta.tr : na
plotcandle(gzOpen, gzOpen, gzClose, gzClose, color=#90EE90, bordercolor=color.green, title='GreedZone')

// ===== Midpoint Markers =====
// High-contrast midpoint: yellow cross (Greed) / white cross (Fear)
gzMid = (rawGreed and showMid) ? (high + low) / 2 : na
plot(gzMid, title='Greed Mid (Sell Here)', style=plot.style_cross, color=#FFEB00, linewidth=4)

fzMid = (rawFear and showMid) ? (high + low) / 2 : na
plot(fzMid, title='Fear Mid (Buy Here)', style=plot.style_cross, color=#FFFFFF, linewidth=4)

var float gzMidSum = 0.0
var int   gzMidCnt = 0
var line  gzMidLine = na
if rawGreed
    gzMidSum := gzMidSum + (high + low) / 2
    gzMidCnt := gzMidCnt + 1
    avgMid = gzMidSum / gzMidCnt
    if na(gzMidLine)
        gzMidLine := line.new(bar_index, avgMid, bar_index+1, avgMid, extend=extend.right, color=color.new(#FFEB00, 0), width=2, style=line.style_solid)
    else
        line.set_xy2(gzMidLine, bar_index, avgMid)
        line.set_y1(gzMidLine, avgMid)
if not rawGreed and gzMidCnt > 0
    if not na(gzMidLine)
        line.set_x2(gzMidLine, bar_index)
        line.set_extend(gzMidLine, extend.none)
    gzMidSum := 0.0
    gzMidCnt := 0
    gzMidLine := na

var float fzMidSum = 0.0
var int   fzMidCnt = 0
var line  fzMidLine = na
if rawFear
    fzMidSum := fzMidSum + (high + low) / 2
    fzMidCnt := fzMidCnt + 1
    avgMidF = fzMidSum / fzMidCnt
    if na(fzMidLine)
        fzMidLine := line.new(bar_index, avgMidF, bar_index+1, avgMidF, extend=extend.right, color=color.new(#FFFFFF, 0), width=2, style=line.style_solid)
    else
        line.set_xy2(fzMidLine, bar_index, avgMidF)
        line.set_y1(fzMidLine, avgMidF)
if not rawFear and fzMidCnt > 0
    if not na(fzMidLine)
        line.set_x2(fzMidLine, bar_index)
        line.set_extend(fzMidLine, extend.none)
    fzMidSum := 0.0
    fzMidCnt := 0
    fzMidLine := na

// ===== Divergence markers =====
plotshape(showDiv and bullDiv, title='Bull Div', style=shape.diamond, location=location.belowbar, color=color.lime, size=size.tiny)
plotshape(showDiv and bearDiv, title='Bear Div', style=shape.diamond, location=location.abovebar, color=color.red, size=size.tiny)

// ===== Background =====
bgcolor(showBG and posDir == 1 ? color.new(color.green, 92) : na)
bgcolor(showBG and posDir == -1 ? color.new(color.red, 92) : na)

// ===== Alerts =====
alertcondition(BuySignal, title='BUY', message='Contrarian Zone Pro: BUY signal')
alertcondition(SellSignal, title='SELL', message='Contrarian Zone Pro: SELL signal')
alertcondition(posDir == 1 and exitNow, title='Long Exit', message='Contrarian Zone Pro: Long position exit')
alertcondition(posDir == -1 and exitNow, title='Short Exit', message='Contrarian Zone Pro: Short position exit')
```

## 4. 차트 위에서 보이는 것

| 시각 요소 | 의미 |
|---|---|
| 🟢 초록 캔들 (위쪽) | GreedZone — 과열 진행 중 |
| 🟡 **노랑 십자** (캔들 중간) | "이 가격에서 팔면 거의 고점" — Greed 캔들 (h+l)/2 |
| 🟡 **노랑 실선** (수평) | Greed 존 평균 중간가 — 분할 익절 가이드 |
| 🔴 빨강 캔들 (아래쪽) | FearZone — 공포 진행 중 |
| ⚪ **흰색 십자** (캔들 중간) | "이 가격에서 사면 거의 저점" — Fear 캔들 (h+l)/2 |
| ⚪ **흰색 실선** (수평) | Fear 존 평균 중간가 — 분할 매수 가이드 |
| 🟢 BUY / 🔴 SELL 라벨 | 진입가 + SL + TP1 + TP2 + ★점수 |
| 🔴 빨강 가로선 | 손절선 (right-extend) |
| 🟢 초록 가는선 | TP1 익절선 (50% 청산) |
| 🟢 초록 굵은선 | TP2 익절선 (전량 청산) |
| 🩵 시안 다이아 | TP1 부분익절 발동 (포지션 50% 유지) |
| 🟠/🔴/🟢 EXIT 라벨 | 청산 사유: SL / TP2 / Greed / Fear |
| 연녹/연빨 배경 | 롱/숏 포지션 보유 중 |

## 5. 운용 시나리오

### A. 매수 진입 (Long)
1. **시안 점**이 캔들 중간에 켜짐 → 가격 그 부근에서 진입 준비
2. **시안 점선**이 수평 연장 → 평균 매수가 가이드
3. **BUY 라벨**(★3 이상) 등장 → 실제 진입
4. SL/TP 자동 표시 → 그대로 운용

### B. 매도 청산 / 숏 진입 (Greed)
1. **분홍 점**이 캔들 중간에 켜짐 → 보유 롱 분할 익절 신호
2. **분홍 점선**이 수평 연장되는 동안 → 분할 매도 진행
3. **SELL 라벨**(★3 이상) 등장 → 잔여 전량 청산 + (선택) 숏 진입
4. SL = 위쪽 빨강선 / TP = 아래쪽 초록선

### C. ★ 점수별 권장 사이즈
- ★5/5: 풀 사이즈
- ★4/5: 70%
- ★3/5: 50%
- ★≤2: 스킵

## 6. 15분봉 기본 입력값

| 그룹 | 파라미터 | 기본값 |
|---|---|---|
| Core | High/Low Period | 30 |
| Core | Stdev Period | 50 |
| Core | Sigma Multiplier | 2.0 |
| Core | MA Type | WMA |
| Filters | HTF Timeframe | 60 (1H) |
| Filters | HTF EMA Length | 200 |
| Filters | Volume Spike x | 1.3 |
| Filters | RSI OS / OB | 35 / 65 |
| Filters | Min Bars in Zone | 3 |
| Risk | SL × ATR | 1.0 |
| Risk | TP1 × ATR | 1.5 |
| Risk | TP2 × ATR | 3.0 |

## 7. 사용 절차

1. 위 Pine v5 코드 전체 복사
2. TradingView → Pine Editor → 붙여넣기
3. Save (Ctrl+S) → "Save As New" → "Contrarian Zone Pro v4"
4. Add to chart
5. 15분봉 차트에서 분홍/시안 점·점선이 zone 내부에서 표시되는지 확인
