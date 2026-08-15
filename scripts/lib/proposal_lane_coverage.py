"""提案種別 → decision lane 到達性の宣言表と機械検査（#467 Stage 0）。

実測（`docs/decisions/drafts/467-all-proposal-types-to-morning-yn.md` §1.1・2026-08-15）:
discover が result に書く提案種別は 8 種（`matched_skills` / `skill_evolve` /
`repeating_patterns` / `rule_violation_observed` / `pitfall_candidates` /
`hook_candidates` / `instruction_violation` / `trajectory_skill_candidate`）。うち
朝の y/n（decision lane）`evolve_decisions._candidates._extract_candidates` が実際に
読むのは `matched_skills` と `skill_evolve` の 2 種のみ（`_candidates.py:97,109`）。
（設計ドラフト本文は「7種」と書いているが §1.1 の表自体は8行あり本文側の数え間違い。
baseline も6行に訂正済み・レビュー指摘で判明・2026-08-15）

種別ごとに result 上の格納階層・構造が異なる（`repeating_patterns` は
`phases.discover.tool_usage_patterns` という dict の内側にネストされる等）ため、
キー名の単純集合比較では棚卸しできない。dotted path + selector で宣言し、
契約テスト（`tests/test_proposal_lane_coverage.py`）で宣言と実装の乖離を検出する。

本モジュールは純関数のみで構成する（#379 新設凍結の対象外＝store も
observability section も advisory proposal adapter も weak_signal channel も
作らない。設計 §2 の受入条件 MUST）。

未接続の残りを許す対象は Stage 0 時点で固定した baseline に限定する
（`fixtures/proposal_lane_unconnected_baseline.txt`）。baseline は実装から独立した
git 追跡ファイルに置く（`shrink_freeze.FROZEN_*` と同型）ため、緩めた事実は必ず
PR diff の1行として現れる。

AST 検査（2026-08-15 codex cold review 是正・[Must]1/[Should]3）: 設計 §3.3 は
全ソース走査を前提に「best-effort・赤にしない」としていたが、それは全ソース走査だと
無関係な subscript 代入まで拾い誤検知が多いための判断だった。走査対象を discover の
提案生成元 `discover/runner.py` 1ファイルに絞ることで、既知の非候補キー
（`_error` 診断・件数・下位ラッパー等、`_RUNNER_NON_CANDIDATE_RESULT_KEYS`）を明示
allowlist 化でき、誤検知ゼロで blocking 化できる（`find_undeclared_runner_result_keys`）。
それでも塞がらない残余: helper 関数の戻り値経由で書かれるキー・動的キー生成は静的に
追えない（実測では runner.py にこのパターンは無いが将来混入し得る）。

**`lane_connected=True` の意味を誤読しないこと（Stage 0 の担保範囲）**: 本モジュールと
契約テストが機械検査するのは、設計 §1.3「接続済みの4点セット」のうち **#1 候補抽出
（`_extract_candidates` が当該種別を返す）だけ**である。#2 運搬（pending payload が判断材料を
落とさず運ぶ）・#3 表示/承認（`skills/evolve/SKILL.md` Step 3 / Step 7.8 の手順が扱う）・
#4 固定（契約テストの CI 登録）は本検査の対象外で、Stage 1 以降で別途担保する。
したがって `lane_connected=True` は「**朝の y/n に実際に出る**ことの証明ではなく、
`_extract_candidates` が読むことの宣言」である。この2つを同一視すると、抽出だけ通って
表示されない種別を「接続済み」と誤って数えることになる。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

# selector の取り得る値。他の値は extract_elements が ValueError で拒否する。
SELECTORS: Tuple[str, ...] = ("list_of_dict", "dict_of_list", "scalar")


@dataclass(frozen=True)
class ProposalKind:
    """提案種別 1 つの宣言（設計 §3.1）。"""

    kind: str
    source_path: str  # dotted path（例: "phases.discover.matched_skills"）
    selector: str  # "list_of_dict" | "dict_of_list" | "scalar"
    element_key: Optional[str] = None  # selector が dict_of_list のときの内側キー
    lane_connected: bool = False  # §1.3 の4点セットを満たすか（現状 True は2種のみ）


# 実測（設計 §1.1・runner.py 実コード確認済み）。全 8 種はいずれも result 上で
# list of dict として格納されている（構造裏取り結果は各行のコメント参照）。
#
# `rule_violation_observed_error`（runner.py:340）は宣言しない — これは例外発生時の
# エラーメッセージ記録であって提案種別ではない（他の *_error キーと同様、result_schema
# の CANONICAL 対象外の診断用フィールド）。
PROPOSAL_KINDS: Tuple[ProposalKind, ...] = (
    ProposalKind(
        kind="matched_skills",
        source_path="phases.discover.matched_skills",
        selector="list_of_dict",
        lane_connected=True,  # _candidates.py:97
    ),
    ProposalKind(
        kind="skill_evolve",
        source_path="phases.skill_evolve.assessments",
        selector="list_of_dict",
        lane_connected=True,  # _candidates.py:109
    ),
    ProposalKind(
        kind="repeating_patterns",
        # tool_usage_analyzer が書く "repeating_patterns" は tool_result 直下ではなく
        # phases.discover.tool_usage_patterns という dict の内側（runner.py:317-336）。
        source_path="phases.discover.tool_usage_patterns.repeating_patterns",
        selector="list_of_dict",
        lane_connected=False,
    ),
    ProposalKind(
        kind="rule_violation_observed",
        # rule_violation_lane.partition_rule_violations が result 直下に書く
        # （runner.py:337-338）。要素は `{pattern, count, examples, violated_command, ...}`
        # の dict（rule_violation_lane.py:265, :417 のコメントで確認）。
        source_path="phases.discover.rule_violation_observed",
        selector="list_of_dict",
        lane_connected=False,
    ),
    ProposalKind(
        kind="pitfall_candidates",
        source_path="phases.discover.pitfall_candidates",
        selector="list_of_dict",
        lane_connected=False,  # runner.py:369
    ),
    ProposalKind(
        kind="hook_candidates",
        source_path="phases.discover.hook_candidates",
        selector="list_of_dict",
        lane_connected=False,  # runner.py:374
    ),
    ProposalKind(
        kind="instruction_violation",
        source_path="phases.discover.instruction_violations",
        selector="list_of_dict",
        lane_connected=False,  # runner.py:443
    ),
    ProposalKind(
        kind="trajectory_skill_candidate",
        source_path="phases.discover.trajectory_skill_candidates",
        selector="list_of_dict",
        lane_connected=False,  # runner.py:272
    ),
)


def connected_kinds() -> Tuple[ProposalKind, ...]:
    """lane_connected=True の種別のみ返す。"""
    return tuple(pk for pk in PROPOSAL_KINDS if pk.lane_connected)


def unconnected_kind_names() -> frozenset:
    """lane_connected=False の種別名の集合。"""
    return frozenset(pk.kind for pk in PROPOSAL_KINDS if not pk.lane_connected)


# lane_connected=True の各 kind が `_extract_candidates`（evolve_decisions/_candidates.py）の
# 出力でどの proposal_type として現れるかの対応（実装の単一ソースは _candidates.py 自身。
# ここはテスト用の期待値ミラーであり、kind 単位の対応検査（テスト側）が実装との乖離を検出する
# ため存在する。2026-08-15 codex cold review [Must]2 是正: 件数・集合比較だけでは
# lane_connected の入れ替え（例: skill_evolve=False かつ pitfall_candidates=True）を検出
# できないため、kind ごとに ablate した envelope で個別対応を確認する契約テストを追加した）。
CONNECTED_KIND_EXPECTED_PROPOSAL_TYPE: Dict[str, str] = {
    "matched_skills": "skill_diff",  # _candidates.py:105
    "skill_evolve": "skill_evolve",  # _candidates.py:122
}


# baseline は実装から独立した git 追跡ファイルに置く（shrink_freeze.FROZEN_* と同型）。
# 緩めた事実（未接続を許す種別の追加）が必ず PR diff の1行として現れるようにするため。
_BASELINE_FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "proposal_lane_unconnected_baseline.txt"
)


def load_unconnected_baseline(path: Optional[Path] = None) -> frozenset:
    """baseline ファイルから未接続許容の種別名集合を読む（空行・前後空白は無視）。"""
    target = Path(path) if path is not None else _BASELINE_FIXTURE_PATH
    lines = target.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip())


def _walk_dotted_path(envelope: Dict[str, Any], dotted_path: str) -> Any:
    """dotted path を辿って値を取り出す。途中で辞書でない/欠落したら None。"""
    node: Any = envelope
    for part in dotted_path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


def extract_elements(pk: ProposalKind, envelope: Dict[str, Any]) -> List[Any]:
    """envelope から pk.source_path の要素列を selector に従って取り出す。

    path が無い・型が selector に合わない場合は空リストを返す（run 結果が
    その種別を含んでいないケースに寛容に振る舞う）。selector 自体が不正な
    値なら ValueError（宣言ミスを早期に検出するため寛容にしない）。
    """
    if pk.selector not in SELECTORS:
        raise ValueError(f"unknown selector: {pk.selector!r} (kind={pk.kind!r})")

    node = _walk_dotted_path(envelope, pk.source_path)

    if pk.selector == "list_of_dict":
        return list(node) if isinstance(node, list) else []

    if pk.selector == "dict_of_list":
        if not isinstance(node, dict) or pk.element_key is None:
            return []
        inner = node.get(pk.element_key)
        return list(inner) if isinstance(inner, list) else []

    # scalar: 値そのものが1件の提案を表す（真値のみ「1件」とみなす）。
    return [node] if node else []


def _const_str(node: ast.AST) -> Optional[str]:
    """ast ノードがリテラル文字列なら str を返す。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


