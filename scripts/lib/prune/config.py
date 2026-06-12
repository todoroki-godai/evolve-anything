"""prune の閾値定数 + evolve-state.json からの設定ロード (旧 prune.py 由来)。

prune/__init__.py から re-export される（後方互換）。
DATA_DIR は package 経由で遅延参照する（テスト mock.patch.object(prune, "DATA_DIR", ...) 追従）。
"""
import json


DEFAULT_DECAY_DAYS = 90
DEFAULT_DECAY_THRESHOLD = 0.2
CORRECTION_PENALTY = 0.15
ZERO_INVOCATION_DAYS = 30

# Skill 発火の usage 記録経路が修正された日付 (#478)。
# この日以前のデータは欠損しているため、zero_invocation を「使われていない」と
# 断定せず advisory を付与して人間判断に委ねる。
USAGE_RECORDING_FIX_DATE = "2026-06-12"

# Retirement 機構 (Library Drift arXiv:2605.19576 に基づく)
RETIREMENT_CONTRIBUTION_THRESHOLD = 0.3  # これ以下の貢献スコアをアーカイブ候補とみなす
RETIREMENT_MIN_INVOCATIONS = 5  # スコア算出に必要な最低呼び出し回数

DEFAULT_MERGE_SIMILARITY_THRESHOLD = 0.60
DEFAULT_INTERACTIVE_MERGE_THRESHOLD = 0.40
DEFAULT_DRIFT_THRESHOLD = 0.5


def _load_state_value(key: str, default: float, *, validate_range: bool = False) -> float:
    """evolve-state.json から指定キーの float 値を読み込む共通ヘルパ。

    DATA_DIR は package 経由で遅延参照（mock.patch 追従）。
    validate_range=True で 0.0 <= val <= 1.0 を検証。
    """
    from . import DATA_DIR  # noqa: PLC0415

    state_file = DATA_DIR / "evolve-state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            val = float(state.get(key, default))
            if validate_range and not (0.0 <= val <= 1.0):
                return default
            return val
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
    return default


def load_merge_similarity_threshold() -> float:
    """evolve-state.json から reorganize_merge_similarity_threshold を読み込む。"""
    return _load_state_value("reorganize_merge_similarity_threshold", DEFAULT_MERGE_SIMILARITY_THRESHOLD)


def load_interactive_merge_threshold() -> float:
    """evolve-state.json から interactive_merge_similarity_threshold を読み込む。"""
    return _load_state_value("interactive_merge_similarity_threshold", DEFAULT_INTERACTIVE_MERGE_THRESHOLD)


def load_decay_threshold() -> float:
    """evolve-state.json から decay_threshold を読み込む。"""
    return _load_state_value("decay_threshold", DEFAULT_DECAY_THRESHOLD)


def load_drift_threshold() -> float:
    """evolve-state.json から reference_drift_threshold を読み込む。"""
    return _load_state_value("reference_drift_threshold", DEFAULT_DRIFT_THRESHOLD, validate_range=True)
