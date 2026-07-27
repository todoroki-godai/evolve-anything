"""evolve decision の identity 関数群（`evolve_decisions` から分離・#287）。

**提案の identity**（「同じ提案か」）と**判断イベントの identity**（「同じ判断か」）を
別関数として並べて置くための module。この2つを1つの ID に兼ねさせたことが
#279 → #286 → #290 で3回同じ場所を踏んだ根因なので、定義を隣り合わせにして
「どちらの identity の話をしているか」を読み違えにくくする。

module 定数を持たない純関数だけを置く（`evolve_decisions` 側の `QUEUE_ROOT` /
`MARKER_ROOT` を monkeypatch するテスト経路に影響しない）。
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Dict, List, Optional


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_run_id() -> str:
    return "evrun_" + uuid.uuid4().hex


def _legacy_run_id(pending: List[Dict[str, Any]]) -> str:
    """旧 marker を安定した synthetic run として扱う。"""
    identity = "\n".join(sorted(str(entry.get("id", "")) for entry in pending))
    return "legacy_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]


def _proposal_id(skill_path: str, before_sha: str) -> str:
    """**提案**の content identity = (対象パス, 適用前の内容)。

    「同じ提案か」だけを表す。「同じ判断イベントか」は別キー（``_decision_event_id``）で
    表す — 1つの ID に両方を兼ねさせると必ずどちらかが壊れる（#279→#286→#290 で
    3回踏んだ）:

    - **run_id を混ぜてはいけない**（#279）: ID が run ごとに変わると判断イベントも
      run 跨ぎで別物になり、1回の apply が optimize_history に N 重記録される。
    - **パス単独にしてもいけない**（#286）: 判断イベントキーが恒久キーになり、同じ
      スキルの2回目以降の accept が冪等 dedup で捨てられる（生涯1件しか母集団に入らない）。
    - **before_sha を混ぜても、これ単独では足りない**（#290）: 対象の内容が過去の状態へ
      循環すると過去の ID が再利用されるため、判断イベントキーが再び衝突する。
    """
    return "evdiff_" + hashlib.sha1(
        f"{skill_path}\n{before_sha}".encode("utf-8")
    ).hexdigest()[:12]


def _decision_event_id(proposal_id: str, kind: str, after_content: str) -> str:
    """**判断イベント**の identity = (提案, 判断種別, 判断時点の内容)（#290）。

    ``record_evolve_diff_decision`` の冪等 dedup キー。提案 ID と分離することで、

    - 同じ apply を二重 drain しても after が同じ＝同キー（冪等は保つ）
    - 内容が循環して提案 ID が再利用されても after が違う＝別キー（欠落しない）

    の両方が成り立つ。提案 ID 側の identity 設計を変えても、この分離がある限り
    判断イベントの冪等性は巻き添えにならない。
    """
    return f"{proposal_id}_{kind}_{_sha256(after_content)[:12]}"


def _tracked_path(entry: Dict[str, Any]) -> Optional[str]:
    """entry が accept 判定に使うファイルパス（skill 提案 / advisory 提案の単一ソース）。

    advisory は対象が SKILL.md とは限らない（pytest.ini 等）ので ``target_path`` を持つ。
    パースを2箇所に分けると片側だけ直して desync する（pitfall_copied_parse_convention_partial_fix）
    ため、ingest・``undrained_applied``・marker supersede はこの1関数を共有する。
    """
    return entry.get("target_path") or entry.get("skill_path")
