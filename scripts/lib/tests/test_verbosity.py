"""verbosity（回答冗長性の学習ループ）テスト — #75。

standalone ~/.claude/verbosity の仕組みを evolve-anything に統合した移植先を検証する。
決定論・ゼロ LLM: judge の Haiku 呼び出し（call_haiku）は **必ず mock**（no-llm-in-tests）。
read は書込を一切しない（read-only 純度）。write は store_write barrier（ADR-049）経由。
pj_slug スコープ。HOME 隔離（#457）は scripts/lib/tests/conftest の autouse fixture。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import rl_common  # noqa: E402
from verbosity import judge as _judge  # noqa: E402
from verbosity import query as _q  # noqa: E402
from verbosity import store as _vstore  # noqa: E402
from audit.sections_verbosity import build_verbosity_section  # noqa: E402

SLUG = "evolve-anything"


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    """DATA_DIR を tmp に向ける（read 側 store.DATA_DIR + write 側 rl_common.DATA_DIR）。"""
    d = tmp_path / "evolve-anything"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_vstore, "DATA_DIR", d)
    monkeypatch.setattr(rl_common, "DATA_DIR", d)
    monkeypatch.delenv("EVOLVE_WRITE_GUARD", raising=False)
    return d


def _write_candidates(d: Path, recs: list) -> None:
    """テスト用に候補レコードを直接 jsonl に書く（hook の書込先を模す）。"""
    path = d / _vstore.CANDIDATES_STORE
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
        encoding="utf-8",
    )


def _cand(h: str, *, slug: str = SLUG, project: str = "evolve-anything", chars: int = 1200) -> dict:
    return {
        "ts": "2026-06-24T00:00:00+00:00",
        "session_id": "s1",
        "pj_slug": slug,
        "project": project,
        "cwd": f"/x/{project}",
        "char_len": chars,
        "line_count": 10,
        "hash": h,
        "text": "長い応答 " * 50,
    }


# ───────────────────────────── store ─────────────────────────────

def test_read_candidates_filters_by_slug_and_dedups_hash(data_dir):
    """pj_slug でフィルタし、同一 hash は dedup する。"""
    _write_candidates(data_dir, [
        _cand("aaa"),
        _cand("aaa"),  # 重複 hash
        _cand("bbb"),
        _cand("ccc", slug="other-pj"),  # 別 PJ
    ])
    out = _vstore.read_candidates(SLUG, data_dir=data_dir)
    assert {c["hash"] for c in out} == {"aaa", "bbb"}


def test_read_candidates_folds_legacy_slug_alias(data_dir):
    """#112: PJ rename の legacy slug（rl-anything）も canonical slug の read で拾う。"""
    _write_candidates(data_dir, [
        _cand("aaa", slug="evolve-anything"),
        _cand("bbb", slug="rl-anything"),  # legacy
    ])
    out = _vstore.read_candidates(SLUG, data_dir=data_dir)
    assert {c["hash"] for c in out} == {"aaa", "bbb"}


def test_read_candidates_missing_file_returns_empty_and_writes_nothing(data_dir):
    """ファイル不在でも [] を返し、ファイルを作らない（read-only 純度）。"""
    out = _vstore.read_candidates(SLUG, data_dir=data_dir)
    assert out == []
    assert not (data_dir / _vstore.CANDIDATES_STORE).exists()


def test_write_verdict_goes_through_store_write_barrier(data_dir):
    """write_verdict は store_write("verbosity_verdicts.jsonl") 経由（場所は内部解決）。

    ``rl_common.store_write`` 属性は再エクスポートされた *関数* に解決されるため、
    mock ターゲットは importlib でモジュールを取得してから setattr する（#38 と同型）。
    """
    import importlib
    sw_mod = importlib.import_module("rl_common.store_write")
    captured: dict = {}

    def fake(name, record, **kw):
        captured["name"] = name
        captured["record"] = record

    with mock.patch.object(sw_mod, "store_write", fake):
        _vstore.write_verdict({"hash": "aaa", "pj_slug": SLUG, "verbose": True})
    assert captured["name"] == _vstore.VERDICTS_STORE


def test_read_verdicts_last_append_wins(data_dir):
    """同一 hash は append 順で last-append-wins（再判定の上書き）。"""
    path = data_dir / _vstore.VERDICTS_STORE
    path.write_text(
        "\n".join(json.dumps(r) for r in [
            {"hash": "aaa", "pj_slug": SLUG, "verbose": False},
            {"hash": "aaa", "pj_slug": SLUG, "verbose": True},
        ]) + "\n",
        encoding="utf-8",
    )
    v = _vstore.read_verdicts(SLUG, data_dir=data_dir)
    assert v["aaa"]["verbose"] is True


