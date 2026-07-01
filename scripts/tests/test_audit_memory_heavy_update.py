#!/usr/bin/env python3
"""audit の memory_heavy_update 検出ロジックのテスト。

Issue #97 / arXiv:2605.12978: LLM 自己更新メモリの劣化警告。
#104 再設計: heavy_update を「churn（高 update_count）+ 肥大化（大行数）」の劣化シグナルに純化する。
`update_count >= 10` **かつ** 行数 >= 80 の 2 条件で発火（健全性は問わない）。

- update_count は memory_capability の use_read 軸で「活性（良）」として加点される指標（正の信号）。
  低い閾値（旧 3）で発火させると同一 run で「活性（良）」と「要対応（悪）」に二重分類する矛盾を生む。
  閾値を引き上げ肥大化との複合に純化することで、通常の活発更新を誤検知しない。
- amamo の簡潔メモリ（update 55 / 40 行）が誤検知された FP は、行数閾値 80 が救う（40 < 80）。
- 旧 #104 の健全性ゲート（maintain 軸 = freshness に委ねる）は、temporal メタデータの無い通常メモリで
  detector がほぼ発火せず near-inert になったため撤去した（健全/非健全を問わず肥大化で発火する）。
詳細: docs/research/faulty-updated-memories.md
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit.issues import (  # noqa: E402
    collect_issues,
    MEMORY_HEAVY_UPDATE_THRESHOLD,
    MEMORY_HEAVY_UPDATE_LINE_THRESHOLD,
)


def _setup_minimal_project(tmp_path: Path) -> Path:
    """audit 実行に必要な最低限のディレクトリ構造を作る。

    find_artifacts は .claude/memory/ を memory のソースとして見る。
    """
    (tmp_path / ".claude" / "memory").mkdir(parents=True)
    (tmp_path / ".claude" / "rules").mkdir(parents=True)
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("# Test\n", encoding="utf-8")
    return tmp_path


def _make_memory(
    tmp_path: Path,
    name: str,
    update_count: int | None,
    extra_lines: int = 0,
) -> Path:
    """update_count=None は frontmatter なしファイル。extra_lines で本文行数を増やす。

    line_count = content.count("\\n") + 1（全行数）。本テンプレでは extra_lines>=1 のとき
    line_count = 6 + extra_lines（frontmatter 4 + `# Body` 1 + 本文 extra_lines）。
    健全性は heavy_update の判定に無関係になったため superseded は付与しない。
    """
    f = tmp_path / ".claude" / "memory" / name
    if update_count is None:
        f.write_text("# No frontmatter\nContent.\n", encoding="utf-8")
    else:
        body_lines = "\n".join([f"- item {i}" for i in range(extra_lines)])
        f.write_text(
            f"---\nname: {name}\nupdate_count: {update_count}\n---\n# Body\n{body_lines}\n",
            encoding="utf-8",
        )
    return f


def _heavy(issues):
    return [i for i in issues if i["type"] == "memory_heavy_update"]


def test_update_threshold_default_is_ten():
    """update 閾値定数は 10（#104 で 3→10 に引き上げ）。"""
    assert MEMORY_HEAVY_UPDATE_THRESHOLD == 10


def test_line_threshold_default_is_eighty():
    """行数閾値定数は 80（#104 で 30→80 に引き上げ・amamo 40 行を除外）。"""
    assert MEMORY_HEAVY_UPDATE_LINE_THRESHOLD == 80


