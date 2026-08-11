"""daily.proposal_digest のテスト（#409 セッション開始時の改善案提示）。

毎朝の digest 生成（決定論・LLM 非依存・read-only）と、SessionStart 提示用の
既読フィルタリングを検証する。

検証観点:
- queue に載っている PJ ごとに ``daily_review.build_review`` の group をそのまま digest 化する
  （新しい group 化ロジックを再発明しない）
- digest 生成は read-only（対象ストアを一切変更しない）
- 同一 idiom テキストが 2 つ以上の異なる PJ に出現する group は global レーンへマージされ、
  per_pj 側から除外される（1 PJ のみの出現は per_pj のまま）
- 1 PJ の digest 生成が例外を投げても他 PJ の digest は失われない（fail-open）
- build_session_proposals は per_pj[pj_slug] + global を結合し、既読 signal_key を含む group を
  除外し、先頭 limit 件だけ返す
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from daily import proposal_digest as pd  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402


def _sig(text: str, line_no: int, pj_slug: str, session_id: str = "s1") -> WeakSignal:
    # source_path に pj_slug を含める: signal_key は channel+provenance のハッシュなので、
    # 同一 text でも実運用どおり PJ ごとに別 transcript（別 source_path）由来なら別キーになる
    # （同一 source_path/line_no だと append_signals の dedup で片方が握り潰される）。
    prov = {"source_path": f"/{pj_slug}.jsonl", "line_no": line_no, "text": text, "reason": "r"}
    return WeakSignal(
        channel="llm_judge",
        provenance=prov,
        detected_at=datetime.now(timezone.utc).isoformat(),
        session_id=session_id,
        pj_slug=pj_slug,
    )


def _queue(*slugs: str) -> list:
    return [{"pj_slug": s} for s in slugs]


# ─────────────────────────────────────────────────────────────────
# build_proposal_digest
# ─────────────────────────────────────────────────────────────────
def test_empty_queue_entries_returns_empty_digest(tmp_path: Path):
    out = pd.build_proposal_digest([], data_dir=tmp_path)
    assert out["per_pj"] == {}
    assert out["global"] == []
    assert "generated_at" in out


def test_pj_without_signals_absent_from_per_pj(tmp_path: Path):
    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert out["per_pj"] == {}


def test_single_pj_group_is_slimmed(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("git statusじゃなくてgit diffを使って", 1, "pj-a")], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    groups = out["per_pj"]["pj-a"]
    assert len(groups) == 1
    g = groups[0]
    assert g["signal_keys"]
    assert g["channel"] == "llm_judge"
    assert g["count"] == 1
    assert "git diff" in g["representative"]
    assert "idiom" in g
    assert "confirmable_idiom" in g


def test_max_per_pj_limits_group_count(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    # jaccard 分離のため keyword が重ならない題材を使う（extract_keywords は数字を落とすため
    # 「別々の指摘そのN」のような templated 文字列は N によらず同一 group に collapse する）。
    texts = [
        "cdを使わずgitのCオプションで実行して",
        "コミットメッセージに共著者表記を付けないで",
        "テストは先に書いて失敗を確認して",
        "変数名は英語表記に統一して",
        "エラーは握りつぶさずログに出力して",
    ]
    sigs = [_sig(t, i, "pj-a") for i, t in enumerate(texts)]
    append_signals(sigs, path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path, max_per_pj=2)
    assert len(out["per_pj"]["pj-a"]) == 2


def test_build_proposal_digest_is_read_only(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("読み取り専用のはず", 1, "pj-a")], path=ws)
    before_files = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}

    pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)

    after_files = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert before_files == after_files


def test_pj_failure_does_not_abort_other_pjs(tmp_path: Path, monkeypatch):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("生きてるPJの指摘", 1, "pj-ok")], path=ws)

    orig = pd._daily_review.build_review

    def _boom(pj_slug, **kwargs):
        if pj_slug == "pj-bad":
            raise RuntimeError("boom")
        return orig(pj_slug, **kwargs)

    monkeypatch.setattr(pd._daily_review, "build_review", _boom)
    out = pd.build_proposal_digest(_queue("pj-bad", "pj-ok"), data_dir=tmp_path)
    assert "pj-bad" not in out["per_pj"]
    assert "pj-ok" in out["per_pj"]


def test_global_lane_merges_same_idiom_text_across_two_pjs(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("git statusじゃなくてgit diffを使って", 1, "pj-a"),
            _sig("git statusじゃなくてgit diffを使って", 1, "pj-b"),
        ],
        path=ws,
    )

    out = pd.build_proposal_digest(_queue("pj-a", "pj-b"), data_dir=tmp_path)
    assert len(out["global"]) == 1
    g = out["global"][0]
    assert len(g["signal_keys"]) == 2
    # 2 PJ 分マージ済みなので per_pj 側からは除外される。
    assert "pj-a" not in out["per_pj"]
    assert "pj-b" not in out["per_pj"]


def test_single_pj_occurrence_stays_in_per_pj_not_global(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("1 PJ にしか無い指摘", 1, "pj-a")], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert out["global"] == []
    assert len(out["per_pj"]["pj-a"]) == 1


def test_global_group_carries_project_paths_from_queue_entries(tmp_path: Path):
    """#412 [Must]4: digest は各 PJ の project_path（queue エントリの絶対パス）を保持する。"""
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("git statusじゃなくてgit diffを使って", 1, "pj-a"),
            _sig("git statusじゃなくてgit diffを使って", 1, "pj-b"),
        ],
        path=ws,
    )
    queue_entries = [
        {"pj_slug": "pj-a", "project_path": "/abs/pj-a"},
        {"pj_slug": "pj-b", "project_path": "/abs/pj-b"},
    ]
    out = pd.build_proposal_digest(queue_entries, data_dir=tmp_path)
    assert out["project_paths"]["pj-a"] == "/abs/pj-a"
    assert out["project_paths"]["pj-b"] == "/abs/pj-b"
    g = out["global"][0]
    assert set(g["keys_by_pj"]) == {"pj-a", "pj-b"}
    assert len(g["keys_by_pj"]["pj-a"]) == 1
    assert len(g["keys_by_pj"]["pj-b"]) == 1


