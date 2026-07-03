"""#531 束縛経路テスト — フェーズ分割の silent-fail 回帰フェンス。

ADR-048 の「re-export すれば import 無変更で通る」は **import 文の解決**の話。
テスト/本体が依存する `setattr(evolve, "<name>", ...)` の **動的束縛**は別問題で、
`run_evolve` / `main` を別 module へ抽出すると差し替えが**すり抜けて mock が効かず、
テスト緑のまま実関数が走る**（実環境走査で激遅化 or 別挙動）。

対策（evolve.py §531 で実装）: 差し替え対象 helper を `import evolve as _ev; _ev.<name>()`
経由で呼び、束縛先をパッケージ `evolve`（__init__）の 1 箇所に集約する。

本テストは **分割前（単一ファイル状態）で緑**になり、後続の phase 抽出 PR（#6-#8）で
束縛がすり抜けたら**赤に転じて**確証を与える回帰フェンスである。HOME 隔離は
`skills/evolve/scripts/tests/conftest.py` の autouse fixture が担う（#457）。
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
_PLUGIN_ROOT = _SCRIPTS.parent.parent.parent
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts" / "rl"))

import evolve  # noqa: E402


# ── run_evolve 内から呼ぶ helper の束縛（setattr(evolve, ...) が run_evolve に効く） ──


def test_setattr_check_data_sufficiency_reaches_run_evolve(monkeypatch):
    """`setattr(evolve, "check_data_sufficiency", X)` が run_evolve の Phase 1 呼び出しに効く。"""
    calls = {"n": 0}

    def _sentinel(project_dir=None):
        calls["n"] += 1
        return {
            "sessions": 0, "observations": 0, "total_observations": 100,
            "sufficient": True, "telemetry_empty": False,
            "backfill_recommended": False, "no_new_observations": False, "message": "x",
        }

    monkeypatch.setattr(evolve, "check_data_sufficiency", _sentinel)
    monkeypatch.setattr(
        evolve, "check_fitness_function",
        lambda project_dir=None: {"has_fitness": False, "fitness_functions": []},
    )
    # observe_first=True なら重いフェーズ前に early-return（実 store を走査しない）。
    result = evolve.run_evolve(observe_first=True)
    assert calls["n"] == 1, "check_data_sufficiency の差し替えが run_evolve にすり抜けた（束縛フェンス破れ）"
    assert result["phases"]["observe"]["message"] == "x"


def test_setattr_check_fitness_function_reaches_run_evolve(monkeypatch):
    """`setattr(evolve, "check_fitness_function", X)` が run_evolve の Phase 1.5 呼び出しに効く。"""
    calls = {"n": 0}

    def _sentinel(project_dir=None):
        calls["n"] += 1
        return {"has_fitness": True, "fitness_functions": ["sentinel"]}

    monkeypatch.setattr(
        evolve, "check_data_sufficiency",
        lambda project_dir=None: {
            "sessions": 0, "observations": 0, "total_observations": 100,
            "sufficient": True, "telemetry_empty": False,
            "backfill_recommended": False, "no_new_observations": False, "message": "x",
        },
    )
    monkeypatch.setattr(evolve, "check_fitness_function", _sentinel)
    result = evolve.run_evolve(observe_first=True)
    assert calls["n"] == 1, "check_fitness_function の差し替えが run_evolve にすり抜けた"
    assert result["phases"]["fitness"]["fitness_functions"] == ["sentinel"]


# ── main から呼ぶ差し替え対象の束縛（setattr(evolve, ...) が main に効く） ──


def test_setattr_run_evolve_reaches_main(monkeypatch, capsys):
    """`setattr(evolve, "run_evolve", X)` が main()→run_evolve 呼び出しに効く。"""
    calls = {"n": 0}

    def _sentinel(**kwargs):
        calls["n"] += 1
        return {"phases": {}, "slug": "x", "stub": True}

    monkeypatch.setattr(evolve, "run_evolve", _sentinel)
    monkeypatch.setattr(sys, "argv", ["evolve", "--dry-run"])
    evolve.main()
    assert calls["n"] == 1, "run_evolve の差し替えが main にすり抜けた（main 抽出後の silent fail 予兆）"


def test_setattr_resolve_evolve_slug_reaches_main(monkeypatch, capsys):
    """`setattr(evolve, "_resolve_evolve_slug", X)` が main(--print-out-path) に効く。"""
    calls = {"n": 0}

    def _sentinel(root):
        calls["n"] += 1
        return "sentinelslug"

    monkeypatch.setattr(evolve, "_resolve_evolve_slug", _sentinel)
    monkeypatch.setattr(sys, "argv", ["evolve", "--print-out-path"])
    evolve.main()
    out = capsys.readouterr().out
    assert calls["n"] == 1, "_resolve_evolve_slug の差し替えが main にすり抜けた"
    assert "/tmp/rl_evolve_sentinelslug.json" in out
