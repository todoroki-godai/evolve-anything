"""evolve drain enforcement のユニットテスト（#402）。

#402: ingest（Step 7.8 drain）が SKILL.md prose 依存だった enforcement gap を是正する。
本テストは drain の3要素を決定論で固定する:

  1. **pending marker**（emit が dry-run でも書く運用ポインタ。評価 store/queue とは別物）
  2. **drain_pending**（`evolve --drain` の実体。marker or result-json から pending を取り
     ingest→冪等記録→marker クリア）
  3. **undrained_applied**（SessionStart リマインドの signal。store を読まず #358 を踏まない）

すべて LLM-free・決定論。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_decisions as ed  # noqa: E402
import optimize_history_store as ohs  # noqa: E402


_BEFORE = "# my-skill\n\nトリガー: foo\n\n旧手順。\n"
_AFTER = "# my-skill\n\nトリガー: foo bar baz\n\n改善された手順を踏む。\n"


@pytest.fixture
def skill_file(tmp_path):
    d = tmp_path / "skills" / "my-skill"
    d.mkdir(parents=True)
    p = d / "SKILL.md"
    p.write_text(_BEFORE, encoding="utf-8")
    return p


@pytest.fixture
def result_with_match(skill_file):
    return {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"matched_skill": "my-skill", "skill_path": str(skill_file), "pattern": "p"}
                ]
            }
        }
    }


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """marker / queue / store を全て temp に隔離する。"""
    monkeypatch.setattr(ed, "MARKER_ROOT", tmp_path / "evolve_pending")
    monkeypatch.setattr(ed, "QUEUE_ROOT", tmp_path / "evolve_decisions")
    monkeypatch.setattr(ohs, "HISTORY_ROOT", tmp_path / "optimize_history")
    return tmp_path


def _store_count(slug="testslug"):
    return len(ohs.load_history(slug))


# ─── 1. marker は環境非依存（hook/tool で割れない＝#358 回避） ────────────────


@pytest.mark.real_marker_root
@pytest.mark.real_home  # MARKER_ROOT は import 時に実 home で凍結。autouse の HOME 隔離をオプトアウト（#471）
def test_marker_root_is_home_based_not_env_derived():
    # QUEUE_ROOT は DATA_DIR(env 派生) 配下だが、MARKER_ROOT は home 固定。
    # これにより emit(tool 文脈)と SessionStart(hook 文脈)が同一パスに合意する。
    assert ed.MARKER_ROOT == Path.home() / ".claude" / "evolve-anything" / "evolve_pending"
    assert "evolve_pending" in str(ed.marker_path("anything"))


# ─── 2. emit は dry-run でも marker を書くが store/queue は触らない ───────────


def test_emit_dry_run_writes_marker_but_not_queue_or_store(result_with_match, isolated):
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    assert out["count"] == 1
    # marker は書かれる（drain 検出の signal）
    marker = ed.read_pending_marker("testslug")
    assert marker is not None
    assert len(marker["pending"]) == 1
    assert marker["pending"][0]["before_sha"]
    # 評価状態（queue / optimize_history）は dry-run で一切触らない
    assert ed.read_queue("testslug") == []
    assert _store_count() == 0


def test_emit_empty_run_does_not_clear_another_runs_marker(result_with_match, isolated):
    first = ed.emit_decisions(
        result_with_match, dry_run=True, slug="testslug", run_id="run-a"
    )
    assert ed.read_pending_marker("testslug") is not None
    # 候補ゼロの別 run が、既存 run の drain 待ちを消してはならない。
    ed.emit_decisions(
        {"phases": {}}, dry_run=True, slug="testslug", run_id="run-b"
    )
    marker = ed.read_pending_marker("testslug")
    assert [run["run_id"] for run in marker["runs"]] == ["run-a"]
    assert marker["pending"][0]["id"] == first["pending"][0]["id"]


def test_emit_same_skill_in_concurrent_runs_collapses_to_one_entry(result_with_match, isolated):
    """同一 skill_path への提案は content identity が同じ＝1件に畳む。

    proposal ID に run_id を混ぜると、同じファイルへの同じ提案が run ごとに別 entry として
    積み上がり、marker/queue が単調増加して optimize_history の冪等記録も壊れる。
    別 worktree は skill_path（絶対パス）が異なるので、この dedup は並行 run を潰さない。
    """
    first = ed.emit_decisions(
        result_with_match, dry_run=True, slug="testslug", run_id="run-a"
    )
    second = ed.emit_decisions(
        result_with_match, dry_run=True, slug="testslug", run_id="run-b"
    )

    assert first["pending"][0]["id"] == second["pending"][0]["id"]
    marker = ed.read_pending_marker("testslug")
    assert len(marker["pending"]) == 1
    assert [run["run_id"] for run in marker["runs"]] == ["run-b"]


def test_emit_keeps_other_runs_entries_for_different_skills(result_with_match, isolated, tmp_path):
    """別 skill を提案する並行 run は互いに保持される（run 分離の本体要件）。"""
    other = tmp_path / "skills" / "other-skill"
    other.mkdir(parents=True)
    other_md = other / "SKILL.md"
    other_md.write_text(_BEFORE, encoding="utf-8")
    other_result = {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"matched_skill": "other-skill", "skill_path": str(other_md), "pattern": "p"}
                ]
            }
        }
    }

    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug", run_id="run-a")
    ed.emit_decisions(other_result, dry_run=True, slug="testslug", run_id="run-b")

    marker = ed.read_pending_marker("testslug")
    assert [run["run_id"] for run in marker["runs"]] == ["run-a", "run-b"]
    assert {entry["skill_name"] for entry in marker["pending"]} == {"my-skill", "other-skill"}


def test_repeated_dry_run_emits_do_not_accumulate(result_with_match, isolated):
    """標準フロー（dry-run evolve）を繰り返しても marker は増えない（#279 回帰）。

    emit は毎回 run_id 未指定＝新規 uuid になるため、supersede が無いと
    「並行 run」と「自分の前回 run」が区別できず runs[] が単調増加する。
    """
    for _ in range(5):
        ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")

    marker = ed.read_pending_marker("testslug")
    assert len(marker["runs"]) == 1
    assert len(marker["pending"]) == 1


def test_repeated_emits_then_single_apply_records_once(result_with_match, skill_file, isolated):
    """人間の apply 1回は optimize_history 1行（run 跨ぎの冪等記録・#279 回帰）。"""
    out = None
    for _ in range(5):
        out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")  # apply は1回だけ
    pid = out["pending"][0]["id"]

    summary = ed.drain_pending(slug="testslug", accepted={pid})

    assert len(summary["accepted"]) == 1
    assert _store_count() == 1


