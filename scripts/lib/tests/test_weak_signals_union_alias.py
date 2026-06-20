"""weak_signals read 層 union+alias の #46 テスト（Phase 2: weak_signals + 既読）。

PJ rename（rl-anything→evolve-anything）で legacy ``~/.claude/rl-anything`` に取り残された
weak_signals（weak_signals.jsonl）/ 既読集合（correction_review_seen.jsonl）が canonical-only
reader から見えない問題（#46）を、Phase 1（idioms/judged）と同じ ``iter_read_data_dirs`` union +
``canonical_pj_slug`` alias で解消する。**物理 merge せず read 層だけで visibility を回復**。

Phase 2 の核心（flooding 防止）: weak_signals を union すると legacy のシグナルが見えるが、
既読（correction_review_seen）も同時に union しないと「legacy で確認済みのシグナル」が新規扱いで
daily_review に再噴出する。よって read_signals の union と read_reviewed_keys の union は対で入れる。

設計の不変条件:
- 明示 path 指定時は union しない（テスト isolation / write round-trip の hermetic 性を維持）
- union は signal_key（weak_signals）/ key（既読）で dedup・canonical 先頭勝ち
- alias は read 専用（write は現 slug 固定）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import rl_common  # noqa: E402
from correction_semantic import daily_review as dr  # noqa: E402
from correction_semantic import idiom_autopromote as iap  # noqa: E402
from correction_semantic import store as cs_store  # noqa: E402
from weak_signals import store as ws_store  # noqa: E402

LEGACY_SLUG = "rl-anything"
CUR_SLUG = "evolve-anything"
ELIGIBLE_IDIOM = "テストを先に書くべき"  # 8文字以上・stopword/context-token 無し → idiom_eligible


def _patch_union(monkeypatch, dirs) -> None:
    """union 候補 dir を固定する（read_signals / read_idioms / read_reviewed_keys 共通）。"""
    monkeypatch.setattr(
        rl_common, "iter_read_data_dirs", lambda canonical=None: list(dirs)
    )


def _write_signal(
    path: Path,
    pj_slug,
    *,
    channel: str = "llm_judge",
    source_path: str = "/a.jsonl",
    line_no: int = 1,
    text: str = ELIGIBLE_IDIOM,
    promoted: bool = False,
    expired: bool = False,
) -> dict:
    rec = ws_store.WeakSignal(
        channel=channel,
        provenance={"source_path": source_path, "line_no": line_no, "text": text},
        detected_at="2026-06-10T00:00:00+00:00",
        session_id="s1",
        pj_slug=pj_slug,
        promoted=promoted,
        expired=expired,
    ).to_record()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _write_seen(path: Path, key: str, pj_slug: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "key": key,
        "pj_slug": pj_slug,
        "decision": "promoted",
        "reviewed_at": "2026-06-10T00:00:00+00:00",
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _write_idiom(path: Path, idiom, pj_slug, *, confirmed=False, line_no=1) -> dict:
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


# ── union read: legacy dir の weak_signal が見える ─────────────────────


def test_read_signals_unions_legacy(tmp_path, monkeypatch) -> None:
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    _write_signal(legacy / "weak_signals.jsonl", LEGACY_SLUG, text="旧シグナル本文だよ")
    _patch_union(monkeypatch, [canonical, legacy])

    recs = ws_store.read_signals()
    assert len(recs) == 1
    assert recs[0]["pj_slug"] == LEGACY_SLUG


def test_read_signals_dedups_canonical_wins(tmp_path, monkeypatch) -> None:
    """同 signal_key が canonical と legacy 両方にあれば canonical（先頭）が勝つ。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    r_leg = _write_signal(legacy / "weak_signals.jsonl", LEGACY_SLUG, line_no=1)
    r_can = _write_signal(
        canonical / "weak_signals.jsonl", LEGACY_SLUG, line_no=1, promoted=True
    )
    assert r_leg["signal_key"] == r_can["signal_key"]
    _patch_union(monkeypatch, [canonical, legacy])

    recs = ws_store.read_signals()
    assert len(recs) == 1
    assert recs[0]["promoted"] is True  # canonical 先頭勝ち


def test_read_signals_explicit_path_no_union(tmp_path, monkeypatch) -> None:
    """明示 path 指定時は union しない（hermetic）。"""
    legacy = tmp_path / "rl-anything"
    _write_signal(legacy / "weak_signals.jsonl", LEGACY_SLUG)
    _patch_union(monkeypatch, [tmp_path / "evolve-anything", legacy])

    assert ws_store.read_signals(tmp_path / "explicit.jsonl") == []


def test_read_signals_keeps_keyless_records(tmp_path, monkeypatch) -> None:
    """signal_key 欠落レコードは dedup できないので全件残す（取りこぼし防止）。"""
    canonical = tmp_path / "evolve-anything"
    canonical.mkdir(parents=True)
    (canonical / "weak_signals.jsonl").write_text(
        json.dumps({"channel": "llm_judge", "pj_slug": LEGACY_SLUG}) + "\n"
        + json.dumps({"channel": "llm_judge", "pj_slug": LEGACY_SLUG}) + "\n",
        encoding="utf-8",
    )
    _patch_union(monkeypatch, [canonical])

    assert len(ws_store.read_signals()) == 2


# ── 既読 union（flooding 防止の要） ────────────────────────────────────