# ─────────────────────────────────────────────────────────────────
# _extract_global_groups（#412 [Must]3: 連結成分マージ）
# ─────────────────────────────────────────────────────────────────
def _slim(keys, rep=None, idiom=None) -> dict:
    return {
        "signal_keys": list(keys),
        "representative": rep,
        "idiom": idiom,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": len(keys),
        "evidence_text": rep or "",
        "prev_action": "",
    }


def test_extract_global_groups_two_pj_same_text_merges():
    per_pj = {
        "pj-a": [_slim(["ka"], rep="共通テキスト")],
        "pj-b": [_slim(["kb"], rep="共通テキスト")],
    }
    global_groups, remaining = pd._extract_global_groups(per_pj)
    assert len(global_groups) == 1
    assert set(global_groups[0]["signal_keys"]) == {"ka", "kb"}
    assert remaining == {}


def test_extract_global_groups_chain_does_not_promote_single_pj_group():
    """A-B が text1（representative）で一致、B-C が text2（B の idiom / C の representative）で
    一致するチェーン。旧実装は「消費前」distinct_slugs>=2 の判定だけで text2 を通し、text1 で
    既に消費済みの B を merge ループで skip した結果、**C 単独**の group が誤って global 扱い
    されていた。連結成分でマージすれば A/B/C の 3 PJ が 1 つの global group に正しく統合される
    （C だけの偽 global group は作られない）。
    """
    per_pj = {
        "pj-a": [_slim(["ka"], rep="共通テキスト1")],
        "pj-b": [_slim(["kb"], rep="共通テキスト1", idiom="共通テキスト2")],
        "pj-c": [_slim(["kc"], rep="共通テキスト2")],
    }
    global_groups, remaining = pd._extract_global_groups(per_pj)
    assert len(global_groups) == 1
    g = global_groups[0]
    assert set(g["signal_keys"]) == {"ka", "kb", "kc"}
    assert set(g["origin_pjs"]) == {"pj-a", "pj-b", "pj-c"}
    assert set(g["keys_by_pj"]) == {"pj-a", "pj-b", "pj-c"}
    assert g["keys_by_pj"]["pj-a"] == ["ka"]
    assert g["keys_by_pj"]["pj-b"] == ["kb"]
    assert g["keys_by_pj"]["pj-c"] == ["kc"]
    # C 単独の偽 global group が per_pj に取りこぼされていないこと（全 3 PJ が global に吸収済み）
    assert remaining == {}


def test_extract_global_groups_single_pj_component_stays_per_pj():
    per_pj = {
        "pj-a": [_slim(["ka"], rep="孤立テキスト")],
    }
    global_groups, remaining = pd._extract_global_groups(per_pj)
    assert global_groups == []
    assert remaining == per_pj


# ─────────────────────────────────────────────────────────────────
# build_session_proposals
# ─────────────────────────────────────────────────────────────────
def _group(keys, rep="rep") -> dict:
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


def test_build_session_proposals_returns_empty_for_non_dict_queue_data():
    assert pd.build_session_proposals(None, "pj-a", seen_keys=set()) == []


def test_build_session_proposals_returns_empty_when_no_proposals_key():
    assert pd.build_session_proposals({}, "pj-a", seen_keys=set()) == []


def test_build_session_proposals_combines_per_pj_and_global():
    queue_data = {
        "proposals": {
            "per_pj": {"pj-a": [_group(["k1"])]},
            "global": [_group(["k2"])],
        }
    }
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys=set())
    assert len(out) == 2


def test_build_session_proposals_excludes_other_pj():
    queue_data = {
        "proposals": {
            "per_pj": {"pj-a": [_group(["k1"])], "pj-b": [_group(["k2"])]},
            "global": [],
        }
    }
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys=set())
    assert [g["signal_keys"] for g in out] == [["k1"]]


