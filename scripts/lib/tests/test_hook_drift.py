"""hook_drift（他ツール追従 hook の stale_pin 検出）のテスト。

決定論・LLM 非依存なので mock は不要。tmp_path に gstack ディレクトリを模して検査する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import hook_drift  # noqa: E402
from audit.sections_hook import build_hook_drift_section  # noqa: E402


def _make_gstack(tmp_path: Path, *, pinned: str | None, actual: str | None) -> Path:
    """flow-chain.json と .last-setup-version を持つ疑似 ~/.gstack を作る。"""
    gdir = tmp_path / ".gstack"
    gdir.mkdir()
    if pinned is not None:
        (gdir / "flow-chain.json").write_text(
            json.dumps({"gstack_version": pinned, "chain": {}}), encoding="utf-8"
        )
    if actual is not None:
        (gdir / ".last-setup-version").write_text(actual, encoding="utf-8")
    return gdir


# --- check_hook_drift -------------------------------------------------------

def test_gstack_absent_is_not_applicable(tmp_path: Path) -> None:
    """.gstack 自体が無い環境は対象外（applicable=False）。"""
    report = hook_drift.check_hook_drift(gstack_dir=tmp_path / "nonexistent")
    assert report.applicable is False
    assert report.stale_pin is False


def test_flow_chain_absent_is_not_applicable(tmp_path: Path) -> None:
    """.gstack はあるが flow-chain.json が無ければ追従対象が無く対象外。"""
    gdir = _make_gstack(tmp_path, pinned=None, actual="1.55.0.0")
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is False


def test_versions_match_no_drift(tmp_path: Path) -> None:
    """pinned == actual なら stale_pin なし（applicable だが drift なし）。"""
    gdir = _make_gstack(tmp_path, pinned="1.55.0.0", actual="1.55.0.0")
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is True
    assert report.stale_pin is False
    assert report.minor_gap == 0


def test_stale_pin_detected_with_minor_gap(tmp_path: Path) -> None:
    """pinned が actual より古い → stale_pin、minor gap を算出。"""
    gdir = _make_gstack(tmp_path, pinned="1.47.0.0", actual="1.55.0.0")
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is True
    assert report.stale_pin is True
    assert report.pinned_version == "1.47.0.0"
    assert report.actual_version == "1.55.0.0"
    assert report.minor_gap == 8


def test_actual_version_unreadable_cannot_judge(tmp_path: Path) -> None:
    """.last-setup-version が無いと実 version 不明 → 判定不能（stale 断定しない）。"""
    gdir = _make_gstack(tmp_path, pinned="1.47.0.0", actual=None)
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is True
    assert report.actual_version is None
    assert report.stale_pin is False  # 不明を stale と誤検知しない


def test_unparseable_version_falls_back_to_string_compare(tmp_path: Path) -> None:
    """version が数値解析できなくても、文字列不一致なら stale とみなす（gap は 0）。"""
    gdir = _make_gstack(tmp_path, pinned="alpha", actual="beta")
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.applicable is True
    assert report.stale_pin is True
    assert report.minor_gap == 0


def test_report_carries_evidence_source_paths(tmp_path: Path) -> None:
    """検出元パス（evidence）を report に持つ（#394）。独自検証で迷わないため。"""
    gdir = _make_gstack(tmp_path, pinned="1.47.0.0", actual="1.55.0.0")
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.pinned_source == str(gdir / "flow-chain.json")
    assert report.actual_source == str(gdir / ".last-setup-version")


def test_actual_source_none_when_actual_unreadable(tmp_path: Path) -> None:
    """実 version が読めなければ actual_source は None（存在しないパスを出さない）。"""
    gdir = _make_gstack(tmp_path, pinned="1.47.0.0", actual=None)
    report = hook_drift.check_hook_drift(gstack_dir=gdir)
    assert report.actual_source is None
    assert report.pinned_source == str(gdir / "flow-chain.json")


# --- build_hook_drift_section (observability builder) -----------------------

def test_builder_returns_none_when_not_applicable(tmp_path: Path, monkeypatch) -> None:
    """gstack 不在環境では builder は None（沈黙）。"""
    monkeypatch.setattr(
        hook_drift, "_default_gstack_dir", lambda: tmp_path / "nonexistent"
    )
    assert build_hook_drift_section(tmp_path) is None


def test_builder_emits_ok_line_when_clean(tmp_path: Path, monkeypatch) -> None:
    """version 一致時は『評価したが drift なし ✓』を残す（silence != evaluated）。"""
    gdir = _make_gstack(tmp_path, pinned="1.55.0.0", actual="1.55.0.0")
    monkeypatch.setattr(hook_drift, "_default_gstack_dir", lambda: gdir)
    section = build_hook_drift_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "✓" in body
    assert "1.55.0.0" in body


