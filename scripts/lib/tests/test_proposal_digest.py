"""daily.proposal_digest のテスト（#409 セッション開始時の改善案提示）。

毎朝の digest 生成（決定論・LLM 非依存・read-only）と、SessionStart 提示用の
既読フィルタリングを検証する。

検証観点:
- queue に載っている PJ ごとに ``daily_review.build_review`` の group をそのまま digest 化する
  （新しい group 化ロジックを再発明しない）
- digest 生成は read-only（対象ストアを一切変更しない）
- 同一 idiom テキストが 2 つ以上の異なる PJ に出現する group は global レーンへマージされ、
  per_pj 側から除外される（1 PJ のみの出現は per_pj のまま）
- 1 PJ の digest 生成が例外を投げても他 PJ の digest は失われない（fail-open）
- build_session_proposals は per_pj[pj_slug] + global を結合し、既読 signal_key を含む group を
  除外し、先頭 limit 件だけ返す
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic.store import CorrectionIdiom, append_idioms  # noqa: E402
from daily import proposal_digest as pd  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402

# idiom_eligible（#527: 最小長8文字/日常語stopword無し/文脈固有トークン無し）を満たすテキスト。
# #412 round2 [Must]D の union 照合は idiom フィールド限定になるため、group.idiom を populate
# するテキストはこのゲートを通す必要がある。
ELIGIBLE_IDIOM_TEXT = "設定ファイルのパスを直接指定してください"
# floor（8文字）未満・stopword「いやいや」のみ = idiom_eligible を通らない。
INELIGIBLE_IDIOM_TEXT = "いやいや"


def _sig(text: str, line_no: int, pj_slug: str, session_id: str = "s1") -> WeakSignal:
    # source_path に pj_slug を含める: signal_key は channel+provenance のハッシュなので、
    # 同一 text でも実運用どおり PJ ごとに別 transcript（別 source_path）由来なら別キーになる
    # （同一 source_path/line_no だと append_signals の dedup で片方が握り潰される）。
    prov = {"source_path": f"/{pj_slug}.jsonl", "line_no": line_no, "text": text, "reason": "r"}
    return WeakSignal(
        channel="llm_judge",
        provenance=prov,
        detected_at=datetime.now(timezone.utc).isoformat(),
        session_id=session_id,
        pj_slug=pj_slug,
    )


def _sig_at(text: str, line_no: int, pj_slug: str, days_ago: float, session_id: str = "s1") -> WeakSignal:
    """detected_at を明示指定できる版（composite sort の鮮度キー検証用）。"""
    prov = {"source_path": f"/{pj_slug}.jsonl", "line_no": line_no, "text": text, "reason": "r"}
    return WeakSignal(
        channel="llm_judge",
        provenance=prov,
        detected_at=(datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
        session_id=session_id,
        pj_slug=pj_slug,
    )


def _sig_and_idiom(text: str, line_no: int, pj_slug: str, session_id: str = "s1"):
    """weak_signal + 同一 phys key（source_path:line_no）の correction_idiom を対で作る。

    #412 round2 [Must]D-1: global レーンの union 照合対象は idiom フィールドに限定される
    ため、group.idiom を populate するには correction_idioms.jsonl 側にも
    daily_review._idiom_by_phys が突合できる同一 phys のレコードが要る。
    """
    prov = {"source_path": f"/{pj_slug}.jsonl", "line_no": line_no, "text": text, "reason": "r"}
    detected_at = datetime.now(timezone.utc).isoformat()
    sig = WeakSignal(
        channel="llm_judge", provenance=prov, detected_at=detected_at,
        session_id=session_id, pj_slug=pj_slug,
    )
    idiom = CorrectionIdiom(
        idiom=text, provenance=prov, detected_at=detected_at, pj_slug=pj_slug,
    )
    return sig, idiom


def _queue(*slugs: str) -> list:
    return [{"pj_slug": s} for s in slugs]


# ─────────────────────────────────────────────────────────────────
# build_proposal_digest
# ─────────────────────────────────────────────────────────────────
def test_empty_queue_entries_returns_empty_digest(tmp_path: Path):
    out = pd.build_proposal_digest([], data_dir=tmp_path)
    assert out["per_pj"] == {}
    assert out["global"] == []
    assert "generated_at" in out


def test_pj_without_signals_absent_from_per_pj(tmp_path: Path):
    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert out["per_pj"] == {}


def test_single_pj_group_is_slimmed(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("git statusじゃなくてgit diffを使って", 1, "pj-a")], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    groups = out["per_pj"]["pj-a"]
    assert len(groups) == 1
    g = groups[0]
    assert g["signal_keys"]
    assert g["channel"] == "llm_judge"
    assert g["count"] == 1
    assert "git diff" in g["representative"]
    assert "idiom" in g
    assert "confirmable_idiom" in g
    # #498 要件1: reason（判定理由）も slim group に配線される。
    assert g["reason"] == "r"
    # #504 I4: slim group は prev_action キーを持たない。
    assert "prev_action" not in g


def _sig_no_context(text: str, line_no: int, pj_slug: str, session_id: str = "s1") -> WeakSignal:
    """#498 要件4: reason を持たない llm_judge signal（説明不能な形）。"""
    prov = {"source_path": f"/{pj_slug}.jsonl", "line_no": line_no, "text": text}
    return WeakSignal(
        channel="llm_judge",
        provenance=prov,
        detected_at=datetime.now(timezone.utc).isoformat(),
        session_id=session_id,
        pj_slug=pj_slug,
    )


def test_group_without_reason_is_held_back(tmp_path: Path):
    """#498 要件4: reason が無い llm_judge group は y/n を強行せず保留にする。
    黙って減らさず excluded_context_missing_by_pj に件数を surface する。
    """
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig_no_context("推奨で", 1, "pj-a")], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert out["per_pj"] == {}
    assert out["excluded_context_missing_by_pj"] == {"pj-a": 1}


# 境界試験（1件・陰性試験に数えない）: reason が空白のみは保留側に落ちる。
@pytest.mark.parametrize(
    "blank_reason", ["", " ", "　", "\n", "\t"], ids=["empty", "half", "full", "nl", "tab"],
)
def test_group_with_blank_reason_is_held_back(tmp_path: Path, blank_reason: str):
    ws = tmp_path / "weak_signals.jsonl"
    prov = {"source_path": "/pj-a.jsonl", "line_no": 1, "text": "推奨で", "reason": blank_reason}
    sig = WeakSignal(
        channel="llm_judge", provenance=prov,
        detected_at=datetime.now(timezone.utc).isoformat(), session_id="s1", pj_slug="pj-a",
    )
    append_signals([sig], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert out["per_pj"] == {}
    assert out["excluded_context_missing_by_pj"] == {"pj-a": 1}


def test_group_with_reason_is_kept(tmp_path: Path):
    """reason があれば説明材料として足りる。"""
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("推奨でお願いします", 1, "pj-a")], path=ws)  # _sig は reason="r" を設定

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert len(out["per_pj"]["pj-a"]) == 1
    assert out["excluded_context_missing_by_pj"] == {}


