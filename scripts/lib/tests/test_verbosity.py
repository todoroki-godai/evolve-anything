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
    assert "judge.py --run" in body


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
