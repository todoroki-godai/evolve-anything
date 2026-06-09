"""evolve_reconcile — evolve result の相互排他 reconcile / observability 昇格（#400）。

`evolve_introspect.reconcile_split_archive`（split↔archive, #301 #302）と対をなす
**skill_evolve↔archive** の reconcile（#400 バグ#2）と、remediation batch_skip を
observability に強制昇格する関数（#400 バグ#6）を持つ。evolve_introspect.py が
file-size budget（800行）に達したため分離した（archive 寄り候補の収集ヘルパー
`_collect_archive_skills` は evolve_introspect が SoT なのでそこから import する＝一方向依存）。

決定論・LLM 非依存。入力は evolve.run_evolve() の result dict のみ。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from evolve_introspect import _collect_archive_skills

# skill_evolve 提案対象とみなす suitability（high/medium のみ issue / emit 化される）。
_SKILL_EVOLVE_PROPOSED = ("high", "medium")
# remediation classified のうち skill_evolve issue が載りうるバケツ。
_REMEDIATION_PROPOSABLE_BUCKETS = (
    "proposable_custom",
    "proposable_custom_individual",
    "proposable_custom_batch_skip",
    "proposable",
)


def reconcile_skill_evolve_archive(result: Dict[str, Any]) -> Dict[str, Any]:
    """skill_evolve（自己進化提案）と archive（prune）の相互排他を解決する（#400 バグ#2）。

    同一スキルが「self-evolution 適性 high/medium = 自己進化を組み込め」と評価されつつ、
    同じ run の prune で archive 候補にも上がっている矛盾を解消する。**archive を優先**し、
    そのスキルを skill_evolve 提案から除外する:

      1. `skill_evolve.assessments` の該当 high/medium を `suppressed_by_archive` へ降格
         （emit_decisions が high/medium のみ拾うため、これで母集団からも外れる）
      2. `high_suitability` / `medium_suitability` カウントを再計算
      3. `remediation.classified` の `skill_evolve_candidate` issue を archive 対象スキル分だけ
         除外し、`remediation` の対応カウントを整合させる

    split↔archive の `evolve_introspect.reconcile_split_archive` と対をなす（#301 #302）。
    evolve.py が prune フェーズ直後・self-analysis の前に呼ぶ。除外は
    `skill_evolve.evolve_suppressed_by_archive` に記録し silent に消さない。

    Returns:
        {"suppressed": [skill, ...]}
    """
    phases = result.get("phases") if isinstance(result, dict) else None
    if not isinstance(phases, dict):
        return {"suppressed": []}
    se = phases.get("skill_evolve")
    if not isinstance(se, dict) or se.get("error"):
        return {"suppressed": []}
    assessments = se.get("assessments")
    if not isinstance(assessments, list) or not assessments:
        return {"suppressed": []}

    archive_skills = _collect_archive_skills(phases)
    if not archive_skills:
        return {"suppressed": []}

    suppressed: List[str] = []
    for a in assessments:
        if not isinstance(a, dict):
            continue
        if a.get("suitability") in _SKILL_EVOLVE_PROPOSED and a.get("skill_name") in archive_skills:
            suppressed.append(a["skill_name"])
            a["suitability"] = "suppressed_by_archive"

    if not suppressed:
        return {"suppressed": []}

    suppressed_sorted = sorted(set(suppressed))
    suppressed_set = set(suppressed_sorted)

    # カウント再計算（降格を反映）
    se["high_suitability"] = sum(1 for a in assessments if isinstance(a, dict) and a.get("suitability") == "high")
    se["medium_suitability"] = sum(1 for a in assessments if isinstance(a, dict) and a.get("suitability") == "medium")
    se["evolve_suppressed_by_archive"] = suppressed_sorted

    # remediation の skill_evolve issue も除外し count を整合させる。
    remediation = phases.get("remediation")
    if isinstance(remediation, dict):
        classified = remediation.get("classified")
        if isinstance(classified, dict):
            for bucket in _REMEDIATION_PROPOSABLE_BUCKETS:
                lst = classified.get(bucket)
                if not isinstance(lst, list):
                    continue
                kept = [
                    i for i in lst
                    if not (
                        isinstance(i, dict)
                        and i.get("type") == "skill_evolve_candidate"
                        and (i.get("detail") or {}).get("skill_name") in suppressed_set
                    )
                ]
                classified[bucket] = kept
                if bucket in remediation:
                    remediation[bucket] = len(kept)

    return {"suppressed": suppressed_sorted}


def build_remediation_batch_skip_observability(result: Dict[str, Any]) -> Optional[List[str]]:
    """remediation の proposable batch_skip 件数を必ず1行 surface する（#400 バグ#6）。

    低 confidence の proposable は `batch_skip`（まとめスキップ）に分かれ個別提示されないが、
    「何件・握り潰したか」が完全に不可視だと silence != evaluated 原則に反する。SKILL.md の
    surface MUST は守られないことがある（SKILL.md MUST != enforcement）ため、決定論コードで
    `result["observability"]` に1行を昇格させ、Step 3.8 が必ず出す構造化経路に乗せる。

    remediation phase が無い / error のときのみ None（非該当）。phase があれば 0 件でも
    「✓ batch_skip 0件」を返す（沈黙＝配線漏れ誤認を防ぐ）。

    Returns:
        surface する行リスト、または非該当時 None。
    """
    phases = result.get("phases") if isinstance(result, dict) else None
    if not isinstance(phases, dict):
        return None
    remediation = phases.get("remediation")
    if not isinstance(remediation, dict) or remediation.get("error"):
        return None
    n = remediation.get("proposable_custom_batch_skip", 0)
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return [
            f"⚠ remediation batch_skip: 低 confidence の proposable を {n} 件まとめスキップ"
            "（個別未提示・デフォルトはスキップ）。個別に見る場合は展開可"
        ]
    return ["✓ remediation batch_skip: 0 件（まとめスキップ対象なし）"]
