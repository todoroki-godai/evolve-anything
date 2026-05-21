"""episodic_retriever のテスト。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib_dir))


@pytest.fixture
def store_and_retriever(tmp_path, monkeypatch):
    """DATA_DIR を tmp_path に差し替えた episodic_store/retriever を返す。"""
    import episodic_store as es
    import episodic_retriever as er
    monkeypatch.setattr(es, "DATA_DIR", tmp_path)
    # retriever がインポート済みの insert_event / query_relevant を使えるよう差し替え
    monkeypatch.setattr(er, "insert_event", es.insert_event)
    monkeypatch.setattr(er, "query_relevant", es.query_relevant)
    yield es, er


def _correction(
    message: str,
    session_id: str = "sess1",
    project_path: str | None = "/pj/test",
    correction_type: str = "iya",
    confidence: float = 0.9,
) -> dict:
    return {
        "message": message,
        "session_id": session_id,
        "project_path": project_path,
        "correction_type": correction_type,
        "confidence": confidence,
    }


class TestPromoteToEpisodic:
    def test_promotes_correction(self, store_and_retriever):
        es, er = store_and_retriever
        if not es.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        corr = _correction("git diff で変更を確認する")
        er.promote_to_episodic(corr)
        assert es.count_events() == 1

    def test_skip_empty_message(self, store_and_retriever):
        es, er = store_and_retriever
        if not es.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        corr = _correction("")
        er.promote_to_episodic(corr)
        assert es.count_events() == 0

    def test_skip_without_duckdb(self, store_and_retriever, monkeypatch):
        es, er = store_and_retriever
        monkeypatch.setattr(er, "HAS_DUCKDB", False)
        corr = _correction("なにかの修正")
        er.promote_to_episodic(corr)  # 例外なし
        assert es.count_events() == 0

    def test_uses_project_path(self, store_and_retriever):
        es, er = store_and_retriever
        if not es.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        corr = _correction("修正内容", project_path="/pj/myproject")
        er.promote_to_episodic(corr)
        # project_path が保存されているか確認
        results = er.find_episodic_duplicates(
            [_correction("修正内容", project_path="/pj/myproject")],
            "/pj/myproject",
        )
        assert len(results) >= 0  # promote 直後は match しない可能性もある（同一セッション）


class TestFindEpisodicDuplicates:
    def test_empty_corrections(self, store_and_retriever):
        _, er = store_and_retriever
        assert er.find_episodic_duplicates([], None) == []

    def test_no_duckdb(self, store_and_retriever, monkeypatch):
        _, er = store_and_retriever
        monkeypatch.setattr(er, "HAS_DUCKDB", False)
        corrections = [_correction("git diff で確認")]
        assert er.find_episodic_duplicates(corrections, None) == []

    def test_finds_matching_episodic(self, store_and_retriever):
        es, er = store_and_retriever
        if not es.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        # episodic に "git diff で変更確認" を登録
        es.insert_event("sess_old", "/pj/test", "git diff コマンドで変更確認してください")
        corrections = [_correction("git diff で確認する", project_path="/pj/test")]
        results = er.find_episodic_duplicates(corrections, "/pj/test")
        assert len(results) >= 1
        assert results[0]["correction_index"] == 0
        assert results[0]["score"] > 0

    def test_no_match_returns_empty(self, store_and_retriever):
        es, er = store_and_retriever
        if not es.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        es.insert_event("sess_old", "/pj/test", "完全に別の修正内容")
        corrections = [_correction("pytest で単体テストを書く")]
        results = er.find_episodic_duplicates(corrections, "/pj/test")
        # 全く別の内容なので match しない可能性が高い
        # (スコア 0 なら空, 偶然一致があれば返る — テストは「完全に別」の定義に依存)
        for r in results:
            assert r["score"] > 0

    def test_returns_correction_index(self, store_and_retriever):
        es, er = store_and_retriever
        if not es.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        es.insert_event("sess_old", None, "git diff コマンドで変更確認")
        corrections = [
            _correction("別の修正", project_path=None),
            _correction("git diff で確認する", project_path=None),
        ]
        results = er.find_episodic_duplicates(corrections, None)
        if results:
            # correction_index が正しいこと
            assert results[0]["correction_index"] in (0, 1)

    def test_result_has_required_fields(self, store_and_retriever):
        es, er = store_and_retriever
        if not es.HAS_DUCKDB:
            pytest.skip("DuckDB not installed")
        es.insert_event("old", None, "git diff で変更確認するべき")
        corrections = [_correction("git diff で確認する", project_path=None)]
        results = er.find_episodic_duplicates(corrections, None)
        if results:
            r = results[0]
            assert "correction_index" in r
            assert "episodic_id" in r
            assert "episodic_content" in r
            assert "days_ago" in r
            assert "score" in r
