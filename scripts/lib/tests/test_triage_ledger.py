#!/usr/bin/env python3
"""triage_ledger.py のテスト — SKIP 判断の TTL・再発カウンタ永続化（Issue #308）。

3層の見直しトリガー（抑制 / 再発エスカレーション / TTL 切れ）と
per-slug 分離・worktree 安全 slug 解決を検証する。決定論・LLM 非依存。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
sys.path.insert(0, str(_lib_dir))

import triage_ledger as ledger


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("x")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")


# ─────────────────────────────────────────────────────────────────
# slug 解決（optimize_history_store と同パターン: worktree 安全）
# ─────────────────────────────────────────────────────────────────
class TestResolveSlug:
    def test_in_normal_repo_returns_repo_basename(self, tmp_path):
        repo = tmp_path / "my-project"
        _init_repo(repo)
        assert ledger.resolve_slug(cwd=repo) == "my-project"

    def test_in_worktree_returns_main_repo_basename(self, tmp_path):
        repo = tmp_path / "main-repo"
        _init_repo(repo)
        wt = tmp_path / "worktrees" / "feature-x"
        _git(repo, "worktree", "add", "-q", "-b", "feat-x", str(wt))
        assert ledger.resolve_slug(cwd=wt) == "main-repo"

    def test_outside_git_returns_basename(self, tmp_path):
        # #47: 非git dir は basename（writer pj_slug_fast と一致・resolve_pj_slug に単一ソース化）。
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert ledger.resolve_slug(cwd=plain) == "not-a-repo"


# ─────────────────────────────────────────────────────────────────
# ストア（per-slug 分離・読み書き）
# ─────────────────────────────────────────────────────────────────
class TestStore:
    def test_path_under_ledger_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")
        assert ledger.ledger_path("foo") == tmp_path / "triage_decisions" / "foo.jsonl"

    def test_unsafe_chars_sanitized(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")
        assert ledger.ledger_path("a/b").name == "a_b.jsonl"

    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")
        assert ledger.load_ledger("nope") == {}

    def test_per_slug_separation(self, tmp_path, monkeypatch):
        """別 slug のレコードは混ざらない（pitfall_global_datadir_single_file 対策）。"""
        monkeypatch.setattr(ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")
        rec_a = ledger._new_record("k1", "SKIP", 0.0, ["dup"], now=1000.0)
        rec_b = ledger._new_record("k2", "SKIP", 0.0, ["dup"], now=1000.0)
        ledger.upsert_record(rec_a, "proj-a")
        ledger.upsert_record(rec_b, "proj-b")
        assert set(ledger.load_ledger("proj-a").keys()) == {"k1"}
        assert set(ledger.load_ledger("proj-b").keys()) == {"k2"}

    def test_upsert_is_last_write_wins_no_unbounded_growth(self, tmp_path, monkeypatch):
        """同一 candidate_key を複数回 upsert しても load 時は 1 件に collapse する（肥大化防止）。"""
        monkeypatch.setattr(ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")
        for i in range(5):
            rec = ledger._new_record("k1", "SKIP", 0.0, ["dup"], now=1000.0 + i)
            rec["times_seen"] = i + 1
            ledger.upsert_record(rec, "proj")
        loaded = ledger.load_ledger("proj")
        assert set(loaded.keys()) == {"k1"}
        assert loaded["k1"]["times_seen"] == 5

    def test_compact_rewrites_file_to_one_line_per_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")
        for i in range(10):
            ledger.upsert_record(ledger._new_record("k1", "SKIP", 0.0, ["dup"], now=1000.0 + i), "proj")
        path = ledger.ledger_path("proj")
        assert path.read_text().count("\n") > 1  # append 累積
        ledger.compact("proj")
        assert path.read_text().strip().count("\n") == 0  # 1 行に圧縮


# ─────────────────────────────────────────────────────────────────
# 3層トリガー
# ─────────────────────────────────────────────────────────────────
DAY = 86400.0


class TestApplyLedger:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")

    def _skip_meta(self, key="deploy skill"):
        return {
            "skill_name": key,
            "recommendation": "SKIP",
            "reuse_rate": 0.01,
            "duplicate_candidates": ["deploy skill clone"],
            "reason": "low reuse + dup",
        }

    def test_first_skip_records_and_surfaces(self):
        """初回 SKIP は記録され、抑制されず通常通り surface される。"""
        meta = self._skip_meta()
        out = ledger.apply_ledger(meta, slug="proj", now=1000.0)
        assert out["recommendation"] == "SKIP"
        assert out["ledger_status"] == "new"
        assert out["suppressed"] is False
        rec = ledger.load_ledger("proj")[ledger.candidate_key("deploy skill")]
        assert rec["times_seen"] == 1
        assert rec["times_skipped"] == 1

    def test_second_skip_within_cooldown_is_suppressed(self):
        """① 抑制: SKIP 済み & クールダウン内 & 再発閾値未満 → suppressed=True。"""
        meta = self._skip_meta()
        ledger.apply_ledger(meta, slug="proj", now=1000.0)
        out = ledger.apply_ledger(meta, slug="proj", now=1000.0 + DAY)  # 1日後
        assert out["suppressed"] is True
        assert out["ledger_status"] == "suppressed"
        rec = ledger.load_ledger("proj")[ledger.candidate_key("deploy skill")]
        assert rec["times_seen"] == 2

    def test_escalates_to_review_after_threshold(self):
        """② 再発エスカレーション: times_skipped >= ESCALATE_N → SKIP→REVIEW。"""
        meta = self._skip_meta()
        now = 1000.0
        for _ in range(ledger.ESCALATE_N):
            out = ledger.apply_ledger(meta, slug="proj", now=now)
            now += DAY
        assert out["recommendation"] == "REVIEW"
        assert out["ledger_status"] == "escalated"
        assert out["suppressed"] is False
        assert "繰り返し検出" in out["ledger_note"]

    def test_ttl_expiry_forces_one_reeval(self):
        """③ TTL 切れ: now > decided_at + ttl_days → 🔄 1回だけ強制再評価。"""
        meta = self._skip_meta()
        ledger.apply_ledger(meta, slug="proj", now=1000.0)
        expired = 1000.0 + (ledger.DEFAULT_TTL_DAYS + 1) * DAY
        out = ledger.apply_ledger(meta, slug="proj", now=expired)
        assert out["suppressed"] is False
        assert out["ledger_status"] == "ttl_expired"
        assert "🔄" in out["ledger_note"]
        # 再評価後は decided_at が更新され、直後の再発は再び抑制される
        out2 = ledger.apply_ledger(meta, slug="proj", now=expired + DAY)
        assert out2["ledger_status"] == "suppressed"

    def test_non_skip_recommendation_passes_through(self):
        """SKIP 以外（CREATE/REVIEW）は台帳に記録するが抑制しない。"""
        meta = dict(self._skip_meta())
        meta["recommendation"] = "CREATE"
        out = ledger.apply_ledger(meta, slug="proj", now=1000.0)
        assert out["recommendation"] == "CREATE"
        assert out["suppressed"] is False


class TestSummarize:
    def test_summary_line_counts_suppressed(self):
        results = [
            {"suppressed": True}, {"suppressed": True},
            {"suppressed": False},
        ]
        line = ledger.summarize_suppressed(results)
        assert "2" in line
        assert "✓" in line

    def test_summary_line_present_even_when_zero(self):
        """沈黙≠評価: 0 件でも1行残す（ADR-028 同思想）。"""
        line = ledger.summarize_suppressed([{"suppressed": False}])
        assert line  # 空文字でない
        assert "0" in line


class TestApplyLedgerPersistGate:
    """persist=False（dry-run 経路）では台帳を一切書かないが、判定は計算する。

    dry-run の「変更なし」契約を ledger 層で守る回帰テスト（#308 dry-run 副作用バグ）。
    """

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ledger, "LEDGER_ROOT", tmp_path / "triage_decisions")

    def _skip_meta(self, key="deploy skill"):
        return {
            "skill_name": key,
            "recommendation": "SKIP",
            "reuse_rate": 0.01,
            "duplicate_candidates": ["deploy skill clone"],
            "reason": "low reuse + dup",
        }

    def test_persist_false_writes_nothing(self):
        """persist=False なら台帳ファイルを作らない（変更なし契約）。"""
        meta = self._skip_meta()
        ledger.apply_ledger(meta, slug="proj", now=1000.0, persist=False)
        # ファイルが存在しない（書き込みゼロ）
        assert not ledger.ledger_path("proj").exists()
        # load も空
        assert ledger.load_ledger("proj") == {}

    def test_persist_false_still_computes_decision(self):
        """persist=False でも 3層判定（recommendation/ledger_status/suppressed）は返す。"""
        meta = self._skip_meta()
        out = ledger.apply_ledger(meta, slug="proj", now=1000.0, persist=False)
        assert out["recommendation"] == "SKIP"
        assert out["ledger_status"] == "new"
        assert out["suppressed"] is False
        assert out["candidate_key"] == ledger.candidate_key("deploy skill")

    def test_persist_false_decision_matches_persisted_first_skip(self):
        """初回 SKIP の判定は persist の True/False で一致する（観測値は同じ）。"""
        meta = self._skip_meta()
        dry = ledger.apply_ledger(meta, slug="dry", now=1000.0, persist=False)
        wet = ledger.apply_ledger(meta, slug="wet", now=1000.0, persist=True)
        for k in ("recommendation", "ledger_status", "suppressed", "candidate_key"):
            assert dry[k] == wet[k]

    def test_repeated_dry_run_does_not_escalate(self):
        """dry-run を ESCALATE_N 回繰り返しても台帳が育たず昇格しない（副作用なし）。"""
        meta = self._skip_meta()
        now = 1000.0
        for _ in range(ledger.ESCALATE_N + 2):
            out = ledger.apply_ledger(meta, slug="proj", now=now, persist=False)
            now += DAY
        # 毎回「初回 SKIP」のまま（台帳に履歴が残らないため）
        assert out["recommendation"] == "SKIP"
        assert out["ledger_status"] == "new"
        assert not ledger.ledger_path("proj").exists()

    def test_persist_false_passthrough_recommendation(self):
        """SKIP 以外（CREATE）の passthrough も persist=False なら書かない。"""
        meta = dict(self._skip_meta())
        meta["recommendation"] = "CREATE"
        out = ledger.apply_ledger(meta, slug="proj", now=1000.0, persist=False)
        assert out["recommendation"] == "CREATE"
        assert out["ledger_status"] == "passthrough"
        assert not ledger.ledger_path("proj").exists()

    def test_persist_false_does_not_mutate_existing_record(self):
        """既存レコードがあっても persist=False は台帳を更新しない（読むだけ）。"""
        meta = self._skip_meta()
        # 先に 1 件永続化（初回 SKIP）
        ledger.apply_ledger(meta, slug="proj", now=1000.0, persist=True)
        before = dict(ledger.load_ledger("proj")[ledger.candidate_key("deploy skill")])
        # dry-run で 1 日後に再評価 → 抑制判定が返るが台帳は不変
        out = ledger.apply_ledger(meta, slug="proj", now=1000.0 + DAY, persist=False)
        assert out["ledger_status"] == "suppressed"
        assert out["suppressed"] is True
        after = ledger.load_ledger("proj")[ledger.candidate_key("deploy skill")]
        assert after == before  # times_seen 等が増えていない
