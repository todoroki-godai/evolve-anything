"""#444 CLI 配線テスト: `evolve --drain` の `--accepted`/`--rejected`。

現行は `drain_pending(project_dir=..., result_json=...)` を呼ぶだけで `accepted=`/`rejected=`
を渡していないため、`--drain` 単体では accept が絶対に記録されない（`ingest_decisions` の accept
必要条件が ``pid in accepted_ids and applied`` のため）。本テストは:

  1. `--accepted`/`--rejected` が `drain_pending` へ正しく渡ること（複数指定含む）
  2. `--accepted`/`--rejected` を渡さない既存呼び出しが従来どおり動くこと（回帰防止）
  3. バリデーション3種（重複指定・未知 ID・理由なし reject）を拒否し、拒否時は
     `drain_pending` を一切呼ばないこと（部分書込防止）

を固定する。HOME / MARKER_ROOT / DATA_DIR 隔離はルート conftest（#457/#119）が autouse で行う。
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_LIB = _SCRIPTS.parent.parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import evolve  # noqa: E402
import evolve_decisions as ed  # noqa: E402


def _stub_drain_neighbors(monkeypatch):
    """drain_pending 以外の drain persist を無害な固定値へ差し替える（テスト対象を drain_pending の
    呼び出し引数だけに絞る）。"""
    from weak_signals import batch as ws_batch
    from audit import reward_ema as re
    from fleet import queue_state as qs
    from subagent_traces import ingest as st_ingest
    from evolve import _state

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
    monkeypatch.setattr(_state, "persist_last_run_timestamp", lambda **kw: {"written": 0, "dry_run": False})


def _capture_drain_pending(monkeypatch):
    """`evolve_decisions.drain_pending` を差し替え、呼び出し kwargs を記録する。"""
    calls = []

    def _fake(**kw):
        calls.append(kw)
        return {"accepted": [], "rejected": [], "skipped": []}

    monkeypatch.setattr(ed, "drain_pending", _fake)
    return calls


def _result_json_with_pending(tmp_path, ids):
    """result JSON（result.evolve_decisions.pending）を組み立てて書き出す。"""
    payload = {
        "evolve_decisions": {
            "pending": [{"id": pid, "skill_name": pid, "before_sha": "x"} for pid in ids],
        }
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ─── 1. --accepted/--rejected が drain_pending へ正しく渡る ────────────────────


def test_accepted_single_id_passed_to_drain_pending(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj), "--accepted", "evdiff_aaa"],
    )

    evolve.main()

    assert len(calls) == 1
    assert calls[0]["accepted"] == {"evdiff_aaa"}
    assert calls[0]["rejected"] is None
    out = json.loads(capsys.readouterr().out)
    assert "error" not in out


def test_accepted_multiple_ids_passed_to_drain_pending(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa", "evdiff_bbb"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj), "--accepted", "evdiff_aaa", "evdiff_bbb"],
    )

    evolve.main()

    assert calls[0]["accepted"] == {"evdiff_aaa", "evdiff_bbb"}


def test_rejected_single_pair_passed_to_drain_pending(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_ccc"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj), "--rejected", "evdiff_ccc", "ドメイン不一致"],
    )

    evolve.main()

    assert calls[0]["accepted"] is None
    assert calls[0]["rejected"] == {"evdiff_ccc": "ドメイン不一致"}


def test_rejected_multiple_pairs_passed_to_drain_pending(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_ccc", "evdiff_ddd"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj),
         "--rejected", "evdiff_ccc", "ドメイン不一致",
         "--rejected", "evdiff_ddd", "重複提案"],
    )

    evolve.main()

    assert calls[0]["rejected"] == {"evdiff_ccc": "ドメイン不一致", "evdiff_ddd": "重複提案"}


def test_accepted_and_rejected_together(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa", "evdiff_ccc"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj),
         "--accepted", "evdiff_aaa",
         "--rejected", "evdiff_ccc", "ドメイン不一致"],
    )

    evolve.main()

    assert calls[0]["accepted"] == {"evdiff_aaa"}
    assert calls[0]["rejected"] == {"evdiff_ccc": "ドメイン不一致"}


# ─── 2. 回帰防止: --accepted/--rejected 無しの既存呼び出しは従来どおり ──────────


def test_no_decision_args_passes_none_unchanged(monkeypatch, capsys):
    """--accepted/--rejected 未指定時は従来どおり accepted=None, rejected=None で呼ぶ
    （既存呼び出し・#402/#421 の非対話回収経路と同型の契約を drain 本体でも保つ）。"""
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--drain", "--project-dir", "/tmp/whatever"])

    evolve.main()

    assert len(calls) == 1
    assert calls[0]["accepted"] is None
    assert calls[0]["rejected"] is None


