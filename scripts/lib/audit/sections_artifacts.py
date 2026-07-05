"""artifact 衛生の決定論検出器 + observability セクション（#124 #125 #126 #129）。

`artifacts.py` の find_artifacts / find_project_skill_dirs と走査対象（`.claude/skills` /
plugin レイアウト `skills/` / グローバル `~/.claude`）を共有する近傍モジュール。4 検出器を
1 モジュールに集約し、skills 走査を相乗りする。

- #124 detect_global_claude_md: グローバル `~/.claude/CLAUDE.md` の未存在 / 空チェック
- #125 detect_missing_skill_md: SKILL.md を配下に持たないスキルディレクトリ（skill-creator の
  Description Optimization が作る `<skill>-workspace/` 残骸等）
- #126 detect_backup_files: skills 配下の残置バックアップ（*.md.bak / *.backup / *.orig）
- #129 detect_duplicate_skill_names: skill name の跨 scope 重複（symlink wrapper は除外）
- #155 detect_global_hook_plugin_dup: グローバル settings.json の hook が、プラグイン hooks.json の
  hook と同機能（同一イベント・正規化 basename 一致）＝グローバル側残骸疑いの検出

各検出関数は走査 base（home / skill roots / SKILL.md パス群）を **引数で受け取る**。
実 `~/.claude` を直読みするのは default 値の解決時のみで、テストは tmp_path fixture を渡す。

section builder は `advisory.build_advisory_section`（observability contract 準拠）で組み立て、
`observability._OBSERVABILITY_BUILDERS` に登録して markdown / 構造化 両経路へ surface する。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ._constants import EXCLUDED_SKILL_DIRS, is_excluded_skill_path
from .advisory import build_advisory_section


# ==========================================================================
# 共有: skill root の解決
# ==========================================================================


def skill_roots(project_dir: Path) -> List[Path]:
    """PJ 内で SKILL.md が置かれ得るディレクトリ（存在するもののみ）を返す。

    - `.claude/skills`: 通常レイアウト
    - `skills/`: plugin レイアウト（`.claude-plugin/plugin.json` があるリポジトリのみ）
      — find_artifacts と同じゲートで、非 plugin PJ の無関係 `skills/` を誤走査しない。
    """
    project_dir = Path(project_dir)
    roots = [project_dir / ".claude" / "skills"]
    if (project_dir / ".claude-plugin" / "plugin.json").exists():
        roots.append(project_dir / "skills")
    return [r for r in roots if r.exists()]


# ==========================================================================
# #124 グローバル CLAUDE.md
# ==========================================================================


@dataclass
class GlobalClaudeMdReport:
    path: Path
    exists: bool
    is_empty: bool

    @property
    def healthy(self) -> bool:
        return self.exists and not self.is_empty


def detect_global_claude_md(home: Optional[Path] = None) -> GlobalClaudeMdReport:
    """グローバル `~/.claude/CLAUDE.md` の未存在 / 空を検出する。

    home は default 時のみ `Path.home()` を読む（テストは tmp_path を渡す）。
    存在しても strip() 後が空文字なら is_empty=True。読取失敗も空とみなす（要確認の advisory）。
    """
    base = Path.home() if home is None else Path(home)
    path = base / ".claude" / "CLAUDE.md"
    if not path.exists():
        return GlobalClaudeMdReport(path=path, exists=False, is_empty=False)
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        content = ""
    return GlobalClaudeMdReport(path=path, exists=True, is_empty=(content.strip() == ""))


def build_global_claude_md_section(project_dir: Path) -> Optional[List[str]]:
    """グローバル CLAUDE.md が未存在 / 空のときだけ advisory を出す（#124）。

    silence != evaluated: 中身があれば None（沈黙）。project_dir は使わず home を見る。
    """

    def compute(_proj: Path) -> GlobalClaudeMdReport:
        return detect_global_claude_md()

    def render(report: GlobalClaudeMdReport) -> List[str]:
        if not report.exists:
            return [
                f"⚠ グローバル CLAUDE.md が未存在（{report.path}）。全 PJ 共通の指示・"
                "優先順位が未設定です。作成を検討してください。",
            ]
        return [
            f"⚠ グローバル CLAUDE.md が空（{report.path}）。全 PJ 共通の指示が空です。"
            "内容を追記するか、意図的に空なら無視して構いません。",
        ]

    return build_advisory_section(
        project_dir,
        title="Global CLAUDE.md (~/.claude/CLAUDE.md)",
        compute=compute,
        applicable=lambda report: not report.healthy,
        render=render,
    )


# ==========================================================================
# #125 SKILL.md 欠落ディレクトリ
# ==========================================================================


def detect_missing_skill_md(roots: Sequence[Path]) -> List[Path]:
    """SKILL.md を配下に持たないスキルディレクトリを列挙する（#125）。

    各 root の直下ディレクトリを走査し、配下（rglob）に有効な SKILL.md が 1 件も無いものを
    「欠落」とみなす。skill-creator の workspace 残骸（`<skill>-workspace/`）が主対象。
    除外ディレクトリ（`.archive` / `_archived` 等）と dot-dir は対象外
    （`EXCLUDED_SKILL_DIRS` / is_excluded_skill_path と整合）。
    """
    missing: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            if child.name in EXCLUDED_SKILL_DIRS or child.name.startswith("."):
                continue
            has_skill = any(
                not is_excluded_skill_path(md) for md in child.rglob("SKILL.md")
            )
            if not has_skill:
                missing.append(child)
    return missing


def build_missing_skill_md_section(project_dir: Path) -> Optional[List[str]]:
    """SKILL.md を持たないスキルディレクトリを surface する（#125）。"""

    def compute(proj: Path) -> Dict[str, Any]:
        roots = skill_roots(proj)
        return {"roots": roots, "missing": detect_missing_skill_md(roots)}

    def render(data: Dict[str, Any]) -> List[str]:
        missing: List[Path] = data["missing"]
        if not missing:
            return [
                "✓ 評価したが該当なし（全スキルディレクトリに SKILL.md が存在する）",
            ]
        lines = [
            f"⚠ SKILL.md を持たないスキルディレクトリが {len(missing)} 件"
            "（skill-creator の workspace 残骸等）。不要なら削除を検討してください:",
        ]
        for d in missing:
            lines.append(f"  ・{d.name}")
        return lines

    return build_advisory_section(
        project_dir,
        title="Missing SKILL.md (スキルディレクトリ)",
        compute=compute,
        applicable=lambda data: bool(data["roots"]),
        render=render,
    )


