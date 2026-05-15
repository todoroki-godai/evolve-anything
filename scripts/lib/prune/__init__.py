#!/usr/bin/env python3
"""淘汰スクリプト。

dead glob・zero invocation・重複の3基準でアーティファクトを検出し、
アーカイブを提案する。直接削除は行わない（MUST NOT）。
"""
import json
import math
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugin_root import PLUGIN_ROOT
_plugin_root = PLUGIN_ROOT
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from frontmatter import extract_description, parse_frontmatter
from similarity import filter_merge_group_pairs
from discover import load_merge_suppression
from skill_usage_stats import find_unused_global_skills, find_rarely_used_global_skills, find_nested_only_skills

from audit import (
    DATA_DIR,
    classify_artifact_origin,
    find_artifacts,
    load_usage_data,
    aggregate_usage,
    load_usage_registry,
    semantic_similarity_check,
)

ARCHIVE_DIR = DATA_DIR / "archive"

# 閾値定数 + evolve-state.json ロードは prune/config.py に集約済み（後方互換のため再エクスポート）
from .config import (  # noqa: E402, F401
    DEFAULT_DECAY_DAYS,
    DEFAULT_DECAY_THRESHOLD,
    CORRECTION_PENALTY,
    ZERO_INVOCATION_DAYS,
    DEFAULT_MERGE_SIMILARITY_THRESHOLD,
    DEFAULT_INTERACTIVE_MERGE_THRESHOLD,
    DEFAULT_DRIFT_THRESHOLD,
    load_merge_similarity_threshold,
    load_interactive_merge_threshold,
    load_decay_threshold,
    load_drift_threshold,
)


# スキル frontmatter 解析 + 推薦ラベルは prune/skill_inspect.py に集約済み（後方互換のため再エクスポート）
from .skill_inspect import (  # noqa: E402, F401
    _ARCHIVE_KEYWORDS,
    _KEEP_KEYWORDS,
    _KEEP_TRIGGER_THRESHOLD,
    _count_triggers,
    _enrich_candidate,
    extract_skill_summary,
    suggest_recommendation,
)


# corrections.jsonl の読み込み / decay-based クリーンアップは prune/corrections.py に集約済み（後方互換のため再エクスポート）
from .corrections import (  # noqa: E402, F401
    load_corrections,
    cleanup_corrections,
)


# 参照型判定 + 推定キャッシュ + 減衰スコア / pin は prune/skill_inspect.py に集約済み（後方互換のため再エクスポート）
from .skill_inspect import (  # noqa: E402, F401
    _load_skill_type_cache,
    _save_skill_type_cache,
    _resolve_skill_md,
    is_reference_skill,
    _estimate_skill_type,
    compute_decay_score,
    is_pinned,
)



# dead glob / zero invocation / global safe / duplicate / decay 検出は prune/detection.py に集約済み（後方互換のため再エクスポート）
from .detection import (  # noqa: E402, F401
    _expand_glob_pattern,
    detect_dead_globs,
    detect_zero_invocations,
    safe_global_check,
    detect_duplicates,
    detect_decay_candidates,
)




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


# _is_skill_dir は prune/skill_inspect.py に集約済み（後方互換のため再エクスポート）
from .skill_inspect import _is_skill_dir  # noqa: E402, F401


