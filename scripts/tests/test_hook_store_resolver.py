#!/usr/bin/env python3
"""hook-writer 系ストア（usage / skill_activations 等）の dir 解決テスト（#358）。

hook（PostToolUse 等）は CC が設定する CLAUDE_PLUGIN_DATA 配下（plugin-data dir）に
書き込むが、standalone tool/skill 実行時は env 未設定で DATA_DIR が既定 fallback
(~/.claude/rl-anything) に解決され、reader が live テレメトリを取り逃す。
prune の zero_invocation 誤判定（#358）の根因。

resolver は:
  1. CLAUDE_PLUGIN_DATA env があればそこ（hook 実行コンテキスト）
  2. base が既定 fallback 以外に明示設定されていればそれを尊重（custom / テスト patch）
  3. base が既定 fallback のとき install レイアウト
     ~/.claude/plugins/data/<*rl-anything*> を探索（tool/skill 実行コンテキスト）
  4. 探索失敗時は base（後方互換・graceful）
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import rl_common  # noqa: E402
from rl_common.store_paths import _REAL_DEFAULT_FALLBACK  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)


def test_env_set_wins(monkeypatch, tmp_path):
    """base が既定 fallback のとき CLAUDE_PLUGIN_DATA を返す（hook 実行時）。

    base を明示しないと rl_common.DATA_DIR（import 時凍結）の値に依存し full-suite
    の実行順で揺れるため、base=既定 fallback を明示して env 分岐を決定的に通す。
    """
    plugin_dir = tmp_path / "explicit_plugin_data"
    plugin_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(plugin_dir))
    assert rl_common.hook_store_dir(base=_REAL_DEFAULT_FALLBACK) == plugin_dir


def test_probe_finds_install_layout(monkeypatch, tmp_path):
    """base が既定 fallback のとき install レイアウトから plugin-data dir を特定する。"""
    base = tmp_path / "plugins_data"
    canonical = base / "rl-anything-rl-anything"
    canonical.mkdir(parents=True)
    monkeypatch.setattr(rl_common, "PLUGIN_DATA_BASE", base)
    # base=既定 fallback を明示的に渡し probe を有効化
    assert rl_common.hook_store_dir(base=_REAL_DEFAULT_FALLBACK) == canonical


def test_probe_picks_rl_anything_dir_only(monkeypatch, tmp_path):
    """plugin-data base に複数プラグイン dir があっても rl-anything のものを選ぶ。"""
    base = tmp_path / "plugins_data"
    (base / "other-marketplace-other-plugin").mkdir(parents=True)
    canonical = base / "mymarket-rl-anything"
    canonical.mkdir(parents=True)
    monkeypatch.setattr(rl_common, "PLUGIN_DATA_BASE", base)
    assert rl_common.hook_store_dir(base=_REAL_DEFAULT_FALLBACK) == canonical


def test_explicit_base_skips_probe(monkeypatch, tmp_path):
    """base が既定 fallback 以外なら probe せず base を尊重する（テスト patch 保護）。"""
    base = tmp_path / "plugins_data"
    (base / "rl-anything-rl-anything").mkdir(parents=True)
    monkeypatch.setattr(rl_common, "PLUGIN_DATA_BASE", base)
    explicit = tmp_path / "patched_data_dir"
    explicit.mkdir()
    # explicit != 既定 fallback → install レイアウトがあっても explicit を返す
    assert rl_common.hook_store_dir(base=explicit) == explicit


def test_fallback_when_no_plugin_data(monkeypatch, tmp_path):
    """install レイアウトが無ければ base（既定 fallback）を返す（後方互換）。"""
    base = tmp_path / "empty_plugins_data"  # 存在しない
    monkeypatch.setattr(rl_common, "PLUGIN_DATA_BASE", base)
    assert not base.exists()
    assert rl_common.hook_store_dir(base=_REAL_DEFAULT_FALLBACK) == _REAL_DEFAULT_FALLBACK


def test_default_base_is_rl_common_data_dir(monkeypatch, tmp_path):
    """base 省略時は rl_common.DATA_DIR を使う（patch 追従）。"""
    patched = tmp_path / "data_dir"
    patched.mkdir()
    monkeypatch.setattr(rl_common, "DATA_DIR", patched)
    # DATA_DIR は既定 fallback でないので probe されず patched を返す
    assert rl_common.hook_store_dir() == patched


def test_hook_store_path_joins_filename(monkeypatch, tmp_path):
    """hook_store_path は解決した dir に filename を結合する。"""
    base = tmp_path / "plugins_data"
    canonical = base / "rl-anything-rl-anything"
    canonical.mkdir(parents=True)
    monkeypatch.setattr(rl_common, "PLUGIN_DATA_BASE", base)
    got = rl_common.hook_store_path("usage.jsonl", base=_REAL_DEFAULT_FALLBACK)
    assert got == canonical / "usage.jsonl"


def test_env_empty_string_treated_as_unset(monkeypatch, tmp_path):
    """空文字の CLAUDE_PLUGIN_DATA は未設定扱い（Path('') 誤解決を防ぐ）。"""
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "")
    base = tmp_path / "plugins_data"
    canonical = base / "rl-anything-rl-anything"
    canonical.mkdir(parents=True)
    monkeypatch.setattr(rl_common, "PLUGIN_DATA_BASE", base)
    assert rl_common.hook_store_dir(base=_REAL_DEFAULT_FALLBACK) == canonical


def test_multiple_rl_anything_dirs_prefers_recent(monkeypatch, tmp_path):
    """rl-anything dir が複数あれば mtime が新しい方を決定論で選ぶ。"""
    import os
    base = tmp_path / "plugins_data"
    old = base / "a-rl-anything"
    new = base / "b-rl-anything"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    os.utime(old, (1_000_000, 1_000_000))  # old を過去 mtime に
    monkeypatch.setattr(rl_common, "PLUGIN_DATA_BASE", base)
    assert rl_common.hook_store_dir(base=_REAL_DEFAULT_FALLBACK) == new


# --- #358 リグレッション（統合）: tool 実行が hook の書いた plugin-data を読む ---

def test_358_load_usage_data_reads_plugin_data_via_probe(monkeypatch, tmp_path):
    """env 未設定の tool 実行でも hook が plugin-data に書いた usage を読む（#358）。

    bug: env 未設定 → DATA_DIR=fallback → reader が空の fallback を読み prune が
    全スキル zero_invocation 誤判定。fix: probe で plugin-data dir を回収。
    """
    import json
    from datetime import datetime, timezone

    import audit

    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    base = tmp_path / "plugins_data"
    canonical = base / "rl-anything-rl-anything"
    canonical.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    (canonical / "usage.jsonl").write_text(
        json.dumps({"skill_name": "live-skill", "project": None, "timestamp": now}) + "\n"
    )
    monkeypatch.setattr(rl_common, "PLUGIN_DATA_BASE", base)
    # audit.DATA_DIR を既定 fallback に固定 → 明示 base でないので probe が効く
    monkeypatch.setattr(audit, "DATA_DIR", _REAL_DEFAULT_FALLBACK)

    records = audit.load_usage_data()
    assert any(r.get("skill_name") == "live-skill" for r in records), records


def test_358_skill_activations_reads_plugin_data_via_probe(monkeypatch, tmp_path):
    """env 未設定の tool 実行でも hook が書いた skill_activations を読む（#358）。"""
    import json
    from datetime import datetime, timezone

    import skill_usage_stats as sus

    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    base = tmp_path / "plugins_data"
    canonical = base / "rl-anything-rl-anything"
    canonical.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    (canonical / "skill_activations.jsonl").write_text(
        json.dumps({"skill": "live-skill", "ts": now, "invocation_trigger": "top-level"}) + "\n"
    )
    monkeypatch.setattr(rl_common, "PLUGIN_DATA_BASE", base)
    monkeypatch.setattr(rl_common, "DATA_DIR", _REAL_DEFAULT_FALLBACK)

    stats = sus.load_skill_activations(days=30)
    assert "live-skill" in stats, stats
