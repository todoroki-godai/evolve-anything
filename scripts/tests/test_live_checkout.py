"""live_checkout のテスト（#548）。

決定論・LLM 非依存。実 temp-git E2E（bare origin + clone）で origin/HEAD・ahead・dirty を
本物の git で検証する（合成 fixture の false confidence を避ける・spec_trigger と同型）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import live_checkout  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), check=True, capture_output=True, text=True,
    ).stdout


def _write_plugin_marker(repo: Path) -> None:
    marker_dir = repo / ".claude-plugin"
    marker_dir.mkdir(parents=True, exist_ok=True)
    (marker_dir / "plugin.json").write_text(json.dumps({"name": "fixture"}), encoding="utf-8")


@pytest.fixture
def repo_pair(tmp_path: Path) -> Path:
    """bare origin + clone のペアを作り、既定ブランチ ``main`` を1コミットで確立する。

    clone された work tree を返す（``.claude-plugin/plugin.json`` 付き）。
    """
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)

    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", str(bare), str(work))
    _git(work, "config", "user.email", "t@t")
    _git(work, "config", "user.name", "t")
    _git(work, "checkout", "-q", "-b", "main")
    _write_plugin_marker(work)
    (work / "README.md").write_text("hello\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "add", ".claude-plugin/plugin.json")
    _git(work, "commit", "-q", "-m", "init")
    _git(work, "push", "-q", "-u", "origin", "main")
    # origin 側の HEAD を main に向ける（clone 直後は空 bare のため未設定）。
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=str(bare), check=True, capture_output=True,
    )
    # ローカル側の refs/remotes/origin/HEAD は clone 時点（空 bare）で解決済みのまま
    # 残るため、push 後に明示 set-head し直す（さもないと origin/HEAD が未解決のまま）。
    _git(work, "remote", "set-head", "origin", "main")
    return work


def _caller_file(work: Path) -> str:
    """work tree 内の何らかのファイルパスを caller_file として使う（実在不要・resolve のみ）。"""
    return str(work / "hooks" / "fake_caller.py")


class TestPositiveAndControl:
    def test_default_branch_clean_ahead0_is_safe(self, monkeypatch, repo_pair: Path):
        """陽性対照: 既定ブランチ・clean・ahead 0 → safe（誤検出しないこと）。"""
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair), expected_root=str(repo_pair))
        assert result.status == "safe"
        assert result.branch == "main"
        assert result.default_branch == "main"
        assert result.dirty_count == 0
        assert result.ahead_count == 0

    def test_non_default_branch_is_danger(self, monkeypatch, repo_pair: Path):
        _git(repo_pair, "checkout", "-q", "-b", "feature/x")
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "danger"
        assert result.branch == "feature/x"

    def test_dirty_tracked_file_is_danger(self, monkeypatch, repo_pair: Path):
        (repo_pair / "README.md").write_text("changed\n", encoding="utf-8")
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "danger"
        assert result.dirty_count == 1

    def test_untracked_file_alone_is_not_danger(self, monkeypatch, repo_pair: Path):
        """untracked（tracked でない）ファイルだけでは dirty に数えない契約。"""
        (repo_pair / "scratch.txt").write_text("x\n", encoding="utf-8")
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "safe"
        assert result.dirty_count == 0

    def test_ahead_of_origin_is_danger(self, monkeypatch, repo_pair: Path):
        (repo_pair / "extra.txt").write_text("x\n", encoding="utf-8")
        _git(repo_pair, "add", "extra.txt")
        _git(repo_pair, "commit", "-q", "-m", "local only, unpushed")
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "danger"
        assert result.ahead_count == 1


class TestUnknown:
    def test_origin_head_deleted_is_unknown(self, monkeypatch, repo_pair: Path):
        """判定不能ケース③: origin/HEAD 削除。"""
        _git(repo_pair, "remote", "set-head", "origin", "-d")
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "unknown"
        assert result.reason is not None
        assert "main" not in (result.default_branch or "")  # main を仮定していない

    def test_git_absent_is_unknown(self, monkeypatch, repo_pair: Path):
        """判定不能ケース②: git 不在。"""
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)

        def _raise(*a, **k):
            raise FileNotFoundError("git: command not found")

        monkeypatch.setattr(live_checkout.subprocess, "run", _raise)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "unknown"
        assert "git" in result.reason

    def test_caller_and_module_root_mismatch_is_unknown(self, tmp_path: Path, monkeypatch, repo_pair: Path):
        """3者照合: caller_root と module_root が別の木 → 判定不能（main 仮定を避ける安全側）。"""
        other = tmp_path / "other_tree"
        other.mkdir()
        _write_plugin_marker(other)
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        # caller_file は別の木（other）を指す。
        result = live_checkout.check(str(other / "hooks" / "fake.py"))
        assert result.status == "unknown"
        assert "caller" in result.reason

    def test_expected_root_mismatch_is_unknown(self, tmp_path: Path, monkeypatch, repo_pair: Path):
        other = tmp_path / "other_root"
        other.mkdir()
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair), expected_root=str(other))
        assert result.status == "unknown"

    def test_no_plugin_marker_is_unknown(self, tmp_path: Path, monkeypatch):
        """マーカーファイルが無い木では判定不能（root が確定できない）。"""
        bare_tree = tmp_path / "no_marker"
        bare_tree.mkdir()
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", None)
        # module_root は本物の live_checkout.py の実位置から解決される（override 無し）。
        # caller_root は marker が無いため None。
        result = live_checkout.check(str(bare_tree / "x.py"))
        assert result.status == "unknown"


class TestNonGitTreeReasonPointsToIssue:
    """本番の木が git 管理下でない unknown は理由文に #706 を含める（#548 B1・恒久表示が読み飛ばされるのを防ぐ）。"""

    def test_non_git_tree_reason_mentions_706(self, tmp_path: Path, monkeypatch):
        tree = tmp_path / "cache_tree"  # git 管理外（tmp は git repo の外）
        _write_plugin_marker(tree)
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", tree)
        result = live_checkout.check(str(tree / "hooks" / "fake.py"))
        assert result.status == "unknown"
        assert "#706 で対応中" in result.reason

    def test_other_unknown_reason_has_no_706(self, monkeypatch, repo_pair: Path):
        """陽性対照: git 管理下で origin/HEAD が無いだけの unknown には付かない。"""
        _git(repo_pair, "remote", "set-head", "origin", "-d")
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "unknown"
        assert "#706" not in result.reason

    def test_safe_has_no_reason(self, monkeypatch, repo_pair: Path):
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "safe"
        assert result.reason is None


