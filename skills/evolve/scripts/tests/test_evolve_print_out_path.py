"""#525-3 CLI 配線テスト: `evolve --print-out-path` が slug 解決済みの OUT パスを print する。

背景（#525-3 冗長性）: SKILL.md Step 1 は各 Bash 呼び出しごとに

    SLUG="$(python3 -c "...resolve_slug...")"
    OUT="/tmp/rl_evolve_${SLUG}.json"

という slug 再導出ボイラープレートを繰り返していた。evolve は既に slug を解決できるので、
`evolve --print-out-path` で `/tmp/rl_evolve_<slug>.json` の1行を返せば再導出を短縮できる。

このコマンドは evolve 本体を回さず（早期 return）、DATA_DIR/パス解決ロジックには触れない
（slug 解決 + /tmp パス組み立てのみ・#517 evolve.py DATA_DIR と非競合）。

HOME 隔離はこのディレクトリの conftest（#457）が autouse で行う。
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_LIB = _SCRIPTS.parent.parent.parent / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import evolve  # noqa: E402


def test_print_out_path_emits_slug_scoped_tmp_path(monkeypatch, capsys):
    """--print-out-path は /tmp/rl_evolve_<slug>.json の1行だけを出力し、本体を回さない。"""
    # slug 解決を固定（git-common-dir 方式と同じ関数を差し替え）
    monkeypatch.setattr(evolve, "_resolve_evolve_slug", lambda root: "my-pj")
    # 本体が回らないことを保証: run_evolve を呼んだら失敗させる
    monkeypatch.setattr(
        evolve, "run_evolve", lambda **kw: (_ for _ in ()).throw(AssertionError("本体を回した"))
    )
    monkeypatch.setattr(
        sys, "argv", ["evolve.py", "--print-out-path", "--project-dir", "/tmp/whatever"]
    )

    evolve.main()

    out = capsys.readouterr().out.strip()
    assert out == "/tmp/rl_evolve_my-pj.json"


def test_print_out_path_uses_cwd_when_no_project_dir(monkeypatch, capsys):
    """--project-dir 省略時は cwd を slug 解決の起点にする（OUT が slug スコープであること）。"""
    captured = {}

    def _fake_slug(root):
        captured["root"] = root
        return "from-cwd"

    monkeypatch.setattr(evolve, "_resolve_evolve_slug", _fake_slug)
    monkeypatch.setattr(sys, "argv", ["evolve.py", "--print-out-path"])

    evolve.main()

    out = capsys.readouterr().out.strip()
    assert out == "/tmp/rl_evolve_from-cwd.json"
    # slug 解決の起点が渡っている（cwd or project-dir）
    assert "root" in captured
