"""correction_semantic.bootstrap_backlog のテスト（#443 初回バックログ bootstrap）。

既存の weak_signals バックログ（channel=llm_judge・未昇格）を初回 evolve 時に
まとめて確認するための決定論 phase を検証する。

検証観点:
- marker 未設定なら is_bootstrap=True、当該 PJ slug の未昇格 backlog のみを集計する
  （別 PJ の件数が混入しない — DATA_DIR 全PJ共通 pitfall）。
- 内容キーワード（漢字/カタカナ 2 字以上）jaccard≥0.5 で group 化し、代表 idiom と
  signal_keys を返す（一括昇格 UX 用）。
- marker が立っていたら is_bootstrap=False で即返す（pj_total/groups は計算しない＝早期 return）。
- mark_done は dry_run でファイル不変（最下層まで dry-run ゲートを貫通）。
- expired フィールドがあれば防御的に除外する（#442 TTL の reader API が無い前提の浅い連携）。

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import bootstrap_backlog as bb  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402


def _sig(text: str, line_no: int, pj_slug: str = "rl-anything", **prov_extra) -> WeakSignal:
    prov = {"source_path": "/a.jsonl", "line_no": line_no, "text": text, "reason": "r"}
    prov.update(prov_extra)
    return WeakSignal(
        channel="llm_judge",
        provenance=prov,
        detected_at="2026-06-10T00:00:00+00:00",
        session_id="s1",
        pj_slug=pj_slug,
    )


def _marker(tmp_path: Path) -> Path:
    return tmp_path / "bootstrap_done-rl-anything.marker"


# ─────────────────────────────────────────────────────────────────
# keyword 抽出 / jaccard grouping（決定論）
# ─────────────────────────────────────────────────────────────────
def test_extract_keywords_kanji_katakana_2chars():
    kws = bb.extract_keywords("金額がきれてる")
    # 漢字 2 字以上「金額」を拾う。1 字・助詞は拾わない。
    assert "金額" in kws


def test_extract_keywords_drops_single_char_and_particles():
    kws = bb.extract_keywords("赤にして")
    # 1 文字漢字・ひらがなは keyword にならない
    assert all(len(k) >= 2 for k in kws)


def test_extract_keywords_katakana():
    kws = bb.extract_keywords("カテゴリの表示")
    assert "カテゴリ" in kws


def test_group_merges_similar_idioms():
    # 同じ keyword を共有する 2 件は 1 group になる
    sigs = [
        _sig("金額がきれてる", 1),
        _sig("金額の表示がきれてる", 2),
        _sig("全然違うカテゴリの話", 3),
    ]
    groups = bb.group_signals([s.to_record() for s in sigs])
    # 金額系 2 件が 1 group、別件が 1 group → 計 2 group
    assert len(groups) == 2
    big = max(groups, key=lambda g: len(g["signal_keys"]))
    assert len(big["signal_keys"]) == 2
    assert big["representative"]  # 代表 idiom がある


def test_group_keyless_idioms_stay_separate():
    # keyword が抽出できない短い断片は dedup されず別 group のまま（圧縮されない）
    sigs = [_sig("やめて", 1), _sig("ちがう", 2)]
    groups = bb.group_signals([s.to_record() for s in sigs])
    assert len(groups) == 2


def test_group_signal_keys_cover_all_inputs():
    # group 化しても signal_keys の総和は入力件数と一致する（取りこぼし無し）
    sigs = [_sig("金額がきれてる", 1), _sig("金額の表示", 2), _sig("別件です", 3)]
    recs = [s.to_record() for s in sigs]
    groups = bb.group_signals(recs)
    total_keys = sum(len(g["signal_keys"]) for g in groups)
    assert total_keys == len(recs)


# ─────────────────────────────────────────────────────────────────
# build: marker 未設定 → is_bootstrap=True
# ─────────────────────────────────────────────────────────────────
def test_build_returns_bootstrap_when_no_marker(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1), _sig("書き直しして", 2)], path=ws)

    res = bb.build("rl-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["is_bootstrap"] is True
    assert res["pj_total"] == 2
    assert res["groups_total"] >= 1
    assert isinstance(res["groups"], list)
    assert res["dry_run"] is False


def test_build_scopes_to_pj_slug_only(tmp_path: Path):
    # 別 PJ の件数が混入しないこと（Acceptance: cwd の PJ slug のみ）
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("金額がきれてる", 1, pj_slug="rl-anything"),
            _sig("別件です", 2, pj_slug="figma-to-code"),
            _sig("別件2の話", 3, pj_slug="figma-to-code"),
        ],
        path=ws,
    )
    res = bb.build("rl-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["pj_total"] == 1  # rl-anything の 1 件のみ


def test_build_only_counts_unpromoted(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    promoted = _sig("もう昇格済み", 2)
    promoted.promoted = True
    append_signals([promoted], path=ws)
    res = bb.build("rl-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["pj_total"] == 1


def test_build_only_counts_llm_judge_channel(tmp_path: Path):
    # backlog は llm_judge チャネル（#431 のバッチ判定）のみが対象
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    other = WeakSignal("rephrase", {"text": "別チャネル"}, "t", "s", "rl-anything")
    append_signals([other], path=ws)
    res = bb.build("rl-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["pj_total"] == 1


def test_build_excludes_expired_defensively(tmp_path: Path):
    # #442 TTL 連携: read 時に expired フィールドがあれば除外する（浅い防御的読み）
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1), _sig("もう古い話だ", 2)], path=ws)
    recs = [json.loads(x) for x in ws.read_text(encoding="utf-8").splitlines() if x.strip()]
    recs[1]["expired"] = True
    ws.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8"
    )
    res = bb.build("rl-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["pj_total"] == 1


# ─────────────────────────────────────────────────────────────────
# build: marker 立ち後 → is_bootstrap=False で即返す
# ─────────────────────────────────────────────────────────────────
def test_build_returns_false_when_marker_present(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1), _sig("書き直し", 2)], path=ws)
    marker = _marker(tmp_path)
    marker.write_text("", encoding="utf-8")

    res = bb.build("rl-anything", weak_signals_path=ws, marker_path=marker)
    assert res["is_bootstrap"] is False
    # 早期 return: 重い group 化はしない（groups は空でよい）
    assert res["groups"] == []
    assert res["groups_total"] == 0


# ─────────────────────────────────────────────────────────────────
# mark_done: dry-run ゲート貫通
# ─────────────────────────────────────────────────────────────────
def test_mark_done_writes_marker(tmp_path: Path):
    marker = _marker(tmp_path)
    res = bb.mark_done("rl-anything", marker_path=marker, dry_run=False)
    assert res["written"] is True
    assert marker.exists()


def test_mark_done_dry_run_no_write(tmp_path: Path):
    marker = _marker(tmp_path)
    res = bb.mark_done("rl-anything", marker_path=marker, dry_run=True)
    assert res["written"] is False
    assert res["dry_run"] is True
    assert not marker.exists()  # 最下層まで dry-run ゲート貫通


def test_build_dry_run_does_not_write_marker(tmp_path: Path):
    # build 自体は marker を書かない（読み取りのみ）。dry_run を伝播するだけ。
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    marker = _marker(tmp_path)
    res = bb.build("rl-anything", weak_signals_path=ws, marker_path=marker, dry_run=True)
    assert res["dry_run"] is True
    assert not marker.exists()


# ─────────────────────────────────────────────────────────────────
# marker パスの slug スコープ
# ─────────────────────────────────────────────────────────────────
def test_default_marker_path_includes_slug(tmp_path: Path):
    p = bb.default_marker_path("my-pj", base=tmp_path)
    assert p.name == "bootstrap_done-my-pj.marker"
    assert p.parent == tmp_path
