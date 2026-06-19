"""run_discover の繋ぎ目バグ根治テスト（#521 / #526-3）。

#521: 内部検出関数が None / 異常を返したとき、try/except 外で dict subscript
していた箇所（missed_result["missed"] / p["scope"] / enrich_result["matched_skills"]）が
`'NoneType' object is not subscriptable` で run_discover 全体を落としていた。
本テストは各ブロックを mock で None / 異常にしても run_discover が落ちず、
握り潰さずに `*_error` を残して観測可能にすることを検証する。

#526-3: discover が失敗したブロックでも `reflect_data_count` は欠落させず、
下流（SKILL.md Step 6 / Step 10.1 の `reflect_data_count >= 5`）が None で未定義に
ならないよう degraded sentinel `-1`（int を維持）にフォールバックすることを検証する。
sentinel を int に保つのは、CANONICAL 契約が同キーを `kind=int` と宣言しており、
str sentinel だと runtime self-detect（evolve_consistency）が `wrong_kind` drift を
誤検出し幻の「契約乖離 issue」を自作するため（/review #530 で発見）。

TDD-first: 根治実装の前にこのテストを書いている。
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from test_home_isolation import isolate_home  # noqa: E402

import discover  # noqa: E402


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    """run_discover が実 home（~/.claude/projects ≈9925 jsonl）を走査しないよう隔離。"""
    isolate_home(monkeypatch, tmp_path)


# ── #521: 各 subscript ブロックの None 耐性 ───────────────────


def test_missed_skills_none_does_not_crash(tmp_path):
    """detect_missed_skills が None を返しても run_discover は落ちず error を残す。"""
    with mock.patch.object(discover, "detect_missed_skills", return_value=None):
        result = discover.run_discover(project_root=tmp_path)
    # 落ちずに dict を返す
    assert isinstance(result, dict)
    # 握り潰さず観測可能にする
    assert "missed_skill_opportunities_error" in result
    # 下流が依存する reflect_data_count は欠落しない
    assert "reflect_data_count" in result


def test_missed_skills_missing_keys_does_not_crash(tmp_path):
    """detect_missed_skills が想定キー（missed/message）を欠く dict でも落ちない。"""
    with mock.patch.object(discover, "detect_missed_skills", return_value={}):
        result = discover.run_discover(project_root=tmp_path)
    assert isinstance(result, dict)
    # missed/message が無い場合も例外なく成立する（error なしで素通り or 安全に空）
    assert "reflect_data_count" in result


def test_enrich_patterns_none_does_not_crash(tmp_path):
    """_enrich_patterns が None を返しても run_discover は落ちず error を残す。"""
    with mock.patch.object(
        discover, "detect_behavior_patterns",
        return_value=[{"pattern": "x", "type": "t"}],
    ), mock.patch.object(discover, "_enrich_patterns", return_value=None):
        result = discover.run_discover(project_root=tmp_path)
    assert isinstance(result, dict)
    assert "matched_skills_error" in result


def test_determine_scope_failure_does_not_crash(tmp_path):
    """determine_scope が例外でも scope ブロックは握り潰さず error を残す。"""
    with mock.patch.object(
        discover, "detect_behavior_patterns",
        return_value=[{"pattern": "x", "type": "t"}],
    ), mock.patch.object(
        discover, "determine_scope", side_effect=RuntimeError("scope boom"),
    ):
        result = discover.run_discover(project_root=tmp_path)
    assert isinstance(result, dict)
    assert "scope_error" in result
    assert "scope boom" in result["scope_error"]


# ── #30: errors.py の None 値 subscript（#521 regression）─────


def test_detect_error_patterns_tolerates_none_error_value():
    """errors.jsonl の レコードが {"error": None} でも detect_error_patterns は落ちない（#30）。

    `rec.get("error", "")[:200]` は "error" キーが存在し値が明示的に None のとき
    `None[:200]` で `'NoneType' object is not subscriptable` になる。`.get(..., "")`
    のデフォルトは「キー欠落」しか守らず「値が None」を守れない。None 合体で守る。
    """
    import discover.errors as errors_mod
    import telemetry_query

    recs = [
        {"error": None},          # 値が明示的に None（#30 の再現データ）
        {"error": "boom"},
        {"error": "boom"},
        {"error": "boom"},        # 閾値 3 を満たす実エラー
        {},                       # キー欠落（従来 .get default で守れていたケース）
    ]
    # query_errors は detect_error_patterns 内で telemetry_query から local import
    # されるため、ソースモジュール側を mock する（call graph の実呼び先）。
    with mock.patch.object(telemetry_query, "query_errors", return_value=recs):
        patterns = errors_mod.detect_error_patterns(project_root=None)
    # 落ちずに集計でき、None/欠落は空文字扱いで除外、実エラーのみ拾う
    assert isinstance(patterns, list)
    assert any(p["pattern"] == "boom" and p["count"] == 3 for p in patterns)


def test_run_discover_does_not_crash_when_errors_contain_none(tmp_path):
    """errors レコードに None 値があっても run_discover 全体は落ちない（#30）。

    detect_error_patterns は run_discover の try/except 外で呼ばれているため、
    そこが落ちると run_discover 全体が落ち、Phase 2 except に吸われて
    reflect_data_count が欠落する（→ #32 の二次クラッシュ連鎖）。
    """
    import telemetry_query

    with mock.patch.object(
        telemetry_query, "query_errors", return_value=[{"error": None}],
    ):
        result = discover.run_discover(project_root=tmp_path)
    assert isinstance(result, dict)
    # 落ちずに完走するので degraded sentinel ではなく実 contract キーが揃う
    assert "reflect_data_count" in result


# ── #526-3: reflect_data_count の degraded フォールバック ──────


def test_reflect_data_count_present_on_success(tmp_path):
    """正常系では reflect_data_count は int（件数）。"""
    result = discover.run_discover(project_root=tmp_path)
    assert isinstance(result["reflect_data_count"], int)


def test_reflect_data_count_degraded_sentinel_when_reflect_load_fails(tmp_path):
    """load_claude_reflect_data が失敗しても reflect_data_count は欠落せず degraded sentinel -1。"""
    with mock.patch.object(
        discover, "load_claude_reflect_data",
        side_effect=RuntimeError("reflect boom"),
    ):
        result = discover.run_discover(project_root=tmp_path)
    assert isinstance(result, dict)
    # 下流の `>= 5` 比較が None TypeError にならないよう明示値にする。
    # int sentinel に保つことで CANONICAL の kind=int 契約を端から端まで維持する。
    assert result.get("reflect_data_count") == -1
    assert isinstance(result["reflect_data_count"], int)
    assert "reflect_data_count_error" in result


def test_degraded_reflect_count_does_not_trip_conformance_wrong_kind(tmp_path):
    """degraded 経路でも CANONICAL 契約に wrong_kind drift を出さない（/review #530 回帰ガード）。

    str sentinel "unknown" だと evolve_consistency の runtime self-detect が
    `phases.discover.reflect_data_count` を wrong_kind と誤検出し、幻の「契約乖離 issue」を
    自作していた。int sentinel -1 で型契約を保ち、この自己誘発 FP を構造的に封じる。
    """
    from evolve_result_schema import check_conformance_structured  # noqa: PLC0415

    with mock.patch.object(
        discover, "load_claude_reflect_data",
        side_effect=RuntimeError("reflect boom"),
    ):
        disc = discover.run_discover(project_root=tmp_path)
    # evolve が discover 結果を格納する形（phases.discover）に包んで契約検査する
    violations = check_conformance_structured({"phases": {"discover": disc}})
    offending = [
        v for v in violations
        if v.reason == "wrong_kind" and "reflect_data_count" in v.path
    ]
    assert offending == [], f"degraded sentinel が wrong_kind を誘発: {offending}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
