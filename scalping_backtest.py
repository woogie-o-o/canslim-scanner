"""
scalping_backtest.py
====================
나무증권 장중 스캘핑 조건식 백테스트 (v2 - Scientist Agent 최종본)

조건식 (AND 결합):
  1. 수급 양매수   - 외국인 + 기관 동시 순매수 (전일 일봉 investor 데이터)
  2. 30분봉 횡보   - ADX(14) < 25 (박스권, 추세 없음)
  3. 5분봉 반전    - MACD 골든크로스 OR RSI(14) 과매도 반등 OR Stochastic %K/%D 교차
  4. 거래량 급증   - 현재봉 volume / avg_volume > 1.2배
  5. 가격대 필터   - 1,000원 ~ 2,000,000원

매매 규칙:
  - 진입: 신호 봉 직후 다음 봉 시가 (현실적 슬리피지 반영)
  - 익절: +2.0% 도달 시
  - 손절: -0.4% 도달 시
  - 강제청산: 15:20 도달 또는 날짜 변경 시
  - 수수료: 왕복 0.030% (나무증권 MTS 스캘핑 기준)
  - 장중 시간: 09:00 ~ 15:20 (동시호가 제외)

데이터 경로:
  - 3분봉: C:/Users/new123/Downloads/scalping_final/data/features/{코드}_3m_vfr.parquet
  - 수급:  C:/Users/new123/Downloads/scalping_final/data/investor/{코드}_investor.csv

사용법:
  python scalping_backtest.py                          # 전체 종목
  python scalping_backtest.py --tickers 000660 005930  # 특정 종목
  python scalping_backtest.py --tp 0.020 --sl 0.004    # 파라미터 변경

그리드서치:
  python scalping_backtest.py --grid                   # 파라미터 탐색

결과 저장:
  .omc/scientist/reports/{타임스탬프}_backtest_results.csv
  .omc/scientist/figures/{타임스탬프}_backtest_chart.png
"""

from __future__ import annotations

import argparse
import glob
import os
import time
import warnings
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# ─── 경로 설정 ────────────────────────────────────────────────────────────────
FEAT_DIR   = "C:/Users/new123/Downloads/scalping_final/data/features/"
INV_DIR    = "C:/Users/new123/Downloads/scalping_final/data/investor/"
REPORT_DIR = "C:/Users/new123/Documents/카카오톡 받은 파일/종목스캐너/.omc/scientist/reports/"
FIGURE_DIR = "C:/Users/new123/Documents/카카오톡 받은 파일/종목스캐너/.omc/scientist/figures/"

# ─── 기본 파라미터 (그리드서치로 최적화된 값) ─────────────────────────────────
DEFAULT_PARAMS = dict(
    # 30분봉 횡보 판단
    adx_threshold   = 25,        # ADX(14) < 25  → 횡보 판정
    bw_threshold    = 0.08,      # Bollinger Width < 8% (보조)

    # 5분봉(6분봉 프록시) 반전 신호
    rsi_oversold    = 45,        # RSI(14) 45 미만에서 상향 돌파
    stoch_cross_lvl = 40,        # Stochastic %K 60 미만에서 %D 상향 돌파

    # 거래량
    vol_ratio_min   = 1.2,       # 현재봉 / avg_volume > 1.2배

    # 가격대 필터
    price_min       = 1_000,     # 최소 1,000원
    price_max       = 2_000_000, # 최대 200만원

    # 수급 (일봉 investor.csv)
    foreign_min     = 0,         # 외국인 순매수 주수 > 0
    inst_min        = 0,         # 기관 순매수 주수 > 0

    # 매매 파라미터
    take_profit     = 0.020,     # +2.0% 익절
    stop_loss       = 0.004,     # -0.4% 손절
    fee_one_way     = 0.00015,   # 편도 0.015% (나무증권 MTS)
)


# ─── 기술 지표 함수 ───────────────────────────────────────────────────────────

