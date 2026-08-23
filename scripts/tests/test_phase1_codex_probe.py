"""scripts/phase1_codex_probe.py のテスト（ADR-055 Phase 1・#534）。

決定論・LLM 非依存（LLM は本スクリプトから一切呼ばない設計）。
``verify-checks-by-breaking.md`` に従い、各テストの docstring に
「壊す不変条件」と「通したい検査経路」を明記する。①〜④の変異と、ADR
Test Plan C-2 の追加変異（#5〜#9）を実際に適用して赤くなることを確認済み
（結果は実装完了報告に記載。本ファイルには正実装に対する検査のみを残す）。
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import List, Tuple

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import phase1_codex_probe as p  # noqa: E402


# ─────────────────────────────────────────────────────────────────
# real_home テスト用 skip ガード（#536 team-lead 追加確認）
#
# real_home マーカーは root conftest の HOME 隔離を opt-out するだけで、対象
# ディレクトリ（実 ~/.codex/sessions）の実在は保証しない。実在チェックを
# 怠ると、実 Codex セッションが無い環境（CI ランナー・別ユーザーの開発機等）
# では target_files=0 のまま _assert_stage_counts_plausible の下限チェック
# （227件）に引っかかり、意味のある失敗ではなく「環境にデータが無い」だけの
# ことで赤くなる。実測（HOME を実 ~/.codex を含まない一時ディレクトリへ
# 差し替えて実行）で target_files=0 → 2/4 の real_home テストが実際に FAILED
# することを確認済み。
#
# skip 理由は必ず出す（silence != evaluated）。黙って skip して緑に見せない。
_REAL_CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
_skip_if_no_real_codex_sessions = pytest.mark.skipif(
    not _REAL_CODEX_SESSIONS_ROOT.exists(),
    reason=(
        f"{_REAL_CODEX_SESSIONS_ROOT} が存在しません。real_home テストは実 Codex "
        "セッションが存在する環境でのみ意味を持つ検査のため skip します（#536）。"
    ),
)


# ─────────────────────────────────────────────────────────────────
# fixture helpers
# ─────────────────────────────────────────────────────────────────
def _session_meta(sid: str, cwd: str = "/Users/x/matsukaze-utils/evolve-anything", ts="2026-08-20T00:00:00.000Z") -> str:
    return json.dumps({"timestamp": ts, "type": "session_meta", "payload": {"id": sid, "cwd": cwd}})


def _response_user(text: str, ts="2026-08-20T00:00:01.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    })


def _response_role(role: str, text: str, ts="2026-08-20T00:00:01.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]},
    })


def _event_user_message(text: str, ts="2026-08-20T00:00:01.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "event_msg",
        "payload": {"type": "user_message", "message": text},
    })


def _sub_agent_activity(agent_thread_id: str, ts="2026-08-20T00:00:02.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "event_msg",
        "payload": {"type": "sub_agent_activity", "agent_thread_id": agent_thread_id, "kind": "started"},
    })


def _inter_agent_marker(ts="2026-08-20T00:00:03.000Z") -> str:
    return json.dumps({"timestamp": ts, "type": "inter_agent_communication_metadata", "payload": {"trigger_turn": False}})


def _unknown_record(ts="2026-08-20T00:00:04.000Z") -> str:
    # tool_search_call は Must1 で既知のツール呼び出し組になったため、ここでは
    # 未知組の代表として genuinely 未知の組を使う。
    return json.dumps({"timestamp": ts, "type": "response_item", "payload": {"type": "totally_unknown_type"}})


def _function_call(name: str, ts="2026-08-20T00:00:04.000Z") -> str:
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "function_call", "name": name, "call_id": "c1"},
    })


def _write(tmp_path: Path, name: str, lines) -> Path:
    p_ = tmp_path / name
    p_.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p_


# ─────────────────────────────────────────────────────────────────
# 先頭タグ判定（D3）
# ─────────────────────────────────────────────────────────────────
def test_all_nine_machinery_markers_detected():
    """壊す不変条件: D3（機構9種の網羅）／検査経路: is_machinery_text の marker set。"""
    for marker in sorted(p.MACHINERY_MARKERS):
        assert p.is_machinery_text(f"<{marker}>\nbody") is True
    assert p.is_machinery_text("<not_a_marker>\nbody") is False


# 実装定数 (p.MACHINERY_MARKERS) からも独立オラクル定数 (p._ORACLE_MACHINERY_MARKERS)
# からも一切 import・反復しない、テスト自身がリテラルで書き下した第三の集合
# （#536 round3 codex Must1）。これと両定数を突合することで、どちらか片方だけ
# マーカーが削除・改変されても検出できる（両方が同時に同じ変異を受けない限り）。
_INDEPENDENT_LITERAL_MACHINERY_MARKERS = frozenset(
    {
        "recommended_plugins",
        "task-notification",
        "command-name",
        "local-command-stdout",
        "command-message",
        "skill",
        "environment_context",
        "user_action",
        "image",
    }
)


def test_machinery_marker_set_matches_independent_literal():
    """壊す不変条件: item5（MACHINERY_MARKERS からの要素削除・改変を検出）
    ／通したい検査経路: 実装定数・独立オラクル定数それぞれと、テストが直書きした
    リテラル集合との完全一致比較。
    ``p.MACHINERY_MARKERS`` から要素を1件削っても is_machinery_text 自体は
    変更されないため実装内テストは通ってしまうが、本テストはリテラル集合との
    差分として検出する。
    """
    assert p.MACHINERY_MARKERS == _INDEPENDENT_LITERAL_MACHINERY_MARKERS
    assert p._ORACLE_MACHINERY_MARKERS == _INDEPENDENT_LITERAL_MACHINERY_MARKERS


# 入力テキスト → 期待生存可否 の fixture。ラベルは is_machinery_text /
# _oracle_is_machinery_text のどちらも呼ばずに、人手でテキストを読んで判定した
# 期待値（#536 round3 codex Must1「入力→期待生存発話の fixture を実装定数・
# 判定関数から独立して定義する」）。
_INDEPENDENT_SURVIVAL_FIXTURE: List[Tuple[str, bool]] = [
    ("<recommended_plugins>\n一覧です", False),  # 9種の先頭
    ("<task-notification>\n通知", False),
    ("<command-name>ls</command-name>", False),
    ("<local-command-stdout>...</local-command-stdout>", False),
    ("<command-message>...</command-message>", False),
    ("<skill>evolve</skill>", False),
    ("<environment_context>...</environment_context>", False),
    ("<user_action>click</user_action>", False),
    ("<image>base64...</image>", False),
    ("普通の発話です。よろしくお願いします。", True),  # 通常発話
    ("<not_a_marker>本文", True),  # タグはあるが機構マーカーでない
    ("普通の発話に <recommended_plugins> という単語が混じるだけ", True),  # 先頭でない
    ("﻿  \n<skill>evolve</skill>", False),  # BOM・空白混入後の機構タグ
    ("", True),  # 空文字は機構でない
    ("<recommended_plugins", True),  # 閉じ `>` が無く head_tag 相当は不成立
    # #536 round4 codex Must: 全角空白（U+3000）は strip_leading_noise の対象文字集合
    # （"﻿ \t\n\r　"）に含まれる表現差クラス。ASCII 空白・BOM・改行だけの fixture
    # では、全角空白 strip 対応を削る変異（変異1）を検出できない。
    ("　<skill>evolve</skill>", False),  # 全角空白のみ先頭に混入した機構タグ
    ("　\n<recommended_plugins>\n一覧です", False),  # 全角空白+改行の複合
    # #536 round4 codex Must: head_tag は `<tag ...>` の inner を空白分割し先頭語を
    # タグ名とする（属性付きタグにも対応する契約）。属性なしタグしか無い fixture
    # では、この分割対応を削る変異（変異2）を検出できない。
    ('<skill name="evolve">x</skill>', False),  # 属性付き機構タグ（9種のうち1つを代表）
    ('<command-name value="ls">x</command-name>', False),  # 属性付き・両チャネル共通マーカー
    ('<not_a_marker attr="x">本文</not_a_marker>', True),  # 属性付きだが機構マーカーでない（陽性対照）
    # #536 チームリード追加確認: 自己閉じ形（スラッシュの前に空白が無い）は
    # `inner.split()[0]` が "image/" のようにスラッシュ込みで返るため、
    # 正規化を怠ると不一致になる表現差クラス（実データ ~/.codex/sessions 726
    # ファイル・実候補 6467 件を実測し出現数0件を確認済みだが、迷ったら除外
    # せず検査対象に倒す方針で対応する）。
    ("<image/>", False),  # 自己閉じ・スラッシュ直前に空白なし
    ("<skill/>", False),  # 同上
    ("<image />", False),  # 自己閉じ・スラッシュ直前に空白あり（元々検出できていた形）
    ("<not_a_marker/>", True),  # 自己閉じだが機構マーカーでない（陽性対照）
    # タグ名の大小文字ゆれ。実データでは出現0件を実測済みだが、同じ理由で
    # 正規化する（Codex 側の出力ゆれで大文字化される可能性を排除できないため）。
    ("<SKILL>evolve</SKILL>", False),  # 全大文字
    ("<Skill>evolve</Skill>", False),  # 先頭大文字
    ("<COMMAND-NAME>ls</COMMAND-NAME>", False),  # 全大文字・両チャネル共通マーカー
    ("<Not_A_Marker>本文</Not_A_Marker>", True),  # 大文字だが機構マーカーでない（陽性対照）
]


def test_filter_machinery_matches_independently_labeled_fixture():
    """壊す不変条件: item5（マーカー文字列の改変・swap・判定弱体化を、実装からの
    独立算出でなく人手ラベル済み fixture との突合で検出）
    ／通したい検査経路: filter_machinery（run_probe の唯一の適用点と同じ関数）。
    """
    candidates = [
        p.RawCandidate(
            file="f.jsonl", channel="response_item", line_no=i, timestamp="2026-08-20T00:00:00.000Z",
            ts_ms=0.0, text=text, cwd=None, session_id="s1", prev_action=None,
        )
        for i, (text, _expected) in enumerate(_INDEPENDENT_SURVIVAL_FIXTURE)
    ]
    expected_survivors = [text for text, expected in _INDEPENDENT_SURVIVAL_FIXTURE if expected]
    actual_survivors = [c.text for c in p.filter_machinery(candidates)]
    assert actual_survivors == expected_survivors


def test_developer_role_excluded_by_reducer():
    """壊す不変条件: D3（developer role はユーザー発話でない）
    ／通したい検査経路: parse_session_file の role dispatch。
    変異①（役割フィルタの削除）を適用すると本テストが単独で落ちる
    （developer 発話が candidates に混入する）。
    """
    lines = [_session_meta("s1"), _response_role("developer", "system prompt injection")]
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as td:
        path = _write(Path(td), "f.jsonl", lines)
        pf = p.parse_session_file(path)
    assert pf.candidates == []


def test_bom_and_whitespace_before_tag_still_detected():
    """壊す不変条件: D3 表現差（BOM・空白・改行を先頭タグの前に混入させても除外できる）
    ／通したい検査経路: strip_leading_noise → head_tag。
    変異②（strip 呼び出し削除）を適用すると本テストが単独で落ちる。
    """
    text = "﻿  \n<recommended_plugins>\nHere is a list..."
    assert p.is_machinery_text(text) is True


def test_marker_like_text_not_at_head_is_not_excluded():
    """陽性対照: 先頭以外に marker 文字列を含む本文（意味を変えない差分）は除外されない。"""
    text = "普通の発話です。<recommended_plugins> という単語だけ本文中に出てくる。"
    assert p.is_machinery_text(text) is False


# I4（#536 round4 codex Must）: 除外結果は入力チャネルに依存しない。
# ADR（docs/decisions/drafts/055-codex-rollout-ingest.md:281-283）は
# command-name / local-command-stdout / command-message の3種を
# response_item / event_msg 両チャネル共通と実測している。人手ラベル済み
# fixture が全件 channel="response_item" 固定だと、event_msg チャネルだけを
# 無条件生存させる（あるいはその逆）配線の欠陥を検出できない。
_CHANNEL_INDEPENDENT_FIXTURE: List[Tuple[str, str, bool]] = [
    ("<command-name>ls</command-name>", "response_item", False),
    ("<command-name>ls</command-name>", "event_msg", False),
    ("<local-command-stdout>...</local-command-stdout>", "response_item", False),
    ("<local-command-stdout>...</local-command-stdout>", "event_msg", False),
    ("<command-message>...</command-message>", "response_item", False),
    ("<command-message>...</command-message>", "event_msg", False),
    ("普通の発話です。", "response_item", True),
    ("普通の発話です。", "event_msg", True),
]


def test_filter_machinery_is_channel_independent():
    """壊す不変条件: I4（除外結果は入力チャネルに依存しない）
    ／通したい検査経路: filter_machinery（is_machinery_text はテキストのみを見て
    判定し channel を参照しないという設計契約）。
    変異（filter_machinery を channel=="event_msg" のときだけ無条件生存させる等）
    を適用すると、同一マーカーが片方のチャネルでのみ生存し本テストが落ちる。
    """
    candidates = [
        p.RawCandidate(
            file="f.jsonl", channel=channel, line_no=i, timestamp="2026-08-20T00:00:00.000Z",
            ts_ms=0.0, text=text, cwd=None, session_id="s1", prev_action=None,
        )
        for i, (text, channel, _expected) in enumerate(_CHANNEL_INDEPENDENT_FIXTURE)
    ]
    expected_survivors = [
        (text, channel) for text, channel, expected in _CHANNEL_INDEPENDENT_FIXTURE if expected
    ]
    actual_survivors = [(c.text, c.channel) for c in p.filter_machinery(candidates)]
    assert actual_survivors == expected_survivors


# ─────────────────────────────────────────────────────────────────
# D4: 子セッション除外
# ─────────────────────────────────────────────────────────────────
def test_child_session_file_excluded_with_both_markers(tmp_path):
    """壊す不変条件: D4（子セッションのファイル単位除外）
    ／通したい検査経路: split_parent_child が sub_agent_activity.agent_thread_id と
    session_meta.id の一致で判定する。fixture は codex v3 レビュー指摘どおり
    sub_agent_activity と inter_agent_communication_metadata の**両方**を含む
    （変異#6: sub_agent_activity の判定条件をトップレベル type 誤認に差し替えると、
    inter_agent_communication_metadata の存在下でも本テストが単独で落ちる）。
    """
    parent_lines = [
        _session_meta("parent-1"),
        _response_user("親から見た発話"),
        _sub_agent_activity("child-1"),
        _inter_agent_marker(),
    ]
    child_lines = [
        _session_meta("child-1"),
        _response_user("子セッション内の発話（誤って人間発話として拾われてはいけない）"),
    ]
    parent_path = _write(tmp_path, "parent.jsonl", parent_lines)
    child_path = _write(tmp_path, "child.jsonl", child_lines)

    parsed = [p.parse_session_file(parent_path), p.parse_session_file(child_path)]
    ref = p.build_agent_thread_id_set(parsed)
    parents, children = p.split_parent_child(parsed, ref)

    assert {c.path for c in children} == {str(child_path)}
    assert {pf.path for pf in parents} == {str(parent_path)}


@pytest.mark.real_home
@_skip_if_no_real_codex_sessions
def test_child_reference_scope_phase1_vs_full_agreement():
    """壊す不変条件: X2（Phase1限定走査と全走査が一致する）
    ／通したい検査経路: run_probe の child_ref_scope_agreement フラグ。
    """
    result = p.run_probe(
        sessions_root=Path.home() / ".codex" / "sessions",
        base_date=date(2026, 8, 23),
        days=14,
    )
    assert result.child_ref_scope_agreement is True


# ─────────────────────────────────────────────────────────────────
# D5a: セグメント帰属（最重要の変異#9）
# ─────────────────────────────────────────────────────────────────
def test_segment_attribution_uses_most_recent_preceding_session_meta(tmp_path):
    """壊す不変条件: D5a（発話は自分のセグメントの session_meta に帰属する）
    ／通したい検査経路: parse_session_file の current_session_id 更新ロジック。

    複数 session_meta・各セグメントに user 発話・セグメントごとに異なる期待
    session_id を持つ fixture（codex v3 [Must] 反映）。変異#9（先頭ID一律付与へ
    巻き戻し）を適用すると、2番目のセグメントの発話が誤って "seg-A" に帰属し、
    本テストが単独で落ちる（単一 session_meta の fixture では検出できない）。
    """
    lines = [
        _session_meta("seg-A", ts="2026-08-20T00:00:00.000Z"),
        _response_user("セグメントAの発話", ts="2026-08-20T00:00:01.000Z"),
        _session_meta("seg-B", ts="2026-08-20T00:01:00.000Z"),
        _response_user("セグメントBの発話", ts="2026-08-20T00:01:01.000Z"),
    ]
    path = _write(tmp_path, "resume.jsonl", lines)
    pf = p.parse_session_file(path)

    assert len(pf.candidates) == 2
    by_text = {c.text: c.session_id for c in pf.candidates}
    assert by_text["セグメントAの発話"] == "seg-A"
    assert by_text["セグメントBの発話"] == "seg-B"


def test_utterance_before_any_session_meta_is_dropped_and_counted(tmp_path):
    """壊す不変条件: D5a（未帰属発話は採用せず件数を surface する。黙って落とさない）
    ／通したい検査経路: parse_session_file の unattributed_count。
    """
    lines = [_response_user("session_meta より前の発話")]
    path = _write(tmp_path, "no_meta.jsonl", lines)
    pf = p.parse_session_file(path)

    assert pf.candidates == []
    assert pf.unattributed_count == 1


# ─────────────────────────────────────────────────────────────────
# X1: チャネル制約付き dedup
# ─────────────────────────────────────────────────────────────────
def test_dedup_merges_matching_pair_across_channels():
    """壊す不変条件: X1（二重表現を単一発話へ正規化する）
    ／通したい検査経路: dedup_channel_constrained。
    変異#5（dedup を適用せず両チャネルをそのまま emit）を適用すると、
    このテストは2件のまま単独で落ちる。
    """
    r1 = p.RawCandidate("f1", "response_item", 1, "2026-08-20T00:00:00.010Z", 1000.010 * 1000, "同じ発話", None, "s1", None)
    e1 = p.RawCandidate("f1", "event_msg", 2, "2026-08-20T00:00:00.000Z", 1000.0 * 1000, "同じ発話", None, "s1", None)
    out = p.dedup_channel_constrained([r1, e1], threshold_ms=100)
    assert len(out) == 1
    assert out[0].channel == "response_item"  # response_item 側を代表として残す


def test_dedup_keeps_event_msg_only_utterance_independent():
    """壊す不変条件: M1/X1（event_msg のみに存在する発話が失われないこと）
    ／通したい検査経路: dedup_channel_constrained の未マッチ独立残存。
    変異#7（response_item 側だけを実装し event_msg.user_message を丸ごと落とす）を
    実装レベルで適用すると（parse_session_file の event_msg 分岐を削除）、
    run_probe 経由の統合テストでこの発話が消え、後述の統合テストが単独で落ちる。
    """
    e1 = p.RawCandidate("f1", "event_msg", 1, "2026-08-20T00:00:00.000Z", 1000.0, "event_msgだけの発話", None, "s1", None)
    out = p.dedup_channel_constrained([e1], threshold_ms=100)
    assert len(out) == 1
    assert out[0].channel == "event_msg"


def test_dedup_does_not_merge_beyond_threshold():
    """陽性対照: delta が閾値を超える候補は誤統合されない（意味を変えない差分ではなく
    正しい境界動作の確認）。"""
    r1 = p.RawCandidate("f1", "response_item", 1, "2026-08-20T00:00:00.500Z", 500.0, "text", None, "s1", None)
    e1 = p.RawCandidate("f1", "event_msg", 2, "2026-08-20T00:00:00.000Z", 0.0, "text", None, "s1", None)
    out = p.dedup_channel_constrained([r1, e1], threshold_ms=100)
    assert len(out) == 2


def test_event_msg_channel_survives_in_full_parse(tmp_path):
    """壊す不変条件: M1/X1（event_msg.user_message が parse_session_file で拾われる）
    ／通したい検査経路: parse_session_file の event_msg 分岐。
    変異#7 の直接検査（parse 段階）。event_msg 分岐を削除すると本テストが単独で落ちる。
    """
    lines = [_session_meta("s1"), _event_user_message("event_msg経路だけの発話")]
    path = _write(tmp_path, "evm_only.jsonl", lines)
    pf = p.parse_session_file(path)
    assert [c.text for c in pf.candidates] == ["event_msg経路だけの発話"]
    assert pf.candidates[0].channel == "event_msg"


# ─────────────────────────────────────────────────────────────────
# X4: 未知 type の扱い（version フィルタをしない）
# ─────────────────────────────────────────────────────────────────
def test_unknown_type_pair_is_skipped_and_surfaced(tmp_path):
    """壊す不変条件: X4（未知の (type, payload.type) 組を静かに落とさず件数を出す）
    ／通したい検査経路: parse_session_file の unknown_type_pairs カウンタ。
    """
    lines = [_session_meta("s1"), _unknown_record(), _response_user("正常な発話")]
    path = _write(tmp_path, "unknown.jsonl", lines)
    pf = p.parse_session_file(path)
    assert pf.unknown_type_pairs[("response_item", "totally_unknown_type")] == 1
    assert [c.text for c in pf.candidates] == ["正常な発話"]


def test_no_cli_version_based_filtering_in_source():
    """壊す不変条件: X4（『Phase1観測versionのみ許可』方式は不採用。version 自体を見ない）
    ／通したい検査経路: ソースコードに cli_version 依存の分岐が無いこと（静的検査）。
    """
    src = Path(p.__file__).read_text(encoding="utf-8")
    assert "cli_version" not in src


# ─────────────────────────────────────────────────────────────────
# X3: 日付ディレクトリ方式のファイル選定
# ─────────────────────────────────────────────────────────────────
def test_iter_date_dir_files_respects_window(tmp_path):
    """壊す不変条件: X3（日付ディレクトリ方式・ウィンドウ外を含めない）
    ／通したい検査経路: iter_date_dir_files。
    """
    root = tmp_path / "sessions"
    for y, m, d, fname in [
        ("2026", "08", "23", "in_range.jsonl"),
        ("2026", "08", "09", "out_of_range.jsonl"),  # 14日窓の外（基準日8/23なら8/10が下限）
    ]:
        dpath = root / y / m / d
        dpath.mkdir(parents=True)
        (dpath / fname).write_text("{}\n", encoding="utf-8")

    files = p.iter_date_dir_files(root, date(2026, 8, 23), 14)
    names = {f.name for f in files}
    assert "in_range.jsonl" in names
    assert "out_of_range.jsonl" not in names


# ─────────────────────────────────────────────────────────────────
# 本番ストア非汚染ガード
# ─────────────────────────────────────────────────────────────────
def test_production_guard_detects_new_file_creation():
    """壊す不変条件: C-1（実行前は不在だったが実行後に出現したら失敗）
    ／通したい検査経路: verify_production_unchanged。
    変異④相当（検査を無効化・常に成功扱いにする）を適用すると本テストが単独で落ちる。
    """
    before = {"utterances_db": None}
    after = {"utterances_db": "deadbeef"}
    ok, violations = p.verify_production_unchanged(before, after)
    assert ok is False
    assert "utterances_db" in violations[0]


def test_production_guard_passes_when_both_absent():
    """陽性対照: 実行前後とも不在なら合格（新規作成のみを失敗として扱う）。"""
    ok, violations = p.verify_production_unchanged({"x": None}, {"x": None})
    assert ok is True
    assert violations == []


def test_production_guard_passes_when_unchanged_present_file():
    """陽性対照: 既存ファイルの hash が実行前後で同一なら合格。"""
    ok, violations = p.verify_production_unchanged({"x": "abc"}, {"x": "abc"})
    assert ok is True


# ─────────────────────────────────────────────────────────────────
# 実データ E2E（C-1・実機ベンチ）
# ─────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────
# C-1: 実データ regression の桁チェック。
#
# 旧実装は ADR 実測値（2026-08-23 早朝）に対して厳密一致・狭いレンジで
# assert していたため、実 ~/.codex/sessions が日々増える限り毎日落ちる作りに
# なっていた（テスト名は order_of_magnitude＝桁が合っていることの検査のはず
# なのに、実装は完全一致を要求していて名実が食い違っていた）。
#
# 下限は ADR 基準値（データは単調増加想定。削除運用は無い前提）。
# 上限は基準値のおよそ2.2倍。根拠: 本修正当日（2026-08-23）の実測で
# target_files が早朝227→同日昼231へ半日弱で +4 件増加した実ペースに対し、
# 数ヶ月分の通常運用増加を吸収しつつ、重複カウント・暴走等の異常な増加は
# 検出できる余裕として採用した（厳密な統計的根拠ではなく実測ペースからの
# 経験的マージン。今後乖離が大きくなったら基準値ごと更新すること）。
_ADR_BASELINE_LOWER_COUNTS = {
    "target_files": 227,
    "raw": 769,
    "after_child_exclusion": 503,
    "after_machinery_exclusion": 373,
    "after_dedup": 259,
}
_ADR_BASELINE_UPPER_COUNTS = {
    "target_files": 500,
    "raw": 1700,
    "after_child_exclusion": 1100,
    "after_machinery_exclusion": 820,
    "after_dedup": 570,
}


def _assert_stage_counts_plausible(c: "p.StageCounts") -> None:
    """段階カウントが「桁が合っている」ことを検査する。

    (1) 各段階が ADR 基準値の下限〜上限レンジ内であること（stale exact-match
    対策。レンジは上のモジュール定数が単一ソース）。
    (2) パイプライン構造上必ず成り立つ段階間の非増加関係
    （raw >= after_child_exclusion >= after_machinery_exclusion >= after_dedup）。
    これは実データの増減に関係なく常に成り立つべき不変条件で、絶対値レンジと
    違って将来も陳腐化しない。
    """
    for name, lower in _ADR_BASELINE_LOWER_COUNTS.items():
        value = getattr(c, name)
        upper = _ADR_BASELINE_UPPER_COUNTS[name]
        assert lower <= value <= upper, (
            f"{name}={value} は想定レンジ [{lower}, {upper}] 外です"
        )
    assert c.raw >= c.after_child_exclusion >= c.after_machinery_exclusion >= c.after_dedup >= 0, (
        "段階間の非増加関係が崩れています: "
        f"raw={c.raw} after_child_exclusion={c.after_child_exclusion} "
        f"after_machinery_exclusion={c.after_machinery_exclusion} after_dedup={c.after_dedup}"
    )
    assert c.target_files > 0


def test_assert_stage_counts_plausible_rejects_below_lower_bound():
    """陰性試験: 下限未満（データ欠落・収集退行を模す）は赤くなる。
    ／通したい検査経路: _assert_stage_counts_plausible の下限チェック。
    """
    c = p.StageCounts(
        target_files=1, raw=1, after_child_exclusion=1,
        after_machinery_exclusion=1, after_dedup=1,
        unattributed_dropped=0, child_files=0, parse_error_lines=0,
    )
    with pytest.raises(AssertionError):
        _assert_stage_counts_plausible(c)


def test_assert_stage_counts_plausible_rejects_above_upper_bound():
    """陰性試験: 上限超過（重複カウント・暴走を模す）は赤くなる。
    ／通したい検査経路: _assert_stage_counts_plausible の上限チェック。
    """
    c = p.StageCounts(
        target_files=10_000, raw=10_000, after_child_exclusion=10_000,
        after_machinery_exclusion=10_000, after_dedup=10_000,
        unattributed_dropped=0, child_files=0, parse_error_lines=0,
    )
    with pytest.raises(AssertionError):
        _assert_stage_counts_plausible(c)


def test_assert_stage_counts_plausible_rejects_broken_stage_ordering():
    """陰性試験: 段階間の非増加関係が崩れている（フィルタが効いていない等）
    と、絶対値レンジ内でも赤くなる。
    ／通したい検査経路: _assert_stage_counts_plausible の段階間不変条件チェック。
    """
    c = p.StageCounts(
        target_files=300, raw=800, after_child_exclusion=900,  # raw を超える
        after_machinery_exclusion=400, after_dedup=300,
        unattributed_dropped=0, child_files=0, parse_error_lines=0,
    )
    with pytest.raises(AssertionError):
        _assert_stage_counts_plausible(c)


def test_assert_stage_counts_plausible_accepts_baseline_boundary():
    """陽性対照: ADR 基準値そのもの（下限の境界値）は許容される。"""
    c = p.StageCounts(
        target_files=227, raw=769, after_child_exclusion=503,
        after_machinery_exclusion=373, after_dedup=259,
        unattributed_dropped=0, child_files=0, parse_error_lines=0,
    )
    _assert_stage_counts_plausible(c)  # 例外が出ないこと自体が検査


@pytest.mark.real_home
@_skip_if_no_real_codex_sessions
def test_run_probe_against_real_codex_sessions_matches_expected_order_of_magnitude():
    """壊す不変条件: C-1（実データで完走し段階件数が ADR 実測値と整合する）
    ／通したい検査経路: run_probe のパイプライン全体（実 ~/.codex/sessions を読む）
    + _assert_stage_counts_plausible（桁レンジ・段階間不変条件）
    + assert_machinery_exclusion_matches_oracle（item5・実データでの独立オラクル突合）。
    """
    result = p.run_probe(
        sessions_root=Path.home() / ".codex" / "sessions",
        base_date=date(2026, 8, 23),
        days=14,
    )
    c = result.counts
    _assert_stage_counts_plausible(c)
    assert c.parse_error_lines == 0
    p.assert_machinery_exclusion_matches_oracle(result)


@pytest.mark.real_home
@_skip_if_no_real_codex_sessions
def test_run_probe_does_not_touch_production_stores():
    """壊す不変条件: C-3（本番ストアに一切書かない）
    ／通したい検査経路: run_probe 実行前後で本番3ストア + utterances.db の byte hash 不変。
    """
    paths = p.production_store_paths()
    before = p.snapshot_production_hashes(paths)
    p.run_probe(
        sessions_root=Path.home() / ".codex" / "sessions",
        base_date=date(2026, 8, 23),
        days=14,
    )
    after = p.snapshot_production_hashes(paths)
    ok, violations = p.verify_production_unchanged(before, after)
    assert ok is True, violations


# ─────────────────────────────────────────────────────────────────
# Must1: prev_action（ツール呼び出し名の集約）
# ─────────────────────────────────────────────────────────────────
def test_prev_action_collects_only_tool_calls_since_last_user_utterance(tmp_path):
    """壊す不変条件: Must1（prev_action は「直前の human 発話より後・当該発話より前」の
    tool 呼び出しのみを含む。CC 側 extractor.py の定義と同一）
    ／通したい検査経路: parse_session_file の pending_tool_names 集約・リセット。

    変異（直前の user 発話より前の tool 呼び出しまで含めてしまう＝リセットしない）を
    適用すると、2番目の発話の prev_action に "toolA" が二重に混入し本テストが落ちる。
    """
    lines = [
        _session_meta("s1", ts="2026-08-20T00:00:00.000Z"),
        _response_user("最初の発話", ts="2026-08-20T00:00:01.000Z"),
        _function_call("toolA", ts="2026-08-20T00:00:02.000Z"),
        _function_call("toolB", ts="2026-08-20T00:00:03.000Z"),
        _response_user("2番目の発話", ts="2026-08-20T00:00:04.000Z"),
        # 3番目: 直前(2番目)以降に tool 呼び出しが無い。reset が効いていれば None、
        # 効いていなければ toolA/toolB が漏れ出す（mutationA の検出点はここ）。
        _response_user("3番目の発話", ts="2026-08-20T00:00:05.000Z"),
    ]
    path = _write(tmp_path, "prev_action.jsonl", lines)
    pf = p.parse_session_file(path)
    by_text = {c.text: c.prev_action for c in pf.candidates}
    assert by_text["最初の発話"] is None
    assert by_text["2番目の発話"] == "toolA,toolB"
    assert by_text["3番目の発話"] is None


def test_prev_action_caps_at_ten_with_ellipsis(tmp_path):
    """壊す不変条件: Must1（上限10・超過時 "…"。CC 側 _format_prev_action と同一整形）
    ／通したい検査経路: _format_prev_action（extractor.py から re-use、自作しない）。
    変異（上限チェックを外し ",".join のみにする）を適用すると本テストが落ちる。
    """
    lines = [_session_meta("s1", ts="2026-08-20T00:00:00.000Z"),
              _response_user("発話1", ts="2026-08-20T00:00:01.000Z")]
    for i in range(12):
        lines.append(_function_call(f"tool{i}", ts=f"2026-08-20T00:00:0{2 + i % 8}.000Z"))
    lines.append(_response_user("発話2", ts="2026-08-20T00:01:00.000Z"))
    path = _write(tmp_path, "prev_action_cap.jsonl", lines)
    pf = p.parse_session_file(path)
    by_text = {c.text: c.prev_action for c in pf.candidates}
    expected = ",".join(f"tool{i}" for i in range(10)) + ",…"
    assert by_text["発話2"] == expected


def test_prev_action_duplicate_channel_reuses_same_snapshot(tmp_path):
    """壊す不変条件: Must1（response_item/event_msg の重複表現は、どちらが先着でも
    同じ prev_action を持つ。実測: event_msg が先着するケースが多数派 2185/2772）
    ／通したい検査経路: parse_session_file の _next_prev_action の text_hash 判定。
    """
    dup_text = "重複発話"
    lines = [
        _session_meta("s1", ts="2026-08-20T00:00:00.000Z"),
        _function_call("toolA", ts="2026-08-20T00:00:01.000Z"),
        _event_user_message(dup_text, ts="2026-08-20T00:00:02.000Z"),  # 先着（多数派パターン）
        _response_user(dup_text, ts="2026-08-20T00:00:02.010Z"),  # 直後の重複
    ]
    path = _write(tmp_path, "dup_channel_prev_action.jsonl", lines)
    pf = p.parse_session_file(path)
    assert len(pf.candidates) == 2
    assert pf.candidates[0].prev_action == "toolA"
    assert pf.candidates[1].prev_action == "toolA"


@pytest.mark.real_home
@_skip_if_no_real_codex_sessions
def test_prev_action_populated_end_to_end_against_real_data():
    """陽性対照 + 統合検査: 実データで prev_action が非 None の発話が実在すること
    （全件 None のまま構造的に歪んでいないこと・Must1 反映確認）。
    """
    result = p.run_probe(
        sessions_root=Path.home() / ".codex" / "sessions",
        base_date=date(2026, 8, 23),
        days=14,
    )
    non_none = sum(1 for u in result.utterances if u["prev_action"] is not None)
    assert non_none > 0


# ─────────────────────────────────────────────────────────────────
# Must2: --out-dir 拒否ガード
# ─────────────────────────────────────────────────────────────────
def test_validate_out_dir_rejects_claude_home(tmp_path):
    """壊す不変条件: Must2（~/.claude/ 配下への出力を拒否する）
    ／通したい検査経路: validate_out_dir。
    """
    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "evolve-anything").mkdir(parents=True)
    import unittest.mock as mock
    with mock.patch.object(Path, "home", return_value=fake_home):
        reason = p.validate_out_dir(fake_home / ".claude" / "evolve-anything" / "pwned")
    assert reason is not None


def test_validate_out_dir_rejects_codex_home(tmp_path):
    """壊す不変条件: Must2（~/.codex/ 配下への出力を拒否する）。"""
    fake_home = tmp_path / "home"
    (fake_home / ".codex").mkdir(parents=True)
    import unittest.mock as mock
    with mock.patch.object(Path, "home", return_value=fake_home):
        reason = p.validate_out_dir(fake_home / ".codex" / "pwned")
    assert reason is not None


def test_validate_out_dir_rejects_repo_root():
    """壊す不変条件: Must2（リポジトリ作業ディレクトリ配下への出力を拒否する）。"""
    repo_root = Path(p.__file__).resolve().parent.parent
    reason = p.validate_out_dir(repo_root / "scripts" / "pwned")
    assert reason is not None


def test_validate_out_dir_accepts_isolated_tmp_dir(tmp_path, monkeypatch, tmp_path_factory):
    """陽性対照: 隔離された一時ディレクトリは拒否されない。

    root conftest.py の autouse HOME/DATA_DIR 隔離が全テストで
    ``CLAUDE_PLUGIN_DATA=str(tmp_path)`` を設定するため、DATA_DIR が
    item1 の禁止ルートに加わった今、素の ``tmp_path`` を out_dir に使うと
    「DATA_DIR 配下の出力」として常に拒否されてしまう（本テストが検査したい
    「無関係な隔離ディレクトリは拒否されない」という主張とは別の理由で赤くなる）。
    DATA_DIR を tmp_path と無関係な場所へ明示的にずらして検査する。
    """
    unrelated_data_dir = tmp_path_factory.mktemp("unrelated_data_dir")
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: unrelated_data_dir)
    reason = p.validate_out_dir(tmp_path / "phase1_out")
    assert reason is None


def test_main_exits_nonzero_and_creates_no_files_for_forbidden_out_dir(tmp_path):
    """壊す不変条件: Must2（起動時に拒否し、禁止ディレクトリ配下へ何も作らない）
    ／通したい検査経路: main() の validate_out_dir 呼び出し（mkdir より前）。
    レビュアー提供の再現手順の pytest 版。変異（拒否チェックを外す）を適用すると、
    exit code が 0 になり、かつ禁止ディレクトリ配下にファイルが生成され本テストが落ちる。
    """
    fake_home = tmp_path / "home"
    sessions_root = fake_home / "sessions"
    sessions_root.mkdir(parents=True)
    forbidden = fake_home / ".codex"
    import unittest.mock as mock
    with mock.patch.object(Path, "home", return_value=fake_home):
        rc = p.main([
            "--sessions-root", str(sessions_root),
            "--base-date", "2026-08-23",
            "--out-dir", str(forbidden),
        ])
    assert rc != 0
    assert not forbidden.exists() or list(forbidden.iterdir()) == []


# ─────────────────────────────────────────────────────────────────
# Should: dedup 6則（複数候補群のカバー）
# ─────────────────────────────────────────────────────────────────
def _cand(channel, line_no, ts_ms, text, session_id="s1"):
    return p.RawCandidate("f1", channel, line_no, f"ts{ts_ms}", ts_ms, text, None, session_id, None)


def test_dedup_multi_candidate_group_picks_min_delta_with_line_no_tiebreak():
    """壊す不変条件: X1 マッチング6則（走査順・最小delta・同値時line_no・1:1消費）
    ／通したい検査経路: dedup_channel_constrained。
    実データに53群実在する「同一(file,text_hash)で複数候補を持つケース」を模した
    fixture（複数response_item・複数event_msg・入力順を逆転）で6則を固定する。
    """
    # 2つの異なる発話（text_hash が異なる）それぞれに複数候補群を持たせる。
    # 発話A: response_item(t=100) に対し event_msg 候補が [t=140(delta40,line10), t=90(delta10,line5)]
    #        → 最小delta(10)の line_no=5 を選ぶ（規則2）
    # 発話B: response_item(t=500) に対し event_msg 候補が [t=520(delta20,line20), t=480(delta20,line3)]
    #        → delta 同値(20) → line_no が小さい方(line3)を選ぶ（規則3）
    candidates = [
        # 入力順をわざと逆転させる（response_item 側が先とは限らない現実を模す）
        _cand("event_msg", 10, 140.0, "発話A"),
        _cand("event_msg", 5, 90.0, "発話A"),
        _cand("response_item", 1, 100.0, "発話A"),
        _cand("event_msg", 20, 520.0, "発話B"),
        _cand("event_msg", 3, 480.0, "発話B"),
        _cand("response_item", 2, 500.0, "発話B"),
    ]
    out = p.dedup_channel_constrained(candidates, threshold_ms=100)
    # 1:1消費: 発話A・発話Bとも response_item側1件ずつが残り、event_msg側の
    # 未マッチ1件ずつ（マッチしなかった方）が独立して残る＝各3件中2件がdedupで1件に。
    assert len(out) == 4  # response_item x2（代表） + 未マッチevent_msg x2
    resp_out = [c for c in out if c.channel == "response_item"]
    assert {c.text for c in resp_out} == {"発話A", "発話B"}

    evm_unmatched = [c for c in out if c.channel == "event_msg"]
    # 発話A: line_no=5(delta10)がマッチ済みで消費される→残るのはline_no=10(delta40)
    # 発話B: line_no=3(delta20)がマッチ済みで消費される→残るのはline_no=20(delta20)
    assert {c.line_no for c in evm_unmatched} == {10, 20}


def test_dedup_scan_order_is_timestamp_then_line_no_ascending():
    """壊す不変条件: X1規則1（response_item側は(timestamp,line_no)"昇順"で走査する）
    ／通したい検査経路: dedup_channel_constrained 内の resp ソート。

    昇順走査が貪欲法の結果に実際に影響する fixture（r1 は eA のみ到達可能・r2 は
    eA/eB 両方に到達可能で eA を強く優先）を使う。昇順（r1が先着）なら r1 が eA を
    確保し r2 は eB へフォールバックして両方消費される。降順（r2が先着）なら
    r2 が eA を奪い r1 は行き場を失い、eB は誰にも消費されず生き残る。
    """
    threshold = 1000.0
    r1 = _cand("response_item", 1, 0.0, "同文言")
    r2 = _cand("response_item", 2, 100.0, "同文言")
    eA = _cand("event_msg", 10, 50.0, "同文言")  # delta(r1,eA)=50 / delta(r2,eA)=50
    eB = _cand("event_msg", 20, 1090.0, "同文言")  # delta(r1,eB)=1090(不可) / delta(r2,eB)=990(可・eAより悪い)

    out = p.dedup_channel_constrained([r1, r2, eA, eB], threshold_ms=threshold)
    # 昇順(r1→r2)なら r1がeAを確保・r2はeBへフォールバック=両方消費→残るのはresponse_item 2件のみ。
    assert len(out) == 2
    assert {c.channel for c in out} == {"response_item"}


def test_dedup_one_to_one_consumption_not_shared():
    """壊す不変条件: X1規則5（マッチした対は双方消費済み＝1:1。同じevent_msg候補を
    複数のresponse_item候補が使い回さない）
    ／通したい検査経路: dedup_channel_constrained の matched_evm_ids 消費。
    """
    # 同一text_hashのresponse_item候補が2件、event_msg候補は1件のみ。
    # 1:1なら2件目のresponse_itemは誰にもマッチせず、それぞれ独立して残るため
    # 最終的にresponse_item2件 + event_msg0件（1件はマッチ消費）= 2件のまま。
    r1 = _cand("response_item", 1, 100.0, "同文言")
    r2 = _cand("response_item", 2, 101.0, "同文言")
    e1 = _cand("event_msg", 3, 100.0, "同文言")
    out = p.dedup_channel_constrained([r1, r2, e1], threshold_ms=100)
    assert len(out) == 2
    assert {c.channel for c in out} == {"response_item"}


# ─────────────────────────────────────────────────────────────────
# 本番ストア保護ガードの欠陥修正（team-lead 指摘・#534）
# 欠陥1: hashes_after が最後の書込み（report.json）より前に取られる
# 欠陥2: out_dir 検査がディレクトリにしか掛からず、出力ファイル名の symlink 経由で
#        本番ファイルへ書込める
# ─────────────────────────────────────────────────────────────────
def test_write_json_refuses_symlink_escaping_into_forbidden_root(tmp_path, monkeypatch):
    """壊す不変条件: 欠陥2（out_dir 内の出力ファイル名 symlink が本番ファイルへ
    書込むのを拒否する）
    ／通したい検査経路: _write_json が書込み直前に実体パス（symlink 解決後）を
    forbidden_out_dir_roots() と照合する。

    修正前は _write_json が path.write_text をそのまま呼ぶだけで実体パスの検査が
    無く、symlink を辿って victim（本番ファイル役）を上書きしてしまい本テストが
    落ちる。
    """
    victim_dir = tmp_path / "forbidden_root"
    victim_dir.mkdir()
    victim = victim_dir / "utterances.db"
    victim.write_bytes(b"PRISTINE")
    monkeypatch.setattr(p, "forbidden_out_dir_roots", lambda: [victim_dir])

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    evil_link = out_dir / "report.json"
    evil_link.symlink_to(victim)

    with pytest.raises(p.ProductionPathWriteError):
        p._write_json(evil_link, {"x": 1}, out_dir=out_dir)

    assert victim.read_bytes() == b"PRISTINE"


def test_write_json_allows_normal_path_inside_out_dir(tmp_path, monkeypatch, tmp_path_factory):
    """陽性対照: 禁止ルートに触れない通常の out_dir 書込みは成功する。

    root conftest.py の autouse 隔離が DATA_DIR=tmp_path を強制するため、
    item1 導入後は素の tmp_path 配下の out_dir が DATA_DIR 配下と誤認され拒否
    されてしまう。DATA_DIR を tmp_path と無関係な場所へずらして検査する
    （test_validate_out_dir_accepts_isolated_tmp_dir と同じ理由）。
    """
    unrelated_data_dir = tmp_path_factory.mktemp("unrelated_data_dir")
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: unrelated_data_dir)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    p._write_json(out_dir / "report.json", {"x": 1}, out_dir=out_dir)
    assert json.loads((out_dir / "report.json").read_text(encoding="utf-8")) == {"x": 1}


def test_write_json_refuses_escape_outside_out_dir_even_if_not_forbidden_root(tmp_path, monkeypatch):
    """壊す不変条件: item2（out_dir 内の出力ファイル名が、禁止ルート
    （~/.claude・~/.codex・repo・DATA_DIR）のいずれでもない out_dir 外の場所への
    symlink でも拒否される）
    ／通したい検査経路: _write_json の out_dir 包含チェック（relative_to）。
    修正前（out_dir 引数が無く禁止ルート照合のみだった版）は、この escape先が
    禁止ルート allowlist の外なので検査を素通りし本テストが落ちる。

    root conftest.py の autouse 隔離は DATA_DIR=tmp_path を強制するため、
    ``elsewhere`` を素の tmp_path 配下に置くと（意図せず）禁止ルート照合
    （item1 の DATA_DIR 検査）でも引っかかってしまい、item2 固有の効果を
    検査できない。forbidden_out_dir_roots を無関係な場所に固定し、この escape
    が「禁止ルート照合には掛からないが out_dir 包含チェックでは掛かる」ことを
    確実にする。
    """
    unrelated_root = tmp_path / "unrelated_forbidden_root"
    unrelated_root.mkdir()
    monkeypatch.setattr(p, "forbidden_out_dir_roots", lambda: [unrelated_root])

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    victim = elsewhere / "victim.json"
    victim.write_bytes(b"PRISTINE")
    evil_link = out_dir / "report.json"
    evil_link.symlink_to(victim)

    with pytest.raises(p.ProductionPathWriteError):
        p._write_json(evil_link, {"x": 1}, out_dir=out_dir)

    assert victim.read_bytes() == b"PRISTINE"


def test_main_write_json_containment_closes_the_defect1_escape_route(tmp_path, monkeypatch):
    """壊す不変条件: 旧欠陥1（hashes_after が report.json 書込みより前に取られると、
    symlink 経由の本番汚染が検出漏れになる）の攻撃面が、item2（out_dir 包含チェック）
    導入により **report.json の書込み自体でブロックされ、そもそも hashes_after
    取得順序に依存しなくなった** ことを検査する。

    production_store_paths / resolve_evolve_anything_data_dir を意図的に
    forbidden_out_dir_roots 外（out_dir とも victim とも非重複な第三の場所）へ
    向け、旧テストが利用していた「禁止ルート照合だけでは symlink を検出でき
    ない」状況を再現した上で、out_dir 内の report.json という出力ファイル名を
    経由した symlink が victim（本番ストア役）を指していても、_write_json の
    out_dir 包含チェックにより書込み前に例外で止まり victim が汚染されないこと
    を確認する（victim を DATA_DIR 自体の配下に置かないのは、それだと item1 の
    禁止ルート照合だけで検出できてしまい item2 固有の効果を切り分けられない
    ため）。
    ／通したい検査経路: main() 内 _write_json(report.json, out_dir=out_dir) の
    relative_to(out_dir) 検査。
    修正前（out_dir 引数の無い版）はこの escape を検出できず、hashes_after が
    report.json 書込み前に取られていれば ok=True のまま victim が汚染されて
    返っていた（旧テストが検出していた検出漏れそのもの）。
    """
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    victim_dir = tmp_path / "victim_store"
    victim_dir.mkdir()
    victim = victim_dir / "utterances.db"
    victim.write_bytes(b"PRISTINE")

    unrelated_data_dir = tmp_path / "unrelated_data_dir"
    unrelated_data_dir.mkdir()

    monkeypatch.setattr(p, "production_store_paths", lambda: {"utterances_db": victim})
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: unrelated_data_dir)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "report.json").symlink_to(victim)

    with pytest.raises(p.ProductionPathWriteError):
        p.main([
            "--sessions-root", str(sessions_root),
            "--base-date", "2026-08-23",
            "--out-dir", str(out_dir),
        ])

    assert victim.read_bytes() == b"PRISTINE"


def test_main_symlink_into_true_forbidden_root_leaves_victim_untouched(tmp_path, monkeypatch):
    """壊す不変条件: 欠陥2（main() 実行の end-to-end 経路でも、out_dir 内の
    出力ファイル名 symlink が forbidden_out_dir_roots() 配下の実ファイルを
    上書きできない）
    ／通したい検査経路: main() → _write_json の実体パス検査。

    ``_write_json`` 単体テストと異なり、main() を通しで呼び、production_store_paths /
    forbidden_out_dir_roots の双方を同じ偽ルート配下に揃えることで、実際の
    CLI 経路（run_probe→複数ファイル書込→report.json→guard.json）で欠陥2の
    ガードが機能することを検査する。仕様上「例外で止まる」ことも合格
    （委譲プロンプトの (a) 要件）なので、例外送出・非0終了のどちらでも
    victim が変化しないことのみを assert する。
    """
    forbidden_root = tmp_path / "forbidden_root"
    forbidden_root.mkdir()
    victim = forbidden_root / "utterances.db"
    victim.write_bytes(b"PRISTINE")

    monkeypatch.setattr(p, "forbidden_out_dir_roots", lambda: [forbidden_root])
    monkeypatch.setattr(p, "production_store_paths", lambda: {"utterances_db": victim})
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: forbidden_root)

    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "report.json").symlink_to(victim)

    try:
        rc = p.main([
            "--sessions-root", str(sessions_root),
            "--base-date", "2026-08-23",
            "--out-dir", str(out_dir),
        ])
    except p.ProductionPathWriteError:
        pass
    else:
        assert rc != 0

    assert victim.read_bytes() == b"PRISTINE"


def test_main_out_dir_rejection_still_effective(tmp_path):
    """陽性対照 + 回帰検査: 既存の --out-dir 拒否ガード（禁止ルート直指定）が
    欠陥1・欠陥2の修正後も引き続き効くこと。"""
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    repo_root = Path(p.__file__).resolve().parent.parent
    rc = p.main([
        "--sessions-root", str(sessions_root),
        "--base-date", "2026-08-23",
        "--out-dir", str(repo_root / "scripts" / "pwned_by_test"),
    ])
    assert rc != 0
    assert not (repo_root / "scripts" / "pwned_by_test").exists()


def test_main_production_store_guard_ok_true_on_clean_run(tmp_path, monkeypatch):
    """陽性対照: symlink 攻撃や汚染の無い通常実行では production_store_guard.ok
    が True のまま、report.json / guard.json の双方が生成されること。"""
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()

    isolated_store_dir = tmp_path / "isolated_store"
    monkeypatch.setattr(
        p, "production_store_paths",
        lambda: {"utterances_db": isolated_store_dir / "utterances.db"},
    )
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: isolated_store_dir)

    out_dir = tmp_path / "out"
    rc = p.main([
        "--sessions-root", str(sessions_root),
        "--base-date", "2026-08-23",
        "--out-dir", str(out_dir),
    ])

    assert rc == 0
    guard = json.loads((out_dir / "guard.json").read_text(encoding="utf-8"))
    assert guard["ok"] is True
    assert guard["violations"] == []
    report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert "counts" in report
    assert "production_store_guard" not in report


# ─────────────────────────────────────────────────────────────────
# team-lead 追加指摘（外部レビュー #536・item1〜5）
# ─────────────────────────────────────────────────────────────────
def test_validate_out_dir_rejects_dynamic_data_dir(tmp_path, monkeypatch):
    """壊す不変条件: item1（DATA_DIR が CLAUDE_PLUGIN_DATA で ~/.claude 配下以外の
    任意の場所を指す custom 構成でも、その配下への --out-dir は禁止される）
    ／通したい検査経路: forbidden_out_dir_roots が resolve_evolve_anything_data_dir()
    を含めること。
    修正前（ハードコード3ルートのみ）は custom DATA_DIR がどの禁止ルートにも
    一致せず reason が None になり本テストが落ちる。
    """
    custom_data_dir = tmp_path / "custom_data"
    custom_data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(custom_data_dir))
    reason = p.validate_out_dir(custom_data_dir / "phase1_out")
    assert reason is not None


def test_validate_out_dir_accepts_dir_outside_dynamic_data_dir(tmp_path, monkeypatch):
    """陽性対照: custom DATA_DIR 構成でも、それと無関係な隔離ディレクトリは拒否
    されない。"""
    custom_data_dir = tmp_path / "custom_data"
    custom_data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(custom_data_dir))
    reason = p.validate_out_dir(tmp_path / "isolated_out")
    assert reason is None


def test_out_dir_cannot_be_nested_inside_data_dir_end_to_end(tmp_path, monkeypatch):
    """壊す不変条件: item3（item1 のガードにより out_dir は DATA_DIR 配下になり
    得ないため、guard.json を事後 hash 採取後に書いても DATA_DIR の観測範囲を
    汚染しないという設計上の主張が、main() の実行経路でも実際に成立する）
    ／通したい検査経路: main() 起動時の validate_out_dir（forbidden_out_dir_roots
    経由で DATA_DIR を検査）。
    修正前は DATA_DIR が禁止ルートに含まれないため、この --out-dir はそのまま
    受理され、DATA_DIR 配下にファイル一式（guard.json 含む）が作成されてしまい
    本テストが落ちる。
    """
    fake_home = tmp_path / "home"
    sessions_root = fake_home / "sessions"
    sessions_root.mkdir(parents=True)
    data_dir = fake_home / "data"
    data_dir.mkdir()
    monkeypatch.setattr(p, "resolve_evolve_anything_data_dir", lambda: data_dir)
    nested_out = data_dir / "phase1_out"

    rc = p.main([
        "--sessions-root", str(sessions_root),
        "--base-date", "2026-08-23",
        "--out-dir", str(nested_out),
    ])

    assert rc != 0
    assert not nested_out.exists()


def test_snapshot_data_dir_listing_detects_same_size_content_change(tmp_path):
    """壊す不変条件: item4（同一 byte 数のまま内容だけ書き換えられた変更を
    size 比較では見逃すが hash 比較なら検出する）
    ／通したい検査経路: snapshot_data_dir_listing + verify_data_dir_unchanged。
    修正前（サイズのみ比較）は "AAAA"→"BBBB"（同じ4byte）を差分として検出できず
    本テストが落ちる。
    """
    d = tmp_path / "data"
    d.mkdir()
    f = d / "utterances.db"
    f.write_bytes(b"AAAA")
    before = p.snapshot_data_dir_listing(d)
    f.write_bytes(b"BBBB")
    after = p.snapshot_data_dir_listing(d)
    ok, violations = p.verify_data_dir_unchanged(before, after)
    assert ok is False
    assert "utterances.db" in violations[0]


def test_snapshot_data_dir_listing_passes_when_unchanged(tmp_path):
    """陽性対照: 内容が変化していなければ hash 比較でも合格する。"""
    d = tmp_path / "data"
    d.mkdir()
    f = d / "x.jsonl"
    f.write_bytes(b"same-content")
    before = p.snapshot_data_dir_listing(d)
    after = p.snapshot_data_dir_listing(d)
    ok, violations = p.verify_data_dir_unchanged(before, after)
    assert ok is True
    assert violations == []


# ─────────────────────────────────────────────────────────────────
# item5: 機構除外の独立オラクル（範囲・単調性チェックだけでは検出できない
# 「同数の別種入替」を検出する）
# ─────────────────────────────────────────────────────────────────
def _small_fixture_for_oracle(sessions_root: Path) -> Path:
    lines = [
        _session_meta("s1"),
        _response_user("<recommended_plugins>\n機構発話", ts="2026-08-20T00:00:01.000Z"),
        _response_user("普通の発話", ts="2026-08-20T00:00:02.000Z"),
    ]
    date_dir = sessions_root / "2026" / "08" / "20"
    date_dir.mkdir(parents=True)
    return _write(date_dir, "oracle.jsonl", lines)


def test_machinery_exclusion_oracle_passes_on_correct_pipeline(tmp_path):
    """陽性対照: filter_machinery が正しく配線されている通常実行では、独立
    オラクルと実際の after_machinery_exclusion が一致し例外が出ない。"""
    _small_fixture_for_oracle(tmp_path)
    result = p.run_probe(sessions_root=tmp_path, base_date=date(2026, 8, 20), days=1)
    p.assert_machinery_exclusion_matches_oracle(result)  # 例外が出ないこと自体が検査
    assert result.counts.after_machinery_exclusion == 1


def test_machinery_exclusion_oracle_detects_disabled_filter(tmp_path, monkeypatch):
    """壊す不変条件: item5（機構除外ステージがパイプラインから外れる＝配線切れ。
    段階間の非増加関係・絶対値レンジだけでは、除外対象が0件になっても
    raw>=after_child_exclusion>=after_machinery_exclusion>=after_dedup と
    レンジ自体は別途破れない限り検出できない）
    ／通したい検査経路: assert_machinery_exclusion_matches_oracle が
    normalized_events から独立に再計算した期待値との不一致を検出する。
    変異④相当（filter_machinery を恒等関数化し検査を無効化する）を run_probe の
    呼び出し点で直接適用する。
    """
    _small_fixture_for_oracle(tmp_path)
    monkeypatch.setattr(p, "filter_machinery", lambda candidates: list(candidates))
    result = p.run_probe(sessions_root=tmp_path, base_date=date(2026, 8, 20), days=1)
    # 配線切れにより機構発話も残ってしまう（本来1件のはずが2件）。
    assert result.counts.after_machinery_exclusion == 2
    with pytest.raises(AssertionError):
        p.assert_machinery_exclusion_matches_oracle(result)


def test_machinery_exclusion_oracle_detects_swap_same_count_different_texts(tmp_path):
    """壊す不変条件: item5 の分散・入替変異（変異③相当）。除外件数
    （after_machinery_exclusion）はレンジ・非増加関係を満たしたまま、除外対象の
    中身だけが入れ替わる（＝機構発話が残り、代わりに関係ない通常発話が誤って
    落とされる）ケースを、独立オラクルの多重集合（Counter）完全一致で検出する
    （件数だけの突合ではこの入替を見逃す）。
    ここでは filter_machinery を「機構発話でなく末尾の候補を1件落とす」偽実装に
    差し替え、除外後件数は正しい（1件）のに中身が入れ替わっていることを示す。
    """
    _small_fixture_for_oracle(tmp_path)

    def _wrong_filter(candidates):
        # 機構マーカーで除外する代わりに、末尾の候補（本来除外されるべきでない
        # 普通の発話）を1件だけ落とす壊れた実装。
        return list(candidates)[:-1]

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(p, "filter_machinery", _wrong_filter)
        result = p.run_probe(sessions_root=tmp_path, base_date=date(2026, 8, 20), days=1)
        # 件数だけ見ればレンジ・非増加関係は満たしうるが、中身は入れ替わっている
        # （残った候補は「機構発話」であり「普通の発話」が誤って落ちている）。
        assert result.counts.after_machinery_exclusion == 1
        surviving_texts = {u["text"] for u in result.utterances}
        assert "普通の発話" not in surviving_texts  # 壊れた実装の症状を確認
        with pytest.raises(AssertionError):
            p.assert_machinery_exclusion_matches_oracle(result)
    finally:
        monkeypatch.undo()
