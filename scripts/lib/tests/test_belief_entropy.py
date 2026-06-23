"""belief_entropy の決定論テスト（LLM 不使用）。

retention/drift プロキシと should_store ゲート、low_signal ガードを検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_lib_dir))

import belief_entropy  # noqa: E402
from belief_entropy import BeliefScore, score_belief  # noqa: E402


def _corr(message: str) -> dict:
    return {"message": message, "type": "feedback"}


# ソース: 11 個の distinct な英語トークン
_SOURCE = [
    _corr("always use absolute paths in bash commands never cd into directories")
]


def test_high_retention_low_drift_stores():
    """ソース用語を保持し余計な主張が少ない要約は保存される。"""
    summary = (
        "Use absolute paths in bash commands. Never cd into directories. "
        "This avoids broken hooks."
    )
    score = score_belief(summary, _SOURCE)
    assert score.should_store is True
    assert score.low_signal is False
    assert score.retention >= 0.9  # ほぼ全ソース用語を保持


def test_low_retention_blocks():
    """ソース情報を落とした無関係な要約はブロックされる。"""
    summary = (
        "Some unrelated note about testing frameworks and pytest fixtures "
        "only here today now."
    )
    score = score_belief(summary, _SOURCE)
    assert score.should_store is False
    assert score.low_signal is False
    assert score.retention < belief_entropy.RETENTION_THRESHOLD


def test_high_drift_blocks_even_with_some_retention():
    """retention は閾値を超えるが大半が非接地（drift 過剰）な要約はブロックされる。"""
    # ソース由来 3 トークン（retention=3/11≈0.27 > 0.25）+ 非接地 22 トークン
    grounded = "absolute paths bash"
    ungrounded = " ".join(f"filler{i}" for i in range(22))
    summary = f"{grounded} {ungrounded}"
    score = score_belief(summary, _SOURCE)
    assert score.retention >= belief_entropy.RETENTION_THRESHOLD
    assert score.drift > belief_entropy.DRIFT_THRESHOLD
    assert score.should_store is False


def test_retention_boundary_inclusive():
    """retention == threshold は保存許可（>= 判定）。"""
    summary = (
        "Always use absolute paths in bash commands. Never cd into directories. Done."
    )
    # 明示閾値で境界を固定（retention=1.0 を 1.0 閾値で境界判定）
    score = score_belief(summary, _SOURCE, retention_threshold=1.0, drift_threshold=1.0)
    assert score.retention == pytest.approx(1.0)
    assert score.should_store is True


def test_empty_corrections_allows_store():
    """比較元が無ければ安全側で保存を許可（low_signal）。"""
    score = score_belief("any non empty summary text here", [])
    assert score.should_store is True
    assert score.low_signal is True


def test_empty_summary_allows_store():
    """要約が空ならブロックしない（low_signal）。"""
    score = score_belief("", _SOURCE)
    assert score.should_store is True
    assert score.low_signal is True


def test_coarse_tokenization_japanese_not_blocked():
    """日本語など粗いトークン化で信号が乏しい場合はブロックしない。"""
    corrections = [_corr("認証 ルーティング")]  # tokenize で 2 トークン < MIN_SIGNAL_TOKENS
    summary = "まったく無関係な要約テキスト"
    score = score_belief(summary, corrections)
    assert score.low_signal is True
    assert score.should_store is True


def test_frontmatter_stripped_before_scoring():
    """frontmatter の構造トークンで drift が過大評価されず、本文で判定される。

    本文はソースを保持する短い要約。frontmatter 込みで測ると非接地トークンが
    増えて drift が跳ねるが、frontmatter を剥がせば should_store=True になる。
    """
    summary = (
        "---\n"
        "name: bash-path-rule\n"
        "description: absolute paths in bash\n"
        "metadata:\n"
        "  type: feedback\n"
        "importance: medium\n"
        "---\n\n"
        "Always use absolute paths in bash commands. Never cd into directories."
    )
    score = score_belief(summary, _SOURCE)
    assert score.should_store is True
    assert score.low_signal is False


def test_strip_frontmatter_noop_without_frontmatter():
    """frontmatter が無いテキストはそのまま（body 判定に影響しない）。"""
    assert belief_entropy._strip_frontmatter("just body text") == "just body text"


def test_score_belief_returns_beliefscore_type():
    score = score_belief("absolute paths bash commands directories", _SOURCE)
    assert isinstance(score, BeliefScore)
    assert 0.0 <= score.retention <= 1.0
    assert 0.0 <= score.drift <= 1.0


# ── clean_summary_head（#69 表示崩れ根治）──────────────────────────────────
def test_clean_summary_head_strips_frontmatter_full_summary():
    """full summary（閉じ --- あり）は frontmatter を除去して本文を返す。"""
    summary = (
        "---\n"
        "name: no-defer-spawn-subagent\n"
        "description: 先送り表現を検出したら即座に\n"
        "---\n"
        "先送りせず background subagent を起動して並行処理する。\n"
    )
    head = belief_entropy.clean_summary_head(summary)
    assert head.startswith("先送りせず background subagent")
    assert "---" not in head
    assert "name:" not in head
    assert "\n" not in head


def test_clean_summary_head_truncated_frontmatter_one_line():
    """既存データ（閉じ --- 無しで truncate 済み）は 1 行化し先頭 --- を落とす。"""
    truncated = "---\nname: no-defer-spawn-subagent-immediately\ndescription: 先送り表現を検出したら即座に backgr"
    head = belief_entropy.clean_summary_head(truncated)
    assert "\n" not in head
    assert not head.startswith("---")
    assert head.startswith("name: no-defer-spawn-subagent-immediately")


def test_clean_summary_head_truncates_long_with_ellipsis():
    head = belief_entropy.clean_summary_head("あ" * 200, limit=80)
    assert head.endswith("…")
    assert len(head) <= 81


def test_summarize_blocks_returns_clean_single_line_heads(tmp_path):
    """summarize_blocks の heads は崩れず 1 行に整形されている（#69）。"""
    import json
    from datetime import datetime, timezone
    blocks = tmp_path / belief_entropy.BLOCKS_FILENAME
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "retention": 0.1,
        "drift": 0.9,
        "summary_head": "---\nname: foo\ndescription: 多段タスクを",
    }
    blocks.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    count, heads = belief_entropy.summarize_blocks(tmp_path, days=30)
    assert count == 1
    assert heads and all("\n" not in h for h in heads)
    assert not heads[0].startswith("---")