def test_signal_with_prev_action_but_no_reason_is_still_held_back(tmp_path: Path):
    """#504 I1: prev_action は判断材料にならない。provenance に prev_action があっても
    reason が無ければ保留される（旧仕様は prev_action/reason の or 条件で救われていた）。
    """
    ws = tmp_path / "weak_signals.jsonl"
    prov = {
        "source_path": "/pj-a.jsonl", "line_no": 1, "text": "推奨で",
        "prev_action": "Edit foo.py",
    }
    sig = WeakSignal(
        channel="llm_judge", provenance=prov,
        detected_at=datetime.now(timezone.utc).isoformat(), session_id="s1", pj_slug="pj-a",
    )
    append_signals([sig], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert out["per_pj"] == {}
    assert out["excluded_context_missing_by_pj"] == {"pj-a": 1}


def test_permission_deny_group_without_reason_is_kept(tmp_path: Path):
    """permission_deny は signal_text 自体が拒否コマンドを合成済みで、reason が無くても
    常に説明可能とみなす（llm_judge/rephrase とは異なる扱い・#498 要件4）。
    """
    ws = tmp_path / "weak_signals.jsonl"
    prov = {
        "source_path": "/pj-a.jsonl", "line_no": 1,
        "tool_name": "Bash", "tool_input_summary": "rm -rf /",
    }
    sig = WeakSignal(
        channel="permission_deny", provenance=prov,
        detected_at=datetime.now(timezone.utc).isoformat(), session_id="s1", pj_slug="pj-a",
    )
    append_signals([sig], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert len(out["per_pj"]["pj-a"]) == 1
    assert out["excluded_context_missing_by_pj"] == {}


# ─────────────────────────────────────────────────────────────────
# #504: prev_action は判断材料にならない — 表示と説明可否判定から外す
# 不変条件 I1（提示可否）/ I2（taint 不到達）/ I3（表示の等値）/ I4（schema）
# ─────────────────────────────────────────────────────────────────
def test_i1_provenance_prev_action_does_not_affect_session_proposal_bytes(tmp_path: Path):
    """I1: 同一入力（signal_key 固定・provenance ハッシュ差の影響を排除）に
    provenance.prev_action を足した版と足さない版で、build_session_proposals の出力が
    バイト等値である（prev_action は提示可否に一切影響しない）。
    """
    def _proposals_json(has_prev_action: bool) -> str:
        d = tmp_path / ("with_pa" if has_prev_action else "without_pa")
        d.mkdir()
        ws = d / "weak_signals.jsonl"
        prov = {
            "source_path": "/pj-a.jsonl", "line_no": 1,
            "text": "設定ファイルのパスを直接指定してください", "reason": "r",
        }
        if has_prev_action:
            prov["prev_action"] = "Edit foo.py"
        sig = WeakSignal(
            channel="llm_judge", provenance=prov,
            detected_at="2026-08-18T00:00:00+00:00", session_id="s1", pj_slug="pj-a",
            signal_key="fixed-signal-key-for-i1",
        )
        append_signals([sig], path=ws)
        out = pd.build_proposal_digest(_queue("pj-a"), data_dir=d)
        proposals = pd.build_session_proposals({"proposals": out}, "pj-a", seen_keys=set())
        return json.dumps(proposals, ensure_ascii=False, sort_keys=True)

    assert _proposals_json(False) == _proposals_json(True)


def test_i2_sentinel_in_provenance_prev_action_does_not_leak_through_pipeline(tmp_path: Path):
    """I2(a)(b)(c): provenance.prev_action に sentinel を入れて digest を組んでも、
    build_session_proposals の返り値全体・build_proposal_systemmessage・build_proposal_prompt
    のいずれにも sentinel が現れない（taint 不到達）。I2(d)（restore_state の hook JSON 全文）は
    hooks/tests/test_restore_state_session_proposals.py 側で別途検証する。
    """
    sentinel = "ZZPREVACTIONSENTINELZZ"
    ws = tmp_path / "weak_signals.jsonl"
    prov = {
        "source_path": "/pj-a.jsonl", "line_no": 1,
        "text": "設定ファイルのパスを直接指定してください", "reason": "r",
        "prev_action": sentinel,
    }
    sig = WeakSignal(
        channel="llm_judge", provenance=prov,
        detected_at=datetime.now(timezone.utc).isoformat(), session_id="s1", pj_slug="pj-a",
    )
    append_signals([sig], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    proposals = pd.build_session_proposals({"proposals": out}, "pj-a", seen_keys=set())
    assert sentinel not in json.dumps(proposals, ensure_ascii=False)  # I2(a)
    assert sentinel not in pd.build_proposal_systemmessage(proposals)  # I2(b)
    assert sentinel not in pd.build_proposal_prompt(proposals, "pj-a")  # I2(c)


def test_i3_material_lines_golden_equality_ignores_stray_prev_action_key(tmp_path: Path):
    """陽性対照3 + I3: count のみの group の背景行は ``  背景: 1回検知``（リスト等値）。
    同じ group にレガシー（本改修前に生成された digest snapshot 由来の）``prev_action``
    キーを足しても戻り値は変わらない。
    """
    g = {"reason": "", "count": 1}
    assert pd._material_lines(g) == ["  背景: 1回検知"]

    g_with_stray_key = dict(g)
    g_with_stray_key["prev_action"] = "Edit foo.py"
    assert pd._material_lines(g_with_stray_key) == pd._material_lines(g)


def test_digest_does_not_truncate_per_pj_groups(tmp_path: Path):
    """ADR-054 PR2-b（B-c 回帰防止）: digest 生成は PJ ごとに切らず全 group を集める。

    旧実装は ``build_review(max_groups=DEFAULT_MAX_PER_PJ=3)`` で PJ ごとに3件へ切って
    いたため、4件目以降は順位規則をどう変えても候補に入れなかった。digest 側は必ず
    ``max_groups=None`` で呼ぶことを固定する。
    """
    ws = tmp_path / "weak_signals.jsonl"
    # jaccard 分離のため keyword が重ならない題材を使う（extract_keywords は数字を落とすため
    # 「別々の指摘そのN」のような templated 文字列は N によらず同一 group に collapse する）。
    texts = [
        "cdを使わずgitのCオプションで実行して",
        "コミットメッセージに共著者表記を付けないで",
        "テストは先に書いて失敗を確認して",
        "変数名は英語表記に統一して",
        "エラーは握りつぶさずログに出力して",
    ]
    sigs = [_sig(t, i, "pj-a") for i, t in enumerate(texts)]
    append_signals(sigs, path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    # 旧実装なら DEFAULT_MAX_PER_PJ=3 で切られていた。5件全てが digest に載る。
    assert len(out["per_pj"]["pj-a"]) == 5


def test_build_proposal_digest_is_read_only(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("読み取り専用のはず", 1, "pj-a")], path=ws)
    before_files = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}

    pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)

    after_files = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert before_files == after_files


def test_pj_failure_does_not_abort_other_pjs(tmp_path: Path, monkeypatch):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("生きてるPJの指摘", 1, "pj-ok")], path=ws)

    orig = pd._daily_review.build_review

    def _boom(pj_slug, **kwargs):
        if pj_slug == "pj-bad":
            raise RuntimeError("boom")
        return orig(pj_slug, **kwargs)

    monkeypatch.setattr(pd._daily_review, "build_review", _boom)
    out = pd.build_proposal_digest(_queue("pj-bad", "pj-ok"), data_dir=tmp_path)
    assert "pj-bad" not in out["per_pj"]
    assert "pj-ok" in out["per_pj"]


# ─────────────────────────────────────────────────────────────────
# freshness_join_stats（ADR-054 PR2-c: 発話時刻 join 失敗4種の区別・silence != evaluated）
# ─────────────────────────────────────────────────────────────────
def test_freshness_join_stats_db_missing(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1, "pj-a")], path=ws)
    # data_dir に utterances.db を置かない → db_missing。
    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    stats = out["freshness_join_stats"]
    assert stats["db_missing"] == 1
    assert stats["duckdb_missing"] == 0
    assert stats["query_error"] == 0
    # map が丸ごと失敗した場合、全 signal_key が detected_at にフォールバックする。
    assert stats["fallback_to_detected_at"] == 1
    assert stats["key_mismatch"] == 0  # 個別不一致ではなく全体障害


def test_freshness_join_stats_duckdb_missing(tmp_path: Path, monkeypatch):
    from daily import proposal_ranking as _ranking
    from utterance_archive import store as ustore

    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1, "pj-a")], path=ws)
    monkeypatch.setattr(ustore, "HAS_DUCKDB", False)
    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    stats = out["freshness_join_stats"]
    assert stats["duckdb_missing"] == 1
    assert stats["db_missing"] == 0
    assert stats["fallback_to_detected_at"] == 1


