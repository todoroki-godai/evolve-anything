"""ADR-054 Phase 0（B1）: SessionStart 通知の収集・digest・merge ロジック。

``hooks/restore_state.py`` 1073行が file-size-budget.md の 800行 hard limit を超えたため、
9系統の収集関数（``_build_*_output``）・``NotificationItem`` 契約・digest/merge ロジックを
本パッケージへ分割した（``audit.py`` 2046→178行・``evolve/__init__.py`` 分割と同じ手法・
#531/ADR-048）。振る舞いは変更しない（純粋な移動）。``hooks/restore_state.py`` は「収集を
呼ぶ → merge → print → commit」の薄いオーケストレーションだけを残す。

公開 API は本ファイルからの再エクスポートで維持される。``hooks/restore_state.py`` はここから
import した名前をそのまま自分の module 名前空間に置くため、既存テストの
``monkeypatch.setattr(restore_state, "_build_x_output", ...)`` は変更なく機能する
（bare name は呼び出し元モジュールの globals で解決されるため）。
"""
from .model import NotificationItem, _classify_daily_snapshot_file  # noqa: F401
from .collectors import (  # noqa: F401
    peek_pending_trigger,
    delete_pending_trigger,
    _build_pending_trigger_output,
    _build_spec_drift_output,
    _resolve_canonical_history_file,
    _build_evolve_drain_output,
    _build_data_dir_migration_output,
    utterance_staleness_advisory,
    _utterance_staleness_age_days,
    _build_utterance_staleness_output,
    _resolve_queue_data,
    _build_evolve_queue_output,
    _build_session_proposal_output,
    _judge_cap_digest,
    _build_judge_cap_output,
    _build_icebox_output,
    _build_live_checkout_output,
    _pj_slug,
    _queue_notice,
)
from .merge import (  # noqa: F401
    TIER2_BUDGET_CHARS,
    _merge_notification_text,
    _build_additional_context,
)
