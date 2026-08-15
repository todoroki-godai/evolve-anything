"""evolve_revert_cli.py のテスト — `bin/evolve-revert` CLI 本体（#402 段階4 §6）。

決定論・LLM 非依存。CLI は段階3 の apply engine（``evolve_revert.apply_revert`` /
``evolve_revert.dump_before``）を呼ぶだけでロジックを持たない契約（設計正典 §6）を検証する。
exit code 契約: 成功 0 / 失敗（entry not found・conflict・拒否等）1 / 引数エラー 2。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

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


class TestListFlag:
    """``--list``（ADR-054 Phase D PR4/D2）: read-only の entry_id 一覧導線。"""

    def test_list_calls_build_revert_listing_not_apply(self, monkeypatch, capsys):
        calls = {}

        def _fake_build(*a, **kw):
            calls["called"] = True
            return []

        def _boom_apply(*a, **kw):
            raise AssertionError("apply_revert must not be called for --list")

        monkeypatch.setattr(cli, "build_revert_listing", _fake_build)
        monkeypatch.setattr(cli, "apply_revert", _boom_apply)

        rc = cli.main(["--list"])

        assert rc == 0
        assert calls == {"called": True}
        assert "0件" in capsys.readouterr().out

    def test_list_renders_items_via_render_revert_listing(self, monkeypatch, capsys):
        monkeypatch.setattr(
            cli, "build_revert_listing",
            lambda *a, **kw: [{
                "entry_id": "p1", "skill_name": "queue", "timestamp": "2026-08-01T00:00:00+00:00",
                "scope": "project", "revert_available": True, "revert_unavailable_reason": None,
            }],
        )

        rc = cli.main(["--list"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "p1" in out
        assert "bin/evolve-revert p1" in out

    def test_list_json_output(self, monkeypatch, capsys):
        items = [{
            "entry_id": "p1", "skill_name": "queue", "timestamp": "2026-08-01T00:00:00+00:00",
            "scope": "project", "revert_available": True, "revert_unavailable_reason": None,
        }]
        monkeypatch.setattr(cli, "build_revert_listing", lambda *a, **kw: items)

        rc = cli.main(["--list", "--json"])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["total"] == 1
        assert payload["items"] == items

    def test_list_with_entry_id_is_argument_error(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["e1", "--list"])
        assert exc.value.code == 2
        assert "併用できません" in capsys.readouterr().err

    def test_list_with_apply_is_argument_error(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--list", "--apply"])
        assert exc.value.code == 2

    def test_json_without_list_is_argument_error(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["e1", "--json"])
        assert exc.value.code == 2
        assert "--list とのみ" in capsys.readouterr().err


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

    def _setup_entry(self, tmp_path, monkeypatch, *, target_content, before_text):
        """#469 テスト共通セットアップ（normal/idempotent/conflict 共有）。"""
        import optimize_history_store as store
        from evolve_decision_ids import (
            REVERT_ENCODING, REVERT_SCHEMA_VERSION, compress_before_content, sha256,
        )

        canonical = tmp_path / "evolve-anything"
        monkeypatch.setattr(store, "HISTORY_ROOT", canonical / "optimize_history")

        target = tmp_path / "SKILL.md"
        target.write_text(target_content, encoding="utf-8")

        store.append_entry(
            {
                "id": "e1", "human_accepted": True, "skill_name": "x",
                "before_sha": sha256(before_text),
                "after_sha": sha256("after content\n"),
                "revert_before_b64": compress_before_content(before_text),
                "revert_schema_version": REVERT_SCHEMA_VERSION,
                "revert_encoding": REVERT_ENCODING,
                "scope": "project", "repo_id": str(tmp_path), "relative_path": "SKILL.md",
            },
            "proj",
        )
        monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj")
        return target

    def test_dry_run_normal_branch_prints_path_and_branch_header(
        self, tmp_path, monkeypatch, capsys
    ):
        """#469: 通常分岐の dry-run 出力に対象パス・repo相対パス・判定・変更行数が含まれる。"""
        target = self._setup_entry(
            tmp_path, monkeypatch,
            target_content="after content\n", before_text="before content\n",
        )

        rc = cli.main(["e1"])
        out = capsys.readouterr().out

        assert rc == 0
        assert str(target) in out
        assert "SKILL.md" in out  # repo 相対パス
        assert "判定" in out and "通常" in out
        assert "変更行数" in out
        # 書込みゼロ（既存契約の維持）
        assert target.read_text(encoding="utf-8") == "after content\n"

    def test_dry_run_idempotent_branch_prints_header(self, tmp_path, monkeypatch, capsys):
        target = self._setup_entry(
            tmp_path, monkeypatch,
            target_content="before content\n", before_text="before content\n",
        )

        rc = cli.main(["e1"])
        out = capsys.readouterr().out

        assert rc == 0
        assert str(target) in out
        assert "冪等" in out

    def test_dry_run_conflict_branch_prints_header(self, tmp_path, monkeypatch, capsys):
        target = self._setup_entry(
            tmp_path, monkeypatch,
            target_content="someone-else-changed-this\n", before_text="before content\n",
        )

        rc = cli.main(["e1"])
        out = capsys.readouterr().out

        assert rc == 1  # conflict は失敗扱い
        assert str(target) in out
        assert "衝突" in out

    def test_apply_real_run_does_not_print_dry_run_header(self, tmp_path, monkeypatch, capsys):
        """--apply（dry_run=False）では #469 のヘッダを付けない（既存の完了メッセージのみ）。"""
        target = self._setup_entry(
            tmp_path, monkeypatch,
            target_content="after content\n", before_text="before content\n",
        )

        rc = cli.main(["e1", "--apply"])
        out = capsys.readouterr().out

        assert rc == 0
        assert "判定:" not in out
        assert target.read_text(encoding="utf-8") == "before content\n"