def aggregate_ohlcv(df: pd.DataFrame, n_bars: int) -> pd.DataFrame:
    """3분봉 DataFrame을 n_bars 단위로 집계. look-ahead bias 없음."""
    df = df.sort_values('dt').reset_index(drop=True)
    df['grp'] = df.index // n_bars
    return df.groupby('grp').agg(
        dt          = ('dt', 'first'),
        open        = ('open', 'first'),
        high        = ('high', 'max'),
        low         = ('low', 'min'),
        close       = ('close', 'last'),
        volume      = ('volume', 'sum'),
        avg_volume  = ('avg_volume', 'last'),
        vwap        = ('vwap', 'last'),
        atr         = ('atr', 'last'),
        atr_pct     = ('atr_pct', 'last'),
        ma240_slope = ('ma240_slope', 'last'),
    ).reset_index(drop=True)


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def macd_indicator(close: pd.Series, fast=12, slow=26, signal=9):
    """MACD 라인, 시그널 라인 반환."""
    m = ema(close, fast) - ema(close, slow)
    s = ema(m, signal)
    return m, s


def rsi(close: pd.Series, period=14) -> pd.Series:
    """RSI(period)."""
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    ag = gain.ewm(alpha=1/period, adjust=False).mean()
    al = loss.ewm(alpha=1/period, adjust=False).mean()
    return 100 - 100 / (1 + ag / (al + 1e-9))


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
               k_period=14, d_period=3):
    """Stochastic %K, %D."""
    low_min  = low.rolling(k_period).min()
    high_max = high.rolling(k_period).max()
    k = 100 * (close - low_min) / (high_max - low_min + 1e-9)
    d = k.rolling(d_period).mean()
    return k, d


