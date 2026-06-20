"""audit セクション圧縮の検証（#49-2/#49-3/#49-6・決定論・LLM 非依存）。

冗長な advisory セクション（全✗ / データ不足）を1行に圧縮する:
- #49-3 Promotion Readiness: 3条件すべて ✗ → 1行（✓ が1つでもあれば従来の全展開）
- #49-6 Fan-out Cost: advantage データ不足 + spawning sessions <5 → 1行
- #49-2 LSP Setup: 未設定 PJ → 1行サマリ（詳細は折り畳み）

compute は決定論関数なので返り値構造を mock して section の畳まれ方だけを検証する。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
for _p in (_LIB,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ── #49-3 Promotion Readiness 全✗ 圧縮 ──────────────────


def _readiness_result(v_pass, d_pass, dir_pass, promote=False):
    return {
        "axes": {"correction_recurrence": {"x": 1}},  # 非空 → 評価対象あり
        "variance": {"pass": v_pass, "pj_count": 0, "reason": "insufficient_pj"},
        "denominator": {"pass": d_pass, "floor": 10, "meeting": []},
        "direction": {"pass": dir_pass, "reason": "no_apply_events"},
        "promote": promote,
    }


def test_promotion_all_fail_compresses_to_one_line(monkeypatch):
    from audit import sections_promotion_readiness as spr
    from audit import outcome_promotion_readiness as opr

    monkeypatch.setattr(
        opr, "compute_promotion_readiness", lambda **k: _readiness_result(False, False, False)
    )
    section = spr.build_promotion_readiness_section(Path("/x"))
    assert section is not None
    text = "\n".join(section)
    # 圧縮版: 条件1/2/3 の個別展開は出ず、1行サマリ + PJ≥2 ヒント
    assert "条件1" not in text
    assert "条件2" not in text
    assert "Outcome Weight Promotion" in text
    assert "PJ" in text and "2" in text


def test_promotion_partial_pass_full_expand(monkeypatch):
    """✓ が1つでもあれば従来の全展開（条件1/2/3 個別表示）。"""
    from audit import sections_promotion_readiness as spr
    from audit import outcome_promotion_readiness as opr

    monkeypatch.setattr(
        opr, "compute_promotion_readiness", lambda **k: _readiness_result(True, False, False)
    )
    section = spr.build_promotion_readiness_section(Path("/x"))
    text = "\n".join(section)
    assert "条件1" in text  # 全展開


# ── #49-6 Fan-out Cost spawning<5 圧縮 ──────────────────


def _fanout_metrics(spawning, adv_value):
    return {
        "applicable": True,
        "cost": {
            "value": {"fanout_session_rate": 0.0, "avg_subagents_per_fanout_session": 0.0},
            "evidence": {
                "fanout_sessions": 0,
                "spawning_sessions": spawning,
                "total_subagents": 0,
                "agent_type_breakdown": {},
            },
        },
        "advantage": {
            "value": adv_value,
            "evidence": {
                "fanout_group_sessions": 0,
                "single_group_sessions": 0,
                "floor": 5,
            },
        },
    }


def test_fanout_low_spawning_compresses(monkeypatch):
    """spawning sessions <5 かつ advantage データ不足 → 1行圧縮。"""
    import fanout_cost
    from audit import sections_fanout as sf

    monkeypatch.setattr(
        fanout_cost, "compute_fanout_metrics", lambda *a, **k: _fanout_metrics(2, None)
    )
    section = sf.build_fanout_cost_section(Path("/x"))
    assert section is not None
    text = "\n".join(section)
    # 圧縮版: cost の詳細展開は出ず、観察中の1行 + 現在件数
    assert "fan-out session 率" not in text
    assert "観察中" in text
    assert "2" in text  # 現在 spawning 件数


def test_fanout_enough_spawning_full_expand(monkeypatch):
    """spawning sessions >=5 なら従来の全展開。"""
    import fanout_cost
    from audit import sections_fanout as sf

    monkeypatch.setattr(
        fanout_cost, "compute_fanout_metrics", lambda *a, **k: _fanout_metrics(8, None)
    )
    section = sf.build_fanout_cost_section(Path("/x"))
    text = "\n".join(section)
    assert "fan-out session 率" in text  # 全展開


def test_fanout_with_advantage_full_expand(monkeypatch):
    """advantage が算出できていれば spawning に関わらず全展開（spawning<5 でも）。"""
    import fanout_cost
    from audit import sections_fanout as sf

    monkeypatch.setattr(
        fanout_cost, "compute_fanout_metrics", lambda *a, **k: _fanout_metrics(2, 0.3)
    )
    section = sf.build_fanout_cost_section(Path("/x"))
    text = "\n".join(section)
    assert "fan-out session 率" in text  # 全展開


# ── #49-2 LSP Setup 圧縮 ──────────────────


def test_lsp_unset_compresses_to_summary(tmp_path):
    """未設定 PJ では1行サマリ + 詳細折り畳み（全展開しない）。"""
    from audit.sections import build_lsp_suggestion_section

    # python ファイルを閾値以上置いて検出させる
    src = tmp_path / "src"
    src.mkdir()
    for i in range(10):
        (src / f"m{i}.py").write_text("x = 1\n")

    section = build_lsp_suggestion_section(tmp_path)
    assert section is not None
    text = "\n".join(section)
    # 1行サマリ: ℹ + 未設定 + 言語名
    assert "ℹ" in text
    assert "LSP" in text
    assert "python" in text
    # 折り畳み（<details>）で詳細を隠す or 大幅圧縮されている（json 全文の必須行は折り畳み内）
    assert "<details>" in text


def test_lsp_set_returns_none(tmp_path):
    """.lsp.json があれば None（既設定）。"""
    from audit.sections import build_lsp_suggestion_section

    (tmp_path / ".lsp.json").write_text('{"python": {}}')
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("x = 1\n")
    assert build_lsp_suggestion_section(tmp_path) is None


# ── #52-5 蓄積条件1行（fanout advantage None / promotion not-promote） ──────────────


def test_fanout_advantage_none_high_spawning_shows_accumulation(monkeypatch):
    """spawning>=5 で advantage None なら全展開 + 蓄積条件1行（#52-5）。"""
    import fanout_cost
    from audit import sections_fanout as sf

    monkeypatch.setattr(
        fanout_cost, "compute_fanout_metrics", lambda *a, **k: _fanout_metrics(8, None)
    )
    section = sf.build_fanout_cost_section(Path("/x"))
    text = "\n".join(section)
    assert "fan-out session 率" in text  # 全展開
    assert "蓄積条件" in text


def test_promotion_partial_pass_shows_accumulation(monkeypatch):
    """部分 pass（✓ 1つ以上だが未充足）なら蓄積条件1行が出る（#52-5）。"""
    from audit import sections_promotion_readiness as spr
    from audit import outcome_promotion_readiness as opr

    monkeypatch.setattr(
        opr, "compute_promotion_readiness", lambda **k: _readiness_result(True, False, False)
    )
    section = spr.build_promotion_readiness_section(Path("/x"))
    text = "\n".join(section)
    assert "蓄積条件" in text


# ── #52-4 Token Consumption 未初期化メッセージ ──────────────


def test_token_uninitialized_shows_outcome_hint(monkeypatch):
    """Token 未初期化メッセージに「実行後に何が見えるか」が添えられる（#52-4）。"""
    import token_usage_store as tus
    from audit.sections import build_token_consumption_section

    # db_empty を強制（HAS_DUCKDB=False で初期化扱い）
    monkeypatch.setattr(tus, "HAS_DUCKDB", False)
    section = build_token_consumption_section(days=30)
    text = "\n".join(section)
    assert "not initialized" in text
    assert "TOP3" in text or "TOP 3" in text
