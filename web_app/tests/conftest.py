"""pytest conftest — 프로젝트 root 를 sys.path 에 추가해 entry_pricing 등 root 모듈 import."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
