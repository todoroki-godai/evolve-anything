"""Observability contract のテスト（決定論・LLM 非依存）。

silence != evaluated 原則を audit↔evolve の契約として明文化する collect_observability の検証。
audit が生成しても evolve が surface しなければ観測性は届かない（#272 で audit 単体は塞いだが
evolve 経由では markdown blob に埋もれて出ない問題を構造化フィールドで解決）。

collect_observability は「該当 PJ に存在する observability セクション」だけを key→行リストで返す。
builder が None を返す項目（その PJ に非該当: CONTEXT.md/pitfalls.md が無い）は除外する。
report.py の markdown 経路と同じ _OBSERVABILITY_BUILDERS を単一ソースとして消費するため、
将来 observability 項目を追加しても両経路に自動伝播する（モグラ叩き防止）。
"""
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
# fitness_evolution は evolve-fitness スキル配下に居るため、calibration_drift builder の
# グローバル history を隔離するテストで import できるよう path を通す（_load_fitness_evolution と同経路）。
_FE_SCRIPTS = _PLUGIN_ROOT / "skills" / "evolve-fitness" / "scripts"
for _p in (_LIB, _SCRIPTS, _FE_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pitfall_registry as reg  # noqa: E402
from audit import generate_report  # noqa: E402
from audit.observability import _OBSERVABILITY_BUILDERS, collect_observability  # noqa: E402

_GROWN = """# Pitfalls

## Active Pitfalls

### A
- **Status**: Active

### B
- **Status**: Active

### C
- **Status**: Active
"""

_CONTEXT = """# Glossary

| Term | Definition | First seen |
|------|-----------|-----------|
| Foo | A thing | 2026-01-01 |
"""


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_empty_when_no_observability_artifacts(tmp_path, monkeypatch):
    """CONTEXT.md も pitfalls.md も無い PJ では空 dict（対象セクション無し）。

    eval_saturation は環境グローバル（DATA_DIR 配下の eval-sets）を読む builder のため、
    実機に eval-sets があると本テストの「PJ アーティファクト無し」前提が崩れる。
    PJ アーティファクト契約を隔離するため eval-sets dir を空 tmp に向ける（#292）。

    calibration_drift も環境グローバル（accept/reject 履歴）を読む builder のため、実機に
    optimize 履歴があると同様に前提が崩れる。store を空 tmp に向けて load_history() を空にし
    「PJ アーティファクト無し」契約を隔離する（#286 / ADR-031 で store 隔離に移行）。

    agent_team も環境グローバル（~/.claude/agents/）を読む builder のため、実機にエージェント
    定義があると同様に前提が崩れる。scan_agents を空に向けて「PJ アーティファクト無し」契約を
    隔離する（#326）。
    """
    import eval_saturation
    monkeypatch.setattr(
        eval_saturation, "_default_eval_sets_dir", lambda: tmp_path / "no-evalsets"
    )
    import optimize_history_store as _ohs
    monkeypatch.setattr(_ohs, "HISTORY_ROOT", tmp_path / "no-history")
    monkeypatch.setattr(_ohs, "resolve_slug", lambda cwd=None: "no-history")
    from audit import sections_agent
    monkeypatch.setattr(sections_agent, "scan_agents", lambda **kw: [])
    # hook_drift も環境グローバル（~/.gstack の flow-chain.json）を読む builder のため、
    # 実機に gstack があると「PJ アーティファクト無し」前提が崩れる。空 tmp に向けて隔離する。
    import hook_drift
    monkeypatch.setattr(hook_drift, "_default_gstack_dir", lambda: tmp_path / "no-gstack")
    # correction_capture も環境グローバル（DATA_DIR 配下の usage.jsonl/corrections.jsonl）を
    # 読む builder のため、実機に live テレメトリがあると「PJ アーティファクト無し」前提が崩れる。
    # store を不在 tmp に向けて active session 0 → None にし、契約を隔離する（#421）。
    from audit import sections_capture
    monkeypatch.setattr(
        sections_capture,
        "_resolve_store_files",
        lambda: (tmp_path / "no-usage.jsonl", tmp_path / "no-corr.jsonl"),
    )
    # orphan_store も環境グローバル（rl-anything 自身の hooks/scripts/skills）を走査する builder
    # のため、実プラグインに orphan ストアがあると同様に前提が崩れる。空 tmp に向けて隔離する（#422）。
    import orphan_store
    monkeypatch.setattr(orphan_store, "_default_plugin_root", lambda: tmp_path / "no-plugin")
    # outcome_metrics も環境グローバル（DATA_DIR 配下の corrections/sessions）を読む builder の
    # ため、実機データがあると「PJ アーティファクト無し」前提が崩れる。空 tmp に向けて隔離する（#423）。
    from audit import outcome_metrics
    monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path / "no-outcome-data")
    result = collect_observability(tmp_path)
    assert result == {}


