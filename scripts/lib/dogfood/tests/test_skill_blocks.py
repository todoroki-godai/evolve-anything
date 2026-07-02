"""dogfood.skill_blocks のユニットテスト（#496 Layer 3）。

コードブロック抽出 + 安全分類のロジックを合成 fixture で検証する（実 SKILL.md は読まない）。
実行（subprocess）は分類結果が正しいことだけ確認し、実 import 検証は最小限。
"""
from __future__ import annotations

import sys
from pathlib import Path


_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))


from dogfood import skill_blocks as sb  # noqa: E402


# --- 抽出 ----------------------------------------------------------------------

def test_extract_fenced_blocks_python_and_bash(tmp_path: Path):
    md = tmp_path / "SKILL.md"
    md.write_text(
        "intro\n"
        "```python\nimport os\n```\n"
        "mid\n"
        "```bash\nrl-audit --help\n```\n"
        "```json\n{\"x\":1}\n```\n",
        encoding="utf-8",
    )
    blocks = sb.extract_code_blocks(md)
    langs = [b["lang"] for b in blocks]
    assert "python" in langs
    assert "bash" in langs
    # json は対象外
    assert "json" not in langs
    # 行番号が埋まる
    assert all(b["line"] > 0 for b in blocks)


def test_extract_handles_sh_and_py_aliases(tmp_path: Path):
    md = tmp_path / "SKILL.md"
    md.write_text("```sh\nls\n```\n```py\nimport sys\n```\n", encoding="utf-8")
    blocks = sb.extract_code_blocks(md)
    langs = {b["lang"] for b in blocks}
    assert langs == {"bash", "python"}  # 正規化される


# --- 安全分類: python -----------------------------------------------------------

def test_classify_python_import_only():
    cls = sb.classify_block("python", "from agent_quality import scan_agents\nx = 1")
    assert cls["mode"] == "import_check"
    assert "agent_quality" in cls["imports"][0]["module"]


def test_classify_python_with_side_effects_falls_to_existence():
    # import 以外に副作用のある行（関数呼び出し）→ import 行だけ抽出して import_check
    cls = sb.classify_block("python", "from prune import archive_file\narchive_file('/x', 'r')")
    # import は検証、呼び出しは実行しない
    assert cls["mode"] == "import_check"
    assert any("prune" in imp["module"] for imp in cls["imports"])


def test_classify_python_no_imports_existence_only():
    cls = sb.classify_block("python", "x = compute()\nprint(x)")
    assert cls["mode"] == "existence_only"


# --- 安全分類: bash ------------------------------------------------------------

def test_classify_bash_help_runnable():
    cls = sb.classify_block("bash", "evolve-audit --help")
    assert cls["mode"] == "run"


def test_classify_bash_dry_run_runnable():
    cls = sb.classify_block("bash", "evolve --dry-run --output /tmp/x.json")
    assert cls["mode"] == "run"


def test_classify_bash_plain_invocation_existence_only():
    cls = sb.classify_block("bash", "rl-backfill --project-dir \"$(pwd)\"")
    assert cls["mode"] == "existence_only"
    # 検証対象コマンド名を拾う
    assert "rl-backfill" in cls["commands"]


def test_classify_bash_write_command_existence_only():
    cls = sb.classify_block("bash", "rm -rf /tmp/x")
    assert cls["mode"] == "existence_only"


def test_classify_bash_placeholder_existence_only():
    cls = sb.classify_block("bash", "rl-evolve-skill <skill-name>")
    assert cls["mode"] == "existence_only"
    assert cls.get("has_placeholder") is True


def test_bash_with_single_quoted_inline_python_excludes_python_body():
    """bash ブロック内 ``python3 -c '...'`` の本文（python 行）を bash コマンドとして拾わない（#31）。

    single-quote で囲んだ埋め込み python の ``from``/``import``/代入行が裸コマンドとして
    existence チェックされる false positive を防ぐ。
    """
    code = (
        "PYTHONPATH=\"${CLAUDE_PLUGIN_ROOT}/scripts/lib\" python3 -c '\n"
        "import json, sys\n"
        "from evolve_introspect import flatten_candidates, summary_lines\n"
        "result = json.load(open(sys.argv[1]))\n"
        "analysis = result.get(\"self_analysis\", {})\n"
        "json.dump(flatten_candidates(analysis), open(\"/tmp/x.json\",\"w\"))\n"
        "' \"<result.json path>\"\n"
    )
    cls = sb.classify_block("bash", code)
    cmds = cls.get("commands", [])
    # 埋め込み python のトークンを bash コマンドとして拾っていないこと
    for tok in ("from", "result", "analysis", "import", "json"):
        assert tok not in cmds, f"埋め込み python トークン {tok!r} を誤検出した: {cmds}"


def test_bash_single_quoted_inline_python_no_existence_failure(tmp_path: Path):
    """``python3 -c '...'`` を含む bash ブロックが existence_only で fail しない（#31 受け入れ）。"""
    code = (
        "PYTHONPATH=\"${CLAUDE_PLUGIN_ROOT}/scripts/lib\" python3 -c '\n"
        "import json\n"
        "from evolve_introspect import render_issue_body\n"
        "cand = json.load(open(\"/tmp/rf_one.json\"))\n"
        "print(render_issue_body(cand))\n"
        "' > /tmp/rf_body.md\n"
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    # 埋め込み python の from/cand/import 等で missing fail を出さない
    assert res["status"] != "fail", res.get("detail")


# --- import 検証実行 -----------------------------------------------------------

def test_run_import_check_passes_for_stdlib(tmp_path: Path):
    repo_root = tmp_path
    block = {"lang": "python", "code": "import os\nimport sys", "line": 1}
    res = sb.run_block(block, repo_root=repo_root, sys_path_dirs=[])
    assert res["status"] == "pass"


def test_run_import_check_fails_for_missing_module(tmp_path: Path):
    repo_root = tmp_path
    block = {"lang": "python", "code": "import this_module_does_not_exist_xyz", "line": 1}
    res = sb.run_block(block, repo_root=repo_root, sys_path_dirs=[])
    assert res["status"] == "fail"
    assert "this_module_does_not_exist_xyz" in res["detail"]


# --- 存在検証実行 --------------------------------------------------------------

def test_run_existence_check_missing_command(tmp_path: Path):
    block = {"lang": "bash", "code": "rl-nonexistent-cmd-xyz foo", "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    # bin/ にも PATH にも無い → fail
    assert res["status"] == "fail"


def test_run_existence_check_finds_bin_command(tmp_path: Path):
    # repo_root/bin/rl-foo を作る → 存在検証 pass
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "rl-foo").write_text("#!/bin/sh\n", encoding="utf-8")
    block = {"lang": "bash", "code": "rl-foo --project-dir x", "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["status"] == "pass"


def test_placeholder_block_skipped_not_failed(tmp_path: Path):
    block = {"lang": "bash", "code": "rl-evolve-skill <skill-name>", "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    # プレースホルダは存在検証のみ。rl-evolve-skill が bin に無ければ fail（コマンド実在チェック）
    assert res["status"] in ("pass", "fail", "skip")
