"""evolve_decision_ids.py の identity 関数テスト（#376）。

worktree 間で同一提案が別 ID になる重複登録バグの修正対象:
`proposal_id` を絶対パスでなく repo 相対パス + repo_id ベースにし、worktree が
違っても同一スキル・同一内容なら同じ ID を返すことを固定する。
`is_orphaned_worktree` は削除済み worktree の pending entry を判定する。
"""
import subprocess
import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_LIB))

import evolve_decision_ids as ids  # noqa: E402


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


# ─── repo_identity ─────────────────────────────────────────────────────────


def test_repo_identity_outside_git_falls_back_to_absolute_path(tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    skill = plain / "SKILL.md"
    skill.write_text("x", encoding="utf-8")

    identity = ids.repo_identity(str(skill))

    assert identity["repo_id"] is None
    assert identity["worktree_root"] is None
    assert identity["relative_path"] == str(skill)


def test_repo_identity_in_repo_returns_relative_path(tmp_path):
    repo = tmp_path / "my-project"
    _init_repo(repo)
    skill = repo / "skills" / "my-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("x", encoding="utf-8")

    identity = ids.repo_identity(str(skill))

    assert identity["relative_path"] == "skills/my-skill/SKILL.md"
    assert identity["worktree_root"] == str(repo.resolve())
    assert identity["repo_id"]


def test_repo_identity_shares_repo_id_across_worktrees(tmp_path):
    """同一リポジトリの worktree 間では repo_id が一致する（重複登録バグの根治対象）。"""
    repo = tmp_path / "main-repo"
    _init_repo(repo)
    (repo / "skills" / "my-skill").mkdir(parents=True)
    (repo / "skills" / "my-skill" / "SKILL.md").write_text("x", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add skill")

    wt = tmp_path / "worktrees" / "feature-x"
    _git(repo, "worktree", "add", "-q", "-b", "feat-x", str(wt))

    id_main = ids.repo_identity(str(repo / "skills" / "my-skill" / "SKILL.md"))
    id_wt = ids.repo_identity(str(wt / "skills" / "my-skill" / "SKILL.md"))

    assert id_main["repo_id"] == id_wt["repo_id"]
    assert id_main["relative_path"] == id_wt["relative_path"] == "skills/my-skill/SKILL.md"
    # worktree_root 自体は worktree ごとに異なる（orphan 判定の対象キー）
    assert id_main["worktree_root"] != id_wt["worktree_root"]


# ─── proposal_id: worktree 間で同一提案が同一 ID になる（#376 AC4）────────────


def test_proposal_id_matches_across_worktrees_for_same_content(tmp_path):
    repo = tmp_path / "main-repo"
    _init_repo(repo)
    (repo / "skills" / "my-skill").mkdir(parents=True)
    (repo / "skills" / "my-skill" / "SKILL.md").write_text("x", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add skill")

    wt = tmp_path / "worktrees" / "feature-x"
    _git(repo, "worktree", "add", "-q", "-b", "feat-x", str(wt))

    before_sha = ids.sha256("旧内容")
    pid_main = ids.proposal_id(str(repo / "skills" / "my-skill" / "SKILL.md"), before_sha)
    pid_wt = ids.proposal_id(str(wt / "skills" / "my-skill" / "SKILL.md"), before_sha)

    assert pid_main == pid_wt


def test_proposal_id_differs_for_different_repos(tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    _init_repo(repo_a)
    _init_repo(repo_b)
    (repo_a / "skills" / "s").mkdir(parents=True)
    (repo_b / "skills" / "s").mkdir(parents=True)

    before_sha = ids.sha256("同じ内容")
    pid_a = ids.proposal_id(str(repo_a / "skills" / "s" / "SKILL.md"), before_sha)
    pid_b = ids.proposal_id(str(repo_b / "skills" / "s" / "SKILL.md"), before_sha)

    assert pid_a != pid_b


def test_proposal_id_falls_back_to_absolute_path_outside_git(tmp_path):
    """git 管理外では従来どおり絶対パスベース（既存テストの後方互換）。"""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    skill = plain / "SKILL.md"
    before_sha = ids.sha256("x")

    pid1 = ids.proposal_id(str(skill), before_sha)
    pid2 = ids.proposal_id(str(skill), before_sha)
    assert pid1 == pid2  # 決定論


# ─── is_orphaned_worktree（#376 AC5）───────────────────────────────────────


def test_is_orphaned_worktree_true_when_root_missing(tmp_path):
    entry = {"worktree_root": str(tmp_path / "gone"), "skill_path": "/x/SKILL.md"}
    assert ids.is_orphaned_worktree(entry) is True


def test_is_orphaned_worktree_false_when_root_exists(tmp_path):
    root = tmp_path / "still-here"
    root.mkdir()
    entry = {"worktree_root": str(root), "skill_path": "/x/SKILL.md"}
    assert ids.is_orphaned_worktree(entry) is False


def test_is_orphaned_worktree_false_when_root_unknown(tmp_path):
    """worktree_root が無い（旧 entry / 判定不能）ときは保守的に orphan 扱いしない。"""
    entry = {"skill_path": str(tmp_path / "not-a-repo" / "SKILL.md")}
    assert ids.is_orphaned_worktree(entry) is False


# ─── merge_revert_fields（#402-D PR1 §2.1 item1）───────────────────────────


def test_merge_revert_fields_applies_allowlist_and_drops_none():
    entry = {"id": "e1", "skill_name": "s"}
    revert_fields = {
        "revert_before_b64": "b64",
        "revert_schema_version": ids.REVERT_SCHEMA_VERSION,
        "revert_unavailable_reason": None,  # None は書かない
        "not_a_revert_field": "ignored",  # allowlist 外は書かない
    }
    ids.merge_revert_fields(entry, revert_fields)
    assert entry["revert_before_b64"] == "b64"
    assert entry["revert_schema_version"] == ids.REVERT_SCHEMA_VERSION
    assert "revert_unavailable_reason" not in entry
    assert "not_a_revert_field" not in entry


def test_merge_revert_fields_noop_when_falsy():
    entry = {"id": "e1"}
    ids.merge_revert_fields(entry, {})
    assert entry == {"id": "e1"}
    ids.merge_revert_fields(entry, None)
    assert entry == {"id": "e1"}


def test_merge_revert_fields_raises_on_key_collision():
    entry = {"id": "e1", "after_sha": "existing"}
    with pytest.raises(ValueError):
        ids.merge_revert_fields(entry, {"after_sha": "new"})


# ─── _decision_event_id_from_sha（#402-D PR1 §2.1 item2・round2 Must4）───────


@pytest.mark.parametrize("kind", ["pending", "accept", "reject"])
@pytest.mark.parametrize("revert_generation", [0, 3])
def test_decision_event_id_from_sha_bit_identical_to_decision_event_id(kind, revert_generation):
    """after_sha = sha256(after_content) であるとき、_decision_event_id_from_sha は
    decision_event_id(proposal_id, kind, after_content, revert_generation) と
    ビット同一の ID を返す（round2 codex Must4 — after 全文を運ばなくても sha だけで
    同じ ID を再構成できるという等価性の契約テスト）。"""
    proposal_id = "evdiff_abc123"
    after_content = "こんにちは、これは after 本文です。"
    after_sha = ids.sha256(after_content)

    expected = ids.decision_event_id(proposal_id, kind, after_content, revert_generation)
    actual = ids._decision_event_id_from_sha(proposal_id, kind, after_sha, revert_generation)
    assert actual == expected
