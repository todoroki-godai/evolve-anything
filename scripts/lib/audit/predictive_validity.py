"""予測妥当性（in/out-of-sample 順位相関）— 重み昇格レディネスの第4条件（#42, advisory）。

ADR-046 の重み昇格レディネス（``outcome_promotion_readiness``）は従来3条件
（分散十分 / データ件数下限 / 方向の妥当性）で判定していた。本モジュールはそこへ
**第4条件「予測妥当性（順位相関）」** を加える。

狙い: 集計平均ベースの skill 順位が分布外へ転移するか（汎化するか）を測る。
「in-sample（過去 = 古い半分のセッション）で良かった skill 順位が、out-of-sample
（新しい半分 = 未知/配備時）でも当たるか」を Spearman 順位相関 rho で評価する。
in-sample で高評価でも out-of-sample で順位が崩れる（rho が低い）なら、その順位は
過去への過学習であり重み昇格すると誤昇格になる — それを保守的にブロックする。

アルゴリズム:
  1. skill_activations.jsonl（DATA_DIR グローバル）を window 内で読む。
     各レコード: {"skill","session_id","project","ts","invocation_trigger","parent_skill"}。
  2. skill 名は namespace prefix（``rl-anything:docs-refresh`` 等）が混じるため bare 名に
     正規化してから集計（#577 pitfall_join_key_namespace_mismatch。
     ``rl_common.bare_skill_name`` の単一ソースに委譲 = 最後の ``:`` 以降・#145）。
  3. sessions は outcome_metrics.read_sessions(base) の union read で
     session_id -> error_count マップを作る（db + 未 ingest jsonl, dedup 済み）。
  4. ts の中央値で時系列分割: 古い半分 = in-sample / 新しい半分 = out-of-sample。
  5. 各半分で skill ごとに first_try_success = (発火 session のうち error_count==0 割合)。
     各半分で 1 skill あたり session 数 ≥ MIN_SESSIONS_PER_SKILL_PER_HALF を満たす skill のみ採用。
  6. 両半分に出現する skill 集合でランキングを作る。集合サイズ < MIN_RANKED_SKILLS なら
     insufficient_data（捏造しない）。
  7. 各半分の first_try_success で skill を順位付け（タイは平均順位）。両順位列の Spearman rho。
  8. rho >= PREDICTIVE_VALIDITY_RHO_FLOOR なら pass。

決定論・LLM 非依存。読み取りのみ（書込は一切しない）。scipy 等の外部依存は足さず
Spearman を純 Python で実装する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import outcome_metrics as _om

# テストは ``monkeypatch.setattr(predictive_validity, "DATA_DIR", tmp_path)`` で
# 直接この module 属性を差し替える（文字列ターゲット patch を避ける既知 pitfall 準拠）。
try:
    from rl_common import DATA_DIR, bare_skill_name
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    DATA_DIR = Path.home() / ".claude" / "evolve-anything"

    def bare_skill_name(name: Optional[str]) -> Optional[str]:  # noqa: D401
        if not name:
            return None
        if name.startswith("Agent:"):
            return None
        return name.rsplit(":", 1)[-1]

# 各半分（in/out-of-sample）で 1 skill を採用する最小 session 数。これ未満だと
# first_try_success が 1〜2 session の結果で 0.0/1.0 に振れ、順位が統計的に無意味になる。
MIN_SESSIONS_PER_SKILL_PER_HALF = 3

# 順位相関を語るのに必要な、両半分に共通出現する skill の最小数。これ未満は
# 相関が不安定（n=2,3 だと rho が ±1 に張り付く）なので insufficient_data とし
# 捏造した相関値を出さない。
MIN_RANKED_SKILLS = 5

# pass 判定の rho 下限。0.5 は「中程度以上の順位一致」の慣例的閾値。
# advisory ゆえ暫定。実コーパス dry-run で見直す（ADR-044 の方針 = 実 PJ データへの
# dry 適用で閾値を確定する。蓄積が薄い現時点では保守的な中央値を採る）。
PREDICTIVE_VALIDITY_RHO_FLOOR = 0.5


def _base(data_dir: Optional[Path]) -> Path:
    return data_dir if data_dir is not None else DATA_DIR


def _ranks(values: List[float]) -> List[float]:
    """値リストへ昇順の順位を割り当てる（タイは平均順位）。

    例: [10, 10, 30] → [1.5, 1.5, 3.0]。決定論。
    """
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    n = len(values)
    while i < n:
        j = i
        # 同値の連続区間を見つける
        while j + 1 < n and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        # [i, j] は同値タイ。順位（1始まり）の平均を割り当てる。
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg_rank
        i = j + 1
    return ranks


def _spearman(x: List[float], y: List[float]) -> float:
    """Spearman 順位相関 rho を純 Python で算出する（タイは平均順位）。

    rho = ランクの Pearson 相関。どちらかのランク列が分散ゼロ（全同値）なら相関は
    定義不能なので 0.0 を返す（捏造しない）。scipy 非依存。
    """
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    rx = _ranks(x)
    ry = _ranks(y)
    n = len(rx)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    cov = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    var_x = sum((v - mean_x) ** 2 for v in rx)
    var_y = sum((v - mean_y) ** 2 for v in ry)
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x ** 0.5 * var_y ** 0.5)


def _first_try_by_skill(
    activations: List[Dict[str, Any]],
    error_by_session: Dict[str, Optional[int]],
) -> Dict[str, float]:
    """1 半分の activation 群から skill ごとの first_try_success を算出する。

    first_try_success = (その skill が発火した session のうち error_count==0 の割合)。
    session_id で join。error_count 不明（None / session レコード無し）の session は
    分母から除外する。各 skill で session 数 ≥ MIN_SESSIONS_PER_SKILL_PER_HALF を
    満たすもののみ返す（floor 未満は統計的に無意味なので不採用）。
    """
    sessions_by_skill: Dict[str, set] = {}
    for rec in activations:
        skill = bare_skill_name(rec.get("skill"))
        sid = rec.get("session_id")
        if not skill or not sid:
            continue
        sessions_by_skill.setdefault(skill, set()).add(sid)

    out: Dict[str, float] = {}
    for skill, sids in sessions_by_skill.items():
        scored = [s for s in sids if error_by_session.get(s) is not None]
        if len(scored) < MIN_SESSIONS_PER_SKILL_PER_HALF:
            continue
        clean = sum(1 for s in scored if error_by_session[s] == 0)
        out[skill] = clean / len(scored)
    return out


def check_predictive_validity(
    days: int = 30, *, data_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """予測妥当性（in/out-of-sample 順位相関）を判定する（読み取りのみ・書込なし）。

    Returns:
        {
          "pass": bool,
          "reason": str | None,            # "insufficient_data" 等。pass や低rho では None
          "rho": float | None,             # Spearman 順位相関（insufficient_data では None）
          "n_skills": int,                 # 両半分に共通出現しランクされた skill 数
          "in_sample_n": int,              # in-sample（古い半分）の activation 件数
          "out_sample_n": int,             # out-of-sample（新しい半分）の activation 件数
          "evidence": [{"skill","rank_in","rank_out","fts_in","fts_out"} ...上位3],
        }
    """
    base = _base(data_dir)

    # 1. window 内の skill_activations を読む。ts でフィルタ。
    since = _om._iso_days_ago(days)
    activations = [
        r for r in _om._read_jsonl(base / "skill_activations.jsonl")
        if _om._in_window(_om._ts_of(r, "ts", "timestamp"), since)
    ]

    if len(activations) < 2:
        return {
            "pass": False, "reason": "insufficient_data", "rho": None,
            "n_skills": 0, "in_sample_n": len(activations), "out_sample_n": 0,
            "evidence": [],
        }

    # 3. session_id -> error_count マップ（union read）。
    # #144: read_sessions は 1 セッションにつき session_summary 型（error_count あり）と
    # instructions_loaded 型（error_count なし）の複数行を返す。手書きの単純代入は
    # 最後に読んだ行が先行行を無条件に上書きするため、error_count 欠損行が後発 timestamp だと
    # 実測値を None に潰し scored（分母）から丸ごと除外してしまう（#138 と同型 seam）。
    # fold_session_error_counts（error_count を持つ行の max・欠損行は既存値を壊さない）で畳む。
    error_by_session = _om.fold_session_error_counts(list(_om.read_sessions(base)))

    # 4. ts の中央値で時系列分割（古い半分 = in-sample / 新しい半分 = out-of-sample）。
    def _ts(r: Dict[str, Any]) -> str:
        return _om._ts_of(r, "ts", "timestamp").replace("Z", "+00:00")

    ordered = sorted(activations, key=_ts)
    mid = len(ordered) // 2
    in_sample = ordered[:mid]
    out_sample = ordered[mid:]

    # 5. 各半分で skill ごとの first_try_success（floor 付き）。
    fts_in = _first_try_by_skill(in_sample, error_by_session)
    fts_out = _first_try_by_skill(out_sample, error_by_session)

    # 6. 両半分に出現する skill 集合。
    common = sorted(set(fts_in) & set(fts_out))
    base_result = {
        "in_sample_n": len(in_sample),
        "out_sample_n": len(out_sample),
    }
    if len(common) < MIN_RANKED_SKILLS:
        return {
            "pass": False, "reason": "insufficient_data", "rho": None,
            "n_skills": len(common), **base_result, "evidence": [],
        }

    # 7. 各半分の first_try_success で順位付け（タイは平均順位）→ Spearman rho。
    vals_in = [fts_in[s] for s in common]
    vals_out = [fts_out[s] for s in common]
    rho = _spearman(vals_in, vals_out)

    ranks_in = _ranks(vals_in)
    ranks_out = _ranks(vals_out)
    # evidence は in-sample 順位（昇順 = 良いほど高 fts ≒ 高ランク）の上位3 skill を出す。
    evidence_all = [
        {
            "skill": common[i],
            "rank_in": round(ranks_in[i], 1),
            "rank_out": round(ranks_out[i], 1),
            "fts_in": round(vals_in[i], 4),
            "fts_out": round(vals_out[i], 4),
        }
        for i in range(len(common))
    ]
    # 「良かった skill = first_try_success が高い」を上位とみなして上位3を載せる。
    evidence = sorted(evidence_all, key=lambda e: e["fts_in"], reverse=True)[:3]

    return {
        "pass": bool(rho >= PREDICTIVE_VALIDITY_RHO_FLOOR),
        "reason": None,
        "rho": round(rho, 4),
        "n_skills": len(common),
        **base_result,
        "evidence": evidence,
    }