def test_read_verdicts_folds_legacy_slug_alias(data_dir):
    """#112: PJ rename の legacy slug（rl-anything）も canonical slug の read で拾う。"""
    path = data_dir / _vstore.VERDICTS_STORE
    path.write_text(
        "\n".join(json.dumps(r) for r in [
            {"hash": "aaa", "pj_slug": "evolve-anything", "verbose": True},
            {"hash": "bbb", "pj_slug": "rl-anything", "verbose": False},  # legacy
        ]) + "\n",
        encoding="utf-8",
    )
    v = _vstore.read_verdicts(SLUG, data_dir=data_dir)
    assert set(v) == {"aaa", "bbb"}


# ───────────────────────────── query ─────────────────────────────

def _write_verdicts(d: Path, recs: list) -> None:
    path = d / _vstore.VERDICTS_STORE
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
        encoding="utf-8",
    )


def test_verbosity_summary_rate_and_patterns(data_dir):
    """判定済みが floor 以上なら冗長率とパターン Top-N を集計する。"""
    _write_candidates(data_dir, [_cand("h1"), _cand("h2"), _cand("h3"), _cand("h4")])
    _write_verdicts(data_dir, [
        {"hash": "h1", "pj_slug": SLUG, "verbose": True, "patterns": ["preamble", "filler"]},
        {"hash": "h2", "pj_slug": SLUG, "verbose": True, "patterns": ["preamble"]},
        {"hash": "h3", "pj_slug": SLUG, "verbose": False, "patterns": []},
    ])
    s = _q.verbosity_summary(SLUG, data_dir=data_dir)
    assert s["candidates"] == 4
    assert s["judged"] == 3
    assert s["pending"] == 1
    assert s["verbose"] == 2
    assert s["verbose_rate"] == pytest.approx(2 / 3, abs=1e-3)
    # preamble が最多。
    assert s["patterns"][0]["pattern"] == "preamble"
    assert s["patterns"][0]["count"] == 2


def test_verbosity_summary_floor_gate_hides_rate(data_dir):
    """判定済みが floor 未満なら verbose_rate は None（不足を明示）。"""
    _write_candidates(data_dir, [_cand("h1"), _cand("h2")])
    _write_verdicts(data_dir, [
        {"hash": "h1", "pj_slug": SLUG, "verbose": True, "patterns": ["preamble"]},
    ])
    s = _q.verbosity_summary(SLUG, data_dir=data_dir)
    assert s["verbose_rate"] is None
    assert s["judged"] == 1


# ───────────────────────────── judge: dry-run ─────────────────────

def test_judge_dryrun_does_not_call_llm_or_write(data_dir, capsys):
    """dry-run は call_haiku を呼ばず、1 バイトも書かない（コストだけ print）。"""
    _write_candidates(data_dir, [_cand("h1"), _cand("h2")])
    before = sorted(p.name for p in data_dir.iterdir())

    with mock.patch.object(_judge, "call_haiku") as m_call:
        res = _judge.run_judge(SLUG, run=False, batch_size=6, data_dir=data_dir)

    m_call.assert_not_called()
    assert res["dry_run"] is True
    assert res["pending"] == 2
    assert res["cost"]["batches"] == 1
    # 書込ゼロ: verdicts も weak_signals も新規作成されない。
    assert sorted(p.name for p in data_dir.iterdir()) == before
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "--run" in out


# ────────────── parse_json_array_result: 空とパース失敗の区別（#273）──────────

def test_parse_json_array_result_ok_true_on_valid_array() -> None:
    raw = json.dumps([{"i": 0, "verbose": True}])
    result = _judge.parse_json_array_result(raw)
    assert result["ok"] is True
    assert len(result["items"]) == 1


def test_parse_json_array_result_ok_false_on_malformed() -> None:
    result = _judge.parse_json_array_result("not json at all {{{")
    assert result["ok"] is False
    assert result["items"] == []


def test_parse_json_array_result_ok_false_on_empty_response() -> None:
    assert _judge.parse_json_array_result("")["ok"] is False
    assert _judge.parse_json_array_result(None)["ok"] is False


def test_parse_json_array_backward_compat_delegates_to_result() -> None:
    raw = json.dumps([{"i": 0, "verbose": True}])
    assert _judge.parse_json_array(raw) == _judge.parse_json_array_result(raw)["items"]


