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

    埋め込み python の import 見逃しを直した後は、この埋め込み python の import 自体も
    検証対象になる（``PYTHONPATH=`` 前置を setup として解決する）。既存 fixture には
    実モジュールが無かったため、ここで最小スタブを用意して実 import まで緑になることを
    確認する。
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
    # 埋め込み python の from/cand/import 等で missing fail を出さない。修正後は
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


def test_run_import_check_preserves_original_order_import_then_use(tmp_path: Path):
    """陽性対照（telemetry.md 型の回帰）: ``import X`` の後で ``X.attr`` を使う代入行があっても
    NameError にならない。setup 行と import 文を「先に setup・後に import」の固定順で
    連結すると、この代入行が setup と誤認識されて import より前に置かれ NameError になる
    （skills/implement/references/telemetry.md で実測した偽陽性）。元の出現順を保つことを確認する。
    """
    code = (
        "import datetime, os, pathlib, sys\n"
        "plugin_root = pathlib.Path(os.environ.get('CLAUDE_PLUGIN_ROOT', '.'))\n"
        "sys.path.insert(0, str(plugin_root / 'scripts' / 'lib'))\n"
    )
    block = {"lang": "python", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["status"] == "pass", res.get("detail")


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


# --- 埋め込み python import（bash 内 python3 -c の import 見逃し回帰） --------------------


def _merge_suppression_block(with_syspath: bool) -> str:
    """``skills/evolve/references/prune-merge.md`` の却下手順ブロックを再現する。"""
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
    """placeholder が import 行と無関係な引数にだけあっても import_check になる。

    修正前は bash ブロック全体の ``_has_placeholder`` 判定で existence_only に落ち、
    ``discover`` の import 自体が一度も検証されなかった。
    """
    code = _merge_suppression_block(with_syspath=True)
    cls = sb.classify_block("bash", code)
    assert cls["mode"] == "import_check"
    assert any(imp["module"] == "discover" for imp in cls["imports"])


def test_run_block_missing_syspath_setup_fails(tmp_path: Path):
    """陰性試験①: sys.path 設定の欠落（見逃していた実例そのもの）→ 赤。"""
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


def test_run_block_missing_function_name_is_caught(tmp_path: Path):
    """陰性試験③: 単一行で完結する ``from X import Y`` の Y typo → 赤。

    単一行で閉じている ``from X import Y`` は verbatim 実行するため（複数行 bracket
    import の開き行だけは従来どおり ``import X`` に正規化し、#496 の SyntaxError 回避を
    維持する）、モジュールは実在していても ``Y`` が実在しない typo は
    ``ImportError: cannot import name ...`` で検出できる。
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
    assert res["mode"] == "import_check"
    assert res["status"] == "fail", res.get("detail")
    assert "no_such_function_xyz" in res["detail"]


def test_classify_bracketed_multiline_import_is_verbatim_via_ast():
    """陽性対照: 複数行 bracket import も ast 経由で verbatim 再構成され検証できる。

    AST から ``ast.unparse`` で再構成するため（テキストの部分切り出しではない）、複数行
    bracket import でも常に構文的に正しい単独文になる。#496 が懸念していた「テキストを
    そのまま実行すると SyntaxError になる」問題は、AST 経由の再構成では発生しない。
    """
    code = "from agent_quality import (\n    scan_agents,\n    score_agent,\n)\n"
    cls = sb.classify_block("python", code)
    assert cls["mode"] == "import_check"
    assert cls["imports"][0]["module"] == "agent_quality"
    assert "scan_agents" in cls["imports"][0]["stmt"]
    assert "score_agent" in cls["imports"][0]["stmt"]


def test_run_block_heredoc_style_missing_syspath_fails(tmp_path: Path):
    """陰性試験④: heredoc（``python3 - <<'EOF'``）形の埋め込み python も import_check に乗る。

    heredoc 本文を isolate 経路に追加した。sys.path 設定が無いままだと import に失敗し赤になる
    （引用符ベースの isolate とは別の検出経路として heredoc 開始行の delimiter を追跡する）。
    """
    code = (
        "python3 - <<'EOF'\n"
        "from discover import add_merge_suppression\n"
        "add_merge_suppression('<primary_skill_name>', '<secondary_skill_name>')\n"
        "EOF\n"
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "fail", res.get("detail")


def test_run_block_heredoc_style_with_syspath_passes(tmp_path: Path):
    """陽性対照: heredoc 形でも sys.path.insert があり実モジュールがあれば緑になる。"""
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "discover.py").write_text(
        "def add_merge_suppression(a, b):\n    pass\n", encoding="utf-8"
    )
    code = (
        "python3 - <<'EOF'\n"
        "import os, sys\n"
        "_root = os.environ.get('CLAUDE_PLUGIN_ROOT') or os.getcwd()\n"
        "sys.path.insert(0, os.path.join(_root, 'scripts', 'lib'))\n"
        "from discover import add_merge_suppression\n"
        "add_merge_suppression('<primary_skill_name>', '<secondary_skill_name>')\n"
        "EOF\n"
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "pass", res.get("detail")


def test_run_block_semicolon_joined_line_missing_syspath_fails(tmp_path: Path):
    """自分で考えた回避手段①: import と setup を同一行にセミコロン連結（一行完結の
    ``python3 -c "..."``、実際に evolve/SKILL.md 等で使われている書き方）も、中身を ``;``
    区切りで疑似行化して同じ経路に通す。sys.path が実際に無効な宛先なら赤になる。
    """
    code = (
        "SLUG=\"$(python3 -c \"import os, sys; sys.path.insert(0,'/nonexistent/x'); "
        "from this_module_does_not_exist_xyz import foo; print(foo())\" "
        "2>/dev/null || echo unknown)\"\n"
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "fail", res.get("detail")


def test_run_block_semicolon_joined_line_with_real_syspath_passes(tmp_path: Path):
    """陽性対照: セミコロン連結の一行完結形でも、実際に解決できる sys.path なら緑になる
    （evolve/SKILL.md 等の実パターンを壊していないことの確認）。"""
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "optimize_history_store.py").write_text(
        "def resolve_slug(cwd):\n    return 'x'\n", encoding="utf-8"
    )
    libdir = str(tmp_path / "scripts" / "lib").replace("\\", "\\\\").replace("'", "\\'")
    code = (
        "SLUG=\"$(PJ=\"$PJ\" python3 -c \"import os, sys; sys.path.insert(0,'" + libdir + "'); "
        "from optimize_history_store import resolve_slug; "
        "print(resolve_slug(cwd=os.environ['PJ']))\" 2>/dev/null || echo unknown)\"\n"
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "pass", res.get("detail")


def test_classify_bash_submodule_typo_is_caught():
    """自分で考えた回避手段②とは別の陽性: サブモジュール名の誤りは import_check で検出できる。

    ``from discover.typo_submodule import x`` は ``import discover.typo_submodule`` に
    正規化され、属性 typo と異なりモジュール解決自体が失敗するため import_check で拾える。
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


# --- codex レビュー M1-M3（ast/shlex ベースの呼び出し単位分離） -----------------------


def test_M1_import_on_opening_line_missing_syspath_fails(tmp_path: Path):
    """陰性試験 M1: import が ``python3 -c "..."`` の開始行そのものにある複数行本文。

    旧実装は引用符が「開いたまま」の行だけを isolate していたため、引用符が開いた
    その行自体（import を含む開始行）を取りこぼしていた。shlex で ``-c`` の引数全体を
    正しく1トークンとして取り出すことでこれを直接解決する。
    """
    code = 'python3 -c "from discover import add_merge_suppression\nprint(1)\n"'
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "fail", res.get("detail")


def test_M1_import_on_opening_line_with_syspath_passes(tmp_path: Path):
    """陽性対照 M1: 開始行に import があっても、sys.path が正しく解決できれば緑になる。"""
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "discover.py").write_text(
        "def add_merge_suppression(a, b):\n    pass\n", encoding="utf-8"
    )
    libdir = str(tmp_path / "scripts" / "lib")
    code = (
        f'python3 -c "import sys; sys.path.insert(0, \'{libdir}\')\n'
        "from discover import add_merge_suppression\n"
        "print(1)\n\""
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "pass", res.get("detail")


def test_M2_second_invocation_without_setup_is_not_hidden_by_first(tmp_path: Path):
    """陰性試験 M2: 同一ブロック内の2つ目の ``python3 -c`` 呼び出しが setup 無しで失敗する。

    旧実装は同一ブロック内の複数呼び出しを1本の python ソースへ連結して1回だけ実行して
    いたため、1つ目の sys.path 設定が2つ目にも「漏れて」効いてしまい、2つ目単独なら
    失敗するはずの import が隠れて pass になっていた。
    """
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "discover.py").write_text(
        "def add_merge_suppression(a, b):\n    pass\n", encoding="utf-8"
    )
    libdir = str(tmp_path / "scripts" / "lib")
    code = (
        f'python3 -c "\nimport sys\nsys.path.insert(0, \'{libdir}\')\n'
        'from discover import add_merge_suppression\n"\n'
        'python3 -c "\nfrom discover import add_merge_suppression\n"\n'
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "fail", res.get("detail")
    assert "import#1" in res["detail"]  # 2 つ目の呼び出しが失敗したと分かる


def test_M2_both_invocations_with_setup_pass(tmp_path: Path):
    """陽性対照 M2: 2つ目の呼び出しにも正しい setup があれば、両方とも緑になる。"""
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "discover.py").write_text(
        "def add_merge_suppression(a, b):\n    pass\n", encoding="utf-8"
    )
    libdir = str(tmp_path / "scripts" / "lib")
    inv = (
        f'python3 -c "\nimport sys\nsys.path.insert(0, \'{libdir}\')\n'
        'from discover import add_merge_suppression\n"\n'
    )
    code = inv + inv
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "pass", res.get("detail")


