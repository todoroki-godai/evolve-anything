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


def _sig(
    text: str,
    line_no: int,
    pj_slug: str = "evolve-anything",
    detected_at: str = "2026-06-10T00:00:00+00:00",
    **prov_extra,
) -> WeakSignal:
    prov = {"source_path": "/a.jsonl", "line_no": line_no, "text": text, "reason": "r"}
    prov.update(prov_extra)
    return WeakSignal(
        channel="llm_judge",
        provenance=prov,
        detected_at=detected_at,
        session_id="s1",
        pj_slug=pj_slug,
    )


def _marker(tmp_path: Path) -> Path:
    return tmp_path / "bootstrap_done-evolve-anything.marker"


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

    res = bb.build("evolve-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
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
            _sig("金額がきれてる", 1, pj_slug="evolve-anything"),
            _sig("別件です", 2, pj_slug="figma-to-code"),
            _sig("別件2の話", 3, pj_slug="figma-to-code"),
        ],
        path=ws,
    )
    res = bb.build("evolve-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["pj_total"] == 1  # evolve-anything の 1 件のみ


def test_build_only_counts_unpromoted(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    promoted = _sig("もう昇格済み", 2)
    promoted.promoted = True
    append_signals([promoted], path=ws)
    res = bb.build("evolve-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["pj_total"] == 1


def test_build_counts_content_rich_excludes_content_poor(tmp_path: Path):
    # #99: backlog は content-rich（llm_judge + rephrase + permission_deny）が対象。
    # content-poor（esc_interrupt / manual_edit_after_ai）は detector 文脈未保存ゆえ除外。
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    rephrase = WeakSignal("rephrase", {"text": "言い直し"}, "t", "s", "evolve-anything")
    deny = WeakSignal(
        "permission_deny",
        {"tool_name": "Bash", "tool_input_summary": "git push"},
        "t", "s", "evolve-anything",
    )
    esc = WeakSignal(
        "esc_interrupt", {"evidence": "[Request interrupted]"}, "t", "s", "evolve-anything"
    )
    append_signals([rephrase, deny, esc], path=ws)
    res = bb.build("evolve-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    # llm_judge + rephrase + permission_deny = 3（esc は除外）。
    assert res["pj_total"] == 3


def test_build_excludes_expired_defensively(tmp_path: Path):
    # #442 TTL 連携: read 時に expired フィールドがあれば除外する（浅い防御的読み）
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1), _sig("もう古い話だ", 2)], path=ws)
    recs = [json.loads(x) for x in ws.read_text(encoding="utf-8").splitlines() if x.strip()]
    recs[1]["expired"] = True
    ws.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8"
    )
    res = bb.build("evolve-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["pj_total"] == 1


# ─────────────────────────────────────────────────────────────────
# build: marker 立ち後 → is_bootstrap=False で即返す
# ─────────────────────────────────────────────────────────────────
def test_build_returns_false_when_marker_present(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1), _sig("書き直し", 2)], path=ws)
    marker = _marker(tmp_path)
    marker.write_text("", encoding="utf-8")

    res = bb.build("evolve-anything", weak_signals_path=ws, marker_path=marker)
    assert res["is_bootstrap"] is False
    # 早期 return: 重い group 化はしない（groups は空でよい）
    assert res["groups"] == []
    assert res["groups_total"] == 0


# ─────────────────────────────────────────────────────────────────
# mark_done: dry-run ゲート貫通
# ─────────────────────────────────────────────────────────────────
def test_mark_done_writes_marker(tmp_path: Path):
    marker = _marker(tmp_path)
    res = bb.mark_done("evolve-anything", marker_path=marker, dry_run=False)
    assert res["written"] is True
    assert marker.exists()


def test_mark_done_dry_run_no_write(tmp_path: Path):
    marker = _marker(tmp_path)
    res = bb.mark_done("evolve-anything", marker_path=marker, dry_run=True)
    assert res["written"] is False
    assert res["dry_run"] is True
    assert not marker.exists()  # 最下層まで dry-run ゲート貫通


