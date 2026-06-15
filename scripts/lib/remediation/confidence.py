"""confidence_score / impact_scope 算出 + classify_issue / classify_issues (旧 remediation.py 由来)。

remediation/__init__.py から re-export される（後方互換）。
DATA_DIR / 各種閾値 / FP 除外 / 原則関数 / skill_origin 関数 / issue_schema 定数は
package 経由で遅延参照する（テスト patch 追従）。
"""
import json
from pathlib import Path
from typing import Any, Dict, List

from issue_schema import (
    SE_SUITABILITY,
    SKILL_EVOLVE_CANDIDATE,
    SKILL_TRIAGE_CREATE,
    SKILL_TRIAGE_MERGE,
    SKILL_TRIAGE_SPLIT,
    SKILL_TRIAGE_UPDATE,
    ST_CONFIDENCE,
    TOOL_USAGE_HOOK_CANDIDATE,
    TOOL_USAGE_RULE_CANDIDATE,
    VERIFICATION_RULE_CANDIDATE,
    VRC_DETECTION_CONFIDENCE,
    WCC_CONFIDENCE,
    WORKFLOW_CHECKPOINT_CANDIDATE,
)

# skill_triage の判定結果が detail に運ぶ confidence を top-level に引き継ぐ対象 (#522-1)
_SKILL_TRIAGE_TYPES = (
    SKILL_TRIAGE_CREATE,
    SKILL_TRIAGE_UPDATE,
    SKILL_TRIAGE_SPLIT,
    SKILL_TRIAGE_MERGE,
)


def compute_impact_scope(file_path: str) -> str:
    """ファイルパスから impact_scope を判定する。

    Returns:
        "file", "project", or "global"
    """
    from . import _GLOBAL_SCOPE_PATTERNS  # noqa: PLC0415

    basename = Path(file_path).name
    if basename in _GLOBAL_SCOPE_PATTERNS:
        return "project"  # CLAUDE.md は全会話に影響するが project scope

    # CLAUDE.md 直下でないが .claude/ 内 → file scope
    # グローバル設定（~/.claude/ 直下の rules 等）→ global
    home_claude = str(Path.home() / ".claude")
    if file_path.startswith(home_claude) and "memory" not in file_path:
        # ~/.claude/rules/ や ~/.claude/skills/ → global
        return "global"

    return "file"


