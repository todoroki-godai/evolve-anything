"""提案種別 → decision lane 到達性の宣言表と機械検査（#467 Stage 0）。

実測（`docs/decisions/drafts/467-all-proposal-types-to-morning-yn.md` §1.1・2026-08-15）:
discover が result に書く提案種別は 7 種（`matched_skills` / `skill_evolve` /
`repeating_patterns` / `pitfall_candidates` / `hook_candidates` /
`instruction_violation` / `trajectory_skill_candidate`）。うち朝の y/n（decision lane）
`evolve_decisions._candidates._extract_candidates` が実際に読むのは `matched_skills` と
`skill_evolve` の 2 種のみ（`_candidates.py:97,109`）。

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
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

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


# 実測（設計 §1.1・runner.py 実コード確認済み）。全 7 種はいずれも result 上で
# list of dict として格納されている（構造裏取り結果は各行のコメント参照）。
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


def find_undeclared_result_keys(
    repo_root: Path, declared_kinds: Iterable[str],
) -> List[str]:
    """宣言表に無い result 代入キーを AST best-effort で警告として列挙する（設計 §3.3）。

    正典は `PROPOSAL_KINDS`（人が書く宣言表）。本関数はそれを補助するだけで、
    ここで見つからない・逆に無関係なキーを拾う偽陽性/偽陰性の両方があり得る
    （helper 関数の戻り値経由・動的キーは静的に追えない）。呼び出し側は結果を
    **警告表示のみ**に使い、テストを赤くする根拠にしない（狼少年防止）。

    走査パターンは `skill_declaration_reachability._iter_py_files` を流用する
    （scripts/**.py + skills/**/scripts/**.py、`.claude` 配下除外）。
    """
    from skill_declaration_reachability import _iter_py_files  # noqa: PLC0415

    declared = set(declared_kinds)
    found: set = set()

    for f in _iter_py_files(Path(repo_root)):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue

        for node in ast.walk(tree):
            # `result[...] = ...` / `<name>[<str>] = ...`
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript):
                        key = _const_str(target.slice)
                        if key is not None:
                            found.add(key)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                # `<name>.setdefault(<str>, ...)`
                if node.func.attr == "setdefault" and node.args:
                    key = _const_str(node.args[0])
                    if key is not None:
                        found.add(key)
                # `<name>.update({<str>: ..., ...})`
                elif node.func.attr == "update" and node.args:
                    arg0 = node.args[0]
                    if isinstance(arg0, ast.Dict):
                        for k in arg0.keys:
                            if k is None:  # `**other` 展開は対象外
                                continue
                            key = _const_str(k)
                            if key is not None:
                                found.add(key)

    return sorted(found - declared)
