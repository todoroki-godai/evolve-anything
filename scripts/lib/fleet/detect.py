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

from pj_slug import (  # noqa: E402
    pj_id_to_path,
    pj_id_to_slug,
    record_project_attribution,
    resolve_pj_slug,
)
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


def _select_transcripts(
    dirs: List[Path], max_files: int
) -> tuple[List[Path], List[Dict[str, str]]]:
    """1 slug の全 dir を合算してから mtime 降順で上限を掛ける（#345）。

    ``max_transcripts`` は「PJ ごとの上限」と説明されているのに、dir ごとに適用すると
    実効上限が ``max_files × dir 数`` になる。worktree を多く持つ PJ では daily の走査量が
    事実上無制限化するため、選択を slug 単位に集約する。同 mtime のタイブレークはパス文字列で
    固定して決定論にする。

    Returns:
        ``(選択された transcript, dir 単位の列挙エラー [{"dir", "error"}...])``

    列挙エラーを無記録で ``continue`` すると、その dir には空の transcript リストが渡って
    ``collect_signals`` は正常終了し、**1 件も読めていない PJ が成功として数えられる**
    （権限喪失が failed_dirs / failed_projects / 終了コード / daily ログの全経路で健全に
    見える沈黙モード）。呼び出し側が failed_dirs へ畳めるよう理由付きで返す。
    """
    if max_files <= 0:
        return [], []
    files: List[Path] = []
    errors: List[Dict[str, str]] = []
    for d in dirs:
        try:
            files.extend(p for p in d.glob("*.jsonl") if p.is_file())
        except OSError as exc:
            errors.append({"dir": str(d), "error": f"{type(exc).__name__}: {exc}"})
            continue
    def _key(p: Path):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (-mtime, str(p))
    files.sort(key=_key)
    return files[:max_files], errors


def _count_unattributed_denies(errors_rows: List[Dict[str, Any]]) -> int:
    """PJ 帰属を持たない permission_denied 行の件数（#312 の除外カウント）。

    strict 判定で落とした件数を観測するための決定論カウンタ。silence != evaluated なので
    0 件でも結果に必ず載せる。
    """
    return sum(
        1 for rec in errors_rows
        if isinstance(rec, dict)
        and rec.get("type") == "permission_denied"
        and record_project_attribution(rec) is None
    )


