"""detect_permission_deny の PJ スコープ（#304 で発見・#206 と同型）。

``detect_permission_deny`` は errors.jsonl の全 permission_denied 行を、呼び出し側が渡した
pj_slug で**無条件に**スタンプしていた。errors.jsonl は全 PJ 共有ストアなので、PJ ごとに
呼ぶと同じ deny が PJ 数だけ複製され、どの PJ でも同じ件数が出る（全PJ bit-exact 同値＝
measurement_bug の指紋）。全 PJ を回すバッチ（fleet detect）では 61 PJ × 5 件の phantom を
生む。判定は #206 の単一ソース述語 ``record_project_match`` に委ねる（属性欠落は寛容側）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from weak_signals.detectors import detect_permission_deny  # noqa: E402


def _deny(project: str | None = None, tool: str = "Bash") -> dict:
    rec = {
        "type": "permission_denied",
        "tool_name": tool,
        "tool_input_summary": "git push",
        "denial_reason": "unknown",
        "timestamp": "2026-04-22T04:43:09.279230+00:00",
        "session_id": "s1",
    }
    if project is not None:
        rec["project"] = project
    return rec


def test_other_project_deny_is_excluded():
    """他 PJ の deny を当 PJ のシグナルとして数えない。"""
    records = [_deny("docs-platform"), _deny("docs-platform")]
    assert detect_permission_deny(records, "amamo") == []


def test_own_project_deny_is_detected():
    records = [_deny("amamo"), _deny("docs-platform")]
    out = detect_permission_deny(records, "amamo")
    assert len(out) == 1
    assert out[0].pj_slug == "amamo"


def test_unattributed_deny_is_kept():
    """project 属性の無い旧レコードは寛容に許容する（#206 の判定仕様と揃える）。"""
    out = detect_permission_deny([_deny(None)], "amamo")
    assert len(out) == 1


def test_no_slug_keeps_everything():
    """slug 未確定なら判定不能として従来どおり全件返す。"""
    out = detect_permission_deny([_deny("docs-platform")], "")
    assert len(out) == 1
