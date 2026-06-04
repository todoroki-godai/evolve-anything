"""自己進化セクション + pitfalls.md テンプレート組み込み (変換提案)。

Phase 8 / Slice 4 で `skill_evolve.py` から切り出し。
`_plugin_root` / `_customize_template` は `__init__.py` を SoT として
`from . import X` 関数本体内 lazy lookup で参照
（`mock.patch("skill_evolve._plugin_root", ...)` /
 `mock.patch("skill_evolve._customize_template")` 経路の互換維持）。

[ADR-037] Phase 1c: テンプレートカスタマイズの claude -p をファイルベース2相へ移行。
`evolve_skill_proposal` は LLM-free（テンプレそのままのフォールバック）になり、
LLM カスタマイズは `emit_customize_request`（Phase A）→ assistant inline（Phase B）→
`ingest_customized_proposal`（Phase C）で行う。fence 除去 + diff budget gate は
`_parse_customization_response` に集約し、Phase C 側で適用する。
"""
import difflib
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_LR_BUDGET = 30
_REQUIRED_SECTIONS = ["Pre-flight", "Failure-triggered Learning"]


def get_skill_lr_budget() -> int:
    """userConfig の skill_lr_budget を返す（デフォルト 30 行）。"""
    try:
        from rl_common.config import load_user_config as _load_user_config
        cfg = _load_user_config()
        return int(cfg.get("skill_lr_budget", _DEFAULT_LR_BUDGET))
    except Exception:
        return _DEFAULT_LR_BUDGET


def count_diff_lines(original: str, modified: str) -> int:
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


def build_customize_prompt(
    skill_name: str,
    skill_content: str,
    template: str,
) -> str:
    """テンプレートカスタマイズの Phase B プロンプトを生成する（決定論）。"""
    return (
        f"以下のテンプレートを、スキル「{skill_name}」の文脈に合わせてカスタマイズしてください。\n"
        f"テンプレートの構造（見出し、テーブル）は維持し、具体的な表現をスキルに合わせてください。\n"
        f"出力はカスタマイズ後のマークダウンのみ（説明不要）。\n\n"
        f"### スキル内容（先頭2000文字）:\n```\n{skill_content[:2000]}\n```\n\n"
        f"### テンプレート:\n```\n{template}\n```"
    )


def _parse_customization_response(
    raw: Optional[Any],
    template: str,
    budget: Optional[int] = None,
) -> str:
    """Phase B 応答から customized セクションを抽出する（[ADR-037] Phase C のパーサ）。

    Phase B の書き手は assistant（非決定論プロデューサ）なので str を寛容に受け、
    コードフェンスを除去する。diff budget（#196, #199）を超える / 抽出不能なら
    template フォールバックを返す。決定論・LLM 非依存。
    """
    if raw is None:
        return template
    output = raw if isinstance(raw, str) else str(raw)
    output = output.strip()
    if not output:
        return template

    # コードブロック除去
    if output.startswith("```") and output.endswith("```"):
        lines = output.split("\n")
        output = "\n".join(lines[1:-1])

    # difflib bounded edit gate (#196, #199)
    if budget is None:
        budget = get_skill_lr_budget()
    diff_lines = count_diff_lines(template, output)
    if diff_lines > budget:
        logger.warning(
            "[evolve-skill] diff %d lines > budget %d, fallback to template",
            diff_lines, budget,
        )
        return template

    return output


def _customize_template(
    skill_name: str,
    skill_content: str,
    template: str,
) -> str:
    """テンプレートカスタマイズの LLM-free フォールバック（テンプレそのまま）。

    [ADR-037] Phase 1c で claude -p を除去。LLM カスタマイズは
    `emit_customize_request`→`ingest_customized_proposal` の2相に移管した。
    決定論経路（run_loop / fixers_rules）はこのフォールバックで完走する。
    """
    return template


