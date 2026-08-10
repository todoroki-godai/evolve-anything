"""bootstrap 消化除外 predicate の単一ソース契約テスト（#94 非対称是正）。

同じ pj_slug に対して ``fleet.queue_materials``（queue の待ち件数）・
``correction_semantic.daily_review``（毎日の y/n 確認）・
``audit.sections_weak_signals``（observability matrix の当PJ未昇格集計）が別々に bootstrap
消化除外を実装していると、marker 設置以前に detected した weak を一部の reader は落とすのに
別の reader は出し続けるという split-brain が起こる
（learning_consumption_state_split / learning_detector_contradiction_shared_predicate と同型。
このファイルのコメント履歴 #112/#117/#159 と同じ「reader が増えるたび1箇所だけ取り残される」
パターンが #94 で3回目に再発したため、本ファイルで単一ソース契約として固定する）。

**「weak_signals を読む全 reader」への一般化はしない**（意図的な設計判断）。reader は意味論的に
2 種類に分かれる:
  - 「待ち/backlog」系（本除外を**適用すべき**）: queue_materials / daily_review /
    sections_weak_signals の当PJ未昇格集計（cur_unpromoted_by_channel 等）
  - 「観測値/生カウント」系（本除外を**適用してはならない**）: sections_weak_signals の
    all_by_channel（全PJ生総数・意図的に raw を維持）、sections_capture._llm_judge_count
    （capture rate 判定用の累計捕捉数。bootstrap で「判断済み」でも capture 自体は起きている
    という別の意味を持つため、除外すると capture rate の意味が変わってしまう）。
  reflect --show-weak-signals も「全件を明示的に見る」低レベル入口として意図的に除外対象外
  （codex レビューで確認済み・この判断に従う）。
  よって本テストは上記「待ち/backlog」系3 reader 間の合意のみを契約化する。

本テストは:
1. 除外 predicate（``_exclude_bootstrap_consumed``）が ``correction_semantic.bootstrap_backlog``
   を単一ソースとし、``fleet.queue_materials`` はそれを re-export しているだけ（同一オブジェクト）
   であることを確認する。
2. 同一の合成 weak_signals ストア + marker に対し、queue / daily_review / sections_weak_signals
   の当PJ未昇格集計が、bootstrap 消化除外について同じ判定（pre-marker weak は全員から消える）を
   通ることを確認する。channel フィルタ・既読(seen)フィルタは daily_review 固有の追加フィルタ
   であり差として許容する（REVIEW_CHANNELS に含まれる llm_judge のみを使いその差を排除する）。
   sections_weak_signals の all_by_channel（生総数）は除外**されない**ことも同テストで確認する。
3. 公開 API の既定引数 ``marker_base=None`` が実際に本番 DATA_DIR（``CLAUDE_PLUGIN_DATA``）の
   marker を探索する契約を検証する（marker_base を明示注入しない・#94 [Should] 是正）。

**#405 round4 是正**: 上記2「待ち/backlog」系の合意を、外部レビュー round4 の [Must] 3件
（``audit.sections_capture._llm_judge_actionable_count`` / ``audit.sections_weak_signals``
の当PJ未昇格集計 / ``icebox_reconcile._eval_weak_signals_unprocessed_count``）へ拡張する。
これら3箇所は「actionable（行動可能・閾値判定に使う）」母集団を組み立てる際、TTL失効(#89)・
bootstrap消化済み(#94) の除外軸の一部が抜けていた。全 actionable reader の単一 predicate
（``correction_semantic.promote.filter_actionable``）に揃えたことを、以下のテストで
signal_key 集合の一致（件数だけでなく「どのレコードが生き残ったか」）として検証する。
``sections_capture._llm_judge_count``（raw・promoted 含む）はこの拡張後も対象外のまま
（観測値としての意味を変えない設計は #94 から不変）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import daily_review as dr  # noqa: E402
from fleet import queue_materials  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402

SLUG = "contract-test-pj"


def _sig(text: str, line_no: int, detected_at: str, channel: str = "llm_judge") -> WeakSignal:
    return WeakSignal(
        channel=channel,
        provenance={"source_path": "/a.jsonl", "line_no": line_no, "text": text, "reason": "r"},
        detected_at=detected_at,
        session_id="s1",
        pj_slug=SLUG,
    )


def _write_marker(tmp_path: Path, slug: str, content: str) -> None:
    (tmp_path / f"bootstrap_done-{slug}.marker").write_text(content, encoding="utf-8")


def test_exclude_bootstrap_consumed_is_single_sourced():
    from correction_semantic.bootstrap_backlog import _exclude_bootstrap_consumed as canonical
    from fleet.queue_materials import _exclude_bootstrap_consumed as reexported

    assert reexported is canonical


def test_queue_and_daily_review_agree_on_pre_marker_exclusion(tmp_path: Path):
    ws = tmp_path / "weak_signals.jsonl"
    now = datetime.now(timezone.utc)
    pre_marker = _sig("marker前の指摘", 1, (now - timedelta(days=3)).isoformat())
    post_marker = _sig("marker後の指摘", 2, (now - timedelta(hours=1)).isoformat())
    append_signals([pre_marker, post_marker], path=ws)
    _write_marker(tmp_path, SLUG, (now - timedelta(days=1)).isoformat())

    # queue 側: 未処理件数は post_marker の 1 件のみ（pre_marker は bootstrap 消化済み扱い）。
    queue_count = queue_materials.weak_unprocessed_by_pj(
        SLUG, weak_signals_path=ws, marker_base=tmp_path
    )
    assert queue_count == 1

    # daily_review 側: 新規 group も post_marker の 1 件のみ（同じ predicate を通る）。
    res = dr.build_review(
        SLUG,
        weak_signals_path=ws,
        seen_path=tmp_path / "correction_review_seen.jsonl",
        marker_base=tmp_path,
    )
    daily_signal_keys = {k for g in res["groups"] for k in g["signal_keys"]}
    assert daily_signal_keys == {post_marker.signal_key}

    # 両者とも pre_marker を落とし post_marker を残す＝bootstrap 消化除外について合意している。
    assert pre_marker.signal_key not in daily_signal_keys


def test_queue_and_daily_review_agree_when_no_marker(tmp_path: Path):
    # marker が無ければ両者とも除外なし（挙動不変・素通し）で一致する。
    ws = tmp_path / "weak_signals.jsonl"
    now = datetime.now(timezone.utc)
    sig = _sig("marker無しの指摘", 1, (now - timedelta(days=2)).isoformat())
    append_signals([sig], path=ws)

    queue_count = queue_materials.weak_unprocessed_by_pj(
        SLUG, weak_signals_path=ws, marker_base=tmp_path
    )
    assert queue_count == 1

    res = dr.build_review(
        SLUG,
        weak_signals_path=ws,
        seen_path=tmp_path / "correction_review_seen.jsonl",
        marker_base=tmp_path,
    )
    daily_signal_keys = {k for g in res["groups"] for k in g["signal_keys"]}
    assert daily_signal_keys == {sig.signal_key}


# ─────────────────────────────────────────────────────────────────
# 3 reader（queue / daily_review / sections_weak_signals）の合意 + raw 総数は非除外（#94 Must）
# ─────────────────────────────────────────────────────────────────
def test_all_three_backlog_readers_agree_and_raw_total_unaffected(
    tmp_path: Path, monkeypatch
):
    """queue / daily_review / observability matrix の当PJ未昇格集計が bootstrap 消化除外に
    ついて一致し、observability matrix の全PJ生総数（all_by_channel）は除外されず raw のまま
    残ることを確認する（codex [Must] 是正・3つ目の reader の非対称解消）。

    codex round2 [Should] 是正: pre_marker と post_marker を**異なる channel**にする。
    同一 channel だと「件数だけ」の一致（例: 当PJ未昇格 1）は、真逆の実装（pre を残し post を
    落とす）でも偶然同じ件数になり通ってしまう（集合の同一性を固定できない）。channel を
    分けることで、queue（`_scoped_kept_signals` の record 実体）・daily_review（signal_keys
    集合）・observability matrix（channel 別 matrix 行）の全てで「どちらの record が
    生き残ったか」を直接検査できるようにする。
    """
    import weak_signals.store as ws_store
    from audit.sections_weak_signals import build_weak_signals_section

    proj = tmp_path / SLUG
    proj.mkdir()

    now = datetime.now(timezone.utc)
    # 異なる channel にして「どちらが生き残ったか」を channel 単位で判別可能にする。
    pre_marker = _sig(
        "marker前の指摘", 1, (now - timedelta(days=3)).isoformat(), channel="rephrase"
    )
    post_marker = _sig(
        "marker後の指摘", 2, (now - timedelta(hours=1)).isoformat(), channel="llm_judge"
    )

    store = tmp_path / "weak_signals.jsonl"
    append_signals([pre_marker, post_marker], path=store)
    monkeypatch.setattr(ws_store, "default_store_path", lambda base=None: store)

    _write_marker(tmp_path, SLUG, (now - timedelta(days=1)).isoformat())

    # queue 側: record 実体（_scoped_kept_signals）で post_marker の signal_key のみ残ることを
    # 直接検査する（件数一致だけだと pre/post 逆転の誤実装を見逃す）。
    kept = queue_materials._scoped_kept_signals(
        SLUG, weak_signals_path=store, marker_base=tmp_path
    )
    kept_keys = {r.get("signal_key") for r in kept}
    assert kept_keys == {post_marker.signal_key}
    assert pre_marker.signal_key not in kept_keys

    # daily_review 側: 新規 group の signal_keys 集合が post_marker のみ（既に identity 検査）。
    res = dr.build_review(
        SLUG,
        weak_signals_path=store,
        seen_path=tmp_path / "correction_review_seen.jsonl",
        marker_base=tmp_path,
    )
    daily_keys = {k for g in res["groups"] for k in g["signal_keys"]}
    assert daily_keys == {post_marker.signal_key}

    # observability matrix 側: channel 別 matrix 行で pre_marker（rephrase）が当PJ未昇格 0、
    # post_marker（llm_judge）が当PJ未昇格 1 であることを個別に検査する（集合の同一性を固定）。
    # 全PJ生総数（all_by_channel）はどちらも raw のまま 1 件ずつ（意図的に除外しない）。
    section = build_weak_signals_section(proj)
    assert section is not None
    body = "\n".join(section)
    assert "言い直し（rephrase）: 全PJ 1 / 当PJ未昇格 0" in body
    assert "llm_judge" in body and "全PJ 1 / 当PJ未昇格 1" in body


# ─────────────────────────────────────────────────────────────────
# 既定 marker_base=None が本番 DATA_DIR を探索する契約（#94 [Should] 是正）
# ─────────────────────────────────────────────────────────────────
def test_default_marker_base_resolves_production_data_dir(tmp_path: Path, monkeypatch):
    """CLAUDE_PLUGIN_DATA を一時領域へ向け、marker_base を省略しても本番既定パス
    （``bootstrap_backlog.default_marker_path``）と同じ場所の marker を探索することを検証する。

    これまでの契約テストは全て marker_base を明示注入しており、公開 API の既定 None が
    実際に DATA_DIR を解決する契約はテストされていなかった（codex レビュー [Should] 指摘）。

    weak_signals ストア自体は明示 ``weak_signals_path`` で hermetic に渡す（union read
    ``read_signals(None)`` は ``rl_common.iter_read_data_dirs()`` 経由で **import 時に確定した
    module-level ``rl_common.DATA_DIR``** を参照するため、テスト内 ``monkeypatch.setenv`` では
    追従しない既知の別問題 — pitfall_module_level_datadir_import_copy・本タスクのスコープ外。
    本テストは ``marker_base=None`` の契約のみを対象にする）。marker 探索（
    ``default_marker_path`` / ``default_seen_path``）は呼び出しのたびに env を読むため
    ``monkeypatch.setenv`` に追従する。
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(data_dir))

    now = datetime.now(timezone.utc)
    ws = tmp_path / "weak_signals.jsonl"  # hermetic 明示パス（union read の対象外）
    pre_marker = _sig("marker前の指摘", 1, (now - timedelta(days=3)).isoformat())
    post_marker = _sig("marker後の指摘", 2, (now - timedelta(hours=1)).isoformat())
    append_signals([pre_marker, post_marker], path=ws)

    from correction_semantic.bootstrap_backlog import default_marker_path

    marker = default_marker_path(SLUG)
    assert marker == data_dir / f"bootstrap_done-{SLUG}.marker"
    marker.write_text((now - timedelta(days=1)).isoformat(), encoding="utf-8")

    # marker_base を渡さない（省略）＝既定 None が CLAUDE_PLUGIN_DATA 経由で data_dir を解決する。
    queue_count = queue_materials.weak_unprocessed_by_pj(SLUG, weak_signals_path=ws)
    assert queue_count == 1

    res = dr.build_review(
        SLUG, weak_signals_path=ws, seen_path=data_dir / "correction_review_seen.jsonl"
    )
    daily_keys = {k for g in res["groups"] for k in g["signal_keys"]}
    assert daily_keys == {post_marker.signal_key}


