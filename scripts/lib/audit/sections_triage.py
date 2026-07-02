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
from typing import Any, Dict, List, Optional

from .advisory import build_advisory_section

# findings に件数を出す triage アクション（OK は「変更なし」なので件数行から除外）。
_TRIAGE_COUNT_ACTIONS = ("CREATE", "UPDATE", "SPLIT", "MERGE")

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
    """skill_triage の findings レーンの位置づけ行を返す（#478, #528-4）。

    PJ に custom スキルが無ければ None（triage 対象外で沈黙）。
    あれば「skill_triage の実データ（CREATE/UPDATE/SPLIT/MERGE 件数）は
    `phases.skill_triage` を参照する」という **findings レーンの軽量リマインダ** を返す。
    triage は再実行しない（重い・副作用回避）ため、件数自体はここでは持たない。

    #528-4: 旧版はこの builder が「必ずサマリ表示すること」という assistant への
    指示文（MUST 表現）を出していたが、observability は findings レーン（実データの観測）
    であって指示の置き場ではない。指示（MUST）は SKILL.md Step 3.8 に移管し、ここは
    「実データがどこにあるか」を案内する findings 行に留める。
    """
    def compute(proj: Path) -> Optional[bool]:
        return True if _has_custom_skills(proj) else None

    def render(_data: bool) -> List[str]:
        return [
            "実データは `result[\"phases\"][\"skill_triage\"]` の CREATE / UPDATE / "
            "SPLIT / MERGE（各リストの件数と上位候補）にある。trajectory 由来の新スキル候補"
            "（CREATE）は埋没しやすいレーン（#478）。表示手順は evolve SKILL.md Step 3.8。",
        ]

    return build_advisory_section(
        project_dir,
        title="Skill Triage (CREATE/UPDATE/SPLIT/MERGE)",
        compute=compute,
        applicable=lambda _data: True,
        render=render,
    )


def build_skill_triage_counts_lines(
    triage_result: Optional[Dict[str, Any]],
) -> Optional[List[str]]:
    """triage_result（phases.skill_triage）から実件数の findings 行を生成する（#528-4）。

    `build_skill_triage_section` は triage を再実行しない設計のため件数を持てず、
    「参照先の案内」だけを出していた。しかし observability.skill_triage は findings
    レーン（実データの観測）であり、件数という findings が無いのは contract 違反だった。
    本関数は evolve.py が既に算出済みの triage_result（`result["phases"]["skill_triage"]`）
    を入力に、CREATE/UPDATE/SPLIT/MERGE の実件数を 1 行に畳んで返す。evolve.py が
    observability.skill_triage（案内行）にこの件数行を追記する。

    silence != evaluated の自己適用: 全 0 件でも件数行を出す（「観測したが 0」と
    「未観測」を区別する）。triage_result が None / error / skipped のときだけ None。

    Args:
        triage_result: `triage_all_skills` の返り値（CREATE/UPDATE/SPLIT/MERGE/OK の
            各キーがリスト）。`{"error": ...}` / `{"skipped": True}` の劣化形も受ける。

    Returns:
        findings 行のリスト。triage が走らなかった場合は None（沈黙）。
    """
    if not isinstance(triage_result, dict):
        return None
    if triage_result.get("error") or triage_result.get("skipped"):
        return None
    # CREATE/UPDATE/SPLIT/MERGE のどのバケツも無い（triage 構造でない）なら沈黙。
    if not any(action in triage_result for action in _TRIAGE_COUNT_ACTIONS):
        return None

    parts = []
    for action in _TRIAGE_COUNT_ACTIONS:
        bucket = triage_result.get(action)
        count = len(bucket) if isinstance(bucket, list) else 0
        parts.append(f"{action} {count}")
    return [
        "実データ件数（findings）: " + " / ".join(parts) + "。",
    ]