def test_undrained_applied_does_not_duplicate_same_skill(result_with_match, skill_file, isolated):
    """SessionStart リマインドが同じスキルを重複表示しない（#279 回帰）。"""
    for _ in range(5):
        ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")

    assert len(ed.undrained_applied("testslug")) == 1


def test_expired_runs_are_dropped_at_read_time(result_with_match, isolated):
    """TTL 超過 run は read 時に落とす（writer 不在でも滞留しない・#279）。"""
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug", run_id="run-old")
    path = ed.marker_path("testslug")
    data = json.loads(path.read_text(encoding="utf-8"))
    stale = datetime.now(timezone.utc) - timedelta(days=ed.PENDING_TTL_DAYS + 1)
    data["runs"][0]["emitted_at"] = stale.isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert ed.read_pending_marker("testslug") is None
    assert ed.undrained_applied("testslug") == []


# ─── 3. undrained_applied は apply 済みのみ返し store を読まない ──────────────


def test_undrained_applied_empty_when_nothing_applied(result_with_match, isolated):
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    # まだ apply していない → 未 drain だが「適用済み」ではない → 沈黙
    assert ed.undrained_applied("testslug") == []


def test_undrained_applied_returns_applied_skill(result_with_match, skill_file, isolated):
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")  # apply 境界
    applied = ed.undrained_applied("testslug")
    assert len(applied) == 1
    assert applied[0]["skill_name"] == "my-skill"


