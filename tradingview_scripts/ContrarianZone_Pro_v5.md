# Contrarian Zone Pro v5 — 온도계·분할매수·리스크테이블·RS Rating

> 원본: Fearzone / GreedZone © Zeiierman (CC BY-NC-SA 4.0)
> 검증 상태: TradingView 서버 컴파일 통과 (0 errors / 0 warnings)
> 대상: 15분봉 (HTF 1H EMA200 기준)

---

## v4 → v5 신규 기능 4가지

| 기능 | 위치 | 설명 |
|---|---|---|
| ① **온도계 신호** | 우측 상단 테이블 | EXTREME FEAR → FEAR → NEUTRAL → GREED → EXTREME GREED + 권장 액션 |
| ② **분할매수 가이드** | BuySignal 발생 시 차트 | 1매(현재가) / 2매(−0.5ATR) / 3매(−1ATR) 점선 3개 + 가격 라벨 |
| ③ **포지션 리스크 테이블** | 우측 하단 테이블 | 진입가·SL·계좌 리스크·매수 수량·R:R·TP 자동 계산 |
| ④ **RS Rating (1–99)** | 온도계 테이블 내 | SPY 대비 상대강도 점수 (80+ = 강세, 20− = 약세) |

---

## Pine v5 코드 (컴파일 검증 완료)

