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

# 実 dry-run の result 構造に合わせる（実フェーズは result["phases"] 配下にネスト、
# env_score は result に存在せず top-level は env_tier のみ — 実機 dry-run で検証済み）。
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
    # env_score でなく top-level に実在する env_tier を surface する
    assert summary["env_tier"] == "small"
    assert "env_score" not in summary
    # 1行であること（途中切断検出の安定性）
    assert stdout.strip().count("\n") == 0


def test_no_output_flag_keeps_full_json_on_stdout(patched_run, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--dry-run"])

    evolve.main()

    stdout = capsys.readouterr().out
    # 後方互換: --output 未指定なら従来通り full JSON を stdout に出す
    parsed = json.loads(stdout)
    assert parsed == _FAKE_RESULT