def test_builder_emits_warning_on_stale_pin(tmp_path: Path, monkeypatch) -> None:
    """stale 時は ⚠ と両 version、見直し誘導を出す。"""
    gdir = _make_gstack(tmp_path, pinned="1.47.0.0", actual="1.55.0.0")
    monkeypatch.setattr(hook_drift, "_default_gstack_dir", lambda: gdir)
    section = build_hook_drift_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "⚠" in body
    assert "1.47.0.0" in body
    assert "1.55.0.0" in body
    # evidence（#394）: 検出元パスを併記する
    assert "flow-chain.json" in body
    assert ".last-setup-version" in body
    assert "出元" in body


def test_builder_registered_in_observability_contract() -> None:
    """observability contract に hook_drift builder が登録されていること。"""
    from audit.observability import _OBSERVABILITY_BUILDERS

    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "hook_drift" in keys


# --- normalize_skill_ref (表記ゆれ正規化) ------------------------------------
# dead_ref の信頼性は正規化変換に依存するため、変換を最初に固定する（#316）。

@pytest.mark.parametrize(
    "raw, expected",
    [
        # プレフィックス除去（コマンドの `/`）
        ("/review", "review"),
        ("/ship", "ship"),
        # プラグイン名前空間 `plugin:skill`
        ("/rl-anything:implement", "implement"),
        ("rl-anything:implement", "implement"),
        # 引数を伴う参照 → スキル名のみ
        ("/rl-anything:spec-keeper update", "spec-keeper"),
        ("/spec-keeper init", "spec-keeper"),
        # 前後空白
        ("  /retro  ", "retro"),
        # 既に裸のスキル名（flow-chain の chain キー）
        ("office-hours", "office-hours"),
        ("design-shotgun", "design-shotgun"),
    ],
)
def test_normalize_skill_ref(raw: str, expected: str) -> None:
    """参照表記を skill 名に正規化する（プレフィックス/名前空間/引数/空白を除去）。"""
    assert hook_drift.normalize_skill_ref(raw) == expected


def test_normalize_skill_ref_empty_returns_none() -> None:
    """空文字・記号のみは正規化不能 → None（dead_ref 判定から除外する材料）。"""
    assert hook_drift.normalize_skill_ref("") is None
    assert hook_drift.normalize_skill_ref("   ") is None
    assert hook_drift.normalize_skill_ref("/") is None


# --- build_live_skill_registry ----------------------------------------------

def test_live_registry_includes_user_and_plugin_skills(tmp_path: Path, monkeypatch) -> None:
    """live registry は ~/.claude/skills と plugin skills を併合する。"""
    skills_dir = tmp_path / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "review").mkdir()
    (skills_dir / "ship").mkdir()
    monkeypatch.setattr(hook_drift, "_user_skills_dir", lambda: skills_dir)
    monkeypatch.setattr(hook_drift, "_plugin_skill_names", lambda: frozenset({"implement"}))
    monkeypatch.setattr(hook_drift, "_repo_self_skill_names", lambda: frozenset())

    registry = hook_drift.build_live_skill_registry()
    assert "review" in registry
    assert "ship" in registry
    assert "implement" in registry


# --- detect_dead_refs --------------------------------------------------------

def _make_gstack_with_chain(tmp_path: Path, chain: dict) -> Path:
    gdir = tmp_path / ".gstack"
    gdir.mkdir()
    (gdir / "flow-chain.json").write_text(
        json.dumps({"gstack_version": "1.0.0.0", "chain": chain}), encoding="utf-8"
    )
    return gdir


def test_dead_refs_empty_when_all_refs_live(tmp_path: Path, monkeypatch) -> None:
    """全参照が live registry に存在すれば dead_ref はゼロ（FP なし）。"""
    chain = {
        "spec": {"next": ["/review", "/rl-anything:implement"]},
        "office-hours": {"next": ["/plan-eng-review"]},
    }
    gdir = _make_gstack_with_chain(tmp_path, chain)
    monkeypatch.setattr(
        hook_drift,
        "build_live_skill_registry",
        lambda: frozenset({"spec", "review", "implement", "office-hours", "plan-eng-review"}),
    )
    dead = hook_drift.detect_dead_refs(gstack_dir=gdir)
    assert dead == []


def test_dead_ref_detected_for_missing_skill(tmp_path: Path, monkeypatch) -> None:
    """live registry に無いスキル名を参照していれば dead_ref として返す。"""
    chain = {
        "spec": {"next": ["/review", "/totally-nonexistent-skill"]},
    }
    gdir = _make_gstack_with_chain(tmp_path, chain)
    monkeypatch.setattr(
        hook_drift,
        "build_live_skill_registry",
        lambda: frozenset({"spec", "review"}),
    )
    dead = hook_drift.detect_dead_refs(gstack_dir=gdir)
    assert len(dead) == 1
    assert dead[0].ref == "/totally-nonexistent-skill"
    assert dead[0].normalized == "totally-nonexistent-skill"
    assert dead[0].source == "spec"


