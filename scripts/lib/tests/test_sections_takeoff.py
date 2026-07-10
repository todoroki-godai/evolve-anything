"""worker-takeoff（completed≠完遂）の決定論検知テスト（#161）。

subagent が harness に completed 扱いされたのに、最終 assistant メッセージが
「完了報告」でなく中間ナレーションのまま終わっている疑いを、subagents.jsonl の
`last_assistant_message`（SubagentStop hook が記録する最終 assistant テキスト）から
決定論・LLM 非依存で検出する。判定は保守側（FP 抑制）の2シグナル AND:
① 完了署名（`=== ... ===` マーカー / 報告見出し）が無い
② 前向きナレーション終端（行末 `:` / Now・Next・Let's 系の進行形で始まる）
①単独では flag しない。判定不能（空・500字打ち切り）は None（未評価）。
"""
import json
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from audit import sections_takeoff as st  # noqa: E402
from audit.sections_takeoff import build_worker_takeoff_section  # noqa: E402
from rl_common.detection import TRUNCATED_LEN, detect_takeoff_divergence  # noqa: E402


# ─────────────────────────── detect_takeoff_divergence（純関数） ───────────────────────────

def test_none_when_message_missing_or_blank():
    """空 / 非文字列 / 空白のみは判定不能で None（未評価）。"""
    assert detect_takeoff_divergence(None) is None
    assert detect_takeoff_divergence("") is None
    assert detect_takeoff_divergence("   \n  ") is None
    assert detect_takeoff_divergence(123) is None


def test_none_when_truncated_to_cap():
    """500字打ち切り（hooks側 MAX_MESSAGE_LENGTH）に達したメッセージは末尾情報が失われて
    いるため判定不能（①②とも信用できない）。"""
    truncated = "Now let's fix this:" + ("x" * (TRUNCATED_LEN - 19))
    assert len(truncated) == TRUNCATED_LEN
    assert detect_takeoff_divergence(truncated) is None


def test_false_when_completion_marker_present():
    """`=== IMPL COMPLETE ... ===` マーカーがあれば完了署名ありで False（未 suspected）。"""
    msg = (
        "実装が完了しました。\n\n"
        "=== IMPL COMPLETE (branch: feat/161-x, commit: abc123) ==="
    )
    assert detect_takeoff_divergence(msg) is False


def test_false_when_report_heading_present():
    """`## 実装完了報告` の見出しがあれば完了署名ありで False。"""
    msg = "## 実装完了報告\n\nブランチ: feat/x\nテスト: 全件green"
    assert detect_takeoff_divergence(msg) is False


def test_true_when_no_marker_and_ends_with_colon():
    """完了署名が無く、行末が `:` で終わる前向きナレーションは疑いあり True。"""
    msg = "テストを一通り書きました。次に検出関数を実装します:"
    assert detect_takeoff_divergence(msg) is True


def test_true_when_no_marker_and_starts_with_lets():
    """完了署名が無く、最終行が Let's 系進行形で始まれば True。"""
    msg = "ここまでで tool_use のパースは完了しました。Let's add the detection function next."
    assert detect_takeoff_divergence(msg) is True


def test_true_when_no_marker_and_starts_with_now():
    """最終行が Now で始まる進行形も True。"""
    msg = "設計を確認しました。Now let's implement the validator."
    assert detect_takeoff_divergence(msg) is True


def test_false_when_no_marker_but_ordinary_ending():
    """完了署名が無くても、前向きナレーション終端でなければ False（①単独では flag しない）。"""
    msg = "全ての変更を確認し、副作用もチェックしました。問題ありません。"
    assert detect_takeoff_divergence(msg) is False


def test_false_when_marker_present_even_with_forward_looking_tail():
    """完了署名がある場合、末尾がたまたま `:` でも AND 判定で False。"""
    msg = (
        "=== SCOUT COMPLETE ===\n\n"
        "残タスク:"
    )
    assert detect_takeoff_divergence(msg) is False


def test_true_with_fullwidth_colon_ending():
    """全角コロン終端も前向きナレーションとして扱う。"""
    msg = "以下を実施します：\n次に環境変数を追加します："
    assert detect_takeoff_divergence(msg) is True


def test_false_when_realistic_truncated_completed_report():
    """500字打ち切りで途中の語で切れた実報告（マーカーが末尾で切り落とされたケース）は
    判定不能 None であり、誤って True にしない。"""
    base = (
        "PR4完了です。annals-kingdomのChronicle本文を翻訳し、コミットとしてまとめました。"
        "検証は4ゲート全て通過しました。範囲外として2点見つけています。1つは、"
        "死因の文言がイベント発生時点の言語で永久に固定される既存設計の非対称で、もう"
    )
    long_report = base * 5
    assert len(long_report) >= TRUNCATED_LEN
    truncated = long_report[:TRUNCATED_LEN]
    assert detect_takeoff_divergence(truncated) is None


