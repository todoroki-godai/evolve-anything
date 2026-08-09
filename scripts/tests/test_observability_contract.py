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

import pytest

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


def _show_culled_sections(monkeypatch):
    """#379 Step 2 表示淘汰は builder→collect_observability の配線契約とは別の関心事。

    本モジュールは「builder が該当時に必ず key を立てるか」という契約（silence != evaluated）
    を検証するので、EVOLVE_SHOW_CULLED=1 で淘汰を解除し Step 2 前と同じ raw 配線を見る。
    淘汰そのものの挙動（デフォルトで隠れる／display_cull 通知）は
    scripts/lib/tests/test_observability_display_cull.py が別途カバーする。

    #379 レビュー指摘（P2）: 以前は autouse fixture で全テストに強制適用していたため、
    production default（env 無し）を実際に検証するテストが本モジュールに存在しなかった。
    淘汰解除が必要なテスト（culled key を対象にするテスト）にのみ明示的に呼ぶ。
    """
    monkeypatch.setenv("EVOLVE_SHOW_CULLED", "1")


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

    本テストの関心は「builder 該当アーティファクト無し」契約であって表示淘汰そのものでは
    ないため EVOLVE_SHOW_CULLED=1 で淘汰を解除する（解除しないと display_cull キーが常に
    1件立ち result == {} が成立しなくなる）。
    """
    _show_culled_sections(monkeypatch)
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
    # orphan_store も環境グローバル（evolve-anything 自身の hooks/scripts/skills）を走査する builder
    # のため、実プラグインに orphan ストアがあると同様に前提が崩れる。空 tmp に向けて隔離する（#422）。
    import orphan_store
    monkeypatch.setattr(orphan_store, "_default_plugin_root", lambda: tmp_path / "no-plugin")
    # outcome_metrics も環境グローバル（DATA_DIR 配下の corrections/sessions）を読む builder の
    # ため、実機データがあると「PJ アーティファクト無し」前提が崩れる。空 tmp に向けて隔離する（#423）。
    from audit import outcome_metrics
    monkeypatch.setattr(outcome_metrics, "DATA_DIR", tmp_path / "no-outcome-data")
    # measurement_bug も環境グローバル（DATA_DIR 配下の growth-state-*.json）を walk する builder の
    # ため、実機データがあると「PJ アーティファクト無し」前提が崩れる。空 tmp に向けて隔離する（#445）。
    from audit import measurement_bug
    monkeypatch.setattr(measurement_bug, "DATA_DIR", tmp_path / "no-growth-state")
    # fanout_cost も環境グローバル（DATA_DIR 配下の subagents.jsonl）を読む builder のため、
    # 実機データがあると「PJ アーティファクト無し」前提が崩れる。空 tmp に向けて隔離する（#14）。
    import fanout_cost
    monkeypatch.setattr(fanout_cost, "DATA_DIR", tmp_path / "no-fanout")
    # memory_capability も環境グローバル（~/.claude/projects/<slug>/memory/）を読む builder のため、
    # 実機の対象 slug に memory があると「PJ アーティファクト無し」前提が崩れる。memory dir を空 tmp に
    # 向けて隔離する（#19）。
    import memory_capability
    monkeypatch.setattr(
        memory_capability, "_resolve_memory_dir", lambda project_dir: tmp_path / "no-memory"
    )
    # global_claude_md も環境グローバル（~/.claude/CLAUDE.md）を読む builder のため、実機に
    # グローバル CLAUDE.md が無い/空だと「PJ アーティファクト無し」前提が崩れる。detect を
    # healthy（非空）に向けて None（沈黙）させ、契約を隔離する（#124）。
    from audit import sections_artifacts
    monkeypatch.setattr(
        sections_artifacts,
        "detect_global_claude_md",
        lambda home=None: sections_artifacts.GlobalClaudeMdReport(
            path=tmp_path / "no-global-claude-md", exists=True, is_empty=False
        ),
    )
    result = collect_observability(tmp_path)
    assert result == {}


def test_unmanaged_pitfalls_key_when_pitfalls_exist(tmp_path, monkeypatch):
    """pitfalls.md があれば unmanaged_pitfalls key が必ず立つ（clean でも ✓ 行）。

    unmanaged_pitfalls は #379 Step 2 で表示淘汰済みのため、builder 配線契約を見る
    本テストでは EVOLVE_SHOW_CULLED=1 で淘汰を解除する。
    """
    _show_culled_sections(monkeypatch)
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


def test_both_keys_when_both_artifacts_present(tmp_path, monkeypatch):
    """両アーティファクトがあれば両 key が surface される。

    unmanaged_pitfalls は淘汰済みのため EVOLVE_SHOW_CULLED=1 で解除する
    （glossary_drift は KEEP なので env なしでも出る）。
    """
    _show_culled_sections(monkeypatch)
    _write(tmp_path / "docs" / "pitfalls.md", _GROWN)
    _write(tmp_path / "CONTEXT.md", _CONTEXT)
    result = collect_observability(tmp_path)
    assert set(result.keys()) >= {"unmanaged_pitfalls", "glossary_drift"}


def test_registered_pitfalls_still_emit_evaluated_line(tmp_path, monkeypatch):
    """登録済み（managed）でも沈黙せず ✓ 行を surface する（silence != evaluated）。"""
    _show_culled_sections(monkeypatch)
    pf = tmp_path / "docs" / "pitfalls.md"
    _write(pf, _GROWN)
    reg.add_managed(tmp_path, pf)
    result = collect_observability(tmp_path)
    assert "unmanaged_pitfalls" in result
    combined = "\n".join(result["unmanaged_pitfalls"])
    assert "✓" in combined


def test_report_markdown_uses_same_single_source(tmp_path, monkeypatch):
    """report.py(markdown) と collect_observability が同じ builder を消費する単一ソース契約。

    collect_observability が返す全セクションは generate_report の markdown に **到達する**
    （将来 _OBSERVABILITY_BUILDERS に項目を足したとき片方だけに出る drift を防ぐ回帰ガード）。

    #49-1/#49-5 で markdown 経路に折り畳みを入れたため「到達」の形は 2 通りある:
    - 要対応（⚠/🔴）セクション → header が full-text で出る
    - クリーン（✓）/ 観察（ℹ・データ不足）セクション → key 名が折り畳み行に残る
    どちらでも「評価したことが見える」ので silence != evaluated は保たれる。

    unmanaged_pitfalls は淘汰済みのため EVOLVE_SHOW_CULLED=1 で解除する（本テストの
    関心は「淘汰されなければ両経路に到達するか」であり淘汰そのものではない）。
    """
    _show_culled_sections(monkeypatch)
    import sys as _sys
    _sys.path.insert(0, str(_LIB))
    from audit.sections_summary import classify_section  # noqa: PLC0415

    _write(tmp_path / "docs" / "pitfalls.md", _GROWN)
    _write(tmp_path / "CONTEXT.md", _CONTEXT)
    obs = collect_observability(tmp_path)
    md = generate_report({}, [], {}, [], [], None, project_dir=tmp_path)
    for key, lines in obs.items():
        header = lines[0]  # "## Xxx" セクション見出し
        if classify_section(lines) == "critical":
            # 要対応セクションは header が full-text 展開される
            assert header in md, f"{header!r} が markdown に出ていない（単一ソース drift）"
        else:
            # クリーン / 観察セクションは折り畳み行に key 名が残る
            assert key in md, f"{key!r} が折り畳み行にも出ていない（silence != evaluated 違反）"


def test_builders_list_is_nonempty_and_callable(tmp_path):
    """_OBSERVABILITY_BUILDERS は (key, callable) のリスト。"""
    assert len(_OBSERVABILITY_BUILDERS) >= 2
    for key, builder in _OBSERVABILITY_BUILDERS:
        assert isinstance(key, str)
        assert callable(builder)


def test_skill_triage_key_when_custom_skills_exist(tmp_path):
    """custom スキルがあれば skill_triage key が必ず立つ（#478）。"""
    skill_dir = tmp_path / ".claude" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\n", encoding="utf-8")
    result = collect_observability(tmp_path)
    assert "skill_triage" in result
    combined = "\n".join(result["skill_triage"])
    assert "Skill Triage" in combined
    assert "CREATE" in combined
    # #528-4: findings レーンの行であって assistant への指示文（必ず〜せよ等の MUST 表現）
    # ではないこと。指示は SKILL.md 側に移管した。
    assert "必ず" not in combined
    assert "せよ" not in combined