class TestTreeInsideAnotherRepo:
    """実行木が別 repo の内側（無視された subdir）なら判定不能。本番の実配置（~/.claude/plugins/cache）と同型（#548/#706）。"""

    @pytest.fixture
    def nested_tree(self, repo_pair: Path) -> Path:
        (repo_pair / ".gitignore").write_text("plugins/\n", encoding="utf-8")
        tree = repo_pair / "plugins" / "cache" / "ver"
        _write_plugin_marker(tree)
        # 本番の性質の再現確認: 無視されていても toplevel は包む repo に解決される。
        top = _git(tree, "rev-parse", "--show-toplevel").strip()
        assert Path(top).resolve() == repo_pair.resolve()
        assert Path(top).resolve() != tree.resolve()
        return tree

    def test_nested_ignored_tree_is_unknown_with_paths_and_issue(self, monkeypatch, nested_tree: Path, repo_pair: Path):
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", nested_tree)
        result = live_checkout.check(str(nested_tree / "hooks" / "fake.py"))
        assert result.status == "unknown"
        assert str(nested_tree.resolve()) in result.reason
        assert str(repo_pair.resolve()) in result.reason
        assert "#706 で対応中" in result.reason
        assert result.branch is None  # 別 repo の branch/dirty へ進んでいない

    def test_tree_is_its_own_toplevel_proceeds_to_judgement(self, monkeypatch, repo_pair: Path):
        """陽性対照A: toplevel == 実行木なら発火せず従来判定（共有 checkout と同配置）。"""
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "safe" and result.branch == "main"

    def test_case_variant_path_is_same_tree(self, monkeypatch, repo_pair: Path):
        """実体の同一性で比較する（文字列一致だと大文字小文字違いで誤って別 repo 扱いになる）。"""
        variant = Path(str(repo_pair).swapcase())
        if not variant.exists() or str(variant.resolve()) == str(repo_pair.resolve()):
            pytest.skip("case-sensitive FS or resolve() が大文字小文字を正規化する環境")
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", variant)
        result = live_checkout.check(str(variant / "hooks" / "fake.py"))
        assert result.status == "safe", result.reason

    def test_non_git_tree_still_takes_head_unresolved_path(self, tmp_path: Path, monkeypatch):
        """新条件が既存の HEAD 解決不能経路を奪わない。"""
        tree = tmp_path / "plain"
        _write_plugin_marker(tree)
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", tree)
        result = live_checkout.check(str(tree / "x.py"))
        assert result.status == "unknown" and result.reason.startswith("HEAD 解決不能")


class TestRegistrySecondary:
    def test_registry_missing_is_skipped_not_fatal(self, monkeypatch, repo_pair: Path):
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        monkeypatch.setattr(live_checkout, "_MARKETPLACE_REGISTRY", repo_pair / "nope.json")
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "safe"  # registry 不在は primary 判定を妨げない
        assert result.registry.status == "skipped"

    def test_registry_corrupt_json_is_unreadable_not_fatal_to_primary(
        self, monkeypatch, repo_pair: Path,
    ):
        """判定不能ケース①: registry JSON 不正。primary（git 判定）は独立して完走する。"""
        bad = repo_pair / "bad_registry.json"
        bad.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        monkeypatch.setattr(live_checkout, "_MARKETPLACE_REGISTRY", bad)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.status == "safe"
        assert result.registry.status == "unreadable"
        assert result.registry.detail is not None

    def test_registry_matching_install_location_is_ok(self, monkeypatch, repo_pair: Path):
        reg = repo_pair / "registry.json"
        reg.write_text(
            json.dumps({"fixture-mp": {"installLocation": str(repo_pair)}}), encoding="utf-8",
        )
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        monkeypatch.setattr(live_checkout, "_MARKETPLACE_REGISTRY", reg)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.registry.status == "ok"

    def test_registry_mismatched_install_location_is_mismatch(self, monkeypatch, repo_pair: Path, tmp_path: Path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        reg = repo_pair / "registry.json"
        reg.write_text(
            json.dumps({"fixture-mp": {"installLocation": str(other)}}), encoding="utf-8",
        )
        monkeypatch.setattr(live_checkout, "_MODULE_ROOT_OVERRIDE", repo_pair)
        monkeypatch.setattr(live_checkout, "_MARKETPLACE_REGISTRY", reg)
        result = live_checkout.check(_caller_file(repo_pair))
        assert result.registry.status == "mismatch"