def test_bloated_churned_memory_detected(tmp_path):
    """update_count == 10 かつ 行数 >= 80 → issue が出る（健全でも発火）。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "heavy.md", update_count=10,
                 extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD)  # line_count = 86

    heavy = _heavy(collect_issues(tmp_path))
    assert len(heavy) == 1
    assert heavy[0]["detail"]["update_count"] == 10
    assert heavy[0]["detail"]["threshold"] == 10
    assert heavy[0]["detail"]["line_threshold"] == 80
    assert heavy[0]["source"] == "build_memory_health_section"
    assert heavy[0]["file"].endswith("heavy.md")


def test_healthy_bloated_memory_still_detected(tmp_path):
    """#104 再設計の核: 健全（superseded なし）でも肥大化 + churn なら発火する。

    旧 #104 の健全性ゲートは metadata 無し通常メモリで detector を near-inert にした。
    撤去後は健全メモリでも肥大化していれば警告する（元 detector の劣化検出目的を回復）。
    """
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "healthy_bloated.md", update_count=55,
                 extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD)  # line_count = 86, 健全

    heavy = _heavy(collect_issues(tmp_path))
    assert len(heavy) == 1, (
        f"健全でも肥大化 + churn なら発火すべき（near-inert 回帰封じ）: {heavy}"
    )
    assert heavy[0]["detail"]["update_count"] == 55


def test_amamo_concise_active_not_detected(tmp_path):
    """#104 FP 回帰封じ: update 55 / ~46 行（簡潔だが活発）は発火しない（行数 < 80）。"""
    _setup_minimal_project(tmp_path)
    # extra_lines=40 → line_count = 46（< 80）。amamo の実 FP を再現。
    _make_memory(tmp_path, "amamo_concise.md", update_count=55, extra_lines=40)

    heavy = _heavy(collect_issues(tmp_path))
    assert heavy == [], (
        f"簡潔だが活発なメモリ（amamo: update 55 / ~46 行）が誤検知された（#104 FP 再発）: {heavy}"
    )


def test_below_update_threshold_not_detected(tmp_path):
    """update_count < 10 → 行数が大きくても発火しない。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "few_updates.md", update_count=9,
                 extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD)  # line_count = 86 だが update 9

    assert _heavy(collect_issues(tmp_path)) == []


def test_below_line_threshold_not_detected(tmp_path):
    """行数 < 80 → update_count が大きくても発火しない（複合条件・#353）。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "concise.md", update_count=15, extra_lines=40)  # line_count = 46

    assert _heavy(collect_issues(tmp_path)) == []


def test_boundary_exact_thresholds_detected(tmp_path):
    """境界: update_count == 10 かつ line_count == 80 ちょうどで発火する（>= 判定）。"""
    _setup_minimal_project(tmp_path)
    # extra_lines=74 → line_count = 6 + 74 = 80 ちょうど。
    _make_memory(tmp_path, "boundary.md", update_count=10, extra_lines=74)

    heavy = _heavy(collect_issues(tmp_path))
    assert len(heavy) == 1
    assert heavy[0]["detail"]["line_count"] == 80


def test_zero_update_count_not_detected(tmp_path):
    """frontmatter なし (update_count=0) → 検出しない（既存ファイルは影響なし）。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "untracked.md", update_count=None)

    assert _heavy(collect_issues(tmp_path)) == []


def test_high_update_count_but_small_content_not_detected(tmp_path):
    """#353: 高 update_count でも行数が閾値未満なら発火しない。

    コスト最適化メモリ（15回更新だが内容が少ない）を誤検知しない。更新だけ多いものは
    正常な活発運用とみなす（heavy_update は肥大化との複合でのみ警告する）。
    """
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "active_small.md", update_count=15, extra_lines=0)

    assert _heavy(collect_issues(tmp_path)) == []


def test_high_update_count_and_large_content_detected(tmp_path):
    """#353/#104: 高 update_count かつ 行数大 → memory_heavy_update フラグが立つ。"""
    _setup_minimal_project(tmp_path)
    _make_memory(tmp_path, "active_large.md", update_count=15,
                 extra_lines=MEMORY_HEAVY_UPDATE_LINE_THRESHOLD)  # line_count = 86

    heavy = _heavy(collect_issues(tmp_path))
    assert len(heavy) == 1
    assert heavy[0]["detail"]["update_count"] == 15
