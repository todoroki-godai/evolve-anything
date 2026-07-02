"""correction_semantic.daily_review のテスト（#446 evolve 内「今日の修正確認」phase）。

前回 evolve 以降の新規 weak_signal（channel=llm_judge・未昇格・非expired）を idiom 単位で
group 化し、頻度降順・上位 max_groups を返す決定論 phase を検証する。

検証観点（Acceptance Criteria 逐条対応）:
- 新規 0 件 → eligible=False, groups=[] を emit（常時 emit）。
- 既読集合（correction_review_seen）に含まれる signal_key は除外（= 新規のみ）。
- 「いいえ」相当 decision="rejected" 追記後は再提示しない。
- 既読集合の重複追記が read 側 set 化で無害（冪等性）。
- record_reviewed は dry_run でファイル不変（最下層まで dry-run ゲート貫通）。
- group は頻度（同 idiom の再発回数）降順・max_groups で切り、remaining を返す。
- 別 PJ slug の件数が混入しない（DATA_DIR 全PJ共通 pitfall）。

決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import daily_review as dr  # noqa: E402
from correction_semantic.store import CorrectionIdiom, append_idioms  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402


def _sig(text: str, line_no: int, pj_slug: str = "evolve-anything", **prov_extra) -> WeakSignal:
    prov = {"source_path": "/a.jsonl", "line_no": line_no, "text": text, "reason": "r"}
    prov.update(prov_extra)
    return WeakSignal(
        channel="llm_judge",
        provenance=prov,
        detected_at="2026-06-10T00:00:00+00:00",
        session_id="s1",
        pj_slug=pj_slug,
    )


def _seen(tmp_path: Path) -> Path:
    return tmp_path / "correction_review_seen.jsonl"


# ─────────────────────────────────────────────────────────────────
# 既読ストア（correction_review_seen.jsonl）
# ─────────────────────────────────────────────────────────────────
def test_seen_keys_empty_when_no_file(tmp_path: Path):
    assert dr.read_reviewed_keys(path=_seen(tmp_path)) == set()


def test_record_reviewed_appends_and_reads_back(tmp_path: Path):
    seen = _seen(tmp_path)
    res = dr.record_reviewed(
        ["k1", "k2"], "evolve-anything", decision="promoted", path=seen
    )
    assert res["written"] == 2
    assert res["dry_run"] is False
    assert dr.read_reviewed_keys(path=seen) == {"k1", "k2"}


def test_record_reviewed_dry_run_no_write(tmp_path: Path):
    # 最下層まで dry-run ゲートを貫通（pitfall_dryrun_stateful_store_write）
    seen = _seen(tmp_path)
    res = dr.record_reviewed(
        ["k1"], "evolve-anything", decision="rejected", path=seen, dry_run=True
    )
    assert res["dry_run"] is True
    assert not seen.exists()


def test_record_reviewed_dedup_is_idempotent(tmp_path: Path):
    # 既読集合の重複追記が read 側 set 化で無害（冪等性）
    seen = _seen(tmp_path)
    dr.record_reviewed(["k1"], "evolve-anything", decision="rejected", path=seen)
    dr.record_reviewed(["k1"], "evolve-anything", decision="rejected", path=seen)
    assert dr.read_reviewed_keys(path=seen) == {"k1"}


# ─────────────────────────────────────────────────────────────────
# build_review: 新規 0 件 → eligible=False（常時 emit）
# ─────────────────────────────────────────────────────────────────
def test_build_review_eligible_false_when_no_signals(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    assert res["eligible"] is False
    assert res["groups"] == []
    assert res["remaining"] == 0
    assert res["dry_run"] is False


def test_build_review_eligible_true_with_new_signals(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    assert res["eligible"] is True
    assert len(res["groups"]) == 1
    g = res["groups"][0]
    assert g["channel"] == "llm_judge"
    assert g["signal_keys"]
    assert "text" in g["evidence"]


# ─────────────────────────────────────────────────────────────────
# build_review: 既読集合に含まれる signal_key は除外（= 新規のみ）
# ─────────────────────────────────────────────────────────────────
def test_build_review_excludes_seen_keys(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    seen = _seen(tmp_path)
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    # その 1 件を既読化（rejected）→ 再提示されない
    recs = dr._read_new(  # 内部ヘルパで signal_key を取得
        "evolve-anything", weak_signals_path=ws, seen_keys=set()
    )
    key = recs[0]["signal_key"]
    dr.record_reviewed([key], "evolve-anything", decision="rejected", path=seen)

    res = dr.build_review("evolve-anything", weak_signals_path=ws, seen_path=seen)
    assert res["eligible"] is False
    assert res["groups"] == []


def test_build_review_reviewed_keys_count(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    seen = _seen(tmp_path)
    dr.record_reviewed(["x1", "x2"], "evolve-anything", decision="promoted", path=seen)
    res = dr.build_review("evolve-anything", weak_signals_path=ws, seen_path=seen)
    assert res["reviewed_keys_count"] == 2


# ─────────────────────────────────────────────────────────────────
# build_review: PJ slug スコープ / 未昇格 / channel / expired 除外
# ─────────────────────────────────────────────────────────────────
def test_build_review_scopes_to_pj_slug(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("金額がきれてる", 1, pj_slug="evolve-anything"),
            _sig("別件です", 2, pj_slug="figma-to-code"),
        ],
        path=ws,
    )
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    # evolve-anything の 1 idiom のみ
    total = sum(len(g["signal_keys"]) for g in res["groups"])
    assert total == 1


def test_build_review_excludes_promoted_and_expired(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    fresh = _sig("金額がきれてる", 1)
    promoted = _sig("昇格済み", 2)
    promoted.promoted = True
    expired = _sig("古い話", 3)
    expired.expired = True
    append_signals([fresh, promoted, expired], path=ws)
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    total = sum(len(g["signal_keys"]) for g in res["groups"])
    assert total == 1


def test_build_review_includes_content_rich_excludes_content_poor(tmp_path: Path):
    # #99: content-rich（llm_judge + rephrase + permission_deny）は対象、
    # content-poor（esc_interrupt / manual_edit_after_ai）は detector が文脈未保存ゆえ除外。
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    rephrase = WeakSignal("rephrase", {"text": "言い直し"}, "t", "s", "evolve-anything")
    deny = WeakSignal(
        "permission_deny",
        {"tool_name": "Bash", "tool_input_summary": "git push --force"},
        "t", "s", "evolve-anything",
    )
    esc = WeakSignal(
        "esc_interrupt", {"evidence": "[Request interrupted]"}, "t", "s", "evolve-anything"
    )
    edit = WeakSignal(
        "manual_edit_after_ai", {"evidence": "File has been modified"}, "t", "s",
        "evolve-anything",
    )
    append_signals([rephrase, deny, esc, edit], path=ws)
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    total = sum(len(g["signal_keys"]) for g in res["groups"])
    # llm_judge + rephrase + permission_deny = 3（esc / manual_edit は除外）。
    assert total == 3
    channels = {g["channel"] for g in res["groups"]}
    assert channels == {"llm_judge", "rephrase", "permission_deny"}


def test_build_review_channels_track_review_channels_single_source(tmp_path: Path):
    # #117 seam fence: 昇格入口（build_review）が surface する channel 集合は
    # review_channels.REVIEW_CHANNELS を単一ソースとして追従することを保証する。
    # daily_review._read_new が独自の literal をハードコードして分岐したら（= 昇格入口と
    # audit 導線・reflect の一本化が崩れたら）検出する。全チャネルを流し込み、surface される
    # channel 集合が REVIEW_CHANNELS と厳密一致・CONTENT_POOR と交わらないことを確認する。
    from correction_semantic.review_channels import (
        CONTENT_POOR_CHANNELS,
        REVIEW_CHANNELS,
    )

    # 各 content-rich チャネルに互いに非類似な発話を与え 1 チャネル 1 group にする
    # （keyword jaccard で跨チャネル merge しないよう固有語を使う）。
    text_by_channel = {
        "llm_judge": "認証ルーティングの設定を直す",
        "rephrase": "データベース接続プールの変更",
    }
    ws = tmp_path / "weak_signals.jsonl"
    sigs = []
    line = 0
    for ch in sorted(REVIEW_CHANNELS | CONTENT_POOR_CHANNELS):
        line += 1
        if ch == "permission_deny":
            prov = {"tool_name": "Bash", "tool_input_summary": "git push --force",
                    "line_no": line, "source_path": "/a.jsonl"}
        elif ch in text_by_channel:
            prov = {"text": text_by_channel[ch], "line_no": line, "source_path": "/a.jsonl"}
        else:  # content-poor（周辺文脈なし）
            prov = {"evidence": "[Request interrupted]", "line_no": line, "source_path": "/a.jsonl"}
        sigs.append(WeakSignal(ch, prov, "t", f"s{line}", "evolve-anything"))
    append_signals(sigs, path=ws)
    res = dr.build_review("evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path))
    surfaced = {g["channel"] for g in res["groups"]}
    assert surfaced == set(REVIEW_CHANNELS)
    assert surfaced.isdisjoint(CONTENT_POOR_CHANNELS)


def test_build_review_permission_deny_distinct_commands_not_collapsed(tmp_path: Path):
    # #99 F1: 異なる拒否コマンドは固定 head「…の実行を拒否」で 1 group に潰れず、別 group
    # として個別に y/n 確認できる（旧 extract_keywords では {実行,拒否} で collapse していた）。
    ws = tmp_path / "weak_signals.jsonl"
    push = WeakSignal(
        "permission_deny",
        {"tool_name": "Bash", "tool_input_summary": "git push --force"},
        "t", "s", "evolve-anything",
    )
    rm = WeakSignal(
        "permission_deny",
        {"tool_name": "Bash", "tool_input_summary": "rm -rf /tmp/x"},
        "t", "s", "evolve-anything",
    )
    append_signals([push, rm], path=ws)
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path)
    )
    deny_groups = [g for g in res["groups"] if g["channel"] == "permission_deny"]
    assert len(deny_groups) == 2


# ─────────────────────────────────────────────────────────────────
# build_review: 頻度降順 + max_groups 切り + remaining
# ─────────────────────────────────────────────────────────────────
def test_build_review_orders_by_frequency_and_caps(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    # 「金額」系を 3 件、「カテゴリ」系を 1 件 → group 化後、金額 group が先頭
    append_signals(
        [
            _sig("金額がきれてる", 1),
            _sig("金額の表示がきれてる", 2),
            _sig("金額のずれ", 3),
            _sig("カテゴリの並び", 4),
        ],
        path=ws,
    )
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path), max_groups=1
    )
    assert len(res["groups"]) == 1
    # max_groups=1 で切ったので残り 1 group は remaining
    assert res["remaining"] == 1
    top = res["groups"][0]
    # 頻度降順: 金額 group（3 件）が先頭
    assert top["evidence"]["count"] == 3
    assert len(top["signal_keys"]) == 3


def test_build_review_uses_idiom_dict_representative(tmp_path: Path):
    # 個人辞書（correction_idioms）の idiom と物理キーで突合し代表 idiom を付ける
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    append_idioms(
        [
            CorrectionIdiom(
                idiom="金額表示の見切れ",
                provenance={"source_path": "/a.jsonl", "line_no": 1},
                detected_at="2026-06-10T00:00:00+00:00",
                pj_slug="evolve-anything",
            )
        ],
        path=idioms,
    )
    res = dr.build_review(
        "evolve-anything",
        weak_signals_path=ws,
        idioms_path=idioms,
        seen_path=_seen(tmp_path),
    )
    assert res["groups"][0]["idiom"] == "金額表示の見切れ"


# ─────────────────────────────────────────────────────────────────
# #528-3 / #527-4: representative 品質 + confirmable_idiom 提示
# ─────────────────────────────────────────────────────────────────
def test_build_review_strips_assistant_quote_from_representative(tmp_path: Path):
    # #528-3: assistant の過去レポート引用混入を strip し user 発話のみ representative にする
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [_sig("やっぱり、高だけにして\n> ℹ️ データ蓄積待ち（PJ≥2）", 1)], path=ws
    )
    res = dr.build_review("evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path))
    g = res["groups"][0]
    assert g["representative"] == "やっぱり、高だけにして"
    assert g["evidence"]["text"] == "やっぱり、高だけにして"


def test_build_review_evidence_has_prev_action(tmp_path: Path):
    # #528-3: 直前 AI 行動の 1 行要約を evidence に添える（一行 representative の判読補助）
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [_sig("やっぱり、高だけにして", 1, prev_action="Edit model-routing.md (effort 設定)")],
        path=ws,
    )
    res = dr.build_review("evolve-anything", weak_signals_path=ws, seen_path=_seen(tmp_path))
    assert res["groups"][0]["evidence"]["prev_action"] == "Edit model-routing.md (effort 設定)"


def test_build_review_confirmable_idiom_eligible(tmp_path: Path):
    # #527-4: eligible な matched idiom は confirmable_idiom に出る
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    append_idioms(
        [CorrectionIdiom(
            idiom="金額表示の見切れを直して",  # eligible
            provenance={"source_path": "/a.jsonl", "line_no": 1},
            detected_at="2026-06-10T00:00:00+00:00",
            pj_slug="evolve-anything",
        )],
        path=idioms,
    )
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, idioms_path=idioms, seen_path=_seen(tmp_path)
    )
    assert res["groups"][0]["confirmable_idiom"] == "金額表示の見切れを直して"


def test_build_review_confirmable_idiom_none_for_overbroad(tmp_path: Path):
    # #527-4: 過汎用 matched idiom（極短）は confirmed 化対象にしない（None）
    ws = tmp_path / "weak_signals.jsonl"
    idioms = tmp_path / "correction_idioms.jsonl"
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    append_idioms(
        [CorrectionIdiom(
            idiom="気がする",  # too_short → confirmable にしない
            provenance={"source_path": "/a.jsonl", "line_no": 1},
            detected_at="2026-06-10T00:00:00+00:00",
            pj_slug="evolve-anything",
        )],
        path=idioms,
    )
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, idioms_path=idioms, seen_path=_seen(tmp_path)
    )
    assert res["groups"][0]["confirmable_idiom"] is None


# ─────────────────────────────────────────────────────────────────
# build_review: dry-run ファイル不変
# ─────────────────────────────────────────────────────────────────
def test_build_review_dry_run_no_write(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    seen = _seen(tmp_path)
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    res = dr.build_review(
        "evolve-anything", weak_signals_path=ws, seen_path=seen, dry_run=True
    )
    assert res["dry_run"] is True
    # build は読み取りのみ。既読集合に一切書かない（追記は apply 時のみ）。
    assert not seen.exists()


# ─────────────────────────────────────────────────────────────────
# build_review: bootstrap-pending シグナルを除外（#476-3 二重提示の解消）
# ─────────────────────────────────────────────────────────────────
def test_build_review_excludes_bootstrap_pending_keys(tmp_path: Path):
    """bootstrap が全包含するシグナルは daily から除外する（二重提示の解消・#476-3）。"""
    ws = tmp_path / "weak_signals.jsonl"
    seen = _seen(tmp_path)
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    recs = dr._read_new("evolve-anything", weak_signals_path=ws, seen_keys=set())
    key = recs[0]["signal_key"]

    # bootstrap が当該 signal_key を保持している → daily からは除外される
    res = dr.build_review(
        "evolve-anything",
        weak_signals_path=ws,
        seen_path=seen,
        exclude_signal_keys={key},
    )
    assert res["eligible"] is False
    assert res["groups"] == []


def test_build_review_no_exclusion_when_set_empty(tmp_path: Path):
    """exclude_signal_keys が空（非 bootstrap run）なら従来通り全件提示する（#476-3）。"""
    ws = tmp_path / "weak_signals.jsonl"
    seen = _seen(tmp_path)
    append_signals([_sig("金額がきれてる", 1)], path=ws)
    res = dr.build_review(
        "evolve-anything",
        weak_signals_path=ws,
        seen_path=seen,
        exclude_signal_keys=set(),
    )
    assert res["eligible"] is True
    assert len(res["groups"]) == 1
