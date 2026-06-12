#!/usr/bin/env python3
"""#477-2: remediation suppression ledger のテスト。

個別承認フローでユーザーがスキップ/却下した提案を dedup_key 単位 + TTL で記録し、
次回 evolve で同じ提案が再出しないよう抑制する（べき等性原則 = 重複提案 MUST NOT）。
triage_ledger（#308）のパターンを踏襲: per-slug 分離・worktree 安全 slug・TTL 45日・
dry-run 非書込。決定論・LLM 非依存。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_test_dir = Path(__file__).resolve().parent
_lib_dir = _test_dir.parent
sys.path.insert(0, str(_lib_dir))

import remediation.suppression_ledger as sl  # noqa: E402


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


def _issue(type_="line_limit_violation", file_="rule.md", **detail):
    return {"type": type_, "file": file_, "detail": detail}


# ── dedup_key ────────────────────────────────────────────────
class TestDedupKey:
    def test_stable_for_same_issue(self):
        a = _issue(file_="x.md", lines=11, limit=10)
        b = _issue(file_="x.md", lines=11, limit=10)
        assert sl.dedup_key(a) == sl.dedup_key(b)

    def test_differs_by_type(self):
        a = _issue(type_="line_limit_violation", file_="x.md")
        b = _issue(type_="hardcoded_value", file_="x.md")
        assert sl.dedup_key(a) != sl.dedup_key(b)

    def test_differs_by_file(self):
        a = _issue(file_="a.md")
        b = _issue(file_="b.md")
        assert sl.dedup_key(a) != sl.dedup_key(b)


# ── slug 解決（triage_ledger と同パターン） ──────────────────
class TestResolveSlug:
    def test_in_worktree_returns_main_repo_basename(self, tmp_path):
        repo = tmp_path / "main-repo"
        _init_repo(repo)
        wt = tmp_path / "worktrees" / "feature-x"
        _git(repo, "worktree", "add", "-q", "-b", "feat-x", str(wt))
        assert sl.resolve_slug(cwd=wt) == "main-repo"

    def test_outside_git_returns_unattributed(self, tmp_path):
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert sl.resolve_slug(cwd=plain) == sl.UNATTRIBUTED_SLUG


# ── record / suppress ────────────────────────────────────────
class TestSuppression:
    def test_record_then_suppressed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        issue = _issue(file_="x.md", lines=11, limit=10)
        assert not sl.is_suppressed(issue, slug="proj")
        sl.record_rejection(issue, slug="proj")
        assert sl.is_suppressed(issue, slug="proj")

    def test_unrecorded_not_suppressed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        sl.record_rejection(_issue(file_="a.md"), slug="proj")
        assert not sl.is_suppressed(_issue(file_="b.md"), slug="proj")

    def test_per_slug_isolation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        issue = _issue(file_="x.md")
        sl.record_rejection(issue, slug="proj-a")
        assert sl.is_suppressed(issue, slug="proj-a")
        assert not sl.is_suppressed(issue, slug="proj-b")

    def test_ttl_expiry_resurfaces(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        issue = _issue(file_="x.md")
        sl.record_rejection(issue, slug="proj", now=1000.0, ttl_days=45)
        # TTL 内は抑制
        assert sl.is_suppressed(issue, slug="proj", now=1000.0 + 10 * 86400)
        # TTL 超過後は再 surface（抑制解除）
        assert not sl.is_suppressed(issue, slug="proj", now=1000.0 + 46 * 86400)

    def test_filter_suppressed_splits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sl, "LEDGER_ROOT", tmp_path / "remediation_suppression")
        kept = _issue(file_="keep.md")
        dropped = _issue(file_="drop.md")
        sl.record_rejection(dropped, slug="proj")
        out = sl.filter_suppressed([kept, dropped], slug="proj")
        assert out["surface"] == [kept]
        assert out["suppressed"] == [dropped]


# ── dry-run 非書込（#308 pitfall 再発防止） ──────────────────
class TestDryRunNoWrite:
    def test_persist_false_does_not_write(self, tmp_path, monkeypatch):
        root = tmp_path / "remediation_suppression"
        monkeypatch.setattr(sl, "LEDGER_ROOT", root)
        issue = _issue(file_="x.md")
        sl.record_rejection(issue, slug="proj", persist=False)
        # ファイルが一切作られていない
        assert not root.exists() or list(root.glob("*.jsonl")) == []
        # 後続の is_suppressed も False（記録されていない）
        assert not sl.is_suppressed(issue, slug="proj")

    def test_persist_true_writes_file(self, tmp_path, monkeypatch):
        root = tmp_path / "remediation_suppression"
        monkeypatch.setattr(sl, "LEDGER_ROOT", root)
        sl.record_rejection(_issue(file_="x.md"), slug="proj", persist=True)
        files = list(root.glob("*.jsonl"))
        assert len(files) == 1
        recs = [json.loads(l) for l in files[0].read_text().splitlines() if l.strip()]
        assert recs and recs[0].get("dedup_key")