def test_unmanaged_pitfalls_key_when_pitfalls_exist(tmp_path):
    """pitfalls.md があれば unmanaged_pitfalls key が必ず立つ（clean でも ✓ 行）。"""
    _write(tmp_path / "docs" / "pitfalls.md", _GROWN)
    result = collect_observability(tmp_path)
    assert "unmanaged_pitfalls" in result
    assert isinstance(result["unmanaged_pitfalls"], list)
    combined = "\n".join(result["unmanaged_pitfalls"])
    assert "Unmanaged Pitfalls" in combined


def test_glossary_drift_key_when_context_exists(tmp_path):
    """CONTEXT.md があれば glossary_drift key が必ず立つ。"""
    _write(tmp_path / "CONTEXT.md", _CONTEXT)
    result = collect_observability(tmp_path)
    assert "glossary_drift" in result
    combined = "\n".join(result["glossary_drift"])
    assert "Glossary Drift" in combined


def test_glossary_drift_surfaces_seed_when_context_absent(tmp_path):
    """CONTEXT.md 不在 + jargon ≥ 閾値なら glossary_drift に seed 提案行が surface する（#275）。

    glossary_seed を独立 phase にしていた初版を observability contract に統合。
    creation gap（用語集を作る trigger が無い）が evolve のたびに両経路で可視化される。
    """
    _write(tmp_path / "SPEC.md", "FooBar と BazQux と MemTrace と QuuxThing を導入した。")
    result = collect_observability(tmp_path)
    assert "glossary_drift" in result
    combined = "\n".join(result["glossary_drift"])
    assert "用語集未作成" in combined


def test_both_keys_when_both_artifacts_present(tmp_path):
    """両アーティファクトがあれば両 key が surface される。"""
    _write(tmp_path / "docs" / "pitfalls.md", _GROWN)
    _write(tmp_path / "CONTEXT.md", _CONTEXT)
    result = collect_observability(tmp_path)
    assert set(result.keys()) >= {"unmanaged_pitfalls", "glossary_drift"}


def test_registered_pitfalls_still_emit_evaluated_line(tmp_path):
    """登録済み（managed）でも沈黙せず ✓ 行を surface する（silence != evaluated）。"""
    pf = tmp_path / "docs" / "pitfalls.md"
    _write(pf, _GROWN)
    reg.add_managed(tmp_path, pf)
    result = collect_observability(tmp_path)
    assert "unmanaged_pitfalls" in result
    combined = "\n".join(result["unmanaged_pitfalls"])
    assert "✓" in combined


def test_report_markdown_uses_same_single_source(tmp_path):
    """report.py(markdown) と collect_observability が同じ builder を消費する単一ソース契約。

    collect_observability が返す全セクションは generate_report の markdown にも含まれる
    （将来 _OBSERVABILITY_BUILDERS に項目を足したとき片方だけに出る drift を防ぐ回帰ガード）。
    """
    _write(tmp_path / "docs" / "pitfalls.md", _GROWN)
    _write(tmp_path / "CONTEXT.md", _CONTEXT)
    obs = collect_observability(tmp_path)
    md = generate_report({}, [], {}, [], [], None, project_dir=tmp_path)
    for _key, lines in obs.items():
        header = lines[0]  # "## Xxx" セクション見出し
        assert header in md, f"{header!r} が markdown に出ていない（単一ソース drift）"


def test_builders_list_is_nonempty_and_callable(tmp_path):
    """_OBSERVABILITY_BUILDERS は (key, callable) のリスト。"""
    assert len(_OBSERVABILITY_BUILDERS) >= 2
    for key, builder in _OBSERVABILITY_BUILDERS:
        assert isinstance(key, str)
        assert callable(builder)
