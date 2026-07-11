"""skill_declaration_reachability（SKILL.md 宣言↔実装 到達可能性）のテスト（#191, #170再発防止）。

決定論・LLM 非依存。tmp_path に疑似リポジトリツリー（skills/*/SKILL.md + scripts/lib/*.py）
を作って静的突合する。実コーパス較正で確定した仕様:

- 抽出はバッククォート inline code span（`` `func(args)` ``）のみを対象にする。
  裸の prose（バッククォート無し）は疑似コード/例示が多く抽出しない（実 SKILL.md 全件較正で
  `gstack(340) / evolve-anything(30)` のような非呼び出し記述がバッククォート内に混在することを
  確認したが、いずれも「自コードベースに定義が無い」フィルタで自然に除外される）。
- コードブロック内の宣言は Layer 3（dogfood/skill_blocks.py）の管轄なので抽出対象外。
- 到達可能性判定は「定義モジュール自身」と `tests/` 配下のみが caller なら到達不能。
  caller は (a) scripts/**.py + skills/**/scripts/**.py の AST 参照（Name/Attribute、
  `import ... as` エイリアス越しの参照も解決）、(b) skills/*/SKILL.md の fenced code block
  （python/bash）内のテキスト一致、の両方を評価する。(b) が無いと「SKILL.md の code block から
  実際に呼ばれている」関数（例: agent-brushup の `check_quality()`）まで誤検出する
  （実コーパス較正で確認・#191）。
- 複数モジュールに同名定義がある candidate は誤帰属回避のため判定対象から除外する（ambiguous）。
- 自コードベースに定義が無い candidate（Python 標準関数・外部ライブラリ・CLI コマンド形）は
  対象外（unresolved）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import skill_declaration_reachability as sdr  # noqa: E402


# --- extract_declared_calls ------------------------------------------------


def test_extract_single_inline_call(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text(
        "## Step 1\n"
        "各エージェントに `check_quality()` を実行する。\n",
        encoding="utf-8",
    )
    calls = sdr.extract_declared_calls(md)
    assert [c.name for c in calls] == ["check_quality"]
    assert calls[0].line == 2


def test_extract_resolves_dotted_name_to_bare_last_segment(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text("`bootstrap_backlog.mark_done(slug, dry_run=dry_run)` で marker を立てる。\n", encoding="utf-8")
    calls = sdr.extract_declared_calls(md)
    assert [c.name for c in calls] == ["mark_done"]


def test_extract_multiple_calls_same_line(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text(
        "`mark_done(dry_run=...)` / `record_reviewed(dry_run=...)` の設計を転記したもの。\n",
        encoding="utf-8",
    )
    calls = sdr.extract_declared_calls(md)
    assert sorted(c.name for c in calls) == ["mark_done", "record_reviewed"]


def test_extract_ignores_code_block_content(tmp_path: Path) -> None:
    """コードブロック内の呼び出しは Layer 3 管轄なので抽出しない。"""
    md = tmp_path / "SKILL.md"
    md.write_text(
        "prose 前\n"
        "```python\n"
        "result = check_quality(a)\n"
        "```\n"
        "prose 後: `check_upstream()` を実行。\n",
        encoding="utf-8",
    )
    calls = sdr.extract_declared_calls(md)
    assert [c.name for c in calls] == ["check_upstream"]
    # コードブロック分の行もスキップされず、prose 側の行番号は元ファイルのまま。
    assert calls[0].line == 5


def test_extract_ignores_bare_mention_without_backticks(tmp_path: Path) -> None:
    """バッククォート無しの `func(args)` 風の記述は抽出しない（疑似コード/例示との区別）。"""
    md = tmp_path / "SKILL.md"
    md.write_text("この関数は func_without_backticks(args) のように呼ばれる（例示）。\n", encoding="utf-8")
    calls = sdr.extract_declared_calls(md)
    assert calls == []


def test_extract_ignores_cli_command_form(tmp_path: Path) -> None:
    """括弧を伴わない CLI コマンド形は抽出対象にならない。"""
    md = tmp_path / "SKILL.md"
    md.write_text("`bin/evolve-tier show` を実行して現状を確認する。\n", encoding="utf-8")
    calls = sdr.extract_declared_calls(md)
    assert calls == []


def test_extract_keeps_placeholder_args(tmp_path: Path) -> None:
    md = tmp_path / "SKILL.md"
    md.write_text("`add_artifact_suppression(<artifact_id>)` を呼ぶ。\n", encoding="utf-8")
    calls = sdr.extract_declared_calls(md)
    assert [c.name for c in calls] == ["add_artifact_suppression"]


# --- helpers to build a fixture repo ---------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


# --- build_call_graph_index / detect_unreachable_declarations --------------


def test_iter_py_files_not_fooled_by_dotclaude_in_repo_root_path(tmp_path: Path) -> None:
    """repo_root 自身の絶対パスに `.claude` セグメントが含まれても除外されない（#191 実機発見）。

    impl-worker は `<repo>/.claude/worktrees/<id>` という worktree で作業するため、
    素朴に `f.parts`（絶対パス全体）で `.claude` 除外すると全ファイルが誤除外され、
    worktree 内で実行するたびに evaluated_count=0 の偽陰性になっていた。
    """
    fake_worktree_root = tmp_path / ".claude" / "worktrees" / "agent-xyz"
    _write(fake_worktree_root / "scripts/lib/foo.py", "def func_a():\n    return 1\n")
    files = sdr._iter_py_files(fake_worktree_root)
    assert any(f.name == "foo.py" for f in files)


def test_reachable_when_called_from_other_module(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/foo.py", "def func_a():\n    return 1\n")
    _write(root / "scripts/lib/bar.py", "from foo import func_a\n\ndef use():\n    return func_a()\n")
    _write(
        root / "skills/demo/SKILL.md",
        "`func_a()` を呼んで結果を使う。\n",
    )
    report = sdr.detect_unreachable_declarations(root)
    assert report.has_skills is True
    assert [u.name for u in report.unreachable] == []
    assert report.evaluated_count == 1


def test_unreachable_when_only_defined_and_tested(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/foo.py", "def zombie_func():\n    return 1\n")
    _write(
        root / "scripts/lib/tests/test_foo.py",
        "from foo import zombie_func\n\ndef test_it():\n    assert zombie_func() == 1\n",
    )
    _write(root / "skills/demo/SKILL.md", "`zombie_func()` を実行し結果を検査する。\n")
    report = sdr.detect_unreachable_declarations(root)
    assert [u.name for u in report.unreachable] == ["zombie_func"]
    assert report.unreachable[0].source == "skills/demo/SKILL.md"
    assert report.unreachable[0].def_files == ("scripts/lib/foo.py",)


def test_reachable_via_skill_md_code_block(tmp_path: Path) -> None:
    """agent-brushup の check_quality 型: 定義モジュール外の .py caller は無いが
    自分の SKILL.md の code block から実際に呼ばれている場合は到達可能とみなす（#191 較正）。"""
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/agent_quality.py", "def check_quality(agent):\n    return {}\n")
    _write(
        root / "skills/agent-brushup/SKILL.md",
        "```python\n"
        "from agent_quality import check_quality\n"
        "result = check_quality(a)\n"
        "```\n"
        "各エージェントに `check_quality()` を実行する。\n",
    )
    report = sdr.detect_unreachable_declarations(root)
    assert report.unreachable == []