def _load_templates(plugin_root: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """自己進化セクション + pitfalls テンプレートを読む。

    Returns:
        (sections_content, pitfalls_content, error)
        テンプレ不在時は (None, None, error メッセージ)
    """
    templates_dir = plugin_root / "skills" / "evolve" / "templates"
    sections_template = templates_dir / "self-evolve-sections.md"
    pitfalls_template = templates_dir / "pitfalls.md"

    missing = []
    if not sections_template.exists():
        missing.append(str(sections_template))
    if not pitfalls_template.exists():
        missing.append(str(pitfalls_template))
    if missing:
        return None, None, f"テンプレートファイルが見つかりません: {', '.join(missing)}"

    return (
        sections_template.read_text(encoding="utf-8"),
        pitfalls_template.read_text(encoding="utf-8"),
        None,
    )


def _assemble_proposal(
    skill_name: str,
    skill_dir: Path,
    customized: str,
    sections_content: str,
    pitfalls_content: str,
) -> Dict[str, Any]:
    """customized セクションから変換提案 dict を組み立てる（共通処理）。

    必須セクション検証 → 欠落時はテンプレそのままにフォールバック → proposal dict 生成。
    rubric checkpoint を出力する。`evolve_skill_proposal`（決定論）と
    `ingest_customized_proposal`（2相）が共有する。
    """
    valid = all(
        re.search(re.escape(s), customized, re.IGNORECASE)
        for s in _REQUIRED_SECTIONS
    )
    if not valid:
        customized = sections_content

    skill_md = skill_dir / "SKILL.md"
    proposal = {
        "skill_name": skill_name,
        "sections_to_add": customized,
        "pitfalls_template": pitfalls_content,
        "skill_md_path": str(skill_md),
        "pitfalls_path": str(skill_dir / "references" / "pitfalls.md"),
        "diff_lines": count_diff_lines(sections_content, customized),
        "error": None,
    }
    from .rubric import rubric_checkpoint
    checkpoint = rubric_checkpoint("propose", proposal)
    for line in checkpoint["stdout_lines"]:
        print(line)
    return proposal


def _rejected_preflight(skill_name: str) -> Optional[Dict[str, Any]]:
    """rejected pre-flight: 過去に rejected_rate > 30% なら skip dict を返す (#200)。"""
    stats = get_rejected_stats(skill_name)
    rejected_rate = stats.get("rejected_rate", 0.0)
    if rejected_rate > 0.30:
        return {
            "status": "skipped",
            "reason": f"rejected_rate={rejected_rate:.0%} > 30%",
        }
    return None


def evolve_skill_proposal(
    skill_name: str,
    skill_dir: Path,
) -> Dict[str, Any]:
    """適性ありスキルに自己進化パターンを組み込む変換提案を生成する（LLM-free）。

    [ADR-037] Phase 1c: テンプレートをそのまま（決定論フォールバック）採用する。
    LLM カスタマイズが必要な場合は `emit_customize_request`→`ingest_customized_proposal`
    の2相を使う。run_loop / fixers_rules はこの決定論経路で完走する。

    Returns:
        {"skill_name": str, "sections_to_add": str, "pitfalls_template": str,
         "skill_md_path": str, "pitfalls_path": str, "error": str|None}
        rejected pre-flight 発動時: {"status": "skipped", "reason": str}
    """
    skipped = _rejected_preflight(skill_name)
    if skipped:
        return skipped

    from . import _plugin_root, _customize_template  # 関数内 lazy lookup
    sections_content, pitfalls_content, error = _load_templates(_plugin_root)
    if error:
        return {"skill_name": skill_name, "error": error}

    skill_md = skill_dir / "SKILL.md"
    skill_content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else ""

    customized = _customize_template(skill_name, skill_content, sections_content)
    return _assemble_proposal(
        skill_name, skill_dir, customized, sections_content, pitfalls_content
    )


def emit_customize_request(
    skill_name: str,
    skill_dir: Path,
) -> Dict[str, Any]:
    """Phase A: テンプレートカスタマイズの request を生成する。

    Returns:
        {"requests": [{"id": skill_name, "prompt": str, "meta": {}}]}
        テンプレ不在時: {"requests": [], "error": str}
    """
    from . import _plugin_root
    from llm_broker import build_requests

    sections_content, _pitfalls, error = _load_templates(_plugin_root)
    if error:
        return {"requests": [], "error": error}

    skill_md = skill_dir / "SKILL.md"
    skill_content = skill_md.read_text(encoding="utf-8") if skill_md.exists() else ""

    items = [{
        "id": skill_name,
        "_skill_content": skill_content,
        "_template": sections_content,
    }]
    requests = build_requests(
        items,
        lambda it: build_customize_prompt(it["id"], it["_skill_content"], it["_template"]),
    )
    for r in requests:
        r["meta"].pop("_skill_content", None)
        r["meta"].pop("_template", None)
    return {"requests": requests}


def ingest_customized_proposal(
    skill_name: str,
    skill_dir: Path,
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
) -> Dict[str, Any]:
    """Phase C: Phase B 応答からカスタマイズ済み変換提案を組み立てる。

    fence 除去 + diff budget gate は `_parse_customization_response` が適用。
    応答欠損 / 予算超過 / 必須セクション欠落はテンプレそのままにフォールバックする。

    Returns:
        evolve_skill_proposal と同形の proposal dict。
        rejected pre-flight / テンプレ不在は同様に早期 return。
    """
    skipped = _rejected_preflight(skill_name)
    if skipped:
        return skipped

    from . import _plugin_root
    from llm_broker import parse_responses

    sections_content, pitfalls_content, error = _load_templates(_plugin_root)
    if error:
        return {"skill_name": skill_name, "error": error}

    parsed = parse_responses(
        requests,
        responses,
        parser=lambda raw: _parse_customization_response(raw, sections_content),
    )
    customized = parsed.get(skill_name, sections_content)
    return _assemble_proposal(
        skill_name, skill_dir, customized, sections_content, pitfalls_content
    )


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

        result = {
            "applied": True,
            "backup_path": str(backup_path),
            "error": None,
        }
        from .rubric import rubric_checkpoint
        checkpoint = rubric_checkpoint("apply", proposal)
        for line in checkpoint["stdout_lines"]:
            print(line)
        return result
    except OSError as e:
        return {"applied": False, "backup_path": None, "error": str(e)}