def _load_calibration_overrides() -> Dict[str, float]:
    """confidence-calibration.json から active なキャリブレーション値を読み込む。"""
    from . import DATA_DIR  # noqa: PLC0415

    cal_file = DATA_DIR / "confidence-calibration.json"
    if not cal_file.exists():
        return {}
    try:
        data = json.loads(cal_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    overrides: Dict[str, float] = {}
    for it, cal in data.get("calibrations", {}).items():
        if isinstance(cal, dict) and cal.get("status") == "active":
            overrides[it] = cal.get("calibrated", cal.get("current", 0.5))
    return overrides


def compute_confidence_score(issue: Dict[str, Any]) -> float:
    """問題タイプと詳細から confidence_score を算出する。

    confidence-calibration.json に active なキャリブレーション値があればそちらを使用。

    Returns:
        0.0 〜 1.0
    """
    from . import (  # noqa: PLC0415
        DUPLICATE_PROPOSABLE_CONFIDENCE,
        DUPLICATE_PROPOSABLE_SIMILARITY,
        MAJOR_EXCESS_RATIO,
        PROPOSABLE_CONFIDENCE,
    )

    issue_type = issue["type"]
    detail = issue.get("detail", {})

    # Check calibration overrides first
    overrides = _load_calibration_overrides()
    if issue_type in overrides:
        return overrides[issue_type]

    if issue_type == "stale_ref":
        # 陳腐化参照は削除の確実性が高い
        return 0.95

    if issue_type == "line_limit_violation":
        # #477-3: 超過率（excess / limit）で confidence をスケールする。
        # 旧実装は「1行超過 → 0.95」と固定で超過幅を考慮せず過剰だった
        # （1行超過でも auto_fixable 手前まで確信を持ってしまう）。超過がわずかなら
        # 確信度を抑え、超過率が大きいほど（manual 帯の手前まで）単調に上げる。
        # auto_fixable（0.9）には昇格させず proposable 帯に留める設計は維持する。
        lines = detail.get("lines", 0)
        limit = detail.get("limit", 1)
        excess = lines - limit if limit > 0 else lines
        ratio = lines / limit if limit > 0 else 999
        if ratio >= MAJOR_EXCESS_RATIO:
            # 大幅超過（160%+）→ 自動修正困難（manual_required へ）
            return 0.3
        if excess <= 0:
            # 超過なし（境界・データ不整合）→ proposable 下限
            return PROPOSABLE_CONFIDENCE
        # 超過率 0（=超過なし）で floor 0.55、MAJOR_EXCESS_RATIO 直前で cap 0.88 に
        # 線形補間。proposable 帯（< AUTO_FIX_CONFIDENCE=0.9）に必ず収まる。
        excess_ratio = excess / limit if limit > 0 else 0.0
        floor, cap = 0.55, 0.88
        span = MAJOR_EXCESS_RATIO - 1.0  # 超過率の有効レンジ（0 〜 0.6）
        frac = min(1.0, excess_ratio / span) if span > 0 else 1.0
        return round(floor + (cap - floor) * frac, 4)

    if issue_type == "near_limit":
        pct = detail.get("pct", 0)
        if pct >= 95:
            return 0.6
        return 0.7

    if issue_type == "duplicate":
        similarity = detail.get("similarity", 0.0)
        if similarity >= DUPLICATE_PROPOSABLE_SIMILARITY:
            return DUPLICATE_PROPOSABLE_CONFIDENCE
        return 0.4  # 低similarity重複の統合は複雑

    if issue_type == "hardcoded_value":
        # 検出結果自体の confidence_score を使用
        return detail.get("confidence_score", 0.5)

    # レイヤー別診断の新 issue type
    if issue_type == "orphan_rule":
        return 0.5  # 孤立判定は不確実性がある

    if issue_type == "stale_rule":
        return 0.95  # ファイル不存在は確実

    if issue_type == "stale_memory":
        return 0.6  # セマンティックパターン検出の不確実性

    if issue_type == "memory_duplicate":
        similarity = detail.get("similarity", 0.5)
        return min(0.8, max(0.6, similarity))  # 類似度に依存

    if issue_type == "hooks_unconfigured":
        return 0.4  # 意図的な場合もある

    if issue_type == "claudemd_phantom_ref":
        return 0.9  # スキル/ルールの実在確認は確実性が高い

    if issue_type == "claudemd_missing_section":
        return 0.95  # セクション有無は確実に判定可能

    if issue_type == TOOL_USAGE_RULE_CANDIDATE:
        return 0.85  # パターンマッチは確実だが global 影響

    if issue_type == TOOL_USAGE_HOOK_CANDIDATE:
        return 0.75  # hook テンプレートの汎用性にバリエーション

    if issue_type == "cap_exceeded":
        return 0.90  # Active 超過は明確に判定可能

    if issue_type == "line_guard":
        return 0.90  # 行数超過は明確に判定可能

    if issue_type == "split_candidate":
        return 0.70  # 分割判断にはドメイン知識が必要

    if issue_type == "preflight_scriptification":
        return 0.70  # スクリプト化候補は proposable

    if issue_type == "untagged_reference_candidates":
        return 0.90  # audit のフィルタ済み候補のため高信頼

    if issue_type == SKILL_EVOLVE_CANDIDATE:
        suitability = detail.get(SE_SUITABILITY, "low")
        if suitability == "high":
            return 0.85
        elif suitability == "medium":
            return 0.60
        return 0.3  # low → 対象外

    if issue_type == VERIFICATION_RULE_CANDIDATE:
        # 検出関数の confidence を使用（regex のみなので proposable 止まり）
        return min(0.85, detail.get(VRC_DETECTION_CONFIDENCE, 0.5))

    if issue_type == WORKFLOW_CHECKPOINT_CANDIDATE:
        # ギャップ検出の confidence をそのまま使用（上限 0.85 = proposable）
        return min(0.85, detail.get(WCC_CONFIDENCE, 0.5))

    if issue_type in _SKILL_TRIAGE_TYPES:
        # skill_triage は detail["confidence"]（CREATE=0.70 等）を権威とする。
        # default 0.5 に降格すると CREATE が partition で batch_skip 落ちし、
        # 個別承認レーン（proposable_custom_individual）に永久に乗らない (#522-1)。
        return detail.get(ST_CONFIDENCE, 0.5)

    return 0.5


def classify_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    """単一の issue を分類し、メタデータを付与する。

    Returns:
        元の issue に confidence_score, impact_scope, category を追加した dict
    """
    # mock.patch("remediation.X", ...) パターンに追従するため package 経由で遅延参照
    from . import (  # noqa: PLC0415
        AUTO_FIX_CONFIDENCE,
        PROPOSABLE_CONFIDENCE,
        REMEDIATION_PRINCIPLES,
        _apply_principles,
        _should_exclude_fp,
        compute_confidence_score,
        is_protected_skill,
        suggest_local_alternative,
        generate_protection_warning,
    )

    # FP 除外チェック（auto_fixable 判定前）
    fp_reason = _should_exclude_fp(issue)
    if fp_reason is not None:
        return {
            **issue,
            "confidence_score": 0.0,
            "impact_scope": compute_impact_scope(issue["file"]),
            "category": "fp_excluded",
            "fp_exclusion_reason": fp_reason,
        }

    confidence = compute_confidence_score(issue)
    scope = compute_impact_scope(issue["file"])

    # 保護スキルへの書込チェック: 保護対象は proposable に降格 + 警告
    file_path = Path(issue["file"])
    protection_warning = None
    if is_protected_skill(file_path):
        skill_name = file_path.parent.name if file_path.name != file_path.parent.name else file_path.stem
        # スキルディレクトリ名を推定
        parts = file_path.parts
        try:
            skills_idx = len(parts) - 1 - list(reversed(parts)).index("skills")
            if skills_idx + 1 < len(parts):
                skill_name = parts[skills_idx + 1]
        except ValueError:
            pass
        project_root = Path.cwd()
        alt_path, _ = suggest_local_alternative(skill_name, project_root)
        protection_warning = generate_protection_warning(skill_name, alt_path)

    # 動的分類
    if protection_warning:
        # 保護スキルへの修正は proposable に降格（ユーザー承認必須）
        category = "proposable"
    elif confidence >= AUTO_FIX_CONFIDENCE and scope in ("file", "project"):
        category = "auto_fixable"
    elif scope == "global" and confidence >= PROPOSABLE_CONFIDENCE:
        # global scope は auto_fixable にせず proposable に留める（ユーザー承認必須）
        category = "proposable"
    elif confidence < PROPOSABLE_CONFIDENCE:
        category = "manual_required"
    else:
        category = "proposable"

    principle_promoted = False
    applied_principles: List[str] = []

    # 原則ベース昇格: proposable 範囲 (0.5 <= confidence < 0.9) の issue に適用
    if category == "proposable" and not protection_warning and scope in ("file", "project"):
        bonus = _apply_principles(issue)
        if bonus > 0 and confidence + bonus >= AUTO_FIX_CONFIDENCE:
            category = "auto_fixable"
            principle_promoted = True
            applied_principles = [
                name for name, p in REMEDIATION_PRINCIPLES.items()
                if issue.get("type", "") in p["applies_to"]
            ]

    result = {
        **issue,
        "confidence_score": confidence,
        "impact_scope": scope,
        "category": category,
    }
    if protection_warning:
        result["protection_warning"] = protection_warning
    if principle_promoted:
        result["principle_promoted"] = True
        result["applied_principles"] = applied_principles
    return result


def partition_proposable_by_confidence(
    proposable: List[Dict[str, Any]],
    threshold: float = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """proposable issue リストを confidence しきい値で2分割する（#377-3）。

    - ``confidence_score >= threshold`` → ``individual``（1件ずつ個別承認）
    - ``confidence_score <  threshold`` → ``batch_skip``（まとめてスキップがデフォルト、
      個別展開は任意）

    Step 5.5 の per-item 承認 MUST が低 confidence FP 群（conf 0.5 中心）で「質問攻め」に
    なる問題を、SKILL.md の文言でなくコード側のしきい値判定で塞ぐ決定論分割。

    confidence_score 欠落は質問攻め回避側（batch_skip）に倒す。両リストとも confidence 降順で
    安定ソートし、提示順を決定論化する。入力リストは破壊しない。

    threshold 未指定時は :data:`PROPOSABLE_INDIVIDUAL_CONFIDENCE` を使う。
    """
    from . import PROPOSABLE_INDIVIDUAL_CONFIDENCE  # noqa: PLC0415

    if threshold is None:
        threshold = PROPOSABLE_INDIVIDUAL_CONFIDENCE

    individual: List[Dict[str, Any]] = []
    batch_skip: List[Dict[str, Any]] = []
    for issue in proposable:
        conf = issue.get("confidence_score", 0.0)
        if conf >= threshold:
            individual.append(issue)
        else:
            batch_skip.append(issue)

    _by_conf = lambda it: it.get("confidence_score", 0.0)  # noqa: E731
    individual.sort(key=_by_conf, reverse=True)
    batch_skip.sort(key=_by_conf, reverse=True)
    return {"individual": individual, "batch_skip": batch_skip}


def partition_proposable_by_scope(
    proposable: List[Dict[str, Any]],
    origin_resolver=None,
) -> Dict[str, List[Dict[str, Any]]]:
    """proposable issue を custom / global スコープに分割する（#477-1）。

    `impact_scope`（impact 由来）と origin（パス由来）の判定が食い違っても、
    impact_scope を最終権威にして global へ寄せる。SKILL.md 上 global scope は
    「参考値・対応不要」であり、個別承認 AskUserQuestion に出してはならない。

    バグ: ~/.claude/rules/ 配下のグローバル rule は `compute_impact_scope` が "global"
    を返す一方、`classify_artifact_origin` は "custom" を返すため、origin だけで
    分割すると proposable_custom_individual に流れ込み、proposable_global は 0 に
    なっていた。ここで impact_scope == "global" OR origin == "global" を global と
    判定して整合を取る（決定論・LLM 非依存）。

    Args:
        proposable: 分類対象の proposable issue リスト。
        origin_resolver: file path → origin 文字列（"custom"/"global"/...）の関数。
            None の場合は impact_scope のみで判定する。

    Returns:
        {"custom": [...], "global": [...]}。入力リストは破壊しない。
    """
    custom: List[Dict[str, Any]] = []
    glob: List[Dict[str, Any]] = []
    for issue in proposable:
        is_global = issue.get("impact_scope") == "global"
        if not is_global and origin_resolver is not None:
            file_path = issue.get("file", "")
            if file_path:
                try:
                    if origin_resolver(file_path) == "global":
                        is_global = True
                except Exception:
                    pass
        if is_global:
            glob.append(issue)
        else:
            custom.append(issue)
    return {"custom": custom, "global": glob}


def classify_issues(issues: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """issue リストを3カテゴリ + fp_excluded に分類する。

    Returns:
        {"auto_fixable": [...], "proposable": [...], "manual_required": [...], "fp_excluded": [...]}
    """
    # mock.patch("remediation.classify_issue") に追従するため package 経由
    from . import classify_issue as _classify_issue  # noqa: PLC0415

    result: Dict[str, List[Dict[str, Any]]] = {
        "auto_fixable": [],
        "proposable": [],
        "manual_required": [],
        "fp_excluded": [],
    }

    for issue in issues:
        classified = _classify_issue(issue)
        category = classified["category"]
        if category in result:
            result[category].append(classified)
        else:
            # 未知のカテゴリは manual_required にフォールバック
            result["manual_required"].append(classified)

    return result
