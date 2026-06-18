"""weak_signals observability builder + store_registry 宣言のテスト（#432）。

決定論・LLM 非依存。silence != evaluated と store_registry の契約緑を検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import weak_signals.store as ws_store  # noqa: E402
from audit.sections_weak_signals import build_weak_signals_section  # noqa: E402


def _seed(tmp_path: Path, monkeypatch, records: list) -> Path:
    """default_store_path を tmp に向け、records を書く。"""
    store = tmp_path / "weak_signals.jsonl"
    if records:
        import json
        with open(store, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    monkeypatch.setattr(ws_store, "default_store_path", lambda base=None: store)
    return store


def test_builder_silent_when_store_empty(tmp_path: Path, monkeypatch) -> None:
    """store が空（レコード 0）なら None（まだ何も検出していない＝沈黙）。"""
    _seed(tmp_path, monkeypatch, [])
    assert build_weak_signals_section(tmp_path) is None


def test_builder_surfaces_channel_counts(tmp_path: Path, monkeypatch) -> None:
    """レコードありならチャネル別件数 + 未昇格数を surface。"""
    _seed(tmp_path, monkeypatch, [
        {"channel": "rephrase", "promoted": False},
        {"channel": "rephrase", "promoted": True},
        {"channel": "esc_interrupt", "promoted": False},
        {"channel": "permission_deny", "promoted": False},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "4 件" in body
    # #528-2: matrix 1 行ずつ（全PJ N / 当PJ未昇格 M）
    assert "言い直し（rephrase）: 全PJ 2" in body
    assert "Esc 中断（esc_interrupt）: 全PJ 1" in body
    assert "当PJ未昇格 3" in body


def test_builder_labels_counts_as_all_projects(tmp_path: Path, monkeypatch) -> None:
    """weak_signals 件数はグローバル集計なので (全PJ) ラベルを付ける（#476-2）。

    bootstrap の pj_total は (当PJ) 集計でラベルなしのため、桁が違って見える混乱を防ぐ。
    """
    _seed(tmp_path, monkeypatch, [
        {"channel": "rephrase", "promoted": False, "pj_slug": "a"},
        {"channel": "rephrase", "promoted": False, "pj_slug": "b"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "全PJ" in body


def test_builder_includes_evolve_hint(tmp_path: Path, monkeypatch) -> None:
    """llm_judge 未昇格ありなら 'evolve' と '今日の修正確認' の誘導行が出る（#444, #562）。

    #562: daily_review phase は llm_judge チャネルのみ対象。evolve 誘導は
    llm_judge 未読があるときだけ出る。決定論チャネルのみでは出ない。
    """
    _seed(tmp_path, monkeypatch, [
        {"channel": "llm_judge", "promoted": False},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    # ADR-028 / issue #444: markdown と構造化経路の両方に同じ行が出る単一ソース契約
    assert "evolve" in body
    assert "今日の修正確認" in body


# ── #490: 当PJフィルタ（unpromoted / by_channel / 昇格導線文） ───────────

def _seed_multi_pj(tmp_path: Path, monkeypatch, records: list) -> Path:
    """複数PJのレコードを含む weak_signals ストアを tmp に設置する。"""
    store = tmp_path / "weak_signals.jsonl"
    import json
    with open(store, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    monkeypatch.setattr(ws_store, "default_store_path", lambda base=None: store)
    return store


def test_unpromoted_count_is_scoped_to_current_pj(tmp_path: Path, monkeypatch) -> None:
    """未昇格件数は当PJ（project_dir の slug）のみを数える（#490）。

    全PJ合計 5 件のうち当PJ未昇格が 2 件の場合、昇格導線文は「当PJ未昇格 2 件」と出る。
    全PJ集計の 5 件（total）は total 表示に残す。
    """
    current_slug = tmp_path.name  # pj_slug_fast(tmp_path) と一致

    _seed_multi_pj(tmp_path, monkeypatch, [
        # 当PJ: 未昇格 2 件
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug},
        {"channel": "esc_interrupt", "promoted": False, "pj_slug": current_slug},
        # 他PJ: 未昇格 3 件（count に含めない）
        {"channel": "rephrase", "promoted": False, "pj_slug": "other-pj-a"},
        {"channel": "rephrase", "promoted": False, "pj_slug": "other-pj-b"},
        {"channel": "permission_deny", "promoted": False, "pj_slug": "other-pj-b"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)

    # total は全PJ集計（5件）のまま残す
    assert "5 件" in body
    assert "全PJ" in body

    # 昇格導線文は当PJのみ（2件）
    assert "当PJ未昇格 2" in body or ("当PJ" in body and "2 件" in body)
    # 全PJ合計の未昇格数（5件）が昇格導線文に出ていないこと
    # （total と区別できる数字）
    assert "未昇格 5" not in body


def test_by_channel_counts_scoped_to_current_pj(tmp_path: Path, monkeypatch) -> None:
    """チャネル別内訳は当PJのみで集計する（#490）。

    全PJ: rephrase 3件（当PJ1、他PJ2）。チャネル別では「言い直し 1」と表示する。
    """
    current_slug = tmp_path.name

    _seed_multi_pj(tmp_path, monkeypatch, [
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug},
        {"channel": "rephrase", "promoted": False, "pj_slug": "other-pj"},
        {"channel": "rephrase", "promoted": True, "pj_slug": "other-pj"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)

    # total は全PJ集計（3件）
    assert "3 件" in body

    # #528-2 matrix: 当PJ未昇格は 1（rephrase 当PJ1未昇格）、全PJ は 3
    assert "言い直し（rephrase）: 全PJ 3 / 当PJ未昇格 1" in body


def test_evolve_hint_absent_when_no_current_pj_unpromoted(tmp_path: Path, monkeypatch) -> None:
    """当PJに未昇格がゼロなら昇格導線文が出ない（他PJに未昇格があっても）（#490）。"""
    current_slug = tmp_path.name

    _seed_multi_pj(tmp_path, monkeypatch, [
        # 当PJ: 全件昇格済み
        {"channel": "rephrase", "promoted": True, "pj_slug": current_slug},
        # 他PJ: 未昇格あり
        {"channel": "rephrase", "promoted": False, "pj_slug": "other-pj"},
        {"channel": "esc_interrupt", "promoted": False, "pj_slug": "other-pj"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)

    # total は全PJ集計（3件）で表示される
    assert "3 件" in body
    # 当PJ未昇格ゼロ → 昇格導線文なし
    assert "今日の修正確認" not in body


def test_builder_registered_in_observability_contract() -> None:
    from audit.observability import _OBSERVABILITY_BUILDERS

    keys = [k for k, _ in _OBSERVABILITY_BUILDERS]
    assert "weak_signals" in keys


# ── #528-2: チャネル別×スコープ matrix ───────────────────────────────


def test_channel_scope_matrix_one_line_each(tmp_path: Path, monkeypatch) -> None:
    """チャネル別×スコープ（全PJ / 当PJ未昇格）を 1 行ずつ matrix 表示する（#528-2）。

    「347 件（全PJ集計）（llm_judge 6）。うち当PJ未昇格 6 件」が初見で読めない問題を、
    `<channel>: 全PJ N / 当PJ未昇格 M` の matrix 1 行ずつに分解する。
    """
    current_slug = tmp_path.name
    _seed_multi_pj(tmp_path, monkeypatch, [
        # 当PJ: llm_judge 未昇格 2 / 昇格済み 1
        {"channel": "llm_judge", "promoted": False, "pj_slug": current_slug, "signal_key": "a"},
        {"channel": "llm_judge", "promoted": False, "pj_slug": current_slug, "signal_key": "b"},
        {"channel": "llm_judge", "promoted": True, "pj_slug": current_slug, "signal_key": "c"},
        # 他PJ: llm_judge 4 件（全PJ集計の母数に入る）
        {"channel": "llm_judge", "promoted": False, "pj_slug": "other", "signal_key": "d"},
        {"channel": "llm_judge", "promoted": False, "pj_slug": "other", "signal_key": "e"},
        {"channel": "llm_judge", "promoted": False, "pj_slug": "other", "signal_key": "f"},
        {"channel": "llm_judge", "promoted": True, "pj_slug": "other", "signal_key": "g"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    # matrix 行: 全PJ 7 / 当PJ未昇格 2
    assert "llm_judge" in body
    assert "全PJ 7" in body
    assert "当PJ未昇格 2" in body


# ── #525-1: 未昇格と未読の分離 ───────────────────────────────────────


def test_unpromoted_splits_unread_via_seen_store(tmp_path: Path, monkeypatch) -> None:
    """昇格導線文が「未昇格 N 件（うち未読 M 件）」と既読を分離する（#525-1）。

    daily phase「新規なし（既読済）」と weak_signals「未昇格 N 件は昇格可能」の
    噛み合わなさを、既読ストアと突合した未読数を併記して解消する。
    """
    import correction_semantic.daily_review as dr
    current_slug = tmp_path.name
    seen_path = tmp_path / "correction_review_seen.jsonl"
    import json
    # 当PJ未昇格 3 件のうち 2 件は既読
    with open(seen_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"key": "k1"}) + "\n")
        f.write(json.dumps({"key": "k2"}) + "\n")
    monkeypatch.setattr(dr, "default_seen_path", lambda base=None: seen_path)

    _seed_multi_pj(tmp_path, monkeypatch, [
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug, "signal_key": "k1"},
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug, "signal_key": "k2"},
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug, "signal_key": "k3"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "当PJ未昇格 3" in body
    assert "未読 1" in body


def test_unread_count_robust_when_seen_store_missing(tmp_path: Path, monkeypatch) -> None:
    """既読ストアが無い / 読めない場合、未読 = 未昇格 にフォールバックする（#525-1）。"""
    current_slug = tmp_path.name
    _seed_multi_pj(tmp_path, monkeypatch, [
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug, "signal_key": "x1"},
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug, "signal_key": "x2"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "当PJ未昇格 2" in body
    assert "未読 2" in body


# ── idiom_dict 自動昇格の surface（安全弁②・ADR-047） ────────────────


def _seed_autopromote(tmp_path: Path, monkeypatch, *, corrections, idioms=None) -> None:
    """weak_signals は空のまま、corrections / correction_idioms を tmp に向ける。"""
    import json

    import correction_semantic.store as cs_store

    ws_store_path = tmp_path / "weak_signals.jsonl"
    # weak_signals を 1 件置いて builder が None で沈黙しないようにする
    with open(ws_store_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"channel": "rephrase", "promoted": False}) + "\n")
    monkeypatch.setattr(ws_store, "default_store_path", lambda base=None: ws_store_path)

    corr_path = tmp_path / "corrections.jsonl"
    with open(corr_path, "w", encoding="utf-8") as f:
        for r in corrections:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # sections_weak_signals が読む corrections パス解決を tmp に向ける
    import audit.sections_weak_signals as sws
    monkeypatch.setattr(sws, "_corrections_path", lambda: corr_path)

    idioms_path = tmp_path / "correction_idioms.jsonl"
    if idioms:
        with open(idioms_path, "w", encoding="utf-8") as f:
            for r in idioms:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    monkeypatch.setattr(cs_store, "default_idioms_path", lambda base=None: idioms_path)


def test_builder_surfaces_idiom_autopromote_count(tmp_path: Path, monkeypatch) -> None:
    """corrections に idiom_dict 昇格があれば N 件 + idiom 一覧を surface する（安全弁②）。"""
    _seed_autopromote(
        tmp_path, monkeypatch,
        corrections=[
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "message": "四国めたんじゃなくて（後置型）", "invalidated": False},
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "message": "緑じゃなくて赤", "invalidated": False},
            {"source": "reflect_confirmed", "message": "別経路"},  # 数えない
        ],
    )
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "idiom_dict" in body and "自動昇格" in body
    assert "2 件" in body


