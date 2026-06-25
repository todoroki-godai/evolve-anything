"""fleet.queue — 学習素材ベースの evolve 待ち列挙ロジック（#79 Phase 1a）。

「前回 evolve 以降に自然蓄積した学習素材が閾値以上の PJ」を **決定論・ゼロ LLM** で
列挙する。毎朝の定期実行（Phase 1b #80）の入口で、ユーザーが対話で処理する PJ を選ぶ。

待ち定義:
  material_count = weak_unprocessed（未昇格・未expired の weak_signals）
                 + new_corrections（前回 evolve 以降の新規 corrections）
  material_count >= threshold の PJ を待ちとする。

補助シグナル（フィルタには使わず列挙理由に併記）:
  activity_since = {subagents, sessions}（前回 evolve 以降の活動量）。

reader は副作用なし（読み取りのみ）。書込（per-PJ last_evolve state）は
``queue_state.persist_last_evolve`` が evolve の apply 境界で行う（本モジュールは読まない）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


# --- alias fold: rename 済 PJ の旧 slug を現 slug に畳む -----------------------


def _aliases_for(slug: str) -> set:
    """``slug``（現 slug）に集計すべき全 slug（自身 + 畳まれる旧名）の集合を返す。

    rename 済 PJ（例: ``rl-anything`` → ``evolve-anything``）の旧 slug レコードを
    現 slug の集計に含めるため、``pj_slug.pj_slug_aliases_for`` を read 層別名 SoT として
    再利用する（write 側 deriver には適用しない）。import 失敗時は ``{slug}`` で保守的に
    フォールバック（自身のみ・cross-PJ 副作用なし）。
    """
    try:
        from pj_slug import pj_slug_aliases_for
        a = pj_slug_aliases_for(slug)
        return a or {slug}
    except Exception:
        return {slug}


# --- 純関数: 閾値判定 + 並び替え ---------------------------------------------


def select_evolve_queue(
    pj_materials: List[Dict[str, Any]],
    threshold: int,
) -> List[Dict[str, Any]]:
    """per-PJ material リストから material_count >= threshold の待ち PJ を返す。

    各 material dict は ``{pj_slug, weak_unprocessed, new_corrections,
    last_evolve_at, activity_since}`` を持つ。material_count = weak + corr を算出し、
    閾値以上のものを material_count 降順（同数は pj_slug 昇順）で返す。各要素に
    ``material_count`` / ``reason`` を付与する。純関数（store I/O なし・テスト容易）。
    """
    selected: List[Dict[str, Any]] = []
    for m in pj_materials:
        weak = int(m.get("weak_unprocessed", 0) or 0)
        corr = int(m.get("new_corrections", 0) or 0)
        count = weak + corr
        if count < threshold:
            continue
        selected.append(
            {
                "pj_slug": m["pj_slug"],
                "project_path": m.get("project_path"),
                "material_count": count,
                "weak_unprocessed": weak,
                "new_corrections": corr,
                "last_evolve_at": m.get("last_evolve_at"),
                "activity_since": m.get("activity_since", {"subagents": 0, "sessions": 0}),
                "reason": f"weak={weak} + new corr={corr} >= {threshold}",
            }
        )
    selected.sort(key=lambda x: (-x["material_count"], x["pj_slug"]))
    return selected


# --- store reader: weak_signals 未処理カウント（PJ 別）-----------------------


def weak_unprocessed_by_pj(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
) -> int:
    """pj_slug の未処理（promoted=False かつ expired=False）weak_signals 件数を返す。

    既存 reader（``correction_semantic.promote.read_unpromoted``）を再利用し、pj_slug で
    scope する（weak_signals.jsonl は単一 DATA_DIR ファイル・pj_slug は record 属性）。
    ``weak_signals_path`` を渡すとそのファイルのみ（hermetic・テスト注入）。未指定なら
    production 既定（union read）。ファイル不在 → 0。
    """
    from correction_semantic.promote import read_unpromoted

    recs = read_unpromoted(weak_signals_path=weak_signals_path, exclude_expired=True)
    aliases = _aliases_for(pj_slug)
    return sum(1 for r in recs if r.get("pj_slug") in aliases)


# --- store reader: 前回 evolve 以降の corrections カウント（PJ 別）------------


def _correction_slug(project_path: Any) -> str:
    """corrections の ``project_path`` を weak_signals と同じ bare slug に正規化する。

    実コーパスでは ``project_path`` が **フルパス**（``/Users/.../amamo``・古い hook）と
    **bare slug**（``amamo``・#593 後）の両方で混在する。weak_signals の ``pj_slug`` は
    bare slug なので、突合のため双方を ``project_name_from_dir``（pj_slug_fast → worktree
    切詰 → basename fallback の単一ソース）で bare slug に畳む。新方式を発明しない。
    """
    if not isinstance(project_path, str) or not project_path:
        return ""
    try:
        from rl_common import project_name_from_dir
        slug = project_name_from_dir(project_path)
        if slug:
            return slug
    except Exception:
        pass
    return Path(project_path).name


def new_corrections_by_pj(
    pj_slug: str,
    *,
    last_evolve_at: Optional[str] = None,
    corrections_path: Path,
) -> int:
    """pj_slug の corrections のうち ``last_evolve_at`` 以降の件数を返す。

    ``last_evolve_at=None``（state 不在 = 初回）は全件カウント（初回＝全件待ち）。
    corrections は ``project_path`` を bare slug に正規化（``_correction_slug``）してから
    scope する（実コーパスでフルパス / slug が混在するため）。timestamp は ISO8601 文字列の
    辞書順比較で十分（None 時は全件なので影響しない）。ファイル不在 → 0。
    """
    path = Path(corrections_path)
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    count = 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            rec = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        if _correction_slug(rec.get("project_path")) not in _aliases_for(pj_slug):
            continue
        if last_evolve_at is not None:
            ts = rec.get("timestamp")
            if not isinstance(ts, str) or ts <= last_evolve_at:
                continue
        count += 1
    return count


# --- 統合: per-PJ material 収集 + queue result 組み立て -----------------------


def build_queue_result(
    *,
    pj_slugs: List[str],
    threshold: int,
    weak_signals_path: Optional[Path],
    corrections_path: Path,
    last_evolve_map: Dict[str, str],
    activity_map: Dict[str, Dict[str, int]],
    generated_at: str,
    pj_paths: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """各 PJ の学習素材を集計し、Phase 1b #80 契約の queue result dict を返す。

    schema:
      {generated_at, threshold, tracked_total, skipped_dead, queue: [{pj_slug,
       project_path, material_count, weak_unprocessed, new_corrections,
       last_evolve_at, activity_since, reason}]}

    weak/corr の reader はそれぞれ ``weak_unprocessed_by_pj`` / ``new_corrections_by_pj``。
    queue は ``select_evolve_queue``（純関数）で閾値フィルタ + 降順ソートする。

    ``pj_paths``（slug → 実パス）を渡すと、実ディレクトリが不在の PJ（rename 済の dead
    パス等・#79）は queue に出さず ``skipped_dead`` に分離する（silent truncation 禁止＝
    透明化）。各 material/queue entry には ``project_path`` を添え、利用側が親 dir 推測なしに
    ``/cd`` できるようにする。``pj_paths=None``（未指定）は後方互換: dead 判定をせず全件 live・
    ``project_path=None``。``tracked_total`` は dead 含む全 tracked 数のまま。
    """
    paths = pj_paths or {}
    materials: List[Dict[str, Any]] = []
    skipped_dead: List[Dict[str, Any]] = []
    for slug in pj_slugs:
        path = paths.get(slug)
        if path is not None and not Path(path).is_dir():
            skipped_dead.append({"pj_slug": slug, "project_path": path})
            continue
        last = last_evolve_map.get(slug)
        materials.append(
            {
                "pj_slug": slug,
                "project_path": path,
                "weak_unprocessed": weak_unprocessed_by_pj(
                    slug, weak_signals_path=weak_signals_path
                ),
                "new_corrections": new_corrections_by_pj(
                    slug, last_evolve_at=last, corrections_path=corrections_path
                ),
                "last_evolve_at": last,
                "activity_since": activity_map.get(slug, {"subagents": 0, "sessions": 0}),
            }
        )

    queue = select_evolve_queue(materials, threshold=threshold)
    return {
        "generated_at": generated_at,
        "threshold": threshold,
        "tracked_total": len(pj_slugs),
        "queue": queue,
        "skipped_dead": skipped_dead,
    }
