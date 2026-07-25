#!/usr/bin/env python3
"""配布物の manifest・構文・CLI smoke をまとめて検証する CI entry point。"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(_LIB_DIR))

from distribution_check import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
