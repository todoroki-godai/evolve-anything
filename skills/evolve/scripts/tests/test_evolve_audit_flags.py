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
    with mock.patch.object(audit, "run_audit", return_value="## audit report") as m:
        run_evolve(project_dir=str(tmp_path))

    assert m.called, "run_evolve が run_audit を呼んでいない"
    _, kwargs = m.call_args
    assert kwargs.get("memory_trace") is True, "memory_trace=True が audit に渡っていない（MemTrace が evolve で効かない）"
    assert kwargs.get("constitutional_score") is True, "constitutional_score=True が渡っていない（slop_detector が evolve で効かない）"
