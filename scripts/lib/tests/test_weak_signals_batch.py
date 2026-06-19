"""weak_signals.batch のオーケストレーションテスト（#432）。

決定論・LLM 非依存。実環境のデータソース（errors.jsonl / transcript / utterances.db）は
全て引数注入で tmp / 合成データに差し替える。dry-run で DATA_DIR / store への書き込みが
ゼロであることを E2E で assert する（pitfall_dryrun_stateful_store_write）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from weak_signals import batch as ws_batch  # noqa: E402


def _make_corpus(tmp_path: Path) -> dict:
    """errors.jsonl + transcript + utterances（合成）を tmp に用意する。"""
    errors = tmp_path / "errors.jsonl"
    with open(errors, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "permission_denied", "tool_name": "Bash",
                            "tool_input_summary": "x", "denial_reason": "deny",
                            "session_id": "s1"}) + "\n")

    projects_root = tmp_path / "projects"
    pj_dir = projects_root / "-Users-x-evolve-anything"
    pj_dir.mkdir(parents=True)
    tp = pj_dir / "session.jsonl"
    with open(tp, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "sessionId": "s1", "message": {"role": "user",
                "content": [{"type": "text", "text": "[Request interrupted by user]"}]}}) + "\n")
        f.write(json.dumps({"type": "user", "sessionId": "s1", "message": {"role": "user",
                "content": [{"type": "tool_result", "is_error": True,
                "content": [{"type": "text", "text": "<tool_use_error>File has been modified "
                            "since read, either by the user or by a linter.</tool_use_error>"}]}]}}) + "\n")

    utterances = [
        {"session_id": "s1", "line_no": 1, "text": "prod まで動作確認してほしい",
         "source_path": str(tp), "pj_slug": "evolve-anything"},
        {"session_id": "s1", "line_no": 2, "text": "prod まで動作確認してほしい",
         "source_path": str(tp), "pj_slug": "evolve-anything"},
    ]
    return {"errors_path": errors, "projects_root": projects_root, "utterances": utterances}


def test_run_batch_detects_all_four_channels(tmp_path: Path) -> None:
    corpus = _make_corpus(tmp_path)
    store = tmp_path / "weak_signals.jsonl"
    res = ws_batch.run_batch(
        "evolve-anything",
        store_path=store,
        errors_path=corpus["errors_path"],
        projects_root=corpus["projects_root"],
        utterances=corpus["utterances"],
    )
    detected = res["detected"]
    assert detected.get("permission_deny") == 1
    assert detected.get("esc_interrupt") == 1
    assert detected.get("manual_edit_after_ai") == 1
    assert detected.get("rephrase") == 1
    assert res["total"] == 4
    assert res["written"] == 4
    # store に 4 件書かれている
    lines = [l for l in store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 4


def test_run_batch_dry_run_writes_nothing(tmp_path: Path) -> None:
    """dry-run E2E: 検出はするが store ファイルを一切作らない（書き込みゼロ）。"""
    corpus = _make_corpus(tmp_path)
    store = tmp_path / "weak_signals.jsonl"
    res = ws_batch.run_batch(
        "evolve-anything",
        dry_run=True,
        store_path=store,
        errors_path=corpus["errors_path"],
        projects_root=corpus["projects_root"],
        utterances=corpus["utterances"],
    )
    assert res["dry_run"] is True
    assert res["total"] == 4  # 検出は走る
    assert res["written"] == 4  # 「書くはずだった」件数
    # 実ファイルは作られない
    assert not store.exists()


def test_run_batch_dedup_on_rerun(tmp_path: Path) -> None:
    """2 回目の実行で同一シグナルは dedup される（store は増えない）。"""
    corpus = _make_corpus(tmp_path)
    store = tmp_path / "weak_signals.jsonl"
    ws_batch.run_batch("evolve-anything", store_path=store,
                       errors_path=corpus["errors_path"],
                       projects_root=corpus["projects_root"],
                       utterances=corpus["utterances"])
    res2 = ws_batch.run_batch("evolve-anything", store_path=store,
                             errors_path=corpus["errors_path"],
                             projects_root=corpus["projects_root"],
                             utterances=corpus["utterances"])
    assert res2["written"] == 0
    assert res2["skipped_dup"] == 4
    lines = [l for l in store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 4


def test_channel_counts() -> None:
    from weak_signals.store import WeakSignal
    sigs = [
        WeakSignal("rephrase", {"a": 1}, "t", "s", "p"),
        WeakSignal("rephrase", {"a": 2}, "t", "s", "p"),
        WeakSignal("esc_interrupt", {"a": 3}, "t", "s", "p"),
    ]
    counts = ws_batch.channel_counts(sigs)
    assert counts == {"rephrase": 2, "esc_interrupt": 1}
