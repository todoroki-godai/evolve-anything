"""weak_signals.detectors の 4 チャネル検出テスト（#432）。

決定論・LLM 非依存。合成 fixture は実コーパスで観測した実シグナル形状を踏襲する
（verify-data-contract 準拠: transcript の tool_result error / interrupt / permission_denied
レコードの実構造を Read で確認済み）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from weak_signals.detectors import (  # noqa: E402
    REPHRASE_JACCARD_THRESHOLD,
    detect_permission_deny,
    detect_rephrase,
    detect_transcript_signals,
)


# ── ② permission deny ──────────────────────────────────────────

def test_permission_deny_detected_from_error_records() -> None:
    recs = [
        {"type": "permission_denied", "tool_name": "Bash",
         "tool_input_summary": "rm -rf /", "denial_reason": "hard_deny",
         "timestamp": "2026-06-10T00:00:00Z", "session_id": "s1"},
        {"type": "tool_error", "tool_name": "Read"},  # 別種 → 無視
    ]
    sigs = detect_permission_deny(recs, "evolve-anything")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.channel == "permission_deny"
    assert s.provenance["tool_name"] == "Bash"
    assert s.provenance["denial_reason"] == "hard_deny"
    assert s.session_id == "s1"
    assert s.pj_slug == "evolve-anything"


def test_permission_deny_empty_when_no_denials() -> None:
    assert detect_permission_deny([{"type": "tool_error"}], "x") == []


# ── ① 直後手編集 / ④ Esc 中断（transcript 直読）────────────────────

def _write_transcript(tmp_path: Path, records: list) -> Path:
    p = tmp_path / "session.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def test_manual_edit_after_ai_from_tool_result_error(tmp_path: Path) -> None:
    """File has been modified since read の tool_result error を ① として検出。"""
    tp = _write_transcript(tmp_path, [
        {"type": "user", "sessionId": "s9", "message": {"role": "user", "content": [
            {"type": "tool_result", "is_error": True, "content": [
                {"type": "text",
                 "text": "<tool_use_error>File has been modified since read, either by "
                         "the user or by a linter. Read it again.</tool_use_error>"}
            ]}
        ]}},
    ])
    sigs = detect_transcript_signals(tp, "evolve-anything")
    edits = [s for s in sigs if s.channel == "manual_edit_after_ai"]
    assert len(edits) == 1
    assert edits[0].provenance["attribution"] == "user_or_linter"
    assert edits[0].provenance["line_no"] == 1
    assert edits[0].session_id == "s9"


def test_esc_interrupt_detected(tmp_path: Path) -> None:
    """[Request interrupted by user] の user text block を ④ として検出。"""
    tp = _write_transcript(tmp_path, [
        {"type": "user", "sessionId": "s2", "message": {"role": "user", "content": [
            {"type": "text", "text": "[Request interrupted by user]"}
        ]}},
    ])
    sigs = detect_transcript_signals(tp, "evolve-anything")
    interrupts = [s for s in sigs if s.channel == "esc_interrupt"]
    assert len(interrupts) == 1
    assert interrupts[0].session_id == "s2"


def test_transcript_string_content_not_crashing(tmp_path: Path) -> None:
    """message.content が str（block list でない）でもクラッシュしない。"""
    tp = _write_transcript(tmp_path, [
        {"type": "user", "sessionId": "s3", "message": {"role": "user", "content": "ふつうの発話"}},
    ])
    assert detect_transcript_signals(tp, "x") == []


def test_transcript_missing_file_returns_empty(tmp_path: Path) -> None:
    assert detect_transcript_signals(tmp_path / "nope.jsonl", "x") == []


# ── ③ 言い直し（utterances ベース）─────────────────────────────────

def _utt(session_id, line_no, text, **extra):
    return {"session_id": session_id, "line_no": line_no, "text": text,
            "source_path": "/x.jsonl", "pj_slug": "evolve-anything", **extra}


def test_rephrase_detected_for_high_similarity_consecutive() -> None:
    """同一セッション連続の高類似ペアを言い直しとして検出。"""
    utts = [
        _utt("s1", 1, "開発サーバー動かして。目視してみる。"),
        _utt("s1", 2, "開発サーバー動かして。目視してみる"),  # 句点だけ違い → 高類似
    ]
    sigs = detect_rephrase(utts, "evolve-anything")
    assert len(sigs) == 1
    assert sigs[0].channel == "rephrase"
    assert sigs[0].provenance["similarity"] >= REPHRASE_JACCARD_THRESHOLD


def test_rephrase_not_detected_for_unrelated() -> None:
    """無関係な連続発話は検出しない。"""
    utts = [
        _utt("s1", 1, "テストを全部回して結果を見せて"),
        _utt("s1", 2, "じゃあ次はデプロイの設定を確認しよう"),
    ]
    assert detect_rephrase(utts, "evolve-anything") == []


def test_rephrase_excludes_dispatch_templates() -> None:
    """機構ターン（並列 agent 派遣テンプレ）は片方でも含めば除外（FP の直交分離）。

    実コーパス dry-run で最大の FP 源だった near-identical な agent 派遣プロンプトを、
    値の allowlist でなく『機構生成テンプレ』という理由でまとめて落とす。
    """
    base = "あなたは figma-to-code プロジェクトの採点エージェントです。担当は "
    utts = [
        _utt("s1", 1, base + "SP帯。subagent として動く。"),
        _utt("s1", 2, base + "PC帯。subagent として動く。"),  # near-identical だが派遣テンプレ
    ]
    assert detect_rephrase(utts, "evolve-anything") == []


def test_rephrase_skips_cross_session_pairs() -> None:
    """別セッションの発話は隣接していても比較しない。"""
    utts = [
        _utt("s1", 1, "prod まで動作確認して"),
        _utt("s2", 1, "prod まで動作確認して"),
    ]
    assert detect_rephrase(utts, "evolve-anything") == []


def test_rephrase_skips_short_utterances() -> None:
    """トークン数が少なすぎる発話は jaccard が不安定なので除外。"""
    utts = [
        _utt("s1", 1, "はい"),
        _utt("s1", 2, "はい"),
    ]
    assert detect_rephrase(utts, "evolve-anything") == []
