"""correction_semantic.correction_backlog のテスト（#514 修正在庫の朝の確認統合）。

朝の確認（daily_review.build_review）は新規 weak_signal のみを見るため、それ以前に
reflect_status=promoted まで昇格済みの corrections.jsonl レコード（反映先未定のまま
溜まった在庫）には朝の導線が無かった（issue #514）。本テストは corrections.jsonl を
直接読む build_correction_backlog / backlog_with_remaining を検証する。

検証観点（Acceptance Criteria 逐条対応）:
- 在庫が古い順に最大 max_items 件返る
- 旧名 slug（rl-anything）の在庫が pj_slug_match の alias 畳み込みで拾われる
  （naive な basename 一致では落ちることを陰性試験として明示）
- invalidated が真の記録を提示しない
- reflect_status が promoted 以外（pending/applied/skipped）は対象外
- 別 PJ slug の在庫が混入しない
- build_correction_backlog はファイルを1バイトも書かない（読み取り専用）

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import correction_backlog as cb  # noqa: E402
from memory_temporal import make_source_correction_id  # noqa: E402


def _ts(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _correction(
    message: str = "小田原は6000円にして",
    reflect_status: str = "promoted",
    project_path: str = "evolve-anything",
    timestamp: str | None = None,
    session_id: str = "sess1",
    invalidated: bool = False,
) -> dict:
    return {
        "message": message,
        "reflect_status": reflect_status,
        "project_path": project_path,
        "timestamp": timestamp or _ts(0),
        "session_id": session_id,
        "invalidated": invalidated,
    }


def _write_corrections(tmp_path: Path, corrections: list) -> Path:
    filepath = tmp_path / "corrections.jsonl"
    lines = [json.dumps(c, ensure_ascii=False) for c in corrections]
    filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return filepath


# ─────────────────────────────────────────────────────────────────
# build_correction_backlog: 基本挙動
# ─────────────────────────────────────────────────────────────────
def test_returns_empty_list_when_file_missing(tmp_path: Path):
    missing = tmp_path / "corrections.jsonl"
    assert cb.build_correction_backlog("evolve-anything", corrections_path=missing) == []


def test_returns_empty_list_for_empty_file(tmp_path: Path):
    path = _write_corrections(tmp_path, [])
    assert cb.build_correction_backlog("evolve-anything", corrections_path=path) == []


def test_orders_oldest_first_and_limits_to_max_items(tmp_path: Path):
    corrs = [
        _correction(message=f"msg{i}", timestamp=_ts(days_ago), session_id=f"s{i}")
        for i, days_ago in enumerate([5, 40, 1, 20, 60])
    ]
    path = _write_corrections(tmp_path, corrs)
    backlog = cb.build_correction_backlog("evolve-anything", corrections_path=path)
    assert len(backlog) == 3
    # 古い順（days_ago 降順）= 60, 40, 20
    assert [b["message"] for b in backlog] == ["msg4", "msg1", "msg3"]


def test_max_items_none_returns_all(tmp_path: Path):
    corrs = [
        _correction(message=f"msg{i}", timestamp=_ts(days_ago), session_id=f"s{i}")
        for i, days_ago in enumerate([5, 40, 1, 20, 60])
    ]
    path = _write_corrections(tmp_path, corrs)
    backlog = cb.build_correction_backlog(
        "evolve-anything", corrections_path=path, max_items=None
    )
    assert len(backlog) == 5


def test_backlog_item_shape(tmp_path: Path):
    corr = _correction(
        message="もうちょっと短くして", timestamp="2026-06-01T00:00:00+00:00",
        session_id="sess-abc",
    )
    path = _write_corrections(tmp_path, [corr])
    backlog = cb.build_correction_backlog("evolve-anything", corrections_path=path)
    assert len(backlog) == 1
    item = backlog[0]
    assert item["message"] == "もうちょっと短くして"
    assert item["session_id"] == "sess-abc"
    assert item["timestamp"] == "2026-06-01T00:00:00+00:00"
    assert item["source_correction_id"] == make_source_correction_id(
        "sess-abc", "2026-06-01T00:00:00+00:00"
    )
    assert isinstance(item["age_days"], int)
    assert item["age_days"] >= 0
    # routing_hint は実データ全件 None なので出力に含めない（#514 設計）
    assert "routing_hint" not in item


# ─────────────────────────────────────────────────────────────────
# 母集団フィルタ: reflect_status / invalidated / pj_slug
# ─────────────────────────────────────────────────────────────────
def test_excludes_invalidated_records(tmp_path: Path):
    corrs = [
        _correction(message="生きてる", invalidated=False),
        _correction(message="revoke済み", invalidated=True),
    ]
    path = _write_corrections(tmp_path, corrs)
    backlog = cb.build_correction_backlog("evolve-anything", corrections_path=path)
    assert [b["message"] for b in backlog] == ["生きてる"]


def test_excludes_non_promoted_records(tmp_path: Path):
    corrs = [
        _correction(message="pending品", reflect_status="pending"),
        _correction(message="applied品", reflect_status="applied"),
        _correction(message="skipped品", reflect_status="skipped"),
        _correction(message="promoted品", reflect_status="promoted"),
    ]
    path = _write_corrections(tmp_path, corrs)
    backlog = cb.build_correction_backlog("evolve-anything", corrections_path=path)
    assert [b["message"] for b in backlog] == ["promoted品"]


def test_other_pj_not_included(tmp_path: Path):
    corrs = [
        _correction(message="自分のPJ", project_path="evolve-anything"),
        _correction(message="他のPJ", project_path="amamo"),
    ]
    path = _write_corrections(tmp_path, corrs)
    backlog = cb.build_correction_backlog("evolve-anything", corrections_path=path)
    assert [b["message"] for b in backlog] == ["自分のPJ"]


def test_backlog_folds_legacy_slug_alias(tmp_path: Path):
    """旧名 slug（rl-anything）の在庫が pj_slug_match の alias 畳み込みで拾われる（#514）。

    naive な basename 一致（``Path(project_path).name == pj_slug``）では
    ``rl-anything != evolve-anything`` で落ちることを陰性試験の前提として明示する。
    実データ件数はテストに焼き込まない（fixture のみで検証）。
    """
    assert Path("rl-anything").name != "evolve-anything"  # naive 一致は落ちる前提の明示

    corr = _correction(message="旧名PJの在庫", project_path="rl-anything")
    path = _write_corrections(tmp_path, [corr])
    backlog = cb.build_correction_backlog("evolve-anything", corrections_path=path)
    assert [b["message"] for b in backlog] == ["旧名PJの在庫"]


def test_full_path_project_path_normalized_to_slug(tmp_path: Path):
    """project_path がフルパスでも bare slug に正規化して突合する（実コーパス混在対応）。"""
    corr = _correction(
        message="フルパス由来", project_path="/Users/x/matsukaze-utils/evolve-anything"
    )
    path = _write_corrections(tmp_path, [corr])
    backlog = cb.build_correction_backlog("evolve-anything", corrections_path=path)
    assert [b["message"] for b in backlog] == ["フルパス由来"]


# ─────────────────────────────────────────────────────────────────
# 頑健性: 壊れた JSON 行・欠落フィールド
# ─────────────────────────────────────────────────────────────────
def test_malformed_json_lines_skipped(tmp_path: Path):
    good = _correction(message="正常品")
    path = tmp_path / "corrections.jsonl"
    lines = [
        json.dumps(good, ensure_ascii=False),
        "{not valid json",
        "",
        "   ",
        "[]",  # dict でない → スキップ
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    backlog = cb.build_correction_backlog("evolve-anything", corrections_path=path)
    assert [b["message"] for b in backlog] == ["正常品"]


def test_missing_timestamp_does_not_crash_and_sorts_as_oldest(tmp_path: Path):
    """timestamp 欠落レコードは epoch 扱い（=最古）でソート先頭に来る。例外にしない。"""
    corrs = [
        _correction(message="タイムスタンプ無し", timestamp=""),
        _correction(message="通常品", timestamp=_ts(10)),
    ]
    corrs[0]["timestamp"] = ""  # 明示的に空文字
    path = _write_corrections(tmp_path, corrs)
    backlog = cb.build_correction_backlog("evolve-anything", corrections_path=path)
    assert [b["message"] for b in backlog] == ["タイムスタンプ無し", "通常品"]
    # timestamp 欠落時は age_days が算出できず None
    assert backlog[0]["age_days"] is None


# ─────────────────────────────────────────────────────────────────
# 読み取り専用（書込ゼロ）
# ─────────────────────────────────────────────────────────────────
def test_build_correction_backlog_writes_nothing(tmp_path: Path):
    corr = _correction(message="不変であるべき")
    path = _write_corrections(tmp_path, [corr])
    before_bytes = path.read_bytes()
    before_listing = sorted(p.name for p in tmp_path.iterdir())

    cb.build_correction_backlog("evolve-anything", corrections_path=path)

    assert path.read_bytes() == before_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == before_listing


# ─────────────────────────────────────────────────────────────────
# backlog_with_remaining: build_review 統合用（1 回の read で件数対を返す）
# ─────────────────────────────────────────────────────────────────
def test_backlog_with_remaining_counts_beyond_max_items(tmp_path: Path):
    corrs = [
        _correction(message=f"msg{i}", timestamp=_ts(days_ago), session_id=f"s{i}")
        for i, days_ago in enumerate([5, 40, 1, 20, 60])
    ]
    path = _write_corrections(tmp_path, corrs)
    backlog, remaining = cb.backlog_with_remaining("evolve-anything", corrections_path=path)
    assert len(backlog) == 3
    assert remaining == 2


def test_backlog_with_remaining_zero_when_within_max_items(tmp_path: Path):
    corrs = [_correction(message="唯一品")]
    path = _write_corrections(tmp_path, corrs)
    backlog, remaining = cb.backlog_with_remaining("evolve-anything", corrections_path=path)
    assert len(backlog) == 1
    assert remaining == 0
