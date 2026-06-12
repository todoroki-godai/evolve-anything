"""evolve_decisions.py のユニットテスト（#360-A, ADR-041）。

すべて LLM-free。accept/reject の記録は record_evolve_diff_decision 経由で
skill_quality（ルールベース・決定論）採点のみ。claude subprocess は呼ばない。
"""
import json
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_decisions as ed


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def skill_file(tmp_path):
    """テスト用スキル SKILL.md を作って path を返す。"""
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    p = skill_dir / "SKILL.md"
    p.write_text(
        "# my-skill\n\nトリガー: foo bar\n\n手順を踏む。\n", encoding="utf-8"
    )
    return p


@pytest.fixture
def result_with_match(skill_file):
    """discover.matched_skills に skill_file を1件持つ result を返す。"""
    return {
        "phases": {
            "discover": {
                "matched_skills": [
                    {
                        "matched_skill": "my-skill",
                        "skill_path": str(skill_file),
                        "pattern": "cat -> Read 多用",
                        "jaccard_score": 0.6,
                    }
                ]
            }
        }
    }


@pytest.fixture
def hist(tmp_path):
    return tmp_path / "optimize_history" / "testslug.jsonl"


def _read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ─── emit ────────────────────────────────────────────────────────────────


def test_emit_writes_pending_with_before_sha(result_with_match, skill_file, monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
    assert out["count"] == 1
    assert out["persisted"] is True
    queued = ed.read_queue("testslug")
    assert len(queued) == 1
    assert queued[0]["skill_name"] == "my-skill"
    assert queued[0]["before_sha"]  # non-empty
    assert queued[0]["fitness_func"] == "skill_quality"


def test_emit_dry_run_does_not_write(result_with_match, monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    # MARKER_ROOT も明示 patch する（#491: dry-run で marker を作らないことを検証）。
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    assert out["count"] == 1
    assert out["persisted"] is False
    assert ed.read_queue("testslug") == []  # 書き込みゼロ
    # #491: dry-run では marker も作らない（apply→drain 待ちポインタなので）
    assert not ed.marker_path("testslug").exists()
    assert out["marker_written"] is False


def test_emit_dry_run_does_not_clear_existing_marker(result_with_match, monkeypatch, tmp_path):
    """#491: dry-run は既存 marker を削除してもいけない（二方向違反）。"""
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    # 候補ゼロの result を作り、事前に marker を seed する
    empty_result = {"phases": {}}
    ed.write_pending_marker("testslug", [{"id": "x"}])
    assert ed.marker_path("testslug").exists()

    out = ed.emit_decisions(empty_result, dry_run=True, slug="testslug")
    assert out["count"] == 0
    # dry-run は既存 marker を消さない
    assert ed.marker_path("testslug").exists()
    assert out["marker_cleared"] is False


def test_emit_apply_writes_marker(result_with_match, skill_file, monkeypatch, tmp_path):
    """非 dry-run では従来どおり marker を書く（回帰防止）。"""
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
    assert out["count"] == 1
    assert ed.marker_path("testslug").exists()
    assert out["marker_written"] is True


def test_emit_dedups_by_skill_path(skill_file, monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    result = {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"matched_skill": "my-skill", "skill_path": str(skill_file), "pattern": "p1"},
                    {"matched_skill": "my-skill", "skill_path": str(skill_file), "pattern": "p2"},
                ]
            }
        }
    }
    out = ed.emit_decisions(result, dry_run=False, slug="testslug")
    assert out["count"] == 1  # 同一 skill_path は1件に畳む