def test_read_reviewed_keys_unions_legacy(tmp_path, monkeypatch) -> None:
    """legacy の既読 signal_key が現環境の既読集合に union される（再噴出防止）。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    _write_seen(legacy / "correction_review_seen.jsonl", "K_LEGACY", LEGACY_SLUG)
    _patch_union(monkeypatch, [canonical, legacy])

    assert "K_LEGACY" in dr.read_reviewed_keys()


def test_read_reviewed_keys_explicit_path_no_union(tmp_path, monkeypatch) -> None:
    legacy = tmp_path / "rl-anything"
    _write_seen(legacy / "correction_review_seen.jsonl", "K_LEGACY", LEGACY_SLUG)
    _patch_union(monkeypatch, [tmp_path / "evolve-anything", legacy])

    assert dr.read_reviewed_keys(tmp_path / "explicit.jsonl") == set()


# ── alias: 旧 slug タグの legacy weak_signal が現 slug クエリで一致 ──────


def test_read_new_alias_picks_up_legacy_signal(tmp_path, monkeypatch) -> None:
    """daily_review._read_new が旧 slug タグの legacy weak_signal を alias で拾う。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    _write_signal(legacy / "weak_signals.jsonl", LEGACY_SLUG, line_no=3)
    _patch_union(monkeypatch, [canonical, legacy])

    new = dr._read_new(CUR_SLUG, weak_signals_path=None, seen_keys=set())
    assert len(new) == 1
    assert new[0]["pj_slug"] == LEGACY_SLUG


def test_read_new_non_aliased_slug_exact_preserved(tmp_path, monkeypatch) -> None:
    """別名の無い通常 slug は従来どおり exact 一致（既存挙動を壊さない）。"""
    canonical = tmp_path / "evolve-anything"
    _write_signal(canonical / "weak_signals.jsonl", "some-other-pj", line_no=4)
    _patch_union(monkeypatch, [canonical])

    assert dr._read_new(CUR_SLUG, weak_signals_path=None, seen_keys=set()) == []
    assert len(dr._read_new("some-other-pj", weak_signals_path=None, seen_keys=set())) == 1


def test_idiom_by_phys_alias(tmp_path) -> None:
    """daily_review._idiom_by_phys が旧 slug タグの legacy idiom を alias で拾う。"""
    idioms = [
        {
            "pj_slug": LEGACY_SLUG,
            "provenance": {"source_path": "/a.jsonl", "line_no": 9},
            "idiom": "言い回し本文",
        }
    ]
    mapping = dr._idiom_by_phys(idioms, CUR_SLUG)
    assert mapping.get("/a.jsonl:9") == "言い回し本文"


def test_autopromote_matches_legacy_signal_via_alias(tmp_path, monkeypatch) -> None:
    """idiom_autopromote が legacy weak_signal を union+alias で昇格候補にする（line 102）。"""
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    # confirmed idiom（canonical・現 slug）と同 phys の legacy weak_signal（旧 slug）。
    _write_idiom(
        canonical / "correction_idioms.jsonl",
        ELIGIBLE_IDIOM,
        CUR_SLUG,
        confirmed=True,
        line_no=7,
    )
    _write_signal(legacy / "weak_signals.jsonl", LEGACY_SLUG, line_no=7, text=ELIGIBLE_IDIOM)
    _patch_union(monkeypatch, [canonical, legacy])

    res = iap.autopromote(CUR_SLUG, dry_run=True)
    assert res["promoted"] == 1  # alias 無しなら legacy シグナルが弾かれて 0


def test_autopromote_marks_legacy_no_repromotion(tmp_path, monkeypatch) -> None:
    """legacy weak_signal を昇格すると legacy file に promoted=True が立ち再昇格しない（#46 seam）。

    read は union（legacy 可視）だが mark を canonical だけに書くと legacy の promoted=False が
    残り毎 run 再昇格＝重複 corrections になる。_mark_promoted が legacy file も書き換えることを assert。
    """
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    corr = tmp_path / "corrections.jsonl"
    _write_idiom(
        canonical / "correction_idioms.jsonl",
        ELIGIBLE_IDIOM,
        CUR_SLUG,
        confirmed=True,
        line_no=7,
    )
    _write_signal(legacy / "weak_signals.jsonl", LEGACY_SLUG, line_no=7, text=ELIGIBLE_IDIOM)
    _patch_union(monkeypatch, [canonical, legacy])

    first = iap.autopromote(CUR_SLUG, corrections_path=corr, dry_run=False)
    assert first["promoted"] == 1
    # legacy file に promoted=True が永続化された（再昇格防止の根拠）
    legacy_recs = ws_store.read_signals(legacy / "weak_signals.jsonl")
    assert legacy_recs[0]["promoted"] is True
    # 2 回目は再昇格しない（重複 corrections の avalanche を防止）
    second = iap.autopromote(CUR_SLUG, corrections_path=corr, dry_run=False)
    assert second["promoted"] == 0


# ── 結合: legacy 既読が legacy シグナルの再噴出を抑える（flooding 防止） ───


def test_build_review_legacy_seen_prevents_flooding(tmp_path, monkeypatch) -> None:
    canonical = tmp_path / "evolve-anything"
    legacy = tmp_path / "rl-anything"
    rec = _write_signal(legacy / "weak_signals.jsonl", LEGACY_SLUG, line_no=5)
    _patch_union(monkeypatch, [canonical, legacy])

    # 既読が無ければ legacy シグナルは新規として提示される（alias + union が効いている証拠）。
    before = dr.build_review(CUR_SLUG)
    assert before["eligible"] is True

    # legacy 既読を入れると union で既読扱いになり再噴出しない。
    _write_seen(
        legacy / "correction_review_seen.jsonl", rec["signal_key"], LEGACY_SLUG
    )
    after = dr.build_review(CUR_SLUG)
    assert after["eligible"] is False
    assert after["groups"] == []
