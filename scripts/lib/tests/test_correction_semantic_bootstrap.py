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


def test_group_representative_strips_assistant_quote(_=None):
    # #528-3: assistant の過去レポート引用（> ℹ️ …）を strip し user 発話のみを representative に
    sig = _sig("やっぱり、金額表示を直して\n> ℹ️ データ蓄積待ち（ADR-046）", 1)
    groups = bb.group_signals([sig.to_record()])
    assert groups[0]["representative"] == "やっぱり、金額表示を直して"


def test_group_confirmable_idiom_eligible():
    # #527-4: eligible な representative は confirmable_idiom に出る
    sig = _sig("金額表示を赤にしてほしい", 1)  # 12 文字・eligible
    groups = bb.group_signals([sig.to_record()])
    assert groups[0]["confirmable_idiom"] == "金額表示を赤にしてほしい"


def test_group_confirmable_idiom_none_for_overbroad():
    # #527-4: 過汎用 representative（極短）は confirmed 化対象にしない（None）
    sig = _sig("気がする", 1)
    groups = bb.group_signals([sig.to_record()])
    assert groups[0]["confirmable_idiom"] is None


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


# ─────────────────────────────────────────────────────────────────
# テーマクラスタリング（#558・決定論 TF-IDF・LLM 非依存）
# ─────────────────────────────────────────────────────────────────
def _many_groups(n: int):
    """n 個の group dict を作る（representative はテーマ別に keyword を持つ）。

    前半は「金額/表示」系、後半は「コスト/見積もり」系で、TF-IDF クラスタが
    2 テーマ以上に割れるよう内容キーワードを散らす。
    """
    groups = []
    for i in range(n):
        if i % 2 == 0:
            rep = f"金額表示がきれてる{i}"
        else:
            rep = f"コスト見積もりを直して{i}"
        groups.append({
            "representative": rep,
            "signal_keys": [f"k{i}"],
            "size": 1,
            "confirmable_idiom": None,
        })
    return groups


def test_cluster_threshold_constant_defined():
    # 閾値定数が定義されている（根拠コメント付き）。
    assert isinstance(bb.THEME_CLUSTER_THRESHOLD, int)
    assert bb.THEME_CLUSTER_THRESHOLD > 0


def test_cluster_groups_returns_buckets():
    # クラスタ結果は各バケット = {theme_label, group_indices, groups}。
    groups = _many_groups(14)
    buckets = bb.cluster_groups(groups)
    assert isinstance(buckets, list)
    assert len(buckets) >= 1
    for b in buckets:
        assert "theme_label" in b
        assert isinstance(b["theme_label"], str) and b["theme_label"]
        assert "group_indices" in b
        assert "groups" in b
        assert len(b["group_indices"]) == len(b["groups"])
    # 取りこぼし無し: 全 group がいずれかのバケットに 1 回入る。
    covered = sorted(i for b in buckets for i in b["group_indices"])
    assert covered == list(range(len(groups)))


def test_cluster_groups_deterministic():
    # 同入力 → 同出力（決定論）。
    groups = _many_groups(16)
    a = bb.cluster_groups(groups)
    b = bb.cluster_groups(groups)
    assert a == b


def test_cluster_groups_separates_themes():
    # 明確に異なる 2 テーマは別バケットに分かれる。
    groups = _many_groups(14)
    buckets = bb.cluster_groups(groups)
    assert len(buckets) >= 2


# ─────────────────────────────────────────────────────────────────
# build: 閾値超のときだけバケット構造、以下は従来構造（挙動不変）
# ─────────────────────────────────────────────────────────────────
def test_build_no_buckets_below_threshold(tmp_path: Path):
    # group 数 < 閾値 → 従来構造（buckets 無し or None）、挙動は変わらない。
    ws = tmp_path / "weak_signals.jsonl"
    sigs = [_sig(f"金額表示{i}がきれてる", i) for i in range(3)]
    append_signals(sigs, path=ws)
    res = bb.build("rl-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["is_bootstrap"] is True
    # 閾値以下なので buckets は付かない（None）か空。groups は従来どおり残る。
    assert not res.get("theme_buckets")
    assert isinstance(res["groups"], list)


def test_build_buckets_above_threshold(tmp_path: Path):
    # group 数 >= 閾値 → theme_buckets が emit される。
    ws = tmp_path / "weak_signals.jsonl"
    # 各 idiom に固有の漢字 2 字キーワードを与え jaccard で merge されないようにする
    # （group 数 = 件数になり閾値を確実に超える）。テーマは 2 系統に散らす。
    theme_a = ["価格", "費用", "料金", "原価", "予算", "経費", "金額", "代金"]
    theme_b = ["画面", "表示", "配置", "余白", "色彩", "枠線", "書体", "図形"]
    sigs = []
    n = bb.THEME_CLUSTER_THRESHOLD + 4
    for i in range(n):
        if i % 2 == 0:
            kw = theme_a[(i // 2) % len(theme_a)]
            sigs.append(_sig(f"{kw}項目{i}番を直して", i))
        else:
            kw = theme_b[(i // 2) % len(theme_b)]
            sigs.append(_sig(f"{kw}欄{i}番を直して", i))
    append_signals(sigs, path=ws)
    res = bb.build("rl-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["is_bootstrap"] is True
    assert res["groups_total"] >= bb.THEME_CLUSTER_THRESHOLD
    buckets = res.get("theme_buckets")
    assert buckets, "閾値超なら theme_buckets が emit されるべき"
    # 取りこぼし無し
    covered = sorted(i for b in buckets for i in b["group_indices"])
    assert covered == list(range(res["groups_total"]))
