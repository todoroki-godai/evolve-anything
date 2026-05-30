"""fleet plugins サブコマンドの freshness 検出テスト（決定論・LLM 非依存）。

`installed_plugins.json`（インストール中バージョンの正本）と各 marketplace の
`marketplace.json`（最新バージョン + source パス）、および cache のコンテンツを
突き合わせ、以下を検出する:

- ok           : 最新版と一致 + cache コンテンツも source と一致
- update       : marketplace に新しいバージョンがある（インストール版が古い）
- drift        : 同一バージョンだが cache コンテンツが source と乖離（要再インストール）
- unknown      : marketplace に該当が無く比較不能

version が semver 比較できない（'unknown' / git sha 等）プラグインは drift
（コンテンツ差分）にフォールバックする — これが skill-creator 実例のケース。
"""

import json
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import pytest  # noqa: E402

from fleet.plugin_freshness import (  # noqa: E402
    check_plugin_freshness,
    format_plugin_freshness_table,
)


def _write_marketplace(root: Path, mp_name: str, plugins: list[dict]) -> None:
    """marketplaces/<mp>/.claude-plugin/marketplace.json と source ツリーを作る。"""
    mp_dir = root / "marketplaces" / mp_name
    (mp_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (mp_dir / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": mp_name, "plugins": plugins}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_source(root: Path, mp_name: str, rel: str, files: dict[str, str]) -> None:
    base = root / "marketplaces" / mp_name / rel.lstrip("./")
    base.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        fpath = base / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")


def _write_cache(root: Path, mp_name: str, plug: str, version: str, files: dict[str, str]) -> Path:
    base = root / "cache" / mp_name / plug / version
    base.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        fpath = base / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    return base


def _write_installed(root: Path, entries: dict[str, dict]) -> None:
    plugins = {}
    for key, info in entries.items():
        entry = {
            "scope": "user",
            "installPath": str(info["installPath"]),
            "version": info["version"],
        }
        if info.get("gitCommitSha"):
            entry["gitCommitSha"] = info["gitCommitSha"]
        plugins[key] = [entry]
    (root / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": plugins}, ensure_ascii=False),
        encoding="utf-8",
    )


def _by_name(rows):
    return {r.name: r for r in rows}


def test_ok_when_version_and_content_match(tmp_path):
    root = tmp_path / "plugins"
    files = {"SKILL.md": "hello\n"}
    _write_marketplace(root, "mp1", [{"name": "good", "version": "1.0.0", "source": "./plugins/good"}])
    _write_source(root, "mp1", "./plugins/good", files)
    cache = _write_cache(root, "mp1", "good", "1.0.0", files)
    _write_installed(root, {"good@mp1": {"installPath": cache, "version": "1.0.0"}})

    rows = check_plugin_freshness(plugins_root=root)
    r = _by_name(rows)["good@mp1"]
    assert r.status == "ok"
    assert r.installed_version == "1.0.0"
    assert r.latest_version == "1.0.0"


def test_update_available_when_marketplace_has_newer(tmp_path):
    root = tmp_path / "plugins"
    _write_marketplace(root, "mp1", [{"name": "lib", "version": "1.2.1", "source": "./plugins/lib"}])
    _write_source(root, "mp1", "./plugins/lib", {"SKILL.md": "v2\n"})
    cache = _write_cache(root, "mp1", "lib", "1.2.0", {"SKILL.md": "v1\n"})
    _write_installed(root, {"lib@mp1": {"installPath": cache, "version": "1.2.0"}})

    rows = check_plugin_freshness(plugins_root=root)
    r = _by_name(rows)["lib@mp1"]
    assert r.status == "update"
    assert r.installed_version == "1.2.0"
    assert r.latest_version == "1.2.1"


def test_drift_when_unknown_version_content_differs(tmp_path):
    """skill-creator 実例: version='unknown' で semver 比較不能、コンテンツ差分で drift。"""
    root = tmp_path / "plugins"
    _write_marketplace(root, "mp1", [{"name": "sc", "source": "./plugins/sc"}])  # version 無し
    _write_source(root, "mp1", "./plugins/sc", {"SKILL.md": "NEW source\n"})
    cache = _write_cache(root, "mp1", "sc", "unknown", {"SKILL.md": "OLD cache\n"})
    _write_installed(root, {"sc@mp1": {"installPath": cache, "version": "unknown"}})

    rows = check_plugin_freshness(plugins_root=root)
    r = _by_name(rows)["sc@mp1"]
    assert r.status == "drift"


def test_ok_when_unknown_version_content_matches(tmp_path):
    root = tmp_path / "plugins"
    files = {"SKILL.md": "same\n", "scripts/x.py": "print(1)\n"}
    _write_marketplace(root, "mp1", [{"name": "sc", "source": "./plugins/sc"}])
    _write_source(root, "mp1", "./plugins/sc", files)
    cache = _write_cache(root, "mp1", "sc", "unknown", files)
    # cache に .in_use マーカーがあっても無視される
    (cache / ".in_use").write_text("", encoding="utf-8")
    _write_installed(root, {"sc@mp1": {"installPath": cache, "version": "unknown"}})

    rows = check_plugin_freshness(plugins_root=root)
    r = _by_name(rows)["sc@mp1"]
    assert r.status == "ok"


def test_unknown_when_no_marketplace_match(tmp_path):
    """Directory marketplace 等で source が見つからない場合は unknown。"""
    root = tmp_path / "plugins"
    _write_marketplace(root, "mp1", [{"name": "other", "version": "1.0.0", "source": "./plugins/other"}])
    cache = _write_cache(root, "mp1", "ghost", "9.9.9", {"SKILL.md": "x\n"})
    _write_installed(root, {"ghost@mp1": {"installPath": cache, "version": "9.9.9"}})

    rows = check_plugin_freshness(plugins_root=root)
    r = _by_name(rows)["ghost@mp1"]
    assert r.status == "unknown"


def test_unknown_when_external_source_and_no_version(tmp_path):
    """coderabbit 実例: source が外部 git URL（dict）で version 無し → 検証不能で unknown（ok 誤認しない）。"""
    root = tmp_path / "plugins"
    _write_marketplace(root, "mp1", [{
        "name": "ext",
        # version 無し + source は dict（ローカルディレクトリに解決できない）
        "source": {"source": "url", "url": "https://example.com/x.git"},
    }])
    cache = _write_cache(root, "mp1", "ext", "abcdef", {"SKILL.md": "x\n"})
    _write_installed(root, {"ext@mp1": {"installPath": cache, "version": "abcdef"}})

    rows = check_plugin_freshness(plugins_root=root)
    r = _by_name(rows)["ext@mp1"]
    assert r.status == "unknown"
    assert "検証できず" in r.detail


def test_pycache_ignored_in_drift(tmp_path):
    """__pycache__ の差分は drift 判定に影響しない。"""
    root = tmp_path / "plugins"
    _write_marketplace(root, "mp1", [{"name": "p", "source": "./plugins/p"}])
    _write_source(root, "mp1", "./plugins/p", {"SKILL.md": "same\n"})
    cache = _write_cache(root, "mp1", "p", "unknown", {"SKILL.md": "same\n"})
    (cache / "__pycache__").mkdir()
    (cache / "__pycache__" / "x.pyc").write_text("garbage", encoding="utf-8")
    _write_installed(root, {"p@mp1": {"installPath": cache, "version": "unknown"}})

    rows = check_plugin_freshness(plugins_root=root)
    assert _by_name(rows)["p@mp1"].status == "ok"


def test_format_table_json(tmp_path):
    root = tmp_path / "plugins"
    files = {"SKILL.md": "x\n"}
    _write_marketplace(root, "mp1", [{"name": "good", "version": "1.0.0", "source": "./plugins/good"}])
    _write_source(root, "mp1", "./plugins/good", files)
    cache = _write_cache(root, "mp1", "good", "1.0.0", files)
    _write_installed(root, {"good@mp1": {"installPath": cache, "version": "1.0.0"}})

    rows = check_plugin_freshness(plugins_root=root)
    out = format_plugin_freshness_table(rows, as_json=True)
    data = json.loads(out)
    assert data[0]["name"] == "good@mp1"
    assert data[0]["status"] == "ok"


def test_format_table_text_has_header_and_legend(tmp_path):
    root = tmp_path / "plugins"
    _write_marketplace(root, "mp1", [{"name": "lib", "version": "2.0.0", "source": "./plugins/lib"}])
    _write_source(root, "mp1", "./plugins/lib", {"SKILL.md": "new\n"})
    cache = _write_cache(root, "mp1", "lib", "1.0.0", {"SKILL.md": "old\n"})
    _write_installed(root, {"lib@mp1": {"installPath": cache, "version": "1.0.0"}})

    rows = check_plugin_freshness(plugins_root=root)
    out = format_plugin_freshness_table(rows, as_json=False)
    assert "lib@mp1" in out
    assert "update" in out


def test_git_sha_match_ok(tmp_path, monkeypatch):
    import fleet.plugin_freshness as pf

    full_sha = "569c8b6f0b310094d89d0e830fc0bedc9f2fbf23"
    monkeypatch.setattr(pf, "_git_head_sha", lambda _d: full_sha)

    root = tmp_path / "plugins"
    _write_marketplace(root, "git-mp", [{"name": "g", "source": "./"}])
    cache = _write_cache(root, "git-mp", "g", "569c8b6f0b31", {"SKILL.md": "cache only\n"})
    _write_source(root, "git-mp", "./", {"README.md": "repo root only\n"})
    _write_installed(root, {"g@git-mp": {
        "installPath": cache, "version": "569c8b6f0b31", "gitCommitSha": full_sha,
    }})

    r = _by_name(check_plugin_freshness(plugins_root=root))["g@git-mp"]
    # repo-root と cache は内容が違うが sha 一致なので drift にならない（FP 回避）
    assert r.status == "ok"


def test_git_sha_mismatch_update(tmp_path, monkeypatch):
    import fleet.plugin_freshness as pf

    monkeypatch.setattr(pf, "_git_head_sha", lambda _d: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

    root = tmp_path / "plugins"
    _write_marketplace(root, "git-mp", [{"name": "g", "source": "./"}])
    cache = _write_cache(root, "git-mp", "g", "569c8b6f0b31", {"SKILL.md": "x\n"})
    _write_source(root, "git-mp", "./", {"SKILL.md": "x\n"})
    _write_installed(root, {"g@git-mp": {
        "installPath": cache, "version": "569c8b6f0b31",
        "gitCommitSha": "569c8b6f0b310094d89d0e830fc0bedc9f2fbf23",
    }})

    r = _by_name(check_plugin_freshness(plugins_root=root))["g@git-mp"]
    assert r.status == "update"


def test_no_git_head_falls_back_to_content_diff(tmp_path, monkeypatch):
    """.git 無し（head_sha None）なら従来どおり content-diff にフォールバック。"""
    import fleet.plugin_freshness as pf

    monkeypatch.setattr(pf, "_git_head_sha", lambda _d: None)

    root = tmp_path / "plugins"
    _write_marketplace(root, "mp1", [{"name": "g", "source": "./plugins/g"}])
    _write_source(root, "mp1", "./plugins/g", {"SKILL.md": "new\n"})
    cache = _write_cache(root, "mp1", "g", "abcdef", {"SKILL.md": "old\n"})
    _write_installed(root, {"g@mp1": {
        "installPath": cache, "version": "abcdef", "gitCommitSha": "abcdef0000",
    }})

    r = _by_name(check_plugin_freshness(plugins_root=root))["g@mp1"]
    assert r.status == "drift"


def test_missing_installed_file_returns_empty(tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    rows = check_plugin_freshness(plugins_root=root)
    assert rows == []
