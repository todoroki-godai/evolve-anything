"""self_contamination observability builder のテスト（決定論・ゼロ LLM）。

silence != evaluated の境界（transcript 不在 → None / clean → None / 指紋あり → ⚠ + evidence）と、
period-over-period 表示・代表例の対比行を検証する。transcript 走査はコアを monkeypatch で
in-memory report に差し替える（実 ~/.claude を読まない・HOME 隔離下でも決定論）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import self_contamination_scan as scs  # noqa: E402
from audit import sections_self_contamination as ssc  # noqa: E402


def _report(a=0, b=0, c=0, *, recent=None, baseline=None, is_topic=False, domain_fp=0):
    rep = scs.ScanReport()
    for i in range(a):
        rep.family_a.append(scs.Hit("A", i + 1, "text", "court\n<invoke name=\"Bash\">…", session_id="s"))
    for i in range(b):
        rep.family_b.append(scs.Hit("B", i + 1, "text", "<system-reminder>…", session_id="s"))
    for i in range(c):
        rep.family_c.append(
            scs.Hit(
                "C",
                i + 1,
                "text",
                "the user has approved the rewrite",
                reference_text="普通の git 出力でした",
                session_id="s",
            )
        )
    for i in range(domain_fp):
        rep.domain_vocab_fp.append(
            scs.Hit(
                "C",
                i + 1,
                "text",
                "今日は良い天気でしたね、ありがとうございます",
                reference_text="普通の文字起こし出力です",
                session_id="s",
            )
        )
    return scs.ProjectScanReport(
        report=rep,
        recent_counts=recent or {"A": a, "B": b, "C": c},
        baseline_counts=baseline or {"A": 0, "B": 0, "C": 0},
        files_scanned=3,
        is_topic=is_topic,
    )


def _patch(monkeypatch, tmp_path, result):
    """transcript dir を存在させ、scan_project_transcripts を固定 result に差し替える。"""
    tdir = tmp_path / "cc-projects"
    tdir.mkdir(exist_ok=True)
    monkeypatch.setattr(ssc, "resolve_cc_transcript_dir", lambda project_dir: tdir)
    monkeypatch.setattr(ssc, "scan_project_transcripts", lambda *a, **k: result)


def test_none_when_transcript_dir_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ssc, "resolve_cc_transcript_dir", lambda project_dir: tmp_path / "does-not-exist"
    )
    assert ssc.build_self_contamination_section(tmp_path) is None


def test_none_when_scan_returns_none(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, None)
    assert ssc.build_self_contamination_section(tmp_path) is None


def test_silent_when_clean(tmp_path, monkeypatch):
    """指紋ゼロなら section 非表示（clean 時は沈黙）。"""
    _patch(monkeypatch, tmp_path, _report(0, 0, 0))
    assert ssc.build_self_contamination_section(tmp_path) is None


def test_surfaces_counts_and_warning(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, _report(2, 1, 1))
    section = ssc.build_self_contamination_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert section[0].startswith("## ")
    assert "Self-Contamination" in combined
    assert "⚠" in combined
    # 3 Family の件数が出る。
    assert "生タグ漏出" in combined
    assert "偽 system-reminder" in combined
    assert "汚染宣言" in combined


def test_period_over_period_surface(tmp_path, monkeypatch):
    result = _report(3, 0, 0, recent={"A": 3, "B": 0, "C": 0}, baseline={"A": 1, "B": 0, "C": 0})
    _patch(monkeypatch, tmp_path, result)
    combined = "\n".join(ssc.build_self_contamination_section(tmp_path))
    # baseline→recent の推移が表示される。
    assert "baseline" in combined.lower() or "直近" in combined
    assert "3" in combined and "1" in combined


def test_representative_examples_show_contrast(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, _report(0, 0, 1))
    combined = "\n".join(ssc.build_self_contamination_section(tmp_path))
    # Family C 代表例は「作話テキスト」と「直前 tool_result 原文」の対比を出す。
    assert "the user has approved the rewrite" in combined
    assert "普通の git 出力でした" in combined


def test_topic_pj_annotated(tmp_path, monkeypatch):
    """話題 PJ（evolve-anything 自身）では Family C の FP 注記が出る。"""
    _patch(monkeypatch, tmp_path, _report(0, 0, 2, is_topic=True))
    combined = "\n".join(ssc.build_self_contamination_section(tmp_path))
    assert "話題 PJ" in combined or "FP" in combined


# ==================================================================
# ドメイン語彙 FP 除外件数の常時 surface（#203, silence≠evaluated）
# ==================================================================
def test_domain_vocab_fp_count_surfaced_even_when_otherwise_clean(tmp_path, monkeypatch):
    """真の自己汚染ヒットが 0 件でも、ドメイン語彙 FP 除外件数は沈黙にしない。"""
    _patch(monkeypatch, tmp_path, _report(0, 0, 0, domain_fp=43))
    section = ssc.build_self_contamination_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "43" in combined
    assert "ドメイン語彙" in combined


def test_domain_vocab_fp_count_surfaced_alongside_real_hits(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path, _report(0, 0, 2, domain_fp=5))
    combined = "\n".join(ssc.build_self_contamination_section(tmp_path))
    assert "5" in combined
    assert "ドメイン語彙" in combined


def test_domain_vocab_fp_zero_and_otherwise_clean_stays_silent(tmp_path, monkeypatch):
    """ドメイン語彙 FP も真のヒットも 0 件なら、従来どおり沈黙（回帰確認）。"""
    _patch(monkeypatch, tmp_path, _report(0, 0, 0, domain_fp=0))
    assert ssc.build_self_contamination_section(tmp_path) is None