def test_skill_triage_absent_when_no_custom_skills(tmp_path):
    """custom スキルが無い PJ では skill_triage は surface されない（triage 対象外）。"""
    from audit.sections_triage import build_skill_triage_section
    assert build_skill_triage_section(tmp_path) is None


def _triage_result(**counts):
    """CREATE/UPDATE/SPLIT/MERGE/OK のリストを件数分だけ詰めた triage_result を作る。"""
    base = {k: [] for k in ("CREATE", "UPDATE", "SPLIT", "MERGE", "OK")}
    for action, n in counts.items():
        base[action] = [{"action": action} for _ in range(n)]
    return base


def test_triage_counts_lines_emit_actual_numbers():
    """#528-4: triage_result から CREATE/UPDATE/SPLIT/MERGE の実件数が行に入る。"""
    from audit.sections_triage import build_skill_triage_counts_lines

    res = _triage_result(CREATE=2, UPDATE=1, SPLIT=0, MERGE=3)
    combined = "\n".join(build_skill_triage_counts_lines(res))
    assert "CREATE 2" in combined
    assert "UPDATE 1" in combined
    assert "SPLIT 0" in combined
    assert "MERGE 3" in combined


def test_triage_counts_lines_none_when_phase_missing_or_error():
    """skill_triage phase が無い／error の場合は None（沈黙）。"""
    from audit.sections_triage import build_skill_triage_counts_lines

    assert build_skill_triage_counts_lines(None) is None
    assert build_skill_triage_counts_lines({"error": "boom", "skipped": True}) is None


