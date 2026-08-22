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
    return json.dumps({"timestamp": ts, "type": "response_item", "payload": {"type": "tool_search_call"}})


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
    r1 = p.RawCandidate("f1", "response_item", 1, "2026-08-20T00:00:00.010Z", 1000.010 * 1000, "同じ発話", None, "s1")
    e1 = p.RawCandidate("f1", "event_msg", 2, "2026-08-20T00:00:00.000Z", 1000.0 * 1000, "同じ発話", None, "s1")
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
    e1 = p.RawCandidate("f1", "event_msg", 1, "2026-08-20T00:00:00.000Z", 1000.0, "event_msgだけの発話", None, "s1")
    out = p.dedup_channel_constrained([e1], threshold_ms=100)
    assert len(out) == 1
    assert out[0].channel == "event_msg"


def test_dedup_does_not_merge_beyond_threshold():
    """陽性対照: delta が閾値を超える候補は誤統合されない（意味を変えない差分ではなく
    正しい境界動作の確認）。"""
    r1 = p.RawCandidate("f1", "response_item", 1, "2026-08-20T00:00:00.500Z", 500.0, "text", None, "s1")
    e1 = p.RawCandidate("f1", "event_msg", 2, "2026-08-20T00:00:00.000Z", 0.0, "text", None, "s1")
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
    assert pf.unknown_type_pairs[("response_item", "tool_search_call")] == 1
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
