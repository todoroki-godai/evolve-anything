"""#339 CLI 配線テスト: `evolve --drain --correction-responses <path>` が
correction_semantic の Phase C（`ingest_judgement_results`）を apply 境界で実効化する。

根因（#339）: `ingest_judgement_results` に production 呼び出し元が1つも無く、Phase A
（`emit_judgement_requests`・phases_capture 経由）だけが走り続けていた。Phase B（Haiku
判定）は本質的に対話的（assistant がインラインで応答を生成する）なので非対話 CLI の
`--drain` 内では実行できない。SKILL.md 側の新 Step が Phase A→B を行い `responses` を
JSON ファイルへ書き出し、`evolve --drain --correction-responses <path>` が Phase C を
apply 境界（他の apply 系書込 = weak_signals_persisted 等と同型）で実行する設計。

`emitted`（Phase A の再構成）は drain 側で `emit_judgement_requests(slug)` を再実行して
得る（決定論・#279 と同じ「responses だけを外部から渡す」設計）。

HOME 隔離はこのディレクトリの conftest（#457）が autouse で行う。
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_LIB = _SCRIPTS.parent.parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import evolve  # noqa: E402


def _stub_drain_neighbors(monkeypatch):
    """テスト対象以外の drain persist を無害な固定値へ差し替える。"""
    import evolve_decisions as ed
    from weak_signals import batch as ws_batch
    from audit import reward_ema as re
    from fleet import queue_state as qs
    from subagent_traces import ingest as st_ingest
    from evolve import _state

    monkeypatch.setattr(
        ed, "drain_pending", lambda **kw: {"accepted": [], "rejected": [], "skipped": []}
    )
    monkeypatch.setattr(
        ws_batch, "persist_weak_signals_drain",
        lambda slug, **kw: {"written": 0, "dry_run": False},
    )
    monkeypatch.setattr(
        re, "persist_reward_ema_batch", lambda project_dir, **kw: {"persisted": 0}
    )
    monkeypatch.setattr(
        qs, "persist_last_evolve", lambda slug, **kw: {"written": 0, "dry_run": False}
    )
    monkeypatch.setattr(
        st_ingest, "ingest_all_projects",
        lambda **kw: {"ingested": 0, "skipped": 0, "capped": False, "remaining": 0},
    )
    monkeypatch.setattr(
        _state, "persist_last_run_timestamp", lambda **kw: {"written": 0, "dry_run": False}
    )


def test_drain_persists_correction_semantic_when_responses_provided(
    monkeypatch, capsys, tmp_path
):
    """--correction-responses 有りで emit を再構成し ingest_judgement_results を dry_run=False で呼ぶ。"""
    from correction_semantic import batch as cs_batch

    _stub_drain_neighbors(monkeypatch)

    fake_emitted = {"requests": [{"id": "b1", "meta": {"utterances": []}}], "unjudged": 1, "batches": 1}
    monkeypatch.setattr(cs_batch, "emit_judgement_requests", lambda slug: fake_emitted)

    calls = {}

    def _fake_ingest(emitted, responses, *, dry_run=False, **kw):
        calls["emitted"] = emitted
        calls["responses"] = responses
        calls["dry_run"] = dry_run
        return {"corrections": 1, "non_corrections": 0, "weak_written": 1,
                "idioms_written": 1, "judged_written": 1, "dry_run": dry_run}

    monkeypatch.setattr(cs_batch, "ingest_judgement_results", _fake_ingest)

    resp_path = tmp_path / "responses.json"
    resp_path.write_text(json.dumps({"b1": "{\"verdicts\": []}"}), encoding="utf-8")

    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--correction-responses", str(resp_path)],
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["correction_semantic_persisted"]["judged_written"] == 1
    # Phase C は apply 境界（drain）で常に dry_run=False で呼ばれる
    assert calls["dry_run"] is False
    assert calls["emitted"] == fake_emitted
    assert calls["responses"] == {"b1": "{\"verdicts\": []}"}


def test_drain_without_correction_responses_skips_gracefully(monkeypatch, capsys):
    """--correction-responses 無しでは skip 理由を surface し他 persist は継続する。"""
    _stub_drain_neighbors(monkeypatch)

    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["correction_semantic_persisted"] == {"skipped": "no_correction_responses"}
    # result 非依存 persist は無傷（drain 本体を完走）。
    assert "weak_signals_persisted" in out


def test_drain_with_missing_correction_responses_skips_gracefully(
    monkeypatch, capsys, tmp_path
):
    """存在しない --correction-responses パスでも skip 理由を surface し他 persist は継続する。"""
    _stub_drain_neighbors(monkeypatch)

    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--correction-responses", str(missing)],
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["correction_semantic_persisted"] == {"skipped": "correction_responses_not_found"}
    assert "weak_signals_persisted" in out


def test_drain_with_invalid_correction_responses_json_surfaces_error(
    monkeypatch, capsys, tmp_path
):
    """不正 JSON は skip 理由を surface する（drain 本体は完走する）。"""
    _stub_drain_neighbors(monkeypatch)

    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--correction-responses", str(bad)],
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert out["correction_semantic_persisted"]["skipped"].startswith(
        "correction_responses_unreadable"
    )
    assert "weak_signals_persisted" in out


def test_drain_swallows_correction_semantic_error(monkeypatch, capsys, tmp_path):
    """ingest 呼び出しが失敗しても drain 本体は完走し error を surface する。"""
    from correction_semantic import batch as cs_batch

    _stub_drain_neighbors(monkeypatch)

    monkeypatch.setattr(
        cs_batch, "emit_judgement_requests",
        lambda slug: {"requests": [], "unjudged": 0, "batches": 0},
    )

    def _boom(emitted, responses, **kw):
        raise RuntimeError("store unwritable")

    monkeypatch.setattr(cs_batch, "ingest_judgement_results", _boom)

    resp_path = tmp_path / "responses.json"
    resp_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--correction-responses", str(resp_path)],
    )

    evolve.main()

    out = json.loads(capsys.readouterr().out)
    assert "error" in out["correction_semantic_persisted"]
    assert "store unwritable" in out["correction_semantic_persisted"]["error"]
