"""memory_contagion ロジックの境界テスト（#73・決定論・LLM 非依存）。

評価源（human/machine）の蓄積偏りを保守的に分類する 4 verdict
（applicable=False / no_human_baseline / contagion_risk / healthy）の境界を固定する。

corrections / idioms のローダ（provenance_weight.is_human_correction /
store.read_idioms）は既存実装に委ね、本テストは monkeypatch で dict を差し替えて
ContagionReport のロジック境界のみを検証する（実ストア / 実 ~/.claude を読まない）。
"""
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit import memory_contagion as mc  # noqa: E402


def _human_corr():
    """human-source correction record（is_human_correction=True になる形）。"""
    return {"source": "reflect_confirmed"}


def _machine_corr():
    """machine-source correction record（is_human_correction=False になる形）。"""
    return {"source": "hook"}


def _idiom(confirmed):
    return {"idiom": "x", "confirmed": confirmed, "pj_slug": "mine"}


def _patch_loaders(monkeypatch, *, corrections, idioms):
    """corrections / idioms のローダを差し替える（実ストアを読まない）。"""
    monkeypatch.setattr(mc, "_load_corrections", lambda project_dir: list(corrections))
    monkeypatch.setattr(mc, "_load_idioms", lambda project_dir: list(idioms))


# ── applicable=False: 評価データ無し ──────────────────────────────────

def test_not_applicable_when_no_data(tmp_path, monkeypatch):
    _patch_loaders(monkeypatch, corrections=[], idioms=[])
    report = mc.compute_contagion(tmp_path)
    assert report.applicable is False
    assert report.human_total == 0
    assert report.machine_total == 0


# ── no_human_baseline: 人間確認源ゼロ + machine が floor 以上 ───────────

def test_no_human_baseline_when_only_machine_above_floor(tmp_path, monkeypatch):
    """human_total=0 かつ machine_total>=MACHINE_FLOOR → no_human_baseline（ℹ・cry wolf しない）。"""
    machine = [_machine_corr() for _ in range(mc.MACHINE_FLOOR)]
    _patch_loaders(monkeypatch, corrections=machine, idioms=[])
    report = mc.compute_contagion(tmp_path)
    assert report.applicable is True
    assert report.human_total == 0
    assert report.machine_total == mc.MACHINE_FLOOR
    assert report.verdict == "no_human_baseline"


# ── healthy: machine が floor 未満（小標本ノイズ回避）───────────────────

def test_healthy_when_machine_below_floor(tmp_path, monkeypatch):
    """machine_total < MACHINE_FLOOR なら偏り判定しない（healthy）。"""
    machine = [_machine_corr() for _ in range(mc.MACHINE_FLOOR - 1)]
    _patch_loaders(monkeypatch, corrections=machine, idioms=[])
    report = mc.compute_contagion(tmp_path)
    assert report.applicable is True
    assert report.verdict == "healthy"


# ── contagion_risk: machine が human の RATIO 倍以上 + floor 以上 ───────

def test_contagion_risk_when_machine_dominates(tmp_path, monkeypatch):
    """human>0 かつ machine>=floor かつ machine>=RATIO*human → contagion_risk（⚠）。"""
    human = [_human_corr() for _ in range(4)]
    machine = [_machine_corr() for _ in range(12)]  # 12 >= 3.0 * 4 かつ >= floor
    _patch_loaders(monkeypatch, corrections=human + machine, idioms=[])
    report = mc.compute_contagion(tmp_path)
    assert report.applicable is True
    assert report.human_corrections == 4
    assert report.machine_corrections == 12
    assert report.human_total == 4
    assert report.machine_total == 12
    assert report.verdict == "contagion_risk"


def test_healthy_when_balanced(tmp_path, monkeypatch):
    """machine が floor 以上でも RATIO 倍未満なら healthy。"""
    human = [_human_corr() for _ in range(6)]
    machine = [_machine_corr() for _ in range(12)]  # 12 < 3.0 * 6 = 18
    _patch_loaders(monkeypatch, corrections=human + machine, idioms=[])
    report = mc.compute_contagion(tmp_path)
    assert report.verdict == "healthy"


def test_contagion_risk_boundary_exact_ratio(tmp_path, monkeypatch):
    """machine == RATIO * human ちょうどは contagion_risk（>= 比較）。"""
    human = [_human_corr() for _ in range(4)]
    machine = [_machine_corr() for _ in range(12)]  # 12 == 3.0 * 4
    _patch_loaders(monkeypatch, corrections=human + machine, idioms=[])
    report = mc.compute_contagion(tmp_path)
    assert report.verdict == "contagion_risk"


