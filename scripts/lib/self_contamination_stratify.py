"""自己汚染指紋の曝露母数つき層別発生率（stratified exposure rate）計算（#275）。

件数の内訳だけでは相関を評価できないため、model × thinking_state ごとに「曝露（text ブロックを
持つ assistant record 数＝分母）」と「影響を受けた record 数（分子）」を必ず併記する。検出コア
本体の詳細な設計意図は ``self_contamination_scan`` の module docstring を参照。

``self_contamination_scan.py`` の 800 行ハードバジェット超過（file-size-budget.md）を避けるため
この module に分離した。公開 API は ``self_contamination_scan`` から re-export され、呼び出し側
（テスト・observability セクション）は分割を意識せずそのまま使える。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Iterable, List, Optional, Tuple

if TYPE_CHECKING:  # 循環 import 回避（self_contamination_scan がこの module を re-export する）
    from self_contamination_scan import Hit, ScanReport


def classify_thinking_state(record: dict) -> str:
    """record の thinking ブロック在否を3値（present/absent/unknown）で分類する（#275）。

    transcript に thinking ブロックが「在る」ことは「設定上 thinking が有効だった」を意味しない
    ため、フィールド名・値は thinking_enabled でなく thinking_state とする（呼び出し側の命名も
    これに合わせる）。判定順（優先度順）:

    1. content 中に ``type == "thinking"`` ブロックが1つでもあれば ``"present"``
    2. （1 に該当せず）``type == "redacted_thinking"`` を含むなら ``"unknown"``
       （absence 側を汚染しない。redacted は「thinking が起きたが中身が伏せられた」であり
       absent と同一視できない）
    3. content が list でも str でもない（不正な形）なら ``"unknown"``
    4. 上記いずれでもなく thinking ブロックが無ければ ``"absent"``
    """
    msg = record.get("message") if isinstance(record, dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return "absent"  # 旧形式の単純文字列 content。thinking ブロックを持ちえない。
    if not isinstance(content, list):
        return "unknown"
    has_thinking = False
    has_redacted = False
    for b in content:
        if not isinstance(b, dict):
            continue
        bt = b.get("type")
        if bt == "thinking":
            has_thinking = True
        elif bt == "redacted_thinking":
            has_redacted = True
    if has_thinking:
        return "present"
    if has_redacted:
        return "unknown"
    return "absent"


def classify_session_thinking_state(records: "Iterable[dict]") -> str:
    """**セッション単位**の thinking 在否を3値で分類する（#275・層別の正準粒度）。

    record 単位（``classify_thinking_state``）では判定が退化する: CC の transcript は
    thinking ブロックと text ブロックを**別 record に分けて**保存することが多く、text を持つ
    record（＝曝露母数の対象）にはほぼ thinking ブロックが同居しない。実機で確認すると
    thinking 常時 on の fable でも text 保持 record の 356/357 が ``absent`` に落ち、交差表が
    「全セル absent」に潰れて説明変数として機能しなかった。

    thinking の有効/無効は本来セッション（設定）単位の性質なので、同一 transcript 内に
    thinking ブロックを持つ assistant record が 1 つでもあれば、そのセッションの全 record を
    ``"present"`` とする。判定順は ``classify_thinking_state`` と同じ（present > unknown >
    absent）で、``redacted_thinking`` のみのセッションは absence 側を汚染しないよう
    ``"unknown"``。あくまで **transcript 上の観測値であり設定値ではない**（保存されなかった・
    短い応答で出なかった等でも absent になりうる）。
    """
    saw_unknown = False
    for record in records:
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        state = classify_thinking_state(record)
        if state == "present":
            return "present"
        if state == "unknown":
            saw_unknown = True
    return "unknown" if saw_unknown else "absent"


def _extract_model(record: dict) -> str:
    """record の ``message.model`` 原値を返す（正規化・グルーピングしない・#275）。

    取得できなければ ``"unknown"``。effort は transcript から取得できないため触れない。
    """
    msg = record.get("message") if isinstance(record, dict) else None
    if not isinstance(msg, dict):
        return "unknown"
    model = msg.get("model")
    if isinstance(model, str) and model:
        return model
    return "unknown"


def _is_synthetic_model(model: str) -> bool:
    """``<synthetic>`` 等の非実 model 値か（先頭が ``<``・#275）。交差表対象外にする判定。"""
    return model.startswith("<")


@dataclass
class StratumRow:
    """model × thinking_state 1 セル分の層別発生率（#275）。

    ``rate`` は ``affected_records / exposed``。``exposed`` が閾値未満なら ``None``
    （低母数のため率は非表示・呼び出し側が「低母数」と明記する）。
    """

    model: str
    thinking_state: str
    exposed: int
    affected_records: int
    affected_sessions: int
    hits: int
    rate: Optional[float]


_MIN_STRATUM_DENOM = 20


def build_stratum_rows(
    report: "ScanReport",
    *,
    family: str = "A",
    block: str = "text",
    min_denom: int = _MIN_STRATUM_DENOM,
) -> List[StratumRow]:
    """model × thinking_state の層別発生率テーブルを組む（#275）。

    分子＝affected record 数（distinct ``(session_id, line)``）、分母＝``report.exposure``
    （text 保持 assistant record 数）。**分母は ``exposure`` のみを唯一のソースとする**
    （hit だけあって exposure に無いキーは行を作らない）。これにより、scan_records が生成しない
    人工 fixture（例: exposure 未記録の Hit を直接組んだテスト）で分母ゼロの幽霊セルが混入する
    ことを防ぐ。分母が ``min_denom`` 未満のセルは ``rate=None``。``<synthetic>`` 等の非実 model
    は ``report.exposure`` 側で既に除外済みのため、対応する hit 側もここで除外し交差表から外す。
    """
    lane = {"A": report.family_a, "B": report.family_b, "C": report.family_c}[family]
    by_stratum: "Dict[Tuple[str, str], List[Hit]]" = {}
    for h in lane:
        if h.block != block or _is_synthetic_model(h.model):
            continue
        by_stratum.setdefault((h.model, h.thinking_state), []).append(h)

    rows: List[StratumRow] = []
    for key, exposed in report.exposure.items():
        model, thinking_state = key
        group = by_stratum.get(key, [])
        affected_keys = {(h.session_id, h.line) for h in group}
        affected_records = len(affected_keys)
        affected_sessions = len({sid for sid, _ln in affected_keys})
        rate = (affected_records / exposed) if exposed >= min_denom else None
        rows.append(
            StratumRow(
                model=model,
                thinking_state=thinking_state,
                exposed=exposed,
                affected_records=affected_records,
                affected_sessions=affected_sessions,
                hits=len(group),
                rate=rate,
            )
        )
    rows.sort(key=lambda r: (-r.exposed, r.model, r.thinking_state))
    return rows
