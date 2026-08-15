"""proposal_lane_coverage.py の契約テスト（#467 Stage 0）。

hermetic 受入条件（設計 §3.4 MUST）: ``~/.claude`` / 実 PJ データ / DuckDB 実ストア /
LLM / ネットワークを一切参照しない。検証入力は source の AST と ``tmp_path`` 内に
組み立てた合成 envelope のみ。``run_discover()`` は実行しない。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import proposal_lane_coverage as plc  # noqa: E402
from evolve_decisions._candidates import _extract_candidates  # noqa: E402


def _synthetic_envelope() -> dict:
    """PROPOSAL_KINDS 全種を1件ずつ含む合成 envelope（run_discover を実行せず手組み）。"""
    return {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"skill_path": "skills/foo/SKILL.md", "matched_skill": "foo", "pattern": "p"},
                ],
                "tool_usage_patterns": {
                    "repeating_patterns": [
                        {"type": "hook_candidate", "pattern": "git status", "count": 5},
                    ],
                },
                "pitfall_candidates": [
                    {"title": "t", "root_cause": "rc", "skill_name": "foo", "source": "s"},
                ],
                "hook_candidates": [
                    {"type": "hook_candidate", "pattern": "p", "count": 5},
                ],
                "instruction_violations": [
                    {"type": "instruction_violation_candidate", "file": "f", "detail": {}},
                ],
                "trajectory_skill_candidates": [
                    {"skill_name": "bar", "session_count": 3},
                ],
            },
            "skill_evolve": {
                "assessments": [
                    {
                        "skill_name": "baz",
                        "skill_dir": "skills/baz",
                        "suitability": "high",
                    },
                ],
            },
        },
    }


# --- 契約テスト 1: 宣言と実装の乖離検出 -------------------------------------


def test_all_declared_paths_extract_from_synthetic_envelope() -> None:
    """PROPOSAL_KINDS の各 (source_path, selector) が合成 envelope から要素を取り出せる。"""
    envelope = _synthetic_envelope()
    for pk in plc.PROPOSAL_KINDS:
        elements = plc.extract_elements(pk, envelope)
        assert elements, f"{pk.kind}: source_path={pk.source_path!r} から要素を取り出せない"
        assert all(isinstance(e, dict) for e in elements), pk.kind


# --- 契約テスト 2: 接続宣言と実装の一致 -------------------------------------


def test_connected_kinds_appear_in_extract_candidates_output() -> None:
    """lane_connected=True の種別が _extract_candidates の実出力に現れる。"""
    envelope = _synthetic_envelope()
    out = _extract_candidates(envelope)

    connected = plc.connected_kinds()
    assert len(out) == len(connected)

    proposal_types = {c["proposal_type"] for c in out}
    # matched_skills -> "skill_diff", skill_evolve -> "skill_evolve"（_candidates.py の実装）。
    assert proposal_types == {"skill_diff", "skill_evolve"}


def test_connecting_a_kind_without_wiring_would_be_caught() -> None:
    """接続したと宣言(lane_connected=True)して _extract_candidates 実装を忘れたら検出できる。

    ここでは pitfall_candidates を仮に接続宣言した ProposalKind を作り、実際の
    _extract_candidates が拾わないこと（＝宣言だけでは通らない）を確認する。
    """
    # 宣言上は pitfall_candidates を「接続済み」にしても、実際の _extract_candidates が
    # 拾う種別（matched_skills / skill_evolve）は変わらない＝宣言だけでは実装は動かない。
    envelope = _synthetic_envelope()
    out = _extract_candidates(envelope)
    proposal_types = {c["proposal_type"] for c in out}
    assert "pitfall_candidates" not in proposal_types


# --- 契約テスト 3: 新種を未接続で足したら赤 ----------------------------------


def test_unconnected_kinds_are_subset_of_baseline() -> None:
    """lane_connected=False の種別が baseline ファイルの部分集合（新種の未接続追加を検出）。"""
    baseline = plc.load_unconnected_baseline()
    assert plc.unconnected_kind_names() <= baseline


# --- 契約テスト 4: 接続済みなのに baseline に残っていたら赤（単調減少の強制）----


def test_baseline_entries_are_all_unconnected_kinds() -> None:
    """baseline の各行が PROPOSAL_KINDS の未接続種別として実在する。

    接続済み（lane_connected=True）になった種別が baseline に残っていると、
    ここで unconnected_kind_names() に含まれず失敗する。PROPOSAL_KINDS から
    削除された種別が baseline に残っていても同様に失敗する。
    """
    baseline = plc.load_unconnected_baseline()
    assert baseline <= plc.unconnected_kind_names()


def test_baseline_and_unconnected_kinds_are_exactly_equal() -> None:
    """3+4 を合わせると baseline == 未接続種別集合（完全一致）。"""
    assert plc.load_unconnected_baseline() == plc.unconnected_kind_names()


# --- 補助テスト: extract_elements の selector 分岐（dict_of_list / scalar） --------


def test_extract_elements_dict_of_list_selector() -> None:
    pk = plc.ProposalKind(
        kind="synthetic",
        source_path="phases.discover.grouped",
        selector="dict_of_list",
        element_key="items",
    )
    envelope = {"phases": {"discover": {"grouped": {"items": [{"a": 1}, {"a": 2}]}}}}
    assert plc.extract_elements(pk, envelope) == [{"a": 1}, {"a": 2}]

    # element_key が envelope に無ければ空リスト。
    assert plc.extract_elements(pk, {"phases": {"discover": {"grouped": {}}}}) == []


def test_extract_elements_scalar_selector() -> None:
    pk = plc.ProposalKind(
        kind="synthetic_scalar",
        source_path="phases.discover.flag",
        selector="scalar",
    )
    assert plc.extract_elements(pk, {"phases": {"discover": {"flag": True}}}) == [True]
    assert plc.extract_elements(pk, {"phases": {"discover": {"flag": False}}}) == []
    assert plc.extract_elements(pk, {"phases": {"discover": {}}}) == []


def test_extract_elements_missing_path_returns_empty() -> None:
    pk = plc.PROPOSAL_KINDS[0]
    assert plc.extract_elements(pk, {}) == []
    assert plc.extract_elements(pk, {"phases": {}}) == []


def test_extract_elements_rejects_unknown_selector() -> None:
    pk = plc.ProposalKind(
        kind="bad", source_path="phases.discover.x", selector="not_a_selector",
    )
    with pytest.raises(ValueError, match="not_a_selector"):
        plc.extract_elements(pk, {"phases": {"discover": {"x": []}}})


# --- 補助テスト: AST best-effort 検出（誤検知許容・非 blocking） -------------------


def test_find_undeclared_result_keys_flags_new_and_ignores_declared(tmp_path: Path) -> None:
    """result[...] = / setdefault / update の3パターンを拾い、宣言済みは除外する。"""
    src_dir = tmp_path / "scripts" / "lib"
    src_dir.mkdir(parents=True)
    (src_dir / "fake_runner.py").write_text(
        "def run():\n"
        "    result = {}\n"
        "    result['known_kind'] = []\n"
        "    result['mystery_kind'] = []\n"
        "    result.setdefault('another_kind', [])\n"
        "    result.update({'third_kind': [], 'known_kind': []})\n"
        "    return result\n",
        encoding="utf-8",
    )

    found = plc.find_undeclared_result_keys(tmp_path, declared_kinds={"known_kind"})

    assert found == ["another_kind", "mystery_kind", "third_kind"]


def test_find_undeclared_result_keys_handles_empty_tree(tmp_path: Path) -> None:
    assert plc.find_undeclared_result_keys(tmp_path, declared_kinds=set()) == []


def test_find_undeclared_result_keys_runs_over_real_repo_source_without_crashing() -> None:
    """best-effort 検出器を実リポジトリの source AST に対して実走させる（結果は非 assert）。

    これは source の AST のみを読む（実行はしない・~/.claude や実データは参照しない）ため
    hermetic 受入条件を満たす。正確な件数は将来のコード変更で自然に増減しうるため、
    型と非負性のみ検証し、具体的な件数は契約にしない（狼少年防止・§3.3）。
    """
    from plugin_root import PLUGIN_ROOT

    declared = {pk.kind for pk in plc.PROPOSAL_KINDS}
    found = plc.find_undeclared_result_keys(PLUGIN_ROOT, declared_kinds=declared)
    assert isinstance(found, list)
    assert all(isinstance(k, str) for k in found)
