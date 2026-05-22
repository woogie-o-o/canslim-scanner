"""run_quant_nexus.py — 크로스플랫폼 런처 (US-006)

· py -3.13 (Windows) 또는 python3.13 (POSIX) 우선
· $PYTHON313 환경변수 지정 가능
· 실패 시 sys.executable 로 fallback
"""
from __future__ import annotations
import os
import sys
import shutil
import subprocess


def _resolve_python() -> list[str]:
    env = os.environ.get("PYTHON313")
    if env and os.path.exists(env):
        return [env]
    if os.name == "nt":
        if shutil.which("py"):
            try:
                subprocess.check_output(["py", "-3.13", "-c", "import sys"],
                                        stderr=subprocess.STDOUT)
                return ["py", "-3.13"]
            except Exception:
                pass
    for cand in ("python3.13", "python3", "python"):
        p = shutil.which(cand)
        if p:
            return [p]
    return [sys.executable]


def main() -> int:
    proj = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(proj, "quant_nexus_v20.py")
    cmd = _resolve_python() + [target] + sys.argv[1:]
    print(f"[run_quant_nexus] launch: {' '.join(cmd)}")
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