# ─────────────────────────────────────────────────────────────────
# #405 round4 是正: capture / weak_signals section / icebox_reconcile を含む
# 全 actionable reader の合意（TTL失効 + bootstrap消化除外）
# ─────────────────────────────────────────────────────────────────
def test_all_actionable_readers_agree_on_ttl_and_bootstrap_exclusion(
    tmp_path: Path, monkeypatch
):
    """queue / daily_review / capture / weak_signals section / icebox_reconcile の
    actionable 母集団が、TTL失効(#89)・bootstrap消化除外(#94) の両軸について一致することを
    signal_key 集合（件数だけでなく「どのレコードが生き残ったか」）で検証する（round3 の
    「件数だけだと真逆の実装が通る」指摘を踏襲）。

    3種のレコードを用意し、各 reader が同じ1件（actionable）だけを残すことを確認する:
      - pre_marker: marker 設置以前に detected（bootstrap で判断済み）かつ TTL 超過
        （両軸のどちらでも落ちる）
      - ttl_only_expired: marker 設置**後**に detected（bootstrap 消化除外は適用されない）
        だが TTL（45日）超過 — TTL 軸単独の除外を検証する
      - actionable: marker 設置後・TTL 内 — 唯一生き残るべきレコード
    """
    import icebox_reconcile as ir
    from audit.sections_capture import _llm_judge_actionable_count, _llm_judge_count
    from audit.sections_weak_signals import build_weak_signals_section

    # icebox_reconcile.SELF_PJ_SLUG は #352 B6 で固定値（extra 経由の上書き不可）なので、
    # 5 reader すべてが同じ母集団に合意することを検証するため、このテストでは
    # モジュール定数 SLUG（"contract-test-pj"）でなく ir.SELF_PJ_SLUG をレコードの
    # pj_slug に使う。
    islug = ir.SELF_PJ_SLUG
    proj = tmp_path / islug
    proj.mkdir()

    now = datetime.now(timezone.utc)
    store = tmp_path / "weak_signals.jsonl"  # production union read の canonical 実体でもある

    def _isig(text: str, line_no: int, detected_at: str) -> WeakSignal:
        return WeakSignal(
            channel="llm_judge",
            provenance={"source_path": "/a.jsonl", "line_no": line_no, "text": text, "reason": "r"},
            detected_at=detected_at,
            session_id="s1",
            pj_slug=islug,
        )

    pre_marker = _isig("marker前かつTTL超", 1, (now - timedelta(days=120)).isoformat())
    ttl_only_expired = _isig("marker後だがTTL失効", 2, (now - timedelta(days=50)).isoformat())
    actionable = _isig("actionable", 3, (now - timedelta(hours=1)).isoformat())
    append_signals([pre_marker, ttl_only_expired, actionable], path=store)

    _write_marker(tmp_path, islug, (now - timedelta(days=100)).isoformat())

    # queue: record 実体で actionable のみ残ることを直接検査する。
    kept = queue_materials._scoped_kept_signals(
        islug, weak_signals_path=store, marker_base=tmp_path
    )
    assert {r.get("signal_key") for r in kept} == {actionable.signal_key}

    # daily_review: 新規 group の signal_keys が actionable のみ。
    res = dr.build_review(
        islug,
        weak_signals_path=store,
        seen_path=tmp_path / "correction_review_seen.jsonl",
        marker_base=tmp_path,
    )
    daily_keys = {k for g in res["groups"] for k in g["signal_keys"]}
    assert daily_keys == {actionable.signal_key}

    # capture: production union read（CLAUDE_PLUGIN_DATA は root conftest が tmp_path に
    # 隔離済み・rl_common.DATA_DIR も per-test に rebase 済みのため store をそのまま拾う）。
    # raw（_llm_judge_count）は除外されず 3 件のまま、actionable は 1 件だけに絞られることを
    # 対で確認する（「actionable からは消えるが raw 表示からは消えない」を実測する）。
    assert _llm_judge_count(proj) == 3
    assert _llm_judge_actionable_count(proj) == 1

    # icebox_reconcile: 同じ predicate 経由で 1.0 のみ残る。
    icebox_value = ir.EVALUATORS["weak_signals"]["unprocessed_count"](tmp_path, {})
    assert icebox_value == 1.0

    # weak_signals section: 全PJ生総数（raw）は 3 のまま、当PJ未昇格（actionable）は 1。
    section = build_weak_signals_section(proj)
    assert section is not None
    body = "\n".join(section)
    assert "全PJ 3 / 当PJ未昇格 1" in body


