"""restore_state の改善案 SessionStart 提示（#409, #412）。

毎朝の digest（``evolve-queue.json`` の ``proposals`` フィールド）から、当該 PJ 向け改善案
（per_pj[pj_slug] + global の既読フィルタ後）を **2 チャネル同時出力**する（#412 [Must]1）:

- ``systemMessage``（user 可視）: 代表テキストを提示件数分並べ、「応答のあとで採否をお聞き
  します。表示されなかった場合は未処理のまま次回また出ます」と伝える（#412 round2 [Should]E:
  additionalContext 側の prompt instruction 遵守に依存する文言を機械的に保証できる範囲へ修正）
- ``hookSpecificOutput.additionalContext``（Claude 可視・ADR-038）: 「最初の応答を終えた直後に
  AskUserQuestion で y/n 提示せよ」という行動指示

``_build_session_proposal_output()`` は **純関数**（print しない）で dict|None を返す
（#412 [Must]2: SessionStart hook の stdout は 1 行の有効な JSON 応答であるべきで、checkpoint の
``hookSpecificOutput``（sessionTitle）と別行に分かれると片方が黙って捨てられうる）。実際の print
は ``handle_session_start`` が checkpoint の有無を見てから 1 回だけ行う。

- 提示あり → 返り値の hookSpecificOutput.additionalContext に代表テキスト + はい/いいえコマンド
- 提示なし（該当 PJ の group が無い / 全既読）→ None（stdout を汚さない）
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
    # ADR-054 Phase 0: この test file の関心事でない他系統（data_dir_migration・
    # utterance_staleness）が同じ env ガードで一緒に発火し、systemMessage が digest 化
    # するのを防ぐ（additionalContext 側は影響を受けないが、他テストとの一貫性のため）。
    monkeypatch.setattr(restore_state, "_build_data_dir_migration_output", lambda: None)
    monkeypatch.setattr(restore_state, "_build_utterance_staleness_output", lambda: None)
    return source


def _set_project_dir(tmp_path, monkeypatch, name="myproj") -> str:
    """非 git の素 dir を CLAUDE_PROJECT_DIR にする（resolve_pj_slug は basename を返す）。"""
    proj = tmp_path / name
    proj.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    return proj.name  # = 期待される pj_slug


def test_build_fires_with_proposals_for_this_pj(tmp_path, monkeypatch):
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})

    output = restore_state._build_session_proposal_output()
    assert output is not None
    # ADR-038 スキーマ: hookEventName 無しだと additionalContext が解釈されず無言で死ぬ
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    msg = output["hookSpecificOutput"]["additionalContext"]
    assert "git diff" in msg
    assert "AskUserQuestion" in msg
    # 回答コマンドは絶対パス（提示先は他 PJ の cwd なので相対だと No such file になる）
    expected_cmd = str(_HOOKS.parent / "bin" / "evolve-reflect")
    assert expected_cmd.startswith("/")
    assert f"{expected_cmd} --promote-weak k1" in msg
    assert f"{expected_cmd} --reject-weak k1 --pj {slug}" in msg
    # #412 [Must]1: systemMessage（user 可視）も同時に出る
    assert "git diff" in output["systemMessage"]
    # ADR-054 Phase 0 §4.2: digest（2件以上発火時用の短縮形）も同時に返す
    assert output["digest"] == "改善案1件"


# --- #503: decision_text（判断を求める本文）の配線 ---

def test_build_output_includes_decision_text_matching_systemmessage_body(tmp_path, monkeypatch):
    """#503 §3.1-3: collectors.py は systemMessage の prefix を除去した decision_text を
    返す。壊す不変条件=I2/I3／経路=このテスト自身（N1: キーを落とす、N7: 文言を混入する、
    N6: prefix 除去を外す、いずれの mutation でもこの等値比較が崩れて赤くなる）。"""
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})

    output = restore_state._build_session_proposal_output()
    assert output is not None
    assert "decision_text" in output
    assert output["decision_text"] == output["systemMessage"].removeprefix("[evolve-anything] ")


def test_build_decision_text_has_no_embedded_prefix(tmp_path, monkeypatch):
    """N6: removeprefix が外れると decision_text の先頭に "[evolve-anything] " が二重に
    残る。壊す不変条件=I3／経路=このテスト自身。"""
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})

    output = restore_state._build_session_proposal_output()
    assert not output["decision_text"].startswith("[evolve-anything] ")


def test_build_machinery_only_notice_has_no_decision_text(tmp_path, monkeypatch):
    """#503 §3.1-4: 提案0件の notice は判断を求めていないため decision_text を返さない。"""
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {
        "per_pj": {},
        "global": [],
        "excluded_machinery_by_pj": {slug: {"total": 4, "by_channel": {"llm_judge": 4}}},
    })

    output = restore_state._build_session_proposal_output()
    assert output is not None
    assert "decision_text" not in output


