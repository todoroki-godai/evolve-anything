"""#379 Step 2 — generate_report が display_cull を畳まず常時展開表示することの契約テスト。

fold_clean_observability は ℹ マーカーを watch 扱いにしてセクション名だけ残す（本文は畳む）。
display_cull はその折り畳みを経由させず、通知文そのものを必ず本文に出す必要がある
（本文が埋もれると「淘汰した事実」が実質見えなくなり silence != evaluated が破れるため）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import report as report_mod  # noqa: E402

_NOTICE = "ℹ 表示淘汰中: 33 section（#379 Step 2・コード非削除・EVOLVE_SHOW_CULLED=1 で一時表示）"


def _fake_collect_with_cull(_project_dir):
    return {
        "display_cull": [_NOTICE],
        "kept_key": ["## Kept", "✓ 該当なし", ""],
    }


def _fake_collect_without_cull(_project_dir):
    return {"kept_key": ["## Kept", "✓ 該当なし", ""]}


def test_display_cull_notice_appears_verbatim_in_markdown(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "audit.observability.collect_observability", _fake_collect_with_cull
    )

    md = report_mod.generate_report(
        artifacts={},
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        project_dir=tmp_path,
    )

    assert _NOTICE in md


def test_display_cull_key_not_listed_in_clean_fold_line(monkeypatch, tmp_path: Path) -> None:
    """display_cull は fold_clean_observability を経由しないので、畳みセクション名の
    列挙行（## ✓ 評価済みクリーン / ## ℹ 観察中）には現れない。"""
    monkeypatch.setattr(
        "audit.observability.collect_observability", _fake_collect_with_cull
    )

    md = report_mod.generate_report(
        artifacts={},
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        project_dir=tmp_path,
    )

    # display_cull は report.py 側で pop 済みのため、畳み名列挙にも本文にも
    # キー名 "display_cull" そのものは一切現れない（通知文のみが出る）。
    assert "display_cull" not in md


def test_no_notice_line_when_collect_returns_no_cull_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "audit.observability.collect_observability", _fake_collect_without_cull
    )

    md = report_mod.generate_report(
        artifacts={},
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        project_dir=tmp_path,
    )

    assert "表示淘汰中" not in md
