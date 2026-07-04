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
        static_findings = report.findings
        # flow_findings は #123 で追加。古い report との後方互換のため getattr。
        flow_findings = getattr(report, "flow_findings", [])
        static_n = len(static_findings)
        flow_n = len(flow_findings)

        if static_n == 0 and flow_n == 0:
            return [
                f"✓ 評価したが該当なし（skills/ 配下 {report.scanned_files} ファイルを"
                "静的スキャン + 系列フロー解析、危険パターン検出なし）",
            ]

        lines = [
            f"⚠ 取り込みスキルに危険パターンを検出（静的 {static_n} 件 / 系列 {flow_n} 件）。"
            "外部由来スキルの混入・prompt injection・破壊的コマンド・多段注入の可能性。"
            "取り込み前に確認すること（#13 #123）。",
        ]

        # 静的行スキャンの検出（既存表示互換: rel_path:line [severity/category] snippet）。
        if static_n:
            by_sev: dict[str, int] = {}
            for f in static_findings:
                by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
            breakdown = " / ".join(
                f"{sev} {by_sev[sev]}" for sev in ("HIGH", "MEDIUM", "LOW") if sev in by_sev
            )
            lines.append(f"  [静的 {static_n} 件（{breakdown}）]")
            # HIGH を上に並べる（同 severity 内は安定ソート＝rel_path,line 順を維持）。
            ordered = sorted(
                static_findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 9)
            )
            for f in ordered:
                lines.append(
                    f"  ・{f.rel_path}:{f.line} [{f.severity}/{f.category}] {f.snippet}"
                )

        # 系列（マルチステップ）フロー検出（各行単体では benign・組み合わせで悪性）。
        if flow_n:
            lines.append(
                f"  [系列 {flow_n} 件（各行単体では benign・組み合わせで悪性 = 多段注入）]"
            )
            ordered_flow = sorted(
                flow_findings,
                key=lambda ff: (
                    _SEVERITY_ORDER.get(ff.severity, 9),
                    ff.rel_path,
                    ff.producer_line,
                ),
            )
            for ff in ordered_flow:
                lines.append(
                    f"  ・{ff.rel_path}:{ff.producer_line}→{ff.consumer_line} "
                    f"[{ff.severity}/{ff.category}] "
                    f"{ff.producer_snippet} ⇒ {ff.consumer_snippet}"
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