def test_dead_ref_includes_chain_source_keys(tmp_path: Path, monkeypatch) -> None:
    """chain のソースキー自体も live registry と突合する（参照する側のスキルが消えた場合）。"""
    chain = {
        "ghost-skill": {"next": ["/review"]},
    }
    gdir = _make_gstack_with_chain(tmp_path, chain)
    monkeypatch.setattr(
        hook_drift,
        "build_live_skill_registry",
        lambda: frozenset({"review"}),
    )
    dead = hook_drift.detect_dead_refs(gstack_dir=gdir)
    norms = {d.normalized for d in dead}
    assert "ghost-skill" in norms


def test_dead_ref_unnormalizable_ref_is_not_flagged(tmp_path: Path, monkeypatch) -> None:
    """正規化不能（空・記号のみ）の参照は dead_ref にしない（precision 優先・FP 厳禁）。"""
    chain = {
        "spec": {"next": ["/", "  "]},
    }
    gdir = _make_gstack_with_chain(tmp_path, chain)
    monkeypatch.setattr(
        hook_drift, "build_live_skill_registry", lambda: frozenset({"spec"})
    )
    dead = hook_drift.detect_dead_refs(gstack_dir=gdir)
    assert dead == []


def test_dead_ref_absent_flow_chain_returns_empty(tmp_path: Path, monkeypatch) -> None:
    """flow-chain.json が無ければ dead_ref 検査は空（沈黙対象）。"""
    gdir = tmp_path / ".gstack"
    gdir.mkdir()
    monkeypatch.setattr(
        hook_drift, "build_live_skill_registry", lambda: frozenset({"review"})
    )
    dead = hook_drift.detect_dead_refs(gstack_dir=gdir)
    assert dead == []


def test_dead_ref_empty_registry_does_not_flag(tmp_path: Path, monkeypatch) -> None:
    """live registry が空（skill 列挙に失敗）なら全参照が dead に見えるが、
    それは検出器側の不備なので何も flag しない（FP 厳禁・precision 優先）。"""
    chain = {"spec": {"next": ["/review"]}}
    gdir = _make_gstack_with_chain(tmp_path, chain)
    monkeypatch.setattr(hook_drift, "build_live_skill_registry", lambda: frozenset())
    dead = hook_drift.detect_dead_refs(gstack_dir=gdir)
    assert dead == []


# --- build_hook_drift_section: dead_ref surface -----------------------------

def test_builder_surfaces_dead_ref(tmp_path: Path, monkeypatch) -> None:
    """dead_ref があれば section に ⚠ と参照名・出元スキルを出す。"""
    chain = {"spec": {"next": ["/review", "/gone-skill"]}}
    gdir = _make_gstack_with_chain(tmp_path, chain)
    (gdir / ".last-setup-version").write_text("1.0.0.0", encoding="utf-8")
    monkeypatch.setattr(hook_drift, "_default_gstack_dir", lambda: gdir)
    monkeypatch.setattr(
        hook_drift, "build_live_skill_registry", lambda: frozenset({"spec", "review"})
    )
    section = build_hook_drift_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "gone-skill" in body
    assert "spec" in body  # 出元スキル


def test_builder_no_dead_ref_line_when_all_live(tmp_path: Path, monkeypatch) -> None:
    """dead_ref が無ければ dead_ref 行は出さない（沈黙・ノイズを増やさない）。"""
    chain = {"spec": {"next": ["/review"]}}
    gdir = _make_gstack_with_chain(tmp_path, chain)
    (gdir / ".last-setup-version").write_text("1.0.0.0", encoding="utf-8")
    monkeypatch.setattr(hook_drift, "_default_gstack_dir", lambda: gdir)
    monkeypatch.setattr(
        hook_drift, "build_live_skill_registry", lambda: frozenset({"spec", "review"})
    )
    section = build_hook_drift_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "参照先スキル" not in body and "dead" not in body.lower()


# --- 実コーパス FP guard（実 ~/.gstack/flow-chain.json + 実 skill registry）----

@pytest.mark.real_home
def test_real_flow_chain_has_zero_dead_refs() -> None:
    """実環境の flow-chain.json を実 live registry で突合し dead_ref がゼロであること。

    合成 fixture の false confidence を避けるドッグフード。gstack 未導入環境では skip。
    autouse の HOME 隔離を real_home でオプトアウトし、実 ~/.gstack / ~/.claude/skills を読む。
    """
    real_gstack = Path.home() / ".gstack"
    if not (real_gstack / "flow-chain.json").is_file():
        pytest.skip("gstack flow-chain.json が無い環境")
    dead = hook_drift.detect_dead_refs(gstack_dir=real_gstack)
    assert dead == [], f"実 flow-chain に dead_ref（FP の可能性）: {[d.ref for d in dead]}"
