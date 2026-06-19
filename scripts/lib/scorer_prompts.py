"""evolve-scorer の軸別評価プロンプト。

run-loop.py と score_noise.py 両方から参照される単一の Source of Truth。
プロンプト改善時はこのファイルを変更し、両方の機能に同時反映する。
"""

import os
from pathlib import Path
from typing import Dict, Optional

# プロンプトオーバーライドファイル: 存在すれば下記のデフォルトを上書きできる
# `evolve-evaluator-prompts` スキルが書き込む形式:
#   {axis}.txt に {content} placeholder を含むプロンプト本文
PROMPT_OVERRIDE_DIR_ENV = "CLAUDE_PLUGIN_DATA"

DEFAULT_AXIS_WEIGHTS: Dict[str, float] = {
    "technical": 0.40,
    "domain": 0.40,
    "structure": 0.20,
}

DEFAULT_AXIS_PROMPTS: Dict[str, str] = {
    "technical": """以下のClaude Codeスキル定義を技術品質の観点で評価してください。

評価項目（各0.0〜1.0）:
- 明確性: 指示が明確で曖昧さがないか
- 完全性: 必要な情報が全て含まれているか
- 一貫性: 用語・スタイルが統一されているか
- エッジケース: 例外や境界条件への対応があるか
- テスト可能性: 指示の成果を検証できるか

スキル:
```markdown
{content}
```

5項目の平均を total として、数値のみ回答してください（例: 0.75）""",
    "domain": """以下のClaude Codeスキル定義をドメイン品質の観点で評価してください。

評価項目（各0.0〜1.0）:
- 正確性: ドメイン知識が正しいか
- 実用性: 実際のタスクに役立つか
- 保守性: 変更・拡張が容易か
- 完全性: ドメインの重要な側面を網羅しているか

スキル:
```markdown
{content}
```

4項目の平均を total として、数値のみ回答してください（例: 0.75）""",
    "structure": """以下のClaude Codeスキル定義を構造品質の観点で評価してください。

評価項目（各0.0〜1.0）:
- フォーマット: Markdownの構造が適切か
- 長さ: 冗長でなく、かつ不足がないか
- 例示: 具体例が適切に含まれているか
- 参照: 関連リソースへの参照が適切か
- 規約準拠: Claude Code スキルの慣習に沿っているか

スキル:
```markdown
{content}
```

5項目の平均を total として、数値のみ回答してください（例: 0.75）""",
}


def _override_dir() -> Optional[Path]:
    """プロンプトオーバーライドディレクトリを返す（存在する場合のみ）。"""
    base = os.environ.get(PROMPT_OVERRIDE_DIR_ENV)
    if not base:
        return None
    p = Path(base) / "scorer_prompts"
    return p if p.exists() else None


def get_axis_prompts() -> Dict[str, str]:
    """現在有効な軸別プロンプトを返す（オーバーライドがあれば反映）。"""
    prompts = dict(DEFAULT_AXIS_PROMPTS)
    override_dir = _override_dir()
    if override_dir is not None:
        for axis in prompts.keys():
            override_file = override_dir / f"{axis}.txt"
            if override_file.exists():
                prompts[axis] = override_file.read_text(encoding="utf-8")
    return prompts


def write_override(axis: str, prompt: str) -> Path:
    """プロンプトオーバーライドを書き込み、保存先パスを返す。

    オーバーライドはユーザー設定ディレクトリに保存され、
    プラグイン本体のソースを変更せずに評価プロンプトを差し替えできる。
    """
    import os
    base = os.environ.get(PROMPT_OVERRIDE_DIR_ENV)
    if not base:
        raise RuntimeError(
            f"環境変数 {PROMPT_OVERRIDE_DIR_ENV} が未設定。"
            "プラグインデータディレクトリを設定してください。"
        )
    if axis not in DEFAULT_AXIS_PROMPTS:
        raise ValueError(f"未知の軸: {axis}（許可: {list(DEFAULT_AXIS_PROMPTS.keys())}）")
    if "{content}" not in prompt:
        raise ValueError("プロンプトに {content} placeholder が必要です")

    out_dir = Path(base) / "scorer_prompts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{axis}.txt"
    out_file.write_text(prompt, encoding="utf-8")
    return out_file
