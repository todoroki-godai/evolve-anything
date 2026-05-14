"""audit パッケージ内で共有される定数。

audit/__init__.py と audit/memory.py 等のサブモジュール双方から
参照される LIMITS / _STOPWORDS をここに集約する。
循環 import を避けるため、サブモジュールはここからのみ import する。
"""
from line_limit import (
    MAX_PROJECT_RULE_LINES,
    MAX_RULE_LINES,
    MAX_SKILL_LINES,
)

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
