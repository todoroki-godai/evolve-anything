"""correction_semantic.store の confirmed/revoke 拡張テスト（ADR-047 / #447）。

human-confirmed idiom の confirmed=True 化・confirmed テキスト集合読み取り・revoke（取り消し）を
検証する。confirmed の単位は「pj_slug × idiom テキスト」（同じ言い回しの新規発話にも効く）。
これらは idiom_autopromote の発火ゲート（confirmed が立つまで一切昇格しない）と
安全弁③（取り消しで巻き戻る）を支える。決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from correction_semantic import store as cs_store  # noqa: E402

SLUG = "evolve-anything"


def _idiom(text="四国めたんじゃなくて", source_path="/a.jsonl", line_no=1, pj_slug=SLUG):
    return cs_store.CorrectionIdiom(
        idiom=text,
        provenance={"source_path": source_path, "line_no": line_no,
                    "session_id": "s1", "reason": "正しい値の後置型"},
        detected_at="2026-06-10T00:00:00+00:00",
        pj_slug=pj_slug,
    )


def _seed(path: Path, *idioms):
    cs_store.append_idioms(list(idioms), path=path)
    return [it.idiom_key for it in idioms]


# ── schema: 新フィールドのデフォルト ─────────────────────────────────


def test_new_idiom_defaults_unconfirmed() -> None:
    """新規 idiom レコードは confirmed=False（未確認）で書かれる。"""
    rec = _idiom().to_record()
    assert rec["confirmed"] is False
    assert rec["confirmed_at"] is None
    assert rec["confirmed_by"] is None
    assert rec["revoked_at"] is None


def test_read_confirmed_texts_empty_when_none_confirmed(tmp_path: Path) -> None:
    """confirmed=True が 1 件も無ければ confirmed テキスト集合は空（雪崩防止の起点）。"""
    p = tmp_path / "correction_idioms.jsonl"
    _seed(p, _idiom(line_no=1), _idiom(text="緑じゃなくて赤", line_no=2))
    assert cs_store.read_confirmed_idiom_texts(SLUG, p) == set()


# ── confirm: 人間確認で confirmed=True を立てる（テキスト単位） ──────


def test_confirm_sets_confirmed_true(tmp_path: Path) -> None:
    p = tmp_path / "correction_idioms.jsonl"
    keys = _seed(p, _idiom(line_no=1), _idiom(text="緑じゃなくて赤", line_no=2))
    res = cs_store.confirm_idioms([keys[0]], path=p, confirmed_by="daily_review")
    assert res["confirmed"] == 1
    texts = cs_store.read_confirmed_idiom_texts(SLUG, p)
    assert "四国めたんじゃなくて" in texts
    assert "緑じゃなくて赤" not in texts
    # confirmed_at / confirmed_by が立つ
    recs = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_key = {r["idiom_key"]: r for r in recs}
    assert by_key[keys[0]]["confirmed"] is True
    assert by_key[keys[0]]["confirmed_at"] is not None
    assert by_key[keys[0]]["confirmed_by"] == "daily_review"
    assert by_key[keys[1]]["confirmed"] is False


def test_confirm_marks_all_records_of_same_text(tmp_path: Path) -> None:
    """同テキスト・別 phys の record が複数あれば、確認は全 record に効く（テキスト単位）。"""
    p = tmp_path / "correction_idioms.jsonl"
    # 同じ言い回しを別発話（別 line_no）から 2 件拾った状態
    keys = _seed(
        p,
        _idiom(text="四国めたんじゃなくて", line_no=1),
        _idiom(text="四国めたんじゃなくて", line_no=2),
    )
    assert keys[0] != keys[1]  # idiom_key は phys 違いで別値
    res = cs_store.confirm_idioms([keys[0]], path=p, confirmed_by="daily_review")
    # 引数は片方の idiom_key だが、同テキストの 2 record とも confirmed=True
    assert res["confirmed"] == 2
    recs = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert all(r["confirmed"] is True for r in recs)


def test_confirm_dry_run_writes_nothing(tmp_path: Path) -> None:
    p = tmp_path / "correction_idioms.jsonl"
    keys = _seed(p, _idiom(line_no=1))
    before = p.read_text(encoding="utf-8")
    res = cs_store.confirm_idioms([keys[0]], path=p, confirmed_by="daily_review", dry_run=True)
    assert res["dry_run"] is True
    assert res["confirmed"] == 1  # 確認するはずだった件数
    assert p.read_text(encoding="utf-8") == before  # ファイル不変


def test_confirm_unknown_key_is_noop(tmp_path: Path) -> None:
    p = tmp_path / "correction_idioms.jsonl"
    _seed(p, _idiom(line_no=1))
    res = cs_store.confirm_idioms(["nonexistent"], path=p, confirmed_by="daily_review")
    assert res["confirmed"] == 0
    assert cs_store.read_confirmed_idiom_texts(SLUG, p) == set()


# ── revoke: 取り消しで confirmed=False + revoked_at（安全弁③・テキスト単位） ──


def test_revoke_clears_confirmed_and_sets_revoked_at(tmp_path: Path) -> None:
    p = tmp_path / "correction_idioms.jsonl"
    keys = _seed(p, _idiom(line_no=1))
    cs_store.confirm_idioms([keys[0]], path=p, confirmed_by="daily_review")
    assert "四国めたんじゃなくて" in cs_store.read_confirmed_idiom_texts(SLUG, p)

    res = cs_store.revoke_idiom(keys[0], path=p)
    assert res["revoked"] == 1
    # confirmed テキスト集合から外れる（autopromote 対象外に戻る）
    assert cs_store.read_confirmed_idiom_texts(SLUG, p) == set()
    recs = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    rec = recs[0]
    assert rec["confirmed"] is False
    assert rec["revoked_at"] is not None


def test_revoke_clears_all_records_of_same_text(tmp_path: Path) -> None:
    """同テキスト・別 phys の record が複数あれば、取り消しは全 record に効く（テキスト単位）。"""
    p = tmp_path / "correction_idioms.jsonl"
    keys = _seed(
        p,
        _idiom(text="四国めたんじゃなくて", line_no=1),
        _idiom(text="四国めたんじゃなくて", line_no=2),
    )
    cs_store.confirm_idioms([keys[0]], path=p, confirmed_by="daily_review")
    res = cs_store.revoke_idiom(keys[1], path=p)  # 別 key を渡しても同テキスト全件
    assert res["revoked"] == 2
    assert cs_store.read_confirmed_idiom_texts(SLUG, p) == set()


def test_revoke_dry_run_writes_nothing(tmp_path: Path) -> None:
    p = tmp_path / "correction_idioms.jsonl"
    keys = _seed(p, _idiom(line_no=1))
    cs_store.confirm_idioms([keys[0]], path=p, confirmed_by="daily_review")
    before = p.read_text(encoding="utf-8")
    res = cs_store.revoke_idiom(keys[0], path=p, dry_run=True)
    assert res["dry_run"] is True
    assert res["revoked"] == 1
    assert p.read_text(encoding="utf-8") == before  # ファイル不変
    # confirmed のまま（dry-run は巻き戻さない）
    assert "四国めたんじゃなくて" in cs_store.read_confirmed_idiom_texts(SLUG, p)
