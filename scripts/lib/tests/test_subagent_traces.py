"""subagent_traces（subagent 内部軌跡ストア）テスト — #38。

親セッションの error_count しか見ない既存 outcome 帰属の盲点（subagent が内部で error
連発しても最終成功すれば一発成功と誤記録）を塞ぐため、subagent transcript の
tool_use / tool_result / is_error 列をパースして per-agent_type で advisory 集計する。

決定論・ゼロ LLM。read は書込を一切しない（dry-run 純度）。write は store_write barrier
（ADR-049）経由。pj_slug スコープ。HOME 隔離（#457）は autouse fixture で isolate_home。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from test_home_isolation import isolate_home  # noqa: E402

import rl_common  # noqa: E402
from subagent_traces import extractor as _ext  # noqa: E402
from subagent_traces import ingest as _ingest  # noqa: E402
from subagent_traces import query as _query  # noqa: E402
from subagent_traces import store as _tstore  # noqa: E402


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    isolate_home(monkeypatch, tmp_path)


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    """DATA_DIR を tmp に向ける（read 側 store.DATA_DIR + write 側 rl_common.DATA_DIR）。"""
    d = tmp_path / "evolve-anything"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_tstore, "DATA_DIR", d)
    monkeypatch.setattr(rl_common, "DATA_DIR", d)
    monkeypatch.delenv("EVOLVE_WRITE_GUARD", raising=False)
    return d


def _write_transcript(path: Path, lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(ln) for ln in lines) + "\n", encoding="utf-8"
    )


def _assistant_line(blocks: list) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


# ─────────────────────────── extractor ───────────────────────────

def test_extract_trace_counts_tool_use_result_error(tmp_path):
    """tool_use / tool_result / is_error / text を正しく数える。"""
    t = tmp_path / "tr.jsonl"
    _write_transcript(t, [
        _assistant_line([
            {"type": "text", "text": "やります"},
            {"type": "tool_use", "id": "1", "name": "Bash", "input": {}},
        ]),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": "boom", "is_error": True},
        ]}},
        _assistant_line([
            {"type": "tool_use", "id": "2", "name": "Edit", "input": {}},
        ]),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "2", "content": "ok", "is_error": False},
        ]}},
    ])
    tr = _ext.extract_trace(t)
    assert tr is not None
    assert tr["tool_use_count"] == 2
    assert tr["tool_result_count"] == 2
    assert tr["tool_error_count"] == 1
    assert tr["text_block_count"] == 1
    assert tr["tools"] == {"Bash": 1, "Edit": 1}


def test_extract_trace_first_try_success_when_no_error(tmp_path):
    """エラーなし transcript は first_try_success=True。"""
    t = tmp_path / "ok.jsonl"
    _write_transcript(t, [
        _assistant_line([{"type": "tool_use", "id": "1", "name": "Read", "input": {}}]),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": "x", "is_error": False},
        ]}},
    ])
    tr = _ext.extract_trace(t)
    assert tr["tool_error_count"] == 0
    assert tr["first_try_success"] is True


def test_extract_trace_first_try_failure_when_error_present(tmp_path):
    """is_error が 1 つでもあれば first_try_success=False（内部リトライ盲点の検出）。"""
    t = tmp_path / "fail.jsonl"
    _write_transcript(t, [
        _assistant_line([{"type": "tool_use", "id": "1", "name": "Bash", "input": {}}]),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": "err", "is_error": True},
        ]}},
    ])
    assert _ext.extract_trace(t)["first_try_success"] is False


def test_extract_trace_ignores_string_content(tmp_path):
    """message.content が str の行は tool 集計対象外（壊れない）。"""
    t = tmp_path / "str.jsonl"
    _write_transcript(t, [
        {"type": "user", "message": {"role": "user", "content": "ただのテキスト"}},
        _assistant_line([{"type": "tool_use", "id": "1", "name": "Bash", "input": {}}]),
    ])
    tr = _ext.extract_trace(t)
    assert tr["tool_use_count"] == 1
    assert tr["text_block_count"] == 0


def test_extract_trace_missing_file_returns_none(tmp_path):
    """存在しないファイルは None。"""
    assert _ext.extract_trace(tmp_path / "nope.jsonl") is None


def test_extract_trace_unparseable_file_returns_none(tmp_path):
    """全行 parse 不能（中身がゴミ）は None。"""
    t = tmp_path / "broken.jsonl"
    t.write_text("not json at all\n{also bad\n", encoding="utf-8")
    assert _ext.extract_trace(t) is None


# ─────────────────────────── store ───────────────────────────

def test_write_then_read_roundtrip_scoped_by_slug(data_dir):
    """write_trace → read_traces が slug スコープで往復する。"""
    _tstore.write_trace({"agent_id": "a1", "pj_slug": "projA", "first_try_success": True})
    _tstore.write_trace({"agent_id": "b1", "pj_slug": "projB", "first_try_success": False})
    got = _tstore.read_traces("projA")
    assert set(got) == {"a1"}
    assert got["a1"]["first_try_success"] is True


def test_read_traces_folds_legacy_slug_alias(data_dir):
    """#112: PJ rename の legacy slug（rl-anything）も canonical slug の read で拾う。"""
    _tstore.write_trace({"agent_id": "a1", "pj_slug": "rl-anything", "first_try_success": True})
    _tstore.write_trace({"agent_id": "b1", "pj_slug": "evolve-anything", "first_try_success": False})
    got = _tstore.read_traces("evolve-anything")
    assert set(got) == {"a1", "b1"}


