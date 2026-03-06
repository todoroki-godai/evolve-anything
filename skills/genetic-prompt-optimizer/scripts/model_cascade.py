"""Model Cascade: FrugalGPT カスケードによるモデルコストの段階化"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# デフォルトモデル定数
TIER1_MODEL: str = "haiku"
TIER2_MODEL: str = "sonnet"
TIER3_MODEL: str = "opus"

_TIER_MODELS = {1: TIER1_MODEL, 2: TIER2_MODEL, 3: TIER3_MODEL}


class ModelCascade:
    """3段カスケードでモデルコストを段階化する。"""

    def __init__(self, config: dict | None = None, enabled: bool = True):
        """カスケード設定を読み込み。

        config keys: tier1, tier2, tier3 (モデル名)
        環境変数: TIER1_MODEL, TIER2_MODEL, TIER3_MODEL でも上書き可能
        """
        self._enabled = enabled
        config = config or {}

        self._models = {
            1: os.environ.get("TIER1_MODEL", config.get("tier1", TIER1_MODEL)),
            2: os.environ.get("TIER2_MODEL", config.get("tier2", TIER2_MODEL)),
            3: os.environ.get("TIER3_MODEL", config.get("tier3", TIER3_MODEL)),
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    def get_model(self, tier: int) -> str:
        """指定 Tier のモデル名を返す。"""
        if tier not in (1, 2, 3):
            raise ValueError(f"Invalid tier: {tier}. Must be 1, 2, or 3")
        return self._models[tier]

    def run_with_tier(
        self,
        prompt: str,
        tier: int,
        *,
        cwd: Optional[str] = None,
        timeout: int = 120,
    ) -> str:
        """指定 Tier のモデルでプロンプトを実行。

        失敗時は次の Tier にエスカレーション。
        Tier 3 失敗時はエラーを伝搬。
        """
        if tier not in (1, 2, 3):
            raise ValueError(f"Invalid tier: {tier}. Must be 1, 2, or 3")

        model = self._models[tier]
        try:
            return self._execute(prompt, model, cwd=cwd, timeout=timeout)
        except Exception as e:
            if tier < 3:
                next_tier = tier + 1
                logger.warning(
                    f"Tier {tier} ({model}) failed: {e}. "
                    f"Escalating to Tier {next_tier} ({self._models[next_tier]})"
                )
                return self.run_with_tier(prompt, next_tier, cwd=cwd, timeout=timeout)
            else:
                raise

    def _execute(
        self, prompt: str, model: str, *, cwd: Optional[str] = None, timeout: int = 120
    ) -> str:
        """claude -p --model でプロンプトを実行。"""
        cmd = ["claude", "-p", "--model", model]
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude -p failed (model={model}): {result.stderr}")
        return result.stdout


def load_cascade_config(config_path: Optional[str | Path] = None) -> dict:
    """設定ファイル（YAML）からカスケード設定を読み込む。
    パースエラー時はデフォルト値を使用。
    """
    if config_path is None:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        logger.warning(f"Failed to parse cascade config {path}, using defaults")
        return {}
