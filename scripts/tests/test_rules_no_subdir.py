"""`.claude/rules/` 配下にサブディレクトリを作らない不変条件を守る (#660)。

`.claude/rules/**` は毎セッション常時ロードされるため、直下以外の階層に
置かれたファイルは「遅延読み込みのつもり」でも常時ロードされてしまう
（実例: `.claude/rules/refs/no-denylist-checks.md` が `.claude/refs/` に
移設される前、常時ロードされ続けていた）。判定はパスの深さのみで行い、
ファイル名・拡張子には依存しない（`refs` という名前で判定すると
別名のサブディレクトリに改名するだけで破れる）。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RULES_DIR = ROOT / ".claude" / "rules"


def find_rules_subdir_violations(rules_dir: Path) -> list[Path]:
    """rules_dir 直下以外にあるファイルを列挙する。

    symlink で作られたディレクトリも `Path.is_dir()` がターゲットを解決して
    真として扱うため、symlink 経由のネストも辿る。rules_dir 直下の
    ファイル（symlink であっても）は対象外 — 本チェックが守るのは
    「サブディレクトリを作らない」という構造の不変条件であり、
    直下ファイルの中身が symlink かどうかは別の懸念。
    """
    violations: list[Path] = []
    # `Path.glob` は走査時の PermissionError を抑制して空を返す（Python 3.14 実測）。
    # 読めないのに「違反なし」と判定すると検査が黙って無力化するため、
    # 送出する `iterdir()` を使い、検査不能は赤にする（2026-09-24 codex レビュー [Must]）。
    for path in sorted(rules_dir.iterdir()):
        if path.is_dir():
            violations.extend(_walk_files(path))
    return violations


def _walk_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_dir():
            files.extend(_walk_files(entry))
        else:
            files.append(entry)
    return files


def test_rules_dir_has_no_subdirectories() -> None:
    violations = find_rules_subdir_violations(RULES_DIR)
    assert violations == [], (
        ".claude/rules/ は常時ロードされるため直下にのみファイルを置く。"
        f"サブディレクトリ内に見つかったファイル: {violations}。"
        "遅延読込したいものは .claude/refs/ へ置く。"
    )


def test_root_resolution_points_at_this_repository() -> None:
    """走査の起点がずれていないことを、この検査とは独立な目印で確かめる。

    `ROOT` は `parents[2]` 固定なので、このファイルを1階層深いディレクトリへ移すと
    起点が `scripts/` へずれる。ずれ先には `.claude/rules` が無く、走査は
    「違反なし」で緑になってしまう（2026-09-24 codex レビュー [Must]）。
    起点の実在だけを確認しても、その確認自体を消されたら気づけないため、
    **リポジトリ固有の目印** で起点そのものを固定する。
    """
    assert (ROOT / "pytest.ini").is_file(), f"起点がリポジトリ root でない: {ROOT}"
    assert (ROOT / ".claude-plugin").is_dir(), f"起点がリポジトリ root でない: {ROOT}"
    assert RULES_DIR.is_dir(), f"走査の起点が見つかりません: {RULES_DIR}"


# --- 検査自身の回帰テスト（この検査を壊す変異で赤くなることを固定する） ---


def test_flat_rules_dir_is_clean(tmp_path: Path) -> None:
    """陽性対照: 直下にファイルだけの正常配置で誤検出しない。"""
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "a.md").write_text("x")
    (rules / "b.md").write_text("x")
    assert find_rules_subdir_violations(rules) == []


def test_empty_subdirectory_is_allowed(tmp_path: Path) -> None:
    """陽性対照: 空のサブディレクトリは違反にしない（完成条件の対象外）。"""
    rules = tmp_path / "rules"
    (rules / "empty").mkdir(parents=True)
    assert find_rules_subdir_violations(rules) == []


def test_nested_file_is_detected(tmp_path: Path) -> None:
    rules = tmp_path / "rules"
    (rules / "refs").mkdir(parents=True)
    target = rules / "refs" / "detail.md"
    target.write_text("x")
    assert find_rules_subdir_violations(rules) == [target]


def test_deeply_nested_and_hidden_files_are_detected(tmp_path: Path) -> None:
    """名前・拡張子・隠しファイルに依存せずパスの深さだけで判定する。"""
    rules = tmp_path / "rules"
    (rules / "a" / "b").mkdir(parents=True)
    hidden = rules / "a" / ".hidden"
    deep = rules / "a" / "b" / "x.txt"
    hidden.write_text("x")
    deep.write_text("x")
    assert find_rules_subdir_violations(rules) == sorted([hidden, deep])


def test_unreadable_rules_dir_is_not_silently_clean(tmp_path: Path) -> None:
    """読取不能なら「違反なし」にせず送出する（黙って緑にしない）。"""
    rules = tmp_path / "rules"
    (rules / "refs").mkdir(parents=True)
    (rules / "refs" / "detail.md").write_text("x")
    rules.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            find_rules_subdir_violations(rules)
    finally:
        rules.chmod(0o755)


def test_missing_rules_dir_is_not_silently_clean(tmp_path: Path) -> None:
    """起点が存在しないなら「違反なし」にせず送出する。"""
    with pytest.raises(FileNotFoundError):
        find_rules_subdir_violations(tmp_path / "does-not-exist")
