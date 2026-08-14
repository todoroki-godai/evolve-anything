"""fleet.queue: weak_unprocessed_by_pj の bootstrap 消化除外（#94）。

bootstrap marker 設置以前に detected された weak を material 計数から除外する read 時導出
ロジックの hermetic テスト。detected_at は now 相対で生成し TTL（45日・#89・read 時導出）と
干渉させない（固定過去日付だとテスト実行日次第で先に expired 除外されてしまう）。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from fleet.queue import bootstrap_consumed_by_pj, weak_unprocessed_by_pj  # noqa: E402
from weak_signals.store import WeakSignal, append_signals  # noqa: E402

SLUG = "figma-to-code"


def _sig(text: str, line_no: int, detected_at: str, pj_slug: str = SLUG) -> WeakSignal:
    return WeakSignal(
        channel="llm_judge",
        provenance={
            "source_path": "/a.jsonl",
            "line_no": line_no,
            "text": text,
            "reason": "r",
        },
        detected_at=detected_at,
        session_id="s1",
        pj_slug=pj_slug,
    )


def _machinery_sig(line_no: int, detected_at: str, pj_slug: str = SLUG) -> WeakSignal:
    """harness 注入テキスト（委譲メッセージ）を持つ machinery signal（#443 PR2-a codex [Should]2）。"""
    return WeakSignal(
        channel="llm_judge",
        provenance={
            "source_path": "/a.jsonl",
            "line_no": line_no,
            "text": "<teammate-message>委譲メッセージ本文</teammate-message>",
        },
        detected_at=detected_at,
        session_id="s1",
        pj_slug=pj_slug,
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _write_marker(tmp_path: Path, slug: str, content: str) -> None:
    (tmp_path / f"bootstrap_done-{slug}.marker").write_text(content, encoding="utf-8")


def test_no_marker_counts_all(tmp_path):
    now = datetime.now(timezone.utc)
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("a", 1, _iso(now - timedelta(days=2))),
            _sig("b", 2, _iso(now - timedelta(days=1))),
        ],
        path=ws,
    )
    # marker 無し → 全件カウント（除外なし・従来挙動）。
    assert weak_unprocessed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 2
    assert bootstrap_consumed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 0


def test_marker_excludes_pre_marker_weak(tmp_path):
    now = datetime.now(timezone.utc)
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("a", 1, _iso(now - timedelta(days=3))),
            _sig("b", 2, _iso(now - timedelta(days=2))),
        ],
        path=ws,
    )
    _write_marker(tmp_path, SLUG, _iso(now - timedelta(days=1)))  # 両 weak より後
    # marker 以前 detected の 2 件は判断済み → 除外（figma 116→0 の再現）。
    assert weak_unprocessed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 0
    assert bootstrap_consumed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 2


def test_marker_keeps_post_marker_weak(tmp_path):
    now = datetime.now(timezone.utc)
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("old", 1, _iso(now - timedelta(days=3))),   # marker 前 → 除外
            _sig("new", 2, _iso(now - timedelta(hours=1))),  # marker 後 → 残る
        ],
        path=ws,
    )
    _write_marker(tmp_path, SLUG, _iso(now - timedelta(days=1)))
    # marker 後に溜まった新規 weak は正当な待ちとして残す（線引きの正しさ）。
    assert weak_unprocessed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 1
    assert bootstrap_consumed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 1


def test_empty_marker_uses_mtime_fallback(tmp_path):
    # 旧形式の空 marker は mtime（≒now）にフォールバック。過去の weak は除外（後方互換）。
    now = datetime.now(timezone.utc)
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("a", 1, _iso(now - timedelta(days=2)))], path=ws)
    _write_marker(tmp_path, SLUG, "")  # 空 = mark_done 改修前の旧 marker
    assert weak_unprocessed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 0


def test_unparseable_detected_at_kept(tmp_path):
    # detected_at が parse 不能 → 安全側で残す（誤って queue から落とさない）。
    now = datetime.now(timezone.utc)
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("a", 1, "not-a-date")], path=ws)
    _write_marker(tmp_path, SLUG, _iso(now))
    assert weak_unprocessed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 1


