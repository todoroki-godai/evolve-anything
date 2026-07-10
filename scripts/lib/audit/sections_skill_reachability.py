"""SKILL.md 宣言↔実装 到達可能性の observability セクション生成（#191, #170再発防止）。

#115 共通枠（`build_advisory_section`）経由。sections_testpaths.py と同じ「環境グローバル系
builder」で、検査対象は evolve-anything 自身のリポジトリ（skills/*/SKILL.md +
scripts/**.py）。self-audit 時は project_dir がプラグインルートと一致するためそれをそのまま
リポジトリルートとして突合する。

「SKILL.md 散文が『実行する』と宣言する callable が、実コードのどこからも呼ばれていない
（到達不能）」状態は #170（`intention_check` ゾンビ宣言）と同型で、放置すると宣言と実装の
乖離に誰も気づけない。決定論静的解析による再発検知を audit に常設する。
"""
from pathlib import Path
from typing import List, Optional

from .advisory import build_advisory_section


def build_skill_reachability_section(project_dir: Path) -> Optional[List[str]]:
    """SKILL.md 宣言↔実装の到達不能な callable を audit に surface する。

    観測可能性:
    - skill_declaration_reachability モジュール未解決 → None（沈黙）
    - skills/*/SKILL.md が無い PJ（このチェック非該当）→ None（沈黙）
    - 到達不能な宣言なし → 「評価したが該当なし ✓」（silence != evaluated）
    - 到達不能な宣言あり → ⚠ + evidence（宣言元 SKILL.md:行番号 / 定義モジュール、#394）
    """

    def compute(proj: Path):
        try:
            import skill_declaration_reachability as sdr
        except ImportError:
            return None
        return sdr.detect_unreachable_declarations(proj)

    def render(report) -> List[str]:
        if not report.unreachable:
            return [
                f"✓ 評価したが該当なし（宣言 {report.evaluated_count} 件を判定・"
                f"ambiguous {report.ambiguous_count} 件・対象外 {report.unresolved_count} 件は除外）",
            ]
        lines = [
            f"⚠ SKILL.md が宣言する callable のうち production コードから到達不能なものが "
            f"{len(report.unreachable)} 件（#170 ゾンビ宣言と同型 — 宣言と実装の乖離）。",
        ]
        for u in report.unreachable:
            lines.append(
                f"  ・`{u.name}()`（宣言: {u.source}:{u.line} / 定義: {', '.join(u.def_files)}）"
            )
        return lines

    return build_advisory_section(
        project_dir,
        title="Skill Declaration Reachability (SKILL.md 宣言↔実装 到達可能性、#191)",
        compute=compute,
        applicable=lambda report: report.has_skills,
        render=render,
    )
