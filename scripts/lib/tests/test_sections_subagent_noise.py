"""subagents.jsonl の agent_type ノイズ内訳 advisory section のテスト（#142-8b）。

subagents.jsonl のノイズ（空 agent_type / ID 形）を当 PJ スコープで 2 種に分解し、件数・率・
最古/最新 timestamp を surface する。reader/writer は is_noise_agent_type で除外済みだが、
除外率と内訳が誰にも見えず旧レコード残存か live writer 故障かの切り分けができなかった。
決定論・LLM 非依存・advisory のみ（スコア重み非関与）。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit import sections_subagent_noise as sn  # noqa: E402
from audit.sections_subagent_noise import build_subagent_noise_section  # noqa: E402


def _ts(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _write_subagents(base: Path, rows):
    with open(base / "subagents.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _rec(agent_type, project="mine", ts=None):
    return {
        "agent_type": agent_type,
        "project": project,
        "timestamp": ts if ts is not None else _ts(30),
    }


def test_none_when_no_records(tmp_path, monkeypatch):
    """当 PJ の subagents レコードが無ければ None（沈黙）。"""
    monkeypatch.setattr(sn, "DATA_DIR", tmp_path)
    _write_subagents(tmp_path, [])
    assert build_subagent_noise_section(tmp_path / "mine") is None


def test_none_when_no_noise(tmp_path, monkeypatch):
    """レコードはあるがノイズ 0 件なら None（無ければ非表示・dup_residue と同慣習）。"""
    monkeypatch.setattr(sn, "DATA_DIR", tmp_path)
    _write_subagents(tmp_path, [_rec("impl-worker"), _rec("researcher")])
    assert build_subagent_noise_section(tmp_path / "mine") is None


def test_breakdown_splits_empty_and_id_form(tmp_path, monkeypatch):
    """空文字と ID 形を別々に数え、件数・率・最古/最新 timestamp を出す。"""
    monkeypatch.setattr(sn, "DATA_DIR", tmp_path)
    old = _ts(100)
    mid = _ts(60)
    rows = [
        _rec("impl-worker"),           # clean
        _rec("researcher"),            # clean
        _rec("", ts=old),              # empty（最古）
        _rec("   ", ts=mid),           # empty（空白のみ）
        _rec("aab2173eb119c5b91", ts=mid),  # id_form（pure hex 17桁）
    ]
    _write_subagents(tmp_path, rows)
    section = build_subagent_noise_section(tmp_path / "mine")
    assert section is not None
    combined = "\n".join(section)
    # 内訳: 空文字 2 件 / ID 形 1 件、全 5 件中ノイズ 3 件（60.0%）
    assert "空文字 2 件" in combined
    assert "ID 形 1 件" in combined
    assert "3 件" in combined and "5 件" in combined
    # 最古 timestamp が oldest として出る
    assert old in combined


def test_historical_residue_marked_info(tmp_path, monkeypatch):
    """最新ノイズが古ければ ℹ（historical residue）で表示する。"""
    monkeypatch.setattr(sn, "DATA_DIR", tmp_path)
    _write_subagents(tmp_path, [_rec("worker"), _rec("", ts=_ts(90))])
    section = build_subagent_noise_section(tmp_path / "mine")
    assert section is not None
    combined = "\n".join(section)
    assert "ℹ" in combined
    assert "residue" in combined
    # 現行 writer は guard 済みである旨を明示（表示のみ・集計影響なし）
    assert "guard" in combined or "記録しません" in combined


def test_recent_noise_marked_warning(tmp_path, monkeypatch):
    """最新ノイズが直近なら ⚠（live writer 疑い）で表示する。"""
    monkeypatch.setattr(sn, "DATA_DIR", tmp_path)
    _write_subagents(tmp_path, [_rec("worker"), _rec("", ts=_ts(1))])
    section = build_subagent_noise_section(tmp_path / "mine")
    assert section is not None
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "live writer" in combined or "writer" in combined


def test_other_pj_records_not_counted(tmp_path, monkeypatch):
    """他 PJ のノイズは当 PJ の内訳に混ざらない（当 PJ スコープ）。"""
    monkeypatch.setattr(sn, "DATA_DIR", tmp_path)
    rows = [
        _rec("worker", project="mine"),
        _rec("", project="other", ts=_ts(50)),  # 他 PJ のノイズ
    ]
    _write_subagents(tmp_path, rows)
    # 当 PJ "mine" にはノイズ 0 件 → None
    assert build_subagent_noise_section(tmp_path / "mine") is None


def test_section_is_advisory_list_of_str(tmp_path, monkeypatch):
    """section は str のリスト（advisory・スコア値を含めない）。"""
    monkeypatch.setattr(sn, "DATA_DIR", tmp_path)
    _write_subagents(tmp_path, [_rec("worker"), _rec("", ts=_ts(80))])
    section = build_subagent_noise_section(tmp_path / "mine")
    assert isinstance(section, list)
    assert all(isinstance(x, str) for x in section)
    assert section[0].startswith("## ")


def test_kind_helper_classifies_empty_and_id_form():
    """noise_agent_type_kind が空 / ID 形 / 本物 を分類する（単一ソース）。"""
    from rl_common import noise_agent_type_kind

    assert noise_agent_type_kind("") == "empty"
    assert noise_agent_type_kind("   ") == "empty"
    assert noise_agent_type_kind(None) == "empty"
    assert noise_agent_type_kind("aab2173eb119c5b91") == "id_form"
    assert noise_agent_type_kind("impl-worker") is None
    assert noise_agent_type_kind("gamer-mvp29") is None