def test_builder_excludes_invalidated_from_autopromote_count(tmp_path: Path, monkeypatch) -> None:
    """invalidated=True（revoke 済み）は自動昇格件数から除外する。"""
    _seed_autopromote(
        tmp_path, monkeypatch,
        corrections=[
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "message": "生きてる", "invalidated": False},
            {"source": "idiom_dict", "promoted_by": "idiom_dict",
             "message": "取り消し済み", "invalidated": True},
        ],
    )
    section = build_weak_signals_section(tmp_path)
    body = "\n".join(section or [])
    assert "1 件" in body


def test_builder_no_autopromote_line_when_none(tmp_path: Path, monkeypatch) -> None:
    """idiom_dict 昇格が 0 件なら自動昇格行は出さない（ノイズを足さない）。"""
    _seed_autopromote(
        tmp_path, monkeypatch,
        corrections=[{"source": "reflect_confirmed", "message": "x"}],
    )
    section = build_weak_signals_section(tmp_path)
    body = "\n".join(section or [])
    assert "自動昇格" not in body


# ── store_registry の宣言契約 ─────────────────────────────────────

def test_weak_signals_declared_in_store_registry() -> None:
    """weak_signals.jsonl が writer/reader/retention 宣言済みであること（#434 事前ゲート）。
    #442 で retention が permanent→ttl(45日) に変更された。
    """
    import store_registry

    decl = store_registry.declaration_for("weak_signals.jsonl")
    assert decl is not None
    assert decl.writer
    assert decl.reader
    assert decl.retention == "ttl"  # #442: TTL 45 日（corrections decay と整合）
    assert decl.ttl_days == 45
    assert decl.writer_locus == "batch"