def test_build_dry_run_does_not_write_marker(tmp_path: Path):
    # build 自体は marker を書かない（読み取りのみ）。dry_run を伝播するだけ。
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    marker = _marker(tmp_path)
    res = bb.build("evolve-anything", weak_signals_path=ws, marker_path=marker, dry_run=True)
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


def _many_short_jp_groups(n: int):
    """短い日本語発話断片を多数（#568 の実データを模した）作る。

    実コーパス（figma-to-code 108 groups）では各発話が固有名詞中心で共通語彙が
    薄く、word-level TF-IDF だとほぼ全 group が別バケットに割れた（108→48）。
    ここでは固有トークンを散りばめた短文を生成し、char n-gram + 上限ガードで
    AskUserQuestion に畳める規模（≤ MAX_THEME_BUCKETS）まで圧縮できることを検証する。
    """
    # 各発話に固有の語を混ぜて jaccard merge を回避し group 数 = n にする。
    nouns = [
        "フッター", "余白", "見出し", "配色", "枠線", "ボタン", "リンク", "画像",
        "金額", "請求", "明細", "目次", "経費", "口座", "残高", "税率",
        "派遣", "探検", "詳細", "情報量", "違和感", "メンバー", "通知", "履歴",
    ]
    verbs = ["を直して", "がおかしい", "を変えて", "が気になる", "を調整して", "が切れてる"]
    groups = []
    for i in range(n):
        noun = nouns[i % len(nouns)]
        verb = verbs[i % len(verbs)]
        rep = f"{noun}{verb}{i}番"
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


def test_max_theme_buckets_constant_defined():
    # #568: バケット数の上限ガード定数が定義されている。
    assert isinstance(bb.MAX_THEME_BUCKETS, int)
    assert bb.MAX_THEME_BUCKETS > 0


def test_cluster_groups_caps_short_jp_fragments():
    # #568 root cause: 短い日本語断片が多数（40+）あると word-level TF-IDF では
    # ほぼ畳めなかった（実コーパス 108→48）。char n-gram + 上限ガードで
    # AskUserQuestion に畳める規模（≤ MAX_THEME_BUCKETS）まで圧縮できること。
    groups = _many_short_jp_groups(48)
    buckets = bb.cluster_groups(groups)
    assert len(buckets) <= bb.MAX_THEME_BUCKETS, (
        f"短い日本語断片 48 group が {len(buckets)} バケットに割れた"
        f"（上限 {bb.MAX_THEME_BUCKETS} 以下に畳むべき）"
    )
    # 取りこぼし無し: 全 group がいずれかのバケットに 1 回入る。
    covered = sorted(i for b in buckets for i in b["group_indices"])
    assert covered == list(range(len(groups)))


def test_cluster_groups_short_jp_deterministic():
    # #568: 短文の上限ガード経路でも決定論（同入力 → 同バケット割当）。
    groups = _many_short_jp_groups(48)
    a = bb.cluster_groups(groups)
    b = bb.cluster_groups(groups)
    assert a == b


def test_cluster_groups_single_bucket_without_sklearn(monkeypatch):
    # #568: sklearn 不在を模した経路では graceful degradation で単一バケット。
    # cluster_groups 内の char TF-IDF 構築を ImportError 相当に潰す。
    import correction_semantic.bootstrap_backlog as _bb

    def _boom(*a, **k):
        raise ImportError("no sklearn")

    monkeypatch.setattr(_bb, "_build_char_tfidf", _boom)
    groups = _many_short_jp_groups(48)
    buckets = _bb.cluster_groups(groups)
    assert len(buckets) == 1
    assert sorted(buckets[0]["group_indices"]) == list(range(len(groups)))


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


def _representative_excerpt_present(bucket) -> bool:
    """theme_label に当該バケット代表シグナルの冒頭抜粋が含まれるか。"""
    label = bucket["theme_label"]
    for g in bucket["groups"]:
        rep = (g.get("representative") or "").strip()
        if not rep:
            continue
        # 代表の冒頭 N 文字（小さめ）が label に現れていれば抜粋併記済み。
        head = rep[: min(8, len(rep))]
        if head and head in label:
            return True
    return False


