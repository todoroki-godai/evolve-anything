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
from datetime import datetime, timezone
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


def _equivalence_slugs(slug: str) -> set:
    """``slug`` と同一 PJ を指す全 slug（自身 + canonical + 双方の alias）の集合を返す。

    ``_aliases_for`` は **現 slug** を渡したとき旧名を畳む（``evolve-anything`` →
    ``{evolve-anything, rl-anything}``）が、**旧 slug** を渡すと自身しか返さない
    （``rl-anything`` → ``{rl-anything}``）。activity counts は collectors が canonical
    （現 slug）でキー付けするため、tracked slug が旧名のとき素の ``_aliases_for(旧名)``
    では現 slug の値を回収できない（#87 ③）。canonical 方向も合算して両義性を解消する。
    """
    canon = _canonical_slug(slug)
    out = set(_aliases_for(slug))
    out |= _aliases_for(canon)
    out.add(canon)
    out.add(slug)
    return {s for s in out if s}


def fold_activity_counts(
    slug: str,
    subagent_counts: Dict[str, int],
    session_counts: Dict[str, int],
) -> Dict[str, int]:
    """``slug`` の activity（subagents/sessions）を alias fold して合算する（#87 ③）。

    weak/corr は ``_aliases_for`` で旧 slug を畳むのに対し、``activity_map`` は素の
    ``.get(slug)`` で組まれていたため、tracked slug が旧名（``rl-anything``）だと
    collectors が canonical（``evolve-anything``）でキー付けした実値（実 155 sessions 等）が
    0 に落ちていた。同一 PJ を指す全 slug（``_equivalence_slugs``）にわたって合算し、
    weak/corr と同じ namespace に揃える。event log は dir 跨ぎでも dedup 不要なので単純合算。
    """
    eq = _equivalence_slugs(slug)
    sub = sum(int(subagent_counts.get(s, 0) or 0) for s in eq)
    sess = sum(int(session_counts.get(s, 0) or 0) for s in eq)
    return {"subagents": sub, "sessions": sess}


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
        last_evolve = m.get("last_evolve_at")
        # #92: 未 drain（last_evolve_at=None）は corr が「前回 evolve 以降の増分」でなく全件。
        # 『new corr』だと never と矛盾して見えるので「全件・未 drain」と明示する。
        if last_evolve is None:
            reason = f"weak={weak} + corr={corr}（全件・未 drain）>= {threshold}"
        else:
            reason = f"weak={weak} + new corr={corr} >= {threshold}"
        selected.append(
            {
                "pj_slug": m["pj_slug"],
                "project_path": m.get("project_path"),
                "material_count": count,
                "weak_unprocessed": weak,
                "new_corrections": corr,
                "last_evolve_at": last_evolve,
                "activity_since": m.get("activity_since", {"subagents": 0, "sessions": 0}),
                "reason": reason,
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


def _parse_iso(s: Any) -> Optional[datetime]:
    """ISO8601 文字列を tz-aware datetime にする。`Z` / `+00:00` 終端を吸収。

    naive（tz 無し）は UTC とみなして aware 比較を可能にする。パース不能 → None。
    """
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ts_strictly_after(ts: Any, last: str) -> bool:
    """``ts`` が ``last`` より厳密に後なら True（同一 instant は False＝除外）。

    実コーパスの corrections は `Z` 終端 / `+00:00` 終端が混在し、``last_evolve_at`` は
    ``persist_last_evolve`` が ``.isoformat()``＝`+00:00` で書く。辞書順だと
    ``"...Z" > "...+00:00"`` が同一 instant でも True になり drain と同時刻の corr を
    誤って新規計上する。両者を datetime にパースして比較し、片方でもパース不能なときのみ
    辞書順へフォールバック（旧挙動温存）。
    """
    a = _parse_iso(ts)
    b = _parse_iso(last)
    if a is not None and b is not None:
        return a > b
    return isinstance(ts, str) and ts > last


def new_corrections_by_pj(
    pj_slug: str,
    *,
    last_evolve_at: Optional[str] = None,
    corrections_path: Path,
) -> int:
    """pj_slug の corrections のうち ``last_evolve_at`` 以降の件数を返す。

    ``last_evolve_at=None``（state 不在 = 初回）は全件カウント（初回＝全件待ち）。
    corrections は ``project_path`` を bare slug に正規化（``_correction_slug``）してから
    scope する（実コーパスでフルパス / slug が混在するため）。timestamp は ``_ts_strictly_after``
    で datetime 比較する（`Z` / `+00:00` 終端混在を吸収・None 時は全件なので影響しない）。
    ファイル不在 → 0。
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
            if not _ts_strictly_after(ts, last_evolve_at):
                continue
        count += 1
    return count


def count_unattributed_corrections(corrections_path: Path) -> Dict[str, Any]:
    """``project_path`` 欠落で PJ 帰属不能な corrections を source 別に数える（#91）。

    ``_correction_slug`` が空文字に落ちるレコード（``project_path`` が空/None）は、どの PJ の
    ``material_count`` にも数えられず ``untracked_with_material`` にも ``skipped_phantom`` にも
    出ないため queue から構造的に完全不可視になる（silent truncation の一種）。#86/#88 の
    「無音で落とさない」原則の最後の穴埋めとして、件数 + source 内訳を advisory に surface する。

    返り値: ``{"total": int, "by_source": {source: count}}``。``source`` 欠落は ``(unknown)``。
    ファイル不在 / 読込失敗 → ``{"total": 0, "by_source": {}}``（advisory ゆえ落とさない）。
    """
    result: Dict[str, Any] = {"total": 0, "by_source": {}}
    path = Path(corrections_path)
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result
    by_source: Dict[str, int] = result["by_source"]
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
        if _correction_slug(rec.get("project_path")):
            continue  # 帰属可能なものは対象外
        result["total"] += 1
        src = rec.get("source") or "(unknown)"
        by_source[src] = by_source.get(src, 0) + 1
    return result


# --- untracked だが学習素材を持つ PJ の advisory 列挙（#86）-------------------

_UNKNOWN_PROJECT_LABEL = "(unknown)"  # collectors._UNKNOWN_PROJECT_LABEL と一致させる


def _canonical_slug(slug: str) -> str:
    """slug を canonical（rename 旧→現）に畳む。import 失敗時は素通し。"""
    try:
        from pj_slug import canonical_pj_slug
        return canonical_pj_slug(slug) or slug
    except Exception:
        return slug


def collect_untracked_materials(
    *,
    material_slugs: List[str],
    tracked_slugs: set,
    threshold: int,
    weak_signals_path: Optional[Path],
    corrections_path: Path,
    dir_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    """material（weak/corr）を持つが queue 母集団（tracked）に居ない PJ を advisory 列挙する（#86）。

    queue の母集団は fleet-config.json の ``tracked_projects`` 限定だが、material 母集団
    （weak_signals / corrections に出現する全 pj_slug）の方が広い。この不一致で、material を
    持つ untracked PJ（例: amamo weak 64 件だが tracked 外）が待ちにも skipped_dead にも
    出ず完全沈黙し真の evolve 候補を取りこぼす（O2）。本関数はその差集合を surface する。

    対象 slug は以下を**全て満たす**もの: ① ``tracked_slugs`` に無い ② ``dir_map`` に
    実 dir を持つ（``Path(dir_map[slug]).is_dir()`` が真＝phantom/temp slug 除外ゲート）
    ③ ``(unknown)`` でない。各 ``material_slugs`` は ``canonical_pj_slug`` で fold
    （rename 旧 slug を現 slug に畳む。import 失敗時は素通し）してから dedup する。

    対象 slug について ``weak_unprocessed_by_pj`` + ``new_corrections_by_pj``
    （untracked は last_evolve state 無し＝全件）を集計し、``material_count >= threshold``
    のものを material_count 降順（同数は pj_slug 昇順）で返す。

    Returns:
        ``[{pj_slug, project_path, material_count, weak_unprocessed, new_corrections}]``。
        純関数（store I/O は既存 reader 経由・dir_map/material_slugs は呼び側が用意）。
    """
    tracked_canon = {_canonical_slug(s) for s in tracked_slugs}
    seen: set = set()
    candidates: List[str] = []
    for raw in material_slugs:
        slug = _canonical_slug(raw)
        if not slug or slug == _UNKNOWN_PROJECT_LABEL:
            continue
        if slug in tracked_canon:
            continue
        path = dir_map.get(slug)
        if not path or not Path(path).is_dir():
            continue
        if slug in seen:
            continue
        seen.add(slug)
        candidates.append(slug)

    out: List[Dict[str, Any]] = []
    for slug in candidates:
        weak = weak_unprocessed_by_pj(slug, weak_signals_path=weak_signals_path)
        corr = new_corrections_by_pj(
            slug, last_evolve_at=None, corrections_path=corrections_path
        )
        count = weak + corr
        if count < threshold:
            continue
        out.append(
            {
                "pj_slug": slug,
                "project_path": dir_map[slug],
                "material_count": count,
                "weak_unprocessed": weak,
                "new_corrections": corr,
            }
        )
    out.sort(key=lambda x: (-x["material_count"], x["pj_slug"]))
    return out


def collect_phantom_materials(
    *,
    material_slugs: List[str],
    tracked_slugs: set,
    threshold: int,
    weak_signals_path: Optional[Path],
    corrections_path: Path,
    dir_map: Dict[str, str],
) -> List[Dict[str, Any]]:
    """閾値以上 material を持つが実 dir に解決できない untracked slug を列挙する（#88）。

    ``collect_untracked_materials`` の ``is_dir()`` ゲートで黙って drop される slug
    （例: temp slug ``tmpdcm8avo8`` material=5）を透明化するための対称関数。
    ``skipped_dead`` は透明化するのに phantom だけ不可視という非対称（O1 と非対称）を是正する。

    対象 slug は: ① ``tracked_slugs``（canonical fold 後）に無い ② ``(unknown)`` でない
    ③ ``dir_map`` で実 dir に**解決できない**（``collect_untracked_materials`` の補集合）
    ④ material_count（weak + corr・untracked は全件）が threshold 以上。material_count 降順
    （同数は pj_slug 昇順）で返す。waiting には昇格させない（temp slug は意図的に除外）。

    Returns:
        ``[{pj_slug, material_count, weak_unprocessed, new_corrections}]``（project_path は
        解決できないので付けない）。純関数（store I/O は既存 reader 経由）。
    """
    tracked_canon = {_canonical_slug(s) for s in tracked_slugs}
    seen: set = set()
    candidates: List[str] = []
    for raw in material_slugs:
        slug = _canonical_slug(raw)
        if not slug or slug == _UNKNOWN_PROJECT_LABEL:
            continue
        if slug in tracked_canon:
            continue
        path = dir_map.get(slug)
        if path and Path(path).is_dir():
            continue  # 実 dir 解決可 → untracked 側（phantom でない）
        if slug in seen:
            continue
        seen.add(slug)
        candidates.append(slug)

    out: List[Dict[str, Any]] = []
    for slug in candidates:
        weak = weak_unprocessed_by_pj(slug, weak_signals_path=weak_signals_path)
        corr = new_corrections_by_pj(
            slug, last_evolve_at=None, corrections_path=corrections_path
        )
        count = weak + corr
        if count < threshold:
            continue
        out.append(
            {
                "pj_slug": slug,
                "material_count": count,
                "weak_unprocessed": weak,
                "new_corrections": corr,
            }
        )
    out.sort(key=lambda x: (-x["material_count"], x["pj_slug"]))
    return out


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
    material_slugs: Optional[List[str]] = None,
    untracked_dir_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """各 PJ の学習素材を集計し、Phase 1b #80 契約の queue result dict を返す。

    schema:
      {generated_at, threshold, tracked_total, skipped_dead, untracked_with_material,
       queue: [{pj_slug, project_path, material_count, weak_unprocessed,
       new_corrections, last_evolve_at, activity_since, reason}]}

    weak/corr の reader はそれぞれ ``weak_unprocessed_by_pj`` / ``new_corrections_by_pj``。
    queue は ``select_evolve_queue``（純関数）で閾値フィルタ + 降順ソートする。

    ``pj_paths``（slug → 実パス）を渡すと、実ディレクトリが不在の PJ（rename 済の dead
    パス等・#79）は queue に出さず ``skipped_dead`` に分離する（silent truncation 禁止＝
    透明化）。各 material/queue entry には ``project_path`` を添え、利用側が親 dir 推測なしに
    ``/cd`` できるようにする。``pj_paths=None``（未指定）は後方互換: dead 判定をせず全件 live・
    ``project_path=None``。``tracked_total`` は dead 含む全 tracked 数のまま。

    ``material_slugs``（weak/corr に出現する全 slug）+ ``untracked_dir_map``（slug→実 dir）が
    **両方**与えられたら、tracked 母集団に居ない material 持ち PJ を ``collect_untracked_materials``
    で集計し ``untracked_with_material`` に入れる（#86 O2 — material 母集団まで母数を広げ
    untracked を advisory 表示）。どちらか None なら ``untracked_with_material=[]``（後方互換）。
    ``tracked_total`` は意味を変えず ``len(pj_slugs)``（tracked 母数）のまま。

    #87: tracked path が dead でも ``_canonical_slug(slug)`` が ``untracked_dir_map`` の
    live dir に解決できるなら、その live path に **redirect** して material を集計し waiting
    候補に乗せる（``skipped_dead`` に入れない）。rename-but-live（tracked=旧 dead path・
    store=旧 slug・discovery=新 live dir）で evolve-anything 自身が消えた dogfood バグの根治。
    redirect できない真の dead は従来通り ``skipped_dead`` に入れるが、material 数を添えて
    透明化する（``skipped_dead[*]`` に weak_unprocessed/new_corrections/material_count）。

    #88: 閾値以上 material を持つが実 dir に解決できない untracked slug（temp slug 等）は
    ``collect_phantom_materials`` で ``skipped_phantom`` に分離する（waiting には昇格しない）。
    """
    paths = pj_paths or {}
    redirect_map = untracked_dir_map or {}
    materials: List[Dict[str, Any]] = []
    skipped_dead: List[Dict[str, Any]] = []
    for slug in pj_slugs:
        path = paths.get(slug)
        if path is not None and not Path(path).is_dir():
            # #87 ①: dead だが canonical 先が live dir に解決できれば redirect。
            canon = _canonical_slug(slug)
            live = redirect_map.get(canon)
            if live and Path(live).is_dir():
                last = last_evolve_map.get(canon, last_evolve_map.get(slug))
                # activity は旧 slug（tracked 名）の entry を優先し、無ければ canonical。
                act = activity_map.get(
                    slug, activity_map.get(canon, {"subagents": 0, "sessions": 0})
                )
                materials.append(
                    {
                        "pj_slug": canon,
                        "project_path": live,
                        "weak_unprocessed": weak_unprocessed_by_pj(
                            canon, weak_signals_path=weak_signals_path
                        ),
                        "new_corrections": new_corrections_by_pj(
                            canon, last_evolve_at=last, corrections_path=corrections_path
                        ),
                        "last_evolve_at": last,
                        "activity_since": act,
                    }
                )
                continue
            # #87 ②: 真の dead でも material 数を添えて透明化する。
            d_weak = weak_unprocessed_by_pj(slug, weak_signals_path=weak_signals_path)
            d_corr = new_corrections_by_pj(
                slug,
                last_evolve_at=last_evolve_map.get(slug),
                corrections_path=corrections_path,
            )
            skipped_dead.append(
                {
                    "pj_slug": slug,
                    "project_path": path,
                    "weak_unprocessed": d_weak,
                    "new_corrections": d_corr,
                    "material_count": d_weak + d_corr,
                }
            )
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

    # redirect で waiting に乗った canonical slug は untracked/phantom 母集団から除外する
    # （二重列挙防止）。tracked + redirect 済 canonical を tracked 扱いにする。
    tracked_for_untracked = set(pj_slugs) | {m["pj_slug"] for m in materials}

    if material_slugs is not None and untracked_dir_map is not None:
        untracked = collect_untracked_materials(
            material_slugs=material_slugs,
            tracked_slugs=tracked_for_untracked,
            threshold=threshold,
            weak_signals_path=weak_signals_path,
            corrections_path=corrections_path,
            dir_map=untracked_dir_map,
        )
        phantom = collect_phantom_materials(
            material_slugs=material_slugs,
            tracked_slugs=tracked_for_untracked,
            threshold=threshold,
            weak_signals_path=weak_signals_path,
            corrections_path=corrections_path,
            dir_map=untracked_dir_map,
        )
    else:
        untracked = []
        phantom = []

    return {
        "generated_at": generated_at,
        "threshold": threshold,
        "tracked_total": len(pj_slugs),
        "queue": queue,
        "skipped_dead": skipped_dead,
        "untracked_with_material": untracked,
        "skipped_phantom": phantom,
        # #91: project_path 欠落で PJ 帰属不能な corrections（どの母数にも入らず不可視）を透明化。
        "unattributed_corrections": count_unattributed_corrections(corrections_path),
    }
