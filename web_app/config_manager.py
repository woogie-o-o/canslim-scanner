# -*- coding: utf-8 -*-
"""config_manager.py — JSON 기반 설정 관리자.

웹 대시보드에서 입력한 API 키/토큰을 config.json에 저장하고,
앱 시작 시 os.environ에 로드하여 기존 모듈(telegram_notifier 등)이
수정 없이 설정을 인식하도록 한다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# 설정 키 정의 (그룹별)
SETTINGS_SCHEMA: dict[str, list[dict[str, str]]] = {
    "Telegram 알림": [
        {"key": "TELEGRAM_BOT_TOKEN",   "label": "Bot Token",       "type": "password"},
        {"key": "TELEGRAM_CHAT_ID",     "label": "Chat ID",         "type": "text"},
    ],
    "DART (전자공시)": [
        {"key": "DART_API_KEY",         "label": "API Key",         "type": "password"},
    ],
    "Naver 뉴스 API": [
        {"key": "NAVER_CLIENT_ID",      "label": "Client ID",       "type": "password"},
        {"key": "NAVER_CLIENT_SECRET",  "label": "Client Secret",   "type": "password"},
    ],
    "4축 분석": [
        {"key": "FOUR_AXIS_FETCH_TIMEOUT_SEC", "label": "데이터 조회 제한시간(초)", "type": "text"},
        {"key": "FOUR_AXIS_INFO_TIMEOUT_SEC",  "label": "종목명 조회 제한시간(초)", "type": "text"},
        {"key": "FOUR_AXIS_MIN_ROWS",          "label": "최소 필요 봉 수",         "type": "text"},
    ],
    "Finnhub (US 수급 데이터)": [
        {"key": "FINNHUB_API_KEY",            "label": "API Key (finnhub.io 무료 발급)", "type": "password"},
    ],
    "Gemini (심층 분석)": [
        {"key": "GEMINI_API_KEY",             "label": "API Key (Google AI Studio 무료 발급)", "type": "password"},
    ],
}


def load_config() -> dict[str, str]:
    """config.json을 읽어 dict로 반환. 파일 없으면 빈 dict."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(data: dict[str, str]) -> None:
    """설정을 config.json에 저장."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def apply_to_environ(data: dict[str, str] | None = None) -> None:
    """설정값을 os.environ에 반영하여 기존 모듈이 인식하도록 한다."""
    if data is None:
        data = load_config()
    for key, value in data.items():
        if value:  # 빈 문자열은 설정하지 않음
            os.environ[key] = str(value)


def get_masked(data: dict[str, str]) -> dict[str, str]:
    """민감 키를 마스킹하여 반환 (앞 4자만 노출)."""
    schema_keys: dict[str, str] = {}
    for fields in SETTINGS_SCHEMA.values():
        for f in fields:
            schema_keys[f["key"]] = f["type"]

    masked: dict[str, str] = {}
    for key, value in data.items():
        if not value:
            masked[key] = ""
        elif schema_keys.get(key) == "password" and len(value) > 4:
            masked[key] = value[:4] + "*" * (len(value) - 4)
        else:
            masked[key] = value
    return masked


def get_connection_status(data: dict[str, str]) -> dict[str, dict[str, Any]]:
    """각 서비스의 연결 상태를 확인."""
    status: dict[str, dict[str, Any]] = {}

    # Telegram
    tg_ok = bool(data.get("TELEGRAM_BOT_TOKEN") and data.get("TELEGRAM_CHAT_ID"))
    status["Telegram"] = {"connected": tg_ok}

    # DART
    status["DART"] = {"connected": bool(data.get("DART_API_KEY"))}

    # Naver
    naver_ok = bool(data.get("NAVER_CLIENT_ID") and data.get("NAVER_CLIENT_SECRET"))
    status["Naver"] = {"connected": naver_ok}

    # Finnhub
    status["Finnhub"] = {"connected": bool(data.get("FINNHUB_API_KEY"))}

    # Gemini
    status["Gemini"] = {"connected": bool(data.get("GEMINI_API_KEY"))}

    return status
