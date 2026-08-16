"""load_claude_reflect_data の入力集合テスト（#475 §5.1）。

pending + promoted を拾う（promoted を落とすと「いまは反映しない」を選んだ保留が
何件たまっても evolve が /reflect 実行を提案しなくなる — P3/P4 の穴の再発）。
"""
import json

from discover import suppression


def _write_corrections(tmp_path, records):
    filepath = tmp_path / "corrections.jsonl"
    filepath.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return filepath


def test_pending_and_promoted_are_counted(tmp_path, monkeypatch):
    records = [
        {"message": "a", "reflect_status": "pending"},
        {"message": "b", "reflect_status": "promoted"},
        {"message": "c", "reflect_status": "applied"},
        {"message": "d", "reflect_status": "skipped"},
    ]
    _write_corrections(tmp_path, records)
    monkeypatch.setattr("discover.DATA_DIR", tmp_path)

    result = suppression.load_claude_reflect_data()

    assert {r["message"] for r in result} == {"a", "b"}


def test_missing_reflect_status_defaults_to_pending(tmp_path, monkeypatch):
    records = [{"message": "no-status-field"}]
    _write_corrections(tmp_path, records)
    monkeypatch.setattr("discover.DATA_DIR", tmp_path)

    result = suppression.load_claude_reflect_data()

    assert len(result) == 1


def test_no_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("discover.DATA_DIR", tmp_path)
    assert suppression.load_claude_reflect_data() == []
