# -*- coding: utf-8 -*-
"""
핸드드로잉 스타일 차트 렌더러.
matplotlib 의 xkcd() 컨텍스트로 손그림 효과를 내고,
4축 분석 주석(Annotation)을 화살표·점선·동그라미로 차트에 얹는다.

패널 구성 (위→아래):
  ① 가격 + EMA + BB + 주석       (비율 7)
  ② 거래량 + OBV                 (비율 2)
  ③ RSI 14                       (비율 2)
  ④ MACD + Signal + Histogram    (비율 2)

출력: PIL.Image.Image  (tkinter ImageTk.PhotoImage 로 임베드)
"""
from __future__ import annotations
import io
import os
from typing import List, Optional

import logging
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")            # tkinter 충돌 방지
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyArrowPatch, Circle
from matplotlib import font_manager as fm
from PIL import Image

# xkcd 폰트 미설치 경고 억제 (matplotlib 자체 폴백 사용)
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")

from four_axis_analyzer import Annotation, FourAxisResult


# ───────── 한글 폰트 자동 탐색 ────────────────────────────────────
def _korean_font() -> Optional[str]:
    candidates = ["Malgun Gothic", "맑은 고딕", "NanumGothic", "Nanum Gothic",
                  "Apple SD Gothic Neo", "Noto Sans CJK KR", "Gulim", "Dotum"]
    available = {f.name for f in fm.fontManager.ttflist}
    for c in candidates:
        if c in available:
            return c

    # 폰트 매니저 캐시에 없으면 시스템 폰트 디렉토리에서 직접 등록 시도
    import os as _os
    import sys as _sys
    font_paths = []
    if _sys.platform.startswith("win"):
        winfonts = _os.path.join(_os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        for fn, nm in [
            ("malgun.ttf", "Malgun Gothic"),
            ("malgunbd.ttf", "Malgun Gothic"),
            ("NanumGothic.ttf", "NanumGothic"),
            ("gulim.ttc", "Gulim"),
            ("batang.ttc", "Batang"),
        ]:
            p = _os.path.join(winfonts, fn)
            if _os.path.exists(p):
                font_paths.append((p, nm))
    elif _sys.platform == "darwin":
        for p in ("/Library/Fonts/AppleSDGothicNeo.ttc",
                  "/System/Library/Fonts/AppleSDGothicNeo.ttc"):
            if _os.path.exists(p):
                font_paths.append((p, "Apple SD Gothic Neo"))
    else:
        for p in ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
            if _os.path.exists(p):
                font_paths.append((p, "NanumGothic"))

    for path, name in font_paths:
        try:
            fm.fontManager.addfont(path)
            # 등록 후 캐시 재확인
            if name in {f.name for f in fm.fontManager.ttflist}:
                return name
        except Exception:
            continue

    # 마지막 폴백: 파일이 있으면 그 폰트 이름 자체를 리턴 (matplotlib가 경로로 찾아줌)
    if font_paths:
        try:
            fp = fm.FontProperties(fname=font_paths[0][0])
            return fp.get_name()
        except Exception:
            pass
    return None

KFONT = _korean_font()
# matplotlib 전역 폰트도 설정 (개별 fontname 지정 누락된 곳 대비)
if KFONT:
    try:
        import matplotlib as _mpl
        _mpl.rcParams["font.family"] = [KFONT, "DejaVu Sans"]
        _mpl.rcParams["axes.unicode_minus"] = False
    except Exception:
        pass

# ───────── 지표 계산 헬퍼 ─────────────────────────────────────────
def _ema_local(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def _rsi_local(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def _macd_local(close: pd.Series, f=12, s=26, sig=9):
    ema_f  = _ema_local(close, f)
    ema_s  = _ema_local(close, s)
    macd   = ema_f - ema_s
    signal = _ema_local(macd, sig)
    hist   = macd - signal
    return macd, signal, hist

def _obv_local(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


class HandDrawnChartRenderer:
    """4축 분석 결과 + OHLCV → 손그림 멀티패널 차트 PNG → PIL.Image."""

    def __init__(self, hist: pd.DataFrame, result: FourAxisResult,
                 ticker: str = "", lookback: int = 120,
                 width_px: int = 720, height_px: int = 600, dpi: int = 100):
        from four_axis_analyzer import _ema, _bb
        h = hist.copy()
        # GreedZone 시리즈 계산 (차트 배경 음영용)
        self._gz_zone = None
        try:
            from greedzone import calc_greedzone
            gz_series = calc_greedzone(h, low_period=112, stdev_period=50, _return_series=True)
            if gz_series is not None and len(gz_series) == len(h):
                self._gz_zone = gz_series
        except Exception:
            pass
        # 기본 지표
        if "EMA20" not in h.columns:
            h["EMA20"]  = _ema(h["Close"], 20)
            h["EMA50"]  = _ema(h["Close"], 50)
            h["EMA200"] = _ema(h["Close"], 200)
            bbu, bbm, bbl, bbw = _bb(h["Close"])
            h["BB_UP"], h["BB_MID"], h["BB_LOW"] = bbu, bbm, bbl
        # 서브패널 지표
        if "RSI14" not in h.columns:
            h["RSI14"] = _rsi_local(h["Close"], 14)
        if "MACD" not in h.columns:
            h["MACD"], h["MACD_SIG"], h["MACD_HIST"] = _macd_local(h["Close"])
        if "OBV" not in h.columns:
            h["OBV"] = _obv_local(h["Close"], h["Volume"])

        self._full_len = len(h)
        self._lookback = min(lookback, len(h))
        self._offset   = self._full_len - self._lookback
        self.hist      = h.tail(self._lookback).reset_index(drop=False)
        self.result    = result
        self.ticker    = ticker
        self.size      = (width_px / dpi, height_px / dpi)
        self.dpi       = dpi

    # ---------------------------------------------------------------
    def render(self) -> Image.Image:
        # 폰트/선두께 자동 스케일 — 기준 720px, 1200px이면 1.67×
        # 브라우저에서 width:100%로 축소될 때도 읽히도록 베이스 사이즈 상향
        s = max(1.0, (self.size[0] * self.dpi) / 720.0)
        fs_title  = int(round(14 * s))
        fs_tick   = int(round(10 * s))
        fs_ylabel = int(round(9  * s))
        fs_legend = int(round(9  * s))
        fs_ann    = int(round(10 * s))
        fs_zone   = int(round(8  * s))
        lw_scale  = s

        with plt.xkcd(scale=1.0, length=80, randomness=2):
            fig = plt.figure(figsize=self.size, dpi=self.dpi, facecolor="#FFFFFF")
            # 2패널 구성 — 가격 + 거래량 (RSI/MACD는 4축 분석 점수 카드와 중복이라 제거)
            gs  = gridspec.GridSpec(
                2, 1,
                figure=fig,
                height_ratios=[5, 2],
                hspace=0.10,
            )
            ax_price = fig.add_subplot(gs[0])
            ax_vol   = fig.add_subplot(gs[1], sharex=ax_price)

            for ax in (ax_price, ax_vol):
                ax.set_facecolor("#FAFAFA")
                ax.spines["top"].set_visible(False)
                ax.spines["right"].set_visible(False)
                ax.tick_params(colors="#666", labelsize=fs_tick)
                ax.grid(True, axis="y", alpha=0.15, linewidth=0.5)

            x     = np.arange(len(self.hist))
            close = self.hist["Close"].values

            # ── ① 가격 패널 ─────────────────────────────────────────
            ax_price.plot(x, close, color="#191919", linewidth=2.2 * lw_scale,
                          label="Close", zorder=3)
            for col, c, lw, dash in [
                ("EMA20",  "#3182F6", 1.6 * lw_scale, None),
                ("EMA50",  "#FF8A00", 1.4 * lw_scale, None),
                ("EMA200", "#721FE5", 1.2 * lw_scale, (6, 4)),
            ]:
                if col in self.hist.columns:
                    y = self.hist[col].values
                    line, = ax_price.plot(x, y, color=c, linewidth=lw,
                                          label=col, alpha=0.85, zorder=2)
                    if dash:
                        line.set_dashes(dash)

            if "BB_UP" in self.hist.columns:
                up_line, = ax_price.plot(x, self.hist["BB_UP"].values,
                                         color="#888", linewidth=0.8 * lw_scale, alpha=0.6)
                lo_line, = ax_price.plot(x, self.hist["BB_LOW"].values,
                                         color="#888", linewidth=0.8 * lw_scale, alpha=0.6)
                up_line.set_dashes((2, 3))
                lo_line.set_dashes((2, 3))
                ax_price.fill_between(x, self.hist["BB_LOW"].values,
                                      self.hist["BB_UP"].values,
                                      color="#3182F6", alpha=0.05, zorder=1)

            # ── GreedZone 구간 음영 ──────────────────────────────────
            if self._gz_zone is not None:
                gz_tail = self._gz_zone.iloc[-self._lookback:].reset_index(drop=True).values
                if len(gz_tail) == len(x):
                    ymin, ymax = ax_price.get_ylim()
                    ax_price.fill_between(
                        x, ymin, ymax,
                        where=gz_tail.astype(bool),
                        color="#FCD34D", alpha=0.18, zorder=0,
                        label="GreedZone",
                    )
                    ax_price.set_ylim(ymin, ymax)

            # 어노테이션/하이쿠 제목 제거 — 차트 위 텍스트는 모두 분석 카드로 분리
            ax_price.set_title(
                self.ticker,
                fontname=KFONT or "DejaVu Sans",
                fontsize=fs_title, color="#191919", loc="left", pad=8,
            )
            leg = ax_price.legend(loc="upper left", fontsize=fs_legend, frameon=False, ncol=4)
            for t in leg.get_texts():
                t.set_color("#444")
            plt.setp(ax_price.get_xticklabels(), visible=False)

            # ── ② 거래량 + OBV 패널 ──────────────────────────────────
            vol = self.hist["Volume"].values
            colors_vol = ["#3182F6" if c >= p else "#F04452"
                          for c, p in zip(close, np.roll(close, 1))]
            colors_vol[0] = "#3182F6"
            ax_vol.bar(x, vol, color=colors_vol, alpha=0.55, width=0.8)
            ax_vol.set_ylabel("Vol", fontsize=fs_ylabel, color="#666")
            ax_vol.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda v, _: f"{v/1e6:.0f}M" if v >= 1e6 else f"{v/1e3:.0f}K"))

            if "OBV" in self.hist.columns:
                ax_obv = ax_vol.twinx()
                obv = self.hist["OBV"].values
                ax_obv.plot(x, obv, color="#FF8A00", linewidth=1.2 * lw_scale, alpha=0.85, label="OBV")
                ax_obv.spines["top"].set_visible(False)
                ax_obv.tick_params(colors="#666", labelsize=fs_ylabel)
                ax_obv.set_ylabel("OBV", fontsize=fs_ylabel, color="#FF8A00")
                ax_obv.yaxis.set_major_formatter(
                    plt.FuncFormatter(lambda v, _: f"{v/1e6:.0f}M" if abs(v) >= 1e6 else f"{v/1e3:.0f}K"))

            # x축 날짜 레이블 (이제 최하단 패널인 거래량에 표시)
            if "Date" in self.hist.columns:
                dates = pd.to_datetime(self.hist["Date"])
            elif "index" in self.hist.columns:
                dates = pd.to_datetime(self.hist["index"])
            else:
                dates = None
            if dates is not None:
                step = max(1, len(x) // 6)
                ticks = x[::step]
                labels = [dates.iloc[i].strftime("%m/%d") for i in ticks if i < len(dates)]
                ax_vol.set_xticks(ticks[:len(labels)])
                ax_vol.set_xticklabels(labels, fontsize=fs_tick, color="#666")

            # tight_layout이 일부 Axes(워터마크/주석 텍스트 포함)와 호환되지 않아
            # 수동 여백 지정으로 대체 — UserWarning 제거
            fig.subplots_adjust(left=0.06, right=0.97, top=0.94, bottom=0.10, hspace=0.10)

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=self.dpi,
                        facecolor="#FFFFFF", bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            return Image.open(buf).copy()

    # ---------------------------------------------------------------
    def _draw_annotations(self, ax, x, close):
        s = max(1.0, (self.size[0] * self.dpi) / 720.0)
        fs_ann = int(round(10 * s))
        lw_scale = s
        seen = set()
        pr = (close.max() - close.min()) or 1.0
        gap = pr * 0.14  # 슬롯 간격 확대 (0.09 → 0.14) — 라벨 겹침 방지
        placed: list[float] = []

        def _slot(desired: float, up: bool = True) -> float:
            y = desired
            step = gap if up else -gap
            for _ in range(20):
                if all(abs(y - p) >= gap for p in placed):
                    break
                y += step
            placed.append(y)
            return y

        for ann in self.result.annotations:
            abs_idx = ann.idx if ann.idx >= 0 else self._full_len + ann.idx
            i = abs_idx - self._offset
            if i < 0 or i >= len(close):
                continue
            key = (i, ann.kind, ann.text)
            if key in seen:
                continue
            seen.add(key)
            xi = x[i]
            yi = ann.y if ann.y else close[i]
            # 라벨 길이 단축 (30 → 18) — 좁은 화면에서 겹침 방지
            label = ann.text if len(ann.text) <= 18 else ann.text[:16] + "…"

            if ann.kind == "arrow_up":
                ty = _slot(yi - pr * 0.13, up=False)
                ax.annotate(
                    label, xy=(xi, yi), xytext=(xi, ty),
                    fontsize=fs_ann, color="#00C853",
                    fontname=KFONT or "DejaVu Sans",
                    ha="center", va="top",
                    arrowprops=dict(arrowstyle="-|>", color="#00C853",
                                    lw=1.4 * lw_scale, mutation_scale=14 * s),
                )
            elif ann.kind == "arrow_down":
                ty = _slot(yi + pr * 0.13, up=True)
                ax.annotate(
                    label, xy=(xi, yi), xytext=(xi, ty),
                    fontsize=fs_ann, color="#F04452",
                    fontname=KFONT or "DejaVu Sans",
                    ha="center", va="bottom",
                    arrowprops=dict(arrowstyle="-|>", color="#F04452",
                                    lw=1.4 * lw_scale, mutation_scale=14 * s),
                )
            elif ann.kind == "circle":
                r = pr * 0.025
                ax.add_patch(Circle((xi, yi), radius=r, fill=False,
                                    edgecolor="#FF8A00", linewidth=1.6 * lw_scale,
                                    zorder=4))
                ty = _slot(yi + r * 1.8, up=True)
                ax.text(xi, ty, label,
                        fontsize=fs_ann, color="#FF8A00", ha="center",
                        fontname=KFONT or "DejaVu Sans")
            elif ann.kind == "dashed":
                hl = ax.axhline(yi, color="#888", linewidth=0.8 * lw_scale, alpha=0.6)
                hl.set_dashes((4, 3))
                ty = _slot(yi, up=True)
                ax.text(x[-1], ty, " " + label,
                        fontsize=fs_ann, color="#666",
                        fontname=KFONT or "DejaVu Sans", va="center")
            else:  # note
                ty = _slot(yi, up=True)
                ax.text(xi, ty, label,
                        fontsize=fs_ann, color="#3182F6",
                        fontname=KFONT or "DejaVu Sans",
                        ha="right", va="bottom",
                        bbox=dict(boxstyle="round,pad=0.2", fc="#FFF3D8",
                                  ec="#FF8A00", lw=0.8 * lw_scale, alpha=0.85))


# ───────── 단독 실행: 시각 검증 ───────────────────────────────────
if __name__ == "__main__":
    import yfinance as yf
    from four_axis_analyzer import FourAxisAnalyzer

    tk = "AAPL"
    h  = yf.Ticker(tk).history(period="1y")
    r  = FourAxisAnalyzer(h, tk).analyze()
    img = HandDrawnChartRenderer(h, r, ticker=tk).render()
    out = os.path.join(os.path.dirname(__file__), "_renderer_test.png")
    img.save(out)
    print(f"saved: {out}  size={img.size}")
    print(f"phase={r.phase}  stars={r.signal_stars}  haiku={r.haiku}")
