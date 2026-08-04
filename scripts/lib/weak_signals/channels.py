"""weak_signals.channels — weak_signal channel の正準集合（producer 側単一ソース、#379 Step 1）。

外部レビュー指摘: ``correction_semantic/review_channels.py`` の ``REVIEW_CHANNELS``
``CONTENT_POOR_CHANNELS`` は「y/n 確認に出すか」という**消費側の分類**であり、実際に
``append_signals()`` へ書き込まれる channel の allowlist ではない。``WeakSignal.channel``
は任意 str で ``append_signals()`` 側に allowlist が無いため、新 detector が
review_channels.py に載らない channel 文字列で書いても、shrink_freeze の凍結契約テスト
（review_channels.py の分類集合だけを見ていた）は素通りしてしまっていた。

本モジュールは実際の producer（``WeakSignal(channel=...)`` を生成する全箇所）の channel
リテラルを集約した正準集合を定義する。review_channels.py の分類集合はこの正準集合の
部分集合であるべき（契約テストで検証: test_correction_semantic_review_channels.py）。
``weak_signals.store.append_signals()`` はこの正準集合を凍結中の書込みゲートに使う。

現行 6 channel（#379 Step 1 実装時点で producer 全 grep により確認済み。新 channel を
追加するときはこの一覧と shrink_freeze.FROZEN_WEAK_SIGNAL_CHANNELS を同時に更新する）:

- ``llm_judge``            — correction_semantic/batch.py（Haiku バッチ判定・#431）
- ``rephrase``             — weak_signals/detectors.py（言い直し検出・#432③）
- ``permission_deny``      — weak_signals/detectors.py（ツール実行拒否検出・#432②）
- ``verbosity``            — verbosity/judge.py（冗長判定・#75, #171）
- ``esc_interrupt``        — weak_signals/detectors.py（Esc 中断検出）
- ``manual_edit_after_ai`` — weak_signals/detectors.py（直後手編集検出）
"""
from __future__ import annotations

from typing import FrozenSet

WEAK_SIGNAL_CHANNELS: FrozenSet[str] = frozenset(
    {
        "llm_judge",
        "rephrase",
        "permission_deny",
        "verbosity",
        "esc_interrupt",
        "manual_edit_after_ai",
    }
)
