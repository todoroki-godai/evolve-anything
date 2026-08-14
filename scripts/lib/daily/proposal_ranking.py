"""daily.proposal_ranking — 朝の提示の順位付け（ADR-054 PR2-c）。

``daily.proposal_digest`` から composite sort・utterances.db 発話時刻 join のロジックを
切り出した**純リファクタ分割**（振る舞い不変・#379 新設凍結に非抵触）。``proposal_digest.py``
が 500 行を超えつつあり（``line_limit.MAX_PYTHON_SOURCE_LINES``）、これ以上ランキングロジックを
足すと 800 行の分割必須ラインに触れるため、``fleet/queue_materials.py`` / `evolve/__init__.py`
と同じ手法で先に切り出す。新しい store / observability section / advisory adapter /
weak_signal channel は増やさない。

## composite sort の4キー（この順序で厳密に。設計 docs/decisions/drafts/054-phase-be-design.md）

1. PJ 横断で見えているか（``cross_pj_confirmed`` が非空 **または** global レーン所属）。
   confirmed（他 PJ で human が y を押した idiom と一致）と global（idiom テキストが2 PJ 以上で
   観測された連結成分）は**別物**なので or を取る（同一視しない）。
2. 再発回数（``len(signal_keys)``）
3. 鮮度（**発話時刻**。``uttered_at``（utterances.db read 時 join）優先、無ければ
   ``detected_at``（judge の判定時刻。発話時刻ではない）にフォールバック）
4. 決定論の担保（``min(signal_keys)``）

既読差し引き後の再計算を可能にするため、composite_sort_key は呼び出し側が既に既読差し引き
済みの ``signal_keys``/``signal_meta_by_key`` を持つ group を受け取る前提で、残存キーだけから
都度計算する（別途「再計算」ステップを持たない — 呼ぶたびに最新の残存キーで計算される）。

## 発話時刻 join のバッチ設計

PJ ごと・group ごとに ``query_utterances`` を呼ぶと全 DB 走査の反復になるため、
``build_uttered_at_map`` は **全 PJ を一度だけ read** し、物理PK ``(source_path, line_no)``
→ ``timestamp`` の map を作る O(U+S) の一括方式にする（``daily.proposal_digest`` が
digest 生成時に1回だけ呼ぶ）。DuckDB は ``query_utterances_all_projects`` 経由で
``read_only=True`` で開く（pitfall_duckdb_read_opens_readwrite 準拠、呼び出し先の契約）。

失敗は4種に区別する（silence != evaluated）: ``db_missing`` / ``duckdb_missing`` /
``query_error`` はいずれか1つが起きた回の digest 全体に効く 0/1 フラグ、``key_mismatch`` は
map 構築に成功したが個別の signal_key の物理キーが見つからなかった件数、
``fallback_to_detected_at`` は理由を問わず detected_at へフォールバックした signal_key の
総数（①②③ が起きた回は全 signal_key がここに乗る）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _parse_iso_epoch(value: Optional[str]) -> Optional[float]:
    """ISO8601 文字列を epoch 秒に変換する。`Z` 終端を吸収。parse 不能なら None。"""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def is_global_group(group: Dict[str, Any]) -> bool:
    """group が global レーン所属（``daily.proposal_digest._extract_global_groups`` の結果）か。

    ``origin_pjs``（2 PJ 以上の連結成分から merge されたときだけ付与される）の有無で判定する。
    """
    return bool(group.get("origin_pjs"))


def _group_freshness_epoch(group: Dict[str, Any]) -> float:
    """group の残存 signal_keys から鮮度（uttered_at 優先・無ければ detected_at）の max を返す。

    どの signal_key も時刻が parse できなければ 0.0（同 tier 内で最後に回る安全側）。
    """
    meta_map = group.get("signal_meta_by_key") or {}
    keys = group.get("signal_keys") or []
    best = 0.0
    for key in keys:
        meta = meta_map.get(key) or {}
        ts = meta.get("uttered_at") or meta.get("detected_at")
        epoch = _parse_iso_epoch(ts)
        if epoch is not None and epoch > best:
            best = epoch
    return best


def composite_sort_key(group: Dict[str, Any]) -> Tuple[int, int, float, str]:
    """4キーの composite sort key を返す（昇順 sort でそのまま優先順位順になる）。

    呼び出し側（``sorted(candidates, key=composite_sort_key)``）は既読差し引き後の
    ``signal_keys``/``signal_meta_by_key`` を渡すこと。残存キーだけから毎回計算するため、
    既読差し引き後に別途「再計算」する必要はない。
    """
    keys = group.get("signal_keys") or []
    cross_pj_confirmed = group.get("cross_pj_confirmed") or []
    # キー1: confirmed（cross_pj_confirmed 非空）または global 所属（別物なので or）。
    tier1 = bool(cross_pj_confirmed) or is_global_group(group)
    # キー2: 再発回数（残存 signal_keys 件数）。
    count = len(keys)
    # キー3: 鮮度（発話時刻優先・detected_at フォールバック）。
    freshness = _group_freshness_epoch(group)
    # キー4: 決定論の担保。signal_keys が空（本来は呼び出し側で除外済みのはず）でも
    # クラッシュしないよう安全側の空文字にする。
    min_key = min(keys) if keys else ""
    return (0 if tier1 else 1, -count, -freshness, min_key)


# ─────────────────────────────────────────────────────────────────
# 発話時刻 join（O(U+S) 一括方式・失敗4種の区別）
# ─────────────────────────────────────────────────────────────────
_STATS_KEYS = ("db_missing", "duckdb_missing", "query_error")


def _empty_stats() -> Dict[str, int]:
    return {k: 0 for k in _STATS_KEYS}


def build_uttered_at_map(
    db_path: Optional[Path] = None,
) -> Tuple[Dict[Tuple[str, int], str], Dict[str, int]]:
    """utterances.db を1回だけ read し、物理PK → 発話時刻（timestamp）の map を返す。

    Returns: ``(map, stats)``。``map`` は ``{(source_path, line_no): timestamp}``。
    ``stats`` は ``{"db_missing": 0|1, "duckdb_missing": 0|1, "query_error": 0|1}``
    （①②③ のいずれか1つだけが 1 になる。個別キー不一致・フォールバック件数は
    呼び出し側 ``daily.proposal_digest`` が map 適用時に集計する — 本関数は map 構築のみ
    の責務）。db_path 未指定は production 既定（``utterance_archive.ingest.default_db_path``）。
    """
    stats = _empty_stats()
    try:
        from utterance_archive import store as _ustore
    except Exception:
        stats["duckdb_missing"] = 1
        return {}, stats
    if not _ustore.HAS_DUCKDB:
        stats["duckdb_missing"] = 1
        return {}, stats

    if db_path is None:
        try:
            from utterance_archive.ingest import default_db_path
            db_path = default_db_path()
        except Exception:
            stats["db_missing"] = 1
            return {}, stats
    db_path = Path(db_path)
    if not db_path.exists():
        stats["db_missing"] = 1
        return {}, stats

    try:
        from utterance_archive.query import query_utterances_all_projects
        rows = query_utterances_all_projects(db_path=db_path)
    except Exception:
        stats["query_error"] = 1
        return {}, stats

    out: Dict[Tuple[str, int], str] = {}
    for r in rows:
        sp = r.get("source_path")
        ln = r.get("line_no")
        ts = r.get("timestamp")
        if sp is None or ln is None or not ts:
            continue
        try:
            key = (sp, int(ln))
        except (TypeError, ValueError):
            continue
        # 同一物理キーの複数行は最初勝ち（query の ORDER BY session_id, timestamp, line_no
        # 順で決定論。物理 PK 契約上は本来1行だが、防御的に dedup する）。
        out.setdefault(key, ts)
    return out, stats


def lookup_uttered_at(
    uttered_at_map: Dict[Tuple[str, int], str],
    source_path: Optional[str],
    line_no: Any,
) -> Optional[str]:
    """物理キーで uttered_at_map を引く。型不正・未登録は None（呼び出し側が detected_at
    フォールバック + 統計をカウントする）。
    """
    if source_path is None or line_no is None:
        return None
    try:
        return uttered_at_map.get((source_path, int(line_no)))
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────
# 相対時刻表示（PR2-d: 提示文の判断材料）
# ─────────────────────────────────────────────────────────────────
def relative_time_label(
    value: Optional[str], *, now: Optional[datetime] = None,
) -> Optional[str]:
    """発話時刻（ISO8601）を「3週間前の発話」のような相対表記にする。parse 不能は None。

    しきい値: 1日未満=今日 / 14日未満=N日前 / 60日未満=N週間前 / それ以上=Nヶ月前。
    judge ラグがある以上、古い文脈の指摘に無自覚に y を押させない安全弁として使う
    （ADR-054 PR2-d）。
    """
    epoch = _parse_iso_epoch(value)
    if epoch is None:
        return None
    now_epoch = (now or datetime.now(timezone.utc)).timestamp()
    delta_days = max(0.0, now_epoch - epoch) / 86400.0
    if delta_days < 1:
        return "今日の発話"
    if delta_days < 14:
        return f"{int(delta_days)}日前の発話"
    if delta_days < 60:
        return f"{int(delta_days // 7)}週間前の発話"
    return f"{max(1, int(delta_days // 30))}ヶ月前の発話"