# ── idioms の confirmed/unconfirmed が集計に入る ──────────────────────

def test_idioms_contribute_to_totals(tmp_path, monkeypatch):
    """confirmed idiom は human_total に、unconfirmed idiom は machine_total に入る。"""
    idioms = [_idiom(True), _idiom(True)] + [_idiom(False) for _ in range(10)]
    _patch_loaders(monkeypatch, corrections=[], idioms=idioms)
    report = mc.compute_contagion(tmp_path)
    assert report.confirmed_idioms == 2
    assert report.unconfirmed_idioms == 10
    assert report.human_total == 2
    assert report.machine_total == 10
    # human=2, machine=10>=floor かつ 10 >= 3.0*2=6 → contagion_risk
    assert report.verdict == "contagion_risk"


def test_human_and_machine_totals_combine_corrections_and_idioms(tmp_path, monkeypatch):
    """human_total = human_corrections + confirmed_idioms / machine_total 同様。"""
    human = [_human_corr() for _ in range(2)]
    machine = [_machine_corr() for _ in range(3)]
    idioms = [_idiom(True), _idiom(False), _idiom(False)]
    _patch_loaders(monkeypatch, corrections=human + machine, idioms=idioms)
    report = mc.compute_contagion(tmp_path)
    assert report.human_corrections == 2
    assert report.machine_corrections == 3
    assert report.confirmed_idioms == 1
    assert report.unconfirmed_idioms == 2
    assert report.human_total == 3   # 2 + 1
    assert report.machine_total == 5  # 3 + 2


# ── PJ slug スコープ: 別 PJ の record が混ざっても当 PJ 分だけ数える ──────
# 実ローダ（_load_corrections / _load_idioms）を tmp ストアで動かし、capture_rate
# 方式の PJ スコープが効くことを検証する（全PJ共通 DATA_DIR pitfall）。

import json  # noqa: E402


def test_idioms_scoped_to_current_pj(tmp_path, monkeypatch):
    """別 PJ slug の idiom record は当 PJ 集計に混ざらない（#73 / 全PJ共通ストア）。"""
    from pj_slug import pj_slug_fast
    from correction_semantic import store as cs_store

    this_slug = pj_slug_fast(tmp_path)
    idioms_path = tmp_path / "correction_idioms.jsonl"
    rows = (
        [{"idiom": "a", "confirmed": True, "pj_slug": this_slug, "idiom_key": f"m{i}"}
         for i in range(2)]
        + [{"idiom": "b", "confirmed": False, "pj_slug": this_slug, "idiom_key": f"u{i}"}
           for i in range(3)]
        # 他 PJ の record（混ざってはいけない）
        + [{"idiom": "c", "confirmed": False, "pj_slug": "other-pj", "idiom_key": f"o{i}"}
           for i in range(50)]
    )
    with open(idioms_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    monkeypatch.setattr(cs_store, "default_idioms_path", lambda base=None: idioms_path)

    # 実 _load_idioms を使う（corrections は空にして idioms のみ検証）。
    monkeypatch.setattr(mc, "_load_corrections", lambda project_dir: [])
    report = mc.compute_contagion(tmp_path)
    assert report.confirmed_idioms == 2
    assert report.unconfirmed_idioms == 3  # 他PJ 50 件は混ざらない


def test_corrections_scoped_to_current_pj(tmp_path, monkeypatch):
    """別 PJ の correction record は当 PJ 集計に混ざらない（#73 / capture_rate 方式）。"""
    from audit import memory_contagion as mod

    corr_path = tmp_path / "corrections.jsonl"
    this_path = str(tmp_path)  # 当PJ project_path
    rows = (
        [{"source": "reflect_confirmed", "project_path": this_path} for _ in range(2)]
        + [{"source": "hook", "project_path": this_path} for _ in range(3)]
        # 他 PJ の correction（混ざってはいけない）
        + [{"source": "hook", "project_path": "/somewhere/other-pj"} for _ in range(50)]
    )
    with open(corr_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    monkeypatch.setattr(mod, "_corrections_path", lambda: corr_path)

    monkeypatch.setattr(mc, "_load_idioms", lambda project_dir: [])
    report = mc.compute_contagion(tmp_path)
    assert report.human_corrections == 2
    assert report.machine_corrections == 3  # 他PJ 50 件は混ざらない