def test_freshness_join_stats_query_error(tmp_path: Path, monkeypatch):
    from utterance_archive import query as uquery
    from utterance_archive import store as ustore

    if not ustore.HAS_DUCKDB:
        pytest.skip("DuckDB 未インストール")

    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("金額がきれてる", 1, "pj-a")], path=ws)
    (tmp_path / "utterances.db").write_bytes(b"")  # exists() だけ真にする

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(uquery, "query_utterances_all_projects", _boom)
    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    stats = out["freshness_join_stats"]
    assert stats["query_error"] == 1
    assert stats["fallback_to_detected_at"] == 1


def test_freshness_join_stats_key_mismatch_and_uses_uttered_at(tmp_path: Path):
    from utterance_archive import store as ustore
    from utterance_archive.extractor import Utterance

    if not ustore.HAS_DUCKDB:
        pytest.skip("DuckDB 未インストール")

    ws = tmp_path / "weak_signals.jsonl"
    hit = _sig("金額がきれてる", 1, "pj-a")  # source_path=/pj-a.jsonl line_no=1
    miss = _sig("カテゴリの並び", 2, "pj-a")  # DB に対応行なし
    append_signals([hit, miss], path=ws)

    db = tmp_path / "utterances.db"
    with ustore.connection(db) as con:
        ustore.insert_utterances(con, [
            Utterance("/pj-a.jsonl", 1, "pj-a", "s1", "2026-05-01T00:00:00+00:00",
                      "金額がきれてる", "h1", None, "dialogue", 1),
        ])

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    stats = out["freshness_join_stats"]
    assert stats == {
        "db_missing": 0, "duckdb_missing": 0, "query_error": 0,
        "key_mismatch": 1, "fallback_to_detected_at": 1,
    }
    # hit 側は uttered_at が join で埋まる。
    hit_group = out["per_pj"]["pj-a"][0] if out["per_pj"]["pj-a"][0]["signal_keys"] == [hit.signal_key] else out["per_pj"]["pj-a"][1]
    assert hit_group["signal_meta_by_key"][hit.signal_key]["uttered_at"] == "2026-05-01T00:00:00+00:00"


# ─────────────────────────────────────────────────────────────────
# 実ストア dry-run（PR2-e P8・tacchi 追加要求）
# ─────────────────────────────────────────────────────────────────
@pytest.mark.real_home
def test_real_corpus_top_proposals_have_no_machinery():
    """実ストア dry-run で上位提示に machinery が混入していないことを検査する。

    合成 fixture だけでは §1.1 の発見（朝の候補 300件中 47件=15.7%が委譲メッセージ）を
    再現できない（tacchi 指摘）。read-only（一切書き込まない）。root conftest の autouse
    HOME 隔離は import 時に ``CLAUDE_PLUGIN_DATA`` を tmp dir へ固定する（#420）ため、
    ``real_home`` マーカーだけでは env 経由の DATA_DIR 解決は実 home に戻らない。
    本テストは ``data_dir`` に実 ``~/.claude/evolve-anything`` を**明示**渡すことで env
    解決を経由せず実ストアを読む。実ストアにデータが無い環境ではスキップする。
    """
    from correction_semantic.review_channels import REVIEW_CHANNELS
    from rl_common.detection import is_machinery_prompt
    from weak_signals.store import default_store_path, read_signals

    real_data_dir = Path.home() / ".claude" / "evolve-anything"
    real_weak_signals_path = default_store_path(base=real_data_dir)
    if not real_weak_signals_path.exists():
        pytest.skip("実ストア（weak_signals.jsonl）が存在しない環境")

    recs = read_signals(real_weak_signals_path)  # 明示 path（hermetic・union read しない）
    slugs = sorted({
        r.get("pj_slug") for r in recs
        if r.get("pj_slug") and r.get("channel") in REVIEW_CHANNELS
    })
    if not slugs:
        pytest.skip("実ストアに content-rich weak_signal が無い環境")

    queue_entries = [{"pj_slug": s} for s in slugs]
    digest = pd.build_proposal_digest(queue_entries, data_dir=real_data_dir)

    checked_any = False
    for slug in slugs:
        proposals = pd.build_session_proposals({"proposals": digest}, slug, seen_keys=set())
        for g in proposals:
            checked_any = True
            for text_field in ("representative", "evidence_text"):
                text = g.get(text_field) or ""
                assert not is_machinery_prompt(text), (
                    f"{slug}: machinery leaked into top proposals: {text[:80]!r}"
                )
            for rep in g.get("all_representatives") or []:
                assert not is_machinery_prompt(rep or "")
    if not checked_any:
        pytest.skip("実ストアに未既読の提案候補が無い環境（全既読/全 promoted 済み等）")


def test_global_lane_merges_same_idiom_text_across_two_pjs(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    idioms_path = tmp_path / "correction_idioms.jsonl"
    sig_a, idiom_a = _sig_and_idiom(ELIGIBLE_IDIOM_TEXT, 1, "pj-a")
    sig_b, idiom_b = _sig_and_idiom(ELIGIBLE_IDIOM_TEXT, 1, "pj-b")
    append_signals([sig_a, sig_b], path=ws)
    append_idioms([idiom_a, idiom_b], path=idioms_path)

    out = pd.build_proposal_digest(_queue("pj-a", "pj-b"), data_dir=tmp_path)
    assert len(out["global"]) == 1
    g = out["global"][0]
    assert len(g["signal_keys"]) == 2
    # 2 PJ 分マージ済みなので per_pj 側からは除外される。
    assert "pj-a" not in out["per_pj"]
    assert "pj-b" not in out["per_pj"]


def test_single_pj_occurrence_stays_in_per_pj_not_global(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("1 PJ にしか無い指摘", 1, "pj-a")], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert out["global"] == []
    assert len(out["per_pj"]["pj-a"]) == 1


# ─────────────────────────────────────────────────────────────────
# excluded_machinery_by_pj（codex [Must]1・#443 PR2-a）
# ─────────────────────────────────────────────────────────────────
def test_build_proposal_digest_surfaces_excluded_machinery_by_pj(tmp_path: Path):
    """build_review の excluded_machinery_total/by_channel を per_pj と同じ持ち方
    （{slug: {...}}）で digest 側にも集約する（従来は捨てられ利用者に見えなかった）。
    """
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("生きてる指摘", 1, "pj-a"),
            _sig("<teammate-message>委譲メッセージ本文</teammate-message>", 2, "pj-a"),
        ],
        path=ws,
    )
    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert out["excluded_machinery_by_pj"] == {
        "pj-a": {"total": 1, "by_channel": {"llm_judge": 1}},
    }
    # 除外対象は groups に載らない（machinery は build_review 側で既に除外済み）。
    assert len(out["per_pj"]["pj-a"]) == 1


def test_build_proposal_digest_no_machinery_key_when_none_excluded(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("生きてる指摘", 1, "pj-a")], path=ws)
    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert "pj-a" not in out["excluded_machinery_by_pj"]


def test_build_proposal_digest_surfaces_rephrase_similarity_dedup_by_pj(
    tmp_path: Path, monkeypatch,
):
    """daily review の rephrase dedup 件数を digest 経路でも失わない（#543）。"""
    monkeypatch.setattr(
        pd._daily_review,
        "build_review",
        lambda *_args, **_kwargs: {
            "groups": [],
            "excluded_machinery_total": 0,
            "rephrase_similarity_dedup_count": 2,
        },
    )

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)

    assert out["rephrase_similarity_dedup_by_pj"] == {"pj-a": 2}


