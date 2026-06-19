"""weak_signals.batch — 検出オーケストレーション（#432）。

evolve/audit の非 dry-run 経路から呼ばれるバッチ層。hot path（hooks）には何も足さない。

責務:
- 4 チャネルの検出器を実データソースに対して走らせる（errors.jsonl / transcript / utterances.db）
- 検出結果を dedup して weak_signals.jsonl に追記（dry-run は書き込みゼロ）
- チャネル別の検出件数サマリを返す（audit の advisory surface 用）

dry-run ゲート貫通（pitfall_dryrun_stateful_store_write）: ``dry_run=True`` のとき
**検出は走るが store_path への書き込みは一切しない**（append_signals が最下層で弾く）。
これにより「dry-run で検出件数を観測 + 書き込みゼロ」を両立する。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .detectors import (
    detect_permission_deny,
    detect_rephrase,
    detect_transcript_signals,
)
from .store import WeakSignal, append_signals

# 1 バッチで走査する transcript の上限（bench 設計 pitfall: 全量実走を避ける）。
# 直近セッションに絞る（mtime 降順で上位 N）。
DEFAULT_MAX_TRANSCRIPTS = 60


def _project_dir_for_slug(projects_root: Path, pj_slug: str) -> Optional[Path]:
    """pj_slug に対応する ~/.claude/projects/<encoded> ディレクトリを探す。

    encoded dir 名は basename がそのまま slug に一致しないため、各 transcript の cwd
    から導いた slug でなく、encoded 名の末尾一致で粗く照合する。見つからなければ None。
    """
    if not projects_root.is_dir():
        return None
    for d in projects_root.iterdir():
        if not d.is_dir():
            continue
        # encoded 名は `-Users-...-<repo>` 形式。slug が末尾に現れるかで照合。
        if d.name.endswith(pj_slug) or d.name.endswith(pj_slug.replace("-", "")):
            return d
    return None


def _recent_transcripts(pj_dir: Path, max_files: int) -> List[Path]:
    files = [p for p in pj_dir.glob("*.jsonl") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:max_files]


def _read_errors(errors_path: Path) -> List[Dict[str, Any]]:
    if not errors_path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(errors_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        return []
    return out


def collect_signals(
    pj_slug: str,
    *,
    projects_root: Optional[Path] = None,
    errors_path: Optional[Path] = None,
    utterances: Optional[List[Dict[str, Any]]] = None,
    max_transcripts: int = DEFAULT_MAX_TRANSCRIPTS,
) -> List[WeakSignal]:
    """4 チャネルを全部走らせて WeakSignal のフラットなリストを返す（書き込みなし）。

    各データソースは引数で注入できる（テストは tmp ファイル / 合成データを渡す）。
    未指定なら実環境の正準パスを解決する。
    """
    signals: List[WeakSignal] = []

    # ② permission deny ← errors.jsonl
    if errors_path is None:
        from rl_common import hook_store_path

        import rl_common as _rc

        errors_path = hook_store_path("errors.jsonl", base=_rc.DATA_DIR)
    signals.extend(detect_permission_deny(_read_errors(errors_path), pj_slug))

    # ① 直後手編集 / ④ Esc 中断 ← transcript jsonl 直読
    if projects_root is None:
        projects_root = Path.home() / ".claude" / "projects"
    pj_dir = _project_dir_for_slug(projects_root, pj_slug)
    if pj_dir is not None:
        for tp in _recent_transcripts(pj_dir, max_transcripts):
            signals.extend(detect_transcript_signals(tp, pj_slug))

    # ③ 言い直し ← utterances.db（query_utterances）
    if utterances is None:
        try:
            from utterance_archive.query import query_utterances

            utterances = query_utterances(pj_slug, source_kinds=("dialogue",))
        except Exception:
            utterances = []
    signals.extend(detect_rephrase(utterances, pj_slug))

    return signals


def channel_counts(signals: List[WeakSignal]) -> Dict[str, int]:
    """チャネル別の検出件数（summary 用）。"""
    counts: Dict[str, int] = {}
    for sig in signals:
        counts[sig.channel] = counts.get(sig.channel, 0) + 1
    return counts


def run_batch(
    pj_slug: str,
    *,
    dry_run: bool = False,
    store_path: Optional[Path] = None,
    projects_root: Optional[Path] = None,
    errors_path: Optional[Path] = None,
    utterances: Optional[List[Dict[str, Any]]] = None,
    max_transcripts: int = DEFAULT_MAX_TRANSCRIPTS,
) -> Dict[str, Any]:
    """検出 → dedup → 書き込み（dry-run はゼロ書き込み）。

    Returns:
        {"detected": チャネル別検出件数 dict, "total": int,
         "written": 新規書き込み, "skipped_dup": 重複, "dry_run": bool}
    """
    signals = collect_signals(
        pj_slug,
        projects_root=projects_root,
        errors_path=errors_path,
        utterances=utterances,
        max_transcripts=max_transcripts,
    )
    counts = channel_counts(signals)
    write_res = append_signals(signals, path=store_path, dry_run=dry_run)
    return {
        "detected": counts,
        "total": len(signals),
        "written": write_res["written"],
        "skipped_dup": write_res["skipped_dup"],
        "dry_run": write_res["dry_run"],
    }


def persist_weak_signals_drain(
    pj_slug: str,
    *,
    store_path: Optional[Path] = None,
    projects_root: Optional[Path] = None,
    errors_path: Optional[Path] = None,
    utterances: Optional[List[Dict[str, Any]]] = None,
    max_transcripts: int = DEFAULT_MAX_TRANSCRIPTS,
) -> Dict[str, Any]:
    """apply 境界（`evolve --drain`）で決定論 weak_signals を永続化する（#484）。

    根因（#484）: 標準 evolve フローは ``evolve --dry-run`` 分析 → assistant が対話適用、
    である。``run_batch`` は ``run_evolve`` の中で ``dry_run=dry_run`` で呼ばれるため、dry-run
    分析パスでは ``append_signals`` の最下層 dry-run ゲート（#491 invariant）で常に書き込み
    ゼロになる。非 dry-run の evolve は標準フローでまず走らないので、決定論3チャネル
    （manual_edit_after_ai / esc_interrupt / rephrase）が実 PJ で**一度も永続化されない**。

    決定論検出は冪等（signal_key dedup）なので、#400 の evolve_decisions と同型に
    **apply 境界の drain**（tool 文脈・非 dry-run・正準 DATA_DIR）で永続化する。
    これは「dry-run 分析は何も書かない（#491）」契約を破らずに永続化を成立させる。

    ``run_batch(pj_slug, dry_run=False)`` の薄いラッパだが、apply 境界専用の入口として
    名前を分けることで「ここは必ず書き込む経路」という意図を CLI / SKILL.md から明示する。

    Returns:
        run_batch と同じ dict（``dry_run`` は常に False）。
    """
    return run_batch(
        pj_slug,
        dry_run=False,
        store_path=store_path,
        projects_root=projects_root,
        errors_path=errors_path,
        utterances=utterances,
        max_transcripts=max_transcripts,
    )
