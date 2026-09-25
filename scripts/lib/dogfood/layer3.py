"""Layer 3 オーケストレーション（#496）: 全 SKILL.md を走査して block を検証実行する。

skill_blocks の抽出・分類・実行ロジックを全 ``skills/*/SKILL.md`` に適用し、結果を集約する。
import 検証は SKILL.md / bin が前提とする sys.path だけを積む（conftest 下駄なし）＝
ユーザーと同じ起動経路。#486/#487/#488/#495 を赤として検出するのが受け入れ基準。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from . import skill_blocks


def _layer3_sys_path_dirs(repo_root: Path) -> List[Path]:
    """import 検証時にゲートが追加する sys.path。

    **空リストを返す**のが肝（#496/#487）。ユーザーが SKILL.md の python ブロックを
    Claude セッション内でそのまま実行するとき、``scripts/lib`` は自動では path に乗らない。
    ゲートが scripts/lib を勝手に注入すると sys.path 不足バグ（#487 agent-brushup 型）を
    隠してしまう。各ブロックが自前で行う ``sys.path.insert`` だけが
    ``skill_blocks._run_import_check`` の setup として効く＝素の起動経路を忠実に再現する。
    """
    return []


def find_skill_mds(repo_root: Path) -> List[Path]:
    """``skills/*/SKILL.md`` と ``skills/*/references/*.md`` を列挙する（ソート済み）。

    ``references/*.md`` は従来走査対象外だった（prune-merge.md 等の見逃しの根本原因の1つ）。
    SKILL.md から参照される補助手順書であり、同じコードブロック規約（fenced python/bash）
    を使うため、SKILL.md と同じ検証対象にする。
    """
    skills_dir = Path(repo_root) / "skills"
    if not skills_dir.exists():
        return []
    paths = list(skills_dir.glob("*/SKILL.md")) + list(skills_dir.glob("*/references/*.md"))
    return sorted(paths)


def _skill_name_for(skill_md: Path) -> str:
    """``skill_md`` が属するスキル名を返す（``references/*.md`` は親の親を見る）。"""
    if skill_md.parent.name == "references":
        return skill_md.parent.parent.name
    return skill_md.parent.name


def run_layer3(repo_root: Path) -> Dict[str, Any]:
    """全 SKILL.md / references/*.md の code block を抽出・分類・検証実行する。

    返り値: ``{"skills": [{"skill": name, "blocks": [run_block 結果...]}],
               "summary": {"pass": n, "fail": n, "skip": n}}``
    """
    repo_root = Path(repo_root)
    sys_path_dirs = _layer3_sys_path_dirs(repo_root)
    out_skills: List[Dict[str, Any]] = []
    summary = {"pass": 0, "fail": 0, "skip": 0}

    for skill_md in find_skill_mds(repo_root):
        skill_name = _skill_name_for(skill_md)
        blocks = skill_blocks.extract_code_blocks(skill_md)
        block_results: List[Dict[str, Any]] = []
        for block in blocks:
            try:
                res = skill_blocks.run_block(block, repo_root=repo_root, sys_path_dirs=sys_path_dirs)
            except Exception as e:  # noqa: BLE001
                res = {
                    "status": "fail",
                    "mode": "error",
                    "line": block.get("line", 0),
                    "source": block.get("source", ""),
                    "detail": f"run_block raised: {e!r}",
                }
            summary[res["status"]] = summary.get(res["status"], 0) + 1
            block_results.append(res)
        out_skills.append({"skill": skill_name, "blocks": block_results})

    return {"skills": out_skills, "summary": summary}
