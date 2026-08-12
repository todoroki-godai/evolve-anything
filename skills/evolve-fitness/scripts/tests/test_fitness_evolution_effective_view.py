"""#402 段階4 M4: fitness_evolution.py の raw/effective 分離。

``fitness_evolution.load_history()`` は ``optimize_history_store`` の wrapper ではなく
指定 ``history_file`` を直接 JSONL 読みする別実装で、同じ関数が
  - ``record_evolve_diff_decision`` の raw ID dedup（:236）— raw が正しい
  - calibration の業務読取（``run_fitness_evolution`` の母集団判定）— effective が必要
の両方に使われている（設計正典 §1）。単純な import 置換では分離できないため、raw の
``load_history`` はそのまま残し、業務読取用の ``load_effective_history`` を新設して
モジュール内で経路を明示的に分ける。
"""
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "skills" / "evolve-fitness" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import fitness_evolution as fe  # noqa: E402


def _write_jsonl(path: Path, records: list) -> None:
    import json
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


class TestLoadEffectiveHistoryExcludesRevert:
    def test_excludes_reverted_accept_and_revert_event(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        _write_jsonl(history_file, [
            {"id": "e1", "human_accepted": True, "best_fitness": 0.6, "fitness_func": "skill_quality"},
            {
                "event_type": "revert", "reverted_entry_id": "e1", "revert_event_id": "rev1",
                "revert_generation": 1, "scope": "project", "repo_id": "r", "relative_path": "p",
            },
            {"id": "e2", "human_accepted": False, "fitness_func": "skill_quality"},
        ])

        effective = fe.load_effective_history(history_file)

        ids = [r.get("id") for r in effective]
        assert ids == ["e2"]

    def test_no_revert_events_passes_through_unchanged(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        records = [
            {"id": "e1", "human_accepted": True, "best_fitness": 0.6},
            {"id": "e2", "human_accepted": False},
        ]
        _write_jsonl(history_file, records)

        assert fe.load_effective_history(history_file) == records

    def test_default_history_file_resolution_matches_load_history(self, tmp_path, monkeypatch):
        """history_file/project_dir 省略時は load_history と同じデフォルト解決を使う。"""
        import optimize_history_store as store

        monkeypatch.setattr(store, "HISTORY_ROOT", tmp_path / "optimize_history")
        monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj")
        store.append_entry({"id": "e1", "human_accepted": True}, "proj")

        assert fe.load_effective_history() == fe.load_history()


class TestLoadHistoryRawDedupUnaffected:
    """raw 経路（record_evolve_diff_decision の ID dedup）は revert 反映の影響を受けない
    ——revert 済みでも同一 id の再書込みを防げなければならない（冪等性維持）。"""

    def test_record_with_existing_id_is_idempotent_even_after_revert(self, tmp_path):
        history_file = tmp_path / "history.jsonl"
        _write_jsonl(history_file, [
            {"id": "evolve_diff_abc", "human_accepted": True, "skill_name": "x"},
            {
                "event_type": "revert", "reverted_entry_id": "evolve_diff_abc",
                "revert_event_id": "rev1", "revert_generation": 1,
                "scope": "project", "repo_id": "r", "relative_path": "p",
            },
        ])

        result = fe.record_evolve_diff_decision(
            skill_name="x",
            after_content="---\nname: x\ndescription: d\n---\nbody",
            diff_summary="d",
            human_accepted=True,
            entry_id="evolve_diff_abc",
            history_file=history_file,
        )

        # 冪等: 既存 id なので新規行は追記されず、raw 上の既存レコード数は変わらない。
        lines = history_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert result["id"] == "evolve_diff_abc"


class TestRunFitnessEvolutionUsesEffectiveHistoryByDefault:
    def test_default_load_calls_load_effective_history_not_raw(self, monkeypatch):
        calls = []

        def _fake_effective(*args, **kwargs):
            calls.append(("effective", args, kwargs))
            return [{"human_accepted": True, "best_fitness": 0.5}] * 30

        def _fake_raw(*args, **kwargs):
            calls.append(("raw", args, kwargs))
            return []

        monkeypatch.setattr(fe, "load_effective_history", _fake_effective)
        monkeypatch.setattr(fe, "load_history", _fake_raw)

        fe.run_fitness_evolution()

        kinds = [c[0] for c in calls]
        assert "effective" in kinds
        assert "raw" not in kinds

    def test_explicit_history_override_bypasses_effective_loader(self, monkeypatch):
        """history を明示注入した場合は effective loader を呼ばない（呼び出し側の責務）。"""
        calls = []
        monkeypatch.setattr(
            fe, "load_effective_history", lambda *a, **kw: calls.append("effective")
        )

        fe.run_fitness_evolution(history=[{"human_accepted": True, "best_fitness": 0.5}] * 30)

        assert calls == []