def test_undrained_applied_empty_when_no_marker(isolated):
    assert ed.undrained_applied("nope") == []


# ─── 4. drain_pending: apply 後に記録し marker をクリア（CLI 実体） ───────────


def test_drain_pending_records_accept_and_clears_marker(result_with_match, skill_file, isolated):
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")  # apply
    summary = ed.drain_pending(slug="testslug", accepted={out["pending"][0]["id"]})
    assert len(summary["accepted"]) == 1
    assert _store_count() == 1  # 母集団 +1
    assert ohs.load_history("testslug")[-1]["human_accepted"] is True
    assert ed.read_pending_marker("testslug") is None  # marker クリア


def test_drain_pending_nothing_applied_records_nothing(result_with_match, isolated):
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    summary = ed.drain_pending(slug="testslug")  # 未 apply
    assert summary["accepted"] == []
    assert _store_count() == 0
    assert summary["deferred"] == summary["skipped"]
    # 未判断は deferred として保持し、後から apply/reject できる。
    assert ed.read_pending_marker("testslug") is not None


def test_drain_pending_reads_result_json_when_given(result_with_match, skill_file, isolated, tmp_path):
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    rj = tmp_path / "result.json"
    rj.write_text(json.dumps({"evolve_decisions": out}), encoding="utf-8")
    skill_file.write_text(_AFTER, encoding="utf-8")  # apply
    pid = out["pending"][0]["id"]
    summary = ed.drain_pending(slug="testslug", result_json=str(rj), accepted={pid})
    assert len(summary["accepted"]) == 1
    assert _store_count() == 1


def test_result_json_drain_consumes_only_that_runs_entries(
    result_with_match, skill_file, isolated, tmp_path
):
    """result-json drain は当該 run の提案だけ消化し、他 run の別提案を残す。"""
    other = tmp_path / "skills" / "other-skill"
    other.mkdir(parents=True)
    other_md = other / "SKILL.md"
    other_md.write_text(_BEFORE, encoding="utf-8")
    other_result = {
        "phases": {
            "discover": {
                "matched_skills": [
                    {"matched_skill": "other-skill", "skill_path": str(other_md), "pattern": "p"}
                ]
            }
        }
    }
    first = ed.emit_decisions(
        result_with_match, dry_run=True, slug="testslug", run_id="run-a"
    )
    ed.emit_decisions(other_result, dry_run=True, slug="testslug", run_id="run-b")
    result_path = tmp_path / "run-a.json"
    result_path.write_text(json.dumps({"evolve_decisions": first}), encoding="utf-8")
    skill_file.write_text(_AFTER, encoding="utf-8")  # run-a 側だけ apply

    pid = first["pending"][0]["id"]
    ed.drain_pending(slug="testslug", result_json=str(result_path), accepted={pid})

    marker = ed.read_pending_marker("testslug")
    assert [run["run_id"] for run in marker["runs"]] == ["run-b"]
    assert [entry["skill_name"] for entry in marker["pending"]] == ["other-skill"]


def test_drain_pending_idempotent_second_call_no_double(result_with_match, skill_file, isolated):
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")
    pid = out["pending"][0]["id"]
    ed.drain_pending(slug="testslug", accepted={pid})
    # 2回目（marker 再生成して再 drain しても二重記録なし＝冪等）
    out2 = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    ed.drain_pending(slug="testslug", accepted={out2["pending"][0]["id"]})
    assert _store_count() == 1


