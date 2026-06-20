"""audit レポート UX 改善のテスト（#49 / #52・決定論・LLM 非依存）。

issue #49（冗長性）と #52（Next Actions 欠如）の修正案を検証する:
- classify_section: observability セクション 1 本を critical/watch/clean に分類
- build_recommended_actions_section: 🔴/🟡/✅ の判定カードを末尾に出す（#52-1）
- build_tldr_block / fold_clean_observability: TL;DR + クリーン折り畳み（#49-1/#49-5）

新規モジュール sections_summary は observability.py の単一ソース契約（ADR-028）を壊さず、
report.py の markdown 経路にだけ 3 段構成（TL;DR / 要対応展開 / クリーン折り畳み）を足す。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
for _p in (_LIB,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from audit import generate_report  # noqa: E402
from audit.sections_summary import (  # noqa: E402
    build_recommended_actions_section,
    build_tldr_block,
    classify_section,
    fold_clean_observability,
)


# ── classify_section（critical / watch / clean） ──────────────────


def test_classify_clean_section():
    """✓ のみのセクションは clean。"""
    lines = ["## Foo", "", "✓ 評価したが drift なし（5 件）", ""]
    assert classify_section(lines) == "clean"


def test_classify_critical_when_warning():
    """⚠ を含むセクションは critical。"""
    lines = ["## Foo", "", "⚠ 未登録 pitfalls.md 3 件あり", ""]
    assert classify_section(lines) == "critical"


def test_classify_critical_when_red():
    """🔴 を含むセクションは critical。"""
    lines = ["## Foo", "", "🔴 要対応", ""]
    assert classify_section(lines) == "critical"


def test_classify_watch_when_info():
    """ℹ / データ不足のみのセクションは watch。"""
    lines = ["## Foo", "", "ℹ データ不足（サンプル不足）", ""]
    assert classify_section(lines) == "watch"


def test_classify_warning_wins_over_check():
    """✓ と ⚠ が混在する場合は critical を優先（要対応を埋もれさせない）。"""
    lines = ["## Foo", "", "✓ A は問題なし", "⚠ B に問題あり", ""]
    assert classify_section(lines) == "critical"


# ── fold_clean_observability（#49-1 / #49-5） ──────────────────


def test_fold_clean_collapses_clean_sections():
    """全 ✓ の observability セクションは展開せず1行に集約され、名前が残る。"""
    sections = {
        "glossary_drift": ["## Glossary Drift", "", "✓ 構造 drift なし（用語集 5 件）", ""],
        "orphan_store": ["## Orphan Stores", "", "✓ orphan store なし", ""],
        "unmanaged_pitfalls": ["## Unmanaged Pitfalls", "", "⚠ 未登録 pitfalls.md 2 件", ""],
    }
    expanded, clean_names, watch_names = fold_clean_observability(sections)
    # clean セクションは展開行に含まれない
    expanded_text = "\n".join(expanded)
    assert "Glossary Drift" not in expanded_text
    assert "Orphan Stores" not in expanded_text
    # 要対応セクションは展開される
    assert "Unmanaged Pitfalls" in expanded_text
    # clean 名はリストに残る（silence != evaluated）
    assert "glossary_drift" in clean_names
    assert "orphan_store" in clean_names
    assert "unmanaged_pitfalls" not in clean_names


def test_fold_keeps_watch_separate():
    """ℹ（データ不足）のセクションは watch に分類され、clean に畳まれない。"""
    sections = {
        "fanout_cost": ["## Fan-out Cost", "", "ℹ Fan-out: cost 観察中", ""],
    }
    expanded, clean_names, watch_names = fold_clean_observability(sections)
    assert "fanout_cost" in watch_names
    assert "fanout_cost" not in clean_names


# ── build_tldr_block（#49-5） ──────────────────


def test_tldr_block_shows_three_counts():
    """TL;DR は 要対応N / 観察中M / クリーンK の3数字を出す。"""
    lines = build_tldr_block(critical=2, watch=1, clean=8)
    text = "\n".join(lines)
    assert "TL;DR" in text
    assert "要対応 2" in text
    assert "観察中 1" in text
    assert "クリーン 8" in text


def test_tldr_block_all_clean():
    """全クリーン時も要対応 0 件を明示する（沈黙しない）。"""
    lines = build_tldr_block(critical=0, watch=0, clean=10)
    text = "\n".join(lines)
    assert "要対応 0" in text


# ── build_recommended_actions_section（#52-1） ──────────────────


def test_recommended_actions_violations_red():
    """Line Limit Violations ≥1 → 🔴 + evolve/分割の導線。"""
    lines = build_recommended_actions_section(
        violations=[{"file": "a.py", "lines": 900, "limit": 800}],
        token_uninitialized=False,
        capture_starved=False,
        scope_candidates=[],
    )
    text = "\n".join(lines)
    assert "推奨アクション" in text
    assert "🔴" in text
    assert "evolve" in text or "分割" in text


def test_recommended_actions_token_red():
    """Token 未初期化 → 🔴 evolve-fleet tokens --backfill。"""
    lines = build_recommended_actions_section(
        violations=[],
        token_uninitialized=True,
        capture_starved=False,
        scope_candidates=[],
    )
    text = "\n".join(lines)
    assert "🔴" in text
    assert "evolve-fleet tokens --backfill" in text


def test_recommended_actions_capture_red():
    """Correction capture 枯渇 → 🔴 corrections.jsonl 確認 + hook 見直し。"""
    lines = build_recommended_actions_section(
        violations=[],
        token_uninitialized=False,
        capture_starved=True,
        scope_candidates=[],
    )
    text = "\n".join(lines)
    assert "🔴" in text
    assert "corrections.jsonl" in text


def test_recommended_actions_scope_yellow():
    """Scope Advisory の project-scope 候補 → 🟡 スコープ移動 or prune。"""
    lines = build_recommended_actions_section(
        violations=[],
        token_uninitialized=False,
        capture_starved=False,
        scope_candidates=[{"skill": "foo"}],
    )
    text = "\n".join(lines)
    assert "🟡" in text
    assert "prune" in text or "スコープ" in text


def test_recommended_actions_all_clean():
    """問題ゼロなら ✅ 1行（必ずセクションは出す — MUST）。"""
    lines = build_recommended_actions_section(
        violations=[],
        token_uninitialized=False,
        capture_starved=False,
        scope_candidates=[],
    )
    text = "\n".join(lines)
    assert "推奨アクション" in text
    assert "✅" in text


# ── generate_report 統合（#52-3 violations 導線 / 推奨アクション末尾） ──────────────


def test_generate_report_violations_next_action_link():
    """#52-3: violations セクション末尾に evolve/分割の導線が出る（project_dir=None で軽量）。"""
    md = generate_report(
        artifacts={"skills": []},
        violations=[{"file": "a.py", "lines": 900, "limit": 800}],
        usage={},
        duplicates=[],
        advisories=[],
        project_dir=None,
    )
    assert "Line Limit Violations" in md
    assert "/evolve-anything:evolve" in md
    assert "800行超" in md


def test_generate_report_always_has_recommended_actions():
    """推奨アクションセクションは project_dir=None でも必ず末尾に出る（MUST）。"""
    md = generate_report(
        artifacts={"skills": []},
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        project_dir=None,
    )
    assert "## 推奨アクション" in md


def test_generate_report_next_milestone_block():
    """#52-2: next_milestone を渡すと「成長の次の一手」ブロックが出る。"""
    md = generate_report(
        artifacts={"skills": []},
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        project_dir=None,
        next_milestone=["### Next Milestone", "Next phase: **Initial Nurturing**", ""],
    )
    assert "成長の次の一手" in md
    assert "Initial Nurturing" in md
