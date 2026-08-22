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

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import phase1_codex_probe as p  # noqa: E402


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
@pytest.mark.real_home
def test_run_probe_against_real_codex_sessions_matches_expected_order_of_magnitude():
    """壊す不変条件: C-1（実データで完走し段階件数が ADR 実測値と整合する）
    ／通したい検査経路: run_probe のパイプライン全体（実 ~/.codex/sessions を読む）。

    ADR 実測時点（2026-08-23 早朝）の値は 227/769/503/373/259。本テスト実行時点
    までの通常利用差分（実測 2〜3 件の増分。ADR 自身も 671→675→677→679 と
    測定間の増分を記録している）を許容し、厳密一致でなく近傍レンジで検査する。
    最終 dedup 後の値（259）は許容差ゼロで一致することを確認する
    （dedup はマッチ対の text_hash が同一のため machinery 除外の順序と可換）。
    """
    result = p.run_probe(
        sessions_root=Path.home() / ".codex" / "sessions",
        base_date=date(2026, 8, 23),
        days=14,
    )
    c = result.counts
    assert c.target_files == 227
    assert 760 <= c.raw <= 780
    assert 495 <= c.after_child_exclusion <= 510
    assert 365 <= c.after_machinery_exclusion <= 380
    assert c.after_dedup == 259
    assert c.parse_error_lines == 0


@pytest.mark.real_home
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


def test_validate_out_dir_accepts_isolated_tmp_dir(tmp_path):
    """陽性対照: 隔離された一時ディレクトリは拒否されない。"""
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
