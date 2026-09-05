"""tier skill の設計意図と直接編集禁止の運用契約を固定する（#625）。

段落の完全一致（split("\\n\\n")[2]）は、段落の前に別段落が挿入されるだけの無害な
編集（見出し追加・前置き段落の追加等）でも位置ずれで赤くなる過剰検出だった
（#625 レビュー [Should]）。本文が期待段落を**含むこと**＋当該節（H1見出しから次の
``## `` 見出しまでの区間）に「以前は」「往来」「YYYY-MM-DD 形式の日付」のような
**履歴語り**（歴史的経緯・比較の書き方）が混入していないこと、の2段判定に変える。
"""

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


def test_tier_skill_rationale_paragraph_present() -> None:
    text = TIER_SKILL.read_text(encoding="utf-8")
    assert EXPECTED_PARAGRAPH in text


def test_tier_skill_keeps_no_direct_edit_cli_invariant() -> None:
    text = TIER_SKILL.read_text(encoding="utf-8")
    assert "**このスキル自体はファイルを直接編集しない**" in text
    assert "`bin/evolve-tier` CLI 経由で行い" in text
