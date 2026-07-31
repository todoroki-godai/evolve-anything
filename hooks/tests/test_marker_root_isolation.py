"""MARKER_ROOT 隔離が root conftest の autouse で全 testpath に効くことの契約テスト。

Why: `evolve_decisions.MARKER_ROOT` は env 非依存の import 時 home 固定で、
`CLAUDE_PLUGIN_DATA`(DATA_DIR) 隔離でも `_rebase_module_data_dirs` の DATA_DIR 派生
判定でも捕まらない。隔離が `scripts/lib/tests/conftest.py` の autouse だけに
あったため、他 testpath（hooks / skills / bin）のテストが marker を書くと
**同一 xdist worker 内の後続テストへ漏れる**。実際に `restore_state` の自動 drain
（#421）が他テストの残した pending marker を拾い、`test_no_checkpoint_noop` が
「出力は空」の assert で落ちる order-dependent flake が出た（テスト追加で xdist の
配置が変わると顕在化する: pitfall_xdist_scheduling_exposes_nonhermetic_snapshot）。

隔離は root conftest の autouse に昇格した。このテストは hooks/tests（= scripts/lib/tests
以外の testpath）に置くことで昇格が効いていることを検査する。実 home との比較では
検査できない（root conftest が HOME 自体を隔離するため `Path.home()` は既に tmp）ので、
**当該テストの `tmp_path` 配下にあること**＝テスト単位で分離されていることを見る。
"""
from pathlib import Path


def test_marker_root_is_scoped_to_this_test(tmp_path):
    import evolve_decisions

    marker_root = Path(evolve_decisions.MARKER_ROOT)
    assert marker_root.is_relative_to(tmp_path), (
        f"MARKER_ROOT が per-test 隔離されていない: {marker_root}（期待: {tmp_path} 配下）"
    )
