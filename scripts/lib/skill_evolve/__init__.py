#!/usr/bin/env python3
"""スキル自己進化適性判定エンジン。

テレメトリ3軸 + LLMキャッシュ2軸の5項目スコアリングで
スキルの自己進化適性を判定する。

Phase 8 で `skill_evolve.py` (754 行) をパッケージに分割:
- `telemetry_scoring.py`: テレメトリ3軸 (frequency / diversity / evaluability)
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- 定数 (design Decision 9) ---

MEDIUM_SUITABILITY_THRESHOLD = 8
HIGH_SUITABILITY_THRESHOLD = 12
ROOT_CAUSE_JACCARD_THRESHOLD = 0.5
HOT_TIER_MAX_ITEMS = 5
ACTIVE_PITFALL_CAP = 10
GRADUATION_THRESHOLDS = {3: 10, 2: 5, 1: 3}
STALE_KNOWLEDGE_MONTHS = 6
ANTI_PATTERN_REJECTION_COUNT = 2
BAND_AID_THRESHOLD = 10
SUCCESS_PATTERN_LIMIT = 2
WARM_TOKEN_BUDGET = 1000
HOT_TOKEN_BUDGET = 500
HIGH_CONFIDENCE = 0.85
MEDIUM_CONFIDENCE = 0.60
CANDIDATE_PROMOTION_COUNT = 2

# pitfall-lifecycle-automation 定数
INTEGRATION_JACCARD_THRESHOLD = 0.3
GRADUATED_TTL_DAYS = 30
STALE_ESCALATION_MONTHS = 3
PITFALL_MAX_LINES = 100
ERROR_FREQUENCY_THRESHOLD = 3

# 検証系スキル自動昇格キーワード
VERIFICATION_SKILL_KEYWORDS = [
    "verify", "validate", "check", "lint", "test", "qa", "audit",
    "assert", "inspect", "scan",
]

# 合理化防止テーブル定数 (superpowers-knowledge-integration)
RATIONALIZATION_MIN_CORRECTIONS = 3
RATIONALIZATION_SKIP_KEYWORDS = [
    "skip", "スキップ", "省略", "bypass", "later", "後で",
    "不要", "unnecessary", "without", "なし", "いらない",
    "面倒", "time", "時間がない", "急ぎ",
]
RATIONALIZATION_OUTCOME_WINDOW_DAYS = 30

# LLMキャッシュ
# `<repo>/scripts/lib/skill_evolve/__init__.py` → `<repo>/scripts`
_plugin_root = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path.home() / ".claude" / "rl-anything"
CACHE_FILE = DATA_DIR / "skill-evolve-cache.json"

# --- ユーティリティ ---


def _file_hash(path: Path) -> str:
    """ファイルの SHA256 ハッシュを返す。"""
    content = path.read_text(encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _load_cache() -> Dict[str, Any]:
    """LLMスコアリングキャッシュを読み込む。"""
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    """LLMスコアリングキャッシュを保存する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- 自己進化済み判定 ---


def is_self_evolved_skill(skill_dir: Path) -> bool:
    """スキルが既に自己進化パターンを持っているか判定する。

    判定条件:
    - references/pitfalls.md が存在する
    - SKILL.md に Failure-triggered Learning セクションが存在する
    """
    pitfalls = skill_dir / "references" / "pitfalls.md"
    skill_md = skill_dir / "SKILL.md"

    if not pitfalls.exists():
        return False
    if not skill_md.exists():
        return False

    content = skill_md.read_text(encoding="utf-8")
    return bool(re.search(r"(?i)failure[- ]triggered\s+learning", content))


def is_verification_skill(skill_name: str, skill_dir: Path) -> bool:
    """検証系スキルかどうかを判定する。

    スキル名またはSKILL.md内容にVERIFICATION_SKILL_KEYWORDSが含まれればTrue。
    検証系スキルは失敗時のインパクトが大きいため、テレメトリに関係なく
    自己進化パターンの組み込みを推奨する。
    """
    name_lower = skill_name.lower()
    for kw in VERIFICATION_SKILL_KEYWORDS:
        if kw in name_lower:
            return True

    skill_md = skill_dir / "SKILL.md"
    if skill_md.exists():
        try:
            content = skill_md.read_text(encoding="utf-8").lower()
            for kw in VERIFICATION_SKILL_KEYWORDS:
                if kw in content:
                    return True
        except OSError:
            pass

    return False


# --- テレメトリ3軸 (telemetry_scoring.py に分離 / Phase 8 Slice 1) ---
from .telemetry_scoring import (  # noqa: E402,F401
    TELEMETRY_LOOKBACK_DAYS,
    _score_execution_frequency,
    _score_failure_diversity,
    _score_output_evaluability,
    compute_telemetry_scores,
)


