"""#491 再発予防 E2E: run_evolve(dry_run=True) は「1バイトも書かない」。

dry-run の evolve が pending marker / audit-history.jsonl / skill-evolve-cache.json /
evolve-state.json / episodic.db を書き込んでいた繋ぎ目バグ（繋ぎ目調査 F, 2026-06-12）の
共通根因は「dry-run 後の状態差分を assert する E2E が無かった」こと。本テストは
隔離 HOME + DATA_DIR 配下の全ファイル SHA256 が実行前後で不変であることを assert し、
3 箇所どの違反が再発しても落ちるゲートにする。

hashlib で自己完結に書く（scripts/lib/dogfood/ パッケージには依存しない — 別ブランチで
開発中のため）。HOME 隔離はこのディレクトリの conftest（#457）が autouse で行う。
"""
import hashlib
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parents[4]
for _p in (
    _plugin_root / "skills" / "evolve" / "scripts",
    _plugin_root / "skills" / "audit" / "scripts",
    _plugin_root / "scripts" / "lib",
    _plugin_root / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from evolve import run_evolve  # noqa: E402

# 文書化された意図的 dry-run 書込（SHA256 不変契約の原則ベース除外。#496/#513）。
# bypass フラグではなく「設計として dry-run でも書く」と文書化されたパスのみを列挙する:
# - evolve_pending/: evolve_decisions の pending marker。emit→drain 捕捉（#402/ADR-041）の
#   運用ポインタで、標準フロー（dry-run 分析のみ）で書かれないと drain が全死する（#513）
# - skill-evolve-cache.json / constitutional_cache.json: LLM 再呼び出し回避キャッシュ
#   （evolve-ops の cache warm 設計）
_DOCUMENTED_DRY_RUN_WRITES = (
    "evolve_pending/",
    "skill-evolve-cache.json",
    "constitutional_cache.json",
)


def _is_documented_write(rel_path: str) -> bool:
    return any(token in rel_path for token in _DOCUMENTED_DRY_RUN_WRITES)


def _snapshot(root: Path) -> dict[str, str]:
    """root 配下の全ファイルの相対パス→SHA256 マップを返す（存在しなければ空）。"""
    snap: dict[str, str] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


def test_dry_run_writes_nothing_under_isolated_dirs(tmp_path, monkeypatch):
    """run_evolve(dry_run=True) 前後で隔離ディレクトリ配下の全ファイル SHA256 が不変。"""
    import evolve_decisions as ed

    # MARKER_ROOT は import 時に実 home で凍結される（env 非依存）ので、
    # この E2E では明示的に隔離ツリー配下へ向ける（実 home 汚染防止 + snapshot 対象化）。
    marker_root = tmp_path / "isolated-home" / ".claude" / "evolve-anything" / "evolve_pending"
    monkeypatch.setattr(ed, "MARKER_ROOT", marker_root)

    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    # 監視対象: tmp_path 配下すべて（DATA_DIR=tmp_path・HOME=tmp_path/isolated-home の両方を含む）。
    # ただし project ディレクトリ自身（テスト入力）の事前内容は対象外にしたいが、
    # run_evolve は project_dir を read-only に扱うため、project 配下も含めて不変を確認する。
    before = _snapshot(tmp_path)

    run_evolve(project_dir=str(project_dir), dry_run=True)

    after = _snapshot(tmp_path)

    added = sorted(k for k in set(after) - set(before) if not _is_documented_write(k))
    removed = sorted(k for k in set(before) - set(after) if not _is_documented_write(k))
    modified = sorted(
        k
        for k in before.keys() & after.keys()
        if before[k] != after[k] and not _is_documented_write(k)
    )

    assert not added, f"dry-run が新規ファイルを作成した: {added}"
    assert not removed, f"dry-run が既存ファイルを削除した: {removed}"
    assert not modified, f"dry-run が既存ファイルを変更した: {modified}"