def test_read_traces_last_append_wins(data_dir):
    """同一 agent_id の再 ingest は last-append-wins。"""
    _tstore.write_trace({"agent_id": "a1", "pj_slug": "p", "tool_error_count": 2})
    _tstore.write_trace({"agent_id": "a1", "pj_slug": "p", "tool_error_count": 0})
    got = _tstore.read_traces("p")
    assert got["a1"]["tool_error_count"] == 0


def test_read_traces_missing_file_does_not_create(data_dir):
    """ファイル不在で read_traces は {} を返し、ファイルを作らない（dry-run 純度）。"""
    assert _tstore.read_traces("p") == {}
    assert not (data_dir / _tstore.STORE_NAME).exists()


def test_write_trace_goes_through_store_write_barrier(data_dir):
    """write_trace は store_write barrier 経由（直接 append しない・ADR-049）。"""
    import importlib
    sw_mod = importlib.import_module("rl_common.store_write")
    with pytest.MonkeyPatch.context() as mp:
        captured = {}

        def fake(name, record, **kw):
            captured["name"] = name
            captured["record"] = record

        mp.setattr(sw_mod, "store_write", fake)
        _tstore.write_trace({"agent_id": "x", "pj_slug": "p"})
    assert captured["name"] == "subagent_traces.jsonl"


def test_read_all_agent_ids_collects_across_slugs(data_dir):
    """read_all_agent_ids は slug を跨いで全 agent_id を返す（dedup 用）。"""
    _tstore.write_trace({"agent_id": "a1", "pj_slug": "x"})
    _tstore.write_trace({"agent_id": "b1", "pj_slug": "y"})
    assert _tstore.read_all_agent_ids() == {"a1", "b1"}


# ─────────────────────────── ingest ───────────────────────────

def _write_subagents(data_dir: Path, records: list) -> None:
    p = data_dir / "subagents.jsonl"
    p.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )


def test_ingest_extracts_and_writes_traces(data_dir, tmp_path):
    """現存 transcript を持つ未 ingest 行だけ軌跡を抽出して書く。"""
    tr = tmp_path / "agent1.jsonl"
    _write_transcript(tr, [
        _assistant_line([{"type": "tool_use", "id": "1", "name": "Bash", "input": {}}]),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": "e", "is_error": True},
        ]}},
    ])
    _write_subagents(data_dir, [{
        "agent_id": "a1", "agent_type": "impl-worker",
        "agent_transcript_path": str(tr), "project": "/home/u/myproj",
        "session_id": "s1", "timestamp": "2026-06-24T00:00:00Z",
    }])
    res = _ingest.ingest_all_projects()
    assert res["ingested"] == 1
    assert res["capped"] is False
    traces = _tstore.read_traces("myproj")
    assert traces["a1"]["first_try_success"] is False
    assert traces["a1"]["agent_type"] == "impl-worker"


def test_ingest_skips_already_ingested(data_dir, tmp_path):
    """既 ingest 済 agent_id は skip（dedup）。"""
    tr = tmp_path / "a.jsonl"
    _write_transcript(tr, [_assistant_line([{"type": "tool_use", "id": "1", "name": "Read", "input": {}}])])
    rec = {"agent_id": "a1", "agent_type": "t", "agent_transcript_path": str(tr), "project": "/p/myproj"}
    _write_subagents(data_dir, [rec])
    assert _ingest.ingest_all_projects()["ingested"] == 1
    res2 = _ingest.ingest_all_projects()
    assert res2["ingested"] == 0
    assert res2["skipped"] == 1


def test_ingest_skips_missing_transcript(data_dir):
    """agent_transcript_path が不在（掃除済み）の行は skip し書かない。"""
    _write_subagents(data_dir, [{
        "agent_id": "a1", "agent_type": "t",
        "agent_transcript_path": "/nonexistent/gone.jsonl", "project": "/p/myproj",
    }])
    res = _ingest.ingest_all_projects()
    assert res["ingested"] == 0
    assert res["skipped"] == 1
    assert _tstore.read_traces("myproj") == {}


def test_ingest_caps_at_max_new_and_surfaces_remaining(data_dir, tmp_path):
    """max_new で打ち切り、capped=True と remaining を返す（沈黙切り捨てしない）。"""
    records = []
    for i in range(5):
        tr = tmp_path / f"a{i}.jsonl"
        _write_transcript(tr, [_assistant_line([{"type": "tool_use", "id": "1", "name": "Read", "input": {}}])])
        records.append({
            "agent_id": f"a{i}", "agent_type": "t",
            "agent_transcript_path": str(tr), "project": "/p/myproj",
        })
    _write_subagents(data_dir, records)
    res = _ingest.ingest_all_projects(max_new=2)
    assert res["ingested"] == 2
    assert res["capped"] is True
    assert res["remaining"] == 3
    # 次回 ingest で残りを消化できる（dedup で二重書きしない）。
    res2 = _ingest.ingest_all_projects(max_new=200)
    assert res2["ingested"] == 3
    assert res2["capped"] is False


def test_ingest_does_not_walk_projects_tree(data_dir, tmp_path, monkeypatch):
    """ingest は agent_transcript_path に名指しされた本だけ読み、projects 全 walk しない。

    rglob を呼んだら fail させ、named transcript のみ読むことを構造的に固定する（runaway 防止）。
    """
    tr = tmp_path / "named.jsonl"
    _write_transcript(tr, [_assistant_line([{"type": "tool_use", "id": "1", "name": "Read", "input": {}}])])
    _write_subagents(data_dir, [{
        "agent_id": "a1", "agent_type": "t",
        "agent_transcript_path": str(tr), "project": "/p/myproj",
    }])

    orig_rglob = Path.rglob

    def boom_rglob(self, pattern, *a, **k):
        raise AssertionError(f"projects 全 walk は禁止: rglob({pattern}) が呼ばれた")

    monkeypatch.setattr(Path, "rglob", boom_rglob)
    try:
        res = _ingest.ingest_all_projects()
    finally:
        monkeypatch.setattr(Path, "rglob", orig_rglob)
    assert res["ingested"] == 1


def test_ingest_no_subagents_file_is_graceful(data_dir):
    """subagents.jsonl が無くても例外を出さず 0 件で返す。"""
    res = _ingest.ingest_all_projects()
    assert res == {"ingested": 0, "skipped": 0, "capped": False, "remaining": 0}


