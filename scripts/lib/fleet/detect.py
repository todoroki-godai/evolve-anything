"""全 PJ 横断の決定論 weak_signals 検出（#304）。

**なぜ必要か（根因）**: 決定論 weak_signals の永続化は ``evolve --drain`` の apply 境界に
しか配線されていない（#484/#513）。daily runner（``evolve-daily-run``）が回すのは
``fleet ingest`` → ``fleet tokens`` → ``fleet queue`` の3ステップだけで検出を含まないため、

1. evolve を回さない → 学習素材が 1 件も生まれない
2. ``fleet queue`` は素材量で待ちを判定するので ``queue_status=EMPTY``
3. 「evolve 待ちなし」と表示 → ユーザーは evolve を回さない → 1 へ

という鶏卵ループになる。とくに一度も evolve していない PJ は素材が作られる機会自体が無く、
queue から永久に発見されない。本 module は検出を evolve から切り離し、
**evolve を回さなくても素材が自動で溜まる**ようにする（決定論・ゼロ LLM・冪等）。

**母集団**: fleet config の tracked PJ ではなく ``~/.claude/projects`` の実 transcript dir。
tracked に載っていない PJ こそ素材が必要なので、実際に対話が記録されている dir を全部見る。

**slug の名前空間**: read 側（``fleet queue`` / reflect）と揃えるため ``resolve_pj_slug``
を通す。worktree の transcript dir をそのまま slug にすると幻 slug ができて当該 PJ の
素材として数えられない（pitfall_worktree_slug_show_toplevel と同型）ため、
``pj_id_to_path`` で実パスへ戻してから解決する。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from pj_slug import pj_id_to_path, pj_id_to_slug, resolve_pj_slug  # noqa: E402
from weak_signals.batch import (  # noqa: E402
    DEFAULT_MAX_TRANSCRIPTS,
    _read_errors,
    channel_counts,
    collect_signals,
)
from weak_signals.store import append_signals  # noqa: E402

# backfill（過去チャットの取りこぼし回収）向けの既定上限。
# 通常運用の DEFAULT_MAX_TRANSCRIPTS(60) は「直近セッション」を想定した値なので、
# 遡って拾うときはこちらを使う。
BACKFILL_MAX_TRANSCRIPTS = 2000

# CC の encoded dir 名に現れる worktree 区切り（`/.claude-worktrees/` の `.` が落ちた形）。
_WORKTREE_MARKER = "--claude-worktrees-"


def _slug_for_project_dir(pj_dir: Path, fs_root: Optional[Path]) -> str:
    """transcript dir 名 → read 側と同じ名前空間の pj_slug。

    worktree dir は本体 repo の slug に寄せる（``resolve_pj_slug`` が git-common-dir の
    親を見るため）。実パスに復元できない dir（削除済み PJ・改名）は貪欲復元の
    fallback slug をそのまま使い、無音で落とさない。
    """
    real = pj_id_to_path(pj_dir.name, root=fs_root)
    if real is not None:
        try:
            return resolve_pj_slug(real)
        except Exception:
            pass
    # 撤去済み worktree は実パス復元が効かず、dir 名そのままだと
    # `rl-anything--claude-worktrees-feedback` のような幻 slug になる。worktree 区切りより
    # 前（本体 repo 側）で解決し直し、素材を本体 PJ に寄せる。
    name = pj_dir.name
    if _WORKTREE_MARKER in name:
        head = name.split(_WORKTREE_MARKER, 1)[0]
        head_path = pj_id_to_path(head, root=fs_root)
        if head_path is not None:
            try:
                return resolve_pj_slug(head_path)
            except Exception:
                pass
        return pj_id_to_slug(head, root=fs_root)
    return pj_id_to_slug(name, root=fs_root)


def detect_all_projects(
    *,
    projects_root: Optional[Path] = None,
    store_path: Optional[Path] = None,
    errors_path: Optional[Path] = None,
    utterances: Optional[List[Dict[str, Any]]] = None,
    max_transcripts: int = DEFAULT_MAX_TRANSCRIPTS,
    dry_run: bool = False,
    only: Optional[List[str]] = None,
    fs_root: Optional[Path] = None,
    progress: bool = True,
) -> Dict[str, Any]:
    """全 PJ の決定論 weak_signals を検出し永続化する（冪等・ゼロ LLM）。

    Args:
        projects_root: transcript ルート（default ``~/.claude/projects``）
        store_path:    weak_signals.jsonl（default は正準 DATA_DIR 解決）
        errors_path:   errors.jsonl（default は hook store 解決）。1 回だけ読んで
                       全 PJ に使い回す（PJ 数ぶん再パースしない）
        utterances:    rephrase チャネル用の発話行。``None`` なら PJ ごとに
                       utterances.db を引く。``[]`` を渡すと DB 非依存で回せる
        max_transcripts: PJ ごとの transcript 上限（backfill は大きくする）
        dry_run:       ``True`` ならストアに一切触れない（#491 invariant）
        only:          対象 pj_slug の絞り込み
        fs_root:       pj_id → 実パス復元の探索起点（テスト用。default ``/``）

    Returns:
        {"projects": int, "written": int, "skipped_dup": int, "total": int,
         "dry_run": bool, "per_pj": [{"pj_slug", "detected", "written",
         "skipped_dup", "total"}...]}
    """
    root = Path(projects_root) if projects_root else Path.home() / ".claude" / "projects"
    agg: Dict[str, Any] = {
        "projects": 0, "written": 0, "skipped_dup": 0, "total": 0,
        "dry_run": dry_run, "per_pj": [],
    }
    if not root.is_dir():
        return agg

    # errors.jsonl は 1 回だけパースして全 PJ で共有する（7MB 級を PJ 数ぶん読まない）。
    if errors_path is None:
        from rl_common import hook_store_path
        import rl_common as _rc

        errors_path = hook_store_path("errors.jsonl", base=_rc.DATA_DIR)
    errors_rows = _read_errors(Path(errors_path))

    pj_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    # 同一 slug に複数 dir（本体 + worktree）が対応しうるので slug 単位に束ねる。
    by_slug: Dict[str, List[Path]] = {}
    for pj_dir in pj_dirs:
        slug = _slug_for_project_dir(pj_dir, fs_root)
        if only and slug not in only:
            continue
        by_slug.setdefault(slug, []).append(pj_dir)

    total_slugs = len(by_slug)
    for i, (slug, dirs) in enumerate(sorted(by_slug.items()), 1):
        # 1 slug に複数 dir（本体 + worktree）が対応しうるので、**全 dir 分を集めてから
        # 1 回だけ書く**。dir ごとに append すると dry-run では毎回「未書込のストア」と
        # 突合するため、同一シグナルを dir 数ぶん重複計上してしまう（dry-run と実書込の
        # 件数が食い違う = 観測が嘘をつく）。
        signals = []
        for idx, pj_dir in enumerate(dirs):
            try:
                signals.extend(collect_signals(
                    slug,
                    projects_root=root,
                    errors=errors_rows if idx == 0 else [],  # deny は PJ 単位で1回
                    utterances=utterances,
                    max_transcripts=max_transcripts,
                    pj_dir=pj_dir,
                ))
            except Exception as exc:  # 1 dir の失敗で全体を止めない（daily は fail-open）
                if progress:
                    sys.stderr.write(f"[fleet:detect] {slug}: skipped ({exc})\n")
                continue
        detected = channel_counts(signals)
        total = len(signals)
        write_res = append_signals(signals, path=store_path, dry_run=dry_run)
        written = write_res["written"]
        skipped = write_res["skipped_dup"]

        agg["projects"] += 1
        agg["written"] += written
        agg["skipped_dup"] += skipped
        agg["total"] += total
        agg["per_pj"].append({
            "pj_slug": slug, "detected": detected, "written": written,
            "skipped_dup": skipped, "total": total,
        })
        if progress:
            sys.stderr.write(
                f"[{i}/{total_slugs}] {slug}: detected={total} written={written} "
                f"dup={skipped}\n"
            )
    return agg
