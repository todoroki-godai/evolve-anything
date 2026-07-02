"""testpaths カバレッジの observability セクション生成（#468）。

sections_orphan.py と同じ「環境グローバル系 builder」。検査対象は evolve-anything 自身の
リポジトリ（pytest.ini + tests/ ツリー）であり、引数 project_dir は self-audit 時に
プラグインルートと一致するため、それをリポジトリルートとして突合する（observability contract
互換 `(project_dir) -> Optional[List[str]]`）。

「canonical コマンドが収集しない tests/」は、フルスイートを謳いつつ一部テストを永久にスキップ
する温床になる（scripts/lib/tests の 1111 件が #468 で発覚）。testpaths 宣言と実 tests/ ツリーの
静的突合を audit に常設し、手動列挙の漏れを毎回可視化する。
"""
from pathlib import Path
from typing import List, Optional

from .advisory import build_advisory_section


def build_testpaths_coverage_section(project_dir: Path) -> Optional[List[str]]:
    """pytest.ini の testpaths が漏らす tests/ ディレクトリを audit に surface する。

    観測可能性:
    - testpaths_coverage モジュール未解決 → None（沈黙）
    - pytest.ini に testpaths 宣言が無い PJ（このチェック非該当）→ None（沈黙）
    - 漏れなし → 「評価したが該当なし ✓」（silence != evaluated）
    - 漏れあり → ⚠ で uncovered な tests/ ディレクトリを併記（evidence, #394）
    """

    def compute(proj: Path):
        try:
            import testpaths_coverage
        except ImportError:
            return None
        return testpaths_coverage.detect_uncovered_test_dirs(proj)

    def render(report) -> List[str]:
        if not report.uncovered:
            return [
                "✓ 評価したが該当なし（リポジトリ内の全 tests/ ディレクトリが "
                "pytest.ini の testpaths 配下にある＝bare pytest で全件収集される）",
            ]
        lines = [
            f"⚠ test_*.py を含むのに testpaths が収集しない tests/ ディレクトリが "
            f"{len(report.uncovered)} 件。フルスイートを謳いつつ永久にスキップされる温床になる。"
            "pytest.ini の testpaths に追記すること（#468）。",
        ]
        for rel in report.uncovered:
            lines.append(f"  ・{rel}（testpaths 未収録）")
        return lines

    # testpaths 宣言が無い環境は評価対象が無い → 沈黙（hook_drift の applicable=False 相当）。
    return build_advisory_section(
        project_dir,
        title="Testpaths Coverage (testpaths が収集しない tests/)",
        compute=compute,
        applicable=lambda report: report.has_testpaths,
        render=render,
    )
