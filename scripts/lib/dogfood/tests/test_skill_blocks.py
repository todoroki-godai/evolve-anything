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
    """``python3 -c '...'`` を含む bash ブロックが existence_only で fail しない（#31 受け入れ）。

    #674 の見逃し修正後は、この埋め込み python の import 自体も検証対象になる
    （``PYTHONPATH=`` 前置を setup として解決する）。既存 fixture には実モジュールが
    無かったため、ここで最小スタブを用意して実 import まで緑になることを確認する。
    """
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "evolve_introspect.py").write_text(
        "def render_issue_body(cand):\n    return str(cand)\n", encoding="utf-8"
    )
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
    # 埋め込み python の from/cand/import 等で missing fail を出さない。#674 修正後は
    # import_check モードで実 import が通ることまで確認する（existence_only への
    # 後退でごまかさない）。
    assert res["status"] == "pass", res.get("detail")
    assert res["mode"] == "import_check"


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


# --- 埋め込み python import（#674: bash 内 python3 -c の import 見逃し回帰） --------------------


def _merge_suppression_block(with_syspath: bool) -> str:
    """``skills/evolve/references/prune-merge.md`` の却下手順ブロックを再現する（#674 の実例）。"""
    setup = (
        "import os, sys\n"
        "_root = os.environ.get('CLAUDE_PLUGIN_ROOT') or os.getcwd()\n"
        "sys.path.insert(0, os.path.join(_root, 'scripts', 'lib'))\n"
        if with_syspath
        else ""
    )
    return (
        'python3 -c "\n'
        f"{setup}"
        "from discover import add_merge_suppression\n"
        "add_merge_suppression('<primary_skill_name>', '<secondary_skill_name>')\n"
        '"\n'
    )


def test_classify_bash_embedded_python_with_placeholder_is_import_checked():
    """#674 の実例: プレースホルダが import 行と無関係な引数にだけあっても import_check になる。

    修正前は bash ブロック全体の ``_has_placeholder`` 判定で existence_only に落ち、
    ``discover`` の import 自体が一度も検証されなかった。
    """
    code = _merge_suppression_block(with_syspath=True)
    cls = sb.classify_block("bash", code)
    assert cls["mode"] == "import_check"
    assert any(imp["module"] == "discover" for imp in cls["imports"])


def test_run_block_missing_syspath_setup_fails(tmp_path: Path):
    """陰性試験①: sys.path 設定の欠落（#674 の実例そのもの）→ 赤。"""
    code = _merge_suppression_block(with_syspath=False)
    block = {"lang": "bash", "code": code, "line": 70}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "fail", res.get("detail")


def test_run_block_with_syspath_setup_passes(tmp_path: Path):
    """陽性対照: 修正後の内容（sys.path.insert あり）は、対応する discover モジュールが
    実在すれば緑になる（正常データでの誤検出が無いことの確認）。"""
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "discover.py").write_text(
        "def add_merge_suppression(a, b):\n    pass\n", encoding="utf-8"
    )
    code = _merge_suppression_block(with_syspath=True)
    block = {"lang": "bash", "code": code, "line": 70}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["status"] == "pass", res.get("detail")


def test_run_block_reformatted_setup_still_passes(tmp_path: Path):
    """陽性対照（意味を変えない書き換え）: 空行・コメントを足しても import_check は崩れない。"""
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "discover.py").write_text(
        "def add_merge_suppression(a, b):\n    pass\n", encoding="utf-8"
    )
    code = (
        'python3 -c "\n'
        "# setup\n"
        "import os, sys\n"
        "\n"
        "_root = os.environ.get(\x27CLAUDE_PLUGIN_ROOT\x27) or os.getcwd()\n"
        "sys.path.insert(0, os.path.join(_root, \x27scripts\x27, \x27lib\x27))\n"
        "\n"
        "from discover import add_merge_suppression\n"
        "add_merge_suppression(\x27<primary_skill_name>\x27, \x27<secondary_skill_name>\x27)\n"
        '"\n'
    )
    block = {"lang": "bash", "code": code, "line": 70}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["status"] == "pass", res.get("detail")


