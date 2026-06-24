"""fleet recall の E2E ベンチ（transcript-store-bench ルール準拠）。

実測コーパス規模（14 PJ / 168 markdown / 760K, 2026-05-28 計測）に合わせた合成
コーパスで 1 回完走させ、wall time と件数を assertion する。LLM 非依存なので
決定論的に再現可能。実 `~/.claude/projects` がある環境では追加で smoke も走らせる。
"""

import sys
import time
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import pytest  # noqa: E402

from fleet.recall import recall  # noqa: E402

# 実測規模（余裕を見て上振れ側）
_N_PJ = 14
_FILES_PER_PJ = 12  # 14 * 12 = 168 ≒ 実測 168
_WALL_BUDGET_SEC = 5.0  # 実測 ~0.1s。回帰検知用に大きめの上限


def _build_corpus(root: Path) -> int:
    total = 0
    for pj in range(_N_PJ):
        mem = root / f"-Users-x-pj-{pj:02d}" / "memory"
        mem.mkdir(parents=True)
        for f in range(_FILES_PER_PJ):
            body = f"fact {f} in pj {pj}. duckdb checkpoint write amplification. " * 8
            (mem / f"note-{f:02d}.md").write_text(
                f"---\nname: note-{pj}-{f}\ndescription: pj {pj} note {f} caching\n---\n{body}\n",
                encoding="utf-8",
            )
            total += 1
        # 各 PJ に index も
        (mem / "MEMORY.md").write_text(f"# pj {pj} index\n- duckdb summary\n", encoding="utf-8")
        total += 1
    return total


def test_実規模合成コーパスで完走(tmp_path):
    root = tmp_path / "projects"
    total_files = _build_corpus(root)
    assert total_files >= 168  # 実測規模を下回らない

    start = time.monotonic()
    hits = recall("duckdb checkpoint", limit=20, projects_root=root)
    elapsed = time.monotonic() - start

    # wall time: 回帰検知（O(N) 全 scan が極端に遅くないこと）
    assert elapsed < _WALL_BUDGET_SEC, f"recall が遅い: {elapsed:.2f}s > {_WALL_BUDGET_SEC}s"
    # 件数: limit でちょうど切り詰められる（全 PJ にマッチ語があるため >limit ヒット）
    assert len(hits) == 20
    # 決定論: 2 回目も同順
    again = recall("duckdb checkpoint", limit=20, projects_root=root)
    assert [h.file_path for h in hits] == [h.file_path for h in again]
    # index ペナルティ: 本文ファイルが index より上位（先頭は本文）
    assert hits[0].is_index is False


def test_横断性_複数PJがヒットしうる(tmp_path):
    root = tmp_path / "projects"
    _build_corpus(root)
    # limit を全件超にして横断性を見る（同点 tie-break は pj_display 順で偏るため、
    # 限定 limit だと先頭 PJ に偏るのは正しい挙動。横断網羅は十分大きな limit で確認）
    hits = recall("caching", limit=500, projects_root=root)
    pjs = {h.pj_display for h in hits}
    # description に "caching" を全 PJ に入れたので複数 PJ 横断でヒット
    assert len(pjs) == _N_PJ


def test_validity_aware_ranking_fresh上位_staleも残る(tmp_path):
    """query が fresh / stale 両方に一致 → fresh が上位・stale も結果に残る（#74）。

    RaMem(iii): validity で降格はするがハード除外しない（フォールバック保持）。
    """
    root = tmp_path / "projects"
    mem = root / "-Users-x-pj-validity" / "memory"
    mem.mkdir(parents=True)
    # 同一 TF（widget が同回数）で fresh は decay 無し、stale は decay 超過
    (mem / "fresh.md").write_text(
        "---\nname: fresh-widget\ndescription: d\n---\nwidget widget widget config.\n",
        encoding="utf-8",
    )
    (mem / "stale.md").write_text(
        "---\nname: stale-widget\ndescription: d\n"
        "valid_from: '2020-01-01T00:00:00+00:00'\ndecay_days: 1\n---\n"
        "widget widget widget config.\n",
        encoding="utf-8",
    )
    hits = recall("widget", projects_root=root)
    names = [h.file_path.name for h in hits]
    # stale もハード除外されず結果に残る（フォールバック保持）
    assert "fresh.md" in names
    assert "stale.md" in names
    # fresh が stale より上位
    assert names.index("fresh.md") < names.index("stale.md")


@pytest.mark.skipif(
    not list(Path.home().glob(".claude/projects/*/memory/*.md")),
    reason="実 ~/.claude/projects の memory コーパスが無い環境",
)
def test_実コーパスsmoke():
    """実 home の memory を 1 回横断完走させる（壊れず・時間内）。"""
    start = time.monotonic()
    hits = recall("test", limit=5)
    elapsed = time.monotonic() - start
    assert elapsed < _WALL_BUDGET_SEC
    # 結果は 0 件でもよい（query 次第）。例外なく完走することが本質
    assert isinstance(hits, list)