def test_emit_overwrites_stale_queue(result_with_match, monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    ed._write_queue("testslug", [{"id": "stale", "skill_path": "x", "before_sha": "z"}])
    ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
    queued = ed.read_queue("testslug")
    assert all(q["id"] != "stale" for q in queued)  # 旧バッチは消える


# ─── ingest ──────────────────────────────────────────────────────────────


def test_ingest_applied_is_accept(result_with_match, skill_file, monkeypatch, tmp_path, hist):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
    # 適用された体で内容を変更する
    skill_file.write_text("# my-skill\n\n改善されたトリガー: foo bar baz\n\n手順を踏む。\n", encoding="utf-8")
    summary = ed.ingest_decisions("testslug", dry_run=False, history_file=hist)
    assert len(summary["accepted"]) == 1
    assert summary["rejected"] == []
    assert summary["skipped"] == []
    recs = _read_jsonl(hist)
    assert len(recs) == 1
    assert recs[0]["human_accepted"] is True
    assert recs[0]["fitness_func"] == "skill_quality"


def test_ingest_unchanged_with_explicit_reject(result_with_match, skill_file, monkeypatch, tmp_path, hist):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
    pid = out["pending"][0]["id"]
    # 内容は変えない（未適用）＋明示的に却下
    summary = ed.ingest_decisions(
        "testslug", rejected={pid: "ドメイン不一致"}, dry_run=False, history_file=hist
    )
    assert summary["rejected"] == [pid]
    assert summary["accepted"] == []
    recs = _read_jsonl(hist)
    assert len(recs) == 1
    assert recs[0]["human_accepted"] is False
    assert recs[0]["rejection_reason"] == "ドメイン不一致"


def test_ingest_unchanged_no_reject_is_skip_not_recorded(result_with_match, monkeypatch, tmp_path, hist):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    out = ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
    pid = out["pending"][0]["id"]
    summary = ed.ingest_decisions("testslug", dry_run=False, history_file=hist)
    assert summary["skipped"] == [pid]
    assert summary["accepted"] == []
    assert summary["rejected"] == []
    assert _read_jsonl(hist) == []  # skip は母集団に入れない


def test_ingest_dry_run_no_write_no_queue_mutation(result_with_match, skill_file, monkeypatch, tmp_path, hist):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
    skill_file.write_text("# my-skill changed\n", encoding="utf-8")
    summary = ed.ingest_decisions("testslug", dry_run=True, history_file=hist)
    assert len(summary["accepted"]) == 1  # 分類はする
    assert _read_jsonl(hist) == []  # でも書かない
    assert len(ed.read_queue("testslug")) == 1  # キューも触らない


def test_ingest_clears_consumed_queue(result_with_match, skill_file, monkeypatch, tmp_path, hist):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    ed.emit_decisions(result_with_match, dry_run=False, slug="testslug")
    skill_file.write_text("# my-skill changed\n", encoding="utf-8")
    ed.ingest_decisions("testslug", dry_run=False, history_file=hist)
    assert ed.read_queue("testslug") == []  # 消化済みは消える


def test_ingest_accepts_pending_directly_without_queue(result_with_match, skill_file, monkeypatch, tmp_path, hist):
    """#400 バグ#1: dry-run 運用では emit がキューを書かない。result.evolve_decisions.pending を
    直接 ingest に渡せば、apply 後のディスク差分から accept を記録できる（キュー不要）。"""
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    # dry-run の emit はキューを書かないが pending（before_sha 付き）は返す
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    assert out["persisted"] is False
    assert ed.read_queue("testslug") == []  # キューは空のまま
    # assistant が apply した体で内容変更
    skill_file.write_text("# my-skill\n\n改善: foo bar baz\n", encoding="utf-8")
    summary = ed.ingest_decisions(
        "testslug", dry_run=False, history_file=hist, pending=out["pending"]
    )
    assert len(summary["accepted"]) == 1  # キューが空でも result.pending から accept
    recs = _read_jsonl(hist)
    assert len(recs) == 1
    assert recs[0]["human_accepted"] is True


def test_ingest_pending_direct_does_not_mutate_queue(result_with_match, skill_file, monkeypatch, tmp_path, hist):
    """pending を直接渡したときはキューを SoT としないので、キューに触れない。"""
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text("# changed\n", encoding="utf-8")
    ed.ingest_decisions("testslug", dry_run=False, history_file=hist, pending=out["pending"])
    assert ed.read_queue("testslug") == []  # キューは生成も変更もされない


def test_e2e_dry_run_cycle_increments_fitness_store(result_with_match, skill_file, monkeypatch, tmp_path):
    """#400 バグ#1 の決定的実証（real verification）。

    dry-run 検証では『apply 境界』を越えないため、母集団が増えないバグを構造的に観測できなかった。
    本テストは dry-run 分析 → apply → Step 7.8 ingest の1サイクルを実走させ、**fitness が読む
    正準ストア optimize_history が実際に +1 される**ことを、ingest の default パス（明示 history_file
    なし）と fitness の load_history が同一経路を共有することまで含めて検証する。これが緑なら
    「実 evolve を回しても 0/30 から動かない」症状が構造的に解消されたことの証拠になる。
    """
    import optimize_history_store as ohs
    # 正準ストアを tmp に向ける（ingest の default 書込先と fitness の読込先が同一経路であることを使う）
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    # fitness_evolution を import 可能にする
    _fe_dir = _LIB.parent.parent / "skills" / "evolve-fitness" / "scripts"
    sys.path.insert(0, str(_fe_dir))
    import fitness_evolution as fe

    slug = "e2e-slug"
    # 1) dry-run の emit（キューは書かない）→ pending（before_sha 付き）を得る
    out = ed.emit_decisions(result_with_match, dry_run=True, slug=slug)
    assert ed.read_queue(slug) == []  # dry-run はストアに何も書かない（契約）

    # 2) assistant が apply（スキル内容を変更）
    skill_file.write_text("# my-skill\n\n改善されたトリガー: foo bar baz\n", encoding="utf-8")

    # 3) Step 7.8: result.pending を直接渡して ingest（明示 history_file なし＝正準ストアへ書く）
    summary = ed.ingest_decisions(slug, dry_run=False, pending=out["pending"])
    assert len(summary["accepted"]) == 1

    # 4) fitness 側が同じ正準ストアを読んで +1 を観測する（0/30 から動いた）
    hist_path = ohs.history_path(slug)
    history = fe.load_history(history_file=hist_path)
    assert len(history) == 1
    assert history[0]["human_accepted"] is True
    fe_result = fe.run_fitness_evolution(history=history)
    assert fe_result["data_count"] == 1  # ← これが旧コードでは永遠に 0 だった


def test_ingest_pending_direct_reject(result_with_match, skill_file, monkeypatch, tmp_path, hist):
    """pending 直接渡しでも明示却下は reject 記録される。"""
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    pid = out["pending"][0]["id"]
    summary = ed.ingest_decisions(
        "testslug", rejected={pid: "不一致"}, dry_run=False, history_file=hist, pending=out["pending"]
    )
    assert summary["rejected"] == [pid]
    recs = _read_jsonl(hist)
    assert recs[0]["human_accepted"] is False


def test_emit_no_matches_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    out = ed.emit_decisions({"phases": {"discover": {"matched_skills": []}}}, dry_run=False, slug="testslug")
    assert out["count"] == 0
    assert ed.read_queue("testslug") == []


# ─── skill_evolve 拡張（ADR-041 follow-up）─────────────────────────────────


def test_emit_includes_skill_evolve_high_medium(skill_file, monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    skill_dir = str(skill_file.parent)
    result = {
        "phases": {
            "skill_evolve": {
                "assessments": [
                    {"skill_name": "my-skill", "skill_dir": skill_dir, "suitability": "high"},
                ]
            }
        }
    }
    out = ed.emit_decisions(result, dry_run=False, slug="testslug")
    assert out["count"] == 1
    q = ed.read_queue("testslug")
    assert q[0]["skill_name"] == "my-skill"
    assert q[0]["proposal_type"] == "skill_evolve"
    assert q[0]["fitness_func"] == "skill_quality"  # 母集団は均質


def test_emit_skill_evolve_skips_rejected_and_already_evolved(skill_file, monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    skill_dir = str(skill_file.parent)
    result = {
        "phases": {
            "skill_evolve": {
                "assessments": [
                    {"skill_name": "my-skill", "skill_dir": skill_dir, "suitability": "rejected"},
                    {"skill_name": "my-skill", "skill_dir": skill_dir, "suitability": "already_evolved"},
                ]
            }
        }
    }
    out = ed.emit_decisions(result, dry_run=False, slug="testslug")
    assert out["count"] == 0  # high/medium 以外は提案対象外


def test_emit_dedups_across_discover_and_skill_evolve(skill_file, monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    skill_dir = str(skill_file.parent)
    result = {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"matched_skill": "my-skill", "skill_path": str(skill_file), "pattern": "p"}
                ]
            },
            "skill_evolve": {
                "assessments": [
                    {"skill_name": "my-skill", "skill_dir": skill_dir, "suitability": "medium"}
                ]
            },
        }
    }
    out = ed.emit_decisions(result, dry_run=False, slug="testslug")
    assert out["count"] == 1  # 同一 skill_path は1件に畳む（discover 優先）
