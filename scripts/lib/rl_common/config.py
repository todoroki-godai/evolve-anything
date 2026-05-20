"""rl-anything userConfig (CC v2.1.83 manifest.userConfig) ユーティリティ。

USER_CONFIG_DEFAULTS / load_user_config / is_user_config_explicit / _parse_bool
を提供する。DATA_DIR 等のディレクトリ系は ``rl_common.__init__`` に残置
（テストが ``mock.patch.object(rl_common, "DATA_DIR", ...)`` で差し替えるため）。
"""
import os

# --- userConfig (CC v2.1.83 manifest.userConfig) ---
_USER_CONFIG_PREFIX = "CLAUDE_PLUGIN_OPTION_"

USER_CONFIG_DEFAULTS: dict[str, object] = {
    "auto_trigger": True,
    "evolve_interval_days": 7,
    "audit_interval_days": 30,
    "min_sessions": 10,
    "cooldown_hours": 24,
    "language": "ja",
    "growth_display": True,
    # cleanup スキル: 一時ディレクトリ削除候補の prefix (カンマ区切り)。
    # manifest.userConfig は boolean/number/string のみサポートのため string で
    # 受け取り、scripts/lib/cleanup_scanner.parse_prefix_config で list 化する。
    # 安全側デフォルト: rl-anything 名前空間のみ (ADR-021)。
    "cleanup_tmp_prefixes": "rl-anything-",
    # tool_duration hook: Bash コマンドをスロー判定する閾値 (ミリ秒)。
    # 短い値（例: 500）にすると検出数が増えるが JSONL が肥大化する。
    "slow_threshold_ms": 1000,
    # subagent_observe hook: セッション内 subagent 数がこの値に達したら警告。
    "subagent_warning_threshold": 5,
    # skill_evolve_assessment: LLM 評価に含めるグローバルスキル名（カンマ区切り）。
    # デフォルト空 = global は全除外。自作グローバルスキルがある場合に追加する。
    "evolve_global_allowlist": "",
}


def _parse_bool(value: str) -> bool:
    """文字列を bool にパースする。"""
    return value.lower() in ("true", "1", "yes")


def load_user_config() -> dict:
    """CC v2.1.83 userConfig の環境変数をパースしデフォルトとマージして返す。

    各キーは CLAUDE_PLUGIN_OPTION_<key> 環境変数で上書き可能。
    不正値はサイレントにデフォルトにフォールバックする。
    """
    config = dict(USER_CONFIG_DEFAULTS)
    for key, default in USER_CONFIG_DEFAULTS.items():
        env_val = os.environ.get(f"{_USER_CONFIG_PREFIX}{key}")
        if env_val is None:
            continue
        # bool/int キーへの空文字は未設定として扱う。string 型のみ空文字を意図的な override として許容 (#77)
        if not env_val and not isinstance(default, str):
            continue
        if isinstance(default, bool):
            config[key] = _parse_bool(env_val)
        elif isinstance(default, int):
            try:
                config[key] = int(env_val)
            except (ValueError, TypeError):
                pass  # keep default
        else:
            config[key] = env_val
    return config


def is_user_config_explicit(key: str) -> bool:
    """指定キーの userConfig 環境変数が明示的にセットされているか判定する。"""
    return os.environ.get(f"{_USER_CONFIG_PREFIX}{key}") is not None
