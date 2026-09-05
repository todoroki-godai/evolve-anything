"""tier skill の設計意図と直接編集禁止の運用契約を固定する（#625）。"""

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


def test_tier_skill_rationale_paragraph_exact_snapshot() -> None:
    text = TIER_SKILL.read_text(encoding="utf-8")
    paragraph = text.split("\n\n")[2]
    assert paragraph == EXPECTED_PARAGRAPH


def test_tier_skill_keeps_no_direct_edit_cli_invariant() -> None:
    text = TIER_SKILL.read_text(encoding="utf-8")
    assert "**このスキル自体はファイルを直接編集しない**" in text
    assert "`bin/evolve-tier` CLI 経由で行い" in text