# ─────────────────────────── query ───────────────────────────

def test_per_agent_type_summary_floor_and_rates(data_dir):
    """agent_type 別の一発成功率・平均 tool error を floor ゲート付きで集計する。"""
    # impl-worker: 3 件（2 成功 1 失敗）→ floor 通過、成功率 0.6667。
    _tstore.write_trace({"agent_id": "w1", "pj_slug": "p", "agent_type": "impl-worker",
                         "first_try_success": True, "tool_error_count": 0})
    _tstore.write_trace({"agent_id": "w2", "pj_slug": "p", "agent_type": "impl-worker",
                         "first_try_success": True, "tool_error_count": 0})
    _tstore.write_trace({"agent_id": "w3", "pj_slug": "p", "agent_type": "impl-worker",
                         "first_try_success": False, "tool_error_count": 3})
    # reviewer: 1 件のみ → floor 未満で除外。
    _tstore.write_trace({"agent_id": "r1", "pj_slug": "p", "agent_type": "reviewer",
                         "first_try_success": True, "tool_error_count": 0})
    out = _query.per_agent_type_summary("p", min_traces=3)
    assert len(out) == 1
    s = out[0]
    assert s["agent_type"] == "impl-worker"
    assert s["n"] == 3
    assert s["first_try_success_rate"] == round(2 / 3, 4)
    assert s["avg_tool_error"] == 1.0


def test_per_agent_type_summary_excludes_noise_agent_type(data_dir):
    """空 / ID 形 agent_type は除外する（is_noise_agent_type 単一ソース）。"""
    for i in range(3):
        _tstore.write_trace({"agent_id": f"n{i}", "pj_slug": "p", "agent_type": "",
                             "first_try_success": True, "tool_error_count": 0})
    assert _query.per_agent_type_summary("p", min_traces=1) == []


def test_per_agent_type_summary_empty_when_no_data(data_dir):
    """データが無ければ空リスト。"""
    assert _query.per_agent_type_summary("p") == []


# ─────────────────────────── audit section ───────────────────────────

def test_section_silent_when_no_traces(data_dir, monkeypatch):
    """当 PJ の軌跡が 0 件なら None（沈黙・評価対象なし）。"""
    from audit import sections_subagent_traces as sec
    monkeypatch.setattr(sec, "_slug_for", lambda p: "p")
    assert sec.build_subagent_traces_section(Path("/x/p")) is None


def test_section_emits_data_insufficient_when_below_floor(data_dir, monkeypatch):
    """軌跡はあるが floor 未満なら沈黙でなくデータ不足を明示する（silence != evaluated）。"""
    from audit import sections_subagent_traces as sec
    monkeypatch.setattr(sec, "_slug_for", lambda p: "p")
    _tstore.write_trace({"agent_id": "w1", "pj_slug": "p", "agent_type": "impl-worker",
                         "first_try_success": True, "tool_error_count": 0})
    out = sec.build_subagent_traces_section(Path("/x/p"))
    assert out is not None
    joined = "\n".join(out)
    assert "Subagent Internal Traces" in joined
    assert "データ不足" in joined


def test_section_emits_agent_type_rows(data_dir, monkeypatch):
    """floor を満たす agent_type は内部一発成功率の行を出す。"""
    from audit import sections_subagent_traces as sec
    monkeypatch.setattr(sec, "_slug_for", lambda p: "p")
    for i in range(3):
        _tstore.write_trace({"agent_id": f"w{i}", "pj_slug": "p", "agent_type": "impl-worker",
                             "first_try_success": i != 0, "tool_error_count": 0 if i else 4})
    out = sec.build_subagent_traces_section(Path("/x/p"))
    joined = "\n".join(out)
    assert "impl-worker" in joined
    assert "内部一発成功率" in joined


