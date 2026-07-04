"""pitfall_lint hook（編集時 warn-only）のテスト。

LLM を呼ばない決定論テスト。evaluate() を直接叩き、enable 済みファイルにのみ
反応し・ファイルを書き換えず・状態に応じた警告を返すことを検証する。
"""
import io
import json
import sys
from pathlib import Path

_hooks = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_hooks))
sys.path.insert(0, str(_hooks.parent / "scripts" / "lib"))
sys.path.insert(0, str(_hooks.parent / "skills" / "pitfall-curate" / "scripts"))

import pitfall_lint
import pitfall_registry
from parse import normalize

CANONICAL = normalize(
    "# Pitfalls\n\n## Active Pitfalls\n\n### サンプル\n- **Status**: Active\n"
)
# インラインパイプ metadata → normalize がバレットへ展開する drift
DRIFT = "# Pitfalls\n\n## Active\n\n### サンプル\n**Status**: Active | **Last-seen**: 2026-05-29\n"
INDEX = (
    "# index\n\n> TOC\n\n| # | 問題 |\n|---|------|\n| 1 | a |\n\n"
    "- [pitfalls-x.md](pitfalls-x.md) — x\n- [pitfalls-y.md](pitfalls-y.md) — y\n"
)


def _setup(tmp_path, content):
    pf = tmp_path / ".claude" / "skills" / "x" / "references" / "pitfalls.md"
    pf.parent.mkdir(parents=True)
    pf.write_text(content, encoding="utf-8")
    return pf


def _event(path):
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}}


def test_unmanaged_file_is_silent(tmp_path):
    pf = _setup(tmp_path, DRIFT)  # drift でも未登録なら無反応
    assert pitfall_lint.evaluate(_event(pf), str(tmp_path)) is None


def test_managed_canonical_is_silent(tmp_path):
    pf = _setup(tmp_path, CANONICAL)
    pitfall_registry.add_managed(tmp_path, pf)
    assert pitfall_lint.evaluate(_event(pf), str(tmp_path)) is None


def test_managed_drift_warns(tmp_path):
    pf = _setup(tmp_path, DRIFT)
    pitfall_registry.add_managed(tmp_path, pf)
    msg = pitfall_lint.evaluate(_event(pf), str(tmp_path))
    assert msg is not None
    assert "差分" in msg


def test_managed_drift_does_not_mutate_file(tmp_path):
    pf = _setup(tmp_path, DRIFT)
    pitfall_registry.add_managed(tmp_path, pf)
    pitfall_lint.evaluate(_event(pf), str(tmp_path))
    assert pf.read_text(encoding="utf-8") == DRIFT  # 書き換えない


def test_managed_danger_warns_about_data_loss(tmp_path):
    pf = _setup(tmp_path, INDEX)
    pitfall_registry.add_managed(tmp_path, pf)
    msg = pitfall_lint.evaluate(_event(pf), str(tmp_path))
    assert msg is not None
    assert "失う" in msg


def test_error_result_is_silent(tmp_path):
    pf = _setup(tmp_path, DRIFT)
    pitfall_registry.add_managed(tmp_path, pf)
    ev = _event(pf)
    ev["tool_result"] = {"is_error": True}
    assert pitfall_lint.evaluate(ev, str(tmp_path)) is None


def test_non_edit_tool_is_silent(tmp_path):
    pf = _setup(tmp_path, DRIFT)
    pitfall_registry.add_managed(tmp_path, pf)
    ev = {"tool_name": "Bash", "tool_input": {"file_path": str(pf)}}
    assert pitfall_lint.evaluate(ev, str(tmp_path)) is None


def _run_main(monkeypatch, event, project_dir):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    pitfall_lint.main()


def test_main_drift_emits_systemmessage_json(tmp_path, monkeypatch, capsys):
    """#110: PostToolUse hook は裸テキストでなく systemMessage JSON を stdout に出す。

    裸テキストは tool_result に連結され表示層を汚染する。警告は user 向け
    systemMessage チャネル（ADR-038）にのみ載せる。stdout がそのまま JSON パース
    できることを assert する（裸テキストなら json.loads がここで落ちる）。
    """
    pf = _setup(tmp_path, DRIFT)
    pitfall_registry.add_managed(tmp_path, pf)
    _run_main(monkeypatch, _event(pf), tmp_path)
    out = capsys.readouterr().out.strip()
    assert out
    parsed = json.loads(out)
    assert set(parsed.keys()) == {"systemMessage"}
    assert "差分" in parsed["systemMessage"]


def test_main_ok_is_silent(tmp_path, monkeypatch, capsys):
    """ok 状態では stdout に何も出さない（tool_result 非汚染）。"""
    pf = _setup(tmp_path, CANONICAL)
    pitfall_registry.add_managed(tmp_path, pf)
    _run_main(monkeypatch, _event(pf), tmp_path)
    assert capsys.readouterr().out == ""
