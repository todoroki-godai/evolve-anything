"""spec_trigger のテスト。

2 層:
  ① is_spec_relevant_commit のユニット（純関数・ゲートの中心ロジック）
  ② detect() の実 temp-git E2E（git を本当に叩く。合成 fixture の false
     confidence を避ける — pitfall_claude_md_skills_parser_format /
     learning_synthetic_fixture_false_confidence）

LLM は一切呼ばない（決定論モジュール）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import spec_trigger  # noqa: E402


# ─────────────────────────────────────────────────────────────────
# ① ゲート純関数
# ─────────────────────────────────────────────────────────────────
class TestIsSpecRelevantCommit:
    def test_feat_changing_code_without_spec_fires(self):
        # 実コーパスの真 fire 例（941fa14f 相当）
        fires, breaking = spec_trigger.is_spec_relevant_commit(
            "feat(remediation): proposable を confidence で2分割",
            ["scripts/lib/remediation.py", "scripts/tests/test_remediation.py"],
        )
        assert fires is True
        assert breaking is False

    def test_refactor_changing_code_without_spec_fires(self):
        fires, _ = spec_trigger.is_spec_relevant_commit(
            "refactor(evolve): モジュール分割",
            ["scripts/lib/evolve.py"],
        )
        assert fires is True

    def test_fix_does_not_fire(self):
        # バグ修正は挙動を触っても仕様を変えない → quiet（FP 抑制の肝）
        fires, _ = spec_trigger.is_spec_relevant_commit(
            "fix(hardcoded_detector): 過剰検出を是正",
            ["scripts/lib/hardcoded_detector.py"],
        )
        assert fires is False

    def test_chore_release_does_not_fire(self):
        fires, _ = spec_trigger.is_spec_relevant_commit(
            "chore(release): v1.90.1",
            [".claude-plugin/plugin.json", "CHANGELOG.md"],
        )
        assert fires is False

    def test_feat_touching_claude_md_is_quiet(self):
        # CLAUDE.md = この PJ の生きた spec。触れていれば drift でない（ae10d1d3 相当）
        fires, _ = spec_trigger.is_spec_relevant_commit(
            "feat(subagent-guard): 時間窓ベースへ変更",
            ["scripts/lib/subagent.py", "CLAUDE.md", "SPEC.md"],
        )
        assert fires is False

    def test_feat_touching_spec_dir_is_quiet(self):
        fires, _ = spec_trigger.is_spec_relevant_commit(
            "feat(x): something",
            ["scripts/lib/x.py", "spec/x.md"],
        )
        assert fires is False

    def test_feat_touching_adr_is_quiet(self):
        fires, _ = spec_trigger.is_spec_relevant_commit(
            "feat(x): something",
            ["scripts/lib/x.py", "docs/decisions/099-x.md"],
        )
        assert fires is False

    def test_feat_with_only_docs_does_not_fire(self):
        # 挙動コード（scripts/hooks の .py）を触っていない → 対象外
        fires, _ = spec_trigger.is_spec_relevant_commit(
            "feat(docs): add guide",
            ["docs/site/index.html", "README.md"],
        )
        assert fires is False

    def test_breaking_feat_flagged(self):
        fires, breaking = spec_trigger.is_spec_relevant_commit(
            "feat(api)!: drop legacy field",
            ["scripts/lib/api.py"],
        )
        assert fires is True
        assert breaking is True

    def test_breaking_bang_after_scope(self):
        _, breaking = spec_trigger.is_spec_relevant_commit(
            "feat!: big change",
            ["scripts/lib/api.py"],
        )
        assert breaking is True

    def test_hooks_py_change_fires(self):
        fires, _ = spec_trigger.is_spec_relevant_commit(
            "feat(hook): new observe",
            ["hooks/observe.py"],
        )
        assert fires is True


class TestCommitType:
    def test_plain(self):
        assert spec_trigger.commit_type("feat(x): y") == ("feat", False)

    def test_breaking(self):
        assert spec_trigger.commit_type("feat(x)!: y") == ("feat", True)

    def test_no_scope(self):
        assert spec_trigger.commit_type("fix: y") == ("fix", False)


# ─────────────────────────────────────────────────────────────────
# ② detect() の実 temp-git E2E
# ─────────────────────────────────────────────────────────────────
def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, subject: str, files: dict[str, str]) -> str:
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    r = tmp_path / "myproj"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "checkout", "-q", "-b", "main")
    _commit(r, "chore: init", {"README.md": "init"})
    # marker を temp に隔離（_DATA_DIR_OVERRIDE 経由。__getattr__ 提供名の直 patch は
    # 恒久 shadow するため使わない・#148/#136）。marker root は tmp_path/"spec_trigger"。
    monkeypatch.setattr(spec_trigger, "_DATA_DIR_OVERRIDE", tmp_path)
    return r


def test_first_run_sets_marker_without_flooding(repo: Path):
    # 履歴があっても初回はマーカーをセットするだけで提案しない
    _commit(repo, "feat(x): big feature", {"scripts/lib/x.py": "code"})
    res = spec_trigger.detect(cwd=repo, now=1000.0)
    assert res["message"] is None
    # マーカーは HEAD に進んでいる
    slug = spec_trigger.resolve_slug(repo)
    marker = json.loads((spec_trigger.MARKER_ROOT / f"{slug}.json").read_text())
    assert marker["last_sha"] == _git(repo, "rev-parse", "HEAD")


def test_new_unspecced_feat_fires(repo: Path):
    spec_trigger.detect(cwd=repo, now=1000.0)  # 初回マーカー
    _commit(repo, "feat(remediation): 挙動変更", {"scripts/lib/remediation.py": "v2"})
    res = spec_trigger.detect(cwd=repo, now=2000.0)
    assert res["message"] is not None
    assert "remediation" in res["message"]
    assert len(res["fires"]) == 1


def test_feat_touching_claude_md_stays_quiet(repo: Path):
    spec_trigger.detect(cwd=repo, now=1000.0)
    _commit(
        repo,
        "feat(x): with spec update",
        {"scripts/lib/x.py": "v2", "CLAUDE.md": "updated"},
    )
    res = spec_trigger.detect(cwd=repo, now=2000.0)
    assert res["message"] is None


def test_fix_stays_quiet(repo: Path):
    spec_trigger.detect(cwd=repo, now=1000.0)
    _commit(repo, "fix(x): bug", {"scripts/lib/x.py": "patched"})
    res = spec_trigger.detect(cwd=repo, now=2000.0)
    assert res["message"] is None


def test_spec_touch_in_range_clears_pending(repo: Path):
    spec_trigger.detect(cwd=repo, now=1000.0)
    # 未追従 feat で fire
    _commit(repo, "feat(a): change", {"scripts/lib/a.py": "v2"})
    res1 = spec_trigger.detect(cwd=repo, now=2000.0)
    assert res1["fires"]
    # その後 spec を更新するコミットが来たら pending は解消（沈黙）
    _commit(repo, "docs(spec): catch up", {"SPEC.md": "now documented"})
    res2 = spec_trigger.detect(cwd=repo, now=3000.0)
    assert res2["message"] is None
    slug = spec_trigger.resolve_slug(repo)
    marker = json.loads((spec_trigger.MARKER_ROOT / f"{slug}.json").read_text())
    assert marker["pending"] == []


def test_cooldown_reminder_then_drop(repo: Path):
    spec_trigger.detect(cwd=repo, now=1000.0)
    _commit(repo, "feat(a): change", {"scripts/lib/a.py": "v2"})
    # 初回 fire（即時 surface）
    r1 = spec_trigger.detect(cwd=repo, now=2000.0)
    assert r1["fires"]
    # cooldown 内の再起動では再提示しない
    r2 = spec_trigger.detect(cwd=repo, now=2001.0)
    assert r2["message"] is None
    # cooldown 明け → 1回だけリマインド
    later = 2000.0 + spec_trigger.COOLDOWN_DAYS * 86400.0 + 1
    r3 = spec_trigger.detect(cwd=repo, now=later)
    assert r3["message"] is not None
    assert r3["reminders"]
    # さらに cooldown 明け → MAX_REMINDERS 超で沈黙（nag しない）
    later2 = later + spec_trigger.COOLDOWN_DAYS * 86400.0 + 1
    r4 = spec_trigger.detect(cwd=repo, now=later2)
    assert r4["message"] is None


def test_dry_run_writes_nothing(repo: Path):
    spec_trigger.detect(cwd=repo, now=1000.0)  # 初回マーカー（persist=True）
    slug = spec_trigger.resolve_slug(repo)
    marker_file = spec_trigger.MARKER_ROOT / f"{slug}.json"
    before = marker_file.read_text()
    _commit(repo, "feat(a): change", {"scripts/lib/a.py": "v2"})
    # persist=False では検出はするが marker を書き換えない
    res = spec_trigger.detect(cwd=repo, now=2000.0, persist=False)
    assert res["fires"]  # 判定は走る
    assert marker_file.read_text() == before  # 書き込みゼロ


def test_breaking_change_suggests_adr(repo: Path):
    spec_trigger.detect(cwd=repo, now=1000.0)
    _commit(repo, "feat(api)!: breaking", {"scripts/lib/api.py": "v2"})
    res = spec_trigger.detect(cwd=repo, now=2000.0)
    assert res["message"] is not None
    assert "ADR" in res["message"]


def test_non_git_dir_is_silent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(spec_trigger, "_DATA_DIR_OVERRIDE", tmp_path)
    res = spec_trigger.detect(cwd=tmp_path / "nope", now=1000.0)
    assert res["message"] is None


def test_no_main_or_master_stays_silent_not_head(tmp_path: Path, monkeypatch):
    # trunk(main/master) を解決できないリポでは HEAD（現在ブランチ）に落とさず沈黙する。
    # 落とすと作業中の feature ブランチの自分のコミットを誤提案してしまう（レビュー指摘の回帰ガード）。
    r = tmp_path / "trunkless"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "checkout", "-q", "-b", "feature/wip")
    _commit(r, "feat(x): work in progress", {"scripts/lib/x.py": "code"})
    monkeypatch.setattr(spec_trigger, "_DATA_DIR_OVERRIDE", tmp_path)
    # head_sha は main も master も無いので None → detect は沈黙、マーカーも作らない
    assert spec_trigger.head_sha(r) is None
    res = spec_trigger.detect(cwd=r, now=1000.0)
    assert res["message"] is None
