"""Doc Budget（hot ドキュメントの byte/セクション/ポインタ予算）の observability セクション（#319）。

`doc_budget.py` の3検査（ファイル単位 byte 予算 / セクション単位 byte 予算 / ポインタ実在突合）を
audit advisory として常設する。SPEC.md が 41KB まで肥大してから初めて気づいた（#318）の
再発防止 — `install ≠ enforcement` 対策として spec-keeper 起動に依存しない検査面を追加する。
"""
from pathlib import Path
from typing import List, Optional

from .advisory import build_advisory_section


def build_doc_budget_section(project_dir: Path) -> Optional[List[str]]:
    """hot ドキュメントの byte/セクション/ポインタ予算超過を audit に surface する。

    観測可能性:
    - モジュール未解決 / SPEC.md・CLAUDE.md いずれも無い PJ → None（沈黙）
    - 該当なし → 「評価したが該当なし ✓」（silence != evaluated）
    - 該当あり → MUST 超過は ⚠、healthy 超過・ポインタ不整合は ℹ（evidence 付き、#394）
    """

    def compute(proj: Path):
        try:
            import doc_budget
        except ImportError:
            return None
        return doc_budget.check_doc_budget(proj)

    def render(report) -> List[str]:
        if not report.has_findings():
            return ["✓ 評価したが該当なし（hot ドキュメントの byte/セクション/ポインタ予算は健全）"]

        lines: List[str] = []

        must = [f for f in report.file_findings if f.severity == "must"]
        healthy = [f for f in report.file_findings if f.severity == "healthy"]
        if must:
            lines.append(f"⚠ MUST 閾値超過ファイルが {len(must)} 件（cold へのセクション移動が必要）:")
            for f in must:
                lines.append(
                    f"  ・{f.path}: {f.byte_size:,} bytes（MUST {f.must_bytes:,} bytes 超）"
                )
        if healthy:
            lines.append(f"ℹ healthy 閾値超過ファイルが {len(healthy)} 件（MUST 未満・監視推奨）:")
            for f in healthy:
                lines.append(
                    f"  ・{f.path}: {f.byte_size:,} bytes（healthy {f.healthy_bytes:,} bytes 超）"
                )

        if report.section_findings:
            lines.append(
                f"ℹ 肥大セクションが {len(report.section_findings)} 件"
                "（セクション単体が全体の40%超かつ4KB超、または8KB超）:"
            )
            for s in report.section_findings:
                lines.append(
                    f"  ・{s.file} #{s.heading}: {s.byte_size:,} bytes（{s.pct:.1f}%）"
                )

        if report.pointer_findings:
            lines.append(
                f"⚠ SPEC.md/CLAUDE.md 内のリンク切れが {len(report.pointer_findings)} 件"
                "（移動時のリンク切れ・hook_drift の dead_ref と同型）:"
            )
            for p in report.pointer_findings:
                kind_label = "リンク先ファイル不在" if p.kind == "missing_file" else "アンカー不在"
                lines.append(f"  ・{p.source_file}: [{p.link_text}]({p.raw_target}) — {kind_label}")

        return lines

    return build_advisory_section(
        project_dir,
        title="Doc Budget (hot ドキュメントの byte/セクション/ポインタ予算)",
        compute=compute,
        applicable=lambda report: report is not None and report.applicable,
        render=render,
    )
