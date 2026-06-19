"""Layer 1 隔離コピー方式のユニットテスト（#496）。

テスト対象:
  - copy_data_dir_to_tmp: DATA_DIR を tmp にコピーしコピー先パスを返す
  - CACHE_EXCLUDE_NAMES: cache 除外リストが正しいファイルを含む
  - check_dry_run_invariance の隔離動作:
      - CLAUDE_PLUGIN_DATA がコピー先に設定されて evolve subprocess に渡る
      - snapshot 比較がコピー先（isolated dir）で行われる（実 DATA_DIR は比較対象外）
      - cache 除外ファイルはコピー先で変更されても diff に含まれない
  - 実 DATA_DIR はゲート実行中に変更されても結果に影響しない（ambient write 隔離）
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pytest

_lib_dir = Path(__file__).resolve().parent.parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from test_home_isolation import isolate_home  # noqa: E402

from dogfood import layer1  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    isolate_home(monkeypatch, tmp_path / "_home")


# ─────────────────────────────────────────────────────────────
# copy_data_dir_to_tmp
# ─────────────────────────────────────────────────────────────

def test_copy_data_dir_copies_files(tmp_path):
    """copy_data_dir_to_tmp がファイルをコピーすることを確認する。"""
    src = tmp_path / "data"
    src.mkdir()
    (src / "state.json").write_text('{"x": 1}', encoding="utf-8")
    (src / "sub").mkdir()
    (src / "sub" / "nested.jsonl").write_text("line\n", encoding="utf-8")

    dest = layer1.copy_data_dir_to_tmp(src, tmp_path / "isolated")
    assert (dest / "state.json").read_text(encoding="utf-8") == '{"x": 1}'
    assert (dest / "sub" / "nested.jsonl").read_text(encoding="utf-8") == "line\n"


def test_copy_data_dir_returns_dest_path(tmp_path):
    """copy_data_dir_to_tmp がコピー先の Path を返すことを確認する。"""
    src = tmp_path / "data"
    src.mkdir()
    dest_base = tmp_path / "isolated"
    result = layer1.copy_data_dir_to_tmp(src, dest_base)
    assert isinstance(result, Path)
    assert result.exists()
    assert result.is_dir()


def test_copy_data_dir_when_src_missing(tmp_path):
    """src が存在しない場合はコピー先を空 dir として作成する。"""
    src = tmp_path / "nonexistent"
    dest = layer1.copy_data_dir_to_tmp(src, tmp_path / "isolated")
    assert dest.is_dir()
    assert list(dest.iterdir()) == []


# ─────────────────────────────────────────────────────────────
# CACHE_EXCLUDE_NAMES
# ─────────────────────────────────────────────────────────────

def test_cache_exclude_names_contains_skill_evolve_cache():
    """skill-evolve-cache.json が除外リストに含まれる。

    evolve-ops の cache warm 設計で意図された dry-run 書込のため除外。
    """
    assert "skill-evolve-cache.json" in layer1.CACHE_EXCLUDE_NAMES


def test_cache_exclude_names_contains_constitutional_cache():
    """constitutional_cache.json が除外リストに含まれる。

    LLM 再呼び出し回避キャッシュとして意図された dry-run 書込のため除外。
    """
    assert "constitutional_cache.json" in layer1.CACHE_EXCLUDE_NAMES


def test_cache_exclude_names_is_frozenset():
    """除外リストは frozenset（不変）である。"""
    assert isinstance(layer1.CACHE_EXCLUDE_NAMES, frozenset)


# ─────────────────────────────────────────────────────────────
# CACHE_EXCLUDE_PATH_PREFIXES — ディレクトリ prefix 単位の除外（#513）
# ─────────────────────────────────────────────────────────────

def test_cache_exclude_path_prefixes_contains_evolve_pending():
    """evolve_pending/ が path prefix 除外に含まれる（#402/ADR-041 の意図された書込）。"""
    assert "evolve_pending/" in layer1.CACHE_EXCLUDE_PATH_PREFIXES


def test_cache_exclude_path_prefixes_is_frozenset():
    """path prefix 除外リストは frozenset（不変）である。"""
    assert isinstance(layer1.CACHE_EXCLUDE_PATH_PREFIXES, frozenset)


def test_invariance_evolve_pending_dir_excluded(monkeypatch, tmp_path):
    """evolve_pending/ 配下への書込は diff に含まれない（意図された運用ポインタ）。"""
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "state.json").write_text("{}", encoding="utf-8")

    def fake_run_pending_write(repo_root, output_path, env=None):
        if env and "CLAUDE_PLUGIN_DATA" in env:
            data_path = Path(env["CLAUDE_PLUGIN_DATA"])
            pending = data_path / "evolve_pending"
            pending.mkdir(exist_ok=True)
            (pending / "evolve-anything.json").write_text('{"pending": true}', encoding="utf-8")
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_pending_write)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo", data_dir=real_data, out_dir=tmp_path / "out"
    )
    assert res["status"] == "pass", res
    all_changed = (
        res["diff"].get("added", [])
        + res["diff"].get("modified", [])
        + res["diff"].get("removed", [])
    )
    assert not any(p.startswith("evolve_pending/") for p in all_changed)


