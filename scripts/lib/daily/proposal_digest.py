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

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from correction_semantic import daily_review as _daily_review
from correction_semantic import store as _cs_store
from correction_semantic.store import normalize_idiom_text
from weak_signals.store import default_store_path as _default_weak_signals_path

# 1 PJ あたり digest に載せる group の上限（build_review にそのまま渡す・SKILL.md の
# 「今日の修正確認」既定 max_groups=5 より絞る — セッション開始時提示は既読フィルタ後に
# さらに MAX_SESSION_PROPOSALS で絞られるため、digest 段階では複数セッション分の候補を
# 持たせる程度で十分）。
DEFAULT_MAX_PER_PJ = 3

# 1 セッションで提示する改善案の上限（受け入れ条件「1セッションの提示件数が上限を超えない」）。
MAX_SESSION_PROPOSALS = 2

_EVIDENCE_TEXT_TRUNC = 200
_PREV_ACTION_TRUNC = 120


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


def _slim_group(g: Dict[str, Any]) -> Dict[str, Any]:
    """daily_review group を提示に必要な最小限へ縮める。"""
    evidence = g.get("evidence") or {}
    count = evidence.get("count")
    if not isinstance(count, int):
        count = len(g.get("signal_keys") or [])
    return {
        "signal_keys": list(g.get("signal_keys") or []),
        "representative": g.get("representative", ""),
        "idiom": g.get("idiom"),
        "confirmable_idiom": g.get("confirmable_idiom"),
        "channel": g.get("channel", ""),
        "count": count,
        "evidence_text": _truncate(evidence.get("text", ""), _EVIDENCE_TEXT_TRUNC),
        "prev_action": _truncate(evidence.get("prev_action", ""), _PREV_ACTION_TRUNC),
    }


def _group_norm_texts(g: Dict[str, Any]) -> List[str]:
    """slim group から照合対象の正規化テキスト候補を返す（cross_pj_priority と同方針）。"""
    out: List[str] = []
    for key in ("idiom", "representative"):
        norm = normalize_idiom_text(g.get(key))
        if norm and norm not in out:
            out.append(norm)
    return out


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

        merged_keys: List[str] = []
        keys_by_pj: Dict[str, List[str]] = {}
        merged: Optional[Dict[str, Any]] = None
        total_count = 0
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

        if not merged_keys or merged is None:
            continue
        merged["signal_keys"] = merged_keys
        merged["count"] = total_count
        merged["origin_pjs"] = distinct_slugs
        # #412 [Must]4: origin PJ ごとの signal_key を保持する。global group の「はい」が
        # 現在 PJ の実績として誤帰属されないよう、reflect 呼び出し時に PJ ごと分離するため。
        merged["keys_by_pj"] = keys_by_pj
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
    max_per_pj: int = DEFAULT_MAX_PER_PJ,
) -> Dict[str, Any]:
    """queue の待ち PJ から改善案 digest を生成する（決定論・read-only・LLM 非依存）。

    Returns: {"generated_at": iso, "per_pj": {slug: [group, ...]}, "global": [group, ...]}

    1 PJ の digest 生成が例外を投げても他 PJ の digest 生成は継続する（fail-open）。
    ``data_dir`` 未指定時は各ストアの本番既定パス（DATA_DIR 環境変数解決）を使う。
    """
    weak_signals_path = None
    idioms_path = None
    seen_path = None
    marker_base = None
    if data_dir is not None:
        data_dir = Path(data_dir)
        weak_signals_path = _default_weak_signals_path(base=data_dir)
        idioms_path = _cs_store.default_idioms_path(base=data_dir)
        seen_path = _daily_review.default_seen_path(base=data_dir)
        marker_base = data_dir

    per_pj: Dict[str, List[Dict[str, Any]]] = {}
    for slug in _pj_slugs(queue_entries):
        try:
            review = _daily_review.build_review(
                slug,
                weak_signals_path=weak_signals_path,
                idioms_path=idioms_path,
                seen_path=seen_path,
                max_groups=max_per_pj,
                dry_run=True,
                marker_base=marker_base,
            )
        except Exception:
            continue  # 他 PJ の digest を巻き添えにしない（fail-open）
        groups = [_slim_group(g) for g in (review.get("groups") or [])]
        if groups:
            per_pj[slug] = groups

    global_groups, per_pj = _extract_global_groups(per_pj)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "per_pj": per_pj,
        "global": global_groups,
        # #412 [Must]4: global group を PJ ごとの --project-path で正しく帰属させるための
        # 絶対パス表（queue エントリが既に持つ project_path をそのまま転記・新しい解決経路は作らない）。
        "project_paths": _pj_project_paths(queue_entries),
    }


