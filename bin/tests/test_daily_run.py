"""bin/evolve-daily-run のステップ順序テスト（#157）。

daily runner が `fleet ingest` → `fleet tokens --backfill`（増分 token 取込）→
`fleet queue --json` の順に subprocess を呼ぶこと、token ingest が失敗しても
後続 queue を継続すること（ingest と同じ扱い）を検証する。

subprocess は mock（単体テストで別プロセス / LLM を起動しない）。
"""
from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "evolve-daily-run"


def _load_module():
    # 拡張子なしスクリプトは loader を明示指定する（spec_from_file_location だけでは None）。
    loader = SourceFileLoader("evolve_daily_run_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _FakeResult:
    def __init__(self, returncode=0, stdout="{}"):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _install_fake_run(mod, monkeypatch, tokens_rc=0):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[-1] == "--json" or "queue" in cmd:
            return _FakeResult(returncode=0, stdout='{"queue": []}')
        if "tokens" in cmd:
            return _FakeResult(returncode=tokens_rc)
        return _FakeResult(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return calls


def test_runner_calls_ingest_tokens_queue_in_order(monkeypatch):
    mod = _load_module()
    calls = _install_fake_run(mod, monkeypatch)
    rc = mod.main()
    assert rc == 0
    joined = [" ".join(c) for c in calls]
    # 3 ステップが呼ばれる
    assert any(c.endswith("ingest") for c in joined), joined
    assert any("tokens --backfill" in c for c in joined), joined
    assert any("queue --json" in c for c in joined), joined
    # 順序: ingest → tokens → queue
    i_ingest = next(i for i, c in enumerate(joined) if c.endswith("ingest"))
    i_tokens = next(i for i, c in enumerate(joined) if "tokens --backfill" in c)
    i_queue = next(i for i, c in enumerate(joined) if "queue --json" in c)
    assert i_ingest < i_tokens < i_queue, joined


def test_token_ingest_failure_continues_to_queue(monkeypatch):
    """token ingest が非ゼロ終了しても queue は実行され main は 0 を返す。"""
    mod = _load_module()
    calls = _install_fake_run(mod, monkeypatch, tokens_rc=1)
    rc = mod.main()
    assert rc == 0
    joined = [" ".join(c) for c in calls]
    assert any("queue --json" in c for c in joined), joined


def test_runner_pins_interpreter_for_fleet_subprocess(monkeypatch):
    """全 fleet 呼び出しが sys.executable を先頭に付けて起動する（launchd 最小 PATH で子が 3.9 に落ちない）。

    runner 自身は pin された homebrew python で走るので sys.executable = homebrew。それを
    evolve-fleet の起動に明示的に渡し、bare パス起動時の #!/usr/bin/env python3 → /usr/bin 3.9
    解決を回避する（PATH 非依存の決定論伝播）。
    """
    mod = _load_module()
    calls = _install_fake_run(mod, monkeypatch)
    rc = mod.main()
    assert rc == 0
    # fleet を叩く呼び出し（evolve-fleet を含む）はすべて cmd[0] == sys.executable
    fleet_calls = [c for c in calls if any("evolve-fleet" in part for part in c)]
    assert fleet_calls, calls
    for c in fleet_calls:
        assert c[0] == sys.executable, c