def test_invariance_non_pending_dir_writes_still_detected(monkeypatch, tmp_path):
    """evolve_pending/ 以外のディレクトリ書込は依然 fail として検出される。"""
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "state.json").write_text("{}", encoding="utf-8")

    def fake_run_other_dir(repo_root, output_path, env=None):
        if env and "CLAUDE_PLUGIN_DATA" in env:
            data_path = Path(env["CLAUDE_PLUGIN_DATA"])
            other = data_path / "some_other_dir"
            other.mkdir(exist_ok=True)
            (other / "marker.json").write_text('{"bug": true}', encoding="utf-8")
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_other_dir)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo", data_dir=real_data, out_dir=tmp_path / "out"
    )
    assert res["status"] == "fail", res
    assert "some_other_dir/marker.json" in res["diff"]["added"]


# ─────────────────────────────────────────────────────────────
# CACHE_EXCLUDE_JSON_KEYS — 共有 state ファイル内のキャッシュキー除外
# ─────────────────────────────────────────────────────────────

def test_cache_exclude_json_keys_contains_skill_type_cache():
    """evolve-state.json 内の skill_type_cache が JSON キー除外に含まれる。

    skill_type_cache は prune の参照型判定 LLM 推定結果のキャッシュで、
    skill-evolve-cache.json / constitutional_cache.json と同カテゴリの
    意図された dry-run 書込。ただし evolve-state.json という実 state も持つ
    共有ファイルに同居するため、ファイル単位でなく JSON キー単位で除外する。
    """
    assert "evolve-state.json" in layer1.CACHE_EXCLUDE_JSON_KEYS
    assert "skill_type_cache" in layer1.CACHE_EXCLUDE_JSON_KEYS["evolve-state.json"]


def test_invariance_skill_type_cache_key_excluded(monkeypatch, tmp_path):
    """evolve-state.json の skill_type_cache キーだけの変更は diff に含まれない。"""
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "evolve-state.json").write_text(
        '{"last_run_timestamp": "t0", "skill_type_cache": {}}', encoding="utf-8"
    )

    def fake_run_cache_only(repo_root, output_path, env=None):
        if env and "CLAUDE_PLUGIN_DATA" in env:
            sf = Path(env["CLAUDE_PLUGIN_DATA"]) / "evolve-state.json"
            # skill_type_cache だけを更新（意図された cache warm）
            sf.write_text(
                '{"last_run_timestamp": "t0", "skill_type_cache": {"a/SKILL.md": {"type": "action", "mtime": 1.0}}}',
                encoding="utf-8",
            )
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_cache_only)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo", data_dir=real_data, out_dir=tmp_path / "out"
    )
    assert res["status"] == "pass", res


def test_invariance_real_state_change_still_detected(monkeypatch, tmp_path):
    """evolve-state.json の cache キー以外の変更は依然 fail として検出される。

    JSON キー除外が共有ファイルの実 state 書込バグを隠さないことを確認する。
    """
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "evolve-state.json").write_text(
        '{"last_run_timestamp": "t0", "skill_type_cache": {}}', encoding="utf-8"
    )

    def fake_run_state_change(repo_root, output_path, env=None):
        if env and "CLAUDE_PLUGIN_DATA" in env:
            sf = Path(env["CLAUDE_PLUGIN_DATA"]) / "evolve-state.json"
            # 実 state（last_run_timestamp）を dry-run で書き換えるバグ
            sf.write_text(
                '{"last_run_timestamp": "MUTATED", "skill_type_cache": {}}',
                encoding="utf-8",
            )
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_state_change)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo", data_dir=real_data, out_dir=tmp_path / "out"
    )
    assert res["status"] == "fail", res
    assert "evolve-state.json" in res["diff"]["modified"]


# ─────────────────────────────────────────────────────────────
# check_dry_run_invariance — 隔離コピー動作
# ─────────────────────────────────────────────────────────────

def test_invariance_passes_claude_plugin_data_to_subprocess(monkeypatch, tmp_path):
    """CLAUDE_PLUGIN_DATA がコピー先ディレクトリを指す env で subprocess を起動する。"""
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "state.json").write_text("{}", encoding="utf-8")

    received_envs = []

    def fake_run(repo_root, output_path, env=None):
        received_envs.append(dict(env) if env else {})
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run)
    layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
    )
    assert len(received_envs) == 1
    env = received_envs[0]
    # CLAUDE_PLUGIN_DATA はコピー先（real_data とは異なるパス）を指す
    assert "CLAUDE_PLUGIN_DATA" in env
    isolated_data_path = Path(env["CLAUDE_PLUGIN_DATA"])
    assert isolated_data_path != real_data
    assert isolated_data_path.is_dir()