def test_build_surfaces_excluded_machinery_for_this_pj(tmp_path, monkeypatch):
    """codex [Must]1（#443 PR2-a）: digest の excluded_machinery_by_pj[slug] を systemMessage
    に表示する（formatter/consumer まで表示を通す）。"""
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {
        "per_pj": {slug: [_group(["k1"])]},
        "global": [],
        "excluded_machinery_by_pj": {slug: {"total": 2, "by_channel": {"llm_judge": 2}}},
    })

    output = restore_state._build_session_proposal_output()
    assert output is not None
    assert "machinery" in output["systemMessage"]
    assert "2" in output["systemMessage"]


def test_build_silent_on_machinery_when_other_pj_excluded(tmp_path, monkeypatch):
    """他 PJ 分の machinery 除外は当該 PJ の systemMessage に混入しない（slug スコープ）。"""
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {
        "per_pj": {slug: [_group(["k1"])]},
        "global": [],
        "excluded_machinery_by_pj": {"other-pj": {"total": 9, "by_channel": {"llm_judge": 9}}},
    })

    output = restore_state._build_session_proposal_output()
    assert output is not None
    assert "machinery" not in output["systemMessage"]


def test_build_fires_machinery_only_notice_when_groups_empty(tmp_path, monkeypatch):
    """codex 2巡目 [Must]1: 提案候補が全件 machinery で groups が空でも、除外件数が非ゼロなら
    通知を返す（silence != evaluated）。旧実装は `if not groups: return None` が machinery
    件数を読む前に発火し、除外が最も効いた瞬間にだけ完全に沈黙していた。
    """
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    # per_pj に当該 slug のエントリが無い（＝候補が全件 machinery で digest 側から落ちた想定）。
    _write_queue(source, {
        "per_pj": {},
        "global": [],
        "excluded_machinery_by_pj": {slug: {"total": 4, "by_channel": {"llm_judge": 4}}},
    })

    output = restore_state._build_session_proposal_output()
    assert output is not None
    assert "machinery" in output["systemMessage"]
    assert "4" in output["systemMessage"]
    # 通常の「改善案があります」文面（AskUserQuestion 誘導）は使わない — 提案が無い事実を示す。
    assert "改善案があります" not in output["systemMessage"]
    # AskUserQuestion で確認すべき提案が無いため additionalContext は付けない。
    assert "hookSpecificOutput" not in output


def test_build_surfaces_excluded_context_missing_for_this_pj(tmp_path, monkeypatch):
    """#498 要件4: digest の excluded_context_missing_by_pj[slug] を systemMessage に透明化する。"""
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {
        "per_pj": {slug: [_group(["k1"])]},
        "global": [],
        "excluded_context_missing_by_pj": {slug: 2},
    })

    output = restore_state._build_session_proposal_output()
    assert output is not None
    assert "保留" in output["systemMessage"]
    assert "2" in output["systemMessage"]


def test_build_fires_context_missing_only_notice_when_groups_empty(tmp_path, monkeypatch):
    """#498 要件4: 提案候補が全件説明不能で groups が空でも、除外件数が非ゼロなら通知を返す
    （silence != evaluated・machinery-only 通知と同じ流儀）。
    """
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {
        "per_pj": {},
        "global": [],
        "excluded_context_missing_by_pj": {slug: 3},
    })

    output = restore_state._build_session_proposal_output()
    assert output is not None
    assert "保留" in output["systemMessage"]
    assert "3" in output["systemMessage"]
    assert "改善案があります" not in output["systemMessage"]
    assert "hookSpecificOutput" not in output


