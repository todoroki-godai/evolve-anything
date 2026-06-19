"""correction_semantic.store のテスト（#431 個人辞書 + 判定進捗）。

個人辞書（correction_idioms.jsonl）への provenance 付き追記・dedup・dry-run ゼロ書込、
および判定済み発話の物理キー進捗を検証する。決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import store as cs_store  # noqa: E402


def _idiom(text="四国めたんじゃなくて", source_path="/a.jsonl", line_no=1):
    return cs_store.CorrectionIdiom(
        idiom=text,
        provenance={"source_path": source_path, "line_no": line_no,
                    "session_id": "s1", "reason": "正しい値の後置型"},
        detected_at="2026-06-10T00:00:00+00:00",
        pj_slug="evolve-anything",
    )


# ── utterance_key（判定進捗の物理キー） ──────────────────────────────


def test_utterance_key_uses_physical_pk() -> None:
    u = {"source_path": "/x.jsonl", "line_no": 42, "text": "foo"}
    assert cs_store.utterance_key(u) == "/x.jsonl:42"


def test_utterance_key_stable() -> None:
    u1 = {"source_path": "/x.jsonl", "line_no": 42}
    u2 = {"source_path": "/x.jsonl", "line_no": 42}
    assert cs_store.utterance_key(u1) == cs_store.utterance_key(u2)


# ── 個人辞書 append/read ───────────────────────────────────────────


def test_append_writes_idiom(tmp_path: Path) -> None:
    store = tmp_path / "correction_idioms.jsonl"
    res = cs_store.append_idioms([_idiom()], path=store)
    assert res["written"] == 1
    lines = [l for l in store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    assert "四国めたん" in lines[0]


def test_append_dry_run_writes_nothing(tmp_path: Path) -> None:
    store = tmp_path / "correction_idioms.jsonl"
    res = cs_store.append_idioms([_idiom()], path=store, dry_run=True)
    assert res["dry_run"] is True
    assert res["written"] == 1  # 書くはずだった件数
    assert not store.exists()


def test_append_dedup_on_rerun(tmp_path: Path) -> None:
    store = tmp_path / "correction_idioms.jsonl"
    cs_store.append_idioms([_idiom()], path=store)
    res2 = cs_store.append_idioms([_idiom()], path=store)
    assert res2["written"] == 0
    assert res2["skipped_dup"] == 1
    lines = [l for l in store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1


def test_read_idioms_empty_when_missing(tmp_path: Path) -> None:
    assert cs_store.read_idioms(tmp_path / "nope.jsonl") == []


# ── 判定進捗（judged keys） ────────────────────────────────────────


def test_record_and_read_judged_keys(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1", "/a.jsonl:2"], path=prog)
    judged = cs_store.read_judged_keys(prog)
    assert judged == {"/a.jsonl:1", "/a.jsonl:2"}


def test_record_judged_dry_run_writes_nothing(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1"], path=prog, dry_run=True)
    assert not prog.exists()


def test_judged_keys_dedup_across_runs(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1"], path=prog)
    cs_store.record_judged(["/a.jsonl:1", "/a.jsonl:2"], path=prog)
    assert cs_store.read_judged_keys(prog) == {"/a.jsonl:1", "/a.jsonl:2"}


def test_filter_unjudged(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1"], path=prog)
    utterances = [
        {"source_path": "/a.jsonl", "line_no": 1, "text": "old"},
        {"source_path": "/a.jsonl", "line_no": 2, "text": "new"},
    ]
    unjudged = cs_store.filter_unjudged(utterances, judged_keys=cs_store.read_judged_keys(prog))
    assert len(unjudged) == 1
    assert unjudged[0]["line_no"] == 2
