"""write barrier（store_write 単一書込ゲート）のテスト（ADR-049 / #55 Phase 2a）。

決定論・LLM 非依存。store_write が:
  - active 登録ストアを canonical DATA_DIR 配下に atomic append する
  - 未登録 / 非 active ストアを runtime guard で弾く（warn-only / reject の2モード）
  - 場所を呼び出し側に一切決めさせない（store_name → DATA_DIR/name 解決は内部のみ）
  - 例外口は別名関数 store_write_raw（フラグでない・ADR-049 決定5）
を検証する。

Phase 2b 完了後の既定は **reject**: 全 caller（hooks 10 + scripts/lib 6）が store_write 経由
へ移行済み。本テストは新 API の契約（reject 既定・不正値の warn de-escalate・raw 例外口）と
write-path-set keyset snapshot の不変を固める。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import rl_common  # noqa: E402
import shrink_freeze  # noqa: E402
import store_registry  # noqa: E402
from rl_common import guard_problem, store_write, store_write_raw  # noqa: E402
from rl_common.store_write import StoreWriteError  # noqa: E402


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """rl_common.DATA_DIR を tmp に向ける（store_write の canonical 解決先）。"""
    d = tmp_path / "evolve-anything"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rl_common, "DATA_DIR", d)
    # env 由来の guard モード上書きがテスト環境に漏れないよう除去（コード既定 reject を確定）。
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


def test_store_write_creates_missing_canonical_data_dir(tmp_path, monkeypatch):
    """fresh install の未作成 DATA_DIR でも書込みを黙って失わない。"""
    missing = tmp_path / "nested" / "evolve-anything"
    monkeypatch.setattr(rl_common, "DATA_DIR", missing)

    store_write("usage.jsonl", {"x": 1})

    assert _read_lines(missing / "usage.jsonl") == [{"x": 1}]
    assert missing.stat().st_mode & 0o777 == 0o700


def test_store_write_caller_cannot_choose_location(data_dir):
    """store_write は basename のみ受け、別 dir への path traversal を canonical 配下に閉じる。

    "corrections.jsonl" は DATA_DIR 直下にしか出ない（呼び出し側が場所を決められない）。
    """
    store_write("corrections.jsonl", {"k": 1})
    assert (data_dir / "corrections.jsonl").exists()
    # DATA_DIR の外（親）には何も書かれない。
    assert not (data_dir.parent / "corrections.jsonl").exists()


# --- runtime guard: warn モード（明示 de-escalate / 移行期・緊急避難口）-------

def test_warn_mode_undeclared_store_still_writes(data_dir, capsys):
    """未登録ストアは guard_mode="warn" 明示なら警告を出すが書込は継続する（緊急避難）。"""
    store_write("totally_new_store.jsonl", {"v": 1}, guard_mode="warn")
    err = capsys.readouterr().err
    assert "write-barrier" in err
    assert "未登録" in err
    assert _read_lines(data_dir / "totally_new_store.jsonl") == [{"v": 1}]


def test_warn_mode_non_active_store_still_writes(data_dir, capsys, monkeypatch):
    """legacy/dead ストアも guard_mode="warn" 明示なら警告のみで書込継続（緊急避難）。"""
    legacy = StoreDeclaration_legacy()
    monkeypatch.setattr(
        store_registry, "_DECLARATIONS", [legacy], raising=True
    )
    store_write("legacy_store.jsonl", {"v": 1}, guard_mode="warn")
    err = capsys.readouterr().err
    assert "非 active" in err
    assert _read_lines(data_dir / "legacy_store.jsonl") == [{"v": 1}]


def test_active_store_write_is_silent(data_dir, capsys):
    """active ストアは（既定 reject でも）警告を出さない（ノイズを出さない）。"""
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


# --- runtime guard: kind=json ストアへの jsonl append 禁止（#399 codex round1 Should 1） -----
#
# kind=json は単一 JSON オブジェクトを丸ごと上書きするストア（evolve-state.json 等）。
# store_write は jsonl append 専用のため、kind=json ストアへ渡すと jsonl 行を追記して
# 単一 JSON オブジェクトを破壊しうる。runtime guard で reject する。

def test_reject_kind_json_store_raises_and_does_not_write(data_dir, monkeypatch):
    """kind=json ストアへの store_write は reject し書込しない（reject モード）。"""
    monkeypatch.setattr(
        store_registry, "_DECLARATIONS", [StoreDeclaration_json_kind()], raising=True
    )
    with pytest.raises(StoreWriteError, match="kind=json"):
        store_write("single_object.json", {"v": 1}, guard_mode="reject")
    assert not (data_dir / "single_object.json").exists()


def test_kind_json_guard_warns_but_still_writes_in_warn_mode(data_dir, monkeypatch):
    """warn モードでは kind=json でも警告のみで書込は継続する（既存 guard と同型の緊急避難）。"""
    monkeypatch.setattr(
        store_registry, "_DECLARATIONS", [StoreDeclaration_json_kind()], raising=True
    )
    store_write("single_object.json", {"v": 1}, guard_mode="warn")
    assert _read_lines(data_dir / "single_object.json") == [{"v": 1}]


def test_all_declared_json_named_stores_have_kind_json():
    """basename が `.json` の宣言は全て kind=json（remediation_surfaced/<slug>.json 含む）。

    #399 codex round1 Should 1: `.json`（単一オブジェクト）を jsonl 既定のまま宣言すると
    store_write 経由の append が破壊しうる、という指摘の再発防止。新規 `.json` 宣言を
    追加したら本テストが kind 指定漏れを検出する。
    """
    for d in store_registry.declarations():
        if d.name.endswith(".json"):
            assert d.kind == "json", f"{d.name}: kind=json の指定漏れ"


def test_default_guard_mode_is_reject(data_dir):
    """guard_mode 未指定・env 未設定なら既定は reject（ADR-049 ②・#55 capstone）。

    全 writer 移行（2b）完了に伴い既定を warn-only → reject へ昇格した。明示モードも env も
    無い素の呼び出しで、未登録ストアは StoreWriteError を送出し書込しないことを固定する。
    """
    with pytest.raises(StoreWriteError, match="未登録"):
        store_write("phantom_default.jsonl", {"v": 1})
    assert not (data_dir / "phantom_default.jsonl").exists()


def test_guard_mode_from_env(data_dir, monkeypatch):
    """EVOLVE_WRITE_GUARD=reject が既定モードを上書きする。"""
    monkeypatch.setenv("EVOLVE_WRITE_GUARD", "reject")
    with pytest.raises(StoreWriteError):
        store_write("phantom.jsonl", {"v": 1})


def test_env_warn_de_escalates_default_reject(data_dir, monkeypatch):
    """EVOLVE_WRITE_GUARD=warn は既定 reject を warn へ下げ書込継続（コード変更なしの緊急避難口）。"""
    monkeypatch.setenv("EVOLVE_WRITE_GUARD", "warn")
    store_write("phantom_env_warn.jsonl", {"v": 1})
    assert _read_lines(data_dir / "phantom_env_warn.jsonl") == [{"v": 1}]


def test_invalid_guard_mode_falls_back_to_warn(data_dir, capsys):
    """不正な guard_mode 値は warn へ de-escalate（typo を理由に既定 reject へ昇格させない）。"""
    store_write("phantom.jsonl", {"v": 1}, guard_mode="bogus")
    assert _read_lines(data_dir / "phantom.jsonl") == [{"v": 1}]


# --- store_write_raw: 明示パスの例外口 ---------------------------------------

def test_store_write_raw_writes_explicit_path(tmp_path_factory):
    """store_write_raw は明示パスにそのまま追記する（registry 照合なし）。

    ``_isolate_plugin_data``（root conftest 自動隔離）は per-test の ``tmp_path`` を
    rl_common.DATA_DIR にそのまま rebase するため、素の ``tmp_path`` は「DATA_DIR 配下」に
    なってしまい #379 凍結ゲートの対象になる（テスト対象の意図＝DATA_DIR 外の明示パス、
    とは無関係な confound）。``tmp_path_factory`` の別 mktemp で DATA_DIR と兄弟の
    独立ディレクトリを使い、意図通り「DATA_DIR 外」を表す。
    """
    target = tmp_path_factory.mktemp("explicit") / "anywhere.jsonl"
    store_write_raw(target, {"r": 1})
    assert _read_lines(target) == [{"r": 1}]


def test_store_write_raw_does_not_consult_registry(tmp_path_factory, monkeypatch):
    """store_write_raw は未登録名でも guard を発火しない（DATA_DIR 外の明示パス契約）。"""
    monkeypatch.setattr(store_registry, "_DECLARATIONS", [], raising=True)
    target = tmp_path_factory.mktemp("explicit") / "undeclared.jsonl"
    store_write_raw(target, {"r": 1})  # 例外を出さない
    assert _read_lines(target) == [{"r": 1}]


# --- store_write_raw: #379 Step 1 凍結ゲート（修正4） -------------------------
#
# store_registry 未登録 basename でも書ける raw 経路の穴（外部レビュー指摘）を、
# 書込み先が正準 DATA_DIR 配下のときに限って塞ぐ。DATA_DIR 外の明示パス（テスト
# isolation の tmp 等）は従来通り対象外＝上の2テストの契約は変えない。

def test_store_write_raw_rejects_unknown_basename_under_canonical_datadir_when_frozen(
    data_dir,
) -> None:
    """凍結中、正準 DATA_DIR 配下への未登録 basename 書込みは reject する。"""
    target = data_dir / "totally_new_store.jsonl"
    with pytest.raises(StoreWriteError, match="未登録"):
        store_write_raw(target, {"v": 1})
    assert not target.exists()


def test_store_write_raw_allows_known_basename_under_canonical_datadir(data_dir) -> None:
    """凍結中でも登録済み basename（FROZEN_STORES ∪ store_registry 宣言）は通常通り書ける。"""
    target = data_dir / "corrections.jsonl"
    store_write_raw(target, {"v": 1})
    assert _read_lines(target) == [{"v": 1}]


def test_store_write_raw_rejects_specialized_boundary_under_canonical_datadir(
    data_dir,
) -> None:
    target = data_dir / "reflect_apply_events.jsonl"
    with pytest.raises(StoreWriteError, match="専用の追記境界"):
        store_write_raw(target, {"correction_id": "a" * 32})
    assert not target.exists()


def test_store_write_raw_boundary_cannot_be_downgraded_to_warn(data_dir) -> None:
    target = data_dir / "reflect_apply_events.jsonl"
    with pytest.raises(StoreWriteError, match="専用の追記境界"):
        store_write_raw(target, {"correction_id": "a" * 32}, guard_mode="warn")
    assert not target.exists()


def test_store_write_raw_rejects_relative_specialized_boundary(
    data_dir, monkeypatch
) -> None:
    monkeypatch.chdir(data_dir)
    target = Path("reflect_apply_events.jsonl")
    with pytest.raises(StoreWriteError, match="専用の追記境界"):
        store_write_raw(target, {"correction_id": "a" * 32})
    assert not target.exists()


@pytest.mark.parametrize(
    ("frozen", "guard_mode"),
    [(True, "warn"), (False, None)],
    ids=["frozen-warn", "unfrozen-default-reject"],
)
def test_store_write_raw_rejects_missing_case_alias_of_specialized_boundary(
    data_dir, monkeypatch, frozen, guard_mode
) -> None:
    monkeypatch.setattr(shrink_freeze, "SHRINK_FREEZE_ACTIVE", frozen)
    declared = data_dir / "reflect_apply_events.jsonl"
    alias = data_dir / "Reflect_Apply_Events.jsonl"

    with pytest.raises(StoreWriteError, match="専用の追記境界"):
        store_write_raw(alias, {"correction_id": "a" * 32}, guard_mode=guard_mode)
    assert not declared.exists()
    assert not alias.exists()


@pytest.mark.parametrize(
    ("frozen", "guard_mode"),
    [(True, "warn"), (False, None)],
    ids=["frozen-warn", "unfrozen-default-reject"],
)
def test_store_write_raw_rejects_missing_dangling_alias_of_specialized_boundary(
    data_dir, monkeypatch, frozen, guard_mode
) -> None:
    monkeypatch.setattr(shrink_freeze, "SHRINK_FREEZE_ACTIVE", frozen)
    declared = data_dir / "reflect_apply_events.jsonl"
    alias = data_dir / "alias.jsonl"
    alias.symlink_to(declared.name)

    with pytest.raises(StoreWriteError, match="専用の追記境界"):
        store_write_raw(alias, {"correction_id": "a" * 32}, guard_mode=guard_mode)
    assert not declared.exists()
    assert alias.is_symlink()


@pytest.mark.parametrize(
    ("frozen", "guard_mode"),
    [(True, "warn"), (False, None)],
    ids=["frozen-warn", "unfrozen-default-reject"],
)
def test_store_write_raw_rejects_dangling_case_alias_of_specialized_boundary(
    data_dir, monkeypatch, frozen, guard_mode
) -> None:
    """symlink 解決後の未作成 basename も大小文字を正規化して専用境界と照合する。"""
    monkeypatch.setattr(shrink_freeze, "SHRINK_FREEZE_ACTIVE", frozen)
    declared = data_dir / "reflect_apply_events.jsonl"
    case_alias = data_dir / "Reflect_Apply_Events.jsonl"
    alias = data_dir / "alias.jsonl"
    alias.symlink_to(case_alias.name)

    with pytest.raises(StoreWriteError, match="専用の追記境界"):
        store_write_raw(alias, {"correction_id": "a" * 32}, guard_mode=guard_mode)
    assert not declared.exists()
    assert not case_alias.exists()
    assert alias.is_symlink()


def test_store_write_raw_keeps_explicit_path_exception_for_boundary_store(
    tmp_path_factory, data_dir
) -> None:
    target = tmp_path_factory.mktemp("explicit-boundary") / "reflect_apply_events.jsonl"
    store_write_raw(target, {"correction_id": "a" * 32})
    assert _read_lines(target) == [{"correction_id": "a" * 32}]


def test_store_write_raw_ignores_unknown_basename_outside_canonical_datadir(
    tmp_path_factory, data_dir
) -> None:
    """DATA_DIR 外の明示パスは凍結ゲートの対象外（store_write_raw 本来の「場所を尊重する」
    契約を維持する）。``data_dir`` フィクスチャで DATA_DIR を tmp_path/evolve-anything に
    固定した上で、それとは兄弟の独立ディレクトリへ書く（#420 の tmp_path rebase confound
    を避ける・上の test_store_write_raw_writes_explicit_path と同じ理由）。"""
    target = tmp_path_factory.mktemp("elsewhere") / "totally_new_store.jsonl"
    store_write_raw(target, {"v": 1})  # 例外を出さない
    assert _read_lines(target) == [{"v": 1}]


def test_store_write_raw_allows_unknown_basename_when_unfrozen(
    data_dir, monkeypatch
) -> None:
    """凍結解除中（SHRINK_FREEZE_ACTIVE=False）は正準 DATA_DIR 配下でも未登録 basename を通す。"""
    monkeypatch.setattr(shrink_freeze, "SHRINK_FREEZE_ACTIVE", False)
    target = data_dir / "totally_new_store.jsonl"
    store_write_raw(target, {"v": 1})
    assert _read_lines(target) == [{"v": 1}]


def test_store_write_raw_warn_mode_downgrades_reject(data_dir, capsys) -> None:
    """guard_mode="warn" 明示（緊急避難口）は reject を warn に降格し書込を継続する。"""
    target = data_dir / "totally_new_store.jsonl"
    store_write_raw(target, {"v": 1}, guard_mode="warn")
    err = capsys.readouterr().err
    assert "write-barrier" in err
    assert "未登録" in err
    assert _read_lines(target) == [{"v": 1}]


def test_store_write_raw_env_warn_downgrades_reject(data_dir, monkeypatch, capsys) -> None:
    """EVOLVE_WRITE_GUARD=warn（env）でも同様に降格する（既存 store_write の緊急避難と同一 env）。"""
    monkeypatch.setenv("EVOLVE_WRITE_GUARD", "warn")
    target = data_dir / "totally_new_store.jsonl"
    store_write_raw(target, {"v": 1})
    assert _read_lines(target) == [{"v": 1}]


def test_store_write_raw_fail_open_when_store_registry_unavailable(
    data_dir, monkeypatch
) -> None:
    """store_registry が import 不能な環境では凍結ゲート自体を fail-open する
    （既存 write barrier の `_guard_problem` と同じ fail-open 流儀）。"""
    monkeypatch.setitem(sys.modules, "store_registry", None)
    target = data_dir / "totally_new_store.jsonl"
    store_write_raw(target, {"v": 1})  # 例外を出さない
    assert _read_lines(target) == [{"v": 1}]


# --- write-path-set keyset snapshot（ADR-049 安全網）-------------------------
#
# active ストアの集合 = store_write が canonical DATA_DIR 配下に書く対象の不変。
# 2b の caller 移行（append_jsonl 直呼び → store_write）でこの集合は不変であるべき。
# 集合が変わるのは #46（legacy へ status 変更）/ #54（dead 削除）/ 新ストア追加の
# 「意図した変更」のみ。意図せず変わったらこのテストが落ちる。
_EXPECTED_ACTIVE_STORES = [
    "advisory_decisions.jsonl",
    "agent-brushup-state.json",
    "bootstrap_done-<slug>.marker",
    "correction_idioms.jsonl",
    "correction_judged.jsonl",
    "correction_review_seen.jsonl",
    "corrections.jsonl",
    "errors.jsonl",
    "evolve-proposals-<date>.json",
    "evolve-queue-state.jsonl",
    "evolve-queue.json",
    "evolve-state.json",
    "false_positives.jsonl",
    "fleet-config.json",
    "icebox-status.json",
    "icebox-verdicts.json",
    "icebox_verdict_seen.jsonl",
    "memory_transition_checks.jsonl",
    # #475 §12 決定4: 未登録だった live store の宣言バックフィル。
    "optimize_history/<slug>.jsonl",
    "pj_slug_cache.json",
    "reflect_apply_events.jsonl",
    "remediation-outcomes.jsonl",
    "remediation_suppression/<slug>.jsonl",
    "remediation_surfaced/<slug>.json",
    "reward_ema.jsonl",
    "sessions.jsonl",
    "skill-evolve-cache.json",
    "skill-evolve-denylist.json",
    "skill_activations.jsonl",
    "subagent_traces.jsonl",
    "subagents.jsonl",
    "usage-registry.jsonl",
    "usage.jsonl",
    "utterances.db",
    "verbosity_candidates.jsonl",
    "verbosity_verdicts.jsonl",
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
        if getattr(store_registry.declaration_for(name), "write_boundary", None):
            continue
        store_write(name, {"probe": name})
        assert (data_dir / name).exists()
        # 解決先は常に canonical 直下（別 dir に漏れない）。
        assert json.loads((data_dir / name).read_text().splitlines()[0]) == {"probe": name}


def test_generic_store_write_rejects_specialized_boundary(data_dir):
    with pytest.raises(StoreWriteError, match="専用の追記境界"):
        store_write("reflect_apply_events.jsonl", {"probe": 1})
    assert not (data_dir / "reflect_apply_events.jsonl").exists()


# --- Phase 2b wave 3: scripts/lib caller の store_write 経由ルーティング -------
#
# 既存の behavioral テストは isolation 用に明示 path= を渡すため store_write_raw 分岐を
# 踏む。本群は **production の path 無し（gate）分岐** が store_write へ正しく流れることを
# 構造的に固定する（store 名の取り違え regression を検出）。store_write を mock するため
# 実書込は起きない。

def test_false_positive_routes_through_store_write(data_dir):
    """add_false_positive は store_write("false_positives.jsonl") 経由（#55 wave 3）。"""
    import rl_common
    with mock.patch.object(rl_common, "store_write") as m_sw:
        rl_common.add_false_positive("いや、そうじゃなくて optimize を使って", "iya")
    assert m_sw.call_count == 1
    assert m_sw.call_args.args[0] == "false_positives.jsonl"


