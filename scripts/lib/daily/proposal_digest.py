"""daily.proposal_digest — 改善案 digest 生成 + SessionStart 提示（#409）。

毎朝の daily runner が「evolve 待ち PJ 一覧」（``fleet queue``）だけでなく「その中身
（改善案そのもの）」を ``evolve-queue.json`` に埋め込むことで、SessionStart hook が
コマンドを叩かずに y/n 提示できるようにする。

digest 生成（``build_proposal_digest``）は決定論・read-only・LLM 非依存。新しい group 化
ロジックは発明せず、既存の ``correction_semantic.daily_review.build_review``
（dry_run=True・読み取り専用）をそのまま呼び、その group を提示用に slim 化するだけ。

global レーンの判定（暫定・issue #409 の未決点に対する裁定）: issue 本文の暫定案
（``~/.claude`` 配下の artifact を global 扱い）は weak_signal group には適用できない
（group は artifact ではなくユーザー発話の束であり、対象 artifact を持たない）。代わりの
暫定基準として、**同一 idiom テキストが 2 つ以上の異なる PJ の weak_signal に出現する
group を global レーンに載せる**。正規化は ``correction_semantic.store.normalize_idiom_text``
を再利用する（新しい正規化を書かない）。global に載った group は 1 回答えたら他 PJ でも
再提示されないよう、全 PJ 分の signal_keys をマージして 1 件にまとめ、per_pj 側から除外する。

SessionStart 提示（``build_session_proposals``）は digest（per_pj[pj_slug] + global）から
既読 signal_key を含む group を除外し、先頭 ``limit`` 件だけを返す（read-only、既読判定は
``correction_semantic.daily_review.read_reviewed_keys`` を再利用）。
"""
from __future__ import annotations

import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from correction_semantic import daily_review as _daily_review
from correction_semantic import store as _cs_store
from correction_semantic.idiom_filter import idiom_eligible
from correction_semantic.store import normalize_idiom_text
from daily import proposal_ranking as _ranking
from weak_signals.store import default_store_path as _default_weak_signals_path

# 1 セッションで提示する改善案の上限（受け入れ条件「1セッションの提示件数が上限を超えない」）。
MAX_SESSION_PROPOSALS = 2

# global レーンへマージする連結成分（connected component）のサイズ上限（#412 round2 [Must]D-3）。
# 同一 idiom テキストで多数の group が連結された場合、成分全体を無条件で1つの global group に
# 丸めると人間が精査しきれない量になる。上限超過時は global 化せず per_pj に残す（安全側）。
MAX_GLOBAL_COMPONENT_GROUPS = 5

_EVIDENCE_TEXT_TRUNC = 200
_REASON_TRUNC = 200

# #498: llm_judge/rephrase は representative が生の発話断片のみ（review_channels.signal_text
# が user_only_text をそのまま返す・channel 別の合成をしない）。permission_deny/verbosity は
# signal_text 自体が拒否コマンド・判定理由を合成済み（review_channels.py）で representative
# だけで説明になる。前者だけ reason の有無で説明可否を判定する（#504: prev_action は外した）。
_BARE_UTTERANCE_CHANNELS = frozenset({"llm_judge", "rephrase"})


