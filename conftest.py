"""evolve-anything ルート conftest.py

全テストで CLAUDE_PLUGIN_DATA を tmp_path に強制し、本番 ~/.claude/evolve-anything/
配下を保護する autouse fixture を提供する。

Why: Phase 1 開発時に test fixture が patch_data_dir に session_store パスを
含めていなかったため、本番 sessions.db に test レコードが流入した。fixture 追加
忘れによる本番汚染を構造的に防ぐ最後の砦としてここに置く。

加えて、テスト中に LLM (claude CLI / anthropic SDK) を直接呼ぶことを禁止する
guard を session 起動時にインストールする。issue #41: テスト時の LLM 実呼び出しは
1 セッション 1.5M token 消費の主要因。mock 漏れを構造的に検出するため、
subprocess.run(["claude", ...]) を呼んだ瞬間に RuntimeError を投げる。
正当な用途 (integration テスト等) は環境変数 RL_ALLOW_LLM_IN_TESTS=1 で解除可。

【DATA_DIR の構造的隔離 — #420】
store モジュール（session_store / token_usage_store / growth_journal 等）は
``DATA_DIR`` を **import 時に確定** する。autouse fixture の per-test
``monkeypatch.setenv`` は import より後に走るため、import 時キャプチャ組には
効かず、手動 patch 許可リストに漏れた growth_journal が実 ~/.claude/evolve-anything/
を汚染した（977 件中 852 件 = 87%）。許可リスト方式は「4 匹目のモグラ」を
構造的に生む。よって **入口で塞ぐ**: 全テストモジュールの import より先に走る
conftest トップレベルで ``CLAUDE_PLUGIN_DATA`` を session 一時 dir に固定する。
import 時キャプチャ組も含め一律に隔離され、モジュール個別 patch は不要になる
（tmp_path は fixture でしか使えないので tempfile.mkdtemp で session dir を作る）。
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

# HOME 隔離 helper（#457/#119）を single source から取り込む。scripts/lib を path に
# 載せる（top-level に stdlib 名衝突が無いことを確認済み。#119）。個別ディレクトリの
# conftest 頼みだった HOME 隔離を root 全体の autouse へ昇格するための import。
_LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from test_home_isolation import isolate_home  # noqa: E402

# 実 HOME を必要とするテスト（live API bench / 実 PJ ingest 等）の opt-out マーカー。
# これらは実 ~/.claude を読む正当な用途なので HOME 隔離をスキップする（#119）。
_REAL_HOME_MARKERS = ("real_home", "bench", "bench_ingest")


def _isolate_data_dir_at_import() -> None:
    """全テストモジュールの import より先に CLAUDE_PLUGIN_DATA を tmp に固定する。

    import 時に DATA_DIR をキャプチャする store モジュールを構造的に隔離する
    最初の砦（#420）。既に環境（CI で意図的に設定）に値があれば尊重する。
    """
    if os.environ.get("CLAUDE_PLUGIN_DATA"):
        return
    session_dir = tempfile.mkdtemp(prefix="evolve-anything-test-data-")
    os.environ["CLAUDE_PLUGIN_DATA"] = session_dir
    # session 終了時に掃除（残骸を作らない）。
    atexit.register(lambda: shutil.rmtree(session_dir, ignore_errors=True))


_isolate_data_dir_at_import()


def _install_llm_guard():
    """テスト中の claude CLI subprocess 呼び出しを検出して落とす。

    subprocess.run / subprocess.Popen をプロセス全体で差し替える。mock.patch で
    更に上書きするテストは setattr/delattr の通常動作で衝突しない（既存テストで
    実証済み）。RL_ALLOW_LLM_IN_TESTS の評価は runtime（_guarded_* 関数内）に
    寄せて、test 起動後に環境変数を変えても効くようにしている。
    """
    _orig_run = subprocess.run
    _orig_popen = subprocess.Popen

    def _allowed() -> bool:
        return os.environ.get("RL_ALLOW_LLM_IN_TESTS") == "1"

    def _is_llm_call(args) -> bool:
        # list/tuple のみ判定対象。shell=True 由来の string コマンドは誤検出を避けるため対象外
        if not isinstance(args, (list, tuple)) or not args:
            return False
        first = args[0]
        if isinstance(first, (list, tuple)) and first:
            first = first[0]
        if not isinstance(first, str):
            return False
        return first == "claude" or first.endswith("/claude")

    def _guarded_run(args, *a, **kw):
        if not _allowed() and _is_llm_call(args):
            raise RuntimeError(
                "LLM call from test detected (subprocess.run claude ...). "
                "Mock subprocess.run or the calling function. "
                "See .claude/rules/no-llm-in-tests.md. "
                "Override with RL_ALLOW_LLM_IN_TESTS=1 for integration tests."
            )
        return _orig_run(args, *a, **kw)

    def _guarded_popen(args, *a, **kw):
        if not _allowed() and _is_llm_call(args):
            raise RuntimeError(
                "LLM call from test detected (subprocess.Popen claude ...). "
                "Mock subprocess.Popen or the calling function. "
                "See .claude/rules/no-llm-in-tests.md."
            )
        return _orig_popen(args, *a, **kw)

    subprocess.run = _guarded_run
    subprocess.Popen = _guarded_popen


_install_llm_guard()


def _rebase_module_data_dirs(monkeypatch, sys_modules, tmp_path) -> None:
    """import 済み store モジュールの import 時キャプチャ DATA_DIR を per-test に貼り替える。

    手動 patch 許可リスト（session_store / token_usage_store /
    optimize_history_store の 3 件をベタ書き）の **構造的代替**（#420）。
    許可リストは新 store 追加時に「次のモグラ」（漏れたモジュールの本番汚染）を
    生む欠陥があった。ここでは ``sys.modules`` を走査して module-level の
    ``DATA_DIR`` / ``_DATA_DIR_VAL`` を持つモジュールを機械的に発見し、その値と、
    旧 DATA_DIR 配下に派生した他の ``Path`` 属性（``SESSIONS_DB`` /
    ``PENDING_TRIGGER_FILE`` / ``HISTORY_ROOT`` 等）を per-test の ``tmp_path`` に
    rebase する。monkeypatch.setattr 経由なのでテスト終了時に自動復元される。
    """
    import os as _os
    from pathlib import Path as _Path

    real_home = (_Path.home() / ".claude").resolve()

    for mod in list(sys_modules.values()):
        if mod is None:
            continue
        for dd_attr in ("DATA_DIR", "_DATA_DIR_VAL"):
            old = getattr(mod, dd_attr, None)
            if not isinstance(old, _Path):
                continue
            try:
                old_resolved = old.resolve()
            except OSError:
                continue
            # 既に tmp 隔離済み（real home 配下でない）でも、テスト間で共有される
            # session dir を指しているとリークするため一律 per-test に貼り替える。
            # 派生 Path 属性（DATA_DIR 配下のもの）も合わせて rebase。
            for attr_name in dir(mod):
                if attr_name.startswith("__"):
                    continue
                val = getattr(mod, attr_name, None)
                if not isinstance(val, _Path):
                    continue
                try:
                    val_resolved = val.resolve()
                except OSError:
                    continue
                if val_resolved == old_resolved:
                    monkeypatch.setattr(mod, attr_name, tmp_path, raising=False)
                    continue
                # old DATA_DIR 配下の派生パス → tmp_path 配下へ rebase
                try:
                    rel = val_resolved.relative_to(old_resolved)
                except ValueError:
                    continue
                monkeypatch.setattr(mod, attr_name, tmp_path / rel, raising=False)
            break  # DATA_DIR を見つけたら _DATA_DIR_VAL は再走査しない


@pytest.fixture(autouse=True)
def _isolate_plugin_data(tmp_path, tmp_path_factory, monkeypatch, request):
    """per-test の追加隔離。

    トップレベル ``_isolate_data_dir_at_import`` が session 一時 dir を固定する
    ことで import 時キャプチャ組も実 home から構造的に隔離される（#420）。本
    fixture は per-test の env を tmp_path に上書きし、call-time に env を読む
    ストアをテスト単位で分離する（書き込みのテスト間漏れ防止）。

    さらに ``_rebase_module_data_dirs`` で import 済みの全 store モジュールの
    import 時キャプチャ DATA_DIR を per-test tmp_path に機械的に貼り替える。
    かつての手動 patch 許可リスト（session_store / token_usage_store /
    optimize_history_store の 3 件ベタ書き）はこの機械 sweep で撤去した。許可
    リストは新 store 追加時に「次のモグラ」を生む構造的欠陥で、機械 sweep が
    その根を断つ（漏れが原理的に起きない）。

    【HOME 隔離 — #119】
    ``CLAUDE_PLUGIN_DATA``(=DATA_DIR) 隔離は ``Path.home()`` 由来パスには効かない。
    run_evolve 系は後段フェーズで ``Path.home()/.claude/projects``（実環境
    ≈9925 jsonl / 1.9GB）を default 走査し、未隔離だと 1 件数十秒に膨張する
    （#457）。従来この HOME 隔離は ``skills/evolve/scripts/tests/`` の autouse と
    各テストの手動 ``isolate_home`` import 頼みで、「隔離を知らないと膨張する罠」が
    残っていた。ここで root 全体の autouse へ昇格し、全 testpath を一律に隔離する。
    実 HOME を要するテスト（live API bench / 実 PJ ingest）は ``_REAL_HOME_MARKERS``
    （real_home / bench / bench_ingest）で opt-out する。
    """
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    _rebase_module_data_dirs(monkeypatch, sys.modules, tmp_path)
    if not any(request.node.get_closest_marker(m) for m in _REAL_HOME_MARKERS):
        # 隔離 HOME は **test の tmp_path の外**（factory 側の別 basetemp）に作る。
        # tmp_path 直下に置くと、tmp_path を列挙・監視するテスト（fleet
        # enumerate_projects / *.iterdir() == [] の does-not-write 系）に
        # ``isolated-home`` が混入して壊れる（#119）。
        home_base = tmp_path_factory.mktemp("isolated-home-base")
        isolate_home(monkeypatch, home_base)
