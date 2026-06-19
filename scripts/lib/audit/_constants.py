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
# - .archive: evolve-anything 自身がアーカイブしたスキル
# - _archived / disabled: ユーザーが手動でアーカイブ/無効化したスキル（#337）。
#   sys-bots は `.claude/skills/_archived/<name>/` に退避するため、除外しないと
#   remediation の missing_effort や skill_evolve batch に混入してノイズになる
#   （effort 付与提案が無意味・約80%の誤検知の一因）
# - .gstack-backup: gstack がスキル更新前に退避したバックアップ。実スキルと 1:1 で
#   コピーされるため、除外しないと phantom duplicate を大量検出する（docs-platform
#   evolve で 104 件、remediation の manual_required を支配し本物の issue を埋もれさせた）
EXCLUDED_SKILL_DIRS = frozenset({".archive", "_archived", "disabled", ".gstack-backup"})

# 走査汚染源として除外するディレクトリ名（#419）。
# - node_modules: 外部 npm パッケージ同梱の SKILL.md が rglob で拾われ、
#   hardcoded_value 検出のノイズ源になる
EXCLUDED_VENDOR_DIRS = frozenset({"node_modules"})

# rglob("SKILL.md") は `.hermes`（gstack 同梱コピー）/ `.git` 等の任意 dot-dir まで
# 再帰してしまう。走査起点（`.claude/skills/`）より深いコンポーネントの dot-dir
# 配下はすべて除外する（#419）。dot-dir 判定は `skills/` セグメント以降にのみ効かせ、
# 走査対象 PJ の親パスに dot-dir（例: ~/.config/... 配下の PJ）があっても誤除外しない。
_SCAN_ANCHOR_DIR = "skills"


def is_excluded_skill_path(path: Path) -> bool:
    """SKILL.md のパスが収集除外対象か判定する。

    除外対象（#337, #419）:
    - .archive / _archived / disabled / .gstack-backup 配下（アーカイブ・バックアップ）
    - node_modules 配下（外部 vendor パッケージ）
    - 走査起点 `skills/` より深い任意の dot-dir 配下（.hermes / .git 等の走査汚染）
    """
    parts = path.parts
    if any(part in EXCLUDED_SKILL_DIRS for part in parts):
        return True
    if any(part in EXCLUDED_VENDOR_DIRS for part in parts):
        return True
    # 最初の `skills/` セグメント以降の component に dot-dir があれば除外。
    # 親パス（PJ ルートより上）の dot-dir は走査対象外なので評価しない。
    # 最初の出現を anchor にするのは、`.hermes/skills/...` のように dot-dir 配下に
    # 入れ子の `skills/` を持つ vendor コピーを取りこぼさないため（最後の出現だと
    # 入れ子 skills 以降に dot-dir が無く誤って許容してしまう）。
    try:
        anchor_idx = parts.index(_SCAN_ANCHOR_DIR)
    except ValueError:
        anchor_idx = -1
    for part in parts[anchor_idx + 1:]:
        if part.startswith("."):
            return True
    return False

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
