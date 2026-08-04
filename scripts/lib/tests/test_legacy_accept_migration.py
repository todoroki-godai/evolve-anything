"""legacy_accept_migration.py のテスト（#376 AC6）。

evolve 提案 accept が hash 差分のみで記録されていた旧契約（decision_source 無し）の
`fitness_func=skill_quality` かつ `source=evolve_remediation`（record_evolve_diff_decision
由来）レコードを、削除でなく `fitness_eligible=False` で無効化する。dry-run 既定（安全側）。
optimize/evolve-loop 由来の正当な accept（別経路で人間が同期的に y/n 判断済み）は
decision_source を持たなくても対象外にする（source が違う）。

すべて LLM-free・決定論。
"""
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import legacy_accept_migration as lam  # noqa: E402


def _write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        (json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else r) + "\n"
        for r in records
    )
    path.write_text(body, encoding="utf-8")


def _read_jsonl(path: Path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


LEGACY_ACCEPT = {
    "id": "evolve_diff_legacy1", "source": "evolve_remediation",
    "fitness_func": "skill_quality", "human_accepted": True,
    "skill_name": "s", "timestamp": "2026-08-04T16:17:00+00:00",
}
ALREADY_EXPLICIT = {
    "id": "evolve_diff_new1", "source": "evolve_remediation",
    "fitness_func": "skill_quality", "human_accepted": True,
    "decision_source": "explicit_accept", "skill_name": "s2",
}
OPTIMIZE_ACCEPT = {
    "id": "opt_1", "source": "optimize", "fitness_func": "skill_quality",
    "human_accepted": True, "skill_name": "s3",
}
LEGACY_REJECT = {
    "id": "evolve_diff_legacy_reject", "source": "evolve_remediation",
    "fitness_func": "skill_quality", "human_accepted": False,
    "skill_name": "s4",
}


def test_missing_history_file_returns_empty_report(tmp_path):
    report = lam.invalidate_legacy_accepts(tmp_path / "nope.jsonl")
    assert report["candidates"] == []
    assert report["invalidated"] == 0


def test_dry_run_default_reports_candidates_without_writing(tmp_path):
    hist = tmp_path / "history.jsonl"
    _write_jsonl(hist, [LEGACY_ACCEPT, ALREADY_EXPLICIT, OPTIMIZE_ACCEPT, LEGACY_REJECT])

    report = lam.invalidate_legacy_accepts(hist)  # dry_run 既定

    assert report["dry_run"] is True
    assert report["candidates"] == ["evolve_diff_legacy1"]
    assert report["invalidated"] == 0
    # ファイルは一切変更されない
    records = _read_jsonl(hist)
    assert records == [LEGACY_ACCEPT, ALREADY_EXPLICIT, OPTIMIZE_ACCEPT, LEGACY_REJECT]


def test_apply_invalidates_matching_records_only(tmp_path):
    hist = tmp_path / "history.jsonl"
    _write_jsonl(hist, [LEGACY_ACCEPT, ALREADY_EXPLICIT, OPTIMIZE_ACCEPT, LEGACY_REJECT])

    report = lam.invalidate_legacy_accepts(hist, dry_run=False)

    assert report["dry_run"] is False
    assert report["invalidated"] == 1
    records = _read_jsonl(hist)
    by_id = {r["id"]: r for r in records}
    target = by_id["evolve_diff_legacy1"]
    assert target["fitness_eligible"] is False
    assert target["invalidation_reason"] == "legacy_hash_proxy_false_positive"
    assert "invalidated_at" in target
    # 他は無傷
    assert "fitness_eligible" not in by_id["evolve_diff_new1"]
    assert "fitness_eligible" not in by_id["opt_1"]
    assert "fitness_eligible" not in by_id["evolve_diff_legacy_reject"]


def test_apply_is_idempotent_on_second_run(tmp_path):
    hist = tmp_path / "history.jsonl"
    _write_jsonl(hist, [LEGACY_ACCEPT])
    lam.invalidate_legacy_accepts(hist, dry_run=False)

    report2 = lam.invalidate_legacy_accepts(hist, dry_run=False)

    assert report2["candidates"] == []  # 既に無効化済みは候補に出ない
    assert report2["invalidated"] == 0
    records = _read_jsonl(hist)
    assert len(records) == 1  # 二重書きされない


def test_malformed_json_line_is_preserved_verbatim(tmp_path):
    hist = tmp_path / "history.jsonl"
    _write_jsonl(hist, [LEGACY_ACCEPT, "{ not json"])

    lam.invalidate_legacy_accepts(hist, dry_run=False)

    lines = hist.read_text(encoding="utf-8").splitlines()
    assert "{ not json" in lines  # 壊れた行は無関係な migration で消さない


def test_main_dry_run_default_does_not_write(tmp_path, monkeypatch, capsys):
    hist = tmp_path / "history.jsonl"
    _write_jsonl(hist, [LEGACY_ACCEPT])
    monkeypatch.setattr(sys, "argv", ["prog", "--history-file", str(hist)])

    assert lam.main() == 0

    out = capsys.readouterr().out
    assert "候補: 1 件" in out
    assert "無効化: 0 件" in out
    records = _read_jsonl(hist)
    assert "fitness_eligible" not in records[0]


def test_main_apply_writes(tmp_path, monkeypatch, capsys):
    hist = tmp_path / "history.jsonl"
    _write_jsonl(hist, [LEGACY_ACCEPT])
    monkeypatch.setattr(sys, "argv", ["prog", "--history-file", str(hist), "--apply"])

    assert lam.main() == 0

    out = capsys.readouterr().out
    assert "無効化: 1 件" in out
    records = _read_jsonl(hist)
    assert records[0]["fitness_eligible"] is False
