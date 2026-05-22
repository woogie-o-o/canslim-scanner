# -*- coding: utf-8 -*-
"""Toast 알림 — plyer → win10toast → PowerShell BurntToast → print 폴백."""
from __future__ import annotations
import logging
import platform
import subprocess


log = logging.getLogger(__name__)
_BACKEND: str | None = None


def _detect_backend() -> str:
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    # 1) plyer (cross-platform)
    try:
        import plyer  # noqa
        _BACKEND = "plyer"
        return _BACKEND
    except Exception:
        pass
    # 2) win10toast (Windows)
    if platform.system() == "Windows":
        try:
            import win10toast  # noqa
            _BACKEND = "win10toast"
            return _BACKEND
        except Exception:
            pass
        # 3) PowerShell New-BurntToastNotification
        _BACKEND = "powershell"
        return _BACKEND
    _BACKEND = "print"
    return _BACKEND


def is_available() -> bool:
    """실제 토스트(시각적) 가능 여부 — print 폴백이면 False."""
    return _detect_backend() != "print"


def notify(title: str, msg: str, urgent: bool = False, timeout: int = 5) -> bool:
    """
    Returns: 성공 여부 (실패해도 예외 없이 False).
    """
    backend = _detect_backend()
    title = str(title)[:64]
    msg   = str(msg)[:256]
    try:
        if backend == "plyer":
            from plyer import notification
            notification.notify(title=title, message=msg, timeout=timeout)
            return True
        if backend == "win10toast":
            from win10toast import ToastNotifier
            ToastNotifier().show_toast(title, msg, duration=timeout, threaded=True)
            return True
        if backend == "powershell":
            # 인젝션 방지: 제어문자 제거 + PowerShell 단일 따옴표 이스케이프('→'')
            def _ps_escape(s: str) -> str:
                s = "".join(ch for ch in s if ch.isprintable() or ch == " ")
                return s.replace("'", "''")
            t = _ps_escape(title)
            m = _ps_escape(msg)
            ps_script = (
                f"try {{ New-BurntToastNotification -Text '{t}', '{m}' }} "
                f"catch {{ Write-Host '[notify] {t}: {m}' }}"
            )
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return True
    except Exception as e:
        log.warning("notify backend %s failed: %s", backend, e)
    # print 폴백
    print(f"[notify] {title}: {msg}")
    return False


if __name__ == "__main__":
    print("backend:", _detect_backend(), "available:", is_available())
    ok = notify("Test", "v21 notifier 동작 확인")
    print("[OK] notifier returned", ok)
