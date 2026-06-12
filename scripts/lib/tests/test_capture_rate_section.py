"""capture rate observability builder のテスト（#421）。

correction capture 率を audit/evolve の observability contract に載せる builder。
sections_eval / sections_hook と同じ `(project_dir) -> Optional[List[str]]` 契約。
advisory 表示のみ（スコア重み非関与）。決定論・LLM 非依存。
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit.sections_capture import build_capture_rate_section  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _setup_stores(tmp_path, monkeypatch, *, usage_rows, corr_rows):
    """usage.jsonl / corrections.jsonl を tmp に用意し hook_store_path をそこへ向ける。"""
    usage = tmp_path / "usage.jsonl"
    corr = tmp_path / "corrections.jsonl"
    with open(usage, "w", encoding="utf-8") as f:
        for r in usage_rows:
            f.write(json.dumps(r) + "\n")
    if corr_rows:
        with open(corr, "w", encoding="utf-8") as f:
            for r in corr_rows:
                f.write(json.dumps(r) + "\n")

    # builder は _resolve_store_files() で store パスを解決するので、それを tmp に向ける。
    # module オブジェクトを import して patch する（string パスの setattr は submodule が
    # 未 import だと AttributeError になり、クロスパッケージ実行順に依存して落ちるため避ける）。
    from audit import sections_capture
    monkeypatch.setattr(sections_capture, "_resolve_store_files", lambda: (usage, corr))


def _usage(sid, n):
    return [{"session_id": sid, "skill_name": "Bash", "ts": _now_iso()} for _ in range(n)]


def test_none_when_no_usage(tmp_path, monkeypatch):
    """usage が無い（active session 0）→ None（テレメトリ未蓄積で対象外）。"""
    _setup_stores(tmp_path, monkeypatch, usage_rows=[], corr_rows=[])
    assert build_capture_rate_section(tmp_path) is None


def test_evaluated_line_when_full_capture(tmp_path, monkeypatch):
    """active session があり capture 良好 → ✓ 行で評価痕跡を残す。"""
    _setup_stores(
        tmp_path,
        monkeypatch,
        usage_rows=_usage("s1", 30),
        corr_rows=[{"session_id": "s1", "timestamp": _now_iso()}],
    )
    section = build_capture_rate_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "Correction Capture" in combined
    assert "100%" in combined


def test_warning_line_when_low_capture(tmp_path, monkeypatch):
    """active session があるのに capture 0% → ⚠ で starvation を surface。"""
    _setup_stores(
        tmp_path,
        monkeypatch,
        usage_rows=_usage("s1", 30) + _usage("s2", 40),
        corr_rows=[],
    )
    section = build_capture_rate_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "0%" in combined


def test_advisory_only_no_score_field(tmp_path, monkeypatch):
    """builder は行リストのみ返す（スコア値を含めない advisory）。"""
    _setup_stores(
        tmp_path,
        monkeypatch,
        usage_rows=_usage("s1", 30),
        corr_rows=[{"session_id": "s1", "timestamp": _now_iso()}],
    )
    section = build_capture_rate_section(tmp_path)
    assert isinstance(section, list)
    assert all(isinstance(line, str) for line in section)


# ── #476-1: channel 別表示で llm_judge 大量捕捉時の誤「枯渇」警告を解消 ──────

_THIS_SLUG = "rl-anything"
_OTHER_SLUG = "other-pj"


def _seed_weak_signals(tmp_path, monkeypatch, *, this_pj=0, other_pj=0):
    """weak_signals.jsonl に当PJ / 他PJ の llm_judge レコードを置く（slug スコープ検証用）。

    #476 fixup: weak_signals.jsonl は全PJ共通ストアなので当PJ slug でフィルタする。
    resolve_slug を当PJ slug に固定し、当PJ / 他PJ のレコードを混在させて検証できるようにする。
    """
    import weak_signals.store as ws_store

    store = tmp_path / "weak_signals.jsonl"
    with open(store, "w", encoding="utf-8") as f:
        for i in range(this_pj):
            f.write(json.dumps({"channel": "llm_judge", "promoted": False,
                                "signal_key": f"a{i}", "pj_slug": _THIS_SLUG}) + "\n")
        for i in range(other_pj):
            f.write(json.dumps({"channel": "llm_judge", "promoted": False,
                                "signal_key": f"b{i}", "pj_slug": _OTHER_SLUG}) + "\n")
    monkeypatch.setattr(ws_store, "default_store_path", lambda base=None: store)
    # _llm_judge_count は optimize_history_store.resolve_slug を呼ぶ。当PJ slug に固定する。
    import optimize_history_store
    monkeypatch.setattr(optimize_history_store, "resolve_slug", lambda cwd=None: _THIS_SLUG)


def test_no_starvation_warning_when_llm_judge_captures(tmp_path, monkeypatch):
    """hook capture 0% でも 当PJ llm_judge が大量捕捉していれば「枯渇」警告を出さない（#476-1）。"""
    _setup_stores(
        tmp_path,
        monkeypatch,
        usage_rows=_usage("s1", 30) + _usage("s2", 40),
        corr_rows=[],
    )
    _seed_weak_signals(tmp_path, monkeypatch, this_pj=313)
    section = build_capture_rate_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    # 「枯渇している可能性」の誤警告は出ない
    assert "枯渇している可能性" not in combined
    # channel 別の内訳が表示される（当PJ ラベル付き）
    assert "hook" in combined
    assert "llm_judge" in combined and "313" in combined
    assert "当PJ" in combined


def test_starvation_warning_when_both_channels_empty(tmp_path, monkeypatch):
    """hook capture 0% かつ 当PJ llm_judge も 0 件なら従来通り「枯渇」警告を出す（#476-1）。"""
    _setup_stores(
        tmp_path,
        monkeypatch,
        usage_rows=_usage("s1", 30),
        corr_rows=[],
    )
    _seed_weak_signals(tmp_path, monkeypatch, this_pj=0)
    section = build_capture_rate_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "枯渇している可能性" in combined


def test_other_pj_llm_judge_not_counted(tmp_path, monkeypatch):
    """他PJ の llm_judge シグナルは当PJ の枯渇判定を抑制しない（#476 fixup スコープ混在）。"""
    _setup_stores(
        tmp_path,
        monkeypatch,
        usage_rows=_usage("s1", 30),
        corr_rows=[],
    )
    # 当PJ は 0 件、他PJ に 313 件 → 当PJ は枯渇のまま（他PJ が誤抑制しない）
    _seed_weak_signals(tmp_path, monkeypatch, this_pj=0, other_pj=313)
    section = build_capture_rate_section(tmp_path)
    assert section is not None
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "枯渇している可能性" in combined
    # 当PJ の llm_judge は 0 件として表示される
    assert "llm_judge 0 件" in combined
