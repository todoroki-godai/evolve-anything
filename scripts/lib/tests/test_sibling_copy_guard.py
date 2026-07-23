#!/usr/bin/env python3
"""sibling_copy_guard（diff-scoped 兄弟コピー検出、#210）のテスト。

決定論・LLM 非依存。git を使うのは1本の実 git E2E テストのみ（LLM を一切呼ばないため
no-llm-in-tests の対象外）、残りは合成 diff テキストに対する純関数テスト。

検証対象:
  - ``normalize_line``: 空白正規化（前後 trim + 内部連続空白の圧縮）
  - ``is_trivial_line``: trivial 行除外床（空行/pass/return/return None/continue/break/
    else:/try:/コメントのみ/最小トークン数未満）
  - ``parse_diff_removed_lines``: unified diff（``git diff`` 出力）から削除行（＝変更前の
    内容。検出の起点はこちら側）の (file, line_no, text) を抽出（新規ファイル/削除ファイル
    は対象外・複数 hunk・追加行での line_no 加算・「\\ No newline at end of file」マーカーの
    無視 を含む）
  - ``detect_sibling_copies``: #40 型の再現（同一の弱いロジック片が複数箇所にコピーされ
    片側だけ変更された場合の検出）・trivial 行の非検出・変更行そのもの/変更ファイル自身の
    除外・実 git diff からの E2E
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import sibling_copy_guard as scg  # noqa: E402


# --- normalize_line ----------------------------------------------------------


def test_normalize_line_trims_leading_and_trailing_whitespace() -> None:
    assert scg.normalize_line("    idx = text.find(x, 3)   ") == "idx = text.find(x, 3)"


def test_normalize_line_collapses_internal_whitespace() -> None:
    assert scg.normalize_line("idx  =   text.find(x,   3)") == "idx = text.find(x, 3)"


def test_normalize_line_treats_tabs_as_whitespace() -> None:
    assert scg.normalize_line("\tidx\t=\ttext.find(x, 3)") == "idx = text.find(x, 3)"


def test_normalize_line_whitespace_only_variants_are_identical() -> None:
    """空白差分だけの行は同一視される（issue 明記の正規化ユニットテスト）。"""
    a = scg.normalize_line("    result = do_thing(a,b)")
    b = scg.normalize_line("result   =   do_thing(a,b)  ")
    assert a == b


# --- is_trivial_line -----------------------------------------------------------


def test_trivial_line_empty_string() -> None:
    assert scg.is_trivial_line("") is True


def test_trivial_line_pass() -> None:
    assert scg.is_trivial_line("pass") is True


def test_trivial_line_return_none() -> None:
    assert scg.is_trivial_line("return None") is True


def test_trivial_line_bare_return() -> None:
    assert scg.is_trivial_line("return") is True


def test_trivial_line_continue_and_break() -> None:
    assert scg.is_trivial_line("continue") is True
    assert scg.is_trivial_line("break") is True


def test_trivial_line_else_and_try() -> None:
    assert scg.is_trivial_line("else:") is True
    assert scg.is_trivial_line("try:") is True


def test_trivial_line_comment_only() -> None:
    assert scg.is_trivial_line("# just a comment explaining below") is True


def test_trivial_line_below_min_token_threshold() -> None:
    # デフォルト閾値 4 未満のトークン数は trivial 扱い。
    assert scg.is_trivial_line("x = 1", min_tokens=4) is True


def test_non_trivial_line_meets_token_threshold() -> None:
    normalized = scg.normalize_line('idx = text.find("---", 3)')
    assert scg.is_trivial_line(normalized, min_tokens=4) is False


def test_trivial_threshold_is_configurable() -> None:
    normalized = scg.normalize_line("x = 1")
    assert scg.is_trivial_line(normalized, min_tokens=2) is False


def test_trivial_line_import_statement() -> None:
    """import 文はモジュール依存の宣言であり「弱いロジック片の複製」ではないため trivial
    扱い（#210 実コーパス dry-run 較正: `from typing import ...` 等が repo 全体で大量一致し
    FP の大半を占めることを確認して追加）。"""
    assert scg.is_trivial_line(scg.normalize_line("from typing import List, Optional")) is True
    assert scg.is_trivial_line(scg.normalize_line("import os")) is True
    assert scg.is_trivial_line(scg.normalize_line("    from foo.bar import baz")) is True


def test_non_trivial_line_is_not_mistaken_for_import() -> None:
    """`from` / `import` で始まらない通常のロジック行は誤って trivial 扱いされない。"""
    normalized = scg.normalize_line('idx = text.find("---", 3)')
    assert scg.is_trivial_line(normalized) is False


# --- parse_diff_removed_lines --------------------------------------------------


def test_parse_diff_removed_lines_basic_modification() -> None:
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "index abc123..def456 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -10,3 +10,3 @@ def something():\n"
        "     context line\n"
        "-    old_line = 1\n"
        "+    new_line = 2\n"
        "     context line\n"
    )
    changed = scg.parse_diff_removed_lines(diff_text)
    assert [(c.file, c.line_no, c.text) for c in changed] == [
        ("foo.py", 11, "    old_line = 1"),
    ]


def test_parse_diff_removed_lines_new_file_yields_nothing() -> None:
    """新規ファイル追加は「変更前の内容」が存在しないため検出対象外（設計 e）。"""
    diff_text = (
        "diff --git a/bar.py b/bar.py\n"
        "new file mode 100644\n"
        "index 0000000..abc123\n"
        "--- /dev/null\n"
        "+++ b/bar.py\n"
        "@@ -0,0 +1,3 @@\n"
        "+line1\n"
        "+line2\n"
        "+line3\n"
    )
    changed = scg.parse_diff_removed_lines(diff_text)
    assert changed == []


def test_parse_diff_removed_lines_deleted_file_yields_nothing() -> None:
    """ファイル自体が削除される diff は新ファイル側パスが無いため対象外（設計 e）。"""
    diff_text = (
        "diff --git a/gone.py b/gone.py\n"
        "deleted file mode 100644\n"
        "index abc123..0000000\n"
        "--- a/gone.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-old1\n"
        "-old2\n"
    )
    changed = scg.parse_diff_removed_lines(diff_text)
    assert changed == []


def test_parse_diff_removed_lines_multiple_hunks_same_file() -> None:
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "index abc123..def456 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-a = 1\n"
        "+a = 100\n"
        " b = 2\n"
        "@@ -20,2 +20,2 @@\n"
        " c = 3\n"
        "-d = 4\n"
        "+d = 400\n"
    )
    changed = scg.parse_diff_removed_lines(diff_text)
    assert [(c.file, c.line_no, c.text) for c in changed] == [
        ("foo.py", 1, "a = 1"),
        ("foo.py", 21, "d = 4"),
    ]


def test_parse_diff_removed_lines_multiple_files() -> None:
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "index abc..def 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 1\n"
        "+x = 2\n"
        "diff --git a/bar.py b/bar.py\n"
        "index abc..def 100644\n"
        "--- a/bar.py\n"
        "+++ b/bar.py\n"
        "@@ -5,1 +5,1 @@\n"
        "-y = 1\n"
        "+y = 2\n"
    )
    changed = scg.parse_diff_removed_lines(diff_text)
    assert [(c.file, c.line_no) for c in changed] == [("foo.py", 1), ("bar.py", 5)]


def test_parse_diff_removed_lines_ignores_no_newline_marker() -> None:
    diff_text = (
        "diff --git a/foo.py b/foo.py\n"
        "index abc..def 100644\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x = 1\n"
        "\\ No newline at end of file\n"
        "+x = 2\n"
        "\\ No newline at end of file\n"
    )
    changed = scg.parse_diff_removed_lines(diff_text)
    assert [(c.file, c.line_no, c.text) for c in changed] == [("foo.py", 1, "x = 1")]


def test_parse_diff_removed_lines_empty_diff_yields_nothing() -> None:
    assert scg.parse_diff_removed_lines("") == []


# --- detect_sibling_copies: #40 型再現 ------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _diff_for_single_line_change(
    file: str, old_line_no: int, old_text: str, new_text: str
) -> str:
    return (
        f"diff --git a/{file} b/{file}\n"
        f"index abc..def 100644\n"
        f"--- a/{file}\n"
        f"+++ b/{file}\n"
        f"@@ -{old_line_no},1 +{old_line_no},1 @@\n"
        f"-{old_text}\n"
        f"+{new_text}\n"
    )


def test_detect_sibling_copies_finds_unmodified_copies(tmp_path: Path) -> None:
    """#40 型: 同一の弱いロジック片が reader/writer 複数箇所にコピーされ、片側だけ変更。"""
    root = _make_repo(tmp_path)
    weak_line = '    idx = text.find("---", 3)'
    _write(root / "scripts/lib/reader.py", f"def read(text):\n{weak_line}\n    return idx\n")
    _write(root / "scripts/lib/writer.py", f"def write(text):\n{weak_line}\n    return idx\n")
    _write(root / "scripts/lib/archiver.py", f"def archive(text):\n{weak_line}\n    return idx\n")

    diff_text = _diff_for_single_line_change(
        "scripts/lib/reader.py", 2, weak_line, '    idx = text.find("---", 3, 10)'
    )
    matches = scg.detect_sibling_copies(diff_text, root)

    assert len(matches) == 1
    m = matches[0]
    assert m.changed_file == "scripts/lib/reader.py"
    assert m.changed_line == 2
    sibling_files = {s.file for s in m.siblings}
    assert sibling_files == {"scripts/lib/writer.py", "scripts/lib/archiver.py"}