# ==========================================================================
# #126 残置バックアップファイル
# ==========================================================================


_BACKUP_GLOBS = ("*.md.bak", "*.backup", "*.orig")


def detect_backup_files(roots: Sequence[Path]) -> List[Path]:
    """skills 配下の残置バックアップファイルを列挙する（#126）。

    パターン: `*.md.bak` / `*.backup` / `*.orig`。除外ディレクトリ配下は対象外。
    """
    seen: set[Path] = set()
    found: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for pattern in _BACKUP_GLOBS:
            for f in root.rglob(pattern):
                if is_excluded_skill_path(f):
                    continue
                if f in seen:
                    continue
                seen.add(f)
                found.append(f)
    return sorted(found)


def build_backup_files_section(project_dir: Path) -> Optional[List[str]]:
    """skills 配下の残置バックアップファイルを surface する（#126）。"""

    def compute(proj: Path) -> Dict[str, Any]:
        roots = skill_roots(proj)
        return {"roots": roots, "backups": detect_backup_files(roots)}

    def render(data: Dict[str, Any]) -> List[str]:
        backups: List[Path] = data["backups"]
        if not backups:
            return [
                "✓ 評価したが該当なし（skills 配下にバックアップファイル残骸なし）",
            ]
        lines = [
            f"⚠ skills 配下に残置バックアップファイルが {len(backups)} 件"
            "（*.md.bak / *.backup / *.orig）。不要なら削除を検討してください:",
        ]
        for f in backups:
            lines.append(f"  ・{f.parent.name}/{f.name}")
        return lines

    return build_advisory_section(
        project_dir,
        title="Backup Files (skills 配下の残置)",
        compute=compute,
        applicable=lambda data: bool(data["roots"]),
        render=render,
    )


# ==========================================================================
# #129 skill name 跨 scope 重複
# ==========================================================================


def _is_symlink_path(path: Path) -> bool:
    """SKILL.md 自身、または親スキルディレクトリが symlink かを判定する。"""
    try:
        if path.is_symlink():
            return True
        return path.parent.is_symlink()
    except OSError:
        return False


