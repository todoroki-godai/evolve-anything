"""pitfall_registry のテスト（決定論・LLM 非依存）。

レジストリは「hook がどの pitfalls.md を監視するか」のオプトイン台帳。
install で hook は配られるが、enable で登録するまで hook は無反応、という設計を支える。
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "lib"))

from lib import pitfall_registry as reg


def _pf(tmp_path: Path) -> Path:
    d = tmp_path / ".claude" / "skills" / "x" / "references"
    d.mkdir(parents=True)
    p = d / "pitfalls.md"
    p.write_text("# Pitfalls\n", encoding="utf-8")
    return p


def test_load_empty_when_no_registry(tmp_path):
    assert reg.load_managed(tmp_path) == []


def test_add_then_is_managed(tmp_path):
    pf = _pf(tmp_path)
    assert reg.add_managed(tmp_path, pf) is True
    assert reg.is_managed(tmp_path, pf) is True
    assert reg.load_managed(tmp_path) == [
        ".claude/skills/x/references/pitfalls.md"
    ]


def test_add_duplicate_is_noop(tmp_path):
    pf = _pf(tmp_path)
    reg.add_managed(tmp_path, pf)
    assert reg.add_managed(tmp_path, pf) is False
    assert reg.load_managed(tmp_path) == [
        ".claude/skills/x/references/pitfalls.md"
    ]


def test_unmanaged_file_is_not_managed(tmp_path):
    pf = _pf(tmp_path)
    other = tmp_path / ".claude" / "skills" / "y" / "pitfalls.md"
    assert reg.is_managed(tmp_path, pf) is False
    assert reg.is_managed(tmp_path, other) is False


def test_remove_managed(tmp_path):
    pf = _pf(tmp_path)
    reg.add_managed(tmp_path, pf)
    assert reg.remove_managed(tmp_path, pf) is True
    assert reg.is_managed(tmp_path, pf) is False
    # 二度目は False（既に無い）
    assert reg.remove_managed(tmp_path, pf) is False


def test_corrupt_registry_returns_empty(tmp_path):
    p = tmp_path / ".claude" / "rl-anything" / "pitfall-managed.json"
    p.parent.mkdir(parents=True)
    p.write_text("{ not json", encoding="utf-8")
    assert reg.load_managed(tmp_path) == []  # raise しない


def test_path_outside_project_kept_absolute(tmp_path):
    outside = tmp_path.parent / "external_pitfalls.md"
    outside.write_text("# Pitfalls\n", encoding="utf-8")
    reg.add_managed(tmp_path, outside)
    # プロジェクト外は絶対パスのまま記録され、is_managed で一致する
    assert reg.is_managed(tmp_path, outside) is True
    assert str(outside.resolve()) in reg.load_managed(tmp_path)
