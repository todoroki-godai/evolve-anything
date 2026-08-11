"""bin/evolve-daily-run の改善案 digest 配線テスト（#409）。

`fleet queue --json` の出力（``queue`` フィールド）から
``daily.proposal_digest.build_proposal_digest`` を呼び、結果を evolve-queue.json の
``proposals`` フィールドへ埋め込む。新しい group 化ロジックは発明せず既存 digest ビルダーを
そのまま使う。digest 生成が例外を投げても daily-run 全体は落とさない（fail-open、既存の
llm_judge ステップと同型）。

``daily.proposal_digest.build_proposal_digest`` 自体は monkeypatch し、単体テストで実際の
weak_signals 走査（decision ロジック）を再検証しない（それは scripts/lib/tests/
test_proposal_digest.py の責務）。ここでは「呼ばれるか」「結果が正しいキーに入るか」
「失敗しても daily-run が継続するか」の配線だけを検証する。
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

from daily import proposal_digest  # noqa: E402


def _load_module():
    loader = SourceFileLoader("evolve_daily_run_under_test_proposals", str(SCRIPT))
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
    def fake_run(cmd, **kwargs):
        if cmd[0] == "gh":
            return _FakeResult(returncode=0, stdout="[]")
        if "queue" in cmd and "--json" in cmd:
            return _FakeResult(returncode=queue_rc, stdout=queue_stdout)
        return _FakeResult(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)


def test_proposal_digest_embedded_in_evolve_queue_json(monkeypatch, tmp_path):
    mod = _load_module()
    sample_queue = [{"pj_slug": "pj-a"}]
    _install_fake_run(
        mod, monkeypatch,
        queue_stdout=json.dumps({"queue": sample_queue, "generated_at": "2026-08-11T00:00:00+00:00"}),
    )

    captured = {}

    def fake_digest(queue_entries, **kwargs):
        captured["queue_entries"] = queue_entries
        captured["kwargs"] = kwargs
        return {"generated_at": "x", "per_pj": {"pj-a": [{"signal_keys": ["k1"]}]}, "global": []}

    monkeypatch.setattr(proposal_digest, "build_proposal_digest", fake_digest)
    monkeypatch.setattr(mod, "_proposal_digest", proposal_digest, raising=False)

    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert payload["proposals"] == {
        "generated_at": "x", "per_pj": {"pj-a": [{"signal_keys": ["k1"]}]}, "global": [],
    }
    assert captured["queue_entries"] == sample_queue
    assert captured["kwargs"]["data_dir"] == tmp_path
    # queue 本体の既存フィールドは維持される（上書きでなく追加）。
    assert payload["queue"] == sample_queue


def test_proposal_digest_failure_does_not_crash_daily_run(monkeypatch, tmp_path, capsys):
    mod = _load_module()
    _install_fake_run(mod, monkeypatch)

    def fake_digest(queue_entries, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(proposal_digest, "build_proposal_digest", fake_digest)
    monkeypatch.setattr(mod, "_proposal_digest", proposal_digest, raising=False)

    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert "proposals" not in payload
    assert "proposal digest error" in capsys.readouterr().err


def test_proposal_digest_coexists_with_llm_judge_summary(monkeypatch, tmp_path):
    mod = _load_module()
    _install_fake_run(mod, monkeypatch, queue_stdout='{"queue": []}')

    def fake_judge(**kwargs):
        return {"unjudged_total": 0, "selected": 0, "capped": False, "corrections": 0, "call_failed": 0}

    monkeypatch.setattr(mod.judge_runner, "run_daily_judge", fake_judge)

    def fake_digest(queue_entries, **kwargs):
        return {"generated_at": "x", "per_pj": {}, "global": []}

    monkeypatch.setattr(proposal_digest, "build_proposal_digest", fake_digest)
    monkeypatch.setattr(mod, "_proposal_digest", proposal_digest, raising=False)

    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "evolve-queue.json").read_text(encoding="utf-8"))
    assert "llm_judge" in payload
    assert "proposals" in payload
