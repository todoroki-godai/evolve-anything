"""Scope Advisory セクションの action 導線テスト（#48-F4）。

旧実装は report.py がインラインで「- skill: N projects ... → consider project-scope」を
出すだけで、ユーザーが「project-scope へどう移動するか」の操作導線が無かった
（advisory 止まり → alert fatigue）。build_scope_advisory_section はレンダリングを
テスト可能な helper に切り出し、project-scope 候補がある場合に具体的な移動手順を
1 行添える。決定論・LLM 非依存・read-only（副作用なし）。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit.scope import build_scope_advisory_section  # noqa: E402


def test_none_when_no_advisories():
    """advisory が無ければ None（沈黙・report.py の `if advisories:` ガードと整合）。"""
    assert build_scope_advisory_section([]) is None
    assert build_scope_advisory_section(None) is None


def test_renders_items_and_header():
    """各 advisory を従来通り 1 行で出し、ヘッダを付ける。"""
    advisories = [
        {
            "skill": "review",
            "project_count": 1,
            "projects": ["/work/mine"],
            "last_used": "2026-06-19T10:00:00+00:00",
            "recommendation": "consider project-scope",
        },
    ]
    section = build_scope_advisory_section(advisories)
    assert section is not None
    combined = "\n".join(section)
    assert "## Scope Advisory" in combined
    assert "review" in combined
    assert "1 projects" in combined
    assert "consider project-scope" in combined
    # last_used は日付だけ（[:10]）に丸める従来挙動を維持
    assert "2026-06-19" in combined


def test_action_line_when_project_scope_candidate():
    """project-scope 候補があるとき、具体的な移動手順の action 行を添える（#48-F4）。"""
    advisories = [
        {
            "skill": "daily-report",
            "project_count": 1,
            "projects": ["/work/mine"],
            "last_used": "2026-06-20T10:00:00+00:00",
            "recommendation": "consider project-scope",
        },
    ]
    section = build_scope_advisory_section(advisories)
    assert section is not None
    combined = "\n".join(section)
    # 移動先の具体パス導線（global → project-local）
    assert ".claude/skills/" in combined
    # 操作のヒント（移動 という語で導線を示す）
    assert "移動" in combined


def test_no_action_line_when_all_keep_global():
    """全て keep global（複数PJ使用）なら移動 action 行は出さない（不要なノイズを増やさない）。"""
    advisories = [
        {
            "skill": "shared-skill",
            "project_count": 3,
            "projects": ["/a", "/b", "/c"],
            "last_used": "2026-06-20T10:00:00+00:00",
            "recommendation": "keep global",
        },
    ]
    section = build_scope_advisory_section(advisories)
    assert section is not None
    combined = "\n".join(section)
    assert "shared-skill" in combined
    assert "keep global" in combined
    # project-scope 候補が無いので移動手順は出さない
    assert "移動" not in combined


def test_read_only_does_not_mutate_input():
    """副作用なし（入力 advisories を破壊しない）。"""
    advisories = [
        {
            "skill": "review",
            "project_count": 1,
            "projects": ["/work/mine"],
            "last_used": "2026-06-19T10:00:00+00:00",
            "recommendation": "consider project-scope",
        },
    ]
    snapshot = [dict(a) for a in advisories]
    build_scope_advisory_section(advisories)
    assert advisories == snapshot
