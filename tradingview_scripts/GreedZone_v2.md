# GreedZone v3 — 정확도 개선 + 컴파일 검증 완료

> 원본: [GreedZone indicator (Zeiierman) - Contrarian Indicator](https://kr.tradingview.com/script/JMFy63DG-GreedZone-indicator-Contrarian-Indicator/)
> 라이선스: CC BY-NC-SA 4.0 © Zeiierman (개선판도 동일 라이선스 승계)
> **검증 상태**: TradingView 서버 컴파일 통과 (`pine_check` 0 errors / 0 warnings)

---

## 1. 원본 로직 요약

```
GZ1 = (lowest(src,30) - src) / lowest(src,30)     // 30봉 저점 대비 상승률 (음수)
GZ2 = WMA(src, 30)                                 // 가격 평활화

Greedzone = (GZ1 < AVG1 - 1σ) AND (GZ2 > AVG2 + 1σ)
```

## 2. 원본의 7가지 정확도 문제

| # | 문제 | 실전 영향 |
|---|---|---|
| 1 | 추세 필터 부재 | 강한 상승추세에서 항상 ON → 조기 청산/숏 트랩 |
| 2 | 1σ 임계값 | 너무 자주 발생 → 진짜 과열(2σ↑)과 구분 불가 |
| 3 | 거래량 무시 | 클라이맥스 톱의 거래량 폭증 미반영 |
| 4 | 모멘텀 확인 없음 | RSI 과매수/약세 다이버전스 없는 단순 상승도 신호로 잡힘 |
| 5 | Zone ≠ 진입신호 | 첫 음봉 반전을 안 줌 |
| 6 | HTF 컨텍스트 없음 | 상위 추세에 따라 의미가 완전히 다름 |
| 7 | 알림 entry=exit 동일 | 신호 종류 구분 불가 |

## 3. v3에서 모두 해결

- ✅ HTF EMA200 추세필터 (상승추세에서 숏 차단)
- ✅ Long 청산 전용 모드 (`exitOnlyMode`)
- ✅ Sigma 배수 입력화 (기본 2σ)
- ✅ 거래량 스파이크 확인
- ✅ RSI 과매수 확인 (> 65)
- ✅ 반전봉 트리거 (Zone 성숙 + 첫 음봉)
- ✅ 알림 3종 분리

## 4. 검증된 Pine v5 코드

```pinescript
//@version=5
indicator('GreedZone v3', overlay=true, shorttitle='GreedZoneV3')

src           = input.source(ohlc4, 'Source', group='Core')
lowPeriod     = input.int(30, 'Low Period', minval=2, group='Core')
stdevPeriod   = input.int(50, 'Stdev Period', minval=2, group='Core')
sigmaMult     = input.float(2.0, 'Sigma Multiplier', minval=0.5, step=0.1, group='Core')
matype        = input.string('WMA', 'MA Type', options=['SMA','EMA','WMA','HMA','RMA'], group='Core')

useTrend      = input.bool(true, 'HTF Trend Filter (downtrend only)', group='Filters')
htfTF         = input.timeframe('60', 'HTF Timeframe', group='Filters')
trendLen      = input.int(200, 'HTF EMA Length', minval=2, group='Filters')

useVol        = input.bool(true, 'Volume Confirmation', group='Filters')
volMult       = input.float(1.3, 'Volume Spike x', step=0.1, group='Filters')

useRSI        = input.bool(true, 'RSI Overbought Confirmation', group='Filters')
rsiLen        = input.int(14, 'RSI Length', minval=2, group='Filters')
rsiOB         = input.int(65, 'RSI Overbought Level', group='Filters')

useConfirm    = input.bool(true, 'Reversal Bar Trigger', group='Filters')
minBarsInZone = input.int(3, 'Min Bars in Zone', minval=1, group='Filters')
exitOnlyMode  = input.bool(false, 'Long-Exit Only Mode', group='Filters')

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

ll       = ta.lowest(src, lowPeriod)
GZ1      = (ll - src) / ll
GZ1Limit = avg(matype, GZ1, stdevPeriod) - sigmaMult * ta.stdev(GZ1, stdevPeriod)
GZ2      = avg(matype, src, lowPeriod)
GZ2Limit = avg(matype, GZ2, stdevPeriod) + sigmaMult * ta.stdev(GZ2, stdevPeriod)
rawGreed = GZ1 < GZ1Limit and GZ2 > GZ2Limit

f_ema(simple int l) => ta.ema(close, l)
htfClose = request.security(syminfo.tickerid, htfTF, close, lookahead=barmerge.lookahead_off)
htfEMA   = request.security(syminfo.tickerid, htfTF, f_ema(trendLen), lookahead=barmerge.lookahead_off)
trendOK  = exitOnlyMode or not useTrend or htfClose < htfEMA

volOK    = not useVol or volume > ta.sma(volume, 20) * volMult
rsiOK    = not useRSI or ta.rsi(close, rsiLen) > rsiOB

barsIn      = math.sum(rawGreed ? 1 : 0, minBarsInZone)
zoneMature  = barsIn >= minBarsInZone
bearBar     = close < open and close < close[1]
trigger     = useConfirm ? (rawGreed and zoneMature and bearBar) : (rawGreed and zoneMature)

GreedZone = rawGreed
Signal    = trigger and trendOK and volOK and rsiOK

gzOpen  = GreedZone ? high + ta.tr : na
gzClose = GreedZone ? high + 2 * ta.tr : na
plotcandle(gzOpen, gzOpen, gzClose, gzClose, color=#90EE90, bordercolor=color.green, title='GreedZone')

plotshape(Signal and exitOnlyMode, title='Confirmed Exit', style=shape.triangledown, location=location.abovebar, size=size.small, color=color.orange, text='EXIT')
plotshape(Signal and not exitOnlyMode, title='Confirmed Short', style=shape.triangledown, location=location.abovebar, size=size.small, color=color.red, text='SELL')

alertcondition(Signal, title='Confirmed Greed Signal', message='GreedZone v3: filtered signal')
alertcondition(GreedZone and not GreedZone[1], title='Zone Enter', message='GreedZone Entered')
alertcondition(not GreedZone and GreedZone[1], title='Zone Exit', message='GreedZone Exited')
```

## 5. 운용 모드

### Mode A: Long 청산 전용 (안전)
- `Long-Exit Only Mode = ON`
- Fearzone v3로 매수한 포지션의 익절 시점 알림용
- 숏 진입은 안 함
- HTF 추세 무관하게 신호 발생

### Mode B: 양방향 (Fearzone과 페어)
- `Long-Exit Only Mode = OFF`
- `HTF Trend Filter = ON`
- HTF 하락추세에서만 SELL 신호
- Long 청산 + Short 진입 둘 다 가능

### Mode C: Aggressive (숙련자만)
- `Long-Exit Only Mode = OFF`
- `HTF Trend Filter = OFF`
- 모든 과열 구간에서 숏 진입 — 상승추세에서도 작동 (위험)

## 6. 15분봉 추천 입력값

| 파라미터 | 보수 (스윙) | **기본 (권장)** | 공격 (스캘핑) |
|---|---|---|---|
| Low Period | 144 | **30** | 64 |
| Stdev Period | 200 | **50** | 96 |
| Sigma Multiplier | 2.5 | **2.0** | 1.5 |
| HTF Timeframe | 240 (4H) | **60 (1H)** | 60 |
| HTF EMA Length | 200 | **200** | 100 |
| Volume Spike x | 1.5 | **1.3** | 1.2 |
| RSI Overbought | 70 | **65** | 60 |
| Min Bars in Zone | 4 | **3** | 2 |
| Long-Exit Only | ON | **OFF** | OFF |

## 7. Fearzone + GreedZone 페어 운용

```
[Fearzone BUY 신호] → 진입
   ↓ 보유
[GreedZone EXIT 신호] → 익절 청산 (50%)
   ↓ 잔여 보유
[가격이 HTF EMA50 하회 or GreedZone SELL] → 전량 청산
   ↓
[HTF 하락추세 진입 시] GreedZone SHORT 활성화
   ↓
[Fearzone BUY] → 숏 청산
```

## 8. 손익 가이드 (숏 진입 시, 15m ATR 기준)

- 손절: 진입가 + ATR(14) × 1.0
- 1차 익절: 진입가 - ATR × 1.5 → 50% 청산
- 2차 익절: Fearzone 신호 발생 시 or 4H EMA50 도달

## 9. 사용 절차

1. 위 Pine v5 코드 블록 전체 복사
2. TradingView → Pine Editor 열기 → 붙여넣기
3. **Save (Ctrl+S)** → "Save As New" → 이름 지정
4. **Add to chart**
5. 차트에 초록 캔들(Zone) + 빨강/주황 삼각형(Signal) 표시 확인