# discover/runner.py の `result[...]` / `result.setdefault(...)` に実在するが提案候補
# **ではない**既知キー（診断エラー・件数・下位ラッパー等）。実測（2026-08-15、runner.py 全文
# `result[` / `result.setdefault(` grep）で確定した完全列挙。ここに無い新規キーは
# `find_undeclared_runner_result_keys` が「未宣言の提案候補かもしれない」として検出する。
# 新しい非候補キーを runner.py に足す場合はここへの追記が必要（新候補キーを足す場合は
# PROPOSAL_KINDS への追記が必要 — どちらのケースも黙って通さない）。
_RUNNER_NON_CANDIDATE_RESULT_KEYS: FrozenSet[str] = frozenset(
    {
        "reflect_data_count",
        "reflect_data_count_error",
        "missed_skill_opportunities",
        "missed_skill_opportunities_error",
        "missed_skill_message",
        "trajectory_skill_candidates_error",
        "scope_error",
        "total_candidates",
        "matched_skills_error",
        "unmatched_patterns",
        "verification_needs",
        "verification_needs_error",
        "tool_usage_patterns",  # repeating_patterns の下位ラッパー本体（宣言表は nested path で参照）
        "rule_violation_observed_error",
        "recommended_artifacts",
        "installed_artifacts",
        "pitfall_candidates_error",
        "instruction_violations_error",
        "constraint_decay_warnings",
        "constraint_decay_findings",
        "constraint_decay_error",
        "stall_recovery_patterns",
        "stall_recovery_error",
        "workflow_checkpoint_gaps",
        "workflow_checkpoint_gaps_error",
    }
)