@dataclass
class SkillNameGroup:
    name: str
    paths: List[Path]  # 実体（非 symlink）の SKILL.md パス（2 件以上）
    symlink_paths: List[Path] = field(default_factory=list)  # symlink wrapper（区別表示用）


def detect_duplicate_skill_names(skill_md_paths: Sequence[Path]) -> List[SkillNameGroup]:
    """skill name（frontmatter の `name`）でグルーピングし跨 scope 重複を報告する（#129）。

    frontmatter name が無い場合は親ディレクトリ名で代替する。symlink wrapper
    （例: gstack の `_gstack-command`）は実体を二重計上させて偽陽性を生むため、実体
    （非 symlink）が 2 件以上あるグループだけを重複として発火し、symlink は `symlink_paths`
    に区別して残す（除外だが可視化）。
    """
    from frontmatter import parse_frontmatter

    groups: Dict[str, List[Path]] = {}
    for path in skill_md_paths:
        fm = parse_frontmatter(path)
        name = fm.get("name") or path.parent.name
        groups.setdefault(str(name), []).append(path)

    result: List[SkillNameGroup] = []
    for name, paths in sorted(groups.items()):
        real = [p for p in paths if not _is_symlink_path(p)]
        symlinks = [p for p in paths if _is_symlink_path(p)]
        if len(real) < 2:
            continue  # 実体 1 つ以下（symlink wrapper 由来の重複）は非該当
        result.append(
            SkillNameGroup(name=name, paths=sorted(real), symlink_paths=sorted(symlinks))
        )
    return result


def build_duplicate_skill_names_section(project_dir: Path) -> Optional[List[str]]:
    """skill name の跨 scope 重複を surface する（#129）。

    走査は find_artifacts（`.claude/skills` / plugin `skills/` / グローバル `~/.claude`）に
    相乗りするため、project scope と global scope をまたいだ同名重複を検出できる。
    """

    def compute(proj: Path) -> Dict[str, Any]:
        from .artifacts import find_artifacts

        skills = find_artifacts(proj).get("skills", [])
        return {"scanned": len(skills), "groups": detect_duplicate_skill_names(skills)}

    def render(data: Dict[str, Any]) -> List[str]:
        groups: List[SkillNameGroup] = data["groups"]
        if not groups:
            return [
                "✓ 評価したが該当なし（skill name の跨 scope 重複なし）",
            ]
        lines = [
            f"⚠ 同一 skill name が複数ディレクトリに存在（跨 scope 重複）が {len(groups)} 件。"
            "統合または削除を検討してください:",
        ]
        for g in groups:
            dirs = ", ".join(str(p.parent) for p in g.paths)
            lines.append(f"  ・{g.name}: {dirs}")
            if g.symlink_paths:
                syms = ", ".join(str(p.parent) for p in g.symlink_paths)
                lines.append(f"    （symlink wrapper・重複計上から除外: {syms}）")
        return lines

    return build_advisory_section(
        project_dir,
        title="Duplicate Skill Names (跨 scope 重複)",
        compute=compute,
        applicable=lambda data: data["scanned"] > 0,
        render=render,
    )


# ==========================================================================
# #155 グローバル hook × プラグイン hook 残骸重複
# ==========================================================================


_SCRIPT_BASENAME_RE = re.compile(r"([\w.\-]+\.py)")


def _default_plugin_hooks_path() -> Path:
    """プラグインルートの `hooks/hooks.json`（parents[3]=プラグインルート）。

    sections_artifacts.py は `scripts/lib/audit/` 配下なので parents[3] がプラグインルート。
    """
    return Path(__file__).resolve().parents[3] / "hooks" / "hooks.json"


def _normalize_script_name(command: str) -> Optional[str]:
    """コマンド文字列から script basename を抽出し正規化名を返す（無ければ None）。

    `([\\w.\\-]+\\.py)` で最初の *.py を拾い、basename の `.py` を除去 → lower() かつ
    `-`/`_` を除去（`record-verbosity` と `record_verbosity` を同一視するため）。
    """
    m = _SCRIPT_BASENAME_RE.search(command)
    if not m:
        return None
    stem = os.path.basename(m.group(1))[: -len(".py")]
    return stem.lower().replace("-", "").replace("_", "")


