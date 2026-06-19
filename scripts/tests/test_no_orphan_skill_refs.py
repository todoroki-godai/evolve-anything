#!/usr/bin/env python3
"""archive 済みスキルへのオーファン参照が repo 内に残っていないことを保証する smoke test。

Issue #25 の再発防止。archive ディレクトリに移動された skill 名がリポジトリ内コードから
import / path 参照されている場合は失敗する。
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ARCHIVE_DIR = Path(
    os.environ.get("CLAUDE_PLUGIN_DATA")
    or (Path.home() / ".claude" / "evolve-anything")
) / "archive"


def _list_archived_skill_names() -> list[str]:
    """archive ディレクトリから skill 名らしきものを抽出する。

    archive のファイル形式: `YYYYMMDD_HHMMSS_<original_name>` （prune.archive_file 由来）
    """
    if not _ARCHIVE_DIR.exists():
        return []
    names: set[str] = set()
    for path in _ARCHIVE_DIR.iterdir():
        if path.suffix == ".json":
            continue
        # YYYYMMDD_HHMMSS_<name> プレフィックスを剥がす
        m = re.match(r"^\d{8}_\d{6}_(.+?)(?:\.[^.]+)?$", path.name)
        if m:
            candidate = m.group(1)
            # ファイル名拡張子を持つもの（SKILL.md / *.py 等）はスキル名でないので除外
            if "." not in candidate and len(candidate) > 1:
                names.add(candidate)
    return sorted(names)


def _was_repo_skill(skill_name: str) -> bool:
    """skill_name が evolve-anything リポジトリのスキルだった履歴があるか（git で判定）。

    `_ARCHIVE_DIR`（= `~/.claude/evolve-anything/archive` または `$CLAUDE_PLUGIN_DATA/archive`）は
    開発機の **グローバル runtime archive** で、全 PJ・全プラグインの prune 結果が混在する。
    そのため他プラグイン由来のスキル名（例: openspec-* プラグインの `openspec-apply-change`）が
    archive に入り、それが evolve-anything のテストにフィクスチャ文字列として現れると、オーファン検査が
    「evolve-anything が archive した自分のスキル」と取り違えて誤検知する（非ハーメチックな FP）。
    オーファン検査は **evolve-anything 自身がリポジトリで持っていたスキル** だけを対象にすべきなので、
    git 履歴に `skills/<name>/` が一度も存在しない名前は除外する。

    git が使えない環境では従来どおり全件検査する（安全側 = 取りこぼさない）。
    """
    try:
        out = subprocess.run(
            ["git", "log", "--all", "--oneline", "-1", "--", f"skills/{skill_name}/"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode != 0:
            return True  # git エラー時は安全側で検査対象に残す
        return bool(out.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True


def _grep_repo(needle: str) -> list[str]:
    """repo 内で needle を含む行を返す。git grep を優先、フォールバックは rglob。"""
    try:
        out = subprocess.run(
            ["git", "grep", "-l", needle],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode == 0:
            return [line for line in out.stdout.splitlines() if line.strip()]
        if out.returncode == 1:
            return []  # no match
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # フォールバック: pure-Python
    matches = []
    for p in _REPO_ROOT.rglob("*.py"):
        if "__pycache__" in p.parts or "archive" in p.parts:
            continue
        try:
            if needle in p.read_text(encoding="utf-8", errors="ignore"):
                matches.append(str(p.relative_to(_REPO_ROOT)))
        except OSError:
            continue
    return matches


def test_no_orphan_archived_skill_refs() -> None:
    """archive 済み skill への import / path ref が repo 内に残っていないこと。"""
    # グローバル runtime archive には他プラグイン由来のスキルも混ざる。evolve-anything 自身が
    # リポジトリで持っていた（git 履歴に skills/<name>/ がある）名前だけをオーファン検査対象にする。
    archived = [n for n in _list_archived_skill_names() if _was_repo_skill(n)]
    if not archived:
        pytest.skip("no evolve-anything-origin archived skills found in {}".format(_ARCHIVE_DIR))

    def _filter_noise(refs: list[str]) -> list[str]:
        return [
            r for r in refs
            if "CHANGELOG" not in r and "openspec" not in r and "/archive/" not in r
        ]

    orphan_refs: dict[str, list[str]] = {}
    for skill_name in archived:
        # path 参照: skills/<name>/
        path_refs = _filter_noise(_grep_repo(f"skills/{skill_name}/"))
        # import 参照: Issue #25 本丸（`from {skill} import` / `import {skill}`）
        import_refs = _filter_noise(_grep_repo(f"from {skill_name} import"))
        import_refs += _filter_noise(_grep_repo(f"import {skill_name}"))
        all_refs = sorted(set(path_refs + import_refs))
        if all_refs:
            orphan_refs[skill_name] = all_refs

    assert not orphan_refs, (
        f"orphan refs to archived skills found: {orphan_refs}. "
        "Run check_import_dependencies before archiving."
    )


def test_provenance_gate_excludes_cross_plugin_archive_names() -> None:
    """プロベナンス・ゲートが他プラグイン由来の archive 名を検査対象から外すこと（非ハーメチック FP の回帰）。

    `openspec-apply-change` 等の openspec プラグインスキルが開発機のグローバル archive に
    入っていても、evolve-anything のリポジトリスキルではないので検査対象外になる。一方で
    evolve-anything 自身のスキル（prune/evolve/audit）は検査対象に残る。
    """
    # evolve-anything のリポジトリスキルは対象に残る
    assert _was_repo_skill("prune")
    assert _was_repo_skill("evolve")
    # 他プラグイン由来 / 存在しない名前は対象外
    assert not _was_repo_skill("openspec-apply-change")
    assert not _was_repo_skill("this-skill-never-existed-xyz")
