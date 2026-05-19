"""optimize.py から抽出したコアロジック。

エラー分類・コンテキスト収集・プロンプト構築・LLM呼び出し・ゲート判定を
DirectPatchOptimizer から独立した純粋関数として提供する。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# regression_gate を単独 import 可能にするため自己パス解決
_CORE_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LIB_PATH = str(_CORE_PLUGIN_ROOT / "scripts" / "lib")
if _LIB_PATH not in sys.path:
    sys.path.insert(0, _LIB_PATH)

PITFALLS_MAX_ROWS = 50
PITFALLS_HEADER = "| Source | Pattern | Score |\n|--------|---------|-------|\n"


# ── corrections / context 収集 ─────────────────────────────────────


def collect_corrections(
    target_skill_name: str,
    corrections_path: Path,
    max_items: int,
) -> List[Dict[str, Any]]:
    """corrections.jsonl から対象スキル関連の pending レコードを抽出する。"""
    if not corrections_path.exists():
        return []

    corrections = []
    try:
        for line in corrections_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("reflect_status") == "applied":
                continue
            last_skill = record.get("last_skill") or ""
            if target_skill_name.lower() in last_skill.lower():
                corrections.append(record)
    except OSError:
        return []

    return corrections[-max_items:]


def collect_context(
    target_path: Path,
    plugin_root: Path,
    target_skill_name: str,
) -> Dict[str, Any]:
    """workflow_stats, audit collect_issues, pitfalls.md を統合してコンテキスト辞書を返す。"""
    context: Dict[str, Any] = {}

    try:
        stats_path = Path.home() / ".claude" / "rl-anything" / "workflow_stats.json"
        if stats_path.exists():
            data = json.loads(stats_path.read_text(encoding="utf-8"))
            hint = extract_workflow_hint(data, target_skill_name)
            if hint:
                context["workflow_hint"] = hint
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: workflow_stats 読み込み失敗: {e}", file=sys.stderr)

    try:
        audit_script = plugin_root / "skills" / "audit" / "scripts" / "audit.py"
        if audit_script.exists():
            sys.path.insert(0, str(audit_script.parent))
            from audit import collect_issues  # type: ignore[import]
            issues = collect_issues(Path.cwd())
            if issues:
                context["audit_issues"] = issues[:10]
    except Exception as e:
        print(f"Warning: audit collect_issues 失敗: {e}", file=sys.stderr)

    try:
        pitfalls_file = target_path.parent / "references" / "pitfalls.md"
        if pitfalls_file.exists():
            context["pitfalls"] = pitfalls_file.read_text(encoding="utf-8")
    except OSError as e:
        print(f"Warning: pitfalls.md 読み込み失敗: {e}", file=sys.stderr)

    return context


def extract_workflow_hint(data: Dict[str, Any], target_skill_name: str) -> str:
    """workflow_stats.json からスキル向けのヒントを抽出する。"""
    if "hints" not in data or "stats" not in data:
        return ""
    for key, hint_text in data.get("hints", {}).items():
        key_parts = key.split(":")
        if target_skill_name in key_parts or key == target_skill_name:
            return hint_text
    return ""


# ── 戦略決定 ────────────────────────────────────────────────────────


def determine_strategy(mode: str, corrections: List[Dict[str, Any]]) -> str:
    """corrections 有無とモード指定から最適化戦略を決定する。"""
    if mode == "auto":
        return "error_guided" if corrections else "llm_improve"
    if mode == "error_guided":
        if not corrections:
            print("対象スキルの corrections が見つかりません。llm_improve モードにフォールバックします。")
            return "llm_improve"
        return "error_guided"
    return "llm_improve"


# ── プロンプト構築 ───────────────────────────────────────────────────


def build_patch_prompt(
    skill_content: str,
    corrections: List[Dict[str, Any]],
    context: Dict[str, Any],
    strategy: str,
    is_rule_file: bool,
    max_lines: int,
) -> str:
    """モードに応じたパッチプロンプトを構築する。"""
    file_type = "ルール" if is_rule_file else "スキル"
    rule_note = "ルールは3行以内が原則です。" if is_rule_file else "冗長な説明を避け、簡潔に保ってください。"
    line_constraint = (
        f"\n\n**重要な制約**: 出力は {max_lines} 行以内に収めてください。{rule_note}"
    )

    prompt_parts = [
        f"以下のClaude Code{file_type}定義を改善してください。\n",
        f"元の{file_type}:\n```markdown\n{skill_content}\n```\n",
    ]

    if strategy == "error_guided":
        prompt_parts.append("## 修正すべき問題点\n")
        prompt_parts.append("以下のユーザー修正フィードバックに基づいて、スキルを改善してください:\n")
        for i, corr in enumerate(corrections, 1):
            msg = corr.get("message", "")
            ctype = corr.get("correction_type", "unknown")
            learning = corr.get("extracted_learning", "")
            prompt_parts.append(f"\n### 修正 {i} (type: {ctype})")
            if msg:
                prompt_parts.append(f"メッセージ: {msg}")
            if learning:
                prompt_parts.append(f"学習: {learning}")
        prompt_parts.append("\n上記のフィードバックを反映し、同じ問題が再発しないようにスキルを修正してください。\n")
    else:
        prompt_parts.append("## 改善方針\n")
        prompt_parts.append(
            "以下の情報を参考に、スキルの品質を向上させてください:\n"
            "- より具体的な例を追加\n"
            "- 曖昧な指示を明確化\n"
            "- 構造を整理\n"
            "- 不要な冗長性を削除\n"
            "- エッジケースの対処を追加\n"
        )

    if context.get("workflow_hint"):
        prompt_parts.append(f"\n## ワークフロー分析からの示唆\n{context['workflow_hint']}\n")

    if context.get("audit_issues"):
        prompt_parts.append("\n## 検出された構造的問題\n")
        for issue in context["audit_issues"]:
            prompt_parts.append(
                f"- [{issue.get('type', '')}] {issue.get('file', '')}: {issue.get('detail', '')}"
            )
        prompt_parts.append("")

    if context.get("pitfalls"):
        prompt_parts.append(f"\n## 過去の失敗パターン\n{context['pitfalls']}\n")

    fm_note = (
        "\n**重要**: ファイル先頭の `---` で始まる YAML frontmatter は"
        "必ずそのまま保持してください（削除・変更禁止）。"
        if skill_content.startswith("---")
        else ""
    )
    prompt_parts.append(
        f"改善後の{file_type}全文をMarkdownで出力してください。"
        "```markdown と ``` で囲んでください。"
        f"{fm_note}"
        f"{line_constraint}"
    )

    return "\n".join(prompt_parts)


# ── LLM 呼び出し ────────────────────────────────────────────────────


def call_llm(prompt: str, claude_cwd: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """claude -p を1回呼び出し、パッチ結果を返す。

    Returns:
        (patched_content, error) のタプル。成功時は error=None。
    """
    try:
        run_kwargs: Dict[str, Any] = dict(
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if claude_cwd:
            run_kwargs["cwd"] = claude_cwd
        result = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            **run_kwargs,
        )
        if result.returncode != 0:
            return None, f"claude -p がエラーコード {result.returncode} で終了"

        content = extract_markdown(result.stdout)
        if not content:
            return None, "LLM レスポンスからコンテンツを抽出できませんでした"

        return content, None

    except subprocess.TimeoutExpired:
        return None, "LLM コールがタイムアウトしました（180秒）"
    except FileNotFoundError:
        return None, "claude CLI が見つかりません"


def extract_markdown(text: str) -> Optional[str]:
    """```markdown ... ``` ブロックからコンテンツを抽出する。

    複数ブロックがある場合は最長のものを返す。
    """
    pattern = r"```(?:markdown)?\s*\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        longest = max(matches, key=len).strip()
        if longest:
            return longest
    stripped = text.strip()
    if stripped:
        return stripped
    return None


def _extract_frontmatter(content: str) -> Tuple[str, str]:
    """YAML frontmatter と本文を分離する。

    \r\n 改行を正規化してから処理する（Windows 環境対応）。

    Returns:
        (frontmatter, body) — frontmatter は "---\n...\n---\n" 形式。
        frontmatter がない場合は ("", content)。
    """
    normalized = content.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return "", content
    end = normalized.find("\n---\n", 4)
    if end == -1:
        return "", content
    fm = normalized[: end + 5]  # "---\n" 末尾まで含む
    body = normalized[end + 5 :]
    return fm, body


def restore_frontmatter_if_lost(candidate: str, original: str) -> str:
    """LLM が frontmatter を消した場合に元の frontmatter を自動補完する。

    original に frontmatter があり candidate に無い場合のみ補完。
    """
    if not original.startswith("---"):
        return candidate
    if candidate.startswith("---"):
        return candidate
    fm, _ = _extract_frontmatter(original)
    if fm:
        return fm + candidate
    return candidate


# ── ゲート / pitfall ─────────────────────────────────────────────────


def format_gate_reason(reason: Optional[str]) -> str:
    """ゲート不合格理由をユーザー向けメッセージに変換する。"""
    if not reason:
        return "不明な理由"
    if reason == "empty":
        return "パッチ内容が空です"
    if reason.startswith("line_limit_exceeded"):
        return f"行数制限超過（{reason}）"
    if reason.startswith("forbidden_pattern"):
        return f"禁止パターン検出（{reason}）"
    if reason.startswith("pitfall_pattern"):
        return f"既知の失敗パターン検出（{reason}）"
    if reason == "frontmatter_lost":
        return "YAML frontmatter が消失しました"
    return reason


from regression_gate import check_gates  # noqa: E402  (sys.path set above)


def run_regression_gate(
    content: str,
    original: Optional[str],
    max_lines: int,
    pitfall_path: Optional[str],
) -> Tuple[bool, Optional[str]]:
    """構造的必要条件のハードゲートチェック。regression_gate に委譲。"""
    result = check_gates(
        candidate=content,
        original=original,
        max_lines=max_lines,
        pitfall_patterns_path=pitfall_path,
    )
    if result.passed:
        return True, None
    reason = result.reason
    if reason == "empty_content":
        reason = "empty"
    return False, reason


# ── fitness ──────────────────────────────────────────────────────────


def run_custom_fitness(
    content: str,
    fitness_func: str,
    plugin_root: Path,
) -> Optional[float]:
    """カスタム適応度関数を実行（参考スコア表示用）。"""
    if fitness_func == "default":
        return None

    project_root = Path.cwd()
    fitness_path = project_root / "scripts" / "rl" / "fitness" / f"{fitness_func}.py"

    if not fitness_path.exists():
        plugin_fitness_path = plugin_root / "scripts" / "fitness" / f"{fitness_func}.py"
        if plugin_fitness_path.exists():
            fitness_path = plugin_fitness_path
        else:
            print(f"  適応度関数が見つかりません: {fitness_func}")
            return None

    try:
        result = subprocess.run(
            [sys.executable, str(fitness_path)],
            input=content,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            score = float(result.stdout.strip())
            return max(0.0, min(1.0, score))
        else:
            print(f"  適応度関数エラー: {result.stderr.strip()}")
    except (ValueError, subprocess.TimeoutExpired) as e:
        print(f"  適応度関数実行失敗: {type(e).__name__}")

    return None


# ── pitfall 記録 ─────────────────────────────────────────────────────


def record_pitfall(
    target_path: str,
    source: str,
    pattern: str,
    score: Optional[float] = None,
) -> None:
    """失敗パターンを references/pitfalls.md に記録する。"""
    target = Path(target_path)
    refs_dir = target.parent / "references"
    refs_dir.mkdir(parents=True, exist_ok=True)
    pitfalls_file = refs_dir / "pitfalls.md"

    score_str = f"{score:.2f}" if score is not None else "-"
    new_row = f"| {source} | {pattern} | {score_str} |"

    existing_rows: List[str] = []
    if pitfalls_file.exists():
        lines = pitfalls_file.read_text(encoding="utf-8").strip().split("\n")
        for line in lines[2:]:
            if line.strip().startswith("|"):
                existing_rows.append(line.strip())

    for row in existing_rows:
        parts = [p.strip() for p in row.split("|")]
        if len(parts) >= 4 and parts[2] == pattern:
            return  # 重複

    existing_rows.append(new_row)

    if len(existing_rows) > PITFALLS_MAX_ROWS:
        existing_rows = existing_rows[-PITFALLS_MAX_ROWS:]

    output = PITFALLS_HEADER + "\n".join(existing_rows) + "\n"
    pitfalls_file.write_text(output, encoding="utf-8")


# ── population broadcast helper ──────────────────────────────────────


def generate_candidate(
    prompt: str,
    original_content: str,
    claude_cwd: Optional[str],
    max_lines: int,
    pitfall_path: Optional[str],
) -> Dict[str, Any]:
    """1候補を生成してゲート判定まで行う（PopulationBroadcastOptimizer から利用）。

    warn-only の pre_check を実行し、warnings を標準出力に出力する。
    regression_gate を通過した場合のみ passed=True を返す。
    """
    from regression_gate import pre_check  # type: ignore[import]

    content, error = call_llm(prompt, claude_cwd)
    if error or not content:
        return {"content": None, "passed": False, "error": error or "empty", "fitness": None}

    # frontmatter が消えていれば元のものを自動補完
    content = restore_frontmatter_if_lost(content, original_content)

    # pre_check (warn-only): passed は常に True
    pc = pre_check(content, original_content)
    for w in pc.warnings:
        print(f"[pre_check warn] {w}")

    passed, gate_reason = run_regression_gate(
        content, original_content, max_lines, pitfall_path
    )
    return {
        "content": content,
        "passed": passed,
        "gate_reason": gate_reason,
        "fitness": None,
    }