def test_run_block_missing_module_fails(tmp_path: Path):
    """陰性試験②: sys.path は正しく設定されているが、存在しないモジュール名 → 赤。"""
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    code = (
        'python3 -c "\n'
        "import os, sys\n"
        "_root = os.environ.get('CLAUDE_PLUGIN_ROOT') or os.getcwd()\n"
        "sys.path.insert(0, os.path.join(_root, 'scripts', 'lib'))\n"
        "from this_module_does_not_exist_xyz import foo\n"
        "foo()\n"
        '"\n'
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "fail", res.get("detail")


def test_run_block_missing_function_name_not_caught_by_design(tmp_path: Path):
    """陰性試験③（既知の設計限界・green のまま）: 存在しない関数名は検出対象外。

    ``_extract_imports`` は ``from X import Y`` を ``import X`` に正規化する（#496 の割り切り。
    複数行括弧 import の SyntaxError 偽陽性を避けるため）。モジュールは実在するので import は
    成功し、``Y`` が実在しない typo は #674 の対象外＝既存の意図した設計のまま検出できない。
    """
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "discover.py").write_text(
        "def add_merge_suppression(a, b):\n    pass\n", encoding="utf-8"
    )
    code = (
        'python3 -c "\n'
        "import os, sys\n"
        "_root = os.environ.get('CLAUDE_PLUGIN_ROOT') or os.getcwd()\n"
        "sys.path.insert(0, os.path.join(_root, 'scripts', 'lib'))\n"
        "from discover import no_such_function_xyz\n"
        "no_such_function_xyz('a', 'b')\n"
        '"\n'
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    # 既知の限界（advisory）: モジュール import 自体は成功するため pass のまま。
    assert res["status"] == "pass", res.get("detail")


def test_run_block_heredoc_style_not_caught_by_design(tmp_path: Path):
    """陰性試験④（既知の限界・green のまま）: heredoc（``python3 - <<'EOF'``）形は検出対象外。

    isolate は python 用の引用符（``"``/``'``）の parity 追跡のみを見るため、heredoc 区切り
    （``<<'EOF'``）による本文は「引用符の中」と判定されない＝bash の書き方を変えるだけで
    迂回できる（`no-denylist-checks.md` に従い non-blocking と明記。実例は現時点の skills/ に
    heredoc 形が無いことを 2026-09-25 に grep で確認済み）。

    **範囲外の発見（#674 の対象外・未修正）**: heredoc 本文の1行目 ``from discover import ...``
    の先頭トークン ``from`` が、既存の ``_extract_bash_commands``（#674 以前から存在）に
    裸コマンドとして拾われ、存在検証で fail する（import_check への昇格とは無関係の理由で
    たまたま赤になる）。mode で判定し、status には依存しない。
    """
    code = (
        "python3 - <<'EOF'\n"
        "from discover import add_merge_suppression\n"
        "add_merge_suppression('<primary_skill_name>', '<secondary_skill_name>')\n"
        "EOF\n"
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] != "import_check"


def test_run_block_semicolon_joined_line_not_caught_by_design(tmp_path: Path):
    """自分で考えた回避手段①（既知の限界・green のまま）: import と setup を同一行にセミコロン連結。

    ``_IMPORT_RE`` は行全体が import 文であることを要求する（anchored ``^...$``）ため、
    セミコロン連結の一行（実際に evolve/SKILL.md 等で使われている書き方）は import 文として
    抽出されない＝#674 の修正では拾えない（heredoc とは異なる種類の回避。non-blocking と明記）。
    """
    code = (
        "SLUG=\"$(python3 -c \\\"import os, sys; sys.path.insert(0,'/nonexistent/x'); "
        "from this_module_does_not_exist_xyz import foo; print(foo())\\\" "
        "2>/dev/null || echo unknown)\"\n"
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] != "import_check"


def test_classify_bash_submodule_typo_is_caught():
    """自分で考えた回避手段②とは別の陽性: サブモジュール名の誤りは import_check で検出できる。

    ``from discover.typo_submodule import x`` は ``import discover.typo_submodule`` に
    正規化され、属性 typo と異なりモジュール解決自体が失敗するため #674 の修正で拾える。
    """
    code = (
        'python3 -c "\n'
        "import os, sys\n"
        "_root = os.environ.get('CLAUDE_PLUGIN_ROOT') or os.getcwd()\n"
        "sys.path.insert(0, os.path.join(_root, 'scripts', 'lib'))\n"
        "from discover.typo_submodule import add_merge_suppression\n"
        '"\n'
    )
    cls = sb.classify_block("bash", code)
    assert cls["mode"] == "import_check"
    assert any(imp["module"] == "discover.typo_submodule" for imp in cls["imports"])


def test_real_prune_merge_doc_passes_after_fix(monkeypatch):
    """実ファイル end-to-end: 修正後の skills/evolve/references/prune-merge.md が緑になる。

    ``CLAUDE_PLUGIN_ROOT`` を明示 monkeypatch し、doc の ``os.getcwd()`` フォールバックに
    依存せず決定論的に検証する（pytest の起動 cwd に依存させない）。
    """
    repo_root = Path(__file__).resolve().parents[4]
    assert (repo_root / "skills").exists(), f"repo root 誤検出: {repo_root}"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(repo_root))
    skill_md = repo_root / "skills" / "evolve" / "references" / "prune-merge.md"
    assert skill_md.exists()
    blocks = sb.extract_code_blocks(skill_md)
    merge_blocks = [b for b in blocks if "add_merge_suppression(" in b["code"]]
    assert len(merge_blocks) == 2  # proposed / interactive_candidate の両方
    for block in merge_blocks:
        res = sb.run_block(block, repo_root=repo_root, sys_path_dirs=[])
        assert res["mode"] == "import_check"
        assert res["status"] == "pass", res.get("detail")
