"""evolve_revert._availability — board 表示用の revert 可否判定（#402 段階4 §3）。

listing 時点（apply 前）に、accept entry がそもそも revert 可能なデータを持っているかを
判定する純粋関数（I/O ゼロ）。apply 時のみ判明する failure（対象パスの解決失敗・hardlink
等・``_target.resolve_target`` / ``_apply.apply_revert`` の対象）とは別レイヤー——対象が
その後移動・削除されていても、ここでの判定は変わらない（「revert データを持っているか」
のみを見る）。

理由コードは3種（設計正典 §3）。**機械用コード + 人間用の日本語1行を組で持つ**
（``REASON_LABELS``。board の表示は日本語なので表示側は日本語を出す・コード名は
プラグイン内部の PR 履歴を知らないと意味が取れないため）:

  - ``pre_extension``:    記録拡張（PR-1）前に採用された entry、または PR-1 パイプライン
                           （``evolve_decisions``）を経由しない writer（
                           ``optimize.py`` の ``save_history_entry`` / ``run_loop.py``）
                           による entry。revert フィールド（``revert_schema_version`` 等）
                           が一切無い。**恒久的に戻せない**（今後もデータは増えない）。
  - ``lane_unsupported``:  skill lane 以外の採用（rules / hooks 等）。**現行データでは
                           到達しない予約コード** — ADR-041 により remediation（rules/
                           hooks の採用）は ``optimize_history`` へ一切書き込まれない
                           （``evolve_decisions/_candidates.py::_extract_candidates`` の
                           docstring・``_ingest.py`` の advisory 分離を実測確認）ため、
                           現行の ``optimize_history`` は構造的に skill lane のみで
                           構成される。scope の取りうる値が将来増えた場合に備える。
  - ``before_too_large``:  emit 時の圧縮後サイズが上限を超え本文を保存できなかった
                           （``evolve_decision_ids.REVERT_REASON_BEFORE_TOO_LARGE`` と
                           同一コード。二重定義を避けるためそこから import する）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from evolve_decision_ids import REVERT_REASON_BEFORE_TOO_LARGE

REASON_PRE_EXTENSION = "pre_extension"
REASON_LANE_UNSUPPORTED = "lane_unsupported"
REASON_BEFORE_TOO_LARGE = REVERT_REASON_BEFORE_TOO_LARGE

# apply engine（_target.resolve_target）が受け付ける scope と同じ集合。ここでの
# 判定は listing 時点の静的チェックなので、apply 側の root 解決は行わない。
_SUPPORTED_SCOPES = ("global", "project")

REASON_LABELS: Dict[str, str] = {
    REASON_PRE_EXTENSION: (
        "戻す機能の導入前に採用されたため、変更前の内容が残っていません"
        "（今後も戻せません）"
    ),
    REASON_LANE_UNSUPPORTED: (
        "この種類の採用（rules / hooks 等）は戻す機能の対象外です"
        "（skill の採用のみ対象）"
    ),
    REASON_BEFORE_TOO_LARGE: (
        "変更前ファイルが保存上限を超えていたため戻せません"
        "（同じスキルを次に採用した時は戻せる可能性があります）"
    ),
}


def compute_revert_availability(entry: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """accept entry の revert 可否を判定する（純粋関数）。

    Returns:
        (available, reason) — 利用可能なら ``(True, None)``。不可なら
        ``(False, <上記 REASON_* のいずれか>)``。
    """
    if entry.get("revert_unavailable_reason") == REASON_BEFORE_TOO_LARGE:
        return False, REASON_BEFORE_TOO_LARGE
    if not entry.get("revert_schema_version"):
        return False, REASON_PRE_EXTENSION
    if entry.get("scope") not in _SUPPORTED_SCOPES:
        return False, REASON_LANE_UNSUPPORTED
    if not entry.get("revert_before_b64"):
        # スキーマはあるが本文が無く理由コードも付いていない（防御的フォールバック。
        # 通常は before_too_large で reason が付くはずだが、欠落時は安全側に倒す）。
        return False, REASON_PRE_EXTENSION
    # #402-D PR1 §2.7（team-lead 裁定）: apply_revert の必須検査（_apply.py の
    # after_sha/id 必須チェック）と同じ2条件を listing 時点でも検査する。schema は
    # あるが id/after_sha が無い entry は「戻すのに必要なデータが揃っていない」と
    # いう意味で pre_extension に相乗りさせる（新しい理由コードは作らない）。
    # D1 の新規 entry（A/B/C 全経路）は本設計により id/after_sha を常に伴って書か
    # れるため今この時点では実害は無いが、将来別の writer が増えたときの再発を
    # 構造で防ぐ（§0 の「read 側不変」制約の唯一の例外）。
    if not entry.get("id") or not entry.get("after_sha"):
        return False, REASON_PRE_EXTENSION
    return True, None
