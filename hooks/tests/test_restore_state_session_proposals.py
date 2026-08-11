"""restore_state の改善案 SessionStart 提示（#409）。

毎朝の digest（``evolve-queue.json`` の ``proposals`` フィールド）から、当該 PJ 向け改善案
（per_pj[pj_slug] + global の既読フィルタ後）を additionalContext（ADR-038 = Claude 向け
チャネル。sessionTitle と同型の ``hookSpecificOutput`` キーで出す）で提示し、Claude に
AskUserQuestion で y/n 提示するよう指示する。

- 提示あり → hookSpecificOutput.additionalContext に代表テキスト + はい/いいえコマンドが出る
- 提示なし（該当 PJ の group が無い / 全既読）→ 沈黙（stdout を汚さない）
- 他 PJ 向けの group は混入しない
- env ガード・read-only 契約は evolve-queue notice と同型
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))

import data_dir_migration as ddm  # noqa: E402
import restore_state  # noqa: E402


def _fresh_generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _group(keys, rep="git statusじゃなくてgit diffを使って") -> dict:
    return {
        "signal_keys": list(keys),
        "representative": rep,
        "idiom": None,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": 1,
        "evidence_text": rep,
        "prev_action": "",
    }


def _write_queue(data_dir: Path, proposals: dict) -> None:
    payload = {
        "generated_at": _fresh_generated_at(),
        "threshold": 3,
        "tracked_total": 1,
        "queue": [],
        "proposals": proposals,
    }
    (data_dir / "evolve-queue.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _install_env(tmp_path, monkeypatch):
    source = tmp_path / "plugins" / "data" / "evolve-anything-evolve-anything"
    source.mkdir(parents=True)
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: Path(p) == source)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(source))
    return source


def _set_project_dir(tmp_path, monkeypatch, name="myproj") -> str:
    """非 git の素 dir を CLAUDE_PROJECT_DIR にする（resolve_pj_slug は basename を返す）。"""
    proj = tmp_path / name
    proj.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    return proj.name  # = 期待される pj_slug


def test_deliver_fires_with_proposals_for_this_pj(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})

    restore_state._deliver_session_proposals()
    out = capsys.readouterr().out
    assert out
    payload = json.loads(out.strip())
    # ADR-038 スキーマ: hookEventName 無しだと additionalContext が解釈されず無言で死ぬ
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    msg = payload["hookSpecificOutput"]["additionalContext"]
    assert "git diff" in msg
    assert "AskUserQuestion" in msg
    # 回答コマンドは絶対パス（提示先は他 PJ の cwd なので相対だと No such file になる）
    expected_cmd = str(_HOOKS.parent / "bin" / "evolve-reflect")
    assert expected_cmd.startswith("/")
    assert f"{expected_cmd} --promote-weak k1" in msg
    assert f"{expected_cmd} --reject-weak k1 --pj {slug}" in msg


def test_deliver_silent_when_no_proposals_for_this_pj(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {"other-pj": [_group(["k1"])]}, "global": []})

    restore_state._deliver_session_proposals()
    assert capsys.readouterr().out == ""


def test_deliver_silent_when_all_signal_keys_seen(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})
    (source / "correction_review_seen.jsonl").write_text(
        json.dumps({"key": "k1", "pj_slug": slug, "decision": "promoted",
                    "reviewed_at": _fresh_generated_at()}) + "\n",
        encoding="utf-8",
    )

    restore_state._deliver_session_proposals()
    assert capsys.readouterr().out == ""


def test_deliver_silent_when_no_queue_file(tmp_path, monkeypatch, capsys):
    _install_env(tmp_path, monkeypatch)
    _set_project_dir(tmp_path, monkeypatch)
    restore_state._deliver_session_proposals()
    assert capsys.readouterr().out == ""


def test_deliver_silent_outside_install_layout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    _set_project_dir(tmp_path, monkeypatch)
    restore_state._deliver_session_proposals()
    assert capsys.readouterr().out == ""


def test_deliver_silent_without_env(monkeypatch, capsys):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    restore_state._deliver_session_proposals()
    assert capsys.readouterr().out == ""


def test_deliver_silent_without_project_dir(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_queue(source, {"per_pj": {"whatever": [_group(["k1"])]}, "global": []})
    restore_state._deliver_session_proposals()
    assert capsys.readouterr().out == ""


def test_deliver_does_not_write(tmp_path, monkeypatch):
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})
    before = {p.name for p in source.iterdir()}
    restore_state._deliver_session_proposals()
    after = {p.name for p in source.iterdir()}
    assert before == after  # read-only


def test_handle_session_start_invokes_session_proposals(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})
    restore_state.handle_session_start({})
    out = capsys.readouterr().out
    assert "AskUserQuestion" in out
