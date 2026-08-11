"""correction_semantic.store のテスト（#431 個人辞書 + 判定進捗）。

個人辞書（correction_idioms.jsonl）への provenance 付き追記・dedup・dry-run ゼロ書込、
および判定済み発話の物理キー進捗を検証する。決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import store as cs_store  # noqa: E402


def _idiom(text="四国めたんじゃなくて", source_path="/a.jsonl", line_no=1):
    return cs_store.CorrectionIdiom(
        idiom=text,
        provenance={"source_path": source_path, "line_no": line_no,
                    "session_id": "s1", "reason": "正しい値の後置型"},
        detected_at="2026-06-10T00:00:00+00:00",
        pj_slug="evolve-anything",
    )


# ── utterance_key（判定進捗の物理キー） ──────────────────────────────


def test_utterance_key_uses_physical_pk() -> None:
    u = {"source_path": "/x.jsonl", "line_no": 42, "text": "foo"}
    assert cs_store.utterance_key(u) == "/x.jsonl:42"


def test_utterance_key_stable() -> None:
    u1 = {"source_path": "/x.jsonl", "line_no": 42}
    u2 = {"source_path": "/x.jsonl", "line_no": 42}
    assert cs_store.utterance_key(u1) == cs_store.utterance_key(u2)


# ── 個人辞書 append/read ───────────────────────────────────────────


def test_append_writes_idiom(tmp_path: Path) -> None:
    store = tmp_path / "correction_idioms.jsonl"
    res = cs_store.append_idioms([_idiom()], path=store)
    assert res["written"] == 1
    lines = [l for l in store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    assert "四国めたん" in lines[0]


def test_append_dry_run_writes_nothing(tmp_path: Path) -> None:
    store = tmp_path / "correction_idioms.jsonl"
    res = cs_store.append_idioms([_idiom()], path=store, dry_run=True)
    assert res["dry_run"] is True
    assert res["written"] == 1  # 書くはずだった件数
    assert not store.exists()


def test_append_dedup_on_rerun(tmp_path: Path) -> None:
    store = tmp_path / "correction_idioms.jsonl"
    cs_store.append_idioms([_idiom()], path=store)
    res2 = cs_store.append_idioms([_idiom()], path=store)
    assert res2["written"] == 0
    assert res2["skipped_dup"] == 1
    lines = [l for l in store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1


def test_read_idioms_empty_when_missing(tmp_path: Path) -> None:
    assert cs_store.read_idioms(tmp_path / "nope.jsonl") == []


# ── 判定進捗（judged keys） ────────────────────────────────────────


def test_record_and_read_judged_keys(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1", "/a.jsonl:2"], path=prog)
    judged = cs_store.read_judged_keys(prog)
    assert judged == {"/a.jsonl:1", "/a.jsonl:2"}


def test_record_judged_dry_run_writes_nothing(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1"], path=prog, dry_run=True)
    assert not prog.exists()


def test_judged_keys_dedup_across_runs(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1"], path=prog)
    cs_store.record_judged(["/a.jsonl:1", "/a.jsonl:2"], path=prog)
    assert cs_store.read_judged_keys(prog) == {"/a.jsonl:1", "/a.jsonl:2"}


# ── #410 [Must]B: judged_at / est_tokens（当日累積カウント用）────────────────


def test_record_judged_attaches_judged_at_timestamp(tmp_path: Path) -> None:
    import json as _json
    from datetime import datetime

    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1"], path=prog)
    rec = _json.loads(prog.read_text(encoding="utf-8").splitlines()[0])
    assert "judged_at" in rec
    # tz-aware ISO8601 としてパースできる（他 store の judged_at/detected_at と同型）。
    dt = datetime.fromisoformat(rec["judged_at"])
    assert dt.tzinfo is not None


def test_record_judged_stores_est_tokens_when_given(tmp_path: Path) -> None:
    import json as _json

    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(
        ["/a.jsonl:1", "/a.jsonl:2"], path=prog,
        est_tokens_by_key={"/a.jsonl:1": 42},
    )
    recs = {
        r["key"]: r
        for r in (_json.loads(l) for l in prog.read_text(encoding="utf-8").splitlines() if l.strip())
    }
    assert recs["/a.jsonl:1"]["est_tokens"] == 42
    assert "est_tokens" not in recs["/a.jsonl:2"]  # 見積もり未指定キーは付与しない


def test_count_judged_today_sums_count_and_tokens_for_today_only(tmp_path: Path) -> None:
    from datetime import datetime, timedelta, timezone

    prog = tmp_path / "correction_judged.jsonl"
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
    yesterday = now - timedelta(days=1)
    _write_judged_records(
        prog,
        [
            {"key": "/a.jsonl:1", "judged_at": now.isoformat(), "est_tokens": 100},
            {"key": "/a.jsonl:2", "judged_at": now.isoformat(), "est_tokens": 50},
            {"key": "/a.jsonl:3", "judged_at": yesterday.isoformat(), "est_tokens": 999},
        ],
    )
    out = cs_store.count_judged_today(path=prog, now=now)
    assert out["count"] == 2  # 今日の2件のみ（昨日の1件は含めない）
    assert out["est_tokens"] == 150


def test_count_judged_today_normalizes_offset_to_utc_calendar_day(tmp_path: Path) -> None:
    """#410 round2 [Should]① UTC暦日正規化: dt.date() を offset のまま直接比較すると、
    UTC の「今日」に属するレコードでも、他 offset の wall-clock 日付が別日になる境界で
    誤って除外/混入する。dt.astimezone(timezone.utc).date() で正規化すること。

    "2026-08-11T01:00:00+09:00" は UTC 換算で "2026-08-10T16:00:00+00:00"
    （JST は UTC+9 なので 9 時間引く）＝ UTC の 8/10。now が UTC の 8/10 なら、この
    レコードは「今日」に含めるのが正しい（offset のまま .date() を取ると誤って 8/11 と
    判定し除外してしまう）。
    """
    from datetime import datetime, timezone

    prog = tmp_path / "correction_judged.jsonl"
    now = datetime(2026, 8, 10, 20, 0, 0, tzinfo=timezone.utc)
    jst_but_utc_today = "2026-08-11T01:00:00+09:00"  # UTC 換算では 8/10 16:00
    _write_judged_records(prog, [{"key": "/a.jsonl:1", "judged_at": jst_but_utc_today, "est_tokens": 10}])
    out = cs_store.count_judged_today(path=prog, now=now)
    assert out["count"] == 1
    assert out["est_tokens"] == 10


def test_count_judged_today_missing_est_tokens_treated_as_zero(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    prog = tmp_path / "correction_judged.jsonl"
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
    _write_judged_records(prog, [{"key": "/a.jsonl:1", "judged_at": now.isoformat()}])
    out = cs_store.count_judged_today(path=prog, now=now)
    assert out["count"] == 1
    assert out["est_tokens"] == 0


def test_count_judged_today_legacy_record_without_judged_at_excluded(tmp_path: Path) -> None:
    """judged_at 欠落（#410 以前の旧レコード）は当日集計に含めない（安全側 — count_judged_today
    の目的は「今日どれだけ消費したか」で、日付不明を「今日」と誤カウントしない）。
    """
    from datetime import datetime, timezone

    prog = tmp_path / "correction_judged.jsonl"
    now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
    _write_judged_records(prog, [{"key": "/a.jsonl:1"}])
    out = cs_store.count_judged_today(path=prog, now=now)
    assert out["count"] == 0
    assert out["est_tokens"] == 0


def test_count_judged_today_empty_store_is_zero(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    out = cs_store.count_judged_today(
        path=tmp_path / "nope.jsonl", now=datetime(2026, 8, 10, tzinfo=timezone.utc)
    )
    assert out == {"count": 0, "est_tokens": 0}


def _write_judged_records(path: Path, recs: list) -> None:
    import json as _json

    path.write_text(
        "".join(_json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8"
    )


# ── #410 round3 [Must]2: 課金済みだが判定確定できなかった試行（billed-but-unconfirmed）──
# 応答欠損（billed）・パース失敗は LLM 呼び出し自体は成功し課金が発生しているのに、
# 従来は correction_judged.jsonl に一切記録せず「未判定のまま」にしていた。これは同日中に
# 何度でも同じ対象へ再送信でき、当日累積の予算（daily_token_limit）が事実上機能しない
# 抜け穴になる。#379 新設凍結のため新ストアは作らず、既存 correction_judged.jsonl に
# "key" フィールドを持たない record（billed_attempt）を追記する。read_judged_keys は
# "key" の有無だけを見るため billed_attempt には反応せず（＝未判定のまま次回再試行できる）、
# count_judged_today は judged_at のある全レコードを合算するため billed_attempt のコストも
# 当日累積へ正しく計上される。


def test_record_billed_attempts_dry_run_writes_nothing(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    res = cs_store.record_billed_attempts([100], path=prog, dry_run=True)
    assert res == {"written": 1, "dry_run": True}
    assert not prog.exists()


def test_record_billed_attempts_writes_keyless_records(tmp_path: Path) -> None:
    import json as _json

    prog = tmp_path / "correction_judged.jsonl"
    res = cs_store.record_billed_attempts([100, 200], path=prog)
    assert res == {"written": 2, "dry_run": False}
    recs = [_json.loads(l) for l in prog.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(recs) == 2
    assert all("key" not in r for r in recs)
    assert all(r.get("billed_attempt") is True for r in recs)
    assert {r["est_tokens"] for r in recs} == {100, 200}
    assert all("judged_at" in r for r in recs)


def test_record_billed_attempts_not_treated_as_judged(tmp_path: Path) -> None:
    """billed_attempt record は read_judged_keys に一切現れない（未判定のまま残る）。"""
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_billed_attempts([100], path=prog)
    assert cs_store.read_judged_keys(prog) == set()


def test_record_billed_attempts_counted_in_count_judged_today(tmp_path: Path) -> None:
    """billed_attempt（"key" 無し）record は est_tokens には合算されるが、count（判定件数
    上限用）には数えない（#410 round4 [Must]1+2 是正: 発話レコードと試行レコードを同じ単位で
    合算していると件数上限が保守側にずれる。件数は "key" を持つレコードのみ・トークンは
    全レコード合算に分離した）。
    """
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_billed_attempts([100, 50], path=prog)
    out = cs_store.count_judged_today(path=prog)
    assert out["count"] == 0  # "key" を持たないため件数上限には数えない
    assert out["est_tokens"] == 150  # トークン上限には計上される


def test_count_judged_today_separates_count_from_tokens_with_mixed_records(
    tmp_path: Path,
) -> None:
    """"key" 付き（確定判定）と "key" 無し（予約/billed_attempt）が混在する当日実績を、
    count は前者のみ・est_tokens は両方合算で正しく分離する（#410 round4 [Must]1+2）。
    """
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1", "/a.jsonl:2"], path=prog, est_tokens_by_key=None)
    cs_store.record_billed_attempts([300], path=prog)
    out = cs_store.count_judged_today(path=prog)
    assert out["count"] == 2  # key 付きレコードのみ
    assert out["est_tokens"] == 300  # billed_attempt（key 無し）分のみ計上（key 付きは0）


def test_record_billed_attempts_empty_list_writes_nothing(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    res = cs_store.record_billed_attempts([], path=prog)
    assert res == {"written": 0, "dry_run": False}
    assert not prog.exists()


def test_filter_unjudged(tmp_path: Path) -> None:
    prog = tmp_path / "correction_judged.jsonl"
    cs_store.record_judged(["/a.jsonl:1"], path=prog)
    utterances = [
        {"source_path": "/a.jsonl", "line_no": 1, "text": "old"},
        {"source_path": "/a.jsonl", "line_no": 2, "text": "new"},
    ]
    unjudged = cs_store.filter_unjudged(utterances, judged_keys=cs_store.read_judged_keys(prog))
    assert len(unjudged) == 1
    assert unjudged[0]["line_no"] == 2
