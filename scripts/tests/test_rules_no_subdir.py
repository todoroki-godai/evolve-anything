"""`.claude/rules/` 配下にサブディレクトリを作らない不変条件を守る (#660)。

`.claude/rules/**` は毎セッション常時ロードされるため、直下以外の階層に
置かれたファイルは「遅延読み込みのつもり」でも常時ロードされてしまう
（実例: `.claude/rules/refs/no-denylist-checks.md` が `.claude/refs/` に
移設される前、常時ロードされ続けていた）。判定はパスの深さのみで行い、
ファイル名・拡張子には依存しない（`refs` という名前で判定すると
別名のサブディレクトリに改名するだけで破れる）。
"""
from pathlib import Path

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
    for path in sorted(rules_dir.glob("*")):
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
