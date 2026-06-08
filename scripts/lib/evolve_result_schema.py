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


# 実 dry-run（evolve.py --dry-run --project-dir <repo>）で検証した正準キー一覧。
CANONICAL: List[Key] = [
    # --- remediation: proposable は「件数(int)」。実体は classified.proposable[] ---
    Key("phases.remediation.total_issues", int),
    Key("phases.remediation.auto_fixable", int, note="件数。実体は classified.auto_fixable[]"),
    Key("phases.remediation.proposable", int, note="件数。実体は classified.proposable[]（配列ではない）"),
    Key("phases.remediation.proposable_custom", int),
    Key("phases.remediation.proposable_global", int),
    Key("phases.remediation.manual_required", int, note="件数。実体は classified.manual_required[]"),
    Key("phases.remediation.classified", dict),
    Key("phases.remediation.classified.proposable", list, item_keys=["type", "file"]),
    Key("phases.remediation.classified.auto_fixable", list),
    Key("phases.remediation.classified.manual_required", list),
    Key("phases.remediation.classified.proposable_custom", list),
    Key("phases.remediation.classified.proposable_global", list),
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
]


def canonical_paths() -> Set[str]:
    """CANONICAL の全 path 集合。"""
    return {k.path for k in CANONICAL}


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


def check_conformance(result: Dict[str, Any]) -> List[str]:
    """実 result が CANONICAL に一致するか検査し、違反メッセージのリストを返す。

    空リスト = 完全準拠。phase が error / skipped の場合、その配下の
    optional キーの欠落は違反にしない（パイプラインの正常分岐）。
    """
    violations: List[str] = []
    for key in CANONICAL:
        inactive = _phase_inactive(result, key.phase)
        found, value = _resolve(result, key.path)
        if not found:
            # 非アクティブ phase 配下、または optional キーは欠落許容。
            if inactive or key.optional:
                continue
            violations.append(f"missing: {key.path}")
            continue
        if value is None:
            if key.nullable:
                continue
            violations.append(f"null not allowed: {key.path}")
            continue
        # bool は int のサブクラスなので、int 期待時に bool を弾く
        if key.kind is int and isinstance(value, bool):
            violations.append(f"wrong kind: {key.path} expected int got bool")
            continue
        if not isinstance(value, key.kind):
            violations.append(
                f"wrong kind: {key.path} expected {key.kind.__name__} got {type(value).__name__}"
            )
            continue
        if key.kind is list and key.item_keys and value:
            first = value[0]
            if isinstance(first, dict):
                missing = [ik for ik in key.item_keys if ik not in first]
                if missing:
                    violations.append(
                        f"item key missing: {key.path}[].{{{','.join(missing)}}}"
                    )
    return violations


def extract_documented_paths(text: str) -> Set[str]:
    """テキスト（SKILL.md 等）が明示する result dotted path を抽出する。

    `phases.X.Y...`（先頭 `result.` は許容して剥がす）形式の明示パスのみ。散文中の
    キー名片（`.proposable` 単独など）は対象外＝precision 優先で誤検出を避ける。
    """
    import re

    pattern = re.compile(r"(?:result\.)?(phases\.[A-Za-z_]+(?:\.[A-Za-z_]+)+)")
    return set(pattern.findall(text))
