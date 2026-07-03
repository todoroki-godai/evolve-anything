"""audit 完了後に注入された observability 行を Markdown レポートへ反映する
reconcile helper のテスト（#141-7a）。

背景: evolve フルランでは audit フェーズが Markdown + TL;DR を確定した後に
remediation フェーズが `result["observability"]` へ remediation_batch_skip を注入する。
レンダリング済み Markdown には反映されず TL;DR 件数（要対応 6）が top-level
observability の再集計（要対応 7）と食い違い、注入行が Markdown 本文に grep 0 件になる。
reconcile_injected_observability が (1) 注入行の分類に応じ TL;DR を +1、(2) 本文へ追記して
両契約（surface される + TL;DR/Markdown 一致）を満たす。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit.sections_summary import reconcile_injected_observability  # noqa: E402


def _report(critical: int = 6, watch: int = 7, clean: int = 15) -> str:
    """audit レポートの最小骨格（TL;DR + 本文 + 推奨アクション）。"""
    return "\n".join(
        [
            "# Environment Audit",
            "",
            "## TL;DR",
            f"要対応 {critical} 件 / 観察中 {watch} 件 / 評価済みクリーン {clean} 件",
            "",
            "## Unmanaged Pitfalls",
            "✓ 評価したが該当なし",
            "",
            "## 推奨アクション",
            "",
            "✅ 問題なし: 要対応・要観察の項目はありません。",
            "",
        ]
    )


def test_critical_line_bumps_critical_count():
    """⚠ の注入行は TL;DR の要対応を +1 する。"""
    md = _report(critical=6, watch=7, clean=15)
    lines = ["⚠ remediation batch_skip: 低 confidence の proposable を 9 件まとめスキップ"]
    out = reconcile_injected_observability(md, "Remediation Batch Skip", lines)
    assert "要対応 7 件 / 観察中 7 件 / 評価済みクリーン 15 件" in out
    # 元の 6 件 表記は消える
    assert "要対応 6 件" not in out


def test_injected_line_appears_in_body():
    """注入行が Markdown 本文に追記される（grep 可視化）。"""
    md = _report()
    lines = ["⚠ remediation batch_skip: 低 confidence の proposable を 9 件まとめスキップ"]
    out = reconcile_injected_observability(md, "Remediation Batch Skip", lines)
    assert "remediation batch_skip" in out
    assert "## Remediation Batch Skip" in out


def test_injected_section_before_recommended_actions():
    """追記は推奨アクションカードの直前に差し込む（他 observability と近接）。"""
    md = _report()
    lines = ["⚠ remediation batch_skip: 9 件まとめスキップ"]
    out = reconcile_injected_observability(md, "Remediation Batch Skip", lines)
    assert out.index("## Remediation Batch Skip") < out.index("## 推奨アクション")


def test_clean_line_bumps_clean_count():
    """✓ の注入行（0 件）は評価済みクリーンを +1 する。"""
    md = _report(critical=6, watch=7, clean=15)
    lines = ["✓ remediation batch_skip: 0 件（まとめスキップ対象なし）"]
    out = reconcile_injected_observability(md, "Remediation Batch Skip", lines)
    assert "要対応 6 件 / 観察中 7 件 / 評価済みクリーン 16 件" in out


def test_watch_line_bumps_watch_count():
    """ℹ の注入行は観察中を +1 する。"""
    md = _report(critical=6, watch=7, clean=15)
    lines = ["ℹ 参考情報のみ"]
    out = reconcile_injected_observability(md, "Some Section", lines)
    assert "要対応 6 件 / 観察中 8 件 / 評価済みクリーン 15 件" in out


def test_appends_at_end_when_no_recommended_actions():
    """推奨アクション見出しが無ければ末尾へ追記する（TL;DR 置換は保持）。"""
    md = "\n".join(
        [
            "# Environment Audit",
            "",
            "## TL;DR",
            "要対応 6 件 / 観察中 7 件 / 評価済みクリーン 15 件",
            "",
            "## Unmanaged Pitfalls",
            "✓ 評価したが該当なし",
            "",
        ]
    )
    lines = ["⚠ remediation batch_skip: 9 件まとめスキップ"]
    out = reconcile_injected_observability(md, "Remediation Batch Skip", lines)
    assert "要対応 7 件" in out
    assert "## Remediation Batch Skip" in out


def test_only_first_tldr_line_is_bumped():
    """TL;DR 行は先頭 1 箇所だけ置換する（本文に同形の数字があっても壊さない）。"""
    md = _report(critical=6, watch=7, clean=15)
    # 本文に紛らわしい別行を追加
    md = md.replace(
        "✓ 評価したが該当なし",
        "✓ 評価したが該当なし\n要対応 99 件 / 観察中 99 件 / 評価済みクリーン 99 件",
    )
    lines = ["⚠ x"]
    out = reconcile_injected_observability(md, "S", lines)
    # 先頭 TL;DR は 6→7、本文の 99 行は不変
    assert "要対応 7 件 / 観察中 7 件 / 評価済みクリーン 15 件" in out
    assert "要対応 99 件 / 観察中 99 件 / 評価済みクリーン 99 件" in out