# ── P1-2（codex 指摘）: 意味的に壊れた要素・部分応答の厳格検証 ─────────────


def test_parse_json_array_result_ok_false_on_string_index() -> None:
    """i が文字列型（"0"）は不正 — 型違いを黙って通さない。"""
    raw = json.dumps([{"i": "0", "verbose": True}])
    result = _judge.parse_json_array_result(raw)
    assert result["ok"] is False
    assert result["items"] == []


def test_parse_json_array_result_ok_false_on_string_verbose() -> None:
    """verbose が文字列 "false" は bool("false")==True の罠を踏まず不正として弾く。"""
    raw = json.dumps([{"i": 0, "verbose": "false"}])
    result = _judge.parse_json_array_result(raw)
    assert result["ok"] is False


def test_parse_json_array_result_ok_false_on_duplicate_index() -> None:
    raw = json.dumps([{"i": 0, "verbose": True}, {"i": 0, "verbose": False}])
    assert _judge.parse_json_array_result(raw)["ok"] is False


def test_parse_json_array_result_expected_len_requires_full_coverage() -> None:
    """expected_len=3 で i={0,1} しか無い（部分応答）は網羅性不足として ok=False。"""
    raw = json.dumps([{"i": 0, "verbose": True}, {"i": 1, "verbose": False}])
    result = _judge.parse_json_array_result(raw, expected_len=3)
    assert result["ok"] is False
    assert result["items"] == []


def test_parse_json_array_result_expected_len_ok_when_full_coverage() -> None:
    raw = json.dumps([{"i": 0, "verbose": True}, {"i": 1, "verbose": False}])
    result = _judge.parse_json_array_result(raw, expected_len=2)
    assert result["ok"] is True
    assert len(result["items"]) == 2


def test_parse_json_array_result_expected_len_ok_false_on_empty_array() -> None:
    """非空バッチに空配列が返るのは網羅性を満たさないので不正（該当なしの明示ではない）。"""
    result = _judge.parse_json_array_result("[]", expected_len=3)
    assert result["ok"] is False


def test_parse_json_array_result_legitimate_empty_when_no_expected_len() -> None:
    """expected_len 無しの直接呼び出しは、空配列を従来どおり許容する（単体利用向け）。"""
    result = _judge.parse_json_array_result("[]")
    assert result["ok"] is True
    assert result["items"] == []


# ───────────────────────────── judge: --run (mock) ───────────────

def test_judge_run_persists_verdicts_and_emits_weak_signals(data_dir, capsys):
    """--run は Haiku（mock）判定を verdicts へ永続化し、verbose を weak_signals へ emit する。"""
    _write_candidates(data_dir, [_cand("h1"), _cand("h2")])
    weak_path = data_dir / "weak_signals.jsonl"

    fake = json.dumps([
        {"i": 0, "verbose": True, "patterns": ["preamble", "filler"], "note": "前置きが冗長"},
        {"i": 1, "verbose": False, "patterns": [], "note": ""},
    ])
    with mock.patch.object(_judge, "call_haiku", return_value=fake) as m_call:
        res = _judge.run_judge(
            SLUG, run=True, batch_size=6, data_dir=data_dir, weak_signals_path=weak_path
        )

    assert m_call.call_count == 1
    assert res["dry_run"] is False
    assert res["judged_now"] == 2
    assert res["verbose"] == 1
    assert res["verdicts_written"] == 2
    assert res["weak_written"] == 1

    # verdicts が永続化された。
    v = _vstore.read_verdicts(SLUG, data_dir=data_dir)
    assert v["h1"]["verbose"] is True
    assert v["h2"]["verbose"] is False

    # weak_signals に channel=verbosity で 1 件 emit。
    ws_lines = [json.loads(ln) for ln in weak_path.read_text().splitlines() if ln]
    assert len(ws_lines) == 1
    assert ws_lines[0]["channel"] == "verbosity"
    assert ws_lines[0]["pj_slug"] == SLUG
    assert ws_lines[0]["provenance"]["hash"] == "h1"

    # suggestion が出力に現れる（auto-apply しないが提示する）。
    out = capsys.readouterr().out
    assert "rules/concise.md 追記案" in out
    assert "output-styles/concise.md" in out  # 自動編集しない旨


