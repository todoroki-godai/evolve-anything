"""取り込みスキルの静的脆弱性スキャン observability セクション（SkillSpector 型・#13）。

sections_testpaths.py と同型の「環境グローバル系 builder」（observability contract 互換
`(project_dir) -> Optional[List[str]]`）。skills/ を持つ PJ でのみ該当し、外部由来スキルに
紛れ込んだ危険パターン（リモート取得→shell / 秘密 exfil / 破壊的コマンド / prompt injection /
全ツール付与）を `skill_vuln_scan` で静的検出して audit に常設する（LLM 非依存・決定論）。

surface 規則（observability contract / sections_summary.classify_section に整合）:
- skill_vuln_scan モジュール未解決 → None（沈黙）
- root/skills/ が無い PJ（非該当）→ None（沈黙）
- findings なし → 「評価したが該当なし ✓」（silence != evaluated）
- findings あり → ⚠ で件数 + severity 別内訳 + 各 finding の evidence 行
"""
from pathlib import Path
from typing import List, Optional

from .advisory import build_advisory_section

# severity の並び順（HIGH を上に）。
_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def build_skill_vuln_section(project_dir: Path) -> Optional[List[str]]:
    """取り込みスキルの危険パターンを audit に surface する。"""

    def compute(proj: Path):
        try:
            import skill_vuln_scan
        except ImportError:
            return None
        return skill_vuln_scan.scan_skills(proj)

    def render(report) -> List[str]:
        if not report.findings:
            return [
                f"✓ 評価したが該当なし（skills/ 配下 {report.scanned_files} ファイルを静的スキャン、"
                "危険パターン検出なし）",
            ]

        # severity 別内訳。
        by_sev: dict[str, int] = {}
        for f in report.findings:
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        breakdown = " / ".join(
            f"{sev} {by_sev[sev]}" for sev in ("HIGH", "MEDIUM", "LOW") if sev in by_sev
        )

        lines = [
            f"⚠ 取り込みスキルに危険パターンを {len(report.findings)} 件検出（{breakdown}）。"
            "外部由来スキルの混入・prompt injection・破壊的コマンドの可能性。取り込み前に確認すること（#13）。",
        ]
        # HIGH を上に並べる（同 severity 内は安定ソート＝rel_path,line 順を維持）。
        ordered = sorted(
            report.findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 9)
        )
        for f in ordered:
            lines.append(
                f"  ・{f.rel_path}:{f.line} [{f.severity}/{f.category}] {f.snippet}"
            )
        return lines

    # skills/ が無い PJ はこのチェック非該当 → 沈黙。
    return build_advisory_section(
        project_dir,
        title="Skill Vulnerability (取り込みスキルの静的脆弱性スキャン)",
        compute=compute,
        applicable=lambda report: report.applicable,
        render=render,
    )