def test_section_warns_on_low_first_try_success(data_dir, monkeypatch):
    """一発成功率が閾値未満の agent_type は ⚠ を出し、critical に分類される（#76 Finding A）。

    親は最終成功しか見ない盲点（#38）を audit サマリで surface するには、⚠/🔴 が無いと
    report.py の畳み込みで『✓ 評価済みクリーン』に必ず埋没する。低 rate で ⚠ を発火させる。
    """
    from audit import sections_subagent_traces as sec
    from audit.sections_summary import classify_section
    monkeypatch.setattr(sec, "_slug_for", lambda p: "p")
    # general-purpose: 6 件中 1 成功（rate 0.17）= 実 PJ dogfood の figma 相当。
    for i in range(6):
        _tstore.write_trace({"agent_id": f"g{i}", "pj_slug": "p", "agent_type": "general-purpose",
                             "first_try_success": i == 0, "tool_error_count": 0 if i == 0 else 2})
    out = sec.build_subagent_traces_section(Path("/x/p"))
    assert out is not None
    joined = "\n".join(out)
    assert "⚠" in joined
    assert "general-purpose" in joined
    # report.py の分類で full-text 展開される（畳み込みで消えない）。
    assert classify_section(out) == "critical"


def test_section_warns_on_high_avg_tool_error_even_if_rate_ok(data_dir, monkeypatch):
    """rate が閾値以上でも平均 tool error が過多なら ⚠（#76 Finding A・独立ゲート）。

    成功多数の中に『内部で大量リトライしてから成功』する agent が混じるケースを拾う。
    """
    from audit import sections_subagent_traces as sec
    from audit.sections_summary import classify_section
    monkeypatch.setattr(sec, "_slug_for", lambda p: "p")
    # 4 件中 3 成功（rate 0.75 ≥ 0.5）だが 1 件が tool_error 24 → 平均 6.0 ≥ 5。
    for i in range(3):
        _tstore.write_trace({"agent_id": f"ok{i}", "pj_slug": "p", "agent_type": "retry-heavy",
                             "first_try_success": True, "tool_error_count": 0})
    _tstore.write_trace({"agent_id": "bad", "pj_slug": "p", "agent_type": "retry-heavy",
                         "first_try_success": False, "tool_error_count": 24})
    out = sec.build_subagent_traces_section(Path("/x/p"))
    joined = "\n".join(out)
    assert "⚠" in joined
    assert classify_section(out) == "critical"


def test_section_no_warning_for_healthy_rates(data_dir, monkeypatch):
    """rate ≥ 閾値 かつ tool error 少なら ⚠ を出さず clean のまま（FP 抑制）。"""
    from audit import sections_subagent_traces as sec
    from audit.sections_summary import classify_section
    monkeypatch.setattr(sec, "_slug_for", lambda p: "p")
    for i in range(4):
        _tstore.write_trace({"agent_id": f"s{i}", "pj_slug": "p", "agent_type": "senpai",
                             "first_try_success": True, "tool_error_count": 0})
    out = sec.build_subagent_traces_section(Path("/x/p"))
    joined = "\n".join(out)
    assert "⚠" not in joined
    assert classify_section(out) == "clean"


def test_section_borderline_half_rate_not_flagged(data_dir, monkeypatch):
    """一発成功率ちょうど 0.50（境界・実 PJ の Explore 相当）は ⚠ を出さない（strict <）。"""
    from audit import sections_subagent_traces as sec
    from audit.sections_summary import classify_section
    monkeypatch.setattr(sec, "_slug_for", lambda p: "p")
    # 4 件中 2 成功（rate 0.50）、失敗側 tool_error は 1 で過多でない。
    for i in range(4):
        _tstore.write_trace({"agent_id": f"e{i}", "pj_slug": "p", "agent_type": "Explore",
                             "first_try_success": i < 2, "tool_error_count": 0 if i < 2 else 1})
    out = sec.build_subagent_traces_section(Path("/x/p"))
    joined = "\n".join(out)
    assert "⚠" not in joined
    assert classify_section(out) == "clean"