def test_global_group_carries_project_paths_from_queue_entries(tmp_path: Path):
    """#412 [Must]4: digest は各 PJ の project_path（queue エントリの絶対パス）を保持する。"""
    ws = tmp_path / "weak_signals.jsonl"
    idioms_path = tmp_path / "correction_idioms.jsonl"
    sig_a, idiom_a = _sig_and_idiom(ELIGIBLE_IDIOM_TEXT, 1, "pj-a")
    sig_b, idiom_b = _sig_and_idiom(ELIGIBLE_IDIOM_TEXT, 1, "pj-b")
    append_signals([sig_a, sig_b], path=ws)
    append_idioms([idiom_a, idiom_b], path=idioms_path)
    queue_entries = [
        {"pj_slug": "pj-a", "project_path": "/abs/pj-a"},
        {"pj_slug": "pj-b", "project_path": "/abs/pj-b"},
    ]
    out = pd.build_proposal_digest(queue_entries, data_dir=tmp_path)
    assert out["project_paths"]["pj-a"] == "/abs/pj-a"
    assert out["project_paths"]["pj-b"] == "/abs/pj-b"
    g = out["global"][0]
    assert set(g["keys_by_pj"]) == {"pj-a", "pj-b"}
    assert len(g["keys_by_pj"]["pj-a"]) == 1
    assert len(g["keys_by_pj"]["pj-b"]) == 1


# ─────────────────────────────────────────────────────────────────
# _extract_global_groups（#412 [Must]3: 連結成分マージ）
# ─────────────────────────────────────────────────────────────────
def _slim(keys, rep=None, idiom=None) -> dict:
    return {
        "signal_keys": list(keys),
        "representative": rep,
        "idiom": idiom,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": len(keys),
        "evidence_text": rep or "",
    }


def test_extract_global_groups_two_pj_same_text_merges():
    per_pj = {
        "pj-a": [_slim(["ka"], idiom=ELIGIBLE_IDIOM_TEXT)],
        "pj-b": [_slim(["kb"], idiom=ELIGIBLE_IDIOM_TEXT)],
    }
    global_groups, remaining = pd._extract_global_groups(per_pj)
    assert len(global_groups) == 1
    assert set(global_groups[0]["signal_keys"]) == {"ka", "kb"}
    assert remaining == {}


def test_extract_global_groups_single_pj_component_stays_per_pj():
    per_pj = {
        "pj-a": [_slim(["ka"], rep="孤立テキスト", idiom=ELIGIBLE_IDIOM_TEXT)],
    }
    global_groups, remaining = pd._extract_global_groups(per_pj)
    assert global_groups == []
    assert remaining == per_pj


# ─────────────────────────────────────────────────────────────────
# _extract_global_groups（#412 round2 [Must]D: 連結成分の暴走防止）
# ─────────────────────────────────────────────────────────────────
def test_extract_global_groups_representative_overlap_alone_does_not_merge():
    """#412 round2 [Must]D-1: union の照合対象は idiom フィールドのみ。representative が
    同一でも idiom が無ければ連結しない（旧実装は representative も照合対象にしていたため、
    短い一般文が多数の案を連結する暴走の原因だった）。
    """
    per_pj = {
        "pj-a": [_slim(["ka"], rep="共通の発話断片")],
        "pj-b": [_slim(["kb"], rep="共通の発話断片")],
    }
    global_groups, remaining = pd._extract_global_groups(per_pj)
    assert global_groups == []
    assert remaining == per_pj


def test_extract_global_groups_ineligible_idiom_text_not_merged():
    """#412 round2 [Must]D-2: idiom_eligible（#527 較正済み FP ガード）を通らない idiom
    （過短・stopword のみ等）は union に使わない。同一テキストが2 PJ に出現しても
    global 化しない。
    """
    per_pj = {
        "pj-a": [_slim(["ka"], idiom=INELIGIBLE_IDIOM_TEXT)],
        "pj-b": [_slim(["kb"], idiom=INELIGIBLE_IDIOM_TEXT)],
    }
    global_groups, remaining = pd._extract_global_groups(per_pj)
    assert global_groups == []
    assert remaining == per_pj


def test_extract_global_groups_component_size_cap_keeps_per_pj():
    """#412 round2 [Must]D-3: 成分サイズが上限（既定 MAX_GLOBAL_COMPONENT_GROUPS）を超えたら
    global 化せず per_pj に残す（安全側 — 人間が個別に見る）。
    """
    per_pj = {
        f"pj-{i}": [_slim([f"k{i}"], idiom=ELIGIBLE_IDIOM_TEXT)]
        for i in range(pd.MAX_GLOBAL_COMPONENT_GROUPS + 1)
    }
    global_groups, remaining = pd._extract_global_groups(per_pj)
    assert global_groups == []
    assert remaining == per_pj


def test_extract_global_groups_component_at_cap_still_merges():
    """成分サイズがちょうど上限のときは通常通りマージされる（境界値）。"""
    per_pj = {
        f"pj-{i}": [_slim([f"k{i}"], idiom=ELIGIBLE_IDIOM_TEXT)]
        for i in range(pd.MAX_GLOBAL_COMPONENT_GROUPS)
    }
    global_groups, remaining = pd._extract_global_groups(per_pj)
    assert len(global_groups) == 1
    assert remaining == {}


def test_extract_global_groups_carries_all_representatives():
    """#412 round2 [Must]D-4: merge 後の group は成分内の全 group の代表文を保持する
    （1件しか見せずに全キーを承認させないための提示材料）。
    """
    per_pj = {
        "pj-a": [_slim(["ka"], rep="代表文A", idiom=ELIGIBLE_IDIOM_TEXT)],
        "pj-b": [_slim(["kb"], rep="代表文B", idiom=ELIGIBLE_IDIOM_TEXT)],
    }
    global_groups, _remaining = pd._extract_global_groups(per_pj)
    assert global_groups[0]["all_representatives"] == ["代表文A", "代表文B"]


def test_extract_global_groups_carries_reps_by_pj():
    """#413: 代表文を PJ 別（``reps_by_pj``）にも保持し、部分処理後の既読差し引きで
    ``keys_by_pj`` と一緒に絞れるようにする。
    """
    per_pj = {
        "pj-a": [_slim(["ka"], rep="代表文A", idiom=ELIGIBLE_IDIOM_TEXT)],
        "pj-b": [_slim(["kb"], rep="代表文B", idiom=ELIGIBLE_IDIOM_TEXT)],
    }
    global_groups, _remaining = pd._extract_global_groups(per_pj)
    assert global_groups[0]["reps_by_pj"] == {"pj-a": ["代表文A"], "pj-b": ["代表文B"]}


# ─────────────────────────────────────────────────────────────────
# build_session_proposals
# ─────────────────────────────────────────────────────────────────
# ADR-054 PR2-b/PR2-c 契約テスト（実パイプライン E2E: build_proposal_digest →
# build_session_proposals）
# ─────────────────────────────────────────────────────────────────
def test_global_lane_reachable_when_per_pj_already_has_limit_unread(tmp_path: Path):
    """B-d 回帰防止: per_pj に既に limit 件（既定2件）の未読 group がある状態でも、
    global group（2 PJ 以上で observed）が early break で握り潰されず提示に到達すること。
    """
    ws = tmp_path / "weak_signals.jsonl"
    idioms_path = tmp_path / "correction_idioms.jsonl"
    sig_a1 = _sig("cdを使わずgitのCオプションで実行して", 1, "pj-a")
    sig_a2 = _sig("コミットメッセージに共著者表記を付けないで", 2, "pj-a")
    sig_g_a, idiom_g_a = _sig_and_idiom(ELIGIBLE_IDIOM_TEXT, 3, "pj-a")
    sig_g_b, idiom_g_b = _sig_and_idiom(ELIGIBLE_IDIOM_TEXT, 1, "pj-b")
    append_signals([sig_a1, sig_a2, sig_g_a, sig_g_b], path=ws)
    append_idioms([idiom_g_a, idiom_g_b], path=idioms_path)

    out = pd.build_proposal_digest(_queue("pj-a", "pj-b"), data_dir=tmp_path)
    assert len(out["per_pj"]["pj-a"]) == 2  # per_pj に既に limit 件（2件）ある
    assert len(out["global"]) == 1          # global group が別途存在する

    proposals = pd.build_session_proposals({"proposals": out}, "pj-a", seen_keys=set())
    all_keys = {k for g in proposals for k in g["signal_keys"]}
    assert sig_g_a.signal_key in all_keys or sig_g_b.signal_key in all_keys