def calc_adx(high: pd.Series, low: pd.Series, close: pd.Series,
             period=14) -> pd.Series:
    """ADX(period)."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    dm_p = np.where((high.diff() > 0) & (high.diff() > -low.diff()), high.diff(), 0.0)
    dm_m = np.where((-low.diff() > 0) & (-low.diff() > high.diff()), -low.diff(), 0.0)
    atr14  = pd.Series(tr,   index=high.index).ewm(alpha=1/period, adjust=False).mean()
    dmp14  = pd.Series(dm_p, index=high.index).ewm(alpha=1/period, adjust=False).mean()
    dmm14  = pd.Series(dm_m, index=high.index).ewm(alpha=1/period, adjust=False).mean()
    di_p   = 100 * dmp14 / (atr14 + 1e-9)
    di_m   = 100 * dmm14 / (atr14 + 1e-9)
    dx     = 100 * (di_p - di_m).abs() / (di_p + di_m + 1e-9)
    return dx.ewm(alpha=1/period, adjust=False).mean()


# ─── 신호 생성 ────────────────────────────────────────────────────────────────

def compute_signals(df3m: pd.DataFrame, inv_df: pd.DataFrame,
                    params: dict) -> pd.DataFrame:
    """
    3분봉에 조건 플래그와 진입 신호('signal') 컬럼을 추가한 DataFrame 반환.
    모든 지표는 현재 봉까지의 데이터만 참조 (look-ahead bias 없음).
    """
    df = df3m.copy()
    df['dt'] = pd.to_datetime(df['time'])

    # 장중 시간 필터: 09:00 ~ 15:20 (동시호가 제외)
    df = df[(df['dt'].dt.time >= pd.Timestamp('09:00').time()) &
            (df['dt'].dt.time <= pd.Timestamp('15:20').time())].copy()
    df = df.sort_values('dt').reset_index(drop=True)

    if len(df) < 50:
        return pd.DataFrame()

    # 조건 1: 가격대 필터
    df['cond_price'] = (df['close'] >= params['price_min']) & \
                       (df['close'] <= params['price_max'])

    # 조건 2: 거래량 급증
    df['vol_ratio']   = df['volume'] / (df['avg_volume'] + 1e-9)
    df['cond_volume'] = df['vol_ratio'] > params['vol_ratio_min']

    # 조건 3: 수급 (외국인 + 기관 동시 순매수, 당일 기준)
    inv = inv_df.copy()
    inv['date'] = pd.to_datetime(inv['date']).dt.date
    inv_f = dict(zip(inv['date'], inv['foreign_net']))
    inv_i = dict(zip(inv['date'], inv['inst_net']))
    df['bar_date']        = df['dt'].dt.date
    df['foreign_net_day'] = df['bar_date'].map(inv_f).fillna(0)
    df['inst_net_day']    = df['bar_date'].map(inv_i).fillna(0)
    df['cond_investor']   = (df['foreign_net_day'] > params['foreign_min']) & \
                            (df['inst_net_day']    > params['inst_min'])

    # 조건 4: 30분봉 횡보 (ADX < adx_threshold)
    # 3분봉 10개 = 30분봉, 이전 30분봉 기준으로 판단 (look-ahead 방지)
    df30 = aggregate_ohlcv(df, 10)
    df30['adx_30m']      = calc_adx(df30['high'], df30['low'], df30['close'], 14)
    df30['sideways_30m'] = df30['adx_30m'] < params['adx_threshold']
    df['grp_30m'] = df.index // 10
    sw_prev = df30['sideways_30m'].shift(1).fillna(False)
    df['cond_sideways_30m'] = df['grp_30m'].map(
        dict(zip(df30.index, sw_prev))
    ).fillna(False)

    # 조건 5: 5분봉 반전 신호 (6분봉 프록시 = 3분봉 2개 집계)
    df6 = aggregate_ohlcv(df, 2)

    # 5a. MACD 히스토그램 음→양 전환 (골든크로스)
    ml, ms = macd_indicator(df6['close'])
    df6['macd_hist'] = ml - ms
    df6['sig_macd']  = (df6['macd_hist'] > 0) & (df6['macd_hist'].shift(1) <= 0)

    # 5b. RSI 과매도 구간 상향 돌파
    df6['rsi_v']   = rsi(df6['close'], 14)
    df6['sig_rsi'] = (df6['rsi_v'] > params['rsi_oversold']) & \
                     (df6['rsi_v'].shift(1) <= params['rsi_oversold'])

    # 5c. Stochastic %K가 %D 상향 돌파 (60 미만 구간)
    k6, d6 = stochastic(df6['high'], df6['low'], df6['close'])
    df6['sig_stoch'] = (k6 > d6) & (k6.shift(1) <= d6.shift(1)) & \
                       (k6 < params['stoch_cross_lvl'] + 20)

    # 세 신호 중 1개 이상 충족
    df6['reversal'] = df6['sig_macd'] | df6['sig_rsi'] | df6['sig_stoch']

    # 이전 6분봉 기준 적용
    df['grp_6m'] = df.index // 2
    rev_prev = df6['reversal'].shift(1).fillna(False)
    df['cond_reversal_6m'] = df['grp_6m'].map(
        dict(zip(df6.index, rev_prev))
    ).fillna(False)

    # 최종 진입 신호 (5개 조건 AND)
    df['signal'] = (
        df['cond_price']        &
        df['cond_volume']       &
        df['cond_investor']     &
        df['cond_sideways_30m'] &
        df['cond_reversal_6m']
    )

    return df


# ─── 매매 시뮬레이션 ──────────────────────────────────────────────────────────

def simulate_trades(sig_df: pd.DataFrame, params: dict) -> dict:
    """
    진입 신호 기반 트레이드 시뮬레이션.
      - 진입: 신호 봉의 다음 봉 시가 (슬리피지 반영)
      - 익절/손절: bar의 high/low로 터치 여부 판단
      - 강제청산: 15:20 도달 또는 날짜 바뀔 때
      - 중복 진입 없음: 포지션 보유 중 신호 무시
    """
    tp  = params['take_profit']
    sl  = params['stop_loss']
    fee = params['fee_one_way'] * 2  # 왕복 수수료

    df = sig_df.sort_values('dt').reset_index(drop=True)
    df['date'] = df['dt'].dt.date

    trades    = []
    in_trade  = False
    entry_idx = None

    for i in range(len(df)):
        row = df.iloc[i]

        # ── 포지션 보유 중 청산 판단 ──────────────────────────────────────────
        if in_trade:
            gain = (row['high'] - entry_price) / entry_price
            loss = (row['low']  - entry_price) / entry_price
            eod  = (row['dt'].time() >= pd.Timestamp('15:20').time()) or \
                   (row['date'] != entry_date)

            if gain >= tp:
                exit_price, reason = entry_price * (1 + tp), 'TP'
            elif loss <= -sl:
                exit_price, reason = entry_price * (1 - sl), 'SL'
            elif eod:
                exit_price, reason = row['close'], 'EOD'
            else:
                continue  # 청산 조건 미충족, 다음 봉 대기

            pnl_pct = (exit_price - entry_price) / entry_price - fee
            trades.append({
                'entry_dt':    entry_dt,
                'exit_dt':     row['dt'],
                'entry_price': entry_price,
                'exit_price':  exit_price,
                'exit_reason': reason,
                'pnl_pct':     pnl_pct,
                'hold_bars':   i - entry_idx,
            })
            in_trade = False

        # ── 신호 발생 시 다음 봉 시가로 진입 ─────────────────────────────────
        if not in_trade and row['signal'] and i + 1 < len(df):
            nxt = df.iloc[i + 1]
            if nxt['date'] == row['date']:  # 당일 진입만
                in_trade    = True
                entry_idx   = i
                entry_price = nxt['open']
                entry_date  = row['date']
                entry_dt    = nxt['dt']

    if not trades:
        return {'trades': pd.DataFrame(), 'n_trades': 0}

    tdf  = pd.DataFrame(trades)
    wins = tdf[tdf['pnl_pct'] > 0]
    loss = tdf[tdf['pnl_pct'] <= 0]

    wr  = len(wins) / len(tdf) * 100
    aw  = wins['pnl_pct'].mean() * 100   if len(wins)  else 0.0
    al  = loss['pnl_pct'].mean() * 100   if len(loss)  else 0.0
    pf  = abs(aw / al)                    if al != 0    else np.nan

    mc, cc = 0, 0
    for v in (tdf['pnl_pct'] > 0).astype(int):
        cc = 0 if v else cc + 1
        mc = max(mc, cc)

    return {
        'trades':          tdf,
        'n_trades':        len(tdf),
        'win_rate':        wr,
        'avg_pnl_pct':     tdf['pnl_pct'].mean() * 100,
        'avg_win_pct':     aw,
        'avg_loss_pct':    al,
        'profit_factor':   pf,
        'total_pnl_pct':   tdf['pnl_pct'].sum() * 100,
        'max_consec_loss': mc,
        'exit_counts':     tdf['exit_reason'].value_counts().to_dict(),
        'sharpe_approx':   tdf['pnl_pct'].mean() / (tdf['pnl_pct'].std() + 1e-9) * np.sqrt(252),
    }


# ─── 전체 백테스트 실행 ───────────────────────────────────────────────────────

def run_backtest(tickers=None, params=None, verbose=True):
    """
    지정된 종목 목록(또는 전체)에 대해 백테스트 수행.

    Returns:
        res_df:     종목별 요약 DataFrame
        all_trades: 전체 개별 거래 DataFrame
    """
    if params is None:
        params = DEFAULT_PARAMS

    feat_tickers = [
        os.path.basename(f).replace("_3m_vfr.parquet", "")
        for f in glob.glob(FEAT_DIR + "*_3m_vfr.parquet")
    ]
    inv_tickers = [
        f.replace("_investor.csv", "")
        for f in os.listdir(INV_DIR) if f.endswith("_investor.csv")
    ]
    common = sorted(set(feat_tickers) & set(inv_tickers))

    if tickers:
        missing = [t for t in tickers if t not in common]
        if missing and verbose:
            print(f"Warning: 데이터 없는 종목: {missing}")
        common = [t for t in tickers if t in common]

    if verbose:
        print(f"백테스트 대상: {len(common)}개 종목")
        print(f"파라미터: TP={params['take_profit']:.1%} SL={params['stop_loss']:.1%} "
              f"VR>{params['vol_ratio_min']} ADX<{params['adx_threshold']}")

    results, all_trades_list = [], []
    t0 = time.time()

    for i, code in enumerate(common):
        try:
            df_3m  = pd.read_parquet(FEAT_DIR + f"{code}_3m_vfr.parquet")
            df_inv = pd.read_csv(INV_DIR + f"{code}_investor.csv")

            sig = compute_signals(df_3m, df_inv, params)
            if len(sig) == 0 or sig['signal'].sum() < 3:
                continue

            res = simulate_trades(sig, params)
            if res['n_trades'] < 3:
                continue

            results.append({
                'ticker':          code,
                'n_trades':        res['n_trades'],
                'win_rate':        round(res['win_rate'], 2),
                'avg_pnl_pct':     round(res['avg_pnl_pct'], 4),
                'avg_win_pct':     round(res['avg_win_pct'], 4),
                'avg_loss_pct':    round(res['avg_loss_pct'], 4),
                'profit_factor':   round(float(res['profit_factor']), 3)
                                   if not np.isnan(res['profit_factor']) else 0.0,
                'total_pnl_pct':   round(res['total_pnl_pct'], 3),
                'max_consec_loss': res['max_consec_loss'],
                'n_signals':       int(sig['signal'].sum()),
                'exit_TP':         res['exit_counts'].get('TP',  0),
                'exit_SL':         res['exit_counts'].get('SL',  0),
                'exit_EOD':        res['exit_counts'].get('EOD', 0),
            })

            t = res['trades'].copy()
            t['ticker'] = code
            all_trades_list.append(t)

        except Exception as e:
            if verbose:
                print(f"  오류 {code}: {e}")

        if verbose and (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(common)}] 유효 {len(results)}개, {elapsed:.0f}초 경과")

    res_df     = pd.DataFrame(results)
    all_trades = pd.concat(all_trades_list, ignore_index=True) \
                 if all_trades_list else pd.DataFrame()

    if verbose:
        elapsed = time.time() - t0
        print(f"\n완료: {len(res_df)}개 종목, {len(all_trades)}개 거래, {elapsed:.0f}초")

    return res_df, all_trades


# ─── 결과 저장 ────────────────────────────────────────────────────────────────

def save_results(res_df: pd.DataFrame, all_trades: pd.DataFrame, params: dict):
    """백테스트 결과 CSV 및 차트 저장."""
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(FIGURE_DIR, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # CSV
    csv_path = os.path.join(REPORT_DIR, f"{ts}_backtest_results.csv")
    res_df.to_csv(csv_path, index=False, encoding='utf-8-sig')

    # 차트
    fig = plt.figure(figsize=(16, 10))
    fig.suptitle("스캘핑 백테스트 결과", fontsize=13, fontweight='bold')
    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.35)

    ax1 = fig.add_subplot(gs[0, 0])
    ax1.hist(res_df['win_rate'], bins=25, color='steelblue', edgecolor='white')
    ax1.set_title('Win Rate Distribution')
    ax1.set_xlabel('Win Rate (%)')
    ax1.set_ylabel('# Tickers')

    ax2 = fig.add_subplot(gs[0, 1])
    ax2.hist(res_df['total_pnl_pct'], bins=35, color='seagreen', edgecolor='white')
    ax2.axvline(0, color='red', lw=1.5, ls='--')
    ax2.axvline(res_df['total_pnl_pct'].median(), color='orange', lw=2,
                label=f"Median {res_df['total_pnl_pct'].median():.2f}%")
    ax2.set_title('Total PnL per Ticker (%)')
    ax2.set_xlabel('Total PnL (%)')
    ax2.legend(fontsize=8)

    if len(all_trades):
        pnl_arr = all_trades['pnl_pct'].values * 100
        ax3 = fig.add_subplot(gs[0, 2])
        ax3.hist(pnl_arr, bins=50, color='mediumpurple', edgecolor='white',
                 range=(-1.5, 2.5))
        ax3.axvline(pnl_arr.mean(), color='red', lw=2,
                    label=f"Mean {pnl_arr.mean():.4f}%")
        ax3.set_title('Per-Trade PnL (%)')
        ax3.set_xlabel('PnL (%)')
        ax3.legend(fontsize=8)

    ax4 = fig.add_subplot(gs[1, 0])
    ax4.hist(res_df['profit_factor'].clip(0, 6), bins=25,
             color='goldenrod', edgecolor='white')
    ax4.axvline(res_df['profit_factor'].median(), color='red', lw=2,
                label=f"Median {res_df['profit_factor'].median():.2f}")
    ax4.set_title('Profit Factor Distribution')
    ax4.set_xlabel('Profit Factor')
    ax4.legend(fontsize=8)

    ax5 = fig.add_subplot(gs[1, 1])
    exits = {
        'TP':  res_df['exit_TP'].sum(),
        'SL':  res_df['exit_SL'].sum(),
        'EOD': res_df['exit_EOD'].sum(),
    }
    ax5.pie(exits.values(), labels=exits.keys(),
            colors=['#2ecc71', '#e74c3c', '#3498db'], autopct='%1.1f%%')
    ax5.set_title('Exit Reason (All Trades)')

    ax6 = fig.add_subplot(gs[1, 2])
    sc = ax6.scatter(res_df['win_rate'], res_df['total_pnl_pct'],
                     c=res_df['n_trades'], cmap='viridis', alpha=0.5, s=25)
    plt.colorbar(sc, ax=ax6, label='# Trades')
    ax6.axhline(0, color='red', lw=1, ls='--')
    ax6.set_title('Win Rate vs Total PnL')
    ax6.set_xlabel('Win Rate (%)')
    ax6.set_ylabel('Total PnL (%)')

    fig_path = os.path.join(FIGURE_DIR, f"{ts}_backtest_chart.png")
    plt.savefig(fig_path, dpi=150, bbox_inches='tight')
    plt.close()

    return csv_path, fig_path


# ─── 파라미터 그리드서치 ──────────────────────────────────────────────────────

def grid_search(tickers=None, verbose=True):
    """핵심 파라미터 조합 탐색. 결과 DataFrame 반환."""
    import itertools

    param_grid = {
        'take_profit':   [0.012, 0.015, 0.020],
        'stop_loss':     [0.004, 0.005, 0.007],
        'vol_ratio_min': [1.0, 1.2, 1.5],
        'adx_threshold': [20, 25, 30],
    }

    combos = list(itertools.product(
        param_grid['take_profit'],
        param_grid['stop_loss'],
        param_grid['vol_ratio_min'],
        param_grid['adx_threshold'],
    ))

    grid_results = []
    for tp, sl, vr, adx_t in combos:
        p = dict(DEFAULT_PARAMS)
        p.update(take_profit=tp, stop_loss=sl,
                 vol_ratio_min=vr, adx_threshold=adx_t)

        res_df, all_trades = run_backtest(tickers=tickers, params=p, verbose=False)
        if len(res_df) == 0 or len(all_trades) == 0:
            continue

        total = res_df['n_trades'].sum()
        wtd_wr  = (res_df['win_rate']  * res_df['n_trades']).sum() / total
        wtd_pnl = (res_df['avg_pnl_pct'] * res_df['n_trades']).sum() / total
        pf      = res_df['profit_factor'].median()
        score   = wtd_wr * pf

        grid_results.append({
            'tp': tp, 'sl': sl, 'vr': vr, 'adx': adx_t,
            'n_tickers': len(res_df), 'n_trades': total,
            'wtd_wr': round(wtd_wr, 2),
            'wtd_avg_pnl': round(wtd_pnl, 4),
            'median_pf': round(pf, 3),
            'score': round(score, 3),
        })
        if verbose:
            print(f"tp={tp} sl={sl} vr={vr} adx={adx_t} | "
                  f"n={total} wr={wtd_wr:.1f}% pf={pf:.2f} score={score:.1f}")

    return pd.DataFrame(grid_results).sort_values('score', ascending=False)


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def print_summary(res_df: pd.DataFrame, all_trades: pd.DataFrame):
    total = res_df['n_trades'].sum()
    wtd_wr  = (res_df['win_rate']  * res_df['n_trades']).sum() / total
    wtd_pnl = (res_df['avg_pnl_pct'] * res_df['n_trades']).sum() / total

    print("\n" + "=" * 60)
    print("집계 결과")
    print("=" * 60)
    print(f"분석 종목 수:         {len(res_df)}")
    print(f"총 거래 수:           {total:,}")
    print(f"가중 평균 승률:       {wtd_wr:.2f}%")
    print(f"가중 평균 PnL/거래:   {wtd_pnl:.4f}%")
    print(f"중앙값 총 PnL:        {res_df['total_pnl_pct'].median():.3f}%")
    print(f"수익 종목 비율:       {(res_df['total_pnl_pct']>0).mean()*100:.1f}%")
    print(f"중앙값 손익비:        {res_df['profit_factor'].median():.3f}")
    print(f"중앙값 최대연속손실:  {res_df['max_consec_loss'].median():.0f}회")

    print("\n상위 15 종목 (total_pnl 기준):")
    top = res_df.nlargest(15, 'total_pnl_pct')[
        ['ticker', 'n_trades', 'win_rate', 'avg_pnl_pct',
         'profit_factor', 'total_pnl_pct', 'max_consec_loss']
    ]
    print(top.to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="스캘핑 백테스트 v2")
    parser.add_argument('--tickers', nargs='*',
                        help='분석 종목코드 목록 (미지정 시 전체)')
    parser.add_argument('--tp',  type=float,
                        default=DEFAULT_PARAMS['take_profit'],  help='익절 비율')
    parser.add_argument('--sl',  type=float,
                        default=DEFAULT_PARAMS['stop_loss'],    help='손절 비율')
    parser.add_argument('--vr',  type=float,
                        default=DEFAULT_PARAMS['vol_ratio_min'], help='거래량 배율')
    parser.add_argument('--adx', type=float,
                        default=DEFAULT_PARAMS['adx_threshold'], help='ADX 임계값')
    parser.add_argument('--grid', action='store_true',
                        help='파라미터 그리드서치 실행')
    args = parser.parse_args()

    if args.grid:
        print("=== 파라미터 그리드서치 ===")
        grid_df = grid_search(tickers=args.tickers, verbose=True)
        print("\n=== 상위 10 조합 ===")
        print(grid_df.head(10).to_string(index=False))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(REPORT_DIR, exist_ok=True)
        path = os.path.join(REPORT_DIR, f"{ts}_grid_search.csv")
        grid_df.to_csv(path, index=False, encoding='utf-8-sig')
        print(f"\nGrid search 결과 저장: {path}")
    else:
        params = dict(DEFAULT_PARAMS)
        params.update(take_profit=args.tp, stop_loss=args.sl,
                      vol_ratio_min=args.vr, adx_threshold=args.adx)

        print("=" * 60)
        print("스캘핑 백테스트 v2 시작")
        print("=" * 60)

        res_df, all_trades = run_backtest(
            tickers=args.tickers, params=params, verbose=True
        )

        if len(res_df) == 0:
            print("유효한 백테스트 결과 없음.")
        else:
            print_summary(res_df, all_trades)
            csv_path, fig_path = save_results(res_df, all_trades, params)
            print(f"\nReport CSV: {csv_path}")
            print(f"Chart PNG:  {fig_path}")