def build_session_proposals(
    queue_data: Optional[Dict[str, Any]],
    pj_slug: str,
    *,
    seen_keys: Set[str],
    limit: int = MAX_SESSION_PROPOSALS,
) -> List[Dict[str, Any]]:
    """SessionStart で提示する改善案 group を返す（read-only・既読フィルタ済み）。

    per_pj[pj_slug] + global を結合し、signal_keys が 1 つでも既読なら group ごと除外し、
    先頭 ``limit`` 件を返す。該当なしなら空リスト（呼び出し側が完全沈黙する）。
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

    out: List[Dict[str, Any]] = []
    for g in candidates:
        if not isinstance(g, dict):
            continue
        keys = g.get("signal_keys") or []
        if not keys:
            continue
        if any(k in seen_keys for k in keys):
            continue
        out.append(g)
        if len(out) >= limit:
            break
    return out


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
    """
    lines = [
        "[evolve-anything] 改善案があります。ユーザーの最初のメッセージへの応答を終えた"
        "直後に、以下を AskUserQuestion で1件ずつ y/n 提示してください。ユーザーの依頼より"
        "先に割り込まないこと。ユーザーが提示を断ったらその場では再提示しないこと。",
    ]
    for g in groups:
        rep = g.get("representative") or g.get("evidence_text") or ""
        lines.append(f"- 案: {rep}")
        keys_by_pj = g.get("keys_by_pj")
        if isinstance(keys_by_pj, dict) and keys_by_pj:
            for origin_slug, origin_keys in keys_by_pj.items():
                keys = ",".join(origin_keys)
                origin_path = (project_paths or {}).get(origin_slug)
                path_flag = f" --project-path {origin_path}" if origin_path else ""
                lines.append(
                    f"  はい({origin_slug}): {reflect_cmd} --promote-weak {keys}"
                    f"{path_flag} --pj {origin_slug}"
                )
                lines.append(
                    f"  いいえ({origin_slug}): {reflect_cmd} --reject-weak {keys} --pj {origin_slug}"
                )
        else:
            keys = ",".join(g.get("signal_keys") or [])
            lines.append(f"  はい: {reflect_cmd} --promote-weak {keys}")
            lines.append(f"  いいえ: {reflect_cmd} --reject-weak {keys} --pj {pj_slug}")
    return "\n".join(lines)


def build_proposal_systemmessage(groups: List[Dict[str, Any]]) -> str:
    """改善案の代表テキストを systemMessage（user 可視チャネル）本文として組み立てる（#412 [Must]1）。

    additionalContext（Claude 可視）だけでは、ユーザーがコマンドを打つまで中身が見えない。
    先頭 ``MAX_SESSION_PROPOSALS`` 件の代表テキストを並べて可視化し、「この後 y/n で確認する」
    ことを伝える。ADR-038 の代替案C（両チャネル同時出力）と同型。
    """
    reps = []
    for g in groups[:MAX_SESSION_PROPOSALS]:
        rep = (g.get("representative") or g.get("evidence_text") or "").strip()
        if rep:
            reps.append(rep)
    if not reps:
        return "[evolve-anything] 改善案があります。この後 y/n で確認します。"
    joined = " / ".join(f"「{r}」" for r in reps)
    return f"[evolve-anything] 改善案があります: {joined}。この後 y/n で確認します。"