# ─────────────────────────── build_worker_takeoff_section（audit section） ───────────────────────────

def _write_subagents(base: Path, rows):
    with open(base / "subagents.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _rec(agent_id, agent_type, last_assistant_message, project="mine"):
    return {
        "agent_id": agent_id,
        "agent_type": agent_type,
        "last_assistant_message": last_assistant_message,
        "project": project,
    }


def test_none_when_no_records(tmp_path, monkeypatch):
    """当 PJ の subagents レコードが無ければ None（沈黙・評価対象なし）。"""
    monkeypatch.setattr(st, "DATA_DIR", tmp_path)
    _write_subagents(tmp_path, [])
    assert build_worker_takeoff_section(tmp_path / "mine") is None


def test_none_when_none_suspected(tmp_path, monkeypatch):
    """全件 completed かつ完了署名ありなら疑いゼロ → None（無ければ非表示）。"""
    monkeypatch.setattr(st, "DATA_DIR", tmp_path)
    rows = [
        _rec("a1", "impl-worker", "=== IMPL COMPLETE (branch: x, commit: y) ==="),
        _rec("a2", "researcher", "## 完了報告\n\n調査完了しました。"),
    ]
    _write_subagents(tmp_path, rows)
    assert build_worker_takeoff_section(tmp_path / "mine") is None


def test_suspected_takeoff_surfaced_with_agent_type_breakdown(tmp_path, monkeypatch):
    """完了署名なし+前向きナレーション終端の行が agent_type 別に ⚠ surface される。"""
    monkeypatch.setattr(st, "DATA_DIR", tmp_path)
    rows = [
        _rec("a1", "impl-worker", "Now let's add the test file for the detection function."),
        _rec("a2", "impl-worker", "=== IMPL COMPLETE (branch: x, commit: y) ==="),
        _rec("a3", "researcher", "全て調査済みです。問題ありません。"),
    ]
    _write_subagents(tmp_path, rows)
    section = build_worker_takeoff_section(tmp_path / "mine")
    assert section is not None
    combined = "\n".join(section)
    assert "⚠" in combined
    assert "impl-worker" in combined
    assert "1" in combined  # impl-worker: 1/2 件 suspected


def test_last_append_wins_per_agent_id(tmp_path, monkeypatch):
    """同一 agent_id の再発火は最新（append 順で後の行）を採用する。"""
    monkeypatch.setattr(st, "DATA_DIR", tmp_path)
    rows = [
        _rec("a1", "impl-worker", "Now let's continue working on this."),
        _rec("a1", "impl-worker", "=== IMPL COMPLETE (branch: x, commit: y) ==="),
    ]
    _write_subagents(tmp_path, rows)
    # 最新行（完了署名あり）が採用されるため疑いは検出されない。
    assert build_worker_takeoff_section(tmp_path / "mine") is None


def test_noise_agent_type_excluded(tmp_path, monkeypatch):
    """空 / ID 形 agent_type のノイズ行は評価対象から除外する。"""
    monkeypatch.setattr(st, "DATA_DIR", tmp_path)
    rows = [
        _rec("", "", "Now let's do something."),
        _rec("aab2173eb119c5b91", "aab2173eb119c5b91", "Let's proceed with this task."),
    ]
    _write_subagents(tmp_path, rows)
    assert build_worker_takeoff_section(tmp_path / "mine") is None


def test_other_pj_records_not_counted(tmp_path, monkeypatch):
    """他 PJ の suspected 行は当 PJ の集計に混ざらない（当 PJ スコープ）。"""
    monkeypatch.setattr(st, "DATA_DIR", tmp_path)
    rows = [
        _rec("a1", "worker", "全て完了しました。問題ありません。", project="mine"),
        _rec("a2", "worker", "Now let's continue with the next step.", project="other"),
    ]
    _write_subagents(tmp_path, rows)
    assert build_worker_takeoff_section(tmp_path / "mine") is None


def test_section_is_advisory_list_of_str(tmp_path, monkeypatch):
    """section は str のリスト（advisory・スコア値を含めない）。"""
    monkeypatch.setattr(st, "DATA_DIR", tmp_path)
    rows = [_rec("a1", "worker", "Now let's fix this issue in the next pass.")]
    _write_subagents(tmp_path, rows)
    section = build_worker_takeoff_section(tmp_path / "mine")
    assert isinstance(section, list)
    assert all(isinstance(x, str) for x in section)
    assert section[0].startswith("## ")
