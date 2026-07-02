"""artifact 衛生の決定論検出器 + observability セクション（#124 #125 #126 #129）。

`artifacts.py` の find_artifacts / find_project_skill_dirs と走査対象（`.claude/skills` /
plugin レイアウト `skills/` / グローバル `~/.claude`）を共有する近傍モジュール。4 検出器を
1 モジュールに集約し、skills 走査を相乗りする。

- #124 detect_global_claude_md: グローバル `~/.claude/CLAUDE.md` の未存在 / 空チェック
- #125 detect_missing_skill_md: SKILL.md を配下に持たないスキルディレクトリ（skill-creator の
  Description Optimization が作る `<skill>-workspace/` 残骸等）
- #126 detect_backup_files: skills 配下の残置バックアップ（*.md.bak / *.backup / *.orig）
- #129 detect_duplicate_skill_names: skill name の跨 scope 重複（symlink wrapper は除外）

各検出関数は走査 base（home / skill roots / SKILL.md パス群）を **引数で受け取る**。
実 `~/.claude` を直読みするのは default 値の解決時のみで、テストは tmp_path fixture を渡す。

section builder は `advisory.build_advisory_section`（observability contract 準拠）で組み立て、
`observability._OBSERVABILITY_BUILDERS` に登録して markdown / 構造化 両経路へ surface する。
"""
from __future__ import annotations

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