def test_judge_run_no_pending_is_noop(data_dir):
    """未判定が無ければ何も書かず判定もしない。"""
    _write_candidates(data_dir, [_cand("h1")])
    _write_verdicts(data_dir, [{"hash": "h1", "pj_slug": SLUG, "verbose": True, "patterns": []}])
    with mock.patch.object(_judge, "call_haiku") as m_call:
        res = _judge.run_judge(SLUG, run=True, data_dir=data_dir)
    m_call.assert_not_called()
    assert res["judged_now"] == 0


def test_judge_run_malformed_json_does_not_persist_verdicts(data_dir, capsys):
    """#273: 応答は届いたが JSON が壊れているバッチは verdict を書かず未判定のまま残す。

    従来は parse_json_array が [] にフォールバックし、全件 verbose=False の verdict が
    永続化されていた（偽陰性の永続化）。呼び出し失敗（call_haiku 例外）と同様に
    スキップし、次回 judge --run で再試行できるようにする。
    """
    _write_candidates(data_dir, [_cand("h1"), _cand("h2")])
    weak_path = data_dir / "weak_signals.jsonl"

    with mock.patch.object(_judge, "call_haiku", return_value="not json at all {{{") as m_call:
        res = _judge.run_judge(
            SLUG, run=True, batch_size=6, data_dir=data_dir, weak_signals_path=weak_path
        )

    assert m_call.call_count == 1
    assert res["judged_now"] == 0
    assert res["parse_failed"] == 1
    # verdicts が 1 件も永続化されない（対象 hash が judged に入らない = 次回再試行される）
    assert _vstore.read_verdicts(SLUG, data_dir=data_dir) == {}
    assert not weak_path.exists()
    err = capsys.readouterr().err
    assert "解釈失敗" in err


def test_judge_run_partial_response_does_not_persist_any_verdict(data_dir, capsys):
    """#273 P1-2: 6件中1件しか返らない部分応答は、バッチ全体を未判定のまま残す。

    従来は返ってこなかった index を `by_i.get(i, {})` で空 dict 補完し verbose=False として
    確定していた（偽陰性の永続化）。網羅性検証（expected_len）でバッチ全体を失格にする。
    """
    _write_candidates(data_dir, [_cand(f"h{i}") for i in range(6)])
    weak_path = data_dir / "weak_signals.jsonl"
    # 6 件中 index 0 のみ応答（部分応答）。
    fake = json.dumps([{"i": 0, "verbose": True, "patterns": ["preamble"], "note": "x"}])

    with mock.patch.object(_judge, "call_haiku", return_value=fake) as m_call:
        res = _judge.run_judge(
            SLUG, run=True, batch_size=6, data_dir=data_dir, weak_signals_path=weak_path
        )

    assert m_call.call_count == 1
    assert res["judged_now"] == 0
    assert res["parse_failed"] == 1
    # verdicts が 1 件も永続化されない（h0 を含め全件 pending のまま）。
    assert _vstore.read_verdicts(SLUG, data_dir=data_dir) == {}
    assert not weak_path.exists()


def test_judge_skips_already_judged(data_dir):
    """判定済み hash は再判定対象から除外する（dedup）。"""
    _write_candidates(data_dir, [_cand("h1"), _cand("h2")])
    _write_verdicts(data_dir, [{"hash": "h1", "pj_slug": SLUG, "verbose": False, "patterns": []}])
    fake = json.dumps([{"i": 0, "verbose": True, "patterns": ["meta"], "note": "x"}])
    with mock.patch.object(_judge, "call_haiku", return_value=fake):
        res = _judge.run_judge(SLUG, run=True, data_dir=data_dir, weak_signals_path=data_dir / "weak_signals.jsonl")
    # h1 は除外されるので今回判定は h2 の 1 件のみ。
    assert res["judged_now"] == 1


# ───────────────────────────── audit section ─────────────────────

def test_section_silent_when_no_candidates(data_dir):
    """候補ゼロなら None（沈黙）。"""
    with mock.patch("audit.sections_verbosity._slug_for", return_value=SLUG):
        assert build_verbosity_section(Path("/x/evolve-anything")) is None


def test_section_shows_pending_when_unjudged(data_dir):
    """候補ありで判定済み floor 未満 → データ不足 + judge --run 誘導を明示（silence != evaluated）。"""
    _write_candidates(data_dir, [_cand("h1"), _cand("h2")])
    with mock.patch("audit.sections_verbosity._slug_for", return_value=SLUG):
        lines = build_verbosity_section(Path("/x/evolve-anything"))
    assert lines is not None
    body = "\n".join(lines)
    assert "未判定" in body


