"""多視点評価 observability builder のテスト（#564）。

決定論・LLM 非依存。silence != evaluated の境界と、3部品集約 → 多視点 surface を検証する。
telemetry ストアは monkeypatch で in-memory に差し替える（DATA_DIR / duckdb に依存しない）。
monkeypatch は文字列ターゲットを避け、import した module を直接参照する（既知 pitfall 準拠）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from audit import sections_multiview as smv  # noqa: E402
from audit import usage as usage_mod  # noqa: E402


def _make_skill(project_dir: Path, name: str) -> None:
    """custom スキル（SKILL.md を持つディレクトリ）を作る。"""
    skill_dir = project_dir / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# " + name, encoding="utf-8")


def _patch_inputs(monkeypatch, *, usage, sessions) -> None:
    """builder が読む usage/sessions を in-memory に差し替える。"""
    monkeypatch.setattr(usage_mod, "load_usage_data",
                        lambda days=30, project_root=None: usage)
    # query_sessions は telemetry_query から import される。builder の _gather_inputs 内で
    # `from telemetry_query import query_sessions` するため、module を直接 patch する。
    import telemetry_query
    monkeypatch.setattr(telemetry_query, "query_sessions",
                        lambda project=None: sessions)


def test_silent_when_no_custom_skills(tmp_path: Path) -> None:
    """custom スキルが無い PJ は None（evolve 対象が無い → 沈黙）。"""
    assert smv.build_multiview_eval_section(tmp_path) is None


def test_silent_when_usage_empty(tmp_path: Path, monkeypatch) -> None:
    """custom スキルはあるが usage が空（テレメトリ未蓄積）→ None（沈黙）。"""
    _make_skill(tmp_path, "foo")
    _patch_inputs(monkeypatch, usage=[], sessions=[])
    assert smv.build_multiview_eval_section(tmp_path) is None


def test_clean_when_all_skills_neutral(tmp_path: Path, monkeypatch) -> None:
    """usage はあるが全スキルが中立 → 「評価したが該当なし ✓」を出す（silence != evaluated）。"""
    _make_skill(tmp_path, "foo")
    # foo: アウトカム良好だが chaos 入力なし（builder は chaos を再実行しない）→
    # important でないので reusable も付かず、退行/過学習/コストも非該当 = 中立。
    usage = [{"skill_name": "foo", "session_id": "s1"}]
    sessions = [{"session_id": "s1", "error_count": 0, "tool_sequence": []}]
    _patch_inputs(monkeypatch, usage=usage, sessions=sessions)
    section = smv.build_multiview_eval_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "評価したが該当視点なし" in body
    assert "custom スキル 1 件" in body


def test_surfaces_overfit_and_cost(tmp_path: Path, monkeypatch) -> None:
    """少数セッションで low success + high rework のスキルを多視点で surface。"""
    _make_skill(tmp_path, "bad")
    # bad: 2 sessions, 両方 error あり（first_try=0）, 編集バースト連続 → rework=1.0
    usage = [
        {"skill_name": "bad", "session_id": "s1"},
        {"skill_name": "bad", "session_id": "s2"},
    ]
    edit_seq = ["Edit", "Edit", "Edit"]
    sessions = [
        {"session_id": "s1", "error_count": 2, "tool_sequence": edit_seq},
        {"session_id": "s2", "error_count": 1, "tool_sequence": edit_seq},
    ]
    _patch_inputs(monkeypatch, usage=usage, sessions=sessions)
    section = smv.build_multiview_eval_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "**bad**" in body
    assert "過学習疑い" in body
    assert "コスト増" in body
    # evidence に数字が添う
    assert "一発成功率 0.00" in body
    assert "rework 1.00" in body


def test_registered_in_observability_contract() -> None:
    """observability の単一ソース（_OBSERVABILITY_BUILDERS）に登録されている。"""
    from audit.observability import _OBSERVABILITY_BUILDERS
    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "multiview_eval" in keys


def test_collect_observability_includes_multiview(tmp_path: Path, monkeypatch) -> None:
    """collect_observability 経由でも多視点セクションが出る（両経路の単一ソース確認）。"""
    from audit.observability import collect_observability

    _make_skill(tmp_path, "bad")
    usage = [{"skill_name": "bad", "session_id": "s1"}]
    edit_seq = ["Edit", "Edit", "Edit"]
    sessions = [{"session_id": "s1", "error_count": 2, "tool_sequence": edit_seq}]
    _patch_inputs(monkeypatch, usage=usage, sessions=sessions)
    result = collect_observability(tmp_path)
    assert "multiview_eval" in result
    assert any("Multiview Eval" in line for line in result["multiview_eval"])