def test_priority_group_reaches_display_even_when_inserted_last(tmp_path: Path):
    """B-c 回帰防止 + composite sort: 挿入順で4番目（最後）でも最も新しい発話なら、
    limit=2 の表示に到達すること（旧実装は max_per_pj=3 で切ってから挿入順の先頭2件しか
    出さなかった）。
    """
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig_at("cdを使わずgitのCオプションで実行して", 1, "pj-a", days_ago=4),
            _sig_at("コミットメッセージに共著者表記を付けないで", 2, "pj-a", days_ago=3),
            _sig_at("テストは先に書いて失敗を確認して", 3, "pj-a", days_ago=2),
            _sig_at("変数名は英語表記に統一して", 4, "pj-a", days_ago=0.01),  # 最新
        ],
        path=ws,
    )
    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)
    assert len(out["per_pj"]["pj-a"]) == 4  # PR2-b: 打ち切られず全件 digest に載る

    proposals = pd.build_session_proposals({"proposals": out}, "pj-a", seen_keys=set())
    assert len(proposals) == 2
    reps = {g["representative"] for g in proposals}
    assert "変数名は英語表記に統一して" in reps  # 最新の発話が limit=2 に含まれる


def test_seen_key_subtraction_changes_order(tmp_path: Path):
    """codex 2巡目 [Nit]: 既読差し引き後の残存キーによって composite sort の順序が変わる
    ケースを固定する。group の代表 count（再発回数）は既読差し引き前後で変わりうるので、
    既読を1件差し引いたことで count タイが崩れ順位が入れ替わることを確認する。
    """
    ws = tmp_path / "weak_signals.jsonl"
    now = datetime.now(timezone.utc)
    # group A: 2件（同一発話が2回検出＝再発回数2）
    sig_a1 = _sig_at("エラーは握りつぶさずログに出力して", 1, "pj-a", days_ago=1)
    sig_a2 = _sig_at("エラーは握りつぶさずログに出力しろ", 2, "pj-a", days_ago=1)
    # group B: 1件（再発回数1）だが group A より新しい
    sig_b1 = _sig_at("変数名は英語表記に統一して", 3, "pj-a", days_ago=0.01)
    append_signals([sig_a1, sig_a2, sig_b1], path=ws)

    out = pd.build_proposal_digest(_queue("pj-a"), data_dir=tmp_path)

    # 既読差し引き前: group A（count=2）がキー2で group B（count=1）より優先される。
    before = pd.build_session_proposals({"proposals": out}, "pj-a", seen_keys=set(), limit=1)
    assert before[0]["count"] == 2 or len(before[0]["signal_keys"]) == 2

    # group A の signal_key を1件既読にすると count=1 に落ち、group B（より新しい・count=1）
    # とタイになりキー3（鮮度）で group B が優先される。
    after = pd.build_session_proposals(
        {"proposals": out}, "pj-a", seen_keys={sig_a1.signal_key}, limit=1,
    )
    assert after[0]["signal_keys"] == [sig_b1.signal_key]


def _group(keys, rep="rep") -> dict:
    return {
        "signal_keys": list(keys),
        "representative": rep,
        "idiom": None,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": 1,
        "evidence_text": rep,
    }


def test_build_session_proposals_returns_empty_for_non_dict_queue_data():
    assert pd.build_session_proposals(None, "pj-a", seen_keys=set()) == []


def test_build_session_proposals_returns_empty_when_no_proposals_key():
    assert pd.build_session_proposals({}, "pj-a", seen_keys=set()) == []


def test_build_session_proposals_combines_per_pj_and_global():
    queue_data = {
        "proposals": {
            "per_pj": {"pj-a": [_group(["k1"])]},
            "global": [_group(["k2"])],
        }
    }
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys=set())
    assert len(out) == 2


def test_build_session_proposals_excludes_other_pj():
    queue_data = {
        "proposals": {
            "per_pj": {"pj-a": [_group(["k1"])], "pj-b": [_group(["k2"])]},
            "global": [],
        }
    }
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys=set())
    assert [g["signal_keys"] for g in out] == [["k1"]]


def test_build_session_proposals_subtracts_seen_key_leaves_other_groups_and_keys():
    """#412 round2 [Must]A: 既読 key は group から差し引くだけで group ごとは消さない。"""
    queue_data = {
        "proposals": {
            "per_pj": {"pj-a": [_group(["k1", "k2"]), _group(["k3"])]},
            "global": [],
        }
    }
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys={"k2"})
    assert [g["signal_keys"] for g in out] == [["k1"], ["k3"]]


def test_build_session_proposals_subtracts_seen_keys_instead_of_dropping_group():
    """#412 round2 [Must]A: group 内の一部 key だけ既読（部分昇格失敗等）でも group ごと
    消さず、既読 key を差し引いた残りで再提示する（残り 0 件の group のみ除外）。
    """
    queue_data = {
        "proposals": {
            "per_pj": {"pj-a": [_group(["k1", "k2"])]},
            "global": [],
        }
    }
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys={"k1"})
    assert len(out) == 1
    assert out[0]["signal_keys"] == ["k2"]


def test_build_session_proposals_drops_group_when_all_keys_seen():
    queue_data = {
        "proposals": {
            "per_pj": {"pj-a": [_group(["k1", "k2"])]},
            "global": [],
        }
    }
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys={"k1", "k2"})
    assert out == []


def test_build_session_proposals_subtracts_seen_keys_in_keys_by_pj():
    """global group の ``keys_by_pj`` からも既読 key を差し引く。"""
    group = {
        "signal_keys": ["ka", "kb"],
        "representative": "共通テキスト",
        "idiom": None,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": 2,
        "evidence_text": "共通テキスト",
        "keys_by_pj": {"pj-a": ["ka"], "pj-b": ["kb"]},
        "origin_pjs": ["pj-a", "pj-b"],
    }
    queue_data = {"proposals": {"per_pj": {}, "global": [group]}}
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys={"ka"})
    assert len(out) == 1
    assert out[0]["signal_keys"] == ["kb"]
    assert out[0]["keys_by_pj"] == {"pj-b": ["kb"]}


def test_build_session_proposals_subtracts_reps_by_pj_for_partial_promotion():
    """#413: global group を部分処理（一部 PJ だけ昇格成功）した後、既読 PJ の代表文が
    表示（``reps_by_pj`` / ``all_representatives``）から除外されること。実行コマンドの
    絞り込み（``keys_by_pj``）と一致させ、「もう答えた案がまた出た」という誤認を防ぐ。
    """
    group = {
        "signal_keys": ["ka", "kb"],
        "representative": "代表文A",
        "idiom": None,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": 2,
        "evidence_text": "代表文A",
        "keys_by_pj": {"pj-a": ["ka"], "pj-b": ["kb"]},
        "origin_pjs": ["pj-a", "pj-b"],
        "reps_by_pj": {"pj-a": ["代表文A"], "pj-b": ["代表文B"]},
        "all_representatives": ["代表文A", "代表文B"],
    }
    queue_data = {"proposals": {"per_pj": {}, "global": [group]}}
    # pj-a のキー(ka)は既読 = pj-a 側は昇格成功済みとして扱う
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys={"ka"})
    assert len(out) == 1
    assert out[0]["signal_keys"] == ["kb"]
    assert out[0]["reps_by_pj"] == {"pj-b": ["代表文B"]}
    assert out[0]["all_representatives"] == ["代表文B"]
    assert "代表文A" not in out[0]["all_representatives"]


def test_build_session_proposals_respects_limit():
    groups = [_group([f"k{i}"]) for i in range(5)]
    queue_data = {"proposals": {"per_pj": {"pj-a": groups}, "global": []}}
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys=set(), limit=2)
    assert len(out) == 2


