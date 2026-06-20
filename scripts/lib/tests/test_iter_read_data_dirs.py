"""rl_common.iter_read_data_dirs の候補 dir 列挙テスト（#45 read 統一）。

DATA_DIR 断片化（canonical / legacy rename / plugins-data hook split）の移行期に、
reader が全候補 dir を union read できるよう「存在する候補 dir」を返すヘルパ。

最重要の不変条件は **hermetic 性**: 候補は canonical.parent からの相対で導出するため、
tmp canonical を渡すテストでは兄弟 dir が存在せず [canonical] のみを返す
（実 home ~/.claude を絶対に読まない）。この導出方式が test 隔離 pitfall
（store モジュールが実 home を読んで xdist 非hermetic になる #420/#457）を構造的に防ぐ。

決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import rl_common  # noqa: E402


def _claude_layout(root: Path) -> Path:
    """``root`` 下に ~/.claude 相当のレイアウトを作り canonical dir を返す。"""
    canonical = root / "evolve-anything"
    canonical.mkdir(parents=True, exist_ok=True)
    return canonical


def test_tmp_canonical_returns_only_canonical(tmp_path):
    """兄弟 dir が無い tmp canonical では canonical 1 つだけ（実 home を読まない）。"""
    canonical = _claude_layout(tmp_path)
    dirs = rl_common.iter_read_data_dirs(canonical)
    assert dirs == [canonical]


def test_includes_existing_legacy_and_plugins_data(tmp_path):
    """存在する legacy / plugins-data 候補を canonical に続けて列挙する。"""
    canonical = _claude_layout(tmp_path)
    legacy = tmp_path / "rl-anything"
    legacy.mkdir()
    pdata = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    pdata.mkdir(parents=True)
    pdata_old = tmp_path / "plugins" / "data" / "rl-anything-rl-anything"
    pdata_old.mkdir(parents=True)

    dirs = rl_common.iter_read_data_dirs(canonical)
    # canonical 先頭・存在する候補を全部含む。
    assert dirs[0] == canonical
    assert set(dirs) == {canonical, legacy, pdata, pdata_old}


def test_excludes_nonexistent_candidates(tmp_path):
    """存在しない候補は返さない（legacy のみ作成）。"""
    canonical = _claude_layout(tmp_path)
    legacy = tmp_path / "rl-anything"
    legacy.mkdir()
    dirs = rl_common.iter_read_data_dirs(canonical)
    assert dirs == [canonical, legacy]


def test_canonical_first_ordering(tmp_path):
    """canonical は必ず先頭（union で dedup 時に canonical を優先させるため）。"""
    canonical = _claude_layout(tmp_path)
    (tmp_path / "rl-anything").mkdir()
    dirs = rl_common.iter_read_data_dirs(canonical)
    assert dirs[0] == canonical


def test_dedup_by_resolved_path(tmp_path):
    """同一 resolve 先の候補は 1 回だけ（canonical == legacy になる病的ケースの保険）。"""
    # canonical 自身を legacy 名にして parent からの導出と衝突させる。
    canonical = tmp_path / "rl-anything"
    canonical.mkdir()
    dirs = rl_common.iter_read_data_dirs(canonical)
    # canonical と「parent/rl-anything」は同一 → 1 つに dedup。
    assert dirs.count(canonical) == 1


def test_default_uses_module_data_dir(tmp_path, monkeypatch):
    """canonical 省略時は rl_common.DATA_DIR を使う。"""
    canonical = _claude_layout(tmp_path)
    monkeypatch.setattr(rl_common, "DATA_DIR", canonical)
    dirs = rl_common.iter_read_data_dirs()
    assert dirs == [canonical]
