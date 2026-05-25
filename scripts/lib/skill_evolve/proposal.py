"""自己進化セクション + pitfalls.md テンプレート組み込み (変換提案)。

Phase 8 / Slice 4 で `skill_evolve.py` から切り出し。
`_plugin_root` / `_customize_template` は `__init__.py` を SoT として
`from . import X` 関数本体内 lazy lookup で参照
（`mock.patch("skill_evolve._plugin_root", ...)` /
 `mock.patch("skill_evolve._customize_template")` 経路の互換維持）。
"""
import difflib
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_DEFAULT_LR_BUDGET = 30


def get_skill_lr_budget() -> int:
    """userConfig の skill_lr_budget を返す（デフォルト 30 行）。"""
    try:
        from rl_common.config import load_user_config as _load_user_config
        cfg = _load_user_config()
        return int(cfg.get("skill_lr_budget", _DEFAULT_LR_BUDGET))
    except Exception:
        return _DEFAULT_LR_BUDGET


def _count_diff_lines(original: str, modified: str) -> int:
    """unified diff で +/- 行数（ヘッダー除く）を返す。"""
    diff = list(difflib.unified_diff(
        original.splitlines(), modified.splitlines(), lineterm=""
    ))
    return sum(
        1 for line in diff
        if line.startswith(("+", "-")) and not line.startswith(("---", "+++"))
    )


def get_rejected_stats(skill_name: str) -> Dict[str, Any]:
    """trigger_engine.self_evolution.get_rejected_stats の薄いラッパー。

    一方向 import (skill_evolve → trigger_engine) を保持。
    """
    from trigger_engine.self_evolution import get_rejected_stats as _get_rejected_stats
    return _get_rejected_stats(skill_name)


def evolve_skill_proposal(
    skill_name: str,
    skill_dir: Path,
) -> Dict[str, Any]:
    """適性ありスキルに自己進化パターンを組み込む変換提案を生成する。

    Returns:
        {"skill_name": str, "sections_to_add": str, "pitfalls_template": str,
         "skill_md_path": str, "pitfalls_path": str, "error": str|None}
        rejected pre-flight 発動時: {"status": "skipped", "reason": str}
    """
    # rejected pre-flight: 過去に rejected_rate > 30% なら skip (#200)
    stats = get_rejected_stats(skill_name)
    rejected_rate = stats.get("rejected_rate", 0.0)
    if rejected_rate > 0.30:
        return {
            "status": "skipped",
            "reason": f"rejected_rate={rejected_rate:.0%} > 30%",
        }

    from . import _plugin_root, _customize_template  # 関数内 lazy lookup
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

            # difflib bounded edit gate (#196, #199)
            budget = get_skill_lr_budget()
            diff_lines = _count_diff_lines(template, output)
            if diff_lines > budget:
                logger.warning(
                    "[evolve-skill] diff %d lines > budget %d, fallback to template",
                    diff_lines, budget,
                )
                return template

            return output
    except (subprocess.TimeoutExpired, OSError):
        pass
    # フォールバック: テンプレートそのまま
    return template


def apply_evolve_proposal(proposal: Dict[str, Any]) -> Dict[str, Any]:
    """evolve_skill_proposal() の返り値を受け取り、SKILL.md セクション追記 +
    references/pitfalls.md 作成 + バックアップ作成を実行する。

    Returns:
        {"applied": bool, "backup_path": str|None, "error": str|None}
    """
    if proposal.get("status") == "skipped":
        return {"applied": False, "backup_path": None, "error": None, "skipped": True, "reason": proposal.get("reason")}

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

        # reason_refs HTML コメント追記 (#201)
        correction_ids: List[str] = proposal.get("correction_ids", [])
        if correction_ids:
            ids_yaml = ", ".join(f'"{cid}"' for cid in correction_ids)
            reason_refs_block = f"\n<!-- reason_refs: [{ids_yaml}] -->\n"
            new_content = new_content + reason_refs_block

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