# --- LLM 2軸 (llm_scoring.py に分離 / Phase 8 Slice 2) ---
from .llm_scoring import (  # noqa: E402,F401
    _EXTERNAL_DEPENDENCY_KEYWORDS,
    _count_external_keywords,
    _score_external_dependency,
    _score_judgment_complexity_llm,
    compute_llm_scores,
)


# --- 分類 & アンチパターン検出 ---


def classify_suitability(total_score: int) -> str:
    """合計スコアから適性を3段階分類する。"""
    if total_score >= HIGH_SUITABILITY_THRESHOLD:
        return "high"
    if total_score >= MEDIUM_SUITABILITY_THRESHOLD:
        return "medium"
    return "low"


def detect_anti_patterns(
    scores: Dict[str, int],
    skill_dir: Path,
) -> List[Dict[str, str]]:
    """評価時3パターンのアンチパターンを検出する。

    Returns:
        [{"pattern": str, "reason": str}, ...]
    """
    patterns: List[Dict[str, str]] = []

    # Noise Collector: 失敗多様性=1 かつ エラーデータあり
    # エラーデータ0件（テレメトリ不在）は判定不能として除外
    if scores.get("diversity", 0) == 1 and scores.get("error_count", 0) > 0:
        patterns.append({
            "pattern": "Noise Collector",
            "reason": "失敗パターンが少ないため、スキル本体の1回修正が効果的です",
        })

    # Context Bloat: 頻度=3 かつ 判断=1
    if scores.get("frequency", 0) == 3 and scores.get("judgment_complexity", 0) == 1:
        patterns.append({
            "pattern": "Context Bloat",
            "reason": "Pre-flight のトークンコストが学習価値を超える可能性があります",
        })

    # Band-Aid: references/ 内の知見蓄積が閾値超
    # SKILL.md の手順ステップ/チェックリストは除外し、
    # references/ のみカウントすることで誤検出を防ぐ
    refs_dir = skill_dir / "references"
    if refs_dir.exists():
        troubleshoot_items = 0
        for ref_file in refs_dir.glob("*.md"):
            ref_content = ref_file.read_text(encoding="utf-8")
            troubleshoot_items += len(re.findall(
                r"^[\s]*[-*]\s+", ref_content, re.MULTILINE
            ))
        if troubleshoot_items > BAND_AID_THRESHOLD:
            patterns.append({
                "pattern": "Band-Aid",
                "reason": f"references/ 内の知見蓄積が{troubleshoot_items}件超 — 設計見直しを推奨",
            })

    return patterns


# --- メインエントリポイント ---


