"""evolve.py `--output` フラグのテスト（B案: 巨大 JSON を stdout 一発出力しない）。

背景: evolve.py は result dict 全体を `print(json.dumps(..., indent=2))` で stdout に
吐いていた。SKILL.md は以降の多数ステップでこの単一巨大 JSON を読ませる設計だが、
Bash 出力上限や `head -200` で途中切断され invalid JSON になる事故が多発していた。
`--output <path>` でファイルに full JSON を書き、stdout には小さな1行サマリだけ出す。
"""

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_SCRIPTS.parent.parent.parent / "scripts" / "lib"))

import evolve  # noqa: E402

# 実 dry-run の result 構造に合わせる（実フェーズは result["phases"] 配下にネスト）。
# env_score は #523-2/#526-2 で top-level に構造化 dict として surface されるようになった。
_FAKE_RESULT = {
    "phases": {
        "observe": {"action": "ok"},
        "fitness": {"has_fitness": True},
    },
    "observability": {"glossary_drift": ["✓ drift なし"]},
    "env_tier": "small",
}


@pytest.fixture
def patched_run(monkeypatch):
    """run_evolve を固定 dict に差し替える（重いパイプライン/LLM を回さない）。"""
    monkeypatch.setattr(evolve, "run_evolve", lambda **kwargs: dict(_FAKE_RESULT))


def test_output_flag_writes_full_json_to_file(patched_run, monkeypatch, tmp_path, capsys):
    out = tmp_path / "rl_evolve_out.json"
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--dry-run", "--output", str(out)])

    evolve.main()

    # ファイルには full result がそのまま書かれている
    assert out.exists()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written == _FAKE_RESULT


def test_output_flag_stdout_is_small_summary_not_full_json(patched_run, monkeypatch, tmp_path, capsys):
    out = tmp_path / "rl_evolve_out.json"
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--dry-run", "--output", str(out)])

    evolve.main()

    stdout = capsys.readouterr().out
    # stdout は1行サマリ（compact JSON）。full result の中身を含まない
    summary = json.loads(stdout)
    assert summary["output"] == str(out)
    assert "observability" not in summary  # 巨大 result を stdout に混ぜない
    # phases は実フェーズ名（result["phases"] 配下）を列挙する。トップレベルキーではない
    assert summary["phases"] == ["fitness", "observe"]
    assert "observability" not in summary["phases"]  # top-level キーは混ぜない
    # top-level に実在する env_tier を surface する
    assert summary["env_tier"] == "small"
    # この fixture には env_score を入れていないのでサマリにも出ない（None/欠落時は出さない）
    assert "env_score" not in summary
    # 1行であること（途中切断検出の安定性）
    assert stdout.strip().count("\n") == 0


def test_summary_surfaces_env_score_when_present():
    """#523-2/#526-2: result に env_score(dict) があれば 1 行サマリにも level/score を出す。"""
    from pathlib import Path

    result_ok = dict(_FAKE_RESULT)
    result_ok["env_score"] = {"score": 0.72, "level": 7, "degraded": False}
    summary = evolve._summarize_result(result_ok, Path("/tmp/out.json"))
    assert summary["env_score"] == {"score": 0.72, "level": 7}

    result_degraded = dict(_FAKE_RESULT)
    result_degraded["env_score"] = {"score": None, "degraded": True, "previous_level": 6}
    summary_d = evolve._summarize_result(result_degraded, Path("/tmp/out.json"))
    assert summary_d["env_score"] == {"degraded": True, "previous_level": 6}


def test_no_output_flag_keeps_full_json_on_stdout(patched_run, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--dry-run"])

    evolve.main()

    stdout = capsys.readouterr().out
    # 後方互換: --output 未指定なら従来通り full JSON を stdout に出す
    parsed = json.loads(stdout)
    assert parsed == _FAKE_RESULT


# --- #336: stdout = pure JSON 契約。診断ガイダンスは stderr へ分離する ---


def test_insufficient_data_warning_goes_to_stderr_not_stdout(capsys):
    """データ未取得/不足のガイダンスは stderr に出し stdout を汚染しない（#336）。

    stdout は result JSON 専用。ここに人間向けの「テレメトリ未取得」等を混ぜると
    利用側の json.loads が先頭の非 JSON 行で失敗する。
    """
    evolve._warn_insufficient_data(
        {"backfill_recommended": True, "message": "usage.jsonl 不在"}
    )
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout には1文字も出さない
    assert "テレメトリ未取得" in captured.err
    # #486: 削除済みの /rl-anything:backfill を案内しない。observe + evolve が現行経路。
    assert "/rl-anything:backfill" not in captured.err
    assert "evolve" in captured.err

    evolve._warn_insufficient_data(
        {"backfill_recommended": False, "message": "観測 1 件のみ"}
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "データ不足" in captured.err
    assert "--force" in captured.err


def test_main_stdout_pure_json_even_with_diagnostics(monkeypatch, capsys):
    """run_evolve が診断を吐いても main() の stdout は必ず純粋 JSON（#336）。

    run_evolve の途中で `_warn_insufficient_data` が呼ばれる実挙動を模す。
    診断が stderr に分離されていれば stdout は json.loads で必ずパースできる。
    """
    def fake_run(**kwargs):
        evolve._warn_insufficient_data(
            {"backfill_recommended": True, "message": "no telemetry yet"}
        )
        return dict(_FAKE_RESULT)

    monkeypatch.setattr(evolve, "run_evolve", fake_run)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--dry-run"])

    evolve.main()

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)  # 非 JSON 行が混ざれば例外で落ちる
    assert parsed == _FAKE_RESULT
    assert "no telemetry yet" in captured.err  # 診断は stderr 側