def test_second_accept_for_same_skill_is_recorded(result_with_match, skill_file, isolated):
    """同じスキルの2回目以降の accept も母集団に入る（#286）。

    proposal ID がパス単独だと ``entry_id = f"{pid}_accept"`` が恒久キーになり、
    2回目の accept が `record_evolve_diff_decision` の冪等 dedup で捨てられていた
    （1スキル生涯1件しか optimize_history に入らない）。ID に before_sha を混ぜて解消。
    """
    # 1周目: 提案 → 適用 → drain
    out1 = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")
    ed.drain_pending(slug="testslug", accepted={out1["pending"][0]["id"]})
    assert _store_count() == 1

    # 2周目: 同じスキルに別の提案 → 適用 → drain
    out2 = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER + "\nさらに改善した手順。\n", encoding="utf-8")
    ed.drain_pending(slug="testslug", accepted={out2["pending"][0]["id"]})

    assert _store_count() == 2


def test_proposal_id_changes_when_file_content_changes(result_with_match, skill_file, isolated):
    first = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    skill_file.write_text(_AFTER, encoding="utf-8")
    second = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")

    assert first["pending"][0]["id"] != second["pending"][0]["id"]


def test_legacy_path_only_id_entry_is_superseded(result_with_match, skill_file, isolated):
    """#279 のパス単独 ID で書かれた古い marker entry は新 emit で片付く（二重通知の防止）。"""
    import hashlib

    legacy_id = (
        "evdiff_" + hashlib.sha1(str(skill_file).encode("utf-8")).hexdigest()[:12]
    )
    ed.write_pending_marker(
        "testslug",
        [
            {
                "id": legacy_id,
                "skill_name": "my-skill",
                "skill_path": str(skill_file),
                "before_sha": ed.sha256(_BEFORE),
            }
        ],
        run_id="legacy_run",
    )

    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")

    marker = ed.read_pending_marker("testslug")
    ids = [entry["id"] for entry in marker["pending"]]
    assert legacy_id not in ids
    assert len(ids) == 1


def test_reemit_between_edits_records_single_accept(result_with_match, skill_file, isolated):
    """未 drain のまま内容が変わって再 emit されても、1回の apply は accept 1件（#290）。

    supersede が ID 一致だけだと、before_sha 違いの pending が同じファイルについて
    複数世代 residue し、ingest が「今のファイル ≠ その entry の before_sha」で
    **全部 accept 判定**する（1 apply が N 件記録＝#279 が潰した N 重記録の別経路再導入）。
    """
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")  # before = A
    skill_file.write_text(_AFTER, encoding="utf-8")  # 手で B へ
    ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")  # before = B
    skill_file.write_text(_AFTER + "\n最終形。\n", encoding="utf-8")  # 適用して C へ

    # 同一ファイルの未 drain 提案は最新1件だけが marker に残る
    marker = ed.read_pending_marker("testslug")
    assert len(marker["pending"]) == 1
    pid = marker["pending"][0]["id"]

    ed.drain_pending(slug="testslug", accepted={pid})
    assert _store_count() == 1


def test_content_cycle_does_not_drop_later_accept(result_with_match, skill_file, isolated):
    """内容が過去の状態へ循環して提案 ID が再利用されても accept は欠落しない（#290）。

    提案 ID は (パス, before_sha) なので A→B→A と戻ると過去の ID が復活する。
    判断イベントキーを提案 ID 単独にしていると3回目が冪等 dedup で捨てられる。
    """
    contents = [_AFTER, _BEFORE, _AFTER + "\n三度目。\n"]  # A→B, B→A, A→C
    for content in contents:
        out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
        skill_file.write_text(content, encoding="utf-8")
        ed.drain_pending(slug="testslug", accepted={out["pending"][0]["id"]})

    assert _store_count() == 3


def test_drain_pending_explicit_reject_records_negative(result_with_match, isolated):
    out = ed.emit_decisions(result_with_match, dry_run=True, slug="testslug")
    pid = out["pending"][0]["id"]
    summary = ed.drain_pending(slug="testslug", rejected={pid: "ドメイン不一致"})
    assert summary["rejected"] == [pid]
    assert _store_count() == 1
    assert ohs.load_history("testslug")[-1]["human_accepted"] is False
