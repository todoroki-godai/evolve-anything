"""run_evolve が audit を「全部効く」既定フラグ付きで呼ぶことを保証する回帰テスト。

#264 で MemTrace を、#255 で slop_detector（constitutional に 10% ブレンド）を実装したが、
evolve のデフォルト経路 (`run_audit(project_dir)`) はどちらのフラグも渡しておらず、
「evolve するだけ」では新機能が観測される出力に現れなかった（install ≠ enforcement）。
本テストは run_evolve が memory_trace=True / constitutional_score=True を audit に
伝播することを検証する。
"""
import sys
from pathlib import Path
from unittest import mock

_plugin_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_plugin_root / "skills" / "evolve" / "scripts"))
sys.path.insert(0, str(_plugin_root / "skills" / "audit" / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "scripts"))

import audit  # noqa: E402
from evolve import run_evolve  # noqa: E402


def test_run_evolve_passes_full_effect_flags_to_audit(tmp_path):
    """evolve は MemTrace + constitutional(slop 込み) を既定で audit に依頼する。"""
    # NOTE: `audit` module-level の参照を直接 patch すると順序依存 flaky になる。
    # skills/audit/scripts/audit.py は import 時に `sys.modules["audit"]` を本物の
    # パッケージ(scripts/lib/audit)で上書きする shim であり、他テスト
    # (test_audit_memory_bytes / _quality_trends が `skills/audit/scripts` を
    #  sys.path 先頭に入れて import → shim 実行) が走ると、本ファイルの
    # module-level `import audit` で束縛したオブジェクトと runtime の
    # `sys.modules["audit"]` が別オブジェクトになる。evolve.py の
    # `from audit import run_audit` は後者を読むため、前者を patch しても効かず
    # m.called == False になっていた（フルスイートでのみ FAIL する原因）。
    # 文字列ターゲットで patch すると enter 時に sys.modules["audit"] を解決するため
    # evolve.py が見る live オブジェクトと一致し、import 順に依存しなくなる。
    live_audit = sys.modules.get("audit", audit)
    with mock.patch.object(live_audit, "run_audit", return_value="## audit report") as m:
        run_evolve(project_dir=str(tmp_path))

    assert m.called, "run_evolve が run_audit を呼んでいない"
    _, kwargs = m.call_args
    assert kwargs.get("memory_trace") is True, "memory_trace=True が audit に渡っていない（MemTrace が evolve で効かない）"
    assert kwargs.get("constitutional_score") is True, "constitutional_score=True が渡っていない（slop_detector が evolve で効かない）"


def test_run_evolve_dry_run_passes_dry_run_to_audit(tmp_path):
    """#491: run_evolve(dry_run=True) は audit にも dry_run=True を貫通させる。"""
    live_audit = sys.modules.get("audit", audit)
    with mock.patch.object(live_audit, "run_audit", return_value="## audit report") as m:
        run_evolve(project_dir=str(tmp_path), dry_run=True)
    assert m.called
    _, kwargs = m.call_args
    assert kwargs.get("dry_run") is True, "dry_run=True が audit に渡っていない（dry-run で audit-history を汚す）"


def test_run_audit_dry_run_does_not_record_completion(tmp_path, monkeypatch):
    """#491: run_audit(dry_run=True) は audit-history / evolve-state を更新しない。

    run_audit は `_record_audit_completion` を orchestrator モジュール内の名前で呼ぶため、
    再エクスポート先の `audit` でなく orchestrator の名前を patch する。
    """
    import audit.orchestrator as orch
    called = {"n": 0}
    monkeypatch.setattr(orch, "_record_audit_completion", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    orch.run_audit(project_dir=str(tmp_path), dry_run=True)
    assert called["n"] == 0, "dry_run=True なのに _record_audit_completion が呼ばれた"


def test_run_audit_default_records_completion(tmp_path, monkeypatch):
    """回帰防止: audit 単体 CLI 既定（dry_run=False）では従来どおり記録する。"""
    import audit.orchestrator as orch
    called = {"n": 0}
    monkeypatch.setattr(orch, "_record_audit_completion", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    orch.run_audit(project_dir=str(tmp_path))
    assert called["n"] == 1, "dry_run=False で _record_audit_completion が呼ばれていない"
