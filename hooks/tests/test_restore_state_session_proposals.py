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
from session_notify.model import NotificationItem  # noqa: E402


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


# --- #504 I2(d): 本改修前に生成された（キャッシュされた古い）evolve-queue.json snapshot の
# group に 'prev_action' キーが残っていても、SessionStart hook の最終出力に漏れないこと ---

def test_i2d_stale_prev_action_key_does_not_leak_into_hook_json(tmp_path, monkeypatch, capsys):
    """evolve-queue.json は毎朝の daily runner が書く永続 snapshot。本改修前に生成された
    ファイルがまだ更新されていない状態（鮮度: キャッシュされた古い成果物）で group dict に
    'prev_action' キーが残っていても、handle_session_start が stdout に出す hook JSON 全文
    （systemMessage + hookSpecificOutput.additionalContext + decision_text 由来の merge を
    含む）のどこにもその内容が現れないこと。collectors.py の decision_text 経由の再注入
    （round2 [Must]）もこの1本で捕まえる。
    """
    sentinel = "ZZPREVACTIONSENTINELZZ"
    source = _install_env(tmp_path, monkeypatch)
    slug = _set_project_dir(tmp_path, monkeypatch)
    stale_group = _group(["k1"])
    stale_group["prev_action"] = sentinel
    _write_queue(source, {"per_pj": {slug: [stale_group]}, "global": []})
    # decision_text は発火2件以上の merge 経路でのみ最終 systemMessage に連結される
    # （発火1件は items[0].text をそのまま使い decision_text を経由しない・merge.py:29-30）。
    # collectors.py の decision_text 経由の再注入（round2 [Must]）を通すため、もう1系統を
    # 同時発火させる。
    monkeypatch.setattr(
        restore_state, "_build_evolve_drain_output",
        lambda: NotificationItem(label="drain", tier=1, text="生きてる系統", digest="digest"),
    )

    restore_state.handle_session_start({})
    out = capsys.readouterr().out
    assert sentinel not in out


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


# --- #541 S1: 既読ストアの read/write data_dir 同一性契約 ---
#
# collectors._build_session_proposal_output は digest 生成と同じ data_dir を
# ``daily_review.default_seen_path(base=data_dir)`` で明示指定して読む一方、
# reflect.py の --promote-weak/--reject-weak/--already-reflected-weak は
# ``daily_review.record_reviewed`` を path 未指定（env CLAUDE_PLUGIN_DATA からの
# production 既定解決）で呼ぶ。両者が同じ物理ファイルを指していないと、既読化した
# はずの signal_key が翌朝また提示される（pitfall_datadir_hook_tool_split と同型）。
def _align_store_write_data_dir(monkeypatch, source: Path) -> None:
    """``store_write`` が使う ``rl_common.DATA_DIR`` を ``source`` に合わせる。

    ``rl_common.DATA_DIR`` はモジュール import 時に一度だけ ``resolve_data_dir`` した
    値のキャッシュ（本番プロセスは1プロセス=1回の起動なので env と常に一致する）。
    root conftest の autouse ``_isolate_plugin_data`` はこの属性を **test 開始時点**の
    ``tmp_path`` へ rebase 済みだが、本テストは ``_install_env`` で **test body 内**に
    改めて ``CLAUDE_PLUGIN_DATA=source`` を設定するため、その後で属性を揃え直さないと
    ``record_reviewed``（path 省略・本番経路）の書込先が digest 読み取り側（env から
    都度再解決）とずれる。これは #541 が検査したい read/write 分裂そのものではなく、
    2 段階の env シミュレーションを重ねる本テストファイル特有の前提合わせ。
    """
    sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))
    import rl_common
    monkeypatch.setattr(rl_common, "DATA_DIR", source, raising=False)


class TestSeenStoreDataDirContract:
    def test_promoted_key_written_without_explicit_path_is_excluded_from_digest(
        self, tmp_path, monkeypatch,
    ):
        """write 側（record_reviewed を path 省略で呼ぶ = CLI の本番経路）と read 側
        （_build_session_proposal_output 内の seen_path）が同じ data_dir を指すことを、
        実際に書いて読む往復で確認する（mock で片側だけ差し替えない）。
        """
        source = _install_env(tmp_path, monkeypatch)
        slug = _set_project_dir(tmp_path, monkeypatch)
        _write_queue(source, {"per_pj": {slug: [_group(["k1", "k2"])]}, "global": []})
        _align_store_write_data_dir(monkeypatch, source)

        sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))
        from correction_semantic.daily_review import record_reviewed

        # CLI の本番経路と同じ呼び方（path 省略）で k1 のみ既読化する。
        record_reviewed(["k1"], slug, decision="already_reflected")

        output = restore_state._build_session_proposal_output()
        assert output is not None
        msg = output["hookSpecificOutput"]["additionalContext"]
        # k1 は既読フィルタで落ち、k2 だけが残存 group として提示される。
        assert "--promote-weak k2" in msg
        assert "--promote-weak k1,k2" not in msg
        assert "--promote-weak k1 " not in msg
        assert "--promote-weak k1," not in msg

    def test_all_keys_already_reflected_suppresses_the_group_entirely(
        self, tmp_path, monkeypatch,
    ):
        """group 内の全 signal_key が既読化されていれば group ごと消える
        （残存 0 件 = 既読ストアが read/write で同一ファイルを指している最も強い証拠）。
        """
        source = _install_env(tmp_path, monkeypatch)
        slug = _set_project_dir(tmp_path, monkeypatch)
        _write_queue(source, {"per_pj": {slug: [_group(["k1"])]}, "global": []})
        _align_store_write_data_dir(monkeypatch, source)

        sys.path.insert(0, str(_HOOKS.parent / "scripts" / "lib"))
        from correction_semantic.daily_review import record_reviewed

        record_reviewed(["k1"], slug, decision="already_reflected")

        output = restore_state._build_session_proposal_output()
        assert output is None  # 提示する group が無い（沈黙）
