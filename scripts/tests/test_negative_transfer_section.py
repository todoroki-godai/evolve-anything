"""negative_transfer observability builder のテスト（#288・決定論）。

usage データの有無・回帰有無で section が None / ℹ / ✓ / ⚠ を返すこと、
observability contract（両経路伝播）に登録されていることを検証する。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from audit import usage as audit_usage  # noqa: E402
from audit.observability import _OBSERVABILITY_BUILDERS, collect_observability  # noqa: E402
from audit.sections import build_negative_transfer_section  # noqa: E402


def _patch_usage(monkeypatch, records):
    """load_usage_data を固定レコードに差し替える（builder は call-time import）。"""
    monkeypatch.setattr(audit_usage, "load_usage_data", lambda **kw: list(records))


def test_none_when_no_usage_data(tmp_path, monkeypatch):
    """usage レコードが無ければ対象外（None）。"""
    _patch_usage(monkeypatch, [])
    assert build_negative_transfer_section(tmp_path) is None


def test_info_line_when_no_computable_component(tmp_path, monkeypatch):
    """レコードはあるが component transfer 算出不可 → ℹ 行（silence != evaluated）。"""
    # 単一スキルのみ → compute_component_transfer は [] を返す
    _patch_usage(monkeypatch, [
        {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
    ])
    section = build_negative_transfer_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "Negative Transfer" in combined
    assert "ℹ" in combined
    assert "算出対象なし" in combined


def test_clean_line_when_no_regression(tmp_path, monkeypatch):
    """回帰なし → 評価済 ✓ 行を残す。"""
    _patch_usage(monkeypatch, [
        {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "outcome": "success"},
        {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "outcome": "success"},
    ])
    section = build_negative_transfer_section(tmp_path)
    combined = "\n".join(section)
    assert "✓" in combined
    assert "negative transfer なし" in combined


def test_warn_line_with_regression(tmp_path, monkeypatch):
    """回帰あり → ⚠ で対象コンポーネントと影響スキルを surface。"""
    _patch_usage(monkeypatch, [
        {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "outcome": "success"},
        {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "outcome": "error"},
        {"skill_name": "ship", "ts": "2026-01-03T01:00:00Z", "outcome": "error"},
    ])
    section = build_negative_transfer_section(tmp_path)
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "review" in combined
    assert "ship" in combined


def test_byte_invariance_info(tmp_path, monkeypatch):
    """#115 advisory 共通枠への載せ替えで ℹ（算出対象なし）出力を 1 バイトも変えない。"""
    _patch_usage(monkeypatch, [
        {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
    ])
    assert build_negative_transfer_section(tmp_path) == [
        "## Negative Transfer (更新コンポーネント別)",
        "",
        "ℹ 評価したが component transfer 算出対象なし"
        "（追加スキルの前後で既存スキルの success/error データが不足）。",
        "",
    ]


def test_byte_invariance_clean(tmp_path, monkeypatch):
    """#115 載せ替えで ✓（回帰なし）出力を 1 バイトも変えない。"""
    _patch_usage(monkeypatch, [
        {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "outcome": "success"},
        {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "outcome": "success"},
    ])
    assert build_negative_transfer_section(tmp_path) == [
        "## Negative Transfer (更新コンポーネント別)",
        "",
        "✓ 評価したが negative transfer なし（1 件の更新コンポーネントを評価）",
        "",
    ]


def test_byte_invariance_warn(tmp_path, monkeypatch):
    """#115 載せ替えで ⚠（affected 入れ子 loop）出力を 1 バイトも変えない。"""
    _patch_usage(monkeypatch, [
        {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-01T01:00:00Z", "outcome": "success"},
        {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "outcome": "error"},
        {"skill_name": "ship", "ts": "2026-01-03T01:00:00Z", "outcome": "error"},
    ])
    assert build_negative_transfer_section(tmp_path) == [
        "## Negative Transfer (更新コンポーネント別)",
        "",
        "⚠ 既存スキルの成功率を下げた更新コンポーネントあり。"
        "`/evolve-anything:evolve-skill` で該当スキルの見直しを検討:",
        "- **review** (net Δ-100%):",
        "    - ship: before=100% → after=0% (Δ-100%)",
        "",
    ]


def test_registered_in_observability_contract():
    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "negative_transfer" in keys


def test_collect_observability_surfaces_negative_transfer(tmp_path, monkeypatch):
    """collect_observability 経由でも negative_transfer key が立つ（両経路伝播）。"""
    _patch_usage(monkeypatch, [
        {"skill_name": "ship", "ts": "2026-01-01T00:00:00Z", "outcome": "success"},
        {"skill_name": "review", "ts": "2026-01-02T00:00:00Z", "outcome": "success"},
        {"skill_name": "ship", "ts": "2026-01-03T00:00:00Z", "outcome": "error"},
    ])
    result = collect_observability(tmp_path)
    assert "negative_transfer" in result
