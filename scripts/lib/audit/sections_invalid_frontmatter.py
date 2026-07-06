"""Invalid Frontmatter の決定論検出器 + observability セクション（#167）。

背景: atlas-breeaders の 3 スキルが frontmatter の YAML として不正だった
（`description:` の非引用スカラーに `トリガー: ` のコロン+空白が入り `yaml.safe_load` が
ScannerError）。CC は frontmatter を `yaml.safe_load` で読むため、これらは name/description/
trigger を読めず**自動発火していなかった**。`frontmatter.parse_frontmatter` は不正 YAML を
黙って `{}` にフォールバックするため、①missing_effort に誤分類され、②「CC 発火不能」を
直接 surface する検出器が無かった。本モジュールがその欠落を埋める。

判定は `frontmatter.detect_frontmatter_error`（frontmatter はあるが YAML パース不能なものを
1 行 error として返す・`parse_frontmatter` は改変しない）に委譲する。決定論・LLM 非依存。

スコープは `.claude/skills/**/SKILL.md` のみ（rules/agents/* は今回対象外・将来拡張）。
section builder は #115 共通枠（`advisory.build_advisory_section`）で組み立て、
`observability._OBSERVABILITY_BUILDERS` に登録して markdown / 構造化 両経路へ surface する。
severity は ⚠（CC 発火不能＝重大）。clean（0 件）時は section を出さない（None・沈黙）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ._constants import is_excluded_skill_path
from .advisory import build_advisory_section


def detect_invalid_frontmatter(project_dir: Path) -> List[Dict[str, str]]:
    """`.claude/skills/**/SKILL.md` から YAML パース不能な frontmatter を列挙する（#167）。

    archived/backup 配下（`is_excluded_skill_path`）は除外。出力はパス昇順（決定論）。

    Returns:
        [{"skill_name", "skill_path", "error"}] のリスト（該当なしは空リスト）。
    """
    from frontmatter import detect_frontmatter_error

    project_dir = Path(project_dir)
    skills_dir = project_dir / ".claude" / "skills"
    if not skills_dir.is_dir():
        return []

    invalid: List[Dict[str, str]] = []
    for skill_md in sorted(skills_dir.rglob("SKILL.md")):
        if is_excluded_skill_path(skill_md):
            continue
        error = detect_frontmatter_error(skill_md)
        if error:
            invalid.append({
                "skill_name": skill_md.parent.name,
                "skill_path": str(skill_md),
                "error": error,
            })
    # agents/* / rules/* は今回対象外（将来拡張）。SKILL.md のみをスコープとする。
    return invalid


def build_invalid_frontmatter_section(project_dir: Path) -> Optional[List[str]]:
    """YAML パース不能な frontmatter のスキルを audit に surface する（#167）。

    観測可能性:
    - skills ディレクトリが無い PJ → None（沈黙）
    - 該当なし（全 valid）→ None（沈黙。CC 発火不能は「該当あり」でのみ警告する重大系）
    - 該当あり → ⚠ + evidence（skill 名 + 相対 path + 1 行 error）
    """

    def compute(proj: Path) -> Dict[str, Any]:
        skills_dir = proj / ".claude" / "skills"
        return {
            "has_skills_dir": skills_dir.is_dir(),
            "invalid": detect_invalid_frontmatter(proj),
            "project_dir": proj,
        }

    def render(data: Dict[str, Any]) -> List[str]:
        invalid: List[Dict[str, str]] = data["invalid"]
        proj: Path = data["project_dir"]
        lines = [
            f"⚠ frontmatter が YAML として壊れているスキルが {len(invalid)} 件"
            "（CC は frontmatter を yaml.safe_load で読むため、これらのスキルは name/"
            "description/trigger を読めず**自動発火しません**）。frontmatter を修正してください:",
        ]
        for e in invalid:
            try:
                rel = Path(e["skill_path"]).relative_to(proj)
            except ValueError:
                rel = Path(e["skill_path"])
            lines.append(f"  ・{e['skill_name']} ({rel}): {e['error']}")
        return lines

    return build_advisory_section(
        project_dir,
        title="Invalid Frontmatter (CC 発火不能なスキル)",
        compute=compute,
        # skills ディレクトリが無い PJ、または壊れ 0 件なら沈黙（None）。
        applicable=lambda data: data["has_skills_dir"] and bool(data["invalid"]),
        render=render,
    )
