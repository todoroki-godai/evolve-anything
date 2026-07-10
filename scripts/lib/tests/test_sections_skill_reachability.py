"""sections_skill_reachability（SKILL.md 宣言↔実装 到達可能性の observability section）のテスト（#191）。

#115 共通枠（build_advisory_section）経由の builder。tmp_path に疑似リポジトリツリーを作り、
clean（該当なし ✓）/ hit（⚠ + evidence）/ 非該当（None）の3シナリオを確認する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.sections_skill_reachability import build_skill_reachability_section  # noqa: E402


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_section_none_when_no_skill_md(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root / "scripts/lib/foo.py", "def func_a():\n    return 1\n")
    assert build_skill_reachability_section(root) is None


def test_section_clean_marker_when_all_reachable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root / "scripts/lib/foo.py", "def func_a():\n    return 1\n")
    _write(root / "scripts/lib/bar.py", "from foo import func_a\n\ndef use():\n    return func_a()\n")
    _write(root / "skills/demo/SKILL.md", "`func_a()` を呼ぶ。\n")
    section = build_skill_reachability_section(root)
    assert section is not None
    assert any("✓" in line for line in section)


def test_section_warns_with_evidence_when_unreachable(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write(root / "scripts/lib/foo.py", "def zombie_func():\n    return 1\n")
    _write(root / "skills/demo/SKILL.md", "`zombie_func()` を実行する。\n")
    section = build_skill_reachability_section(root)
    assert section is not None
    joined = "\n".join(section)
    assert "⚠" in joined
    assert "zombie_func" in joined
    assert "skills/demo/SKILL.md" in joined