def test_detect_sibling_copies_no_match_when_no_siblings(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    weak_line = '    idx = text.find("---", 3)'
    _write(root / "scripts/lib/reader.py", f"def read(text):\n{weak_line}\n    return idx\n")

    diff_text = _diff_for_single_line_change(
        "scripts/lib/reader.py", 2, weak_line, '    idx = text.find("---", 3, 10)'
    )
    matches = scg.detect_sibling_copies(diff_text, root)
    assert matches == []


# --- detect_sibling_copies: trivial 行は誤検出しない -----------------------------


def test_detect_sibling_copies_ignores_trivial_return_none(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/a.py", "def f():\n    if True:\n        return None\n")
    _write(root / "scripts/lib/b.py", "def g():\n    if True:\n        return None\n")

    diff_text = _diff_for_single_line_change(
        "scripts/lib/a.py", 3, "        return None", "        return 1"
    )
    matches = scg.detect_sibling_copies(diff_text, root)
    assert matches == []


def test_detect_sibling_copies_ignores_short_lines_below_token_threshold(
    tmp_path: Path,
) -> None:
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/a.py", "def f():\n    x = 1\n")
    _write(root / "scripts/lib/b.py", "def g():\n    x = 1\n")

    diff_text = _diff_for_single_line_change("scripts/lib/a.py", 2, "    x = 1", "    x = 2")
    matches = scg.detect_sibling_copies(diff_text, root)
    assert matches == []


# --- detect_sibling_copies: 変更行/変更ファイル自身を除外 -----------------------


def test_detect_sibling_copies_excludes_changed_line_itself(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    weak_line = '    idx = text.find("---", 3)'
    # 変更後のファイル1つだけで、他に兄弟コピーが無い場合は検出されない
    # （＝変更行自身が「同型の未変更行」として自己マッチしないことの確認）。
    _write(root / "scripts/lib/only.py", f"def f(text):\n{weak_line}\n    return idx\n")

    diff_text = _diff_for_single_line_change(
        "scripts/lib/only.py", 2, weak_line, '    idx = text.find("---", 3, 10)'
    )
    matches = scg.detect_sibling_copies(diff_text, root)
    assert matches == []


def test_detect_sibling_copies_excludes_changed_file_even_at_other_lines(
    tmp_path: Path,
) -> None:
    """変更されたファイル自身は、変更行と別の行に同型コードが残っていても除外する。"""
    root = _make_repo(tmp_path)
    weak_line = '    idx = text.find("---", 3)'
    _write(
        root / "scripts/lib/self_copy.py",
        f"def read(text):\n{weak_line}\n    return idx\n\n"
        f"def read_again(text):\n{weak_line}\n    return idx\n",
    )

    diff_text = _diff_for_single_line_change(
        "scripts/lib/self_copy.py", 2, weak_line, '    idx = text.find("---", 3, 10)'
    )
    matches = scg.detect_sibling_copies(diff_text, root)
    assert matches == []


# --- detect_sibling_copies: スコープ外ファイルは無視 -----------------------------


def test_detect_sibling_copies_ignores_files_outside_scope(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    weak_line = '    idx = text.find("---", 3)'
    _write(root / "scripts/lib/reader.py", f"def read(text):\n{weak_line}\n    return idx\n")
    # scope 外（scripts/**.py, skills/**/scripts/**.py のいずれにも該当しない）
    _write(root / "docs/notes.py", f"def note(text):\n{weak_line}\n    return idx\n")

    diff_text = _diff_for_single_line_change(
        "scripts/lib/reader.py", 2, weak_line, '    idx = text.find("---", 3, 10)'
    )
    matches = scg.detect_sibling_copies(diff_text, root)
    assert matches == []


def test_detect_sibling_copies_includes_skills_scripts_scope(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    weak_line = '    idx = text.find("---", 3)'
    _write(root / "scripts/lib/reader.py", f"def read(text):\n{weak_line}\n    return idx\n")
    _write(
        root / "skills/demo/scripts/helper.py",
        f"def helper(text):\n{weak_line}\n    return idx\n",
    )

    diff_text = _diff_for_single_line_change(
        "scripts/lib/reader.py", 2, weak_line, '    idx = text.find("---", 3, 10)'
    )
    matches = scg.detect_sibling_copies(diff_text, root)
    assert len(matches) == 1
    assert {s.file for s in matches[0].siblings} == {"skills/demo/scripts/helper.py"}


def test_iter_py_files_not_fooled_by_dotclaude_in_repo_root_path(tmp_path: Path) -> None:
    """repo_root 自身の絶対パスに `.claude` セグメントが含まれても除外されない
    （skill_declaration_reachability #191 と同型の実機発見済みパターンの回帰防止）。"""
    fake_worktree_root = tmp_path / ".claude" / "worktrees" / "agent-xyz"
    _write(fake_worktree_root / "scripts/lib/foo.py", "def func_a():\n    return 1\n")
    files = scg._iter_py_files(fake_worktree_root)
    assert any(f.name == "foo.py" for f in files)


# --- get_diff_text: 実 git E2E -------------------------------------------------


def _init_real_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)


def test_get_diff_text_and_detect_end_to_end_with_real_git(tmp_path: Path) -> None:
    """実 git（LLM 非使用）で diff を生成し、そのまま検出まで通す E2E テスト。"""
    root = tmp_path / "repo"
    _init_real_git_repo(root)

    weak_line = '    idx = text.find("---", 3)'
    _write(root / "scripts/lib/reader.py", f"def read(text):\n{weak_line}\n    return idx\n")
    _write(root / "scripts/lib/writer.py", f"def write(text):\n{weak_line}\n    return idx\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    # reader.py だけ片側修正する。
    _write(
        root / "scripts/lib/reader.py",
        'def read(text):\n    idx = text.find("---", 3, 10)\n    return idx\n',
    )

    diff_text = scg.get_diff_text(root, ["HEAD"])
    matches = scg.detect_sibling_copies(diff_text, root)

    assert len(matches) == 1
    assert matches[0].changed_file == "scripts/lib/reader.py"
    assert {s.file for s in matches[0].siblings} == {"scripts/lib/writer.py"}