def test_bootstrap_consumed_isolates_bootstrap_axis_from_future_filter_actionable_growth(
    tmp_path, monkeypatch,
):
    """#405 round7 [Should]2: bootstrap_consumed_by_pj の差分は bootstrap 軸だけを反映する。

    以前の実装は「``read_unpromoted`` ベースの独立集計（scoped）」と「``filter_actionable``
    経由の集計（kept）」の差分を取っていた。両者はたまたま同じ3軸（promoted/TTL/reviewed）を
    ``_filter_unpromoted`` 経由で共有していたため一致していたが、``filter_actionable`` の
    本体に（``_filter_unpromoted`` を経由しない）新しい軸が直接足されると、その軸は kept
    側にしか反映されず、差分が bootstrap 軸以外まで拾ってしまう構造だった。

    ここでは ``correction_semantic.promote.filter_actionable`` を monkeypatch し「bootstrap
    と独立な仮想の新軸（line_no==2 を追加除外）」を足しても、``bootstrap_consumed_by_pj`` が
    返す値が bootstrap 消化分（marker 前 detected の 1 件）だけを反映し、新軸で除外された分
    まで誤って含めないことを固定する。
    """
    now = datetime.now(timezone.utc)
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("old", 1, _iso(now - timedelta(days=3))),   # marker 前 → bootstrap 除外対象
            _sig("new", 2, _iso(now - timedelta(hours=1))),  # marker 後・仮想の新軸で除外される想定
        ],
        path=ws,
    )
    _write_marker(tmp_path, SLUG, _iso(now - timedelta(days=1)))

    import correction_semantic.promote as cs_promote_module

    real_filter_actionable = cs_promote_module.filter_actionable

    def _fake_filter_actionable(records, pj_slug, **kwargs):
        out = real_filter_actionable(records, pj_slug, **kwargs)
        # 仮想の「新軸」: bootstrap 非依存に line_no==2 のレコードを追加除外する。
        return [r for r in out if r.get("provenance", {}).get("line_no") != 2]

    monkeypatch.setattr(cs_promote_module, "filter_actionable", _fake_filter_actionable)

    # bootstrap 消化はあくまで「old」1件のみ（新軸で除外された「new」を含めない）。
    assert bootstrap_consumed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 1


def test_marker_scoped_to_pj(tmp_path):
    # 別 PJ の marker は当該 PJ の weak を除外しない（slug スコープ）。
    now = datetime.now(timezone.utc)
    ws = tmp_path / "weak_signals.jsonl"
    append_signals([_sig("a", 1, _iso(now - timedelta(days=2)))], path=ws)
    _write_marker(tmp_path, "other-pj", _iso(now))  # 別 PJ の marker のみ
    assert weak_unprocessed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 1


def test_format_queue_table_surfaces_consumed():
    # 待ち 0（figma が除外で消える）でも消化済みを脚注に出す（silent truncation 禁止・#94）。
    from fleet.formatters import format_queue_table

    result = {
        "queue": [],
        "tracked_total": 5,
        "threshold": 5,
        "bootstrap_consumed": [{"pj_slug": "figma-to-code", "consumed": 116}],
    }
    out = format_queue_table(result)
    assert "bootstrap 消化済み" in out
    assert "figma-to-code 116件" in out


def test_format_queue_table_silent_when_no_consumed():
    # consumed 空なら脚注を出さない。
    from fleet.formatters import format_queue_table

    out = format_queue_table({"queue": [], "tracked_total": 5, "threshold": 5})
    assert "bootstrap 消化済み" not in out


def test_format_queue_table_weak_semantics_when_waiting():
    # 待ち PJ があるとき WEAK 列の意味（未処理のみ）を脚注で明示する（②）。
    from fleet.formatters import format_queue_table

    result = {
        "queue": [
            {
                "pj_slug": "amamo",
                "material_count": 56,
                "weak_unprocessed": 16,
                "new_corrections": 40,
                "last_evolve_at": None,
                "reason": "weak=16 + corr=40",
            }
        ],
        "tracked_total": 5,
        "threshold": 5,
    }
    out = format_queue_table(result)
    assert "WEAK は content-rich 未処理のみ" in out


def test_format_queue_table_weak_semantics_absent_when_empty():
    # 待ち 0（WEAK 列が無い）では列の意味注記を出さない。
    from fleet.formatters import format_queue_table

    out = format_queue_table({"queue": [], "tracked_total": 5, "threshold": 5})
    assert "WEAK は content-rich 未処理のみ" not in out


def test_bootstrap_consumed_by_pj_unaffected_by_machinery(tmp_path):
    """codex [Should]2 回帰テスト: machinery（委譲メッセージ等の harness 注入）が混在しても
    bootstrap_consumed_by_pj は bootstrap 軸だけを反映する（machinery は二重計上されない）。

    machinery は ``filter_actionable(scoped, None)`` の時点で（bootstrap 消化除外の前に）
    落ちるため、marker 前後どちらの machinery レコードも consumed 集計に混ざらない
    （codex が「壊れていない」と確認した契約を固定する）。
    """
    now = datetime.now(timezone.utc)
    ws = tmp_path / "weak_signals.jsonl"
    append_signals(
        [
            _sig("old", 1, _iso(now - timedelta(days=3))),        # marker 前・非machinery → consumed
            _sig("new", 2, _iso(now - timedelta(hours=1))),       # marker 後・非machinery → 残る
            _machinery_sig(3, _iso(now - timedelta(days=3))),     # marker 前・machinery → 集計対象外
            _machinery_sig(4, _iso(now - timedelta(hours=1))),    # marker 後・machinery → 集計対象外
        ],
        path=ws,
    )
    _write_marker(tmp_path, SLUG, _iso(now - timedelta(days=1)))
    # machinery 2件の有無に関わらず、consumed は非machinery の marker 前1件のみ。
    assert bootstrap_consumed_by_pj(SLUG, weak_signals_path=ws, marker_base=tmp_path) == 1
