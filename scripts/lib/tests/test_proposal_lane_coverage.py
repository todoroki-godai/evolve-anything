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


# kind ごとの代表要素1件（selector=list_of_dict 前提）。_synthetic_envelope（全種込み）と
# _envelope_with_only（ablation・1種のみ）の両方がこれを共有する。
_ELEMENT_BY_KIND = {
    "matched_skills": {"skill_path": "skills/foo/SKILL.md", "matched_skill": "foo", "pattern": "p"},
    "skill_evolve": {"skill_name": "baz", "skill_dir": "skills/baz", "suitability": "high"},
    "repeating_patterns": {"type": "hook_candidate", "pattern": "git status", "count": 5},
    "rule_violation_observed": {
        "pattern": "cd X", "count": 3, "examples": [], "violated_command": "cd",
    },
    "pitfall_candidates": {"title": "t", "root_cause": "rc", "skill_name": "foo", "source": "s"},
    "hook_candidates": {"type": "hook_candidate", "pattern": "p", "count": 5},
    "instruction_violation": {
        "type": "instruction_violation_candidate", "file": "f", "detail": {},
    },
    "trajectory_skill_candidate": {"skill_name": "bar", "session_count": 3},
    "missed_skill_opportunities": {
        "skill": "review", "triggers_matched": ["review this"], "session_count": 2,
    },
    "verification_needs": {
        "id": "v1", "description": "d", "evidence": [],
        "detection_result": {"applicable": True, "evidence": [], "confidence": 1.0},
    },
    "recommended_artifacts": {"recommendation_id": "r1", "type": "rule", "evidence": {}},
    "stall_recovery_patterns": {
        "command_pattern": "git status", "session_count": 3,
        "recovery_actions": [], "confidence": 0.6,
    },
    "workflow_checkpoint_gaps": {"skill_name": "foo", "gaps": [{"gap": "g"}]},
    "constraint_decay_warnings": {
        "type": "constraint_decay", "session_id": "s1", "decay_rate": 0.4,
    },
    "constraint_decay_findings": {
        "type": "constraint_decay", "session_id": "s1", "decay_rate": 0.1,
    },
}


def _set_by_dotted_path(tree: dict, dotted_path: str, value) -> None:
    """dotted path を辿って dict を掘り、末端に value を設定する（無ければ都度 dict を作る）。"""
    parts = dotted_path.split(".")
    cursor = tree
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _synthetic_envelope() -> dict:
    """PROPOSAL_KINDS 全種を1件ずつ含む合成 envelope（run_discover を実行せず手組み）。"""
    envelope: dict = {}
    for pk in plc.PROPOSAL_KINDS:
        _set_by_dotted_path(envelope, pk.source_path, [_ELEMENT_BY_KIND[pk.kind]])
    return envelope


def _envelope_with_only(kind: str) -> dict:
    """指定した1 kind のデータのみを含む envelope（他の種別のデータは一切含めない）。

    lane_connected の入れ替え（例: skill_evolve=False かつ pitfall_candidates=True）を
    検出するための ablation 用 helper（codex round review [Must]2）。
    """
    pk = next(p for p in plc.PROPOSAL_KINDS if p.kind == kind)
    envelope: dict = {}
    _set_by_dotted_path(envelope, pk.source_path, [_ELEMENT_BY_KIND[kind]])
    return envelope


# --- 契約テスト 1: 宣言と実装の乖離検出 -------------------------------------


def test_all_declared_paths_extract_from_synthetic_envelope() -> None:
    """PROPOSAL_KINDS の各 (source_path, selector) が合成 envelope から要素を取り出せる。"""
    envelope = _synthetic_envelope()
    for pk in plc.PROPOSAL_KINDS:
        elements = plc.extract_elements(pk, envelope)
        assert elements, f"{pk.kind}: source_path={pk.source_path!r} から要素を取り出せない"
        assert all(isinstance(e, dict) for e in elements), pk.kind


# --- 契約テスト 2: 接続宣言と実装の kind 単位の対応検査 ----------------------
#
# 件数比較・proposal_type 集合比較だけでは lane_connected の入れ替え
# （例: skill_evolve=False かつ pitfall_candidates=True）を検出できない — 接続数が
# 変わらなければ両方の assertion が素通りしてしまう（codex cold review [Must]2）。
# ここでは connected_kinds() の各 kind を「その kind のデータだけを含む envelope」で
# ablate し、_extract_candidates の出力が個別に対応する proposal_type を返すことを
# 1 kind ずつ検証する。


