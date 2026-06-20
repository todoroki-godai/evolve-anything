"""write barrier（store_write 単一書込ゲート）のテスト（ADR-049 / #55 Phase 2a）。

決定論・LLM 非依存。store_write が:
  - active 登録ストアを canonical DATA_DIR 配下に atomic append する
  - 未登録 / 非 active ストアを runtime guard で弾く（warn-only / reject の2モード）
  - 場所を呼び出し側に一切決めさせない（store_name → DATA_DIR/name 解決は内部のみ）
  - 例外口は別名関数 store_write_raw（フラグでない・ADR-049 決定5）
を検証する。

Phase 2a は **挙動不変の土台**: store_write/store_write_raw を新設するが既存 14 caller
（append_jsonl 直呼び）は未移行。本テストは新 API 単体の契約を固める。caller 移行（2b）は
write-path-set keyset snapshot の不変で守る。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import rl_common  # noqa: E402
import store_registry  # noqa: E402
from rl_common import store_write, store_write_raw  # noqa: E402
from rl_common.store_write import StoreWriteError  # noqa: E402


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """rl_common.DATA_DIR を tmp に向ける（store_write の canonical 解決先）。"""
    d = tmp_path / "evolve-anything"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rl_common, "DATA_DIR", d)
    # env 由来の既定 guard モードがテスト環境に漏れないよう除去（既定 warn を確定）。
    monkeypatch.delenv("EVOLVE_WRITE_GUARD", raising=False)
    return d


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln]


# --- store_write: active ストアへの正常書込 ----------------------------------

def test_store_write_appends_active_store_under_datadir(data_dir):
    """active 登録ストアは DATA_DIR/<name> に追記される（場所は内部解決）。"""
    store_write("corrections.jsonl", {"a": 1})
    store_write("corrections.jsonl", {"a": 2})
    recs = _read_lines(data_dir / "corrections.jsonl")
    assert recs == [{"a": 1}, {"a": 2}]


def test_store_write_sets_600_perms_on_new_file(data_dir):
    """新規作成時 append_jsonl 経由で 600 パーミッションが付く（atomic primitive 継承）。"""
    store_write("usage.jsonl", {"x": 1})
    mode = (data_dir / "usage.jsonl").stat().st_mode & 0o777
    assert mode == 0o600


def test_store_write_caller_cannot_choose_location(data_dir):
    """store_write は basename のみ受け、別 dir への path traversal を canonical 配下に閉じる。

    "corrections.jsonl" は DATA_DIR 直下にしか出ない（呼び出し側が場所を決められない）。
    """
    store_write("corrections.jsonl", {"k": 1})
    assert (data_dir / "corrections.jsonl").exists()
    # DATA_DIR の外（親）には何も書かれない。
    assert not (data_dir.parent / "corrections.jsonl").exists()


# --- runtime guard: warn-only（Phase 2a 既定）--------------------------------

def test_warn_only_undeclared_store_still_writes(data_dir, capsys):
    """未登録ストアは warn-only 既定では警告を出すが書込は継続する（挙動不変）。"""
    store_write("totally_new_store.jsonl", {"v": 1})
    err = capsys.readouterr().err
    assert "write-barrier" in err
    assert "未登録" in err
    assert _read_lines(data_dir / "totally_new_store.jsonl") == [{"v": 1}]


def test_warn_only_non_active_store_still_writes(data_dir, capsys, monkeypatch):
    """legacy/dead ストアも warn-only では警告のみで書込継続（reject 昇格は移行後）。"""
    legacy = StoreDeclaration_legacy()
    monkeypatch.setattr(
        store_registry, "_DECLARATIONS", [legacy], raising=True
    )
    store_write("legacy_store.jsonl", {"v": 1})
    err = capsys.readouterr().err
    assert "非 active" in err
    assert _read_lines(data_dir / "legacy_store.jsonl") == [{"v": 1}]


def test_warn_only_active_store_is_silent(data_dir, capsys):
    """active ストアは警告を出さない（ノイズを出さない）。"""
    store_write("corrections.jsonl", {"v": 1})
    assert "write-barrier" not in capsys.readouterr().err


# --- runtime guard: reject モード --------------------------------------------

def test_reject_mode_undeclared_raises_and_does_not_write(data_dir):
    """reject モードでは未登録ストアは StoreWriteError を送出し書込しない。"""
    with pytest.raises(StoreWriteError, match="未登録"):
        store_write("phantom.jsonl", {"v": 1}, guard_mode="reject")
    assert not (data_dir / "phantom.jsonl").exists()


def test_reject_mode_non_active_raises(data_dir, monkeypatch):
    """reject モードでは非 active ストアも送出（write は active のみ許可）。"""
    monkeypatch.setattr(
        store_registry, "_DECLARATIONS", [StoreDeclaration_legacy()], raising=True
    )
    with pytest.raises(StoreWriteError, match="非 active"):
        store_write("legacy_store.jsonl", {"v": 1}, guard_mode="reject")
    assert not (data_dir / "legacy_store.jsonl").exists()


def test_reject_mode_active_store_writes_normally(data_dir):
    """reject モードでも active ストアは通常通り書込する。"""
    store_write("usage.jsonl", {"v": 1}, guard_mode="reject")
    assert _read_lines(data_dir / "usage.jsonl") == [{"v": 1}]


def test_guard_mode_from_env(data_dir, monkeypatch):
    """EVOLVE_WRITE_GUARD=reject が既定モードを上書きする。"""
    monkeypatch.setenv("EVOLVE_WRITE_GUARD", "reject")
    with pytest.raises(StoreWriteError):
        store_write("phantom.jsonl", {"v": 1})


def test_invalid_guard_mode_falls_back_to_warn(data_dir, capsys):
    """不正な guard_mode 値は warn にフォールバック（reject に化けて誤爆させない）。"""
    store_write("phantom.jsonl", {"v": 1}, guard_mode="bogus")
    assert _read_lines(data_dir / "phantom.jsonl") == [{"v": 1}]


# --- store_write_raw: 明示パスの例外口 ---------------------------------------

def test_store_write_raw_writes_explicit_path(tmp_path):
    """store_write_raw は明示パスにそのまま追記する（registry 照合なし）。"""
    target = tmp_path / "anywhere.jsonl"
    store_write_raw(target, {"r": 1})
    assert _read_lines(target) == [{"r": 1}]


def test_store_write_raw_does_not_consult_registry(tmp_path, monkeypatch):
    """store_write_raw は未登録名でも guard を発火しない（明示パス契約）。"""
    monkeypatch.setattr(store_registry, "_DECLARATIONS", [], raising=True)
    target = tmp_path / "undeclared.jsonl"
    store_write_raw(target, {"r": 1})  # 例外を出さない
    assert _read_lines(target) == [{"r": 1}]


# --- write-path-set keyset snapshot（ADR-049 安全網）-------------------------
#
# active ストアの集合 = store_write が canonical DATA_DIR 配下に書く対象の不変。
# 2b の caller 移行（append_jsonl 直呼び → store_write）でこの集合は不変であるべき。
# 集合が変わるのは #46（legacy へ status 変更）/ #54（dead 削除）/ 新ストア追加の
# 「意図した変更」のみ。意図せず変わったらこのテストが落ちる。
_EXPECTED_ACTIVE_STORES = [
    "bootstrap_done-<slug>.marker",
    "correction_idioms.jsonl",
    "correction_judged.jsonl",
    "correction_review_seen.jsonl",
    "corrections.jsonl",
    "errors.jsonl",
    "remediation_suppression/<slug>.jsonl",
    "remediation_surfaced/<slug>.json",
    "sessions.jsonl",
    "skill_activations.jsonl",
    "subagents.jsonl",
    "usage-registry.jsonl",
    "usage.jsonl",
    "utterances.db",
    "weak_signals.jsonl",
    "workflows.jsonl",
]


def test_active_store_path_set_snapshot():
    """active ストア集合の keyset snapshot（書込先パス集合の不変・ADR-049）。"""
    assert store_registry.active_store_names() == _EXPECTED_ACTIVE_STORES


def test_store_write_resolves_every_active_store_under_canonical(data_dir):
    """各 active ストアの store_write 解決先は DATA_DIR/<name> に一致する（場所不変）。

    テンプレ名（<slug> 含む）/ db は flat append 対象外なので jsonl basename のみ検証する。
    """
    for name in store_registry.active_store_names():
        if "<" in name or not name.endswith(".jsonl"):
            continue
        store_write(name, {"probe": name})
        assert (data_dir / name).exists()
        # 解決先は常に canonical 直下（別 dir に漏れない）。
        assert json.loads((data_dir / name).read_text().splitlines()[0]) == {"probe": name}


def StoreDeclaration_legacy() -> "store_registry.StoreDeclaration":
    """status=legacy のダミー宣言（guard テスト用ヘルパ）。"""
    return store_registry.StoreDeclaration(
        name="legacy_store.jsonl",
        writer="（テスト用ダミー）",
        reader="（テスト用ダミー）",
        retention="permanent",
        status="legacy",
    )
