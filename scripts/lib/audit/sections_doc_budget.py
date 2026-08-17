"""Doc Budget（hot ドキュメントの byte/セクション/ポインタ予算）の observability セクション（#319）。

`doc_budget.py` の3検査（ファイル単位 byte 予算 / セクション単位 byte 予算 / ポインタ実在突合）を
audit advisory として常設する。SPEC.md が 41KB まで肥大してから初めて気づいた（#318）の
再発防止 — `install ≠ enforcement` 対策として spec-keeper 起動に依存しない検査面を追加する。

CLAUDE.md 契約不変条件の欠落（#415 `claude_md_contract.py`）も **同じ既存セクションに相乗り**
させて surface する。#379 新設凍結（新しい observability section の追加禁止）に抵触しないよう、
新規セクションは作らず `doc_budget` セクションの本文を拡張するだけに留める。
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from .advisory import build_advisory_section


@dataclass
class _DocBudgetBundle:
    """既存の byte 予算 report と CLAUDE.md 契約検査結果をまとめて render に渡す入れ物。

    新しい observability section を作らないための合成（#379 新設凍結）。
    """

    report: Any
    contract_missing: List[dict]
    must_stay_missing: List[dict]

    def has_findings(self) -> bool:
        return bool(self.report.has_findings() or self.contract_missing or self.must_stay_missing)


def build_doc_budget_section(project_dir: Path) -> Optional[List[str]]:
    """hot ドキュメントの byte/セクション/ポインタ予算超過 + CLAUDE.md 契約欠落を audit に surface する。

    観測可能性:
    - モジュール未解決 / SPEC.md・CLAUDE.md いずれも無い PJ → None（沈黙）
    - 該当なし → 「評価したが該当なし ✓」（silence != evaluated）
    - 該当あり → MUST 超過・契約欠落は ⚠、healthy 超過・ポインタ不整合は ℹ（evidence 付き、#394）
    """

    def compute(proj: Path):
        try:
            import doc_budget
        except ImportError:
            return None
        report = doc_budget.check_doc_budget(proj)
        try:
            import claude_md_contract

            contract_missing = claude_md_contract.check_claude_md_contracts(proj)
            must_stay_missing = claude_md_contract.check_must_stay_sections(proj)
        except ImportError:
            contract_missing, must_stay_missing = [], []
        return _DocBudgetBundle(report, contract_missing, must_stay_missing)

    def render(bundle: _DocBudgetBundle) -> List[str]:
        if not bundle.has_findings():
            return ["✓ 評価したが該当なし（hot ドキュメントの byte/セクション/ポインタ予算・CLAUDE.md 契約は健全）"]

        report = bundle.report
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

        if bundle.contract_missing:
            lines.append(
                f"⚠ CLAUDE.md 契約不変条件の欠落が {len(bundle.contract_missing)} 件"
                "（圧縮/編集で契約が hot から消えた疑い・#415）:"
            )
            for f in bundle.contract_missing:
                lines.append(f"  ・{f['invariant']}: 欠落語 {f['missing']}")

        if bundle.must_stay_missing:
            lines.append(
                f"⚠ CLAUDE.md 移設禁止セクションの欠落が {len(bundle.must_stay_missing)} 件（#415）:"
            )
            for f in bundle.must_stay_missing:
                lines.append(f"  ・{f['section']}（{f['reason']}）")

        return lines

    return build_advisory_section(
        project_dir,
        title="Doc Budget (hot ドキュメントの byte/セクション/ポインタ予算)",
        compute=compute,
        applicable=lambda bundle: bundle is not None and bundle.report.applicable,
        render=render,
    )
