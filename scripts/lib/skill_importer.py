"""コミュニティスキル import コアロジック。

fetch → validate → preview → confirm → copy のパイプラインを提供する。
セキュリティ: scripts/ の自動実行は一切しない。
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union
from urllib.parse import urlparse

import yaml

# スキル名バリデーション: 英数字・ハイフン・アンダースコアのみ、先頭は英数字
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")

# owner/repo/subpath セグメントバリデーション
_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,99}$")


def _validate_segment(val: str, label: str) -> None:
    """owner/repo/subpath の各セグメントが安全な文字列かチェックする。"""
    if not _SAFE_SEGMENT_RE.match(val) or ".." in val:
        raise ValueError(f"不正な {label}: {val!r}")


@dataclass
class GitHubSource:
    owner: str
    repo: str
    subpath: str | None  # リポジトリ内サブパス（任意）


@dataclass
class LocalSource:
    path: Path


@dataclass
class SkillMetadata:
    name: str
    description: str
    allowed_tools: list[str]
    source_path: Path  # tmp dir 内のパス
    has_scripts: bool
    script_files: list[str]  # preview 表示用


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_source(spec: str) -> GitHubSource | LocalSource:
    """ソース文字列を解析して GitHubSource または LocalSource を返す。

    "owner/repo"         → GitHubSource(owner, repo, None)
    "owner/repo/path"    → GitHubSource(owner, repo, "path")
    "https://github.com/..." → GitHubSource(...)
    "/local/path"        → LocalSource(Path(...))
    """
    # http:// は拒否
    if spec.startswith("http://"):
        raise ValueError("http:// は非対応です。https:// を使用してください。")

    # HTTPS URL
    if spec.startswith("https://"):
        parsed = urlparse(spec)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            raise ValueError(f"Invalid GitHub URL: {spec}")
        owner = parts[0]
        repo = parts[1].removesuffix(".git")
        subpath = "/".join(parts[2:]) if len(parts) > 2 else None
        _validate_segment(owner, "owner")
        _validate_segment(repo, "repo")
        if subpath:
            for part in Path(subpath).parts:
                _validate_segment(part, "subpath")
        return GitHubSource(owner=owner, repo=repo, subpath=subpath or None)

    # ローカルパス（/ で始まるもの）
    if spec.startswith("/") or spec.startswith("./") or spec.startswith("../"):
        return LocalSource(path=Path(spec))

    # 相対パスでも実在するディレクトリならローカルとして扱う
    if Path(spec).exists():
        return LocalSource(path=Path(spec))

    # owner/repo[/subpath] 形式
    parts = spec.split("/")
    if len(parts) >= 2:
        owner = parts[0]
        repo = parts[1]
        subpath = "/".join(parts[2:]) if len(parts) > 2 else None
        _validate_segment(owner, "owner")
        _validate_segment(repo, "repo")
        if subpath:
            for part in Path(subpath).parts:
                _validate_segment(part, "subpath")
        return GitHubSource(owner=owner, repo=repo, subpath=subpath or None)

    raise ValueError(f"Cannot parse source spec: {spec!r}")


def fetch_skill(source: GitHubSource | LocalSource, tmp_dir: Path) -> Path:
    """GitHub なら git clone --depth=1、ローカルならコピー。スキルディレクトリのパスを返す。"""
    if isinstance(source, LocalSource):
        dest = tmp_dir / source.path.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source.path, dest)
        return dest

    # GitHubSource
    clone_url = f"https://github.com/{source.owner}/{source.repo}.git"
    clone_dest = tmp_dir / source.repo
    subprocess.run(
        ["git", "clone", "--depth=1", clone_url, str(clone_dest)],
        check=True,
        capture_output=True,
        text=True,
    )

    if source.subpath:
        return clone_dest / source.subpath
    return clone_dest


def validate_skill(skill_path: Path, skills_dir: Path | None = None) -> tuple[SkillMetadata | None, ValidationResult]:
    """SKILL.md の frontmatter を検証。name/description 必須。

    Args:
        skill_path: スキルディレクトリのパス
        skills_dir: 名前衝突チェック用の既存スキルディレクトリ（省略時はチェックしない）

    Returns:
        (SkillMetadata, ValidationResult) のタプル。
        ValidationResult.valid=False の場合、SkillMetadata は None の場合がある。
    """
    skill_md = skill_path / "SKILL.md"
    errors: list[str] = []
    warnings: list[str] = []

    if not skill_md.exists():
        return None, ValidationResult(
            valid=False,
            errors=[f"SKILL.md が見つかりません: {skill_md}"],
        )

    # frontmatter 解析
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as e:
        return None, ValidationResult(valid=False, errors=[f"SKILL.md 読み取りエラー: {e}"])

    fm = _parse_frontmatter_text(text)

    # 必須フィールドチェック
    name = fm.get("name")
    if not name:
        errors.append("frontmatter に 'name' フィールドが必要です")

    description = fm.get("description")
    if not description:
        errors.append("frontmatter に 'description' フィールドが必要です")

    if errors:
        return None, ValidationResult(valid=False, errors=errors, warnings=warnings)

    # 名前の正規化
    name = str(name).strip()
    description = str(description).strip()

    # スキル名バリデーション
    if not _SAFE_NAME_RE.match(name):
        errors.append(f"スキル名に使用できない文字が含まれています: {name!r}")

    # allowed-tools
    allowed_tools_raw = fm.get("allowed-tools", fm.get("allowed_tools", []))
    if isinstance(allowed_tools_raw, list):
        allowed_tools = [str(t) for t in allowed_tools_raw]
    elif isinstance(allowed_tools_raw, str):
        allowed_tools = [s.strip() for s in allowed_tools_raw.split(",")]
    else:
        allowed_tools = []

    # scripts/ ディレクトリのスキャン
    has_scripts = False
    script_files: list[str] = []
    scripts_dir_path = skill_path / "scripts"
    if scripts_dir_path.is_dir():
        has_scripts = True
        for f in sorted(scripts_dir_path.rglob("*")):
            if f.is_file():
                rel = f.relative_to(skill_path)
                script_files.append(str(rel))
        if script_files:
            warnings.append(
                f"このスキルには scripts/ が含まれています ({len(script_files)} ファイル)。"
                " スクリプトは自動実行されませんが、内容を確認してください。"
            )

    # 名前衝突チェック
    if skills_dir is not None and name:
        existing = skills_dir / name
        if existing.exists():
            errors.append(
                f"スキル名 '{name}' は既に存在します: {existing}"
                " (上書きするには --force を使用してください)"
            )

    if errors:
        return None, ValidationResult(valid=False, errors=errors, warnings=warnings)

    metadata = SkillMetadata(
        name=name,
        description=description,
        allowed_tools=allowed_tools,
        source_path=skill_path,
        has_scripts=has_scripts,
        script_files=script_files,
    )
    return metadata, ValidationResult(valid=True, errors=[], warnings=warnings)


def preview_skill(metadata: SkillMetadata) -> str:
    """スキルのプレビューテキストを生成（インストール前に表示する）。"""
    lines = [
        "=" * 50,
        f"スキル名: {metadata.name}",
        f"説明: {metadata.description}",
    ]
    if metadata.allowed_tools:
        lines.append(f"使用ツール: {', '.join(metadata.allowed_tools)}")
    if metadata.has_scripts:
        lines.append(f"スクリプト ({len(metadata.script_files)} ファイル):")
        for sf in metadata.script_files:
            lines.append(f"  - {sf}")
    lines.append("=" * 50)
    return "\n".join(lines)


def install_skill(metadata: SkillMetadata, skills_dir: Path, force: bool = False) -> None:
    """skills/{name}/ にコピー。force=False で名前衝突時は FileExistsError。"""
    dest = skills_dir / metadata.name

    # パス・トラバーサルガード
    try:
        dest.resolve().relative_to(skills_dir.resolve())
    except ValueError:
        raise ValueError(f"不正なインストール先: {dest}")

    if dest.exists():
        if not force:
            raise FileExistsError(
                f"スキル '{metadata.name}' は既に存在します: {dest}"
                " (上書きするには force=True を指定してください)"
            )
        shutil.rmtree(dest)

    shutil.copytree(metadata.source_path, dest)


def _parse_frontmatter_text(text: str) -> dict:
    """YAML frontmatter テキストを辞書として返す。frontmatter なければ空辞書。"""
    if not text.startswith("---"):
        return {}

    end = text.find("---", 3)
    if end == -1:
        return {}

    yaml_str = text[3:end].strip()
    if not yaml_str:
        return {}

    try:
        parsed = yaml.safe_load(yaml_str)
        return parsed if isinstance(parsed, dict) else {}
    except yaml.YAMLError:
        return {}