def test_each_connected_kind_maps_individually_to_extract_candidates_output() -> None:
    """lane_connected=True の各 kind が、単独 envelope で個別に正しい proposal_type を返す。"""
    connected = plc.connected_kinds()
    assert connected, "lane_connected=True の種別が無い（契約テストが自明に緑になる）"

    for pk in connected:
        envelope = _envelope_with_only(pk.kind)
        out = _extract_candidates(envelope)
        proposal_types = {c["proposal_type"] for c in out}

        expected = plc.CONNECTED_KIND_EXPECTED_PROPOSAL_TYPE.get(pk.kind)
        assert expected is not None, (
            f"{pk.kind}: lane_connected=True だが CONNECTED_KIND_EXPECTED_PROPOSAL_TYPE に"
            "期待値が無い（未知の kind が誤って接続宣言された可能性）"
        )
        assert proposal_types == {expected}, (
            f"{pk.kind} 単独の envelope で _extract_candidates を呼んだ結果が "
            f"proposal_type={expected!r} にならない（実際: {proposal_types}）。"
            "宣言（lane_connected=True）と実装（_extract_candidates が実際に読む種別）が"
            "食い違っている"
        )


def test_unconnected_kind_produces_no_extract_candidates_output() -> None:
    """lane_connected=False の各 kind は、単独 envelope でも _extract_candidates が空を返す。

    接続していない種別のデータだけを与えても候補が出ないことを確認する
    （誤って読まれてしまっていないかの逆方向チェック）。
    """
    for pk in plc.PROPOSAL_KINDS:
        if pk.lane_connected:
            continue
        envelope = _envelope_with_only(pk.kind)
        out = _extract_candidates(envelope)
        assert out == [], f"{pk.kind}: 未接続のはずだが _extract_candidates が拾っている: {out}"


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


# --- 補助テスト（blocking）: runner.py スコープの AST 検出 ------------------------
#
# 2026-08-15 codex cold review 是正（[Must]1/[Should]3）: 走査対象を discover/runner.py
# 1ファイルに絞ることで誤検知ゼロを狙い、非 blocking の警告から blocking の契約に格上げした。


def test_find_undeclared_runner_result_keys_flags_new_and_ignores_known(tmp_path: Path) -> None:
    """result[...] = / result.setdefault(...) を拾い、宣言済み・既知非候補は除外する。"""
    fake = tmp_path / "fake_runner.py"
    fake.write_text(
        "def run_discover():\n"
        "    result = {}\n"
        "    result['matched_skills'] = []\n"  # 宣言済み（PROPOSAL_KINDS）
        "    result['reflect_data_count'] = 0\n"  # 既知非候補（allowlist）
        "    result['dummy_candidates'] = []\n"  # 未宣言・未知＝赤にすべき
        "    result.setdefault('another_dummy', [])\n"  # 同上（setdefault 経路）
        "    tool_result = {}\n"
        "    tool_result['repeating_patterns'] = []\n"  # result 以外の変数は対象外
        "    return result\n",
        encoding="utf-8",
    )

    found = plc.find_undeclared_runner_result_keys(fake)

    assert found == ["another_dummy", "dummy_candidates"]


def test_find_undeclared_runner_result_keys_empty_when_all_known(tmp_path: Path) -> None:
    fake = tmp_path / "fake_runner.py"
    fake.write_text(
        "def run_discover():\n"
        "    result = {}\n"
        "    result['matched_skills'] = []\n"
        "    result['reflect_data_count'] = 0\n"
        "    return result\n",
        encoding="utf-8",
    )
    assert plc.find_undeclared_runner_result_keys(fake) == []


def test_find_undeclared_runner_result_keys_is_empty_for_real_runner() -> None:
    """discover/runner.py 実ファイルに対して実走し、未宣言キーがゼロであることを blocking で検証する。

    これは source の AST のみを読む（実行はしない・~/.claude や実データは参照しない）ため
    hermetic 受入条件を満たす。ここが赤くなる＝runner.py に新しい result キーが追加され
    宣言表（PROPOSAL_KINDS）にも既知非候補 allowlist にも反映されていない状態。
    """
    assert plc.find_undeclared_runner_result_keys() == []
