# -*- coding: utf-8 -*-
"""
4축 핸드드로잉 분석 카드 — tkinter 컴포넌트.

build_four_axis_card(parent, ticker, hist, C, F)
  → parent 프레임 안에 카드 UI를 채워넣고 반환.

C, F: quant_nexus_v20 의 컬러/폰트 토큰 dict (상호 의존 회피용 주입).
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk
import logging
import traceback

import pandas as pd
from PIL import ImageTk


# ── 레이아웃 토큰 ───────────────────────────────────────────────
_PAD_X = 16            # 카드 외곽 좌우
_PAD_SECTION_Y = 10    # 섹션 간 수직 여백
_PAD_INNER_X = 14      # 박스 내부 좌우
_PAD_INNER_Y = 10      # 박스 내부 상하
_BAR_W = 4             # 좌측 컬러바 너비 (시맨틱 강조)
_WRAP = 720


def _is_kr_ticker(ticker: str) -> bool:
    """6자리 순수 숫자 → KR 종목."""
    t = (ticker or "").split(".")[0].strip()
    return len(t) == 6 and t.isdigit()


def _fmt_price(value, ticker: str) -> str:
    """KR: ₩75,000 / US: $145.23 — 시장별 통화·자릿수."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if _is_kr_ticker(ticker):
        return f"₩{v:,.0f}"
    return f"${v:,.2f}"


def _verdict_color(score: int, C: dict) -> str:
    """4축 점수 → 시맨틱 색상 토큰만 사용."""
    if score >= 4: return C["GREEN"]
    if score == 3: return C.get("TEXT_SUB", C["TEXT_MAIN"])
    if score == 2: return C["GOLD"]
    return C["RED"]