def _hook_commands_by_event(hooks_section: Any) -> Dict[str, List[str]]:
    """hooks セクション（{event: [{matcher, hooks:[{command}]}]}）を event→command 群に展開。"""
    result: Dict[str, List[str]] = {}
    if not isinstance(hooks_section, dict):
        return result
    for event, groups in hooks_section.items():
        if not isinstance(groups, list):
            continue
        cmds: List[str] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            for entry in group.get("hooks", []) or []:
                if isinstance(entry, dict) and isinstance(entry.get("command"), str):
                    cmds.append(entry["command"])
        if cmds:
            result[event] = cmds
    return result


def _load_hooks_section(path: Path) -> Dict[str, List[str]]:
    """settings.json / hooks.json を読み event→command 群を返す。読取失敗は空 dict。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return _hook_commands_by_event(data.get("hooks"))


@dataclass
class GlobalHookDupReport:
    event: str
    global_command: str
    plugin_command: str
    normalized: str


def detect_global_hook_plugin_dup(
    home: Optional[Path] = None,
    plugin_hooks_path: Optional[Path] = None,
) -> List[GlobalHookDupReport]:
    """グローバル settings.json の hook がプラグイン hooks.json の hook と同機能かを検出する（#155）。

    「同機能」＝ **同一イベントキー**（Stop / PreToolUse 等）かつ、コマンド文字列から抽出した
    script の**正規化 basename が一致**（`-`/`_` 無視・lower）。一致するグローバル側 hook を
    「グローバル残骸疑い」として返す（プラグイン版へ一本化すべき二重登録の残骸）。

    home は default 時のみ `Path.home()` を読む（テストは tmp_path を渡す）。
    plugin_hooks_path は default 時のみプラグインルートの hooks.json を読む。
    settings.json / hooks.json の読取失敗（未存在・壊れ JSON）は空リストで握る（advisory を壊さない）。
    """
    base = Path.home() if home is None else Path(home)
    settings_path = base / ".claude" / "settings.json"
    plugin_path = (
        _default_plugin_hooks_path() if plugin_hooks_path is None else Path(plugin_hooks_path)
    )

    global_by_event = _load_hooks_section(settings_path)
    plugin_by_event = _load_hooks_section(plugin_path)

    reports: List[GlobalHookDupReport] = []
    for event, global_cmds in global_by_event.items():
        plugin_cmds = plugin_by_event.get(event)
        if not plugin_cmds:
            continue  # 同一イベントにプラグイン hook が無い → スコープ外
        plugin_by_norm: Dict[str, str] = {}
        for cmd in plugin_cmds:
            norm = _normalize_script_name(cmd)
            if norm and norm not in plugin_by_norm:
                plugin_by_norm[norm] = cmd
        for gcmd in global_cmds:
            norm = _normalize_script_name(gcmd)
            if norm and norm in plugin_by_norm:
                reports.append(
                    GlobalHookDupReport(
                        event=event,
                        global_command=gcmd,
                        plugin_command=plugin_by_norm[norm],
                        normalized=norm,
                    )
                )
    return reports


def build_global_hook_plugin_dup_section(project_dir: Path) -> Optional[List[str]]:
    """プラグインと重複するグローバル hook 残骸を surface する（#155）。

    残骸ゼロなら None（沈黙）。project_dir は使わず home / plugin hooks を見る（global_claude_md 同様）。
    """

    def compute(_proj: Path) -> Dict[str, Any]:
        return {"dups": detect_global_hook_plugin_dup()}

    def render(data: Dict[str, Any]) -> List[str]:
        dups: List[GlobalHookDupReport] = data["dups"]
        lines = [
            f"⚠ プラグイン hooks.json と同機能のグローバル hook が {len(dups)} 件（同一イベント・"
            "正規化 basename 一致）。グローバル側が残骸の可能性。~/.claude/settings.json の該当 "
            "hook を除去しプラグイン版へ一本化を検討してください:",
        ]
        for d in dups:
            lines.append(f"  ・[{d.event}] global: {d.global_command}")
            lines.append(f"            plugin: {d.plugin_command}")
        return lines

    return build_advisory_section(
        project_dir,
        title="Global Hook Residue (plugin と重複するグローバル hook)",
        compute=compute,
        applicable=lambda data: bool(data["dups"]),
        render=render,
    )