def _truncate(text: Optional[str], limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _pj_slugs(queue_entries: Optional[List[Dict[str, Any]]]) -> List[str]:
    """queue エントリ（``fleet queue`` の ``queue`` リスト）から pj_slug を順序保存で抽出する。"""
    out: List[str] = []
    seen: Set[str] = set()
    for item in queue_entries or []:
        if not isinstance(item, dict):
            continue
        slug = item.get("pj_slug")
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def _pj_project_paths(queue_entries: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    """queue エントリから ``{pj_slug: project_path}`` を抽出する（#412 [Must]4）。

    global レーンの group から origin PJ ごとの ``--project-path`` を組み立てるために使う
    （``fleet queue`` の各エントリは既に絶対パス ``project_path`` を持つ・新しい解決経路は作らない）。
    project_path が無い/文字列でないエントリは除外する。
    """
    out: Dict[str, str] = {}
    for item in queue_entries or []:
        if not isinstance(item, dict):
            continue
        slug = item.get("pj_slug")
        path = item.get("project_path")
        if slug and isinstance(path, str) and path:
            out.setdefault(slug, path)
    return out


def _slim_group(
    g: Dict[str, Any],
    *,
    uttered_at_map: Optional[Dict[tuple, str]] = None,
    map_available: bool = False,
    freshness_stats: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """daily_review group を提示に必要な最小限へ縮める。

    ADR-054 PR2-c: ``signal_meta_by_key``（signal_key ごとの発話時刻・判定時刻・cross_pj）と
    ``cross_pj_confirmed`` を追加で保持する。既読差し引き後に ``composite_sort_key`` が
    残存 signal_keys だけから順位キーを再計算できるようにするため（group 集約後の
    ``count`` だけでは個々の signal_key の情報が失われる）。

    ``uttered_at_map``/``map_available``/``freshness_stats`` は
    ``daily.proposal_ranking.build_uttered_at_map`` の結果をそのまま渡す（省略時は
    join を行わず全件 detected_at フォールバック扱い＝呼び出し側が失敗を集計しない場合の
    後方互換フォールバック）。
    """
    evidence = g.get("evidence") or {}
    count = evidence.get("count")
    if not isinstance(count, int):
        count = len(g.get("signal_keys") or [])
    cross_pj_confirmed = list(g.get("cross_pj_confirmed") or [])

    signal_meta_by_key: Dict[str, Dict[str, Any]] = {}
    for m in g.get("members") or []:
        key = m.get("signal_key")
        if not key:
            continue
        uttered_at = None
        if map_available and uttered_at_map is not None:
            uttered_at = _ranking.lookup_uttered_at(
                uttered_at_map, m.get("source_path"), m.get("line_no"),
            )
        if not uttered_at and freshness_stats is not None:
            freshness_stats["fallback_to_detected_at"] = (
                freshness_stats.get("fallback_to_detected_at", 0) + 1
            )
            if map_available:
                freshness_stats["key_mismatch"] = freshness_stats.get("key_mismatch", 0) + 1
        signal_meta_by_key[key] = {
            "uttered_at": uttered_at,
            "detected_at": m.get("detected_at"),
            "cross_pj": cross_pj_confirmed,
        }

    return {
        "signal_keys": list(g.get("signal_keys") or []),
        "representative": g.get("representative", ""),
        "idiom": g.get("idiom"),
        "confirmable_idiom": g.get("confirmable_idiom"),
        "channel": g.get("channel", ""),
        "count": count,
        "evidence_text": _truncate(evidence.get("text", ""), _EVIDENCE_TEXT_TRUNC),
        # #498: 何を根拠に改善候補と判断したか（llm_judge の Haiku 判定理由。自然文・
        # channel名やスコア値は含まない — batch.py/prompt.py が生成する自由文）。
        "reason": _truncate(evidence.get("reason", ""), _REASON_TRUNC),
        "cross_pj_confirmed": cross_pj_confirmed,
        "signal_meta_by_key": signal_meta_by_key,
    }


def _group_has_explanation(g: Dict[str, Any]) -> bool:
    """群を「何をしている時に・なぜ拾われたか」まで説明できるかを判定する（#498 要件4）。

    ``llm_judge``/``rephrase`` は representative が生の発話断片のみなので、``reason`` が
    無いと説明できない（保留にし、除外件数は ``excluded_context_missing_by_pj`` に surface
    する — silence != evaluated）。``permission_deny``/``verbosity`` は representative 自体が
    拒否コマンド・判定理由を合成済み（``review_channels.signal_text``）なので常に説明可能
    とみなす。

    #504: ``prev_action``（ツール名の連結。仕様が謳う「1行要約」ではない・実測で
    説明可否の判定結果を1件も変えないことを確認済み）は判定材料から外した。
    """
    if g.get("channel") not in _BARE_UTTERANCE_CHANNELS:
        return True
    return bool((g.get("reason") or "").strip())


def _recorded_message_preview(g: Dict[str, Any]) -> str:
    """corrections.jsonl に記録される message 本文を ``promote._correction_message`` と
    同一規則で再現する（text+reason があれば ``text（reason）``。新しい要約は作らない・
    #498 要件5「反映されるちょうどの1行」）。
    """
    text = g.get("evidence_text") or g.get("representative") or ""
    reason = g.get("reason") or ""
    if text and reason:
        return f"{text}（{reason}）"
    return text or reason


def _group_norm_texts(g: Dict[str, Any]) -> List[str]:
    """slim group から照合対象の正規化テキスト候補を返す。

    #412 round2 [Must]D-1: 照合対象は ``idiom`` フィールドのみに限定する（``representative``
    は生の発話断片であり、「テストを書く」「確認する」のような短い一般文が多数の案を連結して
    しまう暴走の原因だった。正規化完全一致という粗い判定と組み合わさると、意味の異なる案まで
    1 成分に取り込まれ、しかも代表表示は先頭 1 件だけなのに「はい」は成分内の全 key を昇格する
    ＝人間が見ていない案まで承認されてしまう）。

    #412 round2 [Must]D-2: 既存の較正済み FP ガード ``idiom_eligible``（最小長 floor / 日常語
    stopword / 文脈固有トークンの3ゲート・#527）を再利用し、これを通らない idiom テキストは
    union に使わない（新しい閾値を発明しない）。
    """
    norm = normalize_idiom_text(g.get("idiom"))
    if norm and idiom_eligible(norm):
        return [norm]
    return []


def _extract_global_groups(
    per_pj: Dict[str, List[Dict[str, Any]]],
) -> "tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]":
    """同一テキストで繋がる group を **連結成分（connected components）** で global レーンへ
    マージし、per_pj から除外する（#412 [Must]3）。

    旧実装は「消費前」（他 text による消費を考慮する前）の distinct_slugs>=2 だけでテキスト
    ごとに独立判定していた。A の idiom が B の representative と一致（text1）、B の idiom が
    C の representative と一致（text2）という連鎖では、text1 の処理で A/B が先に消費され、
    text2 の distinct_slugs 判定は「消費前」の {B, C} で通過してしまう。しかし実際に merge
    ループへ入れるのは未消費の C だけなので、**C 1 PJ だけの group が誤って global 扱い**に
    なっていた（distinct_slugs>=2 の判定条件と実際にマージされる中身が食い違う）。

    連結成分なら「同じテキストで直接・間接に繋がっている group 全体」を 1 単位として扱うため、
    この食い違いが起きない — 成分内の distinct slug 数で判定し、成分全体を丸ごとマージする。
    """
    # ノード = (slug, idx) の1本の配列に平坦化し、union-find で連結成分を求める。
    nodes: List[tuple] = []
    node_index: Dict[tuple, int] = {}
    for slug, groups in per_pj.items():
        for idx in range(len(groups)):
            node_index[(slug, idx)] = len(nodes)
            nodes.append((slug, idx))

    parent = list(range(len(nodes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    text_to_node_ids: Dict[str, List[int]] = {}
    for slug, groups in per_pj.items():
        for idx, g in enumerate(groups):
            node_i = node_index[(slug, idx)]
            for text in _group_norm_texts(g):
                text_to_node_ids.setdefault(text, []).append(node_i)

    for _text, node_ids in text_to_node_ids.items():
        first = node_ids[0]
        for other in node_ids[1:]:
            union(first, other)

    components: Dict[int, List[int]] = {}
    for node_i in range(len(nodes)):
        root = find(node_i)
        components.setdefault(root, []).append(node_i)

    global_groups: List[Dict[str, Any]] = []
    consumed_node_ids: Set[int] = set()

    # 出現順（node id 昇順 = per_pj.items() の走査順）で成分を処理し、出力順を決定論にする。
    for root in sorted(components, key=lambda r: min(components[r])):
        member_node_ids = sorted(components[root])
        distinct_slugs: List[str] = []
        for node_i in member_node_ids:
            slug, _idx = nodes[node_i]
            if slug not in distinct_slugs:
                distinct_slugs.append(slug)
        if len(distinct_slugs) < 2:
            continue  # 1 PJ しか含まない成分は per_pj に残す（global 扱いにしない）
        # #412 round2 [Must]D-3: 成分サイズ上限。超過分は global 化せず per_pj に残す
        # （consume しない＝filtered_per_pj に自然に残る）。
        if len(member_node_ids) > MAX_GLOBAL_COMPONENT_GROUPS:
            continue

        merged_keys: List[str] = []
        keys_by_pj: Dict[str, List[str]] = {}
        merged: Optional[Dict[str, Any]] = None
        total_count = 0
        all_representatives: List[str] = []
        # ADR-054 PR2-c: 成分内の全 group の signal_meta_by_key を union する
        # （composite sort が merge 後の group でも残存 signal_keys から順位キーを
        # 計算できるようにするため）。signal_key は record 単位で一意なので衝突しない。
        merged_meta_by_key: Dict[str, Dict[str, Any]] = {}
        # #413: 代表文を PJ 別にも保持する。all_representatives は成分全体のフラット表示用、
        # reps_by_pj は「既読差し引き」用の帰属情報 — keys_by_pj と同じ粒度（origin PJ）で
        # 持たせておき、部分処理後は build_session_proposals で keys_by_pj と一緒に絞る。
        reps_by_pj: Dict[str, List[str]] = {}
        for node_i in member_node_ids:
            slug, idx = nodes[node_i]
            g = per_pj[slug][idx]
            consumed_node_ids.add(node_i)
            pj_keys = keys_by_pj.setdefault(slug, [])
            for k in g["signal_keys"]:
                if k not in merged_keys:
                    merged_keys.append(k)
                if k not in pj_keys:
                    pj_keys.append(k)
            total_count += g["count"]
            if merged is None:
                merged = dict(g)
            # #412 round2 [Must]D-4: 成分内の全 group の代表文を保持する。global group の
            # 提示は成分の先頭1件だけでなく全代表文を列挙し、「はい」で全 key を昇格する前に
            # 人間が見ていない案が含まれていないか確認できるようにする。
            rep = g.get("representative") or g.get("evidence_text") or ""
            if rep and rep not in all_representatives:
                all_representatives.append(rep)
            if rep:
                pj_reps = reps_by_pj.setdefault(slug, [])
                if rep not in pj_reps:
                    pj_reps.append(rep)
            merged_meta_by_key.update(g.get("signal_meta_by_key") or {})

        if not merged_keys or merged is None:
            continue
        merged["signal_keys"] = merged_keys
        merged["count"] = total_count
        merged["origin_pjs"] = distinct_slugs
        # #412 [Must]4: origin PJ ごとの signal_key を保持する。global group の「はい」が
        # 現在 PJ の実績として誤帰属されないよう、reflect 呼び出し時に PJ ごと分離するため。
        merged["keys_by_pj"] = keys_by_pj
        merged["all_representatives"] = all_representatives
        merged["reps_by_pj"] = reps_by_pj
        merged["signal_meta_by_key"] = merged_meta_by_key
        global_groups.append(merged)

    filtered_per_pj: Dict[str, List[Dict[str, Any]]] = {}
    for slug, groups in per_pj.items():
        remaining = [
            g for idx, g in enumerate(groups) if node_index[(slug, idx)] not in consumed_node_ids
        ]
        if remaining:
            filtered_per_pj[slug] = remaining

    return global_groups, filtered_per_pj


def build_proposal_digest(
    queue_entries: Optional[List[Dict[str, Any]]],
    *,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """queue の待ち PJ から改善案 digest を生成する（決定論・read-only・LLM 非依存）。

    Returns: {"generated_at": iso, "per_pj": {slug: [group, ...]}, "global": [group, ...],
              "project_paths": {slug: path}, "excluded_machinery_by_pj": {slug: {...}},
              "rephrase_similarity_dedup_by_pj": {slug: int},
              "excluded_context_missing_by_pj": {slug: int}, "freshness_join_stats": {...}}

    1 PJ の digest 生成が例外を投げても他 PJ の digest 生成は継続する（fail-open）。
    ``data_dir`` 未指定時は各ストアの本番既定パス（DATA_DIR 環境変数解決）を使う。
    """
    weak_signals_path = None
    idioms_path = None
    seen_path = None
    marker_base = None
    utterances_db_path: Optional[Path] = None
    if data_dir is not None:
        data_dir = Path(data_dir)
        weak_signals_path = _default_weak_signals_path(base=data_dir)
        idioms_path = _cs_store.default_idioms_path(base=data_dir)
        seen_path = _daily_review.default_seen_path(base=data_dir)
        marker_base = data_dir
        utterances_db_path = data_dir / "utterances.db"

    # ADR-054 PR2-c: 発話時刻 join は全 PJ を一度だけ read する O(U+S) 一括方式
    # （PJ ごと・group ごとに query すると全 DB 走査の反復になる）。失敗4種のうち
    # db_missing/duckdb_missing/query_error は build_uttered_at_map が判定し、
    # key_mismatch/fallback_to_detected_at は _slim_group が signal_key 単位で集計する。
    uttered_at_map, freshness_stats = _ranking.build_uttered_at_map(utterances_db_path)
    map_available = not any(
        freshness_stats.get(k) for k in ("db_missing", "duckdb_missing", "query_error")
    )
    freshness_stats.setdefault("key_mismatch", 0)
    freshness_stats.setdefault("fallback_to_detected_at", 0)

    per_pj: Dict[str, List[Dict[str, Any]]] = {}
    # codex [Must]1 是正: build_review() が返す excluded_machinery_total/by_channel
    # （#443 PR2-a・silence != evaluated）を per_pj/project_paths と同じ持ち方
    # （{slug: ...} の辞書）で digest 側にも集約する。捨てると朝の digest 経路だけ
    # 候補数が減るのに除外件数が利用者に見えなくなる。
    excluded_machinery_by_pj: Dict[str, Dict[str, Any]] = {}
    rephrase_similarity_dedup_by_pj: Dict[str, int] = {}
    # #498 要件4: 説明文を組み立てられない group（llm_judge/rephrase で reason が無い・
    # #504: prev_action は判定材料から外した）は y/n を強行せず保留にする。黙って減らさず
    # 件数を surface する（excluded_machinery_by_pj と同じ {slug: count} の流儀）。
    excluded_context_missing_by_pj: Dict[str, int] = {}
    for slug in _pj_slugs(queue_entries):
        try:
            # ADR-054 PR2-b: 順位と打ち切りを分離するため、digest 生成は必ず max_groups=None
            # （無制限）で呼ぶ。PJ ごとに切ってから global 化・既読差し引きをすると、
            # 打ち切り後の group は順位規則をどう変えても候補に入らない（issue #443 B-c）。
            # 打ち切り（表示件数の上限）は composite sort 適用後に build_session_proposals
            # 側で行う。
            review = _daily_review.build_review(
                slug,
                weak_signals_path=weak_signals_path,
                idioms_path=idioms_path,
                seen_path=seen_path,
                max_groups=None,
                dry_run=True,
                marker_base=marker_base,
            )
        except Exception:
            continue  # 他 PJ の digest を巻き添えにしない（fail-open）
        groups = [
            _slim_group(
                g,
                uttered_at_map=uttered_at_map,
                map_available=map_available,
                freshness_stats=freshness_stats,
            )
            for g in (review.get("groups") or [])
        ]
        explainable_groups = [g for g in groups if _group_has_explanation(g)]
        context_missing = len(groups) - len(explainable_groups)
        if context_missing:
            excluded_context_missing_by_pj[slug] = context_missing
        if explainable_groups:
            per_pj[slug] = explainable_groups
        machinery_total = review.get("excluded_machinery_total") or 0
        if machinery_total:
            excluded_machinery_by_pj[slug] = {
                "total": machinery_total,
                "by_channel": review.get("excluded_machinery_by_channel") or {},
            }
        rephrase_dedup_count = review.get("rephrase_similarity_dedup_count") or 0
        if rephrase_dedup_count:
            rephrase_similarity_dedup_by_pj[slug] = rephrase_dedup_count

    global_groups, per_pj = _extract_global_groups(per_pj)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "per_pj": per_pj,
        "global": global_groups,
        # #412 [Must]4: global group を PJ ごとの --project-path で正しく帰属させるための
        # 絶対パス表（queue エントリが既に持つ project_path をそのまま転記・新しい解決経路は作らない）。
        "project_paths": _pj_project_paths(queue_entries),
        # codex [Must]1（#443 PR2-a）: machinery 除外件数を slug 別に透明化する
        # （silence != evaluated）。0 件の slug はキーを持たない（他の {slug: ...} 辞書と同じ流儀）。
        "excluded_machinery_by_pj": excluded_machinery_by_pj,
        "rephrase_similarity_dedup_by_pj": rephrase_similarity_dedup_by_pj,
        # #498 要件4: 保留にした（説明不能な）group の件数を slug 別に透明化する
        # （silence != evaluated）。0 件の slug はキーを持たない。
        "excluded_context_missing_by_pj": excluded_context_missing_by_pj,
        # ADR-054 PR2-c: 発話時刻 join の失敗内訳（silence != evaluated）。
        # db_missing/duckdb_missing/query_error は 0/1（今回の digest 生成1回に対する
        # 全体障害）、key_mismatch は map 構築成功後の個別 signal_key 不一致件数、
        # fallback_to_detected_at は理由を問わない detected_at フォールバック総数。
        "freshness_join_stats": freshness_stats,
    }


def build_session_proposals(
    queue_data: Optional[Dict[str, Any]],
    pj_slug: str,
    *,
    seen_keys: Set[str],
    limit: int = MAX_SESSION_PROPOSALS,
) -> List[Dict[str, Any]]:
    """SessionStart で提示する改善案 group を返す（read-only・既読フィルタ済み）。

    per_pj[pj_slug] + global を結合し、group から既読 signal_key を差し引く（group ごと
    除外はしない）。残り key が 0 件になった group だけを除外し、**composite sort
    （``daily.proposal_ranking.composite_sort_key``）を一度だけ適用してから**先頭 ``limit``
    件を返す（ADR-054 PR2-b/PR2-c）。該当なしなら空リスト（呼び出し側が完全沈黙する）。

    #412 round2 [Must]A: 「group 内に既読キーが1つでもあれば group 全体を除外」だと、
    ``--promote-weak`` が成功キーだけ既読化する（round1 是正済み）ため、
    ``{成功キー, expired キー}`` の group は expired キーが未既読のまま group ごと消え、
    二度と再提示されなくなる。既読キーだけを差し引いて残りキーで再提示することで解消する。

    #413: global group の ``keys_by_pj``（実行コマンドの絞り込み）に加え ``reps_by_pj``
    （PJ 別代表文）も同じ既読差し引きを適用する。処理済み PJ の代表文が ``all_representatives``
    に再掲されると「もう答えた案がまた出た」という誤認を招くため、表示（代表文）と実体
    （実行コマンド）を同じ既読集合で絞る。``signal_meta_by_key`` も同じ集合で絞り、
    composite sort が既読差し引き後の残存キーだけから順位キーを計算できるようにする。

    ADR-054 PR2-b（B-d 回帰防止）: 旧実装は ``per_pj`` を先に concat し、``len(out) >= limit``
    で早期 break していたため、``per_pj`` に未既読が ``limit`` 件あると global group には
    永久に到達できなかった（global レーンが構造的に死んでいた）。**全候補を集めてから sort し、
    最後に slice する**ことで解消する（早期 break は行わない）。
    """
    if not isinstance(queue_data, dict):
        return []
    proposals = queue_data.get("proposals")
    if not isinstance(proposals, dict):
        return []

    per_pj = proposals.get("per_pj") or {}
    global_groups = proposals.get("global") or []
    candidates: List[Dict[str, Any]] = []
    if isinstance(per_pj, dict):
        candidates.extend(per_pj.get(pj_slug) or [])
    if isinstance(global_groups, list):
        candidates.extend(global_groups)

    # ADR-054 PR2-b: per_pj/global の全候補をまず集め切る（早期 break しない・B-d 回帰防止）。
    survivors: List[Dict[str, Any]] = []
    for g in candidates:
        if not isinstance(g, dict):
            continue
        keys = g.get("signal_keys") or []
        if not keys:
            continue
        remaining_keys = [k for k in keys if k not in seen_keys]
        if not remaining_keys:
            continue
        g = dict(g)
        g["signal_keys"] = remaining_keys
        # ADR-054 PR2-c: 残存キーだけの signal_meta_by_key に絞る。composite sort（キー2/3）
        # が既読差し引き後の残存キーから再計算できるようにする。
        meta_by_key = g.get("signal_meta_by_key")
        if isinstance(meta_by_key, dict):
            # set は内包表記の外で1回だけ作る（旧実装は反復ごとに再生成しており
            # group 内キー数に対して O(n^2)。PR2-b の全件化でキー数の上限も無くなった）。
            remaining_set = set(remaining_keys)
            g["signal_meta_by_key"] = {
                k: v for k, v in meta_by_key.items() if k in remaining_set
            }
        keys_by_pj = g.get("keys_by_pj")
        new_keys_by_pj: Optional[Dict[str, List[str]]] = None
        if isinstance(keys_by_pj, dict):
            new_keys_by_pj = {}
            for origin_slug, origin_keys in keys_by_pj.items():
                remaining_origin_keys = [k for k in origin_keys if k not in seen_keys]
                if remaining_origin_keys:
                    new_keys_by_pj[origin_slug] = remaining_origin_keys
            g["keys_by_pj"] = new_keys_by_pj
        # #413: keys_by_pj と同じ既読差し引きを reps_by_pj にも適用する。実行コマンドが
        # 絞られた origin PJ の代表文だけを残し、既に答えた PJ の代表文が再掲されないように
        # する（表示 = all_representatives と実体 = signal_keys/keys_by_pj の食い違い是正）。
        reps_by_pj = g.get("reps_by_pj")
        if isinstance(reps_by_pj, dict) and new_keys_by_pj is not None:
            new_reps_by_pj = {
                origin_slug: origin_reps
                for origin_slug, origin_reps in reps_by_pj.items()
                if origin_slug in new_keys_by_pj
            }
            g["reps_by_pj"] = new_reps_by_pj
            flattened_reps: List[str] = []
            for origin_reps in new_reps_by_pj.values():
                for r in origin_reps:
                    if r and r not in flattened_reps:
                        flattened_reps.append(r)
            g["all_representatives"] = flattened_reps
        survivors.append(g)

    # ADR-054 PR2-b/PR2-c: 既読差し引き後の最終集合に composite sort を一度だけ適用してから
    # limit で切る（順位と打ち切りの分離）。
    survivors.sort(key=_ranking.composite_sort_key)
    return survivors[:limit]


def _context_suffix(g: Dict[str, Any], pj_slug: str) -> str:
    """ADR-054 PR2-d: 発話の実時刻（相対表記）+ 観測/confirmed cross-PJ を1行にまとめる。

    `cross_pj_confirmed` だけを足す旧案は実データで発火0件＝実質 no-op だったため、
    観測ベース cross-PJ（global レーン）・発話の実時刻という実際に朝の y/n に欠けていた
    判断材料を出す（判断材料が無ければ空文字＝ノイズを足さない）。channel 名
    （llm_judge/rephrase 等のジャーゴン）は出さない。
    """
    parts: List[str] = []
    freshness_iso = _ranking.group_freshness_iso(g)
    if freshness_iso:
        label = _ranking.relative_time_label(freshness_iso)
        if label:
            parts.append(label)
    note = _ranking.cross_pj_note(g, pj_slug)
    if note:
        parts.append(note)
    if not parts:
        return ""
    return "（" + " ・ ".join(parts) + "）"


def _review_protocol_ref(reflect_cmd: str) -> str:
    """反映先つき4択の詳細手順（#475 §4）への絶対パス参照を組み立てる。

    ``reflect_cmd``（``.../bin/evolve-reflect``）からプラグインルートを逆算する（新しい
    解決経路は作らない — pitfall: SKILL.md script は ``${CLAUDE_PLUGIN_ROOT}`` 起点で書く、
    と同型）。逆算に失敗したら（テスト用の相対フォールバック値など）リポジトリ相対パスを返す。
    """
    try:
        plugin_root = Path(reflect_cmd).resolve().parent.parent
        candidate = plugin_root / "skills" / "evolve" / "references" / "correction-review.md"
        return str(candidate)
    except Exception:
        return "skills/evolve/references/correction-review.md"


def _material_lines(g: Dict[str, Any]) -> List[str]:
    """#498 要件1: 「なぜ拾われたか」「何回起きたか」を判断材料として出す。

    ``reason``/``count`` を配線するだけで、新しい要約は作らない（``build_review`` が既に持つ
    ``evidence`` フィールドをそのまま使う）。channel 名や類似度の数値は出さない
    （#498 要件2・review_channels.py と同じジャーゴン禁止方針）。

    #504: ``prev_action``（ツール名の連結で仕様の「1行要約」ではない）は出さない。``g`` に
    レガシー（本改修前に生成された digest snapshot 由来）の ``prev_action`` キーが残って
    いても読まない（意図的に無視する）。
    """
    lines: List[str] = []
    preview = _recorded_message_preview(g)
    if preview:
        lines.append(f"  記録される内容: 「{preview}」")
    detail_parts: List[str] = []
    count = g.get("count")
    if isinstance(count, int) and count:
        detail_parts.append(f"{count}回検知")
    if detail_parts:
        lines.append("  背景: " + "・".join(detail_parts))
    return lines


#582: 提案の必須提示項目「推奨」の契約文。SKILL.md / references/proposal-protocol.md の
# MUST one-liner と同じ要求をこの1定数に集約し、SessionStart の additionalContext へ
# 到達させる。**文言を変えるときは3箇所すべてを同時に変える**（片側 desync は
# test_reflect_choice_docs_sync / test_restore_state_session_proposals が赤にする）。
RECOMMENDATION_INSTRUCTION = (
    "各案は判断材料（記録される内容・背景）を提示したうえで、**推奨（4択のどれを選ぶ"
    "べきかと理由を1行）を必ず添える**こと。材料だけ並べて判断を丸投げしない。"
    "推せるだけの材料が無いときは「推奨なし: <理由>」と書く（空欄・省略は禁止）。"
)

# 推奨契約を語句だけ残して反転・骨抜きにする書き方を弾く（codex レビュー [Should]）。
# 契約文の後段で「ただし推奨は表示しない」等を足しても検査が通ってしまうのを防ぐ。
RECOMMENDATION_CONTRADICTIONS = (
    "推奨は表示しない",
    "推奨は省略",
    "推奨は任意",
    "推奨は書ける場合",
    "推奨を添えなくてよい",
)


def _reflect_choice_lines(
    q_reflect_cmd: str,
    keys: str,
    *,
    promote_extra: str,
    reject_pj_flag: str,
    review_ref: str,
) -> List[str]:
    """#498 要件3/5・#475 §4・#541 D: 反映先つき4択（ルールに書く/いまは反映しない/既に
    反映済み/いいえ）をはい/いいえの代わりに提示する。「ルールに書く」は記録することしか
    保証しない（＝反映済みではない）ため、選んでも実際にルール文書へ書くのはこのあと
    Claude 自身が行う作業であることを明示する（過剰約束の禁止・#498 要件3）。

    #541 D-1: 旧4択の①共通ルール／②PJルールを「①ルールに書く」1つへ統合し、空いた枠に
    ③「既に反映済み」を追加した（AskUserQuestion の options は maxItems=4 で5つ目を
    追加できないため）。反映先（共通/PJ）は①選択後に Claude が提案し、ユーザーが一言で
    直せる形にする（この2択の AskUserQuestion は再発明しない — 反映先ファイル選定と同じ
    既存の書き込み規約に委ねる）。

    #541 D-2: ③「既に反映済み」の実体は `--already-reflected-weak`
    （`daily_review.record_reviewed(decision="already_reflected")` のみ）で、
    `--promote-weak` は呼ばない。`--promote-weak` は corrections.jsonl に
    `reflect_status="promoted"` の correction を新規作成するため、そのまま使うと
    #514 の修正在庫レーンが「まだ反映されていません」と蒸し返す（再提示バグの引っ越し）。
    """
    promote_cmd = f"{q_reflect_cmd} --promote-weak {keys}{promote_extra}"
    already_reflected_cmd = f"{q_reflect_cmd} --already-reflected-weak {keys}{reject_pj_flag}"
    reject_cmd = f"{q_reflect_cmd} --reject-weak {keys}{reject_pj_flag}"
    return [
        "  この指摘をどう扱いますか？（AskUserQuestion。Other に自由記述も可）",
        "    1) ルールに書く（共通ルール／このPJのルール・あとで1コマンドで取り消せます）",
        "    2) いまは反映しない（記録のみ・AI の振る舞いは変わりません）",
        "    3) 既に反映済み（記録だけ既読にします・ルールへは書き込みません）",
        "    4) いいえ（記録も反映もしません）",
        f"  1 を選んだ場合: まず `{promote_cmd}` で記録する（この時点ではまだ"
        "ルール文書には反映されていません）。次に反映先（共通ルール／このPJのルール）を"
        "Claude が提案し、違えば一言で直せます。決まったら書く文面を自分で起草し"
        f"対象ファイルへ追記、`--apply` で反映を確認する。手順の詳細は {review_ref} の"
        "「反映先つき4択」を参照",
        f"  2 を選んだ場合: `{promote_cmd}`（記録のみ・ルールには反映されません）",
        f"  3 を選んだ場合: `{already_reflected_cmd}`（記録のみ・ルールへの反映はしません）",
        f"  4 を選んだ場合: `{reject_cmd}`",
    ]


def build_proposal_prompt(
    groups: List[Dict[str, Any]],
    pj_slug: str,
    *,
    reflect_cmd: str = "bin/evolve-reflect",
    project_paths: Optional[Dict[str, str]] = None,
) -> str:
    """AskUserQuestion 指示 + 各 group の回答コマンドを additionalContext 本文として組み立てる。

    ``reflect_cmd`` は **絶対パスで渡す**（呼び出し元 hook がプラグインルートから解決する）。
    提示先は他 PJ の cwd なので、相対 ``bin/evolve-reflect`` を埋め込むと "No such file"
    になる（pitfall: SKILL.md script は ``${CLAUDE_PLUGIN_ROOT}`` 起点で書く、と同型）。
    既定値は単体テスト・手動確認向けのフォールバック。

    #412 [Must]1: additionalContext は SessionStart 時点で Claude に届くだけで、
    「ユーザーの依頼が無いときだけ提示する」という条件はユーザーが何か打つまで永久に評価
    されない（対話ターンが始まらないため）。行動指示を「最初の応答を終えた直後に必ず提示する
    （ただし作業には割り込まない）」へ変更する。あわせて systemMessage（``build_proposal_systemmessage``）
    を同時出力し、ユーザーがコマンド無しでも中身に到達できるようにする（2チャネル同時出力）。

    #412 [Must]4: global レーンの group（``keys_by_pj`` を持つ）は origin PJ ごとに
    ``--project-path``/``--pj`` を明示したコマンド行を出す。他PJ由来の signal_key を現在 PJ の
    project_path で昇格すると、昇格した correction が誤って現在 PJ の実績として記録される
    （project_paths が無い origin は ``--project-path`` を省略し、reflect 側の
    ``CLAUDE_PROJECT_DIR``/cwd フォールバックに委ねる）。

    #498 要件5（#475 の反映先つき4択を戻す）: 素の「はい/いいえ」でなく共通ルール/PJルール/
    いまは反映しない/いいえ の4択を提示する指示に変える。draft_line 起草・ファイル追記・
    ``--apply`` は既存手順（``correction-review.md`` の「反映先つき4択」）に委ね再発明しない。
    """
    # #412 round2 [Must]C: 提示コマンドは実行ファイルパス・project_path・pj_slug・
    # signal_key を無 quoting で埋め込んでおり、空白を含む絶対パスで argparse が壊れ、
    # shell metacharacter を含む値は別コマンドとして解釈されうる。埋め込む値は全て
    # shlex.quote する（keys はカンマ区切りの1トークンとして shell に渡るため、
    # join 後の文字列をまるごと quote する）。
    q_reflect_cmd = shlex.quote(reflect_cmd)
    review_ref = _review_protocol_ref(reflect_cmd)
    lines = [
        "[evolve-anything] 改善案があります。ユーザーの最初のメッセージへの応答を終えた"
        "直後に、以下を AskUserQuestion で1件ずつ確認してください（はい/いいえの二択ではなく"
        "下記の4択）。ユーザーの依頼より先に割り込まないこと。ユーザーが提示を断ったら"
        "その場では再提示しないこと。",
        RECOMMENDATION_INSTRUCTION,
    ]
    for g in groups:
        # #412 round2 [Must]D-4: all_representatives（成分内の全 group の代表文）があれば
        # 先頭1件だけでなく全て列挙する。1件しか見せずに成分内の全 key を承認させないため。
        all_reps = g.get("all_representatives")
        if isinstance(all_reps, list) and len(all_reps) > 1:
            lines.append("- 案（複数件をまとめて確認）:")
            for r in all_reps:
                lines.append(f"  - {r}")
        else:
            rep = g.get("representative") or g.get("evidence_text") or ""
            lines.append(f"- 案: {rep}")
        # #498 要件1: 何をしている時に・なぜ・何回、を判断材料として添える。
        lines.extend(_material_lines(g))
        # ADR-054 PR2-d: 発話の実時刻・観測/confirmed cross-PJ を判断材料として添える。
        context = _context_suffix(g, pj_slug)
        if context:
            lines.append(f"  {context}")
        keys_by_pj = g.get("keys_by_pj")
        if isinstance(keys_by_pj, dict) and keys_by_pj:
            for origin_slug, origin_keys in keys_by_pj.items():
                keys = shlex.quote(",".join(origin_keys))
                q_origin_slug = shlex.quote(origin_slug)
                origin_path = (project_paths or {}).get(origin_slug)
                path_flag = f" --project-path {shlex.quote(origin_path)}" if origin_path else ""
                lines.append(f"  対象PJ: {origin_slug}")
                lines.extend(_reflect_choice_lines(
                    q_reflect_cmd, keys,
                    promote_extra=f"{path_flag} --pj {q_origin_slug}",
                    reject_pj_flag=f" --pj {q_origin_slug}",
                    review_ref=review_ref,
                ))
        else:
            keys = shlex.quote(",".join(g.get("signal_keys") or []))
            q_pj_slug = shlex.quote(pj_slug)
            lines.extend(_reflect_choice_lines(
                q_reflect_cmd, keys,
                promote_extra="",
                reject_pj_flag=f" --pj {q_pj_slug}",
                review_ref=review_ref,
            ))
    return "\n".join(lines)


def build_proposal_systemmessage(
    groups: List[Dict[str, Any]],
    *,
    excluded_machinery: int = 0,
    excluded_context_missing: int = 0,
    pj_slug: Optional[str] = None,
) -> str:
    """改善案の代表テキストを systemMessage（user 可視チャネル）本文として組み立てる（#412 [Must]1）。

    additionalContext（Claude 可視）だけでは、ユーザーがコマンドを打つまで中身が見えない。
    先頭 ``MAX_SESSION_PROPOSALS`` 件の代表テキストを並べて可視化する。ADR-038 の代替案C
    （両チャネル同時出力）と同型。

    #412 round2 [Should]E: 「この後 y/n で確認します」は additionalContext 側の prompt
    instruction 遵守に依存しており機械的に保証できない。実際に保証できるのは「応答の後で
    採否を聞く」という意図の伝達までで、聞き逃された場合は次回また出る（表示されなかった
    場合に何が起きるかを正確に書く）。

    ``excluded_machinery``（codex [Must]1・#443 PR2-a）: この PJ の digest 生成時に machinery
    （委譲メッセージ等の harness 注入）を理由に候補から除外した件数。>0 のときだけ末尾に
    1行添える（silence != evaluated）。0 のときは従来どおりノイズを足さない。

    ``excluded_context_missing``（#498 要件4）: 説明文を組み立てられず y/n を保留した件数。
    >0 のときだけ末尾に1行添える（silence != evaluated）。0 のときはノイズを足さない。

    ``pj_slug``（ADR-054 PR2-d）: 渡すと先頭 group（``groups`` は呼び出し側で composite sort
    済みの前提＝最優先の1件）の発話の実時刻・cross-PJ 判断材料を末尾に添える。省略時
    （後方互換）は従来どおり付けない。additionalContext（``build_proposal_prompt``）側は
    全 group に付くのに対し、systemMessage は概要チャネルなので先頭1件だけに絞る。
    """
    reps: List[str] = []
    for g in groups[:MAX_SESSION_PROPOSALS]:
        # #412 round2 [Must]D-4: all_representatives（成分内の全 group の代表文）があれば
        # 先頭1件だけでなく全て列挙する。
        all_reps = g.get("all_representatives")
        if isinstance(all_reps, list) and all_reps:
            for r in all_reps:
                r = (r or "").strip()
                if r and r not in reps:
                    reps.append(r)
        else:
            rep = (g.get("representative") or g.get("evidence_text") or "").strip()
            if rep and rep not in reps:
                reps.append(rep)
    if not reps:
        base = (
            "[evolve-anything] 改善案があります。応答のあとで採否をお聞きします。"
            "表示されなかった場合は未処理のまま次回また出ます。"
        )
    else:
        joined = " / ".join(f"「{r}」" for r in reps)
        base = (
            f"[evolve-anything] 改善案があります: {joined}。応答のあとで採否をお聞きします。"
            "表示されなかった場合は未処理のまま次回また出ます。"
        )
    if pj_slug is not None and groups:
        # ADR-054 PR2-d: 先頭（最優先）group の判断材料だけ添える。
        context = _context_suffix(groups[0], pj_slug)
        if context:
            base += f" {context}"
    if excluded_machinery > 0:
        base += (
            f" （machinery 除外 {excluded_machinery} 件は委譲メッセージ等の harness 注入のため"
            "候補に含まれていません・実際に確認可能な件数には含まれていません・#443）"
        )
    if excluded_context_missing > 0:
        base += (
            f" （説明材料が無く保留 {excluded_context_missing} 件は今回の確認対象に含まれて"
            "いません・#498）"
        )
    # #503 §3.1-5': y/n が来なかったときに利用者が取れる手段を明示する（pull 導線）。
    # E8「提示が無かった」の再発時、利用者側から拾い直せるようにする。
    base += " 聞かれなければ『改善案を教えて』と言ってください。"
    return base