def test_default_limit_is_max_session_proposals_constant():
    groups = [_group([f"k{i}"]) for i in range(pd.MAX_SESSION_PROPOSALS + 3)]
    queue_data = {"proposals": {"per_pj": {"pj-a": groups}, "global": []}}
    out = pd.build_session_proposals(queue_data, "pj-a", seen_keys=set())
    assert len(out) == pd.MAX_SESSION_PROPOSALS


# ─────────────────────────────────────────────────────────────────
# build_proposal_prompt
# ─────────────────────────────────────────────────────────────────
def test_build_proposal_prompt_contains_representative_and_commands():
    groups = [_group(["k1", "k2"], rep="テスト前にRED実測")]
    msg = pd.build_proposal_prompt(groups, "pj-a")
    assert "テスト前にRED実測" in msg
    assert "AskUserQuestion" in msg
    assert "bin/evolve-reflect --promote-weak k1,k2" in msg
    assert "bin/evolve-reflect --reject-weak k1,k2 --pj pj-a" in msg


def test_build_proposal_prompt_offers_four_way_destination_choice():
    """#541 D: ①②（共通ルール/PJルール）を「ルールに書く」1つへ統合し、空いた枠に
    「既に反映済み」を追加した4択（ルールに書く/いまは反映しない/既に反映済み/いいえ）を
    提示する（#475 §4 の反映先つき4択の再構成）。
    """
    groups = [_group(["k1"], rep="rep")]
    msg = pd.build_proposal_prompt(groups, "pj-a")
    assert "ルールに書く" in msg
    assert "いまは反映しない" in msg
    assert "既に反映済み" in msg
    assert "いいえ" in msg
    assert "はい: " not in msg  # 旧・素の y/n 表記は出さない


def test_build_proposal_prompt_already_reflected_choice_records_without_promoting():
    """#541 D-2 決着（v2 [Must]1）: 「既に反映済み」の実体は record_reviewed のみで
    --promote-weak を呼ばない（#514 の在庫レーンへ再提示バグが引っ越すのを防ぐ）。
    """
    groups = [_group(["k1"], rep="rep")]
    msg = pd.build_proposal_prompt(groups, "pj-a")
    assert "bin/evolve-reflect --already-reflected-weak k1 --pj pj-a" in msg
    # 「既に反映済み」の指示行自体には --promote-weak を含めない（実体を混同させない）。
    already_line = next(
        line for line in msg.splitlines() if "--already-reflected-weak" in line
    )
    assert "--promote-weak" not in already_line


def test_build_proposal_prompt_does_not_overpromise_rule_reflection():
    """#498 要件3: 「ルールに書く」を選んでも、この時点ではまだ反映されていないことを
    明示する（promote は記録のみで反映先ファイルへの書込みは別工程・promote.py の
    reflect_status="promoted" と整合）。
    """
    groups = [_group(["k1"], rep="rep")]
    msg = pd.build_proposal_prompt(groups, "pj-a")
    assert "まだルール文書には反映されていません" in msg
    assert "記録のみ・ルールには反映されません" in msg  # 「いまは反映しない」選択時の説明


def test_build_proposal_prompt_references_established_review_protocol():
    """#498 要件5: draft_line 起草・ファイル追記の手順は再発明せず、既存の
    「反映先つき4択」手順（correction-review.md）を絶対パスで参照する。
    """
    groups = [_group(["k1"], rep="rep")]
    msg = pd.build_proposal_prompt(
        groups, "pj-a", reflect_cmd="/abs/plugin/bin/evolve-reflect",
    )
    assert "/abs/plugin/skills/evolve/references/correction-review.md" in msg
    assert "反映先つき4択" in msg


def test_build_proposal_prompt_includes_reason_and_count_not_prev_action():
    """#498 要件1: 「なぜ拾われたか」「何回起きたか」が読める（陽性対照）。

    #504 I2(b)(c)/I3: ``g`` に本改修前の digest snapshot 由来のレガシー ``prev_action``
    キーが残っていても（鮮度: キャッシュされた古い成果物）、additionalContext・systemMessage
    のどちらにもその内容が漏れないこと（stray key defense — I3/I2 の陰性試験4/5/6 の kill 対象）。
    """
    g = _group(["k1", "k2"], rep="rep")
    g["prev_action"] = "Edit foo.py"  # レガシー snapshot を模した stray key
    g["reason"] = "正しい値を後置で言い直している"
    g["count"] = 2
    msg = pd.build_proposal_prompt([g], "pj-a")
    sysmsg = pd.build_proposal_systemmessage([g])
    assert "正しい値を後置で言い直している" in msg
    assert "2回検知" in msg
    assert "Edit foo.py" not in msg
    assert "Edit foo.py" not in sysmsg


def test_build_proposal_prompt_recorded_content_matches_correction_message_format():
    """#498 要件5「反映されるちょうどの1行」: 記録される内容のプレビューは
    ``correction_semantic.promote._correction_message`` と同じ text（reason）形式。
    """
    from correction_semantic.promote import _build_correction_record

    g = _group(["k1"], rep="評価前にテストを書いて")
    g["reason"] = "順序が逆になっている"
    msg = pd.build_proposal_prompt([g], "pj-a")

    # 実際に promote 時に corrections.jsonl へ書かれる message と完全一致することを検証する
    # （自作の要約でなく実際の記録内容そのもの）。
    rec = {
        "provenance": {"text": "評価前にテストを書いて", "reason": "順序が逆になっている"},
    }
    expected_message = _build_correction_record(rec, "/pj-a")["message"]
    assert f"記録される内容: 「{expected_message}」" in msg


def test_build_proposal_prompt_does_not_leak_channel_jargon():
    """#498 要件2: group の内部 channel 値（llm_judge 等のジャーゴン）をそのまま出さない
    （group には channel="llm_judge" が乗っているが、プレビュー生成はそれを読まない）。
    """
    g = _group(["k1"], rep="rep")
    g["reason"] = "正しい値を後置で言い直している"
    assert g["channel"] == "llm_judge"  # 前提: group には channel 値が乗っている
    msg = pd.build_proposal_prompt([g], "pj-a")
    assert "llm_judge" not in msg
    assert "rephrase" not in msg
    assert "permission_deny" not in msg


def test_build_proposal_prompt_honors_absolute_reflect_cmd():
    """提示先は他 PJ の cwd なので、呼び出し元が渡す絶対パスを埋め込むこと。

    相対 ``bin/evolve-reflect`` のままだと対象 PJ に該当ファイルが無く No such file になる
    （pitfall_skill_md_plugin_root と同型の失敗）。
    """
    groups = [_group(["k1"], rep="rep")]
    msg = pd.build_proposal_prompt(groups, "pj-a", reflect_cmd="/abs/plugin/bin/evolve-reflect")
    assert "/abs/plugin/bin/evolve-reflect --promote-weak k1" in msg
    assert "/abs/plugin/bin/evolve-reflect --reject-weak k1 --pj pj-a" in msg
    assert "\n  はい: bin/evolve-reflect" not in msg


def test_build_proposal_prompt_instructs_after_first_reply_not_interrupt():
    """#412 [Must]1: additionalContext は「ユーザーの最初の応答を終えた直後」に発火させる
    行動指示にする（従来の「依頼が無い場合のみ」だと永久に発火しない）。
    """
    groups = [_group(["k1"], rep="rep")]
    msg = pd.build_proposal_prompt(groups, "pj-a")
    assert "AskUserQuestion" in msg
    assert "最初のメッセージへの応答を終えた直後" in msg
    assert "割り込ま" in msg  # 「ユーザーの依頼より先に割り込むな」系の文言