```pinescript
//@version=5
indicator('Contrarian Zone Pro v5', overlay=true, shorttitle='CZP v5', max_lines_count=500, max_labels_count=500)

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
slMult        = input.float(1.0, 'SL x ATR', step=0.1, group='Risk')
tp1Mult       = input.float(1.5, 'TP1 x ATR', step=0.1, group='Risk')
tp2Mult       = input.float(3.0, 'TP2 x ATR', step=0.1, group='Risk')
acctSize      = input.float(10000, 'Account Size ($)', step=100, group='Risk')
acctRisk      = input.float(1.0, 'Account Risk % per trade', step=0.1, group='Risk')

// ===== Visual =====
showMid       = input.bool(true, 'Show Greed/Fear Midpoint Marker', group='Visual')
showDiv       = input.bool(true, 'Show RSI Divergences', group='Visual')
showBG        = input.bool(true, 'Trade Background Tint', group='Visual')
showThermo    = input.bool(true, 'Show Thermometer Signal', group='Visual')
showDCA       = input.bool(true, 'Show DCA Guide (분할매수)', group='Visual')
showRiskTbl   = input.bool(true, 'Show Position Risk Table', group='Visual')
showRS        = input.bool(true, 'Show RS Rating', group='Visual')
rsBenchmark   = input.symbol('SPY', 'RS Benchmark', group='Visual')
rsLen         = input.int(63, 'RS Period (bars)', minval=10, group='Visual')

// ===== MA helper =====
avg(simple string mt, series float s, simple int l) =>
    switch mt
        'SMA' => ta.sma(s, l)
        'EMA' => ta.ema(s, l)
        'WMA' => ta.wma(s, l)
        'HMA' => ta.hma(s, l)
        'RMA' => ta.rma(s, l)
        => ta.sma(s, l)

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
htfClose     = request.security(syminfo.tickerid, htfTF, close, lookahead=barmerge.lookahead_off)
htfEMA       = request.security(syminfo.tickerid, htfTF, f_ema(trendLen), lookahead=barmerge.lookahead_off)
trendUp      = htfClose > htfEMA
trendDn      = htfClose < htfEMA
fearTrendOK  = not useTrend or trendUp
greedTrendOK = not useTrend or trendDn

// ===== Filters =====
volOK      = not useVol or volume > ta.sma(volume, 20) * volMult
rsi        = ta.rsi(close, rsiLen)
rsiFearOK  = not useRSI or rsi < rsiOS
rsiGreedOK = not useRSI or rsi > rsiOB

// ===== RSI Divergence =====
pivLen  = 5
phPrice = ta.pivothigh(high, pivLen, pivLen)
plPrice = ta.pivotlow(low, pivLen, pivLen)
phRSI   = ta.pivothigh(rsi, pivLen, pivLen)
plRSI   = ta.pivotlow(rsi, pivLen, pivLen)
var float lastPH  = na
var float lastPHR = na
var float lastPL  = na
var float lastPLR = na
bearDiv = false
bullDiv = false
if not na(phPrice)
    if not na(lastPH) and phPrice > lastPH and not na(phRSI) and not na(lastPHR) and phRSI < lastPHR
        bearDiv := true
    lastPH  := phPrice
    lastPHR := phRSI
if not na(plPrice)
    if not na(lastPL) and plPrice < lastPL and not na(plRSI) and not na(lastPLR) and plRSI > lastPLR
        bullDiv := true
    lastPL  := plPrice
    lastPLR := plRSI

// ===== Zone maturity + reversal =====
fearBars    = math.sum(rawFear  ? 1 : 0, minBarsInZone)
greedBars   = math.sum(rawGreed ? 1 : 0, minBarsInZone)
fearMature  = fearBars  >= minBarsInZone
greedMature = greedBars >= minBarsInZone
bullBar = close > open and close > close[1]
bearBar = close < open and close < close[1]

fearTrig  = useConfirm ? (rawFear  and fearMature  and bullBar) : (rawFear  and fearMature)
greedTrig = useConfirm ? (rawGreed and greedMature and bearBar) : (rawGreed and greedMature)

BuySignal  = fearTrig  and fearTrendOK  and volOK and rsiFearOK
SellSignal = greedTrig and greedTrendOK and volOK and rsiGreedOK

// ===== Signal strength =====
buyScore  = (rawFear?1:0)  + (fearMature?1:0)  + (bullBar?1:0) + (volOK?1:0) + (bullDiv?1:0)
sellScore = (rawGreed?1:0) + (greedMature?1:0) + (bearBar?1:0) + (volOK?1:0) + (bearDiv?1:0)

// ===== ATR =====
atr = ta.atr(atrLen)

// ===== RS Rating =====
benchClose  = request.security(rsBenchmark, timeframe.period, close, lookahead=barmerge.lookahead_off)
stockPerf   = not na(close[rsLen]) and close[rsLen] > 0 ? (close - close[rsLen]) / close[rsLen] * 100 : 0.0
benchPerf   = not na(benchClose[rsLen]) and benchClose[rsLen] > 0 ? (benchClose - benchClose[rsLen]) / benchClose[rsLen] * 100 : 0.0
rsRaw       = stockPerf - benchPerf
rsHigh      = ta.highest(rsRaw, 252)
rsLow       = ta.lowest(rsRaw, 252)
rsRange     = rsHigh - rsLow
rsRatingRaw = rsRange > 0 ? math.round(1 + (rsRaw - rsLow) / rsRange * 98) : 50
rsRating    = int(math.max(1, math.min(99, rsRatingRaw)))

// ===== Thermometer value (-100 to +100) =====
thermoBase = (rsi - 50) * 2
thermoVal  = rawGreed ? math.max(thermoBase, 30.0) : rawFear ? math.min(thermoBase, -30.0) : thermoBase

// ===== Trade State Machine =====
var int   posDir   = 0
var float entryPx  = na
var float slPx     = na
var float tp1Px    = na
var float tp2Px    = na
var bool  tp1Hit   = false
var line  slLine   = na
var line  tp1Line  = na
var line  tp2Line  = na

if BuySignal and posDir == 0
    posDir  := 1
    entryPx := close
    slPx    := close - atr * slMult
    tp1Px   := close + atr * tp1Mult
    tp2Px   := close + atr * tp2Mult
    tp1Hit  := false
    slLine  := line.new(bar_index, slPx,  bar_index+1, slPx,  extend=extend.right, color=color.red,   width=2)
    tp1Line := line.new(bar_index, tp1Px, bar_index+1, tp1Px, extend=extend.right, color=color.green, width=1)
    tp2Line := line.new(bar_index, tp2Px, bar_index+1, tp2Px, extend=extend.right, color=color.green, width=2)
    label.new(bar_index, low,  'BUY @ ' + str.tostring(close, format.mintick) + '\nSL: ' + str.tostring(slPx, format.mintick) + '\nTP1: ' + str.tostring(tp1Px, format.mintick) + '\nTP2: ' + str.tostring(tp2Px, format.mintick) + '\n' + str.tostring(buyScore) + '/5', style=label.style_label_up,   color=color.new(color.lime, 0), textcolor=color.black, size=size.small)

if SellSignal and posDir == 0
    posDir  := -1
    entryPx := close
    slPx    := close + atr * slMult
    tp1Px   := close - atr * tp1Mult
    tp2Px   := close - atr * tp2Mult
    tp1Hit  := false
    slLine  := line.new(bar_index, slPx,  bar_index+1, slPx,  extend=extend.right, color=color.red,   width=2)
    tp1Line := line.new(bar_index, tp1Px, bar_index+1, tp1Px, extend=extend.right, color=color.green, width=1)
    tp2Line := line.new(bar_index, tp2Px, bar_index+1, tp2Px, extend=extend.right, color=color.green, width=2)
    label.new(bar_index, high, 'SELL @ ' + str.tostring(close, format.mintick) + '\nSL: ' + str.tostring(slPx, format.mintick) + '\nTP1: ' + str.tostring(tp1Px, format.mintick) + '\nTP2: ' + str.tostring(tp2Px, format.mintick) + '\n' + str.tostring(sellScore) + '/5', style=label.style_label_down, color=color.new(color.red,  0), textcolor=color.white, size=size.small)

// ===== Exit Logic =====
exitReason = ''
exitNow    = false

if posDir == 1
    if low <= slPx
        exitReason := 'SL'
        exitNow    := true
    else if not tp1Hit and high >= tp1Px
        tp1Hit := true
        label.new(bar_index, high, 'TP1\n' + str.tostring(tp1Px, format.mintick), style=label.style_diamond, color=color.aqua, textcolor=color.black, size=size.tiny)
    else if high >= tp2Px
        exitReason := 'TP2'
        exitNow    := true
    else if SellSignal
        exitReason := 'Greed'
        exitNow    := true

if posDir == -1
    if high >= slPx
        exitReason := 'SL'
        exitNow    := true
    else if not tp1Hit and low <= tp1Px
        tp1Hit := true
        label.new(bar_index, low, 'TP1\n' + str.tostring(tp1Px, format.mintick), style=label.style_diamond, color=color.aqua, textcolor=color.black, size=size.tiny)
    else if low <= tp2Px
        exitReason := 'TP2'
        exitNow    := true
    else if BuySignal
        exitReason := 'Fear'
        exitNow    := true

if exitNow
    exitCol  = exitReason == 'SL' ? color.red : exitReason == 'TP2' ? color.green : color.orange
    yPos     = posDir == 1 ? high : low
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

// ===== Zone Candles =====
fzOpen  = rawFear  ? low  - ta.tr : na
fzClose = rawFear  ? low  - 2 * ta.tr : na
plotcandle(fzOpen, fzOpen, fzClose, fzClose, color=#FC6C85, bordercolor=color.red,   title='FearZone')
gzOpen  = rawGreed ? high + ta.tr : na
gzClose = rawGreed ? high + 2 * ta.tr : na
plotcandle(gzOpen, gzOpen, gzClose, gzClose, color=#90EE90, bordercolor=color.green, title='GreedZone')

// ===== Midpoint Markers =====
gzMid = (rawGreed and showMid) ? (high + low) / 2 : na
plot(gzMid, title='Greed Mid', style=plot.style_cross, color=#FFEB00, linewidth=4)
fzMid = (rawFear  and showMid) ? (high + low) / 2 : na
plot(fzMid, title='Fear Mid',  style=plot.style_cross, color=#FFFFFF, linewidth=4)

var float gzMidSum  = 0.0
var int   gzMidCnt  = 0
var line  gzMidLine = na
if rawGreed
    gzMidSum := gzMidSum + (high + low) / 2
    gzMidCnt := gzMidCnt + 1
    avgMid    = gzMidSum / gzMidCnt
    if na(gzMidLine)
        gzMidLine := line.new(bar_index, avgMid, bar_index+1, avgMid, extend=extend.right, color=color.new(#FFEB00, 0), width=2)
    else
        line.set_xy2(gzMidLine, bar_index, avgMid)
        line.set_y1(gzMidLine, avgMid)
if not rawGreed and gzMidCnt > 0
    if not na(gzMidLine)
        line.set_x2(gzMidLine, bar_index)
        line.set_extend(gzMidLine, extend.none)
    gzMidSum  := 0.0
    gzMidCnt  := 0
    gzMidLine := na

var float fzMidSum  = 0.0
var int   fzMidCnt  = 0
var line  fzMidLine = na
if rawFear
    fzMidSum := fzMidSum + (high + low) / 2
    fzMidCnt := fzMidCnt + 1
    avgMidF   = fzMidSum / fzMidCnt
    if na(fzMidLine)
        fzMidLine := line.new(bar_index, avgMidF, bar_index+1, avgMidF, extend=extend.right, color=color.new(#FFFFFF, 0), width=2)
    else
        line.set_xy2(fzMidLine, bar_index, avgMidF)
        line.set_y1(fzMidLine, avgMidF)
if not rawFear and fzMidCnt > 0
    if not na(fzMidLine)
        line.set_x2(fzMidLine, bar_index)
        line.set_extend(fzMidLine, extend.none)
    fzMidSum  := 0.0
    fzMidCnt  := 0
    fzMidLine := na

// ===== DCA Guide (분할매수 가이드) =====
if showDCA and BuySignal
    dcaL1 = close
    dcaL2 = close - atr * 0.5
    dcaL3 = close - atr * 1.0
    line.new(bar_index, dcaL1, bar_index + 40, dcaL1, color=color.new(color.lime,   40), width=1, style=line.style_dashed)
    line.new(bar_index, dcaL2, bar_index + 40, dcaL2, color=color.new(color.yellow, 40), width=1, style=line.style_dashed)
    line.new(bar_index, dcaL3, bar_index + 40, dcaL3, color=color.new(color.orange, 40), width=1, style=line.style_dashed)
    label.new(bar_index, dcaL1, '1매 ' + str.tostring(dcaL1, format.mintick), style=label.style_none, textcolor=color.lime,   size=size.tiny)
    label.new(bar_index, dcaL2, '2매 ' + str.tostring(dcaL2, format.mintick), style=label.style_none, textcolor=color.yellow, size=size.tiny)
    label.new(bar_index, dcaL3, '3매 ' + str.tostring(dcaL3, format.mintick), style=label.style_none, textcolor=color.orange, size=size.tiny)

// ===== Thermometer Table (우측 상단) =====
var table thermoTbl = table.new(position.top_right, 2, 6, border_width=1, frame_color=color.gray, frame_width=1)
if showThermo and barstate.islast
    thermoColor  = thermoVal >= 60 ? color.red : thermoVal >= 20 ? color.orange : thermoVal >= -20 ? color.gray : thermoVal >= -60 ? color.aqua : color.blue
    thermoLabel  = thermoVal >= 60 ? 'EXTREME GREED' : thermoVal >= 20 ? 'GREED' : thermoVal >= -20 ? 'NEUTRAL' : thermoVal >= -60 ? 'FEAR' : 'EXTREME FEAR'
    thermoAction = thermoVal >= 60 ? 'SELL/Short 준비' : thermoVal >= 20 ? '분할 매도' : thermoVal >= -20 ? '관망' : thermoVal >= -60 ? '분할 매수' : '적극 매수'
    rsColor      = rsRating >= 80 ? color.lime : rsRating >= 60 ? color.green : rsRating >= 40 ? color.gray : rsRating >= 20 ? color.orange : color.red
    trendLabel   = trendUp ? 'UP' : 'DOWN'
    trendColor   = trendUp ? color.green : color.red
    rsiDispColor = rsi < rsiOS ? color.lime : rsi > rsiOB ? color.red : color.gray
    table.cell(thermoTbl, 0, 0, '온도계 신호',  bgcolor=color.new(color.black, 10), text_color=color.white,  text_size=size.small)
    table.cell(thermoTbl, 1, 0, thermoLabel,   bgcolor=color.new(thermoColor, 20), text_color=color.white,  text_size=size.small)
    table.cell(thermoTbl, 0, 1, 'Fear/Greed',  bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(thermoTbl, 1, 1, str.tostring(math.round(thermoVal)) + '%', bgcolor=color.new(thermoColor, 50), text_color=color.white, text_size=size.tiny)
    table.cell(thermoTbl, 0, 2, '권장 액션',   bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(thermoTbl, 1, 2, thermoAction,  bgcolor=color.new(thermoColor, 50), text_color=color.white,  text_size=size.tiny)
    table.cell(thermoTbl, 0, 3, 'RS Rating',   bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(thermoTbl, 1, 3, str.tostring(rsRating) + ' / 99', bgcolor=color.new(rsColor, 50), text_color=color.white, text_size=size.tiny)
    table.cell(thermoTbl, 0, 4, 'HTF Trend',   bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(thermoTbl, 1, 4, trendLabel,    bgcolor=color.new(trendColor, 50),  text_color=color.white,  text_size=size.tiny)
    table.cell(thermoTbl, 0, 5, 'RSI',         bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(thermoTbl, 1, 5, str.tostring(math.round(rsi * 10) / 10), bgcolor=color.new(rsiDispColor, 50), text_color=color.white, text_size=size.tiny)

// ===== Position Risk Table (우측 하단) =====
var table riskTbl = table.new(position.bottom_right, 2, 7, border_width=1, frame_color=color.gray, frame_width=1)
if showRiskTbl and barstate.islast
    eP   = posDir != 0 and not na(entryPx) ? entryPx : close
    slP  = posDir != 0 and not na(slPx)   ? slPx    : close - atr * slMult
    tp1P = posDir != 0 and not na(tp1Px)  ? tp1Px   : close + atr * tp1Mult
    tp2P = posDir != 0 and not na(tp2Px)  ? tp2Px   : close + atr * tp2Mult
    riskAmt   = acctSize * acctRisk / 100
    slDist    = math.abs(eP - slP)
    posShares = slDist > 0 ? math.floor(riskAmt / slDist) : 0
    posCost   = posShares * eP
    rr1       = slDist > 0 ? math.round(math.abs(tp1P - eP) / slDist * 100) / 100 : 0.0
    rr2       = slDist > 0 ? math.round(math.abs(tp2P - eP) / slDist * 100) / 100 : 0.0
    dirLabel  = posDir == 1 ? 'LONG' : posDir == -1 ? 'SHORT' : '대기중'
    hdrColor  = posDir == 1 ? color.green : posDir == -1 ? color.red : color.gray
    table.cell(riskTbl, 0, 0, '포지션 리스크', bgcolor=color.new(color.black, 10), text_color=color.white,  text_size=size.small)
    table.cell(riskTbl, 1, 0, dirLabel,        bgcolor=color.new(hdrColor,       30), text_color=color.white,  text_size=size.small)
    table.cell(riskTbl, 0, 1, '진입(예정)가',  bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(riskTbl, 1, 1, str.tostring(eP, format.mintick),  bgcolor=color.new(color.black, 60), text_color=color.white,  text_size=size.tiny)
    table.cell(riskTbl, 0, 2, 'SL 가격',       bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(riskTbl, 1, 2, str.tostring(slP, format.mintick), bgcolor=color.new(color.red,   70), text_color=color.white,  text_size=size.tiny)
    table.cell(riskTbl, 0, 3, '리스크 $',      bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(riskTbl, 1, 3, str.tostring(acctRisk) + '% = $' + str.tostring(math.round(riskAmt)), bgcolor=color.new(color.black, 60), text_color=color.yellow, text_size=size.tiny)
    table.cell(riskTbl, 0, 4, '매수 수량',     bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(riskTbl, 1, 4, str.tostring(posShares) + 'sh ($' + str.tostring(math.round(posCost)) + ')', bgcolor=color.new(color.black, 60), text_color=color.white, text_size=size.tiny)
    table.cell(riskTbl, 0, 5, 'R:R',           bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(riskTbl, 1, 5, '1:' + str.tostring(rr1) + ' / 1:' + str.tostring(rr2), bgcolor=color.new(color.black, 60), text_color=color.yellow, text_size=size.tiny)
    table.cell(riskTbl, 0, 6, 'TP1 / TP2',    bgcolor=color.new(color.black, 40), text_color=color.silver, text_size=size.tiny)
    table.cell(riskTbl, 1, 6, str.tostring(tp1P, format.mintick) + ' / ' + str.tostring(tp2P, format.mintick), bgcolor=color.new(color.green, 70), text_color=color.white, text_size=size.tiny)

// ===== Divergence Markers =====
plotshape(showDiv and bullDiv, title='Bull Div', style=shape.diamond, location=location.belowbar, color=color.lime, size=size.tiny)
plotshape(showDiv and bearDiv, title='Bear Div', style=shape.diamond, location=location.abovebar, color=color.red,  size=size.tiny)

// ===== Background =====
bgcolor(showBG and posDir ==  1 ? color.new(color.green, 92) : na)
bgcolor(showBG and posDir == -1 ? color.new(color.red,   92) : na)

// ===== Alerts =====
alertcondition(BuySignal,                title='BUY',        message='CZP v5: BUY signal')
alertcondition(SellSignal,               title='SELL',       message='CZP v5: SELL signal')
alertcondition(posDir ==  1 and exitNow, title='Long Exit',  message='CZP v5: Long exit')
alertcondition(posDir == -1 and exitNow, title='Short Exit', message='CZP v5: Short exit')
```

