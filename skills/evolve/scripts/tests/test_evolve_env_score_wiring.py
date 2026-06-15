"""#523-2 / #526-2: evolve result に構造化 env_score を surface する回帰テスト。

Phase 3: Audit は run_audit の戻り（markdown レポート文字列）だけを保持し、
構造化 env_score を捨てていた。SKILL.md / references/report-narration.md の
「Report クライマックス（成長レベル）」は出力 JSON のトップレベル `result["env_score"]`
を読んで compute_level する設計なのに、その field が存在せず成長レベル演出が一度も
発火しなかった（silence != evaluated 原則の自己違反）。

本テストは run_evolve が:
  1. env_score 算出成功時 → トップレベル `result["env_score"]` に構造化値（score + level）を置く
  2. env_score 算出失敗時 → 黙らず degraded 表示（取得失敗 + 前回 level フォールバック）を置く
ことを検証する。

run_audit / compute_environment_fitness は LLM 経路（constitutional cache 保存等）と
実環境ストア走査を避けるため必ず mock する。HOME 隔離は conftest の autouse fixture。
"""
import sys
from pathlib import Path
from unittest import mock

_plugin_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "rl"))
sys.path.insert(0, str(_plugin_root / "scripts" / "rl" / "fitness"))

import audit  # noqa: E402
import environment  # noqa: E402  # patch ターゲットを sys.modules に載せる
import evolve  # noqa: E402
from evolve import run_evolve  # noqa: E402

_ = environment  # 使用済みマーク（patch ターゲット）


def test_run_evolve_surfaces_structured_env_score(tmp_path):
    """env_score 算出成功時、result トップレベルに env_score（score + level）が surface される。"""
    live_audit = sys.modules.get("audit", audit)
    fake_env = {"overall": 0.72, "sources": ["coherence", "telemetry"]}
    with mock.patch.object(live_audit, "run_audit", return_value="## audit report"), \
         mock.patch.object(evolve, "_compute_env_score_struct", wraps=evolve._compute_env_score_struct) as spy, \
         mock.patch("environment.compute_environment_fitness", return_value=fake_env):
        result = run_evolve(project_dir=str(tmp_path))

    assert spy.called, "_compute_env_score_struct が呼ばれていない（env_score 配線漏れ）"
    assert "env_score" in result, "result トップレベルに env_score field が無い（SKILL.md が読む場所）"
    es = result["env_score"]
    assert isinstance(es, dict), "env_score は構造化 dict であるべき"
    assert es.get("score") == 0.72, f"env_score.score が surface されていない: {es}"
    assert es.get("level") is not None, "env_score.level（compute_level 結果）が無い"
    assert es.get("title_ja") is not None
    assert es.get("title_en") is not None
    assert es.get("degraded") is not True, "成功時は degraded であってはならない"


def test_run_evolve_env_score_degraded_when_compute_fails(tmp_path):
    """env_score 算出失敗時、黙らず degraded 表示を surface する（silence != evaluated）。"""
    live_audit = sys.modules.get("audit", audit)
    with mock.patch.object(live_audit, "run_audit", return_value="## audit report"), \
         mock.patch("environment.compute_environment_fitness", side_effect=RuntimeError("boom")):
        result = run_evolve(project_dir=str(tmp_path))

    assert "env_score" in result, "失敗時も env_score field を必ず置く（degraded surface）"
    es = result["env_score"]
    assert isinstance(es, dict)
    assert es.get("degraded") is True, "算出失敗時は degraded=True で明示する"
    assert es.get("score") is None, "失敗時 score は None（捏造しない）"
    assert "reason" in es, "失敗理由を surface する"


def test_run_evolve_env_score_dry_run_does_not_record_fitness(tmp_path, monkeypatch):
    """dry_run=True 時、env_score 算出は fitness 履歴を書かない（record=False を渡す）。"""
    live_audit = sys.modules.get("audit", audit)
    captured = {}

    def fake_compute(proj, *args, **kwargs):
        captured["record"] = kwargs.get("record")
        return {"overall": 0.5, "sources": ["coherence"]}

    with mock.patch.object(live_audit, "run_audit", return_value="## audit report"), \
         mock.patch("environment.compute_environment_fitness", side_effect=fake_compute):
        run_evolve(project_dir=str(tmp_path), dry_run=True)

    assert captured.get("record") is False, "dry_run=True なのに record=True で fitness 履歴を汚す"


def test_run_evolve_captures_audit_stderr_into_warnings(tmp_path):
    """#523-1: audit 実行中の stderr（Chaos スキップ等）が self_analysis に配線される。"""
    live_audit = sys.modules.get("audit", audit)

    def fake_audit(*args, **kwargs):
        # run_audit が出すスキップ通知を模す（Python warnings ではなく素の stderr print）
        print("Chaos Testing スキップ: スキップ 2 件（worktree 残骸）", file=sys.stderr)
        return "## audit report"

    with mock.patch.object(live_audit, "run_audit", side_effect=fake_audit), \
         mock.patch("environment.compute_environment_fitness", return_value={"overall": 0.5, "sources": []}):
        result = run_evolve(project_dir=str(tmp_path))

    warnings = result.get("warnings", [])
    msgs = [w.get("message", "") for w in warnings if isinstance(w, dict)]
    assert any("Chaos Testing スキップ" in m for m in msgs), (
        f"audit stderr が result['warnings'] に捕捉されていない: {msgs}"
    )
    # self_analysis.runtime_errors が stderr を拾って「警告なし」と誤報告しない
    sa = result.get("self_analysis", {})
    rt = sa.get("runtime_errors", {}) if isinstance(sa, dict) else {}
    summary = rt.get("summary_line", "")
    assert "stderr 警告なし" not in summary, (
        f"stderr があるのに runtime_errors が「警告なし」と報告: {summary}"
    )
