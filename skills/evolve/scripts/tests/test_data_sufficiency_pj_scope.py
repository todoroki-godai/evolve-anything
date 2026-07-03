"""evolve のデータ十分性チェックの PJ スコープ（#136）。

現象: atlas-breeaders 向け dry-run（実 distinct session 63 件）で「60681 セッション,
2720 新規観測 — データ十分」。旧 slug rl-anything の全 PJ 集計が支配し、cold-start /
小規模 PJ の「データ不足」判定が無意味化していた。

根因（2 層）:
  1. PJ フィルタ欠落: count_new_sessions / count_new_observations /
     _count_total_observations に project スコープが無かった
  2. 時間フィルタは #135 で writer 復活。本テストは read 側の PJ スコープ + per-PJ
     time filter（evolve-queue-state.jsonl の last_evolve_at）を固定する

決定論・LLM 非依存。HOME/DATA_DIR 隔離は root conftest autouse（#457/#420）。
"""
import json
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


def _isolate(tmp_path, monkeypatch):
    """evolve / session_store / rl_common の DATA_DIR を tmp に固定する。"""
    import evolve
    import session_store
    import rl_common

    monkeypatch.setattr(evolve, "DATA_DIR", tmp_path)
    monkeypatch.setattr(evolve, "EVOLVE_STATE_FILE", tmp_path / "evolve-state.json")
    monkeypatch.setattr(session_store, "SESSIONS_JSONL", tmp_path / "sessions.jsonl")
    monkeypatch.setattr(session_store, "SESSIONS_DB", tmp_path / "sessions.db")
    monkeypatch.setattr(rl_common, "DATA_DIR", tmp_path)


class TestCountObservationsScope:
    def test_project_none_counts_all(self, tmp_path, monkeypatch):
        """project=None は全 PJ の usage を数える（後方互換）。"""
        import evolve
        _isolate(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "usage.jsonl",
            [
                {"timestamp": "2026-06-01T00:00:00+00:00", "session_id": "s1", "project": "proj-a"},
                {"timestamp": "2026-06-02T00:00:00+00:00", "session_id": "s2", "project": "proj-b"},
            ],
        )
        assert evolve.count_new_observations(project=None) == 2

    def test_project_scopes_observations(self, tmp_path, monkeypatch):
        """project 指定で当 PJ の usage だけを数える（他 PJ を除外）。"""
        import evolve
        _isolate(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "usage.jsonl",
            [
                {"timestamp": "2026-06-01T00:00:00+00:00", "session_id": "a1", "project": "proj-a"},
                {"timestamp": "2026-06-02T00:00:00+00:00", "session_id": "a2", "project": "proj-a"},
                {"timestamp": "2026-06-03T00:00:00+00:00", "session_id": "b1", "project": "proj-b"},
            ],
        )
        assert evolve.count_new_observations(project="proj-a") == 2

    def test_total_observations_scopes(self, tmp_path, monkeypatch):
        """_count_total_observations も project スコープする（project=None は全件）。"""
        import evolve
        _isolate(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "usage.jsonl",
            [
                {"timestamp": "2026-06-01T00:00:00+00:00", "project": "proj-a"},
                {"timestamp": "2026-06-02T00:00:00+00:00", "project": "proj-a"},
                {"timestamp": "2026-06-03T00:00:00+00:00", "project": "proj-b"},
            ],
        )
        assert evolve._count_total_observations(project=None) == 3
        assert evolve._count_total_observations(project="proj-a") == 2


