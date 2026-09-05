"""tier skill の設計意図と直接編集禁止の運用契約を固定する（#625）。

段落の完全一致（split("\\n\\n")[2]）は、段落の前に別段落が挿入されるだけの無害な
編集（見出し追加・前置き段落の追加等）でも位置ずれで赤くなる過剰検出だった
（#625 レビュー [Should]）。本文が期待段落を**含むこと**＋当該節（H1見出しから次の
``## `` 見出しまでの区間）に「以前は」「往来」「YYYY-MM-DD 形式の日付」のような
**履歴語り**（歴史的経緯・比較の書き方）が混入していないこと、の2段判定に変える。
"""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TIER_SKILL = REPO_ROOT / "skills" / "tier" / "SKILL.md"

EXPECTED_PARAGRAPH = """\
model-routing のティア（HEAD/HARD/NORMAL/MECH/REVIEW ↔ model/effort）の正典は
`~/.claude/model-tiers.json`（CLI: `bin/evolve-tier`、#193）が一元管理する。分散管理（rule・
各 PJ の agent frontmatter・settings.json に個別記載）だとモデル変更のたびに全ファイルへの
手動追従が必要になり、取りこぼしによる設定ズレが起きる。**このスキル自体はファイルを直接編集しない** — 全ての変更は
`bin/evolve-tier` CLI 経由で行い、このスキルは「何をどう変えるか」の対話的な聞き取りと、
sync 適用前の diff 提示・承認取得を担う UX レイヤーに徹する。"""

# 履歴語りの sentinel: 「以前は」「往来」、YYYY-MM-DD 形式の日付。
# いずれか1つでも当該節に混入したら赤くする。
_HISTORY_SENTINELS = ("以前は", "往来")
_DATE_PATTERN = re.compile(r"20\d\d-\d\d-\d\d")


def _rationale_section(text: str) -> str:
    """H1 見出し（``# ...``）から次の ``## `` 見出しまでの区間を抜き出す。

    ``split("\\n\\n")[2]`` のような位置固定でなく見出し境界で抜き出すため、
    節の前に別段落が挿入されても（見出し・前置きの追加等）区間として拾い続ける。
    """
    idx_h1 = text.index("\n# ")
    idx_next_heading = text.index("\n## ", idx_h1)
    return text[idx_h1:idx_next_heading]


def test_tier_skill_rationale_paragraph_present() -> None:
    text = TIER_SKILL.read_text(encoding="utf-8")
    assert EXPECTED_PARAGRAPH in text


def test_tier_skill_rationale_section_has_no_history_narration() -> None:
    section = _rationale_section(TIER_SKILL.read_text(encoding="utf-8"))
    for sentinel in _HISTORY_SENTINELS:
        assert sentinel not in section
    assert not _DATE_PATTERN.search(section)


def test_tier_skill_keeps_no_direct_edit_cli_invariant() -> None:
    text = TIER_SKILL.read_text(encoding="utf-8")
    assert "**このスキル自体はファイルを直接編集しない**" in text
    assert "`bin/evolve-tier` CLI 経由で行い" in text