def test_store_registry_declarations_valid() -> None:
    """宣言自身の整合性が緑であること。"""
    import store_registry

    assert store_registry.validate_declarations() == []


def test_batch_jsonl_excluded_from_stale() -> None:
    """writer_locus=batch の jsonl は stale 突合の除外対象に入る（#432）。"""
    import store_registry

    exempt = store_registry.stale_exempt_names()
    assert "weak_signals.jsonl" in exempt
    # db も従来通り除外対象に残る
    assert "utterances.db" in exempt


def test_contract_drift_does_not_flag_weak_signals_stale() -> None:
    """実プラグインツリーで weak_signals.jsonl が stale 誤検知されないこと（回帰）。"""
    import orphan_store

    drift = orphan_store.detect_store_contract_drift()
    assert "weak_signals.jsonl" not in drift.stale


# ── #562: hint チャネルスコープ修正 ─────────────────────────────────────────


def test_hint_evolve_absent_when_only_deterministic_channels_unread(
    tmp_path: Path, monkeypatch
) -> None:
    """決定論チャネル（manual_edit_after_ai等）のみ未読の場合、
    「今日の修正確認 phase」行が出ないこと（#562）。

    llm_judge 未読 0 件なら daily_review phase は 0 件しか出さず、
    「今日の修正確認 phase で昇格可能」という hint が誤誘導になる。
    """
    current_slug = tmp_path.name
    _seed_multi_pj(tmp_path, monkeypatch, [
        # 決定論チャネルのみ: 当PJ未昇格・未読
        {"channel": "manual_edit_after_ai", "promoted": False, "pj_slug": current_slug, "signal_key": "d1"},
        {"channel": "esc_interrupt", "promoted": False, "pj_slug": current_slug, "signal_key": "d2"},
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug, "signal_key": "d3"},
        # llm_judge は 0 件
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    # 決定論チャネルのみ → evolve の「今日の修正確認 phase」行は出ない
    assert "今日の修正確認" not in body
    # 代わりに reflect 誘導行が出る
    assert "reflect" in body or "--promote-weak" in body


def test_hint_reflect_shown_for_deterministic_unread(
    tmp_path: Path, monkeypatch
) -> None:
    """決定論チャネル未読 M 件は reflect --promote-weak 誘導行が出ること（#562）。"""
    current_slug = tmp_path.name
    _seed_multi_pj(tmp_path, monkeypatch, [
        {"channel": "manual_edit_after_ai", "promoted": False, "pj_slug": current_slug, "signal_key": "d1"},
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug, "signal_key": "d2"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    # 決定論チャネル 2 件 → reflect --promote-weak 誘導
    assert "--promote-weak" in body
    assert "2" in body  # 件数が含まれる


def test_hint_evolve_shown_for_llm_judge_unread(
    tmp_path: Path, monkeypatch
) -> None:
    """llm_judge 未読ありなら「今日の修正確認 phase」行が出ること（#562・従来動作の保持）。"""
    current_slug = tmp_path.name
    _seed_multi_pj(tmp_path, monkeypatch, [
        {"channel": "llm_judge", "promoted": False, "pj_slug": current_slug, "signal_key": "j1"},
        {"channel": "llm_judge", "promoted": False, "pj_slug": current_slug, "signal_key": "j2"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "今日の修正確認" in body
    assert "evolve" in body


def test_hint_evolve_absent_when_llm_judge_unread_is_zero(
    tmp_path: Path, monkeypatch
) -> None:
    """llm_judge 未読 0 件（全員昇格済みまたは既読）なら今日の修正確認行が出ないこと（#562）。"""
    import correction_semantic.daily_review as dr
    current_slug = tmp_path.name
    seen_path = tmp_path / "correction_review_seen.jsonl"
    import json
    # j1 は既読
    with open(seen_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"key": "j1"}) + "\n")
    monkeypatch.setattr(dr, "default_seen_path", lambda base=None: seen_path)

    _seed_multi_pj(tmp_path, monkeypatch, [
        # llm_judge: 未昇格だが既読（= daily phase 対象外）
        {"channel": "llm_judge", "promoted": False, "pj_slug": current_slug, "signal_key": "j1"},
        # 決定論チャネル: 未昇格・未読
        {"channel": "rephrase", "promoted": False, "pj_slug": current_slug, "signal_key": "d1"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    # llm_judge 未読 0 → 今日の修正確認行なし
    assert "今日の修正確認" not in body
    # 決定論チャネル未読あり → reflect 誘導あり
    assert "--promote-weak" in body


def test_hint_both_channels_when_mixed(
    tmp_path: Path, monkeypatch
) -> None:
    """llm_judge と決定論チャネルの両方が未読なら両方の誘導行が出ること（#562）。"""
    current_slug = tmp_path.name
    _seed_multi_pj(tmp_path, monkeypatch, [
        {"channel": "llm_judge", "promoted": False, "pj_slug": current_slug, "signal_key": "j1"},
        {"channel": "manual_edit_after_ai", "promoted": False, "pj_slug": current_slug, "signal_key": "d1"},
    ])
    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    # llm_judge あり → evolve 誘導
    assert "今日の修正確認" in body
    # 決定論チャネルあり → reflect 誘導
    assert "--promote-weak" in body


# ── #583: 過去未読 backlog（daily phase 圏外）の昇格導線を別レーンで surface ──


def _point_daily_seen(tmp_path: Path, monkeypatch, seen_keys: list) -> None:
    """daily_review.default_seen_path を tmp に向け、seen_keys を既読として書く。"""
    import json

    import correction_semantic.daily_review as dr

    seen_path = tmp_path / "correction_review_seen.jsonl"
    with open(seen_path, "w", encoding="utf-8") as f:
        for k in seen_keys:
            f.write(json.dumps({"key": k}) + "\n")
    monkeypatch.setattr(dr, "default_seen_path", lambda base=None: seen_path)


def _set_bootstrap_marker(tmp_path: Path, monkeypatch, *, done: bool) -> None:
    """bootstrap_backlog.default_marker_path を tmp に向け、marker の有無を制御する。"""
    import correction_semantic.bootstrap_backlog as bb

    marker = tmp_path / "bootstrap_done.marker"
    if done:
        marker.write_text("", encoding="utf-8")
    monkeypatch.setattr(bb, "default_marker_path", lambda slug, base=None: marker)


def _llm_judge_recs(slug: str, n: int, *, start: int = 0) -> list:
    """互いに非類似な user 発話を持つ未読 llm_judge レコードを n 件作る。

    daily_review の group 化（内容キーワード jaccard≥0.5）で圧縮されないよう、各レコードに
    固有名詞中心の異なる発話を与える（1 件 = 1 group になる）。provenance を持たせて
    build_review が representative を組めるようにする。
    """
    distinct = [
        "アルファ機能を直して", "ベータ画面の余白", "ガンマ集計のバグ",
        "デルタ認証エラー", "イプシロン描画崩れ", "ゼータ通知設定",
        "イータ検索結果", "シータ並び順修正",
    ]
    out = []
    for i in range(n):
        text = distinct[(start + i) % len(distinct)] + f"ケース{start + i}"
        out.append({
            "channel": "llm_judge",
            "promoted": False,
            "pj_slug": slug,
            "signal_key": f"j{start + i}",
            "provenance": {
                "source_path": f"/x/{start + i}.jsonl",
                "line_no": start + i,
                "text": text,
                "reason": "修正指示",
            },
        })
    return out


def test_backlog_lane_surfaced_when_bootstrap_done_and_daily_overflows(
    tmp_path: Path, monkeypatch
) -> None:
    """bootstrap marker 済み + 未読 llm_judge が daily 上位(max_groups)を超えるとき、
    過去 backlog 全件を reflect --promote-weak で昇格できる別レーン導線を surface する（#583）。

    daily_review.build_review は max_groups=5 でトップ group しか提示せず、bootstrap も
    marker 済みなら何も拾わない。結果、超過分は両 phase から構造的に外れるため、案内文の
    「今日の修正確認 phase で昇格可能」だけだと過去未読分に入口がない。
    """
    current_slug = tmp_path.name
    # 7 件の互いに非類似な未読 llm_judge（daily は上位 5 group のみ → remaining 2）
    _seed_multi_pj(tmp_path, monkeypatch, _llm_judge_recs(current_slug, 7))
    _point_daily_seen(tmp_path, monkeypatch, [])
    _set_bootstrap_marker(tmp_path, monkeypatch, done=True)

    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    # 従来の evolve 誘導は残る（daily が拾う分はある）
    assert "今日の修正確認" in body
    # 過去 backlog 全件の入口（reflect --show-weak-signals / --promote-weak）が別レーンで出る
    assert "--show-weak-signals" in body
    assert "--promote-weak" in body
    # llm_judge チャネルに絞る案内であること（決定論チャネル用の従来 reflect 行と区別）
    assert "llm_judge" in body


def test_backlog_lane_absent_when_bootstrap_pending(
    tmp_path: Path, monkeypatch
) -> None:
    """bootstrap marker 未設定（pending）なら、過去 backlog は bootstrap がまとめて拾うので
    別レーン導線は出さない（誤誘導・重複案内を避ける）（#583）。"""
    current_slug = tmp_path.name
    _seed_multi_pj(tmp_path, monkeypatch, _llm_judge_recs(current_slug, 7))
    _point_daily_seen(tmp_path, monkeypatch, [])
    _set_bootstrap_marker(tmp_path, monkeypatch, done=False)

    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "今日の修正確認" in body
    # bootstrap pending → backlog 別レーンは出さない
    assert "--show-weak-signals" not in body


def test_backlog_lane_absent_when_daily_covers_all(
    tmp_path: Path, monkeypatch
) -> None:
    """bootstrap marker 済みでも未読 llm_judge が daily 上位に収まる（remaining=0）なら、
    過去 backlog の取りこぼしは無いので別レーン導線を出さない（#583）。"""
    current_slug = tmp_path.name
    # 3 件 → daily の max_groups=5 に収まる（remaining=0）
    _seed_multi_pj(tmp_path, monkeypatch, _llm_judge_recs(current_slug, 3))
    _point_daily_seen(tmp_path, monkeypatch, [])
    _set_bootstrap_marker(tmp_path, monkeypatch, done=True)

    section = build_weak_signals_section(tmp_path)
    assert section is not None
    body = "\n".join(section)
    assert "今日の修正確認" in body
    assert "--show-weak-signals" not in body
