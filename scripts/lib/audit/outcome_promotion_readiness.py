"""ADR-046 重み昇格レディネスの決定論判定（#461, advisory）。

ADR-046 は outcome 3軸（correction 再発率 / 一発成功率 / rework 率近似）を
environment fitness の重みへ繰り入れてよい条件を3つ定めた。本モジュールは
その3条件を既存ストアのみから LLM 非依存・決定論で測定する:

  1. 分散が十分    — 軸値が全 PJ で同値でない（全 PJ 同値 = 測定バグ強シグナル、
                     measurement_bug #445 の思想を流用）。
  2. データ件数下限 — 軸の分母（暫定 correction≥10 / sessions≥30）を満たす PJ が複数ある。
  3. 方向の妥当性  — env 改善イベント（reflect/evolve 適用 = optimize_history の
                     human_accepted=True）を anchor に前後窓で軸値を比較し、期待方向へ
                     動く相関が見える。

3条件すべて pass で初めて「重み昇格を提案」する（advisory 表示のみ。重みには入れない）。
判断期日（2026-06-24〜07-08頃）に人が勘で判断するのを防ぐ機構。

データ契約（実ストアの実際の列。outcome_metrics #423 と共有 + per-PJ 識別子を追加で読む）:
  - corrections.jsonl: correction_type / session_id / timestamp / **project_path**（PJ 識別）
  - sessions.jsonl   : error_count / tool_sequence / timestamp / **project**（PJ 識別）
  - optimize_history/<slug>.jsonl: human_accepted(bool) / timestamp（apply イベント anchor）

軸計算の素（窓判定・編集バースト等）は outcome_metrics の純ヘルパを再利用する
（重複実装を避ける）。決定論・LLM 非依存。読み取りのみ（書込は一切しない）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import outcome_metrics as _om

# テストは ``monkeypatch.setattr(outcome_promotion_readiness, "DATA_DIR", tmp_path)`` で
# 直接この module 属性を差し替える（文字列ターゲット patch を避ける既知 pitfall 準拠）。
try:
    from rl_common import DATA_DIR
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"

# ADR-046 の暫定下限。correction 系軸は分母 = correction 件数、session 系軸は分母 = session 数。
CORRECTION_FLOOR = 10
SESSION_FLOOR = 30

# 「複数 PJ」= 2 PJ 以上（ADR-046）。
_MIN_PJ = 2

# 条件3 の前後窓のデフォルト幅（日）。
# ADR-044 準拠で実 PJ データ dry-run の観察値から決める。apply イベント（optimize_history）の
# 蓄積が薄いため、anchor 前後で十分なサンプルを拾える広めの窓を既定にする（#461 実測で確定）。
DEFAULT_WINDOW_DAYS = 14

# corrections / sessions レコードの PJ 識別フィールド（実ストアの実列に合わせる）。
_CORRECTION_PJ_FIELDS = ("project_path", "project", "project_name")
_SESSION_PJ_FIELDS = ("project", "project_name", "project_path")

# 軸ごとの「期待方向」。期待方向に値が動いた = 環境改善（apply）が効いた証拠。
#   correction_recurrence: 低いほど良い → after < before が期待
#   first_try_success    : 高いほど良い → after > before が期待
#   rework               : 低いほど良い → after < before が期待
_AXIS_BETTER_WHEN_LOWER = {
    "correction_recurrence": True,
    "first_try_success": False,
    "rework": True,
}


# ─── per-PJ helpers ──────────────────────────────────────────────────────────


def _pj_of(rec: Dict[str, Any], fields) -> str:
    """レコードの PJ 識別フィールドを worktree 安全な slug に正規化して返す（#593）。

    フィールド優先順（correction は project_path 優先 / session は project 優先）を
    維持したまま、最初に値のあるフィールドを ``outcome_metrics._normalize_pj``
    （= ``pj_slug_from_cwd``）経由で正規化する。これにより worktree フルパス
    （例 ``/x/amamo/.claude/worktrees/evolve``）が本体 repo slug（``amamo``）に畳まれ、
    幻の別PJ slug が cross-PJ 統計に混入しなくなる。読み取り時正規化なので既存データ
    込みで即解消する。同パッケージ ``outcome_metrics`` の ``_project_match`` と同方式
    （新しい正規化を発明しない）。空値はスキップし、どの候補も無ければ "" を返す。
    """
    for f in fields:
        v = rec.get(f)
        if v:
            return _om._normalize_pj(str(v)) or ""
    return ""


def _records_in_window(store: str, days: int, base: Path) -> List[Dict[str, Any]]:
    since = _om._iso_days_ago(days)
    return [
        r for r in _om._read_jsonl(base / store)
        if _om._in_window(_om._ts_of(r, "timestamp", "first_timestamp"), since)
    ]


def _sessions_in_window(days: int, base: Path) -> List[Dict[str, Any]]:
    """#469: session レコードは union read（db + 未 ingest jsonl）で取得する。

    sessions.jsonl 直読（``_records_in_window("sessions.jsonl", ...)``）だと #415 の
    rotate で live jsonl がほぼ空になり、条件2（sessions≥30 分母）と条件3（前後窓 paired
    session）が構造的に常に空 = 永遠に ✗ になっていた。読み取り経路は session_store に
    1 つだけ実装し outcome_metrics 経由で共有する（二重実装回避）。
    """
    since = _om._iso_days_ago(days)
    return [
        r for r in _om.read_sessions(base)
        if _om._in_window(_om._ts_of(r, "timestamp", "first_timestamp"), since)
    ]


def _base(data_dir: Optional[Path]) -> Path:
    return data_dir if data_dir is not None else DATA_DIR


def per_pj_correction_recurrence(
    days: int = 30, *, data_dir: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """PJ 別の correction 再発率 + 分母（correction 件数）。

    Returns: {pj: {"value": float, "denominator": int, "distinct_types": int}}。
    """
    base = _base(data_dir)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in _records_in_window("corrections.jsonl", days, base):
        pj = _pj_of(r, _CORRECTION_PJ_FIELDS)
        if not pj:
            continue
        grouped.setdefault(pj, []).append(r)

    out: Dict[str, Dict[str, Any]] = {}
    for pj, recs in grouped.items():
        sessions_by_type: Dict[str, set] = {}
        for r in recs:
            ctype = r.get("correction_type")
            if not ctype:
                continue
            sessions_by_type.setdefault(ctype, set()).add(r.get("session_id") or "")
        if not sessions_by_type:
            continue
        distinct = len(sessions_by_type)
        recurring = sum(1 for s in sessions_by_type.values() if len(s) >= 2)
        out[pj] = {
            "value": round(recurring / distinct, 4),
            "denominator": len(recs),
            "distinct_types": distinct,
        }
    return out


def per_pj_first_try_success(
    days: int = 30, *, data_dir: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """PJ 別の一発成功率 + 分母（session 数）。

    Returns: {pj: {"value": float, "denominator": int}}。
    """
    base = _base(data_dir)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in _sessions_in_window(days, base):
        pj = _pj_of(r, _SESSION_PJ_FIELDS)
        if not pj:
            continue
        grouped.setdefault(pj, []).append(r)

    out: Dict[str, Dict[str, Any]] = {}
    for pj, recs in grouped.items():
        scored = [r for r in recs if r.get("error_count") is not None]
        if not scored:
            continue
        clean = sum(1 for r in scored if r.get("error_count") == 0)
        out[pj] = {"value": round(clean / len(scored), 4), "denominator": len(scored)}
    return out


def per_pj_rework(
    days: int = 30, *, min_consecutive: int = 3, data_dir: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """PJ 別の rework 率(近似) + 分母（編集ありセッション数）。

    Returns: {pj: {"value": float, "denominator": int}}。
    """
    base = _base(data_dir)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for r in _sessions_in_window(days, base):
        pj = _pj_of(r, _SESSION_PJ_FIELDS)
        if not pj:
            continue
        grouped.setdefault(pj, []).append(r)

    out: Dict[str, Dict[str, Any]] = {}
    for pj, recs in grouped.items():
        edit_sessions = 0
        rework_sessions = 0
        for r in recs:
            seq = r.get("tool_sequence")
            if not isinstance(seq, list) or not seq:
                continue
            if not any(t in _om._EDIT_TOOLS for t in seq):
                continue
            edit_sessions += 1
            if _om._has_edit_burst(seq, min_consecutive):
                rework_sessions += 1
        if not edit_sessions:
            continue
        # #569: edit_sessions が floor 未満なら rework 率は分母数件で 0.0/1.0 に振れ
        # 統計的に無意味。value=None + sample_insufficient で明示し、将来 rework を gate
        # 条件に組み込んでも #563 と同じ分母1の 1.0 張り付き FP を再発させない。floor は
        # correction_recurrence(#563-2) / outcome_metrics(#529-2) と同一定数を使う。
        insufficient = edit_sessions < _om.MIN_EDIT_SESSIONS_FLOOR
        out[pj] = {
            "value": None if insufficient else round(rework_sessions / edit_sessions, 4),
            "denominator": edit_sessions,
            "sample_insufficient": insufficient,
        }
    return out


# ─── 条件1: 分散が十分 ────────────────────────────────────────────────────────


def check_variance(per_pj_value: Dict[str, float]) -> Dict[str, Any]:
    """軸値が全 PJ で同値でないか（分散十分か）を判定する。

    measurement_bug #445 の思想を流用: 全 PJ が bit-exact に揃う = 測定バグ強シグナル。
    PJ が 2 未満なら分散を語れない（insufficient_pj）。
    """
    values = list(per_pj_value.values())
    if len(values) < _MIN_PJ:
        return {"pass": False, "reason": "insufficient_pj", "pj_count": len(values)}
    if len(set(values)) == 1:
        return {
            "pass": False,
            "reason": "all_identical",
            "pj_count": len(values),
            "value": values[0],
        }
    return {"pass": True, "pj_count": len(values), "distinct_values": len(set(values))}


# ─── 条件2: データ件数下限 ────────────────────────────────────────────────────


def check_denominators(denom_by_pj: Dict[str, int], *, floor: int) -> Dict[str, Any]:
    """分母 floor を満たす PJ が複数（≥2）あるかを判定する。"""
    meeting = sorted(pj for pj, n in denom_by_pj.items() if n >= floor)
    return {
        "pass": len(meeting) >= _MIN_PJ,
        "floor": floor,
        "meeting": meeting,
        "denominators": dict(sorted(denom_by_pj.items())),
    }


# ─── 条件3: 方向の妥当性 ──────────────────────────────────────────────────────


def _load_apply_anchors(base: Path) -> Dict[str, List[str]]:
    """optimize_history/<slug>.jsonl から human_accepted=True の timestamp を PJ 別に集める。

    apply イベント（reflect/evolve 適用）の anchor。ファイル名 stem が PJ slug。

    #24: 書込側（optimize_history_store.resolve_slug = git-common-dir authoritative）が
    worktree 安全 slug を出すのが原則だが、write-side fix 以前に書かれた legacy ファイルや
    フルパス痕跡 stem が混じると幻PJ として cross-PJ anchor 統計を汚す。読み取り時にも
    ``_normalize_pj``（= corrections/sessions の PJ キー正規化と同一関数）で stem を畳んで
    二重防御する。clean な slug は normalize しても同値なので無害。
    """
    hist_dir = base / "optimize_history"
    if not hist_dir.is_dir():
        return {}
    anchors: Dict[str, List[str]] = {}
    for path in sorted(hist_dir.glob("*.jsonl")):
        slug = _om._normalize_pj(path.stem) or path.stem
        for rec in _om._read_jsonl(path):
            if rec.get("human_accepted") is True:
                ts = _om._ts_of(rec, "timestamp")
                if ts:
                    anchors.setdefault(slug, []).append(ts)
    return anchors


# worktree ディレクトリ名の典型 prefix（CC の worktree 隔離が生成する命名）。
# optimize_history の slug stem がこれに該当したら、本体 repo 名に正規化されず worktree
# ディレクトリ名がそのまま slug 化された汚染（#24）の強いシグナル。
_WORKTREE_NAME_PREFIXES = ("agent-", "worktree-agent-", "worktree-")

# 意図的な保全 slug（worktree 名ではない）。検出から除外する。
_UNATTRIBUTED_SLUG = "_unattributed"


def detect_worktree_name_slugs(data_dir: Optional[Path] = None) -> List[str]:
    """optimize_history に worktree ディレクトリ名 stem の slug ファイルが混じっていないか
    健全性チェックする（#24・書込側正規化漏れ / legacy 汚染の可視化）。

    検出シグナル: slug stem が worktree ディレクトリ名の典型 prefix
    （``agent-`` / ``worktree-agent-`` / ``worktree-``）で始まる。これらは
    ``git rev-parse --show-toplevel`` の basename を slug 化してしまう既知の罠
    （pitfall_worktree_slug_show_toplevel）で本体 repo 名へ正規化されなかった痕跡。

    Returns: 疑わしい slug（stem）のソート済みリスト。clean なら []。
    読み取りのみ・決定論・LLM 非依存。``_unattributed`` 等の意図的 slug は除外。
    """
    base = _base(data_dir)
    hist_dir = base / "optimize_history"
    if not hist_dir.is_dir():
        return []
    suspects: List[str] = []
    for path in sorted(hist_dir.glob("*.jsonl")):
        stem = path.stem
        if stem == _UNATTRIBUTED_SLUG:
            continue
        if any(stem.startswith(p) for p in _WORKTREE_NAME_PREFIXES):
            suspects.append(stem)
    return sorted(suspects)


def _axis_value_in_range(
    records: List[Dict[str, Any]],
    pj_fields,
    slug: str,
    lo: str,
    hi: str,
    axis_kind: str,
    *,
    min_consecutive: int = 3,
) -> Optional[float]:
    """slug の records のうち [lo, hi) 窓に入るものから 1 軸値を算出する。

    axis_kind: "first_try_success" のみ session ベースで実装（apply 前後比較に十分な
    分母が取れる軸）。anchor 前後の sample が無ければ None。
    """
    sel = []
    for r in records:
        if _pj_of(r, pj_fields) != slug:
            continue
        ts = _om._ts_of(r, "timestamp", "first_timestamp").replace("Z", "+00:00")
        if not ts:
            continue
        if lo <= ts < hi:
            sel.append(r)
    if not sel:
        return None
    if axis_kind == "first_try_success":
        scored = [r for r in sel if r.get("error_count") is not None]
        if not scored:
            return None
        clean = sum(1 for r in scored if r.get("error_count") == 0)
        return clean / len(scored)
    return None


def check_direction(
    days: int = 60, *, window_days: int = DEFAULT_WINDOW_DAYS,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """apply イベント anchor の前後窓で軸が期待方向へ動く相関を判定する。

    現状の実装軸: first_try_success（apply 前後で十分な session 分母が取れる軸）。
    anchor ごとに [anchor-window, anchor) と [anchor, anchor+window) の軸値を比較し、
    期待方向（first_try は上昇）へ動いた anchor が過半なら pass。

    apply イベントが 1 つも無ければ判定不能（reason=no_apply_events → fail）。
    """
    base = _base(data_dir)
    anchors = _load_apply_anchors(base)
    if not anchors:
        return {"pass": False, "reason": "no_apply_events", "anchors": 0, "evidence": []}

    sessions = _sessions_in_window(days, base)
    # slug（optimize_history のキー）と sessions の PJ 識別子は別表記になりうるため、
    # session 側の PJ 値とゆるく突合する（slug が PJ 値に含まれる / 一致）。
    evidence: List[Dict[str, Any]] = []
    expected_dirs = 0
    compared = 0

    from datetime import datetime, timedelta

    for slug, ts_list in anchors.items():
        # session 側で slug にマッチする PJ 値を探す（完全一致 or slug を含む）。
        for anchor_ts in ts_list:
            try:
                a = datetime.fromisoformat(anchor_ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            lo = (a - timedelta(days=window_days)).isoformat()
            mid = a.isoformat()
            hi = (a + timedelta(days=window_days)).isoformat()

            # session 側の PJ キー候補を slug で解決する
            pj_key = _resolve_session_pj(sessions, slug)
            if pj_key is None:
                continue
            before = _axis_value_in_range(
                sessions, _SESSION_PJ_FIELDS, pj_key, lo, mid, "first_try_success"
            )
            after = _axis_value_in_range(
                sessions, _SESSION_PJ_FIELDS, pj_key, mid, hi, "first_try_success"
            )
            if before is None or after is None:
                continue
            compared += 1
            better_when_lower = _AXIS_BETTER_WHEN_LOWER["first_try_success"]
            improved = (after < before) if better_when_lower else (after > before)
            if improved:
                expected_dirs += 1
            evidence.append({
                "pj": pj_key,
                "axis": "first_try_success",
                "before": round(before, 4),
                "after": round(after, 4),
                "improved": improved,
            })

    if compared == 0:
        return {
            "pass": False, "reason": "no_paired_windows",
            "anchors": sum(len(v) for v in anchors.values()), "evidence": [],
        }
    passed = expected_dirs > compared / 2
    return {
        "pass": passed,
        "anchors": sum(len(v) for v in anchors.values()),
        "compared": compared,
        "expected_direction": expected_dirs,
        "window_days": window_days,
        "evidence": evidence,
    }


def _resolve_session_pj(sessions: List[Dict[str, Any]], slug: str) -> Optional[str]:
    """optimize_history の slug に対応する session 側 PJ 値を解決する。

    slug は worktree 安全 slug（PJ ディレクトリ名ベース）。session の project は
    フルパスのことがあるため、完全一致 → slug を末尾 basename に含む の順で突合する。
    """
    pj_values = {_pj_of(r, _SESSION_PJ_FIELDS) for r in sessions}
    pj_values.discard("")
    if slug in pj_values:
        return slug
    norm_slug = slug.replace("_", "-")
    for pj in sorted(pj_values):
        # PJ 値はフルパス（/p/a）/ basename / slug 表記が混在しうるため、
        # _ と / を - に正規化したうえで basename 一致 → 包含の順に突合する。
        norm_pj = pj.replace("_", "-").replace("/", "-")
        if Path(pj).name.replace("_", "-") == norm_slug:
            return pj
        if norm_slug and (norm_slug == norm_pj or norm_slug in norm_pj.split("-")):
            return pj
    return None


# ─── 統合 ─────────────────────────────────────────────────────────────────────


def compute_promotion_readiness(
    days: int = 30, *, window_days: int = DEFAULT_WINDOW_DAYS,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """ADR-046 の3条件を測定し「重み昇格を提案」可否を返す（読み取りのみ・書込なし）。

    Returns:
        {
          "promote": bool,                     # 3条件すべて pass か
          "variance": {...},                   # 条件1（代表軸 = correction_recurrence）
          "denominator": {...},                # 条件2（correction floor）
          "direction": {...},                  # 条件3
          "axes": {axis: {pj: {value, denominator}}},  # per-PJ 生データ（evidence）
          "window_days": int,
        }
    """
    cr = per_pj_correction_recurrence(days, data_dir=data_dir)
    fs = per_pj_first_try_success(days, data_dir=data_dir)
    rw = per_pj_rework(days, data_dir=data_dir)

    # 条件1（分散）: 代表軸として correction_recurrence の per-PJ 値を使う。
    # #563-2: 最小分母 floor 未満の PJ は値が統計的に無意味（distinct_types が小さいと
    # 1 type の再発有無で 0.0/1.0 に振れる）。floor 未満を variance 入力から除外しないと、
    # サブ floor の PJ が一斉に 1.0 へ張り付き「全 PJ 同値 = 測定バグ」を恒久 false positive
    # にする。outcome_metrics.correction_recurrence_rate は #529-2 で floor 済みだが、
    # readiness の per_pj_correction_recurrence は重複実装で floor が漏れていた（実 PJ E2E で
    # 発見・#563 が解消すると宣言した promotion_readiness 条件1 の FP の真因）。floor は
    # outcome_metrics と同一定数を使う（二重管理を避ける）。
    variance = check_variance({
        pj: v["value"]
        for pj, v in cr.items()
        if v.get("distinct_types", 0) >= _om.MIN_DISTINCT_TYPES_FLOOR
    })
    # 条件2（分母）: correction 件数の floor を満たす PJ が複数あるか。
    denominator = check_denominators(
        {pj: v["denominator"] for pj, v in cr.items()}, floor=CORRECTION_FLOOR
    )
    # 条件3（方向）: apply イベント前後の軸変化。
    direction = check_direction(
        days=max(days, window_days * 4), window_days=window_days, data_dir=data_dir
    )

    promote = bool(variance["pass"] and denominator["pass"] and direction["pass"])
    return {
        "promote": promote,
        "variance": variance,
        "denominator": denominator,
        "direction": direction,
        "axes": {
            "correction_recurrence": cr,
            "first_try_success": fs,
            "rework": rw,
        },
        "window_days": window_days,
    }