_DEFAULT_RUNNER_PATH = Path(__file__).resolve().parent / "discover" / "runner.py"


def _discover_declared_result_keys() -> FrozenSet[str]:
    """PROPOSAL_KINDS のうち `phases.discover.<key>` 直下（ネスト無し）の種別が
    runner.py の `result` へ書く literal key 名の集合。

    source_path の**末尾セグメント**を使う（`kind` は概念上の識別子であり literal key
    と異なることがある — `instruction_violation`/`trajectory_skill_candidate` は kind が
    単数形だが実際の result 上の key は複数形 `instruction_violations`/
    `trajectory_skill_candidates`）。`repeating_patterns` のようにネストされる種別
    （`phases.discover.tool_usage_patterns.repeating_patterns`）は runner.py の
    `result[...]` に直接現れない（`tool_result[...]` 経由）ため対象外。
    """
    out = set()
    for pk in PROPOSAL_KINDS:
        parts = pk.source_path.split(".")
        if len(parts) == 3 and parts[0] == "phases" and parts[1] == "discover":
            out.add(parts[-1])
    return frozenset(out)


def find_undeclared_runner_result_keys(runner_path: Optional[Path] = None) -> List[str]:
    """discover/runner.py の result 代入キーのうち宣言表にも既知非候補にも無いものを検出する。

    2026-08-15 codex cold review 是正（[Must]1/[Should]3）。走査対象を単一ファイルに絞り
    `_RUNNER_NON_CANDIDATE_RESULT_KEYS` で既知の非候補キーを明示除外することで、誤検知
    ゼロを狙って blocking 化した（設計 §3.3 の「best-effort・非 blocking」からの変更）。

    走査は「`result` という名前の変数への代入」のみに限定する（`tool_result` /
    `partitioned` / `missed_result` 等の同ファイル内の無関係な変数を拾わないため）。
    対応するのは `result[<文字列リテラル>] = ...` と `result.setdefault(<文字列リテラル>, ...)`
    （実測: runner.py の `result` に対する `.update(` 呼び出しは無い・2026-08-15）。

    残余（それでも塞がらないもの）: helper 関数の戻り値経由で動的に構築されるキー・
    文字列以外から計算されるキー名は静的に追えない。
    """
    target = Path(runner_path) if runner_path is not None else _DEFAULT_RUNNER_PATH
    tree = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))

    found: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "result"
                ):
                    key = _const_str(t.slice)
                    if key is not None:
                        found.add(key)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "result"
            and node.func.attr == "setdefault"
            and node.args
        ):
            key = _const_str(node.args[0])
            if key is not None:
                found.add(key)

    expected = _discover_declared_result_keys() | _RUNNER_NON_CANDIDATE_RESULT_KEYS
    return sorted(found - expected)
