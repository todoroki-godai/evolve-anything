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
