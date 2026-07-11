"""bin/evolve-daily-run のステップ順序テスト（#157, #194）。

daily runner が `fleet ingest` → `fleet tokens --backfill`（増分 token 取込）→
`fleet queue --json` → `gh issue list --label icebox --state closed`（icebox 棚卸しの
気づきトリガー、#194）の順に subprocess を呼ぶこと、token ingest / gh issue list が
失敗しても後続ステップおよび daily-run 全体を継続すること（fail-open）を検証する。

subprocess は mock（単体テストで別プロセス / LLM を起動しない）。
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
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


def _install_fake_run(mod, monkeypatch, tokens_rc=0, gh_rc=0, gh_stdout="[]", gh_raises=None):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "gh":
            if gh_raises is not None:
                raise gh_raises
            return _FakeResult(returncode=gh_rc, stdout=gh_stdout)
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
    # 4 ステップが呼ばれる
    assert any(c.endswith("ingest") for c in joined), joined
    assert any("tokens --backfill" in c for c in joined), joined
    assert any("queue --json" in c for c in joined), joined
    assert any(c.startswith("gh issue list") for c in joined), joined
    # 順序: ingest → tokens → queue → gh issue list
    i_ingest = next(i for i, c in enumerate(joined) if c.endswith("ingest"))
    i_tokens = next(i for i, c in enumerate(joined) if "tokens --backfill" in c)
    i_queue = next(i for i, c in enumerate(joined) if "queue --json" in c)
    i_gh = next(i for i, c in enumerate(joined) if c.startswith("gh issue list"))
    assert i_ingest < i_tokens < i_queue < i_gh, joined


def test_token_ingest_failure_continues_to_queue(monkeypatch):
    """token ingest が非ゼロ終了しても queue は実行され main は 0 を返す。"""
    mod = _load_module()
    calls = _install_fake_run(mod, monkeypatch, tokens_rc=1)
    rc = mod.main()
    assert rc == 0
    joined = [" ".join(c) for c in calls]
    assert any("queue --json" in c for c in joined), joined


def test_gh_icebox_list_uses_expected_args(monkeypatch):
    mod = _load_module()
    calls = _install_fake_run(mod, monkeypatch)
    rc = mod.main()
    assert rc == 0
    gh_call = next(c for c in calls if c[0] == "gh")
    assert gh_call == [
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
        "closedAt",
        "--limit",
        "100",
    ]


def test_icebox_status_written_on_gh_success(monkeypatch, tmp_path):
    mod = _load_module()
    now = datetime.now(timezone.utc)
    oldest = (now - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    newest = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    gh_stdout = json.dumps([{"closedAt": oldest}, {"closedAt": newest}])
    _install_fake_run(mod, monkeypatch, gh_stdout=gh_stdout)
    rc = mod.main()
    assert rc == 0
    icebox_path = tmp_path / "icebox-status.json"
    assert icebox_path.exists()
    payload = json.loads(icebox_path.read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert 119 <= payload["oldest_days"] <= 120
    assert "generated_at" in payload


def test_icebox_status_empty_list_writes_zero_count(monkeypatch, tmp_path):
    mod = _load_module()
    _install_fake_run(mod, monkeypatch, gh_stdout="[]")
    rc = mod.main()
    assert rc == 0
    payload = json.loads((tmp_path / "icebox-status.json").read_text(encoding="utf-8"))
    assert payload["count"] == 0
    assert payload["oldest_days"] == 0


def test_gh_failure_does_not_overwrite_existing_icebox_status(monkeypatch, tmp_path):
    """gh 失敗時は既存ファイルを壊さず daily-run 全体も継続する（fail-open）。"""
    mod = _load_module()
    icebox_path = tmp_path / "icebox-status.json"
    existing = {"count": 1, "oldest_days": 5, "generated_at": "existing"}
    icebox_path.write_text(json.dumps(existing), encoding="utf-8")
    _install_fake_run(mod, monkeypatch, gh_rc=1, gh_stdout="")
    rc = mod.main()
    assert rc == 0  # daily-run 全体は落ちない
    assert json.loads(icebox_path.read_text(encoding="utf-8")) == existing


def test_gh_binary_missing_does_not_crash_daily_run(monkeypatch, tmp_path):
    """gh コマンド自体が無い環境でも daily-run 全体を落とさない。"""
    mod = _load_module()
    _install_fake_run(mod, monkeypatch, gh_raises=FileNotFoundError("gh not found"))
    rc = mod.main()
    assert rc == 0


def test_gh_malformed_json_does_not_overwrite_existing_icebox_status(monkeypatch, tmp_path):
    """gh の stdout が期待形式でない場合も既存ファイルを壊さず継続する。"""
    mod = _load_module()
    icebox_path = tmp_path / "icebox-status.json"
    existing = {"count": 1, "oldest_days": 5, "generated_at": "existing"}
    icebox_path.write_text(json.dumps(existing), encoding="utf-8")
    _install_fake_run(mod, monkeypatch, gh_stdout="not json")
    rc = mod.main()
    assert rc == 0
    assert json.loads(icebox_path.read_text(encoding="utf-8")) == existing
