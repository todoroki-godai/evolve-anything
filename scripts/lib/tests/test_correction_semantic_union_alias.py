"""correction_semantic read 層 union+alias の #46 テスト（Phase 1: idioms + judged）。

PJ rename（rl-anything→evolve-anything）で legacy ``~/.claude/rl-anything`` に取り残された
個人辞書（correction_idioms.jsonl）/ 判定進捗（correction_judged.jsonl）が canonical-only
reader から見えない問題（#46）を、capture_rate と同じ ``iter_read_data_dirs`` union +
``canonical_pj_slug`` alias で解消する。**物理 merge せず read 層だけで visibility を回復**。

設計の不変条件:
- 明示 path 指定時は union しない（テスト isolation / write round-trip の hermetic 性を維持）
- union は idiom_key（idiom）/ key（judged）で dedup・canonical 先頭勝ち
- alias は read 専用（write は現 slug 固定）

weak_signals の union は daily_review の既読（correction_review_seen）取り残しと結合するため
Phase 2 に分離（本テストの対象外）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import rl_common  # noqa: E402
from correction_semantic import idiom_autopromote as iap  # noqa: E402
from correction_semantic import store as cs_store  # noqa: E402

LEGACY_SLUG = "rl-anything"
CUR_SLUG = "evolve-anything"


def _write_idiom(path: Path, idiom, pj_slug, confirmed=False, line_no=1) -> dict:
    rec = cs_store.CorrectionIdiom(
        idiom=idiom,
        provenance={"source_path": "/a.jsonl", "line_no": line_no},
        detected_at="2026-06-10T00:00:00+00:00",
        pj_slug=pj_slug,
        confirmed=confirmed,
    ).to_record()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _patch_union(monkeypatch, dirs) -> None:
    """read_idioms / read_judged_keys が参照する union 候補 dir を固定する（hermetic）。"""
    monkeypatch.setattr(
        rl_common, "iter_read_data_dirs", lambda canonical=None: list(dirs)
    )


# ── union read: legacy dir のレコードが見える ───────────────────────


def test_read_idioms_unions_legacy(tmp_path, monkeypatch) -> None:
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    _write_idiom(legacy / "correction_idioms.jsonl", "旧言い回し", LEGACY_SLUG)
    _patch_union(monkeypatch, [canonical, legacy])

    recs = cs_store.read_idioms()
    assert [r["idiom"] for r in recs] == ["旧言い回し"]


def test_read_idioms_dedups_canonical_wins(tmp_path, monkeypatch) -> None:
    """同 idiom_key が canonical と legacy 両方にあれば canonical（先頭）が勝つ。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    r_leg = _write_idiom(legacy / "correction_idioms.jsonl", "x", LEGACY_SLUG, line_no=1)
    r_can = _write_idiom(
        canonical / "correction_idioms.jsonl", "x", LEGACY_SLUG, confirmed=True, line_no=1
    )
    assert r_leg["idiom_key"] == r_can["idiom_key"]
    _patch_union(monkeypatch, [canonical, legacy])

    recs = cs_store.read_idioms()
    assert len(recs) == 1
    assert recs[0]["confirmed"] is True  # canonical 先頭勝ち


def test_explicit_path_no_union(tmp_path, monkeypatch) -> None:
    """明示 path 指定時は union しない（hermetic）。"""
    legacy = tmp_path / "rl-anything"
    _write_idiom(legacy / "correction_idioms.jsonl", "旧", LEGACY_SLUG)
    _patch_union(monkeypatch, [tmp_path / "evolve-anything", legacy])

    assert cs_store.read_idioms(tmp_path / "explicit.jsonl") == []


def test_read_judged_keys_unions(tmp_path, monkeypatch) -> None:
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    legacy.mkdir(parents=True)
    (legacy / "correction_judged.jsonl").write_text(
        json.dumps({"key": "/a.jsonl:1"}) + "\n", encoding="utf-8"
    )
    _patch_union(monkeypatch, [canonical, legacy])

    assert "/a.jsonl:1" in cs_store.read_judged_keys()


def test_read_judged_keys_explicit_path_no_union(tmp_path, monkeypatch) -> None:
    legacy = tmp_path / "rl-anything"
    legacy.mkdir(parents=True)
    (legacy / "correction_judged.jsonl").write_text(
        json.dumps({"key": "/a.jsonl:1"}) + "\n", encoding="utf-8"
    )
    _patch_union(monkeypatch, [tmp_path / "evolve-anything", legacy])

    assert cs_store.read_judged_keys(tmp_path / "explicit.jsonl") == set()


# ── alias: 旧 slug タグの legacy record が現 slug クエリで一致 ──────────


def test_read_confirmed_idiom_texts_alias(tmp_path, monkeypatch) -> None:
    """legacy の confirmed idiom（旧 slug タグ）が現 slug クエリで見える（alias）。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    _write_idiom(legacy / "correction_idioms.jsonl", "確認済", LEGACY_SLUG, confirmed=True)
    _patch_union(monkeypatch, [canonical, legacy])

    texts = cs_store.read_confirmed_idiom_texts(CUR_SLUG)
    assert "確認済" in texts


def test_cross_pj_excludes_aliased_self(tmp_path, monkeypatch) -> None:
    """旧 slug の自 PJ confirmed idiom は cross-PJ シグナルに混ぜない（alias 自己除外）。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    _write_idiom(legacy / "correction_idioms.jsonl", "自PJ", LEGACY_SLUG, confirmed=True)
    _patch_union(monkeypatch, [canonical, legacy])

    cross = cs_store.read_cross_pj_confirmed_idiom_texts(CUR_SLUG)
    assert "自PJ" not in cross  # rl-anything は evolve-anything の別名 = self


def test_phys_to_idiom_alias(tmp_path, monkeypatch) -> None:
    """idiom_autopromote._phys_to_idiom が旧 slug タグの legacy idiom を alias で拾う。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    _write_idiom(legacy / "correction_idioms.jsonl", "言い回し", LEGACY_SLUG, line_no=7)
    _patch_union(monkeypatch, [canonical, legacy])

    mapping = iap._phys_to_idiom(CUR_SLUG, None)
    assert mapping.get("/a.jsonl:7", {}).get("idiom") == "言い回し"


def test_non_aliased_slug_exact_match_preserved(tmp_path, monkeypatch) -> None:
    """別名の無い通常 slug は従来どおり exact 一致（既存挙動を壊さない）。"""
    canonical = tmp_path / "evolve-anything"
    _write_idiom(canonical / "correction_idioms.jsonl", "他PJ", "some-other-pj", confirmed=True)
    _patch_union(monkeypatch, [canonical])

    assert cs_store.read_confirmed_idiom_texts(CUR_SLUG) == set()
    assert cs_store.read_confirmed_idiom_texts("some-other-pj") == {"他PJ"}
