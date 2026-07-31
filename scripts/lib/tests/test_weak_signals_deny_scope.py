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


# ── strict（fan-out 文脈）・#312 ───────────────────────────────────
# 単一 PJ 文脈（evolve/reflect）は「判定不能なら落とさない」が安全側なので既定は寛容のまま。
# 全 PJ へ fan-out する fleet detect は逆で、寛容に通すと未帰属レコードが必ずどこか 1 PJ
# （dedup 後は slug 辞書順の先頭）に誤帰属する。同じ述語を文脈で切り替える。

def test_strict_excludes_unattributed_deny():
    """strict では未帰属 deny を当 PJ のシグナルにしない。"""
    assert detect_permission_deny([_deny(None)], "amamo", strict=True) == []


def test_strict_keeps_own_project_deny():
    """strict でも当 PJ に帰属する deny は従来どおり検出する。"""
    out = detect_permission_deny([_deny("amamo"), _deny(None)], "amamo", strict=True)
    assert len(out) == 1
    assert out[0].pj_slug == "amamo"
