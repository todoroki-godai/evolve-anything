"""audit パッケージ内で共有される定数。

audit/__init__.py と audit/memory.py 等のサブモジュール双方から
参照される LIMITS / _STOPWORDS をここに集約する。
循環 import を避けるため、サブモジュールはここからのみ import する。
"""
from pathlib import Path

from line_limit import (
    MAX_PROJECT_RULE_LINES,
    MAX_RULE_LINES,
    MAX_SKILL_LINES,
)

# スキル収集・重複検出から除外するサブディレクトリ名。
# - .archive: rl-anything 自身がアーカイブしたスキル
# - _archived / disabled: ユーザーが手動でアーカイブ/無効化したスキル（#337）。
#   sys-bots は `.claude/skills/_archived/<name>/` に退避するため、除外しないと
#   remediation の missing_effort や skill_evolve batch に混入してノイズになる
#   （effort 付与提案が無意味・約80%の誤検知の一因）
# - .gstack-backup: gstack がスキル更新前に退避したバックアップ。実スキルと 1:1 で
#   コピーされるため、除外しないと phantom duplicate を大量検出する（docs-platform
#   evolve で 104 件、remediation の manual_required を支配し本物の issue を埋もれさせた）
EXCLUDED_SKILL_DIRS = frozenset({".archive", "_archived", "disabled", ".gstack-backup"})


def is_excluded_skill_path(path: Path) -> bool:
    """SKILL.md のパスが収集除外対象（.archive / _archived / disabled / .gstack-backup 配下）か判定する。"""
    return any(part in EXCLUDED_SKILL_DIRS for part in path.parts)

LIMITS = {
    "CLAUDE.md": 200,  # warning のみ（violation としては扱わない）
    "rules": MAX_RULE_LINES,
    "project_rules": MAX_PROJECT_RULE_LINES,
    "SKILL.md": MAX_SKILL_LINES,
    "MEMORY.md": 200,
    "memory": 120,
}

# セマンティック検証用ストップワード（英語冠詞・前置詞・助動詞 + 日本語助詞）
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "must", "can", "could",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "and", "but", "or", "nor", "not", "so", "yet", "if", "then", "than",
    "it", "its", "this", "that", "these", "those", "he", "she", "they",
    "we", "you", "i", "me", "my", "your", "his", "her", "our", "their",
    "の", "は", "が", "を", "に", "で", "と", "も", "や", "か",
    "する", "した", "して", "です", "ます", "ある", "いる", "なる",
})
