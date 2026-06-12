"""skill_triage の CREATE/UPDATE/SPLIT/MERGE サマリ surface 契約（#478）。

背景: trajectory 由来の新スキル候補（triage の CREATE）が remediation の
低 confidence batch_skip の1行に畳まれ、実質ユーザーに提示されない問題があった。
evolve SKILL.md にも triage 結果を表示する Step が無かった。

この builder は triage を**再実行しない**（eval set 生成・meta_quality 等が重く、
audit phase で副作用を起こすため）。代わりに「evolve の result["phases"]["skill_triage"]
にある CREATE/UPDATE/SPLIT/MERGE を必ず surface せよ」という契約行を出す。
silence != evaluated 原則を triage 経路にも適用する。

applicability: PJ に custom スキルが1つでもあるときだけ契約行を出す（スキルが
無い PJ では triage 対象が無いので None で沈黙）。判定は skills ディレクトリの
SKILL.md 存在チェックのみ（決定論・LLM 非依存・triage 非実行）。

observability contract から参照される `build_*_section` 契約
（`(project_dir) -> Optional[List[str]]`）は他 builder と同一。
"""
from pathlib import Path
from typing import List, Optional

# custom スキルを探す候補ディレクトリ（PJ ローカル）。
_SKILL_DIR_CANDIDATES = (
    Path(".claude") / "skills",
    Path("skills"),
)


def _has_custom_skills(project_dir: Path) -> bool:
    """PJ に SKILL.md を持つ custom スキルが1つでもあるか（決定論・cheap）。"""
    for rel in _SKILL_DIR_CANDIDATES:
        base = project_dir / rel
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and (child / "SKILL.md").exists():
                return True
    return False


def build_skill_triage_section(project_dir: Path) -> Optional[List[str]]:
    """skill_triage の結果サマリを必ず surface する契約行を返す（#478）。

    PJ に custom スキルが無ければ None（triage 対象外で沈黙）。
    あれば「evolve の skill_triage phase の CREATE/UPDATE/SPLIT/MERGE を必ず提示せよ」
    という契約行を返す。triage は再実行しない（重い・副作用回避）。
    """
    if not _has_custom_skills(project_dir):
        return None

    return [
        "## Skill Triage (CREATE/UPDATE/SPLIT/MERGE)",
        "",
        "evolve の `result[\"phases\"][\"skill_triage\"]` にある CREATE / UPDATE / "
        "SPLIT / MERGE 候補を必ずサマリ表示すること（#478）。"
        "trajectory 由来の新スキル候補（CREATE）が remediation の低 confidence "
        "batch_skip に畳まれて埋没しないよう、各アクションの件数と上位候補を surface する。",
        "",
    ]
