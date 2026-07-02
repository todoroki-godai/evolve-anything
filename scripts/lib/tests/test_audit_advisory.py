"""advisory section 共通枠（scripts/lib/audit/advisory.py）のユニットテスト（#115 Phase 1）。

決定論・LLM 非依存。25 個の observability builder が各自再実装していた同一契約
（import 失敗→None / 評価対象0→None / 定型ヘッダ / header+body+[""] 組み立て）を
集約した 2 層 helper の振る舞いを固定する。

契約の要点:
- advisory_header は "## " を付与し、blurb があれば空行を挟む（header/trailer 規約の単一ソース）。
- finalize は header + body + [""]（末尾に1本だけ空行を足す）。
- build_advisory_section は compute→None or applicable False で section 全体 None、
  さもなくば finalize(advisory_header, render) を返す。
- マーカー（✓/⚠/ℹ）は render 側が入れる（clean/hit 判断は render の責務）。
  sections_summary.classify_section は header ではなく本文のマーカーで分類するため、
  helper は header/trailer だけを規約化し body には触れない。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit.advisory import (  # noqa: E402
    advisory_header,
    build_advisory_section,
    finalize,
)
from audit.sections_summary import classify_section  # noqa: E402


# --- advisory_header ---------------------------------------------------------


def test_header_prepends_double_hash_and_blank() -> None:
    assert advisory_header("Foo Bar") == ["## Foo Bar", ""]


def test_header_title_has_no_leading_hash_from_caller() -> None:
    # 呼び出し側は "## " を付けない（helper が単一ソース）。
    out = advisory_header("Orphan Stores (writer あり / reader なし)")
    assert out[0] == "## Orphan Stores (writer あり / reader なし)"
    assert out[1] == ""


def test_header_with_blurb_inserts_blank_after_blurb() -> None:
    out = advisory_header("Title", blurb=["説明1", "説明2"])
    assert out == ["## Title", "", "説明1", "説明2", ""]


def test_header_empty_blurb_list_behaves_like_no_blurb() -> None:
    assert advisory_header("Title", blurb=[]) == ["## Title", ""]


# --- finalize ----------------------------------------------------------------


def test_finalize_appends_single_trailing_blank() -> None:
    header = ["## Title", ""]
    body = ["✓ clean"]
    assert finalize(header, body) == ["## Title", "", "✓ clean", ""]


def test_finalize_body_should_not_carry_its_own_trailer() -> None:
    # render は末尾空行を含めない前提（finalize が1本だけ足す）。
    header = ["## T", ""]
    body = ["⚠ 1件", "  ・evidence"]
    assert finalize(header, body) == ["## T", "", "⚠ 1件", "  ・evidence", ""]


# --- build_advisory_section --------------------------------------------------


def _compute_ok(_proj: Path):
    return {"hits": 0}


def _render_clean(_data):
    return ["✓ 評価したが該当なし"]


def test_build_returns_none_when_compute_none(tmp_path: Path) -> None:
    out = build_advisory_section(
        tmp_path,
        title="T",
        compute=lambda _p: None,
        applicable=lambda _d: True,
        render=lambda _d: ["✓ x"],
    )
    assert out is None


def test_build_returns_none_when_not_applicable(tmp_path: Path) -> None:
    out = build_advisory_section(
        tmp_path,
        title="T",
        compute=_compute_ok,
        applicable=lambda _d: False,
        render=_render_clean,
    )
    assert out is None


def test_build_does_not_call_render_when_not_applicable(tmp_path: Path) -> None:
    calls: list[str] = []

    def render(_data):
        calls.append("render")
        return ["x"]

    build_advisory_section(
        tmp_path,
        title="T",
        compute=_compute_ok,
        applicable=lambda _d: False,
        render=render,
    )
    assert calls == []


def test_build_assembles_header_body_trailer(tmp_path: Path) -> None:
    out = build_advisory_section(
        tmp_path,
        title="Testpaths Coverage",
        compute=_compute_ok,
        applicable=lambda _d: True,
        render=_render_clean,
    )
    assert out == ["## Testpaths Coverage", "", "✓ 評価したが該当なし", ""]


def test_build_passes_path_object_to_compute() -> None:
    seen: list[object] = []

    def compute(proj):
        seen.append(proj)
        return {"x": 1}

    build_advisory_section(
        "/some/str/path",
        title="T",
        compute=compute,
        applicable=lambda _d: True,
        render=lambda _d: ["✓"],
    )
    assert len(seen) == 1
    assert isinstance(seen[0], Path)


def test_build_with_blurb_and_warn_body(tmp_path: Path) -> None:
    out = build_advisory_section(
        tmp_path,
        title="T",
        compute=_compute_ok,
        applicable=lambda _d: True,
        render=lambda _d: ["⚠ 1件", "  ・evidence"],
        blurb=["この節の説明"],
    )
    assert out == ["## T", "", "この節の説明", "", "⚠ 1件", "  ・evidence", ""]


def test_build_output_is_classify_compatible(tmp_path: Path) -> None:
    # helper が組んだ section は classify_section で正しく分類できる
    # （マーカーは body に含まれ、header/trailer は分類に干渉しない）。
    clean = build_advisory_section(
        tmp_path, title="T", compute=_compute_ok,
        applicable=lambda _d: True, render=lambda _d: ["✓ ok"],
    )
    warn = build_advisory_section(
        tmp_path, title="T", compute=_compute_ok,
        applicable=lambda _d: True, render=lambda _d: ["⚠ bad"],
    )
    watch = build_advisory_section(
        tmp_path, title="T", compute=_compute_ok,
        applicable=lambda _d: True, render=lambda _d: ["ℹ データ不足"],
    )
    assert classify_section(clean) == "clean"
    assert classify_section(warn) == "critical"
    assert classify_section(watch) == "watch"