---

## 차트에서 보이는 것

| 시각 요소 | 위치 | 의미 |
|---|---|---|
| **온도계 테이블** | 우측 상단 | 현재 시장 온도 + RS Rating + HTF Trend + RSI |
| **포지션 리스크 테이블** | 우측 하단 | 진입가·SL·매수수량·R:R·TP (항상 표시, 대기중엔 예상값) |
| 🟢 1매/🟡 2매/🟠 3매 점선 | BuySignal 발생 시 | 분할매수 3단계 가이드 (−0ATR / −0.5ATR / −1ATR) |
| 🟡 노랑 십자 | GreedZone 봉 중간 | 분할 매도 자리 |
| ⚪ 흰색 십자 | FearZone 봉 중간 | 분할 매수 자리 |
| BUY/SELL 라벨 | 신호 발생 봉 | 진입가 + SL + TP1 + TP2 + 점수/5 |
| TP1 다이아몬드 | 50% 익절 도달 | 잔여 50% 계속 보유 |
| EXIT 라벨 | 청산 봉 | SL / TP2 / 반대신호 사유 |

## 온도계 기준

| 값 | 표시 | 색상 | 권장 액션 |
|---|---|---|---|
| ≥ +60 | EXTREME GREED | 빨강 | SELL/Short 준비 |
| +20 ~ +59 | GREED | 주황 | 분할 매도 |
| −20 ~ +19 | NEUTRAL | 회색 | 관망 |
| −60 ~ −21 | FEAR | 시안 | 분할 매수 |
| ≤ −61 | EXTREME FEAR | 파랑 | 적극 매수 |

## RS Rating 기준

| 점수 | 의미 |
|---|---|
| 80–99 | 시장 대비 강한 상대강도 — 매수 우선 |
| 60–79 | 평균 이상 |
| 40–59 | 중립 |
| 20–39 | 약세 |
| 1–19 | 시장 대비 크게 부진 — 주의 |

## 사용 절차

1. Pine Editor → 코드 전체 복사 → Save As New → `CZP v5`
2. 차트 추가 (15분봉 권장)
3. **Inputs → Risk**: `Account Size`와 `Account Risk %` 본인 계좌에 맞게 설정
4. **Inputs → Visual**: `RS Benchmark` — 미국주식은 `SPY`, 나스닥은 `QQQ`
5. 우측 상단 **온도계** 확인 → FEAR/EXTREME FEAR 구간에서만 매수 진입
6. BuySignal 발생 시 **1매/2매/3매 점선**을 분할 매수 가이드로 활용
7. 우측 하단 **리스크 테이블**에서 매수 수량 확인 후 주문
