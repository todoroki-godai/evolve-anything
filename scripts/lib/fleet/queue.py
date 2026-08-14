"""fleet.queue — 学習素材ベースの evolve 待ち列挙ロジック（#79 Phase 1a）。

「前回 evolve 以降に自然蓄積した学習素材が閾値以上の PJ」を **決定論・ゼロ LLM** で
列挙する。毎朝の定期実行（Phase 1b #80）の入口で、ユーザーが対話で処理する PJ を選ぶ。

待ち定義:
  material_count = weak_unprocessed（未昇格・未expired・**content-rich** channel の weak_signals）
                 + new_corrections（前回 evolve 以降の新規 corrections）
  material_count >= threshold の PJ を待ちとする。
  content-poor channel（REVIEW_CHANNELS 外・昇格不能）は material に載せず footer で透明化（#113）。

補助シグナル（フィルタには使わず列挙理由に併記）:
  activity_since = {subagents, sessions}（前回 evolve 以降の活動量）。

reader は副作用なし（読み取りのみ）。書込（per-PJ last_evolve state）は
``queue_state.persist_last_evolve`` が evolve の apply 境界で行う（本モジュールは読まない）。

store reader（weak_signals/corrections の PJ 別集計）と untracked/phantom material 分類
関数は ``fleet/queue_materials.py`` に切り出し済み（#298・800行分割必須ラインの回避）。
本モジュールは re-export して既存 import path（``from fleet.queue import X``）を保つ。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .queue_materials import (  # noqa: F401 (再エクスポート含む)
    _aliases_for,
    _canonical_slug,
    _correction_slug,
    bootstrap_consumed_by_pj,
    collect_phantom_materials,
    collect_untracked_materials,
    count_unattributed_corrections,
    new_corrections_by_pj,
    weak_content_poor_by_pj,
    weak_machinery_by_pj,
    weak_unprocessed_by_pj,
)

# #267 C5: 未帰属 corrections の SETUP_REQUIRED 判定に使う集計窓（他の 30 日窓集計と統一）。
_UNATTRIBUTED_WINDOW_DAYS = 30


# --- alias fold: rename 済 PJ の旧 slug を現 slug に畳む -----------------------


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

    material dict が ``verify_pending``（``queue_verify.compute_verify_pending`` の返り値。
    呼び側 ``build_queue_result`` が store から読んで載せる）を持つ場合、accepted > 0 なら
    ``reason`` にその件数を追記し、返り値にも ``verify_pending`` をそのまま含める（#267
    Sprint 1）。verify_pending が無い/accepted=0 の PJ は従来通りの reason 文字列のまま。

    #267 C1: ``verify_pending["status"]`` が ``"none"`` 以外（verifiable/awaiting_exposure）
    の PJ は material_count が閾値未満でも queue に含める。evolve 直後（material がリセット
    された直後）こそ verify 待ちが最も可視化されるべき瞬間であり、閾値フィルタだけだとその
    瞬間に queue から消えてしまうため。閾値未満で昇格した item は reason の語順を反転し
    verify 待ちを主節にする（``format_verify_pending_promoted_reason``）。ソート順は
    material_count 降順のまま変えない（verify 昇格 item は定義上 material_count が低いので
    自然に下位へ並ぶ — 「まだ実行してよい」の目印であり緊急度の逆転を意味しないため、
    特別扱いの並び替えはしない）。
    """
    from .queue_verify import (
        STATUS_NONE,
        format_verify_pending_promoted_reason,
        format_verify_pending_suffix,
    )

    selected: List[Dict[str, Any]] = []
    for m in pj_materials:
        weak = int(m.get("weak_unprocessed", 0) or 0)
        corr = int(m.get("new_corrections", 0) or 0)
        count = weak + corr
        verify_pending = m.get("verify_pending")
        vp_status = (verify_pending or {}).get("status", STATUS_NONE)
        verify_promoted = count < threshold and vp_status != STATUS_NONE
        if count < threshold and not verify_promoted:
            continue
        last_evolve = m.get("last_evolve_at")
        if verify_promoted:
            reason = format_verify_pending_promoted_reason(
                verify_pending, material_count=count, threshold=threshold
            )
        else:
            # #92→A: 初回（last_evolve_at=None）は corr が「前回 evolve 以降の増分」でなく
            # 全件。『new corr』だと never と矛盾して見える。`未 drain` は emit→drain 2 相の
            # 内部 plumbing 用語なので、CLI 直読みの利用者向けには `初回・全件` の業務語で
            # 明示する。
            if last_evolve is None:
                reason = f"weak={weak} + corr={corr}（初回・全件）>= {threshold}"
            else:
                reason = f"weak={weak} + new corr={corr} >= {threshold}"
            reason += format_verify_pending_suffix(verify_pending)
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
                "verify_pending": verify_pending,
            }
        )
    selected.sort(key=lambda x: (-x["material_count"], x["pj_slug"]))
    return selected


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
      {generated_at, threshold, tracked_total, queue_status, queue_status_reason,
       skipped_dead, untracked_with_material,
       queue: [{pj_slug, project_path, material_count, weak_unprocessed,
       new_corrections, last_evolve_at, activity_since, reason, verify_pending}]}

    ``queue_status``（READY/SETUP_REQUIRED/EMPTY）+ ``queue_status_reason``（1行）は
    queue が空のとき「本当に素材が無い」か「素材はあるのに処理できていない」かを区別する
    （#267 Sprint 1）。各 queue item の ``verify_pending``（直近 run で accept 済・未検証の
    提案）は ``queue_verify.verify_pending_by_pj`` の read-time 導出（新規ストアは作らない）。

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

    # #267 Sprint 1: verify 待ち（直近 run で accept 済・未検証の提案）を material dict に
    # 載せる。store I/O はここ（build_queue_result 経由で queue_verify に委譲）で行い、
    # select_evolve_queue は純関数のままにする。バルク read + group by の実装（#267 I3）は
    # queue.py の行数バジェット（800行分割必須）を圧迫しないよう queue_verify 側に置く。
    from .queue_verify import attach_verify_pending

    attach_verify_pending(materials, canonicalize=_canonical_slug)

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

    # #94: bootstrap で消化済み（marker 以前 detected）として material から除外した weak を
    # 透明化する（silent truncation 禁止 — 除外しないと TTL まで material を膨らませ誤読を
    # 招くが、黙って除外すると「なぜ消えたか」が不明になる）。consumed>0 の PJ のみ。
    consumed_slugs = sorted(
        {m["pj_slug"] for m in materials} | {d["pj_slug"] for d in skipped_dead}
    )
    bootstrap_consumed: List[Dict[str, Any]] = []
    for s in consumed_slugs:
        c = bootstrap_consumed_by_pj(s, weak_signals_path=weak_signals_path)
        if c > 0:
            bootstrap_consumed.append({"pj_slug": s, "consumed": c})

    # #113: content-poor channel（REVIEW_CHANNELS 外）で material から除外した weak を透明化する。
    # y/n 確認から除外され promote しても昇格不能な死荷重ゆえ material_count には載せないが、黙って
    # 落とすと「なぜ WEAK が生検出より少ないか」が不明になる（silent truncation 禁止）。poor>0 のみ。
    weak_content_poor: List[Dict[str, Any]] = []
    for s in consumed_slugs:
        p = weak_content_poor_by_pj(s, weak_signals_path=weak_signals_path)
        if p > 0:
            weak_content_poor.append({"pj_slug": s, "content_poor": p})

    # #443 PR2-a: machinery（委譲メッセージ等の harness 注入）を理由に material から除外した
    # weak を透明化する（silence != evaluated）。REVIEW_CHANNELS 内のみ計上し content-poor
    # 側と二重計上しない（weak_machinery_by_pj が単一ソース）。machinery>0 の PJ のみ。
    weak_machinery: List[Dict[str, Any]] = []
    for s in consumed_slugs:
        m = weak_machinery_by_pj(s, weak_signals_path=weak_signals_path)
        if m > 0:
            weak_machinery.append({"pj_slug": s, "machinery": m})

    # #267 C5: 未帰属 corrections は帰属先 PJ が無く自然失効しないため、時刻窓なしで全件数える
    # と1件の古いレコードが SETUP_REQUIRED を永久ラッチさせる。直近 30 日窓に絞る。
    unattributed_since = (
        datetime.now(timezone.utc) - timedelta(days=_UNATTRIBUTED_WINDOW_DAYS)
    ).isoformat()
    unattributed_corrections = count_unattributed_corrections(
        corrections_path, since=unattributed_since
    )

    # #267 Sprint 1: queue が空のとき「本当に素材が無い」(EMPTY) か「素材はあるのに処理
    # できていない」(SETUP_REQUIRED) かを状態ラベル + 1行理由で明示する。
    from .queue_verify import compute_queue_status

    status = compute_queue_status(
        queue=queue,
        untracked_with_material=untracked,
        skipped_dead=skipped_dead,
        skipped_phantom=phantom,
        unattributed_total=unattributed_corrections.get("total", 0),
    )

    return {
        "generated_at": generated_at,
        "threshold": threshold,
        "tracked_total": len(pj_slugs),
        "queue": queue,
        "queue_status": status["queue_status"],
        "queue_status_reason": status["queue_status_reason"],
        "skipped_dead": skipped_dead,
        "untracked_with_material": untracked,
        "skipped_phantom": phantom,
        # #94: bootstrap 消化済み（破棄/TTL 任せ判断済み）で material から除外した weak の透明化。
        "bootstrap_consumed": bootstrap_consumed,
        # #113: content-poor channel（昇格不能）で material から除外した weak の透明化。
        "weak_content_poor": weak_content_poor,
        # #443 PR2-a: machinery（harness 注入の委譲メッセージ等）で material から除外した
        # weak の透明化（REVIEW_CHANNELS 内のみ・silence != evaluated）。
        "weak_machinery": weak_machinery,
        # #91: project_path 欠落で PJ 帰属不能な corrections（どの母数にも入らず不可視）を透明化。
        "unattributed_corrections": unattributed_corrections,
    }
