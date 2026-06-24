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
from .outcome_promotion_readiness import check_variance

# triage の中で順位付けの対象になる action（OK は提案でないので除外）。
_RANKED_ACTIONS = ("CREATE", "UPDATE", "SPLIT", "MERGE")

# rework 判定の連続編集しきい値（outcome_metrics.rework_rate のデフォルトと一致）。
_REWORK_MIN_CONSECUTIVE = 3


def _skill_of(rec: Dict[str, Any]) -> str:
    """usage レコードからスキル名を取り出す（skill_name 優先、skill フォールバック）。"""
    return rec.get("skill_name") or rec.get("skill") or ""


def _skill_of_correction(rec: Dict[str, Any]) -> str:
    """correction レコードからスキル名を取り出す（correction_detect が書く last_skill）。"""
    return rec.get("last_skill") or rec.get("skill_name") or rec.get("skill") or ""


def attribute_outcomes(
    *,
    usage: List[Dict[str, Any]],
    sessions: List[Dict[str, Any]],
    corrections: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """スキル単位に outcome 3軸 + reward 分散を分解する。

    Args:
        usage: usage.jsonl 相当のレコード列（skill/skill_name → session_id）。
        sessions: sessions 相当のレコード列（session_id → error_count / tool_sequence）。
        corrections: corrections.jsonl 相当のレコード列（last_skill / correction_type /
            session_id）。#10 の correction 再発率軸の素。**現状ほぼ空**なので省略可。
            空・None のときは correction_recurrence=None（graceful、既存2軸を壊さない）。

    Returns:
        {skill: {
            "first_try_success": float|None,    # error_count==0 セッション割合（高いほど良い）
            "rework": float|None,               # 編集ありセッション中の編集バースト割合（高いほど悪い）
            "correction_recurrence": float|None,# 同型 correction の再発率（高いほど悪い, #10）
            "n_sessions": int,                  # そのスキルに紐づく distinct session 数
            "degraded": bool,                   # 3軸とも算出不能か
            "reward_variance": dict,            # #28 RODS: reward 分散判定（check_variance）
        }}

    usage に現れたスキルだけを返す。session が 1 つも引けないスキルは degraded=True。
    correction_recurrence / reward_variance は session 由来でないので degraded 判定には
    含めない（既存2軸の degraded 契約を変えない — multiview_eval 互換）。
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

    # スキル → そのスキルに帰属する correction レコード列（#10）。
    corrections_by_skill: Dict[str, List[Dict[str, Any]]] = {}
    for rec in corrections or []:
        skill = _skill_of_correction(rec)
        if skill:
            corrections_by_skill.setdefault(skill, []).append(rec)

    out: Dict[str, Dict[str, Any]] = {}
    for skill, sids in sessions_by_skill.items():
        recs = [session_by_id[sid] for sid in sids if sid in session_by_id]
        corr_recurrence = _correction_recurrence(corrections_by_skill.get(skill, []))
        if not recs:
            # usage はあるが session が 1 つも引けない → session 系2軸は degraded。
            # correction 軸は session 非依存なので算出できれば残す（graceful）。
            out[skill] = {
                "first_try_success": None,
                "rework": None,
                "correction_recurrence": corr_recurrence,
                "n_sessions": 0,
                "degraded": True,
                "reward_variance": check_variance({}),
            }
            continue
        first_try = _first_try_success(recs)
        rework = _rework(recs)
        out[skill] = {
            "first_try_success": first_try,
            "rework": rework,
            "correction_recurrence": corr_recurrence,
            "n_sessions": len(recs),
            # session 系2軸とも算出不能なら degraded（片方でも出れば順位に使える）。
            # correction/variance は session 非依存なので degraded 契約には含めない。
            "degraded": first_try is None and rework is None,
            "reward_variance": _reward_variance(recs),
        }
    return out


def _correction_recurrence(recs: List[Dict[str, Any]]) -> Optional[float]:
    """スキルに帰属する correction の再発率（同型が複数 distinct session に跨る割合）。

    定義は outcome_metrics.correction_recurrence_rate と同型（distinct correction_type を
    分母、2 つ以上の distinct session に出た type を分子）。ただし表示側 floor
    （MIN_DISTINCT_TYPES_FLOOR）は per-skill では分母がさらに小さくなるため課さず、
    値が出なければ None を返す graceful 設計にする（#10: corrections.jsonl は現状ほぼ空）。
    """
    sessions_by_type: Dict[str, set] = {}
    for r in recs:
        ctype = r.get("correction_type")
        sid = r.get("session_id") or ""
        if not ctype:
            continue
        sessions_by_type.setdefault(ctype, set()).add(sid)
    distinct_types = len(sessions_by_type)
    if distinct_types == 0:
        return None
    recurring = sum(1 for s in sessions_by_type.values() if len(s) >= 2)
    return round(recurring / distinct_types, 4)


def _reward_variance(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """#28 RODS: スキルの session 別 reward 分散を check_variance で判定する。

    reward proxy = session 別 一発成功（error_count==0 → 1.0 / それ以外 → 0.0）。
    分散が大きい（成功と失敗が拮抗）= 能力境界付近 = 学習余地が最も大きい、という
    RODS の targeting 発想を流用。check_variance（per-PJ 測定バグ検出器）を per-skill に
    そのまま転用する（新方式は発明しない）。error_count 欠損 session は除外。

    返り値は check_variance の生 dict（pass=True で「分散十分 = 学習余地大」）。
    """
    reward_by_session: Dict[str, float] = {}
    for r in recs:
        ec = r.get("error_count")
        if ec is None:
            continue
        sid = r.get("session_id") or ""
        if not sid:
            continue
        reward_by_session[sid] = 1.0 if ec == 0 else 0.0
    return check_variance(reward_by_session)


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
      - 一発成功率が低い      → (1 - first_try_success) が大
      - rework 率が高い       → rework が大
      - correction 再発率が高い → correction_recurrence が大（#10 3軸目）
    揃った軸の平均、片方しか無ければそれだけ、全軸欠損(degraded)は neutral=0.0。
    correction_recurrence は corrections.jsonl が現状ほぼ空＝大半 None なので、
    None のときは平均の分子・分母から外す（既存2軸の挙動を変えない graceful）。

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
    cr = attr.get("correction_recurrence")
    if cr is not None:
        components.append(float(cr))
    if not components:
        return 0.0
    return round(sum(components) / len(components), 4)


def _ema_stability_label(rec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """reward_ema.ema_stability_label の遅延ラッパ（#64・循環 import 回避）。

    reward_ema は本モジュール（attribute_outcomes）を import するため、module-top で
    逆方向 import すると循環する。呼び出し時に遅延 import する。
    """
    from .reward_ema import ema_stability_label
    return ema_stability_label(rec)


def _negative_transfer_skills(
    negative_transfer: Optional[List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """negative_transfer 検出結果から「フラグ立ち」スキルのみ抽出する（#10 安全弁）。

    usage.compute_negative_transfer の返り値（[{skill_name, negative_transfer(bool),
    delta_score, ...}]）のうち negative_transfer=True のものだけを skill→record で返す。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for rec in negative_transfer or []:
        if rec.get("negative_transfer") is True:
            name = rec.get("skill_name") or rec.get("skill") or ""
            if name:
                out[name] = rec
    return out


def apply_outcome_ranking(
    triage_result: Dict[str, Any],
    *,
    usage: List[Dict[str, Any]],
    sessions: List[Dict[str, Any]],
    corrections: Optional[List[Dict[str, Any]]] = None,
    negative_transfer: Optional[List[Dict[str, Any]]] = None,
    reward_ema: Optional[Dict[str, dict]] = None,
) -> Dict[str, Any]:
    """triage 候補の各 action リストを outcome priority 降順で再配置する（純粋関数）。

    各候補に ``outcome`` evidence を付与し、(suppressed asc, outcome_priority desc,
    confidence desc) で安定ソートする。元の入力 triage_result / 候補 dict は変更しない
    （shallow copy + 候補も copy）。

    安全弁（#10）: ``negative_transfer`` でフラグの立ったスキル（追加が他スキルの成功率を
    下げた）は ``outcome.suppressed=True`` を立て、ランキングの末尾へ落とす（rollback 候補
    として理由を evidence に残す）。outcome が悪い＝本来トップでも、退行を生んだスキルを
    優先進化対象にしないための gate。

    #28 RODS: 各候補に reward 分散判定（``outcome.reward_variance``）を添える。高分散 =
    能力境界 = 学習余地大。advisory 列のみで自動昇格はしない（順位は従来 priority 主導）。

    #64 MAA: ``reward_ema`` を渡すと各候補に ``outcome.reward_ema``（バッチ跨ぎ符号付き
    EMA レコード or None）を添え、``outcome_ranking[action]["reward_ema"]`` に skill 別
    の通時安定ラベルを記録する。この関数は in-memory 純粋契約を保つため DATA_DIR を読まず、
    prior EMA は呼び出し側が読んで渡す（read のみ＝dry-run 安全）。**順位は変えない**
    （advisory のみ）。``reward_ema=None`` のときは完全に従来挙動。

    dry-run observability（#393-#396 準拠: 数字に意味を添える）のため、action ごとの
    before/after スキル順と changed フラグ、suppress されたスキルを ``outcome_ranking`` に
    記録する。

    triage が skipped のときはそのまま返す（順位付けの対象なし）。
    """
    if triage_result.get("skipped"):
        return triage_result

    attribution = attribute_outcomes(
        usage=usage, sessions=sessions, corrections=corrections
    )
    suppressed = _negative_transfer_skills(negative_transfer)

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
                outcome = {
                    "priority": priority,
                    "first_try_success": attr.get("first_try_success"),
                    "rework": attr.get("rework"),
                    "correction_recurrence": attr.get("correction_recurrence"),
                    "n_sessions": attr.get("n_sessions", 0),
                    "degraded": attr.get("degraded", False),
                    "reward_variance": attr.get("reward_variance") or check_variance({}),
                }
            else:
                # usage に現れない（テレメトリ皆無の）スキル → neutral, degraded 明示。
                outcome = {
                    "priority": 0.0,
                    "first_try_success": None,
                    "rework": None,
                    "correction_recurrence": None,
                    "n_sessions": 0,
                    "degraded": True,
                    "reward_variance": check_variance({}),
                }
            # 安全弁: negative_transfer 検出スキルは昇格を抑制し rollback 候補にする。
            nt = suppressed.get(skill)
            if nt is not None:
                outcome["suppressed"] = True
                outcome["suppress_reason"] = (
                    f"negative_transfer (delta={nt.get('delta_score')})"
                )
            else:
                outcome["suppressed"] = False
            # #64 MAA: バッチ跨ぎ符号付き EMA レコード（advisory 列・順位非影響）。
            # 渡されなければ None（従来挙動を壊さない）。
            outcome["reward_ema"] = (reward_ema or {}).get(skill)
            new_cand["outcome"] = outcome
            enriched.append(new_cand)

        # 安定ソート: suppressed を末尾（False=0 を先、True=1 を後）→ priority 降順 →
        # confidence 降順。Python の sort は stable なので同点は入力順を保つ。
        ordered = sorted(
            enriched,
            key=lambda c: (
                0 if c["outcome"].get("suppressed") else 1,  # 抑制を末尾へ
                c["outcome"]["priority"],
                float(c.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        result[action] = ordered

        after = [c.get("skill", "") for c in ordered]
        suppressed_here = [
            c.get("skill", "") for c in ordered if c["outcome"].get("suppressed")
        ]
        ranking_evidence[action] = {
            "before": before,
            "after": after,
            "changed": before != after,
            # 順位を実際に動かした上位候補の根拠（意味を添える）。
            "scores": {
                c.get("skill", ""): c["outcome"]["priority"] for c in ordered
            },
            # #10 安全弁: rollback 候補として抑制されたスキル。
            "suppressed": suppressed_here,
            # #28 RODS: 高分散（学習余地大）のスキル。
            "high_reward_variance": [
                c.get("skill", "")
                for c in ordered
                if (c["outcome"].get("reward_variance") or {}).get("pass")
            ],
            # #64 MAA: skill 別の通時安定ラベル（バッチ跨ぎ EMA・advisory）。
            "reward_ema": {
                c.get("skill", ""): _ema_stability_label((reward_ema or {}).get(c.get("skill", "")))
                for c in ordered
            },
        }

    if ranking_evidence:
        result["outcome_ranking"] = ranking_evidence
    return result
