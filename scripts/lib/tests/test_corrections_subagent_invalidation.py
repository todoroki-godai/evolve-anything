"""corrections_subagent_invalidation.py のテスト（ADR-054 Phase A3 縮小版）。

ADR-054 §2.2 実測: llm_judge 336件中33件が weak_signal_provenance.source_path に
`/subagents/` を含み、うち2件が promoted=True で corrections.jsonl まで到達していた
（subagent 由来の出力を人間発話として誤検出した FP）。§414 の決定論基準
（source_path に /subagents/ を含む）を llm_judge channel の corrections レコードに適用し、
既存の `invalidated` フラグ（安全弁③・provenance_weight.is_human_correction / growth_report
が既に除外条件として読む）で論理無効化する。物理削除はしない。

dry-run 既定（安全側）。rephrase channel の同種汚染（6件・A2 適用後の残存）は本 migration の
対象外（ADR §5/§7.1 の「corrections 昇格済み2件」＝llm_judge 起源の2件のみを指す）。

すべて LLM-free・決定論。
"""
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import corrections_subagent_invalidation as csi  # noqa: E402


def _write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        (json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else r) + "\n"
        for r in records
    )
    path.write_text(body, encoding="utf-8")


def _read_jsonl(path: Path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


SUBAGENT_LLM_JUDGE = {
    "source": "reflect_confirmed",
    "correction_type": "semantic_idiom",
    "weak_signal_key": "836826fb11c47e48",
    "weak_signal_channel": "llm_judge",
    "weak_signal_provenance": {
        "source_path": "/Users/x/.claude/projects/-p/s1/subagents/agent-abc.jsonl",
        "line_no": 38,
    },
    "invalidated": False,
}
NORMAL_LLM_JUDGE = {
    "source": "reflect_confirmed",
    "correction_type": "semantic_idiom",
    "weak_signal_key": "aaa111",
    "weak_signal_channel": "llm_judge",
    "weak_signal_provenance": {
        "source_path": "/Users/x/.claude/projects/-p/s1/s1.jsonl",
        "line_no": 5,
    },
    "invalidated": False,
}
SUBAGENT_REPHRASE = {
    # rephrase channel の /subagents/ 汚染は本 migration の対象外（scope 縮小・ADR §7.1）
    "source": "reflect_confirmed",
    "correction_type": "semantic_idiom",
    "weak_signal_key": "bbb222",
    "weak_signal_channel": "rephrase",
    "weak_signal_provenance": {
        "source_path": "/Users/x/.claude/projects/-p/s1/subagents/agent-def.jsonl",
        "line_no": 1,
    },
    "invalidated": False,
}
ALREADY_INVALIDATED = {
    "source": "reflect_confirmed",
    "correction_type": "semantic_idiom",
    "weak_signal_key": "ccc333",
    "weak_signal_channel": "llm_judge",
    "weak_signal_provenance": {
        "source_path": "/Users/x/.claude/projects/-p/s1/subagents/agent-ghi.jsonl",
        "line_no": 9,
    },
    "invalidated": True,
}


def test_missing_corrections_file_returns_empty_report(tmp_path):
    report = csi.invalidate_subagent_llm_judge_corrections(tmp_path / "nope.jsonl")
    assert report["candidates"] == []
    assert report["invalidated"] == 0


def test_dry_run_default_reports_candidates_without_writing(tmp_path):
    corr = tmp_path / "corrections.jsonl"
    _write_jsonl(corr, [SUBAGENT_LLM_JUDGE, NORMAL_LLM_JUDGE, SUBAGENT_REPHRASE, ALREADY_INVALIDATED])

    report = csi.invalidate_subagent_llm_judge_corrections(corr)  # dry_run 既定

    assert report["dry_run"] is True
    assert report["candidates"] == ["836826fb11c47e48"]
    assert report["invalidated"] == 0
    records = _read_jsonl(corr)
    assert records == [SUBAGENT_LLM_JUDGE, NORMAL_LLM_JUDGE, SUBAGENT_REPHRASE, ALREADY_INVALIDATED]


def test_apply_invalidates_matching_records_only(tmp_path):
    corr = tmp_path / "corrections.jsonl"
    _write_jsonl(corr, [SUBAGENT_LLM_JUDGE, NORMAL_LLM_JUDGE, SUBAGENT_REPHRASE, ALREADY_INVALIDATED])

    report = csi.invalidate_subagent_llm_judge_corrections(corr, dry_run=False)

    assert report["dry_run"] is False
    assert report["invalidated"] == 1
    records = _read_jsonl(corr)
    by_key = {r["weak_signal_key"]: r for r in records}
    target = by_key["836826fb11c47e48"]
    assert target["invalidated"] is True
    assert target["invalidation_reason"] == "adr054_a3_subagent_contamination"
    assert "invalidated_at" in target
    # rephrase channel（対象外）・通常 llm_judge・既invalidated は無傷
    assert by_key["bbb222"]["invalidated"] is False
    assert by_key["aaa111"]["invalidated"] is False
    assert by_key["ccc333"].get("invalidation_reason") is None  # 既存の invalidated=True は上書きしない


def test_apply_is_idempotent_on_second_run(tmp_path):
    corr = tmp_path / "corrections.jsonl"
    _write_jsonl(corr, [SUBAGENT_LLM_JUDGE])
    csi.invalidate_subagent_llm_judge_corrections(corr, dry_run=False)

    report2 = csi.invalidate_subagent_llm_judge_corrections(corr, dry_run=False)

    assert report2["candidates"] == []  # 既に invalidated=True は候補に出ない
    assert report2["invalidated"] == 0
    records = _read_jsonl(corr)
    assert len(records) == 1  # 二重書きされない


def test_malformed_json_line_is_preserved_verbatim(tmp_path):
    corr = tmp_path / "corrections.jsonl"
    _write_jsonl(corr, [SUBAGENT_LLM_JUDGE, "{ not json"])

    csi.invalidate_subagent_llm_judge_corrections(corr, dry_run=False)

    lines = corr.read_text(encoding="utf-8").splitlines()
    assert "{ not json" in lines  # 壊れた行は migration で消さない


def test_missing_provenance_or_channel_is_not_a_candidate(tmp_path):
    corr = tmp_path / "corrections.jsonl"
    no_prov = {"source": "reflect_confirmed", "weak_signal_key": "z1", "weak_signal_channel": "llm_judge"}
    no_channel = {
        "source": "reflect_confirmed", "weak_signal_key": "z2",
        "weak_signal_provenance": {"source_path": "/a/subagents/b.jsonl"},
    }
    _write_jsonl(corr, [no_prov, no_channel])

    report = csi.invalidate_subagent_llm_judge_corrections(corr, dry_run=False)

    assert report["candidates"] == []
    assert report["invalidated"] == 0


def test_invalidated_record_excluded_from_count_human_corrections(tmp_path):
    """既存 reader（provenance_weight.count_human_corrections）が invalidate 後を正しく除外する。"""
    from correction_semantic.provenance_weight import count_human_corrections

    corr = tmp_path / "corrections.jsonl"
    _write_jsonl(corr, [SUBAGENT_LLM_JUDGE, NORMAL_LLM_JUDGE])
    before = count_human_corrections(_read_jsonl(corr))
    assert before == 2

    csi.invalidate_subagent_llm_judge_corrections(corr, dry_run=False)

    after = count_human_corrections(_read_jsonl(corr))
    assert after == 1


def test_main_dry_run_default_does_not_write(tmp_path, monkeypatch, capsys):
    corr = tmp_path / "corrections.jsonl"
    _write_jsonl(corr, [SUBAGENT_LLM_JUDGE])
    monkeypatch.setattr(sys, "argv", ["prog", "--corrections-file", str(corr)])

    assert csi.main() == 0

    out = capsys.readouterr().out
    assert "候補: 1 件" in out
    assert "無効化: 0 件" in out
    records = _read_jsonl(corr)
    assert records[0]["invalidated"] is False


def test_main_apply_writes(tmp_path, monkeypatch, capsys):
    corr = tmp_path / "corrections.jsonl"
    _write_jsonl(corr, [SUBAGENT_LLM_JUDGE])
    monkeypatch.setattr(sys, "argv", ["prog", "--corrections-file", str(corr), "--apply"])

    assert csi.main() == 0

    out = capsys.readouterr().out
    assert "無効化: 1 件" in out
    records = _read_jsonl(corr)
    assert records[0]["invalidated"] is True
