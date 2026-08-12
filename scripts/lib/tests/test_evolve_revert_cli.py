"""evolve_revert_cli.py のテスト — `bin/evolve-revert` CLI 本体（#402 段階4 §6）。

決定論・LLM 非依存。CLI は段階3 の apply engine（``evolve_revert.apply_revert`` /
``evolve_revert.dump_before``）を呼ぶだけでロジックを持たない契約（設計正典 §6）を検証する。
exit code 契約: 成功 0 / 失敗（entry not found・conflict・拒否等）1 / 引数エラー 2。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import evolve_revert_cli as cli  # noqa: E402


class TestArgumentValidation:
    def test_dump_before_and_apply_are_mutually_exclusive(self, capsys):
        rc = cli.main(["e1", "--apply", "--dump-before", "/tmp/x"])
        assert rc == 2
        assert "排他" in capsys.readouterr().err

    def test_missing_entry_id_is_argparse_error(self, capsys):
        try:
            cli.main([])
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError("expected SystemExit")


class TestApplyDelegation:
    """CLI は apply_revert を呼ぶだけ（ロジックを持たない）ことを確認する。"""

    def test_default_is_dry_run(self, monkeypatch, capsys):
        calls = {}

        def _fake_apply(entry_id, *, slug=None, dry_run=True, allow_metadata_loss=False):
            calls["entry_id"] = entry_id
            calls["dry_run"] = dry_run
            calls["allow_metadata_loss"] = allow_metadata_loss
            return cli.ApplyResult(ok=True, dry_run=dry_run, entry_id=entry_id, message="ok")

        monkeypatch.setattr(cli, "apply_revert", _fake_apply)

        rc = cli.main(["e1"])

        assert rc == 0
        assert calls == {"entry_id": "e1", "dry_run": True, "allow_metadata_loss": False}
        assert "ok" in capsys.readouterr().out

    def test_apply_flag_disables_dry_run(self, monkeypatch):
        calls = {}
        monkeypatch.setattr(
            cli, "apply_revert",
            lambda entry_id, **kw: (calls.update(kw) or cli.ApplyResult(
                ok=True, dry_run=kw.get("dry_run", True), entry_id=entry_id, message="done"
            )),
        )

        rc = cli.main(["e1", "--apply"])

        assert rc == 0
        assert calls["dry_run"] is False

    def test_allow_metadata_loss_flag_threaded(self, monkeypatch):
        calls = {}
        monkeypatch.setattr(
            cli, "apply_revert",
            lambda entry_id, **kw: (calls.update(kw) or cli.ApplyResult(
                ok=True, dry_run=kw.get("dry_run", True), entry_id=entry_id, message="ok"
            )),
        )

        cli.main(["e1", "--apply", "--allow-metadata-loss"])

        assert calls["allow_metadata_loss"] is True

    def test_apply_failure_returns_exit_one(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli, "apply_revert",
            lambda entry_id, **kw: cli.ApplyResult(
                ok=False, dry_run=kw.get("dry_run", True), entry_id=entry_id,
                reason="entry_not_found", message="entry_id が見つかりません: e1",
            ),
        )

        rc = cli.main(["e1"])

        assert rc == 1
        assert "見つかりません" in capsys.readouterr().out


class TestDumpBeforeDelegation:
    def test_dump_before_calls_dump_before_not_apply(self, monkeypatch, tmp_path, capsys):
        calls = {}

        def _fake_dump(entry_id, dest, *, slug=None):
            calls["entry_id"] = entry_id
            calls["dest"] = dest
            return cli.DumpResult(ok=True, path=str(dest))

        def _fake_apply(*a, **kw):
            raise AssertionError("apply_revert must not be called for --dump-before")

        monkeypatch.setattr(cli, "dump_before", _fake_dump)
        monkeypatch.setattr(cli, "apply_revert", _fake_apply)

        dest = tmp_path / "before.txt"
        rc = cli.main(["e1", "--dump-before", str(dest)])

        assert rc == 0
        assert calls == {"entry_id": "e1", "dest": str(dest)}
        assert str(dest) in capsys.readouterr().out

    def test_dump_before_failure_returns_exit_one(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli, "dump_before",
            lambda entry_id, dest, **kw: cli.DumpResult(ok=False, reason="dest_exists"),
        )

        rc = cli.main(["e1", "--dump-before", "/tmp/exists"])

        assert rc == 1
        assert "dest_exists" in capsys.readouterr().err


class TestEndToEndDryRunNoWrite:
    """実 store 経由（dry-run）で対象ファイル・history・sidecar への書込ゼロを確認する。"""

    def test_dry_run_zero_writes(self, tmp_path, monkeypatch, capsys):
        import optimize_history_store as store
        from evolve_decision_ids import (
            REVERT_ENCODING, REVERT_SCHEMA_VERSION, compress_before_content, sha256,
        )

        canonical = tmp_path / "evolve-anything"
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")

        target = tmp_path / "SKILL.md"
        target.write_text("after content\n", encoding="utf-8")
        before_text = "before content\n"

        store.append_entry(
            {
                "id": "e1", "human_accepted": True, "skill_name": "x",
                "before_sha": sha256(before_text),
                "after_sha": sha256(target.read_text(encoding="utf-8")),
                "revert_before_b64": compress_before_content(before_text),
                "revert_schema_version": REVERT_SCHEMA_VERSION,
                "revert_encoding": REVERT_ENCODING,
                "scope": "project", "repo_id": str(tmp_path), "relative_path": "SKILL.md",
            },
            "proj",
        )
        monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj")

        before_snapshot = set(tmp_path.rglob("*"))
        rc = cli.main(["e1"])
        after_snapshot = set(tmp_path.rglob("*"))

        assert rc == 0
        assert before_snapshot == after_snapshot
        assert target.read_text(encoding="utf-8") == "after content\n"
