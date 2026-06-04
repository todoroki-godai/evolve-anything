"""claude -p 全廃の回帰ゲート（[ADR-037] Phase 1a）。

2026-06-15 の Agent SDK クレジット分離に対応するため、変換済みモジュールに
`subprocess` 経由の `claude` 呼び出し（claude -p）が再混入していないことを AST で検証する。
docstring/コメント中の "claude -p" 言及では誤検知しない（文字列リテラルの subprocess 引数のみ検査）。

新たに claude -p を分離した経路（1b 以降）は CONVERTED_MODULES に追記する。
まだ claude -p を残す経路は KNOWN_REMAINING に明示し、silent な取りこぼしを防ぐ。
"""

import ast
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[3]

# claude -p を完全分離したモジュール（subprocess による claude 呼び出しゼロ）
CONVERTED_MODULES = [
    # Phase 1a
    "scripts/lib/llm_broker.py",
    "scripts/lib/world_context.py",
    "scripts/quality_monitor.py",
    # Phase 1b（scoring 系: constitutional / principles）
    "scripts/rl/fitness/constitutional.py",
    "scripts/rl/fitness/principles.py",
    # Phase 1c（evolve 系: skill_evolve の judgment 採点 / テンプレカスタマイズ）
    "scripts/lib/skill_evolve/llm_scoring.py",
    "scripts/lib/skill_evolve/proposal.py",
    # Phase 1d-i（reflect 検出系: corrections の意味検証 / 指示違反判定）
    "scripts/lib/semantic_detector.py",
    "scripts/lib/critical_instruction_extractor.py",
]

# まだ claude -p を残す既知の経路（順次 CONVERTED へ移す。silent 取りこぼし防止）。
# 不変条件: claude -p を呼ぶ全モジュールは CONVERTED_MODULES か KNOWN_REMAINING の
# どちらかに必ず載る（台帳を網羅的に保つ）。
KNOWN_REMAINING = [
    "scripts/lib/score_noise.py",                   # _run_claude_prompt（bin/rl-prompt-compare 後方互換、DEPRECATED）
    "scripts/lib/remediation/fixers_rules.py",      # line_limit 修正の LLM 圧縮/分離（Phase 1d-ii で変換予定）
    "scripts/lib/remediation/fixers_quality.py",    # split 候補修正の LLM 生成（Phase 1d-ii で変換予定）
    "hooks/auto_memory_runner.py",                  # Stop hook の memory 生成（Phase 2 で evolve 吸収予定）
]

_SUBPROCESS_CALLERS = {"run", "Popen", "call", "check_output", "check_call"}


def _iter_string_literals(node: ast.AST):
    """式ツリー配下の全文字列リテラルを yield する（list/tuple/引数を再帰）。"""
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            yield child.value


def find_claude_subprocess_calls(source: str) -> list:
    """subprocess.run/Popen 等の呼び出しで引数に 'claude' を含む箇所の行番号を返す。"""
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # subprocess.run(...) / sp.Popen(...) のような属性呼び出しを対象にする
        if not (isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_CALLERS):
            continue
        literals = []
        for arg in node.args:
            literals.extend(_iter_string_literals(arg))
        for kw in node.keywords:
            if kw.value is not None:
                literals.extend(_iter_string_literals(kw.value))
        if any("claude" in lit for lit in literals):
            hits.append(node.lineno)
    return hits


@pytest.mark.parametrize("rel_path", CONVERTED_MODULES)
def test_converted_modules_have_no_claude_p(rel_path):
    """変換済みモジュールは subprocess 経由の claude 呼び出しを持たない（claude -p 全廃）。"""
    path = _PLUGIN_ROOT / rel_path
    assert path.exists(), f"{rel_path} が存在しない"
    hits = find_claude_subprocess_calls(path.read_text(encoding="utf-8"))
    assert hits == [], f"{rel_path} に claude -p 呼び出しが残存（行 {hits}）。[ADR-037] 全廃対象"


def test_detector_flags_known_claude_p_call():
    """検出器の正の確認: 実際の claude subprocess 呼び出しを検出できる。"""
    src = 'import subprocess\nsubprocess.run(["claude", "-p", "--output-format", "text"])\n'
    assert find_claude_subprocess_calls(src) == [2]


def test_detector_ignores_docstring_mentions():
    """検出器の負の確認: docstring/コメント中の "claude -p" は検出しない。"""
    src = '"""claude -p を全廃した。"""\nx = "claude -p は呼ばない"\n'
    assert find_claude_subprocess_calls(src) == []


def test_known_remaining_paths_still_exist():
    """KNOWN_REMAINING の経路が存在することを確認（リネーム時に台帳を腐らせない）。"""
    for rel_path in KNOWN_REMAINING:
        assert (_PLUGIN_ROOT / rel_path).exists(), f"{rel_path} が見つからない（台帳更新漏れ）"