def test_invariance_snapshot_uses_isolated_copy_not_real_data(monkeypatch, tmp_path):
    """ambient write が real DATA_DIR を変更しても結果が pass になる。

    実 DATA_DIR への hook 書込（ambient write）はゲートの偽赤の原因。
    コピー側だけで比較するため real_data への書込は検出されない。
    """
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "state.json").write_text("{}", encoding="utf-8")

    def fake_run_with_ambient_write(repo_root, output_path, env=None):
        # ambient write: 実 DATA_DIR に evolve-state.json が書かれる（hook 相当）
        (real_data / "evolve-state.json").write_text('{"written_by_hook": true}', encoding="utf-8")
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_with_ambient_write)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
    )
    # 実 DATA_DIR への ambient write は検出されず pass
    assert res["status"] == "pass", res


def test_invariance_detects_writes_in_isolated_copy(monkeypatch, tmp_path):
    """dry-run バグによるコピー先への書込は fail として検出される。

    subprocess に渡した env から CLAUDE_PLUGIN_DATA を取得し、
    そこへの書込を dry-run 違反として検出する。
    """
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "state.json").write_text("{}", encoding="utf-8")

    def fake_run_with_bug_write(repo_root, output_path, env=None):
        # dry-run バグ: コピー先（CLAUDE_PLUGIN_DATA）にファイルを書く
        if env and "CLAUDE_PLUGIN_DATA" in env:
            bug_file = Path(env["CLAUDE_PLUGIN_DATA"]) / "bug-marker.json"
            bug_file.write_text('{"bug": true}', encoding="utf-8")
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_with_bug_write)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
    )
    # コピー先への書込は fail として検出される
    assert res["status"] == "fail", res
    assert "bug-marker.json" in res["diff"]["added"]


def test_invariance_cache_files_excluded_from_diff(monkeypatch, tmp_path):
    """skill-evolve-cache.json / constitutional_cache.json の変更は diff に含まれない。

    これらは evolve-ops の cache warm 設計で意図された dry-run 書込のため除外。
    """
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "state.json").write_text("{}", encoding="utf-8")

    def fake_run_with_cache_write(repo_root, output_path, env=None):
        # cache ファイルを書く（意図された書込）
        if env and "CLAUDE_PLUGIN_DATA" in env:
            data_path = Path(env["CLAUDE_PLUGIN_DATA"])
            (data_path / "skill-evolve-cache.json").write_text('{"cached": 1}', encoding="utf-8")
            (data_path / "constitutional_cache.json").write_text('{"cached": 2}', encoding="utf-8")
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_with_cache_write)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
    )
    # cache ファイルの変更は除外 → pass
    assert res["status"] == "pass", res
    # diff にも含まれない
    all_changed = (
        res["diff"].get("added", [])
        + res["diff"].get("modified", [])
        + res["diff"].get("removed", [])
    )
    assert "skill-evolve-cache.json" not in all_changed
    assert "constitutional_cache.json" not in all_changed


def test_invariance_non_cache_writes_still_detected(monkeypatch, tmp_path):
    """cache 除外以外のファイル変更は依然として fail として検出される。

    除外リストが他の dry-run バグを隠さないことを確認する。
    """
    real_data = tmp_path / "real_data"
    real_data.mkdir()
    (real_data / "state.json").write_text("{}", encoding="utf-8")

    def fake_run_mixed(repo_root, output_path, env=None):
        if env and "CLAUDE_PLUGIN_DATA" in env:
            data_path = Path(env["CLAUDE_PLUGIN_DATA"])
            # cache ファイル（除外対象）
            (data_path / "skill-evolve-cache.json").write_text('{}', encoding="utf-8")
            # 非 cache ファイル（検出対象）
            (data_path / "evolve-state.json").write_text('{"bug": true}', encoding="utf-8")
        output_path.write_text('{"phases":{}}', encoding="utf-8")
        return {"returncode": 0, "stderr": ""}

    monkeypatch.setattr(layer1, "_run_evolve_dry_run", fake_run_mixed)
    res = layer1.check_dry_run_invariance(
        repo_root=tmp_path / "repo",
        data_dir=real_data,
        out_dir=tmp_path / "out",
    )
    assert res["status"] == "fail", res
    all_changed = (
        res["diff"].get("added", [])
        + res["diff"].get("modified", [])
        + res["diff"].get("removed", [])
    )
    assert "evolve-state.json" in all_changed
    # cache ファイルは含まれない
    assert "skill-evolve-cache.json" not in all_changed