def test_triage_counts_lines_zero_all_still_emits():
    """全 0 件でも findings として件数行を出す（silence != evaluated）。"""
    from audit.sections_triage import build_skill_triage_counts_lines

    combined = "\n".join(build_skill_triage_counts_lines(_triage_result()))
    assert "CREATE 0" in combined


def test_triage_counts_lines_instruction_free():
    """findings レーンなので MUST 指示文（必ず/せよ）を含まない（#528-4）。"""
    from audit.sections_triage import build_skill_triage_counts_lines

    combined = "\n".join(build_skill_triage_counts_lines(_triage_result(CREATE=1)))
    assert "必ず" not in combined
    assert "せよ" not in combined


def test_production_default_culls_without_env(tmp_path, monkeypatch):
    """production default（EVOLVE_SHOW_CULLED 未設定）で実際に表示淘汰が効くこと（#379 P2）。

    以前は本モジュール全体が autouse fixture で EVOLVE_SHOW_CULLED=1 を強制していたため、
    「淘汰が実際に production default で機能しているか」を確認する統合テストが本モジュールに
    存在しなかった（レビュー指摘）。unmanaged_pitfalls（淘汰済み）と glossary_drift（KEEP）
    の両方を同時に作り、env なしで期待どおり片方だけ隠れることを検証する。
    """
    monkeypatch.delenv("EVOLVE_SHOW_CULLED", raising=False)
    _write(tmp_path / "docs" / "pitfalls.md", _GROWN)
    _write(tmp_path / "CONTEXT.md", _CONTEXT)

    result = collect_observability(tmp_path)

    # culled key: builder 該当アーティファクトがあっても production default では隠れる。
    assert "unmanaged_pitfalls" not in result
    # KEEP key: builder が非 None を返せば production default でも現れる。
    assert "glossary_drift" in result
    # 淘汰した事実自体は必ず surface する（silence != evaluated）。
    assert "display_cull" in result
    combined = "\n".join(result["display_cull"])
    assert "32 section" in combined