def test_call_only_from_tests_dir_stays_unreachable(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/foo.py", "def zombie_func():\n    return 1\n")
    _write(
        root / "skills/demo/scripts/tests/test_zombie.py",
        "from foo import zombie_func\n\ndef test_it():\n    zombie_func()\n",
    )
    _write(root / "skills/demo/SKILL.md", "`zombie_func()` を実行する。\n")
    report = sdr.detect_unreachable_declarations(root)
    assert [u.name for u in report.unreachable] == ["zombie_func"]


def test_aliased_import_call_counts_as_reachable(tmp_path: Path) -> None:
    """`from mod import func as _f` の後 `_f(...)` で呼ぶパターン（#191 較正: reconcile_surfaced 型）。"""
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/suppression_ledger.py", "def reconcile_surfaced(tracked, persist=False):\n    return {}\n")
    _write(
        root / "scripts/lib/cli.py",
        "from suppression_ledger import reconcile_surfaced as _reconcile\n\n"
        "def run():\n    return _reconcile({}, persist=True)\n",
    )
    _write(root / "skills/demo/SKILL.md", "`reconcile_surfaced(persist=True)` で自動却下する。\n")
    report = sdr.detect_unreachable_declarations(root)
    assert report.unreachable == []


def test_bare_reexport_import_without_call_does_not_count(tmp_path: Path) -> None:
    """`from mod import func  # re-export` だけ（呼び出しなし）は到達可能の根拠にしない
    （#191 較正: check_upstream 型 — 再輸出だけでは zombie を隠せない）。"""
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/agent_quality_upstream.py", "def check_upstream():\n    return {}\n")
    _write(
        root / "scripts/lib/agent_quality.py",
        "from agent_quality_upstream import check_upstream  # noqa: F401 -- re-export\n",
    )
    _write(root / "skills/demo/SKILL.md", "`check_upstream()` で更新チェックする。\n")
    report = sdr.detect_unreachable_declarations(root)
    assert [u.name for u in report.unreachable] == ["check_upstream"]


def test_ambiguous_definition_is_excluded_from_unreachable(tmp_path: Path) -> None:
    """同名関数が複数モジュールに定義されている candidate は誤帰属回避のため skip する。"""
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/a.py", "def run():\n    return 1\n")
    _write(root / "scripts/lib/b.py", "def run():\n    return 2\n")
    _write(root / "skills/demo/SKILL.md", "`run()` を実行する。\n")
    report = sdr.detect_unreachable_declarations(root)
    assert report.unreachable == []
    assert report.ambiguous_count == 1
    assert report.evaluated_count == 0


def test_unresolved_definition_is_excluded_from_unreachable(tmp_path: Path) -> None:
    """自コードベースに定義が無い candidate（stdlib/外部ライブラリ/CLI 相当）は skip する。"""
    root = _make_repo(tmp_path)
    _write(root / "skills/demo/SKILL.md", "`Path(__file__)` をベースに解決する。\n")
    report = sdr.detect_unreachable_declarations(root)
    assert report.unreachable == []
    assert report.unresolved_count == 1
    assert report.evaluated_count == 0


def test_definition_only_in_test_file_is_not_recognized(tmp_path: Path) -> None:
    """定義が tests/ 配下にしか無い candidate は「自コードベースの実関数」と認めない。"""
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/tests/test_helpers.py", "def helper_only_in_tests():\n    return 1\n")
    _write(root / "skills/demo/SKILL.md", "`helper_only_in_tests()` を呼ぶ。\n")
    report = sdr.detect_unreachable_declarations(root)
    assert report.unreachable == []
    assert report.unresolved_count == 1


def test_duplicate_declaration_in_same_skill_dedups_to_one_entry(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/foo.py", "def zombie_func():\n    return 1\n")
    _write(
        root / "skills/demo/SKILL.md",
        "1回目: `zombie_func()` を実行する。\n"
        "2回目: `zombie_func()` の結果を検査する。\n",
    )
    report = sdr.detect_unreachable_declarations(root)
    assert len(report.unreachable) == 1


def test_no_skill_md_returns_non_applicable_report(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/foo.py", "def func_a():\n    return 1\n")
    report = sdr.detect_unreachable_declarations(root)
    assert report.has_skills is False
    assert report.unreachable == []


def test_line_number_preserved_after_preceding_code_block(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/foo.py", "def zombie_func():\n    return 1\n")
    _write(
        root / "skills/demo/SKILL.md",
        "```python\n"
        "x = 1\n"
        "y = 2\n"
        "```\n"
        "`zombie_func()` を実行する。\n",
    )
    report = sdr.detect_unreachable_declarations(root)
    assert report.unreachable[0].line == 5
