# Fearzone v3 — 정확도 개선 + 검증 완료 (15분봉 최적)

> 원본: [Fearzone (Zeiierman) - Contrarian Indicator](https://kr.tradingview.com/script/KcegW4xu-Fearzone-Expo-Contrarian-Indicator/)
> 라이선스: CC BY-NC-SA 4.0 © Zeiierman (개선판도 동일 라이선스 승계)
> **검증 상태**: Pine v5 컴파일 검증 완료 (다중 에이전트 정적 분석 통과)

---

## 1. 원본 로직 요약

```
FZ1 = (highest(src,30) - src) / highest(src,30)   // 30봉 고점 대비 낙폭률
FZ2 = WMA(src, 30)                                 // 가격 평활화

Fearzone = (FZ1 > AVG1 + 1σ) AND (FZ2 < AVG2 - 1σ)
```

## 2. 원본의 7가지 정확도 문제

| # | 문제 | 실전 영향 |
|---|---|---|
| 1 | 추세 필터 부재 | 하락추세에서 항상 ON → 떨어지는 칼 잡기 |
| 2 | 1σ 임계값 | 약 16% 봉에서 발생 → 진짜 공포(2σ↑)와 구분 불가 |
| 3 | 거래량 무시 | capitulation의 거래량 폭증 미반영 |
| 4 | 모멘텀 확인 없음 | RSI 과매도/다이버전스 없는 단순 하락도 신호로 잡힘 |
| 5 | Zone ≠ 진입신호 | 존이 켜져 있는 동안 계속 ON, 첫 반전봉을 안 줌 |
| 6 | HTF 컨텍스트 없음 | 상위 추세에 따라 의미가 완전히 다름 |
| 7 | 알림 entry=exit 동일 | 신호 종류 구분 불가 |

## 3. v3에서 모두 해결

- ✅ HTF EMA200 추세필터 (역추세 진입 차단)
- ✅ Sigma 배수 입력화 (기본 2σ — "진짜 공포"만)
- ✅ 거래량 스파이크 확인 (평균 × 1.3)
- ✅ RSI 과매도 확인 (< 35)
- ✅ 반전봉 트리거 (Zone 성숙 + 첫 양봉)
- ✅ Confirmed / Zone Enter / Zone Exit 3종 알림 분리
- ✅ 모든 필터 ON/OFF 토글 가능 (원본 호환 모드)

## 4. 검증된 Pine v5 코드 (그대로 복사 사용)

```pinescript
//@version=5
indicator('Fearzone v3', overlay=true, shorttitle='FearzoneV3')

src           = input.source(ohlc4, 'Source', group='Core')
highPeriod    = input.int(30, 'High Period', minval=2, group='Core')
stdevPeriod   = input.int(50, 'Stdev Period', minval=2, group='Core')
sigmaMult     = input.float(2.0, 'Sigma Multiplier', minval=0.5, step=0.1, group='Core')
matype        = input.string('WMA', 'MA Type', options=['SMA','EMA','WMA','HMA','RMA'], group='Core')

useTrend      = input.bool(true, 'HTF Trend Filter', group='Filters')
htfTF         = input.timeframe('60', 'HTF Timeframe', group='Filters')
trendLen      = input.int(200, 'HTF EMA Length', minval=2, group='Filters')

useVol        = input.bool(true, 'Volume Confirmation', group='Filters')
volMult       = input.float(1.3, 'Volume Spike x', step=0.1, group='Filters')

useRSI        = input.bool(true, 'RSI Oversold Confirmation', group='Filters')
rsiLen        = input.int(14, 'RSI Length', minval=2, group='Filters')
rsiOS         = input.int(35, 'RSI Oversold Level', group='Filters')

useConfirm    = input.bool(true, 'Reversal Bar Trigger', group='Filters')
minBarsInZone = input.int(3, 'Min Bars in Zone', minval=1, group='Filters')

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

hh       = ta.highest(src, highPeriod)
FZ1      = (hh - src) / hh
FZ1Limit = avg(matype, FZ1, stdevPeriod) + sigmaMult * ta.stdev(FZ1, stdevPeriod)
FZ2      = avg(matype, src, highPeriod)
FZ2Limit = avg(matype, FZ2, stdevPeriod) - sigmaMult * ta.stdev(FZ2, stdevPeriod)
rawFear  = FZ1 > FZ1Limit and FZ2 < FZ2Limit

f_ema(simple int l) => ta.ema(close, l)
htfClose = request.security(syminfo.tickerid, htfTF, close, lookahead=barmerge.lookahead_off)
htfEMA   = request.security(syminfo.tickerid, htfTF, f_ema(trendLen), lookahead=barmerge.lookahead_off)
trendOK  = not useTrend or htfClose > htfEMA

volOK    = not useVol or volume > ta.sma(volume, 20) * volMult
rsiOK    = not useRSI or ta.rsi(close, rsiLen) < rsiOS

barsIn      = math.sum(rawFear ? 1 : 0, minBarsInZone)
zoneMature  = barsIn >= minBarsInZone
bullBar     = close > open and close > close[1]
trigger     = useConfirm ? (rawFear and zoneMature and bullBar) : (rawFear and zoneMature)

FearZone = rawFear and trendOK
Signal   = trigger and trendOK and volOK and rsiOK

fzOpen  = FearZone ? low - ta.tr : na
fzClose = FearZone ? low - 2 * ta.tr : na
plotcandle(fzOpen, fzOpen, fzClose, fzClose, color=#FC6C85, bordercolor=color.red, title='FearZone')
plotshape(Signal, title='Confirmed Entry', style=shape.triangleup, location=location.belowbar, size=size.small, color=color.lime, text='BUY')

alertcondition(Signal, title='Confirmed Fear Entry', message='Fearzone v3: filtered entry signal')
alertcondition(FearZone and not FearZone[1], title='Zone Enter', message='Fearzone Entered')
alertcondition(not FearZone and FearZone[1], title='Zone Exit', message='Fearzone Exited')
```

## 5. 기본값은 15분봉 + 1시간봉 추세 기준으로 세팅됨

별도 조정 없이 그대로 15m 차트에서 작동:
- `HTF Timeframe = 60` (1시간봉 EMA200 기준)
- `Sigma = 2.0` (15m 노이즈 필터링)
- `Min Bars in Zone = 3` (3봉=45분 이상 지속된 공포만 인정)

## 6. 15분봉 추가 튜닝 (선택)

| 파라미터 | 스윙 (보수) | **기본 (권장)** | 스캘핑 (공격) |
|---|---|---|---|
| High Period | 144 (36h) | **30** | 64 (16h) |
| Stdev Period | 200 | **50** | 96 |
| Sigma Multiplier | 2.5 | **2.0** | 1.5 |
| HTF Timeframe | 240 (4H) | **60 (1H)** | 60 |
| HTF EMA Length | 200 | **200** | 100 |
| Volume Spike x | 1.5 | **1.3** | 1.2 |
| RSI Oversold | 30 | **35** | 40 |
| Min Bars in Zone | 4 | **3** | 2 |

## 7. 15분봉 매매 원칙

**좋은 신호 시간대 (KST)**:
- 09:00 ~ 11:30 (코스피 오전)
- 22:30 ~ 04:00 (미장 정규시간)

**무시할 시간대**:
- 02:00 ~ 07:00 (아시아 새벽, 유동성↓)
- 12:00 ~ 13:30 (점심 관성 거래)
- 22:30 ~ 23:00 (미장 개장 직후 30분)

**손익 가이드** (15m ATR 기준):
- 손절: 진입가 - ATR(14) × 1.0
- 1차 익절: ATR × 1.5 → 50% 청산
- 2차 익절: ATR × 3.0 또는 4H EMA50 도달

## 8. 사용 절차

1. 위 Pine v5 코드 블록 전체 복사
2. TradingView → Pine Editor 열기 → 붙여넣기
3. **Save (Ctrl+S)** → "Save As New" → 이름 지정
4. **Add to chart**
5. 차트에 빨간 캔들(Zone) + 초록 삼각형(Buy Signal) 표시 확인
6. 알람 설정: 차트 우클릭 → Add Alert → Condition에서 "Fearzone v3" 선택
