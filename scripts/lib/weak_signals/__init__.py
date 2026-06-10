"""weak_signals — 暗黙修正シグナルの決定論検出 → weak_signals レーン（#432）。

明示的な修正発話は語彙依存で稀（#431）。一方、修正の**行動シグナル**は語彙非依存で
決定論検出できる。本パッケージは 4 チャネルをゼロ LLM・hot path 非介入（バッチ側）で検出し、
``weak_signals.jsonl`` レーンに provenance 付きで記録する。

チャネル:
1. 直後手編集    — Claude の Edit/Write 直後にユーザー（or linter）が同一ファイルを変更
2. permission deny — ツール実行の拒否（errors.jsonl の permission_denied レコード）
3. 言い直し      — 連続する human 発話の高類似（utterances.db / jaccard token 重複）
4. Esc 中断      — 実行中の介入（transcript の [Request interrupted by user]）

corrections 本流には**直接入れない**。deny は「今はやるな」、手編集は「続きの作業」の
可能性があり本質的にノイジーなため、reflect 確認後にのみ昇格する（#431 と共有するレーン）。

サブモジュール:
- ``store``     — weak_signals.jsonl の append/read（dry-run 書き込みゼロ・slug スコープ）
- ``detectors`` — 4 チャネルの決定論検出器
- ``batch``     — 検出オーケストレーション（dedup + dry-run ゲート貫通）
"""
from __future__ import annotations

from .store import (  # noqa: F401
    WeakSignal,
    append_signals,
    default_store_path,
    read_signals,
)

CHANNELS = ("manual_edit_after_ai", "permission_deny", "rephrase", "esc_interrupt")
