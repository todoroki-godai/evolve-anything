"""提案種別 → decision lane 到達性の宣言表と機械検査（#467 Stage 0）。

実測（`docs/decisions/drafts/467-all-proposal-types-to-morning-yn.md` §1.1・2026-08-15）:
discover が result に書く提案種別は当初 8 種（`matched_skills` / `skill_evolve` /
`repeating_patterns` / `rule_violation_observed` / `pitfall_candidates` /
`hook_candidates` / `instruction_violation` / `trajectory_skill_candidate`）としていたが、
codex cold review 2巡目 [Must] 是正（2026-08-15）で `_RUNNER_NON_CANDIDATE_RESULT_KEYS`
allowlist の再分類を行い、実際は候補データ（下流で issue 化・別 SKILL.md の y/n 提示に
繋がる）だった 7 種を追加した: `missed_skill_opportunities` / `verification_needs` /
`recommended_artifacts` / `stall_recovery_patterns` / `workflow_checkpoint_gaps` /
`constraint_decay_warnings` / `constraint_decay_findings`。計 15 種のうち、朝の y/n
（decision lane）`evolve_decisions._candidates._extract_candidates` が実際に読むのは
`matched_skills` と `skill_evolve` の 2 種のみ（`_candidates.py:97,109`）。
（設計ドラフト本文は「7種」と書いているが §1.1 の表自体は8行あり本文側の数え間違い。
レビュー指摘で判明・2026-08-15）

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
（`_RUNNER_NON_CANDIDATE_RESULT_KEYS`）を明示 allowlist 化でき、誤検知ゼロで
blocking 化できる（`find_undeclared_runner_result_keys`）。
それでも塞がらない残余: helper 関数の戻り値経由で書かれるキー・動的キー生成は静的に
追えない（実測では runner.py にこのパターンは無いが将来混入し得る）。

**allowlist に入れてよい基準（2026-08-15 codex cold review 2巡目 [Must] 是正で厳格化）**:
1回目の是正では `_error` 接尾辞のみを基準にしており、`verification_needs` /
`stall_recovery_patterns` / `workflow_checkpoint_gaps` / `recommended_artifacts` /
`missed_skill_opportunities` 等、実際は下流で issue 化・提案提示に繋がる候補データが
「名前が xxx_error でないから allowlist」という粗い基準で紛れ込み、検査が骨抜きになった
（codex 実測: `phases_remediate.py:144,153`・`skills/discover/SKILL.md:57-67` 等で確認）。
allowlist に残してよいのは以下 3 種のみに限定する。**判断に迷ったら allowlist に入れず
`PROPOSAL_KINDS` 側へ倒す**（見落としより過剰宣言のほうが安全）:

1. 例外時のエラー記録（`*_error`、値は `str(e)`）
2. 件数・集計値そのもの（int。リストではなく個別レビュー対象になり得ない）
3. 下位モジュールの生データをそのまま格納しただけの構造ラッパーで、それ自体が
   独立した読み手を持たないもの（例: `tool_usage_patterns` — 中身は `repeating_patterns`
   等の個別キーとして別途宣言され、ラッパー自体を読む処理は sub-key 抽出のみ）

上記に該当しない list-of-dict 値は、たとえ現在どこからも読まれていなくても
（例: `constraint_decay_warnings`/`constraint_decay_findings` は実測でゼロ読み手だった）
`PROPOSAL_KINDS` に `lane_connected=False` で宣言し baseline に加える。「読み手がゼロ」
こそ Stage 0 が可視化すべき対象である。

**`lane_connected=True` の意味を誤読しないこと（Stage 0 の担保範囲）**: 本モジュールと
契約テストが機械検査するのは、設計 §1.3「接続済みの4点セット」のうち **#1 候補抽出
（`_extract_candidates` が当該種別を返す）だけ**である。#2 運搬（pending payload が判断材料を
落とさず運ぶ）・#3 表示/承認（`skills/evolve/SKILL.md` Step 3 / Step 7.8 の手順が扱う）・
#4 固定（契約テストの CI 登録）は本検査の対象外で、Stage 1 以降で別途担保する。
したがって `lane_connected=True` は「**朝の y/n に実際に出る**ことの証明ではなく、
`_extract_candidates` が読むことの宣言」である。この2つを同一視すると、抽出だけ通って
表示されない種別を「接続済み」と誤って数えることになる。

**allowlist 方式が原理的に残す非対称性（2026-08-15 実測）**: `find_undeclared_runner_result_keys`
は allowlist 登録キーを走査対象から除外するため、**候補データを allowlist に入れて検査を回避する
経路は blocking テストでは検出できない**（実測: `verification_needs` を `PROPOSAL_KINDS` から外し
allowlist に戻すと、AST 検査は緑のまま baseline 整合性テストだけが赤くなる。baseline も同時に
削れば全緑で通る）。これは `shrink_freeze.FROZEN_*` が持つ性質と同型で、allowlist という
「検査されないリスト」を持つ限り機械では塞げない。現状の対処は3つ:

1. allowlist は根拠コメント必須で、緩めた事実が PR diff に現れる
2. 残してよい基準を上記3種に狭く限定し、迷ったら `PROPOSAL_KINDS` 側へ倒す
3. baseline 整合性テストが部分的な安全網になる（`PROPOSAL_KINDS` から外すだけでは赤）

**将来案**: allowlist を廃し、全 result キーを単一の宣言表に載せて `is_proposal: bool` で分類すれば
「検査されないリスト」自体が消え、分類変更が1つの表の1行 diff として並列にレビューできる。
Stage 0 のスコープを超えるため本 PR では採らない。
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


# 実測（設計 §1.1・runner.py 実コード確認済み）。全 15 種はいずれも result 上で
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
    # 以下7種は 2026-08-15 codex cold review 2巡目 [Must] 是正で allowlist から昇格。
    # 「名前が *_error でない」以外の実測根拠（下流 reader）を個別に確認済み。
    ProposalKind(
        kind="missed_skill_opportunities",
        # detect_missed_skills が返す {"missed": [...]}` の "missed"（patterns.py:318-319、
        # 要素は {skill, triggers_matched, session_count}）。runner.py:240 で result 直下へ。
        # phases_diagnose.py:149 で triage_all_skills(missed_skills=...) に渡され、
        # skill_triage CREATE/UPDATE 判定 → phases_remediate.py:120 make_skill_triage_issue
        # を経由して人間の判断に繋がる（未接続＝_extract_candidates は読まない）。
        source_path="phases.discover.missed_skill_opportunities",
        selector="list_of_dict",
        lane_connected=False,
    ),
    ProposalKind(
        kind="verification_needs",
        # verification_catalog/runner.py:120 detect_verification_needs が List[Dict] を返す
        # （runner.py:305-307）。phases_remediate.py:144-151 で make_verification_rule_issue
        # に変換され issue 化。加えて skills/discover/SKILL.md:69-74（Step 5.5）で個別に
        # description/evidence を表示する独自の y/n 提示経路も持つ（evolve の朝の y/n とは別）。
        source_path="phases.discover.verification_needs",
        selector="list_of_dict",
        lane_connected=False,
    ),
    ProposalKind(
        kind="recommended_artifacts",
        # discover/artifacts.py:170-178 detect_recommended_artifacts が List[Dict] を返す
        # （runner.py:343-347）。skills/discover/SKILL.md:57-67（Step 5）で「導入する/しない」の
        # y/n を提示し、却下時は add_artifact_suppression を呼ぶ独自の承認フローを持つ
        # （evolve の朝の y/n とは別レーン）。
        source_path="phases.discover.recommended_artifacts",
        selector="list_of_dict",
        lane_connected=False,
    ),
    ProposalKind(
        kind="stall_recovery_patterns",
        # tool_usage_analyzer/stall.py:81-92 detect_stall_recovery_patterns が List[Dict] を
        # 返す（runner.py:474-475）。phases_remediate.py:153-156 で
        # make_stall_recovery_issue に変換され issue 化。
        source_path="phases.discover.stall_recovery_patterns",
        selector="list_of_dict",
        lane_connected=False,
    ),
    ProposalKind(
        kind="workflow_checkpoint_gaps",
        # runner.py:485-501 が {skill_name, gaps} の list を組み立てる。
        # phases_remediate.py:158-167 で make_workflow_checkpoint_issue に変換され issue 化。
        source_path="phases.discover.workflow_checkpoint_gaps",
        selector="list_of_dict",
        lane_connected=False,
    ),
    ProposalKind(
        kind="constraint_decay_warnings",
        # discover/patterns.py:25-117 detect_constraint_decay が返す List[Dict]
        # （{"type": "constraint_decay", "session_id", "decay_rate", ...}）のうち
        # decay_rate > 0.3 の WARNING のみ（runner.py:449-457）。実測（2026-08-15 repo 全文
        # grep）: runner.py 以外に読み手が存在しない孤児データ — allowlist へ「名前で
        # 非候補と誤認」せず、読み手ゼロという事実そのものを PROPOSAL_KINDS で可視化する。
        source_path="phases.discover.constraint_decay_warnings",
        selector="list_of_dict",
        lane_connected=False,
    ),
    ProposalKind(
        kind="constraint_decay_findings",
        # 上記と同じ detect_constraint_decay の全件（WARNING 未満も含む・runner.py:459）。
        # 読み手ゼロは constraint_decay_warnings と同じ実測結果。
        source_path="phases.discover.constraint_decay_findings",
        selector="list_of_dict",
        lane_connected=False,
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
# **ではない**既知キー。実測（2026-08-15、runner.py 全文 `result[` / `result.setdefault(`
# grep）で確定した完全列挙。ここに無い新規キーは `find_undeclared_runner_result_keys` が
# 「未宣言の提案候補かもしれない」として検出する。新しい非候補キーを runner.py に足す場合は
# ここへの追記が必要（新候補キーを足す場合は PROPOSAL_KINDS への追記が必要 — どちらの
# ケースも黙って通さない）。**各エントリの分類根拠は上記 docstring の3基準のいずれかを
# 満たすことをコメントで明示する（2026-08-15 codex cold review 2巡目 [Must] 是正）**。
_RUNNER_NON_CANDIDATE_RESULT_KEYS: FrozenSet[str] = frozenset(
    {
        "reflect_data_count",  # 基準2: reflect データ件数（int）。SKILL.md が `>=5` 閾値比較のみ
        "reflect_data_count_error",  # 基準1: *_error
        "missed_skill_opportunities_error",  # 基準1: *_error
        # 基準3寄りだが list ではなく str（missed_result.get("message")）。個別レビュー対象になり
        # 得ない単一メッセージ。実測: runner.py 以外に読み手なし（2026-08-15 grep）
        "missed_skill_message",
        "trajectory_skill_candidates_error",  # 基準1: *_error
        "scope_error",  # 基準1: *_error
        "total_candidates",  # 基準2: len(all_patterns) の int 件数
        "matched_skills_error",  # 基準1: *_error
        # 基準3: enrich.py の unmatched_patterns はそのまま phases.enrich へ転記され
        # total_unmatched（件数）/skipped_reason の算出にのみ使われる（phases_diagnose.py:135-138）。
        # 個別 issue 化・y/n 提示への変換は実測（2026-08-15 repo 全文 grep）で確認できず。
        "unmatched_patterns",
        "verification_needs_error",  # 基準1: *_error
        "tool_usage_patterns",  # 基準3: repeating_patterns 等の下位ラッパー本体（宣言表は nested path で参照）
        "rule_violation_observed_error",  # 基準1: *_error
        # 基準3: 既に導入済み artifact の状態表示のみに使われる（対策済み/未対策の表示切替、
        # skills/evolve/references/recommended-actions.md:36-39）。導入候補そのものは別キー
        # recommended_artifacts（PROPOSAL_KINDS 側へ分類済み）。installed 済みは個別 y/n の
        # 対象ではない（すでに導入されているため）。
        "installed_artifacts",
        "pitfall_candidates_error",  # 基準1: *_error
        "instruction_violations_error",  # 基準1: *_error
        "instruction_violations_unresolved",  # 基準2: last_skill を解決できなかった件数（int）。
        # silence != evaluated のため runner.py が明示的に書く可観測性フィールドで、
        # リストでなく個別レビュー対象になり得ない（#467 plugin:skill 名前空間解決）
        "constraint_decay_error",  # 基準1: *_error
        "stall_recovery_error",  # 基準1: *_error
        "workflow_checkpoint_gaps_error",  # 基準1: *_error
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