def test_build_proposal_prompt_global_group_emits_per_origin_pj_commands():
    """#412 [Must]4: global group（keys_by_pj あり）は origin PJ ごとに --project-path/--pj を
    明示したコマンド行を出す。他PJ由来の signal が現在 PJ に誤帰属しないようにするため。
    """
    group = {
        "signal_keys": ["ka", "kb"],
        "representative": "共通テキスト",
        "idiom": None,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": 2,
        "evidence_text": "共通テキスト",
        "keys_by_pj": {"pj-a": ["ka"], "pj-b": ["kb"]},
        "origin_pjs": ["pj-a", "pj-b"],
    }
    project_paths = {"pj-a": "/abs/pj-a", "pj-b": "/abs/pj-b"}
    msg = pd.build_proposal_prompt(
        [group], "pj-a", reflect_cmd="/abs/bin/evolve-reflect", project_paths=project_paths,
    )
    assert "/abs/bin/evolve-reflect --promote-weak ka --project-path /abs/pj-a --pj pj-a" in msg
    assert "/abs/bin/evolve-reflect --reject-weak ka --pj pj-a" in msg
    assert "/abs/bin/evolve-reflect --promote-weak kb --project-path /abs/pj-b --pj pj-b" in msg
    assert "/abs/bin/evolve-reflect --reject-weak kb --pj pj-b" in msg


def test_build_proposal_prompt_lists_all_representatives_for_merged_group():
    """#412 round2 [Must]D-4: all_representatives を持つ group は成分内の全代表文を列挙する。
    「はい」で成分内の全キーを承認する前に、人間が見ていない案まで含まれていないか
    確認できるようにするため。
    """
    group = {
        "signal_keys": ["ka", "kb"],
        "representative": "代表文A",
        "idiom": None,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": 2,
        "evidence_text": "代表文A",
        "keys_by_pj": {"pj-a": ["ka"], "pj-b": ["kb"]},
        "origin_pjs": ["pj-a", "pj-b"],
        "all_representatives": ["代表文A", "代表文B"],
    }
    msg = pd.build_proposal_prompt([group], "pj-a")
    assert "代表文A" in msg
    assert "代表文B" in msg


def test_build_proposal_prompt_quotes_reflect_cmd_with_spaces():
    """#412 round2 [Must]C: reflect_cmd 絶対パスに空白があると argparse が壊れる。shlex.quote する。"""
    import shlex
    groups = [_group(["k1"], rep="rep")]
    reflect_cmd = "/Users/matsukaze takashi/plugin/bin/evolve-reflect"
    msg = pd.build_proposal_prompt(groups, "pj-a", reflect_cmd=reflect_cmd)
    assert shlex.quote(reflect_cmd) in msg
    assert "matsukaze takashi/plugin/bin/evolve-reflect --promote-weak" not in msg


def test_build_proposal_prompt_quotes_project_path_with_spaces():
    """#412 round2 [Must]C: global group の --project-path も quote する。"""
    import shlex
    group = {
        "signal_keys": ["ka"],
        "representative": "共通テキスト",
        "idiom": None,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": 1,
        "evidence_text": "共通テキスト",
        "keys_by_pj": {"pj-a": ["ka"]},
        "origin_pjs": ["pj-a"],
    }
    project_path = "/abs/pj a/with space"
    msg = pd.build_proposal_prompt(
        [group], "pj-a", project_paths={"pj-a": project_path},
    )
    assert f"--project-path {shlex.quote(project_path)}" in msg


def test_build_proposal_prompt_quotes_shell_metacharacter_pj_slug():
    """#412 round2 [Must]C: pj_slug に shell metacharacter が含まれても別コマンドに解釈されない。"""
    import shlex
    groups = [_group(["k1"], rep="rep")]
    pj_slug = "pj; rm -rf /"
    msg = pd.build_proposal_prompt(groups, pj_slug)
    assert f"--pj {shlex.quote(pj_slug)}" in msg


# ─────────────────────────────────────────────────────────────────
# build_proposal_prompt / build_proposal_systemmessage（ADR-054 PR2-d: 判断材料）
# ─────────────────────────────────────────────────────────────────
def _group_with_meta(keys, rep="rep", *, uttered_at=None, detected_at=None,
                      origin_pjs=None, cross_pj_confirmed=None) -> dict:
    g = _group(keys, rep=rep)
    g["signal_meta_by_key"] = {
        k: {"uttered_at": uttered_at, "detected_at": detected_at, "cross_pj": cross_pj_confirmed or []}
        for k in keys
    }
    if origin_pjs is not None:
        g["origin_pjs"] = origin_pjs
    if cross_pj_confirmed is not None:
        g["cross_pj_confirmed"] = cross_pj_confirmed
    return g


def test_build_proposal_prompt_includes_relative_time_and_cross_pj():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(days=21)).isoformat()
    g = _group_with_meta(
        ["k1"], rep="案X", uttered_at=ts, origin_pjs=["pj-a", "amamo", "figma-to-code"],
    )
    msg = pd.build_proposal_prompt([g], "pj-a")
    assert "週間前の発話" in msg
    assert "他2PJでも同種の指摘" in msg
    assert "amamo" in msg and "figma-to-code" in msg
    # channel 名（ジャーゴン）は出さない
    assert "llm_judge" not in msg
    assert "rephrase" not in msg


def test_build_proposal_prompt_confirmed_uses_stronger_wording():
    g = _group_with_meta(["k1"], rep="案Y", detected_at=(datetime.now(timezone.utc)).isoformat(),
                          cross_pj_confirmed=["amamo"])
    msg = pd.build_proposal_prompt([g], "pj-a")
    assert "確認済み" in msg
    assert "amamo" in msg


def test_build_proposal_prompt_silent_when_no_context():
    g = _group(["k1"], rep="案Z")  # signal_meta_by_key 無し・cross_pj/origin_pjs 無し
    msg = pd.build_proposal_prompt([g], "pj-a")
    assert "週間前" not in msg
    assert "PJでも" not in msg
    assert "確認済み" not in msg


def test_build_proposal_systemmessage_includes_top_group_context():
    ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    g = _group_with_meta(["k1"], rep="rep1", detected_at=ts, cross_pj_confirmed=["amamo"])
    msg = pd.build_proposal_systemmessage([g], pj_slug="pj-a")
    assert "日前の発話" in msg
    assert "確認済み" in msg


def test_build_proposal_systemmessage_silent_when_pj_slug_omitted():
    """後方互換: pj_slug 省略時は従来どおり判断材料を付けない。"""
    ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    g = _group_with_meta(["k1"], rep="rep1", detected_at=ts, cross_pj_confirmed=["amamo"])
    msg = pd.build_proposal_systemmessage([g])
    assert "日前の発話" not in msg
    assert "確認済み" not in msg


# ─────────────────────────────────────────────────────────────────
# build_proposal_systemmessage（#412 [Must]1: 2チャネル同時出力）
# ─────────────────────────────────────────────────────────────────
def test_build_proposal_systemmessage_lists_representatives():
    groups = [_group(["k1"], rep="rep1"), _group(["k2"], rep="rep2")]
    msg = pd.build_proposal_systemmessage(groups)
    assert "rep1" in msg
    assert "rep2" in msg
    assert "応答のあとで採否をお聞きします" in msg


def test_build_proposal_systemmessage_wording_does_not_overpromise():
    """#412 round2 [Should]E: 「この後 y/n で確認します」は additionalContext 側の prompt
    instruction 遵守に依存し機械的に保証できない。実際に保証できる内容（応答後に聞く・
    表示されなければ次回また出る）だけを書く。
    """
    msg = pd.build_proposal_systemmessage([])
    assert "応答のあとで採否をお聞きします" in msg
    assert "表示されなかった場合は未処理のまま次回また出ます" in msg
    assert "この後 y/n で確認します" not in msg


def test_build_proposal_systemmessage_appends_pull_instruction():
    """#503 §3.1-5': y/n が来なかったときに利用者が拾い直せる pull 導線を末尾に足す。"""
    groups = [_group(["k1"], rep="rep1")]
    msg = pd.build_proposal_systemmessage(groups)
    assert "改善案を教えて" in msg
    assert msg.endswith("聞かれなければ『改善案を教えて』と言ってください。")


def test_build_proposal_systemmessage_caps_at_max_session_proposals():
    groups = [_group([f"k{i}"], rep=f"rep{i}") for i in range(5)]
    msg = pd.build_proposal_systemmessage(groups)
    assert "rep0" in msg
    assert "rep1" in msg
    assert "rep2" not in msg


