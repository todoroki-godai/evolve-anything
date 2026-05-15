"""参照型スキルのドリフト評価（旧 prune.py 由来）。

prune/__init__.py から re-export される（後方互換）。
`_evaluate_drift` は package 経由で遅延参照する
（テスト mock.patch("prune._evaluate_drift", ...) 追従）。
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import load_drift_threshold
from .skill_inspect import _resolve_skill_md, is_reference_skill


def _gather_drift_context(skill_path: Path, project_dir: Path) -> str:
    """ドリフト評価用のコンテキストを収集する。

    CLAUDE.md、rules、スキル内容から関連ファイルのコンテキストをまとめる。
    """
    context_parts = []

    # スキル内容
    resolved = _resolve_skill_md(skill_path)
    if resolved.exists():
        context_parts.append(f"=== Skill Content ({resolved.name}) ===\n{resolved.read_text(encoding='utf-8')}")

    # CLAUDE.md
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        context_parts.append(f"=== CLAUDE.md ===\n{claude_md.read_text(encoding='utf-8')}")

    # rules
    rules_dir = project_dir / ".claude" / "rules"
    if rules_dir.exists():
        for rule_file in sorted(rules_dir.glob("*.md"))[:10]:
            context_parts.append(f"=== Rule: {rule_file.name} ===\n{rule_file.read_text(encoding='utf-8')}")

    return "\n\n".join(context_parts)


def detect_reference_drift(
    artifacts: Dict[str, List[Path]],
    project_dir: Path,
) -> List[Dict[str, Any]]:
    """参照型スキルの内容とコードベースの乖離度を評価し、ドリフト候補を返す。

    サブエージェント呼び出しで乖離度を 0.0〜1.0 で評価する。
    サブエージェント失敗時はそのスキルを候補に含めない。
    非参照型スキルは評価しない。
    """
    # mock.patch("prune._evaluate_drift", ...) 追従のため package 経由で参照
    from . import _evaluate_drift  # noqa: PLC0415

    threshold = load_drift_threshold()
    candidates = []

    for path in artifacts.get("skills", []):
        # 参照型スキルのみ対象
        if not is_reference_skill(path):
            continue

        skill_name = path.parent.name
        try:
            context = _gather_drift_context(path, project_dir)
            # サブエージェントでドリフト評価
            # 実際の実行時は Agent tool で LLM 評価を行う
            # ここではコンテキスト収集までを行い、スコアは呼び出し側で設定
            drift_result = _evaluate_drift(context, skill_name)
            if drift_result and drift_result.get("drift_score", 0) >= threshold:
                candidates.append({
                    "file": str(path),
                    "skill_name": skill_name,
                    "reason": "reference_drift",
                    "drift_score": drift_result["drift_score"],
                    "drift_reason": drift_result.get("drift_reason", ""),
                })
        except Exception as e:
            # サブエージェント失敗時は候補に含めない（安全側倒し）
            print(f"[prune] drift evaluation failed for {skill_name}: {e}", file=sys.stderr)
            continue

    return candidates


def _evaluate_drift(context: str, skill_name: str) -> Optional[Dict[str, Any]]:
    """ドリフト評価のプレースホルダ。

    実際の prune スキル実行時は Agent tool のサブエージェントで
    コンテキストを評価し、drift_score と drift_reason を返す。
    ここではテスト用にデフォルト値を返す。
    """
    # プレースホルダ実装: 実運用時はサブエージェントで置換
    return {"drift_score": 0.0, "drift_reason": ""}
