#!/usr/bin/env python3
"""tier_policy_cli.py のテスト — `bin/evolve-tier` CLI 本体（#193）。

決定論・LLM 非依存。root conftest の autouse HOME 隔離により
`tier_policy.tiers_config_path()` は毎回未使用の隔離先を指すため、実 ~/.claude には
一切触れない。exit code 契約: 正常 0 / 引数・バリデーションエラー 2 / config strict エラー 1。
"""
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import tier_policy  # noqa: E402
import tier_policy_cli as cli  # noqa: E402


class TestShow:
    def test_show_exit_zero_defaults(self, capsys):
        rc = cli.main(["show"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "HEAD" in out
        assert "defaults" in out

    def test_show_json_contains_tiers(self, capsys):
        rc = cli.main(["show", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["tiers"]["HEAD"]["model"] == "sonnet"
        assert data["source"] == "defaults"


class TestInit:
    def test_init_creates_config(self, capsys):
        path = tier_policy.tiers_config_path()
        assert not path.is_file()
        rc = cli.main(["init"])
        assert rc == 0
        assert path.is_file()

    def test_init_twice_refuses_second(self, capsys):
        cli.main(["init"])
        rc = cli.main(["init"])
        assert rc == 2


class TestSet:
    def test_set_valid_returns_zero(self, capsys):
        rc = cli.main(["set", "HEAD", "--model", "opus", "--effort", "xhigh"])
        assert rc == 0
        policy = tier_policy.load_tier_policy()
        assert policy["HEAD"] == {"model": "opus", "effort": "xhigh"}

    def test_set_no_effort_flag(self, capsys):
        rc = cli.main(["set", "MECH", "--model", "haiku", "--no-effort"])
        assert rc == 0
        policy = tier_policy.load_tier_policy()
        assert policy["MECH"] == {"model": "haiku", "effort": None}

    def test_set_invalid_model_returns_two(self, capsys):
        rc = cli.main(["set", "HEAD", "--model", "gpt5", "--effort", "high"])
        assert rc == 2

    def test_set_missing_required_model_flag_is_argparse_error(self, capsys):
        # argparse は必須引数欠落時に SystemExit(2) を投げる
        try:
            cli.main(["set", "HEAD"])
            assert False, "SystemExit を期待"
        except SystemExit as e:
            assert e.code == 2


class TestSync:
    def test_dry_run_reports_drift_without_writing(self, tmp_path, capsys):
        agent = tmp_path / "a.md"
        agent.write_text(
            "---\nname: a\ntier: HEAD\nmodel: opus\neffort: xhigh\n---\nBody\n",
            encoding="utf-8",
        )
        config_path = tier_policy.tiers_config_path()
        tier_policy.init_config(config_path=config_path)
        cfg = tier_policy.load_tiers_config(config_path=config_path)
        cfg["targets"]["agents"] = [str(agent)]
        tier_policy._atomic_write_json(config_path, cfg)

        original = agent.read_text(encoding="utf-8")
        rc = cli.main(["sync"])
        assert rc == 0
        assert agent.read_text(encoding="utf-8") == original  # dry-run は書き込まない
        out = capsys.readouterr().out
        assert "drift" in out

    def test_apply_writes_drift(self, tmp_path, capsys):
        agent = tmp_path / "a.md"
        agent.write_text(
            "---\nname: a\ntier: HEAD\nmodel: opus\neffort: xhigh\n---\nBody\n",
            encoding="utf-8",
        )
        config_path = tier_policy.tiers_config_path()
        tier_policy.init_config(config_path=config_path)
        cfg = tier_policy.load_tiers_config(config_path=config_path)
        cfg["targets"]["agents"] = [str(agent)]
        tier_policy._atomic_write_json(config_path, cfg)

        rc = cli.main(["sync", "--apply"])
        assert rc == 0
        assert "model: sonnet" in agent.read_text(encoding="utf-8")

    def test_strict_config_error_returns_one(self, capsys):
        config_path = tier_policy.tiers_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{ not json", encoding="utf-8")
        rc = cli.main(["sync"])
        assert rc == 1


class TestDrift:
    def test_drift_exit_zero_no_findings(self, capsys):
        rc = cli.main(["drift"])
        assert rc == 0

    def test_drift_reports_stale_opus(self, tmp_path, capsys):
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        (rules_dir / "note.md").write_text("still using opus\n", encoding="utf-8")
        config_path = tier_policy.tiers_config_path()
        tier_policy.init_config(config_path=config_path)
        cfg = tier_policy.load_tiers_config(config_path=config_path)
        cfg["advisory_scan"] = [str(rules_dir)]
        tier_policy._atomic_write_json(config_path, cfg)

        rc = cli.main(["drift", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert any(f["alias"] == "opus" for f in data)

    def test_strict_config_error_returns_one(self, capsys):
        config_path = tier_policy.tiers_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("{ not json", encoding="utf-8")
        rc = cli.main(["drift"])
        assert rc == 1