def test_session_store_routes_through_store_write_raw(data_dir):
    """session_store._append_jsonl は自己解決パスを尊重し store_write_raw 経由（#55 wave 3）。"""
    import rl_common
    import session_store
    with mock.patch.object(rl_common, "store_write_raw") as m_raw:
        session_store._append_jsonl({"session_id": "s", "timestamp": "t"})
    assert m_raw.call_count == 1
    assert m_raw.call_args.args[0] == session_store.SESSIONS_JSONL


def test_weak_signals_gate_routes_through_store_write(data_dir):
    """append_signals(path 無し) は store_write("weak_signals.jsonl") 経由（#55 wave 3）。"""
    import rl_common
    from weak_signals.store import WeakSignal, append_signals
    sig = WeakSignal(
        channel="llm_judge",
        provenance={"source_path": "/a.jsonl", "line_no": 1, "text": "x", "reason": "r"},
        detected_at="2026-06-10T00:00:00+00:00",
        session_id="s1",
        pj_slug="evolve-anything",
    )
    with mock.patch.object(rl_common, "store_write") as m_sw:
        append_signals([sig])  # path=None → gate
    assert m_sw.call_count == 1
    assert m_sw.call_args.args[0] == "weak_signals.jsonl"


def test_record_judged_gate_routes_through_store_write(data_dir):
    """record_judged(path 無し) は store_write("correction_judged.jsonl") 経由（#55 wave 3）。"""
    import rl_common
    from correction_semantic.store import record_judged
    with mock.patch.object(rl_common, "store_write") as m_sw:
        record_judged(["k1"])  # path=None → gate
    assert m_sw.call_count == 1
    assert m_sw.call_args.args[0] == "correction_judged.jsonl"