class TestCountSessionsScope:
    def test_sessions_scoped_from_store_and_usage(self, tmp_path, monkeypatch):
        """count_new_sessions は sessions + usage の両方を当 PJ に絞って union する。"""
        import evolve
        _isolate(tmp_path, monkeypatch)
        # sessions.jsonl（session_store が読む）
        _write_jsonl(
            tmp_path / "sessions.jsonl",
            [
                {"session_id": "a1", "timestamp": "2026-06-01T00:00:00+00:00", "project": "proj-a"},
                {"session_id": "b1", "timestamp": "2026-06-02T00:00:00+00:00", "project": "proj-b"},
            ],
        )
        # usage.jsonl（backfill 経路・Agent レコードは timestamp を持つ）
        _write_jsonl(
            tmp_path / "usage.jsonl",
            [
                {"session_id": "a2", "timestamp": "2026-06-03T00:00:00+00:00", "project": "proj-a"},
                {"session_id": "b2", "timestamp": "2026-06-04T00:00:00+00:00", "project": "proj-b"},
            ],
        )
        # proj-a の distinct session = a1, a2 の 2。proj-b は数えない。
        assert evolve.count_new_sessions(project="proj-a") == 2


class TestPerPjTimeFilter:
    def test_uses_per_pj_last_evolve_not_cross_pj(self, tmp_path, monkeypatch):
        """時間フィルタは当 PJ の last_evolve_at を使い、他 PJ の drain 時刻に汚染されない（#136-5）。"""
        import evolve
        _isolate(tmp_path, monkeypatch)
        # per-PJ last_evolve: proj-a=06-05, proj-b=06-20（他 PJ）
        _write_jsonl(
            tmp_path / "evolve-queue-state.jsonl",
            [
                {"pj_slug": "proj-a", "last_evolve_at": "2026-06-05T00:00:00+00:00", "ts": "2026-06-05T00:00:00+00:00"},
                {"pj_slug": "proj-b", "last_evolve_at": "2026-06-20T00:00:00+00:00", "ts": "2026-06-20T00:00:00+00:00"},
            ],
        )
        _write_jsonl(
            tmp_path / "usage.jsonl",
            [
                {"timestamp": "2026-06-01T00:00:00+00:00", "session_id": "old", "project": "proj-a"},
                {"timestamp": "2026-06-10T00:00:00+00:00", "session_id": "new", "project": "proj-a"},
            ],
        )
        # proj-a の境界 06-05 を使えば 06-10 の 1 件のみ新規。
        # proj-b の 06-20 を誤って使うと 0 件になる（汚染の回帰ガード）。
        assert evolve.count_new_observations(project="proj-a") == 1


class TestCheckDataSufficiencyScope:
    def test_dry_run_summary_reflects_pj_scope(self, tmp_path, monkeypatch):
        """サマリ行「N セッション…」が他 PJ を混ぜず当 PJ スコープ値になる（#136 現象の直接再現）。"""
        import evolve
        _isolate(tmp_path, monkeypatch)
        # 当 PJ（proj-target）25 件 + 他 PJ（noise-pj）1000 件。
        target_recs = [
            {"timestamp": f"2026-06-01T00:00:{i:02d}+00:00", "session_id": f"t{i}", "project": "proj-target"}
            for i in range(25)
        ]
        noise_recs = [
            {"timestamp": "2026-01-01T00:00:00+00:00", "session_id": f"n{i}", "project": "noise-pj"}
            for i in range(1000)
        ]
        _write_jsonl(tmp_path / "usage.jsonl", target_recs + noise_recs)

        result = evolve.check_data_sufficiency(project_dir="/somewhere/proj-target")
        # total_observations は当 PJ の 25 件のみ（1025 でない）。
        assert result["total_observations"] == 25
        assert "1025" not in result["message"]
        assert "1000" not in result["message"]

    def test_no_project_dir_is_backward_compatible(self, tmp_path, monkeypatch):
        """project_dir 未指定は従来どおり全 PJ 集計（後方互換）。"""
        import evolve
        _isolate(tmp_path, monkeypatch)
        _write_jsonl(
            tmp_path / "usage.jsonl",
            [
                {"timestamp": "2026-06-01T00:00:00+00:00", "project": "proj-a"},
                {"timestamp": "2026-06-02T00:00:00+00:00", "project": "proj-b"},
            ],
        )
        result = evolve.check_data_sufficiency()
        assert result["total_observations"] == 2