def test_build_returns_none_when_groups_and_machinery_both_empty(tmp_path, monkeypatch):
    """codex 2巡目 [Must]1: groups 空 かつ machinery 除外 0 なら従来どおり完全な無音（None）。"""
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {
        "per_pj": {},
        "global": [],
        "excluded_machinery_by_pj": {slug: {"total": 0, "by_channel": {}}},
    })

    assert restore_state._build_session_proposal_output() is None


def test_build_returns_none_when_no_proposals_for_this_pj(tmp_path, monkeypatch):
    source = _install_env(tmp_path, monkeypatch)
    _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {"other-pj": [_group(["k1"])]}, "global": []})

    assert restore_state._build_session_proposal_output() is None


def test_build_returns_none_when_all_signal_keys_seen(tmp_path, monkeypatch):
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})
    (source / "correction_review_seen.jsonl").write_text(
        json.dumps({"key": "k1", "pj_slug": slug, "decision": "promoted",
                    "reviewed_at": _fresh_generated_at()}) + "\n",
        encoding="utf-8",
    )

    assert restore_state._build_session_proposal_output() is None


def test_build_returns_none_when_no_queue_file(tmp_path, monkeypatch):
    _install_env(tmp_path, monkeypatch)
    _set_project_dir(tmp_path, monkeypatch)
    assert restore_state._build_session_proposal_output() is None


def test_build_returns_none_outside_install_layout(tmp_path, monkeypatch):
    monkeypatch.setattr(ddm, "is_cc_install_layout", lambda p: False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "isolated"))
    _set_project_dir(tmp_path, monkeypatch)
    assert restore_state._build_session_proposal_output() is None


def test_build_returns_none_without_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    assert restore_state._build_session_proposal_output() is None


def test_build_returns_none_without_project_dir(tmp_path, monkeypatch):
    source = _install_env(tmp_path, monkeypatch)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    _write_queue(source, {"per_pj": {"whatever": [_group(["k1"])]}, "global": []})
    assert restore_state._build_session_proposal_output() is None


def test_build_does_not_write(tmp_path, monkeypatch):
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})
    before = {p.name for p in source.iterdir()}
    restore_state._build_session_proposal_output()
    after = {p.name for p in source.iterdir()}
    assert before == after  # read-only


def test_handle_session_start_invokes_session_proposals(tmp_path, monkeypatch, capsys):
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})
    restore_state.handle_session_start({})
    out = capsys.readouterr().out
    assert "AskUserQuestion" in out


# --- #412 [Must]2: SessionStart stdout は単一の有効な JSON 応答であること ---

class TestSingleJsonResponse:
    """checkpoint（sessionTitle）と改善案提示（additionalContext）が同時に発生しても、
    hookSpecificOutput を含む行は高々 1 つで、その中に両方が同居すること。
    """

    def test_proposal_and_checkpoint_merge_into_one_line(self, tmp_path, monkeypatch, capsys):
        source = _install_env(tmp_path, monkeypatch)
        slug = _set_project_dir(tmp_path, monkeypatch)
        _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})
        monkeypatch.setattr(
            "common.find_latest_checkpoint",
            lambda _: {"work_context": {"git_branch": "main"}},
        )

        restore_state.handle_session_start({})
        out = capsys.readouterr().out
        # ADR-054 Phase 0 §4.1/§7.1-1: stdout は「0行」か「厳密に1行の JSON dict」の二値。
        lines = out.strip().splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])

        hs = payload["hookSpecificOutput"]
        assert hs["hookEventName"] == "SessionStart"
        assert "AskUserQuestion" in hs["additionalContext"]
        assert hs["sessionTitle"] == f"{(tmp_path / 'myproj').name} | main"

    def test_proposal_alone_when_no_checkpoint(self, tmp_path, monkeypatch, capsys):
        source = _install_env(tmp_path, monkeypatch)
        slug = _set_project_dir(tmp_path, monkeypatch)
        _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})
        monkeypatch.setattr("common.find_latest_checkpoint", lambda _: None)

        restore_state.handle_session_start({})
        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        hs = payload["hookSpecificOutput"]
        assert "AskUserQuestion" in hs["additionalContext"]