def test_accepted_without_drain_is_rejected(monkeypatch, capsys):
    """#450 codex cold review [Should]1: --drain 無しで --accepted/--rejected を渡すと
    通常の evolve が走って判断が黙って捨てられる。--drain 必須にしてエラーで中断する。"""
    calls = _capture_drain_pending(monkeypatch)

    def _boom(**kw):
        raise AssertionError("run_evolve must not run when --accepted is given without --drain")

    monkeypatch.setattr(evolve, "run_evolve", _boom)
    monkeypatch.setattr(
        sys, "argv", ["evolve.py", "--project-dir", "/tmp/whatever", "--accepted", "evdiff_aaa"],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_decision_args"


def test_rejected_without_drain_is_rejected(monkeypatch, capsys):
    """--rejected 側も同様に --drain 必須（S1 の対称ケース）。"""
    calls = _capture_drain_pending(monkeypatch)

    def _boom(**kw):
        raise AssertionError("run_evolve must not run when --rejected is given without --drain")

    monkeypatch.setattr(evolve, "run_evolve", _boom)
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--project-dir", "/tmp/whatever", "--rejected", "evdiff_aaa", "理由"],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_decision_args"


# ─── 3. バリデーション: 拒否時は drain_pending を一切呼ばない ───────────────────


def test_duplicate_id_across_accepted_and_rejected_is_rejected(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj),
         "--accepted", "evdiff_aaa",
         "--rejected", "evdiff_aaa", "理由"],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []  # drain_pending は一切呼ばれない（部分書込防止）
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_decision_args"
    assert any("evdiff_aaa" in d for d in out["details"])


def test_unknown_id_is_rejected(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa"])  # pending に存在するのは aaa のみ
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj), "--accepted", "evdiff_nonexistent"],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_decision_args"
    assert any("evdiff_nonexistent" in d for d in out["details"])


def test_unknown_id_in_rejected_is_rejected(monkeypatch, capsys, tmp_path):
    """#450 [Should]3: --rejected 側の未知 ID も --accepted 側と同様に拒否される。"""
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa"])  # pending に存在するのは aaa のみ
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj), "--rejected", "evdiff_nonexistent", "理由"],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_decision_args"
    assert any("evdiff_nonexistent" in d for d in out["details"])


def test_no_decision_args_does_not_resolve_pending_ids(monkeypatch, capsys, tmp_path):
    """#450 [Should]3: --accepted/--rejected 未指定なら pending 解決自体が走らない
    （result-json read も marker read も呼ばれない）。"""
    _stub_drain_neighbors(monkeypatch)
    _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa"])

    called = {"known_ids": False}

    def _boom(*a, **kw):
        called["known_ids"] = True
        raise AssertionError("_known_pending_ids must not be called when no decision args are given")

    import evolve.cli as _cli_mod
    monkeypatch.setattr(_cli_mod, "_known_pending_ids", _boom)
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--result-json", str(rj)],
    )

    evolve.main()  # 例外を投げずに完走すること（_known_pending_ids が呼ばれていれば AssertionError で落ちる）

    assert called["known_ids"] is False


def test_reject_without_reason_is_rejected(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj), "--rejected", "evdiff_aaa", ""],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_decision_args"
    assert any("evdiff_aaa" in d for d in out["details"])


def test_reject_with_whitespace_only_reason_is_rejected(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj), "--rejected", "evdiff_aaa", "   "],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []


def test_duplicate_id_within_accepted_is_rejected(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj), "--accepted", "evdiff_aaa", "evdiff_aaa"],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []


def test_duplicate_id_within_rejected_is_rejected(monkeypatch, capsys, tmp_path):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    rj = _result_json_with_pending(tmp_path, ["evdiff_aaa"])
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever",
         "--result-json", str(rj),
         "--rejected", "evdiff_aaa", "理由1",
         "--rejected", "evdiff_aaa", "理由2"],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []


# ─── 4. 未知 ID 検査は marker 経由（--result-json 未指定）でも働く ──────────────