def test_section_silent_on_pending_after_parse_failure_when_fully_judged(data_dir, capsys):
    """#273: パース失敗バッチ由来の pending は、全件が判定済みに転じれば advisory も沈黙する。

    パース失敗（judge --run 1 回目）→ 未判定として残る → 再実行で正しく判定できれば
    「未判定」行は出ない（clean 時は沈黙という observability 契約）。
    """
    _write_candidates(data_dir, [_cand(f"h{i}") for i in range(5)])
    weak_path = data_dir / "weak_signals.jsonl"

    # 1 回目: JSON が壊れていて誰も判定されない。
    with mock.patch.object(_judge, "call_haiku", return_value="not json"):
        res1 = _judge.run_judge(
            SLUG, run=True, batch_size=6, data_dir=data_dir, weak_signals_path=weak_path
        )
    assert res1["parse_failed"] == 1
    assert _vstore.read_judged_hashes(SLUG, data_dir=data_dir) == set()

    # 2 回目: 正しい JSON で全件判定。
    fake = json.dumps([{"i": i, "verbose": False, "patterns": [], "note": ""} for i in range(5)])
    with mock.patch.object(_judge, "call_haiku", return_value=fake):
        res2 = _judge.run_judge(
            SLUG, run=True, batch_size=6, data_dir=data_dir, weak_signals_path=weak_path
        )
    assert res2["judged_now"] == 5

    with mock.patch("audit.sections_verbosity._slug_for", return_value=SLUG):
        lines = build_verbosity_section(Path("/x/evolve-anything"))
    assert lines is not None
    body = "\n".join(lines)
    # 全件判定済み（未判定 0 件）なので judge --run 誘導行は出ない（silence when clean）。
    assert "未判定 0 件" in body
    assert "追加判定できます" not in body


def test_section_shows_rate_and_patterns(data_dir):
    """判定済みが floor 以上 → 冗長率 + 多発パターンを advisory 表示。"""
    _write_candidates(data_dir, [_cand("h1"), _cand("h2"), _cand("h3")])
    _write_verdicts(data_dir, [
        {"hash": "h1", "pj_slug": SLUG, "verbose": True, "patterns": ["preamble"]},
        {"hash": "h2", "pj_slug": SLUG, "verbose": True, "patterns": ["preamble", "filler"]},
        {"hash": "h3", "pj_slug": SLUG, "verbose": False, "patterns": []},
    ])
    with mock.patch("audit.sections_verbosity._slug_for", return_value=SLUG):
        lines = build_verbosity_section(Path("/x/evolve-anything"))
    body = "\n".join(lines)
    assert "無駄に冗長率" in body
    assert "preamble" in body


# ──────────────────── judge: __main__ 直接起動（回帰）─────────────

def test_judge_runs_as_direct_script_dry_run(tmp_path):
    """audit が案内する `python3 scripts/lib/verbosity/judge.py` を __main__ 直接起動できる。

    相対 import（from . import ...）だと __main__ で ImportError になる回帰を防ぐ。
    dry-run（既定）なので LLM は呼ばない（subprocess で起動するのは python のみ・claude 不可）。
    """
    import os
    import subprocess

    judge_path = _lib_dir / "verbosity" / "judge.py"
    data = tmp_path / "evolve-anything"
    data.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(data)}
    proc = subprocess.run(
        [sys.executable, str(judge_path), "--slug", "evolve-anything"],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "dry-run" in proc.stdout
    # dry-run は 1 バイトも書かない。
    assert not (data / _vstore.CANDIDATES_STORE).exists()
    assert not (data / _vstore.VERDICTS_STORE).exists()


# ──────────────────── build_suggestion: tz-aware timestamp（#121）─────────────

def test_build_suggestion_timestamp_is_tz_aware():
    """#121: 提案コメントの `suggestion generated <ts>` は tz-aware（ISO8601 辞書順比較罠 #79 の温床解消）。

    naive `datetime.now().isoformat()` だと tz 情報が欠落し、他 store の tz-aware
    timestamp と辞書順比較したとき Z/+00:00 有無で誤序列になりうる（#79）。
    """
    import collections
    import datetime as _dt
    import re

    out = _judge.build_suggestion(collections.Counter({"preamble": 3}))
    assert out is not None
    m = re.search(r"suggestion generated (\S+) by verbosity\.judge", out)
    assert m is not None, out
    parsed = _dt.datetime.fromisoformat(m.group(1))
    assert parsed.tzinfo is not None, f"naive timestamp: {m.group(1)}"