def test_build_proposal_systemmessage_appends_machinery_note_when_excluded():
    """codex [Must]1: formatter が excluded_machinery を受け取ったら透明化する（silence != evaluated）。"""
    groups = [_group(["k1"], rep="rep1")]
    msg = pd.build_proposal_systemmessage(groups, excluded_machinery=3)
    assert "machinery" in msg
    assert "3" in msg


def test_build_proposal_systemmessage_silent_when_no_machinery_excluded():
    groups = [_group(["k1"], rep="rep1")]
    msg = pd.build_proposal_systemmessage(groups, excluded_machinery=0)
    assert "machinery" not in msg


def test_build_proposal_systemmessage_appends_context_missing_note_when_excluded():
    """#498 要件4: 保留にした件数を systemMessage にも透明化する（silence != evaluated）。"""
    groups = [_group(["k1"], rep="rep1")]
    msg = pd.build_proposal_systemmessage(groups, excluded_context_missing=2)
    assert "保留" in msg
    assert "2" in msg


def test_build_proposal_systemmessage_silent_when_no_context_missing_excluded():
    groups = [_group(["k1"], rep="rep1")]
    msg = pd.build_proposal_systemmessage(groups, excluded_context_missing=0)
    assert "保留" not in msg


def test_build_proposal_systemmessage_lists_all_representatives_for_merged_group():
    """#412 round2 [Must]D-4: merge 済み group は代表文を1件だけでなく成分内の全代表文を出す。"""
    group = {
        "signal_keys": ["ka", "kb"],
        "representative": "代表文A",
        "idiom": None,
        "confirmable_idiom": None,
        "channel": "llm_judge",
        "count": 2,
        "evidence_text": "代表文A",
        "keys_by_pj": {"pj-a": ["ka"], "pj-b": ["kb"]},
        "origin_pjs": ["pj-a", "pj-b"],
        "all_representatives": ["代表文A", "代表文B"],
    }
    msg = pd.build_proposal_systemmessage([group])
    assert "代表文A" in msg
    assert "代表文B" in msg


# ─────────────────────────────────────────────────────────────────
# #443 codex cold review: global merge 後の signal_meta_by_key union と
# 既読差し引き後の cross_pj 再計算（union 処理を消すと落ちること）
# ─────────────────────────────────────────────────────────────────
def _slim_with_meta(keys, meta, *, cross_pj=None, rep=None, idiom=None) -> dict:
    g = _slim(keys, rep=rep, idiom=idiom)
    g["signal_meta_by_key"] = meta
    g["cross_pj_confirmed"] = list(cross_pj or [])
    return g


def test_extract_global_groups_unions_signal_meta_from_all_pjs():
    """merge 後の group が**両 PJ 由来**の signal_meta_by_key を保持する。

    union を消して「先頭 group の meta だけ」にすると、片方の PJ のキーの発話時刻・
    cross_pj が失われ、既読差し引き後の順位キー再計算ができなくなる（このテストが落ちる）。
    """
    meta_a = {"ka": {"uttered_at": "2026-08-01T00:00:00+00:00", "detected_at": None, "cross_pj": []}}
    meta_b = {"kb": {"uttered_at": "2026-08-10T00:00:00+00:00", "detected_at": None, "cross_pj": ["pj-c"]}}
    per_pj = {
        "pj-a": [_slim_with_meta(["ka"], meta_a, idiom=ELIGIBLE_IDIOM_TEXT)],
        "pj-b": [_slim_with_meta(["kb"], meta_b, cross_pj=["pj-c"], idiom=ELIGIBLE_IDIOM_TEXT)],
    }
    global_groups, _remaining = pd._extract_global_groups(per_pj)

    assert len(global_groups) == 1
    merged_meta = global_groups[0]["signal_meta_by_key"]
    # 両 PJ 由来のキーが残っている（union が効いている）。
    assert set(merged_meta) == {"ka", "kb"}
    assert merged_meta["ka"]["uttered_at"] == "2026-08-01T00:00:00+00:00"
    assert merged_meta["kb"]["cross_pj"] == ["pj-c"]


def test_seen_filter_drops_cross_pj_when_only_confirmed_key_is_read():
    """confirmed 情報を持つキーだけが既読になったら、その group は tier 1 に居座らない。

    codex cold review [Must]: `composite_sort_key` が top-level の `cross_pj_confirmed` を
    見ていると、confirmed 由来キーが既読で落ちた後も tier 1 のままになる。残存キーの
    `cross_pj` の和から再計算していれば、このテストで順位が入れ替わる。
    """
    from daily import proposal_ranking as pr

    # group X: 2キー。confirmed 情報は "kx-confirmed" だけが持つ。
    meta_x = {
        "kx-confirmed": {"uttered_at": "2026-08-01T00:00:00+00:00", "detected_at": None, "cross_pj": ["pj-c"]},
        "kx-plain": {"uttered_at": "2026-08-01T00:00:00+00:00", "detected_at": None, "cross_pj": []},
    }
    group_x = _slim_with_meta(["kx-confirmed", "kx-plain"], meta_x, cross_pj=["pj-c"])
    # group Y: confirmed 無し・より新しい発話。
    meta_y = {"ky": {"uttered_at": "2026-08-12T00:00:00+00:00", "detected_at": None, "cross_pj": []}}
    group_y = _slim_with_meta(["ky"], meta_y)

    queue_data = {"proposals": {"per_pj": {"pj-a": [group_x, group_y]}, "global": []}}

    # 既読なし: X が confirmed 由来で tier 1 → 先頭。
    before = pd.build_session_proposals(queue_data, "pj-a", seen_keys=set())
    assert before[0]["signal_keys"] == ["kx-confirmed", "kx-plain"]

    # confirmed を持つキーだけ既読化: X は tier 1 を失い、より新しい Y が先頭になる。
    after = pd.build_session_proposals(queue_data, "pj-a", seen_keys={"kx-confirmed"})
    assert after[0]["signal_keys"] == ["ky"]
    # X は消えず、残存キーだけで後ろに残る（group ごと除外しない既存契約）。
    assert ["kx-plain"] in [g["signal_keys"] for g in after]
    # 残存キーからは cross_pj が消えている。
    x_after = [g for g in after if g["signal_keys"] == ["kx-plain"]][0]
    assert pr._remaining_cross_pj(x_after) == []


def test_build_proposal_prompt_requires_recommendation_per_item():
    """朝の4択は判断材料だけでなく「推奨（どれを選ぶべきか＋理由1行）」を必ず添えさせる。

    材料（記録される内容・背景）は答えではない。推奨行の指示が digest から消えると、
    受け取った assistant は材料だけ並べて判断をユーザーへ丸投げする（grill-with-docs
    の tech-eval で検出したギャップ）。
    """
    groups = [_group(["k1"], rep="rep")]
    msg = pd.build_proposal_prompt(groups, "pj-a")
    # 語句の存在ではなく契約文そのもの（共有定数）の完全一致を要求する。
    # 語だけ残して意味を反転させる書き換えを通さないため（codex レビュー [Should]）。
    assert pd.RECOMMENDATION_INSTRUCTION in msg
    assert "推奨なし" in pd.RECOMMENDATION_INSTRUCTION


def test_build_proposal_prompt_carries_recommendation_for_global_lane():
    """#582 round2 [Must]: global レーン（keys_by_pj を持つ group）でも契約文が届くこと。

    per-PJ 経路だけを検査していると「keys_by_pj がある group には契約文を出さない」
    変異が緑のまま通る（全PJ横断の指摘だけ推奨なしになる）。
    """
    g = _group(["k1"], rep="rep")
    g.pop("signal_keys", None)
    g["keys_by_pj"] = {"pj-b": ["k1"]}
    msg = pd.build_proposal_prompt([g], "pj-a", project_paths={"pj-b": "/tmp/pj-b"})
    assert pd.RECOMMENDATION_INSTRUCTION in msg
    assert "--pj pj-b" in msg