def test_unknown_id_rejected_via_marker_when_no_result_json(monkeypatch, capsys):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    monkeypatch.setattr(ed, "resolve_slug", lambda cwd=None: "testslug")
    monkeypatch.setattr(
        ed, "read_pending_marker",
        lambda slug: {"pending": [{"id": "evdiff_aaa"}]},
    )
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--accepted", "evdiff_zzz"],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []


def test_known_id_accepted_via_marker_when_no_result_json(monkeypatch, capsys):
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    monkeypatch.setattr(ed, "resolve_slug", lambda cwd=None: "testslug")
    monkeypatch.setattr(
        ed, "read_pending_marker",
        lambda slug: {"pending": [{"id": "evdiff_aaa"}]},
    )
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--accepted", "evdiff_aaa"],
    )

    evolve.main()

    assert calls[0]["accepted"] == {"evdiff_aaa"}


def test_orphan_worktree_id_treated_as_unknown_via_marker(monkeypatch, capsys):
    """#450 codex cold review [Must]3: marker 経由の未知 ID 検証母集団は drain_pending が実際に
    ingest 対象にする集合（orphan worktree 除外後）と一致させる。

    drain_pending（_drain.py）は marker 経路でのみ orphan worktree（既に消えた worktree に
    属する pending）を _partition_orphaned で ingest 対象から除外する。ここで同じ除外を
    しないと、orphan の ID へ明示 accept/reject を渡したときに「検証は通過するが実際には
    記録されず marker だけ削除されて終わる」サイレント消失が起きる（ユーザーの判断が
    黙って消える）。orphan の ID は未知 ID として拒否されるのが正しい。
    """
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    monkeypatch.setattr(ed, "resolve_slug", lambda cwd=None: "testslug")
    monkeypatch.setattr(
        ed, "read_pending_marker",
        lambda slug: {
            "pending": [
                {"id": "evdiff_alive", "worktree_root": "/tmp"},
                {"id": "evdiff_orphan", "worktree_root": "/nonexistent/definitely-not-real-path-444"},
            ]
        },
    )
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--accepted", "evdiff_orphan"],
    )

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code != 0
    assert calls == []  # drain_pending は一切呼ばれない（orphan の ID がすり抜けて marker だけ消えることはない）
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "invalid_decision_args"
    assert any("evdiff_orphan" in d for d in out["details"])


def test_alive_worktree_id_still_accepted_when_orphan_also_pending(monkeypatch, capsys):
    """orphan 除外が過剰除外でないこと — 生きている worktree の ID は通常どおり accept できる。"""
    _stub_drain_neighbors(monkeypatch)
    calls = _capture_drain_pending(monkeypatch)
    monkeypatch.setattr(ed, "resolve_slug", lambda cwd=None: "testslug")
    monkeypatch.setattr(
        ed, "read_pending_marker",
        lambda slug: {
            "pending": [
                {"id": "evdiff_alive", "worktree_root": "/tmp"},
                {"id": "evdiff_orphan", "worktree_root": "/nonexistent/definitely-not-real-path-444"},
            ]
        },
    )
    monkeypatch.setattr(
        sys, "argv",
        ["evolve.py", "--drain", "--project-dir", "/tmp/whatever", "--accepted", "evdiff_alive"],
    )

    evolve.main()

    assert calls[0]["accepted"] == {"evdiff_alive"}


# ─── 5. ヘルプ文言: 既存 optimizer --accept/--reject との混同防止（要件5） ──────


def test_help_text_clarifies_multiple_id_and_distinguishes_from_optimizer(monkeypatch, capsys):
    """main() 内部で argparse.ArgumentParser を構築するため、--help 実行で SystemExit(0) になる
    ことと、ヘルプ文言に「複数指定」の説明が含まれることを確認する。"""
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--help"])

    with pytest.raises(SystemExit) as exc:
        evolve.main()

    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--accepted" in help_text
    assert "--rejected" in help_text
    assert "複数指定" in help_text
    # #450 codex cold review [Should]2: 「複数指定」だけでなく、既存 optimizer の単数
    # --accept/--reject との区別を実際に説明していることを検査する（説明文を削っても
    # 通ってしまう検査の穴を塞ぐ）。
    assert "genetic-prompt-optimizer" in help_text
    assert help_text.count("とは別物") >= 2  # --accepted 用・--rejected 用の両方に説明がある