def detect_exit_code(result: Dict[str, Any]) -> int:
    """detect 結果 → プロセス終了コード（#313）。

    fail-open 方針（1 PJ の失敗で全体を止めない）は維持しつつ、**全滅は非 zero** で返す。
    #304 で塞いだのは「生ログは毎日更新されるのに素材だけ止まる」沈黙モードなので、
    detect を daily runner に配線したあと権限エラー等で全 PJ が落ちれば同じ沈黙が再発する。
    そのとき「毎朝走っているから大丈夫」と誤認しないよう、全滅とソース読み込み失敗だけを
    非 zero にする（部分失敗は 0 のまま = 観測のみ）。
    """
    if result.get("source_errors"):
        return 1
    failed = len(result.get("failed_projects") or [])
    # 成功 0 件 かつ 失敗あり = 全滅（対象 0 件の no-op と区別する）。
    if failed and int(result.get("projects", 0)) == 0:
        return 1
    return 0


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
        max_transcripts: **PJ（slug）ごと**の transcript 上限（backfill は大きくする）。
                       1 slug が複数 dir（本体 + worktree）を持つ場合も合算してから
                       上限を掛ける（#345）。非正値は走査なしに畳む
        dry_run:       ``True`` ならストアに一切触れない（#491 invariant）
        only:          対象 pj_slug の絞り込み
        fs_root:       pj_id → 実パス復元の探索起点（テスト用。default ``/``）

    Returns:
        {"projects": 成功 PJ 数, "written": int, "skipped_dup": int, "total": int,
         "dry_run": bool, "per_pj": [{"pj_slug", "detected", "written",
         "skipped_dup", "total"}...],
         "failed_dirs": [{"pj_slug", "dir", "error"}...],
         "failed_projects": [{"pj_slug", "errors"}...]（全 dir が失敗した PJ）,
         "degraded_projects": [{"pj_slug", "errors"}...]（一部 dir だけ失敗した PJ）,
         "source_errors": [str...]（errors.jsonl 等の重大ソース失敗）,
         "unattributed_deny": int（PJ 帰属を持たず除外した deny 行数・#312）}
    """
    root = Path(projects_root) if projects_root else Path.home() / ".claude" / "projects"
    agg: Dict[str, Any] = {
        "projects": 0, "written": 0, "skipped_dup": 0, "total": 0,
        "dry_run": dry_run, "per_pj": [],
        "failed_dirs": [], "failed_projects": [], "degraded_projects": [],
        "source_errors": [], "unattributed_deny": 0,
    }
    if not root.is_dir():
        return agg

    # errors.jsonl は 1 回だけパースして全 PJ で共有する（7MB 級を PJ 数ぶん読まない）。
    if errors_path is None:
        from rl_common import hook_store_path
        import rl_common as _rc

        errors_path = hook_store_path("errors.jsonl", base=_rc.DATA_DIR)
    errors_path = Path(errors_path)
    # ``_read_errors`` は OSError を握って [] を返す（fail-open）。それだと「deny が 0 件」と
    # 「ソースが読めない」が同じ見た目になるので、読めるかどうかだけ先に確かめて surface する。
    if errors_path.exists():
        try:
            with open(errors_path, "rb"):
                pass
        except OSError as exc:
            agg["source_errors"].append(f"errors.jsonl: {exc}")
    errors_rows = _read_errors(errors_path)
    agg["unattributed_deny"] = _count_unattributed_denies(errors_rows)

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
        # 上限は PJ 単位。全 dir の transcript を合算してから mtime 降順で上位 N を採る
        # （dir ごとに掛けると実効上限が dir 数倍になる・#345）。
        selected, enum_errors = _select_transcripts(dirs, max_transcripts)
        signals = []
        dir_errors: List[str] = []
        # 列挙できなかった dir は「読めていない」ので、素通りさせず先に失敗として記録する。
        enum_failed = {e["dir"] for e in enum_errors}
        for e in enum_errors:
            dir_errors.append(e["error"])
            agg["failed_dirs"].append(
                {"pj_slug": slug, "dir": e["dir"], "error": e["error"]}
            )
            if progress:
                sys.stderr.write(
                    f"[fleet:detect] {slug}: transcript 列挙失敗 ({e['error']})\n"
                )
        # deny は PJ 単位で1回だけ渡す。列挙失敗 dir を飛ばすので enumerate の index ではなく
        # 「実際に処理した回数」で判定する（先頭 dir が落ちても deny を取りこぼさない）。
        processed = 0
        for pj_dir in dirs:
            if str(pj_dir) in enum_failed:
                continue
            try:
                signals.extend(collect_signals(
                    slug,
                    projects_root=root,
                    errors=errors_rows if processed == 0 else [],
                    utterances=utterances,
                    pj_dir=pj_dir,
                    transcripts=[p for p in selected if p.parent == pj_dir],
                    strict_attribution=True,  # fan-out なので未帰属は誤帰属になる（#312）
                ))
            except Exception as exc:  # 1 dir の失敗で全体を止めない（daily は fail-open）
                detail = f"{type(exc).__name__}: {exc}"
                dir_errors.append(detail)
                agg["failed_dirs"].append(
                    {"pj_slug": slug, "dir": str(pj_dir), "error": detail}
                )
                if progress:
                    sys.stderr.write(f"[fleet:detect] {slug}: skipped ({exc})\n")
                continue
            processed += 1

        if dir_errors and len(dir_errors) == len(dirs):
            # 全 dir 失敗 = この PJ は検出できていない。成功件数に混ぜると
            # 「毎朝走っているから大丈夫」と誤認する（#313）。
            agg["failed_projects"].append({"pj_slug": slug, "errors": dir_errors})
            if progress:
                sys.stderr.write(f"[{i}/{total_slugs}] {slug}: FAILED\n")
            continue
        if dir_errors:
            # 一部 dir だけ失敗（本体 OK・worktree NG が典型）。fail-open のまま成功に数えるが、
            # failed_projects だけを見ていると daily ログで完全に沈黙するので別レーンで surface。
            agg["degraded_projects"].append({"pj_slug": slug, "errors": dir_errors})

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