def _separator(parent, C):
    """섹션 사이 가로 구분선 (얇은 SHADOW 컬러)."""
    sep = tk.Frame(parent, bg=C["SHADOW"], height=1, bd=0)
    sep.pack(fill=tk.X, padx=_PAD_X, pady=(_PAD_SECTION_Y // 2, _PAD_SECTION_Y // 2))
    return sep


def _section(parent, C, F, title: str, accent_color: str,
             title_font_key: str = "BODY_BOLD"):
    """
    표준 섹션 카드: 좌측 4px 컬러바 + 흰 패널 + 타이틀.
    반환: 본문을 채울 수 있는 body Frame (이미 PANEL 배경, padx/pady 확보).
    """
    outer = tk.Frame(parent, bg=C["PANEL"])
    outer.pack(fill=tk.X, padx=_PAD_X, pady=(0, _PAD_SECTION_Y))

    bar = tk.Frame(outer, bg=accent_color, width=_BAR_W)
    bar.pack(side=tk.LEFT, fill=tk.Y)

    inner = tk.Frame(outer, bg=C["PANEL"])
    inner.pack(side=tk.LEFT, fill=tk.X, expand=True,
               padx=(_PAD_INNER_X, _PAD_INNER_X))

    tk.Label(inner, text=title,
             font=F.get(title_font_key, ("Segoe UI", 10, "bold")),
             bg=C["PANEL"], fg=accent_color, anchor="w"
             ).pack(fill=tk.X, pady=(_PAD_INNER_Y, 4))

    body = tk.Frame(inner, bg=C["PANEL"])
    body.pack(fill=tk.X, pady=(0, _PAD_INNER_Y))
    return body


def _friendly_axis_text(axis_name: str, axis) -> str:
    """4축 분석 기술 용어를 일반 투자자가 바로 이해할 수 있는 문장으로 변환."""
    d = getattr(axis, "details", {}) or {}
    score = getattr(axis, "score", 3)

    if axis_name == "trend":
        state     = d.get("ema_state", "")
        slope     = d.get("slope_e20", 0)
        above20   = d.get("above_ema20", False)
        above50   = d.get("above_ema50", False)
        above200  = d.get("above_ema200", False)
        slope_dir = d.get("slope_dir", "")
        if "정배열" in state:
            dir_txt = "오름세가 점점 강해지고 있어요." if slope > 0 else "기울기는 다소 둔화됐지만 구조는 양호합니다."
            pos_str = f"EMA50 {'위' if above50 else '아래'} · EMA200 {'위' if above200 else '아래'}"
            return f"단기·중기·장기 평균선이 모두 위로 정렬된 우상향 구조입니다. {dir_txt} ({pos_str})"
        elif "역배열" in state:
            pos_txt = "주가가 평균선 아래에 있어" if not above20 else "주가가 일부 평균선을 회복했지만"
            return f"평균선이 하락 배열입니다. {pos_txt} 전반적인 하락 압력이 남아 있어요. ({slope_dir})"
        elif "골든크로스" in state:
            return f"단기 평균선이 중기 평균선을 막 돌파했습니다. 추세 전환의 첫 신호일 수 있어요. 거래량으로 확인하세요. ({slope_dir})"
        elif "데드크로스" in state:
            return f"단기 평균선이 중기 평균선 아래로 꺾였습니다. 단기 하락 압력이 커지는 구간이에요. ({slope_dir})"
        else:
            pos_str = f"EMA50 {'위' if above50 else '아래'} · EMA200 {'위' if above200 else '아래'}"
            return f"상승·하락 어느 쪽도 우세하지 않은 방향성 탐색 구간입니다. ({pos_str}) 돌파 신호를 기다리는 게 유리해요."

    elif axis_name == "momentum":
        rsi           = d.get("rsi", 50)
        macd_cross_up = d.get("macd_cross_up", False)
        macd_cross_dn = d.get("macd_cross_dn", False)
        macd_turn_up  = d.get("macd_turn_up", False)
        macd_turn_dn  = d.get("macd_turn_dn", False)
        macd_above    = d.get("macd_above", False)
        bull_div      = d.get("bull_div", False)
        bear_div      = d.get("bear_div", False)
        if macd_cross_up:   macd_txt = " MACD 골든크로스가 발생해 동력이 크게 실렸습니다!"
        elif macd_cross_dn: macd_txt = " MACD 데드크로스가 발생해 주의가 필요해요."
        elif macd_turn_up:  macd_txt = " MACD 히스토그램이 상승 전환해 기세가 살아나고 있어요."
        elif macd_turn_dn:  macd_txt = " MACD 히스토그램이 하락 전환했어요."
        elif macd_above:    macd_txt = " MACD가 시그널 위에 있어 긍정적이에요."
        else:               macd_txt = ""
        if rsi <= 30:
            return f"RSI {rsi:.0f}로 극단적 과매도입니다.{macd_txt} 단기 기술적 반등 가능성이 높아요. 다만 추세 방향을 먼저 확인하세요."
        elif rsi >= 70:
            return f"RSI {rsi:.0f}로 과열 구간입니다.{macd_txt} 단기 차익 실현 압력이 나타날 수 있어요."
        elif rsi >= 55:
            return f"RSI {rsi:.0f}로 모멘텀이 살아있는 구간입니다.{macd_txt}"
        elif bull_div:
            return f"가격은 하락했지만 RSI는 더 높은 저점을 형성했습니다(상승 다이버전스).{macd_txt} 바닥을 다지고 있을 가능성이 있어요."
        elif bear_div:
            return f"가격은 올랐지만 RSI는 더 낮은 고점을 형성했습니다(하락 다이버전스).{macd_txt} 상승 동력이 약해지는 신호예요."
        else:
            return f"RSI {rsi:.0f}. 매수·매도 어느 쪽도 우세하지 않은 중립 구간입니다.{macd_txt} 방향 확인 후 대응하세요."

    elif axis_name == "volatility":
        squeeze = d.get("squeeze", False)
        expand = d.get("expand", False)
        upper_break = d.get("upper_break", False)
        lower_touch = d.get("lower_touch", False)
        bb_pos = d.get("bb_position", 0.5)
        atr_pct = d.get("atr_pct", 0)
        if squeeze:
            return f"볼린저 밴드가 역사적으로 좁게 수축되어 있습니다(ATR {atr_pct:.1f}%). 큰 방향성 움직임이 임박했을 수 있어요. 돌파 방향에 올라타세요."
        elif expand and upper_break:
            return f"밴드가 넓어지며 상단을 돌파했습니다. 강한 상승 모멘텀이 실린 구간이에요 (ATR {atr_pct:.1f}%)."
        elif expand and lower_touch:
            return f"밴드가 넓어지며 하단을 이탈했습니다. 패닉 매도가 출회되는 구간으로 리스크 관리가 중요해요."
        elif lower_touch:
            return f"밴드 하단에 닿았습니다. 단기 과매도로 기술적 반등이 나올 수 있지만, 추세 확인 후 진입하세요."
        elif upper_break:
            return f"밴드 상단을 시도 중입니다. 돌파 성공 시 강한 추가 상승이 가능하지만 거래량을 꼭 확인하세요."
        elif bb_pos > 0.6:
            return f"밴드 상단 쪽에 위치해 있습니다. 추세가 강하면 계속 상승, 모멘텀이 약하면 조정 올 수 있어요."
        elif bb_pos < 0.4:
            return f"밴드 하단 쪽에 위치해 있습니다. 지지선 역할을 하는지, 아래로 뚫리는지 주시하세요."
        else:
            return f"밴드 중간 구간에서 안정적으로 움직이고 있습니다 (ATR {atr_pct:.1f}%). 특별한 신호가 없는 상태예요."

    elif axis_name == "volume":
        v_ratio = d.get("v_ratio", 1.0)
        above_vwap = d.get("above_vwap", True)
        obv_slope = d.get("obv_slope", 0)
        price_up = d.get("price_up", True)
        vwap_txt = "VWAP 위에서" if above_vwap else "VWAP 아래에서"
        if v_ratio >= 1.5 and obv_slope > 0 and above_vwap:
            return f"거래량이 평균의 {v_ratio:.1f}배로 강하고, {vwap_txt} OBV도 상승 중입니다. 기관·외국인 매집 가능성이 있어요."
        elif price_up and obv_slope < 0:
            return f"가격은 올랐지만 OBV는 하락 중입니다. 거래량이 받쳐주지 않는 상승은 지속력이 약할 수 있어요."
        elif not above_vwap and obv_slope < 0:
            return f"VWAP 아래에서 OBV마저 하락 중입니다. 매도 우위 흐름이 우세한 구간이에요."
        elif above_vwap and obv_slope > 0:
            return f"{vwap_txt} OBV가 상승 중입니다. 기관 투자자 우위의 긍정적인 수급 흐름이에요."
        elif v_ratio < 0.7:
            return f"거래량이 평균의 {v_ratio:.1f}배로 저조합니다. 시장 관심이 낮은 구간입니다. 거래량이 늘어날 때 진입을 검토하세요."
        else:
            return f"거래량은 보통 수준(×{v_ratio:.1f})이며 {vwap_txt} 거래되고 있습니다. 수급 중립 구간이에요."

    return ""


# ── 신규 헬퍼 ────────────────────────────────────────────────────────────────

def _yf_ticker(ticker: str) -> str:
    """yfinance 조회용 티커 (KR 6자리 → .KS 접미사)."""
    if _is_kr_ticker(ticker):
        return ticker + ".KS"
    return ticker


def _signal_light_info(result) -> tuple[str, str, str]:
    """4축 평등가중 합산 → (라벨, C 색상키, 근거 한 줄)."""
    avg = (result.trend.score + result.momentum.score +
           result.volatility.score + result.volume.score) / 4

    def _arrow(score: int, pos: str, neu: str, neg: str) -> str:
        return pos if score >= 4 else (neg if score <= 2 else neu)

    parts = [
        _arrow(result.trend.score,      "추세↑", "추세→", "추세↓"),
        _arrow(result.momentum.score,   "모멘텀↑", "모멘텀→", "모멘텀↓"),
        _arrow(result.volatility.score, "변동성↑", "변동성→", "변동성↓"),
        _arrow(result.volume.score,     "수급↑", "수급→", "수급↓"),
    ]
    rationale = "  ".join(parts) + f"  (4축 평균 {avg:.1f}/5)"

    if avg >= 3.5:
        return "매수고려", "GREEN", rationale
    elif avg >= 2.5:
        return "관망", "GOLD", rationale
    else:
        return "위험", "RED", rationale


def _calc_dynamic_sr(hist: pd.DataFrame) -> tuple[list, list]:
    """스윙 고점/저점 피크 탐지 → 저항·지지 각 최대 3개 (numpy, strict 비교)."""
    try:
        import numpy as np
        w = hist.tail(120)
        if len(w) < 11:
            return [], []
        highs = np.asarray(w["High"].values, dtype=float)
        lows  = np.asarray(w["Low"].values,  dtype=float)
        order = 5
        n = len(highs)
        peak_idx   = [i for i in range(order, n - order)
                      if highs[i] > highs[i - order:i].max()
                      and highs[i] > highs[i + 1:i + order + 1].max()]
        trough_idx = [i for i in range(order, n - order)
                      if lows[i] < lows[i - order:i].min()
                      and lows[i] < lows[i + 1:i + order + 1].min()]
        cur = float(hist["Close"].iloc[-1])

        def _cluster(vals: list, pct: float = 0.005) -> list:
            """±0.5% 이내 중복 가격대 병합."""
            out: list = []
            for v in vals:
                if not any(abs(v - r) / (r + 1e-9) < pct for r in out):
                    out.append(v)
            return out

        resistances = _cluster(sorted([highs[i] for i in peak_idx   if highs[i] > cur], reverse=True))[:3]
        supports    = _cluster(sorted([lows[i]  for i in trough_idx if lows[i]  < cur]))[:3]
        return supports, resistances
    except Exception as e:
        logging.warning("동적 S/R 계산 실패: %s", e)
        return [], []


def _fetch_mtf(ticker: str, cur_hist: pd.DataFrame, interval: str = "1d") -> dict:
    """일/주/월봉 EMA 추세 방향 반환 (항상 일봉 기준 — 타임프레임 관계없이 yfinance 재조회)."""
    try:
        import yfinance as yf
        res: dict = {}
        yf_sym = _yf_ticker(ticker)
        # 일봉: 현재 interval이 "1d"이고 데이터가 충분하면 재사용, 아니면 yfinance 조회
        if interval == "1d" and len(cur_hist) >= 26:
            dh = cur_hist
        else:
            dh = yf.Ticker(yf_sym).history(period="1y", interval="1d")
        if len(dh) >= 26:
            d20 = dh["Close"].ewm(span=20, adjust=False).mean()
            d50 = dh["Close"].ewm(span=50, adjust=False).mean()
            res["day"] = "↑" if float(d20.iloc[-1]) > float(d50.iloc[-1]) else "↓"
        wk = yf.Ticker(yf_sym).history(period="3y", interval="1wk")
        if len(wk) >= 26:
            w20 = wk["Close"].ewm(span=20, adjust=False).mean()
            w50 = wk["Close"].ewm(span=50, adjust=False).mean()
            res["week"] = "↑" if float(w20.iloc[-1]) > float(w50.iloc[-1]) else "↓"
        mo = yf.Ticker(yf_sym).history(period="5y", interval="1mo")
        if len(mo) >= 12:
            m12 = mo["Close"].ewm(span=12, adjust=False).mean()
            m26 = mo["Close"].ewm(span=26, adjust=False).mean()
            res["month"] = "↑" if float(m12.iloc[-1]) > float(m26.iloc[-1]) else "↓"
        return res
    except Exception as e:
        logging.warning("MTF 로드 실패: %s", e)
        return {}


def build_four_axis_card(parent: tk.Widget, ticker: str, hist: pd.DataFrame,
                         C: dict, F: dict,
                         canslim: dict | None = None,
                         macro: dict | None = None,
                         interval: str = "1d",
                         display_name: str | None = None) -> tk.Widget:
    """
    parent 안에 4축 핸드드로잉 분석 카드를 구성.
    canslim/macro 가 제공되면 7페르소나 위원회 패널도 추가 렌더.
    실패 시 사유 라벨만 표시.
    """
    container = tk.Frame(parent, bg=C["BG"])
    container.pack(fill=tk.BOTH, expand=True)
    chart_label = str(display_name or ticker or "").strip()

    # ── 0) 타임프레임 전환 버튼 ──────────────────────────────────────
    _TF_OPTIONS = [("일봉", "1d"), ("주봉", "1wk"), ("월봉", "1mo")]
    _PERIOD_MAP  = {"1d": "1y", "1wk": "3y", "1mo": "5y"}
    tf_bar = tk.Frame(container, bg=C["BG"])
    tf_bar.pack(fill=tk.X, padx=_PAD_X, pady=(_PAD_SECTION_Y // 2, 0))
    _btn_refs: list = []
    _tf_token = [0]  # 연타 방지 토큰

    def _on_tf_switch(new_iv: str) -> None:
        if new_iv == interval:
            return
        _tf_token[0] += 1
        my_token = _tf_token[0]
        for b, _ in _btn_refs:
            b.config(state=tk.DISABLED)
        # 로딩 표시
        try:
            _lbl_loading = tk.Label(tf_bar, text=f"  ⏳ {new_iv} 데이터 로딩 중...",
                                    bg=C["BG"], fg=C.get("TEXT_SUB", "#888"),
                                    font=("Segoe UI", 9))
            _lbl_loading.pack(side=tk.LEFT, padx=8)
        except Exception:
            _lbl_loading = None

        def _fetch_and_rebuild() -> None:
            try:
                import yfinance as yf
                nh = yf.Ticker(_yf_ticker(ticker)).history(
                    period=_PERIOD_MAP.get(new_iv, "1y"), interval=new_iv)
                nh.index = pd.to_datetime(nh.index)
            except Exception as e:
                logging.error("타임프레임 데이터 로드 실패: %s", e)
                try:
                    container.after(0, lambda: [
                        b.config(state=tk.NORMAL) for b, _ in _btn_refs])
                except tk.TclError:
                    pass
                return

            def _rebuild() -> None:
                if my_token != _tf_token[0]:
                    return  # 더 최신 요청이 있으면 이 콜백 무시
                if nh.empty or len(nh) < 30:
                    for b, iv in _btn_refs:
                        b.config(state=tk.NORMAL,
                                 bg=C["ACCENT"] if iv == interval else C["PANEL"],
                                 fg="white"      if iv == interval else C["TEXT_MAIN"])
                    try:
                        _lbl_loading.config(
                            text=f"⚠ {new_iv} 데이터 부족 (30봉 미만). 다른 타임프레임을 선택해주세요.")
                    except Exception:
                        pass
                    return
                try:
                    container.destroy()
                except tk.TclError:
                    return
                try:
                    build_four_axis_card(parent, ticker, nh, C, F,
                                         canslim=canslim, macro=macro, interval=new_iv,
                                         display_name=chart_label)
                except Exception as _e:
                    logging.error("타임프레임 전환 실패: %s", _e)
                    try:
                        tk.Label(parent,
                                 text=f"⚠ {new_iv} 차트 생성 실패\n{_e}",
                                 bg=C["BG"], fg=C.get("TEXT_SUB", "#888"),
                                 font=("Segoe UI", 9), justify="left", pady=20
                                 ).pack(padx=16)
                    except Exception:
                        pass

            try:
                container.after(0, _rebuild)
            except tk.TclError:
                pass

        import threading
        threading.Thread(target=_fetch_and_rebuild, daemon=True).start()

    for _tf_lbl, _tf_iv in _TF_OPTIONS:
        _active = (_tf_iv == interval)
        _b = tk.Button(tf_bar, text=_tf_lbl,
                        font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
                        bg=C["ACCENT"] if _active else C["PANEL"],
                        fg="white"      if _active else C["TEXT_MAIN"],
                        relief=tk.SUNKEN if _active else tk.FLAT,
                        bd=1, padx=12, pady=4, cursor="hand2")
        _b.pack(side=tk.LEFT, padx=(0, 4))
        _btn_refs.append((_b, _tf_iv))

    for _b, _tf_iv in _btn_refs:
        _b.config(command=lambda iv=_tf_iv: _on_tf_switch(iv))

    try:
        # 모듈 import 는 함수 내부에서 — 의존성 미설치 시에도 스캐너 자체는 동작
        from four_axis_analyzer import FourAxisAnalyzer
        from handdrawn_renderer import HandDrawnChartRenderer
    except Exception as e:
        _err(container, C, F,
             f"분석 모듈 로드 실패: {e}\n(필수: matplotlib, Pillow, yfinance)")
        return container

    # ── 1) 분석 ────────────────────────────────────────────────
    try:
        if hist is None or len(hist) < 30:
            _err(container, C, F, f"OHLCV 데이터 부족 ({0 if hist is None else len(hist)}봉)")
            return container
        result = FourAxisAnalyzer(hist, ticker).analyze()
    except Exception as e:
        logging.error("4축 분석 실패: %s\n%s", e, traceback.format_exc())
        _err(container, C, F, f"분석 실패: {e}")
        return container

    # ── 2) 헤더 — 현재 국면 + 별점 + 한 줄 혼잣말 ──────────────
    _ph = result.phase
    if any(k in _ph for k in ("신고가 돌파", "신고가권", "강한 상승")):
        phase_color = C["GREEN"]
    elif any(k in _ph for k in ("브레이크아웃",)):
        phase_color = C["ACCENT"]
    elif any(k in _ph for k in ("상승 추세 조정", "상승 추세 유지 속")):
        phase_color = C.get("GOLD", C["TEXT_MAIN"])
    elif any(k in _ph for k in ("고점 부근 경계",)):
        phase_color = C.get("ORANGE", C["GOLD"])
    elif any(k in _ph for k in ("추세 전환 시도", "낙폭 과대")):
        phase_color = C["ACCENT"]
    elif any(k in _ph for k in ("추세 전환 경고", "약세 진행")):
        phase_color = C["RED"]
    elif any(k in _ph for k in ("횡보",)):
        phase_color = C.get("TEXT_SUB", C["TEXT_MAIN"])
    else:
        phase_color = C["TEXT_MAIN"]

    stars = "★" * result.signal_stars + "☆" * (5 - result.signal_stars)
    weak_trend = result.trend.score <= 2
    cmte = None
    try:
        if canslim:
            from persona_committee import evaluate as _cmt_eval
            cmte = _cmt_eval(result, canslim, macro)
    except Exception:
        cmte = None

    if weak_trend and result.signal_stars >= 3:
        entry_note = "  ⚠ 추세 약함. 진입 보류 권장"
        entry_color = C["GOLD"]
    elif cmte is not None:
        canslim_total = float(canslim.get("TotalScore", 50)) if canslim else 50
        if cmte.gate_pass and result.signal_stars >= 4:
            entry_note = f"  · 진입 우호 (위원회 {cmte.buy_count}/7 ✓, CANSLIM {canslim_total:.0f}점)"
            entry_color = C["GREEN"]
        elif canslim_total < 40:
            entry_note = f"  ⚠ CANSLIM {canslim_total:.0f}점 부족 — 펀더멘털 미달, 진입 불가"
            entry_color = C["RED"]
        elif canslim_total < 55:
            entry_note = f"  ⚠ CANSLIM {canslim_total:.0f}점 미흡 — 진입 보류 (위원회 {cmte.buy_count}/7)"
            entry_color = C["GOLD"]
        elif cmte.weak_trend_warning:
            entry_note = f"  ⚠ 별점만 높음. 위원회 {cmte.buy_count}/7, 진입 보류"
            entry_color = C["GOLD"]
        elif cmte.buy_count >= 5:
            entry_note = f"  · 위원회 통과 {cmte.buy_count}/7 (CANSLIM {canslim_total:.0f}점), 추가 트리거 대기"
            entry_color = C["ACCENT"]
        elif cmte.buy_count <= 2:
            entry_note = f"  · 관망 (위원회 {cmte.buy_count}/7, CANSLIM {canslim_total:.0f}점)"
            entry_color = C.get("TEXT_SUB", C["TEXT_MAIN"])
        else:
            entry_note = f"  · 중립 (위원회 {cmte.buy_count}/7, CANSLIM {canslim_total:.0f}점)"
            entry_color = C.get("TEXT_SUB", C["TEXT_MAIN"])
    elif result.signal_stars >= 4:
        entry_note = "  · 별점 높음. 위원회 미평가, 추가 확인 필요"
        entry_color = C["ACCENT"]
    elif result.signal_stars <= 2:
        entry_note = "  · 관망"
        entry_color = C.get("TEXT_SUB", C["TEXT_MAIN"])
    else:
        entry_note = ""
        entry_color = C.get("TEXT_SUB", C["TEXT_MAIN"])

    # ── 2a) 종합 신호등 — 4축 평등가중 합산 ─────────────────────────
    _sl_label, _sl_key, _sl_rationale = _signal_light_info(result)
    _sl_bg = C.get(_sl_key, C["TEXT_MAIN"])
    _sl_frame = tk.Frame(container, bg=_sl_bg)
    _sl_frame.pack(fill=tk.X, padx=_PAD_X, pady=(_PAD_SECTION_Y, 0))
    _sl_inner = tk.Frame(_sl_frame, bg=_sl_bg)
    _sl_inner.pack(fill=tk.X, padx=_PAD_INNER_X, pady=8)
    tk.Label(_sl_inner, text=_sl_label,
             font=F.get("TITLE", ("Segoe UI", 20, "bold")),
             bg=_sl_bg, fg="white", anchor="w",
             ).pack(side=tk.LEFT, padx=(0, 20))
    tk.Label(_sl_inner, text=_sl_rationale,
             font=F.get("BODY", ("Segoe UI", 10)),
             bg=_sl_bg, fg="white", anchor="w",
             ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    hdr = tk.Frame(container, bg=C["PANEL"])
    hdr.pack(fill=tk.X, padx=_PAD_X, pady=(_PAD_SECTION_Y, _PAD_SECTION_Y))

    bar = tk.Frame(hdr, bg=phase_color, width=_BAR_W)
    bar.pack(side=tk.LEFT, fill=tk.Y)

    hinner = tk.Frame(hdr, bg=C["PANEL"])
    hinner.pack(side=tk.LEFT, fill=tk.X, expand=True,
                padx=(_PAD_INNER_X, _PAD_INNER_X))

    tk.Label(hinner, text="현재 국면",
             font=F.get("TINY", ("Segoe UI", 8)),
             bg=C["PANEL"], fg=C.get("TEXT_LABEL", C["TEXT_MAIN"]),
             anchor="w"
             ).pack(fill=tk.X, pady=(_PAD_INNER_Y, 0))
    tk.Label(hinner, text=result.phase,
             font=F.get("TITLE", ("Segoe UI", 20, "bold")),
             bg=C["PANEL"], fg=phase_color, anchor="w",
             wraplength=_WRAP, justify="left"
             ).pack(fill=tk.X, pady=(2, 6))

    tk.Label(hinner, text=f"진입 신호  {stars}{entry_note}",
             font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
             bg=C["PANEL"], fg=entry_color, anchor="w",
             wraplength=_WRAP, justify="left"
             ).pack(fill=tk.X, pady=(0, 6))

    tk.Label(hinner, text=f'💬  "{result.haiku}"',
             font=F.get("BODY", ("Segoe UI", 10, "italic")),
             bg=C["PANEL"], fg=C.get("TEXT_SUB", C["TEXT_MAIN"]),
             anchor="w", wraplength=_WRAP, justify="left"
             ).pack(fill=tk.X, pady=(0, _PAD_INNER_Y))

    # ── 3) 차트 임베드 ────────────────────────────────────────
    try:
        img = HandDrawnChartRenderer(hist, result, ticker=chart_label,
                                     lookback=120,
                                     width_px=720, height_px=640).render()
        photo = ImageTk.PhotoImage(img)
        chart_wrap = tk.Frame(container, bg=C["BG"])
        chart_wrap.pack(fill=tk.X, padx=_PAD_X, pady=(0, _PAD_SECTION_Y))
        chart_lbl = tk.Label(chart_wrap, image=photo, bg=C["BG"], bd=0)
        chart_lbl.image = photo  # GC 방지
        chart_lbl.pack()
    except Exception as e:
        logging.error("차트 렌더 실패: %s\n%s", e, traceback.format_exc())
        _err(container, C, F, f"차트 렌더 실패: {e}", small=True)

    # ── 4) 4축 verdict 표 ─────────────────────────────────────
    table_body = _section(container, C, F, "📊  4축 분석 요약",
                          C["ACCENT"], title_font_key="SUBHEADER")

    axis_keys = [
        ("📈  추세",   "trend",      result.trend),
        ("⚡  모멘텀", "momentum",   result.momentum),
        ("〰️  변동성", "volatility", result.volatility),
        ("📊  수급",   "volume",     result.volume),
    ]
    for label, key, axis in axis_keys:
        row = tk.Frame(table_body, bg=C["PANEL"])
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text=label, width=12,
                 font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
                 bg=C["PANEL"], fg=C["TEXT_MAIN"], anchor="w"
                 ).pack(side=tk.LEFT)
        tk.Label(row, text="●" * axis.score + "○" * (5 - axis.score),
                 width=7, bg=C["PANEL"], fg=_verdict_color(axis.score, C),
                 font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold"))
                 ).pack(side=tk.LEFT)
        friendly = _friendly_axis_text(key, axis)
        tk.Label(row, text=friendly or axis.verdict,
                 bg=C["PANEL"], fg=C.get("TEXT_SUB", C["TEXT_MAIN"]),
                 anchor="w", padx=8,
                 font=F.get("SMALL", ("Segoe UI", 9)),
                 wraplength=500, justify="left"
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    # ── 4.6) 핵심 관찰 ───────────────────────────────────────
    if getattr(result, "key_observation", ""):
        body = _section(container, C, F, "🔎  핵심 관찰", C["GREEN"])
        tk.Label(body, text=result.key_observation,
                 font=F.get("SMALL", ("Segoe UI", 9)),
                 bg=C["PANEL"], fg=C["TEXT_MAIN"], anchor="w",
                 wraplength=_WRAP, justify="left"
                 ).pack(fill=tk.X)

    # ── 4.6b) 핵심 가격대 ────────────────────────────────────
    lv = getattr(result, "key_levels", {}) or {}
    if lv:
        body = _section(container, C, F, "📍  핵심 가격대", C["GOLD"])
        line1 = (f"현재가 {_fmt_price(lv['current'], ticker)}    "
                 f"52주 고 {_fmt_price(lv['high_52w'], ticker)} ({lv['from_high_pct']:+.1f}%)    "
                 f"52주 저 {_fmt_price(lv['low_52w'], ticker)} ({lv['from_low_pct']:+.1f}%)")
        line2 = (f"지지 {_fmt_price(lv['support1'], ticker)}    "
                 f"저항 {_fmt_price(lv['resistance1'], ticker)}")
        tk.Label(body, text=line1,
                 font=F.get("SMALL_BOLD", ("Malgun Gothic", 9, "bold")),
                 bg=C["PANEL"], fg=C["TEXT_MAIN"], anchor="w",
                 wraplength=_WRAP, justify="left"
                 ).pack(fill=tk.X)
        tk.Label(body, text=line2,
                 font=F.get("SMALL_BOLD", ("Malgun Gothic", 9, "bold")),
                 bg=C["PANEL"], fg=C["TEXT_MAIN"], anchor="w",
                 wraplength=_WRAP, justify="left"
                 ).pack(fill=tk.X, pady=(4, 0))

    # ── 4.6d) 동적 지지/저항 (numpy 스윙 극값) ──────────────────────
    _sr_sup, _sr_res = _calc_dynamic_sr(hist)
    if _sr_sup or _sr_res:
        body = _section(container, C, F, "📐  동적 지지/저항",
                        C.get("PURPLE", C["ACCENT"]))
        if _sr_res:
            tk.Label(body,
                     text="저항:  " + "  /  ".join(_fmt_price(v, ticker) for v in _sr_res),
                     font=F.get("SMALL_BOLD", ("Malgun Gothic", 9, "bold")),
                     bg=C["PANEL"], fg=C["RED"], anchor="w",
                     wraplength=_WRAP, justify="left"
                     ).pack(fill=tk.X)
        if _sr_sup:
            tk.Label(body,
                     text="지지:  " + "  /  ".join(_fmt_price(v, ticker) for v in _sr_sup),
                     font=F.get("SMALL_BOLD", ("Malgun Gothic", 9, "bold")),
                     bg=C["PANEL"], fg=C["GREEN"], anchor="w",
                     wraplength=_WRAP, justify="left"
                     ).pack(fill=tk.X, pady=(4, 0))
        if _sr_res and _sr_sup:
            _cur_p  = float(hist["Close"].iloc[-1])
            _near_r = min(_sr_res, key=lambda x: x - _cur_p)   # 모두 > cur
            _near_s = max(_sr_sup, key=lambda x: _cur_p - x)   # 모두 < cur
            _pct_r  = (_near_r - _cur_p) / (_cur_p + 1e-9) * 100
            _pct_s  = (_cur_p - _near_s) / (_cur_p + 1e-9) * 100
            tk.Label(body,
                     text=(f"근거리 저항 {_fmt_price(_near_r, ticker)} (+{_pct_r:.1f}%)  ·  "
                           f"근거리 지지 {_fmt_price(_near_s, ticker)} (-{_pct_s:.1f}%)"),
                     font=F.get("SMALL", ("Segoe UI", 9)),
                     bg=C["PANEL"], fg=C.get("TEXT_SUB", C["TEXT_MAIN"]),
                     anchor="w", wraplength=_WRAP, justify="left"
                     ).pack(fill=tk.X, pady=(4, 0))

    # ── 4.6e) MTF 추세정렬 (비동기 로드) ────────────────────────────
    _mtf_body = _section(container, C, F, "🕐  멀티타임프레임(MTF) 추세정렬",
                         C["ACCENT"])
    _mtf_status = tk.Label(_mtf_body, text="주봉·월봉 로딩 중…",
                            font=F.get("SMALL", ("Segoe UI", 9)),
                            bg=C["PANEL"], fg=C.get("TEXT_SUB", C["TEXT_MAIN"]),
                            anchor="w")
    _mtf_status.pack(fill=tk.X)

    def _render_mtf_result(mtf: dict) -> None:
        try:
            for w in _mtf_body.winfo_children():
                w.destroy()
        except tk.TclError:
            return
        if not mtf:
            tk.Label(_mtf_body, text="MTF 데이터 로드 실패 (네트워크 확인)",
                     font=F.get("SMALL", ("Segoe UI", 9)),
                     bg=C["PANEL"], fg=C["RED"], anchor="w").pack(fill=tk.X)
            return
        day_d  = mtf.get("day",   "?")
        week_d = mtf.get("week",  "?")
        mon_d  = mtf.get("month", "?")
        up_cnt = sum(1 for d in (day_d, week_d, mon_d) if d == "↑")
        if up_cnt == 3:
            align_txt, align_col = "3단계 완전 정배열 — 강한 상승 추세", C["GREEN"]
        elif up_cnt == 2:
            align_txt, align_col = "2단계 정배열 — 중기 상승 추세", C.get("GOLD", C["TEXT_MAIN"])
        elif up_cnt == 1:
            align_txt, align_col = "1단계 — 단기 반등 수준, 관망 병행", C["GOLD"]
        else:
            align_txt, align_col = "전 구간 하락 배열 — 관망 / 숏 유의", C["RED"]
        _row = tk.Frame(_mtf_body, bg=C["PANEL"])
        _row.pack(fill=tk.X, pady=(0, 4))
        for _tf_name, _dir in (("일봉", day_d), ("주봉", week_d), ("월봉", mon_d)):
            _dc = C["GREEN"] if _dir == "↑" else C["RED"]
            tk.Label(_row, text=f"{_tf_name} EMA {_dir}",
                     font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
                     bg=C["PANEL"], fg=_dc, anchor="w", padx=10
                     ).pack(side=tk.LEFT)
        tk.Label(_mtf_body, text=align_txt,
                 font=F.get("SMALL", ("Segoe UI", 9)),
                 bg=C["PANEL"], fg=align_col, anchor="w",
                 wraplength=_WRAP).pack(fill=tk.X)

    def _load_mtf_bg() -> None:
        mtf = _fetch_mtf(ticker, hist, interval)
        try:
            container.after(0, lambda: _render_mtf_result(mtf))
        except tk.TclError:
            pass

    import threading as _threading
    _threading.Thread(target=_load_mtf_bg, daemon=True).start()

    # ── 4.6c) 브레이크아웃 / 시나리오 ───────────────────────
    if getattr(result, "breakout_point", "") or getattr(result, "risk_point", ""):
        body = _section(container, C, F, "🚀  브레이크아웃 / 시나리오", C["ACCENT"])
        if result.breakout_point:
            tk.Label(body, text=f"▲ {result.breakout_point}",
                     font=F.get("SMALL", ("Segoe UI", 9)),
                     bg=C["PANEL"], fg=C["GREEN"], anchor="w",
                     wraplength=_WRAP, justify="left"
                     ).pack(fill=tk.X)
        if result.risk_point:
            tk.Label(body, text=f"▼ {result.risk_point}",
                     font=F.get("SMALL", ("Segoe UI", 9)),
                     bg=C["PANEL"], fg=C["RED"], anchor="w",
                     wraplength=_WRAP, justify="left"
                     ).pack(fill=tk.X, pady=(4, 0))

    # ── 4.7) 지지/저항 플립 ──────────────────────────────────
    if getattr(result, "support_flip", ""):
        body = _section(container, C, F, "🔄  지지/저항 플립", C["ACCENT"])
        tk.Label(body, text=result.support_flip,
                 font=F.get("SMALL", ("Segoe UI", 9)),
                 bg=C["PANEL"], fg=C["TEXT_MAIN"], anchor="w",
                 wraplength=_WRAP, justify="left"
                 ).pack(fill=tk.X)

    # ── 4.8) RSI 시장 주도력 ─────────────────────────────────
    if getattr(result, "rsi_leadership", ""):
        body = _section(container, C, F, "📡  RSI 시장 주도력",
                        C.get("PURPLE", C["ACCENT"]))
        tk.Label(body, text=result.rsi_leadership,
                 font=F.get("SMALL", ("Segoe UI", 9)),
                 bg=C["PANEL"], fg=C["TEXT_MAIN"], anchor="w",
                 wraplength=_WRAP, justify="left"
                 ).pack(fill=tk.X)

    # ── 4.9) 구조화 분석 요약 ────────────────────────────────
    if getattr(result, "structured_analysis", ""):
        body = _section(container, C, F, "📋  구조화 분석 요약",
                        C.get("TEXT_SUB", C["TEXT_MAIN"]))
        tk.Label(body, text=result.structured_analysis,
                 font=F.get("SMALL", ("Malgun Gothic", 9)),
                 bg=C["PANEL"], fg=C["TEXT_MAIN"], anchor="w",
                 wraplength=_WRAP, justify="left"
                 ).pack(fill=tk.X)

    # ── 5) 리스크 ─────────────────────────────────────────────
    body = _section(container, C, F, "⚠️  리스크 포인트", C["GOLD"])
    risk_txt = (f"손절 {_fmt_price(result.risk['stop_loss'], ticker)}  "
                f"({result.risk['stop_pct']}% / ATR×{result.risk['atr_mult']})")
    tk.Label(body, text=risk_txt,
             font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
             bg=C["PANEL"], fg=C["RED"], anchor="w",
             wraplength=_WRAP, justify="left"
             ).pack(fill=tk.X)
    tk.Label(body, text=f"무효화 조건  {result.risk['invalidation']}",
             font=F.get("SMALL", ("Segoe UI", 9)),
             bg=C["PANEL"], fg=C.get("TEXT_SUB", C["TEXT_MAIN"]), anchor="w",
             wraplength=_WRAP, justify="left"
             ).pack(fill=tk.X, pady=(4, 0))

    # ── 5.5) 포지션 사이징 ───────────────────────────────────
    try:
        from position_sizer import calc_position, suggest_targets
        lv = getattr(result, "key_levels", {}) or {}
        entry = float(lv.get("current") or 0)
        stop  = float(result.risk.get("stop_loss") or 0)
        if entry > 0 and 0 < stop < entry:
            equity   = 10_000_000 if _is_kr_ticker(ticker) else 10_000
            risk_pct = 1.0
            ps  = calc_position(equity, risk_pct, entry, stop)
            tgt = suggest_targets(entry, stop)
            body = _section(container, C, F,
                            "🎯  포지션 사이징 (가정 1% 리스크)", C["GREEN"])
            equity_str = _fmt_price(equity, ticker)
            line1 = (f"계좌 {equity_str}  ·  진입 {_fmt_price(entry, ticker)}  ·  "
                     f"손절 {_fmt_price(stop, ticker)} ({ps['stop_pct']}%)")
            line2 = (f"권장 수량 {ps['qty']:,}주  ·  "
                     f"명목 {_fmt_price(ps['notional'], ticker)}  ·  "
                     f"리스크 {_fmt_price(ps['risk_amount'], ticker)}")
            line3 = (f"익절  1R {_fmt_price(tgt['t1_1R'], ticker)}  ·  "
                     f"2R {_fmt_price(tgt['t2_2R'], ticker)}  ·  "
                     f"3R {_fmt_price(tgt['t3_3R'], ticker)}")
            tk.Label(body, text=line1,
                     font=F.get("SMALL", ("Segoe UI", 9)),
                     bg=C["PANEL"], fg=C.get("TEXT_SUB", C["TEXT_MAIN"]),
                     anchor="w", wraplength=_WRAP, justify="left"
                     ).pack(fill=tk.X)
            tk.Label(body, text=line2,
                     font=F.get("SMALL_BOLD", ("Malgun Gothic", 9, "bold")),
                     bg=C["PANEL"], fg=C["TEXT_MAIN"],
                     anchor="w", wraplength=_WRAP, justify="left"
                     ).pack(fill=tk.X, pady=(4, 0))
            tk.Label(body, text=line3,
                     font=F.get("SMALL_BOLD", ("Malgun Gothic", 9, "bold")),
                     bg=C["PANEL"], fg=C["GREEN"],
                     anchor="w", wraplength=_WRAP, justify="left"
                     ).pack(fill=tk.X, pady=(4, 0))
    except Exception as _e:
        logging.warning("position_sizer panel skipped: %s", _e)

    # ── 6) 7-페르소나 위원회 + 통합 등급 ──────────────────────
    if canslim is not None:
        try:
            from persona_committee import evaluate as _committee_eval
            comm = _committee_eval(result, canslim, macro)
        except Exception as e:
            logging.error("위원회 평가 실패: %s\n%s", e, traceback.format_exc())
            comm = None

        if comm is not None:
            if comm.gate_pass:
                gate_color = C["GREEN"]
            elif comm.weak_trend_warning:
                gate_color = C["GOLD"]
            else:
                gate_color = C["RED"]

            body = _section(container, C, F,
                            "🏛  7-페르소나 위원회 + 통합 등급",
                            gate_color, title_font_key="SUBHEADER")

            tk.Label(body, text=comm.summary,
                     font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
                     bg=C["PANEL"], fg=gate_color, anchor="w",
                     wraplength=_WRAP, justify="left"
                     ).pack(fill=tk.X, pady=(0, 6))

            for v in comm.verdicts:
                row = tk.Frame(body, bg=C["PANEL"])
                row.pack(fill=tk.X, pady=2)
                if v.verdict == "매수":
                    vc = C["GREEN"]
                elif v.verdict == "매도":
                    vc = C["RED"]
                else:
                    vc = C["GOLD"]
                tk.Label(row, text=v.name, width=12,
                         font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
                         bg=C["PANEL"], fg=C["TEXT_MAIN"], anchor="w"
                         ).pack(side=tk.LEFT)
                tk.Label(row, text=v.verdict, width=6,
                         font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
                         bg=C["PANEL"], fg=vc, anchor="w"
                         ).pack(side=tk.LEFT)
                tk.Label(row, text=v.rationale,
                         font=F.get("SMALL", ("Segoe UI", 9)),
                         bg=C["PANEL"], fg=C.get("TEXT_SUB", C["TEXT_MAIN"]),
                         anchor="w", wraplength=540, justify="left"
                         ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    return container


def _err(parent, C, F, msg, small=False):
    tk.Label(parent, text=f"  ⚠️  {msg}",
             font=F.get("SMALL" if small else "BODY", ("Segoe UI", 10)),
             bg=C["BG"], fg=C["RED"],
             anchor="w", justify="left", wraplength=720
             ).pack(fill=tk.X, padx=_PAD_X, pady=_PAD_INNER_Y)
