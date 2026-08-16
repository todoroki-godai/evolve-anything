"""migrate_reflect_promoted_status.py のユニットテスト（#475 §4.6）。"""
import json
import sys
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import migrate_reflect_promoted_status as migrator


def _write_jsonl(path: Path, records: list) -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")


def _load_jsonl(path: Path) -> list:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- is_migration_target ---

class TestIsMigrationTarget:
    def test_reflect_confirmed_applied_is_target(self):
        rec = {"source": "reflect_confirmed", "reflect_status": "applied"}
        assert migrator.is_migration_target(rec) is True

    def test_other_source_not_target(self):
        rec = {"source": "hook_detect", "reflect_status": "applied"}
        assert migrator.is_migration_target(rec) is False

    def test_other_status_not_target(self):
        rec = {"source": "reflect_confirmed", "reflect_status": "pending"}
        assert migrator.is_migration_target(rec) is False

    def test_already_promoted_not_target(self):
        """既に promoted のレコードは再移行対象にしない（冪等）。"""
        rec = {"source": "reflect_confirmed", "reflect_status": "promoted"}
        assert migrator.is_migration_target(rec) is False


# --- migrate ---

class TestMigrate:
    def test_dry_run_writes_nothing(self, tmp_path):
        filepath = tmp_path / "corrections.jsonl"
        records = [
            {"source": "reflect_confirmed", "reflect_status": "applied", "message": "a"},
            {"source": "reflect_confirmed", "reflect_status": "applied", "message": "b"},
            {"source": "hook_detect", "reflect_status": "applied", "message": "c"},
        ]
        _write_jsonl(filepath, records)
        before_bytes = filepath.read_bytes()

        result = migrator.migrate(filepath, dry_run=True)

        assert result["migrated"] == 2
        assert result["total"] == 3
        assert result["dry_run"] is True
        assert filepath.read_bytes() == before_bytes

    def test_apply_rewrites_only_targets(self, tmp_path):
        filepath = tmp_path / "corrections.jsonl"
        records = [
            {"source": "reflect_confirmed", "reflect_status": "applied", "message": "a"},
            {"source": "hook_detect", "reflect_status": "applied", "message": "b"},
            {"source": "reflect_confirmed", "reflect_status": "pending", "message": "c"},
        ]
        _write_jsonl(filepath, records)

        result = migrator.migrate(filepath, dry_run=False)

        assert result["migrated"] == 1
        updated = _load_jsonl(filepath)
        assert updated[0]["reflect_status"] == "promoted"
        assert updated[1]["reflect_status"] == "applied"  # 対象外は不変
        assert updated[2]["reflect_status"] == "pending"  # 対象外は不変

    def test_idempotent_second_run_migrates_zero(self, tmp_path):
        """1回目の apply 後、2回目は0件（既に promoted のため対象外＝冪等）。"""
        filepath = tmp_path / "corrections.jsonl"
        _write_jsonl(filepath, [
            {"source": "reflect_confirmed", "reflect_status": "applied", "message": "a"},
        ])
        migrator.migrate(filepath, dry_run=False)

        second = migrator.migrate(filepath, dry_run=False)

        assert second["migrated"] == 0
        assert second["already_migrated"] is True

    def test_no_file_returns_zero(self, tmp_path):
        filepath = tmp_path / "does-not-exist.jsonl"
        result = migrator.migrate(filepath, dry_run=True)
        assert result["total"] == 0
        assert result["migrated"] == 0


# --- CLI ---

class TestMigrateCLI:
    def test_cli_dry_run_default(self, tmp_path, capsys):
        filepath = tmp_path / "corrections.jsonl"
        _write_jsonl(filepath, [
            {"source": "reflect_confirmed", "reflect_status": "applied", "message": "a"},
        ])
        with mock.patch("sys.argv", [
            "migrate_reflect_promoted_status.py", "--corrections-file", str(filepath),
        ]):
            migrator.main()
        output = json.loads(capsys.readouterr().out)
        assert output["dry_run"] is True
        assert output["migrated"] == 1
        # dry-run なので書込みゼロ
        updated = _load_jsonl(filepath)
        assert updated[0]["reflect_status"] == "applied"

    def test_cli_apply_writes(self, tmp_path, capsys):
        filepath = tmp_path / "corrections.jsonl"
        _write_jsonl(filepath, [
            {"source": "reflect_confirmed", "reflect_status": "applied", "message": "a"},
        ])
        with mock.patch("sys.argv", [
            "migrate_reflect_promoted_status.py", "--apply", "--corrections-file", str(filepath),
        ]):
            migrator.main()
        updated = _load_jsonl(filepath)
        assert updated[0]["reflect_status"] == "promoted"