def skill_evolve_assessment(
    project_dir: Optional[Path] = None,
    *,
    project: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """全カスタムスキルの自己進化適性を判定する。

    Args:
        project_dir: プロジェクトディレクトリ
        project: テレメトリのプロジェクトフィルタ

    Returns:
        [{"skill_name": str, "skill_dir": str, "scores": {...},
          "total_score": int, "suitability": str,
          "anti_patterns": [...], "recommendation": str}, ...]
    """
    sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
    from audit import classify_artifact_origin, find_artifacts

    proj = project_dir or Path.cwd()
    artifacts = find_artifacts(proj)

    results: List[Dict[str, Any]] = []

    for skill_path in artifacts.get("skills", []):
        skill_dir = skill_path.parent
        skill_name = skill_dir.name

        # 対象フィルタ: custom/global のみ
        origin = classify_artifact_origin(skill_path)
        if origin == "plugin":
            continue

        # symlink 除外
        if skill_dir.is_symlink():
            continue

        # 既に自己進化済みは除外
        if is_self_evolved_skill(skill_dir):
            results.append({
                "skill_name": skill_name,
                "skill_dir": str(skill_dir),
                "already_evolved": True,
                "suitability": "already_evolved",
            })
            continue

        # テレメトリ3軸
        telemetry = compute_telemetry_scores(skill_name, project=project)

        # LLM 2軸
        llm = compute_llm_scores(skill_name, skill_dir)

        scores = {
            "frequency": telemetry["frequency"],
            "diversity": telemetry["diversity"],
            "evaluability": telemetry["evaluability"],
            "external_dependency": llm["external_dependency"],
            "judgment_complexity": llm["judgment_complexity"],
            "error_count": telemetry["error_count"],
        }

        total_score = sum(scores.values())
        suitability = classify_suitability(total_score)

        # アンチパターン検出
        anti_patterns = detect_anti_patterns(scores, skill_dir)
        rejection_count = sum(
            1 for ap in anti_patterns
            if ap["pattern"] in ("Noise Collector", "Context Bloat", "Band-Aid")
        )

        verification_bypass = False
        if rejection_count >= ANTI_PATTERN_REJECTION_COUNT:
            recommendation = "変換非推奨: 評価時アンチパターン{}件該当".format(rejection_count)
            suitability = "rejected"
        elif suitability == "high":
            recommendation = "変換を推奨"
        elif suitability == "medium":
            recommendation = "変換可能 — ユーザー判断に委ねます"
        elif is_verification_skill(skill_name, skill_dir):
            suitability = "medium"
            verification_bypass = True
            recommendation = "変換可能 — 検証系スキルのため自己進化を推奨"
        else:
            recommendation = "変換非推奨"

        entry = {
            "skill_name": skill_name,
            "skill_dir": str(skill_dir),
            "already_evolved": False,
            "scores": scores,
            "total_score": total_score,
            "suitability": suitability,
            "anti_patterns": anti_patterns,
            "recommendation": recommendation,
            "telemetry_detail": {
                "usage_count": telemetry["usage_count"],
                "error_count": telemetry["error_count"],
                "error_categories": telemetry["error_categories"],
            },
            "llm_cached": llm["cached"],
        }
        if verification_bypass:
            entry["verification_bypass"] = True
        results.append(entry)

    return results


# --- 変換提案 ---


def evolve_skill_proposal(
    skill_name: str,
    skill_dir: Path,
) -> Dict[str, Any]:
    """適性ありスキルに自己進化パターンを組み込む変換提案を生成する。

    Returns:
        {"skill_name": str, "sections_to_add": str, "pitfalls_template": str,
         "skill_md_path": str, "pitfalls_path": str, "error": str|None}
    """
    templates_dir = _plugin_root / "skills" / "evolve" / "templates"
    sections_template = templates_dir / "self-evolve-sections.md"
    pitfalls_template = templates_dir / "pitfalls.md"

    # テンプレート不在チェック
    missing = []
    if not sections_template.exists():
        missing.append(str(sections_template))
    if not pitfalls_template.exists():
        missing.append(str(pitfalls_template))
    if missing:
        return {
            "skill_name": skill_name,
            "error": f"テンプレートファイルが見つかりません: {', '.join(missing)}",
        }

    sections_content = sections_template.read_text(encoding="utf-8")
    pitfalls_content = pitfalls_template.read_text(encoding="utf-8")

    # LLM でスキル文脈にカスタマイズ
    skill_md = skill_dir / "SKILL.md"
    skill_content = ""
    if skill_md.exists():
        skill_content = skill_md.read_text(encoding="utf-8")

    customized = _customize_template(skill_name, skill_content, sections_content)

    # 検証: 必須セクションの存在確認
    required_sections = ["Pre-flight", "Failure-triggered Learning"]
    valid = all(
        re.search(re.escape(s), customized, re.IGNORECASE)
        for s in required_sections
    )

    if not valid:
        # フォールバック: テンプレートをそのまま使用
        customized = sections_content

    return {
        "skill_name": skill_name,
        "sections_to_add": customized,
        "pitfalls_template": pitfalls_content,
        "skill_md_path": str(skill_md),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "error": None,
    }


def _customize_template(
    skill_name: str,
    skill_content: str,
    template: str,
) -> str:
    """テンプレートをスキルの文脈にカスタマイズする。"""
    prompt = (
        f"以下のテンプレートを、スキル「{skill_name}」の文脈に合わせてカスタマイズしてください。\n"
        f"テンプレートの構造（見出し、テーブル）は維持し、具体的な表現をスキルに合わせてください。\n"
        f"出力はカスタマイズ後のマークダウンのみ（説明不要）。\n\n"
        f"### スキル内容（先頭2000文字）:\n```\n{skill_content[:2000]}\n```\n\n"
        f"### テンプレート:\n```\n{template}\n```"
    )
    try:
        result = subprocess.run(
            ["claude", "--print", "-p", prompt],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            # コードブロック除去
            if output.startswith("```") and output.endswith("```"):
                lines = output.split("\n")
                output = "\n".join(lines[1:-1])
            return output
    except (subprocess.TimeoutExpired, OSError):
        pass
    # フォールバック: テンプレートそのまま
    return template


# --- 単一スキル向け共通関数 (D3) ---


def _find_project_dir(skill_dir: Path) -> Optional[Path]:
    """skill_dir からプロジェクトルートを推定する。

    .claude/skills/<name>/ の2階層上をプロジェクトルートとみなす。
    見つからない場合は None。
    """
    # .claude/skills/<skill_name>/SKILL.md → .claude → project_root
    candidate = skill_dir.resolve()
    for _ in range(5):
        parent = candidate.parent
        if parent.name == "skills" and parent.parent.name == ".claude":
            return parent.parent.parent
        candidate = parent
    return None


def assess_single_skill(
    skill_name: str,
    skill_dir: Path,
    *,
    project: Optional[str] = None,
) -> Dict[str, Any]:
    """1スキルの自己進化適性判定結果を返す。

    Returns:
        {"skill_name": str, "skill_dir": str, "already_evolved": bool,
         "suitability": str, "scores": {...}, "total_score": int,
         "anti_patterns": [...], "recommendation": str}
    """
    skill_dir = Path(skill_dir)

    # 既に自己進化済み
    if is_self_evolved_skill(skill_dir):
        return {
            "skill_name": skill_name,
            "skill_dir": str(skill_dir),
            "already_evolved": True,
            "suitability": "already_evolved",
            "workflow_checkpoints": None,
        }

    # テレメトリ3軸
    telemetry = compute_telemetry_scores(skill_name, project=project)

    # LLM 2軸
    llm = compute_llm_scores(skill_name, skill_dir)

    scores = {
        "frequency": telemetry["frequency"],
        "diversity": telemetry["diversity"],
        "evaluability": telemetry["evaluability"],
        "external_dependency": llm["external_dependency"],
        "judgment_complexity": llm["judgment_complexity"],
        "error_count": telemetry["error_count"],
    }

    total_score = sum(scores.values())
    suitability = classify_suitability(total_score)

    # アンチパターン検出
    anti_patterns = detect_anti_patterns(scores, skill_dir)
    rejection_count = sum(
        1 for ap in anti_patterns
        if ap["pattern"] in ("Noise Collector", "Context Bloat", "Band-Aid")
    )

    # 検証系スキルのバイパス判定
    verification_bypass = False
    if rejection_count >= ANTI_PATTERN_REJECTION_COUNT:
        recommendation = "変換非推奨: 評価時アンチパターン{}件該当".format(rejection_count)
        suitability = "rejected"
    elif suitability == "high":
        recommendation = "変換を推奨"
    elif suitability == "medium":
        recommendation = "変換可能 — ユーザー判断に委ねます"
    elif is_verification_skill(skill_name, skill_dir):
        # 検証系スキルは low でも medium に昇格
        suitability = "medium"
        verification_bypass = True
        recommendation = "変換可能 — 検証系スキルのため自己進化を推奨"
    else:
        recommendation = "変換非推奨"

    # ワークフローチェックポイント検出
    workflow_checkpoints = None
    try:
        from workflow_checkpoint import is_workflow_skill, detect_checkpoint_gaps
    except ImportError:
        try:
            from lib.workflow_checkpoint import is_workflow_skill, detect_checkpoint_gaps
        except ImportError:
            is_workflow_skill = None

    if is_workflow_skill is not None and is_workflow_skill(skill_dir):
        try:
            project_dir = _find_project_dir(skill_dir)
            if project_dir:
                workflow_checkpoints = detect_checkpoint_gaps(
                    skill_name, skill_dir, project_dir,
                )
        except Exception:
            workflow_checkpoints = []

    result = {
        "skill_name": skill_name,
        "skill_dir": str(skill_dir),
        "already_evolved": False,
        "scores": scores,
        "total_score": total_score,
        "suitability": suitability,
        "anti_patterns": anti_patterns,
        "recommendation": recommendation,
        "telemetry_detail": {
            "usage_count": telemetry["usage_count"],
            "error_count": telemetry["error_count"],
            "error_categories": telemetry["error_categories"],
        },
        "llm_cached": llm["cached"],
        "workflow_checkpoints": workflow_checkpoints,
    }
    if verification_bypass:
        result["verification_bypass"] = True
    return result


def apply_evolve_proposal(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """evolve_skill_proposal() の返り値を受け取り、SKILL.md セクション追記 +
    references/pitfalls.md 作成 + バックアップ作成を実行する。

    Returns:
        {"applied": bool, "backup_path": str|None, "error": str|None}
    """
    if proposal.get("error"):
        return {"applied": False, "backup_path": None, "error": proposal["error"]}

    skill_md = Path(proposal["skill_md_path"])
    pitfalls_path = Path(proposal["pitfalls_path"])

    try:
        # バックアップ作成 (D6)
        backup_path = skill_md.with_name(skill_md.name + ".pre-evolve-backup")
        original_content = ""
        if skill_md.exists():
            original_content = skill_md.read_text(encoding="utf-8")
            backup_path.write_text(original_content, encoding="utf-8")

        # SKILL.md にセクション追記
        new_content = original_content.rstrip() + "\n\n" + proposal["sections_to_add"] + "\n"
        skill_md.write_text(new_content, encoding="utf-8")

        # references/pitfalls.md 作成
        pitfalls_path.parent.mkdir(parents=True, exist_ok=True)
        pitfalls_path.write_text(proposal["pitfalls_template"], encoding="utf-8")

        return {
            "applied": True,
            "backup_path": str(backup_path),
            "error": None,
        }
    except OSError as e:
        return {"applied": False, "backup_path": None, "error": str(e)}