def test_M3_missing_command_fails_even_with_valid_import(tmp_path: Path):
    """陰性試験 M3: 存在しないコマンド行 + 有効な import が同居する場合、全体が fail になる。

    旧実装は import_check への昇格が見つかった時点で早期 return し、コマンド存在検証
    （``_run_existence_check``）を一度も呼んでいなかった。
    """
    code = "missing_command_review_123\npython3 -c \"import os\"\n"
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "fail", res.get("detail")
    assert "missing_command_review_123" in res["detail"]


def test_M3_existing_command_with_valid_import_passes(tmp_path: Path):
    """陽性対照 M3: コマンドが実在し import も有効なら、両方の検証を経て緑になる。"""
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "rl-foo").write_text("#!/bin/sh\n", encoding="utf-8")
    code = "rl-foo --project-dir x\npython3 -c \"import os\"\n"
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "pass", res.get("detail")


def test_dash_c_arg_not_glued_with_adjacent_closing_paren(tmp_path: Path):
    """回帰: ``$(python3 -c "..." )`` のように閉じ引用符の直後に空白無しで ``)`` が
    続く実パターン（skills/evolve/references/self-analysis.md 等）で、shlex の
    ``whitespace_split`` により ``)`` が引数へ混入し ``SyntaxError`` になっていたバグの
    回帰確認（自分で見つけた副作用。M1-M3 とは別種のバグ）。
    """
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "discover.py").write_text(
        "def add_merge_suppression(a, b):\n    pass\n", encoding="utf-8"
    )
    libdir = str(tmp_path / "scripts" / "lib")
    code = (
        "BODY=$(python3 -c \"\nimport sys\nsys.path.insert(0, '" + libdir + "')\n"
        "from discover import add_merge_suppression\n\")\n"
        "gh issue create --body \"$BODY\"\n"
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "pass", res.get("detail")


def test_self_devised_A_per_invocation_pythonpath_prefix_not_mixed(tmp_path: Path):
    """自分で考えた回避手段A（M1-M3 とは別種）: 2つの呼び出しがそれぞれ別々の
    ``PYTHONPATH=`` 前置を持つとき、1つ目の PYTHONPATH が2つ目にも誤って適用され
    ないことを確認する（M2 は setup 文をブロック本文に書く形だったのに対し、これは
    bash レベルの環境変数前置が呼び出しごとに正しく分離されるかを検証する）。
    """
    (tmp_path / "scripts" / "lib_a").mkdir(parents=True)
    (tmp_path / "scripts" / "lib_a" / "mod_a.py").write_text("x = 1\n", encoding="utf-8")
    # lib_b は意図的に作らない（2つ目の PYTHONPATH は解決できない前提）。
    libdir_a = str(tmp_path / "scripts" / "lib_a")
    libdir_b = str(tmp_path / "scripts" / "lib_b_does_not_exist")
    code = (
        f'PYTHONPATH="{libdir_a}" python3 -c "import mod_a"\n'
        f'PYTHONPATH="{libdir_b}" python3 -c "import mod_a"\n'
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    # 1つ目は mod_a を解決できるが、2つ目は解決できない PYTHONPATH のため全体は fail。
    assert res["status"] == "fail", res.get("detail")
    assert "import#1" in res["detail"]


def test_self_devised_B_heredoc_and_dash_c_mixed_in_same_block(tmp_path: Path):
    """自分で考えた回避手段B（M1-M3 とは別種）: heredoc と ``-c`` 呼び出しが同一ブロック内に
    混在するとき、heredoc 側の setup 欠落が ``-c`` 側の成功に隠されずに fail することを確認する。
    """
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    (tmp_path / "scripts" / "lib" / "discover.py").write_text(
        "def add_merge_suppression(a, b):\n    pass\n", encoding="utf-8"
    )
    libdir = str(tmp_path / "scripts" / "lib")
    code = (
        f'python3 -c "\nimport sys\nsys.path.insert(0, \'{libdir}\')\n'
        'from discover import add_merge_suppression\n"\n'
        "python3 - <<'EOF'\n"
        "from discover import add_merge_suppression\n"  # heredoc 側は setup 無し
        "EOF\n"
    )
    block = {"lang": "bash", "code": code, "line": 1}
    res = sb.run_block(block, repo_root=tmp_path, sys_path_dirs=[])
    assert res["mode"] == "import_check"
    assert res["status"] == "fail", res.get("detail")
    assert "import#1" in res["detail"]