def archive_file(
    filepath: str,
    reason: str,
    *,
    force: bool = False,
    repo_root: Optional[Path] = None,
) -> Optional[str]:
    """ファイルをアーカイブする。直接削除は行わない（MUST NOT）。

    skill ディレクトリ全体（`skills/<name>`）を archive する場合は
    `check_import_dependencies` で外部依存を検査し、参照ありなら
    `SkillDependencyError` を raise する（Issue #25 対策）。

    Args:
        filepath: archive 対象のパス。
        reason: archive 理由（メタデータに記録）。
        force: True なら依存検査結果を警告のみで進める。
        repo_root: リポジトリルート。未指定なら filepath から自動推定。
    """
    src = Path(filepath)
    if not src.exists():
        return None

    # skill ディレクトリ archive の場合は依存検査
    if _is_skill_dir(src):
        # repo_root 推定: src/.../skills/<name> → ../../
        if repo_root is None:
            repo_root = src.parent.parent
        deps = check_import_dependencies(src, Path(repo_root))
        if deps:
            msg = (
                f"skill '{src.name}' has {len(deps)} external dependency(ies): "
                + ", ".join(f"{d['referrer']}({d['kind']})" for d in deps[:5])
                + (" ..." if len(deps) > 5 else "")
                + ". 依存断ち切り PR を先行させてから再実行してください。"
                + " 同名 module を持つ別 skill による誤検出の可能性がある場合のみ"
                + " archive_file(..., force=True) でバイパス可能。"
            )
            if not force:
                raise SkillDependencyError(msg, referrers=deps)
            else:
                print(f"[prune] WARNING: {msg} (force=True, archiving anyway)", file=sys.stderr)

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # タイムスタンプ付きでアーカイブ
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = ARCHIVE_DIR / f"{timestamp}_{src.name}"
    shutil.move(str(src), str(dest))

    # メタデータを保存
    meta = {
        "original_path": str(src),
        "archive_path": str(dest),
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    meta_file = dest.with_suffix(dest.suffix + ".meta.json")
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    return str(dest)


def restore_file(archive_path: str) -> Optional[str]:
    """アーカイブからファイルを復元する。"""
    arch = Path(archive_path)
    if not arch.exists():
        return None

    # メタデータから元のパスを取得
    meta_file = arch.with_suffix(arch.suffix + ".meta.json")
    if not meta_file.exists():
        return None

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    original_path = meta.get("original_path")
    if not original_path:
        return None

    dest = Path(original_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(arch), str(dest))
    meta_file.unlink()

    return str(dest)


def list_archive() -> List[Dict[str, Any]]:
    """アーカイブの一覧を取得する。"""
    if not ARCHIVE_DIR.exists():
        return []

    items = []
    for meta_file in ARCHIVE_DIR.glob("*.meta.json"):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            archive_file_path = str(meta_file).replace(".meta.json", "")
            meta["archive_file"] = archive_file_path
            meta["exists"] = Path(archive_file_path).exists()
            items.append(meta)
        except (json.JSONDecodeError, OSError):
            continue

    return sorted(items, key=lambda x: x.get("timestamp", ""), reverse=True)




def determine_primary(skill_a: str, skill_b: str) -> tuple:
    """どちらのスキルが primary（使用頻度が高い）かを判定する。

    Returns:
        (primary_path, secondary_path, primary_count, secondary_count)
    """
    usage_records = load_usage_data()
    usage_counts = aggregate_usage(usage_records)

    count_a = usage_counts.get(skill_a, 0)
    count_b = usage_counts.get(skill_b, 0)

    if count_a > count_b:
        return (skill_a, skill_b, count_a, count_b)
    elif count_b > count_a:
        return (skill_b, skill_a, count_b, count_a)
    else:
        # 同数の場合はアルファベット順（早い方が primary）
        if skill_a <= skill_b:
            return (skill_a, skill_b, count_a, count_b)
        else:
            return (skill_b, skill_a, count_b, count_a)


def merge_duplicates(
    duplicate_candidates: List[Dict[str, Any]],
    reorganize_merge_groups: Optional[List[Dict[str, Any]]] = None,
    project_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """重複候補とreorganizeマージグループを統合し、マージ提案を生成する。

    Args:
        duplicate_candidates: detect_duplicates() の結果。各要素は path_a, path_b を持つ。
        reorganize_merge_groups: reorganize フェーズからのマージグループ。
            各要素は skills: [name_list] を持つ。
        project_dir: プロジェクトディレクトリ。

    Returns:
        merge_proposals と total_proposals を含む辞書。
    """
    proj = Path(project_dir) if project_dir else Path.cwd()
    artifacts = find_artifacts(proj)

    # スキル名→パスのマッピングを構築
    skill_path_map: Dict[str, str] = {}
    for path in artifacts.get("skills", []):
        skill_name = path.parent.name
        skill_path_map[skill_name] = str(path)

    # 重複ペアを frozenset で収集（重複排除）
    pairs: set = set()

    # duplicate_candidates からペアを抽出
    for item in duplicate_candidates:
        path_a = item.get("path_a", "")
        path_b = item.get("path_b", "")
        # パスからスキル名を取得
        name_a = Path(path_a).parent.name if path_a else ""
        name_b = Path(path_b).parent.name if path_b else ""
        if name_a and name_b and name_a != name_b:
            pairs.add(frozenset([name_a, name_b]))

    # reorganize_merge_groups からペアを抽出（類似度フィルタ適用）
    reorg_filtered_pairs: set = set()
    reorg_skipped_pairs: set = set()
    reorg_interactive_pairs: list = []  # (frozenset, score) のリスト
    if reorganize_merge_groups:
        merge_sim_threshold = load_merge_similarity_threshold()
        interactive_threshold = load_interactive_merge_threshold()
        for group in reorganize_merge_groups:
            group_skills = group.get("skills", [])
            if len(group_skills) < 2:
                continue
            # ペア単位の類似度フィルタ（新シグネチャ: タプル返却）
            passed, interactive = filter_merge_group_pairs(
                group_skills, skill_path_map,
                threshold=merge_sim_threshold,
                interactive_threshold=interactive_threshold,
            )
            passed_set = set(passed)
            reorg_filtered_pairs.update(passed_set)
            reorg_interactive_pairs.extend(interactive)
            # フィルタで除外されたペアを記録（interactive 範囲も除く）
            from itertools import combinations
            all_group_pairs = {frozenset(p) for p in combinations(group_skills, 2)}
            interactive_set = {pair for pair, _score in interactive}
            reorg_skipped_pairs.update(all_group_pairs - passed_set - interactive_set)
        pairs.update(reorg_filtered_pairs)

    # merge suppression セットをループ外で1回だけロード
    suppressed_pairs = load_merge_suppression()

    # 各ペアに対してマージ提案を生成
    merge_proposals: List[Dict[str, Any]] = []
    for pair in sorted(pairs, key=lambda p: sorted(p)):
        names = sorted(pair)
        skill_a, skill_b = names[0], names[1]

        path_a_str = skill_path_map.get(skill_a, "")
        path_b_str = skill_path_map.get(skill_b, "")

        # .pin チェック
        if (path_a_str and is_pinned(Path(path_a_str))) or (
            path_b_str and is_pinned(Path(path_b_str))
        ):
            merge_proposals.append({
                "primary": {"path": path_a_str, "skill_name": skill_a, "usage_count": 0},
                "secondary": {"path": path_b_str, "skill_name": skill_b, "usage_count": 0},
                "status": "skipped_pinned",
            })
            continue

        # プラグイン由来チェック
        origin_a = classify_artifact_origin(Path(path_a_str)) if path_a_str else "custom"
        origin_b = classify_artifact_origin(Path(path_b_str)) if path_b_str else "custom"
        if origin_a == "plugin" or origin_b == "plugin":
            merge_proposals.append({
                "primary": {"path": path_a_str, "skill_name": skill_a, "usage_count": 0},
                "secondary": {"path": path_b_str, "skill_name": skill_b, "usage_count": 0},
                "status": "skipped_plugin",
            })
            continue

        # merge suppression チェック
        pair_key = "::".join([skill_a, skill_b])  # 既にソート済み
        if pair_key in suppressed_pairs:
            merge_proposals.append({
                "primary": {"path": path_a_str, "skill_name": skill_a, "usage_count": 0},
                "secondary": {"path": path_b_str, "skill_name": skill_b, "usage_count": 0},
                "status": "skipped_suppressed",
            })
            continue

        # primary/secondary を判定
        primary_name, secondary_name, primary_count, secondary_count = determine_primary(
            skill_a, skill_b
        )
        merge_proposals.append({
            "primary": {
                "path": skill_path_map.get(primary_name, ""),
                "skill_name": primary_name,
                "usage_count": primary_count,
            },
            "secondary": {
                "path": skill_path_map.get(secondary_name, ""),
                "skill_name": secondary_name,
                "usage_count": secondary_count,
            },
            "status": "proposed",
        })

    # reorganize 由来の interactive candidate を追加
    # (pin/plugin/suppression チェック済みペアのみ、duplicate_candidates 経由で既に pairs に含まれているものは除く)
    for pair, score in sorted(reorg_interactive_pairs, key=lambda x: -x[1]):
        if pair in pairs:
            continue
        names = sorted(pair)
        skill_a, skill_b = names[0], names[1]
        path_a_str = skill_path_map.get(skill_a, "")
        path_b_str = skill_path_map.get(skill_b, "")

        # pin/plugin/suppression チェック
        if (path_a_str and is_pinned(Path(path_a_str))) or (
            path_b_str and is_pinned(Path(path_b_str))
        ):
            merge_proposals.append({
                "primary": {"path": path_a_str, "skill_name": skill_a, "usage_count": 0},
                "secondary": {"path": path_b_str, "skill_name": skill_b, "usage_count": 0},
                "status": "skipped_pinned",
            })
            continue

        origin_a = classify_artifact_origin(Path(path_a_str)) if path_a_str else "custom"
        origin_b = classify_artifact_origin(Path(path_b_str)) if path_b_str else "custom"
        if origin_a == "plugin" or origin_b == "plugin":
            merge_proposals.append({
                "primary": {"path": path_a_str, "skill_name": skill_a, "usage_count": 0},
                "secondary": {"path": path_b_str, "skill_name": skill_b, "usage_count": 0},
                "status": "skipped_plugin",
            })
            continue

        pair_key = "::".join([skill_a, skill_b])
        if pair_key in suppressed_pairs:
            merge_proposals.append({
                "primary": {"path": path_a_str, "skill_name": skill_a, "usage_count": 0},
                "secondary": {"path": path_b_str, "skill_name": skill_b, "usage_count": 0},
                "status": "skipped_suppressed",
            })
            continue

        primary_name, secondary_name, primary_count, secondary_count = determine_primary(
            skill_a, skill_b
        )
        merge_proposals.append({
            "primary": {
                "path": skill_path_map.get(primary_name, ""),
                "skill_name": primary_name,
                "usage_count": primary_count,
            },
            "secondary": {
                "path": skill_path_map.get(secondary_name, ""),
                "skill_name": secondary_name,
                "usage_count": secondary_count,
            },
            "similarity_score": score,
            "status": "interactive_candidate",
        })

    # reorganize 由来でフィルタ除外されたペアを skipped_low_similarity で追加
    # (duplicate_candidates 経由で既に pairs に含まれているものは除く)
    for pair in sorted(reorg_skipped_pairs - pairs, key=lambda p: sorted(p)):
        names = sorted(pair)
        skill_a, skill_b = names[0], names[1]
        path_a_str = skill_path_map.get(skill_a, "")
        path_b_str = skill_path_map.get(skill_b, "")
        merge_proposals.append({
            "primary": {"path": path_a_str, "skill_name": skill_a, "usage_count": 0},
            "secondary": {"path": path_b_str, "skill_name": skill_b, "usage_count": 0},
            "status": "skipped_low_similarity",
        })

    return {
        "merge_proposals": merge_proposals,
        "total_proposals": len(merge_proposals),
    }


def _gather_drift_context(skill_path: Path, project_dir: Path) -> str:
    """ドリフト評価用のコンテキストを収集する。

    CLAUDE.md、rules、スキル内容から関連ファイルのコンテキストをまとめる。
    """
    context_parts = []

    # スキル内容
    resolved = _resolve_skill_md(skill_path)
    if resolved.exists():
        context_parts.append(f"=== Skill Content ({resolved.name}) ===\n{resolved.read_text(encoding='utf-8')}")

    # CLAUDE.md
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        context_parts.append(f"=== CLAUDE.md ===\n{claude_md.read_text(encoding='utf-8')}")

    # rules
    rules_dir = project_dir / ".claude" / "rules"
    if rules_dir.exists():
        for rule_file in sorted(rules_dir.glob("*.md"))[:10]:
            context_parts.append(f"=== Rule: {rule_file.name} ===\n{rule_file.read_text(encoding='utf-8')}")

    return "\n\n".join(context_parts)


def detect_reference_drift(
    artifacts: Dict[str, List[Path]],
    project_dir: Path,
) -> List[Dict[str, Any]]:
    """参照型スキルの内容とコードベースの乖離度を評価し、ドリフト候補を返す。

    サブエージェント呼び出しで乖離度を 0.0〜1.0 で評価する。
    サブエージェント失敗時はそのスキルを候補に含めない。
    非参照型スキルは評価しない。
    """
    threshold = load_drift_threshold()
    candidates = []

    for path in artifacts.get("skills", []):
        # 参照型スキルのみ対象
        if not is_reference_skill(path):
            continue

        skill_name = path.parent.name
        try:
            context = _gather_drift_context(path, project_dir)
            # サブエージェントでドリフト評価
            # 実際の実行時は Agent tool で LLM 評価を行う
            # ここではコンテキスト収集までを行い、スコアは呼び出し側で設定
            drift_result = _evaluate_drift(context, skill_name)
            if drift_result and drift_result.get("drift_score", 0) >= threshold:
                candidates.append({
                    "file": str(path),
                    "skill_name": skill_name,
                    "reason": "reference_drift",
                    "drift_score": drift_result["drift_score"],
                    "drift_reason": drift_result.get("drift_reason", ""),
                })
        except Exception as e:
            # サブエージェント失敗時は候補に含めない（安全側倒し）
            print(f"[prune] drift evaluation failed for {skill_name}: {e}", file=sys.stderr)
            continue

    return candidates


def _evaluate_drift(context: str, skill_name: str) -> Optional[Dict[str, Any]]:
    """ドリフト評価のプレースホルダ。

    実際の prune スキル実行時は Agent tool のサブエージェントで
    コンテキストを評価し、drift_score と drift_reason を返す。
    ここではテスト用にデフォルト値を返す。
    """
    # プレースホルダ実装: 実運用時はサブエージェントで置換
    return {"drift_score": 0.0, "drift_reason": ""}


def run_prune(
    project_dir: Optional[str] = None,
    reorganize_merge_groups: Optional[list] = None,
) -> Dict[str, Any]:
    """Prune を実行して候補を返す。"""
    proj = Path(project_dir) if project_dir else Path.cwd()
    artifacts = find_artifacts(proj)

    zero_invocations, plugin_unused = detect_zero_invocations(artifacts)

    candidates = {
        "dead_globs": detect_dead_globs(proj),
        "zero_invocations": zero_invocations,
        "plugin_unused": plugin_unused,
        "global_candidates": safe_global_check(artifacts),
        "duplicate_candidates": detect_duplicates(artifacts),
        "decay_candidates": detect_decay_candidates(artifacts),
        "reference_drift_candidates": detect_reference_drift(artifacts, proj),
    }

    total = sum(len(v) for v in candidates.values() if isinstance(v, list))
    candidates["total_candidates"] = total

    # rules は淘汰対象外。情報提供のみ。
    rules = artifacts.get("rules", [])
    candidates["rules_info"] = [
        {"name": p.stem, "scope": "global" if ".claude/rules" in str(p) and "projects" not in str(p) else "project"}
        for p in rules
    ]

    # corrections.jsonl クリーンアップ
    cleanup_result = cleanup_corrections()
    candidates["corrections_cleanup"] = cleanup_result

    # マージ提案を生成
    merge_result = merge_duplicates(
        candidates["duplicate_candidates"],
        reorganize_merge_groups=reorganize_merge_groups,
        project_dir=project_dir,
    )
    candidates["merge_result"] = merge_result

    return candidates


if __name__ == "__main__":
    import sys

    project = sys.argv[1] if len(sys.argv) > 1 else None
    result = run_prune(project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