def test_cluster_groups_theme_label_includes_representative_excerpt():
    # #21: 日本語シグナルでは char n-gram の centroid 上位が「、、/って/んだ」のような
    # 意味をなさない断片列になり、theme_label 単独ではバケット選択の手がかりにならない。
    # representative の冒頭抜粋を併記し、人間が選べるラベルにする（option b）。
    groups = _many_short_jp_groups(48)
    buckets = bb.cluster_groups(groups)
    for b in buckets:
        assert _representative_excerpt_present(b), (
            f"theme_label に代表シグナル抜粋が含まれない: {b['theme_label']!r}"
        )


def test_cluster_groups_theme_label_excerpt_in_single_bucket():
    # graceful degradation（単一バケット）でも代表抜粋が併記される。
    import correction_semantic.bootstrap_backlog as _bb

    def _boom(*a, **k):
        raise ImportError("no sklearn")

    _orig = _bb._build_char_tfidf
    try:
        _bb._build_char_tfidf = _boom
        groups = _many_short_jp_groups(48)
        buckets = _bb.cluster_groups(groups)
    finally:
        _bb._build_char_tfidf = _orig
    assert len(buckets) == 1
    assert _representative_excerpt_present(buckets[0])


def test_cluster_groups_theme_label_excerpt_deterministic():
    # #21: 抜粋併記後も決定論（同入力 → 同 label）。
    groups = _many_short_jp_groups(48)
    a = bb.cluster_groups(groups)
    b = bb.cluster_groups(groups)
    assert [x["theme_label"] for x in a] == [x["theme_label"] for x in b]


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
    res = bb.build("evolve-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
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
    res = bb.build("evolve-anything", weak_signals_path=ws, marker_path=_marker(tmp_path))
    assert res["is_bootstrap"] is True
    assert res["groups_total"] >= bb.THEME_CLUSTER_THRESHOLD
    buckets = res.get("theme_buckets")
    assert buckets, "閾値超なら theme_buckets が emit されるべき"
    # 取りこぼし無し
    covered = sorted(i for b in buckets for i in b["group_indices"])
    assert covered == list(range(res["groups_total"]))


# ─────────────────────────────────────────────────────────────────
# bootstrap_done_at: marker 完了時刻の read 時導出（#94）
# ─────────────────────────────────────────────────────────────────
def test_mark_done_writes_iso_timestamp(tmp_path: Path):
    # mark_done は marker に bootstrap 完了 ISO8601 時刻（tz-aware）を書く（空でない）。
    from datetime import datetime

    marker = _marker(tmp_path)
    bb.mark_done("evolve-anything", marker_path=marker, dry_run=False)
    content = marker.read_text(encoding="utf-8").strip()
    dt = datetime.fromisoformat(content)
    assert dt.tzinfo is not None


def test_bootstrap_done_at_none_when_no_marker(tmp_path: Path):
    assert bb.bootstrap_done_at("evolve-anything", marker_path=_marker(tmp_path)) is None


def test_bootstrap_done_at_parses_iso_content(tmp_path: Path):
    marker = _marker(tmp_path)
    marker.write_text("2026-06-25T16:32:00+00:00", encoding="utf-8")
    dt = bb.bootstrap_done_at("evolve-anything", marker_path=marker)
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 6, 25)
    assert dt.tzinfo is not None


def test_bootstrap_done_at_mtime_fallback_for_empty_marker(tmp_path: Path):
    # 旧形式の空 marker（mark_done 改修前）は mtime にフォールバック（後方互換）。
    marker = _marker(tmp_path)
    marker.write_text("", encoding="utf-8")
    dt = bb.bootstrap_done_at("evolve-anything", marker_path=marker)
    assert dt is not None
    assert dt.tzinfo is not None  # aware UTC


def test_bootstrap_done_at_roundtrip(tmp_path: Path):
    # mark_done → bootstrap_done_at が同じ時刻を読み戻せる（書込↔読出の単一契約）。
    marker = _marker(tmp_path)
    bb.mark_done("evolve-anything", marker_path=marker, dry_run=False)
    dt = bb.bootstrap_done_at("evolve-anything", marker_path=marker)
    assert dt is not None and dt.tzinfo is not None
