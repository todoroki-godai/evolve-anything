"""archive 操作 + 重複マージ提案 (旧 prune.py 由来)。

prune/__init__.py から re-export される（後方互換）。
ARCHIVE_DIR と filter_merge_group_pairs は package 経由で遅延参照する
（テスト monkeypatch.setattr(prune, "ARCHIVE_DIR", ...) /
 mock.patch("prune.filter_merge_group_pairs", ...) 追従）。
"""
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from audit import (
    aggregate_usage,
    classify_artifact_origin,
    find_artifacts,
    load_usage_data,
)
from discover import load_merge_suppression

from .config import (
    load_interactive_merge_threshold,
    load_merge_similarity_threshold,
)
from .dependency import (
    SkillDependencyError,
    check_import_dependencies,
)
from .skill_inspect import _is_skill_dir, is_pinned


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
    from . import ARCHIVE_DIR  # noqa: PLC0415  monkeypatch 追従

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

    # タイムスタンプ付きでアーカイブ（tz-aware・UTC 基準。ISO8601 辞書順比較罠 #79 の温床解消）
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
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
    from . import ARCHIVE_DIR  # noqa: PLC0415  monkeypatch 追従

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
    # mock.patch("prune.filter_merge_group_pairs", ...) 追従のため package 経由で参照
    from . import filter_merge_group_pairs  # noqa: PLC0415

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
