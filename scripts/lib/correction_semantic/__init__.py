"""correction_semantic — correction capture の二層化（#431）。

hot hook（CORRECTION_PATTERNS）は語彙依存で文中の修正を構造的に取りこぼす（#431 背景）。
本 package は **定期バッチ LLM 意味判定**（auto_memory の 2 相方式 / ADR-037 と同型）で、
#430 utterances.db の dialogue 発話を Haiku が読み「ユーザーが Claude の方向を正した
ターンか」を二値判定し、修正なら言い回し（イディオム）を抽出する。

二層化の原則:
- 検出結果は **corrections 本流に直接入れない**。#432 と共有する weak_signals レーンへ
  channel="llm_judge" で隔離記録し、reflect 確認後にのみ corrections へ昇格する。
- 抽出した言い回しは provenance（元発話の物理キー・判定理由）付きで **個人辞書**
  （correction_idioms.jsonl）に蓄積。実コーパスで precision 検証後に hot hook の補助
  パターンへ昇格可能（#431 提案 2）。
- フェーズ昇格カウント（growth_engine の corrections>=10）は **human-source のみ**で駆動。
  機械ノイズ（Stop hook 等）で状態が動かないようにする（provenance_weight）。

LLM 呼び出しは llm_broker の 3 相（build_requests / parse_responses）に乗せるため、
Python は claude -p を一切呼ばない（no-llm-in-tests と完全整合）。

サブモジュール:
- ``store``            — correction_idioms.jsonl（個人辞書）+ 判定進捗の append/read（dry-run ゼロ書込）
- ``prompt``           — 30 件バッチプロンプトの組み立て + JSON verdict のパース
- ``batch``            — 判定オーケストレーション（emit / ingest 2 相・weak_signals 隔離記録）
- ``provenance_weight``— corrections の human-source 判定（フェーズ昇格カウント用）
"""
from __future__ import annotations

from .provenance_weight import (  # noqa: F401
    HUMAN_SOURCES,
    MACHINE_SOURCES,
    count_human_corrections,
    is_human_correction,
)

# weak_signals レーンで本判定を識別する channel 名（#432 のレーンを共有）。
LLM_JUDGE_CHANNEL = "llm_judge"

# 1 LLM call にまとめる発話件数（#431: 30 件程度を 1 call）。
DEFAULT_BATCH_SIZE = 30
