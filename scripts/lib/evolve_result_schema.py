"""evolve result JSON の正準スキーマ（1ソース・#375）。

SKILL.md / references が result のキー名を手書きしていたため、実装
（evolve.py / reorganize.py / skill_triage.py）からドリフトし、ドキュメント通りに
jq で掘ると空が返る事故が起きた（remediation.proposable は件数なのに配列として参照、
split は skill_name/line_count なのに .skill/.content_lines として参照、等）。

このモジュールは canonical キー一覧を **1 箇所に固定**し、

  - `check_conformance(result)`        … 実 result が契約に一致するか（impl 側 drift 検出）
  - `extract_documented_paths(text)`   … SKILL.md が言及する dotted path 抽出（doc 側 drift 検出）

の両方が同じ定義を consume する。現状の consumer は契約テスト（#375）のみ（test-time
ゲート）。runtime の self-detect（#377-5）は P2 で本モジュールを consume する予定の
invariant 層であり、本 PR ではまだ配線していない。決定論・LLM 非依存。

`kind` は実 dry-run（`evolve.py --dry-run`）で検証した型に合わせている。phase が
`{"error": ...}` または `skipped=True` の場合、その phase 配下の optional キーは欠落しても
違反にしない（パイプラインの正常な分岐）。

**契約は意図的に部分カバー**（#379-1）。evolve.py は ~18 phase を result に書くが
CANONICAL はその一部のみ登録する。逆方向 drift（新 phase 追加時に契約が静かに陳腐化し
P2 self-detect が「契約なし」と誤認する）を封じるため、登録外の phase は
`UNCOVERED_PHASES` に**明示**する。`COVERED_PHASES ∪ UNCOVERED_PHASES` が全 phase を
覆うことを契約テストが enforce し、新 phase は CANONICAL か UNCOVERED_PHASES の更新を強制する。

P2 self-detect（#377-5, `evolve_consistency`）は本モジュールの安定 API を consume する:
`check_conformance_structured(result) -> List[ConformanceViolation]`（機械可読）と
`CANONICAL` / `documented_path_drift`。`check_conformance`（str 版）は後方互換ラッパ。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass(frozen=True)
class Key:
    """result 上の1つの canonical キー。

    path: ドット区切りの絶対パス（"phases.remediation.proposable"）。
    kind: 期待する Python 型（int / list / dict / str / bool）。
    item_keys: kind=list のとき、各要素 dict が最低限持つべきキー（非空時のみ検査）。
    optional: 欠落しても違反にしない（phase skip / 動的キー）。
    nullable: 値が None でも許容する（batch_guard_trigger など）。
    note: 乖離しやすい点の覚書（doc 生成・レビュー用）。
    """

    path: str
    kind: type
    item_keys: List[str] = field(default_factory=list)
    optional: bool = False
    nullable: bool = False
    note: str = ""

    @property
    def phase(self) -> Optional[str]:
        parts = self.path.split(".")
        return parts[1] if len(parts) >= 2 and parts[0] == "phases" else None

    @property
    def top_level(self) -> Optional[str]:
        """`phases.*` でない top-level path の最上位セグメント（#493）。

        `growth_report` → "growth_report"、`correction_review.daily` → "correction_review"。
        `phases.*` は phase 体系で別管理するため None を返す。
        """
        parts = self.path.split(".")
        if parts[0] == "phases":
            return None
        return parts[0]


# 実 dry-run（evolve.py --dry-run --project-dir <repo>）で検証した正準キー一覧。
CANONICAL: List[Key] = [
    # --- remediation: proposable は「件数(int)」。実体は classified.proposable[] ---
    Key("phases.remediation.total_issues", int),
    Key("phases.remediation.auto_fixable", int, note="件数。実体は classified.auto_fixable[]"),
    Key("phases.remediation.proposable", int, note="件数。実体は classified.proposable[]（配列ではない）"),
    Key("phases.remediation.proposable_custom", int),
    Key("phases.remediation.proposable_global", int),
    Key("phases.remediation.proposable_custom_individual", int, note="件数（#377-3）。conf>=0.7 で個別承認対象。実体は classified.proposable_custom_individual[]"),
    Key("phases.remediation.proposable_custom_batch_skip", int, note="件数（#377-3）。conf<0.7 でまとめてスキップ対象。実体は classified.proposable_custom_batch_skip[]"),
    Key("phases.remediation.suppressed_by_ledger", int, note="件数（#477-2）。suppression ledger で次回再提示を抑制した却下済み提案数（silence != evaluated）"),
    Key("phases.remediation.manual_required", int, note="件数。実体は classified.manual_required[]"),
    Key("phases.remediation.classified", dict),
    Key("phases.remediation.classified.proposable", list, item_keys=["type", "file"]),
    Key("phases.remediation.classified.auto_fixable", list),
    Key("phases.remediation.classified.manual_required", list),
    Key("phases.remediation.classified.proposable_custom", list),
    Key("phases.remediation.classified.proposable_global", list),
    Key("phases.remediation.classified.proposable_custom_individual", list),
    Key("phases.remediation.classified.proposable_custom_batch_skip", list),
    # --- reorganize: split は skill_name/line_count（.skill/.content_lines ではない）。
    #     skipped 時はこれらのキー自体が出ないため optional ---
    Key("phases.reorganize.split_candidates", list, item_keys=["skill_name", "line_count"],
        optional=True, note="skipped 時は欠落。item は skill_name/line_count（.skill/.content_lines は誤）"),
    Key("phases.reorganize.hierarchy_candidates", list, optional=True),
    Key("phases.reorganize.total_split_candidates", int, optional=True),
    # --- skill_evolve ---
    Key("phases.skill_evolve.assessments", list, item_keys=["skill_name", "suitability"]),
    Key("phases.skill_evolve.total_skills", int),
    Key("phases.skill_evolve.high_suitability", int),
    Key("phases.skill_evolve.medium_suitability", int),
    Key("phases.skill_evolve.insufficient_usage", int, note="usage_count==0 で保留した件数（#376）"),
    Key("phases.skill_evolve.rejected", int),
    Key("phases.skill_evolve.batch_guard_trigger", dict, optional=True, nullable=True,
        note="LLM 評価対象が多すぎる時のみ dict。通常は None"),
    # --- skill_triage: action 名がそのままキー。REVIEW/SKIP は動的（候補がある時のみ）---
    Key("phases.skill_triage.CREATE", list),
    Key("phases.skill_triage.UPDATE", list),
    Key("phases.skill_triage.SPLIT", list),
    Key("phases.skill_triage.MERGE", list),
    Key("phases.skill_triage.OK", list),
    Key("phases.skill_triage.SKIP_SUPPRESSED", list),
    Key("phases.skill_triage.skip_suppressed_summary", str),
    Key("phases.skill_triage.SKIP", list, optional=True,
        note="初回 SKIP/TTL 切れ/クールダウン経過で出現（#308）。suppressed でない SKIP の surface 先"),
    Key("phases.skill_triage.REVIEW", list, optional=True,
        note="再発エスカレーション候補がある時のみ出現（#308）。result 初期化に追加済（KeyError 修正）"),
    # --- その他 SKILL.md が dotted path で言及する正準キー ---
    Key("phases.audit.report", str, optional=True),
    Key("phases.discover.reflect_data_count", int, optional=True),
    Key("phases.split_archive_reconcile.suppressed", list, optional=True),
    Key("phases.skill_evolve_archive_reconcile.suppressed", list, optional=True,
        note="skill_evolve↔archive reconcile で archive 優先除外したスキル名（#400 バグ#2）"),
    Key("phases.fitness_evolution.next_action", str, optional=True,
        note="insufficient_data 時の結論 1 行（#400 バグ#5）。evolve.py が提案有無で確定"),
    # ── top-level キー（phases.* の外。#442-#448 で追加・#493 で契約化）─────────
    #   SKILL.md が dotted path で読む実害キーは required（optional にしない）。
    #   rename / kind drift を test-time conformance で検出する。
    # --- correction_review: bootstrap / daily 入口（#443, #446）。SKILL.md 360/392 行で読む ---
    Key("correction_review", dict),
    Key("correction_review.bootstrap", dict,
        note="初回 evolve の weak_signals バックログ消化入口（#443）。SKILL.md 360 行"),
    Key("correction_review.daily", dict,
        note="日次の新規 weak_signal 確認入口（#446）。SKILL.md 392 行。daily.eligible/groups/remaining を読む"),
    # --- growth_report: 成長状態レポート（#448）。SKILL.md 670/677 行で lines を列挙 ---
    Key("growth_report", dict,
        note="成長状態レポート（#448）。SKILL.md 670 行で lines を MUST 列挙。error キー時は表示スキップ"),
    # --- idiom_autopromote: confirmed idiom の機械昇格結果（#447） ---
    Key("idiom_autopromote", dict,
        note="confirmed idiom と同テキストの再発 weak_signal を機械昇格した結果（#447）"),
    # --- observability: 必ず surface すべき行の構造化フィールド（ADR-028）。SKILL.md 238/533 行 ---
    Key("observability", dict,
        note="audit↔evolve の契約として構造化済み（ADR-028）。SKILL.md 238 行で各 key の行を MUST 列挙"),
    # --- evolve_decisions: pending[].id を SKILL.md 173/567/586 行で読む（#400, ADR-041）---
    Key("evolve_decisions", dict,
        note="evolve 提案の accept/reject snapshot（ADR-041）。SKILL.md 551 行"),
    Key("evolve_decisions.pending", list,
        note="before_sha 付き pending 提案（#400）。SKILL.md 567/586 行で pending[].id を読む"),
    # --- weak_signals: 暗黙修正シグナル検出結果（#442） ---
    Key("weak_signals", dict, note="暗黙修正シグナルの検出結果（#442）"),
    Key("weak_signals_ttl", dict,
        note="weak_signals の TTL 失効スキャン結果（#442）。SKILL.md reader が読む実害キー（issue #493 コメント）"),
    # --- correction_semantic: correction capture の二層化バッチ判定（#431） ---
    Key("correction_semantic", dict, note="utterances の dialogue 意味判定バッチ（#431）"),
    # --- self_analysis: evolve result の自己解析（ADR-033）---
    Key("self_analysis", dict, note="evolve result の自己解析→issue 候補（ADR-033）"),
    # --- trigger_summary: auto trigger の発火サマリ ---
    Key("trigger_summary", dict, note="auto trigger の発火統計"),
    # --- warnings: ユーザー向け警告リスト ---
    Key("warnings", list, note="ユーザー向け警告（category/message）"),
    # --- env_tier: 環境規模ティア（small/medium/large）---
    Key("env_tier", str, note="環境規模ティア（observe phase が確定）"),
    Key("env_tier_reason", dict, note="env_tier の根拠（breakdown/count/thresholds）"),
    # --- 識別・メタ情報（全 run 共通の正準フィールド）---
    Key("slug", str, note="PJ slug（git-common-dir 親で解決・ADR-031）"),
    Key("project_dir", str, note="評価対象 PJ の絶対パス"),
    Key("generated_at", str, note="result 生成時刻（ISO8601）"),
    Key("dry_run", bool, note="dry-run 実行フラグ"),
]


# CANONICAL が登録する phase 集合（path から導出）。
COVERED_PHASES: Set[str] = {k.phase for k in CANONICAL if k.phase is not None}

# CANONICAL に**意図的に**含めない phase（#379-1）。evolve.py が書くが契約対象外。
# 新 phase を足したら CANONICAL に登録するか、ここに明示して逆方向契約テストを通す。
UNCOVERED_PHASES: Set[str] = {
    "observe",
    "layer_diagnose",
    "quality_patterns",
    "quality_traces",
    "rationalization_table",
    "fitness",
    "self_evolution",
    "prune",
    "enrich",
    "pitfall_hygiene",
}

# CANONICAL が登録する top-level キー集合（#493）。`phases.*` 以外の最上位セグメント。
COVERED_TOPLEVEL: Set[str] = {k.top_level for k in CANONICAL if k.top_level is not None}

# CANONICAL に**意図的に**含めない top-level キー（#493）。evolve.py が書くが契約対象外。
# 新 top-level キーを足したら CANONICAL に登録するか、ここに明示して逆方向契約テストを通す。
UNCOVERED_TOPLEVEL: Set[str] = {
    "phases",      # phase 体系で別管理（COVERED_PHASES / UNCOVERED_PHASES）
    "timestamp",   # generated_at の別名（同値）。reader 非依存のため非契約化
    "output",      # --output 指定時のみ付く実行メタ（CLI 経路の副産物）
}


def canonical_paths() -> Set[str]:
    """CANONICAL の全 path 集合。"""
    return {k.path for k in CANONICAL}


@dataclass(frozen=True)
class ConformanceViolation:
    """機械可読な契約違反（P2 self-detect 用・#379-5）。

    path:   違反した result 上の dotted path。
    reason: 違反種別 — "missing" / "null_not_allowed" / "wrong_kind" / "item_key_missing"。
    detail: 種別に付随する説明（wrong_kind の "expected int got bool" 等）。
    """

    path: str
    reason: str
    detail: str = ""

    @property
    def message(self) -> str:
        """str 版 check_conformance と後方互換なメッセージ（既存呼び出し・テスト保護）。"""
        if self.reason == "missing":
            return f"missing: {self.path}"
        if self.reason == "null_not_allowed":
            return f"null not allowed: {self.path}"
        if self.reason == "wrong_kind":
            return f"wrong kind: {self.path} {self.detail}".rstrip()
        if self.reason == "item_key_missing":
            return f"item key missing: {self.path}{self.detail}"
        return f"{self.reason}: {self.path} {self.detail}".rstrip()


def _phase_inactive(result: Dict[str, Any], phase: Optional[str]) -> bool:
    """phase が error / skipped で、その配下キーの欠落を許容すべきか。"""
    if phase is None:
        return False
    phases = result.get("phases")
    if not isinstance(phases, dict):
        return True  # phases 自体が無ければ全キー検査不能 → 違反にしない
    sec = phases.get(phase)
    if not isinstance(sec, dict):
        return True  # phase 未実行
    return "error" in sec or sec.get("skipped") is True


def _resolve(result: Dict[str, Any], path: str) -> tuple[bool, Any]:
    """dotted path を辿る。(見つかったか, 値) を返す。"""
    cur: Any = result
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return False, None
        cur = cur[seg]
    return True, cur


def check_conformance_structured(result: Dict[str, Any]) -> List[ConformanceViolation]:
    """実 result が CANONICAL に一致するか検査し、機械可読な違反リストを返す（#379-5）。

    空リスト = 完全準拠。phase が error / skipped の場合、その配下の
    optional キーの欠落は違反にしない（パイプラインの正常分岐）。P2 self-detect は
    (path, reason) を構造的に consume するためこちらを使う。
    """
    violations: List[ConformanceViolation] = []
    for key in CANONICAL:
        inactive = _phase_inactive(result, key.phase)
        found, value = _resolve(result, key.path)
        if not found:
            # 非アクティブ phase 配下、または optional キーは欠落許容。
            if inactive or key.optional:
                continue
            violations.append(ConformanceViolation(key.path, "missing"))
            continue
        if value is None:
            if key.nullable:
                continue
            violations.append(ConformanceViolation(key.path, "null_not_allowed"))
            continue
        # bool は int のサブクラスなので、int 期待時に bool を弾く
        if key.kind is int and isinstance(value, bool):
            violations.append(ConformanceViolation(key.path, "wrong_kind", "expected int got bool"))
            continue
        if not isinstance(value, key.kind):
            violations.append(ConformanceViolation(
                key.path, "wrong_kind",
                f"expected {key.kind.__name__} got {type(value).__name__}",
            ))
            continue
        if key.kind is list and key.item_keys and value:
            first = value[0]
            if isinstance(first, dict):
                missing = [ik for ik in key.item_keys if ik not in first]
                if missing:
                    violations.append(ConformanceViolation(
                        key.path, "item_key_missing", f"[].{{{','.join(missing)}}}",
                    ))
    return violations


def check_conformance(result: Dict[str, Any]) -> List[str]:
    """実 result が CANONICAL に一致するか検査し、違反メッセージのリストを返す。

    `check_conformance_structured` の後方互換ラッパ（人間可読 str）。空リスト = 完全準拠。
    """
    return [v.message for v in check_conformance_structured(result)]


def documented_path_drift(documented: Set[str]) -> Set[str]:
    """doc が参照する path のうち canonical と prefix 整合しないものを返す（#379-3）。

    exact membership（`documented - canonical_paths()`）だと dict 型 canonical キーの
    **sub-field**（例 `phases.skill_evolve.batch_guard_trigger.reason`）を doc 参照しただけで
    drift 扱いになり false-positive で build を壊す。longest-prefix で照合し、以下を「既知」とする:
      - canonical と完全一致
      - canonical キーの子孫（`p` が `canonical + "."` で始まる＝dict sub-field）
      - canonical キーの祖先（`canonical` が `p + "."` で始まる＝中間ノード参照）
    どの canonical とも prefix 整合しない path のみ drift として返す。
    """
    canonical = canonical_paths()
    drift: Set[str] = set()
    for p in documented:
        if p in canonical:
            continue
        if any(p.startswith(c + ".") or c.startswith(p + ".") for c in canonical):
            continue
        drift.add(p)
    return drift


def extract_documented_paths(text: str) -> Set[str]:
    """テキスト（SKILL.md / references 等）が明示する result dotted path を抽出する。

    対応記法:
      - dotted(phases): `phases.X.Y...`（先頭 `result.` は許容して剥がす）
      - dotted(top-level): `result.<top>.<...>`（#493。top が登録済み top-level キーのときのみ。
        precision のため top-level は **必ず `result.` 接頭辞**を要求する。散文中の
        `growth_report.lines` のような裸の dotted は拾わない）
      - bracket: `result["phases"]["X"]...` / `result["growth_report"]["lines"]` /
        `result["growth_report"]`（#379-3, #493、任意対応）→ dotted へ正規化
    散文中のキー名片（`.proposable` 単独など）は対象外＝precision 優先で誤検出を避ける。
    """
    import re

    out: Set[str] = set()
    dotted = re.compile(r"(?:result\.)?(phases\.[A-Za-z_]+(?:\.[A-Za-z_]+)+)")
    out.update(dotted.findall(text))

    # top-level dotted: `result.correction_review.bootstrap` 等（#493）。
    # precision 重視で `result.` を必須とし、leading セグメントが登録済み top-level の場合のみ採る。
    known_top = {k.top_level for k in CANONICAL if k.top_level is not None}
    top_dotted = re.compile(r"result\.([A-Za-z_]+(?:\.[A-Za-z_]+)*)")
    for m in top_dotted.findall(text):
        head = m.split(".")[0]
        if head in known_top:
            out.add(m)

    # bracket 記法: result["phases"]["skill_evolve"][...] → phases.skill_evolve...
    #   1 セグメント（result["growth_report"]）も top-level 登録済みなら採る（#493）。
    bracket = re.compile(r'(?:result)((?:\[\s*["\'][A-Za-z_]+["\']\s*\])+)')
    seg = re.compile(r'\[\s*["\']([A-Za-z_]+)["\']\s*\]')
    for chain in bracket.findall(text):
        segs = seg.findall(chain)
        if not segs:
            continue
        if segs[0] == "phases" and len(segs) >= 2:
            out.add(".".join(segs))
        elif segs[0] in known_top:
            out.add(".".join(segs))
    return out
