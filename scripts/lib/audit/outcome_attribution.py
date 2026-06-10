"""per-skill outcome 帰属 + evolve ターゲットランキング配線（#433 先行スコープ）。

ADR-046 / #423 の outcome_metrics は環境**全体**の行動アウトカム3軸を advisory 表示する
だけで、測定値が evolve のターゲット選定に流れていなかった（読者は audit 内部のみ＝画面表示
で終端）。本モジュールは corrections 非依存の2軸（一発成功率 / rework 率）だけを **スキル単位**
に分解し、skill_triage 候補の順位に自動入力する（閉ループの先行配線）。

スコープ外（#431/#432 で信号が溜まってから配線）:
  - correction 再発率軸（corrections.jsonl が現状ほぼ空、#421 で実測）
  - fitness 重みの変更（ADR-046 の 2-4 週後判断レール）
  - 自動適用（適用判断は従来通り人間）

データ契約（capture_rate.py の join パターンに準拠、実測で確認済み）:
  - usage レコード: ``skill`` または ``skill_name`` → ``session_id``（1 skill 呼び出し = 1 行）
  - sessions レコード: ``session_id`` → ``error_count`` / ``tool_sequence``

帰属は **in-memory のリストのみ** を入力にする（DATA_DIR を再読込しない = dry-run 安全。
evolve は既に query_usage / query_sessions で両者を取得済みなので、それをそのまま渡す）。

degraded 挙動: telemetry が無いスキル・session 欠損・error_count 欠損のときは value=None +
``degraded=True`` を返し、neutral priority(0.0) で順位を動かさない（沈黙でなくデータ不足を明示、
#393-#396 準拠）。None ソート落ち（dict.get None pitfall）を避けるため priority は必ず float。

rework / 編集バーストの定義は outcome_metrics と同一ソースを再利用する（軸の定義が割れない）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .outcome_metrics import _EDIT_TOOLS, _has_edit_burst

# triage の中で順位付けの対象になる action（OK は提案でないので除外）。
_RANKED_ACTIONS = ("CREATE", "UPDATE", "SPLIT", "MERGE")

# rework 判定の連続編集しきい値（outcome_metrics.rework_rate のデフォルトと一致）。
_REWORK_MIN_CONSECUTIVE = 3


def _skill_of(rec: Dict[str, Any]) -> str:
    """usage レコードからスキル名を取り出す（skill_name 優先、skill フォールバック）。"""
    return rec.get("skill_name") or rec.get("skill") or ""


def attribute_outcomes(
    *,
    usage: List[Dict[str, Any]],
    sessions: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """スキル単位に一発成功率 / rework 率を分解する。

    Args:
        usage: usage.jsonl 相当のレコード列（skill/skill_name → session_id）。
        sessions: sessions 相当のレコード列（session_id → error_count / tool_sequence）。

    Returns:
        {skill: {
            "first_try_success": float|None,  # error_count==0 セッション割合（高いほど良い）
            "rework": float|None,             # 編集ありセッション中の編集バースト割合（高いほど悪い）
            "n_sessions": int,                # そのスキルに紐づく distinct session 数
            "degraded": bool,                 # telemetry 不足で軸が算出不能か
        }}

    usage に現れたスキルだけを返す。session が 1 つも引けないスキルは degraded=True。
    """
    # スキル → そのスキルが呼ばれた distinct session_id 集合。
    sessions_by_skill: Dict[str, set] = {}
    for rec in usage:
        skill = _skill_of(rec)
        sid = rec.get("session_id") or ""
        if not skill or not sid:
            continue
        sessions_by_skill.setdefault(skill, set()).add(sid)

    # session_id → session レコード（後勝ちで dedup）。
    session_by_id: Dict[str, Dict[str, Any]] = {}
    for s in sessions:
        sid = s.get("session_id") or ""
        if sid:
            session_by_id[sid] = s

    out: Dict[str, Dict[str, Any]] = {}
    for skill, sids in sessions_by_skill.items():
        recs = [session_by_id[sid] for sid in sids if sid in session_by_id]
        if not recs:
            # usage はあるが session が 1 つも引けない → degraded。
            out[skill] = {
                "first_try_success": None,
                "rework": None,
                "n_sessions": 0,
                "degraded": True,
            }
            continue
        first_try = _first_try_success(recs)
        rework = _rework(recs)
        out[skill] = {
            "first_try_success": first_try,
            "rework": rework,
            "n_sessions": len(recs),
            # 両軸とも算出不能なら degraded（片方でも出れば順位に使える）。
            "degraded": first_try is None and rework is None,
        }
    return out


def _first_try_success(recs: List[Dict[str, Any]]) -> Optional[float]:
    """error_count==0 のセッション割合。error_count 欠損は分母から除外（None pitfall 回避）。"""
    valid = [r for r in recs if r.get("error_count") is not None]
    if not valid:
        return None
    clean = sum(1 for r in valid if r.get("error_count") == 0)
    return round(clean / len(valid), 4)


def _rework(recs: List[Dict[str, Any]]) -> Optional[float]:
    """編集ありセッション中、検証なし連続編集バーストを含むセッション割合。

    定義は outcome_metrics.rework_rate と同一（_has_edit_burst / _EDIT_TOOLS を共有）。
    """
    edit_sessions = 0
    rework_sessions = 0
    for r in recs:
        seq = r.get("tool_sequence")
        if not isinstance(seq, list) or not seq:
            continue
        if not any(t in _EDIT_TOOLS for t in seq):
            continue
        edit_sessions += 1
        if _has_edit_burst(seq, _REWORK_MIN_CONSECUTIVE):
            rework_sessions += 1
    if edit_sessions == 0:
        return None
    return round(rework_sessions / edit_sessions, 4)


def outcome_priority(attr: Dict[str, Any]) -> float:
    """outcome 帰属を「進化対象としての優先度」スコア（0.0-1.0）に変換する。

    高いほど **アウトカムが悪い** = 進化対象として上位に押し上げる:
      - 一発成功率が低い  → (1 - first_try_success) が大
      - rework 率が高い   → rework が大
    両軸が揃えば平均、片方しか無ければ片方のみ、両方欠損(degraded)は neutral=0.0。

    必ず float を返す（None を返さない）ことで後続のソートで None 比較落ちを起こさない。
    """
    if attr.get("degraded"):
        return 0.0
    components: List[float] = []
    fts = attr.get("first_try_success")
    if fts is not None:
        components.append(1.0 - float(fts))
    rw = attr.get("rework")
    if rw is not None:
        components.append(float(rw))
    if not components:
        return 0.0
    return round(sum(components) / len(components), 4)


def apply_outcome_ranking(
    triage_result: Dict[str, Any],
    *,
    usage: List[Dict[str, Any]],
    sessions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """triage 候補の各 action リストを outcome priority 降順で再配置する（純粋関数）。

    各候補に ``outcome`` evidence を付与し、(outcome_priority desc, confidence desc) で安定
    ソートする。元の入力 triage_result / 候補 dict は変更しない（shallow copy + 候補も copy）。

    dry-run observability（#393-#396 準拠: 数字に意味を添える）のため、action ごとの
    before/after スキル順と changed フラグを ``outcome_ranking`` に記録する。

    triage が skipped のときはそのまま返す（順位付けの対象なし）。
    """
    if triage_result.get("skipped"):
        return triage_result

    attribution = attribute_outcomes(usage=usage, sessions=sessions)

    result = dict(triage_result)
    ranking_evidence: Dict[str, Any] = {}

    for action in _RANKED_ACTIONS:
        candidates = triage_result.get(action) or []
        if not candidates:
            continue

        before = [c.get("skill", "") for c in candidates]

        enriched: List[Dict[str, Any]] = []
        for cand in candidates:
            skill = cand.get("skill", "")
            attr = attribution.get(skill)
            new_cand = dict(cand)
            if attr is not None:
                priority = outcome_priority(attr)
                new_cand["outcome"] = {
                    "priority": priority,
                    "first_try_success": attr.get("first_try_success"),
                    "rework": attr.get("rework"),
                    "n_sessions": attr.get("n_sessions", 0),
                    "degraded": attr.get("degraded", False),
                }
            else:
                # usage に現れない（テレメトリ皆無の）スキル → neutral, degraded 明示。
                new_cand["outcome"] = {
                    "priority": 0.0,
                    "first_try_success": None,
                    "rework": None,
                    "n_sessions": 0,
                    "degraded": True,
                }
            enriched.append(new_cand)

        # 安定ソート: priority 降順 → confidence 降順。Python の sort は stable なので
        # 同点は元の順序（confidence 同点なら入力順）を保つ。
        ordered = sorted(
            enriched,
            key=lambda c: (
                c["outcome"]["priority"],
                float(c.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        result[action] = ordered

        after = [c.get("skill", "") for c in ordered]
        ranking_evidence[action] = {
            "before": before,
            "after": after,
            "changed": before != after,
            # 順位を実際に動かした上位候補の根拠（意味を添える）。
            "scores": {
                c.get("skill", ""): c["outcome"]["priority"] for c in ordered
            },
        }

    if ranking_evidence:
        result["outcome_ranking"] = ranking_evidence
    return result
