"""bin/evolve-daily-run の llm_judge Phase B daily 配線テスト（#408）。

`fleet detect` の後・`fleet queue --json` の前に
``correction_semantic.judge_runner.run_daily_judge(run=True, ...)`` を直接呼び出し
（icebox_reconcile と同型の直接 import・非 subprocess）、結果を evolve-queue.json の
``llm_judge`` フィールドへ埋め込む。userConfig（judge_daily_utterance_limit /
judge_daily_token_limit）から上限を渡す。失敗しても daily-run 全体は落とさない
（fail-open、既存ステップと同型）。

subprocess は mock、``judge_runner.run_daily_judge`` も monkeypatch する（単体テストで
実 LLM を起動しない・no-llm-in-tests 完全整合。実 LLM 呼び出しは call_haiku 1箇所に
すでに集約されているため、この層のテストは run_daily_judge そのものを差し替える）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "evolve-daily-run"
_LIB_DIR = Path(__file__).resolve().parents[2] / "scripts" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from correction_semantic import judge_runner  # noqa: E402


def _load_module():
    loader = SourceFileLoader("evolve_daily_run_under_test_judge", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _FakeResult:
    def __init__(self, returncode=0, stdout="{}"):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _install_fake_run(mod, monkeypatch, *, queue_stdout='{"queue": []}', queue_rc=0):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "gh":
            return _FakeResult(returncode=0, stdout="[]")
        if "queue" in cmd and "--json" in cmd:
            return _FakeResult(returncode=queue_rc, stdout=queue_stdout)
        return _FakeResult(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return calls


def test_judge_runs_between_detect_and_queue(monkeypatch):
    mod = _load_module()
    order = []

    def fake_run(cmd, **kwargs):
        if "detect" in cmd:
            order.append("detect")
        if "queue" in cmd and "--json" in cmd:
            order.append("queue")
            return _FakeResult(returncode=0, stdout='{"queue": []}')
        if cmd[0] == "gh":
            return _FakeResult(returncode=0, stdout="[]")
        return _FakeResult(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    def fake_judge(**kwargs):
        order.append("judge")
        return {"unjudged_total": 0, "selected": 0, "capped": False, "corrections": 0, "call_failed": 0}

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)
    rc = mod.main()
    assert rc == 0
    assert order == ["detect", "judge", "queue"], order


def test_judge_called_with_run_true_and_user_config_limits(monkeypatch):
    mod = _load_module()
    _install_fake_run(mod, monkeypatch)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_judge_daily_utterance_limit", "60")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_judge_daily_token_limit", "40000")

    captured = {}

    def fake_judge(**kwargs):
        captured.update(kwargs)
        return {"unjudged_total": 0, "selected": 0, "capped": False, "corrections": 0, "call_failed": 0}

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)
    rc = mod.main()
    assert rc == 0
    assert captured["run"] is True
    assert captured["daily_utterance_limit"] == 60
    assert captured["daily_token_limit"] == 40000


def test_judge_result_embedded_in_evolve_queue_json(monkeypatch, tmp_path):
    mod = _load_module()
    _install_fake_run(mod, monkeypatch, queue_stdout='{"queue": [], "generated_at": "2026-08-10T00:00:00+00:00"}')

    def fake_judge(**kwargs):
        return {
            "unjudged_total": 250,
            "selected": 200,
            "capped": True,
            "corrections": 5,
            "call_failed": 1,
        }

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)
    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert payload["llm_judge"] == {
        "unjudged_before": 250,
        "selected": 200,
        "capped": True,
        "corrections": 5,
        "call_failed": 1,
        "source_failed": False,
        "source_error": None,
        "skipped_locked": False,
    }
    # queue 本体の既存フィールドは維持される（上書きでなく追加）。
    assert payload["queue"] == []


def test_judge_failure_does_not_crash_daily_run(monkeypatch, tmp_path, capsys):
    """run_daily_judge が例外を送出しても daily-run 全体は継続する（fail-open）。"""
    mod = _load_module()
    _install_fake_run(mod, monkeypatch)

    def fake_judge(**kwargs):
        raise RuntimeError("haiku unavailable")

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)
    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert "llm_judge" not in payload
    assert "llm_judge daily run error" in capsys.readouterr().err


def test_judge_not_capped_omits_capped_notice_fields_but_still_embeds(monkeypatch, tmp_path):
    """capped=False でも llm_judge フィールド自体は常に埋め込む（通知判定は読み手の責務）。"""
    mod = _load_module()
    _install_fake_run(mod, monkeypatch)

    def fake_judge(**kwargs):
        return {"unjudged_total": 3, "selected": 3, "capped": False, "corrections": 1, "call_failed": 0}

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)
    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert payload["llm_judge"]["capped"] is False


def test_judge_source_failed_propagates_to_evolve_queue_json(monkeypatch, tmp_path):
    """#410 [Must]E: 発話ソース取得の DB/schema 障害は capped=False でも silent にせず
    evolve-queue.json の llm_judge.source_failed に残す（SessionStart 通知の材料）。
    """
    mod = _load_module()
    _install_fake_run(mod, monkeypatch)

    def fake_judge(**kwargs):
        return {
            "unjudged_total": 0, "selected": 0, "capped": False,
            "corrections": 0, "call_failed": 0,
            "source_failed": True, "source_error": "RuntimeError: duckdb schema mismatch",
        }

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)
    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert payload["llm_judge"]["source_failed"] is True
    assert "duckdb schema mismatch" in payload["llm_judge"]["source_error"]


def test_judge_source_failed_false_when_not_provided(monkeypatch, tmp_path):
    """run_daily_judge の返り値に source_failed が無くても（防御的に）False として扱う。"""
    mod = _load_module()
    _install_fake_run(mod, monkeypatch)

    def fake_judge(**kwargs):
        return {"unjudged_total": 0, "selected": 0, "capped": False, "corrections": 0, "call_failed": 0}

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)
    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert payload["llm_judge"]["source_failed"] is False


def test_judge_skipped_locked_propagates_to_evolve_queue_json(monkeypatch, tmp_path):
    """#410 round2 [Should]②: 別プロセスが lock 保持中で skip したことを evolve-queue.json
    の llm_judge.skipped_locked へ surface する（daily runner のサマリまで伝播）。
    """
    mod = _load_module()
    _install_fake_run(mod, monkeypatch)

    def fake_judge(**kwargs):
        return {
            "unjudged_total": 0, "selected": 0, "capped": False,
            "corrections": 0, "call_failed": 0, "skipped_locked": True,
        }

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)
    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert payload["llm_judge"]["skipped_locked"] is True


def test_judge_skipped_locked_false_when_not_provided(monkeypatch, tmp_path):
    mod = _load_module()
    _install_fake_run(mod, monkeypatch)

    def fake_judge(**kwargs):
        return {"unjudged_total": 0, "selected": 0, "capped": False, "corrections": 0, "call_failed": 0}

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)
    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert payload["llm_judge"]["skipped_locked"] is False
