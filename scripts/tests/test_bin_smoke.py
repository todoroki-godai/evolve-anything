"""bin/ スクリプトの smoke test。

各スクリプトが ImportError なく起動できることを検証する。
publish 前にこのテストが通ることで「起動すらできない」リグレッションを防ぐ。

検証戦略: python3 bin/rl-XXX --help を実行し、
  - exit 0 → OK（argparse --help が通った）
  - exit != 0 かつ stderr に ImportError/ModuleNotFoundError → FAIL
  - exit != 0 かつ stderr に ImportError なし → OK（--help 未対応だが import は通っている）
"""
import subprocess
import sys
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"

# 実行コストが大きいスクリプトは除外（全 PJ スキャン等）
_SKIP_SCRIPTS: set[str] = {
    "rl-fleet",  # DuckDB walk + 全 PJ スキャン
    "rl-gain",   # ~/.claude/projects/ 全体スキャン
}

_IMPORT_ERROR_PATTERNS = (
    "ImportError",
    "ModuleNotFoundError",
    "No module named",
)


def _collect_scripts() -> list[Path]:
    return sorted(
        p for p in _BIN_DIR.glob("rl-*")
        if p.is_file() and p.name not in _SKIP_SCRIPTS
    )


@pytest.mark.parametrize("script", _collect_scripts(), ids=lambda p: p.name)
def test_bin_script_importable(script: Path) -> None:
    """bin/ スクリプトが ImportError なく起動できること。"""
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    combined = result.stdout + result.stderr
    import_error = any(pat in combined for pat in _IMPORT_ERROR_PATTERNS)

    assert not import_error, (
        f"{script.name} の起動中に import エラーが発生:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