def test_icebox_lane_not_falsely_met_from_bootstrap_consumed_only(tmp_path: Path):
    """判断済み（bootstrap 消化済み）項目だけの母集団で、閾値条件が誤って成立しないことを
    検証する（PR #405 round4 [Must]3・icebox_reconcile の実害が最も大きい箇所）。

    marker 設置以前に detected（TTL内・判断済み）のレコード3件のみを用意する。bootstrap
    消化除外を通さない旧実装は actionable=3 を返し `unprocessed_count >= 1` を満たして
    誤って ``lane="met"`` になる。除外を通す新実装は actionable=0 で met にならない。
    """
    import icebox_reconcile as ir

    # icebox_reconcile.SELF_PJ_SLUG は固定値（#352 B6）なので、レコードもこの slug で作る。
    islug = ir.SELF_PJ_SLUG
    now = datetime.now(timezone.utc)
    store = tmp_path / "weak_signals.jsonl"
    judged = [
        WeakSignal(
            channel="llm_judge",
            provenance={"source_path": "/a.jsonl", "line_no": i,
                        "text": f"marker前・判断済み{i}", "reason": "r"},
            detected_at=(now - timedelta(days=3)).isoformat(),
            session_id="s1",
            pj_slug=islug,
        )
        for i in range(1, 4)
    ]
    append_signals(judged, path=store)
    _write_marker(tmp_path, islug, (now - timedelta(days=1)).isoformat())

    value = ir.EVALUATORS["weak_signals"]["unprocessed_count"](tmp_path, {})
    assert value == 0.0

    issue = {
        "number": 1,
        "body": (
            "## 再開条件\n```yaml\nreopen-when:\n  source: weak_signals\n"
            "  metric: unprocessed_count\n  op: \">=\"\n  threshold: 1\n```\n"
        ),
        "closedAt": (now - timedelta(days=200)).isoformat(),
        "updatedAt": (now - timedelta(days=200)).isoformat(),
    }
    verdict = ir.classify_issue(issue, now=now, data_dir=tmp_path)
    assert verdict["lane"] != "met"