def test_record_reviewed_gate_routes_through_store_write(data_dir):
    """record_reviewed(path 無し) は store_write("correction_review_seen.jsonl") 経由（#55 wave 3）。"""
    import rl_common
    from correction_semantic.daily_review import record_reviewed
    with mock.patch.object(rl_common, "store_write") as m_sw:
        record_reviewed(["k1"], "evolve-anything", decision="rejected")  # path=None → gate
    assert m_sw.call_count == 1
    assert m_sw.call_args.args[0] == "correction_review_seen.jsonl"


def StoreDeclaration_legacy() -> "store_registry.StoreDeclaration":
    """status=legacy のダミー宣言（guard テスト用ヘルパ）。"""
    return store_registry.StoreDeclaration(
        name="legacy_store.jsonl",
        writer="（テスト用ダミー）",
        reader="（テスト用ダミー）",
        retention="permanent",
        classification="workflow_state",
        status="legacy",
    )


def StoreDeclaration_json_kind() -> "store_registry.StoreDeclaration":
    """kind=json のダミー宣言（guard テスト用ヘルパ・#399 codex round1 Should 1）。"""
    return store_registry.StoreDeclaration(
        name="single_object.json",
        kind="json",
        writer="（テスト用ダミー）",
        reader="（テスト用ダミー）",
        retention="permanent",
        classification="derived_cache",
    )
# --- guard_problem: correction 専用保存境界向け公開 API -----------------------


def test_guard_problem_is_public_single_source():
    assert guard_problem("corrections.jsonl") is None
    assert "未登録ストア" in guard_problem("definitely-unknown.jsonl")
