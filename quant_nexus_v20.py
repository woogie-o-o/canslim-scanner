"""
(.)(.)스캐너
=============================================================
윌리엄 오닐(William O'Neil) CAN SLIM 원칙 + 월가 퀀트 전략 융합

  C  — Current Quarterly Earnings  (분기 EPS 가속도)
  A  — Annual Earnings Growth       (연간 EPS + ROE ≥ 17%)
  N  — New Highs / Breakout         (52주 신고가 근접 + 컵핸들 피벗)
  S  — Supply & Demand              (거래량 확인 돌파)
  L  — Leader or Laggard            (RS Rating 80+ 주도주만)
  I  — Institutional Sponsorship    (Smart Money Flow)
  M  — Market Direction             (Bear 시장 강력 억제 필터)

v20.0 주요 변경:
  ★ UI/DPI 선명도 전면 개선
  - Windows High DPI 인식: SetProcessDpiAwareness(2→1→legacy) 폴백 체인
  - 폰트 시스템 전면 교체: OS별 최적 한글 폰트 자동 선택
    · Windows → Malgun Gothic (본문) + Consolas (숫자)
    · macOS   → Apple SD Gothic Neo + Menlo
    · Linux   → Noto Sans CJK KR + DejaVu Sans Mono
  - 전역 F[] 폰트 딕셔너리: TITLE/BODY/MONO/BTN 등 용도별 분리
  ★ 섹터 데이터 대규모 확장 (IndexerGo + 네이버 증권 반영)
  - 미국: Mag7·AI반도체·빅테크·핀테크·방산·바이오 등 전면 재편 (섹터당 15~25종목)
  - 한국: HBM·온디바이스AI·K-방산·원전·저PBR금융 등 최신 테마 반영
  ★ v19.0 기능 전부 계승
  - CAN SLIM 7원칙 정밀 구현 / EPS 가속도 / 컵앤핸들 피벗
  - RS Rating 80+ Leader 필터 / Bear 시장 50% Cap
  - 슈퍼 그로스 승수 / Fail-Safe Ceiling / Hurst + Kalman 필터
"""

import warnings
# matplotlib/pyparsing 버전 불일치 DeprecationWarning 억제
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=PendingDeprecationWarning)
warnings.filterwarnings("ignore", message=".*parseString.*")
warnings.filterwarnings("ignore", message=".*resetCache.*")
warnings.filterwarnings("ignore", message=".*enablePackrat.*")

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False

    class _TkDummy:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, *args, **kwargs):
            return self

        def __getattr__(self, _name):
            return self

        def __bool__(self):
            return False

    class _TkNamespace:
        def __getattr__(self, _name):
            return _TkDummy()

    tk = _TkNamespace()  # type: ignore
    ttk = _TkNamespace()  # type: ignore
    messagebox = _TkNamespace()  # type: ignore
import sys
import os
import threading

try:
    import naver_quarter as _naver_q
    _NAVERQ_OK = True
except Exception:
    _naver_q = None  # type: ignore
    _NAVERQ_OK = False

import concurrent.futures
from datetime import datetime, timedelta
import pickle
import logging
import time
import traceback
import hashlib
import json
from functools import wraps
import re
import urllib.request
import valuation_engine
from us_company_info import US_COMPANY_INFO as _US_COMPANY_INFO
from kr_company_info import KR_COMPANY_INFO as _KR_COMPANY_INFO

# ─── 투자지주사 보유지분 테이블 (NAV-할인율 평가용) ──────────────────────
# 투자지주사는 실적/DCF가 아니라 보유 상장지분 시가총액(NAV)에 지주사
# 할인율이 적용돼 주가가 결정된다. 아래는 직접 보유 "상장 자회사" 지분만
# 큐레이션한 것(비상장·간접지분은 보수적으로 제외 → NAV가 보수적으로 산출됨).
#
# 필수 키
#   stakes: [(자회사 6자리코드, 지분율 decimal), ...]
#   buyback_yield: 배당 외 자사주 매입/소각에 의한 추가 주주환원율(decimal).
#                  배당수익률은 런타임 info에서 합산되므로 여기엔 넣지 않는다.
# 선택 키 (정밀화 요소 — 미입력 시 보수적 기본값)
#   unlisted_oku: 비상장·간접지분 추정가치(억원). NAV에 가산. 미입력=0이라
#                 NAV가 하한으로 잡혀 저평가 신호가 둔감해진다. 큰 비상장
#                 자산 보유처(㈜SK의 SK실트론·SK E&S 등)에 한해 입력.
#   discount: {"base": 0.55, "min": 0.30} 종목별 목표 할인율 오버라이드.
#             지배구조 리스크·복층지주 구조가 큰 곳(예: 한진칼)은 base를
#             크게, 적극 주주환원처는 작게. 미입력 시 엔진 기본(0.50/0.25).
#
# ⚠ 지분율·추정가치는 근사치이며 분기보고서 기준으로 주기적 갱신 필요.
HOLDCO_HOLDINGS: dict[str, dict] = {
    "402340": {  # SK스퀘어 — SK ICT 투자지주
        "stakes": [("000660", 0.2007)],          # SK하이닉스
        "buyback_yield": 0.04,                    # 적극적 자사주 소각 정책
    },
    "034730": {  # ㈜SK — SK그룹 지주
        "stakes": [("017670", 0.3057),           # SK텔레콤
                   ("096770", 0.3622),           # SK이노베이션
                   ("402340", 0.3055)],          # SK스퀘어
        "buyback_yield": 0.02,
    },
    "003550": {  # ㈜LG — LG그룹 지주
        "stakes": [("051910", 0.3006),           # LG화학
                   ("066570", 0.3047),           # LG전자
                   ("032640", 0.3766),           # LG유플러스
                   ("051900", 0.3000)],          # LG생활건강
        "buyback_yield": 0.01,
    },
    "000150": {  # 두산 — 두산그룹 지주
        "stakes": [("034020", 0.3039)],          # 두산에너빌리티
        "buyback_yield": 0.0,
    },
    "000880": {  # ㈜한화 — 한화그룹 지주
        "stakes": [("012450", 0.3395),           # 한화에어로스페이스
                   ("009830", 0.3631)],          # 한화솔루션
        "buyback_yield": 0.0,
    },
    "001040": {  # CJ — CJ그룹 지주
        "stakes": [("097950", 0.4455),           # CJ제일제당
                   ("035760", 0.4007)],          # CJ ENM
        "buyback_yield": 0.0,
    },
    "004990": {  # 롯데지주
        "stakes": [("011170", 0.2559),           # 롯데케미칼
                   ("023530", 0.4000),           # 롯데쇼핑
                   ("280360", 0.4840)],          # 롯데웰푸드
        "buyback_yield": 0.0,
    },
    "002790": {  # 아모레퍼시픽홀딩스
        "stakes": [("090430", 0.3706)],          # 아모레퍼시픽
        "buyback_yield": 0.0,
    },
    "008930": {  # 한미사이언스
        "stakes": [("128940", 0.4142)],          # 한미약품
        "buyback_yield": 0.0,
    },
    "180640": {  # 한진칼 — 한진그룹 지주
        "stakes": [("003490", 0.2606)],          # 대한항공
        "buyback_yield": 0.0,
    },
    "004800": {  # 효성 — 효성그룹 지주
        "stakes": [("298000", 0.2134),           # 효성첨단소재
                   ("298050", 0.2194),           # 효성티앤씨
                   ("298040", 0.3240)],          # 효성중공업
        "buyback_yield": 0.0,
    },
    "006260": {  # LS — LS그룹 지주
        "stakes": [("010120", 0.4600)],          # LS ELECTRIC
        "buyback_yield": 0.0,
    },
    "010060": {  # OCI홀딩스
        "stakes": [("456040", 0.4600)],          # OCI
        "buyback_yield": 0.0,
    },
}

# ─── v21 Sprint 1 모듈 (안전 import — 실패해도 앱 구동) ──────────────────
try:
    import macro_gate as _macro_gate
except Exception:
    _macro_gate = None
try:
    import data_quality as _data_quality
except Exception:
    _data_quality = None
try:
    import event_calendar as _event_calendar
except Exception:
    _event_calendar = None
try:
    import position_sizer as _position_sizer
except Exception:
    _position_sizer = None
try:
    from watchlist import WatchlistDB as _WatchlistDB
except Exception:
    _WatchlistDB = None
try:
    import notifier as _notifier
except Exception:
    _notifier = None

# ─── 로깅 설정 ──────────────────────────────────────────────────────────
logging.basicConfig(
    filename='quant_nexus_v20.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ─── High DPI 인식 (Windows 고해상도 흐림 현상 해결) ────────────────────
def _apply_dpi_awareness():
    """
    Windows 환경에서 DPI 스케일링을 비활성화하여 Tkinter 화면 흐림을 제거.
    Per-Monitor DPI v2 → System DPI → 구형 API 순서로 폴백.
    비-Windows 환경에서는 조용히 스킵.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # Windows 8.1+ : Per-Monitor DPI Aware (가장 선명)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        logging.info("[DPI] SetProcessDpiAwareness(2) — Per-Monitor v2 적용")
        return
    except Exception:
        pass
    try:
        import ctypes
        # Windows Vista~ : System DPI Aware
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        logging.info("[DPI] SetProcessDpiAwareness(1) — System DPI 적용")
        return
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
        logging.info("[DPI] SetProcessDPIAware() — 구형 API 적용")
    except Exception as e:
        logging.warning(f"[DPI] DPI 설정 실패(무시): {e}")

_apply_dpi_awareness()

# ─── 폰트 상수 (DPI-Safe, 한글 안티앨리어싱 최적화) ────────────────────
def _resolve_fonts() -> dict:
    """
    OS별 최적 폰트 선택:
      Windows → Malgun Gothic (한글) / Consolas (숫자/코드)
      macOS   → Apple SD Gothic Neo / Menlo
      Linux   → Noto Sans CJK KR / DejaVu Sans Mono
    반환값의 모든 항목은 tkinter font 튜플 형식.
    """
    if sys.platform == "win32":
        kr   = "Malgun Gothic"
        mono = "Consolas"
    elif sys.platform == "darwin":
        kr   = "Apple SD Gothic Neo"
        mono = "Menlo"
    else:
        kr   = "Noto Sans CJK KR"
        mono = "DejaVu Sans Mono"

    return {
        # ── 헤더 / 타이틀 ───────────────────────────────────────────────
        "TITLE":       (kr,   20, "bold"),
        "HEADER":      (kr,   11, "bold"),
        "SUBHEADER":   (kr,   10, "bold"),
        # ── 본문 / 레이블 ───────────────────────────────────────────────
        "BODY":        (kr,   10),
        "BODY_BOLD":   (kr,   10, "bold"),
        "SMALL":       (kr,    9),
        "SMALL_BOLD":  (kr,    9, "bold"),
        "TINY":        (kr,    8),
        # ── 데이터 / 숫자 (고정폭 유지) ─────────────────────────────────
        "MONO":        (mono, 10),
        "MONO_BOLD":   (mono, 10, "bold"),
        "MONO_SM":     (mono,  9),
        "MONO_SM_BD":  (mono,  9, "bold"),
        "MONO_TINY":   (mono,  8),
        # ── 트리뷰 전용 ─────────────────────────────────────────────────
        "TREE":        (kr,   10),
        "TREE_BOLD":   (kr,   10, "bold"),
        "TREE_HEAD":   (kr,   10, "bold"),
        # ── 버튼 ────────────────────────────────────────────────────────
        "BTN":         (kr,   10, "bold"),
        "BTN_SM":      (kr,    9, "bold"),
        # ── CAN SLIM 팝업 ────────────────────────────────────────────────
        "POPUP_TITLE": (kr,   15, "bold"),
        "POPUP_SUB":   (kr,   13, "bold"),
        "POPUP_SCORE": (kr,   10, "bold"),
        "POPUP_SMALL": (kr,    9),
        "POPUP_TINY":  (kr,    8),
    }

F = _resolve_fonts()   # 전역 폰트 딕셔너리 ─ 이하 코드에서 F["TITLE"] 등으로 참조

# ─── 필수 라이브러리 임포트 ─────────────────────────────────────────────
try:
    import yfinance as yf
    import xlsxwriter
    import pandas as pd
    import numpy as np
    import io
    try:
        _YF_CACHE_DIR = os.path.join(os.path.dirname(__file__), ".yfinance-cache")
        os.makedirs(_YF_CACHE_DIR, exist_ok=True)
        if hasattr(yf, "set_tz_cache_location"):
            yf.set_tz_cache_location(_YF_CACHE_DIR)
    except Exception as _e:
        logging.warning("[yf] cache dir init failed: %s", _e)
except ImportError:
    print("필수 라이브러리 설치 필요: pip install yfinance pandas xlsxwriter numpy")
    sys.exit(1)


def _fetch_stooq_history(ticker: str) -> pd.DataFrame | None:
    """Fetch daily US price history from Stooq as a Yahoo fallback."""
    try:
        sym = ticker.upper().replace(".", "-")
        url = f"https://stooq.com/q/d/l/?s={sym.lower()}.us&i=d"
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        if not raw or "Date,Open,High,Low,Close,Volume" not in raw:
            return None
        df = pd.read_csv(io.StringIO(raw))
        if df.empty or "Date" not in df.columns:
            return None
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        df.columns = [c.capitalize() for c in df.columns]
        needed = {"Open", "High", "Low", "Close", "Volume"}
        if not needed.issubset(df.columns):
            return None
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Close"])
        if df.empty or len(df) < 30:
            return None
        return df
    except Exception as e:
        logging.warning("[stooq] history failed %s: %s", ticker, e)
        return None

# ============================================================
# Toss Design System 컬러 팔레트
#   베이스: 화이트/라이트그레이 플랫 디자인
#   액센트: #3182F6 (Toss Blue)
#   텍스트: 5단계 그레이스케일
#   시맨틱: 그린/레드/옐로 — 데이터 포인트 컬러
# ============================================================
C = {
    # 기본 배경 / 패널 — Minimalismo Funcional B2B
    "BG":           "#F8F8F8",
    "PANEL":        "#FFFFFF",
    "SIDEBAR":      "#FFFFFF",
    "HEADER_BG":    "#FFFFFF",

    # 보더 / 디바이더
    "HIGHLIGHT":    "#FFFFFF",
    "SHADOW":       "#E9ECEF",
    "SHADOW_DEEP":  "#DEE2E6",

    # 텍스트 — 5단계 그레이스케일
    "TEXT_MAIN":    "#212529",
    "TEXT_SUB":     "#6C757D",
    "TEXT_LABEL":   "#ADB5BD",

    # 데이터 포인트 컬러
    "GREEN":        "#28A745",
    "RED":          "#DC3545",
    "GOLD":         "#FFC107",
    "ACCENT":       "#007BFF",
    "PURPLE":       "#6F42C1",
    "ORANGE":       "#FD7E14",

    # CAN SLIM 전용 시그널 컬러 (화이트 배경 대비 최적화)
    "CANSLIM_S1":   "#E6A800",   # ⭐⭐⭐⭐ BREAKOUT — 선명한 골드 (최상위 프리미엄)
    "CANSLIM_S2":   "#1A7D34",   # 🚀 MOMENTUM/STRONG LEADER — 딥그린
    "CANSLIM_S3":   "#2060A8",   # ⭐⭐ LEADER — 스틸블루 (다크→블루로 차별화)
    "CANSLIM_S4":   "#6C757D",   # 📊 WATCH LIST — 슬레이트그레이
    "CANSLIM_S5":   "#9EA8B3",   # ⏸ NEUTRAL — 미디엄그레이
    "CANSLIM_S6":   "#A85000",   # ⚠️ CAUTION/BEAR — 번트오렌지 (이전보다 어둡게)
    "CANSLIM_S7":   "#B02A37",   # 📉 AVOID — 다크레드 (이전보다 덜 자극적)

    # 트리뷰 선택
    "SELECT_BG":    "#E7F1FF",
    "SELECT_FG":    "#007BFF",
}

# ─── CAN SLIM 임계값 상수 (한 곳에서 조정) ────────────────────────────
CANSLIM = {
    "EPS_MIN_GROWTH":    0.25,   # C: 분기 EPS 최소 성장률 25%
    "EPS_STRONG":        0.50,   # C: 강한 성장 50%
    "ROE_MIN":           0.17,   # A: 최소 ROE 17%
    "HIGH52W_PCT":       0.05,   # N: 52주 신고가 대비 5% 이내
    "PIVOT_DAYS":        20,     # N: 컵핸들 피벗 감지 일수
    "VOL_BREAKOUT_MIN":  0.40,   # S: 돌파 거래량 40% 이상
    "RS_LEADER_MIN":     80,     # L: 주도주 RS Rating 최솟값
    "RS_LAGGARD_MAX":    40,     # L: 낙오주 RS Rating 상한
    "SCORE_CEIL_LAGGARD":50,     # Fail-Safe: 낙오주 점수 천장
    "SCORE_CEIL_MOMENTUM_OVERRIDE": 70,  # 극강 모멘텀 종목 FailSafe 완화 천장
    "SUPER_MULT_MIN":    1.20,   # 슈퍼 그로스 최소 승수
    "SUPER_MULT_MAX":    1.50,   # 슈퍼 그로스 최대 승수
    "BEAR_CAP":          0.50,   # M: Bear 시장 점수 상한 비율
}

# ════════════════════════════════════════════════════════════
# 딥테크 보정 대상 섹터 (수주·매출 기반 평가가 필요한 미래 산업)
# 이 섹터의 적자 종목은 EPS Fail-Safe를 면제하고 STORY_STOCK으로 라우팅한다.
# 섹터 키는 화면용 섹터 라벨과 정확히 일치해야 한다.
# ════════════════════════════════════════════════════════════
_DEEPTECH_SECTORS: set[str] = {
    "드론·우주",
    "위성·발사체",
    "양자보안·암호",
    "양자센서·하드웨어",
    "원전·SMR",
    "신재생·ESS",
    "자율주행·전장",
    "바이오 신약",
}


def _is_deeptech_story(ticker: str, row: dict) -> bool:
    """딥테크 보정 대상 여부.

    True 조건 (모두 충족):
      - row["Sector"] ∈ _DEEPTECH_SECTORS
      - row["_RevenueGrowth"] > 0 (매출 YoY 증가)
      - row["_MarketCap"] > 1000억원 (= 1e11)
    데이터 결측 시 보수적으로 False.
    """
    sector = row.get("Sector") or ""
    if sector not in _DEEPTECH_SECTORS:
        return False
    rev_growth = row.get("_RevenueGrowth")
    if rev_growth is None or rev_growth <= 0:
        return False
    mcap = row.get("_MarketCap") or 0
    if mcap <= 1e11:
        return False
    return True


# ─── 전략 가중치 — v20.1 전면 확장 ──────────────────────────────────────
#
# 설계 원칙:
#   ① 모든 팩터 키의 합계 = 1.0  (100점 예산 완전 분배)
#   ② 6개 기본 팩터 + 7개 보조 퀀트 + 6개 CAN SLIM 원칙 = 총 19키
#   ③ 모드별로 핵심 그룹에 예산이 집중되어 점수 변별력을 보장
#
# 키 그룹 설명:
#   [기본 퀀트]  momentum  fama_french  mean_reversion  quality  regime  smart_money
#   [보조 퀀트]  mtf  drawdown  volume  rs  price_target  short_int  math
#   [CAN SLIM]  cs_c(EPS가속)  cs_a(ROE)  cs_n(신고가)  cs_s(거래량)  cs_l(주도주)  cs_i(기관)
#
# patch-B2 (거버넌스): 가중치 변경 시 백테스트 sweep ID + Sharpe 인용 필수.
#   형식: `# Source: sweeps/<YYYY>Q<n>_<mode>.csv, IS sharpe=X.X, OOS=Y.Y, n=Z`
#   백테스트 없이 직관으로 조정 시 `# rationale-only, no backtest` 명시 강제.
#   현재 값(2026Q2)은 patch-01 중복 제거 + R6 추세군 상한 반영. IC 측정(patch-04) 후 재캘리브레이션 예정.
#   참고: Entry timing v5.1(line ~5127)은 "sweep 1080조합, sharpe +0.062" 인용 — 동등 수준 거버넌스 적용 목표.
STRATEGY_WEIGHTS: dict[str, dict[str, float]] = {

    # ── BALANCED: 고른 분산, 어느 팩터도 과도하지 않음 ─────────────────
    # NOTE(patch-01): cs_a/cs_s/cs_l/cs_i 중복 제거(P0) — 동일 raw score를
    #   각각 fama_french/volume/rs/smart_money에 흡수.
    "BALANCED": {
        # 기본 퀀트 (합 0.60 — 흡수분 +0.11)
        "momentum":       0.08,
        "fama_french":    0.13,   # +0.05 (cs_a 흡수)
        "mean_reversion": 0.07,
        "quality":        0.10,
        "regime":         0.08,
        "smart_money":    0.10,   # +0.02 (cs_i 흡수)
        # 보조 퀀트 (합 0.33 — sentiment 포함, volume/rs 흡수분 포함)
        "mtf":            0.04,
        "drawdown":       0.03,
        "volume":         0.08,   # +0.04 (cs_s 흡수)
        "rs":             0.08,   # +0.04 (cs_l 흡수)
        "price_target":   0.03,
        "short_int":      0.02,
        "math":           0.02,
        "sentiment":      0.03,
        # CAN SLIM (합 0.11) — 독립 신호 cs_c·cs_n만 유지
        "cs_c":           0.06,
        "cs_a":           0.00,   # 중복 제거 → fama_french 흡수
        "cs_n":           0.05,
        "cs_s":           0.00,   # 중복 제거 → volume 흡수
        "cs_l":           0.00,   # 중복 제거 → rs 흡수
        "cs_i":           0.00,   # 중복 제거 → smart_money 흡수
        # 단타 팩터 (비활성)
        "orb": 0.0, "nr7": 0.0, "bb_revert": 0.0,
    },

    # ── MOMENTUM: 12개월 모멘텀·거래량·신고가 극대화 ────────────────────
    # NOTE(patch-01): cs_a/cs_s/cs_l/cs_i 흡수.
    "MOMENTUM": {
        # 기본 퀀트 (합 0.52)
        "momentum":       0.15,   # ★ 핵심
        "fama_french":    0.07,   # +0.02 (cs_a 흡수)
        "mean_reversion": 0.04,
        "quality":        0.07,
        "regime":         0.11,   # 추세 방향 중시
        "smart_money":    0.09,   # +0.01 (cs_i 흡수)
        # 보조 퀀트 (합 0.33 — sentiment 포함)
        "mtf":            0.05,   # 멀티타임프레임 모멘텀
        "drawdown":       0.02,
        "volume":         0.12,   # +0.06 (cs_s 흡수)
        "rs":             0.08,   # +0.03 (cs_l 흡수)
        "price_target":   0.02,
        "short_int":      0.01,
        "math":           0.01,
        "sentiment":      0.02,
        # CAN SLIM (합 0.14) — 독립 신호만
        "cs_c":           0.05,
        "cs_a":           0.00,   # 중복 제거
        "cs_n":           0.09,   # ★★ 신고가·피벗 돌파
        "cs_s":           0.00,   # 중복 제거
        "cs_l":           0.00,   # 중복 제거
        "cs_i":           0.00,   # 중복 제거
        "orb": 0.0, "nr7": 0.0, "bb_revert": 0.0,
    },

    # ── VALUE: 가치 팩터·ROE·저평가 극대화 ──────────────────────────────
    # NOTE(patch-01): cs_a/cs_s/cs_l/cs_i 흡수.
    "VALUE": {
        # 기본 퀀트 (합 0.65)
        "momentum":       0.04,
        "fama_french":    0.27,   # ★★ +0.10 (cs_a 흡수) — ROE 단일화
        "mean_reversion": 0.10,   # ★ 평균회귀 — 저평가 복귀
        "quality":        0.15,   # ★ 이익률·부채 품질
        "regime":         0.04,
        "smart_money":    0.05,   # +0.02 (cs_i 흡수)
        # 보조 퀀트 (합 0.31 — sentiment 포함)
        "mtf":            0.02,
        "drawdown":       0.04,   # 리스크 관리
        "volume":         0.04,   # +0.02 (cs_s 흡수)
        "rs":             0.06,   # +0.04 (cs_l 흡수)
        "price_target":   0.07,   # ★ DCF 적정가 상승여력
        "short_int":      0.03,
        "math":           0.02,
        "sentiment":      0.03,
        # CAN SLIM (합 0.04) — 독립 신호만
        "cs_c":           0.02,
        "cs_a":           0.00,   # 중복 제거
        "cs_n":           0.02,
        "cs_s":           0.00,   # 중복 제거
        "cs_l":           0.00,   # 중복 제거
        "cs_i":           0.00,   # 중복 제거
        "orb": 0.0, "nr7": 0.0, "bb_revert": 0.0,
    },

    # ── CAN_SLIM: 오닐 7원칙 집중 — C·A·L에 예산 집중 ──────────────────
    # NOTE(patch-01): cs_a/cs_s/cs_l/cs_i 흡수. A/S/L 원칙은 보조 퀀트(fama_french/volume/rs)로 흡수되어 그대로 반영.
    "CAN_SLIM": {
        # 기본 퀀트 (합 0.49)
        "momentum":       0.08,
        "fama_french":    0.13,   # +0.09 (cs_a/A원칙 흡수)
        "mean_reversion": 0.03,
        "quality":        0.09,   # C·A 원칙 기저
        "regime":         0.10,   # M 원칙 (시장 방향)
        "smart_money":    0.06,   # +0.02 (cs_i 흡수)
        # 보조 퀀트 (합 0.32 — sentiment 포함)
        "mtf":            0.03,
        "drawdown":       0.02,
        "volume":         0.10,   # +0.05 (cs_s/S원칙 흡수)
        "rs":             0.11,   # +0.07 (cs_l/L원칙 흡수)
        "price_target":   0.01,
        "short_int":      0.01,
        "math":           0.02,
        "sentiment":      0.02,
        # CAN SLIM (합 0.19) — 독립 신호 cs_c·cs_n만 유지
        "cs_c":           0.12,   # ★★★ C: EPS 가속도
        "cs_a":           0.00,   # 중복 제거 → fama_french
        "cs_n":           0.07,   # ★ N: 신고가·피벗
        "cs_s":           0.00,   # 중복 제거 → volume
        "cs_l":           0.00,   # 중복 제거 → rs
        "cs_i":           0.00,   # 중복 제거 → smart_money
        "orb": 0.0, "nr7": 0.0, "bb_revert": 0.0,
    },

    # ── SCALPING: 단타/스윙 종목 스크리닝 — ORB·NR7·BB반등 집중 ────────
    # NOTE(patch-01): cs_a/cs_s/cs_l/cs_i 흡수.
    "SCALPING": {
        # 기본 퀀트 (합 0.31) — 단기 유효 팩터만 유지
        "momentum":       0.06,
        "fama_french":    0.02,   # +0.01 (cs_a 흡수)
        "mean_reversion": 0.05,
        "quality":        0.02,
        "regime":         0.06,
        "smart_money":    0.09,   # +0.01 (cs_i 흡수)
        # 보조 퀀트 (합 0.29)
        "mtf":            0.03,
        "drawdown":       0.02,
        "volume":         0.13,   # +0.05 (cs_s 흡수)
        "rs":             0.06,   # +0.03 (cs_l 흡수)
        "price_target":   0.01,
        "short_int":      0.01,
        "math":           0.01,
        "sentiment":      0.03,
        # CAN SLIM (합 0.08) — 독립 신호만
        "cs_c":           0.03,
        "cs_a":           0.00,   # 중복 제거
        "cs_n":           0.05,   # ★ 신고가 돌파
        "cs_s":           0.00,   # 중복 제거
        "cs_l":           0.00,   # 중복 제거
        "cs_i":           0.00,   # 중복 제거
        # 단타 팩터 (합 0.32) — ★★★ 핵심
        "orb":            0.14,   # ★★★ 전일 고가 돌파
        "nr7":            0.10,   # ★★ 변동폭 압축 돌파
        "bb_revert":      0.08,   # ★ BB 하단 반등
    },
}

# 런타임 합계 검증 (합≠1이면 즉시 오류 발생 — 개발 중 가중치 실수 방지)
for _mode, _w in STRATEGY_WEIGHTS.items():
    _total = round(sum(_w.values()), 6)
    assert abs(_total - 1.0) < 1e-4, (
        f"STRATEGY_WEIGHTS['{_mode}'] 합계={_total:.6f} ≠ 1.0 — "
        f"가중치 합이 1.0이 되도록 수정하세요."
    )

# R6: 추세군(momentum+mtf+drawdown+volume+rs+cs_n) 합 ≤ 0.55 강제.
# 학계 근거: 단일 신호군(추세) 과집중 시 momentum crash(Daniel-Moskowitz 2016) 노출 증폭.
# 추세 노출 상한 — 이를 넘기려면 의식적 결정 + 주석 근거 필요.
_TREND_FACTORS = ("momentum", "mtf", "drawdown", "volume", "rs", "cs_n")
for _mode, _w in STRATEGY_WEIGHTS.items():
    _trend = round(sum(_w.get(f, 0.0) for f in _TREND_FACTORS), 6)
    assert _trend <= 0.55 + 1e-4, (
        f"STRATEGY_WEIGHTS['{_mode}'] 추세군 합={_trend:.4f} > 0.55 — "
        f"R6 추세 노출 상한 위반. 가중치를 조정하거나 상한 변경 근거를 주석으로 명시하세요."
    )

# ─── 열 툴팁 ──────────────────────────────────────────────────────────
COLUMN_TOOLTIPS = {
    "TICKER":   "종목 코드\n주식을 식별하는 고유 심볼입니다.",
    "Sector":   "섹터명\n해당 종목이 속한 섹터입니다.",
    "Name":     "종목명\n회사의 이름입니다.",
    "Desc":     "업종/사업 설명\n종목의 주요 사업 내용 및 업종을 나타냅니다.",
    "Conv":     "확신도 (Conviction)\n19개 팩터의 합의 수준.\n"
                "• HIGH : 75%+ 팩터가 같은 방향 → 신뢰도 높음\n"
                "• MID  : 55~75% 합의 → 보통\n"
                "• LOW  : 55% 미만 → 팩터 간 상충, 주의 필요",
    "SRank":    "섹터 내 상대 순위\n동일 섹터 내에서의 백분위 위치.\n"
                "• Top 10% : 섹터 최강 종목\n"
                "• Top 25% : 상위권\n"
                "• Top 50% : 중위권\n"
                "• Bottom  : 하위권",
    "Price":    "현재가\n가장 최근 거래된 주가입니다.",
    "Score":    "CAN SLIM 종합 점수 (0~100)\n윌리엄 오닐 7원칙 + 퀀트 전략 융합.\n"
                "• 90+ : ⭐⭐⭐⭐ CAN SLIM BREAKOUT\n"
                "• 80+ : 🚀 HIGH MOMENTUM LEADER\n"
                "• 70+ : ⭐⭐ LEADER\n"
                "• 55+ : 📊 WATCH LIST\n"
                "• 40+ : ⏸ NEUTRAL\n"
                "• 30+ : ⚠️ LAGGARD (AVOID)\n"
                "• 30↓ : 📉 SELL / BEAR AVOID\n\n"
                "※ Fail-Safe: EPS<0 또는 RS<40 → 최대 50점",
    "Day%":     "일간 수익률\n전일 대비 가격 변화율.",
    "Mom12M":   "[N] 12개월 모멘텀\n52주 신고가 근접 여부 포함.",
    "MomScore": "[N+S] 모멘텀+거래량 확인 점수\n컵앤핸들 피벗 돌파 시 가산.",
    "Value":    "[A] 연간 실적 점수\nROE 17%+ 기준 Fama-French 팩터.",
    "Quality":  "[C+A] 실적 품질 점수\nEPS 가속도·ROE·이익률 기반.",
    "RSI":      "RSI (0~100)\n과매수/과매도 보조 지표.",
    "VWAP":     "[I] VWAP 괴리율\n기관 평균단가 대비 위치.",
    "ATR%":     "변동성 (ATR%)\n일평균 가격 변동폭 비율.",
    "Regime":   "[M] 시장 레짐\nBear 시장 → 개별 점수 최대 50% Cap.",
    "Signal":   "CAN SLIM 시그널\n7원칙 종합 판단. 슈퍼 그로스 승수 적용.",
    "Target":   "컨센서스 목표주가 & 괴리율\n네이버 증권 애널리스트 평균 목표주가.\n"
                "• DCF 적정가 대비 괴리율 = (DCF목표가 - 현재가) / 현재가 × 100\n"
                "• 양수: 상승 여력 존재  /  음수: 현재가가 목표 초과",
    "Cmte":     "7-페르소나 위원회 (Committee)\n7명의 투자 전문가 관점 합의 결과.\n"
                "• 형식: 찬성/7 (예: 5/7 ✓)\n"
                "• 5/7 이상: 매수 권고  /  3/7 이하: 관망/회피\n"
                "• 전문가: 성장주·가치주·모멘텀·리스크·섹터·퀀트·거시",
    "Reason":   "핵심 선별 이유 요약\n해당 종목이 왜 높은 점수를 받았는지 상위 요인.\n"
                "예: '⭐ SUPER GROWTH × 1.14  [C✅ A✅ L✅]'\n"
                "더블클릭 시 전체 CAN SLIM 상세 분석 창이 열립니다.",
}


# ============================================================
# 유틸리티 함수
# ============================================================
def safe_get(val, default=0):
    """None 또는 NaN 을 default 로 대체합니다."""
    if val is None:
        return default
    if isinstance(val, float) and np.isnan(val):
        return default
    return val


def _smooth_lerp(x: float, x_lo: float, x_hi: float, y_lo: float, y_hi: float) -> float:
    """Piecewise-linear smooth interpolation. Replaces hard threshold cliffs.
    x ≤ x_lo → y_lo, x ≥ x_hi → y_hi, else linear blend."""
    if x_hi <= x_lo:
        return y_lo
    if x <= x_lo:
        return y_lo
    if x >= x_hi:
        return y_hi
    t = (x - x_lo) / (x_hi - x_lo)
    return y_lo + t * (y_hi - y_lo)


def _smooth_band(x: float, anchors: list) -> float:
    """Multi-anchor piecewise-linear interpolation.
    anchors = [(x0, y0), (x1, y1), ...] sorted by x. Returns smooth y at given x."""
    if not anchors:
        return 0.0
    if x <= anchors[0][0]:
        return anchors[0][1]
    if x >= anchors[-1][0]:
        return anchors[-1][1]
    for i in range(len(anchors) - 1):
        x0, y0 = anchors[i]
        x1, y1 = anchors[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return anchors[-1][1]


def _normalize_div_yield(dy):
    """yfinance dividendYield는 티커마다 % 또는 decimal로 들어와서 통일이 안 됨.
    1.0 초과면 %로 보고 100으로 나눠서 decimal로 정규화."""
    if dy is None:
        return 0.0
    try:
        dy = float(dy)
        if np.isnan(dy):
            return 0.0
        return dy / 100.0 if dy > 1.0 else dy
    except (TypeError, ValueError):
        return 0.0


def safe_div(numerator, denominator, default=0.0):
    """0 나눗셈 방지 유틸리티입니다."""
    if denominator is None or denominator == 0:
        return default
    if numerator is None:
        return default
    return numerator / denominator


def _meanrev_overheat_penalty(rsi: float, bb_pos: float, regime: str) -> tuple[int, str | None]:
    """
    EntryStatus MeanRev 컴포지트의 과열 페널티 계산 (regime-gated).

    추세장(STRONG_BULL/BULL)에서 mean-revert factor는 역수익 부호이므로
    RSI 70+/BB 0.95+ 신호는 약감점(-4), 그 외는 -14/-10 페널티.
    돌려주는 값은 (점수, 태그) — 점수가 0이면 페널티 없음.
    """
    trending = regime in ("STRONG_BULL", "BULL")
    pts = 0
    tag: str | None = None
    if rsi >= 70:
        pts = -4 if trending else -14
        tag = "RSI 과열 (추세 유지)" if trending else "RSI 과열"
    if bb_pos > 0.95:
        bb_pen = -4 if trending else -10
        # 더 강한(더 작은) 페널티로 덮어쓰기
        if bb_pen < pts:
            pts = bb_pen
            tag = "BB 과확장 (추세 유지)" if trending else "BB 과확장"
    return pts, tag


def _compute_entry_status(
    *,
    mr: dict,
    vwap: dict,
    atr: dict,
    regime: dict,
    mom: dict,
    vol_a: dict,
    hist,
    cur: float,
    day_chg: float,
    fail_safe_triggered: bool = False,
    bear_cap_applied: bool = False,
) -> dict:
    """
    EntryStatus 진입 타이밍 점수 계산 (순수 함수, 백테스트/단위테스트 호출 가능).

    V5.1_TUNED 스윕 최적값 유지: base=40, STRONG≥50, NEUTRAL 30~49.
    팩터: MeanRev(RSI+BB+VWAP 통합) / TrendAlign(MA+Regime 통합) / 거래량 점프 /
          돌파+거래량 / MACD / 변동성 / 당일등락 / 안전장치.

    Returns:
        score:      int [0,100]
        status:     'STRONG' | 'NEUTRAL' | 'AVOID'
        label:      한국어 라벨 ('진입 강함' / '관망' / '진입 부적합')
        phrases:    list[str] — 카드 표시용 핵심 신호
        breakdown:  dict[str, {pts:int, tag:str}] — 팩터별 점수 분해
        signals:    dict — ma_aligned/vol_jump_up/atr_squeeze 등 중간 신호
    """
    _e_score = 40
    _phrases: list[str] = []

    _rsi_v = mr.get("rsi", 50.0)
    _bb_pos = mr.get("bb_position", 0.0)
    _macd_div = mr.get("macd_divergence", "NONE")
    _vwap_d = vwap.get("distance", 0.0)
    _atr_p = atr.get("atr_percent", 0.0)
    _reg = regime.get("regime", "SIDEWAYS")
    _pivot = mom.get("pivot_breakout", False)
    _s_conf = vol_a.get("s_confirmed", False)

    _ma_aligned = False
    _vol_jump_up = False
    _atr_squeeze = False
    try:
        _c = hist["Close"]
        _v = hist["Volume"]
        _o = hist["Open"]
        if len(_c) >= 200:
            _sma50  = float(_c.rolling(50).mean().iloc[-1])
            _sma200 = float(_c.rolling(200).mean().iloc[-1])
            _ma_aligned = bool(cur > _sma50 > _sma200)
        if len(_v) >= 20:
            _vol_avg20 = float(_v.rolling(20).mean().iloc[-1])
            _vol_jump_up = bool(
                float(_v.iloc[-1]) > _vol_avg20 * 2.0
                and float(_c.iloc[-1]) > float(_o.iloc[-1])
            )
        if len(_c) >= 30 and _atr_p > 0:
            h_, l_, c_ = hist["High"], hist["Low"], hist["Close"]
            tr = pd.concat([
                (h_ - l_).abs(),
                (h_ - c_.shift()).abs(),
                (l_ - c_.shift()).abs(),
            ], axis=1).max(axis=1)
            atr_series = (tr.rolling(14).mean() / c_) * 100
            _atr_avg30 = float(atr_series.rolling(30).mean().iloc[-1])
            _atr_squeeze = bool(_atr_p < _atr_avg30 * 0.8)
    except Exception:
        pass

    # 1) MeanRev 컴포지트
    mr_pts = 0
    mr_tag = None
    if   _rsi_v < 30: mr_pts = max(mr_pts, 16); mr_tag = "과매도 반등"
    elif _rsi_v < 40: mr_pts = max(mr_pts, 9);  mr_tag = mr_tag or "RSI 저점권"
    _oh_pts, _oh_tag = _meanrev_overheat_penalty(_rsi_v, _bb_pos, _reg)
    if _oh_pts < 0 and _oh_pts < mr_pts:
        mr_pts = _oh_pts
        mr_tag = _oh_tag
    if _bb_pos < -0.7 and mr_pts < 14:
        mr_pts = 14; mr_tag = "BB 하단"
    if mr_pts == 0 and -0.03 <= _vwap_d <= 0.02:
        mr_pts = 4; mr_tag = "VWAP 눌림"
    elif _vwap_d > 0.07 and mr_pts > -6:
        mr_pts = -6; mr_tag = "VWAP 과확장"
    _e_score += mr_pts
    if mr_tag and abs(mr_pts) >= 4:
        _phrases.append(mr_tag)

    # 2) TrendAlign 컴포지트
    trend_pts = 0
    trend_tag = None
    if _ma_aligned and _reg in ("STRONG_BULL", "BULL"):
        trend_pts = 8;  trend_tag = "강한 정배열"
    elif _ma_aligned:
        trend_pts = 5;  trend_tag = "정배열"
    elif _reg == "STRONG_BEAR":
        trend_pts = -15; trend_tag = "강한 약세장"
    elif _reg == "BEAR":
        trend_pts = -8;  trend_tag = "약세장"
    _e_score += trend_pts
    if trend_tag and abs(trend_pts) >= 6:
        _phrases.append(trend_tag)

    # 3) 거래량 점프 — pivot+s_conf(돌파 거래량)와 mutex: volume 이중 산입 방지
    _breakout_active = _pivot and _s_conf
    if _vol_jump_up and not _breakout_active:
        _e_score += 12; _phrases.append("거래량 점프")
    elif _atr_squeeze and _ma_aligned:
        _e_score += 4

    # 4) 돌파 + 거래량
    if _breakout_active:
        _e_score += 10; _phrases.append("거래량 동반 돌파")

    # 5) MACD
    if _macd_div == "BULLISH":
        _e_score += 3
    elif _macd_div == "BEARISH":
        _e_score -= 4

    # 6) 변동성 과대
    if _atr_p > 8.0:
        _e_score -= 10; _phrases.append("변동성 과대")

    # 7) 당일 등락
    if   day_chg >  0.07: _e_score -= 10; _phrases.append("급등 추격 주의")
    elif day_chg < -0.05: _e_score += 4;  _phrases.append("눌림 매수")

    # 8) 안전장치
    if fail_safe_triggered: _e_score -= 15
    if bear_cap_applied:    _e_score -= 10

    entry_score = max(0, min(100, int(_e_score)))

    # 팩터별 분해 (프론트 시각화)
    breakdown: dict = {}
    if mr_pts != 0: breakdown["MeanRev"] = {"pts": mr_pts, "tag": mr_tag or ""}
    if trend_pts != 0: breakdown["Trend"] = {"pts": trend_pts, "tag": trend_tag or ""}
    if _vol_jump_up and not _breakout_active: breakdown["Volume"] = {"pts": 12, "tag": "거래량 점프"}
    elif _atr_squeeze and _ma_aligned: breakdown["Volume"] = {"pts": 4, "tag": "변동성 수축"}
    if _breakout_active: breakdown["Breakout"] = {"pts": 10, "tag": "돌파"}
    if _macd_div == "BULLISH": breakdown["MACD"] = {"pts": 3, "tag": "골든크로스"}
    elif _macd_div == "BEARISH": breakdown["MACD"] = {"pts": -4, "tag": "데드크로스"}
    if _atr_p > 8.0: breakdown["Volatility"] = {"pts": -10, "tag": "변동성 과대"}
    if day_chg > 0.07: breakdown["DayChg"] = {"pts": -10, "tag": "급등 추격"}
    elif day_chg < -0.05: breakdown["DayChg"] = {"pts": 4, "tag": "눌림 매수"}

    if entry_score >= 50:
        status = "STRONG"; label = "진입 강함"
    elif entry_score >= 30:
        status = "NEUTRAL"; label = "관망"
    else:
        status = "AVOID"; label = "진입 부적합"

    return {
        "score": entry_score,
        "status": status,
        "label": label,
        "phrases": _phrases,
        "breakdown": breakdown,
        "signals": {
            "ma_aligned": _ma_aligned,
            "vol_jump_up": _vol_jump_up,
            "atr_squeeze": _atr_squeeze,
            "mr_pts": mr_pts, "mr_tag": mr_tag,
            "trend_pts": trend_pts, "trend_tag": trend_tag,
            "rsi": _rsi_v, "bb_pos": _bb_pos, "vwap_d": _vwap_d,
            "atr_p": _atr_p, "regime": _reg,
            "macd_div": _macd_div, "pivot": _pivot, "s_conf": _s_conf,
        },
    }


def _compute_entry_status_v2(
    *,
    mr: dict,
    vwap: dict,
    atr: dict,
    regime: dict,
    mom: dict,
    vol_a: dict,
    hist,
    cur: float,
    day_chg: float,
    fail_safe_triggered: bool = False,
    bear_cap_applied: bool = False,
) -> dict:
    """EntryStatus v2 — 백테스트 증거기반 재설계.

    검증: KOSPI100+S&P500 top100, 26주/78주 walk-forward 백테스트.
      26주: baseline IC +0.0127 (t=0.38) — FAIL
      78주: baseline IC -0.0072 — 부호 뒤집힘

    v2 설계 원칙:
      - 26w·78w 양쪽에서 양수 edge였던 시그널에만 가중 (pivot×s_conf, vol_jump_up, atr_squeeze, rsi_oversold)
      - ma_aligned(78w 음수), macd_bull(양쪽 음수), bb_upper(78w 음수) → 양수 가중 제거
      - mean-reversion + 거래량 동반 돌파 중심으로 재구성
      - 추세 강도 가산점 제거(증거 부재). 약세장 페널티는 유지(78w에서 약함, 26w에서 강함, 평균 음수).
    """
    _e_score = 50
    _phrases: list[str] = []

    _rsi_v = mr.get("rsi", 50.0)
    _bb_pos = mr.get("bb_position", 0.0)
    _macd_div = mr.get("macd_divergence", "NONE")
    _vwap_d = vwap.get("distance", 0.0)
    _atr_p = atr.get("atr_percent", 0.0)
    _reg = regime.get("regime", "SIDEWAYS")
    _pivot = mom.get("pivot_breakout", False)
    _s_conf = vol_a.get("s_confirmed", False)

    _ma_aligned = False
    _vol_jump_up = False
    _atr_squeeze = False
    _degraded = False
    try:
        _c = hist["Close"]
        _v = hist["Volume"]
        _o = hist["Open"]
        if len(_c) >= 200:
            _sma50  = float(_c.rolling(50).mean().iloc[-1])
            _sma200 = float(_c.rolling(200).mean().iloc[-1])
            _ma_aligned = bool(cur > _sma50 > _sma200)
        if len(_v) >= 20:
            _vol_avg20 = float(_v.rolling(20).mean().iloc[-1])
            _vol_jump_up = bool(
                float(_v.iloc[-1]) > _vol_avg20 * 2.0
                and float(_c.iloc[-1]) > float(_o.iloc[-1])
            )
        if len(_c) >= 30 and _atr_p > 0:
            h_, l_, c_ = hist["High"], hist["Low"], hist["Close"]
            tr = pd.concat([
                (h_ - l_).abs(),
                (h_ - c_.shift()).abs(),
                (l_ - c_.shift()).abs(),
            ], axis=1).max(axis=1)
            atr_series = (tr.rolling(14).mean() / c_) * 100
            _atr_avg30 = float(atr_series.rolling(30).mean().iloc[-1])
            _atr_squeeze = bool(_atr_p < _atr_avg30 * 0.8)
    except Exception as _ex:
        import logging as _lg
        _lg.warning("entry_status_v2 derived signals degraded: %s", _ex)
        _degraded = True

    breakdown: dict = {}

    # 1) 거래량 동반 돌파 — 양쪽 시계 양수. vol_jump와 mutex (double-count 차단).
    _breakout_active = bool(_pivot and _s_conf)
    if _breakout_active:
        _e_score += 12
        _phrases.append("거래량 동반 돌파")
        breakdown["Breakout"] = {"pts": 12, "tag": "거래량 동반 돌파"}

    # 2) 거래량 점프 — pivot+s_conf와 mutex
    if _vol_jump_up and not _breakout_active:
        _e_score += 10
        _phrases.append("거래량 점프")
        breakdown["Volume"] = {"pts": 10, "tag": "거래량 점프"}

    # 3) RSI 과매도 — regime-conditional 가중 (factor 성능이 regime에 의존)
    #    BULL/STRONG_BULL에선 mean-reversion 약함 → 작은 가중
    #    SIDEWAYS/BEAR에선 mean-reversion 강함 (78w 데이터) → 큰 가중
    _trending = _reg in ("BULL", "STRONG_BULL")
    mr_pts = 0
    mr_tag = None
    if _rsi_v < 30:
        mr_pts = 5 if _trending else 12
        mr_tag = "과매도 반등"
    elif _rsi_v < 40:
        mr_pts = 3 if _trending else 6
        mr_tag = "RSI 저점권"

    # 3-b) RSI 과열 페널티 (Stage 1 유지 — regime gate)
    if _rsi_v >= 70:
        if _trending:
            mr_pts = -3
            mr_tag = "RSI 과열(추세 유지)"
        else:
            mr_pts = -10
            mr_tag = "RSI 과열"
    if _bb_pos > 0.95 and not _trending:
        if mr_pts > -8:
            mr_pts = -8
            mr_tag = "BB 상단 과확장"

    if mr_pts != 0:
        _e_score += mr_pts
        if abs(mr_pts) >= 4 and mr_tag:
            _phrases.append(mr_tag)
        breakdown["MeanRev"] = {"pts": mr_pts, "tag": mr_tag or ""}

    # 3-c) MA 정배열 — trending regime에서만 가산 (26w 양수, 78w 음수의 평균)
    #     BULL 환경 한정으로 +5 (26w +0.0017 edge 회복, 78w 영향 최소)
    if _ma_aligned and _trending:
        _e_score += 5
        breakdown["Trend"] = {"pts": 5, "tag": "추세 정배열"}

    # 4) 변동성 수축 — 양쪽 양수 (edge +0.0007 / +0.0061)
    if _atr_squeeze:
        _e_score += 4
        breakdown["Squeeze"] = {"pts": 4, "tag": "변동성 수축"}

    # 5) 변동성 과대 페널티
    if _atr_p > 8.0:
        _e_score -= 6
        _phrases.append("변동성 과대")
        breakdown["Volatility"] = {"pts": -6, "tag": "변동성 과대"}

    # 6) 약세장 페널티
    if _reg == "STRONG_BEAR":
        _e_score -= 12
        _phrases.append("강한 약세장")
        breakdown["Regime"] = {"pts": -12, "tag": "강한 약세장"}
    elif _reg == "BEAR":
        _e_score -= 6
        breakdown["Regime"] = {"pts": -6, "tag": "약세장"}

    # 7) MACD 베어 페널티만 유지 (BULL은 양쪽 시계에서 음수 edge라 제거)
    if _macd_div == "BEARISH":
        _e_score -= 3
        breakdown["MACD"] = {"pts": -3, "tag": "데드크로스"}

    # 8) 당일 급등 추격 페널티 — ATR-normalized (종목별 변동성 보정)
    _daychg_trigger = False
    if _atr_p > 0:
        # day_chg가 ATR의 1.5σ 이상이면 페널티 (atr_p는 %, day_chg는 fraction → 100배 단위 통일)
        _daychg_trigger = bool((day_chg * 100.0) / _atr_p > 1.5)
    else:
        _daychg_trigger = bool(day_chg > 0.07)
    if _daychg_trigger:
        _e_score -= 8
        _phrases.append("급등 추격 주의")
        breakdown["DayChg"] = {"pts": -8, "tag": "급등 추격"}

    # 9) 안전장치
    if fail_safe_triggered:
        _e_score -= 15
    if bear_cap_applied:
        _e_score -= 10

    entry_score = max(0, min(100, int(_e_score)))

    if entry_score >= 55:
        status = "STRONG"; label = "진입 강함"
    elif entry_score >= 25:
        status = "NEUTRAL"; label = "관망"
    else:
        status = "AVOID"; label = "진입 부적합"

    return {
        "score": entry_score,
        "status": status,
        "label": label,
        "phrases": _phrases,
        "breakdown": breakdown,
        "signals": {
            "ma_aligned": _ma_aligned,
            "vol_jump_up": _vol_jump_up,
            "atr_squeeze": _atr_squeeze,
            "mr_pts": mr_pts, "mr_tag": mr_tag,
            "trend_pts": 0, "trend_tag": None,
            "rsi": _rsi_v, "bb_pos": _bb_pos, "vwap_d": _vwap_d,
            "atr_p": _atr_p, "regime": _reg,
            "macd_div": _macd_div, "pivot": _pivot, "s_conf": _s_conf,
            "version": "v2",
            "degraded": _degraded,
        },
    }


# ──────────────────────────────────────────────────────────────────────
# v3 Hysteresis & Persistence (월가 패널 P0-1)
# ──────────────────────────────────────────────────────────────────────

_ENTRY_STATUS_CACHE: dict = {}


def _apply_status_hysteresis(
    ticker: str,
    score: int,
    prev_score: int | None = None,
    prev_status: str | None = None,
    consecutive: int = 0,
    *,
    strong_in: int = 55,
    strong_out: int = 50,
    avoid_in: int = 25,
    avoid_out: int = 30,
    persistence: int = 2,
) -> tuple[str, str, int]:
    """Hysteresis + N-day persistence — flip-flop 차단.

    - STRONG 진입: score>=strong_in AND prev_score>=strong_out AND consecutive>=persistence
    - STRONG 유지: prev_status=STRONG AND score>=strong_out
    - AVOID 진입: score<avoid_in AND consecutive>=persistence
    - AVOID 유지: prev_status=AVOID AND score<=avoid_out
    Returns (status, label, new_consecutive).
    """
    in_strong = score >= strong_in
    in_avoid = score < avoid_in

    if prev_status == "STRONG":
        if score >= strong_out:
            cons = consecutive + 1 if in_strong else 0
            return "STRONG", "진입 강함", cons
    if prev_status == "AVOID":
        if score <= avoid_out:
            cons = consecutive + 1 if in_avoid else 0
            return "AVOID", "진입 부적합", cons

    if in_strong:
        cons = consecutive + 1 if prev_status != "STRONG" else 1
        if (prev_score is not None and prev_score >= strong_out and cons >= persistence) \
                or consecutive + 1 >= persistence:
            return "STRONG", "진입 강함", cons
        return "NEUTRAL", "관망(진입 대기)", cons
    if in_avoid:
        cons = consecutive + 1 if prev_status != "AVOID" else 1
        if cons >= persistence:
            return "AVOID", "진입 부적합", cons
        return "NEUTRAL", "관망(이탈 대기)", cons

    return "NEUTRAL", "관망", 0


def _percentile_rank(scores: dict) -> dict:
    """Universe 내 cross-sectional percentile rank (동률 평균 rank). 0.0~1.0."""
    if not scores:
        return {}
    items = sorted(scores.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 1:
        return {items[0][0]: 1.0}
    ranks: dict = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = avg_rank / (n - 1)
        for k in range(i, j + 1):
            ranks[items[k][0]] = round(pct, 6)
        i = j + 1
    return ranks


def _apply_hysteresis_to_result(ticker: str, result: dict) -> dict:
    """캐시된 직전 상태로 hysteresis 적용. ENTRY_HYSTERESIS=0이면 skip."""
    if os.getenv("ENTRY_HYSTERESIS", "1") == "0":
        return result
    score = int(result.get("score", 50))
    prev = _ENTRY_STATUS_CACHE.get(ticker, {})
    status, label, cons = _apply_status_hysteresis(
        ticker, score,
        prev_score=prev.get("score"),
        prev_status=prev.get("status"),
        consecutive=int(prev.get("consecutive", 0)),
    )
    result = dict(result)
    result["status"] = status
    result["label"] = label
    _ENTRY_STATUS_CACHE[ticker] = {
        "score": score, "status": status, "consecutive": cons,
    }
    return result


def _compute_entry_status_dispatch(*, ticker: str | None = None, **kwargs) -> dict:
    """Feature flag dispatcher — v2 default (regime-conditional). ENTRY_SCORE_V1=1로 legacy 복귀.
    ENTRY_HYSTERESIS=1 (default) + ticker 전달 시 hysteresis 적용."""
    if os.getenv("ENTRY_SCORE_V1", "0") == "1":
        result = _compute_entry_status(**kwargs)
    else:
        result = _compute_entry_status_v2(**kwargs)
    if ticker and os.getenv("ENTRY_HYSTERESIS", "1") != "0":
        result = _apply_hysteresis_to_result(ticker, result)
    return result


def rate_limit(max_per_second: float):
    """호출 빈도를 제한하는 데코레이터 (스레드 안전)."""
    min_interval = 1.0 / max_per_second
    last_called = [0.0]
    lock = threading.Lock()

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with lock:
                elapsed = time.time() - last_called[0]
                wait = min_interval - elapsed
                if wait > 0:
                    time.sleep(wait)
                last_called[0] = time.time()
            result = func(*args, **kwargs)
            return result
        return wrapper
    return decorator


# ============================================================
# 캐시 클래스 (무결성 검증 포함)
# ============================================================
class DataCache:
    """
    피클 기반 파일 캐시.
    - 파일 해시를 별도로 저장하여 오염된 캐시를 자동 폐기합니다.
    - max_age_minutes 를 초과한 항목은 만료 처리합니다.
    """
    REQUIRED_KEYS = {"Ticker", "Name", "Price", "TotalScore", "Signal", "_AvgVol20"}
    NAME_FIXUPS = {
        "LITE": "루멘텀",
    }

    def __init__(self, cache_dir: str = "./cache_v19"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, ticker: str) -> str:
        # 윈도우/POSIX 모두 안전한 파일명: 알파넘+언더스코어 외 전부 치환.
        # 너무 긴 키나 특수문자 포함 키는 md5 해시 접미로 충돌 회피.
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", ticker)
        if safe != ticker or len(safe) > 80:
            digest = hashlib.md5(ticker.encode("utf-8")).hexdigest()[:8]
            safe = f"{safe[:60]}_{digest}"
        return os.path.join(self.cache_dir, f"{safe}.pkl")

    def prune(self, max_age_minutes: int = 60) -> int:
        """만료된 캐시 파일을 디스크에서 제거. 시작 시 1회 호출 권장."""
        removed = 0
        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        try:
            for filename in os.listdir(self.cache_dir):
                if not filename.endswith(".pkl"):
                    continue
                fp = os.path.join(self.cache_dir, filename)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(fp))
                    if mtime < cutoff:
                        os.remove(fp)
                        removed += 1
                except OSError:
                    pass
        except OSError:
            pass
        return removed

    def _hash_file(self, path: str) -> str:
        h = hashlib.md5()
        with open(path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()

    def get(self, ticker: str, max_age_minutes: int = 5):
        path = self._path(ticker)
        if not os.path.exists(path):
            return None
        # 만료 확인
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        if datetime.now() - mtime > timedelta(minutes=max_age_minutes):
            return None
        # 무결성 확인
        try:
            with open(path, "rb") as f:
                raw = f.read()
            data = pickle.loads(raw)
            # 필수 키 검증
            if not isinstance(data, dict) or not self.REQUIRED_KEYS.issubset(data.keys()):
                logging.warning(f"[Cache] 무결성 실패: {ticker}")
                os.remove(path)
                return None
            base_ticker = str(data.get("Ticker") or ticker).split("__")[0].upper()
            fixed_name = self.NAME_FIXUPS.get(base_ticker)
            if fixed_name and data.get("Name") != fixed_name:
                data["Name"] = fixed_name
            return data
        except Exception as e:
            logging.warning(f"[Cache] 로드 실패({ticker}): {e}")
            try:
                os.remove(path)
            except OSError:
                pass
            return None

    def set(self, ticker: str, data: dict):
        path = self._path(ticker)
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, path)
        except Exception as e:
            logging.error(f"[Cache] 저장 실패({ticker}): {e}")
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def clear(self):
        for filename in os.listdir(self.cache_dir):
            if filename.endswith(".pkl"):
                try:
                    os.remove(os.path.join(self.cache_dir, filename))
                except OSError:
                    pass


# ============================================================
# 툴팁 클래스
# ============================================================
class ToolTip:
    """마우스 호버 시 스큐어 스타일 툴팁 표시."""
    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tooltip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None):
        self._hide()  # 이전 tooltip 파괴 (메모리 누수 방지)
        x = self.widget.winfo_rootx() + 28
        y = self.widget.winfo_rooty() + 28
        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")
        self.tooltip.attributes("-topmost", True)
        outer = tk.Frame(self.tooltip, bg=C["SHADOW_DEEP"], bd=0)
        outer.pack()
        inner = tk.Frame(outer, bg=C["PANEL"], bd=0, padx=10, pady=8)
        inner.pack(padx=1, pady=1)
        tk.Label(inner, text=self.text, justify=tk.LEFT,
                 bg=C["PANEL"], fg=C["TEXT_MAIN"],
                 font=F["SMALL"], wraplength=300).pack()

    def _hide(self, _event=None):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


class TreeviewToolTip:
    """Treeview 헤더용 스큐어 툴팁."""
    def __init__(self, treeview: ttk.Treeview, tooltips: dict):
        self.tv = treeview
        self.tooltips = tooltips
        self.tooltip: tk.Toplevel | None = None
        self.current_col: str | None = None
        treeview.bind("<Motion>", self._on_motion)
        treeview.bind("<Leave>", self._hide)

    def _on_motion(self, event):
        if self.tv.identify_region(event.x, event.y) != "heading":
            self._hide()
            self.current_col = None
            return
        raw = self.tv.identify_column(event.x)
        if raw == "#0":
            col = "TICKER"
        else:
            idx = int(raw.lstrip("#")) - 1
            cols = self.tv["columns"]
            col = cols[idx] if 0 <= idx < len(cols) else None
        if col and col != self.current_col:
            self.current_col = col
            self._show(event, col)

    def _show(self, event, col: str):
        self._hide()
        if col not in self.tooltips:
            return
        x, y = event.x_root + 15, event.y_root + 15
        self.tooltip = tk.Toplevel(self.tv)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")
        self.tooltip.attributes("-topmost", True)
        outer = tk.Frame(self.tooltip, bg=C["SHADOW_DEEP"])
        outer.pack()
        title_bar = tk.Frame(outer, bg=C["HEADER_BG"])
        title_bar.pack(fill=tk.X, padx=1, pady=(1, 0))
        tk.Label(title_bar, text=f"  {col}",
                 bg=C["HEADER_BG"], fg=C["ACCENT"],
                 font=F["BODY_BOLD"], pady=5).pack(anchor="w")
        body = tk.Frame(outer, bg=C["PANEL"])
        body.pack(padx=1, pady=(0, 1))
        tk.Label(body, text=self.tooltips[col], justify=tk.LEFT,
                 bg=C["PANEL"], fg=C["TEXT_SUB"],
                 font=F["SMALL"], padx=10, pady=8, wraplength=280).pack()

    def _hide(self, _event=None):
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


# ============================================================
# 전략 패턴(Strategy Pattern): 19개 퀀트 전략 클래스
# ============================================================
class WallStreetQuantStrategies:
    """
    월가 퀀트 펀드 19개 전략 모음.

    각 메서드는 독립적으로 테스트/교체 가능하도록 분리되어 있습니다.
    반환값은 항상 dict 이며, 예외 시 기본값 dict 를 반환합니다.
    """

    # ── 1. Fama-French / CAN SLIM [A] Annual Earnings ────────────────────
    def fama_french(self, hist: pd.DataFrame, info: dict) -> dict:
        """
        [A] Annual Earnings Growth (CAN SLIM A 원칙) + Fama-French 팩터
        ─────────────────────────────────────────────────────────────────
        오닐 원칙: ROE 최소 17% 이상. 미달 시 엄격 감점.
        연간 EPS 성장 + 수익성 + 부채 보수성 → 재무 건전 우량주 선별.

        CAN SLIM A 점수:
          • ROE ≥ 25%  → +18점 (최우량)
          • ROE ≥ 17%  → +10점 (기준 충족)
          • ROE < 17%  → -12점 (오닐 기준 미달: 엄격 감점)
          • ROE < 0    → -25점 (적자 기업: 즉시 낙오주)
        """
        result = {
            "size_score": 0, "value_score": 0,
            "profitability_score": 0, "investment_score": 0,
            "factor_alpha": 0.0,
            # CAN SLIM A 전용
            "roe":           0.0,
            "roe_pass":      False,   # ROE 17% 이상 여부
            "a_score":       0,       # CAN SLIM A 원칙 점수
        }
        try:
            # ── [A] ROE 기준: 오닐 17% ────────────────────────────────
            roe = safe_get(info.get("returnOnEquity"), 0.0)
            result["roe"] = roe
            if roe >= 0.25:
                result["profitability_score"] += 18
                result["roe_pass"]  = True
                result["a_score"]  += 18
            elif roe >= 0.17:
                result["profitability_score"] += 10
                result["roe_pass"]  = True
                result["a_score"]  += 10
            elif roe >= 0.10:
                result["profitability_score"] -= 6
                result["a_score"]  -= 6          # 기준 미달: 감점
            elif roe >= 0:
                result["profitability_score"] -= 12
                result["a_score"]  -= 12
            else:                                 # 적자
                result["profitability_score"] -= 25
                result["a_score"]  -= 25

            # ── Size Factor ───────────────────────────────────────────
            cap = safe_get(info.get("marketCap"), 0)
            if cap > 0:
                if cap < 2e9:    result["size_score"] = 15
                elif cap < 10e9: result["size_score"] = 10
                elif cap < 50e9: result["size_score"] = 5

            # ── Value Factor ──────────────────────────────────────────
            pb = safe_get(info.get("priceToBook"), 0)
            pe = safe_get(info.get("trailingPE"), 0)
            if pb > 0:
                if pb < 1.5:   result["value_score"] += 10
                elif pb < 3:   result["value_score"] += 5
                elif pb > 8:   result["value_score"] -= 5
            if 0 < pe < 15:    result["value_score"] += 8
            elif 0 < pe < 25:  result["value_score"] += 4
            elif pe > 50:      result["value_score"] -= 5

            # ── Gross Margin 보조 ─────────────────────────────────────
            gm = safe_get(info.get("grossMargins"), 0)
            if gm > 0.40:      result["profitability_score"] += 5
            elif gm > 0.25:    result["profitability_score"] += 2

            # ── Investment Factor (부채 보수성) ───────────────────────
            dte = safe_get(info.get("debtToEquity"), 100)
            if dte < 50:       result["investment_score"] = 10
            elif dte < 100:    result["investment_score"] = 5
            elif dte > 200:    result["investment_score"] = -5

            result["factor_alpha"] = (
                result["size_score"]          * 0.12 +
                result["value_score"]         * 0.18 +
                result["profitability_score"] * 0.45 +   # A 원칙 강화
                result["investment_score"]    * 0.25
            )
        except Exception as e:
            logging.error(f"[Strategy] fama_french: {e}")
        return result

    # ── 2. Momentum / CAN SLIM [N] New Highs + [S] Supply & Demand ───────
    def momentum(self, hist: pd.DataFrame) -> dict:
        """
        [N] New Products / New Highs (오닐 N 원칙)
        + Carhart Momentum + 컵앤핸들 피벗 포인트 감지
        ─────────────────────────────────────────────────────────────────
        핵심 추가 로직:
          • 52주 신고가 대비 5% 이내 → 'Near 52W High' 강력 보너스
          • 최근 20일 내 신고가 돌파 → 컵앤핸들 피벗 돌파 신호
          • Carhart 12M 모멘텀 (최근 1개월 제외)
          • RS Rating(0~100) 계산: 시장 대비 상대 강도 점수화

        점수 체계:
          +20 : 52주 신고가 5% 이내
          +15 : 최근 20일 신고가 갱신(피벗 돌파)
          +25 : mom_12m > 50%
          -15 : mom_12m < -20%
        """
        result = {
            "mom_12m": 0.0, "mom_6m": 0.0, "mom_3m": 0.0, "mom_1m": 0.0, "mom_12m_estimated": False,
            "acceleration": 0.0, "momentum_score": 0, "rank": "NEUTRAL",
            # [N] 신고가 관련
            "high_52w":          0.0,
            "dist_from_52w_high": 1.0,   # 52주 고가 대비 거리 (0=신고가)
            "near_52w_high":     False,   # 5% 이내
            "pivot_breakout":    False,   # 최근 20일 내 신고가 돌파
            "rs_rating":         50,      # 0~100 RS 등급
        }
        try:
            closes = hist["Close"]
            n = len(closes)
            if n < 21:
                return result

            cur = float(closes.iloc[-1])

            # ── 52주 신고가 분석 [N] ──────────────────────────────────
            high_52w = float(hist.get("High", hist["Close"]).rolling(min(252, n)).max().iloc[-1])
            result["high_52w"] = high_52w
            if high_52w > 0:
                dist = (high_52w - cur) / high_52w
                result["dist_from_52w_high"] = dist
                result["near_52w_high"] = dist <= CANSLIM["HIGH52W_PCT"]

            # ── 컵앤핸들 피벗 돌파 감지 [N] ──────────────────────────
            pivot_window = CANSLIM["PIVOT_DAYS"]
            if n >= pivot_window + 5:
                # 피벗 이전 20일 최고가
                prev_high = float(closes.iloc[-(pivot_window + 5):-5].max())
                recent_high = float(closes.iloc[-5:].max())
                result["pivot_breakout"] = (recent_high > prev_high * 1.01
                                            and cur > prev_high * 1.005)

            # ── 모멘텀 수익률 계산 ────────────────────────────────────
            def _ret(periods):
                if n >= periods:
                    p = float(closes.iloc[-periods])
                    return (cur - p) / p if p > 0 else 0.0
                elif n > 5:
                    p = float(closes.iloc[0])
                    return (cur - p) / p if p > 0 else 0.0
                return 0.0

            result["mom_1m"] = _ret(21)
            result["mom_3m"] = _ret(63)
            result["mom_6m"] = _ret(126)

            if n >= 252:
                p12 = float(closes.iloc[-252])
                # True 12-month total return (현재가 vs 252거래일 전)
                # 이전엔 Carhart 12-1 (최근 1개월 제외)을 썼지만 사용자 직관과 불일치 → 변경
                result["mom_12m"] = (cur - p12) / p12 if p12 > 0 else 0.0
            else:
                # 데이터 부족 시 외삽하지 않고 실제 보유 기간 수익률 사용
                # + 신뢰도 할인 (데이터 부족 페널티)
                if n >= 126:
                    result["mom_12m"] = result["mom_6m"] * 0.8   # 6M 기준 보수적
                elif n >= 63:
                    result["mom_12m"] = result["mom_3m"] * 0.6   # 3M 기준 더 보수적
                else:
                    result["mom_12m"] = result["mom_1m"] * 0.3
                result["mom_12m_estimated"] = True

            # ── 가속도 ────────────────────────────────────────────────
            m6, m3 = result["mom_6m"], result["mom_3m"]
            result["acceleration"] = m3 - (m6 / 2.0) if m6 != 0 else result["mom_1m"] - (result["mom_3m"] / 3.0)

            # ── RS Rating (0~100): 1년 수익률 기반 대략적 등급 ────────
            m12 = result["mom_12m"]
            if m12 > 1.0:      result["rs_rating"] = 98
            elif m12 > 0.60:   result["rs_rating"] = 92
            elif m12 > 0.40:   result["rs_rating"] = 85
            elif m12 > 0.25:   result["rs_rating"] = 78
            elif m12 > 0.10:   result["rs_rating"] = 65
            elif m12 > 0.00:   result["rs_rating"] = 55
            elif m12 > -0.10:  result["rs_rating"] = 40
            elif m12 > -0.25:  result["rs_rating"] = 28
            else:              result["rs_rating"] = 15
            # 외삽 데이터 → RS Rating을 50 방향으로 수축 (불확실성 반영)
            if result["mom_12m_estimated"]:
                result["rs_rating"] = int(50 + (result["rs_rating"] - 50) * 0.6)

            # ── 점수 산출 ─────────────────────────────────────────────
            score = 0

            # [N] 52주 신고가 근접 보너스
            if result["near_52w_high"]:
                score += 20
            elif result["dist_from_52w_high"] < 0.10:
                score += 10
            elif result["dist_from_52w_high"] > 0.30:
                score -= 8   # 고점 대비 30% 이상 하락 → 낙오주 신호

            # [N] 컵앤핸들 피벗 돌파
            if result["pivot_breakout"]:
                score += 15

            # 12M 모멘텀 점수
            if m12 > 0.50:      score += 25
            elif m12 > 0.30:    score += 20
            elif m12 > 0.15:    score += 15
            elif m12 > 0.05:    score += 10
            elif m12 > 0:       score += 5
            elif m12 < -0.30:   score -= 20
            elif m12 < -0.20:   score -= 15
            elif m12 < -0.10:   score -= 10
            elif m12 < 0:       score -= 5

            # 추세 지속성
            if m3 > 0.02 and m6 > 0.02:     score += 10
            elif m3 > 0 and m6 > 0:         score += 5
            elif m3 < -0.02 and m6 < -0.02: score -= 10

            # 가속도
            acc = result["acceleration"]
            if acc > 0.10:      score += 10
            elif acc > 0.05:    score += 8
            elif acc > 0.02:    score += 4
            elif acc < -0.10:   score -= 8
            elif acc < -0.05:   score -= 5

            if result["mom_1m"] > 0.10:    score += 5
            elif result["mom_1m"] < -0.10: score -= 3

            result["momentum_score"] = score

            # 등급
            if score >= 35:       result["rank"] = "STRONG_MOMENTUM"
            elif score >= 22:     result["rank"] = "POSITIVE"
            elif score >= 10:     result["rank"] = "MILD_POSITIVE"
            elif score <= -15:    result["rank"] = "NEGATIVE"
            elif score <= -5:     result["rank"] = "MILD_NEGATIVE"
        except Exception as e:
            logging.error(f"[Strategy] momentum: {e}")
        return result

    # ── 3. Mean Reversion ──────────────────────────────────────────────────
    def mean_reversion(self, hist: pd.DataFrame) -> dict:
        """
        평균회귀 전략 (볼린저 밴드 + RSI + MACD + Z-Score)
        ─────────────────────────────────────────────
        가격이 평균에서 이탈한 정도를 3가지 지표로 교차 확인.
        볼린저 하단 + RSI<30 + MACD 불리시 다이버전스 동시 발생 시 최고점.
        """
        result = {
            "bb_position": 0.0, "bb_squeeze": False,
            "rsi": 50.0, "rsi_signal": "NEUTRAL",
            "macd_hist": 0.0, "macd_divergence": "NONE",
            "z_score": 0.0, "score": 0, "signal": "NEUTRAL",
        }
        try:
            if len(hist) < 50:
                return result
            c = hist["Close"]
            cur = c.iloc[-1]

            # Bollinger Bands (20,2)
            sma20 = c.rolling(20).mean()
            std20 = c.rolling(20).std()
            ub = sma20 + 2 * std20
            lb = sma20 - 2 * std20
            bw = ub.iloc[-1] - lb.iloc[-1]
            if bw > 0:
                result["bb_position"] = 2 * (cur - lb.iloc[-1]) / bw - 1
            bw_ratio = ((ub - lb) / sma20).rolling(20).mean().iloc[-1]
            result["bb_squeeze"] = (bw / sma20.iloc[-1]) < bw_ratio * 0.7

            # RSI (14)
            delta = c.diff()
            gain = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
            rs = gain / (loss + 1e-9)
            rsi_val = float((100 - 100 / (1 + rs)).iloc[-1])
            result["rsi"] = rsi_val
            if rsi_val < 30:    result["rsi_signal"] = "OVERSOLD"
            elif rsi_val > 70:  result["rsi_signal"] = "OVERBOUGHT"
            elif rsi_val < 40:  result["rsi_signal"] = "WEAK"
            elif rsi_val > 60:  result["rsi_signal"] = "STRONG"

            # MACD (12,26,9)
            ema12 = c.ewm(span=12, adjust=False).mean()
            ema26 = c.ewm(span=26, adjust=False).mean()
            macd  = ema12 - ema26
            sig   = macd.ewm(span=9, adjust=False).mean()
            hist_macd = macd - sig
            result["macd_hist"] = float(hist_macd.iloc[-1])
            if len(hist) >= 20:
                p_up   = c.iloc[-1] > c.iloc[-20]
                m_up   = hist_macd.iloc[-1] > hist_macd.iloc[-20]
                if p_up and not m_up:    result["macd_divergence"] = "BEARISH"
                elif not p_up and m_up:  result["macd_divergence"] = "BULLISH"

            # Z-Score (50일)
            mean50 = c.rolling(50).mean().iloc[-1]
            std50  = c.rolling(50).std().iloc[-1]
            if std50 > 0:
                result["z_score"] = (cur - mean50) / std50

            # 점수
            score = 0
            bp = result["bb_position"]
            if bp < -0.8:    score += 15
            elif bp > 0.8:   score -= 10
            if rsi_val < 30:       score += 15
            elif rsi_val < 40:     score += 8
            elif rsi_val > 70:     score -= 12
            if result["macd_divergence"] == "BULLISH":  score += 12
            elif result["macd_divergence"] == "BEARISH": score -= 10
            z = result["z_score"]
            if z < -2:     score += 15
            elif z < -1:   score += 8
            elif z > 2:    score -= 12
            if result["bb_squeeze"]: score += 5

            result["score"] = score
            if score >= 25:    result["signal"] = "STRONG_BUY"
            elif score >= 10:  result["signal"] = "BUY"
            elif score <= -15: result["signal"] = "SELL"
        except Exception as e:
            logging.error(f"[Strategy] mean_reversion: {e}")
        return result

    # ── 4. ATR 리스크 관리 ────────────────────────────────────────────────
    @staticmethod
    def _find_swing_points(series: pd.Series, order: int = 5) -> list:
        """로컬 극값(스윙 포인트) 탐색. order=양쪽 N봉 비교."""
        pts = []
        arr = series.values
        for i in range(order, len(arr) - order):
            if all(arr[i] <= arr[i - j] for j in range(1, order + 1)) and \
               all(arr[i] <= arr[i + j] for j in range(1, order + 1)):
                pts.append(("low", i, float(arr[i])))
            if all(arr[i] >= arr[i - j] for j in range(1, order + 1)) and \
               all(arr[i] >= arr[i + j] for j in range(1, order + 1)):
                pts.append(("high", i, float(arr[i])))
        return pts

    @staticmethod
    def _find_support_resistance(hist: pd.DataFrame, cur: float, atr14: float) -> tuple:
        """
        다중 방법으로 지지/저항 수준 탐색.
        반환: (support_levels, resistance_levels) — 각각 (price, strength) 리스트
        """
        h, l, c, v = hist["High"], hist["Low"], hist["Close"], hist["Volume"]
        n = len(hist)
        supports = []
        resistances = []
        cur = float(cur) if np.isfinite(cur) else 0.0
        atr14 = float(atr14) if np.isfinite(atr14) and atr14 > 0 else 0.0

        # 1) 스윙 포인트 기반 (최근 60일)
        lb = min(n, 60)
        for tp, idx, px in WallStreetQuantStrategies._find_swing_points(l.iloc[-lb:], order=3):
            if tp == "low" and px < cur:
                age = lb - idx  # 최근일수록 강함
                strength = max(0.3, 1.0 - age / lb)
                supports.append((px, strength))
        for tp, idx, px in WallStreetQuantStrategies._find_swing_points(h.iloc[-lb:], order=3):
            if tp == "high" and px > cur:
                age = lb - idx
                strength = max(0.3, 1.0 - age / lb)
                resistances.append((px, strength))

        # 2) 이동평균 지지/저항
        for period in [20, 50, 200]:
            if n >= period:
                ma = float(c.rolling(period).mean().iloc[-1])
                s = {20: 0.6, 50: 0.8, 200: 1.0}[period]
                if ma < cur and (cur - ma) / cur < 0.10:
                    supports.append((ma, s))
                elif ma > cur and (ma - cur) / cur < 0.10:
                    resistances.append((ma, s))

        # 3) 거래량 가중 가격 클러스터 (Volume Profile 근사)
        if n >= 60:
            _hist60 = hist.iloc[-60:]
            _c60, _v60 = _hist60["Close"].values, _hist60["Volume"].values
            _vsum = _v60.sum()
            if _vsum > 0:
                # 가격을 ATR 단위 버킷으로 그룹화
                bucket = atr14 if atr14 > 0 else 1.0
                from collections import defaultdict
                vol_at = defaultdict(float)
                for px, vol in zip(_c60, _v60):
                    if not (np.isfinite(px) and np.isfinite(vol)):
                        continue
                    key = round(px / bucket) * bucket
                    vol_at[key] += vol
                # 거래량 상위 3개 레벨
                top_levels = sorted(vol_at.items(), key=lambda x: -x[1])[:3]
                for px, vol in top_levels:
                    s = min(1.0, vol / _vsum * 10)  # 거래량 비중 기반 강도
                    if px < cur * 0.99:
                        supports.append((px, s))
                    elif px > cur * 1.01:
                        resistances.append((px, s))

        # 정렬: 지지선은 현재가에 가까운 순, 저항선도 가까운 순
        supports.sort(key=lambda x: -x[0])   # 높은(가까운) 것 먼저
        resistances.sort(key=lambda x: x[0])  # 낮은(가까운) 것 먼저
        return supports, resistances

    @staticmethod
    def _backtest_win_rate(hist: pd.DataFrame, stop_pct: float, t1_pct: float,
                           holding_days: int = 10) -> float:
        """
        과거 일봉으로 간이 승률 시뮬레이션.
        매 종가 진입 → holding_days 이내에 t1 도달 vs stop 도달 비교.
        """
        c = hist["Close"].values
        h = hist["High"].values
        l = hist["Low"].values
        n = len(c)
        if n < 60:
            return 0.0

        wins = 0
        trials = 0
        # 최근 120일~20일 구간에서 시뮬레이션 (최근 20일은 미래 데이터이므로 제외)
        start = max(0, n - 120)
        end = n - holding_days
        for i in range(start, end):
            entry = c[i]
            if entry <= 0:
                continue
            stop_px = entry * (1 - stop_pct)
            t1_px = entry * (1 + t1_pct)
            trials += 1
            hit_t1 = False
            hit_stop = False
            for j in range(1, min(holding_days + 1, n - i)):
                if l[i + j] <= stop_px:
                    hit_stop = True
                    break
                if h[i + j] >= t1_px:
                    hit_t1 = True
                    break
            if hit_t1:
                wins += 1
        return wins / trials if trials > 0 else 0.0

    def atr_risk(self, hist: pd.DataFrame) -> dict:
        """
        스윙 트레이딩용 손절/목표가 (일봉 종가 기준).
        다중 지지/저항 분석 + 과거 승률 시뮬레이션으로 확률 극대화.
        """
        result = {
            "atr_14": 0.0, "atr_percent": 0.0,
            "stop_loss_long": 0.0, "take_profit_1": 0.0, "take_profit_2": 0.0,
            "vol_regime": "NORMAL", "size_suggestion": "NORMAL",
            "rr_ratio": 0.0, "stop_method": "ATR", "win_rate": 0.0,
        }
        try:
            if len(hist) < 14:
                return result
            hist = hist.copy()
            for col in ["High", "Low", "Close", "Volume"]:
                if col in hist.columns:
                    hist[col] = pd.to_numeric(hist[col], errors="coerce")
            hist = hist.replace([np.inf, -np.inf], np.nan)
            hist = hist.dropna(subset=["High", "Low", "Close"])
            if len(hist) < 14:
                return result
            h, l, c = hist["High"], hist["Low"], hist["Close"]
            tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
            atr14 = float(tr.ewm(alpha=1/14, adjust=False).mean().iloc[-1])
            if not np.isfinite(atr14) or atr14 <= 0:
                return result
            result["atr_14"] = atr14
            cur = float(c.iloc[-1])
            if not np.isfinite(cur) or cur <= 0:
                return result
            result["atr_percent"] = (atr14 / cur) * 100
            if not np.isfinite(result["atr_percent"]):
                result["atr_percent"] = 0.0

            # ── 변동성 레짐 ──
            if len(tr) >= 50:
                atr_ratio = float(tr.rolling(20).mean().iloc[-1] / (tr.rolling(50).mean().iloc[-1] + 1e-9))
                if np.isfinite(atr_ratio) and atr_ratio > 1.5:
                    result["vol_regime"] = "HIGH"; result["size_suggestion"] = "REDUCE"
                elif np.isfinite(atr_ratio) and atr_ratio < 0.7:
                    result["vol_regime"] = "LOW"; result["size_suggestion"] = "INCREASE"

            # ── 다중 지지/저항 탐색 ──
            supports, resistances = self._find_support_resistance(hist, cur, atr14)

            # ── 손절가 결정: 가장 가까운 강한 지지선 ──
            stop_atr = cur - 2 * atr14
            if supports:
                # 강도 가중 점수로 최적 지지선 선택
                best_sup = max(supports, key=lambda x: x[1] * (1 - abs(cur - x[0]) / cur))
                stop_support = best_sup[0] - 0.3 * atr14  # 지지선 아래 여유분
                # 현재가 대비 3~12% 범위 내에서만 유효
                dist_pct = (cur - stop_support) / cur
                if 0.03 <= dist_pct <= 0.12:
                    stop = stop_support
                    result["stop_method"] = "지지선"
                else:
                    stop = stop_atr
                    result["stop_method"] = "ATR"
            else:
                stop = stop_atr
                result["stop_method"] = "ATR"

            risk = cur - stop
            if risk <= 0:
                risk = atr14
                stop = cur - risk

            # ── 목표가 결정: 가장 가까운 저항선 + 피보나치 ──
            # 52주 고가
            high_52w = float(h.iloc[-min(len(h), 250):].max())
            # 스윙 범위 피보나치
            lb = min(len(l), 40)
            swing_low = float(l.iloc[-lb:].min())
            swing_high = float(h.iloc[-lb:].max())
            fib_range = swing_high - swing_low
            fib_1618 = (swing_high + fib_range * 0.618) if fib_range > 0 else cur + 3 * atr14
            fib_2618 = (swing_high + fib_range * 1.618) if fib_range > 0 else cur + 5 * atr14

            # T1: 가까운 저항선 > 피보나치 1.618 > ATR 폴백
            t1_candidates = []
            for px, s in resistances[:3]:
                if px > cur * 1.02:
                    t1_candidates.append(px)
            if fib_1618 > cur * 1.02:
                t1_candidates.append(fib_1618)
            t1 = min(t1_candidates) if t1_candidates else cur + 3 * atr14

            # T2: 52주 고가 > 피보나치 2.618 > 먼 저항선
            t2_candidates = []
            if high_52w > t1 * 1.01:
                t2_candidates.append(high_52w)
            if fib_2618 > t1 * 1.01:
                t2_candidates.append(fib_2618)
            for px, s in resistances:
                if px > t1 * 1.05:
                    t2_candidates.append(px)
                    break
            t2 = min(t2_candidates) if t2_candidates else cur + 5 * atr14

            # ── 레짐 보정 (백테스트: 변동성 HIGH 시 3.0×ATR 손절 최적) ──
            if result["vol_regime"] == "HIGH":
                stop = min(stop, cur - 3.0 * atr14)
                risk = cur - stop

            # ── R:R 최소 1.5:1 보장 (백테스트: 1.5:1 이상 수익성 확인) ──
            if risk > 0 and (t1 - cur) / risk < 1.5:
                t1 = cur + risk * 1.5
            if risk > 0 and (t2 - cur) / risk < 2.5:
                t2 = cur + risk * 2.5

            rr = (t1 - cur) / risk if risk > 0 else 0

            # ── 과거 승률 시뮬레이션 (백테스트 최적: 15일 보유) ──
            stop_pct = risk / cur
            t1_pct = (t1 - cur) / cur
            win_rate = self._backtest_win_rate(hist, stop_pct, t1_pct, holding_days=15)

            result["stop_loss_long"] = stop
            result["take_profit_1"] = t1
            result["take_profit_2"] = t2
            result["rr_ratio"] = round(rr, 2)
            result["win_rate"] = round(win_rate * 100, 1)

        except Exception as e:
            logging.error(f"[Strategy] atr_risk: {e}")
        return result

    # ── 5. VWAP 분석 ──────────────────────────────────────────────────────
    def vwap_analysis(self, hist: pd.DataFrame) -> dict:
        """
        VWAP (Volume Weighted Average Price) 20일 롤링 기준.
        기관 평균단가 대비 현재가 위치로 과매수/저가 판단.
        """
        result = {"vwap": 0.0, "distance": 0.0, "above": False, "signal": "NEUTRAL"}
        try:
            if len(hist) < 5:
                return result
            n = min(20, len(hist))
            tp  = (hist["High"] + hist["Low"] + hist["Close"]) / 3
            vol = hist["Volume"]
            vs  = float(vol.tail(n).sum())
            if vs > 0:
                result["vwap"] = float((tp * vol).tail(n).sum()) / vs
            cur = float(hist["Close"].iloc[-1])
            if result["vwap"] > 0:
                d = (cur - result["vwap"]) / result["vwap"]
                result["distance"] = d
                result["above"]    = cur > result["vwap"]
                if d > 0.03:    result["signal"] = "ABOVE_STRONG"
                elif d > 0:     result["signal"] = "ABOVE"
                elif d < -0.03: result["signal"] = "BELOW_WEAK"
                else:           result["signal"] = "BELOW"
        except Exception as e:
            logging.error(f"[Strategy] vwap_analysis: {e}")
        return result

    # ── 6. Market Regime / CAN SLIM [M] Market Direction ─────────────────
    def market_regime(self, hist: pd.DataFrame) -> dict:
        """
        [M] Market Direction (오닐 M 원칙) — 시장 방향이 최우선
        ─────────────────────────────────────────────────────────────────
        오닐 원칙: "시장을 이기는 장사는 없다.
                   Bear 시장에서는 아무리 좋은 종목도 사지 마라."

        핵심: Bear / Strong Bear 시장 → 개별 최종 점수에 50% Cap 강제 적용
             (이 플래그를 _analyze_ticker 에서 활용)

        ADX + SMA 기반 레짐 분류:
          • STRONG_BULL : ADX>25 + 가격>SMA20>SMA50  → score +20
          • BULL        : ADX>20 + 가격>SMA50         → score +12
          • SIDEWAYS    : ADX≤20                       → score  +4
          • BEAR        : 가격<SMA50                   → score -15 + M Cap 발동
          • STRONG_BEAR : 가격<SMA20<SMA50             → score -25 + M Cap 발동
        """
        result = {
            "regime":       "SIDEWAYS",
            "trend_strength": 0,
            "adx":          0.0,
            "score":        0,
            # CAN SLIM M 전용
            "m_bear_cap":   False,   # True → 최종 점수 50% Cap 발동
            "m_label":      "[M] SIDEWAYS",
        }
        try:
            if len(hist) < 50:
                return result
            c, h, l = hist["Close"], hist["High"], hist["Low"]
            pdm = h.diff().clip(lower=0)
            ndm = (-l.diff()).clip(lower=0)
            pdm = pdm.where(pdm > ndm, 0)
            ndm = ndm.where(ndm > pdm, 0)
            tr  = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
            atr14 = tr.ewm(alpha=1/14, adjust=False).mean()
            pdi   = 100 * pdm.ewm(alpha=1/14, adjust=False).mean() / (atr14 + 1e-9)
            mdi   = 100 * ndm.ewm(alpha=1/14, adjust=False).mean() / (atr14 + 1e-9)
            dx    = 100 * (pdi - mdi).abs() / (pdi + mdi + 1e-9)
            adx   = float(dx.ewm(alpha=1/14, adjust=False).mean().iloc[-1])
            result["adx"] = adx if not np.isnan(adx) else 0.0

            sma20 = float(c.rolling(20).mean().iloc[-1])
            sma50 = float(c.rolling(50).mean().iloc[-1])
            cur   = float(c.iloc[-1])

            # 레짐 분류
            score = 0
            if adx > 25 and cur > sma20 > sma50:
                result["regime"] = "STRONG_BULL"; result["trend_strength"] = 90
                score = 20; result["m_label"] = "[M] STRONG_BULL ✅"
            elif adx > 20 and cur > sma50:
                result["regime"] = "BULL"; result["trend_strength"] = 55
                score = 12; result["m_label"] = "[M] BULL ✅"
            elif cur > sma50:
                result["regime"] = "SIDEWAYS_BULL"; result["trend_strength"] = 30
                score = 4; result["m_label"] = "[M] SIDEWAYS (Leaning Bull)"
            elif adx > 25 and cur < sma20 < sma50:
                result["regime"] = "STRONG_BEAR"; result["trend_strength"] = -90
                score = -25; result["m_bear_cap"] = True
                result["m_label"] = "[M] STRONG_BEAR 🚫 — 50% Cap Active"
            elif cur < sma50:
                result["regime"] = "BEAR"; result["trend_strength"] = -55
                score = -15; result["m_bear_cap"] = True
                result["m_label"] = "[M] BEAR 🚫 — 50% Cap Active"
            else:
                result["regime"] = "SIDEWAYS"; result["trend_strength"] = 0
                score = 0; result["m_label"] = "[M] SIDEWAYS"

            result["score"] = score

        except Exception as e:
            logging.error(f"[Strategy] market_regime: {e}")
        return result

    # ── 7. Quality Factor ──────────────────────────────────────────────────
    def quality_factor(self, info: dict) -> dict:
        """
        AQR 스타일 Quality Factor.
        ROE, 이익률, 부채비율, 유동비율로 재무 건전성 평가.
        """
        result = {"quality_score": 0, "profitability": "MEDIUM", "safety": "MEDIUM", "earnings_quality": 0}
        try:
            score = 0
            # patch-C1: 중복 제거 — grossMargins/debtToEquity는 fama_french에서 이미 평가.
            #           ROE도 fama_french + cs_a에서 평가. quality는 영업이익률/유동비율/현금흐름만 담당.
            om   = safe_get(info.get("operatingMargins"), safe_get(info.get("profitMargins"), 0))
            cr   = safe_get(info.get("currentRatio"), 1)
            ocf  = safe_get(info.get("operatingCashflow"), 0)
            rev  = safe_get(info.get("totalRevenue"), 1)

            # 이익률 (영업이익률만 — grossMargins는 fama_french 흡수)
            if om > 0.20:     score += 12; result["profitability"] = "HIGH"
            elif om > 0.10:   score += 6
            elif om < 0:      score -= 8; result["profitability"] = "LOW"

            # 재무 안전성 (유동비율만 — debtToEquity는 fama_french 흡수)
            if cr > 2:        score += 5; result["safety"] = "HIGH"
            elif cr < 1:      score -= 5; result["safety"] = "LOW"

            # 현금흐름 품질 (영업CF / 매출)
            if rev > 0 and ocf > 0:
                cf_ratio = ocf / rev
                if cf_ratio > 0.15:   score += 5
                elif cf_ratio < 0.05: score -= 3

            result["quality_score"]    = score
            result["earnings_quality"] = min(100, max(0, 50 + score))
        except Exception as e:
            logging.error(f"[Strategy] quality_factor: {e}")
        return result

    # ── 8. Smart Money Flow ────────────────────────────────────────────────
    def smart_money_flow(self, hist: pd.DataFrame) -> dict:
        """
        세력 수급 분석 (A/D Line + OBV + MFI).
        거래량과 가격의 방향성 일치 여부로 기관 매집/배분 파악.
        """
        result = {"ad": 0, "obv_trend": "NEUTRAL", "mfi": 50.0, "signal": "NEUTRAL", "score": 0}
        try:
            if len(hist) < 20:
                return result
            h, l, c, v = hist["High"], hist["Low"], hist["Close"], hist["Volume"]
            clv = ((c - l) - (h - c)) / (h - l + 1e-9)
            ad  = (clv * v).cumsum()
            ad_sma = ad.rolling(10).mean()
            if ad.iloc[-1] > ad_sma.iloc[-1] and ad.iloc[-1] > ad.iloc[-10]:
                result["ad"] = 1
            elif ad.iloc[-1] < ad_sma.iloc[-1] and ad.iloc[-1] < ad.iloc[-10]:
                result["ad"] = -1

            obv = (np.sign(c.diff()) * v).cumsum()
            result["obv_trend"] = "BULLISH" if obv.iloc[-1] > obv.rolling(10).mean().iloc[-1] else "BEARISH"

            tp  = (h + l + c) / 3
            mf  = tp * v
            pos = mf.where(tp > tp.shift(1), 0).rolling(14).sum()
            neg = mf.where(tp < tp.shift(1), 0).rolling(14).sum()
            result["mfi"] = float((100 - 100 / (1 + pos / (neg + 1e-9))).iloc[-1])

            score = 0
            if result["ad"] == 1:            score += 10
            elif result["ad"] == -1:         score -= 10
            if result["obv_trend"] == "BULLISH": score += 8
            else:                            score -= 5
            score += _smooth_band(result["mfi"], [
                (10.0, 12.0), (20.0, 10.0), (35.0, 3.0),
                (50.0, 0.0),
                (65.0, -3.0), (80.0, -10.0), (90.0, -12.0),
            ])

            result["score"] = score
            if score >= 15:   result["signal"] = "ACCUMULATION"
            elif score <= -10: result["signal"] = "DISTRIBUTION"
        except Exception as e:
            logging.error(f"[Strategy] smart_money_flow: {e}")
        return result

    # ── 9. Multi-Timeframe Confluence ─────────────────────────────────────
    def mtf_confluence(self, hist: pd.DataFrame) -> dict:
        """
        단기/중기/장기 이동평균 정배열 교차 확인.
        모든 시간대가 BULLISH 일 때 가장 강력한 진입 신호.
        """
        result = {"short": "NEUTRAL", "medium": "NEUTRAL", "long": "NEUTRAL",
                  "score": 0, "signal": "MIXED"}
        try:
            if len(hist) < 50:
                return result
            c = hist["Close"]
            cur = float(c.iloc[-1])
            s5  = float(c.rolling(5).mean().iloc[-1])
            s10 = float(c.rolling(10).mean().iloc[-1])
            s20 = float(c.rolling(20).mean().iloc[-1])
            s50 = float(c.rolling(50).mean().iloc[-1])

            if cur > s5 > s10 > s20:   result["short"]  = "BULLISH"
            elif cur < s5 < s10 < s20: result["short"]  = "BEARISH"
            if s20 > s50 and cur > s50: result["medium"] = "BULLISH"
            elif s20 < s50 and cur < s50: result["medium"] = "BEARISH"
            if len(c) >= 200:
                s200 = float(c.rolling(200).mean().iloc[-1])
                if s50 > s200 and cur > s200:  result["long"] = "BULLISH"
                elif s50 < s200 and cur < s200: result["long"] = "BEARISH"

            bull = sum(v == "BULLISH" for v in [result["short"], result["medium"], result["long"]])
            bear = sum(v == "BEARISH" for v in [result["short"], result["medium"], result["long"]])
            if bull == 3:   result["score"] = 25; result["signal"] = "STRONG_BULLISH"
            elif bull == 2: result["score"] = 15; result["signal"] = "BULLISH"
            elif bear == 3: result["score"] = -20; result["signal"] = "STRONG_BEARISH"
            elif bear == 2: result["score"] = -10; result["signal"] = "BEARISH"
        except Exception as e:
            logging.error(f"[Strategy] mtf_confluence: {e}")
        return result

    # ── 10. Drawdown Risk (Bridgewater) ───────────────────────────────────
    def drawdown_risk(self, hist: pd.DataFrame) -> dict:
        """
        레이 달리오 Bridgewater 스타일 낙폭 분석.
        현재 MDD 크기에 따라 페널티 부과, 회복 중이면 보너스.

        페널티: MDD > 30% → -20점
        """
        result = {"max_dd": 0.0, "current_dd": 0.0, "recovery": 0.0,
                  "score": 0, "risk": "NORMAL"}
        try:
            if len(hist) < 50:
                return result
            c = hist["Close"]
            rolling_max = c.expanding().max()
            dds = (c - rolling_max) / rolling_max
            result["max_dd"]     = float(dds.min())
            result["current_dd"] = float(dds.iloc[-1])
            if len(dds) >= 20:
                d = dds.iloc[-20:]
                if d.iloc[-1] > d.iloc[0]:
                    result["recovery"] = float(d.iloc[-1] - d.iloc[0])

            score = 0
            cdd = abs(result["current_dd"])
            if cdd > 0.30:   score -= 20; result["risk"] = "EXTREME"
            elif cdd > 0.20: score -= 15; result["risk"] = "HIGH"
            elif cdd > 0.10: score -= 8;  result["risk"] = "ELEVATED"
            elif cdd > 0.05: score -= 3;  result["risk"] = "MODERATE"
            else:            score += 5;  result["risk"] = "LOW"
            if result["recovery"] > 0.05: score += 5
            result["score"] = score
        except Exception as e:
            logging.error(f"[Strategy] drawdown_risk: {e}")
        return result

    # ── 11. Volume Anomaly / CAN SLIM [S] Supply & Demand ────────────────
    def volume_anomaly(self, hist: pd.DataFrame) -> dict:
        """
        [S] Supply and Demand (오닐 S 원칙) — 거래량 확인 돌파
        ─────────────────────────────────────────────────────────────────
        오닐 원칙: "돌파는 반드시 평균 거래량의 40~50% 이상으로 확인해야 한다.
                   거래량 없는 상승은 가짜 돌파다."

        점수 체계:
          • 가격↑ + 거래량 ≥ 150% (50%↑) → +18점 (S 원칙 완전 충족)
          • 가격↑ + 거래량 ≥ 140% (40%↑) → +13점 (S 원칙 충족)
          • 가격↑ + 거래량 < 80%          → -15점 (가짜 돌파 강력 페널티)
          • 가격↓ + 거래량 폭증           → -18점 (기관 투매)
          • 거래량 급감                   → -8점  (관심 소멸)

        추가: 가격 상승 + 거래량 미확인 → 'Unconfirmed Breakout' 태그
        """
        result = {
            "ratio":      1.0,
            "trend":      "NORMAL",
            "divergence": False,
            "score":      0,
            "signal":     "NEUTRAL",
            # CAN SLIM S 전용
            "s_confirmed":        False,  # S 원칙 충족
            "unconfirmed_break":  False,  # 거래량 없는 상승 경고
            "breakout_vol_ratio": 1.0,    # 돌파 시 거래량 배수
        }
        try:
            if len(hist) < 30:
                return result

            v = hist["Volume"]
            c = hist["Close"]
            n = len(c)

            v5  = float(v.tail(5).mean())
            v20 = float(v.tail(20).mean())
            v50 = float(v.tail(50).mean()) if n >= 50 else v20

            vol_ratio = v5 / v20 if v20 > 0 else 1.0
            result["ratio"] = vol_ratio
            result["breakout_vol_ratio"] = vol_ratio

            pc5  = (float(c.iloc[-1]) - float(c.iloc[-5])) / float(c.iloc[-5]) if float(c.iloc[-5]) > 0 else 0.0
            pc1  = (float(c.iloc[-1]) - float(c.iloc[-2])) / float(c.iloc[-2]) if n >= 2 and float(c.iloc[-2]) > 0 else 0.0

            score = 0
            vol_thresh_strong = 1.0 + CANSLIM["VOL_BREAKOUT_MIN"] + 0.10  # 150%
            vol_thresh_min    = 1.0 + CANSLIM["VOL_BREAKOUT_MIN"]          # 140%

            # ── [S] 돌파 거래량 확인 ──────────────────────────────────
            if pc5 > 0.02:   # 가격 상승 중
                if vol_ratio >= vol_thresh_strong:         # 50%+ 폭증
                    score += 18
                    result["trend"]       = "CONFIRMED_BREAKOUT"
                    result["signal"]      = "STRONG_S_CONFIRMED"
                    result["s_confirmed"] = True
                elif vol_ratio >= vol_thresh_min:          # 40%+ 증가
                    score += 13
                    result["trend"]       = "BREAKOUT"
                    result["signal"]      = "S_CONFIRMED"
                    result["s_confirmed"] = True
                elif vol_ratio >= 1.10:                    # 10%~40% 증가 (부족)
                    score += 2
                    result["trend"]       = "WEAK_BREAKOUT"
                    result["signal"]      = "S_WEAK"
                else:                                      # 거래량 없는 상승
                    score -= 15
                    result["trend"]       = "UNCONFIRMED"
                    result["signal"]      = "UNCONFIRMED_BREAKOUT"
                    result["unconfirmed_break"] = True
                    result["divergence"]  = True

            elif pc5 < -0.02:   # 가격 하락 중
                if vol_ratio >= vol_thresh_strong:
                    score -= 18
                    result["trend"]  = "INSTITUTIONAL_SELL"
                    result["signal"] = "STRONG_DISTRIBUTION"
                elif vol_ratio >= vol_thresh_min:
                    score -= 12
                    result["trend"]  = "DISTRIBUTION"
                    result["signal"] = "DISTRIBUTION"
                elif vol_ratio >= 1.10:
                    score -= 8
                    result["trend"]  = "SELLING"
                    result["signal"] = "MILD_DISTRIBUTION"

            # 거래량 급감 (관심 소멸)
            if vol_ratio < 0.50:
                score -= 8
                result["trend"]  = "DRY_UP"
                result["signal"] = "NO_INTEREST"

            result["score"] = score

        except Exception as e:
            logging.error(f"[Strategy] volume_anomaly: {e}")
        return result

    # ── 12. Relative Strength / CAN SLIM [L] Leader or Laggard ───────────
    def relative_strength(self, hist: pd.DataFrame) -> dict:
        """
        [L] Leader or Laggard (오닐 L 원칙) — RS Rating 80+ 주도주만
        ─────────────────────────────────────────────────────────────────
        오닐 원칙: "RS 80 미만 종목은 절대 Leader가 아니다."

        RS Rating 체계 (0~100):
          • 80~100: Leader   → 대형 보너스 + Leader 태그
          • 60~79:  Neutral  → 소폭 보너스
          • 40~59:  Laggard  → 감점 시작
          • 0~39:   Laggard  → Fail-Safe 트리거 + 강력 페널티

        3개월 수익률 기반 시장 대비 상대 강도 계산.
        rs_rating은 momentum() 에서 계산된 값을 우선 사용하되,
        여기서도 독립적으로 계산하여 교차 검증.
        """
        result = {
            "rs":           0.0,
            "rank":         "NEUTRAL",
            "outperform":   False,
            "score":        0,
            # CAN SLIM L 전용
            "rs_rating":     50,
            "is_leader":     False,
            "fail_safe_rs":  False,   # RS < 40 → Ceiling 트리거
            "l_tag":         "NEUTRAL",
        }
        try:
            if len(hist) < 60:
                return result
            c = hist["Close"]
            n = len(c)

            # 1M·3M·6M·12M 수익률 가중 RS 계산 (스윙 호흡 — 단기 가속도 강조)
            r1  = (float(c.iloc[-1]) - float(c.iloc[-21])) / float(c.iloc[-21])  if n >= 21  else 0.0
            r3  = (float(c.iloc[-1]) - float(c.iloc[-63])) / float(c.iloc[-63])  if n >= 63  else r1
            r6  = (float(c.iloc[-1]) - float(c.iloc[-126])) / float(c.iloc[-126]) if n >= 126 else r3
            r12 = (float(c.iloc[-1]) - float(c.iloc[-252])) / float(c.iloc[-252]) if n >= 252 else r6 * 0.8

            # 가중 수익률 — 단기(1M·3M) 비중 ↑, 장기(12M)는 momentum 팩터가 담당하므로 축소
            weighted_ret = r1 * 0.25 + r3 * 0.40 + r6 * 0.20 + r12 * 0.15
            result["rs"] = weighted_ret

            # RS Rating 계산 (시장 기준 SPY 연 10% 가정)
            mkt_3m = 0.025
            rs_excess = r3 - mkt_3m
            result["outperform"] = rs_excess > 0

            # RS Rating → 0~100 (스윙 호흡 가중 수익률에 맞춰 임계값 하향 보정)
            if weighted_ret > 0.90:   result["rs_rating"] = 99
            elif weighted_ret > 0.60: result["rs_rating"] = 97
            elif weighted_ret > 0.38: result["rs_rating"] = 93
            elif weighted_ret > 0.25: result["rs_rating"] = 88
            elif weighted_ret > 0.15: result["rs_rating"] = 82
            elif weighted_ret > 0.09: result["rs_rating"] = 74
            elif weighted_ret > 0.03: result["rs_rating"] = 62
            elif weighted_ret > 0.00: result["rs_rating"] = 52
            elif weighted_ret > -0.07: result["rs_rating"] = 38
            elif weighted_ret > -0.18: result["rs_rating"] = 25
            else:                      result["rs_rating"] = 12

            # ── [L] Leader / Laggard 판정 ─────────────────────────────
            rsr = result["rs_rating"]
            score = 0

            if rsr >= CANSLIM["RS_LEADER_MIN"]:      # 80+
                score += 20
                result["is_leader"]  = True
                result["rank"]       = "STRONG_LEADER"
                result["l_tag"]      = "⭐ LEADER (RS {})".format(rsr)
            elif rsr >= 70:
                score += 12
                result["rank"]       = "LEADER"
                result["l_tag"]      = "LEADER (RS {})".format(rsr)
            elif rsr >= 60:
                score += 6
                result["rank"]       = "MILD_OUTPERFORM"
                result["l_tag"]      = "WATCH (RS {})".format(rsr)
            elif rsr >= CANSLIM["RS_LAGGARD_MAX"]:   # 40~59
                score -= 5
                result["rank"]       = "UNDERPERFORM"
                result["l_tag"]      = "LAGGARD (RS {})".format(rsr)
            else:                                     # 0~39
                score -= 20
                result["fail_safe_rs"] = True
                result["rank"]         = "STRONG_LAGGARD"
                result["l_tag"]        = "📉 LAGGARD (RS {}) — AVOID".format(rsr)

            result["score"] = score

        except Exception as e:
            logging.error(f"[Strategy] relative_strength: {e}")
        return result

    # ── 13. Volatility-Adjusted Score (DE Shaw) ───────────────────────────
    def vol_adjusted(self, hist: pd.DataFrame, base_score: float) -> dict:
        """
        DE Shaw 스타일 수익/위험 비율 조정.
        연환산 변동성 대비 3개월 수익률로 멀티플라이어 결정.
        • 고변동+저수익 → ×0.6, 저변동+고수익 → ×1.2
        """
        result = {"volatility": 0.0, "rv_ratio": 0.0,
                  "adj_score": base_score, "efficiency": "NORMAL"}
        try:
            if len(hist) < 30:
                return result
            c = hist["Close"]
            returns = c.pct_change(fill_method=None).dropna()
            vol = float(returns.std() * np.sqrt(252))
            result["volatility"] = vol
            ret3m = (float(c.iloc[-1]) - float(c.iloc[-63])) / float(c.iloc[-63]) if len(c) >= 63 else 0.0
            result["rv_ratio"] = ret3m / vol if vol > 0 else 0.0
            mult = 1.0
            if vol > 0.6:
                mult = 0.6 if ret3m < 0.05 else 0.8
                result["efficiency"] = "VERY_INEFFICIENT" if ret3m < 0.05 else "INEFFICIENT"
            elif vol > 0.4:
                mult = 0.7 if ret3m < 0 else 0.9
                result["efficiency"] = "INEFFICIENT" if ret3m < 0 else "MODERATE"
            elif vol < 0.2:
                mult = 1.2 if ret3m > 0.10 else (1.1 if ret3m > 0.05 else 1.0)
                result["efficiency"] = "VERY_EFFICIENT" if ret3m > 0.10 else "EFFICIENT"
            result["adj_score"] = base_score * mult
        except Exception as e:
            logging.error(f"[Strategy] vol_adjusted: {e}")
        return result

    # ── 14. Earnings Momentum / CAN SLIM [C] Current + [A] Annual ────────
    def earnings_momentum(self, info: dict) -> dict:
        """
        [C] Current Quarterly Earnings (오닐 C 원칙) — EPS 가속도 집중
        [A] Annual Earnings Growth 결합
        ─────────────────────────────────────────────────────────────────
        오닐 핵심: "단순 성장이 아니라 가속도(Acceleration)에 집중하라"

        가속도 판단 (yfinance 분기 EPS 데이터 활용):
          • 분기 EPS 성장률 ≥ 25%        → 기본 보너스
          • 분기 EPS 성장률 ≥ 50%        → 폭발적 보너스 (지수적 상향)
          • 3분기 연속 성장률 가속화      → 가중치 2배 + 태그 'Earnings Acceleration'
          • EPS 성장 < 0                  → 강력 페널티 (Fail-Safe 트리거)

        반환값:
          eps_growth:       연간 EPS 성장률
          eps_acceleration: True/False (3분기 연속 가속)
          c_score:          C 원칙 원점수
          a_score_bonus:    A 원칙 추가 보너스
          fail_safe_eps:    True → 점수 천장 트리거
        """
        result = {
            "eps_growth":       0.0,
            "rev_growth":       0.0,
            "score":            0,
            "trend":            "NEUTRAL",
            # CAN SLIM 전용
            "c_score":          0,
            "eps_acceleration": False,
            "accel_quarters":   0,       # 연속 가속 분기 수
            "fail_safe_eps":    False,   # EPS < 0 → Ceiling 트리거
            "eps_src":          "",      # EPS 성장률 출처 (디버그/표시용)
            "data_missing":     False,   # True → 모든 소스 부재 (진짜 데이터 부족)
        }
        try:
            rg = safe_get(info.get("revenueGrowth"), 0.0)

            # ── [C] 분기 EPS 성장률 소스 폴백 체인 ──────────────────
            # yfinance info 의 earningsGrowth(연간)는 KR·ADR·소형주에서
            # 누락이 잦다. C 원칙은 본래 '분기 실적'이므로 아래 우선순위로
            # 실데이터를 끌어와 '데이터 부족' 오표기를 차단한다.
            #   1) earningsGrowth          (연간 EPS — 기존 동작 보존)
            #   2) earningsQuarterlyGrowth (분기 YoY 순이익 — C 원칙 정통)
            #   3) forwardEps vs trailingEps 파생 성장률
            #   4) revenueGrowth 보수적 프록시 (0.6× 할인)
            eg  = safe_get(info.get("earningsGrowth"), None)
            src = "annual_eps"
            if eg is None:
                eg = safe_get(info.get("earningsQuarterlyGrowth"), None)
                src = "quarterly_eps"
            if eg is None:
                fe = safe_get(info.get("forwardEps"),  None)
                te = safe_get(info.get("trailingEps"), None)
                if fe is not None and te is not None and abs(te) > 1e-9:
                    eg  = (fe - te) / abs(te)
                    src = "forward_vs_trailing_eps"
            if eg is None and rg not in (None, 0.0):
                # 매출 성장만 확보 — 순이익 레버리지 보수 추정(0.6×)
                eg  = rg * 0.6
                src = "revenue_proxy"

            result["rev_growth"] = rg

            # 모든 소스 부재 → 진짜 데이터 부족 (페널티 없음)
            if eg is None:
                result["data_missing"] = True
                return result

            result["eps_growth"] = eg
            result["eps_src"]    = src

            # Fail-Safe 트리거
            if eg < 0:
                result["fail_safe_eps"] = True

            # ── [C] 분기 EPS 가속도 — 지수적 점수 체계 ───────────────
            c_score = 0
            if eg >= 1.00:                        # 100%+ 폭발 성장
                c_score += 40
                result["trend"] = "EXPLOSIVE"
            elif eg >= 0.50:                      # 50%+
                c_score += 28
                result["trend"] = "EXPLOSIVE"
            elif eg >= 0.25:                      # 25%+ (오닐 최소 기준)
                c_score += 18
                result["trend"] = "STRONG"
            elif eg >= 0.15:
                c_score += 10
                result["trend"] = "GOOD"
            elif eg >= 0.05:
                c_score += 5
                result["trend"] = "MODERATE"
            elif eg < -0.30:
                c_score -= 25
                result["trend"] = "SHARPLY_DECLINING"
            elif eg < -0.15:
                c_score -= 18
                result["trend"] = "DECLINING"
            elif eg < 0:
                c_score -= 10
                result["trend"] = "SLIGHT_DECLINE"

            # ── 3분기 연속 가속 판단 (earnings quarterly data 활용) ───
            # yfinance earnings_history or quarterly_earnings 활용 시도
            # 데이터 미존재 시 연간 성장률로 대체 추정
            try:
                qe = info.get("earningsQuarterlyGrowth")
                # 분기별 EPS 직접 비교: earningsHistory 대체 추정
                # earningsQuarterlyGrowth가 있으면 사용, 없으면 연간으로 대체
                if qe is not None:
                    q_growth = safe_get(qe, 0.0)
                    # 단일 분기 데이터만 있을 때: 3분기 연속 판단 불가
                    # → 연간 성장률 + 분기 성장률 방향 일치하면 가속 추정
                    if q_growth > eg * 0.8 and eg > 0.25 and q_growth > 0.25:
                        result["eps_acceleration"] = True
                        result["accel_quarters"]   = 2   # 보수적 추정
                        c_score = int(c_score * 2.0)     # 가중치 2배
                        result["trend"] += " [Earnings Acceleration🔥]"
            except Exception:
                pass

            result["c_score"] = c_score

            # ── [A] 매출 성장 보조 점수 ───────────────────────────────
            a_bonus = 0
            if rg > 0.30:      a_bonus += 10
            elif rg > 0.20:    a_bonus += 8
            elif rg > 0.10:    a_bonus += 5
            elif rg < -0.10:   a_bonus -= 8
            elif rg < 0:       a_bonus -= 3

            result["score"] = c_score + a_bonus

        except Exception as e:
            logging.error(f"[Strategy] earnings_momentum: {e}")
        return result

    # ── 15. DCF Price Target ───────────────────────────────────────────────
    def price_target(self, info: dict, cur_price: float, sector: str = "") -> dict:
        """
        DCF 적정가 대비 괴리 분석 (3단계 DCF + 상대가치 가중평균).
        valuation_engine.run() 의 weighted_mid 를 목표가로 사용.
        sector 가 주어지면 노무라식 12개월 선행 목표가도 함께 계산해 nomura_* 필드로 반환.
        재무데이터 누락 시 점수 0 처리(분석 중단 없음).
        """
        result = {"target": 0.0, "distance": 0.0, "upside": 0.0,
                  "score": 0, "view": "NEUTRAL",
                  "dcf_value": 0.0, "dcf_low": 0.0, "dcf_high": 0.0,
                  "nomura_target": 0.0, "nomura_method": "", "nomura_upside": 0.0,
                  "nomura_bias": 1.0}
        try:
            # yfinance Ticker.info → valuation_engine financials 매핑
            financials = {
                "fcf":               safe_get(info.get("freeCashflow"),     0.0),
                "eps":               safe_get(info.get("trailingEps"),      0.0),
                "book_value":        safe_get(info.get("bookValue"),        0.0),
                "ebitda":            safe_get(info.get("ebitda"),           0.0),
                "shares_outstanding": safe_get(info.get("sharesOutstanding"), 0.0),
                # EV → Equity bridge: 표준 3-stage DCF 마지막 단계
                "cash":              safe_get(info.get("totalCash"),        0.0),
                "debt":              safe_get(info.get("totalDebt"),        0.0),
            }
            ticker = str(info.get("symbol") or info.get("ticker") or "")
            vr = valuation_engine.run(
                ticker=ticker,
                current_price=float(cur_price or 0.0),
                financials=financials,
            )
            # 사용자 공식 = 순수 3-stage DCF (EV→Equity bridge 포함).
            # 상대가치(PER/PBR/EV-EBITDA)는 보조 지표로만 노출, 메인 적정가는 DCF base.
            _lo, mid, _hi = vr.fair_value_range
            target = vr.dcf_value if vr.dcf_value and vr.dcf_value > 0 else mid
            result["dcf_value"]   = float(vr.dcf_value or 0.0)
            result["dcf_low"]     = float(vr.dcf_low or 0.0)
            result["dcf_high"]    = float(vr.dcf_high or 0.0)
            result["per_fair"]    = float(vr.per_fair or 0.0)
            result["pbr_fair"]    = float(vr.pbr_fair or 0.0)
            result["ev_ebitda"]   = float(vr.ev_ebitda_fair or 0.0)
            result["weighted_mid"] = float(mid or 0.0)

            # ── 투자지주사: NAV-할인율 (실적/노무라 경로 우회) ──
            _hnav_ps = float(info.get("_holdco_nav_ps") or 0.0)
            _is_holdco = _hnav_ps > 0.0
            if _is_holdco:
                try:
                    # 종목별 목표 할인율 오버라이드(있으면) → 엔진 kwargs
                    _hd = info.get("_holdco_discount") or {}
                    _disc_kw = {}
                    if isinstance(_hd, dict):
                        if _hd.get("base") is not None:
                            _disc_kw["base_discount"] = float(_hd["base"])
                        if _hd.get("min") is not None:
                            _disc_kw["min_discount"] = float(_hd["min"])
                    hr = valuation_engine.holdco_nav_target_price(
                        nav_ps=_hnav_ps,
                        current_price=cur_price,
                        shareholder_yield=float(info.get("_holdco_shyield") or 0.0),
                        **_disc_kw,
                    )
                    h_tp = float(hr.get("target_price") or 0.0)
                    result["nomura_target"] = h_tp
                    result["nomura_method"] = "NAV-할인율"
                    result["nomura_bias"]   = 1.0
                    result["holdco_components"] = hr.get("components", {})
                    if h_tp > 0 and cur_price > 0:
                        result["nomura_upside"] = (h_tp - cur_price) / cur_price
                except Exception as e:
                    logging.debug(f"[Holdco] nav target: {e}")

            # ── 노무라식 12개월 선행 목표가 (sector 지정 시, 지주사 제외) ──
            try:
                if sector and not _is_holdco:
                    bps_ps = financials["book_value"]  # per share
                    shares_n = financials["shares_outstanding"]
                    net_income = safe_get(info.get("netIncomeToCommon"), 0.0)
                    roe = (net_income / (bps_ps * shares_n)) if (bps_ps > 0 and shares_n > 0) else 0.0
                    nom_fin = {
                        "bps": bps_ps,
                        "eps": financials["eps"],
                        "roe": roe,
                        "shares_outstanding": shares_n,
                        "fcf": financials["fcf"],
                        "cash": financials["cash"],
                        "debt": financials["debt"],
                    }
                    nr = valuation_engine.nomura_target_price(sector, nom_fin)
                    n_tp = float(nr.get("target_price") or 0.0)
                    result["nomura_target"] = n_tp
                    result["nomura_method"] = str(nr.get("method", ""))
                    result["nomura_bias"]   = float(nr.get("components", {}).get("nomura_bias", 1.0))
                    if n_tp > 0 and cur_price > 0:
                        result["nomura_upside"] = (n_tp - cur_price) / cur_price
            except Exception as e:
                logging.debug(f"[Strategy] nomura_target_price: {e}")

            # ── 노무라식 목표가가 있으면 메인 목표가로 우선 사용 ──
            n_tp_main = float(result.get("nomura_target") or 0.0)
            if n_tp_main > 0:
                target = n_tp_main
                result["target_method"] = result.get("nomura_method", "Nomura")
            else:
                result["target_method"] = "DCF"

            if target > 0 and cur_price > 0:
                result["target"] = float(target)
                d = (target - cur_price) / cur_price
                result["distance"] = d; result["upside"] = d
                score, view = valuation_engine.target_upside_score(d)
                result["score"] = round(score, 2)
                result["view"] = view
        except Exception as e:
            logging.error(f"[Strategy] price_target (DCF): {e}")
        return result

    # ── 16. Short Interest Risk (Two Sigma) ───────────────────────────────
    def short_interest(self, info: dict) -> dict:
        """
        공매도 비율 분석 (Two Sigma 스타일).
        공매도 > 20% → 심각한 리스크 페널티 (-15점).
        """
        result = {"pct": 0.0, "ratio": 0.0, "score": 0, "risk": "NORMAL"}
        try:
            sp = safe_get(info.get("shortPercentOfFloat"), 0.0)
            sr = safe_get(info.get("shortRatio"), 0.0)
            result["pct"] = sp; result["ratio"] = sr
            score = 0
            if sp > 0.25:   score -= 18; result["risk"] = "EXTREME"
            elif sp > 0.20: score -= 15; result["risk"] = "VERY_HIGH"
            elif sp > 0.15: score -= 10; result["risk"] = "HIGH"
            elif sp > 0.10: score -= 8;  result["risk"] = "ELEVATED"
            elif sp > 0.05: score -= 3;  result["risk"] = "MODERATE"
            elif 0 < sp < 0.03: score += 5; result["risk"] = "LOW"
            if sr > 10:     score -= 5
            elif sr > 7:    score -= 3
            result["score"] = score
        except Exception as e:
            logging.error(f"[Strategy] short_interest: {e}")
        return result

    # ── 17. Hurst Exponent (Fractal Math) ─────────────────────────────────
    def hurst_exponent(self, hist: pd.DataFrame) -> dict:
        """
        Hurst Exponent R/S 분석 (만델브로 프랙탈 이론 적용).
        ─────────────────────────────────────────────
        H < 0.5 : Mean Reverting  → 박스권 매매 유리
        H ≈ 0.5 : Random Walk     → 예측 불가
        H > 0.5 : Trending        → 추세 추종 유리

        최적화:
          - NumPy 벡터 연산으로 내부 루프 제거
          - lag 범위를 (2..20) 으로 고정해 연산량 O(N×lag) → O(lag²) 수준 유지
        """
        result = {"h": 0.5, "nature": "RANDOM", "score": 0}
        try:
            if len(hist) < 100:
                return result
            closes = hist["Close"].values.astype(float)
            log_ret = np.log(closes[1:] / (closes[:-1] + 1e-12))
            lags = range(2, 20)
            tau_vals, rs_vals = [], []
            for lag in lags:
                n_chunks = len(log_ret) // lag
                if n_chunks < 2:
                    continue
                trimmed = log_ret[:n_chunks * lag].reshape(n_chunks, lag)
                means   = trimmed.mean(axis=1, keepdims=True)
                devs    = np.cumsum(trimmed - means, axis=1)
                ranges  = devs.max(axis=1) - devs.min(axis=1)
                stds    = trimmed.std(axis=1, ddof=1)
                valid   = stds > 0
                if valid.sum() == 0:
                    continue
                rs_mean = (ranges[valid] / stds[valid]).mean()
                tau_vals.append(np.log(lag))
                rs_vals.append(np.log(rs_mean + 1e-12))

            if len(tau_vals) > 2:
                h_val = float(np.polyfit(tau_vals, rs_vals, 1)[0])
                result["h"] = h_val
                score = 0
                if h_val > 0.65:    result["nature"] = "STRONG_TREND";    score = 15
                elif h_val > 0.55:  result["nature"] = "TRENDING";        score = 8
                elif h_val < 0.40:  result["nature"] = "MEAN_REVERTING";  score = 5
                else:               result["nature"] = "RANDOM_WALK";     score = -5
                result["score"] = score
        except Exception as e:
            logging.error(f"[Strategy] hurst_exponent: {e}")
        return result

    # ── 18. Kalman Filter (수치 안정성 강화) ─────────────────────────────
    def kalman_filter(self, hist: pd.DataFrame) -> dict:
        """
        칼만 필터로 주가 노이즈 제거 후 진짜 추세 파악.
        ─────────────────────────────────────────────
        수치 안정성 개선:
          - Q / R 비율을 동적으로 조정해 필터가 극단값으로 발산 방지
          - P 공분산이 0 이하로 내려가지 않도록 clipping 적용
        """
        result = {"kf_price": 0.0, "signal": "NEUTRAL", "score": 0}
        try:
            if len(hist) < 50:
                return result
            closes = hist["Close"].values.astype(float)
            n = len(closes)
            # 측정 노이즈 R: 20일 표준편차 기반 동적 추정
            R = max(float(np.std(closes[-20:])) ** 2 * 0.001, 1e-6)
            Q = R * 1e-3          # 프로세스 노이즈 (R의 0.1%)

            xhat = closes[0]      # 사후 추정값
            P    = 1.0            # 사후 오차 공분산
            kf_prices = np.empty(n, dtype=float)

            for k in range(n):
                # 예측 단계
                xhat_m = xhat
                P_m    = max(P + Q, 1e-10)   # 음수 방지 클리핑
                # 갱신 단계
                K      = P_m / (P_m + R)
                xhat   = xhat_m + K * (closes[k] - xhat_m)
                P      = max((1 - K) * P_m, 1e-10)
                kf_prices[k] = xhat

            kf_price = float(kf_prices[-1])
            result["kf_price"] = kf_price
            cur   = float(closes[-1])
            slope = float(kf_prices[-1] - kf_prices[-5]) if n >= 5 else 0.0
            dev   = (cur - kf_price) / (kf_price + 1e-9)

            score = 0
            if slope > 0:
                if 0 <= dev < 0.05:   score += 15; result["signal"] = "BUY_TREND"
                elif -0.03 < dev < 0: score += 10; result["signal"] = "POSSIBLE_REVERSAL"
                elif dev > 0.05:      score -= 5;  result["signal"] = "OVERHEATED"
            else:
                if dev < 0:           score -= 10; result["signal"] = "SELL_TREND"
            result["score"] = score
        except Exception as e:
            logging.error(f"[Strategy] kalman_filter: {e}")
        return result

    # ── 19. Statistical Arbitrage Z-Score ─────────────────────────────────
    def stat_arb_zscore(self, hist: pd.DataFrame) -> dict:
        """
        통계적 차익거래 Z-Score (정규분포 이탈 판단).
        ─────────────────────────────────────────────
        Z < -2.0 → 95% 확률 반등 구간 (매수 신호 +15점)
        Z > +2.0 → 과매수 구간 (-10점)
        """
        result = {"z": 0.0, "probability": 0.0, "score": 0}
        try:
            if len(hist) < 30:
                return result
            c = hist["Close"]
            mean = c.rolling(20).mean().iloc[-1]
            std  = c.rolling(20).std().iloc[-1]
            if std > 0:
                z = float((c.iloc[-1] - mean) / std)
                result["z"] = z
                score = 0
                if z < -2.0:   score += 15; result["probability"] = 95.0
                elif z < -1.5: score += 8;  result["probability"] = 80.0
                elif z > 2.0:  score -= 10; result["probability"] = 5.0
                result["score"] = score
        except Exception as e:
            logging.error(f"[Strategy] stat_arb_zscore: {e}")
        return result


    # ── 20. Sentiment Proxy (가격-거래량 기반 심리 추정) ─────────────
    def sentiment_proxy(self, hist: pd.DataFrame) -> dict:
        """
        뉴스 센티먼트 프록시 — 외부 API 없이 가격·거래량 패턴으로 시장 심리 추정.
        ─────────────────────────────────────────────────────────────────
        구성 요소:
          1. 가격-거래량 합치도 (상승일 거래량 vs 하락일 거래량)
          2. 갭 방향 편향 (최근 갭업/갭다운 비율)
          3. 종가 위치 (캔들 내 종가 위치 — 매수/매도 압력)

        향후 FinBERT API, 뉴스 크롤링 등 외부 소스 통합 시
        이 메서드를 확장하면 됨.
        """
        result = {"sentiment_score": 0, "signal": "NEUTRAL",
                  "up_vol_ratio": 0.5, "gap_bias": 0.0, "close_strength": 0.5}
        try:
            if len(hist) < 20:
                return result
            c = hist["Close"].iloc[-20:]
            o = hist["Open"].iloc[-20:]
            v = hist["Volume"].iloc[-20:]
            h = hist["High"].iloc[-20:]
            l = hist["Low"].iloc[-20:]

            # 1) 상승일 vs 하락일 거래량 비율
            chg = c.pct_change(fill_method=None)
            up_vol = float(v[chg > 0].sum())
            dn_vol = float(v[chg <= 0].sum())
            total_vol = up_vol + dn_vol
            if total_vol < 1:  # 거래량 0 → 판단 불가, 중립 반환
                return result
            up_ratio = up_vol / total_vol
            result["up_vol_ratio"] = up_ratio

            score = 0
            if up_ratio > 0.65:     score += 10
            elif up_ratio > 0.55:   score += 4
            elif up_ratio < 0.35:   score -= 10
            elif up_ratio < 0.45:   score -= 4

            # 2) 갭 방향 편향 (최근 10일)
            gaps = (o.iloc[1:].values - c.iloc[:-1].values) / (c.iloc[:-1].values + 1e-9)
            gap_up = sum(1 for g in gaps if g > 0.002)
            gap_dn = sum(1 for g in gaps if g < -0.002)
            gap_bias = (gap_up - gap_dn) / max(len(gaps), 1)
            result["gap_bias"] = gap_bias

            if gap_bias > 0.3:      score += 6
            elif gap_bias < -0.3:   score -= 6

            # 3) 종가 위치 (High-Low 범위 내 종가 — 매수압력 지표)
            hl_range = h - l + 1e-9
            close_pos = ((c - l) / hl_range).mean()
            result["close_strength"] = float(close_pos)

            if close_pos > 0.7:     score += 5
            elif close_pos < 0.3:   score -= 5

            result["sentiment_score"] = score
            if score >= 12:   result["signal"] = "BULLISH"
            elif score >= 6:  result["signal"] = "MILD_BULLISH"
            elif score <= -12: result["signal"] = "BEARISH"
            elif score <= -6: result["signal"] = "MILD_BEARISH"
        except Exception as e:
            logging.error(f"[Strategy] sentiment_proxy: {e}")
        return result

    # ── 20. ORB 돌파 (일봉 근사) ─────────────────────────────────────────
    def orb_breakout(self, hist: pd.DataFrame) -> dict:
        """전일 고가 돌파 + 거래량 급증 → 장중 스윙 후보 스크리닝."""
        result = {"score": 0, "signal": "NONE", "breakout_pct": 0.0,
                  "vol_ratio": 0.0}
        try:
            if len(hist) < 5:
                return result
            close = hist["Close"].iloc[-1]
            prev_high = hist["High"].iloc[-2]
            avg_vol = float(hist["Volume"].tail(20).mean()) if len(hist) >= 20 else float(hist["Volume"].mean())
            cur_vol = float(hist["Volume"].iloc[-1])

            if avg_vol < 1:
                return result
            vol_ratio = cur_vol / avg_vol
            result["vol_ratio"] = vol_ratio

            intraday_high = float(hist["High"].iloc[-1])
            high_break_pct = (intraday_high - prev_high) / prev_high if prev_high > 0 else 0.0
            close_break_pct = (close - prev_high) / prev_high if prev_high > 0 else 0.0
            breakout_pct = max(close_break_pct, high_break_pct)
            result["breakout_pct"] = breakout_pct

            # 데이터 오류(분할 미반영·이상치)만 배제. KR 일일 상한 ±30%이므로
            # 10% 돌파는 정상이며, 강한 돌파를 NONE으로 버리지 않는다.
            if breakout_pct > 0.35:
                return result
            # 점수 가중치만 +10%로 캡 → 이상치 1개가 점수를 독식하지 못하게.
            score_pct = min(breakout_pct, 0.10)

            score = 0
            if close > prev_high and vol_ratio >= 2.0 and breakout_pct >= 0.003:
                score = 15
                result["signal"] = "ORB_BREAKOUT"
                score += min(int(score_pct * 200), 10)
                score += min(int((vol_ratio - 2.0) * 3), 5)
            elif vol_ratio >= 1.5 and close_break_pct >= 0.001:
                score = 8
                result["signal"] = "ORB_WEAK"
            elif high_break_pct >= 0.001 and vol_ratio >= 1.2:
                score = 6
                result["signal"] = "ORB_READY"
            elif close >= prev_high * 0.98 and close >= float(hist["Open"].iloc[-1]) and vol_ratio >= 0.95:
                score = 3
                result["signal"] = "ORB_WATCH"
            result["score"] = score
        except Exception as e:
            logging.error(f"[Strategy] orb_breakout: {e}")
        return result

    # ── 21. NR7 변동폭 압축 ──────────────────────────────────────────────
    def nr7_compression(self, hist: pd.DataFrame) -> dict:
        """최근 7일 중 변동폭 최소 + 전일 고가 돌파 → 에너지 방출 시그널."""
        result = {"score": 0, "signal": "NONE", "is_nr7": False,
                  "compression_ratio": 0.0, "vol_ratio": 0.0}
        try:
            if len(hist) < 10:
                return result
            ranges = (hist["High"] - hist["Low"]).tail(7)
            if ranges.iloc[-1] <= 0:
                return result
            avg_range = float(ranges.iloc[:-1].mean())
            if avg_range <= 0:
                return result
            today_range = float(ranges.iloc[-1])
            compression = today_range / avg_range
            result["compression_ratio"] = compression

            is_nr7 = today_range <= float(ranges.min()) * 1.01
            result["is_nr7"] = is_nr7
            if not is_nr7 or compression > 0.6:
                return result

            close = hist["Close"].iloc[-1]
            prev_high = hist["High"].iloc[-2]
            avg_vol = float(hist["Volume"].tail(5).mean())
            cur_vol = float(hist["Volume"].iloc[-1])
            vol_ratio = cur_vol / max(avg_vol, 1)
            result["vol_ratio"] = vol_ratio

            if close > prev_high * 1.005 and vol_ratio >= 1.5:
                score = 15
                score += min(int((1.0 - compression) * 20), 12)
                score += min(int((vol_ratio - 1.5) * 3), 8)
                result["score"] = score
                result["signal"] = "NR7_BREAKOUT"
            elif is_nr7 and compression <= 0.5:
                result["score"] = 6
                result["signal"] = "NR7_READY"
        except Exception as e:
            logging.error(f"[Strategy] nr7_compression: {e}")
        return result

    # ── 22. 볼린저밴드 평균회귀 ──────────────────────────────────────────
    def bb_mean_reversion(self, hist: pd.DataFrame) -> dict:
        """BB 하단 이탈 + 5일 MA 이격 과매도 → 반등 후보 스크리닝."""
        result = {"score": 0, "signal": "NONE", "bb_position": 0.0,
                  "ma5_deviation": 0.0, "vol_ratio": 0.0}
        try:
            if len(hist) < 25:
                return result
            closes = hist["Close"].tail(20)
            sma20 = float(closes.mean())
            std20 = float(closes.std())
            if std20 < 1e-6 or sma20 < 1e-6:
                return result
            bb_lower = sma20 - 2.0 * std20
            bb_upper = sma20 + 2.0 * std20
            bw = bb_upper - bb_lower
            if bw / sma20 < 0.005:
                return result

            cur = float(hist["Close"].iloc[-1])
            bb_pos = (cur - bb_lower) / bw if bw > 0 else 0.5
            result["bb_position"] = bb_pos

            ma5 = float(hist["Close"].tail(5).mean())
            ma5_dev = (cur - ma5) / ma5 if ma5 > 0 else 0
            result["ma5_deviation"] = ma5_dev

            avg_vol = float(hist["Volume"].tail(20).mean())
            cur_vol = float(hist["Volume"].iloc[-1])
            vol_ratio = cur_vol / max(avg_vol, 1)
            result["vol_ratio"] = vol_ratio

            day_chg = (cur - float(hist["Close"].iloc[-2])) / float(hist["Close"].iloc[-2])
            if day_chg < -0.08:
                return result

            if cur <= bb_lower and ma5_dev <= -0.03 and vol_ratio >= 1.5:
                score = 18
                score += min(int(abs(ma5_dev) * 100), 10)
                score += min(int((vol_ratio - 1.5) * 3), 8)
                result["score"] = score
                result["signal"] = "BB_REVERT"
            elif bb_pos < 0.15 and ma5_dev <= -0.02:
                result["score"] = 6
                result["signal"] = "BB_NEAR_LOW"
        except Exception as e:
            logging.error(f"[Strategy] bb_mean_reversion: {e}")
        return result


# ============================================================
# 메인 애플리케이션 클래스
# ============================================================
class QuantNexusApp:
    """
    (.)(.)스캐너 메인 애플리케이션.

    스큐어모피즘 UI + 전략 패턴 아키텍처.
    v20: High DPI 지원 / Malgun Gothic 한글 폰트 / 섹터 대규모 확장.
    """

    def __init__(self, root: tk.Tk):
        logging.info("(.)(.)스캐너 시작")
        self.root = root
        self.root.title("(.)(.)스캐너")

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h   = min(int(sw * 0.88), 1650), min(int(sh * 0.90), 960)
        x, y   = (sw - w) // 2, (sh - h) // 2
        root.geometry(f"{w}x{h}+{x}+{y}")
        root.minsize(1200, 700)
        root.configure(bg=C["BG"])

        self.engine  = WallStreetQuantStrategies()
        self.cache   = DataCache()
        self.current_data: list[dict] = []
        self.selected_sector   = ""
        self.scan_all_mode     = False
        self._ticker_sector_map = {}
        self.vix_value         = 20.0

        self.market_mode   = tk.StringVar(value="US")
        self.strategy_mode = tk.StringVar(value="BALANCED")
        self.nh_filter_on  = tk.BooleanVar(value=False)
        self.notify_enabled = True   # US-002: 스캔 완료 토스트 토글
        self._watchlist_db = None    # US-003: lazy
        # US-006: ticker -> CommitteeResult (LRU 캡으로 무한 증가 방지)
        from collections import OrderedDict as _OD
        self._committee_cache: _OD = _OD()
        self._committee_cache_max = 1000

        self.stats = {k: 0 for k in
                      ["scanned", "strong_buy", "buy", "hold", "sell",
                       "cache_hits", "cache_misses"]}

        self._config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), ".quant_nexus_ui.json"
        )
        self._ui_config = self._load_ui_config()
        self._resize_after_id  = None
        self._fitted_widths    = {}
        self._scan_cancelled   = False
        self._stats_lock       = threading.Lock()
        self._slim_mode        = False  # 기본: 전체 컬럼 표시

        self._sidebar_default_width = 280
        self._sidebar_collapsed = False
        self._naver_target_cache: dict = {}
        self._naver_target_meta: dict = {}  # 증권사 목표가 메타데이터 (애널리스트 수, 고/저)
        self._naver_cache_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "naver_target_cache.pkl"
        )
        self._load_naver_cache()

        self._naver_fund_cache: dict = {}
        self._naver_fund_cache_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "naver_fund_cache.pkl"
        )
        self._load_naver_fund_cache()

        # 투자지주사 NAV 산출용 자회사 시총 캐시: code -> (mktcap_oku, ts)
        self._holdco_quote_cache: dict = {}

        self._init_sector_data()
        self._build_styles()
        self._build_ui()
        self._bind_shortcuts()
        self._restore_ui_state()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────────────────────────────
    # 네이버 증권 컨센서스 목표가 크롤링
    # ─────────────────────────────────────────────────────────────────────
    def _load_naver_cache(self):
        """(DEPRECATED: DCF로 대체됨) 네이버 컨센서스 목표가 캐시 로드."""
        if os.path.exists(self._naver_cache_path):
            try:
                with open(self._naver_cache_path, 'rb') as f:
                    data = pickle.load(f)
                ts = data.get('_ts')
                if ts and (datetime.now() - ts).total_seconds() < 43200:
                    self._naver_target_cache = data
                    self._naver_target_meta = data.get('_meta', {}) or {}
            except Exception:
                pass

    def _save_naver_cache(self):
        """(DEPRECATED: DCF로 대체됨) 네이버 컨센서스 목표가 캐시 저장."""
        self._naver_target_cache['_ts'] = datetime.now()
        self._naver_target_cache['_meta'] = self._naver_target_meta
        try:
            with open(self._naver_cache_path, 'wb') as f:
                pickle.dump(self._naver_target_cache, f)
        except Exception:
            pass

    def _fetch_naver_target(self, ticker: str) -> float | None:
        """(DEPRECATED: DCF로 대체됨) 네이버 모바일 API에서 컨센서스 목표가 조회. 캐시 12시간."""
        code = ticker.split('.')[0]
        cached = self._naver_target_cache.get(code)
        if cached is not None:
            return cached if cached > 0 else None
        _ua = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        def _int(v):
            try:
                return int(str(v).replace(',', '').strip())
            except Exception:
                return 0
        try:
            url = f"https://m.stock.naver.com/api/stock/{code}/integration"
            req = urllib.request.Request(url, headers=_ua)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            ci = data.get('consensusInfo', {})
            tp_str = ci.get('priceTargetMean', '')
            if tp_str:
                target = int(str(tp_str).replace(',', ''))
                if target > 0:
                    self._naver_target_cache[code] = target
                    self._naver_target_meta[code] = "네이버 증권 통합 평균"
                    self._save_naver_cache()
                    return target
        except Exception as e:
            logging.debug(f"Naver target fetch failed for {code}: {e}")

        # 2차 폴백: 증권사 리서치 목록에서 목표가를 평균화
        try:
            for ep in [
                f"https://m.stock.naver.com/api/stock/{code}/finance/research?pageSize=8",
                f"https://m.stock.naver.com/api/stock/{code}/research?pageSize=8",
            ]:
                req = urllib.request.Request(ep, headers=_ua)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                items = data if isinstance(data, list) else (data.get('list') or data.get('reports') or data.get('items') or [])
                tps = []
                for it in items[:8]:
                    tp = _int(it.get('priceTarget') or it.get('targetPrice') or it.get('target'))
                    if tp > 0:
                        tps.append(tp)
                if tps:
                    target = int(round(sum(tps) / len(tps)))
                    if target > 0:
                        self._naver_target_cache[code] = target
                        self._naver_target_meta[code] = "네이버 증권 리서치 평균"
                        self._save_naver_cache()
                        return target
        except Exception as e:
            logging.debug(f"Naver research target fetch failed for {code}: {e}")

        self._naver_target_cache[code] = 0
        self._naver_target_meta[code] = ""
        return None

    # ─────────────────────────────────────────────────────────────────────
    # 네이버 증권 재무 데이터 크롤링 (PER/PBR/ROE/영업이익률/부채비율)
    # ─────────────────────────────────────────────────────────────────────
    # 캐시 스키마 버전 — 필드 추가 시 bump 하여 옛 캐시 자동 무효화
    _NAVER_FUND_SCHEMA = 3  # v3: 네이버 실제 필드(영업이익/당기순이익/EPS/BPS) 기반 + shares 역산

    def _load_naver_fund_cache(self):
        if os.path.exists(self._naver_fund_cache_path):
            try:
                with open(self._naver_fund_cache_path, 'rb') as f:
                    data = pickle.load(f)
                ts = data.get('_ts')
                schema = data.get('_schema', 1)
                if (ts and (datetime.now() - ts).total_seconds() < 43200
                        and schema == self._NAVER_FUND_SCHEMA):
                    self._naver_fund_cache = data
            except Exception:
                pass

    def _save_naver_fund_cache(self):
        self._naver_fund_cache['_ts'] = datetime.now()
        self._naver_fund_cache['_schema'] = self._NAVER_FUND_SCHEMA
        try:
            with open(self._naver_fund_cache_path, 'wb') as f:
                pickle.dump(self._naver_fund_cache, f)
        except Exception:
            pass

    def _fetch_naver_fundamentals(self, ticker: str) -> dict:
        """네이버 finance/annual API에서 PER/PBR/ROE/영업이익률/부채비율 + DCF 입력값(EPS/BPS/영업CF/EBITDA/발행주식수) 조회."""
        code = ticker.split('.')[0]
        cached = self._naver_fund_cache.get(code)
        if cached is not None:
            return cached
        result = {}
        try:
            url = f"https://m.stock.naver.com/api/stock/{code}/finance/annual"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            fi = data.get('financeInfo', {})
            # 최신 실적 연도 키 (컨센서스 제외)
            latest_key = None
            for tr in reversed(fi.get('trTitleList', [])):
                if tr.get('isConsensus') == 'N':
                    latest_key = tr['key']
                    break
            if not latest_key:
                self._naver_fund_cache[code] = result
                return result

            # title → (key, unit_multiplier)
            # 영업CF/EBITDA 는 네이버 표기 단위가 억원 → 원으로 환산(×1e8)
            # 발행주식수는 표기 단위가 주(원)
            # EPS/BPS 는 원/주
            field_map = {
                '영업이익률':       ('operating_margin', 1.0),
                'ROE':              ('roe',              1.0),
                '부채비율':         ('debt_ratio',       1.0),
                'PER':              ('per',              1.0),
                'PBR':              ('pbr',              1.0),
                'EPS':              ('eps_naver',        1.0),
                'BPS':              ('bps_naver',        1.0),
                '영업활동현금흐름': ('opcf_naver',       1e8),
                'EBITDA':           ('ebitda_naver',     1e8),
                '발행주식수':       ('shares_naver',     1.0),
                '배당수익률':       ('div_yield_naver',  1.0),
            }
            for row in fi.get('rowList', []):
                title = row['title']
                if title in field_map:
                    key, mult = field_map[title]
                    cols = row.get('columns', {})
                    if latest_key in cols:
                        val_str = cols[latest_key].get('value', '')
                        if val_str and val_str != 'N/A':
                            try:
                                result[key] = float(val_str.replace(',', '')) * mult
                            except ValueError:
                                pass

            self._naver_fund_cache[code] = result
            if len(self._naver_fund_cache) % 10 == 0:
                self._save_naver_fund_cache()
        except Exception as e:
            logging.debug(f"Naver fund fetch failed for {code}: {e}")
            self._naver_fund_cache[code] = {}
        return result

    # ─────────────────────────────────────────────────────────────────────
    # ttk 스타일 (Toss 플랫 디자인)
    # ─────────────────────────────────────────────────────────────────────
    def _build_styles(self):
        st = ttk.Style()
        st.theme_use("clam")

        # Treeview — 화이트 배경 플랫 스타일
        st.configure("Treeview",
                      background=C["PANEL"],
                      foreground=C["TEXT_MAIN"],
                      fieldbackground=C["PANEL"],
                      font=F["TREE"],
                      rowheight=46,
                      borderwidth=0)
        st.configure("Treeview.Heading",
                      background=C["BG"],
                      foreground=C["TEXT_SUB"],
                      font=F["TREE_HEAD"],
                      relief="flat",
                      borderwidth=0)
        st.map("Treeview",
               background=[("selected", C["SELECT_BG"])],
               foreground=[("selected", C["SELECT_FG"])])

        # PanedWindow sash
        st.configure("TPanedwindow",
                      background=C["BG"])
        st.configure("Sash",
                      sashthickness=6,
                      gripcount=0)

        # Progressbar
        st.configure("TProgressbar",
                      troughcolor=C["SHADOW"],
                      background=C["ACCENT"],
                      thickness=4)

    # ─────────────────────────────────────────────────────────────────────
    # UI 빌드
    # ─────────────────────────────────────────────────────────────────────
    def _skeu_frame(self, parent, raised=True, **kw) -> tk.Frame:
        """플랫 카드 프레임 헬퍼."""
        return tk.Frame(parent, relief="flat", bd=0,
                        bg=kw.pop("bg", C["PANEL"]),
                        highlightbackground=C["SHADOW"],
                        highlightthickness=1, **kw)

    def _skeu_button(self, parent, text, command, active=False,
                     font_size=10, pady=8, **kw) -> tk.Button:
        """Toss 플랫 버튼 헬퍼."""
        bg = C["ACCENT"]    if active else C["BG"]
        fg = C["HIGHLIGHT"] if active else C["TEXT_MAIN"]
        _font = F["BTN"] if font_size >= 10 else F["BTN_SM"]
        btn = tk.Button(
            parent, text=text, command=command,
            font=_font,
            bg=bg, fg=fg,
            relief="flat", bd=0,
            activebackground=C["SELECT_BG"],
            activeforeground=C["ACCENT"],
            cursor="hand2", pady=pady,
            highlightbackground=C["SHADOW"],
            highlightthickness=0, **kw
        )
        return btn

    def _build_ui(self):
        wrap = tk.Frame(self.root, bg=C["BG"])
        wrap.pack(fill=tk.BOTH, expand=True)

        self._build_header(wrap)
        self._build_macro_banner(wrap)

        self.paned = ttk.PanedWindow(wrap, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True, padx=12, pady=(8, 10))

        self._build_sidebar(self.paned)
        self._build_main_panel(self.paned)

    def _build_macro_banner(self, parent):
        """v21: 거시 레짐 + 데이터 지연 배너 (헤더 아래)."""
        bar = tk.Frame(parent, bg="#F5F5F5", height=24,
                       highlightbackground=C.get("SHADOW", "#DDD"),
                       highlightthickness=1)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        self.lbl_macro = tk.Label(bar, text="⚪ 시장 레짐: 조회 중…",
                                  bg="#F5F5F5", fg="#191919",
                                  font=("Segoe UI", 9, "bold"),
                                  padx=10, pady=2)
        self.lbl_macro.pack(side=tk.LEFT)

        if _data_quality is not None:
            try:
                txt = _data_quality.build_delay_badge_text()
                style = _data_quality.build_delay_badge_style()
                self.lbl_delay = tk.Label(bar, text=txt,
                                          bg=style.get("bg", "#FFF3CD"),
                                          fg=style.get("fg", "#664D03"),
                                          font=("Segoe UI", 9, "bold"),
                                          padx=8, pady=2)
                self.lbl_delay.pack(side=tk.RIGHT, padx=8)
            except Exception:
                pass

        self._refresh_macro_banner()

    def _refresh_macro_banner(self):
        """5분마다 거시 레짐 갱신 — 실패해도 앱 진행."""
        if _macro_gate is None or not hasattr(self, "lbl_macro"):
            return
        try:
            state = _macro_gate.get_regime()
            text  = _macro_gate.build_banner_text(state)
            style = _macro_gate.build_banner_style(state)
            self.lbl_macro.configure(text=text,
                                     bg=style.get("bg", "#F5F5F5"),
                                     fg=style.get("fg", "#191919"))
            self.lbl_macro.master.configure(bg=style.get("bg", "#F5F5F5"))
        except Exception as e:
            logging.warning("macro banner refresh failed: %s", e)
        try:
            self.root.after(5 * 60 * 1000, self._refresh_macro_banner)
        except Exception:
            pass

    def _build_header(self, parent):
        """Toss 플랫 헤더 바."""
        hdr = tk.Frame(parent, bg=C["HEADER_BG"], height=76,
                       relief="flat", bd=0,
                       highlightbackground=C["SHADOW"],
                       highlightthickness=1)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)

        inner = tk.Frame(hdr, bg=C["HEADER_BG"])
        inner.pack(fill=tk.BOTH, expand=True, padx=24, pady=12)

        # ─ 왼쪽: 타이틀
        left = tk.Frame(inner, bg=C["HEADER_BG"])
        left.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(left, text="(.)(.)스캐너", font=F["TITLE"],
                 bg=C["HEADER_BG"], fg=C["ACCENT"]).pack(side=tk.LEFT)
        tk.Label(left, text="  주식 스캐너",
                 font=F["BODY"], bg=C["HEADER_BG"], fg=C["GOLD"]).pack(side=tk.LEFT)

        # ─ 오른쪽: 컨트롤
        right = tk.Frame(inner, bg=C["HEADER_BG"])
        right.pack(side=tk.RIGHT, fill=tk.Y)

        # 시장 토글
        mf = tk.Frame(right, bg=C["HEADER_BG"])
        mf.pack(side=tk.RIGHT, padx=(20, 0))
        self.btn_us = self._skeu_button(mf, "🇺🇸 US",  lambda: self._switch_market("US"),  active=True,  padx=14)
        self.btn_us.pack(side=tk.LEFT, padx=3)
        self.btn_kr = self._skeu_button(mf, "🇰🇷 KR",  lambda: self._switch_market("KR"),  active=False, padx=14)
        self.btn_kr.pack(side=tk.LEFT, padx=3)
        self.btn_eu = self._skeu_button(mf, "🇪🇺 EU",  lambda: self._switch_market("EU"),  active=False, padx=14)
        self.btn_eu.pack(side=tk.LEFT, padx=3)

        # 전략 선택
        sf = tk.Frame(right, bg=C["HEADER_BG"])
        sf.pack(side=tk.RIGHT, padx=(0, 20))
        self.btn_sidebar = self._skeu_button(
            sf, "SECTORS >", self._toggle_sidebar,
            active=False, padx=10, pady=5
        )
        self.btn_sidebar.pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(sf, text="Strategy:", font=F["SMALL"],
                 bg=C["HEADER_BG"], fg=C["TEXT_LABEL"]).pack(side=tk.LEFT)
        self._strat_btns = {}
        for mode in ["MOM", "BAL", "VAL", "CAN", "SCA"]:
            key = {"MOM": "MOMENTUM", "BAL": "BALANCED", "VAL": "VALUE", "CAN": "CAN_SLIM", "SCA": "SCALPING"}[mode]
            b = self._skeu_button(sf, mode, lambda k=key: self._set_strategy(k),
                                  active=(key == "BALANCED"), padx=10, pady=5)
            b.pack(side=tk.LEFT, padx=2)
            self._strat_btns[key] = b
        ToolTip(self._strat_btns["MOMENTUM"], "모멘텀 전략: 상승 추세에 가중치 30%")
        ToolTip(self._strat_btns["BALANCED"],  "균형 전략 (기본): 모든 팩터 균등 배분")
        ToolTip(self._strat_btns["VALUE"],      "가치 전략: 저평가 우량주에 가중치 55%")
        ToolTip(self._strat_btns["CAN_SLIM"],
                "⭐ CAN SLIM 모드 (윌리엄 오닐)\n"
                "C: EPS 가속도 집중\n"
                "A: ROE 17%+ 엄격 기준\n"
                "N: 52주 신고가 + 피벗\n"
                "S: 거래량 확인 돌파\n"
                "L: RS 80+ 주도주만\n"
                "M: Bear 시장 억제 필터")
        ToolTip(self._strat_btns["SCALPING"],
                "단타/스윙 스크리너\n"
                "ORB: 전일 고가 돌파 (14%)\n"
                "NR7: 변동폭 압축 돌파 (10%)\n"
                "BB: 볼린저 하단 반등 (8%)\n"
                "거래량·수급 16% 보조")

        # VIX 레이블
        self.lbl_vix = tk.Label(right, text="VIX: ──", font=F["SUBHEADER"],
                                bg=C["HEADER_BG"], fg=C["TEXT_LABEL"])
        self.lbl_vix.pack(side=tk.RIGHT, padx=(0, 20))
        ToolTip(self.lbl_vix, "VIX 공포지수\n• <15: 안정\n• 15~25: 보통\n• 25~30: 불안\n• 30+: 공포")

    def _build_sidebar(self, parent):
        """플랫 사이드바 (섹터 트리 + 버튼 + 상태)."""
        sb = tk.Frame(parent, bg=C["SIDEBAR"],
                      relief="flat", bd=0,
                      highlightbackground=C["SHADOW"],
                      highlightthickness=1,
                      width=self._sidebar_default_width)
        self.sidebar_frame = sb
        parent.add(sb, weight=0)

        tk.Label(sb, text="  📊  SECTORS", font=F["HEADER"],
                 bg=C["SIDEBAR"], fg=C["ACCENT"],
                 relief="flat", bd=0, pady=8).pack(fill=tk.X, padx=10, pady=(12, 5))

        # 트리 컨테이너 (플랫 카드)
        tc = tk.Frame(sb, bg=C["SHADOW"], relief="flat", bd=0)
        tc.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.sector_tree = ttk.Treeview(tc, show="tree", selectmode="browse")
        tree_sb = ttk.Scrollbar(tc, orient="vertical", command=self.sector_tree.yview)
        self.sector_tree.configure(yscrollcommand=tree_sb.set)
        self.sector_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.sector_tree.bind("<<TreeviewSelect>>", self._on_sector_select)
        self._load_sector_tree()

        # 버튼 영역
        bc = tk.Frame(sb, bg=C["SIDEBAR"])
        bc.pack(fill=tk.X, padx=10, pady=8)

        self.btn_scan = self._skeu_button(bc, "▶  SCAN  (F5)", self._start_scan,
                                          active=True, font_size=11, pady=14)
        self.btn_scan.pack(fill=tk.X, pady=(0, 3))

        self.btn_scan_all = self._skeu_button(bc, "🔍  SCAN ALL  (F6)", self._start_scan_all,
                                              active=True, font_size=10, pady=10)
        self.btn_scan_all.pack(fill=tk.X, pady=(0, 3))

        self.btn_stop = self._skeu_button(bc, "■  STOP", self._stop_scan,
                                          active=False, font_size=10, pady=8)
        self.btn_stop.pack(fill=tk.X, pady=(0, 5))
        self.btn_stop.config(state="disabled")

        row2 = tk.Frame(bc, bg=C["SIDEBAR"])
        row2.pack(fill=tk.X, pady=(0, 5))
        self.btn_stats = self._skeu_button(row2, "📊 STATS", self._show_stats, pady=8)
        self.btn_stats.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self.btn_stats.config(state="disabled")
        self.btn_export = self._skeu_button(row2, "⬇ EXCEL", self._export_excel, pady=8)
        self.btn_export.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))
        self.btn_export.config(state="disabled")

        row3 = tk.Frame(bc, bg=C["SIDEBAR"])
        row3.pack(fill=tk.X, pady=(0, 5))
        self.btn_nh = self._skeu_button(row3, "🏦 NH 필터 OFF", self._toggle_nh_filter, pady=8)
        self.btn_nh.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ToolTip(self.btn_nh,
                "NH투자증권 나무 HTS 조건검색식 필터\n"
                "ON: 현재 전략의 NH 조건 통과 종목만 표시\n"
                "OFF: 전체 종목 표시")

        self.btn_clear = self._skeu_button(bc, "🗑  CLEAR CACHE", self._clear_cache,
                                           font_size=9, pady=6)
        self.btn_clear.pack(fill=tk.X, pady=(0, 4))

        self.btn_guide = self._skeu_button(bc, "📘  STRATEGY GUIDE", self._show_guide,
                                           font_size=9, pady=6)
        self.btn_guide.pack(fill=tk.X)

        # 상태 / 진행바
        st_fr = tk.Frame(sb, bg=C["SIDEBAR"], relief="flat", bd=0)
        st_fr.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.lbl_status = tk.Label(st_fr, text="섹터를 선택하세요",
                                   font=F["SMALL"], bg=C["SIDEBAR"], fg=C["TEXT_LABEL"],
                                   anchor="w", pady=4, padx=4)
        self.lbl_status.pack(fill=tk.X)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(st_fr, variable=self.progress_var, maximum=100,
                                            style="TProgressbar")
        self.progress_bar.pack(fill=tk.X, padx=4, pady=(2, 2))
        self.lbl_progress = tk.Label(st_fr, text="", font=F["TINY"],
                                     bg=C["SIDEBAR"], fg=C["TEXT_LABEL"],
                                     anchor="w", padx=4, pady=2)
        self.lbl_progress.pack(fill=tk.X)

    def _set_sidebar_collapsed(self, collapsed: bool):
        self._sidebar_collapsed = bool(collapsed)
        sash_x = 0 if self._sidebar_collapsed else self._sidebar_default_width
        try:
            self.paned.sashpos(0, sash_x)
        except Exception:
            return
        if hasattr(self, "btn_sidebar"):
            self.btn_sidebar.config(
                text="SECTORS >" if self._sidebar_collapsed else "< SECTORS"
            )

    def _toggle_sidebar(self):
        self._set_sidebar_collapsed(not self._sidebar_collapsed)

    def _build_main_panel(self, parent):
        """메인 트리뷰 + 로그 창."""
        rp = tk.Frame(parent, bg=C["BG"])
        parent.add(rp, weight=3)

        # ── Top Pick 카드 띠 ─────────────────────────────────────────
        self._top_picks_bar = tk.Frame(rp, bg=C["SHADOW_DEEP"])
        self._top_picks_bar.pack(fill=tk.X, padx=0, pady=0)
        self._top_picks_bar.pack_forget()  # 스캔 전에는 숨김

        # Tree 컨테이너 (기본/단타 뷰 전환용)
        self.tree_container = tk.Frame(rp, bg=C["BG"])
        self.tree_container.pack(fill=tk.BOTH, expand=True)

        # ── 기본 분석 뷰 ──
        self.main_tree_frame = tk.Frame(self.tree_container, bg=C["SHADOW"], relief="flat", bd=0)
        self.main_tree_frame.pack(fill=tk.BOTH, expand=True)
        tf = self.main_tree_frame

        cols = ("Sector","Name","Desc","Price","Target","Score","Conv","SRank","Day%","Mom12M","MomScore",
                "Value","Quality","RSI","VWAP","ATR%","Regime","Cmte","Signal","Reason")
        self.tree = ttk.Treeview(tf, columns=cols, show="tree headings")

        # ── 컬럼 비율 정의 ──────────────────────────────────────────────
        # weight: 창 크기 변화 시 여분 공간을 배분받는 상대 비율
        # minwidth: 어떤 상황에서도 보장되는 최소 픽셀
        # anchor: 셀 정렬
        _COL_SPEC = {
            # col        weight  minwidth  anchor
            "#0":       (2,      90,       "w"),      # TICKER
            "Sector":   (3,      90,       "w"),      # 섹터명
            "Name":     (3,      100,      "w"),      # 종목명 (설명 분리)
            "Desc":     (4,      150,      "w"),      # 업종/사업 설명
            "Price":    (2,      70,       "e"),      # 우측 정렬 (숫자)
            "Target":   (3,      95,       "e"),      # 목표가 + 괴리율
            "Score":    (2,      58,       "center"),
            "Conv":     (1,      45,       "center"),  # 확신도
            "SRank":    (2,      55,       "center"),  # 섹터 내 순위
            "Day%":     (2,      58,       "center"),
            "Mom12M":   (2,      62,       "center"),
            "MomScore": (2,      62,       "center"),
            "Value":    (2,      55,       "center"),
            "Quality":  (2,      58,       "center"),
            "RSI":      (1,      48,       "center"),
            "VWAP":     (2,      55,       "center"),
            "ATR%":     (1,      52,       "center"),
            "Regime":   (2,      78,       "center"),
            "Cmte":     (2,      62,       "center"),  # US-006: 7-페르소나 위원회 (5/7 ✓)
            "Signal":   (4,      110,      "center"),  # 시그널 텍스트 — 넓게
            "Reason":   (4,      130,      "w"),       # 상위 이유 한줄 요약
        }

        # 초기 width는 minwidth 와 동일하게 시작; stretch=True 로 리사이즈 대응
        self.tree.column("#0",
                         width=_COL_SPEC["#0"][1],
                         minwidth=_COL_SPEC["#0"][1],
                         anchor=_COL_SPEC["#0"][2],
                         stretch=True)
        _COL_LABEL = {"Desc": "설명", "Name": "종목명", "Sector": "섹터"}
        self.tree.heading("#0", text="TICKER")
        for col in cols:
            w, mw, anc = _COL_SPEC[col]
            self.tree.column(col, width=mw, minwidth=mw, anchor=anc, stretch=True)
            self.tree.heading(col, text=_COL_LABEL.get(col, col.upper()),
                              command=lambda c=col: self._sort(c, False))

        # ── 창 크기 변경 시 비율대로 재분배 (디바운싱 적용) ──────────
        def _do_tree_resize(total_w):
            if total_w < 200:
                return
            # 피팅된 너비가 있으면 그것을, 없으면 minwidth를 하한선으로 사용
            fitted = getattr(self, "_fitted_widths", {})
            total_weight = sum(_COL_SPEC[c][0] for c in ("#0",) + cols)
            avail = max(total_w - 18, 200)
            new_w = {}
            for c in ("#0",) + cols:
                wt = _COL_SPEC[c][0]
                floor = max(fitted.get(c, 0), _COL_SPEC[c][1])
                new_w[c] = max(int(avail * wt / total_weight), floor)
            self.tree.column("#0", width=new_w["#0"])
            for col in cols:
                self.tree.column(col, width=new_w[col])

        def _on_tree_resize(event):
            if self._resize_after_id:
                self.root.after_cancel(self._resize_after_id)
            self._resize_after_id = self.root.after(
                50, lambda: _do_tree_resize(event.width)
            )

        tf.bind("<Configure>", _on_tree_resize)

        # ── 툴바 (컬럼 토글) ───────────────────────────────────────
        _SLIM_COLS = ("Sector","Name","Score","Signal","Day%","Mom12M","RSI","Regime")
        toolbar = tk.Frame(tf, bg=C["HEADER_BG"], height=24)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        toolbar.pack_propagate(False)

        def _toggle_slim():
            self._slim_mode = not self._slim_mode
            if self._slim_mode:
                self.tree.configure(displaycolumns=_SLIM_COLS)
                btn_slim.config(text="🔧 전체 컬럼 보기")
            else:
                self.tree.configure(displaycolumns=cols)
                btn_slim.config(text="📋 핵심만 보기")
            self._autofit_columns()

        btn_slim = tk.Button(
            toolbar, text="📋 핵심만 보기",
            command=_toggle_slim,
            font=F["TINY"], bg=C["HEADER_BG"], fg=C["TEXT_LABEL"],
            relief="flat", bd=0, padx=8, cursor="hand2",
            activebackground=C["ACCENT"], activeforeground=C["HIGHLIGHT"],
        )
        btn_slim.pack(side=tk.LEFT, padx=4)

        # ── 색상 범례 ──────────────────────────────────────────────
        legend_bar = tk.Frame(tf, bg=C["SHADOW_DEEP"], height=18)
        legend_bar.pack(side=tk.BOTTOM, fill=tk.X)
        legend_bar.pack_propagate(False)
        _LEGEND = [
            ("●", "#FFD700", "90+ BREAKOUT"),
            ("●", "#4ade80", "80+ LEADER"),
            ("●", "#3182F6", "70+ WATCH"),
            ("●", "#94a3b8", "50+ NEUTRAL"),
            ("●", "#F04452", "<50 WEAK"),
        ]
        for sym, col_hex, lbl in _LEGEND:
            tk.Label(legend_bar, text=f" {sym} {lbl}", font=F["TINY"],
                     bg=C["SHADOW_DEEP"], fg=col_hex).pack(side=tk.LEFT, padx=2)

        hsb = ttk.Scrollbar(tf, orient="horizontal", command=self.tree.xview)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set,
                            displaycolumns=cols)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click_main)
        TreeviewToolTip(self.tree, COLUMN_TOOLTIPS)

        # ── 단타 스크리너 뷰 (SCA 모드 전용) ──
        self.sca_frame = tk.Frame(self.tree_container, bg=C["SHADOW"], relief="flat", bd=0)
        # 초기엔 숨김 (pack 안 함)

        # SCA 헤더
        sca_hdr = tk.Frame(self.sca_frame, bg=C["HEADER_BG"], height=36)
        sca_hdr.pack(fill=tk.X)
        sca_hdr.pack_propagate(False)
        tk.Label(sca_hdr, text="🔫 단타 스크리너  —  섹터 선택 후 SCAN (F5)",
                 font=F["BODY"], bg=C["HEADER_BG"], fg=C["ACCENT"]).pack(side=tk.LEFT, padx=12)
        tk.Label(sca_hdr, text="ORB 돌파 │ NR7 압축 │ BB 반등",
                 font=F["SMALL"], bg=C["HEADER_BG"], fg=C["TEXT_SUB"]).pack(side=tk.RIGHT, padx=12)

        sca_cols = ("Name","Price","Day%","VolRatio","RSI","ATR%",
                    "ORB","NR7","BB","Score","Signal")
        self.sca_tree = ttk.Treeview(self.sca_frame, columns=sca_cols, show="tree headings")

        _SCA_COL = {
            "#0":       (2, 80, "w"),
            "Name":     (4, 110, "w"),
            "Price":    (2, 80, "e"),
            "Day%":     (2, 60, "center"),
            "VolRatio": (2, 65, "center"),
            "RSI":      (1, 48, "center"),
            "ATR%":     (1, 52, "center"),
            "ORB":      (3, 100, "center"),
            "NR7":      (3, 100, "center"),
            "BB":       (3, 100, "center"),
            "Score":    (2, 70, "center"),
            "Signal":   (4, 130, "center"),
        }
        self.sca_tree.column("#0", width=80, minwidth=80, anchor="w", stretch=True)
        self.sca_tree.heading("#0", text="TICKER")
        for col in sca_cols:
            w, mw, anc = _SCA_COL[col]
            self.sca_tree.column(col, width=mw, minwidth=mw, anchor=anc, stretch=True)
            self.sca_tree.heading(col, text=col,
                                  command=lambda c=col: self._sort_sca(c, False))

        sca_hsb = ttk.Scrollbar(self.sca_frame, orient="horizontal", command=self.sca_tree.xview)
        sca_hsb.pack(side=tk.BOTTOM, fill=tk.X)
        sca_vsb = ttk.Scrollbar(self.sca_frame, orient="vertical", command=self.sca_tree.yview)
        self.sca_tree.configure(yscrollcommand=sca_vsb.set, xscrollcommand=sca_hsb.set)
        self.sca_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sca_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.sca_tree.bind("<Double-Button-1>", self._on_double_click_sca)
        self.sca_tree.bind("<Button-3>", self._on_right_click_sca)

        # 로그 창 (플랫)
        lc = tk.Frame(rp, bg=C["PANEL"], relief="flat", bd=0)
        lc.pack(fill=tk.X, pady=(8, 0))
        self.log_text = tk.Text(lc, height=5, bg=C["SIDEBAR"], fg=C["TEXT_SUB"],
                                font=F["SMALL"], relief="flat",
                                wrap=tk.NONE, padx=10, pady=5,
                                insertbackground=C["TEXT_MAIN"])
        log_vsb = ttk.Scrollbar(lc, orient="vertical", command=self.log_text.yview)
        log_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        log_hsb = ttk.Scrollbar(lc, orient="horizontal", command=self.log_text.xview)
        log_hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.log_text.configure(yscrollcommand=log_vsb.set, xscrollcommand=log_hsb.set)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    # ─────────────────────────────────────────────────────────────────────
    # UI 설정 저장 / 복원
    # ─────────────────────────────────────────────────────────────────────
    def _load_ui_config(self) -> dict:
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_ui_config(self):
        sash_x = 0
        if self.paned.panes():
            try:
                sash_x = self.paned.sashpos(0)
            except Exception:
                sash_x = self._sidebar_default_width
        cfg = {
            "geometry": self.root.geometry(),
            "sash_x": max(int(sash_x), 0),
        }
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
        except OSError:
            pass

    def _restore_ui_state(self):
        geo = self._ui_config.get("geometry")
        if geo:
            try:
                import re as _re
                m = _re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geo)
                if m:
                    gw, gh, gx, gy = int(m[1]), int(m[2]), int(m[3]), int(m[4])
                    sw = self.root.winfo_screenwidth()
                    sh = self.root.winfo_screenheight()
                    # 최소 크기 + 화면 안에 최소 100px 이상 보이는지 확인
                    # 다중 모니터 허용: 좌측 모니터 gx 음수도 통과
                    if (gw >= 1200 and gh >= 700
                            and gx < sw - 100 and gy < sh - 100):
                        self.root.geometry(geo)
            except Exception:
                pass
        else:
            # 처음 실행(저장된 창 위치 없음): 위젯 렌더링 후 최대화
            self.root.after(100, lambda: self.root.state("zoomed"))
        sash_x = self._ui_config.get("sash_x")
        if False and sash_x is not None:
            sash_x = max(sash_x, 200)  # 사이드바 최소 200px 보장
            self.root.after(100, lambda: self.paned.sashpos(0, sash_x))

        if sash_x is None:
            sash_x = 0
        try:
            sash_x = int(sash_x)
        except (TypeError, ValueError):
            sash_x = 0
        collapsed = sash_x <= 24
        if not collapsed:
            self._sidebar_default_width = max(sash_x, 200)
        self.root.after(100, lambda: self._set_sidebar_collapsed(collapsed))

    def _on_close(self):
        self._save_ui_config()
        self.root.destroy()

    # ─────────────────────────────────────────────────────────────────────
    # 키보드 단축키
    # ─────────────────────────────────────────────────────────────────────
    def _bind_shortcuts(self):
        self.root.bind("<F5>",          lambda _: self._start_scan())
        self.root.bind("<F6>",          lambda _: self._start_scan_all())
        self.root.bind("<Control-e>",   lambda _: self._export_excel())
        self.root.bind("<Escape>",      lambda _: self.root.quit())

    @staticmethod
    def _score_bg_tag(score) -> str:
        """TotalScore → 5단계 가독성 태그. None/오류 시 'sc_na' 반환."""
        try:
            s = float(score)
        except (TypeError, ValueError):
            return "sc_na"
        if s >= 80: return "sc_A"   # 강력매수권
        if s >= 60: return "sc_B"   # 매수관심
        if s >= 40: return "sc_na"  # 중립 (기본 색 유지)
        if s >= 20: return "sc_C"   # 주의
        return "sc_D"               # 위험

    @staticmethod
    def _configure_score_tags(tree) -> None:
        """5단계 점수 태그를 등록한다. 글자색만 변경해 가독성 확보."""
        F_BOLD = ("Segoe UI", 9, "bold")
        F_NORM = ("Segoe UI", 9)
        tree.tag_configure("sc_A", foreground="#00e676")   # 80+ 밝은 초록
        tree.tag_configure("sc_B", foreground="#69f0ae")   # 60-79 연초록
        tree.tag_configure("sc_na")                        # 40-59 기본색 유지
        tree.tag_configure("sc_C", foreground="#ffab40")   # 20-39 주황
        tree.tag_configure("sc_D", foreground="#ff5252")   # 0-19 밝은 빨강

    # ─────────────────────────────────────────────────────────────────────
    # 섹터 트리 관련
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def _sector_lookup_key(name: str) -> str:
        s = (name or "").strip()
        s = re.sub(r"^[^A-Za-z0-9]+", "", s)
        return s.strip()

    def _display_sector_category(self, name: str) -> str:
        if self.market_mode.get() == "US":
            key = self._sector_lookup_key(name)
            return self.us_sector_category_kr.get(key, name)
        return name

    def _display_sector_name(self, name: str) -> str:
        if self.market_mode.get() == "US":
            key = self._sector_lookup_key(name)
            return self.us_sector_labels_kr.get(key, name)
        return name

    def _resolve_display_name(self, ticker: str, current_name: str = "") -> str:
        ticker = str(ticker or "").strip()
        current_name = str(current_name or "").strip()
        is_kr_t = bool(ticker) and (ticker.endswith(".KS") or ticker.endswith(".KQ"))

        if is_kr_t:
            try:
                from swing_scan.config import stock_names as _sn
                code6 = ticker.split(".")[0].zfill(6)
                nm = _sn.get_name(code6)
                if nm and nm != code6:
                    return nm[:20]
            except Exception:
                pass
            nm = getattr(QuantNexusApp, "KR_NAMES", {}).get(ticker)
            if nm:
                return str(nm)[:20]
        else:
            nm = getattr(QuantNexusApp, "US_NAMES", {}).get(ticker)
            if nm:
                return str(nm)[:20]

        return current_name[:20] if current_name else ticker[:20]

    def _holdco_subsidiary_mktcap(self, code: str) -> float:
        """자회사 시가총액(억원). 6시간 캐시. 실패 시 0.0."""
        now = time.time()
        cached = self._holdco_quote_cache.get(code)
        if cached and (now - cached[1]) < 21600:
            return cached[0]
        mc = 0.0
        try:
            from naver_finance import get_quote
            q = get_quote(code) or {}
            mc = float(q.get("market_cap_oku") or 0.0)
        except Exception as e:
            logging.debug(f"[Holdco] subsidiary mktcap {code}: {e}")
        if mc > 0:
            self._holdco_quote_cache[code] = (mc, now)
        return mc

    def _compute_holdco_nav(self, ticker: str, info: dict) -> dict | None:
        """투자지주사면 보유 상장지분 시가 기반 주당 NAV·주주환원율 산출.

        Returns ``{"nav_ps", "shareholder_yield", "sub_count",
        "unlisted_oku", "discount"}`` 또는 None.

        상장 자회사 지분이 NAV의 골격이고, 거기에 종목별 선택 요소가 더해진다:
          • unlisted_oku: 비상장·간접지분 추정가치(억원). 미입력 시 0 → NAV는
            보수적(실제 NAV의 하한)으로 산출돼 저평가 신호가 둔감해진다.
          • discount: {"base":..,"min":..} 종목별 목표 할인율 오버라이드.
            지배구조 리스크가 큰 곳은 base를 크게, 적극 주주환원처는 작게.
        """
        code = str(ticker or "").split(".")[0]
        spec = HOLDCO_HOLDINGS.get(code)
        if not spec:
            return None

        # 상장 자회사 지분가치 합산 (억원 단위)
        nav_oku = 0.0
        n_ok = 0
        for sub_code, pct in spec.get("stakes", []):
            sub_mc = self._holdco_subsidiary_mktcap(sub_code)
            if sub_mc > 0:
                nav_oku += sub_mc * float(pct)
                n_ok += 1
        if nav_oku <= 0 or n_ok == 0:
            return None

        # 비상장·간접지분 추정가치(억원). 큐레이션된 종목만, 기본 0(보수적).
        try:
            unlisted_oku = float(spec.get("unlisted_oku", 0.0) or 0.0)
        except (TypeError, ValueError):
            unlisted_oku = 0.0
        if unlisted_oku > 0:
            nav_oku += unlisted_oku

        # 지주사 순현금(억원): info 의 totalCash − totalDebt. 누락 시 0.
        try:
            cash = float(safe_get(info.get("totalCash"), 0.0) or 0.0)
            debt = float(safe_get(info.get("totalDebt"), 0.0) or 0.0)
            net_cash_oku = (cash - debt) / 1e8  # 원 → 억원
        except Exception:
            net_cash_oku = 0.0
        nav_oku += net_cash_oku

        shares = float(safe_get(info.get("sharesOutstanding"), 0.0) or 0.0)
        if shares <= 0:
            return None
        nav_ps = (nav_oku * 1e8) / shares  # 억원 → 원, 주당

        # 주주환원율 = 배당수익률 + 큐레이션된 자사주 수익률
        div_y = 0.0
        try:
            dy = info.get("dividendYield")
            if dy is not None:
                dy = float(dy)
                div_y = dy / 100.0 if dy > 1.0 else dy  # % 또는 decimal 모두 수용
        except Exception:
            div_y = 0.0
        sh_yield = div_y + float(spec.get("buyback_yield", 0.0) or 0.0)

        # 종목별 목표 할인율 오버라이드(선택). 미입력 시 엔진 기본값 사용.
        disc = spec.get("discount") if isinstance(spec.get("discount"), dict) else None

        return {
            "nav_ps": nav_ps,
            "shareholder_yield": sh_yield,
            "sub_count": n_ok,
            "unlisted_oku": unlisted_oku,
            "discount": disc,
        }

    def _nomura_sector_hint(self, ticker: str, info: dict) -> str:
        """
        우선순위:
        1) 현재 스캔 맵에서 찾은 내부 섹터
        2) 현재 market sector/industry
        3) valuation_engine가 이해하는 canonical sector token
        """
        sector_name = ""
        try:
            if hasattr(self, "_ticker_sector_map"):
                sector_name = self._ticker_sector_map.get(ticker, "") or ""
        except Exception:
            sector_name = ""
        if not sector_name and hasattr(self, "sectors"):
            try:
                for subs in self.sectors.values():
                    for sec_name, sec_tickers in subs.items():
                        if ticker in sec_tickers:
                            sector_name = str(sec_name or "")
                            raise StopIteration
            except StopIteration:
                pass
            except Exception:
                pass

        raw = " ".join(
            str(x).strip() for x in (
                sector_name,
                info.get("sector"),
                info.get("industry"),
            ) if x
        ).strip()
        raw_u = raw.upper()

        if any(tok in raw_u for tok in ("AI GPU", "HBM", "SEMICON", "SEMICONDUCTOR", "MEMORY", "FABLESS")):
            if "장비" in raw_u or any(tok in raw_u for tok in ("EQUIPMENT", "TOOL", "TOOLS", "TEST", "INSPECTION", "ETCH", "DEPOSITION")):
                return "반도체 장비"
            return "반도체"
        if any(tok in raw_u for tok in ("PLATFORM", "CLOUD", "SAAS", "SOFTWARE")):
            return "플랫폼"
        if any(tok in raw_u for tok in ("BANK", "FINANCE", "FINTECH", "INSURANCE", "EXCHANGE", "DATA")):
            return "금융"
        if any(tok in raw_u for tok in ("BIOTECH", "HEALTHCARE", "PHARMA", "MEDICAL")):
            return "바이오"
        if any(tok in raw_u for tok in ("AUTO", "AUTOM", "CAR", "TRUCK", "TRANSPORT")):
            return "자동차"
        if any(tok in raw_u for tok in ("OIL", "GAS", "ENERGY", "NUCLEAR", "UTILITY", "POWER")):
            return "정유"

        # 최후 fallback: 큐레이션 섹터명이 있으면 그대로, 없으면
        # yfinance 영문 GICS 섹터를 한글로 정규화해 전달 (영문 노출 방지)
        if sector_name:
            return sector_name
        _GICS_KR = {
            "TECHNOLOGY": "기술",
            "COMMUNICATION SERVICES": "커뮤니케이션",
            "CONSUMER CYCLICAL": "경기소비재",
            "CONSUMER DISCRETIONARY": "경기소비재",
            "CONSUMER DEFENSIVE": "필수소비재",
            "CONSUMER STAPLES": "필수소비재",
            "CONSUMER GOODS": "필수소비재",
            "FINANCIAL SERVICES": "금융",
            "FINANCIAL": "금융",
            "FINANCIALS": "금융",
            "HEALTHCARE": "바이오",
            "HEALTH CARE": "바이오",
            "INDUSTRIALS": "산업재",
            "INDUSTRIAL GOODS": "산업재",
            "BASIC MATERIALS": "소재",
            "MATERIALS": "소재",
            "ENERGY": "에너지",
            "UTILITIES": "유틸리티",
            "REAL ESTATE": "부동산",
            "CONGLOMERATES": "지주·복합",
        }
        fb = str(info.get("sector") or info.get("industry") or "").strip()
        return _GICS_KR.get(fb.upper(), fb) if fb else ""

    def _load_sector_tree(self):
        for item in self.sector_tree.get_children():
            self.sector_tree.delete(item)
        # 카테고리는 딕셔너리 삽입 순서(논리 순서) 유지; 서브섹터만 가나다 정렬
        for cat, subs in self.sectors.items():
            cid = self.sector_tree.insert("", "end", text=self._display_sector_category(cat), open=False)
            sub_items = sorted(
                subs.items(),
                key=lambda item: self._display_sector_name(item[0])
            )
            for name, tickers in sub_items:
                label = self._display_sector_name(name)
                self.sector_tree.insert(cid, "end", text=f"{label} ({len(tickers)})",
                                        values=(name,))

    def _on_sector_select(self, _event):
        sel = self.sector_tree.selection()
        if not sel:
            return
        item = self.sector_tree.item(sel[0])
        if item["values"]:
            self.selected_sector = item["values"][0]
            self.lbl_status.config(text=f"선택: {self._display_sector_name(self.selected_sector)}")
            self.scan_all_mode = False
            self._start_scan()

    # ─────────────────────────────────────────────────────────────────────
    # 시장 / 전략 전환
    # ─────────────────────────────────────────────────────────────────────
    def _switch_market(self, market: str):
        if self.market_mode.get() == market:
            return
        self.market_mode.set(market)
        market_map = {
            "US": (self.btn_us,  self.us_sectors),
            "KR": (self.btn_kr,  self.kr_sectors),
            "EU": (self.btn_eu,  self.eu_sectors),
        }
        for key, (btn, _) in market_map.items():
            if key == market:
                btn.config(bg=C["ACCENT"], fg=C["HIGHLIGHT"], relief="flat")
            else:
                btn.config(bg=C["BG"], fg=C["TEXT_MAIN"], relief="flat")
        self.sectors = market_map[market][1]
        self._load_sector_tree()
        self.selected_sector = ""
        self.tree.delete(*self.tree.get_children())
        self.sca_tree.delete(*self.sca_tree.get_children())
        self.current_data = []
        self._committee_cache.clear()  # 캐시는 시장별로 재계산
        self._log(f"✅ 시장 전환 → {market}")

    def _set_strategy(self, strategy: str):
        self.strategy_mode.set(strategy)
        for key, btn in self._strat_btns.items():
            if key == strategy:
                btn.config(bg=C["ACCENT"], fg=C["HIGHLIGHT"], relief="flat")
            else:
                btn.config(bg=C["BG"], fg=C["TEXT_MAIN"], relief="flat")
        # 단타 뷰 전환
        if strategy == "SCALPING":
            self.main_tree_frame.pack_forget()
            self.sca_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self.sca_frame.pack_forget()
            self.main_tree_frame.pack(fill=tk.BOTH, expand=True)
        # 기존 데이터가 있으면 전환된 뷰에 즉시 렌더링
        if self.current_data:
            self.tree.delete(*self.tree.get_children())
            self.sca_tree.delete(*self.sca_tree.get_children())
            self._render_table()
        self._log(f"📊 전략 전환 → {strategy}")

    def _toggle_nh_filter(self):
        on = not self.nh_filter_on.get()
        self.nh_filter_on.set(on)
        if on:
            self.btn_nh.config(bg=C["ACCENT"], fg=C["HIGHLIGHT"], text="🏦 NH 필터 ON")
        else:
            self.btn_nh.config(bg=C["BG"], fg=C["TEXT_MAIN"], text="🏦 NH 필터 OFF")
        if self.current_data:
            self.tree.delete(*self.tree.get_children())
            self.sca_tree.delete(*self.sca_tree.get_children())
            self._render_table()
        self._log(f"🏦 NH 필터 {'ON' if on else 'OFF'}")

    # ─────────────────────────────────────────────────────────────────────
    # 분석 실행
    # ─────────────────────────────────────────────────────────────────────
    def _start_scan(self):
        if not self.selected_sector:
            messagebox.showwarning("섹터 미선택", "분석할 섹터를 선택해 주세요.")
            return
        self._scan_cancelled = False
        self.btn_scan.config(state="disabled", text="⏳ Scanning...")
        self.btn_stop.config(state="normal")
        self.progress_var.set(0)
        self.tree.delete(*self.tree.get_children())
        self.sca_tree.delete(*self.sca_tree.get_children())
        self.current_data = []
        self.stats = {k: 0 for k in self.stats}
        self._committee_cache.clear()  # 새 스캔마다 위원회 재평가
        threading.Thread(target=self._fetch_vix_then_run, daemon=True).start()

    def _start_scan_all(self):
        """전체 섹터 스캔 — 모든 섹터에서 살만한 종목을 한번에 분석."""
        self.scan_all_mode = True
        # 모든 섹터의 티커를 수집 (중복 제거, 첫 등장 섹터 기록)
        ticker_sector = {}
        for subs in self.sectors.values():
            for sec_name, sec_tickers in subs.items():
                for t in sec_tickers:
                    if t not in ticker_sector:
                        ticker_sector[t] = self._display_sector_name(sec_name)
        self._ticker_sector_map = ticker_sector

        self._scan_cancelled = False
        self.btn_scan.config(state="disabled")
        self.btn_scan_all.config(state="disabled", text="⏳ Scanning All...")
        self.btn_stop.config(state="normal")
        self.progress_var.set(0)
        self.tree.delete(*self.tree.get_children())
        self.sca_tree.delete(*self.sca_tree.get_children())
        self.current_data = []
        self.stats = {k: 0 for k in self.stats}
        self._committee_cache.clear()  # 전체 스캔마다 위원회 재평가
        self._log(f"🔍 전체 섹터 스캔 시작 — {len(ticker_sector)}개 고유 종목")
        threading.Thread(target=self._fetch_vix_then_run, daemon=True).start()

    def _stop_scan(self):
        self._scan_cancelled = True
        self.btn_stop.config(state="disabled")
        self._log("⏹ 스캔 중단 요청 — 현재 종목 완료 후 멈춥니다...")

    def _fetch_vix_then_run(self):
        if self.market_mode.get() == "US":
            try:
                self._log("📊 VIX 조회 중...")
                v = yf.Ticker("^VIX").history(period="1d")
                if not v.empty:
                    self.vix_value = float(v["Close"].iloc[-1])
                    col = C["RED"] if self.vix_value > 25 else C["ORANGE"] if self.vix_value > 20 else C["GREEN"]
                    self.root.after(0, lambda: self.lbl_vix.config(
                        text=f"VIX: {self.vix_value:.2f}", fg=col))
                    self._log(f"✅ VIX: {self.vix_value:.2f}")
            except Exception as e:
                logging.error(f"VIX 조회 실패: {e}")
                self.vix_value = 20.0
        self._run_scan()

    def _run_scan(self):
        if self.scan_all_mode:
            tickers = list(self._ticker_sector_map.keys())
        else:
            tickers = []
            for subs in self.sectors.values():
                if self.selected_sector in subs:
                    tickers = subs[self.selected_sector]
                    break
            sector_name = self._display_sector_name(self.selected_sector)
            self._ticker_sector_map = {t: sector_name for t in tickers}
        if not tickers:
            self._log("❌ 티커 목록 없음")
            self.root.after(0, self._finalize_ui)
            return

        total   = len(tickers)
        results = []
        failed  = []
        completed = 0
        # 스레드에서 Tkinter 변수 접근 방지 — 메인 스레드에서 미리 캡처
        self._scan_strategy = self.strategy_mode.get()
        self._scan_market   = self.market_mode.get()
        self._log(f"🔍 {total}개 종목 스캔 [{self._scan_strategy}]")

        # GUI 업데이트 배치: 5개마다 or 마지막
        UPDATE_BATCH = 5

        # 동적 워커 수: 종목 많으면 rate limit 보호를 위해 줄임
        n_workers = 2 if total > 80 else 3 if total > 40 else 4
        PROGRESSIVE_BATCH = 20  # 중간 결과 표시 단위
        PROGRESS_THROTTLE_SEC = 0.2  # after 콜백 폭주 방지: 200ms 간격
        last_progress_ts = [0.0]

        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as ex:
            fmap = {ex.submit(self._analyze_ticker, t): t for t in tickers}
            for fut in concurrent.futures.as_completed(fmap):
                if self._scan_cancelled:
                    for f in fmap:
                        f.cancel()
                    break
                ticker = fmap[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    logging.error(f"[Scan] {ticker} 예외: {e}")
                    res = None
                completed += 1
                pct = (completed / total) * 100

                # 진행률 업데이트 (throttle: 200ms마다 또는 마지막)
                _now = time.time()
                if (_now - last_progress_ts[0] >= PROGRESS_THROTTLE_SEC) or completed == total:
                    last_progress_ts[0] = _now
                    self.root.after(0, lambda p=pct, t=ticker, c=completed, tot=total:
                        (self.progress_var.set(p),
                         self.lbl_progress.config(text=f"{t}... ({c}/{tot})")))

                if res:
                    res["Sector"] = self._ticker_sector_map.get(ticker, "")
                    results.append(res)
                    # 로그는 배치로 (과도한 GUI 점유 방지)
                    if completed % UPDATE_BATCH == 0 or completed == total:
                        msg = f"✅ {completed}/{total} 완료"
                        self.root.after(0, lambda m=msg: self._log(m))
                    # Progressive rendering: PROGRESSIVE_BATCH마다 중간 결과 표시
                    if len(results) % PROGRESSIVE_BATCH == 0 and completed < total:
                        snap = list(results)
                        self.root.after(0, lambda s=snap: self._render_progressive(s))
                else:
                    failed.append(ticker)

        # ── 섹터 내 상대 순위 계산 ──────────────────────────────
        from collections import defaultdict
        _sec_groups = defaultdict(list)
        for r in results:
            sec = r.get("Sector", "")
            if sec:
                _sec_groups[sec].append(r)
        for sec, group in _sec_groups.items():
            group.sort(key=lambda x: x["TotalScore"], reverse=True)
            n = len(group)
            for i, r in enumerate(group):
                if n <= 1:
                    r["SectorRank"] = "-"
                else:
                    pct = (i / (n - 1)) * 100
                    if pct <= 10:   r["SectorRank"] = "Top 10%"
                    elif pct <= 25: r["SectorRank"] = "Top 25%"
                    elif pct <= 50: r["SectorRank"] = "Top 50%"
                    else:           r["SectorRank"] = "Bottom"

        # ── Cross-Sectional RS Rating (백분위 기반 재조정) ─────
        if len(results) >= 5:
            w = STRATEGY_WEIGHTS.get(self._scan_strategy,
                                     STRATEGY_WEIGHTS["BALANCED"])
            rs_total_w = w["rs"] + w["cs_l"]  # RS 관련 가중치 합
            sorted_rs = sorted(results,
                               key=lambda x: x.get("RS_WeightedRet", 0))
            n_rs = len(sorted_rs)
            for i, r in enumerate(sorted_rs):
                old_rs_rating = r["RSRating"]
                # 백분위: 1 ~ 99
                new_rs_rating = int((i / max(n_rs - 1, 1)) * 98) + 1
                r["RSRating"] = new_rs_rating
                r["IsLeader"] = new_rs_rating >= 80
                # 점수 델타 보정 (RS Rating 변화 → 최종 점수 반영)
                # delta_rs(0~98 범위) → [0,100] 스케일에서 가중치 비율만큼 반영
                delta_rs = new_rs_rating - old_rs_rating
                score_adj = (delta_rs / 98.0) * 100.0 * rs_total_w
                r["TotalScore"] = max(0.0, min(100.0,
                                               r["TotalScore"] + score_adj))
            # ── RS 백분위 후 Signal/FailSafe 재계산 ─────────────
            for r in results:
                new_rs = r["RSRating"]
                final  = r["TotalScore"]
                # FailSafe 재평가: RS < 40 이면 재트리거
                rs_fail = new_rs < CANSLIM["RS_LAGGARD_MAX"]
                eps_fail = r.get("FailSafe", False) and not rs_fail  # 기존 EPS 원인 보존
                # 원래 FailSafe가 EPS 원인이었으면 유지
                if r.get("_fail_eps", False):
                    eps_fail = True
                fail_safe = eps_fail or rs_fail
                # 극강 모멘텀 종목은 EPS FailSafe 완화
                _mom_ovr = r.get("_momentum_override", False)
                if fail_safe:
                    _ceil = CANSLIM["SCORE_CEIL_MOMENTUM_OVERRIDE"] if _mom_ovr else CANSLIM["SCORE_CEIL_LAGGARD"]
                    final = min(final, _ceil)
                    r["TotalScore"] = final
                r["FailSafe"] = fail_safe

                # Signal 재결정
                bear_cap = r.get("BearCap", False)
                is_leader = r["IsLeader"]
                s_confirmed = r.get("SConfirmed", False)
                near_high = r.get("NearHighPass", False)
                fulfilled = sum([
                    r.get("EPSAcceleration", False) or (r.get("MomentumScore", 0) > 30),
                    r.get("QualityScore", 0) > 50,
                    is_leader,
                ])

                if fail_safe:
                    if _mom_ovr:       sig = "⚡ MOMENTUM (Fail-Safe 완화)"
                    elif final >= 45:  sig = "⚠️ WATCH (Fail-Safe Active)"
                    else:              sig = "📉 LAGGARD (AVOID)"
                elif bear_cap:
                    sig = "🚫 BEAR MARKET — AVOID"
                elif final >= 90 and fulfilled == 3:
                    sig = "⭐⭐⭐⭐ CAN SLIM BREAKOUT"
                elif final >= 82:
                    if is_leader and s_confirmed:
                        sig = "🚀 HIGH MOMENTUM LEADER"
                    else:
                        sig = "⭐⭐⭐ STRONG LEADER"
                elif final >= 72:
                    sig = "⭐⭐ LEADER"
                elif final >= 60:
                    sig = "⭐ WATCH LIST — Accumulate"
                elif final >= 48:
                    sig = "⏸ NEUTRAL — Hold"
                elif final >= 35:
                    if rs_fail:
                        sig = "📉 LAGGARD (AVOID)"
                    else:
                        sig = "⚠️ CAUTION — Reduce"
                else:
                    sig = "📉 SELL / AVOID"

                # 추가 태그 복원
                if near_high and s_confirmed:
                    sig += " 🔔[BREAKOUT]"
                if r.get("LowLiquidity"):
                    sig += " [LOW LIQ]"
                r["Signal"] = sig

            # 재정렬 후 섹터 순위 갱신
            for sec, group in _sec_groups.items():
                group.sort(key=lambda x: x["TotalScore"], reverse=True)
                ng = len(group)
                for i, r in enumerate(group):
                    if ng <= 1:
                        r["SectorRank"] = "-"
                    else:
                        pct = (i / (ng - 1)) * 100
                        if pct <= 10:   r["SectorRank"] = "Top 10%"
                        elif pct <= 25: r["SectorRank"] = "Top 25%"
                        elif pct <= 50: r["SectorRank"] = "Top 50%"
                        else:           r["SectorRank"] = "Bottom"

        self.current_data = results
        self._save_naver_fund_cache()
        self.root.after(0, lambda: (self._render_table(), self._show_summary(failed)))

        # ── 스캔 완료 토스트 (US-002) ────────────────────────────
        if _notifier is not None and getattr(self, "notify_enabled", True):
            try:
                top = sorted(results, key=lambda x: x.get("TotalScore", 0), reverse=True)
                if top:
                    t1 = top[0]
                    msg = f"{len(results)}종목 / Top1: {t1.get('Ticker','-')} ({t1.get('TotalScore',0):.0f}점)"
                else:
                    msg = f"스캔 완료 — {len(results)}종목 (결과 없음)"
                _notifier.notify("📊 스캔 완료", msg, timeout=4)
            except Exception as _e:
                logging.warning("scan-done notify failed: %s", _e)

        # ── Telegram 자동 알림 비활성화 (swing_mom_scan_alert로 이관) ──

        # ── 사용자 알림 룰 자동 평가 (alert_rules) ─────────────────
        try:
            store = self._alert_rule_store()
            if store is not None and results:
                matches = store.evaluate_batch(results)
                if matches:
                    logging.info("alert-rules matched: %d", len(matches))
                    if _notifier is not None and getattr(self, "notify_enabled", True):
                        first_t, first_r = matches[0]
                        msg = (f"{first_t.get('Ticker','-')}: "
                               f"{first_r.field}{first_r.op}{first_r.threshold} "
                               f"외 {len(matches)-1}건"
                               if len(matches) > 1 else
                               f"{first_t.get('Ticker','-')}: "
                               f"{first_r.name}")
                        try:
                            _notifier.notify("🔔 알림 룰 매칭", msg, timeout=5)
                        except Exception:
                            pass
        except Exception as _e:
            logging.warning("alert-rules eval failed: %s", _e)

    @rate_limit(max_per_second=4)
    def _analyze_ticker(self, ticker: str) -> dict | None:
        """
        (.)(.)스캐너 단일 티커 분석 진입점 (v20.1)
        ─────────────────────────────────────────────────────────────────
        점수 산출 순서 (예산 분배 아키텍처):
          1. 19개 전략 원점수 계산
          2. 전체 팩터를 [0,100]으로 정규화 (6개 기본 퀀트 + 7개 보조 퀀트)
          3. CAN SLIM 7원칙 정규화 + 태그 생성 (C·A·N·S·L·I·M)
          4. 통합 가중 합산: base = Σ(정규화 점수 × w[]) — w[] 합 = 1.0
             ※ 추가 += 누적 방식 완전 폐기 → 인플레이션 원천 차단
          5. Hurst + Kalman 신뢰도 미세 조정 (±4%)
          6. Fail-Safe Ceiling (EPS<0 또는 RS<40 → 50점 상한)
          7. VIX 조정 (±4~20%)
          8. [M] Bear Cap (Bear 시장 → 50점 상한)
          9. 슈퍼 그로스 승수 (C+A+L 충족 시 ×1.07~1.18 — 엄격화)
         10. 변동성 조정 (DE Shaw vol_adjusted)
         11. 최종 클리핑 [0, 100]
         12. CAN SLIM 시그널 결정 + Breakdown 구성
        """
        try:
            # ── 전략 독립 캐시 키: "AAPL__BALANCED", "005930.KS__CAN_SLIM" 등
            # DataCache 내부를 수정하지 않고, 호출부에서 복합 키를 조합한다.
            # _path()의 replace 규칙("."->"_", "/"->"_")과 충돌하지 않도록
            # 구분자로 "__" (이중 언더스코어)를 사용한다.
            # 날짜 포함 캐시 키 — 날짜가 바뀌면 자동으로 새 스캔 (어제 DayChg 고착 방지)
            _today = datetime.now().strftime("%Y%m%d")
            strategy_key = f"{ticker}__{self._scan_strategy}__{_today}"
            # rate-limit 회피: 캐시 TTL 4시간 (KR 풀스캔 부하 경감)
            cached = self.cache.get(strategy_key, max_age_minutes=240)
            if cached:
                with self._stats_lock:
                    self.stats["cache_hits"] += 1
                fixed_name = self._resolve_display_name(ticker, cached.get("Name", ""))
                if fixed_name and cached.get("Name") != fixed_name:
                    cached["Name"] = fixed_name
                return cached
            with self._stats_lock:
                self.stats["cache_misses"] += 1

            stock = yf.Ticker(ticker)
            # period="2y"로 확장 — 252거래일 윈도우(12개월 수익률)를 안정적으로 확보
            # rate-limit (429) 대응: 지수 백오프 재시도, 최종 실패 시 1y 폴백 후 종목 스킵
            hist = None
            try:
                from yfinance.exceptions import YFRateLimitError as _YFRL
            except Exception:
                _YFRL = Exception
            is_us = self._scan_market == "US"
            for _attempt in range(1):
                try:
                    hist = stock.history(period="2y")
                    break
                except _YFRL:
                    logging.warning("[yf] rate-limited %s (US fast path)", ticker)
                    break
                except Exception as _e:
                    logging.warning("[yf] history failed %s: %s", ticker, _e)
                    break
            if hist is None or hist.empty or len(hist) < 30:
                stale = self.cache.get(strategy_key, max_age_minutes=60 * 24 * 30)
                if stale:
                    stale = dict(stale)
                    stale.setdefault("DataSource", "cache")
                    stale.setdefault("DataStatus", "STALE_CACHE")
                    return stale
                if is_us:
                    try:
                        hist = _fetch_stooq_history(ticker)
                    except Exception:
                        hist = None
                else:
                    try:
                        hist = stock.history(period="1y")
                    except Exception:
                        hist = None
                    if hist is None or hist.empty or len(hist) < 30:
                        try:
                            hist = stock.history(period="6mo")
                        except Exception:
                            hist = None
                if hist is None or hist.empty or len(hist) < 30:
                    return None

            try:
                info = stock.info or {}
            except Exception as _e:
                logging.warning(f"[yf.info] {ticker} 실패: {_e}")
                info = {}
            if not info:
                # info 빈 경우 즉시 fast_info fallback (KR 전용 retry+sleep 제거)
                try:
                    fi = stock.fast_info
                    info = {
                        "marketCap": getattr(fi, "market_cap", 0) or 0,
                        "currentPrice": getattr(fi, "last_price", 0) or 0,
                    }
                except Exception:
                    info = {}

            # 실시간 현재가 우선 (장중 등락 정확도): info > fast_info > hist 마지막 봉
            _rt_price = (safe_get(info.get("regularMarketPrice"))
                         or safe_get(info.get("currentPrice"))
                         or safe_get(info.get("ask")) or 0.0)
            _hist_last = safe_get(float(hist["Close"].iloc[-1]))
            cur  = _rt_price if _rt_price > 0 else _hist_last
            # prev: 전일 종가 — hist 마지막 봉(어제 종가) 또는 info.previousClose
            _prev_close = safe_get(info.get("previousClose") or info.get("regularMarketPreviousClose"))
            prev = (_prev_close if _prev_close and _prev_close > 0
                    else (_hist_last if _rt_price > 0 and _hist_last > 0
                          else (safe_get(float(hist["Close"].iloc[-2])) if len(hist) > 1 else cur)))
            # 한국 종목 종목명 조회 우선순위 (KRX 공식명 우선):
            #   1) swing_scan.config.stock_names (FDR/pykrx → KRX 공식 한글명, 권위 소스)
            #   2) KR_NAMES 하드코딩 사전 (KRX 미스 시 큐레이팅 폴백)
            #   3) yfinance longName / shortName (영문 폴백)
            #   4) 티커 코드 폴백
            # 주의: KR_NAMES 에는 재상장·코드재사용·사명변경으로 stale 된 항목이 91건 존재,
            # KRX 공식명을 항상 우선해 잘못된 한글명 매칭을 차단한다.
            _is_kr_t = bool(ticker) and (ticker.endswith(".KS") or ticker.endswith(".KQ"))
            _name = None
            if _is_kr_t:
                try:
                    from swing_scan.config import stock_names as _sn
                    code6 = ticker.split(".")[0].zfill(6)
                    nm2 = _sn.get_name(code6)
                    if nm2 and nm2 != code6:
                        _name = nm2
                except Exception:
                    pass
                if not _name:
                    _name = getattr(QuantNexusApp, "KR_NAMES", {}).get(ticker)
            if not _name and not _is_kr_t:
                _us_names = getattr(QuantNexusApp, "US_NAMES", {})
                _name = _us_names.get(ticker)
            if not _name:
                _name = info.get("longName") or info.get("shortName")
            name = (_name or ticker)[:20]
            # regularMarketChangePercent를 우선 사용 (yfinance 원자값 — 캐시 지연 없음)
            _rt_chg_pct = info.get("regularMarketChangePercent")
            if _rt_chg_pct is not None:
                try:
                    day_chg = float(_rt_chg_pct) / 100.0
                except (TypeError, ValueError):
                    day_chg = safe_div(cur - prev, prev)
            else:
                day_chg = safe_div(cur - prev, prev)

            # ── 유동성 체크 (Low Liquidity Gate) ───────────────────
            # 임계치 상향(2026-05): "관심 없는 종목" 제외 — US $1M→$20M, KR 5억→100억.
            # EXCLUDE 기준은 그 1/2 (US $10M, KR 50억) 이하 — 스캔에서 아예 제외.
            avg_vol_20 = float(hist["Volume"].tail(20).mean()) if len(hist) >= 20 else float(hist["Volume"].mean())
            avg_dollar_vol = avg_vol_20 * cur
            _is_kr = self._scan_market == "KR"
            _liq_cap_thr = 20_000_000_000 if _is_kr else 20_000_000
            _liq_exclude_thr = 10_000_000_000 if _is_kr else 10_000_000
            low_liquidity = avg_dollar_vol < _liq_cap_thr
            if avg_dollar_vol < _liq_exclude_thr:
                # 관심 없는 종목(거래대금 매우 낮음) — 스캔 결과에서 제외
                logging.info(
                    f"[LiqGate] {ticker} excluded: avg_dollar_vol={avg_dollar_vol:,.0f} "
                    f"< thr={_liq_exclude_thr:,.0f}"
                )
                return None

            # ── KR 밸류 팩터 보강 (단일 진실원천) ──────────────────────
            # yfinance 는 .KS/.KQ 의 priceToBook·trailingPE 를 거의 항상
            # None 으로 준다. 그 결과 fama_french 의 value_score 가 0 으로
            # 고정돼 '밸류 팩터'가 집계되지 않았다. 화면 표시 _PER/_PBR 과
            # 동일한 소스(_fetch_naver_fundamentals 네이버 연간 재무 API)를
            # fama_french 호출 '전에' info 에 주입 → 화면·점수 불일치 제거.
            # (_fetch_naver_fundamentals 는 캐시되므로 5277행 재호출은 무비용.)
            if _is_kr_t:
                try:
                    if not info.get("trailingPE") or not info.get("priceToBook"):
                        _nf = self._fetch_naver_fundamentals(ticker)
                        if _nf.get("per") and not info.get("trailingPE"):
                            info["trailingPE"] = float(_nf["per"])
                        if _nf.get("pbr") and not info.get("priceToBook"):
                            info["priceToBook"] = float(_nf["pbr"])
                except Exception as _e:
                    logging.debug(f"[KR value] {ticker} PER/PBR 보강 실패: {_e}")

            # ════════════════════════════════════════════════════════════
            # STEP 1 — 19개 전략 계산
            # ════════════════════════════════════════════════════════════
            ff     = self.engine.fama_french(hist, info)
            mom    = self.engine.momentum(hist)
            mr     = self.engine.mean_reversion(hist)
            atr    = self.engine.atr_risk(hist)
            vwap   = self.engine.vwap_analysis(hist)
            regime = self.engine.market_regime(hist)
            qual   = self.engine.quality_factor(info)
            flow   = self.engine.smart_money_flow(hist)
            mtf    = self.engine.mtf_confluence(hist)
            dd     = self.engine.drawdown_risk(hist)
            vol_a  = self.engine.volume_anomaly(hist)
            rs     = self.engine.relative_strength(hist)
            # earnings_momentum 은 KR 재무 보강(Naver) 이후 호출한다.
            # (raw yfinance info 는 KR earningsGrowth 누락이 잦아 '데이터 부족' 오표기)
            target_source = ""
            broker_target = 0.0  # 증권사 컨센서스 목표가 (KR: 네이버, US: yfinance)
            broker_target_source = ""  # 컨센서스 출처 라벨
            broker_target_count = 0    # 참여 애널리스트 수 (US만 노출)
            if _is_kr:
                # KR: yfinance info 는 freeCashflow/ebitda/bookValue 등이 누락/지연되는 경우가 많아
                # 2026 기준 데이터 우선순위:
                #   1) 네이버 분기 TTM (직전 분기 + 차기 분기 컨센서스 포함 → 2026 기준)
                #   2) 네이버 연간
                fin = None
                if _NAVERQ_OK and _naver_q is not None:
                    try:
                        nq = _naver_q.get_ttm_financials(ticker)
                        if nq.get("available"):
                            fin = nq
                    except Exception as _e:
                        logging.debug(f"[NaverQ] {ticker} TTM 조회 실패: {_e}")

                if fin:
                    if fin.get("eps"):
                        info["trailingEps"] = fin["eps"]
                    if fin.get("bps"):
                        info["bookValue"] = fin["bps"]
                    if fin.get("shares_outstanding"):
                        info["sharesOutstanding"] = fin["shares_outstanding"]
                    if fin.get("operating_income"):
                        # 보수적: 영업이익을 FCF 프록시로 사용
                        info["freeCashflow"] = fin["operating_income"]
                    if fin.get("ebitda"):
                        info["ebitda"] = fin["ebitda"]
                    # CAN SLIM [C] 용 EPS 성장률 — Naver 분기 실데이터 주입
                    if fin.get("eps_growth") is not None:
                        info["earningsGrowth"] = fin["eps_growth"]
                    if fin.get("eps_qoq_growth") is not None:
                        info["earningsQuarterlyGrowth"] = fin["eps_qoq_growth"]
                    src_tag = fin.get("source", "?")
                    target_source = f"DCF ({src_tag} {fin.get('fiscal_period','')})"
                else:
                    # 마지막 폴백: 네이버 연간
                    nf = self._fetch_naver_fundamentals(ticker) or {}
                    if nf.get('eps_naver'):
                        info["trailingEps"] = nf['eps_naver']
                    if nf.get('bps_naver'):
                        info["bookValue"] = nf['bps_naver']
                    target_source = "DCF (Naver 연간)"
                # KR 증권사 컨센서스 목표가 (DCF 와 별도 표시용)
                try:
                    bt = self._fetch_naver_target(ticker)
                    if bt and bt > 0:
                        broker_target = float(bt)
                        _code_for_meta = ticker.split(".")[0]
                        broker_target_source = self._naver_target_meta.get(_code_for_meta, "") or "네이버 증권 컨센서스 (국내 증권사 평균)"
                except Exception:
                    pass
            else:
                # US: 증권사 목표가 = 애널리스트 투자의견 변경(upgrades_downgrades)의
                #     최근 목표가 평균. US 인사이트의 'Analyst Recommendations'와
                #     동일 출처라 두 화면의 기준이 일치한다.
                try:
                    import analyst_consensus as _ac
                    _cons = _ac.summarize_upgrades_downgrades(
                        stock.upgrades_downgrades)
                    if _cons["mean_target"] > 0:
                        broker_target = _cons["mean_target"]
                        broker_target_count = _cons["target_count"]
                        broker_target_source = (
                            f"애널리스트 투자의견 평균 "
                            f"(증권사 {broker_target_count}곳, 최근 의견 기준)"
                        )
                except Exception as _e:
                    logging.debug(
                        f"[US broker_target] {ticker} upgrades_downgrades 실패: {_e}")
                # 폴백: 투자의견 목표가가 없으면 기존 yfinance 컨센서스 사용
                if not broker_target:
                    try:
                        tmp = info.get("targetMeanPrice")
                        if tmp and float(tmp) > 0:
                            broker_target = float(tmp)
                            cnt = info.get("numberOfAnalystOpinions") or 0
                            broker_target_count = int(cnt) if cnt else 0
                            if broker_target_count:
                                broker_target_source = f"Yahoo Finance Analyst Mean ({broker_target_count}명 평균)"
                            else:
                                broker_target_source = "Yahoo Finance Analyst Mean"
                    except Exception:
                        pass
            # KR 재무 보강(Naver)·US targetMeanPrice 반영이 끝난
            # info 로 실적 모멘텀 산출 → KR EPS 성장률 '데이터 부족' 해소
            earn   = self.engine.earnings_momentum(info)
            _sector_for_nomura = self._nomura_sector_hint(ticker, info)
            # 투자지주사면 NAV-할인율 경로로 라우팅 (실적/DCF 무시)
            try:
                _hnav = self._compute_holdco_nav(ticker, info)
                if _hnav:
                    info["_holdco_nav_ps"] = _hnav["nav_ps"]
                    info["_holdco_shyield"] = _hnav["shareholder_yield"]
                    info["_holdco_subs"] = _hnav["sub_count"]
                    info["_holdco_unlisted"] = _hnav.get("unlisted_oku") or 0.0
                    if _hnav.get("discount"):
                        info["_holdco_discount"] = _hnav["discount"]
            except Exception as e:
                logging.debug(f"[Holdco] nav compute {ticker}: {e}")
            pt     = self.engine.price_target(info, cur, sector=_sector_for_nomura)
            if pt.get("target", 0) > 0 and not target_source:
                # 미국/글로벌은 yfinance Ticker.info 기반 DCF 입력값
                target_source = "DCF (yfinance 재무)"
            # 노무라식이 메인 목표가로 채택된 경우 source 라벨 갱신
            if pt.get("nomura_target", 0) > 0 and float(pt.get("target", 0)) == float(pt.get("nomura_target", 0)):
                _nm_method = pt.get("nomura_method") or "Nomura"
                if _nm_method == "NAV-할인율":
                    _hc = pt.get("holdco_components", {}) or {}
                    _cd = _hc.get("current_discount")
                    _td = _hc.get("target_discount")
                    if _cd is not None and _td is not None:
                        target_source = (f"투자지주 NAV-할인율 "
                                         f"(현재할인 {_cd*100:.0f}% vs 적정 {_td*100:.0f}%)")
                    else:
                        target_source = "투자지주 NAV-할인율"
                else:
                    _nm_bias = pt.get("nomura_bias", 1.0)
                    _bias_tag = f" · bias {_nm_bias:.2f}" if _nm_bias and _nm_bias != 1.0 else ""
                    target_source = f"노무라式 {_nm_method}{_bias_tag} ({_sector_for_nomura})"
            si     = self.engine.short_interest(info)
            hurst  = self.engine.hurst_exponent(hist)
            kf     = self.engine.kalman_filter(hist)
            stat   = self.engine.stat_arb_zscore(hist)
            sent   = self.engine.sentiment_proxy(hist)
            orb    = self.engine.orb_breakout(hist)
            nr7    = self.engine.nr7_compression(hist)
            bb_rv  = self.engine.bb_mean_reversion(hist)

            # ════════════════════════════════════════════════════════════
            # STEP 2 — 팩터 원점수 수집 (0~100 정규화된 "팩터 점수")
            # ────────────────────────────────────────────────────────────
            # 설계 원칙:
            #   • 모든 팩터 원점수를 먼저 [0, 100] 범위로 정규화한다.
            #   • _norm_pos: 원점수가 0이 중립이고 양수가 좋은 팩터
            #     (score가 -∞~+∞ 범위)  → 50 기준 선형 매핑
            #   • _norm_raw: 이미 0~N 범위로 반환되는 팩터 (0~30 등)
            #     → 단순 비례 스케일링
            #   • CAN SLIM 원칙 점수도 동일하게 [0,100]으로 정규화 후
            #     가중치를 적용한다 — "c_raw * 0.35 += base" 같은
            #     고정 하드코딩 덧셈은 완전히 폐기한다.
            # ════════════════════════════════════════════════════════════
            w = STRATEGY_WEIGHTS.get(self._scan_strategy,
                                     STRATEGY_WEIGHTS["BALANCED"])

            # ── 투자지주사: 실적/밸류 팩터 무력화, NAV-할인 신호 주축화 ──
            # 지주사 주가는 실적이 아니라 보유 상장지분 NAV에 할인율이
            # 적용돼 결정된다(PER/PBR·ROE·분기EPS는 무의미). 따라서
            # 실적계 팩터 가중치를 0으로 빼고, NAV-할인율을 담은
            # price_target 을 압도적 주축으로, 나머지는 주가/수급 기술
            # 팩터에만 배분한 뒤 합이 1이 되도록 재정규화한다. 이 가중은
            # 전략 무관(STEP 10.7)으로도 동일 적용된다.
            _is_holdco = float(info.get("_holdco_nav_ps") or 0.0) > 0.0
            if _is_holdco:
                _hw = {
                    "price_target": 0.45, "momentum": 0.10, "regime": 0.08,
                    "smart_money": 0.08, "rs": 0.07, "mean_reversion": 0.05,
                    "mtf": 0.04, "volume": 0.03, "drawdown": 0.03,
                    "math": 0.02, "sentiment": 0.02, "short_int": 0.01,
                    "cs_n": 0.01, "cs_s": 0.005, "cs_l": 0.005,
                    "fama_french": 0.0, "quality": 0.0, "cs_c": 0.0,
                    "cs_a": 0.0, "cs_i": 0.0, "orb": 0.0, "nr7": 0.0,
                    "bb_revert": 0.0,
                }
                _hs = sum(_hw.values()) or 1.0
                w = {k: v / _hs for k, v in _hw.items()}

            def _n(raw: float, center: float = 0.0, scale: float = 1.5) -> float:
                """
                연속형 원점수 → [0, 100] 정규화.
                center: 중립(50점)에 해당하는 raw 값 (기본 0)
                scale : raw 1단위가 최종 점수 몇 점에 해당하는지
                """
                return max(0.0, min(100.0, 50.0 + (raw - center) * scale))

            def _n01(raw: float, best: float = 35.0) -> float:
                """
                [0, best] 범위 팩터 → [0, 100] 선형 스케일.
                best: 원점수의 사실상 최고값 (100점에 매핑)
                """
                return max(0.0, min(100.0, raw / best * 100.0))

            # ── 기본 퀀트 6개 정규화 점수 ────────────────────────────
            # scale = 50 / (전략의 실질 반치역) — 전략별 원점수 범위에 맞춤
            f_momentum       = _n(mom["momentum_score"],  scale=0.75)  # [-65,+85]
            f_fama_french    = _n(ff["factor_alpha"],     scale=2.8)   # [-15,+20]
            f_mean_reversion = _n(mr["score"],            scale=1.1)   # [-35,+50]
            f_quality        = _n(qual["quality_score"],  scale=1.5)   # [-25,+40]
            f_regime         = _n(regime["score"],        scale=2.3)   # [-25,+20]
            f_smart_money    = _n(flow["score"],          scale=1.8)   # [-28,+28]

            # ── 보조 퀀트 7개 정규화 점수 ────────────────────────────
            mtf_raw    = {"STRONG_BULLISH": 30, "BULLISH": 15,
                          "NEUTRAL": 0, "BEARISH": -15, "STRONG_BEARISH": -30
                          }.get(mtf["signal"], 0)
            f_mtf          = _n(mtf_raw, center=0, scale=1.67)        # [-30,+30]
            f_drawdown     = _n(dd["score"],          scale=2.8)       # [-20,+15]
            f_volume       = _n(vol_a["score"],       scale=1.7)       # [-23,+35]
            f_rs           = _n(rs["score"],          scale=2.5)       # [-20,+20]
            f_price_target = _n(pt["score"],          scale=2.2)       # [-20,+25]
            f_short_int    = _n(si["score"],          scale=3.3)       # [-15,+15]
            f_math         = _n((hurst["score"] + kf["score"] + stat["score"]) / 3.0, scale=2.5)
            f_sentiment    = _n(sent["sentiment_score"],  scale=2.5)   # [-21,+21]
            f_orb          = _n(orb["score"],   center=15,   scale=2.0)   # [0,30]
            f_nr7          = _n(nr7["score"],   center=17,   scale=3.0)   # [0,35]
            f_bb_revert    = _n(bb_rv["score"], center=18,   scale=1.8)   # [0,36]

            # ════════════════════════════════════════════════════════════
            # STEP 3 — CAN SLIM 원칙별 정규화 점수 산출 + 태그 생성
            # ────────────────────────────────────────────────────────────
            # 원칙별 점수는 모두 [0, 100]으로 정규화하여
            # 가중치 곱셈만으로 최종 점수에 기여한다.
            # 하드코딩된 *0.35, *0.25 같은 원칙별 내부 배율은 폐기.
            # ════════════════════════════════════════════════════════════
            canslim_tags = []

            # ── 투자지주사: 점수 근거 한 줄 설명 (왜 이 점수인지) ────
            if _is_holdco:
                _hc = pt.get("holdco_components", {}) or {}
                _cd = _hc.get("current_discount")
                _td = _hc.get("target_discount")
                _vd = _hc.get("verdict", "")
                if _cd is not None and _td is not None:
                    _cdp, _tdp = _cd * 100.0, _td * 100.0
                    if _vd == "UNDERVALUED":
                        canslim_tags.append(
                            f"💎 지주사라 실적이 아닌 NAV(보유지분 가치)로 평가해요. "
                            f"지금 NAV 대비 {_cdp:.0f}% 할인 거래 중인데 적정 할인은 "
                            f"~{_tdp:.0f}%라, 그만큼 저평가예요")
                    elif _vd == "OVERVALUED":
                        canslim_tags.append(
                            f"💎 지주사라 실적이 아닌 NAV(보유지분 가치)로 평가해요. "
                            f"NAV 할인이 {_cdp:.0f}%로 적정({_tdp:.0f}%)보다 얕아 "
                            f"고평가 구간이에요")
                    else:
                        canslim_tags.append(
                            f"💎 지주사라 실적이 아닌 NAV(보유지분 가치)로 평가해요. "
                            f"NAV 할인 {_cdp:.0f}%가 적정({_tdp:.0f}%)과 비슷한 "
                            f"적정가 수준이에요")
                else:
                    canslim_tags.append(
                        "💎 지주사라 실적이 아닌 보유지분 NAV·할인율로 평가해요 "
                        "(실적·PER·ROE 팩터는 점수에서 제외)")

            # ── [C] Current Quarterly EPS 가속도 ─────────────────────
            c_raw = earn["c_score"]
            if earn["eps_acceleration"]:
                canslim_tags.append("C🔥 분기 실적이 2분기 연속 가속 성장 중이에요")
            elif c_raw >= 28:
                canslim_tags.append("C 분기 순이익이 50% 이상 폭발적으로 늘었어요")
            elif c_raw >= 18:
                canslim_tags.append("C 분기 순이익이 25% 이상 성장해 기준을 충족했어요")
            elif earn["fail_safe_eps"]:
                canslim_tags.append("C⛔ 분기 순이익이 적자예요. 진입에 주의하세요")
            f_cs_c = _n01(max(c_raw, 0.0), best=60.0)   # 60점이 사실상 상한

            # ── [A] Annual Earnings: ROE 17%+ ────────────────────────
            a_raw = ff["a_score"]
            if ff["roe_pass"]:
                canslim_tags.append(f"A✅ 자기자본이익률 {ff['roe']:.0%}로 기준(17%)을 통과했어요")
            else:
                canslim_tags.append(f"A⛔ 자기자본이익률 {ff['roe']:.0%}로 기준(17%)에 미달해요")
            f_cs_a = _n(a_raw)

            # ── [N] New Highs / 컵앤핸들 피벗 ───────────────────────
            n_raw = 0.0
            if mom["near_52w_high"]:
                n_raw += 20
                canslim_tags.append(f"N🚀 52주 최고가에서 {mom['dist_from_52w_high']:.0%} 아래, 신고가 도전 중이에요")
            elif mom["dist_from_52w_high"] < 0.10:
                n_raw += 10
                canslim_tags.append(f"N 52주 최고가에서 10% 이내에 위치했어요")
            else:
                canslim_tags.append(f"N 52주 최고가보다 {mom['dist_from_52w_high']:.0%} 아래에 있어요")
            if mom["pivot_breakout"]:
                n_raw += 15
                canslim_tags.append("N🔔 컵앤핸들 패턴의 피벗을 돌파했어요")
            f_cs_n = _n01(n_raw, best=35.0)

            # ── [S] Supply & Demand (거래량 확인 돌파) ───────────────
            s_raw = vol_a["score"]
            if vol_a["s_confirmed"]:
                canslim_tags.append(f"S✅ 거래량이 평소의 {vol_a['ratio']:.1f}배로 급증해 기관 참여가 확인됐어요")
            elif vol_a["unconfirmed_break"]:
                s_raw -= 10
                canslim_tags.append(f"S⚠️ 가격은 올랐지만 거래량이 부족해요. 가짜 신호일 수 있어요")
            else:
                canslim_tags.append(f"S 거래량이 평소의 {vol_a['ratio']:.1f}배 수준이에요")
            f_cs_s = _n(s_raw)

            # ── [L] Leader or Laggard (RS 80+) ──────────────────────
            l_raw = rs["score"]
            if rs["is_leader"]:
                canslim_tags.append(f"L⭐ 상대강도 {rs['rs_rating']}점으로 시장 주도주예요")
            elif rs["fail_safe_rs"]:
                canslim_tags.append(f"L📉 상대강도 {rs['rs_rating']}점으로 시장 대비 뒤처지고 있어요")
            else:
                canslim_tags.append(f"L 상대강도(RS) {rs['rs_rating']}점이에요")
            f_cs_l = _n(l_raw)

            # ── [I] Institutional Sponsorship (Smart Money) ──────────
            i_raw = flow["score"]
            canslim_tags.append(f"I 기관 자금 흐름은 '{flow['signal']}'이에요")
            f_cs_i = _n(i_raw)

            # ── [M] Market Direction (태그만 — regime에서 점수 처리) ─
            #   다른 원칙(C/A/N/S/L/I)과 동일한 'M+이모지 한글설명' 포맷으로
            #   생성해야 프론트가 본문(main)으로 분류한다. (대괄호 포맷은 보조지표로 빠짐)
            _m_msg = {
                "STRONG_BULL":  "M🔥 시장 전체가 강한 상승 추세예요 (CAN SLIM의 핵심 조건)",
                "BULL":         "M✅ 시장이 상승 추세에 있어요",
                "SIDEWAYS_BULL":"M 시장이 횡보 중이지만 상승 쪽으로 기울어 있어요",
                "STRONG_BEAR":  "M🚫 시장이 강한 하락 추세예요. 점수에 50% 상한이 걸려요",
                "BEAR":         "M🚫 시장이 하락 추세예요. 점수에 50% 상한이 걸려요",
                "SIDEWAYS":     "M 시장이 뚜렷한 방향 없이 횡보 중이에요",
            }.get(regime.get("regime", "SIDEWAYS"),
                  "M 시장이 뚜렷한 방향 없이 횡보 중이에요")
            canslim_tags.append(_m_msg)

            # ════════════════════════════════════════════════════════════
            # STEP 3.5 — Conviction Score (팩터 합의도)
            # ────────────────────────────────────────────────────────────
            # 19개 정규화 팩터 중 같은 방향(>55 또는 <45)의 비율로 산출.
            # HIGH=신뢰도 높음, LOW=팩터 간 상충 → 주의 필요
            # ════════════════════════════════════════════════════════════
            # 가중치>0 인 팩터만 합의도 집계 — 전략별로 비활성 팩터 제외
            _factor_pairs = [
                (f_momentum, w["momentum"]), (f_fama_french, w["fama_french"]),
                (f_mean_reversion, w["mean_reversion"]), (f_quality, w["quality"]),
                (f_regime, w["regime"]), (f_smart_money, w["smart_money"]),
                (f_mtf, w["mtf"]), (f_drawdown, w["drawdown"]),
                (f_volume, w["volume"]), (f_rs, w["rs"]),
                (f_price_target, w["price_target"]), (f_short_int, w["short_int"]),
                (f_math, w["math"]), (f_sentiment, w["sentiment"]),
                (f_cs_c, w["cs_c"]), (f_cs_a, w["cs_a"]), (f_cs_n, w["cs_n"]),
                (f_cs_s, w["cs_s"]), (f_cs_l, w["cs_l"]), (f_cs_i, w["cs_i"]),
                (f_orb, w["orb"]), (f_nr7, w["nr7"]), (f_bb_revert, w["bb_revert"]),
            ]
            _all_f = [s for s, wt in _factor_pairs if wt > 0]
            if not _all_f:
                _all_f = [s for s, _ in _factor_pairs]
            _bull = sum(1 for s in _all_f if s > 55)
            _bear = sum(1 for s in _all_f if s < 45)
            _agree = max(_bull, _bear) / len(_all_f)
            if _agree >= 0.75:   conviction = "HIGH"
            elif _agree >= 0.55: conviction = "MID"
            else:                conviction = "LOW"

            # ════════════════════════════════════════════════════════════
            # STEP 4 — 통합 가중 합산 (예산 분배 방식)
            # ────────────────────────────────────────────────────────────
            # base = Σ (정규화 팩터 점수 × 해당 팩터 가중치)
            # 모든 w[] 합 = 1.0 이므로 base는 자연스럽게 [0, 100] 범위
            # 추가 점수를 += 로 쌓는 하드코딩 구조를 완전히 폐기한다.
            # ════════════════════════════════════════════════════════════
            base = (
                # 기본 퀀트
                f_momentum       * w["momentum"]
                + f_fama_french    * w["fama_french"]
                + f_mean_reversion * w["mean_reversion"]
                + f_quality        * w["quality"]
                + f_regime         * w["regime"]
                + f_smart_money    * w["smart_money"]
                # 보조 퀀트
                + f_mtf            * w["mtf"]
                + f_drawdown       * w["drawdown"]
                + f_volume         * w["volume"]
                + f_rs             * w["rs"]
                + f_price_target   * w["price_target"]
                + f_short_int      * w["short_int"]
                + f_math           * w["math"]
                + f_sentiment      * w["sentiment"]
                # CAN SLIM 원칙
                + f_cs_c           * w["cs_c"]
                + f_cs_a           * w["cs_a"]
                + f_cs_n           * w["cs_n"]
                + f_cs_s           * w["cs_s"]
                + f_cs_l           * w["cs_l"]
                + f_cs_i           * w["cs_i"]
                # 단타 팩터
                + f_orb            * w["orb"]
                + f_nr7            * w["nr7"]
                + f_bb_revert      * w["bb_revert"]
            )
            # base는 이론적으로 [0, 100] 범위.
            # 단, STEP 9 슈퍼 그로스 승수(최대 1.18) 적용 시 사전 100점 클리핑이
            # 보너스를 무력화하는 역설을 막기 위해 [0, 120]으로 여유를 둔다.
            # 최종 클리핑은 STEP 10/11에서 수행.
            base = max(0.0, min(120.0, base))

            # ════════════════════════════════════════════════════════════
            # STEP 5 — Hurst + Kalman 신뢰도 조정 (±4% 이내 소폭 보정)
            # ════════════════════════════════════════════════════════════
            hurst_kalman_trust = 1.0
            if hurst["h"] >= 0.60 and kf["signal"] in ("BUY_TREND", "POSSIBLE_REVERSAL"):
                hurst_kalman_trust = 1.04
                canslim_tags.append(f"[MATH✅] Hurst {hurst['h']:.2f}≥0.6 + Kalman {kf['signal']}")
            elif hurst["h"] < 0.45 and kf["signal"] == "SELL_TREND":
                hurst_kalman_trust = 0.94
                canslim_tags.append(f"[MATH⚠️] Hurst {hurst['h']:.2f} + Kalman SELL — 신뢰도↓")
            else:
                canslim_tags.append(f"[MATH] Hurst {hurst['h']:.2f}  Kalman {kf['signal']}")

            base = max(0.0, min(120.0, base * hurst_kalman_trust))

            # ── Breakdown용 부분 점수 저장 (기존 UI 호환) ────────────
            s_mom  = f_momentum       * w["momentum"]       * 100
            s_ff   = f_fama_french    * w["fama_french"]    * 100
            s_mr   = f_mean_reversion * w["mean_reversion"] * 100
            s_qual = f_quality        * w["quality"]        * 100
            s_reg  = f_regime         * w["regime"]         * 100
            s_flow = f_smart_money    * w["smart_money"]    * 100

            # ════════════════════════════════════════════════════════════
            # STEP 6 — Fail-Safe Ceiling
            # EPS 마이너스 OR RS Rating < 40 → 50점 상한
            # ════════════════════════════════════════════════════════════
            # 지주사는 회계상 적자(EPS<0)여도 NAV 가치는 멀쩡하므로
            # EPS Fail-Safe 면제(주가 기반 RS Fail-Safe는 그대로 적용).
            # 딥테크 수주형 적자 기업도 동일하게 EPS Fail-Safe 면제한다.
            # row dict는 아직 생성 전이므로 게이트가 요구하는 3개 필드를 인라인으로 구성.
            _deeptech_row = {
                "Sector": self._ticker_sector_map.get(ticker, "") if hasattr(self, "_ticker_sector_map") else "",
                "_RevenueGrowth": safe_get(info.get("revenueGrowth"), 0.0),
                "_MarketCap": safe_get(info.get("marketCap"), 0),
            }
            _is_deeptech = _is_deeptech_story(ticker, _deeptech_row)
            fail_safe_triggered = (
                (earn["fail_safe_eps"] and not _is_holdco and not _is_deeptech)
                or rs["fail_safe_rs"]
            )
            fail_safe_label = ""
            # 극강 모멘텀 종목: EPS FailSafe 완화 (RS≥90, 12M수익률>200%, Hurst>0.65)
            _momentum_override = (
                earn["fail_safe_eps"]
                and not rs["fail_safe_rs"]
                and rs["rs_rating"] >= 90
                and mom["mom_12m"] > 2.0
                and hurst["h"] > 0.65
            )
            if fail_safe_triggered:
                if _momentum_override:
                    _ceil = CANSLIM["SCORE_CEIL_MOMENTUM_OVERRIDE"]
                    base = min(base, _ceil)
                    reasons = [f"EPS<0 but RS{rs['rs_rating']}+Mom{mom['mom_12m']:+.0%}"]
                    fail_safe_label = f"⚡ Fail-Safe 완화(극강모멘텀) → 최대 {_ceil}점"
                else:
                    base = min(base, CANSLIM["SCORE_CEIL_LAGGARD"])
                    reasons = []
                    if earn["fail_safe_eps"]: reasons.append("EPS<0")
                    if rs["fail_safe_rs"]:    reasons.append(f"RS{rs['rs_rating']}<40")
                    fail_safe_label = f"⛔ Fail-Safe({', '.join(reasons)}) → 최대 {CANSLIM['SCORE_CEIL_LAGGARD']}점"
                canslim_tags.append(fail_safe_label)

            # ════════════════════════════════════════════════════════════
            # STEP 7 — VIX 조정 (base가 이미 [0,100]이므로 곱셈 범위 축소)
            # ════════════════════════════════════════════════════════════
            vix_m = _smooth_band(self.vix_value, [
                (12.0, 1.04), (15.0, 1.02), (20.0, 1.00),
                (25.0, 0.93), (30.0, 0.86), (35.0, 0.80), (45.0, 0.75),
            ])
            base = max(0.0, min(120.0, base * vix_m))
            # Re-clamp to fail-safe ceiling if it was set (VIX upmove must not bypass cap).
            if fail_safe_triggered:
                _ceil_post = CANSLIM["SCORE_CEIL_MOMENTUM_OVERRIDE"] if _momentum_override else CANSLIM["SCORE_CEIL_LAGGARD"]
                base = min(base, _ceil_post)

            # ════════════════════════════════════════════════════════════
            # STEP 8 — [M] Bear Cap: Bear 시장 → 최종 점수 50% 상한
            # ════════════════════════════════════════════════════════════
            # Bear Cap은 fail-safe 여부와 무관하게 약세장에선 항상 적용 (이전 버전은
            # fail_safe_triggered 시 우회되어 모멘텀 override 종목(천장 70)이 약세장에서
            # 50 캡을 뚫는 버그가 있었음).
            bear_cap_applied = False
            if regime["m_bear_cap"]:
                cap_val = 100.0 * CANSLIM["BEAR_CAP"]   # 50점
                if base > cap_val:
                    base = cap_val
                    bear_cap_applied = True
                    # 주: Bear Cap 사실은 메인 M 원칙 문구(_m_msg: "점수에 50%
                    # 상한이 걸려요")와 '하락장↓' 위험 배지로 이미 표시된다.
                    # 여기서 [M…] 대괄호 태그를 추가하면 프론트가 M을
                    # CAN SLIM 본문이 아닌 보조지표로 잘못 분류하므로 추가하지 않는다.

            # ════════════════════════════════════════════════════════════
            # STEP 9 — 슈퍼 그로스 승수 (엄격화)
            # ────────────────────────────────────────────────────────────
            # base가 이미 [0,100]으로 정규화되어 있으므로
            # 승수 상한을 낮춰 클리핑 후 100점 다발 현상을 방지한다.
            #   C+A+L 완전충족 + EPS 가속: ×1.18 (구버전 ×1.5 → 대폭 축소)
            #   2/3 충족            : ×1.05
            # 이렇게 해도 base가 85점인 종목은 승수 후 최대 100.3 → 클리핑
            # 90점 이상이 희소해지는 효과를 보장한다.
            # ════════════════════════════════════════════════════════════
            super_mult = 1.0
            super_growth_criteria = {
                "C": earn["eps_growth"] >= CANSLIM["EPS_MIN_GROWTH"],
                "A": ff["roe_pass"],
                "L": rs["is_leader"],
            }
            fulfilled = sum(super_growth_criteria.values())

            if not fail_safe_triggered and not bear_cap_applied:
                if fulfilled == 3:
                    if earn["eps_acceleration"]:
                        super_mult = 1.18
                    elif earn["eps_growth"] >= CANSLIM["EPS_STRONG"]:
                        super_mult = 1.14
                    elif mom["near_52w_high"] and vol_a["s_confirmed"]:
                        super_mult = 1.10
                    else:
                        super_mult = 1.07
                    canslim_tags.append(
                        f"⭐ SUPER GROWTH MULTIPLIER × {super_mult:.2f}"
                        f"  (C={'✅' if super_growth_criteria['C'] else '❌'}"
                        f" A={'✅' if super_growth_criteria['A'] else '❌'}"
                        f" L={'✅' if super_growth_criteria['L'] else '❌'})"
                    )
                elif fulfilled == 2:
                    super_mult = 1.05
                    canslim_tags.append(
                        f"[Partial] 2/3 CAN SLIM 조건 충족 × {super_mult:.2f}"
                    )

            base_pre_super = base
            base = max(0.0, min(100.0, base * super_mult))

            # ════════════════════════════════════════════════════════════
            # STEP 10 — 변동성 조정 (DE Shaw) + 최종 클리핑
            # ════════════════════════════════════════════════════════════
            va    = self.engine.vol_adjusted(hist, base)
            final = max(0.0, min(100.0, va["adj_score"]))

            # ════════════════════════════════════════════════════════════
            # STEP 10.5 — Low Liquidity 상한 (시그널 결정 전 적용)
            # ════════════════════════════════════════════════════════════
            if low_liquidity:
                final = min(final, 55.0)
                canslim_tags.append("[LIQ⚠️] 거래대금 부족 → 최대 55점")

            # ════════════════════════════════════════════════════════════
            # STEP 10.7 — 전략 통합 점수 (5개 전략 동시 산출)
            # ────────────────────────────────────────────────────────────
            # 모드 전환 없이 한 번의 스캔에서 5개 전략 점수를 모두 제공.
            # 정규화 팩터(STEP 1~3)는 전략 무관, STEP 4의 가중치만 다르며
            # STEP 5~10.5 후처리도 전략 무관이므로 동일 시퀀스를 반복한다.
            # ════════════════════════════════════════════════════════════
            _factor_values = {
                "momentum":       f_momentum,
                "fama_french":    f_fama_french,
                "mean_reversion": f_mean_reversion,
                "quality":        f_quality,
                "regime":         f_regime,
                "smart_money":    f_smart_money,
                "mtf":            f_mtf,
                "drawdown":       f_drawdown,
                "volume":         f_volume,
                "rs":             f_rs,
                "price_target":   f_price_target,
                "short_int":      f_short_int,
                "math":           f_math,
                "sentiment":      f_sentiment,
                "cs_c":           f_cs_c,
                "cs_a":           f_cs_a,
                "cs_n":           f_cs_n,
                "cs_s":           f_cs_s,
                "cs_l":           f_cs_l,
                "cs_i":           f_cs_i,
                "orb":            f_orb,
                "nr7":            f_nr7,
                "bb_revert":      f_bb_revert,
            }
            all_scores: dict[str, float] = {}
            for _mode, _w in STRATEGY_WEIGHTS.items():
                # 지주사 평가는 전략(공격/방어 등) 무관 — NAV-할인 가중으로 통일
                if _is_holdco:
                    _w = w
                _b = sum(_factor_values[k] * _w.get(k, 0.0) for k in _factor_values)
                _b = max(0.0, min(120.0, _b))
                _b = max(0.0, min(120.0, _b * hurst_kalman_trust))
                if fail_safe_triggered:
                    _ceil = CANSLIM["SCORE_CEIL_MOMENTUM_OVERRIDE"] if _momentum_override else CANSLIM["SCORE_CEIL_LAGGARD"]
                    _b = min(_b, _ceil)
                _b = max(0.0, min(120.0, _b * vix_m))
                if fail_safe_triggered:
                    _ceil_ms = CANSLIM["SCORE_CEIL_MOMENTUM_OVERRIDE"] if _momentum_override else CANSLIM["SCORE_CEIL_LAGGARD"]
                    _b = min(_b, _ceil_ms)
                if regime["m_bear_cap"]:
                    _b = min(_b, 100.0 * CANSLIM["BEAR_CAP"])
                _b = max(0.0, min(100.0, _b * super_mult))
                _va = self.engine.vol_adjusted(hist, _b)
                _f = max(0.0, min(100.0, _va["adj_score"]))
                if low_liquidity:
                    _f = min(_f, 55.0)
                all_scores[_mode] = round(_f, 1)

            # 5개 전략을 통합한 복합 점수 (단일 표시용)
            #  - 평균(60%) + 최대(40%) 가중: 보편적으로 견조하면서 어느 한 전략에서 빛나는 종목 우대
            if all_scores:
                _vals = list(all_scores.values())
                _avg = sum(_vals) / len(_vals)
                _max = max(_vals)
                # Disagreement penalty: 전략간 점수 분산이 클수록 신뢰도 감소
                # (Renaissance/Two Sigma 스타일 — 신호 일관성 가중)
                if len(_vals) >= 2:
                    _mean = _avg
                    _var = sum((v - _mean) ** 2 for v in _vals) / len(_vals)
                    _std = _var ** 0.5
                else:
                    _std = 0.0
                _disagreement_penalty = min(8.0, 0.5 * _std)
                composite_score = round(_avg * 0.6 + _max * 0.4 - _disagreement_penalty, 1)
                composite_score = max(0.0, composite_score)
                final = composite_score  # 표시되는 TotalScore 로 사용

            # v21 실험적 보강 — ENABLE_V21_FACTORS=1 환경변수 시 활성
            try:
                from factor_enhancements_v21 import enhance_score
                _v21 = enhance_score(ticker, final)
                final = _v21["final"]
            except Exception:
                pass

            # ════════════════════════════════════════════════════════════
            # STEP 11 — CAN SLIM 시그널 결정
            # ════════════════════════════════════════════════════════════
            if fail_safe_triggered:
                if _momentum_override: signal = "⚡ MOMENTUM (Fail-Safe 완화)"
                elif final >= 45:      signal = "⚠️ WATCH (Fail-Safe Active)"
                else:                  signal = "📉 LAGGARD (AVOID)"
            elif bear_cap_applied:
                signal = "🚫 BEAR MARKET — AVOID"
            elif final >= 90 and fulfilled == 3:
                signal = "⭐⭐⭐⭐ CAN SLIM BREAKOUT"
            elif final >= 82:
                if rs["is_leader"] and vol_a["s_confirmed"]:
                    signal = "🚀 HIGH MOMENTUM LEADER"
                else:
                    signal = "⭐⭐⭐ STRONG LEADER"
            elif final >= 72:
                signal = "⭐⭐ LEADER"
            elif final >= 60:
                signal = "⭐ WATCH LIST — Accumulate"
            elif final >= 48:
                signal = "⏸ NEUTRAL — Hold"
            elif final >= 35:
                if rs["fail_safe_rs"]:
                    signal = "📉 LAGGARD (AVOID)"
                else:
                    signal = "⚠️ CAUTION — Reduce"
            else:
                signal = "📉 SELL / AVOID"

            # 추가 태그
            if mom["near_52w_high"] and vol_a["s_confirmed"]:
                signal += " 🔔[BREAKOUT]"
            elif mom["pivot_breakout"]:
                signal += " [PIVOT]"
            if earn["eps_acceleration"]:
                signal += " [EPS🔥]"
            if hurst["h"] >= 0.65:
                signal += " [TREND]"
            if vol_a["ratio"] >= 2.0:
                signal += " [VOL🔥]"
            if low_liquidity:
                signal += " [LOW LIQ]"

            # ════════════════════════════════════════════════════════════
            # STEP 12 — Breakdown 구성 (CAN SLIM 원칙 코드 표기)
            # ════════════════════════════════════════════════════════════
            # vol_impact = 변동성 조정 후 점수 - 슈퍼 그로스 직전 base
            # (괄호 누락 + super_mult 분할 보정의 클리핑 손실 문제 수정)
            vol_impact = va["adj_score"] - base_pre_super
            breakdown = [
                # ── CAN SLIM 7원칙 ───────────────────────────────────
                ("[C] EPS 가속도 (Current QE)",
                 None if earn.get("data_missing") else round(earn["c_score"], 1),
                 "실적 데이터가 아직 공개되지 않았어요. (분기 보고 전이거나 공시 미반영)"
                 if earn.get("data_missing") else
                 f"지난 분기 순이익이 {earn['eps_growth']:+.0%} 변동했어요. "
                 f"{'연속 성장 중이에요' if earn.get('eps_acceleration') else '성장 추세예요' if earn.get('trend') == 'up' else '주춤하고 있어요'}."),

                ("[A] 연간실적 ROE 기준 (Annual EPS)",
                 round(ff["a_score"], 1),
                 f"자기자본이익률(ROE) {ff['roe']:.0%}이에요. "
                 f"{'기준(17%)을 통과했어요. 돈을 잘 버는 회사예요.' if ff['roe_pass'] else '기준(17%)에 미달해요. 수익성 점검이 필요해요.'}"),

                ("[N] 신고가·피벗 돌파 (New Highs)",
                 round(n_raw, 1),
                 f"52주 최고가에서 {mom['dist_from_52w_high']:.0%} 아래에 있어요. "
                 f"{'신고가 권역에 진입했어요.' if mom['near_52w_high'] else '아직 신고가까지 거리가 있어요.'}"
                 f"{' 컵앤핸들 피벗 돌파가 감지됐어요.' if mom['pivot_breakout'] else ''}"),

                ("[S] 거래량 확인 돌파 (Supply/Demand)",
                 round(vol_a["score"], 1),
                 f"거래량이 평소의 {vol_a['ratio']:.1f}배예요. "
                 f"{'기관 매수로 돌파가 확인됐어요.' if vol_a['s_confirmed'] else '거래량이 뒷받침되지 않았어요. 가짜 돌파일 수 있어요.' if vol_a['unconfirmed_break'] else '돌파 신호는 없어요.'}"),

                ("[L] 주도주 판별 (Leader/Laggard)",
                 round(rs["score"], 1),
                 f"시장 대비 상대강도(RS) {rs['rs_rating']}점이에요. "
                 f"{'시장을 이끄는 주도주예요.' if rs.get('is_leader') else '시장보다 많이 뒤처지고 있어요.' if rs.get('fail_safe_rs') else '시장과 비슷한 흐름이에요.'}"),

                ("[I] 기관 수급 (Institutional)",
                 round(f_smart_money, 1),
                 f"기관 자금 흐름: '{flow['signal']}'이에요. "
                 f"매수 압력이 {'강해요' if flow['mfi'] > 60 else '약해요' if flow['mfi'] < 40 else '중립이에요'} (MFI {flow['mfi']:.0f})."),

                ("[M] 시장 방향 (Market Direction)",
                 round(f_regime, 1),
                 f"현재 시장 방향: '{regime['m_label']}'이에요. "
                 f"추세 강도가 {'강해요' if regime['adx'] > 25 else '약해요'} (ADX {regime['adx']:.0f})."),

                # ── 보조 퀀트 전략 ────────────────────────────────────
                ("[Quant] Fama-French Factor",
                 round(f_fama_french, 1),
                 f"가치·퀄리티 팩터 알파 {ff['factor_alpha']:+.1f}점이에요. "
                 f"{'저평가 매력이 있어요.' if ff['factor_alpha'] > 0 else '고평가 구간이에요.'}"),

                ("[Quant] Mean Reversion",
                 round(f_mean_reversion, 1),
                 f"RSI {mr['rsi']:.0f}로 "
                 f"{'과매수 구간이에요. 단기 조정 가능성이 있어요.' if mr['rsi'] > 70 else '과매도 구간이에요. 반등 가능성이 있어요.' if mr['rsi'] < 30 else '중립 구간이에요.'} "
                 f"Z-Score {mr['z_score']:+.1f}."),

                ("[Quant] Momentum (Carhart)",
                 round(f_momentum, 1),
                 f"1년간 수익률 {mom['mom_12m']:+.0%}로 모멘텀이 "
                 f"{'강해요.' if mom['mom_12m'] > 0.2 else '양호해요.' if mom['mom_12m'] > 0 else '부진해요.'} "
                 f"(동종 업종 내 {mom['rank']} 순위)"),

                ("[Quant] Multi-Timeframe",
                 round(f_mtf, 1),
                 f"단기·중기·장기 추세 종합: "
                 f"{'강한 상승 추세예요.' if mtf_raw >= 30 else '상승 추세예요.' if mtf_raw > 0 else '하락 추세예요.' if mtf_raw < 0 else '중립이에요.'} "
                 f"(신호: {mtf['signal']})"),

                ("[Quant] Drawdown Risk",
                 round(f_drawdown, 1),
                 f"최근 최대 낙폭(MDD) {dd['current_dd']:.0%}이에요. "
                 f"위험도는 '{dd['risk']}'로 평가돼요."),

                ("[Quant] Smart Money Flow",
                 round(f_smart_money, 1),
                 f"스마트머니 흐름 — A/D: "
                 f"{'매집' if flow['ad'] == 'bullish' else '분산' if flow['ad'] == 'bearish' else '중립'}, "
                 f"OBV 추세: {'상승' if flow['obv_trend'] == 'up' else '하락' if flow['obv_trend'] == 'down' else '횡보'}이에요. "
                 f"기관 자금이 {'들어오고 있어요.' if flow['ad'] == 'bullish' else '빠져나가고 있어요.' if flow['ad'] == 'bearish' else '중립이에요.'}"),

                ("[Quant] Target Price Factor",
                 round(f_price_target, 1),
                 f"목표가 팩터 점수 {pt['score']:+.0f}점을 "
                 f"{'노무라식 ' + str(pt.get('target_method', 'Nomura')) if pt.get('nomura_target', 0) > 0 and float(pt.get('target', 0)) == float(pt.get('nomura_target', 0)) else 'DCF'} "
                 f"기준 목표가로 계산했어요. "
                 f"현재 목표가 {pt['target']:,.0f}, 상승여력 {pt['upside']:+.0%}, 전망은 '{pt['view']}'예요."),

                ("[Quant] Short Interest",
                 round(f_short_int, 1),
                 f"공매도 비율 {si['pct']:.0%}로 위험도는 '{si['risk']}'이에요. "
                 f"{'공매도가 많아 주의가 필요해요.' if si['pct'] > 0.05 else '공매도 부담이 적어요.'}"),

                ("[Math] Hurst Exponent",
                 round(_n(hurst["score"], scale=2.5), 1),
                 f"허스트 지수 {hurst['h']:.2f}로 '{hurst['nature']}'를 나타내요. "
                 f"{'추세가 지속될 가능성이 높아요.' if hurst['h'] > 0.6 else '평균 회귀 성향이 강해요.' if hurst['h'] < 0.4 else '방향성이 불확실해요.'}"),

                ("[Math] Kalman Filter",
                 round(_n(kf["score"], scale=2.5), 1),
                 f"칼만 필터 신호: "
                 f"{'매수' if kf['signal'] == 'buy' else '매도' if kf['signal'] == 'sell' else '중립'}이에요. "
                 f"추세 신뢰도 {hurst_kalman_trust:.0%}예요."),

                ("[Math] Stat Arb Z-Score",
                 round(_n(stat["score"], scale=2.5), 1),
                 f"통계적 Z-Score {stat['z']:+.1f}이에요. "
                 f"{'과매수 가능성이 있어요.' if stat['z'] > 2 else '과매도 가능성이 있어요.' if stat['z'] < -2 else '정상 범위예요.'}"),

                ("[Adj] Vol-Adjusted (DE Shaw)",
                 round(vol_impact, 1),
                 (f"변동성 대비 수익률이 {'효율적이에요' if va['efficiency'] == 'high' else '평범해요' if va['efficiency'] == 'mid' else '낮아요'}. "
                  f"최종 점수에 x{super_mult:.2f} 배율이 적용됐어요.")),

                ("[Sentiment] 시장 심리 프록시",
                 round(f_sentiment, 1),
                 (f"뉴스 없이 가격·거래량만으로 심리를 추정했어요. "
                  f"현재 신호는 '{sent['signal']}'이에요. "
                  f"상승 거래량 비중 {sent['up_vol_ratio']:.0%}, "
                  f"갭 방향 {'+위' if sent['gap_bias'] > 0 else '아래'}, "
                  f"종가 강도 {sent['close_strength']:.0%}예요.")),
            ]

            # SCALPING 전용 단타 팩터 — 가중치>0 일 때만 Breakdown에 노출
            if w.get("orb", 0) > 0 or w.get("nr7", 0) > 0 or w.get("bb_revert", 0) > 0:
                breakdown.extend([
                    ("[Scalp] ORB 돌파",
                     round(f_orb * w["orb"], 1),
                     f"ORB 신호: '{orb.get('signal', 'NONE')}' (점수 {orb.get('score', 0):.1f})"),
                    ("[Scalp] NR7 압축",
                     round(f_nr7 * w["nr7"], 1),
                     f"NR7 신호: '{nr7.get('signal', 'NONE')}' (점수 {nr7.get('score', 0):.1f})"),
                    ("[Scalp] BB 반등",
                     round(f_bb_revert * w["bb_revert"], 1),
                     f"BB 반등 신호: '{bb_rv.get('signal', 'NONE')}' (점수 {bb_rv.get('score', 0):.1f})"),
                ])

            # CAN SLIM 원칙 요약을 Breakdown 첫 줄에 삽입
            principle_summary = "\n".join(canslim_tags)
            breakdown.insert(0, (
                "══ CAN SLIM 원칙 요약 ══",
                round(final, 1),
                principle_summary
            ))

            # ── Breakdown 항목에 계산식 + 상세 과정 추가 ──────────
            def _md(inputs_str, raw, norm_desc, fv, wv):
                """상세 계산 과정 문자열 생성."""
                contrib = fv * wv if wv > 0 else 0
                return (
                    f"📊 입력 데이터\n{inputs_str}\n"
                    f"📐 계산 과정\n"
                    f"① 원점수: {raw:.1f}\n"
                    f"② 정규화: {norm_desc} → {fv:.1f}/100\n"
                    f"③ 가중치: {wv*100:.1f}% ({self._scan_strategy})\n"
                    f"④ 기여도: {fv:.1f} × {wv*100:.1f}% = {contrib:.1f}점"
                )
            _w = w  # 현재 전략 가중치
            _calc_info = {
                "[C]":              (f_cs_c,           _w.get("cs_c", 0)),
                "[A]":              (f_cs_a,           _w.get("cs_a", 0)),
                "[N]":              (f_cs_n,           _w.get("cs_n", 0)),
                "[S]":              (f_cs_s,           _w.get("cs_s", 0)),
                "[L]":              (f_cs_l,           _w.get("cs_l", 0)),
                "[I]":              (f_cs_i,           _w.get("cs_i", 0)),
                "[M]":              (f_regime,         _w.get("regime", 0)),
                "[Quant] Fama":     (f_fama_french,    _w.get("fama_french", 0)),
                "[Quant] Mean":     (f_mean_reversion, _w.get("mean_reversion", 0)),
                "[Quant] Momentum": (f_momentum,       _w.get("momentum", 0)),
                "[Quant] Multi":    (f_mtf,            _w.get("mtf", 0)),
                "[Quant] Drawdown": (f_drawdown,       _w.get("drawdown", 0)),
                "[Quant] Smart":    (f_smart_money,    _w.get("smart_money", 0)),
                "[Quant] Analyst":  (f_price_target,   _w.get("price_target", 0)),
                "[Quant] Short":    (f_short_int,      _w.get("short_int", 0)),
                "[Math] Hurst":     (_n(hurst["score"], scale=2.5), _w.get("math", 0) / 3),
                "[Math] Kalman":    (_n(kf["score"], scale=2.5),    _w.get("math", 0) / 3),
                "[Math] Stat":      (_n(stat["score"], scale=2.5),  _w.get("math", 0) / 3),
                "[Sentiment]":      (f_sentiment,      _w.get("sentiment", 0)),
                "[Scalp] ORB":      (f_orb,            _w.get("orb", 0)),
                "[Scalp] NR7":      (f_nr7,            _w.get("nr7", 0)),
                "[Scalp] BB":       (f_bb_revert,      _w.get("bb_revert", 0)),
            }
            _detail_inputs = {
                "[C]": (c_raw, f"_n01({c_raw:.1f}, best=60)",
                    f"• EPS 성장률: {earn['eps_growth']:+.0%}\n"
                    f"• 가속 성장: {'예 ✓' if earn.get('eps_acceleration') else '아니오'}\n"
                    f"• 추세: {earn.get('trend', '-')}"),
                "[A]": (a_raw, f"_n({a_raw:.1f})",
                    f"• ROE: {ff['roe']:.0%}\n"
                    f"• ROE 기준(17%) 통과: {'예 ✓' if ff['roe_pass'] else '아니오'}"),
                "[N]": (n_raw, f"_n01({n_raw:.1f}, best=35)",
                    f"• 52주 최고가 거리: {mom['dist_from_52w_high']:.0%}\n"
                    f"• 신고가 근접: {'예 ✓' if mom['near_52w_high'] else '아니오'}\n"
                    f"• 피벗 돌파: {'예 ✓' if mom['pivot_breakout'] else '아니오'}"),
                "[S]": (s_raw, f"_n({s_raw:.1f})",
                    f"• 거래량 비율: {vol_a['ratio']:.1f}x\n"
                    f"• 돌파 확인: {'예 ✓' if vol_a['s_confirmed'] else '아니오'}"),
                "[L]": (l_raw, f"_n({l_raw:.1f})",
                    f"• RS Rating: {rs['rs_rating']}\n"
                    f"• 주도주: {'예 ✓' if rs.get('is_leader') else '아니오'}"),
                "[I]": (i_raw, f"_n({i_raw:.1f})",
                    f"• 자금 흐름: {flow['signal']}\n"
                    f"• MFI: {flow['mfi']:.0f}\n"
                    f"• A/D: {flow['ad']}, OBV: {flow['obv_trend']}"),
                "[M]": (regime["score"], f"_n({regime['score']:.1f}, scale=2.3)",
                    f"• 시장 방향: {regime['m_label']}\n"
                    f"• ADX: {regime['adx']:.0f}"),
                "[Quant] Fama": (ff["factor_alpha"], f"_n({ff['factor_alpha']:.1f}, scale=2.8)",
                    f"• 팩터 알파: {ff['factor_alpha']:+.1f}\n"
                    f"• ROE: {ff['roe']:.0%}"),
                "[Quant] Mean": (mr["score"], f"_n({mr['score']:.1f}, scale=1.1)",
                    f"• RSI: {mr['rsi']:.0f}\n"
                    f"• Z-Score: {mr['z_score']:+.1f}"),
                "[Quant] Momentum": (mom["momentum_score"], f"_n({mom['momentum_score']:.1f}, scale=0.75)",
                    f"• 12개월 수익률: {mom['mom_12m']:+.0%}\n"
                    f"• 섹터 내 순위: {mom['rank']}"),
                "[Quant] Multi": (mtf_raw, f"_n({mtf_raw:.1f}, scale=1.67)",
                    f"• MTF 신호: {mtf['signal']}"),
                "[Quant] Drawdown": (dd["score"], f"_n({dd['score']:.1f}, scale=2.8)",
                    f"• 최대 낙폭(MDD): {dd['current_dd']:.0%}\n"
                    f"• 위험도: {dd['risk']}"),
                "[Quant] Smart": (flow["score"], f"_n({flow['score']:.1f}, scale=1.8)",
                    f"• A/D: {flow['ad']}\n"
                    f"• OBV 추세: {flow['obv_trend']}\n"
                    f"• MFI: {flow['mfi']:.0f}"),
                "[Quant] Analyst": (pt["score"], f"_n({pt['score']:.1f}, scale=2.2)",
                    f"• DCF 상승여력: {pt['upside']:+.0%}\n"
                    f"• 전망: {pt['view']}"),
                "[Quant] Short": (si["score"], f"_n({si['score']:.1f}, scale=3.3)",
                    f"• 공매도 비율: {si['pct']:.0%}\n"
                    f"• 위험도: {si['risk']}"),
                "[Math] Hurst": (hurst["score"], f"_n({hurst['score']:.1f}, scale=2.5)",
                    f"• 허스트 지수: {hurst['h']:.2f}\n"
                    f"• 성격: {hurst['nature']}"),
                "[Math] Kalman": (kf["score"], f"_n({kf['score']:.1f}, scale=2.5)",
                    f"• 칼만 신호: {kf['signal']}\n"
                    f"• 추세 신뢰도: {hurst_kalman_trust:.0%}"),
                "[Math] Stat": (stat["score"], f"_n({stat['score']:.1f}, scale=2.5)",
                    f"• Z-Score: {stat['z']:+.1f}"),
                "[Sentiment]": (sent["sentiment_score"], f"_n({sent['sentiment_score']:.1f}, scale=2.5)",
                    f"• 심리 신호: {sent['signal']}\n"
                    f"• 상승 거래량 비중: {sent['up_vol_ratio']:.0%}\n"
                    f"• 종가 강도: {sent['close_strength']:.0%}"),
            }
            for idx in range(1, len(breakdown)):
                lbl, sc, desc = breakdown[idx][:3]
                detail = ""
                for prefix, (fv, wv) in _calc_info.items():
                    if lbl.startswith(prefix):
                        if wv > 0:
                            contrib = fv * wv
                            desc += f"\n📐 점수 {fv:.1f}/100 × 가중치 {wv*100:.1f}% = 기여도 {contrib:.1f}점"
                        # 상세 과정 생성
                        di = _detail_inputs.get(prefix)
                        if di:
                            raw_val, norm_str, inputs_str = di
                            detail = _md(inputs_str, raw_val, norm_str, fv, wv)
                        break
                if lbl.startswith("[Adj]"):
                    desc += f"\n📐 변동성 조정 {vol_impact:+.1f}점 · 슈퍼그로스 배율 ×{super_mult:.2f}"
                    detail = (
                        f"📊 입력 데이터\n"
                        f"• 변동성 효율: {va['efficiency']}\n"
                        f"• 슈퍼그로스 배율: ×{super_mult:.2f}\n"
                        f"📐 계산 과정\n"
                        f"① 변동성 조정 점수: {va['adj_score']:.1f}\n"
                        f"② 조정 전 base: {base_pre_super:.1f}\n"
                        f"③ 영향: {vol_impact:+.1f}점\n"
                        f"④ 최종 점수에 {vol_impact:+.1f}점 반영"
                    )
                breakdown[idx] = (lbl, sc, desc, detail)

            # ── TopReason: 상위 이유 한줄 요약 생성 ──────────────
            top_reasons = []
            if mom["near_52w_high"]:
                top_reasons.append("52주 신고가 근접")
            if vol_a["s_confirmed"]:
                top_reasons.append(f"거래량 {vol_a['ratio']:.1f}x 돌파")
            if rs["is_leader"]:
                top_reasons.append(f"RS {rs['rs_rating']} 주도주")
            if earn["eps_acceleration"]:
                top_reasons.append("EPS 가속")
            if ff["roe_pass"]:
                top_reasons.append(f"ROE {ff['roe']:.0%}")
            if mr["rsi"] <= 30:
                top_reasons.append(f"RSI {mr['rsi']:.0f} 과매도")
            elif mr["rsi"] >= 70:
                top_reasons.append(f"RSI {mr['rsi']:.0f} 과열")
            if pt["upside"] and pt["upside"] > 0.15:
                top_reasons.append(f"DCF +{pt['upside']:.0%}")
            if fail_safe_triggered:
                if earn["fail_safe_eps"]:
                    top_reasons.insert(0, "⛔EPS<0")
                if rs["fail_safe_rs"]:
                    top_reasons.insert(0, f"⛔RS{rs['rs_rating']}<40")
            top_reason_str = " · ".join(top_reasons[:4]) if top_reasons else "-"

            # ── 진입 타이밍 신호 (Entry Timing · V5.1_TUNED) ────────────
            # 인라인 로직은 _compute_entry_status() 순수 함수로 추출됨
            # (Stage 2 백테스트 호출 가능 · 동일 결정성 보장).
            _es = _compute_entry_status_dispatch(
                mr=mr, vwap=vwap, atr=atr, regime=regime, mom=mom, vol_a=vol_a,
                hist=hist, cur=cur, day_chg=day_chg,
                fail_safe_triggered=fail_safe_triggered,
                bear_cap_applied=bear_cap_applied,
            )
            entry_score = _es["score"]
            entry_status = _es["status"]
            status_label = _es["label"]
            _phrases = _es["phrases"]
            _score_breakdown = _es["breakdown"]

            # 한국어 한 줄 코멘트 — 우선순위 기반 핵심 1~2개 픽
            if _phrases:
                entry_phrase = f"{status_label} · " + " · ".join(_phrases[:2])
            else:
                entry_phrase = f"{status_label} · 신호 혼조"

            # ── 진입가 결정 (현재가 추격 금지 · V5.1) ───────────────────
            # 원칙: 현재가에서 풀백/지지를 받은 뒤 진입. 추격 매수는 R:R 무너짐.
            # 후보:
            #   - VWAP (기관 평균단가)
            #   - SMA20 (1차 풀백 지지)
            #   - cur - 0.5×ATR (가벼운 눌림)
            #   - swing_stop * 1.02 (손절 직상)
            # 등급별:
            #   STRONG  → min(cur, vwap, cur-0.3ATR) — 살짝 풀백
            #   NEUTRAL → min(vwap, sma20, cur-0.7ATR) — 더 깊은 풀백 대기
            #   AVOID   → min(sma20*0.99, cur*0.95) — 큰 조정 후만 고려
            try:
                _vwap_px = vwap.get("vwap", 0.0) or cur
                _atr_abs = atr.get("atr_value", 0.0) or (cur * atr.get("atr_percent", 2.0) / 100.0)
                _stop_px = atr.get("stop_loss_long", cur * 0.95)
                _sma20 = cur
                try:
                    if len(hist) >= 20:
                        _sma20 = float(hist["Close"].rolling(20).mean().iloc[-1])
                except Exception:
                    pass

                if entry_status == "STRONG":
                    _entry_raw = min(cur, _vwap_px if _vwap_px > 0 else cur, cur - 0.3 * _atr_abs)
                    _entry_type = "STRONG · 살짝 풀백"
                elif entry_status == "NEUTRAL":
                    _entry_raw = min(_vwap_px if _vwap_px > 0 else cur,
                                     _sma20,
                                     cur - 0.7 * _atr_abs)
                    _entry_type = "NEUTRAL · 풀백 대기"
                else:  # AVOID
                    _entry_raw = min(_sma20 * 0.99, cur * 0.95)
                    _entry_type = "AVOID · 깊은 조정 대기"

                # 손절 직상 보호 (≥ stop*1.015), 상단 캡 (≤ 현재가)
                _entry_raw = max(_entry_raw, _stop_px * 1.015)
                _entry_raw = min(_entry_raw, cur)
                entry_price = round(_entry_raw, 2)
                entry_discount = (cur - entry_price) / cur if cur > 0 else 0.0

                # R:R 재계산 (entry 기준)
                _t1 = atr.get("take_profit_1", cur)
                _t2 = atr.get("take_profit_2", cur)
                _risk = entry_price - _stop_px
                _reward = _t1 - entry_price
                _rr_adj = (_reward / _risk) if _risk > 0 else 0.0
            except Exception:
                entry_price = round(cur, 2)
                _entry_type = "현재가 (계산실패)"
                entry_discount = 0.0
                _rr_adj = atr.get("rr_ratio", 0.0)

            # ── 파생 표시 필드 (프론트 분기 제거 · 브레인스토밍 #2/#4) ────
            # headline_action : 카드 최상단 1-단어 결정 (점수보다 즉시 행동가능)
            # confidence_band : 승률+R:R 괴리를 1개 밴드로 추상화
            #                   (Phase1 app.js 임계와 동일하게 유지 → 서버/클라 일치)
            # one_reason      : 결론 근거 1줄 (문구·칩·유형 중복 통합)
            try:
                _wr = atr.get("win_rate", 0.0) or 0.0
                _rr_v = round(_rr_adj, 2)
                _rr_now_v = atr.get("rr_ratio", 0.0) or 0.0
                _disc = entry_discount  # 0~1
                _ph_joined = " ".join(_phrases)
                if entry_status == "STRONG":
                    headline_action = "지금 매수 가능" if _disc < 0.015 else "풀백 오면 매수"
                elif entry_status == "NEUTRAL":
                    if any(k in _ph_joined for k in ("과열", "RSI", "과매수")):
                        headline_action = "눌림 기다린 후 진입"
                    elif any(k in _ph_joined for k in ("추세", "이평", "골든", "데드")):
                        headline_action = "추세 확인 후 진입"
                    elif any(k in _ph_joined for k in ("거래량", "볼륨", "OBV")):
                        headline_action = "거래량 터질 때 진입"
                    elif any(k in _ph_joined for k in ("변동", "ATR", "VIX")):
                        headline_action = "변동성 잡히면 진입"
                    elif any(k in _ph_joined for k in ("VWAP", "지지", "지지선")):
                        headline_action = "지지 확인 후 진입"
                    else:
                        headline_action = "신호 혼조 — 다음 기회 대기"
                else:  # AVOID
                    if any(k in _ph_joined for k in ("하락", "데드크로스", "약세")):
                        headline_action = "하락 추세 — 지금은 아님"
                    elif any(k in _ph_joined for k in ("변동", "VIX", "ATR")):
                        headline_action = "변동성 과다 — 지금은 아님"
                    elif any(k in _ph_joined for k in ("공매도", "숏")):
                        headline_action = "공매도 압력 — 관망"
                    else:
                        headline_action = "지금 말고 다음 기회 노려라"

                _low_wr = _wr > 0 and _wr < 40
                _low_rr = (_rr_now_v > 0 and _rr_now_v < 1.5) or (_rr_v > 0 and _rr_v < 1.5)
                _hi_wr = _wr >= 55
                _hi_rr = _rr_v >= 2.5 and (_rr_now_v <= 0 or _rr_now_v >= 2.0)
                if _wr <= 0 and _rr_v <= 0:
                    confidence_band = ""
                elif _low_wr or _low_rr:
                    confidence_band = "낮음"
                elif _hi_wr and _hi_rr:
                    confidence_band = "높음"
                else:
                    confidence_band = "보통"

                if _phrases:
                    one_reason = " · ".join(_phrases[:2])
                else:
                    one_reason = "신호 혼조 — 뚜렷한 우위 없음"
            except Exception:
                headline_action = status_label
                confidence_band = ""
                one_reason = ""

            entry_plan = {
                "entry": entry_price,            # ← 풀백 지정가
                "entry_type": _entry_type,
                "entry_discount": round(entry_discount * 100, 2),  # 현재가 대비 %
                "current": round(cur, 2),
                "stop":  round(atr.get("stop_loss_long", cur), 2),
                "t1":    round(atr.get("take_profit_1",  cur), 2),
                "t2":    round(atr.get("take_profit_2",  cur), 2),
                "rr":    round(_rr_adj, 2),       # 풀백 진입 기준 R:R
                "rr_now": atr.get("rr_ratio", 0.0),  # 현재가 기준 (참고)
                "stop_method": atr.get("stop_method", "ATR"),
                "win_rate": atr.get("win_rate", 0.0),
                "score_breakdown": _score_breakdown,
                # 파생 표시 필드 (프론트는 그대로 표시만)
                "headline_action": headline_action,
                "confidence_band": confidence_band,
                "one_reason": one_reason,
            }

            result = {
                "Ticker":           ticker,
                "Name":             name,
                "Price":            cur,
                "DayChg":           day_chg,
                "Mom12M":           mom["mom_12m"],
                "MomentumScore":    mom["momentum_score"],
                "ValueScore":       ff["value_score"],
                "QualityScore":     qual["quality_score"],
                "RSI":              mr["rsi"],
                "VWAPDistance":     vwap["distance"],
                "ATRPercent":       atr["atr_percent"],
                "Regime":           regime["regime"],
                "TotalScore":       final,
                "Scores":           all_scores,
                "Signal":           signal,
                "Breakdown":        breakdown,
                "Drawdown":         dd["current_dd"],
                # CAN SLIM 메타 (통계용)
                "RSRating":         rs["rs_rating"],
                "RS_WeightedRet":   rs["rs"],          # 백분위 재계산용 raw
                "RS_OldScore":      rs["score"],       # 절대 RS score (delta용)
                "IsLeader":         rs["is_leader"],
                "EPSAcceleration":  earn["eps_acceleration"],
                "NearHighPass":     mom["near_52w_high"],
                "SConfirmed":       vol_a["s_confirmed"],
                "SuperMult":        super_mult,
                "FailSafe":         fail_safe_triggered,
                "_fail_eps":        earn["fail_safe_eps"],
                "_momentum_override": _momentum_override,
                "BearCap":          bear_cap_applied,
                "Conviction":       conviction,
                "LowLiquidity":     low_liquidity,
                "TargetPrice":      pt["target"],
                "TargetUpside":     pt["upside"],
                "TargetView":       pt["view"],
                "TargetMethod":     pt.get("target_method", "DCF"),
                "TargetSource":     target_source,
                "BrokerTarget":     broker_target,
                "BrokerTargetSource": broker_target_source,
                "BrokerAnalystCount": broker_target_count,
                "DcfLow":           pt.get("dcf_low", 0.0),
                "DcfHigh":          pt.get("dcf_high", 0.0),
                "PerFair":          pt.get("per_fair", 0.0),
                "PbrFair":          pt.get("pbr_fair", 0.0),
                "EvEbitdaFair":     pt.get("ev_ebitda", 0.0),
                "NomuraTarget":     pt.get("nomura_target", 0.0),
                "NomuraMethod":     pt.get("nomura_method", ""),
                "NomuraUpside":     pt.get("nomura_upside", 0.0),
                "NomuraBias":       pt.get("nomura_bias", 1.0),
                "NomuraUsed":       bool(pt.get("nomura_target", 0) > 0 and float(pt.get("target", 0)) == float(pt.get("nomura_target", 0))),
                # 진입 타이밍 신호 (단기 매수 적정성)
                "EntryScore":       entry_score,
                "EntryStatus":      entry_status,
                "EntryPhrase":      entry_phrase,
                "EntryPlan":        entry_plan,
                # 단타 팩터
                "ORBSignal":        orb["signal"],
                "ORBScore":         orb["score"],
                "ORBBreakoutPct":   orb.get("breakout_pct", 0),
                "ORBVolRatio":      orb.get("vol_ratio", 0),
                "NR7Signal":        nr7["signal"],
                "NR7Score":         nr7["score"],
                "NR7Compression":   nr7.get("compression_ratio", 0),
                "BBSignal":         bb_rv["signal"],
                "BBScore":          bb_rv["score"],
                "BBPosition":       bb_rv.get("bb_position", 0),
                "VolRatio":         vol_a.get("ratio", 0),
                "TopReason":        top_reason_str,
                # 종목 설명
                "Desc":             (self.KR_DESC if _is_kr else self.US_DESC).get(ticker, ""),
                "About":            (info.get("longBusinessSummary") or "")[:300],
                "CompanyInfo":      _KR_COMPANY_INFO.get(ticker, "") if _is_kr else _US_COMPANY_INFO.get(ticker, ""),
                "Industry":         info.get("industry", "") or info.get("sector", ""),
                "Sector":           _sector_for_nomura or (info.get("industry", "") or info.get("sector", "")),
                # NH 필터용 원시 재무 데이터
                "_EPSGrowth":       earn["eps_growth"],
                "_ROE":             ff["roe"],
                "_PBR":             safe_get(info.get("priceToBook"), 0),
                "_PER":             safe_get(info.get("trailingPE"), 0),
                "_ADX":             regime["adx"],
                "_OperatingMargin": safe_get(info.get("operatingMargins") or info.get("profitMargins"), 0),
                "_DebtRatio":       safe_get(info.get("debtToEquity"), 100),
                "_Mom1M":           mom["mom_1m"],
                "_Mom3M":           float((cur / hist["Close"].iloc[-63] - 1) * 100) if len(hist) >= 63 else 0,
                "_DayChgPct":       day_chg * 100,
                # yfinance 미제공 KR 종목은 현재가 × 발행주식수(네이버 분기 역산)로 폴백
                "_MarketCap":       safe_get(info.get("marketCap"), 0)
                                    or ((cur or 0) * (info.get("sharesOutstanding") or 0)),
                "_RevenueGrowth":   safe_get(info.get("revenueGrowth"), 0.0),
                "_MACDHist":        mr.get("macd_hist", 0),
                "_DivYield":        _normalize_div_yield(info.get("dividendYield")),
                "_AvgVol20":        float(hist["Volume"].tail(20).mean()) if len(hist) >= 20 else 0.0,
                "_AvgDollarVol20":  float((hist["Close"] * hist["Volume"]).tail(20).mean()) if len(hist) >= 20 else 0.0,
            }

            # 한국 종목: 네이버 증권 재무 데이터로 보강
            if _is_kr:
                nf = self._fetch_naver_fundamentals(ticker)
                if nf:
                    if 'per' in nf:
                        result['_PER'] = nf['per']
                    if 'pbr' in nf:
                        result['_PBR'] = nf['pbr']
                    if 'roe' in nf:
                        result['_ROE'] = nf['roe'] / 100   # % → 소수
                    if 'operating_margin' in nf:
                        result['_OperatingMargin'] = nf['operating_margin'] / 100
                    if 'debt_ratio' in nf:
                        result['_DebtRatio'] = nf['debt_ratio']
                    if 'div_yield_naver' in nf:
                        result['_DivYield'] = nf['div_yield_naver'] / 100.0   # % → decimal

            self.cache.set(strategy_key, result)
            return result

        except Exception as e:
            logging.error(f"[Ticker] {ticker}: {e}")
            traceback.print_exc()
            return None

    # ─────────────────────────────────────────────────────────────────────
    # NH 투자증권 조건검색 필터
    # ─────────────────────────────────────────────────────────────────────
    def _nh_filter(self, strategy: str, d: dict) -> bool:
        """NH투자증권 나무 HTS 조건검색식 기반 종목 필터.
        yfinance 한국 종목은 PBR/PER 등 데이터가 None인 경우가 많으므로
        데이터 미존재 시 해당 조건은 통과(skip) 처리한다.
        """
        try:
            eps      = d.get("_EPSGrowth", None)
            roe      = d.get("_ROE", None)
            pbr      = d.get("_PBR", None)
            per      = d.get("_PER", None)
            adx      = d.get("_ADX", 0) or 0
            op_mar   = d.get("_OperatingMargin", None)
            debt     = d.get("_DebtRatio", None)
            mom3m    = d.get("_Mom3M", 0) or 0
            day_chg  = d.get("_DayChgPct", 0) or 0
            rsi      = d.get("RSI", 50) or 50
            vol_r    = d.get("VolRatio", 0) or 0
            near_hi  = d.get("NearHighPass", False)
            mcap     = d.get("_MarketCap", 0) or 0
            macd_h   = d.get("_MACDHist", 0) or 0
            bb_pos   = d.get("BBPosition", 0) or 0

            def _chk(val, lo=None, hi=None):
                """값이 None/0이면 데이터 없음 → 통과. 있으면 범위 체크."""
                if val is None or val == 0:
                    return True
                if lo is not None and val < lo:
                    return False
                if hi is not None and val > hi:
                    return False
                return True

            if strategy == "CAN_SLIM":
                return all([
                    _chk(eps, lo=0.25),     # EPS 25%+
                    _chk(roe, lo=0.17),     # ROE 17%+
                    _chk(op_mar, lo=0.05),  # 영업이익률 5%+
                    near_hi,                # 52주 신고가 근접
                    vol_r >= 1.5,           # 거래량 150%+
                    adx >= 20,              # 추세 존재
                    30 <= rsi <= 75,
                    _chk(mcap, lo=1e11),    # 시총 1000억+
                ])
            elif strategy == "MOMENTUM":
                return all([
                    near_hi,                # 52주 신고가 근접
                    mom3m >= 15,            # 3개월 15%+
                    adx >= 25,              # 강한 추세
                    vol_r >= 1.5,
                    macd_h > 0,             # MACD 골든크로스
                    45 <= rsi <= 80,
                    _chk(mcap, lo=5e10),    # 시총 500억+
                    day_chg <= 15,
                ])
            elif strategy == "VALUE":
                return all([
                    _chk(pbr, lo=0.01, hi=1.5),  # 저PBR (데이터 있을 때만)
                    _chk(per, lo=0.01, hi=15),    # 저PER (데이터 있을 때만)
                    _chk(roe, lo=0.17),            # ROE 17%+
                    _chk(op_mar, lo=0.10),         # 영업이익률 10%+
                    _chk(debt, hi=80),             # 부채비율 80% 이하
                    25 <= rsi <= 55,
                    eps is None or eps >= 0,       # 적자 제외
                    _chk(mcap, lo=5e10),
                ])
            elif strategy == "BALANCED":
                return all([
                    _chk(op_mar, lo=0.10),  # 영업이익률 10%+
                    _chk(eps, lo=0.15),     # EPS 15%+
                    _chk(debt, hi=150),
                    adx >= 15,              # 최소 추세
                    40 <= rsi <= 70,
                    mom3m >= 0,             # 하락 제외
                    _chk(mcap, lo=1e11),
                ])
            elif strategy == "SCALPING":
                orb_pass = all([
                    d.get("ORBSignal", "NONE") != "NONE",
                    vol_r >= 2.0,
                    40 <= rsi <= 70,
                    1.5 <= day_chg <= 10,
                    _chk(mcap, lo=3e10),
                ])
                nr7_pass = all([
                    d.get("NR7Signal", "NONE") != "NONE",
                    vol_r >= 1.5,
                    0.5 <= day_chg <= 15,
                    _chk(mcap, lo=3e10),
                ])
                bb_pass = all([
                    bb_pos <= -0.8,         # BB 하단 근접
                    rsi <= 35,
                    vol_r >= 1.2,
                    day_chg >= -3,
                    _chk(mcap, lo=5e10),
                ])
                return orb_pass or nr7_pass or bb_pass
            return True
        except Exception:
            return False

    # ─────────────────────────────────────────────────────────────────────
    # UI 렌더링
    # ─────────────────────────────────────────────────────────────────────
    def _render_table(self):
        self._finalize_ui()
        self._refresh_top_picks()
        if not self.current_data:
            self.lbl_status.config(text="데이터 없음")
            return
        # SCALPING 모드 → 전용 테이블 렌더링
        if self.strategy_mode.get() == "SCALPING":
            self._render_scalping_table()
            return
        self.lbl_status.config(text=f"✅ {len(self.current_data)}개 종목 분석 완료")
        sorted_data = sorted(self.current_data, key=lambda x: x["TotalScore"], reverse=True)
        is_kr = self.market_mode.get() == "KR"
        strategy = self.strategy_mode.get()

        # NH 필터 적용
        total_before = len(sorted_data)
        if self.nh_filter_on.get():
            sorted_data = [d for d in sorted_data if self._nh_filter(strategy, d)]

        # CAN SLIM 집계
        breakout_cnt = sum(1 for d in sorted_data if "BREAKOUT" in d["Signal"] or "MOMENTUM LEADER" in d["Signal"])
        leader_cnt   = sum(1 for d in sorted_data if "LEADER" in d["Signal"])
        laggard_cnt  = sum(1 for d in sorted_data if "LAGGARD" in d["Signal"] or "AVOID" in d["Signal"])
        scan_time = datetime.now().strftime("%H:%M:%S")
        nh_info = f"  │ 🏦 NH {len(sorted_data)}/{total_before}" if self.nh_filter_on.get() else ""
        self.lbl_status.config(text=(
            f"✅ {len(sorted_data)}개 표시 │ "
            f"🔔Breakout: {breakout_cnt}  ⭐Leader: {leader_cnt}  📉Laggard: {laggard_cnt}"
            f"  │ 🕐 {scan_time}{nh_info}"
        ))

        for d in sorted_data:
            sig = d["Signal"]
            # CAN SLIM 시그널 기반 색상 태그
            if "CAN SLIM BREAKOUT" in sig or "MOMENTUM LEADER" in sig:
                tag = "canslim_s1"     # 최고등급 — 골드
            elif "STRONG LEADER" in sig or "⭐⭐⭐" in sig:
                tag = "canslim_s2"     # 딥그린
            elif "LEADER" in sig or "⭐⭐" in sig:
                tag = "canslim_s3"     # 딥네이비
            elif "WATCH" in sig or "Accumulate" in sig or "⭐" in sig:
                tag = "canslim_s4"     # 앰버
            elif "NEUTRAL" in sig or "Hold" in sig or "⏸" in sig:
                tag = "canslim_s5"     # 회색
            elif "CAUTION" in sig or "BEAR" in sig or "Fail-Safe" in sig:
                tag = "canslim_s6"     # 번트오렌지
            else:
                tag = "canslim_s7"     # 다크레드 (LAGGARD/SELL)

            price_str = f"₩{d['Price']:,.0f}" if is_kr else f"${d['Price']:.2f}"
            leader_mark = "⭐" if d.get("IsLeader") else ""

            # ── 수치 시각화 헬퍼 ──
            sc = d['TotalScore']
            if sc >= 90:    sc_viz = f"{sc:.0f} ★★★★★"
            elif sc >= 80:  sc_viz = f"{sc:.0f} ★★★★☆"
            elif sc >= 70:  sc_viz = f"{sc:.0f} ★★★☆☆"
            elif sc >= 55:  sc_viz = f"{sc:.0f} ★★☆☆☆"
            elif sc >= 40:  sc_viz = f"{sc:.0f} ★☆☆☆☆"
            else:           sc_viz = f"{sc:.0f} ☆☆☆☆☆"

            dc = d['DayChg']
            day_viz = f"▲ {dc:+.1%}" if dc > 0 else f"▼ {dc:+.1%}" if dc < 0 else f"  {dc:+.1%}"

            mm = d['Mom12M']
            mom_viz = f"▲ {mm:+.1%}" if mm > 0 else f"▼ {mm:+.1%}" if mm < 0 else f"  {mm:+.1%}"

            rsi = d['RSI']
            if rsi >= 70:    rsi_viz = f"{rsi:.0f} 🔥"
            elif rsi <= 30:  rsi_viz = f"{rsi:.0f} ❄"
            else:            rsi_viz = f"{rsi:.0f}"

            tp = d.get("TargetPrice", 0)
            tp_up = d.get("TargetUpside", 0)
            if tp and tp > 0:
                if is_kr:
                    target_str = f"₩{tp:,.0f} ({tp_up:+.0%})"
                else:
                    target_str = f"${tp:.2f} ({tp_up:+.0%})"
            else:
                target_str = "-"

            _desc_map = self.KR_DESC if is_kr else self.US_DESC
            _desc = _desc_map.get(d['Ticker'], "")
            vals = (
                d.get("Sector", "")[:18],
                d['Name'], _desc, price_str, target_str, sc_viz,
                d.get("Conviction", ""),
                d.get("SectorRank", ""),
                day_viz, mom_viz,
                f"{d.get('RSRating', 0)} {leader_mark}",
                f"{d['ValueScore']:.0f}",
                f"{d['QualityScore']:.0f}",
                rsi_viz,
                f"{d['VWAPDistance']:+.1%}",
                f"{d['ATRPercent']:.1f}%",
                d["Regime"],
                self._committee_str(d),
                d["Signal"],
                d.get("TopReason", "-"),
            )
            wl_mark = "★ " if self._in_watchlist(d['Ticker']) else "  "
            self.tree.insert("", "end",
                             text=f"{wl_mark}{d['Ticker']}",
                             values=vals,
                             tags=(tag, self._score_bg_tag(d['TotalScore'])))

        # 데이터 삽입 직후 — 컬럼 너비를 실제 콘텐츠 길이에 맞게 자동 조절
        self._autofit_columns()

        # CAN SLIM 전용 색상 설정 (전경색)
        self.tree.tag_configure("canslim_s1", foreground=C["CANSLIM_S1"], font=F["SMALL_BOLD"])
        self.tree.tag_configure("canslim_s2", foreground=C["CANSLIM_S2"], font=F["SMALL_BOLD"])
        self.tree.tag_configure("canslim_s3", foreground=C["CANSLIM_S3"])
        self.tree.tag_configure("canslim_s4", foreground=C["CANSLIM_S4"])
        self.tree.tag_configure("canslim_s5", foreground=C["CANSLIM_S5"])
        self.tree.tag_configure("canslim_s6", foreground=C["CANSLIM_S6"])
        self.tree.tag_configure("canslim_s7", foreground=C["CANSLIM_S7"])
        # 점수 구간별 배경색 태그 등록
        self._configure_score_tags(self.tree)

    def _render_scalping_table(self):
        """단타 스크리너 전용 테이블 렌더링."""
        try:
            self._render_scalping_table_impl()
        except Exception as e:
            logging.error(f"[SCA Render] {e}")
            self._log(f"⚠️ 단타 렌더링 오류: {e}")

    def _render_scalping_table_impl(self):
        self.sca_tree.delete(*self.sca_tree.get_children())
        is_kr = self.market_mode.get() == "KR"

        sorted_data = sorted(self.current_data, key=lambda x: x["TotalScore"], reverse=True)
        total_before = len(sorted_data)

        # NH 필터 적용
        if self.nh_filter_on.get():
            sorted_data = [d for d in sorted_data if self._nh_filter("SCALPING", d)]

        self._log(f"[SCA] {len(sorted_data)}개 종목 렌더링")

        # 단타 시그널 집계
        orb_cnt = sum(1 for d in sorted_data if d.get("ORBSignal", "NONE") != "NONE")
        nr7_cnt = sum(1 for d in sorted_data if d.get("NR7Signal", "NONE") != "NONE")
        bb_cnt  = sum(1 for d in sorted_data if d.get("BBSignal", "NONE") != "NONE")
        nh_info = f"  │ 🏦 NH {len(sorted_data)}/{total_before}" if self.nh_filter_on.get() else ""
        self.lbl_status.config(text=(
            f"🔫 단타 스크리너 │ {len(sorted_data)}개 표시 │ "
            f"ORB: {orb_cnt}  NR7: {nr7_cnt}  BB: {bb_cnt}{nh_info}"
        ))

        for d in sorted_data:
            sc = d["TotalScore"]
            orb_sig = d.get("ORBSignal", "NONE")
            nr7_sig = d.get("NR7Signal", "NONE")
            bb_sig  = d.get("BBSignal", "NONE")

            # 단타 시그널 기반 색상
            active_count = sum(1 for s in [orb_sig, nr7_sig, bb_sig] if s != "NONE")
            if active_count >= 2 and sc >= 70:
                tag = "sca_hot"
            elif active_count >= 1 and sc >= 60:
                tag = "sca_warm"
            elif sc >= 50:
                tag = "sca_neutral"
            else:
                tag = "sca_cold"

            price_str = f"₩{d['Price']:,.0f}" if is_kr else f"${d['Price']:.2f}"
            dc = d["DayChg"]
            day_viz = f"▲{dc:+.1%}" if dc > 0 else f"▼{dc:+.1%}" if dc < 0 else f"{dc:+.1%}"
            rsi = d["RSI"]
            rsi_viz = f"{rsi:.0f}🔥" if rsi >= 70 else f"{rsi:.0f}❄" if rsi <= 30 else f"{rsi:.0f}"

            vol_r = d.get("VolRatio", 0)
            vol_viz = f"{vol_r:.1f}x" if vol_r else "-"

            # ORB 시그널 표시
            orb_score = d.get("ORBScore", 0)
            if orb_sig == "ORB_BREAKOUT":
                orb_viz = f"🔥 돌파 ({orb_score})"
            elif orb_sig == "ORB_WEAK":
                orb_viz = f"⚡ 약돌파 ({orb_score})"
            else:
                orb_viz = "-"

            # NR7 시그널 표시
            nr7_score = d.get("NR7Score", 0)
            comp = d.get("NR7Compression", 0)
            if nr7_sig == "NR7_BREAKOUT":
                orb_nr7 = f"🔥 돌파 ({nr7_score})"
            elif nr7_sig == "NR7_READY":
                orb_nr7 = f"⏳ 압축 ({comp:.0%})"
            else:
                orb_nr7 = "-"

            # BB 시그널 표시
            bb_score = d.get("BBScore", 0)
            bb_pos = d.get("BBPosition", 0)
            if bb_sig == "BB_REVERT":
                bb_viz = f"🔄 반등 ({bb_score})"
            elif bb_sig == "BB_NEAR_LOW":
                bb_viz = f"📉 하단 ({bb_pos:.0%})"
            else:
                bb_viz = "-"

            # 종합 시그널 — 메인 트리와 동일한 스케일(점수<40 → 빈 별)
            if sc >= 90:    sc_viz = f"{sc:.0f} ★★★★★"
            elif sc >= 80:  sc_viz = f"{sc:.0f} ★★★★☆"
            elif sc >= 70:  sc_viz = f"{sc:.0f} ★★★☆☆"
            elif sc >= 55:  sc_viz = f"{sc:.0f} ★★☆☆☆"
            elif sc >= 40:  sc_viz = f"{sc:.0f} ★☆☆☆☆"
            else:           sc_viz = f"{sc:.0f} ☆☆☆☆☆"

            # 단타 전용 시그널 텍스트 — 점수 게이팅으로 "저품질 돌파" 오해 방지
            #   sc<40: 신호 무시 (저품질)
            #   sc<55: "(약)" 접두사로 다운그레이드 표시
            sigs = []
            if sc >= 40:
                if orb_sig == "ORB_BREAKOUT": sigs.append("ORB돌파")
                if nr7_sig == "NR7_BREAKOUT": sigs.append("NR7돌파")
                if bb_sig == "BB_REVERT": sigs.append("BB반등")
                if not sigs:
                    if orb_sig == "ORB_WEAK": sigs.append("ORB약")
                    if nr7_sig == "NR7_READY": sigs.append("NR7준비")
                    if bb_sig == "BB_NEAR_LOW": sigs.append("BB하단")
            if sigs and sc < 55:
                sig_text = "(약) " + " | ".join(sigs)
            elif sigs:
                sig_text = " | ".join(sigs)
            elif sc < 40:
                sig_text = "— 저품질 (관망)"
            else:
                sig_text = d["Signal"][:25]

            vals = (
                d["Name"], price_str, day_viz, vol_viz, rsi_viz,
                f"{d['ATRPercent']:.1f}%",
                orb_viz, orb_nr7, bb_viz, sc_viz, sig_text
            )
            self.sca_tree.insert("", "end", text=f"  {d['Ticker']}", values=vals, tags=(tag,))

        # 색상 설정
        self.sca_tree.tag_configure("sca_hot",     foreground="#FF3B30", font=F["SMALL_BOLD"])
        self.sca_tree.tag_configure("sca_warm",    foreground="#FF9500", font=F["SMALL_BOLD"])
        self.sca_tree.tag_configure("sca_neutral", foreground=C["TEXT_MAIN"])
        self.sca_tree.tag_configure("sca_cold",    foreground=C["TEXT_SUB"])

    def _sort_sca(self, col: str, reverse: bool):
        rows = [(self.sca_tree.set(k, col), k) for k in self.sca_tree.get_children("")]
        try:
            rows.sort(key=lambda t: self._parse_sort_val(t[0]), reverse=reverse)
        except (ValueError, TypeError):
            rows.sort(reverse=reverse)
        for idx, (_, k) in enumerate(rows):
            self.sca_tree.move(k, "", idx)
        self.sca_tree.heading(col, command=lambda: self._sort_sca(col, not reverse))

    def _on_double_click_sca(self, _event):
        sel = self.sca_tree.selection()
        if not sel:
            return
        item = self.sca_tree.item(sel[0])
        ticker = item["text"].strip()
        for d in self.current_data:
            if d["Ticker"] == ticker:
                self._show_detail_data(d)
                return

    def _show_detail_data(self, d):
        """단타 스크리너 상세 팝업 — 스캘핑 시그널 (US-002)."""
        display_name = self._resolve_display_name(d.get("Ticker", ""), d.get("Name", ""))
        if display_name and d.get("Name") != display_name:
            d["Name"] = display_name
        win = tk.Toplevel(self.root)
        win.title(f"{d['Name']} ({d['Ticker']})")
        win.geometry("640x560")
        win.configure(bg=C["PANEL"])

        nb = ttk.Notebook(win)
        nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        tab_scalp = tk.Frame(nb, bg=C["PANEL"])
        nb.add(tab_scalp, text="⚡  스캘핑 시그널")

        # 스캘핑 텍스트 (기존 회귀 없음)
        txt = tk.Text(tab_scalp, bg=C["SIDEBAR"], fg=C["TEXT_MAIN"], font=F["BODY"],
                      wrap=tk.WORD, padx=12, pady=10)
        txt.pack(fill=tk.BOTH, expand=True)
        is_kr = self.market_mode.get() == "KR"
        price_str = f"₩{d['Price']:,.0f}" if is_kr else f"${d['Price']:.2f}"
        lines = [
            f"{'─'*40}",
            f"  {d['Name']}  ({d['Ticker']})",
            f"  현재가: {price_str}   등락: {d['DayChg']:+.1%}",
            f"  총점: {d['TotalScore']:.1f}   RSI: {d['RSI']:.0f}   ATR: {d['ATRPercent']:.1f}%",
            f"{'─'*40}",
            f"",
            f"  [ORB 전일고가 돌파]",
            f"    시그널: {d.get('ORBSignal','NONE')}  점수: {d.get('ORBScore',0)}",
            f"    돌파율: {d.get('ORBBreakoutPct',0):.2%}  거래량비: {d.get('ORBVolRatio',0):.1f}x",
            f"",
            f"  [NR7 변동폭 압축]",
            f"    시그널: {d.get('NR7Signal','NONE')}  점수: {d.get('NR7Score',0)}",
            f"    압축비: {d.get('NR7Compression',0):.2%}",
            f"",
            f"  [BB 볼린저 반등]",
            f"    시그널: {d.get('BBSignal','NONE')}  점수: {d.get('BBScore',0)}",
            f"    BB위치: {d.get('BBPosition',0):.0%}",
            f"{'─'*40}",
        ]
        txt.insert("1.0", "\n".join(lines))
        txt.config(state="disabled")

    def _refresh_top_picks(self):
        """스캔 결과에서 조건 충족 상위 종목을 카드 띠로 표시한다."""
        bar = self._top_picks_bar
        for w in bar.winfo_children():
            w.destroy()

        data = getattr(self, "current_data", [])
        if not data:
            bar.pack_forget()
            return

        # 필터: Signal∈{STRONG_BUY,BUY} + Score≥60 + RSRating≥70 + Regime≠BEAR
        _BEAR = {"STRONG_BEAR", "BEAR"}
        candidates = [
            r for r in data
            if r.get("Signal", "") in ("STRONG_BUY", "BUY")
            and r.get("TotalScore", 0) >= 60
            and r.get("RSRating", 0) >= 70
            and r.get("Regime", "") not in _BEAR
            and not r.get("FailSafe", False)
        ]
        # Score 내림차순 상위 5개
        picks = sorted(candidates, key=lambda x: x.get("TotalScore", 0), reverse=True)[:5]

        if not picks:
            bar.pack_forget()
            return

        # 헤더
        hdr = tk.Frame(bar, bg=C["SHADOW_DEEP"])
        hdr.pack(side=tk.LEFT, fill=tk.Y, padx=(6, 0))
        tk.Label(hdr, text="🏆 Top Pick\n(참고용)",
                 font=F["TINY"], bg=C["SHADOW_DEEP"],
                 fg="#f59e0b", justify="center", pady=4).pack(expand=True)

        # 카드
        _SIG_COLOR = {"STRONG_BUY": "#4ade80", "BUY": "#86efac"}
        for r in picks:
            ticker  = r.get("Ticker", r.get("Name", "?"))
            score   = int(r.get("TotalScore", 0))
            signal  = r.get("Signal", "")
            reason  = r.get("Reason", "")
            if len(reason) > 22:
                reason = reason[:21] + "…"
            sig_col = _SIG_COLOR.get(signal, "#94a3b8")
            bg_card = "#1a2a1a" if signal == "STRONG_BUY" else "#1a2430"

            card = tk.Frame(bar, bg=bg_card, relief="flat", bd=0,
                            cursor="hand2")
            card.pack(side=tk.LEFT, fill=tk.Y, padx=2, pady=3)
            tk.Label(card, text=f"  {ticker}  ",
                     font=F["SMALL_BOLD"], bg=bg_card,
                     fg="#e2e8f0", pady=2).pack()
            tk.Label(card, text=f"{score}pt",
                     font=F["BODY_BOLD"], bg=bg_card,
                     fg="#f59e0b", pady=0).pack()
            tk.Label(card, text=signal.replace("_", " "),
                     font=F["TINY"], bg=bg_card,
                     fg=sig_col).pack()
            tk.Label(card, text=reason,
                     font=F["TINY"], bg=bg_card,
                     fg="#94a3b8", padx=6, pady=2).pack()
            card.bind("<Button-1>",
                      lambda e, d=r: self._show_detail_data(d))
            for child in card.winfo_children():
                child.bind("<Button-1>",
                           lambda e, d=r: self._show_detail_data(d))

        # 면책 문구
        tk.Label(bar, text="※ 투자 판단은 본인 책임",
                 font=F["TINY"], bg=C["SHADOW_DEEP"],
                 fg="#475569", padx=6).pack(side=tk.RIGHT, padx=4)

        bar.pack(fill=tk.X, before=self.tree_container)

    def _finalize_ui(self):
        self.btn_scan.config(state="normal", text="▶  SCAN  (F5)")
        self.btn_scan_all.config(state="normal", text="🔍  SCAN ALL  (F6)")
        self.btn_stop.config(state="disabled")
        self.btn_export.config(state="normal", bg=C["ACCENT"], fg=C["HIGHLIGHT"])
        self.btn_stats.config(state="normal",  bg=C["ACCENT"], fg=C["HIGHLIGHT"])
        self.progress_var.set(100)
        self.scan_all_mode = False
        self._scan_cancelled = False

    # ─────────────────────────────────────────────────────────────────────
    # 컬럼 너비 자동 피팅
    # ─────────────────────────────────────────────────────────────────────
    def _autofit_columns(self):
        """컬럼 너비를 헤더·셀 텍스트 최댓값으로 자동 조절하고 결과를 저장한다."""
        from tkinter import font as tkfont

        PAD       = 10
        HEAD_FONT = tkfont.Font(font=F["TREE_HEAD"])
        CELL_FONT = tkfont.Font(font=F["TREE"])
        _COL_LABEL = {"Desc": "설명", "Name": "종목명", "Sector": "섹터"}

        fitted = {}

        # ── "#0" (TICKER) 컬럼 ────────────────────────────────────────
        w0 = HEAD_FONT.measure("TICKER") + PAD * 2
        for iid in self.tree.get_children(""):
            w = CELL_FONT.measure(self.tree.item(iid, "text")) + PAD * 2
            if w > w0:
                w0 = w
        fitted["#0"] = w0
        self.tree.column("#0", width=w0, minwidth=w0)

        # ── 나머지 컬럼 ──────────────────────────────────────────────
        for col in self.tree["columns"]:
            head_label = _COL_LABEL.get(col, col.upper())
            best = HEAD_FONT.measure(head_label) + PAD * 2
            for iid in self.tree.get_children(""):
                w = CELL_FONT.measure(self.tree.set(iid, col)) + PAD * 2
                if w > best:
                    best = w
            fitted[col] = best
            self.tree.column(col, width=best, minwidth=best)

        self._fitted_widths = fitted

    def _render_progressive(self, snapshot: list):
        """스캔 중간 결과를 테이블에 미리 표시 (Progressive rendering)."""
        if self.strategy_mode.get() == "SCALPING":
            return  # SCA 탭은 최종 결과만
        self.tree.delete(*self.tree.get_children())
        # 첫 스캔에서도 색상 적용되도록 tag_configure를 여기서도 호출
        self.tree.tag_configure("canslim_s1", foreground=C["CANSLIM_S1"], font=F["SMALL_BOLD"])
        self.tree.tag_configure("canslim_s2", foreground=C["CANSLIM_S2"], font=F["SMALL_BOLD"])
        self.tree.tag_configure("canslim_s3", foreground=C["CANSLIM_S3"])
        self.tree.tag_configure("canslim_s4", foreground=C["CANSLIM_S4"])
        self.tree.tag_configure("canslim_s5", foreground=C["CANSLIM_S5"])
        self.tree.tag_configure("canslim_s6", foreground=C["CANSLIM_S6"])
        self.tree.tag_configure("canslim_s7", foreground=C["CANSLIM_S7"])
        self._configure_score_tags(self.tree)
        is_kr = self.market_mode.get() == "KR"
        sorted_data = sorted(snapshot, key=lambda x: x["TotalScore"], reverse=True)
        self.lbl_status.config(text=f"🔄 스캔 중... {len(snapshot)}개 종목 (중간 결과)")
        for d in sorted_data[:30]:  # 상위 30개만 미리보기
            sig = d.get("Signal", "")
            if "CAN SLIM BREAKOUT" in sig or "MOMENTUM LEADER" in sig:
                tag = "canslim_s1"
            elif "STRONG LEADER" in sig or "⭐⭐⭐" in sig:
                tag = "canslim_s2"
            elif "LEADER" in sig or "⭐⭐" in sig:
                tag = "canslim_s3"
            elif "WATCH" in sig or "Accumulate" in sig or "⭐" in sig:
                tag = "canslim_s4"
            elif "NEUTRAL" in sig or "Hold" in sig or "⏸" in sig:
                tag = "canslim_s5"
            elif "CAUTION" in sig or "BEAR" in sig or "Fail-Safe" in sig:
                tag = "canslim_s6"
            else:
                tag = "canslim_s7"
            price_str = f"₩{d['Price']:,.0f}" if is_kr else f"${d['Price']:.2f}"
            sc = d['TotalScore']
            _desc_map = self.KR_DESC if is_kr else self.US_DESC
            _desc = _desc_map.get(d['Ticker'], "")
            # 최종 렌더와 컬럼 수(20) 일치
            vals = (
                d.get("Sector", "")[:18],
                d["Name"], _desc, price_str, "-",
                f"{sc:.0f}", "", "", "", "", "", "", "", "", "", "", "",
                "",
                d.get("Signal", ""),
                d.get("TopReason", "-"),
            )
            wl_mark = "★ " if self._in_watchlist(d['Ticker']) else "  "
            self.tree.insert("", "end",
                             text=f"{wl_mark}{d['Ticker']}",
                             values=vals,
                             tags=(tag, self._score_bg_tag(sc)))
        self._autofit_columns()

    def _show_summary(self, failed: list):
        ok = len(self.current_data)
        msg = f"✅ 완료: {ok}개 분석"
        if failed:
            msg += f"  ⚠️ 실패: {len(failed)}개 ({', '.join(failed[:5])}{'…' if len(failed)>5 else ''})"
        self._log(msg)
        self.lbl_progress.config(text="")

    # ─────────────────────────────────────────────────────────────────────
    # 로그
    # ─────────────────────────────────────────────────────────────────────
    def _log(self, msg: str):
        self.root.after(0, self._log_impl, msg)

    def _log_impl(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)
        logging.info(msg)

    # ─────────────────────────────────────────────────────────────────────
    # 정렬
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def _parse_sort_val(text: str):
        """셀 텍스트에서 숫자만 추출하여 정렬용 float로 변환."""
        # 첫 번째 숫자(부호, 소수점 포함)를 추출
        m = re.search(r'[+-]?\d[\d,]*\.?\d*', text.replace(",", ""))
        if m:
            return float(m.group())
        return float('-inf')

    def _sort(self, col: str, reverse: bool):
        rows = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        if not rows:
            return
        # 절반 이상 셀에서 숫자 추출 가능하면 숫자 정렬, 아니면 텍스트 정렬
        numeric_count = sum(1 for v, _ in rows
                            if self._parse_sort_val(v) != float('-inf'))
        if numeric_count >= len(rows) * 0.5:
            rows.sort(key=lambda t: self._parse_sort_val(t[0]), reverse=reverse)
        else:
            rows.sort(key=lambda t: t[0].lower(), reverse=reverse)
        for idx, (_, k) in enumerate(rows):
            self.tree.move(k, "", idx)
        self.tree.heading(col, command=lambda: self._sort(col, not reverse))

    # ─────────────────────────────────────────────────────────────────────
    # 더블클릭 상세 팝업
    # ─────────────────────────────────────────────────────────────────────
    def _on_double_click(self, _event):
        sel = self.tree.selection()
        if sel:
            self._show_detail(sel[0])

    def _show_detail(self, item_id):
        item = self.tree.item(item_id)
        # text 포맷: "  TICKER badge" → 첫 토큰만 추출 (US-005 뱃지 호환)
        ticker = self._extract_ticker(item["text"])
        data   = next((d for d in self.current_data if d["Ticker"] == ticker), None)
        if not data:
            return
        display_name = self._resolve_display_name(ticker, data.get("Name", ""))
        if display_name and data.get("Name") != display_name:
            data["Name"] = display_name

        pop = tk.Toplevel(self.root)
        pop.title(f"📊 {ticker} — CAN SLIM 상세 분석")
        pop.geometry("780x900")
        pop.configure(bg=C["PANEL"])
        pop.resizable(True, True)
        try:
            pop.title(f"{display_name} ({ticker})")
        except Exception:
            pass

        # ── 헤더 ──────────────────────────────────────────────────────
        hdr = tk.Frame(pop, bg=C["HEADER_BG"], relief="flat", bd=0)
        hdr.pack(fill=tk.X)

        # 점수에 따른 헤더 색상
        score = data["TotalScore"]
        if score >= 90:     hdr_fg = C["CANSLIM_S1"]
        elif score >= 80:   hdr_fg = C["CANSLIM_S2"]
        elif score >= 70:   hdr_fg = C["CANSLIM_S3"]
        elif score >= 55:   hdr_fg = C["GOLD"]
        else:               hdr_fg = C["TEXT_MAIN"]

        title_row = tk.Frame(hdr, bg=C["HEADER_BG"])
        title_row.pack(fill=tk.X)
        tk.Label(title_row, text=f"  {display_name}  |  {ticker}",
                 font=F["POPUP_TITLE"], bg=C["HEADER_BG"], fg=C["ACCENT"],
                 pady=8, anchor="w").pack(side=tk.LEFT)

        # US-005: 어닝 D-day 칩
        if _event_calendar is not None:
            try:
                dday, _iso = _event_calendar.earnings_dday(ticker)
                chip = _event_calendar.build_dday_chip(dday)
                if chip["show"]:
                    tk.Label(title_row, text=f"  {chip['text']}  ",
                             font=F.get("POPUP_SUB", F["HEADER"]),
                             bg=chip["bg"], fg=chip["fg"], padx=8, pady=4
                             ).pack(side=tk.LEFT, padx=8)
            except Exception as _e:
                logging.debug("dday chip skipped: %s", _e)

        # 점수 바
        score_row = tk.Frame(hdr, bg=C["HEADER_BG"])
        score_row.pack(fill=tk.X, padx=10, pady=(0, 4))

        tk.Label(score_row, text=f"CAN SLIM Score: {score:.1f} / 100",
                 font=F["POPUP_SUB"], bg=C["HEADER_BG"], fg=hdr_fg
                 ).pack(side=tk.LEFT)

        # CAN SLIM 메타 배지들
        badges = []
        if data.get("IsLeader"):           badges.append("⭐ RS LEADER")
        if data.get("EPSAcceleration"):    badges.append("🔥 EPS ACCEL")
        if data.get("NearHighPass"):       badges.append("🔔 52W HIGH")
        if data.get("SConfirmed"):         badges.append("📊 VOL CONF")
        if data.get("SuperMult", 1) > 1.1: badges.append(f"×{data.get('SuperMult',1):.2f} MULT")
        if data.get("FailSafe"):           badges.append("⛔ FAIL-SAFE")
        if data.get("BearCap"):            badges.append("🚫 BEAR CAP")
        if badges:
            tk.Label(score_row, text="  " + "  ".join(badges),
                     font=F["POPUP_SCORE"], bg=C["HEADER_BG"], fg=C["PURPLE"]
                     ).pack(side=tk.RIGHT)

        tk.Label(hdr, text=f"  Signal: {data['Signal']}",
                 font=F["HEADER"], bg=C["HEADER_BG"], fg=hdr_fg, pady=5,
                 anchor="w").pack(fill=tk.X)

        # CAN SLIM 핵심 지표 요약 바
        kpi_row = tk.Frame(hdr, bg=C["SHADOW_DEEP"])
        kpi_row.pack(fill=tk.X)
        kpis = [
            ("RS Rating", data.get("RSRating", 0)),
            ("Regime",    data.get("Regime", "—")),
            ("RSI",       f"{data.get('RSI', 0):.0f}"),
            ("12M Mom",   f"{data.get('Mom12M', 0):+.1%}"),
            ("Drawdown",  f"{data.get('Drawdown', 0):.1%}"),
        ]
        _KPI_GREEN  = "#1a4a2e"
        _KPI_ORANGE = "#4a3010"
        _KPI_RED    = "#4a1a1a"
        _KPI_GRAY   = "#2a2a3a"
        def _kpi_colors(label, val):
            """(bg, fg) 쌍 반환"""
            if label == "RS Rating":
                v = int(val) if isinstance(val, (int, float)) else 0
                if v >= 80:   return _KPI_GREEN,  "#4ade80"
                if v >= 60:   return _KPI_ORANGE, "#fb923c"
                return _KPI_RED, "#f87171"
            if label == "Regime":
                s = str(val).upper()
                if "BULL" in s:  return _KPI_GREEN,  "#4ade80"
                if "BEAR" in s:  return _KPI_RED,    "#f87171"
                return _KPI_GRAY, "#94a3b8"
            if label == "RSI":
                try: v = float(str(val))
                except ValueError: v = 50
                if v >= 70:  return _KPI_RED,    "#f87171"
                if v <= 30:  return _KPI_GREEN,  "#4ade80"
                return _KPI_GRAY, "#94a3b8"
            if label == "12M Mom":
                s = str(val)
                if s.startswith("+"):  return _KPI_GREEN,  "#4ade80"
                if s.startswith("-"):  return _KPI_RED,    "#f87171"
                return _KPI_GRAY, "#94a3b8"
            if label == "Drawdown":
                try: v = float(str(val).replace("%", "").replace("+", "").replace(",", "")) / 100
                except ValueError: v = 0
                if v >= -0.05:  return _KPI_GREEN,  "#4ade80"
                if v >= -0.15:  return _KPI_ORANGE, "#fb923c"
                return _KPI_RED, "#f87171"
            return C["ACCENT"], C["HIGHLIGHT"]
        for label, val in kpis:
            bg, fg = _kpi_colors(label, val)
            cell = tk.Frame(kpi_row, bg=bg, relief="flat", bd=0)
            cell.pack(side=tk.LEFT, fill=tk.Y, padx=1, pady=1)
            tk.Label(cell, text=label, font=F["TINY"],
                     bg=bg, fg="#94a3b8", padx=8).pack()
            tk.Label(cell, text=str(val), font=F["SMALL_BOLD"],
                     bg=bg, fg=fg, padx=8, pady=3).pack()

        # 노무라식 목표가 + 진입 타이밍 카드
        nomura_card = tk.Frame(win, bg=C["PANEL"])
        nomura_card.pack(fill=tk.X, padx=8, pady=(8, 0))
        nomura_inner = tk.Frame(nomura_card, bg=C["CARD"], relief="flat", bd=0)
        nomura_inner.pack(fill=tk.X)
        tk.Label(nomura_inner, text="노무라식 목표가 / 진입 타이밍",
                 font=F["POPUP_SUB"], bg=C["CARD"], fg=C["ACCENT"],
                 anchor="w", padx=12, pady=8).pack(fill=tk.X)

        nomura_target = d.get("NomuraTarget") or d.get("TargetPrice") or 0
        nomura_method = d.get("NomuraMethod") or d.get("TargetMethod") or "DCF"
        nomura_source  = d.get("TargetSource") or ""
        nomura_bias    = d.get("NomuraBias", 1.0)
        nomura_used    = bool(d.get("NomuraUsed", False))
        nomura_routed  = "메인 목표가 반영" if nomura_used else "참고값"
        nomura_upside  = d.get("NomuraUpside", 0.0)
        if not nomura_upside and nomura_target and d.get("Price"):
            nomura_upside = (float(nomura_target) - float(d["Price"])) / float(d["Price"])

        entry_plan = d.get("EntryPlan") or {}
        entry_score = d.get("EntryScore", 0)
        entry_phrase = d.get("EntryPhrase") or "-"
        entry_status = d.get("EntryStatus") or "-"
        entry_entry = entry_plan.get("entry")
        entry_stop  = entry_plan.get("stop")
        entry_t1    = entry_plan.get("t1")
        entry_t2    = entry_plan.get("t2")
        entry_rr    = entry_plan.get("rr")

        lines = tk.Frame(nomura_inner, bg=C["CARD"])
        lines.pack(fill=tk.X, padx=12, pady=(0, 10))
        row1 = tk.Frame(lines, bg=C["CARD"])
        row1.pack(fill=tk.X)
        tk.Label(row1, text=f"노무라식 목표가: {nomura_target:,.2f}" if nomura_target else "노무라식 목표가: —",
                 font=F["BODY_BOLD"], bg=C["CARD"], fg=C["TEXT_MAIN"]).pack(side=tk.LEFT)
        tk.Label(row1, text=(f"{nomura_upside:+.1%}" if isinstance(nomura_upside, (int, float)) else ""),
                 font=F["BODY_BOLD"], bg=C["CARD"],
                 fg=C["GREEN"] if (nomura_upside or 0) >= 0 else C["RED"]).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(row1, text=f"{nomura_method} · {nomura_routed}",
                 font=F["TINY"], bg=C["CARD"], fg=C["TEXT_SUB"]).pack(side=tk.RIGHT)

        row2 = tk.Frame(lines, bg=C["CARD"])
        row2.pack(fill=tk.X, pady=(4, 0))
        tk.Label(row2, text=f"출처: {nomura_source or '—'}",
                 font=F["TINY"], bg=C["CARD"], fg=C["TEXT_SUB"],
                 anchor="w").pack(side=tk.LEFT)
        tk.Label(row2, text=f"bias {nomura_bias:.2f}" if nomura_bias else "",
                 font=F["TINY"], bg=C["CARD"], fg=C["TEXT_SUB"]).pack(side=tk.RIGHT)

        row3 = tk.Frame(lines, bg=C["CARD"])
        row3.pack(fill=tk.X, pady=(8, 0))
        tk.Label(row3, text=f"진입 타이밍: {entry_phrase}",
                 font=F["BODY_BOLD"], bg=C["CARD"], fg=C["TEXT_MAIN"],
                 anchor="w").pack(side=tk.LEFT)
        tk.Label(row3, text=f"{entry_status} / {entry_score:.0f}점" if isinstance(entry_score, (int, float)) else f"{entry_status}",
                 font=F["TINY"], bg=C["CARD"], fg=C["ACCENT"]).pack(side=tk.RIGHT)

        row4 = tk.Frame(lines, bg=C["CARD"])
        row4.pack(fill=tk.X, pady=(4, 0))
        entry_bits = []
        if entry_entry is not None: entry_bits.append(f"진입가 {entry_entry:,.2f}")
        if entry_stop is not None:  entry_bits.append(f"손절 {entry_stop:,.2f}")
        if entry_t1 is not None:    entry_bits.append(f"1차 {entry_t1:,.2f}")
        if entry_t2 is not None:    entry_bits.append(f"2차 {entry_t2:,.2f}")
        if entry_rr is not None:    entry_bits.append(f"R:R {entry_rr:.2f}:1")
        tk.Label(row4, text=" · ".join(entry_bits) if entry_bits else "진입 계획: —",
                 font=F["TINY"], bg=C["CARD"], fg=C["TEXT_SUB"],
                 anchor="w", justify="left").pack(fill=tk.X)

        # ── 노트북 탭 (CAN SLIM / 4축 핸드드로잉) ─────────────────────
        nb = ttk.Notebook(pop)
        nb.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        tab_canslim = tk.Frame(nb, bg=C["PANEL"])
        tab_4axis   = tk.Frame(nb, bg=C["PANEL"])
        nb.add(tab_canslim, text="📋  CAN SLIM Breakdown")
        nb.add(tab_4axis,   text="🎨  4축 핸드드로잉 분석")

        # 4축 탭은 클릭 시 lazy-load (yfinance 호출 비용 절감)
        def _load_4axis(event=None):
            if getattr(tab_4axis, "_loaded", False): return
            if nb.index(nb.select()) != 1: return
            tab_4axis._loaded = True
            for w in tab_4axis.winfo_children(): w.destroy()
            loading = tk.Label(tab_4axis, text="  📡  데이터 로딩 중…",
                               bg=C["PANEL"], fg=C["TEXT_MAIN"],
                               font=F["BODY"], pady=20)
            loading.pack()
            tab_4axis.update_idletasks()
            try:
                import yfinance as yf
                from analysis_card import build_four_axis_card
                # KR 6자리 코드는 .KS / .KQ 접미사 폴백 필요
                _t = (ticker or "").strip()
                _candidates = [_t]
                _bare = _t.split(".")[0]
                if _bare.isdigit() and len(_bare) == 6 and "." not in _t:
                    _candidates = [f"{_bare}.KS", f"{_bare}.KQ", _bare]
                hist_df = None
                for _sym in _candidates:
                    try:
                        _h = yf.Ticker(_sym).history(period="1y")
                        if _h is not None and not _h.empty and len(_h) >= 30:
                            hist_df = _h
                            break
                    except Exception:
                        continue
                if hist_df is None:
                    raise RuntimeError(
                        f"가격 데이터 없음 (시도: {', '.join(_candidates)})")
                loading.destroy()
                cv = tk.Canvas(tab_4axis, bg=C["PANEL"], highlightthickness=0)
                sb = ttk.Scrollbar(tab_4axis, orient="vertical", command=cv.yview)
                inner = tk.Frame(cv, bg=C["PANEL"])
                inner.bind("<Configure>",
                           lambda e: cv.configure(scrollregion=cv.bbox("all")))
                cv.create_window((0,0), window=inner, anchor="nw")
                cv.configure(yscrollcommand=sb.set)
                sb.pack(side="right", fill="y")
                cv.pack(side="left", fill="both", expand=True)
                _wheel = lambda e: cv.yview_scroll(int(-e.delta/120), "units")
                cv.bind("<MouseWheel>", _wheel)
                inner.bind("<MouseWheel>", _wheel)
                _macro = {"regime": data.get("Regime", "Neutral"),
                          "vix":    data.get("VIX")}
                build_four_axis_card(
                    inner, ticker, hist_df, C, F,
                    canslim=data, macro=_macro,
                    display_name=display_name,
                )
            except Exception as e:
                try:
                    loading.destroy()
                except Exception:
                    pass
                tk.Label(tab_4axis, text=f"  ⚠️  4축 분석 로드 실패: {e}",
                         bg=C["PANEL"], fg=C["RED"], font=F["BODY"],
                         padx=12, anchor="w", justify="left",
                         wraplength=720).pack(fill=tk.X, pady=20)
        nb.bind("<<NotebookTabChanged>>", _load_4axis)

        # ── 스크롤 본문 (CAN SLIM 탭) ────────────────────────────────
        canvas = tk.Canvas(tab_canslim, bg=C["PANEL"], highlightthickness=0)
        vsb    = ttk.Scrollbar(tab_canslim, orient="vertical", command=canvas.yview)
        hsb    = ttk.Scrollbar(tab_canslim, orient="horizontal", command=canvas.xview)
        sf     = tk.Frame(canvas, bg=C["PANEL"])
        sf.bind("<Configure>", lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=sf, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        canvas.pack(side="left", fill="both", expand=True, padx=8, pady=8)
        vsb.pack(side="right", fill="y")

        # 마우스 휠 스크롤 (팝업 내부만 바인딩)
        def _on_mousewheel(e):
            canvas.yview_scroll(int(-1*(e.delta/120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)
        sf.bind("<MouseWheel>", _on_mousewheel)

        # ── Breakdown 데이터 전처리 ──────────────────────────────────
        _bd_all    = data.get("Breakdown", [])
        _bd_total  = float(data.get("TotalScore", 0))
        _bd_signal = data.get("Signal", "-")

        def _bd_sig_color(sig: str, score: float) -> str:
            s = (sig or "").upper()
            if "BREAKOUT"      in s: return C.get("CANSLIM_S1", C["GOLD"])
            if "STRONG_LEADER" in s: return C.get("CANSLIM_S2", C["GREEN"])
            if "MOMENTUM"      in s: return C.get("CANSLIM_S2", C["GREEN"])
            if "LEADER"        in s: return C.get("CANSLIM_S3", C["ACCENT"])
            if "WATCH"         in s: return C.get("CANSLIM_S4", "#6C757D")
            if "CAUTION"       in s: return C.get("CANSLIM_S6", C["GOLD"])
            if "BEAR"          in s: return C.get("CANSLIM_S6", C["GOLD"])
            if "AVOID"         in s: return C.get("CANSLIM_S7", C["RED"])
            return C.get("CANSLIM_S4", C["TEXT_MAIN"])

        _card_col = _bd_sig_color(_bd_signal, _bd_total)

        # ── 1) 총점 요약 카드 ─────────────────────────────────────────
        _card = tk.Frame(sf, bg=_card_col, bd=0)
        _card.pack(fill=tk.X, padx=8, pady=(4, 14))
        _ci = tk.Frame(_card, bg=_card_col, padx=18, pady=12)
        _ci.pack(fill=tk.X)

        _r1 = tk.Frame(_ci, bg=_card_col)
        _r1.pack(fill=tk.X)
        tk.Label(_r1, text=f"★  {_bd_total:.1f}점",
                 font=F.get("HEADER", ("Segoe UI", 16, "bold")),
                 bg=_card_col, fg="white", anchor="w"
                 ).pack(side=tk.LEFT)
        tk.Label(_r1, text=f"  {_bd_signal}",
                 font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
                 bg=_card_col, fg="white", anchor="w"
                 ).pack(side=tk.LEFT, padx=(12, 0))

        _bar_f = max(0, min(20, round(_bd_total / 5)))
        tk.Label(_ci, text="█" * _bar_f + "░" * (20 - _bar_f),
                 font=("Consolas", 10),
                 bg=_card_col, fg="white", anchor="w"
                 ).pack(fill=tk.X, pady=(6, 0))

        # CAN SLIM 7원칙 Pass/Fail 뱃지
        _psc: dict = {}
        for _n, _sv, _ in _bd_all:
            if "══" in _n: continue
            for _ch in "CANSLIM":
                if _n.startswith(f"[{_ch}]") or _n.startswith(f"[{_ch} "):
                    _psc[_ch] = _psc.get(_ch, 0) + _sv
                    break
        _badge_txt = "  ".join(
            f"{ch}{'✓' if _psc.get(ch, 0) > 0 else '✗'}" for ch in "CANSLIM")
        tk.Label(_ci, text=_badge_txt,
                 font=F.get("SMALL_BOLD", ("Segoe UI", 9, "bold")),
                 bg=_card_col, fg="white", anchor="w"
                 ).pack(fill=tk.X, pady=(6, 0))

        # ── 2) 섹션 분류 ─────────────────────────────────────────────
        _sec_cs, _sec_qt, _sec_adj = [], [], []
        for _n, _sv, _d in _bd_all:
            if "══" in _n: continue
            if any(_n.startswith(f"[{c}") for c in "CANSLIM"):
                _sec_cs.append((_n, _sv, _d))
            elif "[Adj]" in _n:
                _sec_adj.append((_n, _sv, _d))
            else:
                _sec_qt.append((_n, _sv, _d))

        _bar_max_v = max(
            [abs(_sv) for _, _sv, _ in _bd_all if "══" not in _] + [1.0])

        def _score_bar_str(sv, bar_max=_bar_max_v, length=8):
            ratio  = max(0.0, min(1.0, abs(sv) / bar_max))
            filled = max(0, round(ratio * length))
            bar    = "█" * filled + "░" * (length - filled)
            if sv > 3:    col = C["GREEN"]
            elif sv > 0:  col = "#00C853"
            elif sv < -3: col = C["RED"]
            elif sv < 0:  col = "#FF7043"
            else:          col = C.get("TEXT_LABEL", C["TEXT_MAIN"])
            return bar, col

        # 팩터 이름 접두어 → 한줄 역할 설명
        _FACTOR_BRIEF: dict[str, str] = {
            "[C]":          "분기 EPS 성장 가속도",
            "[A]":          "연간 ROE 17%+ 기준",
            "[N]":          "52주 신고가·피벗 돌파",
            "[S]":          "거래량 확인 돌파 신호",
            "[L]":          "시장 대비 상대강도(RS)",
            "[I]":          "기관 스마트머니 수급",
            "[M]":          "시장 방향·레짐 판단",
            "[Quant] Fama": "가치·퀄리티 팩터 알파",
            "[Quant] Mean": "RSI·Z-Score 과매수/과매도",
            "[Quant] Mom":  "12개월 모멘텀 강도",
            "[Quant] Multi":"단·중·장기 추세 정렬",
            "[Quant] Draw": "최대 낙폭(MDD) 위험도",
            "[Quant] Smart":"스마트머니 A/D·OBV 흐름",
            "[Quant] Anal": "DCF 적정가 괴리율",
            "[Quant] Short":"공매도 비율 위험도",
            "[Math] Hurst": "추세 지속성 (H>0.5 추세형)",
            "[Math] Kalman":"칼만 필터 추세 신뢰도",
            "[Math] Stat":  "통계적 Z-Score 이상값",
            "[Adj]":        "변동성 조정·슈퍼 배율",
            "[Sentiment]":  "가격·거래량 기반 심리 지수",
            "[Scalp] ORB":  "전일 고가 돌파 단타 신호",
            "[Scalp] NR7":  "7일 최소 변동폭 압축 돌파",
            "[Scalp] BB":   "볼린저밴드 하단 반등 신호",
        }

        def _get_brief(name: str) -> str:
            for prefix, brief in _FACTOR_BRIEF.items():
                if name.startswith(prefix):
                    return brief
            return ""

        def _render_bd_section(title: str, rows: list, accent: str):
            if not rows: return
            _sec_total = sum(_sv for _, _sv, _ in rows)
            _sign_s = "+" if _sec_total >= 0 else ""
            # 섹션 헤더
            _sh = tk.Frame(sf, bg=C["PANEL"])
            _sh.pack(fill=tk.X, padx=8, pady=(10, 0))
            tk.Frame(_sh, bg=accent, width=4
                     ).pack(side=tk.LEFT, fill=tk.Y, pady=2)
            tk.Label(_sh,
                     text=f"  {title}  ({_sign_s}{_sec_total:.1f}점)",
                     font=F.get("BODY_BOLD", ("Segoe UI", 10, "bold")),
                     bg=C["PANEL"], fg=accent, anchor="w", pady=5
                     ).pack(side=tk.LEFT, fill=tk.X)
            # 구분선
            tk.Frame(sf, bg=C.get("SHADOW", "#E0E0E0"), height=1
                     ).pack(fill=tk.X, padx=8, pady=(0, 2))
            # 행
            for _rn, _rv, _rd in rows:
                _rbg = C["PANEL"]
                _rrow = tk.Frame(sf, bg=_rbg, bd=0)
                _rrow.pack(fill=tk.X, padx=8, pady=1)
                # 이름 + 팩터 역할 설명 (세로 배치)
                _name_col = tk.Frame(_rrow, bg=_rbg)
                _name_col.pack(side=tk.LEFT)
                tk.Label(_name_col, text=_rn, width=26,
                         font=F.get("SMALL", ("Segoe UI", 9)),
                         bg=_rbg, fg=C["TEXT_MAIN"],
                         anchor="w", padx=8
                         ).pack(anchor="w")
                _brief = _get_brief(_rn)
                if _brief:
                    tk.Label(_name_col, text=f"  {_brief}",
                             font=("Segoe UI", 7),
                             bg=_rbg, fg=C.get("TEXT_LABEL", C.get("TEXT_SUB", "#888")),
                             anchor="w", padx=8
                             ).pack(anchor="w")
                _bstr, _bcol = _score_bar_str(_rv)
                _rs = "+" if _rv > 0 else ""
                tk.Label(_rrow,
                         text=f"{_bstr}  {_rs}{_rv:.1f}",
                         font=("Consolas", 9),
                         bg=_rbg, fg=_bcol, anchor="w", width=18
                         ).pack(side=tk.LEFT)
                tk.Label(_rrow, text=_rd,
                         font=F.get("SMALL", ("Segoe UI", 9)),
                         bg=_rbg, fg=C.get("TEXT_SUB", C["TEXT_MAIN"]),
                         anchor="w", padx=6, wraplength=400, justify="left"
                         ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        _render_bd_section(
            "CAN SLIM 7원칙", _sec_cs, C.get("CANSLIM_S3", C["ACCENT"]))
        _render_bd_section(
            "퀀트 팩터", _sec_qt, C.get("PURPLE", C["ACCENT"]))
        _render_bd_section(
            "조정 요소", _sec_adj, C.get("TEXT_SUB", C["TEXT_MAIN"]))

        tk.Label(sf, text="", bg=C["PANEL"], height=2).pack()

    # ─────────────────────────────────────────────────────────────────────
    # 우클릭 컨텍스트 메뉴 (US-003)
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def _extract_ticker(raw_text: str) -> str:
        """Treeview text 에서 ticker 추출 (메인/스캘핑 트리 공통).

        포맷 호환: "  005930 ●", "005930  ", "005930 - 삼성전자".
        US-005 뱃지 도입 후 split()[0] 통일.
        """
        s = (raw_text or "").strip()
        # US-003: 관심종목 ★ 마커 제거
        if s.startswith("★"):
            s = s[1:].strip()
        return s.split()[0] if s else ""

    # ── 관심종목 (US-003) ──────────────────────────────────────
    def _get_watchlist(self):
        if self._watchlist_db is None and _WatchlistDB is not None:
            try:
                wl_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "watchlist.db")
                self._watchlist_db = _WatchlistDB(wl_path)
            except Exception as e:
                logging.warning("watchlist init failed: %s", e)
                self._watchlist_db = None
        return self._watchlist_db

    def _in_watchlist(self, ticker: str) -> bool:
        wl = self._get_watchlist()
        if wl is None: return False
        try:
            return wl.get(ticker) is not None
        except Exception:
            return False

    def _committee_str(self, d: dict) -> str:
        """US-006: 7-페르소나 위원회 — 5/7 ✓ / 3/7 ✗ 표시 (캐싱)."""
        tk = d.get("Ticker", "")
        if tk in self._committee_cache:
            r = self._committee_cache[tk]
            self._committee_cache.move_to_end(tk)  # LRU touch
        else:
            try:
                from persona_committee import evaluate as _committee_eval
                macro = {"regime": d.get("Regime", "Neutral"), "vix": d.get("VIX")}
                r = _committee_eval(None, d, macro)
                self._committee_cache[tk] = r
                # LRU 캡 초과 시 가장 오래된 항목 제거
                while len(self._committee_cache) > self._committee_cache_max:
                    self._committee_cache.popitem(last=False)
            except Exception:
                return "-"
        mark = "✓" if r.gate_pass else ("⚠" if r.weak_trend_warning else "✗")
        return f"{r.buy_count}/7 {mark}"

    def _toggle_watchlist(self, ticker: str, data: dict | None = None):
        wl = self._get_watchlist()
        if wl is None:
            logging.warning("watchlist unavailable")
            return
        try:
            if wl.get(ticker):
                wl.remove(ticker)
            else:
                wl.add(ticker)
                if data:
                    try:
                        wl.update_metrics(ticker,
                                          score=int(data.get("TotalScore", 0)),
                                          phase=data.get("Signal", ""))
                    except Exception:
                        pass
            # 표 갱신 (간단히 재렌더)
            try:
                for iid in self.tree.get_children():
                    self.tree.delete(iid)
                self._render_table()
            except Exception:
                pass
        except Exception as e:
            logging.warning("toggle_watchlist failed: %s", e)

    def _run_backtest_dialog(self, ticker: str, entry: str):
        """우클릭 → 백테스트. 별도 스레드에서 실행, 완료 시 차트+요약."""
        if not ticker:
            return
        try:
            from tkinter import messagebox
        except Exception:
            return
        import threading

        def _worker():
            try:
                from backtester import backtest as _bt
                r = _bt(ticker, entry=entry)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "백테스트 실패", f"{ticker}\n{e}"))
                return
            self.root.after(0, lambda: self._show_backtest_window(ticker, entry, r))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_backtest_window(self, ticker: str, entry: str, r: dict):
        """백테스트 결과 — Toplevel + (가능하면) matplotlib equity 차트."""
        win = tk.Toplevel(self.root)
        win.title(f"📈 {ticker} 백테스트 — {entry}")
        win.geometry("760x520")
        try:
            win.configure(bg=C["BG"])
        except Exception:
            pass

        info = (
            f"기간: {r.get('period','')}   거래수: {r.get('trades',0)}   "
            f"승률: {r.get('win_rate',0)*100:.1f}%   "
            f"손익비: {r.get('payoff_ratio',0):.2f}\n"
            f"Sharpe: {r.get('sharpe',0):.2f}   "
            f"MaxDD: {r.get('max_dd',0)*100:.1f}%   "
            f"최종 자본: {r.get('equity_final',1):.3f}"
        )
        tk.Label(win, text=info, justify="left", anchor="w",
                 bg=C.get("PANEL", "#222"), fg=C.get("TEXT_MAIN", "#eee"),
                 padx=10, pady=6).pack(fill="x", padx=10, pady=(10, 4))

        eq = r.get("equity_curve") or []
        ed = r.get("equity_dates") or []
        chart_drawn = False
        if eq and ed:
            try:
                # NOTE: matplotlib.use("Agg") 호출 금지 — FigureCanvasTkAgg 임베드가 깨짐
                from matplotlib.figure import Figure
                from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
                from matplotlib.dates import DateFormatter, AutoDateLocator
                from datetime import datetime
                fig = Figure(figsize=(7.0, 3.4), dpi=100)
                ax = fig.add_subplot(111)
                xs = [datetime.strptime(d, "%Y-%m-%d") for d in ed]
                ax.plot(xs, eq, linewidth=1.4)
                ax.axhline(1.0, linestyle="--", linewidth=0.8, alpha=0.5)
                ax.set_title(f"{ticker} equity (start=1.0)")
                ax.set_ylabel("equity")
                ax.grid(True, alpha=0.3)
                ax.xaxis.set_major_locator(AutoDateLocator())
                ax.xaxis.set_major_formatter(DateFormatter("%y-%m"))
                fig.autofmt_xdate()
                canvas = FigureCanvasTkAgg(fig, master=win)
                canvas.draw()
                canvas.get_tk_widget().pack(fill="both", expand=True,
                                            padx=10, pady=4)
                chart_drawn = True
            except Exception as e:
                logging.warning("backtest chart failed: %s", e)

        if not chart_drawn:
            tk.Label(win, text="(차트 미표시 — matplotlib 미설치 또는 데이터 없음)",
                     bg=C.get("BG", "#111"),
                     fg=C.get("TEXT_DIM", "#888")).pack(pady=10)

        tk.Label(win, text=r.get("summary_text", ""),
                 bg=C.get("BG", "#111"),
                 fg=C.get("TEXT_MAIN", "#eee"),
                 wraplength=720, justify="left").pack(padx=10, pady=(2, 6))
        tk.Button(win, text="닫기", command=win.destroy,
                  padx=12).pack(pady=(0, 10))

    def _show_news_dialog(self, ticker: str):
        """우클릭 → 뉴스 요약·감성 — 별도 스레드 + Toplevel."""
        from tkinter import messagebox
        if not ticker:
            return
        import threading

        def _worker():
            try:
                from news_summarizer import summarize as _ns
                r = _ns(ticker, limit=10)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "뉴스 요약 실패", f"{ticker}\n{e}"))
                return
            self.root.after(0, lambda: self._render_news_window(ticker, r))

        threading.Thread(target=_worker, daemon=True).start()

    def _render_news_window(self, ticker: str, r: dict):
        win = tk.Toplevel(self.root)
        win.title(f"📰 {ticker} 뉴스 요약")
        win.geometry("720x460")
        try:
            win.configure(bg=C["BG"])
        except Exception:
            pass

        head = (
            f"건수 {r.get('count', 0)}   ·   "
            f"평균 감성 {r.get('avg_sentiment', 0):+.2f}   ·   "
            f"긍 {r.get('positive', 0)} / 부 {r.get('negative', 0)} / "
            f"중 {r.get('neutral', 0)}"
        )
        tk.Label(win, text=head, anchor="w", justify="left",
                 bg=C.get("PANEL", "#222"), fg=C.get("TEXT_MAIN", "#eee"),
                 padx=8, pady=6).pack(fill="x", padx=10, pady=(10, 4))

        txt = tk.Text(win, wrap="word", height=18,
                      bg=C.get("BG", "#111"), fg=C.get("TEXT_MAIN", "#eee"),
                      relief="flat", padx=8, pady=6)
        txt.pack(fill="both", expand=True, padx=10, pady=4)
        txt.insert("end", "── 긍정 헤드라인 ──\n")
        for h in (r.get("top_positive") or []):
            txt.insert("end",
                       f"  +{float(h.get('sentiment', 0)):+.2f}  "
                       f"{h.get('title', '')}\n")
        txt.insert("end", "\n── 부정 헤드라인 ──\n")
        for h in (r.get("top_negative") or []):
            txt.insert("end",
                       f"  {float(h.get('sentiment', 0)):+.2f}  "
                       f"{h.get('title', '')}\n")
        txt.insert("end", f"\n{r.get('summary_text', '')}")
        txt.configure(state="disabled")

        tk.Button(win, text="닫기", command=win.destroy,
                  padx=12).pack(pady=(0, 10))

    def _show_naver_news_dialog(self, ticker: str, data: dict | None = None):
        """네이버 검색 API 한국어 뉴스 — 6자리 코드면 종목명으로 검색."""
        from tkinter import messagebox
        if not ticker:
            return
        import threading

        def _query() -> str:
            t = str(ticker).strip().upper().replace(".KS", "").replace(".KQ", "")
            if t.isdigit() and len(t) == 6:
                if isinstance(data, dict):
                    nm = (data.get("Name") or data.get("name")
                          or data.get("KoreanName"))
                    if nm and str(nm).strip() and str(nm).strip() != t:
                        return str(nm).strip()
                try:
                    from naver_finance import get_quote
                    q = get_quote(t)
                    if q.get("name"):
                        return q["name"]
                except Exception:
                    pass
                return t
            return ticker

        def _worker():
            try:
                from naver_news import summarize as _ns
                q = _query()
                r = _ns(q, limit=20)
                r["ticker"] = ticker
                r["count"] = r.get("count", 0)
                r["avg_sentiment"] = r.get("avg_sentiment", 0.0)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "네이버 뉴스 실패", f"{ticker}\n{e}"))
                return
            self.root.after(0, lambda: self._render_news_window(
                f"🇰🇷 {ticker} ({q})", r))

        threading.Thread(target=_worker, daemon=True).start()

    def _show_naver_quote_dialog(self, ticker: str):
        """네이버 금융 시세 + 외국인/기관 수급 요약."""
        from tkinter import messagebox
        if not ticker:
            return
        import threading

        def _worker():
            try:
                from naver_finance import (
                    get_quote, get_investor_flow, build_summary_text,
                )
                q = get_quote(ticker)
                if q.get("error") or q.get("code") is None:
                    self.root.after(0, lambda: messagebox.showwarning(
                        "네이버 금융",
                        f"{ticker}는 한국 6자리 코드가 아니거나 조회 실패."))
                    return
                f = get_investor_flow(ticker)
                txt = build_summary_text(q)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "네이버 금융 실패", f"{ticker}\n{e}"))
                return
            self.root.after(0, lambda: self._render_naver_quote_window(
                ticker, q, f, txt))

        threading.Thread(target=_worker, daemon=True).start()

    def _render_naver_quote_window(self, ticker, q: dict, f: dict, summary: str):
        win = tk.Toplevel(self.root)
        win.title(f"💹 {ticker} 네이버 금융")
        win.geometry("640x460")
        try:
            win.configure(bg=C["BG"])
        except Exception:
            pass
        tk.Label(win, text=summary,
                 bg=C.get("PANEL", "#222"), fg=C.get("TEXT_MAIN", "#eee"),
                 padx=8, pady=8, anchor="w", justify="left",
                 wraplength=600).pack(fill="x", padx=10, pady=(10, 6))
        rows = (f or {}).get("rows") or []
        body = tk.Text(win, wrap="none", height=18,
                       bg=C.get("BG", "#111"), fg=C.get("TEXT_MAIN", "#eee"),
                       relief="flat", padx=8, pady=6)
        body.pack(fill="both", expand=True, padx=10, pady=4)
        body.insert("end", "── 외국인/기관 수급 (최근 10일) ──\n")
        body.insert("end", f"{'날짜':<12}{'종가':>10}{'등락%':>8}"
                           f"{'외국인':>14}{'기관':>14}\n")
        for r in rows:
            body.insert(
                "end",
                f"{r.get('date',''):<12}"
                f"{(r.get('close') or 0):>10,.0f}"
                f"{(r.get('change_pct') or 0):>8.2f}"
                f"{(r.get('foreign_net') or 0):>14,.0f}"
                f"{(r.get('inst_net') or 0):>14,.0f}\n",
            )
        if not rows:
            body.insert("end", "(수급 데이터 없음)\n")
        if f.get("foreign_net_5d") is not None:
            body.insert("end",
                        f"\n5일 누적 — 외국인 {f['foreign_net_5d']:,.0f}주, "
                        f"기관 {f.get('inst_net_5d', 0):,.0f}주\n")
        body.configure(state="disabled")
        tk.Button(win, text="닫기", command=win.destroy,
                  padx=12).pack(pady=(0, 10))

    def _alert_rule_store(self):
        """싱글턴 AlertRuleStore — alert_rules.json 재사용."""
        try:
            from alert_rules import AlertRuleStore
        except Exception:
            return None
        if not hasattr(self, "_alerts") or self._alerts is None:
            try:
                self._alerts = AlertRuleStore("alert_rules.json")
            except Exception:
                self._alerts = None
        return getattr(self, "_alerts", None)

    def _portfolio_db(self):
        """싱글턴 PortfolioTracker — 같은 sqlite 파일을 재사용."""
        try:
            from portfolio_tracker import PortfolioTracker
        except Exception:
            return None
        if not hasattr(self, "_portfolio") or self._portfolio is None:
            try:
                self._portfolio = PortfolioTracker("portfolio.sqlite3")
            except Exception:
                self._portfolio = None
        return getattr(self, "_portfolio", None)

    def _record_position_dialog(self, ticker: str):
        """간단 BUY/SELL 입력 다이얼로그."""
        from tkinter import simpledialog, messagebox
        pt = self._portfolio_db()
        if pt is None:
            messagebox.showerror("오류", "portfolio_tracker 모듈을 사용할 수 없습니다.")
            return
        side = simpledialog.askstring("포지션 기록", f"{ticker} — 종류 (BUY/SELL):",
                                      initialvalue="BUY")
        if not side or side.upper() not in ("BUY", "SELL"):
            return
        side = side.upper()
        qty_s = simpledialog.askstring("포지션 기록", f"{ticker} — 수량:")
        if not qty_s:
            return
        price_s = simpledialog.askstring("포지션 기록", f"{ticker} — 단가:")
        if not price_s:
            return
        try:
            qty = float(qty_s)
            price = float(price_s)
            tid = pt.add_trade(ticker, side, qty, price)
            messagebox.showinfo("기록 완료",
                                f"#{tid} {side} {ticker} {qty}@{price}")
        except Exception as e:
            messagebox.showerror("실패", str(e))

    def _show_portfolio_dialog(self):
        """현재 포지션 / 요약 — Toplevel + Treeview."""
        from tkinter import messagebox, ttk
        pt = self._portfolio_db()
        if pt is None:
            messagebox.showerror("오류", "portfolio_tracker 모듈을 사용할 수 없습니다.")
            return
        try:
            poss = pt.positions()
            summ = pt.summary()
        except Exception as e:
            messagebox.showerror("실패", str(e))
            return

        win = tk.Toplevel(self.root)
        win.title("📊 포지션 / PnL")
        win.geometry("760x460")
        try:
            win.configure(bg=C["BG"])
        except Exception:
            pass

        cols = ("ticker", "qty", "avg", "mkt", "uPnL", "rPnL", "pnl%")
        tv = ttk.Treeview(win, columns=cols, show="headings", height=14)
        widths = {"ticker": 90, "qty": 80, "avg": 100, "mkt": 100,
                  "uPnL": 110, "rPnL": 100, "pnl%": 90}
        labels = {"ticker": "티커", "qty": "수량", "avg": "평균단가",
                  "mkt": "현재가", "uPnL": "미실현", "rPnL": "실현",
                  "pnl%": "수익률%"}
        for c in cols:
            tv.heading(c, text=labels[c])
            tv.column(c, width=widths[c],
                      anchor="center" if c == "ticker" else "e")
        tv.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        if not poss:
            tv.insert("", "end",
                      values=("(보유 포지션 없음)", "", "", "", "", "", ""))
        for p in poss:
            mp = p.get("market_price")
            tv.insert("", "end", values=(
                p.get("ticker", "-"),
                f"{p.get('qty', 0):.2f}",
                f"{p.get('avg_cost', 0):.2f}",
                f"{mp:.2f}" if mp is not None else "—",
                f"{p.get('unrealized_pnl', 0) or 0:+.2f}",
                f"{p.get('realized_pnl', 0) or 0:+.2f}",
                f"{(p.get('total_pnl_pct', 0) or 0) * 100:+.2f}",
            ))

        summary_txt = (
            f"총원가 {summ.get('total_cost', 0):.2f}  ·  "
            f"시가총액 {summ.get('total_market_value', 0):.2f}  ·  "
            f"실현 {summ.get('total_realized', 0):+.2f}  ·  "
            f"미실현 {summ.get('total_unrealized', 0):+.2f}  ·  "
            f"수익률 {(summ.get('return_pct', 0) or 0) * 100:+.2f}%"
        )
        lbl = tk.Label(win, text=summary_txt, anchor="w",
                       bg=C.get("PANEL", "#222"),
                       fg=C.get("TEXT_MAIN", "#eee"), padx=8, pady=6)
        lbl.pack(fill="x", padx=10, pady=(0, 8))

        btn_frame = tk.Frame(win, bg=C.get("BG", "#111"))
        btn_frame.pack(pady=(0, 10))
        tk.Button(btn_frame, text="📊 리스크 분석 (VaR)",
                  command=lambda: self._show_risk_dialog(pt),
                  padx=12).pack(side="left", padx=4)
        tk.Button(btn_frame, text="닫기", command=win.destroy,
                  padx=12).pack(side="left", padx=4)

    def _show_risk_dialog(self, pt):
        """포트폴리오 리스크 — risk_dashboard 호출."""
        from tkinter import messagebox
        import threading

        try:
            from risk_dashboard import (portfolio_var,
                                        from_portfolio_tracker)
        except Exception as e:
            messagebox.showerror("오류", f"risk_dashboard 미사용: {e}")
            return

        try:
            weights = from_portfolio_tracker(pt)
        except Exception as e:
            messagebox.showerror("실패", f"weight 계산 실패: {e}")
            return
        if not weights:
            messagebox.showinfo("리스크 분석",
                                "보유 포지션이 없거나 시가총액이 0입니다.")
            return

        def _worker():
            try:
                r = portfolio_var(weights)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror(
                    "VaR 실패", str(e)))
                return
            msg = (
                f"종목: {len(r.get('tickers', []))}개\n"
                f"Daily VaR(95%): "
                f"{(r.get('daily_var') or 0)*100:.2f}%\n"
                f"Daily CVaR: {(r.get('daily_cvar') or 0)*100:.2f}%\n"
                f"연환산 변동성: "
                f"{(r.get('annual_vol') or 0)*100:.2f}%\n"
                f"Sharpe(1Y): {r.get('sharpe', 0):.2f}\n"
                f"MaxDD(1Y): {(r.get('max_dd') or 0)*100:.1f}%\n\n"
                f"{r.get('summary_text', '')}"
            )
            self.root.after(0, lambda: messagebox.showinfo(
                "📊 포트폴리오 리스크", msg))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_right_click_main(self, event):
        try:
            iid = self.tree.identify_row(event.y)
            if not iid:
                return
            self.tree.selection_set(iid)
            ticker = self._extract_ticker(self.tree.item(iid, "text"))
            data = next((d for d in self.current_data if d.get("Ticker") == ticker), None)
            self._show_context_menu(event, ticker, data, source="main")
        except Exception as e:
            logging.warning(f"[RightClick/Main] {e}")

    def _on_right_click_sca(self, event):
        try:
            iid = self.sca_tree.identify_row(event.y)
            if not iid:
                return
            self.sca_tree.selection_set(iid)
            ticker = self._extract_ticker(self.sca_tree.item(iid, "text"))
            data = next((d for d in self.current_data if d.get("Ticker") == ticker), None)
            self._show_context_menu(event, ticker, data, source="sca")
        except Exception as e:
            logging.warning(f"[RightClick/SCA] {e}")

    def _show_context_menu(self, event, ticker, data, source="main"):
        menu = tk.Menu(self.root, tearoff=0,
                       bg=C["PANEL"], fg=C["TEXT_MAIN"],
                       activebackground=C["ACCENT"],
                       activeforeground=C["HIGHLIGHT"])
        menu.add_command(label=f"📊  {ticker} 상세 보기",
                         command=lambda: self._invoke_detail(ticker, data, source))
        # US-003: 관심종목 토글
        in_wl = self._in_watchlist(ticker)
        menu.add_command(
            label=("★  관심종목에서 제거" if in_wl else "☆  관심종목에 추가"),
            command=lambda: self._toggle_watchlist(ticker, data))
        menu.add_separator()
        menu.add_command(label="📈  5Y 백테스트 (20MA 돌파)",
                         command=lambda: self._run_backtest_dialog(ticker, "20MA_breakout"))
        menu.add_command(label="📉  5Y 백테스트 (RSI 반전)",
                         command=lambda: self._run_backtest_dialog(ticker, "rsi_reversal"))
        menu.add_command(label="💰  포지션 기록 (BUY/SELL)…",
                         command=lambda: self._record_position_dialog(ticker))
        menu.add_command(label="📊  포지션/PnL 보기",
                         command=lambda: self._show_portfolio_dialog())
        menu.add_command(label="📰  뉴스 요약·감성",
                         command=lambda: self._show_news_dialog(ticker))
        menu.add_command(label="🇰🇷  네이버 뉴스 (한국어)",
                         command=lambda: self._show_naver_news_dialog(ticker, data))
        menu.add_command(label="💹  네이버 금융 시세/수급",
                         command=lambda: self._show_naver_quote_dialog(ticker))
        menu.add_separator()
        menu.add_command(label="📋  티커 복사",
                         command=lambda: (self.root.clipboard_clear(),
                                          self.root.clipboard_append(ticker)))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _invoke_detail(self, ticker, data, source):
        # sca_tree 우클릭은 메인 트리 폴백 금지 — 잘못된 종목 상세를 띄움
        if source == "sca":
            if data:
                self._show_detail_data(data)
            else:
                # current_data 미스: ticker 기반 최소 dict 로 표시
                self._show_detail_data({
                    "Ticker": ticker,
                    "Name": self._resolve_display_name(ticker, ticker),
                })
            return
        sel = self.tree.selection()
        if sel:
            self._show_detail(sel[0])

    # ─────────────────────────────────────────────────────────────────────
    # 통계 팝업
    # ─────────────────────────────────────────────────────────────────────
    def _show_stats(self):
        if not self.current_data:
            return
        win = tk.Toplevel(self.root)
        win.title("📊 CAN SLIM 스캔 통계")
        win.geometry("560x640")
        win.configure(bg=C["PANEL"])

        tk.Label(win, text="📊  CAN SLIM SCAN STATISTICS",
                 font=F["POPUP_SUB"], bg=C["PANEL"], fg=C["ACCENT"],
                 pady=16).pack()

        scores = [d["TotalScore"] for d in self.current_data]
        breakouts      = [d for d in self.current_data if "BREAKOUT" in d["Signal"] or "MOMENTUM LEADER" in d["Signal"]]
        leaders        = [d for d in self.current_data if d.get("IsLeader")]
        eps_accel      = [d for d in self.current_data if d.get("EPSAcceleration")]
        near_high      = [d for d in self.current_data if d.get("NearHighPass")]
        s_confirmed    = [d for d in self.current_data if d.get("SConfirmed")]
        fail_safe_lst  = [d for d in self.current_data if d.get("FailSafe")]
        bear_cap_lst   = [d for d in self.current_data if d.get("BearCap")]
        super_mult_lst = [d for d in self.current_data if d.get("SuperMult", 1.0) > 1.1]

        rows = [
            ("── 기본 통계 ──",           ""),
            ("총 분석 종목",              len(self.current_data)),
            ("평균 점수",                f"{sum(scores)/len(scores):.1f} / 100"),
            ("최고 점수",                f"{max(scores):.1f}"),
            ("최저 점수",                f"{min(scores):.1f}"),
            ("", ""),
            ("── CAN SLIM 원칙별 집계 ──", ""),
            ("⭐⭐⭐⭐ BREAKOUT / HI-MOM", len(breakouts)),
            ("[L] RS 80+ 주도주",          len(leaders)),
            ("[C] EPS 가속도 확인",         len(eps_accel)),
            ("[N] 52주 신고가 근접",         len(near_high)),
            ("[S] 거래량 확인 돌파",         len(s_confirmed)),
            ("[슈퍼 그로스] ×1.1 이상",    len(super_mult_lst)),
            ("", ""),
            ("── 위험 필터 ──",           ""),
            ("⛔ Fail-Safe 발동",           len(fail_safe_lst)),
            ("[M] Bear Cap 발동",           len(bear_cap_lst)),
            ("", ""),
            ("── 캐시 ──",               ""),
            ("캐시 히트",                self.stats["cache_hits"]),
            ("API 호출",                 self.stats["cache_misses"]),
        ]

        body = tk.Frame(win, bg=C["PANEL"])
        body.pack(fill=tk.BOTH, expand=True, padx=28, pady=4)

        for label, val in rows:
            if not label:
                tk.Frame(body, bg=C["SHADOW"], height=1).pack(fill=tk.X, pady=5)
                continue
            if "──" in label:
                tk.Label(body, text=label, font=F["SMALL_BOLD"],
                         bg=C["PANEL"], fg=C["ACCENT"], anchor="w").pack(fill=tk.X, pady=(6, 2))
                continue
            r = tk.Frame(body, bg=C["PANEL"])
            r.pack(fill=tk.X, pady=3)
            tk.Label(r, text=label, font=F["BODY"],
                     bg=C["PANEL"], fg=C["TEXT_SUB"]).pack(side=tk.LEFT)
            fg = C["RED"] if isinstance(val, int) and "Bear" in label and val > 0 else C["ACCENT"]
            tk.Label(r, text=str(val), font=F["BODY_BOLD"],
                     bg=C["PANEL"], fg=fg).pack(side=tk.RIGHT)

        self._skeu_button(win, "닫기", win.destroy, font_size=10, pady=8).pack(pady=14)

    # ─────────────────────────────────────────────────────────────────────
    # 전략 가이드 팝업
    # ─────────────────────────────────────────────────────────────────────
    def _show_guide(self):
        win = tk.Toplevel(self.root)
        win.title("📘 (.)(.)스캐너 가이드")
        win.geometry("900x960")
        win.configure(bg=C["PANEL"])
        tk.Label(win, text="⭐  (.)(.)스캐너",
                 font=F["POPUP_SUB"], bg=C["PANEL"], fg=C["ACCENT"], pady=14).pack()
        tk.Label(win, text="윌리엄 오닐(William O'Neil) 7원칙 + 월가 퀀트 19전략 융합",
                 font=F["BODY"], bg=C["PANEL"], fg=C["GOLD"]).pack()

        fr = tk.Frame(win, bg=C["PANEL"])
        fr.pack(fill=tk.BOTH, expand=True, padx=20, pady=(10, 16))
        vsb = ttk.Scrollbar(fr, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb = ttk.Scrollbar(fr, orient="horizontal")
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        txt = tk.Text(fr, bg=C["SIDEBAR"], fg=C["TEXT_MAIN"],
                      font=F["BODY"], yscrollcommand=vsb.set,
                      xscrollcommand=hsb.set,
                      padx=20, pady=16, wrap=tk.NONE, relief="flat", bd=0)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.config(command=txt.yview)
        hsb.config(command=txt.xview)

        txt.tag_config("h1",    font=F["SUBHEADER"], foreground=C["ACCENT"])
        txt.tag_config("h2",    font=F["HEADER"], foreground=C["GOLD"])
        txt.tag_config("body",  font=F["BODY"], foreground=C["TEXT_SUB"])
        txt.tag_config("score", font=F["SMALL"], foreground=C["PURPLE"])
        txt.tag_config("warn",  font=F["BODY_BOLD"], foreground=C["RED"])
        txt.tag_config("sep",   font=F["TINY"], foreground=C["SHADOW"])

        def h1(t):  txt.insert(tk.END, f"\n{t}\n", "h1")
        def h2(t):  txt.insert(tk.END, f"  {t}\n", "h2")
        def b(t):   txt.insert(tk.END, f"    {t}\n", "body")
        def sc(t):  txt.insert(tk.END, f"    {t}\n", "score")
        def w(t):   txt.insert(tk.END, f"  ⚠️  {t}\n", "warn")
        def sep():  txt.insert(tk.END, "  " + "─"*72 + "\n\n", "sep")

        h1("━━━  CAN SLIM 7원칙  (C·A·N·S·L·I·M)  ━━━")
        sep()

        h2("[C]  Current Quarterly Earnings — 분기 EPS 가속도")
        b("오닐 원칙: '단순 성장이 아니라 가속도에 집중하라.'")
        b("직전 분기 EPS 성장률 25% 이상이면 보너스를 부여합니다.")
        sc("EPS ≥ 100%  → c_score +40  (폭발 성장)")
        sc("EPS ≥  50%  → c_score +28  (강한 성장)")
        sc("EPS ≥  25%  → c_score +18  (오닐 최소 기준 충족)")
        sc("EPS  <   0% → c_score -10~-25  + Fail-Safe 트리거")
        b("3분기 연속 가속도 확인 시 → 가중치 ×2배 + [C🔥] Earnings Acceleration 태그")
        sep()

        h2("[A]  Annual Earnings Growth — ROE 17% 이상 필수")
        b("오닐 원칙: 'ROE 17% 미만 기업은 진정한 성장주가 아니다.'")
        sc("ROE ≥ 25%  → a_score +18  (최우량)")
        sc("ROE ≥ 17%  → a_score +10  (기준 충족)")
        sc("ROE < 17%  → a_score  -6 ~ -12  (엄격 감점)")
        sc("ROE  <  0% → a_score  -25  (적자 기업 즉시 낙오)")
        sep()

        h2("[N]  New Highs / Pivot Breakout — 신고가 및 컵앤핸들")
        b("오닐 원칙: '주도주는 언제나 신고가에서 매수한다.'")
        sc("현재가 ≤ 52주 고가의 105%   → n_raw +20  (Near 52W High)")
        sc("현재가 ≤ 52주 고가의 110%   → n_raw +10")
        sc("최근 20일 내 피벗 돌파 감지 → n_raw +15  [N🔔]")
        b("고점 대비 -30% 이상 하락 시 → 낙오주 페널티 적용")
        sep()

        h2("[S]  Supply & Demand — 거래량 확인 돌파")
        b("오닐 원칙: '거래량 없는 상승은 가짜 돌파다.'")
        sc("가격↑  거래량 ≥ 150%  → s_score +18  [S✅ CONFIRMED BREAKOUT]")
        sc("가격↑  거래량 ≥ 140%  → s_score +13  [S✅]")
        w("가격↑  거래량  <  80%  → s_score -15  [S⚠️ UNCONFIRMED — 가짜 돌파 경고]")
        sc("가격↓  거래량 폭증    → s_score -18  [기관 투매]")
        sep()

        h2("[L]  Leader or Laggard — RS Rating 80+ 주도주만")
        b("오닐 원칙: '항상 해당 섹터에서 RS가 가장 높은 1~2종목만 매수하라.'")
        sc("RS Rating 80~100 → is_leader = True  → l_score +20  [L⭐ LEADER]")
        sc("RS Rating 60~ 79 → l_score +6~+12")
        sc("RS Rating 40~ 59 → l_score  -5  (LAGGARD 시작)")
        w("RS Rating  0~ 39 → l_score -20 + Fail-Safe 트리거  [L📉 AVOID]")
        sep()

        h2("[I]  Institutional Sponsorship — 기관 수급")
        b("Smart Money Flow (A/D Line + OBV + MFI) 로 기관 매집 여부 판단.")
        sc("ACCUMULATION 신호 → +10점  |  DISTRIBUTION → -10점")
        sep()

        h2("[M]  Market Direction — 시장 방향 최우선")
        b("오닐 원칙: '시장을 이기는 장사는 없다. Bear 시장에서는 절대 매수 금지.'")
        w("BEAR / STRONG_BEAR 판정 시 → 개별 최종 점수 최대 50점 Cap 강제 적용!")
        sc("STRONG_BULL  → regime_score +20  (억제 없음)")
        sc("BULL         → regime_score +12")
        sc("SIDEWAYS     → regime_score  +4")
        sc("BEAR         → regime_score -15  + [M🚫] 50% Cap 발동")
        sc("STRONG_BEAR  → regime_score -25  + [M🚫] 50% Cap 발동")
        sep()

        h1("━━━  점수 체계 특수 로직  ━━━")
        sep()

        h2("🔢  슈퍼 그로스 승수  (Super Growth Multiplier)")
        b("C + A + L 세 조건 모두 충족 시, TotalScore에 비선형 승수 적용.")
        sc("C✅ A✅ L✅ + EPS Acceleration  → × 1.50  (최고 등급)")
        sc("C✅ A✅ L✅ + EPS ≥ 50%        → × 1.40")
        sc("C✅ A✅ L✅ + 52W High + 거래량 확인  → × 1.35")
        sc("C✅ A✅ L✅ (기본)             → × 1.20")
        sc("2/3 조건 충족               → × 1.08")
        b("→ 90점 이상 고득점 종목이 희소하게 나타납니다.")
        sep()

        h2("⛔  Fail-Safe Ceiling  (낙제점 제도)")
        w("다음 중 하나라도 해당하면 최종 점수 50점 상한 강제 적용:")
        b("  1) EPS 성장률 < 0%  (적자 성장 기업)")
        b("  2) RS Rating < 40   (심각한 낙오주)")
        b("Breakdown 에 [C⛔] 또는 [L📉] 태그로 이유 표시")
        sep()

        h2("🔬  Hurst + Kalman 신뢰도 필터")
        b("Hurst Exponent ≥ 0.60 (추세적 시장) + Kalman BUY_TREND 동시 충족:")
        sc("두 조건 모두 충족  → 전체 점수 × 1.06  [MATH✅]")
        sc("Hurst < 0.45  + Kalman SELL → × 0.92  [MATH⚠️]")
        b("→ 수학적으로 추세가 확인된 종목만 높은 점수를 받습니다.")
        sep()

        h1("━━━  시그널 체계  ━━━")
        sep()
        signals = [
            ("⭐⭐⭐⭐ CAN SLIM BREAKOUT", "90점↑ + C·A·L 3조건 모두 충족. 오닐의 완벽한 매수 조건."),
            ("🚀 HIGH MOMENTUM LEADER",   "82점↑ + RS 주도주 + 거래량 확인 돌파."),
            ("⭐⭐⭐ STRONG LEADER",        "82점↑ 우수 종목."),
            ("⭐⭐ LEADER",                "72점↑ 주도주 구간."),
            ("⭐ WATCH LIST — Accumulate", "60점↑ 관심 종목."),
            ("⏸ NEUTRAL — Hold",          "48점↑ 중립."),
            ("⚠️ CAUTION — Reduce",        "35점↑ 주의. 또는 Fail-Safe 발동."),
            ("📉 SELL / AVOID",            "35점 미만. 매도 또는 회피."),
            ("🚫 BEAR MARKET — AVOID",     "[M] Bear Cap 발동 종목. 시장 방향 역행 금지."),
        ]
        for sig, desc in signals:
            txt.insert(tk.END, f"  {sig}\n", "h2")
            txt.insert(tk.END, f"    → {desc}\n", "body")
        sep()

        h1("━━━  보조 퀀트 전략 (기존 19전략 유지)  ━━━")
        sep()
        quant = [
            ("Fama-French 5-Factor", "노벨상 이론. P/B·P/E·ROE·부채비율로 저평가 우량주 식별."),
            ("Carhart Momentum",     "12M 모멘텀(최근 1개월 제외). 추세 지속성 활용."),
            ("Mean Reversion",       "볼린저+RSI+MACD+Z-Score. 과매도 반등 기회 탐지."),
            ("ATR Risk Management",  "변동성 레짐 판단. 포지션 사이즈 조정."),
            ("VWAP Analysis",        "기관 평균단가 대비 현재가 위치 평가."),
            ("Quality Factor (AQR)", "ROE·마진·부채·유동성 재무 건전성."),
            ("Smart Money Flow",     "A/D Line·OBV·MFI 세력 매집 추적."),
            ("Multi-Timeframe",      "단·중·장기 정배열 동시 성립 선별."),
            ("Drawdown Risk",        "현재 MDD 측정, 낙폭 클수록 페널티."),
            ("Vol-Adjusted Score",   "수익/변동성 비율 Sharpe 개념 배수 조정."),
            ("DCF Target",           "3단계 DCF 적정가 대비 잠재 상승."),
            ("Short Interest",       "공매도 비율 리스크 평가."),
            ("Hurst Exponent",       "R/S 분석. 주가 특성(추세/랜덤/평균회귀) 분류."),
            ("Kalman Filter",        "NASA 알고리즘. 노이즈 제거 후 진짜 추세 파악."),
            ("Stat Arb Z-Score",     "2σ 이탈 고확률 반등/과매수 구간 식별."),
        ]
        for title, desc in quant:
            txt.insert(tk.END, f"  ▸ {title}\n", "h2")
            txt.insert(tk.END, f"    {desc}\n", "body")

        txt.insert(tk.END, "\n\n  \"이것이 진정한 주도주를 찾는 도구다.\"  — William O'Neil\n\n", "h1")
        txt.config(state="disabled")

        self._skeu_button(win, "닫기", win.destroy, font_size=10, pady=8).pack(pady=10)

    # ─────────────────────────────────────────────────────────────────────
    # 캐시 삭제
    # ─────────────────────────────────────────────────────────────────────
    def _clear_cache(self):
        self.cache.clear()
        self._log("🗑  캐시 삭제 완료")
        messagebox.showinfo("캐시", "캐시가 삭제되었습니다.")

    # ─────────────────────────────────────────────────────────────────────
    # 엑셀 내보내기
    # ─────────────────────────────────────────────────────────────────────
    def _export_excel(self):
        if not self.current_data:
            messagebox.showwarning("데이터 없음", "먼저 스캔을 실행해 주세요.")
            return
        try:
            fname = f"(.)(.)스캐너_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            wb    = xlsxwriter.Workbook(fname)
            ws    = wb.add_worksheet("분석_결과")

            # 포맷
            hdr_fmt  = wb.add_format({"bold":1,"bg_color":"#F4F4F4","font_color":"#191919",
                                      "align":"center","border":1,"font_name":"Consolas"})
            ctr_fmt  = wb.add_format({"align":"center","border":1,"font_name":"Consolas","font_size":9})
            good_fmt = wb.add_format({"font_color":"#00C853","bg_color":"#E8F5E9","align":"center",
                                      "border":1,"font_name":"Consolas","font_size":9})
            bad_fmt  = wb.add_format({"font_color":"#F04452","bg_color":"#FFEBEE","align":"center",
                                      "border":1,"font_name":"Consolas","font_size":9})
            txt_fmt  = wb.add_format({"text_wrap":1,"border":1,"valign":"top",
                                      "font_name":"Consolas","font_size":9})

            base_hdrs = ["Ticker","Sector","Name","Price","Score","Signal","TopReason"]
            bd_sample = self.current_data[0].get("Breakdown", []) if self.current_data else []
            strat_hdrs = [b[0] for b in bd_sample]
            all_hdrs   = base_hdrs + strat_hdrs + ["상세 분석 리포트"]

            for ci, h in enumerate(all_hdrs):
                ws.write(0, ci, h, hdr_fmt)
                ws.set_column(ci, ci, 12 if ci < 6 else (60 if ci == len(all_hdrs)-1 else 16))

            for ri, d in enumerate(sorted(self.current_data,
                                          key=lambda x: x["TotalScore"], reverse=True), 1):
                bd     = d.get("Breakdown", [])
                report = (f"[{d['Ticker']}] 종합점수: {d['TotalScore']:.1f}  시그널: {d['Signal']}\n\n"
                          + "\n".join(f"• {n}: {'+' if s>0 else ''}{s:.1f}  {desc}"
                                      for n, s, desc in bd))
                ws.write(ri, 0, d["Ticker"],              ctr_fmt)
                ws.write(ri, 1, d.get("Sector", ""),      ctr_fmt)
                ws.write(ri, 2, d["Name"],                ctr_fmt)
                ws.write(ri, 3, d["Price"],               ctr_fmt)
                ws.write(ri, 4, d["TotalScore"],          ctr_fmt)
                ws.write(ri, 5, d["Signal"],              ctr_fmt)
                ws.write(ri, 6, d.get("TopReason", "-"),  ctr_fmt)
                for ci2, (_, s, _) in enumerate(bd, 7):
                    ws.write(ri, ci2, s, good_fmt if s > 0 else bad_fmt)
                ws.write(ri, 7 + len(bd), report, txt_fmt)

            wb.close()
            self._log(f"✅ 엑셀 저장: {fname}")
            messagebox.showinfo("Excel", f"파일 저장 완료!\n{fname}")
        except Exception as e:
            logging.error(f"Excel 오류: {e}")
            messagebox.showerror("Excel 오류", str(e))

    # ─────────────────────────────────────────────────────────────────────
    # 한국 종목 한글명 사전 (yfinance shortName 영문 대체)
    # ─────────────────────────────────────────────────────────────────────
    KR_NAMES: dict[str, str] = {
        # ── 반도체 ────────────────────────────────────────────────────────
        "000660.KS": "SK하이닉스",       "005930.KS": "삼성전자",
        "042700.KS": "한미반도체",        "000990.KS": "DB하이텍",
        "089030.KQ": "테크윙",            "131290.KQ": "티에스이",
        "095340.KQ": "ISC",               "058470.KQ": "리노공업",
        "240810.KQ": "원익IPS",  # 108320은 LX세미콘(.KQ)으로 삭제
        "393890.KQ": "더블유씨피",
        "032500.KQ": "케이엠더블유",       # 094820은 일진파워(파인드라이브 매핑 오류 삭제)
        "054620.KQ": "APS",
        "086960.KQ": "MDS테크",               "080220.KQ": "제주반도체",
        "200710.KQ": "에이디테크놀로지",   "033640.KQ": "네패스",
        "403870.KQ": "HPSP",              "357780.KQ": "솔브레인",
        "007660.KS": "이수페타시스",       "011070.KS": "LG이노텍",
        "011790.KS": "SKC",               "178920.KS": "PI첨단소재",
        "005290.KQ": "동진쎄미켐",         "166090.KQ": "하나머티리얼즈",
        "457370.KQ": "한켐",               "281820.KS": "케이씨텍",
        "222800.KQ": "심텍",
        "253840.KQ": "수젠텍",      "036540.KQ": "SFA반도체",
        "036810.KQ": "에프에스티",          # 046080은 코다코(웰크론한텍=076080 매핑 오류 삭제)
        # ── AI 인프라 ──────────────────────────────────────────────────────
        "035420.KS": "NAVER",             "035720.KS": "카카오",
        "034220.KS": "LG디스플레이",       "066570.KS": "LG전자",
        "267250.KS": "HD현대",              "018260.KS": "삼성SDS",  # 267250=HD현대(현대글로비스=086280)
        "047050.KS": "포스코인터내셔널",   "030530.KQ": "원익홀딩스",  # 047050은 포스코인터내셔널(포스코DX는 022100)
        "009150.KS": "삼성전기",            "052710.KQ": "아모텍",
        "030200.KS": "KT",                 "017670.KS": "SK텔레콤",
        "032640.KS": "LG유플러스",          # 036490 씨앤유글로벌 매핑 미확인 삭제
        "022100.KS": "포스코DX",             "084730.KQ": "팅크웨어",
        "039560.KQ": "다산네트웍스",           "050860.KQ": "아세아텍",
        # ── 전력 인프라 ────────────────────────────────────────────────────
        "010120.KS": "LS ELECTRIC",        "267260.KS": "HD현대일렉트릭",
        "006260.KS": "LS",                 "062040.KS": "산일전기",
        "298040.KS": "효성중공업",           "033100.KQ": "제룡전기",
        "103590.KS": "일진전기",
        "001440.KS": "대한전선",             "229640.KS": "LS에코에너지",
        "004830.KS": "덕성",                 "103140.KS": "풍산",  # 삼화콘덴서는 001820
        "036460.KS": "한국가스공사",
        "010130.KS": "고려아연",             "034020.KS": "두산에너빌리티",
        "052690.KS": "한전기술",              "015760.KS": "한국전력",
        "051600.KS": "한전KPS",             "083650.KQ": "비에이치아이",
        "112610.KS": "씨에스윈드",
        "009830.KS": "한화솔루션",            "096770.KS": "SK이노베이션",
        # ── K-방산 ────────────────────────────────────────────────────────
        "012450.KS": "한화에어로스페이스",    "064350.KS": "현대로템",
        "047810.KS": "한국항공우주",          "079550.KS": "LIG넥스원",
        "003570.KS": "SNT다이내믹스",         "042660.KS": "한화오션",  # SNT모티브는 064960
        "282720.KQ": "금양그린파워",           # 114570 방산테크 매핑 미확인 삭제
        # 048260 오스템임플란트는 2023 상장폐지, 014070은 성창오토텍(파이오링크=170790 매핑 오류 삭제)
        # 042080 도화엔지니어링 매핑 오류 삭제(도화엔지니어링=002150.KS)
        # ── 조선·해운 ──────────────────────────────────────────────────────
        "329180.KS": "HD현대중공업",          "009540.KS": "HD한국조선해양",
        "010140.KS": "삼성중공업",             "028670.KS": "팬오션",  # 삼성중공업우는 010145
        "011200.KS": "HMM",                  "082740.KS": "한화엔진",
        "071970.KS": "HD현대마린엔진",          # 014030 DL건설은 DL이앤씨로 흡수합병(매핑 삭제)
        "001230.KS": "동국홀딩스",             "009070.KS": "KCTC",
        "000120.KS": "CJ대한통운",             "005880.KS": "대한해운",
        "014160.KS": "대영포장",               "003490.KS": "대한항공",
        # ── 이차전지·ESS ───────────────────────────────────────────────────
        "373220.KS": "LG에너지솔루션",         "006400.KS": "삼성SDI",
        "247540.KQ": "에코프로비엠",            "086520.KQ": "에코프로",
        "003670.KS": "포스코퓨처엠",            "066970.KS": "엘앤에프",
        "051910.KS": "LG화학",                 "450080.KS": "에코프로머티",
        "365550.KS": "ESR켄달스퀘어리츠",              "278280.KQ": "천보",
        "302920.KQ": "더콘텐츠온",  # 091990은 셀트리온헬스(2024 합병상폐)로 삭제
        "123040.KQ": "엠에스오토텍",
        "372170.KQ": "윤성에프앤씨",  # 020150은 .KS(롯데에너지머티리얼즈)이므로 .KQ 항목 삭제. 372170은 윤성에프앤씨(피엔티 아님)
        "137400.KQ": "피엔티",                  # 050960은 수산아이앤티(코스모화학=005420.KS 매핑 오류 삭제)
        "039440.KQ": "에스티아이",          "309930.KQ": "조이웍스앤코",
        "124560.KQ": "태웅로직스",                 "356860.KQ": "티엘비",
        # ── 바이오·헬스케어 ────────────────────────────────────────────────
        "068270.KS": "셀트리온",               "207940.KS": "삼성바이오로직스",
        "326030.KS": "SK바이오팜",              "196170.KQ": "알테오젠",
        "347850.KQ": "디앤디파마텍",           "141080.KQ": "리가켐바이오",
        "087010.KQ": "펩트론",                  "000250.KQ": "삼천당제약",  # 동국제약은 086450
        "214370.KQ": "케어젠",                  "028300.KQ": "HLB",
        "128940.KS": "한미약품",                "185750.KS": "종근당",
        "006280.KS": "녹십자",                  "000100.KS": "유한양행",
        "018670.KS": "SK가스",                  "009420.KS": "한올바이오파마",  # SK케미칼은 285130, 한독은 002390
        "950210.KS": "프레스티지바이오파마",
        "417840.KQ": "저스템",
        "017810.KS": "E1",                       "005090.KS": "SGC에너지",
        "009160.KS": "SIMPAC",                   "222040.KQ": "바이넥스",
        "011760.KS": "현대코퍼레이션",
        "278470.KS": "에이피알",                 "382800.KQ": "지앤비에스 에코",
        "214150.KQ": "클래시스",                 # 335890 지씨씨바이오텍 매핑 미확인 삭제
        "043150.KQ": "바텍",                    "145020.KQ": "휴젤",
        "059090.KQ": "미코",                    "039840.KQ": "디오",  # 메디아나는 041920
        "145720.KS": "덴티움",                  "099190.KQ": "아이센스",  # 바이오인프라는 199730
        "237690.KQ": "에스티팜",                  "298060.KQ": "풍전약품",
        # ── 로봇·자동화 ────────────────────────────────────────────────────
        "454910.KS": "두산로보틱스",              "277810.KQ": "레인보우로보틱스",
        "397030.KQ": "에이프릴바이오",                 "058610.KQ": "에스피지",
        "336370.KS": "솔루스첨단소재",            "455900.KQ": "엔젤로보틱스",
        "014620.KQ": "성광벤드",
        "082270.KQ": "젬백스",              # 094850은 참좋은여행(상아프론테크=089980 매핑 오류 삭제)
        "005380.KS": "현대차",                   "000270.KS": "기아",
        "012330.KS": "현대모비스",                "204320.KS": "HL만도",
        "307950.KS": "현대오토에버",               "018880.KS": "한온시스템",
        "161390.KS": "한국타이어앤테크놀로지",      "097520.KS": "엠씨넥스",  # 중앙첨단소재 아님
        # ── K-소비재 ──────────────────────────────────────────────────────
        "192820.KS": "코스맥스",                  "483650.KS": "달바글로벌",
        "090430.KS": "아모레퍼시픽",              "161890.KS": "한국콜마",
        "241710.KQ": "코스메카코리아",           "051900.KS": "LG생건",
        "237820.KQ": "플레이디",             # 030960은 양지사(한국화장품=123690.KS 매핑 오류 삭제)
        "257720.KQ": "실리콘투",  # 실리콘웍스는 LX세미콘으로 사명변경(108320.KS)
        "003230.KS": "삼양식품",                  "097950.KS": "CJ제일제당",
        "004370.KS": "농심",                     "271560.KS": "오리온",
        "280360.KS": "롯데웰푸드",                "005300.KS": "롯데칠성",
        "007310.KS": "오뚜기",                   "005180.KS": "빙그레",
        "035080.KQ": "그래디언트",             "003550.KS": "LG",
        "178320.KQ": "서진시스템",            "032560.KS": "황금에스티",
        # ── 금융·밸류업 ────────────────────────────────────────────────────
        "105560.KS": "KB금융",                   "055550.KS": "신한지주",
        "086790.KS": "하나금융지주",               "316140.KS": "우리금융지주",
        "138040.KS": "메리츠금융지주",              "024110.KS": "기업은행",
        "323410.KS": "카카오뱅크",                 "071050.KS": "한국금융지주",
        "039490.KS": "키움증권",                   "005940.KS": "NH투자증권",
        "006800.KS": "미래에셋증권",                 "001450.KS": "현대해상",  # 현대차증권은 001500.KS
        "032830.KS": "삼성생명",                   "088350.KS": "한화생명",
        "000810.KS": "삼성화재",                   "005830.KS": "DB손해보험",
        # 000060 메리츠화재는 2022 메리츠금융지주(138040)로 흡수합병(매핑 삭제)
        # ── 콘텐츠·엔터 ────────────────────────────────────────────────────
        "352820.KS": "하이브",                    "035900.KQ": "JYP엔터",
        "041510.KQ": "SM엔터",                    "122870.KQ": "와이지엔터",
        "373200.KQ": "엑스플러스",                    "253450.KQ": "스튜디오드래곤",
        "035760.KQ": "CJ ENM",                    "067160.KQ": "SOOP",
        "462870.KS": "시프트업",                    "259960.KS": "크래프톤",
        "251270.KS": "넷마블",                    "036570.KS": "NC소프트",
        "263750.KQ": "펄어비스",                   "293490.KQ": "카카오게임즈",
        "112040.KQ": "위메이드",
        # ── 건설·건자재 ──────────────────────────────────────────────────
        "000720.KS": "현대건설",                    "375500.KS": "DL이앤씨",
        "006360.KS": "GS건설",                     "294870.KS": "IPARK현대산업개발",
        "028260.KS": "삼성물산",                    "028050.KS": "삼성E&A",
        "047040.KS": "대우건설",                    "000210.KS": "DL",
        "002380.KS": "KCC",                        # 006390 한일현대시멘트 매핑 미확인 삭제
        "004090.KS": "한국석유공업",                 "010780.KS": "아이에스동서",
        "003070.KS": "코오롱글로벌",                 "014820.KS": "동원시스템즈",
        # ── 철강·화학 ────────────────────────────────────────────────────
        "005490.KS": "POSCO홀딩스",                 "004020.KS": "현대제철",
        "001430.KS": "세아베스틸지주",               "058430.KS": "포스코스틸리온",                "002710.KS": "TCC스틸",
        "008350.KS": "남선알미늄",                   "011170.KS": "롯데케미칼",
        "011780.KS": "금호석유화학",                 "006120.KS": "SK디스커버리",
        "298000.KS": "효성화학",                     "069260.KS": "TKG휴켐스",
        "024060.KQ": "흥구석유",                     "003830.KS": "대한화섬",
        # ── 유틸리티·가스 ────────────────────────────────────────────────
        "017390.KS": "서울가스",                     "034590.KS": "인천도시가스",
        "004690.KS": "삼천리",                       # 005030 부산주공 상폐 제거
        "021240.KS": "코웨이",                       "015020.KS": "이스타코",
        "069960.KS": "현대백화점",                    "007070.KS": "GS리테일",
        # ── 추가 섹터 종목 ────────────────────────────────────────────
        "000500.KS": "가온전선",
        "000880.KS": "한화",
        "001500.KS": "현대차증권",
        "003350.KS": "한국화장품제조",
        "003540.KS": "대신증권",
        "004980.KS": "성신양회",
        "005810.KS": "풍산홀딩스",
        "005870.KS": "휴니드",
        "006340.KS": "대원전선",
        "007610.KS": "선도전기",
        "008770.KS": "호텔신라",
        "010170.KQ": "대한광통신",
        "010820.KS": "퍼스텍",
        "012510.KS": "더존비즈온",
        "014680.KS": "한솔케미칼",
        "016360.KS": "삼성증권",
        "017960.KS": "한국카본",
        "022100.KS": "포스코DX",
        "025540.KS": "한국단자공업",
        "031980.KQ": "피에스케이홀딩스",
        "036930.KQ": "주성엔지니어링",
        "039130.KS": "하나투어",
        "042510.KQ": "라온시큐어",
        "053030.KQ": "바이넥스",
        "053300.KQ": "한국정보인증",
        "053800.KQ": "안랩",
        "054450.KQ": "텔레칩스",
        "054940.KQ": "엑사이엔씨",
        "056190.KQ": "에스에프에이",
        "064960.KS": "SNT모티브",
        "065450.KQ": "빅텍",
        "066310.KQ": "큐에스아이",
        "066970.KS": "엘앤에프",
        "069080.KQ": "웹젠",
        "069620.KS": "대웅제약",
        "077970.KS": "STX엔진",
        "078150.KQ": "HB테크놀러지",
        "079160.KS": "CJ CGV",
        "082640.KS": "동양생명",
        "090360.KQ": "로보스타",
        "095700.KQ": "제넥신",
        "097230.KS": "HJ중공업",
        "099320.KQ": "쎄트렉아이",
        "105840.KS": "우진",
        "108490.KQ": "로보티즈",
        "110990.KQ": "디아이티",
        "115500.KQ": "케이씨에스",
        "117730.KQ": "티로보틱스",
        "125490.KQ": "한라캐스트",
        "168360.KQ": "펨트론",
        "175330.KS": "JB금융지주",
        "187790.KQ": "나노",
        "199820.KQ": "제일일렉트릭",
        "203650.KQ": "드림시큐리티",
        # 217190은 그린플러스(토비스=051360.KQ 매핑 오류 삭제)
        "237880.KQ": "클리오",
        "259630.KQ": "엠플러스",
        "272210.KS": "한화시스템",
        "274090.KQ": "켄코아에어로스페이스",
        # 279570 케이뱅크는 미상장(매핑 오류 삭제)
        "298380.KQ": "에이비엘바이오",
        "321370.KQ": "센서뷰",
        "322000.KS": "HD현대에너지솔루션",
        "323280.KQ": "태성",
        "348340.KQ": "뉴로메카",
        "352480.KQ": "씨앤씨인터내셔널",
        "361610.KS": "SK아이이테크놀로지",
        "365340.KQ": "성일하이텍",
        "377330.KQ": "이지트로닉스",
        "377480.KQ": "마음AI",
        "389500.KQ": "에스비비테크",
        "396270.KQ": "넥스트칩",
        "405100.KQ": "큐알티",
        "432720.KQ": "퀄리타스반도체",
        "437730.KQ": "삼현",
        "439260.KS": "대한조선",
        "443060.KS": "HD현대마린솔루션",
        "456010.KQ": "아이씨티케이",
        "456040.KS": "OCI",
        "459510.KQ": "나우로보틱스",
        "475150.KS": "SK이터닉스",
        "475560.KS": "더본코리아",
        "041830.KQ": "인바디",
        "302440.KS": "SK바이오사이언스",
        "336260.KS": "두산퓨얼셀",
        "377300.KS": "카카오페이",
        # 950160은 코오롱티슈진(코위버=056360.KQ 매핑 오류 삭제)
        "950170.KQ": "JTC",
        # ── 기타 ──────────────────────────────────────────────────────────
        "005070.KS": "코스모신소재",                  "139480.KS": "이마트",  # suffix .KQ→.KS
        "008060.KS": "대덕",
        "089590.KS": "제주항공",                  "095720.KS": "웅진씽크빅",
        # 006740은 영풍제지(바이오니아=064550.KQ), 004565는 무효(현대비앤지스틸=004560.KS) 매핑 오류 삭제
        "094360.KQ": "칩스앤미디어",                "032560.KS": "황금에스티",
        "424980.KQ": "마이크로투나노",              "049720.KQ": "고려신용정보",
        "003550.KS": "LG",
        # ── 사이버보안 ─────────────────────────────────────────────────────────
        "136540.KQ": "윈스테크넷",
        "150900.KQ": "파수",                    "488280.KQ": "S2W",
        "411080.KQ": "샌즈랩",
        # ── 우주·위성 ──────────────────────────────────────────────────────────
        "189300.KQ": "인텔리안테크",              "211270.KQ": "AP위성",
        "474170.KQ": "루미르",
        # ── 물류·유통 ──────────────────────────────────────────────────────────
        "002320.KS": "한진",                    "004140.KS": "동방",
        "009180.KS": "한솔로지스틱스",
        # ── 스마트팜·애그테크 ──────────────────────────────────────────────────
        "186230.KQ": "그린플러스",               "403490.KQ": "우듬지팜",
        "000490.KS": "대동",                    "054050.KQ": "NH농우바이오",
        # ── 디지털헬스·AI의료 ──────────────────────────────────────────────────
        "338220.KQ": "뷰노",                    "328130.KQ": "루닛",
        "032850.KQ": "비트컴퓨터",               "033230.KQ": "인성정보",
        # ── EV충전 ────────────────────────────────────────────────────────────
        "234300.KQ": "에스트래픽",               "462520.KS": "조선내화",
        "271940.KS": "일진하이솔루스",             "382900.KQ": "범한퓨얼셀",
        "120110.KS": "코오롱인더",
        # ── 2026-05 추가: 신규 섹터 종목 ─────────────────────────────────────
        "067310.KQ": "하나마이크론",        # HBM 후공정 패키징
        "084370.KQ": "유진테크",            # 반도체 CVD/ALD 장비
        "053610.KQ": "프로텍",              # 반도체 디스펜서
        "304100.KQ": "솔트룩스",            # 자연어AI·LLM
        "121850.KQ": "코이즈",            # 이차전지 자동화 장비
        "020150.KS": "롯데에너지머티리얼즈", # 동박(전지소재)
        "086900.KQ": "메디톡스",            # 보툴리눔톡신
        "008930.KS": "한미사이언스",        # 한미약품 지주
        "102710.KQ": "이엔에프테크놀로지",            # 신약·CDMO
        "092040.KQ": "아미코젠",            # 바이오효소·배지
        "138930.KS": "BNK금융지주",         # 부산·경남은행 지주
        "376300.KQ": "디어유",              # 팬덤플랫폼(버블)
        # ── 2026-05 2차 추가 ─────────────────────────────────────────────────
        "098460.KQ": "고영",                # 3D 검사장비
        "095610.KQ": "테스",                # PECVD 장비
        "218410.KQ": "RFHIC",               # RF 반도체(GaN)
        "067390.KQ": "아스트",                # OLED 증착장비
        "042940.KQ": "상지건설",          # 2차전지 부품(CAN)
        "263720.KQ": "디앤씨미디어",        # GLP-1 신약
        "137310.KS": "에스디바이오센서",    # 체외진단
        "014830.KS": "유니드",              # 가성칼륨 글로벌 1위
        "060280.KQ": "큐렉소",              # 의료수술로봇
        "005740.KS": "크라운해태홀딩스",          # 제과
        "086450.KQ": "동국제약",            # 제약(KR_NAMES 누락분 보강)
        # ── 2026-05 3차 추가: 오매핑 정정(올바른 코드로) + 신규 ─────────────
        "076080.KQ": "웰크론한텍",          # 산업용 보일러·환경설비
        "056360.KQ": "코위버",              # 통신장비(WDM)
        "051360.KQ": "토비스",              # 카지노/자동차 디스플레이
        "064550.KQ": "바이오니아",          # 진단키트·바이오소재
        "170790.KQ": "파이오링크",          # 네트워크 보안장비
        "005420.KS": "코스모화학",          # 이산화티타늄·황산코발트
        "123690.KS": "한국화장품",          # 화장품 OEM/ODM
        "004560.KS": "현대비앤지스틸",      # 스테인리스 냉연
        # 신규
        "064760.KQ": "티씨케이",            # SiC링·반도체부품
        "086900.KQ": "메디톡스",            # 보툴리눔톡신
        "095700.KQ": "제넥신",              # 면역항암·바이오신약
        "060720.KQ": "KH바텍",              # 스마트폰 힌지·메탈부품
        "122990.KQ": "와이솔",              # RF 듀플렉서·SAW필터
        "139130.KS": "iM금융지주",          # 구 DGB금융지주(2024 사명변경)
        # 076600 금강철강 매핑 미확인 — 삭제(국내 상장 미확인)
        # ── 2026-05 4차 추가: 유통·레저·소비재 + 화학·반도체 보강 ─────────
        "241560.KS": "두산밥캣",            # 소형건설장비 글로벌 1위
        "023530.KS": "롯데쇼핑",            # 유통(백화점·마트)
        "004170.KS": "신세계",              # 백화점·면세점
        "282330.KS": "BGF리테일",           # 편의점 CU
        "027410.KS": "BGF",                 # BGF리테일 지주
        "035250.KS": "강원랜드",            # 내국인 카지노
        "114090.KS": "GKL",                 # 외국인 카지노
        "010060.KS": "OCI홀딩스",           # 화학·태양광 지주
        "195870.KS": "해성디에스",          # 반도체 리드프레임
        "008730.KS": "율촌화학",            # 이차전지 파우치필름·포장재
        "339770.KS": "교촌에프앤비",        # 치킨 프랜차이즈
        "030190.KS": "NICE평가정보",        # 신용정보·평가
        "048410.KQ": "현대바이오",          # 항바이러스·신약개발
        "035600.KQ": "KG이니시스",          # 전자결제(PG)
        "089600.KQ": "KT나스미디어",        # 디지털광고 미디어렙(공식명)
        "037560.KS": "LG헬로비전",          # 케이블TV·MVNO
        # ── 2026-05 5차 추가: 방산·자동차·인프라·금융 보강 ───────────────
        "011210.KS": "현대위아",            # 자동차부품·공작기계
        "298020.KS": "효성티앤씨",          # 스판덱스 글로벌 1위(코드정정: 094280→298020)
        "005870.KS": "휴니드",  # 방산 전술통신(suffix정정: .KQ→.KS)
        "145990.KS": "삼양사",              # 식품·화학소재(설탕·전분당)
        # 003410 쌍용C&E는 2024 한앤컴퍼니 상장폐지(매핑 삭제)
        "011930.KS": "신성이엔지",          # 반도체 클린룸·태양광
        # 015350 부산가스 FDR 미확인(상폐 의심) — 매핑 삭제
        # ── 팹리스·AI반도체 보강 ──────────────────────────────────────────
        "394280.KQ": "오픈엣지테크놀로지",  # AI 반도체 IP·뉴럴 프로세서
        "361390.KQ": "모빌린트",            # 엣지AI 비전 반도체
        "102120.KQ": "어보브반도체",        # MCU 팹리스
        "123860.KQ": "아나패스",            # Display IC 팹리스
        "029780.KS": "삼성카드",            # 신용카드
        "053450.KQ": "세코닉스",            # 차량용 카메라 렌즈
        "047310.KQ": "파워로직스",          # 카메라모듈·배터리보호회로
        "002990.KS": "금호건설",            # 중견 건설
        "005810.KS": "풍산홀딩스",          # 동제련·방산탄약 지주
        # ── 2026-05 6차 추가: kr_sectors에 있으나 KR_NAMES 누락이던 20종 (FDR 확인) ───
        "002790.KS": "아모레퍼시픽홀딩스",   # 화장품 지주
        "003090.KS": "대웅",                # 제약 지주
        "032620.KQ": "GC메디아이",          # 의료영상 솔루션
        "039030.KQ": "이오테크닉스",        # 반도체 레이저 마커
        "064290.KQ": "인텍플러스",          # 반도체 외관검사
        "078340.KQ": "컴투스",              # 모바일 게임
        "095660.KQ": "네오위즈",            # 게임 퍼블리셔
        "104830.KQ": "원익머트리얼즈",      # 반도체 특수가스
        "108320.KS": "LX세미콘",            # 디스플레이 구동IC
        "108860.KQ": "셀바스AI",            # AI 음성·필기 인식
        "112290.KQ": "와이씨켐",            # 반도체 포토케미컬
        "140860.KQ": "파크시스템스",        # 원자현미경(AFM)
        "192080.KS": "더블유게임즈",        # 소셜카지노 게임
        "194480.KQ": "데브시스터즈",        # 쿠키런 게임
        "225570.KQ": "넥슨게임즈",          # 넥슨 자회사 게임
        "228760.KQ": "지노믹트리",          # 분자진단(대장암)
        "272290.KQ": "이녹스첨단소재",      # 반도체·디스플레이 소재
        "317330.KQ": "덕산테코피아",        # OLED·반도체 소재
        "357550.KQ": "석경에이티",          # 세라믹·치과재료
        "950140.KQ": "잉글우드랩",          # 화장품 ODM(미국법인)
        # ── 2026-05 바이오 추가: kr_sectors 추가분 (yfinance 확인) ────────────
        "302440.KQ": "이뮨온시아",            # 면역항암 바이오
        "048530.KQ": "인투셀",                # ADC 항체약물접합체
        "950200.KQ": "파멥신",                # ADC·이중항체 바이오
        "389030.KQ": "지니너스",              # 유전체분석·진단
        # ── 2026-05 7차 추가: 시총 상위 누락 대형주 보강 (FDR 확인) ───────────
        "402340.KS": "SK스퀘어",            # SK ICT 투자 지주
        "034730.KS": "SK",                  # SK그룹 지주
        "000150.KS": "두산",                # 두산그룹 지주
        "033780.KS": "KT&G",                # 담배·인삼공사
        "086280.KS": "현대글로비스",        # 물류
        "010950.KS": "S-Oil",               # 정유
        "064400.KS": "LG씨엔에스",          # LG IT서비스
        "180640.KS": "한진칼",              # 한진그룹 지주
        "267270.KS": "HD건설기계",      # 건설기계
        "078930.KS": "GS",                  # GS그룹 지주
        "353200.KS": "대덕전자",            # PCB
        "001040.KS": "CJ",                  # CJ그룹 지주
        "088980.KS": "맥쿼리인프라",        # 인프라 펀드
        "310210.KQ": "보로노이",            # 표적치료제 신약
        "111770.KS": "영원무역",            # 아웃도어 OEM
        "004800.KS": "효성",                # 효성그룹 지주
        "489790.KS": "한화비전",            # 영상감시(CCTV)
        "017800.KS": "현대엘리베이터",      # 엘리베이터
        "131970.KQ": "두산테스나",          # 반도체 후공정 테스트
        "214450.KQ": "파마리서치",          # 리쥬란·필러
        "226950.KQ": "올릭스",              # RNAi 신약
        "005850.KS": "에스엘",              # 자동차 램프
        "383220.KS": "F&F",                 # 패션(MLB·디스커버리)
        "457190.KS": "이수스페셜티케미컬",  # 정밀화학·전고체전해질
        "007340.KS": "DN오토모티브",        # 자동차 배터리·부품
        "001720.KS": "신영증권",            # 증권
        "319660.KQ": "피에스케이",          # 반도체 PR스트립·세정
        "012750.KS": "에스원",              # 보안서비스
        "004990.KS": "롯데지주",            # 롯데그룹 지주
        "009970.KS": "영원무역홀딩스",      # 영원무역 지주
        "009240.KS": "한샘",                # 가구·인테리어
        # 7차 보강 (시총 상위 추가 누락)
        "000080.KS": "하이트진로",          # 주류
        "000370.KS": "한화손해보험",        # 손해보험
        "001120.KS": "LX인터내셔널",        # 종합상사
        "001740.KS": "SK네트웍스",          # 종합상사·렌터카
        "005440.KS": "현대지에프홀딩스",    # 현대그린푸드 지주
        "005720.KS": "넥센",                # 화학·타이어
        "006650.KS": "대한유화",            # 석유화학
        "009410.KS": "태영건설",            # 건설
        "009450.KS": "경동나비엔",          # 보일러
        "009470.KS": "삼화전기",            # 전해콘덴서
        "030000.KS": "제일기획",            # 광고대행
        "030210.KS": "다올투자증권",        # 증권
        "035000.KS": "HS애드",              # 광고
        "044820.KS": "코스맥스비티아이",    # 화장품 지주
        "093370.KS": "후성",                # 특수가스·전해질
        "120030.KS": "조선선재",            # 용접재료
        "272450.KS": "진에어",              # 저비용항공
        "298050.KS": "HS효성첨단소재",      # 타이어코드·탄소섬유
        "383310.KQ": "에코프로에이치엔",    # 환경·소재
        "021040.KQ": "대호특수강",          # 특수강
        # ── 2026-05 8차 추가: 핫 종목 보강 ───────────────────────────────────
        "083450.KQ": "GST",                  # 반도체 테스트소켓
        "170920.KQ": "엘티씨",              # 반도체 식각부품
        "327260.KQ": "RF머트리얼즈",        # RF소재·5G부품
        # ── 2026-05 인기종목 보강 (네이버 시총상위 128) ──────────────────────
        "031210.KS": "서울보증보험", "003690.KS": "코리안리",
        "026960.KS": "동서", "000240.KS": "한국앤컴퍼니",
        "279570.KS": "케이뱅크", "081660.KS": "미스토홀딩스",
        "085620.KS": "미래에셋생명", "007810.KS": "코리아써키트",
        "023590.KS": "다우기술", "395400.KS": "SK리츠",
        "001800.KS": "오리온홀딩스", "006040.KS": "동원산업",
        "032350.KS": "롯데관광개발", "020560.KS": "아시아나항공",
        "003530.KS": "한화투자증권", "030610.KS": "교보증권",
        "073240.KS": "금호타이어", "004000.KS": "롯데정밀화학",
        "012630.KS": "HDC", "034230.KS": "파라다이스",
        "006110.KS": "삼아알미늄", "950160.KQ": "코오롱티슈진",
        "440110.KQ": "파두", "319400.KQ": "현대무벡스",
        "100790.KQ": "미래에셋벤처투자", "032820.KQ": "우리기술",
        "043260.KQ": "성호전자", "347700.KQ": "스피어",
        "078600.KQ": "대주전자재료", "082920.KQ": "비츠로셀",
        "068760.KQ": "셀트리온제약", "140410.KQ": "메지온",
        "027360.KQ": "아주IB투자", "476830.KQ": "알지노믹스",
        "060370.KQ": "LS마린솔루션", "031330.KQ": "에스에이엠티",
        "101490.KQ": "에스앤에스텍", "290650.KQ": "엘앤씨바이오",
        "420770.KQ": "기가비스", "183300.KQ": "코미코",
        "096530.KQ": "씨젠", "039200.KQ": "오스코텍",
        "445680.KQ": "큐리옥스바이오시스템즈", "295310.KQ": "에이치브이엠",
        "090710.KQ": "휴림로봇", "232140.KQ": "와이씨",
        "417200.KQ": "LS머트리얼즈", "475830.KQ": "오름테라퓨틱",
        "038500.KQ": "삼표시멘트", "491000.KQ": "리브스메드",
        "089970.KQ": "브이엠", "003380.KQ": "하림지주",
        "085660.KQ": "차바이오텍", "195940.KQ": "HK이노엔",
        "458870.KQ": "씨어스", "490470.KQ": "세미파이브",
        "065350.KQ": "신성델타테크", "281740.KQ": "레이크머티리얼즈",
        "033790.KQ": "피노", "204270.KQ": "제이앤티씨",
        "439960.KQ": "코스모로보틱스", "388720.KQ": "유일로보틱스",
        "127120.KQ": "제이에스링크", "161580.KQ": "필옵틱스",
        "124500.KQ": "아이티센글로벌", "388210.KQ": "씨엠티엑스",
        "160190.KQ": "하이젠알앤엠", "036830.KQ": "솔브레인홀딩스",
        "007390.KQ": "네이처셀", "213420.KQ": "덕산네오룩스",
        "466100.KQ": "클로봇", "052020.KQ": "에스티큐브",
        "456160.KQ": "지투지바이오", "222080.KQ": "씨아이에스",
        "126340.KQ": "비나텍", "115180.KQ": "큐리언트",
        "056080.KQ": "유진로봇", "094170.KQ": "동운아나텍",
        "376900.KQ": "로킷헬스케어", "074600.KQ": "원익QnC",
        "050890.KQ": "쏠리드", "476060.KQ": "온코닉테라퓨틱스",
        "037460.KQ": "삼지전자", "023160.KQ": "태광",
        "052400.KQ": "코나아이", "046890.KQ": "서울반도체",
        "032190.KQ": "다우데이타", "121600.KQ": "나노신소재",
        "171090.KQ": "선익시스템", "044490.KQ": "태웅",
        "092190.KQ": "서울바이오시스", "006730.KQ": "서부T&D",
        "348370.KQ": "엔켐", "077360.KQ": "덕산하이메탈",
        "009520.KQ": "포스코엠텍", "174900.KQ": "앱클론",
        "358570.KQ": "지아이이노베이션", "199800.KQ": "툴젠",
        "019210.KQ": "와이지-원", "425420.KQ": "티에프이",
        "102940.KQ": "코오롱생명과학", "060250.KQ": "NHN KCP",
        "252990.KQ": "샘씨엔에스", "399720.KQ": "가온칩스",
        "089890.KQ": "코세스", "336570.KQ": "원텍",
        "389470.KQ": "인벤티지랩", "033160.KQ": "엠케이전자",
        "368770.KQ": "파이버프로", "253590.KQ": "네오셈",
        "348210.KQ": "넥스틴", "015750.KQ": "성우하이텍",
        "033500.KQ": "동성화인텍", "024850.KQ": "HLB이노베이션",
        "078160.KQ": "메디포스트", "089010.KQ": "켐트로닉스",
        "486990.KQ": "노타", "475960.KQ": "토모큐브",
        "460930.KQ": "현대힘스", "214430.KQ": "아이쓰리시스템",
        "354320.KQ": "알멕", "372320.KQ": "큐로셀",
        "332570.KQ": "PS일렉트로닉스", "041960.KQ": "코미팜",
        "122640.KQ": "예스티", "448900.KQ": "한국피아이엠",
        "493280.KQ": "아이엠바이오로직스", "093320.KQ": "케이아이엔엑스",
        "457370.KQ": "한켐",               # 반도체·디스플레이 전자소재
        # ── 2026-05 9차 추가: 섹터 보강 신규 종목 ───────────────────────────
        "030520.KQ": "한글과컴퓨터",       # AI오피스SW
        "402030.KQ": "코난테크놀로지",     # AI자연어처리
        "315640.KQ": "딥노이드",           # AI의료영상
        "119650.KS": "가온전선",           # 전력케이블
        "094820.KQ": "비나텍",             # 슈퍼커패시터
        "302380.KS": "파워로직스",         # DC-DC컨버터 (302380.KS, 전력기기)
        "094850.KQ": "아이쓰리시스템",     # 적외선센서·방산
        "048830.KQ": "세일전자",           # 군용통신장비
        "073010.KQ": "케이에스피",         # 선박축계부품
        "006370.KS": "대한조선",           # 중형조선소
        "003850.KS": "보령",               # 제약(카나브)
        "170900.KS": "동아에스티",         # 제약(DA-5512)
        "365550.KQ": "오가노이드사이언스", # 오가노이드플랫폼
        "380440.KQ": "뉴로메카",           # 협동로봇
        "270660.KQ": "에브리봇",           # 가정용청소로봇
        "018290.KQ": "브이티",             # 리들샷마스크
        "439090.KQ": "마녀공장",           # 비건뷰티
        "000060.KS": "메리츠화재",         # 손해보험·밸류업
        "041140.KQ": "넥슨지티",           # 캐주얼게임
        "014620.KS": "성우하이텍",         # 자동차차체부품
    }



    # ─────────────────────────────────────────────────────────────────────
    # KR_DESC — 국내 종목 한줄 설명 (Name 컬럼 옆에 표시)
    # ─────────────────────────────────────────────────────────────────────
    KR_DESC: dict[str, str] = {
        # 반도체
        "000660.KS": "HBM·낸드", "005930.KS": "메모리·파운드리",
        "042700.KS": "TC본더장비", "000990.KS": "8인치파운드리",
        "058470.KQ": "테스트소켓", "095340.KQ": "IC테스트소켓",
        "403870.KQ": "고압어닐장비", "357780.KQ": "CMP소재", "083450.KQ": "테스트소켓",
        "170920.KQ": "반도체식각부품", "327260.KQ": "RF소재·5G",
        "011070.KS": "기판·전장카메라모듈", "009150.KS": "MLCC·반도체기판",
        "281820.KS": "CMP슬러리·세정", "005290.KQ": "포토레지스트",
        "036930.KQ": "CVD장비", "240810.KQ": "ALD장비",
        # AI 인프라
        "035420.KS": "하이퍼클로바X·검색", "035720.KS": "카카오AI·플랫폼",
        "018260.KS": "삼성SDS클라우드", "022100.KS": "AI데이터플랫폼",
        "012510.KS": "ERP·클라우드", "053800.KQ": "EDR·보안관제",
        # 전력 인프라
        "010120.KS": "변압기·배전반", "267260.KS": "변압기수출1위",
        "062040.KS": "중소형변압기", "033100.KQ": "변류기CT",
        "298040.KS": "중대형변압기·수소충전소", "103590.KS": "전력케이블·변압기",
        "034020.KS": "원전EPC·SMR", "052690.KS": "원전설계",
        "015760.KS": "원전·전력공기업", "336260.KS": "수소연료전지",
        "112610.KS": "해상풍력타워", "009830.KS": "태양광모듈",
        "234300.KQ": "EV급속충전운영", "271940.KS": "수소저장탱크",
        "382900.KQ": "선박PEMFC", "120110.KS": "수소MEA소재",
        # 방산
        "012450.KS": "K9자주포·천무로켓", "064350.KS": "K2전차·레드백IFV",
        "047810.KS": "KF-21·위성체제조", "079550.KS": "유도무기·레이더",
        "042660.KS": "해군함정·잠수함", "272210.KS": "전자전·C4ISR",
        "103140.KS": "탄약·동제련", "099320.KQ": "소형위성SAR",
        # 조선·해운
        "329180.KS": "LNG·암모니아선건조", "009540.KS": "조선빅3지주",
        "010140.KS": "LNG선·드릴십", "082740.KS": "선박저속엔진",
        "071970.KS": "선박중속엔진", "443060.KS": "선박A/S·솔루션",
        "011200.KS": "컨테이너해운1위",
        # 이차전지
        "373220.KS": "배터리셀·ESS", "006400.KS": "배터리셀P5·전고체",
        "247540.KQ": "양극재NCA811", "086520.KQ": "에코프로그룹지주",
        "003670.KS": "양극재·리튬정제", "066970.KS": "양극재NCMA",
        "450080.KS": "전구체·양극재소재", "278280.KQ": "전해질LiFSI",
        "051910.KS": "배터리소재·화학", "361610.KS": "분리막SKIET",
        # 바이오·헬스케어
        "068270.KS": "바이오시밀러·CMO", "207940.KS": "바이오위탁CMO",
        "196170.KQ": "알부민융합기술ADC", "141080.KQ": "ADC링커플랫폼",
        "087010.KQ": "GLP-1서방형펩타이드", "128940.KS": "경구GLP-1비만",
        "214370.KQ": "GLP-1펩타이드소재", "028300.KQ": "리보세라닙항암",
        "000100.KS": "렉라자·폐암신약", "145720.KS": "임플란트해외수출",
        "214150.KQ": "HIFU미용기기", "145020.KQ": "보툴리눔톡신",
        "338220.KQ": "AI의료영상진단", "328130.KQ": "AI암병리진단",
        "043150.KQ": "치과X선CT", "041830.KQ": "체성분분석InBody",
        # 로봇·자동화
        "454910.KS": "협동로봇·자율주행AMR", "277810.KQ": "휴머노이드·보행재활",
        "455900.KQ": "착용형재활로봇", "348340.KQ": "협동로봇indy",
        "005380.KS": "완성차·수소차", "000270.KS": "EV·SUV글로벌",
        "012330.KS": "자동차모듈·부품", "307950.KS": "차량SW·OTA",
        "204320.KS": "ADAS·조향·제동",
        # K-소비재
        "278470.KS": "메디큐브·에이지알뷰티", "192820.KS": "ODM화장품1위",
        "090430.KS": "설화수·이니스프리", "051900.KS": "후·숨37",
        "003230.KS": "불닭볶음면수출", "097950.KS": "식품·바이오소재",
        "004370.KS": "신라면글로벌", "271560.KS": "초코파이·포카칩",
        "483650.KS": "달바·비건뷰티",
        # 금융·밸류업
        "105560.KS": "KB국민은행지주", "055550.KS": "신한금융지주",
        "086790.KS": "하나금융지주", "138040.KS": "메리츠화재·금융지주",
        "323410.KS": "인터넷전문은행", "039490.KS": "HTS·주식거래1위",
        "006800.KS": "글로벌투자증권", "000810.KS": "손해보험1위",
        "377300.KS": "간편결제·핀테크",
        # 콘텐츠·엔터
        "352820.KS": "BTS·뉴진스소속사", "035900.KQ": "StrayKids·TWICE소속",
        "041510.KQ": "aespa·EXO소속사", "122870.KQ": "BLACKPINK·트레저",
        "259960.KS": "배틀그라운드·인조이", "462870.KS": "NIKKE·승리의여신",
        "036570.KS": "리니지·TL온라인", "293490.KQ": "오딘·카카오게임",
        # 사이버보안
        "136540.KQ": "IPS·IDS네트워크보안",
        "150900.KQ": "문서DRM보안", "488280.KQ": "다크웹AI위협분석",
        "411080.KQ": "AI사이버위협인텔",
        # 우주·위성
        "099320.KQ": "소형위성SAR제조", "189300.KQ": "저궤도위성안테나",
        "211270.KQ": "위성통신단말기", "474170.KQ": "초소형위성루미르",
        "274090.KQ": "항공기구조물·발사체",
        # 건설·철강·화학
        "000720.KS": "아파트·해외플랜트", "005490.KS": "철강·리튬·이차전지",
        "004020.KS": "고로일관제철", "051910.KS": "배터리소재·화학",
        "011170.KS": "에틸렌·기초화학",
        # 반도체 추가
        "007660.KS": "고다층PCB·AI서버기판",  # 이수페타시스
        "110990.KQ": "반도체번인·메모리검사",  # 디아이티
        "166090.KQ": "실리콘링·식각소재",       # 하나머티리얼즈
        "168360.KQ": "반도체소재지주",
        "031980.KQ": "에싱·건식세정장비",       # 피에스케이
        "033640.KQ": "팬아웃WLP패키징",         # 네패스아크
        "054450.KQ": "차량용AP·SoC팹리스",
        "080220.KQ": "반도체전구체·화학소재",
        "089030.KQ": "반도체테스트핸들러",       # 테크윙
        "200710.KQ": "반도체패키징소재",
        "396270.KQ": "IC테스트소켓·인서트",
        "014680.KS": "과산화수소·반도체소재",   # 한솔케미칼
        "036540.KQ": "반도체후공정·패키징",      # SFA반도체
        "036810.KQ": "BMS·배터리보호회로",
        "178920.KS": "PI필름·배터리절연소재",
        "950170.KQ": "반도체후공정소재",
        "011790.KS": "FC-BGA기판·동박",          # SKC
        "131290.KQ": "반도체식각액·세정소재",   # 이엔에프테크놀로지
        "222800.KQ": "메모리모듈기판",            # 심텍
        # 온디바이스AI·통신
        "052710.KQ": "EMI필터·안테나모듈",       # 아모텍
        "323280.KQ": "5G중계기·통신장비",         # 쏠리드
        "377480.KQ": "온디바이스AI부품",
        "405100.KQ": "AI엣지컴퓨팅모듈",
        "432720.KQ": "온디바이스AI반도체",
        "010170.KQ": "머신비전카메라·의료영상",  # 뷰웍스
        "017670.KS": "5G무선통신서비스",           # SK텔레콤
        "030200.KS": "통신·클라우드·미디어",     # KT
        "032640.KS": "통신·인터넷·IPTV",          # LG유플러스
        "084730.KQ": "광통신장비·부품",
        "187790.KQ": "광케이블·광섬유제조",
        # 전력 인프라 추가
        "025540.KS": "배전반·전력기기",
        "199820.KQ": "전력기기·전선소재",
        "000500.KS": "가온전선·전력케이블",
        "001440.KS": "초고압전력케이블",           # 대한전선
        "006260.KS": "전선·전력기기지주",           # LS
        "006340.KS": "전력케이블·전선",
        "007610.KS": "전선·배전기자재",
        "229640.KS": "전선케이블수출",               # LS에코에너지
        "051600.KS": "원전정비·발전설비",           # 한전KPS
        "083650.KQ": "원전기자재·냉각기",
        "105840.KS": "원전계측제어",
        "096770.KS": "배터리·정유·화학",            # SK이노베이션
        "322000.KS": "해상풍력타워·부품",           # 씨에스윈드
        "456040.KS": "ESS·태양광인버터",
        "475150.KS": "해상풍력·신재생에너지",
        "462520.KS": "EV충전·수소연료전지",
        # 방산 추가
        "000880.KS": "한화지주·방산·항공",
        "064960.KS": "방산부품·총기모터",           # SNT모티브
        "005810.KS": "탄약·발사체부품",              # 퍼스텍
        "005870.KS": "방산전자·EMP방호",              # 빅텍
        "010820.KS": "방산부품·전자전",
        "065450.KQ": "항공전자·방산부품",
        "321370.KQ": "드론·무인기부품",
        "377330.KQ": "소형위성·우주발사",
        "437730.KQ": "항공기부품·알루미늄단조",    # 켄코아에어로스페이스
        # 조선 추가
        "097230.KS": "중소형선박·해양구조물",
        "439260.KS": "해양플랜트·선박",
        "009070.KS": "LNG단열재·탄소소재",          # 한국카본
        "017960.KS": "선박기자재·의장품",
        "077970.KS": "선박·항공엔진",
        "000120.KS": "택배·종합물류",                  # CJ대한통운
        "005880.KS": "벌크해운·물류",
        "014160.KS": "해운·항만물류",
        # 배터리 추가
        "005070.KS": "배터리재료·화학",
        "137400.KQ": "식품기계·배터리장비",
        "259630.KQ": "귀금속회수·배터리리사이클",
        "365340.KQ": "배터리검사장비",
        "372170.KQ": "배터리장비·자동화",
        # 바이오 추가
        "298380.KQ": "항암신약개발",
        "326030.KS": "뇌전증신약·세노바메이트",    # SK바이오팜
        "006280.KS": "혈액제제·백신CMO",              # GC녹십자
        "053030.KQ": "siRNA·진단키트",                  # 바이오니아
        "185750.KS": "CMO·원료의약품",
        "302440.KS": "백신CMO·바이오",                  # SK바이오사이언스
        "950210.KS": "바이오CDMO·위탁생산",              # 프레스티지바이오파마
        "417840.KQ": "반도체장비·소재",                  # 저스템
        "017810.KS": "LPG유통·에너지",                  # E1
        "005090.KS": "집단에너지·발전",                  # SGC에너지
        "009160.KS": "프레스·산업기계",                  # SIMPAC
        "222040.KQ": "항체CDMO·위탁생산",               # 바이넥스
        "011760.KS": "종합상사·물류",                    # 현대코퍼레이션
        "069620.KS": "나보타보툴리눔·신약",            # 대웅제약
        "095700.KQ": "바이오CMO·원료의약품",
        "039840.KQ": "환자모니터·제세동기",
        "059090.KQ": "체외진단·감염검사키트",
        # 로봇 추가
        "056190.KQ": "스마트팩토리·디스플레이자동화",
        "058610.KQ": "산업자동화·로봇부품",
        "090360.KQ": "물류자동화·로봇시스템",
        "108490.KQ": "서보모터·로봇부품",              # 로보티즈
        "117730.KQ": "우주로봇·서비스로봇",
        "125490.KQ": "로봇관절·구동모듈",
        "389500.KQ": "휴머노이드부품·액추에이터",
        "459510.KQ": "로봇부품·정밀기계",
        # K-소비재 추가
        "003350.KS": "화장품브랜드·OEM",
        "237880.KQ": "K뷰티멀티브랜드",               # 클리오
        "352480.KQ": "K뷰티ODM·제조",
        "005180.KS": "빙과·메로나·음료",               # 빙그레
        "007310.KS": "카레·케찹·HMR",                   # 오뚜기
        "280360.KS": "식품·제과프랜차이즈",
        "475560.KS": "외식프랜차이즈",
        "003490.KS": "국제항공·화물운송",               # 대한항공
        "008770.KS": "신라면세점·호텔",                 # 호텔신라
        "039130.KS": "여행패키지·면세점",               # 하나투어
        "079160.KS": "멀티플렉스영화관",                # CJ CGV
        # 금융 추가
        "024110.KS": "IBK중소기업은행",
        "071050.KS": "카카오뱅크지주·증권",
        "175330.KS": "JB지방은행지주",
        # 279570 DGB대구은행지주 매핑 오류 삭제(DGB금융지주=139130.KS)
        "316140.KS": "우리금융지주",
        "001500.KS": "현대차그룹금융증권",
        "003540.KS": "리테일증권·자산운용",             # 대신증권
        "005940.KS": "NH농협투자증권",
        "016360.KS": "삼성증권·자산관리",
        "001450.KS": "현대해상손해보험",
        "005830.KS": "DB손해보험",
        "032830.KS": "삼성생명보험",
        "082640.KS": "동양생명보험",
        "088350.KS": "한화생명보험",
        # 콘텐츠·엔터 추가
        "035760.KQ": "CJ ENM오쇼핑·엠넷",
        "253450.KQ": "드라마제작스튜디오드래곤",
        "069080.KQ": "뮤온라인·전략모바일RPG",          # 웹젠
        "112040.KQ": "위믹스블록체인·게임",              # 위메이드
        "251270.KS": "세븐나이츠·모바일게임",           # 넷마블
        "263750.KQ": "검은사막·붉은사막",                # 펄어비스
        # 사이버보안·양자컴퓨팅 추가
        "042510.KQ": "모바일인증·생체인증보안",
        "203650.KQ": "인증·접근제어보안",
        "053300.KQ": "VPN·제로트러스트보안",
        "054940.KQ": "양자암호·네트워크보안",
        "115500.KQ": "공동인증서·전자서명",
        "456010.KQ": "양자보안·암호인증",
        "066310.KQ": "차량인포테인먼트·디스플레이",
        "078150.KQ": "PCB광학검사·반도체검사",
        # 217190(그린플러스)·950160(코오롱티슈진) 양자 분류 매핑 오류 삭제
        # 건설 추가
        "000210.KS": "DL화학·건설지주",
        "006360.KS": "GS건설·플랜트",
        "028050.KS": "삼성엔지니어링",
        "028260.KS": "삼성물산건설·패션",
        "047040.KS": "대우건설",
        "294870.KS": "IPARK현대산업개발",
        "375500.KS": "DL이앤씨플랜트",
        "002380.KS": "KCC페인트·창호",
        "003070.KS": "건설·유통복합",
        "004090.KS": "배관소재·건자재",
        "004980.KS": "성신양회시멘트",
        "010780.KS": "환경·건설복합",
        "014820.KS": "알루미늄캔·포장재",
        # 철강·화학 추가
        "001230.KS": "후판·봉형강철강",
        "001430.KS": "특수강봉강",
        "002710.KS": "동박·동합금소재",
        "008350.KS": "알루미늄가공소재",
        "010130.KS": "아연·귀금속제련",
        "058430.KS": "희소금속·포스코계열",
        "003830.KS": "에틸렌·PE기초화학",
        "006120.KS": "바이오·화학지주",
        "011780.KS": "합성고무·페놀화학",
        "024060.KQ": "염화칼리·특수화학",
        "069260.KS": "엔지니어링플라스틱",
        "298000.KS": "스판덱스·나일론섬유",
        # 유틸리티·가스 추가
        "004690.KS": "도시가스공급",
        # 005030 부산주공 상폐 제거
        "017390.KS": "서울도시가스",
        "018670.KS": "LPG수입·공급",
        "034590.KS": "인천도시가스",
        "036460.KS": "LNG수입·공급공기업",
        "007070.KS": "편의점GS25·슈퍼",
        "015020.KS": "인테리어건자재리테일",
        "021240.KS": "정수기·공기청정기렌탈",
        "069960.KS": "백화점·아울렛리테일",
        # 물류·유통 추가
        "002320.KS": "택배·종합물류그룹",
        "004140.KS": "항만하역·물류",
        "009180.KS": "육상물류·화물",
        "267250.KS": "자동차·중공업물류",
        "005300.KS": "백화점·마트·이커머스",
        "035080.KQ": "이커머스·여행예약",
        # 스마트팜·디지털헬스 추가
        "000490.KS": "트랙터·농기계ICT",
        "054050.KQ": "스마트온실·수직농장",
        "186230.KQ": "농업바이오·스마트팜",
        "403490.KQ": "수직농장·식물공장",
        "099190.KQ": "디지털의료솔루션",
        "032620.KQ": "EMR·의료정보시스템",
        "032850.KQ": "병원IT·의료정보",
        "033230.KQ": "IT통합·원격의료",
        # ── 2026-05 누락 보강: kr_sectors에는 있으나 KR_DESC 미수록이던 종목 ───
        # 화장품·소비재
        "002790.KS": "화장품 지주사", "161890.KS": "한국콜마·화장품 ODM",
        "123690.KS": "한국화장품·ODM", "241710.KQ": "화장품 ODM·코스메카",
        # 제약·바이오
        "003090.KS": "대웅제약 지주", "008930.KS": "한미약품 지주",
        "086450.KQ": "동국제약·미용필러", "086900.KQ": "메디톡스·보툴리눔",
        "092040.KQ": "효소·바이오 소재", "228760.KQ": "지노믹트리·분자진단",
        "064550.KQ": "바이오니아·분자진단·올리고",
        "137310.KS": "에스디바이오센서·진단", "060280.KQ": "큐렉소·수술 로봇",
        "357550.KQ": "치과 소재·바이오",
        # 유통·소비
        "004170.KS": "신세계 백화점·면세", "023530.KS": "롯데쇼핑·백화점",
        "282330.KS": "BGF리테일·CU 편의점", "339770.KS": "교촌치킨",
        "005740.KS": "크라운해태 지주", "035250.KS": "강원랜드 카지노",
        "114090.KS": "GKL 외국인 카지노",
        # 반도체·디스플레이
        "039030.KQ": "이오테크닉스·레이저 어닐링",
        "053610.KQ": "프로텍·반도체 디스펜서",
        "064290.KQ": "인텍플러스·외관검사",
        "064760.KQ": "티씨케이·SiC링",
        "067310.KQ": "하나마이크론·후공정",
        "084370.KQ": "유진테크·ALD 장비",
        "095610.KQ": "테스·식각·증착",
        "098460.KQ": "고영·3D 검사장비",
        "102710.KQ": "이엔에프테크·식각액",
        "104830.KQ": "원익머트·특수가스",
        "108320.KS": "LX세미콘·DDI 팹리스",
        "112290.KQ": "와이씨켐·EUV PR 소재",
        "195870.KS": "해성디에스·리드프레임",
        "272290.KQ": "이녹스첨단소재·OLED",
        "317330.KQ": "덕산테코피아·OLED",
        # 통신·네트워크·RF
        "056360.KQ": "코위버·광전송장비",
        "122990.KQ": "와이솔·RF SAW필터",
        "218410.KQ": "RFHIC·GaN 트랜지스터",
        "170790.KQ": "파이오링크·ADC·보안",
        # 게임·콘텐츠
        "078340.KQ": "컴투스 모바일 게임",
        "095660.KQ": "네오위즈 퍼블리싱",
        "194480.KQ": "데브시스터즈·쿠키런",
        "225570.KQ": "넥슨게임즈",
        "263720.KQ": "디앤씨미디어·웹소설",
        "376300.KQ": "디어유·팬 메시지",
        # 소재·화학·산업재
        "004560.KS": "현대비앤지스틸·스테인리스",
        "005420.KS": "코스모화학·이산화티타늄",
        "008060.KS": "대덕·PCB",
        "008730.KS": "율촌화학·포장재",
        "010060.KS": "OCI홀딩스·폴리실리콘 지주",
        "014830.KS": "유니드·가성칼륨",
        "020150.KS": "롯데에너지머티·동박",
        "042940.KQ": "상지건설·시공",
        "051360.KQ": "토비스·카지노 모니터",
        "067390.KQ": "아스트·항공기 동체",
        "121850.KQ": "코이즈·광학필름",
        "140860.KQ": "파크시스템스·원자현미경",
        # 금융
        "138930.KS": "BNK금융지주",
        "139130.KS": "iM금융지주",
        # AI·소프트웨어
        "108860.KQ": "셀바스AI·음성·필기",
        "304100.KQ": "솔트룩스·자연어처리",
        # 기타 / 미상
        "192080.KQ": "더존비즈온·B2B SaaS",
        "950140.KS": "JINKO 솔라 KDR",
        # ── 누락 보강 (종목 설명 공란 해소) ──────────────────────
        # 반도체·디스플레이·IT
        "393890.KQ": "2차전지분리막", "032500.KQ": "5G RF·통신장비",
        "054620.KQ": "디스플레이·반도체지주", "086960.KQ": "임베디드SW·차량SW",
        "034220.KS": "OLED패널", "066570.KS": "가전·전장부품",
        "030530.KQ": "반도체소재지주", "039440.KQ": "반도체CCSS·장비",
        "094360.KQ": "비디오IP반도체", "424980.KQ": "MEMS센서·소켓",
        "178320.KQ": "통신·ESS함체", "060720.KQ": "폴더블힌지",
        "053450.KQ": "차량용카메라렌즈", "047310.KQ": "배터리보호회로·카메라모듈",
        "131970.KQ": "반도체웨이퍼테스트", "319660.KQ": "반도체세정·스트립장비",
        "097520.KS": "차량·모바일카메라모듈", "353200.KS": "반도체기판FCBGA",
        "067160.KQ": "라이브스트리밍", "373200.KQ": "디스플레이공정장비",
        "356860.KQ": "메모리모듈", "309930.KQ": "세라믹STF·반도체부품",
        "032560.KS": "스테인리스강판유통", "064400.KS": "IT서비스·SI",
        # 통신·미디어·광고
        "039560.KQ": "네트워크통신장비", "037560.KS": "케이블TV·알뜰폰",
        "089600.KQ": "디지털광고대행", "035600.KQ": "전자결제PG",
        "030000.KS": "광고대행(삼성계열)", "035000.KS": "광고지주(HS애드)",
        "237820.KQ": "디지털퍼포먼스마케팅",
        # 지주·상사·금융
        "047050.KS": "종합상사·LNG", "003550.KS": "LG그룹지주",
        "034730.KS": "SK그룹지주", "078930.KS": "GS그룹지주",
        "001040.KS": "CJ그룹지주", "000150.KS": "두산그룹지주",
        "027410.KS": "BGF그룹지주(편의점)", "004800.KS": "효성그룹지주",
        "004990.KS": "롯데그룹지주", "005440.KS": "현대백화점그룹지주",
        "009970.KS": "영원무역지주", "001120.KS": "종합상사·자원",
        "001740.KS": "상사·렌터카", "402340.KS": "반도체·ICT투자지주",
        "180640.KS": "한진그룹지주", "044820.KS": "화장품ODM지주",
        "088980.KS": "인프라투자MKIF", "029780.KS": "신용카드",
        "001720.KS": "증권", "030210.KS": "증권",
        "000370.KS": "손해보험", "049720.KQ": "채권추심·신용조회",
        "030190.KS": "신용평가·CB",
        # 기계·건설·플랜트
        "050860.KQ": "농기계·트랙터", "267270.KS": "굴착기·건설기계",
        "241560.KS": "소형건설장비(북미)", "011210.KS": "공작기계·차량부품",
        "017800.KS": "엘리베이터", "009450.KS": "보일러·온수기",
        "009470.KS": "전해콘덴서", "282720.KQ": "전기공사·발전플랜트",
        "002990.KS": "건설·토목", "009410.KS": "건설·환경",
        "076080.KQ": "플랜트·환경설비", "011930.KS": "클린룸·태양광",
        "012750.KS": "보안경비서비스", "489790.KS": "CCTV·영상보안",
        # 소재·화학
        "004830.KS": "합성피혁·PU소재", "336370.KS": "동박·전지박",
        "457190.KS": "특수화학·황화합물", "298020.KS": "스판덱스세계1위",
        "298050.KS": "타이어코드세계1위", "093370.KS": "불소화학·전지소재",
        "006650.KS": "석유화학PE·PP", "145990.KS": "식품·화학소재",
        "120030.KS": "용접재료", "021040.KQ": "특수강·선재",
        "383310.KQ": "환경소재·온실가스저감",
        # 자동차·부품·타이어
        "003570.KS": "방산변속기·정밀가공", "123040.KQ": "차체부품·핫스탬핑",
        "007340.KS": "방진부품·배터리", "005850.KS": "차량용램프",
        "161390.KS": "타이어", "005720.KS": "산업소재·타이어계열",
        "018880.KS": "차량공조시스템",
        # 해운·항공·물류
        "028670.KS": "벌크선해운", "124560.KQ": "국제물류포워딩",
        "086280.KS": "물류·완성차운송", "272450.KS": "저비용항공LCC",
        "089590.KS": "저비용항공LCC",
        # 유통·소비재·콘텐츠
        "302920.KQ": "웹툰·IP콘텐츠", "257720.KQ": "K뷰티역직구유통",
        "139480.KS": "대형마트", "095720.KS": "교육출판·학습지",
        "383220.KS": "패션MLB·디스커버리", "111770.KS": "아웃도어OEM",
        "009240.KS": "가구·인테리어", "000080.KS": "주류맥주·소주",
        "192080.KS": "소셜카지노게임", "950140.KQ": "화장품ODM(미국)",
        # 바이오·제약
        "253840.KQ": "체외진단키트", "347850.KQ": "비만치료제신약",
        "000250.KQ": "점안제·바이오시밀러", "009420.KS": "바이오신약(안구건조)",
        "382800.KQ": "반도체스크러버·환경", "237690.KQ": "올리고핵산CDMO",
        "298060.KQ": "의약품유통", "397030.KQ": "항체신약플랫폼",
        "082270.KQ": "알츠하이머신약", "048410.KQ": "항바이러스신약",
        "310210.KQ": "표적항암제", "214450.KQ": "리쥬란·필러",
        "226950.KQ": "RNAi치료제",
        # 바이오 추가분
        "302440.KQ": "면역항암·T세포치료",
        "048530.KQ": "ADC항체약물접합체",
        "950200.KQ": "ADC·이중항체바이오",
        "389030.KQ": "유전체분석·NGS진단",
        # 기타 보강
        "365550.KS": "물류센터리츠", "014620.KQ": "관이음쇠·피팅",
        "033780.KS": "담배·홍삼", "010950.KS": "정유·석유화학",
        # ── 2026-05 인기종목 보강 (네이버 시총상위 128) ──────────────────────
        "031210.KS": "종합보증보험", "003690.KS": "재보험",
        "026960.KS": "식품·포장재", "000240.KS": "타이어그룹지주",
        "279570.KS": "인터넷은행", "081660.KS": "패션지주(FILA)",
        "085620.KS": "생명보험", "007810.KS": "PCB",
        "023590.KS": "IT지주(키움)", "395400.KS": "SK계열리츠",
        "001800.KS": "오리온지주", "006040.KS": "수산식품지주",
        "032350.KS": "카지노·리조트", "020560.KS": "항공",
        "003530.KS": "증권", "030610.KS": "증권",
        "073240.KS": "타이어", "004000.KS": "정밀화학",
        "012630.KS": "건설그룹지주", "034230.KS": "외국인카지노",
        "006110.KS": "알루미늄박", "950160.KQ": "유전자치료제",
        "440110.KQ": "SSD컨트롤러", "319400.KQ": "물류자동화",
        "100790.KQ": "벤처캐피탈", "032820.KQ": "원전계측제어",
        "043260.KQ": "필름콘덴서", "347700.KQ": "우주항공·SW",
        "078600.KQ": "실리콘음극재", "082920.KQ": "리튬1차전지",
        "068760.KQ": "케미컬제약", "140410.KQ": "단심실치료제",
        "027360.KQ": "벤처캐피탈", "476830.KQ": "RNA치료제",
        "060370.KQ": "해저케이블시공", "031330.KQ": "반도체유통",
        "101490.KQ": "블랭크마스크", "290650.KQ": "인체조직이식재",
        "420770.KQ": "기판검사장비", "183300.KQ": "부품세정·코팅",
        "096530.KQ": "분자진단", "039200.KQ": "표적항암신약",
        "445680.KQ": "세포분석자동화", "295310.KQ": "특수합금소재",
        "090710.KQ": "지능형로봇", "232140.KQ": "웨이퍼테스터",
        "417200.KQ": "울트라커패시터", "475830.KQ": "ADC·TPD신약",
        "038500.KQ": "시멘트", "491000.KQ": "복강경수술기구",
        "089970.KQ": "폴리실리콘식각", "003380.KQ": "하림지주",
        "085660.KQ": "세포치료제", "195940.KQ": "위식도신약",
        "458870.KQ": "심전도모니터링", "490470.KQ": "반도체설계서비스",
        "065350.KQ": "가전부품", "281740.KQ": "LED·반도체소재",
        "033790.KQ": "전구체소재", "204270.KQ": "강화유리·커넥터",
        "439960.KQ": "웨어러블로봇", "388720.KQ": "산업용로봇",
        "127120.KQ": "유전체분석", "161580.KQ": "디스플레이장비",
        "124500.KQ": "SI·IT서비스", "388210.KQ": "장비소모성부품",
        "160190.KQ": "산업용모터", "036830.KQ": "소재지주",
        "007390.KQ": "줄기세포치료제", "213420.KQ": "OLED소재",
        "466100.KQ": "로봇소프트웨어", "052020.KQ": "면역항암신약",
        "456160.KQ": "장기지속주사제", "222080.KQ": "2차전지장비",
        "126340.KQ": "슈퍼커패시터", "115180.KQ": "저분자신약",
        "056080.KQ": "청소·서비스로봇", "094170.KQ": "아날로그반도체",
        "376900.KQ": "장기재생플랫폼", "074600.KQ": "석영유리",
        "050890.KQ": "통신중계기", "476060.KQ": "소화기·항암신약",
        "037460.KQ": "통신장비유통", "023160.KQ": "관이음쇠",
        "052400.KQ": "결제IC·지역화폐", "046890.KQ": "LED",
        "032190.KQ": "SW유통지주", "121600.KQ": "CMP슬러리·TCO",
        "171090.KQ": "OLED증착장비", "044490.KQ": "자유형단조",
        "092190.KQ": "LED칩", "006730.KQ": "호텔·석유판매",
        "348370.KQ": "2차전지전해액", "077360.KQ": "솔더볼",
        "009520.KQ": "철강포장·탈산제", "174900.KQ": "항체의약품",
        "358570.KQ": "면역항암신약", "199800.KQ": "유전자교정",
        "019210.KQ": "절삭공구", "425420.KQ": "테스트소켓",
        "102940.KQ": "원료의약·항균제", "060250.KQ": "전자결제",
        "252990.KQ": "세라믹기판", "399720.KQ": "디자인하우스",
        "089890.KQ": "솔더볼장비", "336570.KQ": "미용의료기기",
        "389470.KQ": "약물전달플랫폼", "033160.KQ": "본딩와이어",
        "368770.KQ": "광섬유관성센서", "253590.KQ": "반도체검사장비",
        "348210.KQ": "패턴결함검사", "015750.KQ": "자동차차체부품",
        "033500.KQ": "초저온보냉재", "024850.KQ": "리드프레임",
        "078160.KQ": "줄기세포·제대혈", "089010.KQ": "터치IC·소재",
        "486990.KQ": "AI경량화SW", "475960.KQ": "홀로토모현미경",
        "460930.KQ": "선박기자재", "214430.KQ": "영상센서",
        "354320.KQ": "알루미늄압출", "372320.KQ": "CAR-T치료제",
        "332570.KQ": "전력증폭기모듈", "041960.KQ": "동물약품",
        "122640.KQ": "반도체·DP장비", "448900.KQ": "자동차부품",
        "493280.KQ": "이중항체신약", "093320.KQ": "인터넷연동(IX)",
        "457370.KQ": "반도체·전자소재화학",
        "394280.KQ": "AI반도체IP", "361390.KQ": "엣지AI비전반도체",
        "102120.KQ": "MCU팹리스", "123860.KQ": "DisplayIC팹리스",
        # ── 2026-05 9차 추가 DESC ────────────────────────────────────────────
        "030520.KQ": "AI오피스SW", "402030.KQ": "AI자연어처리",
        "315640.KQ": "AI의료영상", "119650.KS": "전력케이블",
        "094820.KQ": "슈퍼커패시터", "302380.KS": "DC-DC컨버터",
        "094850.KQ": "적외선센서·방산", "048830.KQ": "군용통신장비",
        "073010.KQ": "선박축계부품", "006370.KS": "중형조선소",
        "003850.KS": "제약(카나브)", "170900.KS": "제약(DA-5512)",
        "365550.KQ": "오가노이드플랫폼", "380440.KQ": "협동로봇",
        "270660.KQ": "가정용청소로봇", "018290.KQ": "리들샷마스크",
        "439090.KQ": "비건뷰티", "000060.KS": "손해보험·밸류업",
        "041140.KQ": "캐주얼게임", "014620.KS": "자동차차체부품",
    }

    # ─────────────────────────────────────────────────────────────────────
    # US_NAMES — 미국 종목 한글명 (yfinance 영문명 대신 표시)
    # ─────────────────────────────────────────────────────────────────────
    US_NAMES: dict[str, str] = {
        "AA": "알코아",
        "AAL": "아메리칸 항공",
        "AAPL": "애플",
        "ABBV": "애브비",
        "ABNB": "에어비앤비",
        "ABT": "애보트",
        "ACHR": "아처 에비에이션",
        "ACLS": "액셀리스",
        "ACN": "액센추어",
        "ADBE": "어도비",
        "ADC": "어그리 리얼티",
        "ADI": "아나로그 디바이스",
        "ADM": "아처 대니얼스",
        "ADP": "ADP",
        "AEHR": "에어테스트",
        "AEM": "애그니코 이글",
        "AEO": "아메리칸 이글",
        "AEP": "아메리칸 일렉트릭",
        "AFL": "애플락",
        "AFRM": "어펌",
        "AG": "퍼스트 마제스틱",
        "AI": "C3.ai",
        "AIG": "AIG",
        "AKAM": "아카마이",
        "ALAB": "아스테라 랩스",
        "ALB": "앨버말",
        "ALGN": "얼라인 테크놀로지",
        "ALL": "올스테이트",
        "ALLY": "앨라이 파이낸셜",
        "ALNY": "알나일람",
        "ALT": "알티뮨",
        "AMAT": "어플라이드 머티리얼즈",
        "AMBA": "암바렐라",
        "AMC": "AMC 엔터테인먼트",
        "AMD": "AMD",
        "AME": "아메텍",
        "AMG": "어필리에이티드",
        "AMGN": "암젠",
        "AMH": "아메리칸 홈즈",
        "AMKR": "앰코 테크놀로지",
        "AMT": "아메리칸 타워",
        "AMZN": "아마존",
        "ANET": "아리스타 네트웍스",
        "APA": "APA",
        "APD": "에어 프로덕츠",
        "APLD": "어플라이드 디지털",
        "APO": "아폴로 글로벌",
        "APP": "앱러빈",
        "APTV": "앱티브",
        "ARES": "아레스 매니지먼트",
        "ARM": "ARM",
        "ARRY": "어레이 테크놀로지",
        "ARVN": "아비나스",
        "ASML": "ASML",
        "ASTS": "AST 스페이스모바일",
        "ATR": "앱타그룹",
        "AU": "앵글로 골드",
        "AVB": "아발론베이",
        "AVGO": "브로드컴",
        "AWK": "아메리칸 워터웍스",
        "AXNX": "악소닉스",
        "AXON": "액손 엔터프라이즈",
        "AXP": "아메리칸 익스프레스",
        "AYI": "아큐이티 브랜즈",
        "AZN": "아스트라 제네카",
        "AZTA": "아젠타",
        "BA": "보잉",
        "BAC": "뱅크오브 아메리카",
        "BAH": "부즈앨런 해밀턴",
        "BALL": "볼 코퍼레이션",
        "BAM": "브룩필드 자산운용",
        "BBAI": "빅베어 AI",
        "BCE": "BCE",
        "BE": "블룸 에너지",
        "BEAM": "빔 테라퓨틱스",
        "BEN": "프랭클린 템플턴",
        "BG": "번지",
        "BIIB": "바이오젠",
        "BILL": "빌닷컴",
        "BITF": "비트팜스",
        "BJ": "BJ 홀세일",
        "BK": "뱅크오브 뉴욕멜론",
        "BKNG": "부킹 홀딩스",
        "BLK": "블랙록",
        "BMRN": "바이오마린",
        "BMY": "브리스톨 마이어스",
        "BNTX": "바이오엔텍",
        "BOX": "박스",
        "BR": "브로드리지",
        "BRK-B": "버크셔 해서웨이",
        "BSX": "보스턴 사이언티픽",
        "BTBT": "비트디지털",
        "BTDR": "비트디어",
        "BUD": "AB 인베브",
        "BURL": "벌링턴 스토어스",
        "BWXT": "BWX 테크놀로지스",
        "BX": "블랙스톤",
        "BXP": "BXP",
        "C": "시티그룹",
        "CACI": "카시 인터내셔널",
        "CAG": "코나그라 브랜즈",
        "CALX": "칼릭스",
        "CARR": "캐리어 글로벌",
        "CAT": "캐터필러",
        "CAVA": "카바그룹",
        "CB": "처브",
        "CBOE": "시보글로벌",
        "CBRE": "시비알이",
        "CC": "켐투어스",
        "CCI": "크라운캐슬",
        "CCJ": "카메코",
        "CCK": "크라운 홀딩스",
        "CDW": "CDW",
        "CE": "셀라니즈",
        "CEG": "컨스텔레이션 에너지",
        "CELH": "셀시우스",
        "CEVA": "시바",
        "CF": "CF 인더스트리즈",
        "CFG": "시티즌스 파이낸셜",
        "CFLT": "컨플루언트",
        "CG": "칼라일그룹",
        "CGNX": "코그넥스",
        "CGON": "CG 온콜로지",
        "CHD": "처치앤 드와이트",
        "CHKP": "체크포인트",
        "CHRW": "CH 로빈슨",
        "CHTR": "차터 커뮤니케이션",
        "CI": "시그나",
        "CIEN": "시에나",
        "CIFR": "사이퍼 마이닝",
        "CL": "콜게이트 팜올리브",
        "CLF": "클리블랜드 클리프",
        "CLS": "셀레스티카",
        "CLSK": "클린스파크",
        "CLX": "클로록스",
        "CMC": "커머셜 메탈즈",
        "CMCSA": "컴캐스트",
        "CME": "CME 그룹",
        "CMG": "치폴레",
        "CMI": "커민스",
        "CNC": "센틴",
        "CNK": "시네마크",
        "COF": "캐피탈원",
        "COHR": "코히런트",
        "COHU": "코후",
        "COIN": "코인베이스",
        "COP": "코노코 필립스",
        "COR": "센코라",
        "CORZ": "코어 사이언티픽",
        "COST": "코스트코",
        "CPB": "캠벨스 컴퍼니",
        "CPNG": "쿠팡",
        "CPRI": "카프리 홀딩스",
        "CPT": "캠든 프로퍼티",
        "CRCL": "서클 인터넷",
        "CRM": "세일즈포스",
        "CRSP": "크리스퍼",
        "CRTO": "크리테오",
        "CRUS": "시러스로직",
        "CRWD": "크라우드 스트라이크",
        "CRWV": "코어위브",
        "CSCO": "시스코",
        "CSGP": "코스타그룹",
        "CSX": "CSX",
        "CTVA": "코르테바",
        "CVCO": "카브코 인더스트리",
        "CVS": "CVS 헬스",
        "CVX": "셰브론",
        "CW": "커티스 라이트",
        "D": "도미니언 에너지",
        "DAL": "델타항공",
        "DCPH": "데시페라 파마",
        "DD": "듀폰",
        "DDOG": "데이터독",
        "DE": "디어",
        "DELL": "델",
        "DEO": "디아지오",
        "DG": "달러제너럴",
        "DHI": "DR호튼",
        "DHR": "다나허",
        "DIOD": "다이오즈",
        "DIS": "디즈니",
        "DKNG": "드래프트 킹스",
        "DLR": "디지털 리얼티",
        "DLTR": "달러트리",
        "DNN": "데니슨 마인즈",
        "DOCN": "디지털 오션",
        "DOCU": "도큐사인",
        "DOV": "도버",
        "DOW": "다우",
        "DPZ": "도미노피자",
        "DRI": "다든 레스토랑",
        "DUK": "듀크 에너지",
        "DUOL": "듀오링고",
        "DV": "더블베리파이",
        "DVA": "다비타",
        "DVN": "데본 에너지",
        "DXCM": "덱스콤",
        "EA": "일렉트로닉 아츠",
        "EBAY": "이베이",
        "ECL": "에코랩",
        "ED": "컨솔리데이티드 에디슨",
        "EDIT": "에디타스 메디신",
        "EFX": "에퀴팩스",
        "EL": "에스티로더",
        "ELS": "에퀴티 라이프스타일",
        "ELV": "엘레반스 헬스",
        "EMN": "이스트먼 케미컬",
        "ENPH": "엔페이즈 에너지",
        "ENTG": "인테그리스",
        "ENVX": "에노빅스",
        "EOG": "EOG 리소시즈",
        "EQH": "에퀴터블 홀딩스",
        "EQIX": "에퀴닉스",
        "EQR": "에퀴티 레지덴셜",
        "ESS": "에섹스 프로퍼티",
        "ESTC": "엘라스틱",
        "ET": "에너지 트랜스퍼",
        "ETN": "이튼",
        "ETR": "엔터지",
        "ETSY": "엣시",
        "EW": "에드워즈 라이프사이언스",
        "EXC": "엑셀론",
        "EXLS": "엑셀서비스",
        "EXPE": "익스피디아",
        "EXR": "엑스트라 스페이스",
        "EXTR": "익스트림 네트웍스",
        "F": "포드",
        "FANG": "다이아몬드백 에너지",
        "FCEL": "퓨얼셀 에너지",
        "FCX": "프리포트 맥모란",
        "FDS": "팩트셋",
        "FDX": "페덱스",
        "FIS": "FIS",
        "FISV": "파이서브",
        "FIVE": "파이브 빌로우",
        "FLNC": "플루언스 에너지",
        "FMC": "FMC",
        "FNV": "프랑코 네바다",
        "FORM": "폼팩터",
        "FOUR": "시프트포",
        "FR": "퍼스트 인더스트리얼",
        "FRT": "페더럴 리얼티",
        "FSLR": "퍼스트 솔라",
        "FTNT": "포티넷",
        "FTV": "포티브",
        "FUBO": "푸보TV",
        "GD": "제너럴 다이내믹스",
        "GEN": "젠디지털",
        "GEV": "GE 버노바",
        "GFI": "골드필즈",
        "GILD": "길리어드",
        "GIS": "제너럴 밀스",
        "GL": "글로브 라이프",
        "GLW": "코닝",
        "GM": "제너럴 모터스",
        "GNRC": "제너랙",
        "GOLD": "배릭골드",
        "GOOGL": "알파벳",
        "GPCR": "스트럭처 테라퓨틱스",
        "GPN": "글로벌 페이먼츠",
        "GPS": "갭",
        "GS": "골드만삭스",
        "GSAT": "글로벌 스타",
        "GSK": "GSK",
        "GTLB": "깃랩",
        "HCA": "HCA 헬스케어",
        "HD": "홈디포",
        "HES": "헤스",
        "HIG": "하트포드 파이낸셜",
        "HII": "헌팅턴 잉걸스",
        "HIMS": "힘스앤허스",
        "HL": "헤클라 마이닝",
        "HMC": "혼다",
        "HOLX": "홀로직",
        "HON": "하니웰",
        "HOOD": "로빈후드",
        "HPE": "HPE",
        "HSY": "허쉬",
        "HUBB": "허벨",
        "HUBS": "허브스팟",
        "HUM": "휴마나",
        "HUT": "헛에이트 마이닝",
        "HWM": "하우멧 에어로",
        "IAC": "IAC",
        "IAS": "인테그럴 애드사이언스",
        "IBM": "IBM",
        "ICE": "인터컨티넨탈 익스체인지",
        "ICHR": "아이코르 시스템즈",
        "IDXX": "아이덱스",
        "ILMN": "일루미나",
        "IMAX": "아이맥스",
        "IMVT": "이뮤노반트 사이언스",
        "INCY": "인사이트",
        "INTC": "인텔",
        "INTU": "인튜이트",
        "INVH": "인비테이션 홈즈",
        "IONQ": "아이온큐",
        "IP": "인터내셔널 페이퍼",
        "IR": "잉거솔랜드",
        "IREN": "아이렌",
        "IRM": "아이언 마운틴",
        "ISRG": "인튜이티브 서지컬",
        "ITW": "일리노이 툴웍스",
        "IVZ": "인베스코",
        "JBHT": "JB헌트",
        "JNJ": "존슨앤존슨",
        "JNPR": "주니퍼 네트웍스",
        "JOBY": "조비 에비에이션",
        "JPM": "JP모건",
        "K": "켈라노바",
        "KBH": "KB홈",
        "KEY": "키코프",
        "KGC": "키나로스 골드",
        "KHC": "크래프트 하인즈",
        "KIM": "킴코리얼티",
        "KKR": "KKR",
        "KLAC": "KLA",
        "KLIC": "쿨리케앤 소파",
        "KMB": "킴벌리 클라크",
        "KMI": "킨더모건",
        "KO": "코카콜라",
        "KR": "크로거",
        "KTOS": "크라토스 디펜스",
        "KYMR": "카이메라 테라퓨틱스",
        "LAC": "리튬 아메리카스",
        "LBRDK": "리버티 브로드밴드",
        "LCID": "루시드",
        "LDOS": "레이도스",
        "LEA": "리어",
        "LEN": "레나",
        "LEU": "센트러스 에너지",
        "LH": "래버코프",
        "LHX": "L3 해리스",
        "LI": "리오토",
        "LIN": "린데",
        "LITE": "루멘텀",
        "LLY": "일라이릴리",
        "LMT": "록히드마틴",
        "LNG": "셰니에르 에너지",
        "LNTH": "란테우스 홀딩스",
        "LOW": "로우스",
        "LPLA": "LPL 파이낸셜",
        "LRCX": "램리서치",
        "LSCC": "래티스 반도체",
        "LULU": "룰루레몬",
        "LUNR": "인튜이티브 머신즈",
        "LUV": "사우스웨스트 항공",
        "LYB": "라이온델 바셀",
        "LYV": "라이브 네이션",
        "MA": "마스터카드",
        "MAA": "미드 아메리카",
        "MAN": "맨파워그룹",
        "MARA": "마라 홀딩스",
        "MCD": "맥도날드",
        "MCHP": "마이크로칩",
        "MCK": "맥케슨",
        "MCO": "무디스",
        "MDB": "몽고DB",
        "MDLZ": "몬델리즈",
        "MDT": "메드트로닉",
        "MELI": "메르카도 리브레",
        "MET": "메트라이프",
        "META": "메타",
        "MFC": "매뉴라이프",
        "MGNI": "매그나이트",
        "MHO": "M/I 홈즈",
        "MKC": "맥코믹",
        "MKSI": "MKS 인스트루먼츠",
        "MMM": "쓰리엠",
        "MNDY": "먼데이닷컴",
        "MNST": "몬스터 베버리지",
        "MOH": "몰리나 헬스케어",
        "MOS": "모자이크",
        "MOV": "모바도그룹",
        "MP": "MP 머티리얼즈",
        "MPC": "마라톤 페트롤리엄",
        "MPWR": "모놀리식 파워",
        "MPLX": "MPLX",
        "MRK": "머크",
        "MRNA": "모더나",
        "MRO": "마라톤 오일",
        "MRVL": "마벨 테크놀로지",
        "MS": "모건스탠리",
        "MSCI": "MSCI",
        "MSFT": "마이크로소프트",
        "MSI": "모토로라 솔루션즈",
        "MSTR": "마이크로스트래티지",
        "MTH": "메리티지 홈즈",
        "MU": "마이크론",
        "MYR": "MYR 그룹",
        "NBIS": "네비우스",
        "NDAQ": "나스닥",
        "NEE": "넥스트에라 에너지",
        "NEM": "뉴몬트",
        "NET": "클라우드 플레어",
        "NFLX": "넷플릭스",
        "NIO": "니오",
        "NKE": "나이키",
        "NNE": "나노 뉴클리어",
        "NNN": "NNN 리츠",
        "NOC": "노스롭 그루먼",
        "NOVA": "노바",
        "NOW": "서비스나우",
        "NRG": "NRG 에너지",
        "NSC": "노퍽서던",
        "NTAP": "넷앱",
        "NTES": "넷이즈",
        "NTLA": "인텔리아 테라퓨틱스",
        "NTNX": "누타닉스",
        "NTR": "뉴트리엔",
        "NU": "누홀딩스",
        "NUE": "뉴코어",
        "NVCR": "노보큐어",
        "NVDA": "엔비디아",
        "NVO": "노보 노디스크",
        "NVR": "NVR",
        "NVS": "노바티스",
        "NVT": "엔벤트 일렉트릭",
        "NVTS": "나비타스 반도체",
        "NXPI": "NXP 반도체",
        "NXT": "넥스트래커",
        "O": "리얼티인컴",
        "ODFL": "올드 도미니언",
        "OKE": "원오크",
        "OKLO": "오클로",
        "OKTA": "옥타",
        "OLLI": "올리스 바겐아울렛",
        "OLN": "올린",
        "ON": "온 세미컨덕터",
        "ONON": "온러닝",
        "ONTO": "온투 이노베이션",
        "OR": "오시스코 골드",
        "ORCL": "오라클",
        "OSCR": "오스카헬스",
        "OTIS": "오티스",
        "OWL": "블루아울 캐피탈",
        "OXY": "옥시덴탈",
        "PAA": "플레인스 올아메리칸",
        "PAAS": "팬아메리칸 실버",
        "PANW": "팔로알토 네트웍스",
        "PATH": "유아이패스",
        "PAYX": "페이첵스",
        "PCTY": "페이로시티",
        "PDD": "핀둬둬",
        "PEP": "펩시코",
        "PEPG": "페파젠",
        "PFE": "화이자",
        "PG": "P&G",
        "PGR": "프로그레시브",
        "PH": "파커하니핀",
        "PHM": "풀티그룹",
        "PINS": "핀터레스트",
        "PL": "플래닛 랩스",
        "PKG": "패키징코프",
        "PLD": "프로로지스",
        "PLTK": "플레이티카",
        "PLTR": "팔란티어",
        "PLUG": "플러그파워",
        "PNC": "PNC 파이낸셜",
        "POWL": "파웰 인더스트리즈",
        "PPG": "PPG 인더스트리즈",
        "PPL": "PPL 코퍼레이션",
        "PR": "퍼미안 리소시즈",
        "PRU": "프루덴셜",
        "PSTG": "퓨어 스토리지",
        "PSX": "필립스66",
        "PUBM": "펍매틱",
        "PVH": "PVH",
        "PWR": "퀀타 서비시즈",
        "PYPL": "페이팔",
        "QBTS": "디웨이브 퀀텀",
        "QCOM": "퀄컴",
        "QLYS": "퀄리스",
        "QRVO": "코르보",
        "QS": "퀀텀 스케이프",
        "QSR": "레스토랑 브랜즈",
        "RAMP": "라이브램프",
        "RBLX": "로블록스",
        "RBRK": "루브릭",
        "RCUS": "아커스",
        "RDDT": "레딧",
        "REG": "리전시센터",
        "REGN": "리제네론",
        "REXR": "렉스포드",
        "RF": "리전스 파이낸셜",
        "RGLD": "로열골드",
        "RGTI": "리게티",
        "RIOT": "라이엇",
        "RIVN": "리비안",
        "RJF": "레이먼드 제임스",
        "RKLB": "로켓랩",
        "RKT": "로켓 컴퍼니즈",
        "RL": "랄프로렌",
        "ROK": "록웰 오토메이션",
        "ROKU": "로쿠",
        "ROP": "로퍼테크",
        "ROST": "로스 스토어즈",
        "RPD": "래피드7",
        "RS": "릴라이언스 스틸",
        "RTX": "RTX",
        "RUN": "선런",
        "S": "센티넬원",
        "SAIA": "사이아",
        "SAIC": "사이언스 어플리케이션",
        "SAM": "보스턴 비어",
        "SBAC": "SBA 커뮤니케이션",
        "SBUX": "스타벅스",
        "SCCO": "서던코퍼",
        "SCHW": "찰스슈왑",
        "SE": "씨리미티드",
        "SEDG": "솔라엣지",
        "SEE": "실드에어",
        "SHAK": "쉐이크쉑",
        "SHEN": "셴앤도어 텔레콤",
        "SHOP": "쇼피파이",
        "SHW": "셔윈 윌리엄스",
        "SIMO": "실리콘모션",
        "SITM": "사이타임",
        "SJM": "스머커스",
        "SKX": "스케쳐스",
        "SKY": "스카이라인 챔피언",
        "SLAB": "실리콘랩스",
        "SLB": "슐룸버거",
        "SLF": "선라이프 파이낸셜",
        "SM": "SM 에너지",
        "SMAR": "스마트시트",
        "SMCI": "슈퍼 마이크로",
        "SMG": "스코츠 미라클그로",
        "SMR": "뉴스케일 파워",
        "SNAP": "스냅",
        "SNDK": "샌디스크",
        "SNOW": "스노우 플레이크",
        "SNY": "사노피",
        "SO": "서던컴퍼니",
        "SOFI": "소파이",
        "SOLS": "솔 스트래티지스",
        "SON": "소노코",
        "SONY": "소니",
        "SOUN": "사운드하운드",
        "SPG": "사이먼 프로퍼티",
        "SPGI": "S&P 글로벌",
        "SPOK": "스폭 홀딩스",
        "SPOT": "스포티파이",
        "XYZ": "블록",
        "SQM": "SQM",
        "SRE": "셈프라",
        "STAG": "스태그 인더스트리얼",
        "STEM": "스템",
        "STLA": "스텔란티스",
        "STLD": "스틸 다이내믹스",
        "STT": "스테이트 스트리트",
        "STX": "시게이트",
        "STZ": "컨스텔레이션 브랜즈",
        "SUI": "선 커뮤니티즈",
        "SWKS": "스카이웍스",
        "SYF": "싱크로니",
        "SYK": "스트라이커",
        "T": "AT&T",
        "TAP": "몰슨쿠어스",
        "TDG": "트랜스다임",
        "TEAM": "아틀라시안",
        "TECK": "텍리소시즈",
        "TEM": "템퍼스 AI",
        "TENB": "테너블",
        "TERN": "턴스 파마슈티컬스",
        "TFC": "트루이스트",
        "TGT": "타겟",
        "THC": "테넷 헬스케어",
        "TJX": "TJX 컴퍼니즈",
        "TLN": "탈렌 에너지",
        "TM": "도요타",
        "TMO": "서모피셔",
        "TMUS": "T모바일",
        "TOL": "톨브라더스",
        "TOST": "토스트",
        "TPR": "태피스트리",
        "TRGP": "타르가 리소시즈",
        "TRI": "톰슨로이터",
        "TROW": "T 로우프라이스",
        "TROX": "트로녹스",
        "TRV": "트래블러스",
        "TSCO": "트랙터 서플라이",
        "TSLA": "테슬라",
        "TSM": "TSMC",
        "TSN": "타이슨푸즈",
        "TT": "트레인테크",
        "TTD": "트레이드 데스크",
        "TTWO": "테이크투",
        "TU": "텔루스",
        "TWLO": "트윌리오",
        "TXN": "텍사스 인스트루먼트",
        "TXRH": "텍사스 로드하우스",
        "TXT": "텍스트론",
        "U": "유니티",
        "UAL": "유나이티드 항공",
        "UBER": "우버",
        "UCTT": "울트라클린",
        "UDR": "UDR",
        "UEC": "우라늄 에너지",
        "UHS": "유니버설 헬스",
        "ULTA": "울타뷰티",
        "UNH": "유나이티드 헬스",
        "UNP": "유니언 퍼시픽",
        "UPS": "UPS",
        "UPST": "업스타트",
        "USB": "US 뱅코프",
        "UUUU": "에너지 퓨얼즈",
        "UWM": "UWM 홀딩스",
        "V": "비자",
        "VC": "비스티온",
        "VEEV": "비바 시스템즈",
        "VFC": "VF 코퍼레이션",
        "VICI": "비치 프로퍼티즈",
        "VKTX": "바이킹 테라퓨틱스",
        "VLO": "발레로 에너지",
        "VNO": "보나도 리얼티",
        "VRNS": "바로니스",
        "VRSK": "베리스크",
        "VRT": "버티브",
        "VRTX": "버텍스파마",
        "VSAT": "비아샛",
        "VST": "비스트라",
        "VZ": "버라이즌",
        "W": "웨이페어",
        "WBA": "월그린스",
        "WBD": "워너 브라더스",
        "WDAY": "워크데이",
        "WDC": "웨스턴 디지털",
        "WEC": "WEC 에너지",
        "WEX": "WEX",
        "WFC": "웰스파고",
        "WING": "윙스톱",
        "WIRE": "엔코어 와이어",
        "WK": "워크이바",
        "WMB": "윌리엄스 컴퍼니즈",
        "WMT": "월마트",
        "WOLF": "울프스피드",
        "WPC": "WP케리",
        "WPM": "위튼 프레셔스",
        "WULF": "테라울프",
        "X": "US스틸",
        "XEL": "엑셀 에너지",
        "XOM": "엑손모빌",
        "XPEV": "샤오펑",
        "XPO": "XPO",
        "YUM": "얌브랜즈",
        "ZBH": "짐머 바이오멧",
        "ZD": "지프 데이비스",
        "ZM": "줌",
        "ZS": "지스케일러",
        # ── 신규 추가 (2026-05-17) ──────────────────────────
        "CRDO": "크레도 테크놀로지",
        "AEIS": "어드밴스드 에너지",
        "NVMI": "노바",
        "CAMT": "캠텍",
        "VECO": "비코 인스트루먼츠",
        "EME": "EMCOR",
        "PRIM": "프리모리스",
        "FLR": "플루어",
        "J": "제이콥스 솔루션즈",
        "TMDX": "트랜스메딕스",
        "CDNA": "카리아디엑스",
        "RVMD": "레볼루션 메디슨",
        "NTRA": "나테라",
        "EXAS": "이그잭트 사이언스",
        "CRNX": "크리닉스",
        "INSM": "인스메드",
        "KRYS": "크리스탈 바이오텍",
        "KVUE": "켄뷰",
        "BROS": "두치브라더스",
        "IBKR": "인터랙티브 브로커스",
        "GLOB": "글로반트",
        "LYFT": "리프트",
        "GME": "게임스톱",

        # 2026-05 추가: 게시판 요청 종목 (us_sectors 신규 편입)
        "BKR": "베이커 휴즈",
        "POWI": "파워 인티그레이션스",
        "STM": "ST마이크로일렉트로닉스",
        "QXO": "큐엑스오",
        "LPTH": "라이트패스 테크놀로지",
        "SLDP": "솔리드 파워",
        "SATS": "에코스타",
        "MOD": "모딘 매뉴팩처링",
        "POET": "포엣 테크놀로지스",

        # ── 2026-05 인기종목 보강 (네이버 시총상위 232) ──────────────────────
        "GOOG": "알파벳 Class C",
        "CDNS": "케이던스 디자인 시스템즈",
        "MAR": "메리어트 인터내셔널",
        "SNPS": "시놉시스",
        "ORLY": "오레일리 오토모티브",
        "DASH": "도어대시",
        "CTAS": "신타스",
        "CBRS": "세레브라스 시스템스",
        "PCAR": "파카",
        "TER": "테라다인",
        "ADSK": "오토데스크",
        "FAST": "패스널",
        "FER": "페로비알",
        "ARGX": "아겐스 ADR",
        "FLEX": "플렉스",
        "MDLN": "메드라인",
        "BIDU": "바이두 ADR",
        "FITB": "피프스 서드 뱅코프",
        "CCEP": "코카-콜라 유로퍼시픽 파트너스",
        "JD": "제이디닷컴 ADR",
        "KDP": "큐리그 닥터 페퍼",
        "ERIC": "텔레폰악티에볼라예트 에릭슨 ADR",
        "GFS": "글로벌파운드리",
        "ESLT": "엘빗 시스템즈",
        "VOD": "보다폰 그룹 ADR",
        "ACGL": "아치 캐피털 그룹",
        "TCOM": "트립닷컴 그룹 ADR",
        "CPRT": "코파트",
        "ONC": "비원 메디슨스 ADR",
        "HBAN": "헌팅턴 뱅크셰어스",
        "CASY": "케이시스 제너럴 스토어스",
        "NTRS": "노던 트러스트",
        "RPRX": "로열티 파마",
        "RYAAY": "라이언에어 홀딩스 ADR",
        "TSEM": "타워 세미컨덕터",
        "SYM": "심보틱",
        "GEHC": "GE 헬스케어",
        "MTSI": "M/A-컴 테크놀로지 솔루션스 홀딩스",
        "VRSN": "베리사인",
        "CINF": "신시내티 파이낸셜",
        "STRL": "스털링 인프라스트럭처",
        "WTW": "윌리스 타워스 왓슨",
        "FTAI": "FTAI 애비에이션",
        "UTHR": "유나이티드 테라퓨틱스",
        "TW": "트레이드웹 마켓",
        "CTSH": "코그니전트 테크놀로지 솔루션",
        "EXE": "익스팬드 에너지",
        "PFG": "프린시펄 파이낸셜 그룹",
        "FFIV": "F5",
        "WWD": "우드워드",
        "FCNCA": "퍼스트 시티즌스 뱅크셰어스",
        "FWONK": "포뮬러 원 그룹 Series C",
        "ROIV": "로이반트 사이언시스",
        "VTRS": "비아트리스",
        "FUTU": "푸투 홀딩스 ADR",
        "EVRG": "에버지",
        "LNT": "얼라이언트 에너지",
        "WMG": "워너 뮤직 그룹",
        "VNOM": "바이퍼 에너지",
        "TTMI": "TTM 테크놀로지스",
        "LOGI": "로지텍 인터내셔널",
        "PTC": "PTC",
        "EWBC": "이스트 웨스트 뱅코프",
        "SSNC": "SS&C 테크놀로지스 홀딩스",
        "TPG": "TPG",
        "GMAB": "젠맵 ADR",
        "NBIX": "뉴로크린 바이오사이언시스",
        "NDSN": "노드슨",
        "LAMR": "라마 애드버타이징",
        "HST": "호스트 호텔스 & 리조트",
        "ASND": "어센디스 파마 ADR",
        "GRAB": "그랩 홀딩스",
        "JAZZ": "재즈 파마슈티컬스",
        "AUR": "오로라 이노베이션",
        "LECO": "링컨 일렉트릭 홀딩스",
        "RGC": "리젠셀 바이오사이언스 홀딩스",
        "ARXS": "ARXIS",
        "HTHT": "화주 그룹",
        "AAOI": "어플라이드 옵토일렉트로닉스",
        "HAS": "해즈브로",
        "ARCC": "아레스 캐피탈",
        "RMBS": "램버스",
        "TIGO": "밀리콤 인터내셔널 셀룰러",
        "GLPI": "게이밍 & 레저 프로퍼티스",
        "FOXA": "폭스 Class A",
        "IESC": "IES 홀딩스",
        "PAYP": "페이페이 ADR",
        "FOX": "폭스 Class B",
        "TRMB": "트림블",
        "SMMT": "서밋 테라퓨틱스 ADR",
        "WSE": "와이즈",
        "GH": "가단트 헬쓰",
        "BBIO": "브릿지바이오 파머",
        "EXEL": "엑셀리시스",
        "SMTC": "셈텍",
        "ZBRA": "지브라 테크놀로지스",
        "FRVO": "퍼보 에너지",
        "IONS": "아이오니스 파마슈티컬스",
        "SANM": "산미나",
        "MEDP": "메드페이스 홀딩스",
        "AGNC": "AGNC 인베스트먼트",
        "COO": "쿠퍼 컴퍼니스",
        "MDGL": "매드리갈 파마슈티컬스",
        "AXSM": "액섬 테라퓨틱스",
        "VIAV": "비아비 솔루션스",
        "VICR": "비코",
        "ENLT": "엔라이트 리뉴어블 에너지",
        "COKE": "코카콜라 콘솔리데이티드",
        "ERIE": "이리 인뎀니티",
        "LFUS": "리틀휴즈",
        "DRS": "레오나르도 DRS",
        "BTSG": "브라이트스프링 헬스 서비스",
        "PSKY": "파라마운트 스카이댄스",
        "SEIC": "SEI 인베스트먼트",
        "GLXY": "갤럭시 디지털 홀딩스",
        "AAON": "에이에이온",
        "ARWR": "애로우헤드 파마슈티컬스",
        "PODD": "인슐릿",
        "ENSG": "엔사인 그룹",
        "XE": "X-에너지",
        "CYTK": "사이토키네틱스",
        "WYNN": "윈 리조트",
        "SFD": "스미스필드 푸드",
        "FCFS": "퍼스트캐쉬 홀딩스",
        "WTFC": "윈트러스트 파이낸셜",
        "BRK-A": "버크셔 해서웨이 Class A",
        "BABA": "알리바바 그룹 홀딩스 ADR",
        "HSBC": "HSBC 홀딩스 ADR",
        "GE": "GE 에어로스페이스",
        "PM": "필립 모리스 인터내셔널",
        "SMFG": "스미토모 미쓰이 파이낸셜그룹 ADR",
        "RY": "로열 뱅크 오브 캐나다",
        "SHEL": "쉘 ADR",
        "MUFG": "미쓰비시 UFJ 파이낸셜 그룹 ADR",
        "BHP": "BHP 그룹 ADR",
        "TTE": "토탈에너지스",
        "SAP": "SAP ADR",
        "TD": "토론토 도미니언 은행",
        "SAN": "방코 산탄데르 ADR",
        "UBS": "UBS 그룹 AG",
        "WELL": "웰타워",
        "APH": "암페놀",
        "BTI": "브리티쉬 아메리칸 토바코 ADR",
        "RIO": "리오 틴토 ADR",
        "HDB": "HDFC 뱅크 ADR",
        "UL": "유니레버 ADR",
        "BBVA": "방코 빌바오 비스카야 아르헨타리아 ADR",
        "MO": "알트리아그룹",
        "ENB": "엔브리지",
        "BP": "BP ADR",
        "BN": "브룩필드",
        "BMO": "뱅크 오브 몬트리올",
        "MFG": "미즈호 파이낸셜그룹 ADR",
        "CM": "캐내디언 임페리얼 뱅크 오브 커머스",
        "CNQ": "캐나다 내추럴 리소시스",
        "EQNR": "에퀴노르 ADR",
        "BNS": "뱅크 오브 노바 스코샤",
        "IBN": "ICICI 뱅크 ADR",
        "WM": "웨이스트 매니지먼트",
        "JCI": "존슨 컨트롤스 인터내셔널",
        "EPD": "Enterprise Products Partners Units",
        "ING": "ING 그룹 ADR",
        "E": "에니 ADR",
        "NGG": "내셔널 그리드 ADR",
        "SU": "선코어 에너지",
        "AMX": "아메리카 모빌 ADR 시리즈 L",
        "MRSH": "마쉬 앤 맥레넌 컴퍼니스",
        "BCS": "바클레이즈 ADR",
        "NOK": "노키아 ADR",
        "CP": "캐나디안 퍼시픽 캔자스 시티",
        "LYG": "로이즈 뱅킹 그룹 ADR",
        "CVNA": "카바나",
        "PBR": "페트로브라스 ADR",
        "EMR": "에머슨 일렉트릭",
        "HLT": "힐튼 월드와이드 홀딩스",
        "TRP": "TC 에너지",
        "RCL": "로얄 캐리비안 크루즈",
        "VALE": "발레 ADR",
        "AON": "에이온",
        "ASX": "ASE 테크놀로지 홀딩 ADR",
        "CRH": "CRH",
        "B": "바릭 마이닝",
        "CNI": "커네디언 내셔널 레일웨이",
        "FIX": "컴포트 시스템즈 USA",
        "RSG": "리퍼블릭 서비스",
        "RACE": "페라리",
        "NWG": "내트웨스트 그룹 ADR",
        "URI": "유나이티드 렌탈스",
        "DB": "도이치은행",
        "GWW": "W W 그레인저",
        "TEL": "TE 커넥티비티",
        "KEYS": "키사이트 테크놀로지스",
        "RELX": "렐엑스 ADR",
        "CVE": "세노버스 에너지",
        "AZO": "오토존",
        "AJG": "아서 J 갤러거 앤 코",
        "TAK": "다케다 파마슈티컬 ADR",
        "PSA": "퍼블릭 스토리지",
        "INFY": "인포시스 ADR",
        "ABEV": "암베브 ADR",
        "CAH": "카디널 헬스",
        "MT": "아르셀로미탈 ADR",
        "WAB": "웨스팅하우스 에어 브레이크 테크놀로지",
        "GRMN": "가민",
        "WDS": "우드사이드 에너지 그룹 ADR",
        "UMC": "유나이티드 마이크로일렉트로닉 ADR",
        "FERG": "퍼거슨 엔터프라이즈",
        "AMP": "아메리프라이즈 파이낸셜",
        "ITUB": "이타우 우니방쿠 홀딩 ADR",
        "VTR": "벤타스",
        "IX": "오릭스 ADR",
        "HLN": "헤일리온 ADR",
        "TEVA": "테바 파마슈티컬 인더스트리 ADR",
        "WCN": "웨이스트 커넥션스",
        "BDX": "벡톤 디킨슨 앤 코",
        "PUK": "푸르덴셜 ADR",
        "VIK": "바이킹 홀딩스",
        "PEG": "퍼블릭 서비스 엔터프라이즈",
        "KB": "KB금융지주 ADR",
        "TKO": "TKO 그룹 홀딩스",
        "UI": "유비퀴티",
        "EQT": "EQT",
        "PCG": "퍼시픽 가스 & 일렉트릭",
        "VG": "벤처 글로벌",
        "HAL": "할리버튼",
        "JBL": "자빌",
        "VMC": "벌칸 머티리얼스",
        "SYY": "시스코",
        "LVS": "라스베이거스 샌즈",
        "CHT": "중화 텔레콤 ADR",
        "MLM": "마틴 마리에타 머터리얼스",
        "Q": "큐니티 일렉트로닉스",
        # ── 2026-05-20 누락 보강: 펨코·레딧 바이럴 종목 ───────────────
        "PGY":  "파가야 테크놀로지스",
        "OPEN": "오픈도어 테크놀로지스",
        "DJT":  "트럼프 미디어 & 테크놀로지",
        "BB":   "블랙베리",
        "KSS":  "콜스",
        "DNUT": "크리스피 크림",
        "CHWY": "츄이",
        "PTON": "펠로톤 인터랙티브",
        "LAES": "실즈큐",
        "AEVA": "아에바",
        "LAZR": "루미나 테크놀로지스",
        "RUM":  "럼블",
        "RDFN": "레드핀",
        "SES":  "SES AI",
        "GRRR": "고릴라 테크놀로지",
    }

    # US_DESC — 미국 종목 한글 설명 (Name 컬럼 옆에 표시)
    # ─────────────────────────────────────────────────────────────────────
    US_DESC: dict[str, str] = {
        # Mag 7
        "AAPL": "아이폰 · 맥 · 애플 실리콘", "MSFT": "애저 클라우드 · 코파일럿",
        "NVDA": "AI GPU · 블랙웰 · HBM", "GOOGL": "검색 광고 · 유튜브 · 제미나이",
        "AMZN": "AWS · 이커머스 · 광고", "META": "페북 · 인스타 · 광고 플랫폼",
        "TSLA": "전기차 · FSD · 에너지",
        # AI 플랫폼
        "CRM": "CRM · 세일즈 · AI 에이전트", "NOW": "IT 워크플로 · AI 자동화",
        "SNOW": "데이터 웨어하우스 · 클라우드", "PLTR": "AI 분석 · 빅데이터 플랫폼",
        "ORCL": "클라우드 DB · SaaS 전환", "ADBE": "크리에이티브 · 영상 · PDF",
        "INTU": "세금 신고 · 회계 소프트웨어", "WDAY": "HR · ERP · 클라우드",
        "PATH": "RPA · 업무 자동화 플랫폼",
        # AI 인프라
        "ANET": "데이터센터 · 네트워크 스위치",
        "DELL": "AI 서버 · 스토리지", "SMCI": "AI 슈퍼컴퓨터 · 서버",
        "EQIX": "데이터센터 · 글로벌 리츠", "DLR": "데이터센터 · 리츠",
        "HPE": "엔터프라이즈 · 서버 · 스토리지",
        # 사이버보안
        "CRWD": "클라우드 EDR · 팰콘 플랫폼", "PANW": "차세대 방화벽 · SASE",
        "FTNT": "네트워크 방화벽 · SASE", "ZS": "제로 트러스트 · 클라우드 보안",
        "OKTA": "ID 접근 관리 · IAM", "NET": "클라우드 보안 · CDN",
        "S": "AI 엔드포인트 보안 · EDR",
        # 반도체
        "AMD": "AI GPU · EPYC 서버 CPU",
        "AVGO": "AI 네트워크 칩 · ASIC", "QCOM": "스냅드래곤 · 5G 모뎀",
        "INTC": "x86 CPU · 파운드리 전환", "MU": "HBM3E · 낸드",
        "AMAT": "반도체 증착 · CVD 장비", "LRCX": "반도체 · 식각 장비",
        "KLAC": "공정 제어 · 검사 장비", "ASML": "EUV · 노광 장비 · 독점",
        "ARM": "CPU 아키텍처 · IP 라이선스", "MRVL": "AI 커스텀 ASIC · HBM 컨트롤러",
        "TSM": "파운드리 · 세계 1위 · 대만",
        # 금융·핀테크
        "JPM": "미국 최대 · 투자 은행", "GS": "IB · 트레이딩 · 자산운용",
        "MS": "IB · 자산운용 · 리서치", "BAC": "미국 2위 · 상업 · 소매 은행",
        "V": "비자 결제 · 글로벌 네트워크", "MA": "마스터카드 · 결제 네트워크",
        "PYPL": "페이팔 · 벤모 결제", "XYZ": "모바일 POS · 비트코인 핀테크",
        "COIN": "암호화폐 · 거래소 · 플랫폼", "HOOD": "주식 · 암호화폐 · 거래앱",
        "CME": "선물 · 옵션 · 거래소", "ICE": "NYSE · 파생상품 · 거래소",
        "SPGI": "S&P · 신용평가 · 데이터", "MCO": "무디스 · 신용평가",
        # 방산·산업
        "LMT": "F-35 전투기 · 미사일", "RTX": "패트리어트 미사일 · 항공 엔진",
        "GD": "에이브람스 탱크 · 잠수함", "NOC": "B-21 폭격기 · 우주",
        "BA": "여객기 · 방산 복합체", "GE": "항공 엔진 · 발전 설비",
        "CAT": "건설 중장비 · 세계 1위", "DE": "농기계 · 스마트팜",
        "ETN": "전력 관리 · 데이터센터 전력", "PWR": "전력망 · 공사 · 시공",
        "HUBB": "전력 그리드 부품", "VRT": "데이터센터 · 냉각 · UPS",
        # 에너지
        "XOM": "정유 · LNG · 글로벌", "CVX": "정유 · 가스 · 글로벌",
        "COP": "독립 E&P · 탐사 · 생산", "SLB": "유전 서비스 · 장비",
        "LNG": "LNG · 수출 터미널", "NEE": "전력 · 풍력 · 태양광",
        "FSLR": "박막 태양광 · 모듈", "CEG": "원전 · 청정 에너지",
        "CCJ": "우라늄 · 채굴 · 정제",
        # 헬스케어
        "LLY": "GLP-1 · 젭바운드 · 비만 치료", "NVO": "GLP-1 · 오젬픽 · 비만 치료",
        "JNJ": "의약품 · 의료기기", "ABBV": "휴미라 · 스카이리지 · 면역",
        "MRK": "키트루다 · 항암 · 면역", "PFE": "팍스로비드 · 백신",
        "AMGN": "항체 · 비만 신약", "ISRG": "다빈치 · 수술 로봇",
        "IDXX": "반려동물 · 진단 · 검사", "DXCM": "CGM · 연속 혈당 측정",
        # 소비재
        "SHOP": "이커머스 · 쇼핑몰 플랫폼",
        "COST": "창고형 · 할인 매장", "WMT": "유통 · 이커머스",
        "TGT": "할인 마트 · 소매", "MCD": "패스트푸드 · 프랜차이즈",
        "SBUX": "커피 · 글로벌 체인", "NKE": "스포츠 용품 · 의류",
        "LULU": "애슬레저 · 요가 · 프리미엄",
        "F": "머스탱 마하E · 트럭", "GM": "EV · 울티엄 · 픽업",
        # 소비재 필수재
        "KO": "음료 · 글로벌 브랜드", "PEP": "음료 · 스낵 · 글로벌",
        "PG": "생활용품 · 다각화", "PM": "담배 · IQOS · 글로벌",
        "MO": "담배 · 배당주",
        # 미디어·플랫폼
        "NFLX": "스트리밍 · 오리지널 콘텐츠", "DIS": "디즈니+ · 테마파크",
        "SPOT": "음악 · 스트리밍 · 팟캐스트", "RBLX": "메타버스 · 게임 플랫폼",
        "TTWO": "GTA6 · NBA2K · 게임", "EA": "FIFA · 매든 NFL · 게임",
        # 부동산·리츠
        "AMT": "통신 타워 · 리츠 · 글로벌", "PLD": "물류 창고 · 리츠 · 1위",
        "O": "월배당 · 넷리스 리츠",
        "SPG": "프리미엄 · 아울렛 · 쇼핑몰",
        # 소재·원자재
        "NEM": "금광 · 채굴 · 1위", "FCX": "구리 · 채굴",
        "AA": "알루미늄 · 제련", "LIN": "산업 가스 · 세계 1위",
        "ALB": "리튬 · 채굴 · 정제", "SQM": "칠레 · 리튬 · 채굴 · 정제",
        # 통신
        "T": "무선 · 광통신", "VZ": "5G · 네트워크",
        "TMUS": "5G · 성장주",
        # 비즈니스 서비스
        "ADP": "HR · 급여 · 아웃소싱 1위", "ACN": "IT · 컨설팅 · 글로벌",
        "VRSK": "보험 리스크 · 데이터 분석",
        # AI 플랫폼 추가
        "BOX": "클라우드 · 콘텐츠 · 협업",
        "CFLT": "카프카 · 데이터 스트리밍",
        "DDOG": "클라우드 · 모니터링 · APM",
        "DOCN": "SMB · 클라우드 · VPS",
        "ESTC": "검색 엔진 · 로그 분석",
        "GTLB": "DevSecOps · 통합 개발 플랫폼",
        "HUBS": "마케팅 · CRM · 자동화",
        "IBM": "하이브리드 클라우드 · AI",
        "MDB": "NoSQL · 클라우드 DB",
        "MNDY": "프로젝트 관리 · 협업 SaaS",
        "RBRK": "데이터 보안 · 클라우드 백업",
        "SMAR": "업무 협업 · 스프레드시트 SaaS",
        "TEAM": "Jira · 컨플루언스 · 협업",
        "NTNX": "하이퍼컨버지드 · 인프라 · HCI",
        # AI 인프라 추가
        "CLSK": "비트코인 채굴 · 그린 에너지",
        "CLS": "전자 제조 서비스 · EMS",
        "COHR": "레이저 · 광통신 부품",
        "CORZ": "비트코인 채굴 · 에너지",
        "CSCO": "네트워크 스위치 · 라우터",
        "GLW": "광섬유 · 디스플레이 유리 · 데이터센터",
        "IREN": "비트코인 채굴 · 재생 에너지",
        "IRM": "데이터 보관 · 아카이브",
        "LITE": "광통신 부품 · 데이터센터",
        "NTAP": "엔터프라이즈 · 스토리지",
        "PSTG": "올플래시 · 스토리지 어레이",
        "STX": "HDD · 스토리지",
        "WDC": "HDD · 낸드",
        "WULF": "비트코인 채굴 · HPC",
        # 사이버보안 추가
        "AKAM": "CDN · 엣지 보안",
        "CHKP": "방화벽 · VPN",
        "GEN": "소비자 보안 · 안티바이러스",
        "QLYS": "클라우드 · 취약점 스캔",
        "RPD": "MDR · 보안 운영 플랫폼",
        "TENB": "취약점 관리 · VM",
        "VRNS": "데이터 보안 · DSPM",
        # SaaS 추가
        "APP": "모바일 광고 · AI 플랫폼",
        "BILL": "중소기업 · 청구 결제 자동화",
        "DOCU": "전자 계약 · 서명",
        "DUOL": "AI · 언어 학습",
        "MGNI": "CTV · 광고 플랫폼",
        "PCTY": "HR · 급여 · 클라우드 SaaS",
        "RAMP": "데이터 연결 · 마케팅",
        "TWLO": "CPaaS · 통신 API",
        "VEEV": "생명과학 · CRM · 클라우드",
        "ZM": "화상회의 · 플랫폼",
        "TTD": "프로그래매틱 · 광고 DSP",
        # 반도체 팹리스 추가
        "ALAB": "HBM · CXL · 인터커넥트 칩",
        "ADI": "아날로그 · 혼합 신호 IC",
        "AMBA": "엣지 AI · 영상 칩",
        "CRUS": "오디오 · 반도체 IC",
        "DIOD": "범용 반도체 · 부품",
        "LSCC": "저전력 FPGA · 엣지 AI",
        "MCHP": "MCU · 아날로그 반도체",
        "MPWR": "모놀리식 · 전력 관리 IC",
        "NXPI": "차량용 반도체 · MCU",
        "ON": "전력 반도체 · SiC",
        "QRVO": "RF 부품 · 5G",
        "SLAB": "IoT 반도체 · MCU",
        "SITM": "MEMS · 오실레이터",
        "SWKS": "RF · 5G 모뎀 IC",
        "TXN": "아날로그 · 임베디드 반도체",
        "WOLF": "SiC · 웨이퍼",
        # 반도체 장비 추가
        "ACLS": "이온 주입 · 반도체 장비",
        "AZTA": "반도체 · 저온 보관 · 생명과학",
        "COHU": "반도체 · 테스터 · 핸들러",
        "ENTG": "반도체 소재 · 여과 · 정제",
        "FORM": "웨이퍼 · 프로브카드 · 테스트",
        "ICHR": "가스 전달 모듈 · 반도체",
        "KLIC": "와이어본더 · 반도체 패키징",
        "MKSI": "가스 제어 · 진공 장비 · 반도체",
        "ONTO": "광학 계측 · 검사 장비",
        "UCTT": "반도체 · 클린 서비스",
        "AMKR": "반도체 후공정 · 패키징 · OSAT",
        "CEVA": "무선 IP · DSP · 라이선스",
        "NVTS": "GaN · 전력 반도체 · 고속 충전",
        "SIMO": "낸드 플래시 · 컨트롤러 IC",
        # 크립토 추가
        "BITF": "비트코인 채굴 · 캐나다",
        "BTBT": "비트코인 채굴 · HPC 전환",
        "BTDR": "비트코인 채굴 · 싱가포르",
        "CIFR": "비트코인 채굴 · 마이닝",
        "HUT": "비트코인 채굴 · HPC · 캐나다",
        "MARA": "비트코인 채굴 · 미국 최대",
        "MSTR": "비트코인 · 대량 보유",
        "RIOT": "비트코인 채굴 · 텍사스",
        # 핀테크 추가
        "AFRM": "BNPL · 결제 후불 서비스",
        "ALLY": "온라인 · 자동차 금융 은행",
        "AXP": "신용카드 · 프리미엄",
        "COF": "대형 · 신용카드 · 은행",
        "FISV": "결제 처리 · 금융 IT 솔루션",
        "FOUR": "통합 결제 · 레스토랑 플랫폼",
        "GPN": "글로벌 결제 처리 · 상점 솔루션",
        "MELI": "라틴 아메리카 · 이커머스 · 핀테크",
        "NU": "라틴 · 디지털 은행",
        "SE": "동남아 · 이커머스 · 핀테크 · 게임",
        "SOFI": "디지털 은행 · 학자금 재융자",
        "SYF": "소매 파트너 · 신용카드 금융",
        "TOST": "레스토랑 · POS · 결제 플랫폼",
        "UPST": "AI · 신용 대출",
        # 거래소·데이터 추가
        "CBOE": "옵션 · 선물 · 거래소",
        "FDS": "금융 데이터 · 분석 플랫폼",
        "MSCI": "금융 지수 · ESG · 데이터",
        "NDAQ": "거래소 · 데이터 서비스",
        "TRI": "금융 정보 · 미디어 서비스",
        # 대형은행 추가
        "BK": "커스터디 은행 · 수탁 서비스",
        "C": "글로벌 IB · 은행",
        "CFG": "미국 북동부 · 지역 은행",
        "KEY": "미국 중부 · 지역 은행",
        "PNC": "미국 중부 · 대형 지역 은행",
        "RF": "미국 남동부 · 지역 은행",
        "SCHW": "온라인 증권 · ETF · 자산 관리",
        "STT": "커스터디 은행 · ETF 운용",
        "TFC": "미국 중남부 · 지역 은행",
        "USB": "미국 5위 · 상업 은행",
        "WFC": "미국 4대 · 상업 은행 · 소매",
        # 자산운용·PE 추가
        "AMG": "부티크 · 자산 운용사 연합",
        "APO": "대형 사모펀드 · 크레딧 투자",
        "ARES": "사모 신용 · 대체 투자",
        "BAM": "인프라 · 사모 · 자산 운용",
        "BEN": "글로벌 · 뮤추얼펀드 · 자산 운용",
        "BLK": "세계 최대 · ETF · 인덱스 운용",
        "BX": "사모 · 부동산 · 대체 투자",
        "CG": "대형 사모펀드 · PE",
        "IVZ": "ETF · 자산 운용 · QQQ",
        "KKR": "사모펀드 · 인프라 · 크레딧",
        "LPLA": "독립 투자 자문 · 네트워크",
        "OWL": "비상장 · 신용 · 대체 운용",
        "RJF": "리테일 증권 · 자산 관리",
        "TROW": "액티브 펀드 · 자산 운용",
        # 보험 추가
        "AFL": "암 보험 · 보충 의료보험",
        "AIG": "글로벌 · 손해보험 · 재보험",
        "ALL": "자동차 · 주택 보험 · 미국",
        "BRK-B": "보험 · 투자 지주회사",
        "CB": "글로벌 · 손해보험 · 고급 시장",
        "EQH": "생명보험 · 변액 연금",
        "GL": "저소득층 · 생명보험 · 미국",
        "HIG": "기업 · 손해보험 · 미국",
        "MET": "생명보험 · 연금 보험",
        "MFC": "캐나다 · 생명보험 · 자산 운용",
        "PGR": "자동차 보험 · 성장주",
        "PRU": "생명보험 · 자산 운용 · 미국",
        "SLF": "캐나다 · 금융 · 생명보험",
        "TRV": "기업 · 손해보험 · P&C",
        # 이커머스·여행 추가
        "ABNB": "숙박 공유 · 여행 플랫폼",
        "BKNG": "온라인 · 여행 예약 플랫폼",
        "CPNG": "한국 · 이커머스 · 로켓배송",
        "EBAY": "C2C · 중고 거래 · 마켓플레이스",
        "ETSY": "수공예 · 독립 판매 · 마켓",
        "EXPE": "온라인 · 여행 · 예약",
        "W": "온라인 · 가구 · 인테리어",
        # 리테일 추가
        "AEO": "캐주얼 · 청바지 · 의류",
        "BJ": "창고형 · 회원제 · 소매",
        "BURL": "오프프라이스 · 의류 할인 · 2위",
        "DG": "저가 · 생활 소품 · 체인",
        "DLTR": "균일가 · 소매 · 달러샵",
        "FIVE": "저가 · 청소년 소품 · 체인",
        "GPS": "Gap · Old Navy · 의류",
        "HD": "주택 개조 · DIY",
        "KR": "미국 · 대형 슈퍼마켓 · 체인",
        "LOW": "주택 개조 · DIY · 2위",
        "OLLI": "폐점품 · 할인 소매",
        "ROST": "오프프라이스 · 의류 할인",
        "TJX": "오프프라이스 · 의류 · 1위",
        "TSCO": "농촌 · 생활용품 · 체인",
        # 레스토랑 추가
        "CAVA": "지중해 · 패스트캐주얼",
        "CMG": "멕시코 · 패스트캐주얼",
        "DPZ": "피자 · 배달",
        "DRI": "캐주얼 · 다이닝",
        "QSR": "버거킹 · KFC · 포파이스",
        "SHAK": "프리미엄 버거 · 패스트캐주얼",
        "TXRH": "캐주얼 다이닝 · 스테이크",
        "WING": "치킨 윙 · 패스트푸드 · 체인",
        "YUM": "KFC · 타코벨 · 피자헛",
        # 자동차·EV 추가
        "APTV": "전장 부품 · 자율주행 솔루션",
        "HMC": "자동차 · 오토바이 · 하이브리드",
        "LCID": "고급 · 전기차 · 세단",
        "LEA": "자동차 시트 · 전장 시스템",
        "LI": "중국 · EREV · 전기차",
        "NIO": "중국 · 프리미엄 · 전기차",
        "RIVN": "전기 · 픽업트럭 · SUV",
        "STLA": "지프 · 크라이슬러 · 람",
        "TM": "하이브리드 · 전기차 · 일본 1위",
        "VC": "전장 · 디스플레이 · HV 배터리",
        "XPEV": "전기차 · AI · 자율주행",
        # 의류·뷰티 추가
        "CPRI": "베르사체 · 지미추 · MK",
        "EL": "고급 화장품 · 랩시리즈 · 맥",
        "MOV": "고급 시계 · 보석 · 브랜드",
        "ONON": "기능성 · 러닝화 · 스위스",
        "PVH": "캘빈 클라인 · 토미 힐피거",
        "RL": "고급 · 라이프스타일 · 패션",
        "SKX": "캐주얼 · 스포츠 · 운동화",
        "TPR": "코치 · 케이트 스페이드",
        "ULTA": "뷰티 · 멀티샵 · 화장품",
        "VFC": "노스페이스 · 팀버랜드 · 아웃도어",
        # 음료·주류 추가
        "BUD": "버드와이저 · 코로나 · 스텔라 맥주",
        "CELH": "에너지 음료 · RTD · 건강 기능",
        "DEO": "조니 워커 · 기네스 · 증류주",
        "MNST": "에너지 음료 · 미국 2위",
        "SAM": "크래프트 맥주",
        "STZ": "코로나 · 와인 · 맥주",
        "TAP": "맥주 · 캐나다",
        # 식품·가정 추가
        "CAG": "냉동 식품 · 조리 · 완제품",
        "CHD": "생활용품 · 세제 · 퍼스널케어",
        "CL": "치약 · 구강케어 · 생활용품",
        "CLX": "소독 · 세정 · 생활용품",
        "CPB": "수프 · 소스 · 가공 식품",
        "GIS": "시리얼 · 요구르트 · 식품",
        "HSY": "초콜릿 · 과자 · 미국 1위",
        "K": "시리얼 · 스낵 · 글로벌",
        "KHC": "케찹 · 식품 가공 · 브랜드",
        "KMB": "화장지 · 기저귀 · 생활용품",
        "MDLZ": "오레오 · 나비스코 · 글로벌 과자",
        "MKC": "향신료 · 소스 · 조미료",
        "SJM": "잼 · 커피 · 생활 식품",
        "TSN": "육류 가공 · 닭고기 · 미국 1위",
        "WBA": "약국 체인 · 소매 · 미국",
        # 농업·비료 추가
        "ADM": "곡물 가공 · 원자재 · 트레이딩",
        "BG": "농산물 가공 · 곡물 · 유지",
        "CF": "질소 비료 · 암모니아",
        "CTVA": "종자 · 농약 · 농업 솔루션",
        "FMC": "농약 · 특수 화학 · 작물 보호",
        "MOS": "인산 비료 · 광물 채굴",
        "NTR": "캐나다 · 비료 · 칼리 · 질소",
        "SMG": "원예 · 잔디 비료 · 소비자",
        # 광고·미디어 추가
        "CRTO": "리타게팅 · 디지털 광고",
        "DV": "디지털 광고 검증 · 브랜드 세이프티",
        "IAC": "디지털 미디어 · 포트폴리오",
        "IAS": "광고 검증 · 브랜드 세이프티",
        "PINS": "비주얼 · 발견 · 쇼핑 SNS",
        "PUBM": "개방형 · 프로그래매틱 · 광고",
        "RDDT": "커뮤니티 · 포럼 · SNS 광고",
        "SNAP": "AR · 카메라 · SNS · 메시징",
        "ZD": "디지털 미디어 · 기술 브랜드",
        # 게임·엔터 추가
        "DKNG": "스포츠 베팅 · 온라인 도박",
        "NTES": "중국 · 게임 · 이커머스 · 음악",
        "PLTK": "소셜 카지노 · 모바일 게임",
        "U": "게임 엔진 · 3D · 개발 플랫폼",
        # 스트리밍·미디어 추가
        "AMC": "영화관 체인",
        "CHTR": "케이블 · 인터넷 · 스펙트럼",
        "CNK": "영화관 체인 · 미국 3위",
        "FUBO": "스포츠 · 스트리밍 TV",
        "IMAX": "프리미엄 영화 · 포맷",
        "LYV": "콘서트 · 공연 · 티켓마스터",
        "ROKU": "스트리밍 기기 · 광고 플랫폼",
        "SONY": "PS5 · 게임 · 영화 · 음악",
        "WBD": "영화 · 뉴스 · 스트리밍",
        # REIT 추가
        "CCI": "미국 · 셀타워 · 통신 인프라",
        "EXR": "셀프 스토리지 · REIT",
        "FR": "산업 · 물류 창고 · REIT",
        "REXR": "LA · 산업 물류 · REIT",
        "STAG": "산업 창고 · 단일 세입자 · REIT",
        "AMH": "단독 주택 · 임대 · REIT",
        "AVB": "아파트 · 주거 · REIT",
        "CPT": "선벨트 · 아파트 · REIT",
        "ELS": "RV 파크 · 모바일홈 · REIT",
        "EQR": "도시 · 아파트 · 주거 · REIT",
        "ESS": "캘리포니아 · 아파트 · REIT",
        "INVH": "단독 주택 · 임대 · 미국",
        "MAA": "선벨트 · 아파트 · REIT",
        "SUI": "RV · 모바일홈 · 커뮤니티 · REIT",
        "UDR": "아파트 · 주거 · REIT",
        "ADC": "넷리스 · 소매 · REIT",
        "BXP": "프리미엄 · 오피스 · REIT",
        "FRT": "쇼핑센터 · 넷리스 · REIT",
        "KIM": "필수 소매 · 쇼핑센터 · REIT",
        "NNN": "넷리스 · 소매 · REIT",
        "REG": "식료품 앵커 · 쇼핑센터 · REIT",
        "VICI": "카지노 · 게임 · REIT",
        "VNO": "맨해튼 · 오피스 · 리테일 · REIT",
        "WPC": "글로벌 · 넷리스 · 분산 · REIT",
        # 주택건설 추가
        "CVCO": "조립식 · 모듈러 · 주택",
        "DHI": "미국 1위 · 주택 건설 · 분양",
        "KBH": "서부 · 중저가 · 신규 주택 건설",
        "LEN": "미국 2위 · 주택 건설 · 분양",
        "MHO": "맞춤 주택 · 건설",
        "MTH": "에너지 효율 · 신규 주택 건설",
        "NVR": "주택 건설 · 분양",
        "PHM": "미국 3위 · 주택 건설 · 분양",
        "RKT": "온라인 · 모기지 · 대출 플랫폼",
        "SKY": "이동식 · 조립 주택 · 제조",
        "TOL": "럭셔리 · 고급 주택 · 건설",
        "UWM": "도매 · 모기지",
        # 화학 추가
        "APD": "산업 가스 · 수소 · 인프라",
        "CC": "불소 화학 · 특수 재료",
        "CE": "엔지니어링 · 폴리머 · 화학",
        "DD": "특수 소재 · 반도체 · 전자",
        "DOW": "폴리에틸렌 · 기초 화학 소재",
        "ECL": "위생 · 소독 · 클리닝 서비스",
        "EMN": "특수 폴리머 · 첨가제",
        "LYB": "폴리에틸렌 · 정제 · 화학",
        "OLN": "클로르 알칼리 · 탄약 · 화학",
        "PPG": "도료 · 코팅 · 항공",
        "SHW": "페인트 · 도료 · 코팅",
        "TROX": "이산화티타늄 · 안료",
        # 패키징 추가
        "ATR": "의약품 · 화장품 · 분사기 · 포장",
        "BALL": "음료캔 · 알루미늄 · 포장",
        "CCK": "금속캔 · 식음료 · 포장",
        "IP": "골판지 · 상자 · 포장지",
        "PKG": "골판지 · 포장 · 박스",
        "SEE": "버블랩 · 진공 포장 · 솔루션",
        "SON": "산업 포장 · 소비재 · 튜브",
        # 금속·광업 추가
        "CLF": "철광석 · 철강",
        "CMC": "봉형강 · 철근 · 철강",
        "MP": "희토류 · 채굴",
        "NUE": "전기로 · 미니밀 · 철강 1위",
        "RS": "철강 · 알루미늄 · 유통 서비스",
        "SCCO": "구리 · 광산 · 페루 · 멕시코",
        "STLD": "미니밀 · 전기로 · 철강",
        "TECK": "캐나다 · 구리 · 아연 · 광산",
        "X": "일관 제철 · 철강",
        # 귀금속 추가
        "AEM": "캐나다 · 금광 · 채굴",
        "AG": "멕시코 · 은광 · 채굴",
        "AU": "글로벌 · 금광 · 채굴",
        "FNV": "금광 · 스트리밍 · 로열티",
        "GFI": "남아프리카 · 금광 · 채굴",
        "GOLD": "세계 2위 · 금광 · 채굴",
        "HL": "미국 · 은광 · 채굴",
        "KGC": "캐나다 · 금광 · 채굴 · 글로벌",
        "OR": "캐나다 · 금광 · 스트리밍 · 로열티",
        "PAAS": "중남미 · 은광 · 채굴",
        "RGLD": "금광 · 로열티 · 스트리밍",
        "WPM": "귀금속 · 스트리밍 · 로열티",
        # 리튬·배터리소재 추가
        "ENVX": "실리콘 음극 · 배터리 셀",
        "LAC": "리튬 · 채굴 · 아르헨티나",
        "QS": "고체 전해질 · 전고체 배터리",
        # 통신 추가
        "BCE": "캐나다 · 통신",
        "CMCSA": "케이블 · 미디어 · 스트리밍",
        "LBRDK": "차터 지분 · 케이블 보유",
        "SHEN": "농촌 지역 · 광대역 · 통신",
        "TU": "캐나다 · 통신 · 헬스케어",
        # 5G·위성 추가
        "ASTS": "우주 위성 · 5G 통신",
        "CALX": "광대역 · 통신 · 클라우드 플랫폼",
        "CIEN": "광통신 · 네트워크 · 장비",
        "EXTR": "클라우드 · 네트워크 · 스위치",
        "GSAT": "위성 · IoT · 저궤도 통신",
        "JNPR": "라우터 · 네트워크 · 장비",
        "MSI": "공공 안전 · 무선 통신 · 솔루션",
        "SPOK": "헬스케어 · 페이저 · 무선 통신",
        "VSAT": "위성 · 광대역 통신 · 방산",
        # HR·급여 추가
        "MAN": "글로벌 · 인력 파견 · 채용",
        "PAYX": "중소기업 · 급여 · HR 아웃소싱",
        "WK": "재무 보고 · 컴플라이언스 · 클라우드",
        # 컨설팅·IT서비스 추가
        "BAH": "방산 IT · 사이버 · 컨설팅",
        "CACI": "정부 IT · 사이버 보안",
        "CDW": "IT 솔루션 · 유통",
        "LDOS": "방산 IT · 정부 서비스",
        "SAIC": "정부 IT · 컨설팅",
        # 데이터·분석 추가
        "EFX": "신용 정보 · 데이터 · 분석",
        "EXLS": "분석 · BPO · 디지털 서비스",
        # 비즈니스프로세스 추가
        "BR": "금융 서비스 · IT · BPO",
        "CBRE": "상업 부동산 · 서비스",
        "CSGP": "상업 부동산 · 데이터 · 분석",
        "FIS": "핵심 뱅킹 · 금융 IT",
        "FISV": "결제 처리 · 금융 IT · 솔루션",
        "WEX": "기업 · 플릿 · 연료 결제",
        # 방산·산업 추가
        "AXON": "테이저 · 바디캠 · 공공안전",
        "BWXT": "원자력 부품 · SMR",
        # 에너지 추가
        "APA": "오일 · 탐사 · 생산",
        "DVN": "셰일 오일 · 탐사 · 생산",
        "EOG": "셰일 · 탐사 · 생산",
        "FANG": "퍼미안 분지 · 셰일 원유",
        "HES": "탐사 · 생산 · 가이아나 원유",
        "MPC": "정유 · 휘발유 · 미드스트림",
        "MRO": "탐사 · 생산 · 오일가스",
        "OXY": "서부 텍사스 · 오일 탐사",
        "PSX": "정유 · 화학 · 미드스트림",
        "VLO": "정유 · 휘발유 · 에탄올",
        # 헬스케어 추가
        "AZN": "항암 · 백신 · 호흡기 신약",
        "BMY": "항암 · 면역 치료제",
        "GILD": "항바이러스 · HIV · 간염",
        "GSK": "백신 · 호흡기 치료",
        "NVS": "스위스 빅파마 · 유전자 치료",
        "REGN": "항체 의약품 · 안과 · 항암",
        "SNY": "프랑스 빅파마 · 백신 · 면역",
        "VRTX": "낭성섬유증 · 희귀질환 신약",
        "ABT": "진단 · 의료기기 · 혈당",
        "ALGN": "인비절라인 · 디지털 치과",
        "BSX": "심장 기기 · 중재 시술",
        "DHR": "진단 · 분석 기기 · 생명과학",
        "EW": "심장 판막 · TAVR 수술",
        "HOLX": "여성 건강 · 진단 · 영상",
        "MDT": "의료기기 · 글로벌 최대",
        "NVCR": "전기장 · 종양 치료 기기",
        "SYK": "정형외과 · 로봇 수술 기기",
        "TMO": "과학 장비 · CRO · 생명과학",
        "ZBH": "정형외과 · 임플란트 · 관절",
        "CI": "건강보험 · PBM · 글로벌",
        "CNC": "메디케이드 · 관리 의료보험",
        "COR": "의약품 유통 · 헬스케어",
        "CVS": "약국 · PBM · 보험",
        "DVA": "신장 투석 · 만성 신부전",
        "ELV": "건강보험 · 관리 의료",
        "HCA": "병원 체인 · 미국 최대",
        "HUM": "메디케어 · 어드밴티지 보험",
        "MCK": "의약품 유통 · 미국 1위",
        "MOH": "메디케이드 · 저소득층 보험",
        "THC": "병원 체인 · 앰뷸러토리",
        "UNH": "최대 건강보험 · 옵텀",
        # 운송·물류
        "AAL": "미국 · 항공사",
        "DAL": "미국 · 항공사 · 프리미엄",
        "LUV": "저가 · 항공사",
        "UAL": "미국 · 항공사 · 글로벌",
        "UBER": "라이드셰어 · 배달 · 모빌리티",
        "CSX": "화물 철도 · 미국 동부",
        "NSC": "화물 철도 · 미국 동부",
        "UNP": "화물 철도 · 미국 서부",
        "JBHT": "트럭 운송 · 물류",
        "ODFL": "LTL · 화물 운송",
        "SAIA": "LTL · 화물 운송",
        "XPO": "물류 · 화물 운송",
        "FDX": "택배 · 물류 · 글로벌",
        "UPS": "택배 · 물류 · 글로벌",
        "CHRW": "물류 · 중개 · 운송",
        # 유틸리티
        "AEP": "전력 · 유틸리티",
        "AWK": "수도 · 유틸리티",
        "D": "전력 · 천연가스 · 유틸리티",
        "DUK": "전력 · 유틸리티 · 미국 최대",
        "ED": "전력 · 유틸리티 · 뉴욕",
        "EXC": "전력 · 유틸리티",
        "NRG": "전력 · 소매 · 에너지",
        "PPL": "전력 · 유틸리티",
        "SO": "전력 · 유틸리티 · 미국 남부",
        "SRE": "전력 · 천연가스 · 유틸리티",
        "WEC": "전력 · 유틸리티",
        "XEL": "전력 · 유틸리티 · 풍력",
        # 미드스트림·파이프라인
        "ET": "미드스트림 · 파이프라인",
        "ETR": "전력 · 원전 · 유틸리티",
        "KMI": "미드스트림 · 파이프라인 · 천연가스",
        "MPLX": "미드스트림 · 파이프라인",
        "OKE": "미드스트림 · 천연가스",
        "PAA": "미드스트림 · 원유 파이프라인",
        "SM": "셰일 · 탐사 · 생산",
        "TRGP": "미드스트림 · 천연가스",
        "WMB": "미드스트림 · 천연가스 · 파이프라인",
        "TLN": "전력 · 원전 · 데이터센터",
        "VST": "전력 · 원전 · 소매",
        "PR": "퍼미안 · 셰일 · 오일",
        # 클린에너지
        "ARRY": "태양광 · 트래커 · 시스템",
        "BE": "연료전지 · 수소",
        "ENPH": "태양광 · 마이크로 인버터",
        "FCEL": "연료전지 · 수소",
        "FLNC": "에너지 · 저장 · 배터리",
        "NOVA": "주택 태양광 · 에너지",
        "PLUG": "수소 · 연료전지",
        "RUN": "주택 태양광 · 에너지",
        "SEDG": "태양광 · 인버터",
        "STEM": "AI · 에너지 저장",
        "NXT": "태양광 · 트래커 · 시스템",
        "GNRC": "비상 발전기 · 에너지 저장",
        # 우라늄·원전
        "DNN": "우라늄 · 채굴 · 캐나다",
        "NNE": "소형 원자로 · SMR",
        "OKLO": "소형 원자로 · SMR",
        "SMR": "소형 모듈 원자로 · SMR",
        "UEC": "우라늄 · 채굴 · 미국",
        "UUUU": "우라늄 · 희토류 · 채굴",
        # 산업재
        "AME": "전자 계측 · 산업 장비",
        "CARR": "냉난방 · HVAC · 빌딩",
        "CGNX": "머신 비전 · 산업 자동화",
        "CMI": "디젤 엔진 · 수소",
        "CW": "방산 · 산업 · 원전",
        "DOV": "산업 · 다각화 기업",
        "FTV": "산업 기술 · 계측",
        "GEV": "전력 장비 · 풍력 · 가스터빈",
        "HON": "산업 · 항공 · 자동화",
        "HWM": "항공 부품 · 금속 가공",
        "IR": "산업 장비 · 압축기",
        "ITW": "산업 · 다각화 제조",
        "MMM": "산업 · 소비재 · 다각화",
        "NVT": "전기 연결 · 보호 장비",
        "OTIS": "엘리베이터 · 에스컬레이터",
        "PH": "모션 제어 · 유압",
        "POWL": "전력 배전 · 변전 · 데이터센터",
        "ROK": "산업 자동화 · 제어",
        "ROP": "산업 소프트웨어 · 다각화",
        "TDG": "항공 부품 · 방산",
        "TT": "냉난방 · HVAC · 기후 제어",
        "TXT": "항공 · 방산 · 산업",
        "AYI": "조명 · 빌딩 관리 시스템",
        "MYR": "전기 공사 · 인프라",
        "WIRE": "전선 · 케이블 · 구리",
        # 방산·우주 추가
        "HII": "군함 · 항공모함 · 잠수함",
        "KTOS": "드론 · 미사일 · 방산",
        "LHX": "통신 · 전자전 · 방산",
        "LUNR": "달 착륙선 · 우주",
        "RKLB": "소형 로켓 · 우주 발사",
        # 바이오텍
        "ALNY": "RNAi · 유전자 치료",
        "ALT": "비만 · 간질환 · 바이오",
        "ARVN": "단백질 분해 · PROTAC",
        "BEAM": "유전자 편집 · 베이스 에디팅",
        "BIIB": "신경과학 · 알츠하이머",
        "BMRN": "희귀질환 · 유전자 치료",
        "BNTX": "mRNA · 백신 · 항암",
        "CRSP": "유전자 편집 · CRISPR",
        "DCPH": "항암 · 키나아제 억제제",
        "EDIT": "유전자 편집 · CRISPR",
        "GPCR": "GPCR · 비만 · 신약",
        "ILMN": "유전체 · 시퀀싱",
        "IMVT": "자가면역 · FcRn 항체",
        "INCY": "항암 · 자가면역 · 신약",
        "KYMR": "단백질 분해 · 면역",
        "LNTH": "의료 영상 · 방사성 의약품",
        "MRNA": "mRNA · 백신 · 치료제",
        "NTLA": "유전자 편집 · CRISPR",
        "PEPG": "올리고뉴클레오타이드 · 근육 질환",
        "RCUS": "항암 · 면역 치료",
        "TERN": "NASH · 간질환 · 비만",
        "VKTX": "GLP-1 · 비만 · 간질환",
        # 헬스케어서비스
        "OSCR": "건강보험 · 테크",
        "LH": "임상 검사 · 진단",
        "UHS": "병원 · 정신건강",
        # 이커머스
        "PDD": "중국 · 이커머스 · 테무",
        # 통신인프라
        "SBAC": "셀타워 · 통신 인프라 · REIT",
        # 기타 산업
        "AXNX": "비뇨기과 · 의료기기 · 신경조절",
        "BWX": "원자력 부품 · SMR",
        "CGON": "AI · 자율주행 · 레이더",
        # ── 2026-05 누락 보강: us_sectors에는 있으나 US_DESC 미수록 종목 ───
        # eVTOL · 모빌리티
        "ACHR": "Archer · 전기 수직이착륙기",
        "JOBY": "Joby · eVTOL 에어택시",
        # AI · 양자 · 데이터센터
        "AI":   "C3.ai · 기업용 AI 플랫폼",
        "BBAI": "BigBear.ai · 국방 AI",
        "SOUN": "SoundHound · 음성 AI",
        "TEM":  "Tempus AI · 정밀의학",
        "IONQ": "이온트랩 양자컴퓨터",
        "RGTI": "Rigetti · 양자 프로세서",
        "QBTS": "D-Wave · 양자 어닐링",
        "APLD": "Applied Digital · AI 호스팅",
        "CRWV": "CoreWeave · GPU 클라우드",
        "NBIS": "Nebius · AI 클라우드",
        "SOLS": "Solaris · 데이터센터 전력",
        # 메모리 · 헬스 · 핀테크
        "SNDK": "SanDisk · 낸드 메모리",
        "HIMS": "Hims&Hers · 원격 의료",
        "CRCL": "Circle · USDC 스테이블코인",
        # ── 신규 추가 (2026-05-17) ──────────────────────────
        "CRDO": "데이터센터 케이블 · SerDes · AI",
        "AEIS": "반도체 RF · 플라즈마 전원",
        "NVMI": "반도체 계측 · 메트롤로지",
        "CAMT": "반도체 검사 · 패키징",
        "VECO": "반도체 장비 · MOCVD",
        "EME": "전기 시공 · 데이터센터",
        "PRIM": "전력망 · 인프라 시공",
        "FLR": "플랜트 · 엔지니어링",
        "J": "엔지니어링 · 인프라 컨설팅",
        "TMDX": "장기이식 · 의료기기",
        "CDNA": "이식 거부 진단 · 검사",
        "RVMD": "RAS 항암 · 표적치료",
        "NTRA": "유전자 검사 · NIPT · MRD",
        "EXAS": "대장암 진단 · Cologuard",
        "CRNX": "쿠싱병 · 내분비 치료제",
        "INSM": "희귀폐질환 · 항생제",
        "KRYS": "유전자치료 · 피부질환",
        "KVUE": "소비자 헬스 · 타이레놀 · J&J 분사",
        "BROS": "드라이브스루 커피",
        "IBKR": "전자 증권 브로커 · 마진론",
        "GLOB": "IT 컨설팅 · 디지털 엔지니어링",
        "LYFT": "라이드셰어 · 모빌리티",
        "GME": "게임 리테일 · 밈주식",

        # 2026-05 추가: 게시판 요청 종목
        "BKR": "유전 서비스 · LNG 설비 · 에너지 기술",
        "POWI": "고전압 전력반도체 · 에너지 효율 IC",
        "STM": "유럽 종합반도체 · MCU · 전력소자",
        "QXO": "건축자재 유통 · M&A 롤업",
        "LPTH": "적외선 광학 · 렌즈 · 방산 광부품",
        "SLDP": "전고체 배터리 · 차세대 셀",
        "SATS": "위성통신 · 디시 네트워크 · 5G",
        "MOD": "열관리 · 데이터센터 냉각 · 전장",
        "POET": "실리콘 포토닉스 · AI 광인터커넥트",

        # ── 2026-05 인기종목 보강 (네이버 시총상위) ──────────────────────
        "GOOG": "온라인 서비스 · 플랫폼",
        "CDNS": "소프트웨어",
        "MAR": "호텔 · 크루즈",
        "SNPS": "소프트웨어",
        "ORLY": "자동차 부품 · 소매",
        "DASH": "온라인 서비스 · 플랫폼",
        "CTAS": "경영 지원 서비스",
        "CBRS": "통합 하드웨어 및 소프트웨어",
        "PCAR": "중장비 · 차량",
        "TER": "반도체 장비 · 테스트",
        "ADSK": "소프트웨어",
        "FAST": "산업 기계 · 장비",
        "FER": "건설 · 엔지니어링",
        "ARGX": "바이오 · 신약 연구",
        "FLEX": "전자 장비 · 부품",
        "MDLN": "의료 장비 · 유통",
        "BIDU": "온라인 서비스 · 플랫폼",
        "FITB": "은행",
        "CCEP": "음료",
        "JD": "백화점",
        "KDP": "음료",
        "ERIC": "통신 · 네트워크 장비",
        "GFS": "반도체 장비 · 테스트",
        "ESLT": "항공우주 · 방산",
        "VOD": "무선 통신",
        "ACGL": "손해보험",
        "TCOM": "레저 · 엔터테인먼트",
        "CPRT": "온라인 서비스 · 플랫폼",
        "ONC": "바이오 · 신약 연구",
        "HBAN": "은행",
        "CASY": "식품 소매 · 유통",
        "NTRS": "자산운용 · 투자",
        "RPRX": "제약",
        "RYAAY": "항공사",
        "TSEM": "반도체",
        "SYM": "산업 기계 · 장비",
        "GEHC": "첨단 의료 장비",
        "MTSI": "반도체",
        "VRSN": "IT 서비스 · 컨설팅",
        "CINF": "손해보험",
        "STRL": "건설 · 엔지니어링",
        "WTW": "보험 · 중개",
        "FTAI": "항공우주 · 방산",
        "UTHR": "제약",
        "TW": "금융 시장 운영",
        "CTSH": "IT 서비스 · 컨설팅",
        "EXE": "오일 · 가스 E&P",
        "PFG": "생명 · 건강 보험",
        "FFIV": "IT 서비스 · 컨설팅",
        "WWD": "항공우주 · 방산",
        "FCNCA": "은행",
        "FWONK": "방송 · 미디어",
        "ROIV": "제약",
        "VTRS": "제약",
        "FUTU": "핀테크",
        "EVRG": "전력 유틸리티",
        "LNT": "전력 유틸리티",
        "WMG": "엔터테인먼트 제작",
        "VNOM": "오일 · 가스 E&P",
        "TTMI": "반도체",
        "LOGI": "컴퓨터 하드웨어",
        "PTC": "소프트웨어",
        "EWBC": "은행",
        "SSNC": "IT 서비스 · 컨설팅",
        "TPG": "자산운용 · 투자",
        "GMAB": "바이오 · 신약 연구",
        "NBIX": "제약",
        "NDSN": "산업 기계 · 장비",
        "LAMR": "리츠 · 부동산",
        "HST": "리츠 · 부동산",
        "ASND": "바이오 · 신약 연구",
        "GRAB": "소프트웨어",
        "JAZZ": "제약",
        "AUR": "소프트웨어",
        "LECO": "산업 기계 · 장비",
        "RGC": "바이오 · 신약 연구",
        "ARXS": "산업 기계 · 장비",
        "HTHT": "호텔 · 크루즈",
        "AAOI": "전자 장비 · 부품",
        "HAS": "완구 · 키즈 제품",
        "ARCC": "자산운용 · 투자",
        "RMBS": "반도체",
        "TIGO": "무선 통신",
        "GLPI": "리츠 · 부동산",
        "FOXA": "방송 · 미디어",
        "IESC": "건설 · 엔지니어링",
        "PAYP": "경영 지원 서비스",
        "FOX": "방송 · 미디어",
        "TRMB": "소프트웨어",
        "SMMT": "바이오 · 신약 연구",
        "WSE": "온라인 서비스 · 플랫폼",
        "GH": "의료 장비 · 유통",
        "BBIO": "제약",
        "EXEL": "바이오 · 신약 연구",
        "SMTC": "반도체",
        "ZBRA": "전자 장비 · 부품",
        "FRVO": "전력 유틸리티",
        "IONS": "바이오 · 신약 연구",
        "SANM": "전자 장비 · 부품",
        "MEDP": "바이오 · 신약 연구",
        "AGNC": "리츠 · 부동산",
        "COO": "의료 장비 · 유통",
        "MDGL": "바이오 · 신약 연구",
        "AXSM": "제약",
        "VIAV": "통신 · 네트워크 장비",
        "VICR": "전기 부품 · 장비",
        "ENLT": "민자 발전",
        "COKE": "음료",
        "ERIE": "손해보험",
        "LFUS": "전자 장비 · 부품",
        "DRS": "항공우주 · 방산",
        "BTSG": "의료 시설 · 서비스",
        "PSKY": "엔터테인먼트 제작",
        "SEIC": "자산운용 · 투자",
        "GLXY": "투자은행 · 중개",
        "AAON": "전기 부품 · 장비",
        "ARWR": "바이오 · 신약 연구",
        "PODD": "의료 장비 · 유통",
        "ENSG": "의료 시설 · 서비스",
        "XE": "중전기 장비",
        "CYTK": "바이오 · 신약 연구",
        "WYNN": "카지노 · 게이밍",
        "SFD": "식품 가공",
        "FCFS": "소비자 금융",
        "WTFC": "은행",
        "BRK-A": "복합 소비재 대기업",
        "BABA": "온라인 서비스 · 플랫폼",
        "HSBC": "은행",
        "SMFG": "은행",
        "RY": "은행",
        "SHEL": "통합 오일 · 가스",
        "MUFG": "은행",
        "BHP": "광물 채굴",
        "TTE": "통합 오일 · 가스",
        "SAP": "소프트웨어",
        "TD": "은행",
        "SAN": "은행",
        "UBS": "자산운용 · 투자",
        "WELL": "리츠 · 부동산",
        "APH": "전자 장비 · 부품",
        "BTI": "담배",
        "RIO": "광물 채굴",
        "HDB": "은행",
        "UL": "생활 필수 소비재",
        "BBVA": "은행",
        "ENB": "오일 · 가스 수송",
        "BP": "정유 · 가스 마케팅",
        "BN": "자산운용 · 투자",
        "BMO": "은행",
        "MFG": "은행",
        "CM": "은행",
        "CNQ": "오일 · 가스 E&P",
        "EQNR": "통합 오일 · 가스",
        "BNS": "은행",
        "IBN": "은행",
        "WM": "환경 서비스",
        "JCI": "전기 부품 · 장비",
        "EPD": "오일 · 가스 수송",
        "ING": "은행",
        "E": "통합 오일 · 가스",
        "NGG": "복합 유틸리티",
        "SU": "정유 · 가스 마케팅",
        "AMX": "통합 통신",
        "MRSH": "보험 · 중개",
        "BCS": "은행",
        "NOK": "통신 · 네트워크 장비",
        "CP": "화물 · 물류",
        "LYG": "은행",
        "CVNA": "자동차 부품 · 소매",
        "PBR": "통합 오일 · 가스",
        "EMR": "전기 부품 · 장비",
        "HLT": "호텔 · 크루즈",
        "TRP": "오일 · 가스 수송",
        "RCL": "호텔 · 크루즈",
        "VALE": "철강",
        "AON": "보험 · 중개",
        "ASX": "반도체 장비 · 테스트",
        "CRH": "건설 자재",
        "B": "금 채굴",
        "CNI": "화물 · 물류",
        "FIX": "건설 · 엔지니어링",
        "RSG": "환경 서비스",
        "RACE": "자동차 · 트럭 제조",
        "NWG": "은행",
        "URI": "경영 지원 서비스",
        "DB": "은행",
        "GWW": "산업 기계 · 장비",
        "TEL": "전자 장비 · 부품",
        "KEYS": "전자 장비 · 부품",
        "RELX": "IT 서비스 · 컨설팅",
        "CVE": "오일 · 가스 E&P",
        "AZO": "자동차 부품 · 소매",
        "AJG": "보험 · 중개",
        "TAK": "제약",
        "PSA": "리츠 · 부동산",
        "INFY": "IT 서비스 · 컨설팅",
        "ABEV": "양조 · 주류",
        "CAH": "제약",
        "MT": "철강",
        "WAB": "중장비 · 차량",
        "GRMN": "통신 · 네트워크 장비",
        "WDS": "오일 · 가스 E&P",
        "UMC": "반도체",
        "FERG": "건설 자재 · 비품",
        "AMP": "자산운용 · 투자",
        "ITUB": "은행",
        "VTR": "리츠 · 부동산",
        "IX": "소비자 금융",
        "HLN": "제약",
        "TEVA": "제약",
        "WCN": "환경 서비스",
        "BDX": "의료 장비 · 유통",
        "PUK": "생명 · 건강 보험",
        "VIK": "레저 · 엔터테인먼트",
        "PEG": "복합 유틸리티",
        "KB": "은행",
        "TKO": "엔터테인먼트 제작",
        "UI": "통신 · 네트워크 장비",
        "EQT": "오일 · 가스 E&P",
        "PCG": "전력 유틸리티",
        "VG": "오일 · 가스 E&P",
        "HAL": "유전 서비스 · 장비",
        "JBL": "전자 장비 · 부품",
        "VMC": "건설 자재",
        "SYY": "식품 소매 · 유통",
        "LVS": "카지노 · 게이밍",
        "CHT": "통합 통신",
        "MLM": "건설 자재",
        "Q": "반도체",
        # ── 2026-05-20 누락 보강: 펨코·레딧 바이럴 종목 ───────────────
        "PGY":  "AI 신용평가 · 자산담보대출 핀테크",
        "OPEN": "부동산 iBuying · AI 가격결정",
        "DJT":  "Truth Social · 보수 SNS · 미디어",
        "BB":   "IoT · QNX 차량용 OS · 사이버보안",
        "KSS":  "미국 백화점 · 종합 리테일",
        "DNUT": "도넛 · 글로벌 프랜차이즈",
        "CHWY": "반려동물 이커머스 · 정기배송",
        "PTON": "홈피트니스 · 인터랙티브 바이크",
        "LAES": "양자내성 암호 · 보안 칩 · IoT",
        "AEVA": "4D FMCW 라이다 · 자율주행 센서",
        "LAZR": "라이다 · 자율주행 인지 시스템",
        "RUM":  "동영상 플랫폼 · 클라우드 서비스",
        "RDFN": "온라인 부동산 중개 플랫폼",
        "SES":  "리튬메탈 배터리 · UAM · EV",
        "GRRR": "AI 비전 · 스마트시티 · 영상분석",
        # ── 2026-05-20 핫 종목 추가 ──────────────────────────
        "AEHR": "SiC 웨이퍼 번인 테스트 장비",
        "LEU":  "HALEU 우라늄 농축 · SMR 연료",
        "PL":   "초소형 위성 군집 · 지구 관측",
    }

    # ─────────────────────────────────────────────────────────────────────
    # 섹터 데이터 초기화 (원본 종목 리스트 유지)
    # ─────────────────────────────────────────────────────────────────────
    def _init_sector_data(self):
        # ─────────────────────────────────────────────────────────────────
        # us_sectors v20.1 — Russell 1000 수준 전면 재편 (섹터 13개, 알파벳 정렬)
        # 2025~2026 주도 테마: Mag7·AI반도체·방산·GLP-1·금융거래소·소비재 필수재
        # ─────────────────────────────────────────────────────────────────
        self.us_sectors = {

            # ── 1. AI & 빅테크 ────────────────────────────────────────────
            "🤖 AI & Mega Tech": {
                # Magnificent 7 — S&P500 시가총액 35%+ 지배
                "Mag 7":              ["AAPL","AMZN","GOOGL","META","MSFT","NVDA","TSLA"],

                # AI 플랫폼·클라우드·엔터프라이즈
                "AI Platform & Cloud":["ADBE","AI","AMZN","BBAI","BOX","CFLT","CRM","DDOG",
                                       "DOCN","ESTC","GOOGL","GRRR","GTLB","HUBS","IBM","INTU",
                                       "MDB","MNDY","MSFT","NOW","NTNX","ORCL","PATH","PLTR",
                                       "RBRK","SNOW","SOUN","TEAM","TEM","WDAY"],

                # AI 인프라·데이터센터·광통신 (AAOI 데이터센터 옵티컬 포함)
                "AI Infrastructure":  ["AAOI","AMT","ANET","APLD","CLSK","CLS","COHR","CORZ","CRDO","CRWV",
                                       "CSCO","DELL","DLR","EQIX","GLW","HPE","IONQ","IREN","IRM",
                                       "LITE","NBIS","NTAP","PSTG","QBTS","RGTI","SMCI","STX",
                                       "VRT","WDC","WULF"],

                # 사이버보안
                "Cybersecurity":      ["AKAM","BB","CHKP","CRWD","FTNT","GEN","LAES","NET",
                                       "OKTA","PANW","QLYS","RPD","S","TENB","VRNS","ZS"],

                # SaaS·소프트웨어 고성장
                "SaaS & Software":    ["ADBE","ADSK","ANSS","APP","BILL","CDNS","CRM","CTSH","DOCU","DUOL",
                                       "EPAM","FFIV","GLOB","GTLB","HUBS","JKHY","MGNI","MNDY","NOW",
                                       "PCOR","PCTY","PTC","RAMP","RNG","SHOP","SNPS","TRMB","TWLO",
                                       "TTD","TYL","VEEV","ZBRA","ZM"],
            },

            # ── 2. AI 반도체 ──────────────────────────────────────────────
            "🔬 AI Semiconductors": {
                # AI GPU·HBM 핵심 — NVDA 생태계 직접 수혜
                "AI GPU & HBM Core":  ["ALAB","AMD","ARM","AVGO","INTC","KLAC","LRCX",
                                       "MRVL","MU","NVDA","TSM"],

                # 팹리스·아날로그·전력반도체 (indie Semi 차량용 SoC 포함)
                "Fabless & Analog":   ["ADI","AMBA","CRUS","DIOD","INDI","LSCC","MCHP","MPWR",
                                       "NXPI","ON","POET","POWI","QCOM","QRVO","SLAB","SITM",
                                       "STM","SWKS","TXN","WOLF"],

                # 반도체 장비·소재·검사
                "Semicon Equipment":  ["ACLS","AEHR","AEIS","AMAT","ASML","AZTA","CAMT","COHU","ENTG",
                                       "FORM","ICHR","KLAC","KLIC","LRCX","MKSI","NVMI","ONTO","UCTT","VECO"],

                # 메모리·스토리지·패키징
                "Memory & Packaging": ["AMKR","CEVA","MU","NTAP","NVTS","PSTG","SIMO",
                                       "SMCI","SNDK","STX","WDC"],
            },

            # ── 3. 핀테크 & 금융 ──────────────────────────────────────────
            "💰 Finance & Fintech": {
                # 크립토·블록체인 (BTC ETF + ETH 트레저리 BMNR 포함)
                "Crypto & Blockchain":["BITF","BMNR","BTBT","BTDR","CIFR","CLSK","COIN",
                                       "CORZ","CRCL","HOOD","HUT","IREN","MARA","MSTR","RIOT","SOLS","WULF"],

                # 핀테크·결제·BNPL
                "Fintech & Payments": ["AFRM","ALLY","AXP","BILL","COF","FISV",
                                       "FOUR","GPN","IBKR","MA","MELI","NU","PGY","PYPL","SE",
                                       "SOFI","SYF","TOST","UPST","V","XYZ"],

                # 금융거래소·신용평가·데이터 — Russell 1000 핵심 (신규)
                "Exchanges & Data":   ["CBOE","CME","FDS","ICE","MCO","MSCI",
                                       "NDAQ","SPGI","TRI","VRSK"],

                # 월가 대형 은행·투자은행
                "Mega Banks & IB":    ["BAC","BK","BMO","C","CFG","CMA","EWBC","FITB","GS","HBAN","HSBC",
                                       "JPM","KEY","MS","MTB","NTRS","PB","PNC","PNFP","RF","RY","SCHW",
                                       "SNV","STT","TD","TFC","USB","WAL","WFC","ZION"],

                # 대안자산·PE·자산운용
                "Alt Assets & PE":    ["AMG","APO","ARES","BAM","BEN","BLK","BX",
                                       "CG","IVZ","KKR","LPLA","OWL","RJF","TROW"],

                # 보험·보험중개 (브로커 포함 — Russell 1000 핵심)
                "Insurance":          ["ACGL","AFL","AIG","AJG","ALL","AON","BRK-B","BRO","CB","CINF",
                                       "EG","EQH","ERIE","GL","HIG","MET","MFC","MMC","PGR","PRU",
                                       "RNR","SLF","TRV","WRB","WTW"],
            },

            # ── 4. 산업 & 방산 ────────────────────────────────────────────
            "🏭 Industrial & Defense": {
                # 항공우주·방산 (지정학 리스크 고조, 국방예산 급증)
                "Aerospace & Defense":["ACHR","ASTS","AXON","BA","BAH","BWXT","CACI","CW",
                                       "GD","GE","HII","HWM","JOBY","KTOS","LDOS","LHX","LMT",
                                       "LPTH","LUNR","NOC","PL","RKLB","RTX","SAIC","TDG","TXT"],

                # 전력 인프라·그리드 (AI 데이터센터 전력 수요 폭증)
                "Power Grid & Infra": ["AYI","EME","ETN","GEV","GNRC","HON","HUBB",
                                       "MYR","NVT","POWL","PRIM","PWR","VRT","WIRE"],

                # 산업 복합기업·자동화·기계·환경서비스 (Russell 1000 보강)
                "Industrials":        ["ALLE","ALSN","AME","AOS","CAT","CARR","CGNX","CMI","DE","DOV",
                                       "FAST","FLR","FTV","GEV","GFL","GGG","GWW","HEI","HON","IR",
                                       "ITW","J","LKQ","MAS","MMM","MOD","OTIS","PCAR","PH","PNR",
                                       "POOL","QXO","ROK","ROP","RSG","SNA","SWK","TT","TTC","URI",
                                       "WAB","WCN","WM"],

                # 물류·운송·해운 (ZIM 고배당 컨테이너선 포함)
                "Transportation":     ["AAL","CHRW","CSX","DAL","FDX","JBHT","JBLU","LUV","LYFT",
                                       "NSC","ODFL","SAIA","UAL","UBER","UNP","UPS","XPO","ZIM"],
            },

            # ── 5. 에너지 ────────────────────────────────────────────────
            "⚡ Energy": {
                # 오일·가스 메이저·E&P (Russell 1000 + 브라질·셰일 보강)
                "Oil & Gas Majors":   ["APA","AR","BKR","CHK","CIVI","COP","CRGY","CTRA","CVX","DVN",
                                       "EC","EOG","EQT","FANG","HES","MPC","MRO","MTDR","OXY","PBR",
                                       "PR","PSX","RRC","SLB","SM","TPL","VLO","XOM"],

                # 미드스트림·파이프라인 (안정적 배당, 리쇼어링 수혜)
                "Midstream & Pipeline":["ENB","ET","KMI","LNG","MPLX","OKE","PAA","PBA","TRGP","TRP","VG","WMB"],

                # 클린에너지·태양광·풍력
                "Clean Energy":       ["ARRY","BE","ENPH","FCEL","FLNC","FSLR","GEV",
                                       "NEE","NOVA","NXT","PLUG","RUN","SEDG","STEM"],

                # 원자력·우라늄·SMR (AI 전력 수요 → 원전 르네상스)
                "Nuclear & Uranium":  ["BWXT","BWX","CCJ","CEG","DNN","LEU","NNE","OKLO",
                                       "SMR","TLN","UEC","UUUU","VST"],

                # 유틸리티 (Russell 1000 전면 보강)
                "Utilities":          ["AEE","AEP","AES","ATO","AWK","CMS","CNP","D","DTE","DUK","ED",
                                       "EIX","ES","ETR","EVRG","EXC","FE","LNT","NEE","NI","NRG",
                                       "PCG","PEG","PNW","PPL","SO","SRE","WEC","WTRG","XEL"],
            },

            # ── 6. 헬스케어 & 바이오 ──────────────────────────────────────
            "🧬 Healthcare & Biotech": {
                # 빅파마 (비만치료제·ADC·면역항암 메가트렌드)
                "Big Pharma":         ["ABBV","AMGN","AZN","BMY","GILD","GSK","JNJ",
                                       "LLY","MRK","NVO","NVS","PFE","REGN","SAVA","SNY","VRTX"],

                # GLP-1·비만치료제 (2024~2026 최대 주도 테마)
                "GLP-1 & Obesity":    ["ALT","AMGN","CGON","GPCR","LNTH","LLY",
                                       "NVO","PEPG","TERN","VKTX"],

                # ADC·면역항암·유전자치료
                "ADC & Gene Therapy": ["ALNY","ARVN","BEAM","BIIB","BMRN","BNTX","CRNX","CRSP",
                                       "DCPH","EDIT","ILMN","IMVT","INCY","INSM","KRYS","KYMR","MRNA",
                                       "NTLA","RCUS","RVMD"],

                # 의료기기·수술로봇·진단·라이프사이언스 (Russell 1000 보강)
                "Medical Devices":    ["A","ABT","ALGN","AXNX","BAX","BDX","BSX","CAH","CDNA","DXCM",
                                       "DHR","EW","EXAS","GEHC","HOLX","HSIC","IDXX","ISRG","LH","MDT",
                                       "MTD","NTRA","NVCR","PEN","PODD","RMD","RVTY","STE","SYK","TFX",
                                       "TMDX","TMO","WAT","ZBH","ZTS"],

                # 헬스케어 서비스·PBM·CRO·CDMO·정신건강 (Acadia 행동건강 포함)
                "Healthcare Services":["ACHC","CI","CNC","COR","CTLT","CVS","DVA","ELV","HCA","HIMS",
                                       "HUM","IQV","MCK","MOH","OSCR","THC","UHS","UNH"],
            },

            # ── 7. 소비재 & 리테일 ────────────────────────────────────────
            "🛍️ Consumer & Retail": {
                # 이커머스·여행·예약
                "E-commerce & Travel":["ABNB","AMZN","BABA","BIDU","BKNG","CHWY","CPNG","DASH",
                                       "EBAY","ETSY","EXPE","GRAB","JD","MELI","PDD","SE","SHOP","W"],

                # 대형 리테일·디스카운트·전문점
                "Retail Giants":      ["AEO","ANF","BBWI","BBY","BJ","BURL","COST","DG","DKS","DLTR",
                                       "FIVE","FND","GME","GPS","HD","KR","KSS","LOW","OLLI","RH",
                                       "ROST","TGT","TJX","TSCO","URBN","WMT","WSM"],

                # 레스토랑
                "Restaurants":        ["BROS","CAVA","CMG","DNUT","DPZ","DRI","MCD","QSR","SBUX",
                                       "SHAK","TXRH","WING","YUM"],

                # 자동차·EV (중고차 플랫폼 Carvana 포함)
                "Auto & EV":          ["AEVA","APTV","CVNA","F","GM","HMC","LAZR","LCID","LEA","LI","NIO",
                                       "ON","OUST","RIVN","STLA","TM","TSLA","VC","XPEV"],

                # 럭셔리·스포츠웨어·뷰티 (e.l.f. 인디뷰티 포함)
                "Luxury & Apparel":   ["BIRK","CPRI","CROX","EL","ELF","GIL","LULU","MOV","NKE","ONON","PTON",
                                       "PVH","RL","SKX","TPR","TPX","ULTA","VFC"],

                # 호텔·카지노·크루즈 (여행 리오프닝 + 일본·마카오 카지노 재개)
                "Hotels & Gaming":    ["BYD","CCL","CZR","H","HLT","LVS","MAR","MGM","NCLH","PENN",
                                       "RCL","WYNN"],
            },

            # ── 8. 소비자 필수재 & 식음료 (신규) ─────────────────────────────
            "🥤 Consumer Staples": {
                # 음료·주류 (경기방어 + 글로벌 브랜드)
                "Beverages & Spirits":["BUD","CCEP","CELH","DEO","KDP","KO","MNST","PEP","SAM","STZ","TAP"],

                # 식품·생활용품·식품유통 (Beyond Meat 식물성단백 포함)
                "Food & Household":   ["BYND","CAG","CHD","CL","CLX","CPB","GIS","HSY","K","KHC","KMB",
                                       "KVUE","MDLZ","MKC","PG","SJM","SYY","TSN","WBA"],

                # 칸나비스 (대마초 합법화·연방 재분류 모멘텀, 레딧 인기)
                "Cannabis":           ["ACB","CGC","SNDL","TLRY"],

                # 비료·농업·식량 (식량안보 테마)
                "Agriculture & Agri": ["ADM","BG","CF","CTVA","FMC","MOS","NTR","SMG"],
            },

            # ── 9. 미디어 & 엔터테인먼트 ─────────────────────────────────
            "🎮 Media & Entertainment": {
                # 소셜미디어·광고 플랫폼 (AI 광고 효율화)
                "Social & Ad Tech":   ["CRTO","DJT","DV","GOOGL","IAC","IAS","META","MGNI",
                                       "PINS","PUBM","RDDT","RUM","SNAP","TTD","ZD"],

                # 게임·e스포츠
                "Gaming":             ["DKNG","EA","MSFT","NTES","PLTK","RBLX","TCEHY","TTWO","U"],

                # 스트리밍·콘텐츠·미디어 (전통 미디어 보강)
                "Streaming & Content":["AMC","CHTR","CNK","DIS","FOX","FOXA","FUBO","IMAX","LYV",
                                       "NFLX","NWSA","PARA","ROKU","SIRI","SONY","SPOT","WBD"],
            },

            # ── 10. 부동산 ───────────────────────────────────────────────
            "🏠 Real Estate": {
                # 데이터센터 REIT (AI 인프라 수혜)
                "Data Center REITs":  ["AMT","CCI","DLR","EQIX","IRM","SBAC"],

                # 산업용·물류 REIT
                "Industrial REITs":   ["EXR","FR","PLD","REXR","STAG"],

                # 주거용 REIT
                "Residential REITs":  ["AMH","AVB","CPT","ELS","EQR","ESS","INVH",
                                       "MAA","SUI","UDR"],

                # 리테일·상업·호텔 REIT
                "Retail & Office":    ["ADC","ARE","BXP","FRT","HST","KIM","KRC","NNN","O","REG",
                                       "SPG","VICI","VNO","WPC"],

                # 헬스케어 REIT (시니어 하우징·바이오·헬스케어 인프라)
                "Healthcare REITs":   ["DOC","MPW","OHI","VTR","WELL"],

                # 주택건설·모기지
                "Homebuilders":       ["CVCO","DHI","KBH","LEN","MHO","MTH","NVR","OPEN",
                                       "PHM","RDFN","RKT","SKY","TOL","UWM"],
            },

            # ── 11. 소재 & 원자재 ─────────────────────────────────────────
            "🧪 Materials & Commodities": {
                # 특수·산업화학·향료
                "Chemicals":          ["ALB","APD","ASH","AVNT","CC","CE","CRH","DD","DOW","ECL","EMN",
                                       "FUL","HUN","IFF","LIN","LYB","OLN","PPG","RPM","SHW","TROX"],

                # 건자재·골재 (건설·인프라 직접 수혜)
                "Construction Materials":["EXP","MLM","VMC"],

                # 패키징·용기 (리쇼어링·이커머스 수혜)
                "Packaging":          ["ATR","BALL","CCK","IP","PKG","SEE","SON"],

                # 금속·광업 (구리·알루미늄·희토류)
                "Metals & Mining":    ["AA","CLF","CMC","FCX","MP","NUE","RS",
                                       "SCCO","STLD","TECK","X"],

                # 금·귀금속 (인플레이션·지정학 헤지)
                "Gold & Precious":    ["AEM","AG","AU","FNV","GFI","GOLD","HL",
                                       "KGC","NEM","OR","PAAS","RGLD","WPM"],

                # 리튬·배터리 소재 (EV·ESS 수요)
                "Lithium & Battery":  ["ALB","ENVX","LAC","QS","SES","SLDP","SQM"],
            },

            # ── 12. 통신 & 5G ─────────────────────────────────────────────
            "📡 Telecom & 5G": {
                # 통신 대형주 (5G 구축 완료, 안정 배당)
                "Telecom Giants":     ["BCE","CHTR","CMCSA","LBRDK",
                                       "SHEN","T","TU","TMUS","VZ"],

                # 5G·네트워크 장비·위성통신
                "5G & Satellite":     ["ASTS","CALX","CIEN","CSCO","EXTR","GSAT",
                                       "JNPR","MSI","NOK","QCOM","SATS","SPOK","VSAT"],
            },

            # ── 13. 비즈니스 서비스 & 데이터 (신규) ──────────────────────────
            "💼 Business & Data Services": {
                # HR·급여·인력관리 (경기 방어 + 고마진 구독)
                "HR & Payroll":       ["ADP","MAN","PAYX","PCTY","WK"],

                # 경영컨설팅·IT서비스·정부IT
                "Consulting & IT Svc":["ACN","BAH","CACI","CDW","LDOS","SAIC"],

                # 데이터·분석·신용평가 (독점적 데이터 모트)
                "Data & Analytics":   ["EFX","EXLS","FDS","MCO","SPGI","TRI","VRSK"],

                # 기업 프로세스·결제인프라
                "Business Process":   ["BR","CBRE","CSGP","FIS","FISV","GPN","WEX"],
            },
        }
        self.us_sector_category_kr = {
            "AI & Mega Tech": "AI·메가테크",
            "AI Semiconductors": "AI 반도체",
            "Finance & Fintech": "금융·핀테크",
            "Industrial & Defense": "산업재·방산",
            "Energy": "에너지",
            "Healthcare & Biotech": "헬스케어·바이오",
            "Consumer & Retail": "소비재·리테일",
            "Consumer Staples": "필수소비재",
            "Media & Entertainment": "미디어·엔터",
            "Real Estate": "부동산",
            "Materials & Commodities": "소재·원자재",
            "Telecom & 5G": "통신·5G",
            "Business & Data Services": "비즈니스·데이터 서비스",
        }
        self.us_sector_labels_kr = {
            "Mag 7": "매그니피센트 7",
            "AI Platform & Cloud": "AI 플랫폼·클라우드",
            "AI Infrastructure": "AI 인프라",
            "Cybersecurity": "사이버보안",
            "SaaS & Software": "SaaS·소프트웨어",
            "AI GPU & HBM Core": "AI GPU·HBM 핵심",
            "Fabless & Analog": "팹리스·아날로그",
            "Semicon Equipment": "반도체 장비",
            "Memory & Packaging": "메모리·패키징",
            "Crypto & Blockchain": "크립토·블록체인",
            "Fintech & Payments": "핀테크·결제",
            "Exchanges & Data": "거래소·데이터",
            "Mega Banks & IB": "대형은행·IB",
            "Alt Assets & PE": "대체자산·PE",
            "Insurance": "보험",
            "Aerospace & Defense": "항공우주·방산",
            "Power Grid & Infra": "전력망·인프라",
            "Industrials": "산업재",
            "Transportation": "운송",
            "Oil & Gas Majors": "석유·가스 메이저",
            "Midstream & Pipeline": "미드스트림·파이프라인",
            "Clean Energy": "청정에너지",
            "Nuclear & Uranium": "원전·우라늄",
            "Utilities": "유틸리티",
            "Big Pharma": "대형 제약",
            "GLP-1 & Obesity": "GLP-1·비만치료",
            "ADC & Gene Therapy": "ADC·유전자치료",
            "Medical Devices": "의료기기",
            "Healthcare Services": "헬스케어 서비스",
            "E-commerce & Travel": "이커머스·여행",
            "Retail Giants": "대형 리테일",
            "Restaurants": "외식",
            "Auto & EV": "자동차·EV",
            "Luxury & Apparel": "럭셔리·의류",
            "Beverages & Spirits": "음료·주류",
            "Food & Household": "식품·생활용품",
            "Agriculture & Agri": "농업·비료",
            "Social & Ad Tech": "소셜·애드테크",
            "Gaming": "게임",
            "Streaming & Content": "스트리밍·콘텐츠",
            "Data Center REITs": "데이터센터 리츠",
            "Industrial REITs": "산업용 리츠",
            "Residential REITs": "주거용 리츠",
            "Retail & Office": "리테일·오피스",
            "Homebuilders": "주택건설",
            "Chemicals": "화학",
            "Packaging": "패키징",
            "Metals & Mining": "금속·광산",
            "Gold & Precious": "금·귀금속",
            "Lithium & Battery": "리튬·배터리",
            "Telecom Giants": "통신 대형주",
            "5G & Satellite": "5G·위성",
            "HR & Payroll": "HR·급여",
            "Consulting & IT Svc": "컨설팅·IT 서비스",
            "Data & Analytics": "데이터·애널리틱스",
            "Business Process": "비즈니스 프로세스",
        }

        # ─────────────────────────────────────────────────────────────────
        # kr_sectors v20.1 — 전면 재검증
        #   기준: KRX 업종분류 / 네이버 증권 테마 / 2025~2026 시장 주도 테마
        #   원칙: ① 종목이 해당 테마 사업을 실제로 영위
        #         ② 대장주 우선, 직접 수혜 관련주로만 구성
        #         ③ 이종업종 혼입·의미없는 중복 제거
        # ─────────────────────────────────────────────────────────────────
        self.kr_sectors = {

            # ── 1. 반도체 ────────────────────────────────────────────────
            "🔬 반도체": {
                # SK하이닉스 HBM·삼성전자 메모리 + 후공정 핵심 밸류체인
                "메모리·HBM":         [                                       "000660.KS","000990.KS",
                                       "005930.KS","007660.KS",
                                       "110990.KQ","402340.KS",
                                       "166090.KQ","168360.KQ",
                                       "067310.KQ"],  # 하나마이크론(HBM 후공정 패키징), SK스퀘어(SK하이닉스 지주)

                # 팹리스(설계) + 파운드리 수혜
                "시스템반도체":       [                                       "031980.KQ","033640.KQ",
                                       "054450.KQ","080220.KQ",
                                       "089030.KQ","200710.KQ",
                                       "240810.KQ","396270.KQ",   # 넥스트칩, 아이닉스
                                       "440110.KQ","394280.KQ",   # 파두(SSD컨트롤러), 오픈엣지(AI IP)
                                       "399720.KQ","094360.KQ",   # 가온칩스(AI ASIC), 칩스앤미디어(비디오IP)
                                       "361390.KQ","102120.KQ",   # 모빌린트(엣지AI), 어보브반도체(MCU)
                                       "123860.KQ"],               # 아나패스(Display IC)

                # 장비·소재 — 노광·식각·세정·CVD + 포토레지스트·CMP + 테스트소켓 (오킨스전자)
                "반도체장비·소재":    [                                       "005290.KQ","014680.KS","322310.KQ",
                                       "036540.KQ","036810.KQ",
                                       "036930.KQ","178920.KS",
                                       "281820.KS","357780.KQ",
                                       "403870.KQ","950170.KQ",
                                       "240810.KQ","272290.KQ","357550.KQ",
                                       "058470.KQ","104830.KQ","140860.KQ",
                                       "108320.KS","095340.KQ","108860.KQ",
                                       "228760.KQ","064760.KQ","039030.KQ",
                                       "317330.KQ","112290.KQ","064290.KQ",
                                       "084370.KQ","053610.KQ",
                                       "098460.KQ","095610.KQ","218410.KQ","067390.KQ","195870.KS",
                                       "042700.KS","089030.KQ","131290.KQ",
                                       "083450.KQ","170920.KQ","327260.KQ",
                                       "039440.KQ","058970.KS",
                                       "417840.KQ"],              # GST, 엘티씨, RF머트리얼즈, 에스티아이, LX세미콘, 저스템

                # AI 서버·HBM용 기판·패키징
                "AI서버기판·패키징":  [                                       "008060.KS","009150.KS",
                                       "011070.KS","011790.KS",
                                       "131290.KQ","222800.KQ"],  # 대덕전자(AI서버기판), SKC(FC-BGA)
            },

            # ── 2. AI 인프라 ─────────────────────────────────────────────
            "🤖 AI 인프라": {
                # AI 클라우드·데이터센터·엔터프라이즈 AI·전자결제
                "AI플랫폼·클라우드":  [                                       "012510.KS","018260.KS",
                                       "022100.KS","035420.KS",
                                       "035720.KS","053800.KQ",
                                       "304100.KQ",
                                       "030520.KQ","402030.KQ",
                                       "315640.KQ","035600.KQ","035890.KQ"],  # 한글과컴퓨터, 코난테크놀로지, 딥노이드, KG이니시스(PG결제), 서희건설

                # 온디바이스AI·엣지AI 핵심 부품 + AI 가전·전장 + FPCB·OLED 소재
                "온디바이스AI":       [                                       "052710.KQ","054450.KQ",
                                       "323280.KQ","377480.KQ",
                                       "405100.KQ","432720.KQ",
                                       "066570.KS","011070.KS",
                                       "034220.KS","090460.KQ","213420.KQ",
                                       "049070.KQ","248070.KQ"],  # LG전자, LG이노텍, LG디스플레이, 비에이치(FPCB), 덕산네오룩스(OLED소재), 인탑스(모바일·로봇), 솔루엠(전자부품)

                # 5G·광통신·AI 네트워크 인프라 (KMW RF필터 포함)
                "통신·광네트워크":    [                                       "010170.KQ","017670.KS",
                                       "030200.KS","032640.KS",
                                       "084730.KQ","187790.KQ","056360.KQ","122990.KQ",
                                       "032500.KQ"],              # 뷰웍스, 대한광통신, 코위버, 와이솔, KMW(5G RF필터)
            },

            # ── 3. 전력 인프라 ───────────────────────────────────────────
            "⚡ 전력 인프라": {
                # 변압기·차단기·배전반·GIS — 글로벌 수주 사상 최대
                "변압기·전력기기":    [                                       "010120.KS","025540.KS",
                                       "033100.KQ","062040.KS",
                                       "103590.KS","199820.KQ",
                                       "267260.KS","298040.KS",
                                       "094820.KQ","302380.KS"],  # 제일일렉트릭, 한국단자공업, 비나텍(슈퍼커패시터), 파워로직스(DC-DC컨버터)

                # 초고압·해저·지중 전력케이블
                "전선·케이블":        [                                       "000500.KS","001440.KS",
                                       "006260.KS","006340.KS",
                                       "007610.KS","229640.KS",
                                       "119650.KS"],  # 대원전선, 선도전기, 가온전선

                # 원전 EPC·기자재·SMR
                "원전·SMR":           [                                       "015760.KS","034020.KS",
                                       "051600.KS","052690.KS",
                                       "083650.KQ","105840.KS"],  # 비에이치아이, 우진

                # 태양광·풍력·ESS·수소연료전지
                "신재생·ESS":         [                                       "009830.KS","096770.KS",
                                       "112610.KS","322000.KS",
                                       "336260.KS","373220.KS",
                                       "456040.KS","475150.KS",
                                       "100090.KQ"],  # 두산퓨얼셀(수소), 씨에스윈드(풍력), SK오션플랜트(해상풍력)

                # EV충전 인프라·수소모빌리티
                "EV충전·수소모빌리티":[                                       "120110.KS","234300.KQ",
                                       "271940.KS","298040.KS",
                                       "382900.KQ","462520.KS"],  # 코오롱인더(수소MEA), 효성중공업(수소충전소)
            },

            # ── 4. K-방산 ────────────────────────────────────────────────
            "🛡️ K-방산": {
                # 방산 수출 대형주
                "방산 대형주":        [                                       "000880.KS","012450.KS",
                                       "047810.KS","064350.KS",
                                       "064960.KS","079550.KS"],  # SNT모티브, 한화

                # 탄약·전자전·방산부품
                "방산 부품·전자전":   [                                       "005810.KS","005870.KS",
                                       "010820.KS","065450.KQ",
                                       "103140.KS","272210.KS",
                                       "094850.KQ","048830.KQ"],  # 퍼스텍, 빅텍, 아이쓰리시스템(적외선센서), 세일전자(군용통신)

                # 드론·소형위성·우주발사체
                "드론·우주":          [                                       "047810.KS","099320.KQ",
                                       "274090.KQ","321370.KQ",
                                       "377330.KQ","437730.KQ"],  # 이지트로닉스(항공전자), 센서뷰
            },

            # ── 5. 조선·해운 ─────────────────────────────────────────────
            "⚓ 조선·해운": {
                # LNG선·암모니아선·군함 — 빅3 수주 잔고 최대
                "대형 조선":          [                                       "009540.KS","010140.KS",
                                       "042660.KS","097230.KS",
                                       "329180.KS","439260.KS"],  # 대한조선, HJ중공업

                # 선박 엔진·기자재·서비스 + LNG보냉재 + 중형조선
                "조선 기자재":        [                                       "009070.KS","017960.KS",
                                       "071970.KS","077970.KS",
                                       "082740.KS","443060.KS",
                                       "073010.KQ","006370.KS",
                                       "033500.KS","010620.KS"],  # 동성화인텍(LNG보냉재 대장주), 현대미포조선(MR탱커·PC선)

                # 컨테이너·벌크·LNG 해운
                "해운·물류":          [                                       "000120.KS","005880.KS",
                                       "011200.KS","014160.KS"],  # 대한해운, CJ대한통운
            },

            # ── 6. 이차전지·ESS ──────────────────────────────────────────
            "🔋 이차전지·ESS": {
                # 배터리 셀 제조
                "배터리 셀":          [                                       "006400.KS","096770.KS",
                                       "247540.KQ","373220.KS"],  # SK이노베이션(SK온), 에코프로비엠

                # 양극재·음극재·전해질·분리막 + 전지박 (솔루스첨단소재)
                "배터리 소재":        [                                       "003670.KS","005070.KS",
                                       "051910.KS","066970.KS",
                                       "086520.KQ","278280.KQ",
                                       "361610.KS","450080.KS",
                                       "042940.KQ","005420.KS","008730.KS",
                                       "093370.KS","348370.KQ","336370.KS"],  # 솔루스첨단소재(동박·전지박), 엔켐(전해액)

                # 배터리 장비·리사이클
                "배터리 장비·리사이클":[                                       "137400.KQ","259630.KQ",
                                       "365340.KQ","372170.KQ",
                                       "121850.KQ","020150.KS"],  # 윤성에프앤씨, 성일하이텍(리사이클), 코윈테크, 롯데에너지머티리얼즈
            },

            # ── 7. 바이오·헬스케어 ───────────────────────────────────────
            "🧬 바이오·헬스케어": {
                # 항체·ADC·이중항체·GLP-1 신약 개발 + 셀트리온제약·광동제약
                "바이오 신약":        [                                       "000100.KS","028300.KQ",
                                       "068270.KS","141080.KQ",
                                       "196170.KQ","207940.KS",
                                       "298380.KQ","326030.KS",
                                       "069620.KS","003090.KS","086450.KQ","009420.KS",
                                       "145020.KQ",
                                       "086900.KQ","008930.KS","102710.KQ","092040.KQ",
                                       "302440.KQ","048530.KQ","039200.KQ","174900.KQ",
                                       "397030.KQ","085660.KQ","195940.KQ","048410.KQ",
                                       "950200.KQ","389030.KQ",
                                       "003850.KS","170900.KS","365550.KQ",
                                       "068760.KQ","009290.KS"],  # 셀트리온제약, 광동제약(비타500·헛개차)

                # 위탁생산(CMO)·위탁개발생산(CDMO)
                "CMO·CDMO":           [                                       "006280.KS","053030.KQ",
                                       "068270.KS","128940.KS",
                                       "145020.KQ","185750.KS",
                                       "207940.KS","302440.KS"],  # SK바이오사이언스, 휴젤

                # 비만치료제(GLP-1) — 2026 글로벌 메가테마
                "비만치료제·GLP-1":   [                                       "000100.KS","069620.KS",
                                       "087010.KQ","095700.KQ",
                                       "128940.KS","214370.KQ",
                                       "263720.KQ"],              # 제넥신, 유한양행, 디앤디파마텍(GLP-1 신약)

                # 의료기기·미용기기·체외진단 + 필러·에스테틱 (휴메딕스)
                "의료기기·디지털헬스":[                                       "039840.KQ","041830.KQ",
                                       "059090.KQ","145020.KQ",
                                       "145720.KS","214150.KQ",
                                       "137310.KS","064550.KQ","200670.KQ"],  # 인바디, 에스디바이오센서, 바이오니아, 휴메딕스(HA필러·에스테틱)
            },

            # ── 8. 로봇·자동화 ───────────────────────────────────────────
            "🦾 로봇·자동화": {
                # 산업용·협동 로봇·물류 자동화
                "산업로봇·물류자동화":[                                       "056190.KQ","058610.KQ",
                                       "090360.KQ","108490.KQ",
                                       "348340.KQ","454910.KS",
                                       "060280.KQ",
                                       "380440.KQ","270660.KQ"],  # 로보스타, 로보티즈, 큐렉소(의료로봇), 뉴로메카(협동로봇), 에브리봇(청소로봇)

                # 휴머노이드·모빌리티 로봇 핵심 부품
                "휴머노이드 부품":    [                                       "117730.KQ","125490.KQ",
                                       "277810.KQ","389500.KQ",
                                       "455900.KQ","459510.KQ"],  # 티로보틱스, 한라캐스트

                # 자율주행·전장 (한온시스템·HL만도·에스엘 차량 핵심부품 보강)
                "자율주행·전장":      [                                       "000270.KS","005380.KS",
                                       "009150.KS","011070.KS",
                                       "012330.KS","307950.KS","051360.KQ",
                                       "161390.KS","018880.KS","204320.KS","005850.KS","075180.KS",
                                       "267270.KS","014620.KS"],  # 한온시스템(공조), HL만도(ADAS), 에스엘(헤드램프), 새론오토모티브(브레이크)
            },

            # ── 9. K-소비재 ──────────────────────────────────────────────
            "🛍️ K-소비재": {
                # K-뷰티·K-패션·K-건기식 — 인디 브랜드·글로벌 OEM·ODM
                "K-뷰티":             [                                       "003350.KS","051900.KS",
                                       "090430.KS","192820.KS",
                                       "237880.KQ","278470.KS",
                                       "352480.KQ","483650.KS",
                                       "161890.KS","002790.KS","950140.KS","241710.KQ","123690.KS",
                                       "257720.KQ","018290.KQ","439090.KQ",
                                       "383220.KS","020000.KS","093050.KS",
                                       "200130.KQ","194700.KQ"],  # F&F, 한섬(패션 대장), LF(닥스·헤지스), 콜마비앤에이치·노바렉스(건기식 ODM)

                # K-푸드·음료 — 글로벌 K-컬처 확산 직접 수혜
                "K-푸드·음료":        [                                       "003230.KS","004370.KS",
                                       "005180.KS","007310.KS",
                                       "033780.KS",
                                       "097950.KS","271560.KS",
                                       "280360.KS","475560.KS",
                                       "005740.KS","339770.KS",
                                       "005440.KS","049770.KS","003960.KS"],  # KT&G, 현대그린푸드, 동원F&B, 사조대림

                # 면세·여행·항공·LCC
                "면세·여행":          [                                       "003490.KS","008770.KS",
                                       "039130.KS","079160.KS",
                                       "023530.KS","004170.KS","282330.KS",
                                       "035250.KS","114090.KS",
                                       "020560.KS","089590.KS","272450.KQ"],  # 아시아나항공, 제주항공, 진에어(LCC)
            },

            # ── 10. 금융·밸류업 ──────────────────────────────────────────
            "💰 금융·밸류업": {
                # 은행·금융지주
                "은행·금융지주":      [                                       "024110.KS","055550.KS",
                                       "071050.KS","086790.KS",
                                       "105560.KS","138040.KS",
                                       "175330.KS",  # 279570 매핑 오류 제거
                                       "316140.KS","323410.KS",
                                       "377300.KS","138930.KS","139130.KS"],  # 카카오페이(핀테크), BNK금융지주, DGB금융지주(iM뱅크)

                # 증권·자산운용
                "증권·자산운용":      [                                       "001500.KS","003540.KS",
                                       "005940.KS","006800.KS",
                                       "016360.KS","039490.KS"],  # 대신증권, 현대차증권

                # 손해·생명 보험
                "보험":               [                                       "000810.KS","001450.KS",
                                       "005830.KS","032830.KS",
                                       "082640.KS","088350.KS",
                                       "000060.KS"],  # 삼성생명, 동양생명, 메리츠화재(손해보험·밸류업)
            },

            # ── 11. 콘텐츠·엔터 ──────────────────────────────────────────
            "🎮 콘텐츠·엔터": {
                # K-팝·드라마·IP
                "K-엔터·IP":          [                                       "035760.KQ","035900.KQ",
                                       "041510.KQ","122870.KQ",
                                       "253450.KQ","352820.KS",
                                       "376300.KQ"],              # 스튜디오드래곤, CJ ENM, 디어유(팬덤플랫폼)

                # 게임
                "게임":               [                                       "036570.KS","069080.KQ",
                                       "112040.KQ","251270.KS",
                                       "259960.KS","263750.KQ",
                                       "293490.KQ","462870.KS",
                                       "192080.KQ","095660.KQ","225570.KQ",
                                       "078340.KQ","194480.KQ",
                                       "041140.KQ"],  # 더블유게임즈·네오위즈·넥슨게임즈·컴투스·데브시스터즈, 넥슨지티(캐주얼게임)
            },

            # ── 12. 양자컴퓨팅 ──────────────────────────────────────────
            "🔮 양자컴퓨팅": {
                # 양자암호·양자보안 — PQC(양자내성암호) 상용화
                "양자보안·암호":      [                                       "042510.KQ","053300.KQ",
                                       "054940.KQ","115500.KQ",
                                       "203650.KQ","456010.KQ"],  # 엑스게이트(양자VPN), 한국정보인증

                # 양자센서·양자컴퓨팅 하드웨어·소재
                "양자센서·하드웨어":  [                                       "066310.KQ","078150.KQ",
                                       ],  # 217190(그린플러스)·950160(코오롱티슈진) 매핑 오류 제거
            },

            # ── 13. 건설·건자재 ────────────────────────────────────────────
            "🏗️ 건설·건자재": {
                # 대형 건설사
                "대형 건설":          [                                       "000210.KS","000720.KS",
                                       "006360.KS","028050.KS",
                                       "028260.KS","047040.KS",
                                       "294870.KS","375500.KS"],  # 대우건설, DL

                # 건자재·시멘트·인테리어
                "건자재·시멘트":      [                                       "002380.KS","003070.KS",
                                       "004090.KS","004980.KS",
                                       "010780.KS","014820.KS"],  # 코오롱글로벌, 동원시스템즈
            },

            # ── 14. 철강·화학 ──────────────────────────────────────────────
            "⚙️ 철강·화학": {
                # 철강·비철금속
                "철강·비철":          [                                       "001230.KS","001430.KS",
                                       "002710.KS","004020.KS",
                                       "005490.KS","008350.KS",
                                       "010130.KS","058430.KS","004560.KS"],  # TCC스틸, 남선알미늄, 현대비앤지스틸(STS냉연) (076600 미확인 제거)

                # 석유화학·정밀화학
                "석유화학·정밀화학":  [                                       "003830.KS","006120.KS",
                                       "011170.KS","011780.KS",
                                       "024060.KQ","051910.KS",
                                       "069260.KS","298000.KS",
                                       "014830.KS","010060.KS"],  # 흥구석유, 대한화섬, 유니드(가성칼륨), OCI홀딩스(화학지주)
            },

            # ── 15. 유틸리티·가스 ──────────────────────────────────────────
            "🔥 유틸리티·가스": {
                # 가스·에너지 유틸리티
                "가스·에너지":        [                                       "004690.KS","017390.KS",
                                       "018670.KS","034590.KS",
                                       "036460.KS"],              # 삼천리, 서울가스 (005030 부산주공 상폐 제거)

                # 생활인프라·환경
                "생활인프라·환경":    [                                       "007070.KS","015020.KS",
                                       "021240.KS","069960.KS"],  # 현대백화점, GS리테일
            },

            # ── 16. 사이버보안 ──────────────────────────────────────────────────
            "🔒 사이버보안": {
                # 엔드포인트·네트워크·정보보호
                "엔드포인트·네트워크보안":[                                       "042510.KQ","053800.KQ",
                                       "136540.KQ",
                                       "150900.KQ","203650.KQ","170790.KQ"], # 라온시큐어, 드림시큐리티, 파이오링크(L4/L7 보안스위치)

                # AI 기반 위협분석·제로트러스트
                "AI위협분석·제로트러스트":[                                       "053300.KQ","054940.KQ",
                                       "115500.KQ","411080.KQ",
                                       "456010.KQ","488280.KQ"], # 엑스게이트(양자VPN), 한국정보인증
            },

            # ── 17. 우주·위성 ──────────────────────────────────────────────────
            "🛰️ 우주·위성": {
                # 위성 제조·통신·발사체 부품
                "위성·발사체":        [                                       "099320.KQ","189300.KQ",
                                       "211270.KQ","274090.KQ",
                                       "437730.KQ","474170.KQ"],  # 켄코아에어로스페이스, 삼현(발사체부품)

                # 항공기 제조·MRO·부품
                "항공MRO·부품":       [                                       "003490.KS",
                                       "047810.KS","077970.KS"],  # 대한항공(MRO), STX엔진(항공엔진)
            },

            # ── 18. 물류·유통 ──────────────────────────────────────────────────
            "🚚 물류·유통": {
                # 택배·종합물류·SCM
                "택배·종합물류":      [                                       "000120.KS","002320.KS",
                                       "004140.KS","009180.KS",
                                       "267250.KS"],              # 동방(항만하역)

                # 유통·이커머스·대형마트·편의점지주·가격비교
                "유통·이커머스":      [                                       "005300.KS","007070.KS",
                                       "015020.KS","035080.KQ",
                                       "069960.KS",
                                       "139480.KS","027410.KS","119860.KQ"],  # 이마트, BGF(편의점지주), 다나와(가격비교)
            },

            # ── 19. 스마트팜·애그테크 ────────────────────────────────────────
            "🌾 스마트팜·애그테크": {
                # 스마트온실·수직농장·농기계ICT
                "스마트팜·농기계":    [                                       "000490.KS","004370.KS",
                                       "054050.KQ","097950.KS",
                                       "186230.KQ","403490.KQ"],  # 농심(식품R&D), CJ제일제당(식품바이오)
            },

            # ── 20. 디지털헬스·AI의료 ────────────────────────────────────────
            "💊 디지털헬스·AI의료": {
                # AI 의료영상·진단·신약발견
                "AI의료영상·진단":    [                                       "099190.KQ","214150.KQ",
                                       "328130.KQ","338220.KQ"],  # 바이오인프라(디지털의료), 클래시스(에너지기반치료)

                # 헬스케어 플랫폼·병원IT·EMR
                "헬스케어플랫폼·EMR": [                                       "032620.KQ","032850.KQ",
                                       "033230.KQ"],              # 인성정보(원격의료)
            },

            # ── 21. 지주사·종합상사 ──────────────────────────────────────────
            "🏢 지주사·종합상사": {
                # 대형 지주사
                "대형 지주사":        [                                       "003550.KS","034730.KS",
                                       "078930.KS","004800.KS",
                                       "004990.KS","180640.KS",
                                       "028260.KS","000150.KS"],  # LG, SK, GS, 효성, 롯데지주, 한진칼, 삼성물산, 두산

                # 종합상사·무역
                "종합상사·무역":      [                                       "047050.KS","001120.KS",
                                       "001740.KS","011760.KS"],  # 포스코인터내셔널, LX인터내셔널, SK네트웍스, 현대코퍼레이션
            },

            # ── 22. 정유·에너지 ──────────────────────────────────────────────
            "⛽ 정유·에너지": {
                # 정유·정제
                "정유":               [                                       "010950.KS","096770.KS",
                                       "267250.KS"],              # S-Oil, SK이노베이션, HD현대(오일뱅크)

                # 에너지유통·화학
                "에너지유통·화학":    [                                       "017810.KS","006650.KS",
                                       "005090.KS"],              # E1(LPG유통), 대한유화, SGC에너지(집단에너지)
            },

            # ── 24. 건설기계·중공업 ──────────────────────────────────────────
            "🏗️ 건설기계·중공업": {
                # 굴착기·건설장비
                "건설기계":           [                                       "267270.KS","241560.KS"],  # HD현대건설기계, 두산밥캣

                # 중공업·산업기계 (두산에너빌리티는 원전·SMR 섹터로 분류 — 딥테크 보정 적용)
                "중공업·플랜트":      [                                       "298040.KS",
                                       "017800.KS","009160.KS"],  # 효성중공업, 현대엘리베이, SIMPAC(프레스)
            },

            # ── 23. 바이오 CDMO ──────────────────────────────────────────────
            "💉 바이오 CDMO": {
                # CDMO·위탁생산 전문
                "CDMO 전문":          [                                       "950210.KS","237690.KQ",
                                       "222040.KQ","207940.KS",
                                       "302440.KS"],              # 프레스티지바이오파마, 에스티팜(올리고CDMO), 바이넥스(항체CDMO), 삼성바이오로직스, SK바이오사이언스
            },
        }
        self.eu_sectors = {
            # ── 1. 럭셔리·자동차 ──────────────────────────────────────────────
            "🏎️ Luxury & Auto": {
                # 유럽 럭셔리 대형주 — LVMH·에르메스·Kering 삼두마차
                "Luxury Houses":    ["MC.PA","RMS.PA","KER.PA","CFR.SW","OR.PA","MONC.MI","ADS.DE","PUM.DE"],
                # 프리미엄 자동차 — 페라리·포르셰·BMW·벤츠
                "Premium Auto":     ["RACE.MI","P911.DE","BMW.DE","MBG.DE","VOW3.DE","STLAM.MI",
                                     "VOLVB.ST","VOLCAR-B.ST"],
            },
            # ── 2. 헬스케어 ────────────────────────────────────────────────────
            "🧬 Healthcare": {
                # 유럽 빅파마 — 노보노디스크(GLP-1)·로슈·노바티스
                "European Pharma":  ["NOVO-B.CO","ROG.SW","NOVN.SW","SAN.PA","GSK.L","AZN.L","BAYN.DE","UCB.BR"],
                # 의료기기·진단 — 지멘스헬시니어스·필립스·프레지니우스
                "MedTech & Devices":["SHL.DE","PHG.AS","FRE.DE","FME.DE","STMN.SW"],
            },
            # ── 3. 테크·반도체 ──────────────────────────────────────────────────
            "🤖 Tech & Semi": {
                # AI 핵심 공급망 — ASML 독점 EUV 노광장비
                "Semiconductors":   ["ASML.AS","ASMI.AS","BESI.AS","STMPA.PA","IFX.DE","AIXA.DE"],
                # 엔터프라이즈 소프트웨어 — SAP·Dassault·Adyen
                "IT Software":      ["SAP.DE","DSY.PA","TEMN.SW","ADYEN.AS","CAP.PA"],
                # 5G 네트워크 장비 — 노키아·에릭슨
                "Telecom Equip":    ["NOKIA.HE","ERIC-B.ST"],
            },
            # ── 4. 산업·방산 ────────────────────────────────────────────────────
            "🏭 Industrial & Defense": {
                # 방산 수출 급증 — 라인메탈·에어버스·BAE·롤스로이스
                "Defense":          ["RHM.DE","AIR.PA","BAES.L","RR.L","SAF.PA","LDO.MI","SAAB-B.ST"],
                # 중장비·자동화 — 지멘스·ABB·아틀라스코프코
                "Engineering":      ["SIE.DE","ABB.SW","ALFA.ST","SAND.ST","SKF-B.ST","ANDR.VI","ATCO-A.ST"],
            },
            # ── 5. 에너지 ────────────────────────────────────────────────────────
            "⚡ Energy": {
                # 오일메이저 — 토탈·셸·BP·에쿼노르
                "Oil & Gas":        ["TTE.PA","SHEL.L","BP.L","EQNR.OL","ENI.MI","GALP.LS","OMV.VI"],
                # 클린에너지·그리드 — E.ON·RWE·외르스테드
                "Clean Energy":     ["EOAN.DE","RWE.DE","ORSTED.CO","VER.VI","EDPR.LS","EDP.LS","SOLARIA.MC"],
            },
            # ── 6. 금융 ──────────────────────────────────────────────────────────
            "💰 Finance": {
                # 유럽 주요 은행 — BNP·도이체·산탄데르·ING·HSBC
                "Banks":            ["BNP.PA","DBK.DE","SAN.MC","INGA.AS","UCG.MI","HSBA.L",
                                     "ISP.MI","GLE.PA","NDA-SE.ST","CABK.MC"],
                # 보험·자산운용 — 알리안츠·AXA·취리히·뮌헨리
                "Insurance & AM":   ["AXA.PA","ALV.DE","ZURN.SW","MUV2.DE","SCOR.PA",
                                     "SAMPO.HE","NN.AS","ASR.AS"],
            },
            # ── 7. 소비재·식음료 ───────────────────────────────────────────────
            "🛍️ Consumer & Food": {
                # 글로벌 식음료 — 네슬레·하이네켄·AB인베브·디아지오
                "Food & Beverage":  ["NESN.SW","HEIA.AS","ABI.BR","BN.PA","RI.PA",
                                     "DGE.L","CARL-B.CO","CARR.PA","CPG.L"],
                # 생활용품·리테일 — 유니레버·바이어스도르프·인디텍스
                "FMCG & Retail":    ["ULVR.L","BEI.DE","RKT.L","ITX.MC","WPP.L","PRX.AS"],
            },
            # ── 8. 통신 ──────────────────────────────────────────────────────────
            "📡 Telecom": {
                # 유럽 통신 대형주 — 도이체텔레콤·오렌지·보다폰·텔레포니카
                "Telecom Giants":   ["DTE.DE","ORA.PA","VOD.L","TEF.MC","SCMN.SW",
                                     "TELIA.ST","BT-A.L","PROX.BR"],
            },
            # ── 9. 소재·화학 ──────────────────────────────────────────────────
            "🧪 Materials": {
                # 특수화학 — BASF·아케마·솔베이·린데
                "Chemicals":        ["BAS.DE","AKE.PA","SOLB.BR","LIN.DE","DSM.AS","GIVN.SW"],
                # 광업·금속 — 글렌코어·리오틴토·앵글로아메리칸·BHP
                "Mining & Metals":  ["CRH","GLEN.L","RIO.L","AAL.L","BHP.L",
                                     "BOLIDEN.ST","AURUBIS.DE","SSAB-A.ST"],
            },
        }

        self.sectors = self.us_sectors


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    try:
        logging.info("(.)(.)스캐너 시작")
        root = tk.Tk()
        app  = QuantNexusApp(root)
        root.mainloop()
    except KeyboardInterrupt:
        print("\n종료")
        sys.exit(0)
    except Exception as e:
        print(f"FATAL ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)
