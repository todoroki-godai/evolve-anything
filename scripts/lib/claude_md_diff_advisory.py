"""claude_md_diff_advisory.py — CLAUDE.md 変更時、契約語を含む差分行を CI ログへ出力する（#415）。

**判定はしない。落とさない。advisory のみ。** `claude_md_contract.py` の脅威モデル訂正
（守るのは「うっかり削除」だけ・改ざん耐性は無い）を受けて、圧縮 PR を人間がレビューする
際の材料をログに出す素朴な仕組みとして追加した。凝った検出・除外ロジックは持たない
（`git diff` の対象行を、契約語の部分文字列一致で grep するだけ）。

失敗しても exit 0（advisory が壊れて CI 全体を落とすのは本末転倒）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import claude_md_contract as _contract  # noqa: E402


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _contract_tokens() -> list[str]:
    tokens = {tok for inv in _contract.REQUIRED_INVARIANTS for tok in inv.all_of}
    tokens.update(_contract.MUST_STAY_SECTIONS)
    return sorted(tokens)


def _git_diff(repo_root: Path, base_ref: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "diff", f"{base_ref}...HEAD", "--", "CLAUDE.md"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    base_ref = argv[0] if argv else "origin/main"
    repo_root = _repo_root()

    diff = _git_diff(repo_root, base_ref)
    if diff is None:
        print(f"[claude_md_diff_advisory] git diff 取得に失敗（base={base_ref}）。スキップ。")
        return 0
    if not diff.strip():
        print("[claude_md_diff_advisory] CLAUDE.md に差分なし。")
        return 0

    tokens = _contract_tokens()
    hit_lines = [
        line
        for line in diff.splitlines()
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith(("+++", "---"))
        and any(tok in line for tok in tokens)
    ]

    print(f"[claude_md_diff_advisory] CLAUDE.md 差分中、契約語を含む行: {len(hit_lines)} 件")
    for line in hit_lines:
        print(f"  {line}")
    print(
        "[claude_md_diff_advisory] これは advisory です（判定・fail はしません）。"
        "圧縮 PR のレビュー時に上記の行を目視確認してください。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
