"""skill ディレクトリの import / パス参照依存検査（旧 prune.py 由来）。

prune/__init__.py から re-export される（後方互換）。
"""
from pathlib import Path
from typing import Any, Dict, List, Optional


class SkillDependencyError(Exception):
    """skill ディレクトリの archive 時に外部依存が検出された場合に raise される。

    Issue #25 の再発防止。force=True で archive_file を呼べばバイパス可能。
    """

    def __init__(self, message: str, referrers: Optional[List[Dict[str, Any]]] = None):
        super().__init__(message)
        self.referrers = referrers or []


_IMPORT_RE_TEMPLATE = (
    # `import foo` / `import foo as f` / `import foo.bar` /
    # `from foo import ...` / `from foo.bar import ...`
    r"(?:^|\n)\s*(?:from\s+{module}(?:\.[A-Za-z_][\w.]*)?\s+import"
    r"|import\s+{module}(?:\.[A-Za-z_]|\s|,|$))"
)


def _list_skill_module_names(skill_dir: Path) -> List[str]:
    """skill ディレクトリの scripts/ 配下から Python モジュール名を抽出する。"""
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.exists() or not scripts_dir.is_dir():
        return []
    names = []
    for py in scripts_dir.rglob("*.py"):
        if py.name.startswith("__"):
            continue
        names.append(py.stem)
    return sorted(set(names))


def _git_grep_files(pattern: str, repo_root: Path) -> Optional[List[str]]:
    """git grep -lP で pattern にマッチするファイルを返す。

    PCRE 構文（`(?:...)` / `\\s` 等）を使うため `-P` を要求する。
    PCRE 非対応の git ビルド・git 未インストール・コマンドエラー時は None を返し、
    呼び出し側に pure-Python フォールバックを促す。
    """
    import subprocess
    try:
        # --untracked: 未 commit の参照（新規追加した import 等）も検出対象に含める
        out = subprocess.run(
            ["git", "grep", "-lP", "--untracked", pattern],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode == 0:
            return [line for line in out.stdout.splitlines() if line.strip()]
        if out.returncode == 1:
            return []
        # PCRE 非対応 / pattern エラー → fallback
        return None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _is_git_repo(repo_root: Path) -> bool:
    """repo_root が git リポジトリか判定。"""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(repo_root),
            capture_output=True,
            timeout=5,
        )
        return out.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _iter_text_files(repo_root: Path):
    """repo 配下のテキストファイルを iterate する（共通ヘルパ）。"""
    skip_dirs = {"__pycache__", ".git", "node_modules", "archive"}
    text_suffixes = {".py", ".sh", ".md", ".json", ".toml", ".yaml", ".yml", ""}
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix not in text_suffixes:
            continue
        yield path


def _python_grep_files_per_module(
    pattern: str, repo_root: Path, module_names: List[str]
) -> Dict[str, List[str]]:
    """alternation pattern で 1 度全 walk し、match した行から module 名を逆引き。

    O(modules * files) を O(files) に圧縮するための最適化（F4）。
    """
    import re
    regex = re.compile(pattern)
    # module_names の重複検出用個別 regex（マッチ後の逆引き）
    per_mod = {m: re.compile(_IMPORT_RE_TEMPLATE.format(module=re.escape(m)))
               for m in module_names}
    result: Dict[str, List[str]] = {m: [] for m in module_names}
    for path in _iter_text_files(repo_root):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if not regex.search(content):
            continue
        rel = str(path.relative_to(repo_root))
        for mod, mod_re in per_mod.items():
            if mod_re.search(content):
                result[mod].append(rel)
    return result


def _python_grep_files(pattern: str, repo_root: Path) -> List[str]:
    """pure-Python フォールバック: 全ファイルから regex 検索。"""
    import re
    regex = re.compile(pattern)
    matches = []
    for path in _iter_text_files(repo_root):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if regex.search(content):
            matches.append(str(path.relative_to(repo_root)))
    return matches


def _is_excluded_referrer(rel_path: str, skill_dir_rel: str) -> bool:
    """除外対象（自身ディレクトリ・__pycache__・archive 配下）か判定。"""
    if rel_path.startswith(skill_dir_rel + "/") or rel_path == skill_dir_rel:
        return True
    parts = Path(rel_path).parts
    if "__pycache__" in parts or "archive" in parts:
        return True
    return False


def check_import_dependencies(
    skill_path: Path, repo_root: Path
) -> List[Dict[str, Any]]:
    """skill ディレクトリの外部依存（import / path ref）を検査する。

    Args:
        skill_path: 検査対象のスキルディレクトリ（または SKILL.md パス）。
        repo_root: リポジトリルート。

    Returns:
        参照元のリスト。各要素 = {"referrer": "rel/path", "kind": "import|path_ref", "match": str}
    """
    skill_dir = Path(skill_path)
    if skill_dir.is_file():
        skill_dir = skill_dir.parent
    repo_root = Path(repo_root).resolve()
    try:
        skill_dir_rel = str(skill_dir.resolve().relative_to(repo_root))
    except ValueError:
        # skill が repo 外
        return []

    skill_name = skill_dir.name
    referrers: List[Dict[str, Any]] = []
    seen: set = set()

    # 1. Python モジュール名から import 文を検索
    module_names = _list_skill_module_names(skill_dir)
    if module_names:
        # git grep は per-module（"match" にどの module を記録）
        # pure-Python は alternation 1 回にまとめて O(modules*files) → O(files) に圧縮
        if _is_git_repo(repo_root):
            for mod in module_names:
                pattern = _IMPORT_RE_TEMPLATE.format(module=mod)
                files = _git_grep_files(pattern, repo_root) or []
                for f in files:
                    if _is_excluded_referrer(f, skill_dir_rel):
                        continue
                    key = (f, "import", mod)
                    if key in seen:
                        continue
                    seen.add(key)
                    referrers.append({"referrer": f, "kind": "import", "match": f"module:{mod}"})
        else:
            import re as _re
            alt = "|".join(_re.escape(m) for m in module_names)
            pattern = _IMPORT_RE_TEMPLATE.format(module=f"(?:{alt})")
            files_per_mod = _python_grep_files_per_module(
                pattern, repo_root, module_names
            )
            for mod, files in files_per_mod.items():
                for f in files:
                    if _is_excluded_referrer(f, skill_dir_rel):
                        continue
                    key = (f, "import", mod)
                    if key in seen:
                        continue
                    seen.add(key)
                    referrers.append({"referrer": f, "kind": "import", "match": f"module:{mod}"})

    # 2. skills/<name>/ パス参照を検索
    path_pattern = f"skills/{skill_name}/"
    files = _git_grep_files(path_pattern, repo_root)
    if files is None:
        # pure-Python: literal contain（_iter_text_files で共通化）
        files = []
        for path in _iter_text_files(repo_root):
            try:
                if path_pattern in path.read_text(encoding="utf-8", errors="ignore"):
                    files.append(str(path.relative_to(repo_root)))
            except OSError:
                continue
    for f in files:
        if _is_excluded_referrer(f, skill_dir_rel):
            continue
        key = (f, "path_ref", path_pattern)
        if key in seen:
            continue
        seen.add(key)
        referrers.append({
            "referrer": f,
            "kind": "path_ref",
            "match": path_pattern,
        })

    return referrers
