"""bin/evolve-daily-run の icebox 3レーン棚卸しステップ（第5ステップ、#352）テスト。

既存 icebox 通知ステップ（#194・件数/最古日数だけの gh 呼び出し）の直後に、
`gh issue list --json number,body,closedAt,updatedAt` を read-only で叩き、
`icebox_reconcile.build_verdicts` で決定論分類した結果を `icebox-verdicts.json` に保存する。
gh の失敗・タイムアウト・想定外 JSON でも daily-run 全体は落とさない（fail-open、
既存 icebox ステップと同型）。

subprocess は mock（単体テストで別プロセス / LLM を起動しない）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "evolve-daily-run"
_LIB_DIR = Path(__file__).resolve().parents[2] / "scripts" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

import icebox_reconcile  # noqa: E402


def _load_module():
    loader = SourceFileLoader("evolve_daily_run_under_test_icebox_reconcile", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class _FakeResult:
    def __init__(self, returncode=0, stdout="{}"):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _install_fake_run(
    mod,
    monkeypatch,
    *,
    reconcile_rc=0,
    reconcile_stdout="[]",
    reconcile_raises=None,
    status_gh_stdout="[]",
):
    """gh 呼び出しを2種に振り分ける（status 用 `--json closedAt` / reconcile 用 `number,body,...`）。"""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "gh":
            json_idx = cmd.index("--json") + 1 if "--json" in cmd else -1
            fields = cmd[json_idx] if json_idx >= 0 else ""
            if "body" in fields:
                if reconcile_raises is not None:
                    raise reconcile_raises
                return _FakeResult(returncode=reconcile_rc, stdout=reconcile_stdout)
            return _FakeResult(returncode=0, stdout=status_gh_stdout)
        if "queue" in cmd:
            return _FakeResult(returncode=0, stdout='{"queue": []}')
        return _FakeResult(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return calls


def test_reconcile_step_runs_after_status_step(monkeypatch):
    mod = _load_module()
    calls = _install_fake_run(mod, monkeypatch)
    rc = mod.main()
    assert rc == 0
    joined = [" ".join(c) for c in calls]
    gh_calls = [c for c in joined if c.startswith("gh issue list")]
    assert len(gh_calls) == 2, joined
    i_status = next(i for i, c in enumerate(joined) if "closedAt" in c and "body" not in c)
    i_reconcile = next(i for i, c in enumerate(joined) if "body" in c)
    assert i_status < i_reconcile, joined


def test_reconcile_gh_call_uses_expected_fields(monkeypatch):
    mod = _load_module()
    calls = _install_fake_run(mod, monkeypatch)
    mod.main()
    reconcile_call = next(c for c in calls if c[0] == "gh" and "body" in " ".join(c))
    assert reconcile_call == [
        "gh",
        "issue",
        "list",
        "--repo",
        "todoroki-godai/evolve-anything",
        "--label",
        "icebox",
        "--state",
        "closed",
        "--json",
        "number,body,closedAt,updatedAt",
        "--limit",
        "100",
    ]


def test_writes_icebox_verdicts_json(monkeypatch, tmp_path):
    mod = _load_module()
    now = datetime.now(timezone.utc)
    closed = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = (
        f"{icebox_reconcile.REOPEN_HEADING}\n\n```yaml\n"
        "reopen-when:\n  source: weak_signals\n  metric: unprocessed_count\n"
        '  op: ">"\n  threshold: 999999\n```\n'
    )
    issues = [{"number": 321, "body": body, "closedAt": closed, "updatedAt": closed}]
    _install_fake_run(mod, monkeypatch, reconcile_stdout=json.dumps(issues))
    rc = mod.main()
    assert rc == 0
    verdicts_path = tmp_path / "icebox-verdicts.json"
    assert verdicts_path.exists()
    payload = json.loads(verdicts_path.read_text(encoding="utf-8"))
    assert "generated_at" in payload
    assert len(payload["verdicts"]) == 1
    assert payload["verdicts"][0]["number"] == 321
    assert payload["verdicts"][0]["lane"] is None  # threshold 999999 は満たさない


def test_reconcile_gh_failure_does_not_crash_daily_run(monkeypatch, tmp_path):
    mod = _load_module()
    _install_fake_run(mod, monkeypatch, reconcile_rc=1)
    rc = mod.main()
    assert rc == 0
    assert not (tmp_path / "icebox-verdicts.json").exists()


def test_reconcile_gh_timeout_does_not_crash_daily_run(monkeypatch, tmp_path):
    mod = _load_module()
    _install_fake_run(
        mod, monkeypatch, reconcile_raises=mod.subprocess.TimeoutExpired(cmd="gh", timeout=30)
    )
    rc = mod.main()
    assert rc == 0
    assert not (tmp_path / "icebox-verdicts.json").exists()


def test_reconcile_gh_missing_binary_does_not_crash_daily_run(monkeypatch, tmp_path):
    mod = _load_module()
    _install_fake_run(mod, monkeypatch, reconcile_raises=FileNotFoundError("gh not found"))
    rc = mod.main()
    assert rc == 0


def test_reconcile_malformed_json_does_not_crash_daily_run(monkeypatch, tmp_path):
    mod = _load_module()
    _install_fake_run(mod, monkeypatch, reconcile_stdout="not json")
    rc = mod.main()
    assert rc == 0
    assert not (tmp_path / "icebox-verdicts.json").exists()


def test_reconcile_does_not_affect_queue_return_code(monkeypatch, tmp_path):
    """queue 失敗 (rc!=0) でも reconcile ステップは実行され、最終 rc は queue 由来のまま。"""
    mod = _load_module()
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "gh":
            json_idx = cmd.index("--json") + 1
            if "body" in cmd[json_idx]:
                return _FakeResult(returncode=0, stdout="[]")
            return _FakeResult(returncode=0, stdout="[]")
        if "queue" in cmd:
            return _FakeResult(returncode=3, stdout="")
        return _FakeResult(returncode=0)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc = mod.main()
    assert rc == 3
    assert any(c[0] == "gh" and "body" in " ".join(c) for c in calls)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