def test_build_session_proposals_excludes_group_with_any_seen_key():
    queue_data = {
        "proposals": {
            "per_pj": {"pj-a": [_group(["k1", "k2"]), _group(["k3"])]},
            "global": [],
        }
    }
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys={"k2"})
    assert [g["signal_keys"] for g in out] == [["k3"]]


def test_build_session_proposals_respects_limit():
    groups = [_group([f"k{i}"]) for i in range(5)]
    queue_data = {"proposals": {"per_pj": {"pj-a": groups}, "global": []}}
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys=set(), limit=2)
    assert len(out) == 2


def test_default_limit_is_max_session_proposals_constant():
    groups = [_group([f"k{i}"]) for i in range(pd.MAX_SESSION_PROPOSALS + 3)]
    queue_data = {"proposals": {"per_pj": {"pj-a": groups}, "global": []}}
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys=set())
    assert len(out) == pd.MAX_SESSION_PROPOSALS


# ─────────────────────────────────────────────────────────────────
# build_proposal_prompt
# ─────────────────────────────────────────────────────────────────
def test_build_proposal_prompt_contains_representative_and_commands():
    groups = [_group(["k1", "k2"], rep="テスト前にRED実測")]
    msg = pd.build_proposal_prompt(groups, "pj-a")
    assert "テスト前にRED実測" in msg
    assert "AskUserQuestion" in msg
    assert "bin/evolve-reflect --promote-weak k1,k2" in msg
    assert "bin/evolve-reflect --reject-weak k1,k2 --pj pj-a" in msg


def test_build_proposal_prompt_honors_absolute_reflect_cmd():
    """提示先は他 PJ の cwd なので、呼び出し元が渡す絶対パスを埋め込むこと。

    相対 ``bin/evolve-reflect`` のままだと対象 PJ に該当ファイルが無く No such file になる
    （pitfall_skill_md_plugin_root と同型の失敗）。
    """
    groups = [_group(["k1"], rep="rep")]
    msg = pd.build_proposal_prompt(groups, "pj-a", reflect_cmd="/abs/plugin/bin/evolve-reflect")
    assert "/abs/plugin/bin/evolve-reflect --promote-weak k1" in msg
    assert "/abs/plugin/bin/evolve-reflect --reject-weak k1 --pj pj-a" in msg
    assert "\n  はい: bin/evolve-reflect" not in msg


def test_build_proposal_prompt_instructs_after_first_reply_not_interrupt():
    """#412 [Must]1: additionalContext は「ユーザーの最初の応答を終えた直後」に発火させる
    行動指示にする（従来の「依頼が無い場合のみ」だと永久に発火しない）。
    """
    groups = [_group(["k1"], rep="rep")]
    msg = pd.build_proposal_prompt(groups, "pj-a")
    assert "AskUserQuestion" in msg
    assert "最初のメッセージへの応答を終えた直後" in msg
    assert "割り込ま" in msg  # 「ユーザーの依頼より先に割り込むな」系の文言


def test_build_proposal_prompt_global_group_emits_per_origin_pj_commands():
    """#412 [Must]4: global group（keys_by_pj あり）は origin PJ ごとに --project-path/--pj を
    明示したコマンド行を出す。他PJ由来の signal が現在 PJ に誤帰属しないようにするため。
    """
    group = {
        "signal_keys": ["ka", "kb"],
        "representative": "共通テキスト",
        "idiom": None,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": 2,
        "evidence_text": "共通テキスト",
        "prev_action": "",
        "keys_by_pj": {"pj-a": ["ka"], "pj-b": ["kb"]},
        "origin_pjs": ["pj-a", "pj-b"],
    }
    project_paths = {"pj-a": "/abs/pj-a", "pj-b": "/abs/pj-b"}
    msg = pd.build_proposal_prompt(
        [group], "pj-a", reflect_cmd="/abs/bin/evolve-reflect", project_paths=project_paths,
    )
    assert "/abs/bin/evolve-reflect --promote-weak ka --project-path /abs/pj-a --pj pj-a" in msg
    assert "/abs/bin/evolve-reflect --reject-weak ka --pj pj-a" in msg
    assert "/abs/bin/evolve-reflect --promote-weak kb --project-path /abs/pj-b --pj pj-b" in msg
    assert "/abs/bin/evolve-reflect --reject-weak kb --pj pj-b" in msg


# ─────────────────────────────────────────────────────────────────
# build_proposal_systemmessage（#412 [Must]1: 2チャネル同時出力）
# ─────────────────────────────────────────────────────────────────
def test_build_proposal_systemmessage_lists_representatives():
    groups = [_group(["k1"], rep="rep1"), _group(["k2"], rep="rep2")]
    msg = pd.build_proposal_systemmessage(groups)
    assert "rep1" in msg
    assert "rep2" in msg
    assert "y/n" in msg


def test_build_proposal_systemmessage_caps_at_max_session_proposals():
    groups = [_group([f"k{i}"], rep=f"rep{i}") for i in range(5)]
    msg = pd.build_proposal_systemmessage(groups)
    assert "rep0" in msg
    assert "rep1" in msg
    assert "rep2" not in msg
