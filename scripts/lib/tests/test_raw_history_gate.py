"""raw_history_gate（optimize_history_store の raw read allowlist gate）のテスト（#402 PR-2 段階2 §5）。

決定論・LLM 非依存。tmp_path に疑似リポジトリツリー（scripts/lib/*.py）を作って静的突合する。
判定は **bare 名でなく import 元**で行う（skill_declaration_reachability.py の AST 静的解析の
作法に揃える）。

段階2 のスコープ: checker 本体 + 期待違反 fixture のテストまで。production ツリー全体への
gate 有効化（実 repo_root への適用）は段階4（未移行 reader が残るため必ず失敗する）。
本ファイルは実 repo を一切 scan しない。
"""
from __future__ import annotations

import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

import raw_history_gate as gate  # noqa: E402


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


# --- 検出: from-import 形 ----------------------------------------------------


def test_detects_from_import_direct_call(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "from optimize_history_store import load_history\n\n"
        "def foo(slug):\n"
        "    return load_history(slug)\n",
    )
    report = gate.check_raw_history_reads(root)
    assert [v.callsite for v in report.violations] == ["scripts/lib/reader.py:foo"]
    assert report.violations[0].called == "load_history"
    assert report.ok is False


def test_detects_load_raw_history_variant(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "from optimize_history_store import load_raw_history\n\n"
        "def foo(slug):\n"
        "    return load_raw_history(slug)\n",
    )
    report = gate.check_raw_history_reads(root)
    assert len(report.violations) == 1
    assert report.violations[0].called == "load_raw_history"


def test_detects_load_raw_history_with_aliases_variant(tmp_path: Path) -> None:
    """#402 段階3 M-B: entry 検索用に新設した raw reader も gate の射程に入っている
    （新規 reader が allowlist 未整備のまま素通りする＝§5 の S2 が防ごうとした失敗
    モードをこの PR 自身が作らないことの回帰防止）。
    """
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "from optimize_history_store import load_raw_history_with_aliases\n\n"
        "def foo(entry_id, slug):\n"
        "    return load_raw_history_with_aliases(slug)\n",
    )
    report = gate.check_raw_history_reads(root)
    assert len(report.violations) == 1
    assert report.violations[0].called == "load_raw_history_with_aliases"


def test_detects_import_alias_of_function(tmp_path: Path) -> None:
    """``from optimize_history_store import load_history as _lh`` の後 ``_lh(...)`` 呼出し。"""
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "from optimize_history_store import load_history as _lh\n\n"
        "def foo(slug):\n"
        "    return _lh(slug)\n",
    )
    report = gate.check_raw_history_reads(root)
    assert len(report.violations) == 1
    assert report.violations[0].called == "load_history"


# --- 検出: module attribute 形 ------------------------------------------------


def test_detects_module_attribute_call_with_alias(tmp_path: Path) -> None:
    """``import optimize_history_store as store`` の後 ``store.load_history(...)`` 呼出し。"""
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "import optimize_history_store as store\n\n"
        "def foo(slug):\n"
        "    return store.load_history(slug)\n",
    )
    report = gate.check_raw_history_reads(root)
    assert len(report.violations) == 1
    assert report.violations[0].called == "load_history"


def test_detects_module_attribute_call_without_alias(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "import optimize_history_store\n\n"
        "def foo(slug):\n"
        "    return optimize_history_store.load_raw_history(slug)\n",
    )
    report = gate.check_raw_history_reads(root)
    assert len(report.violations) == 1
    assert report.violations[0].called == "load_raw_history"


# --- FP guard: 同名別モジュール（bare 名一致ではなく import 元で判定） --------


def test_false_positive_guard_same_name_different_module(tmp_path: Path) -> None:
    """`fitness_evolution.load_history()` は別実装（同名別物）なので誤検出しない
    （trigger_engine/session_corrections.py:53 の実コーパスパターン・§5 の FP 較正）。
    """
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/fitness_evolution.py",
        "def load_history(history_file=None):\n    return []\n",
    )
    _write(
        root / "scripts/lib/session_corrections.py",
        "import fitness_evolution\n\n"
        "def use():\n"
        "    return fitness_evolution.load_history()\n",
    )
    report = gate.check_raw_history_reads(root)
    assert report.violations == []
    assert report.ok is True


def test_false_positive_guard_local_function_named_load_history(tmp_path: Path) -> None:
    """自ファイル内で定義された同名 ``load_history`` を呼ぶだけ（import なし）は対象外。"""
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/local_only.py",
        "def load_history():\n    return []\n\n"
        "def use():\n"
        "    return load_history()\n",
    )
    report = gate.check_raw_history_reads(root)
    assert report.violations == []


# --- allowlist -----------------------------------------------------------


def test_allowlisted_callsite_passes(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "from optimize_history_store import load_history\n\n"
        "def foo(slug):\n"
        "    return load_history(slug)\n",
    )
    report = gate.check_raw_history_reads(root, allowlist=["scripts/lib/reader.py:foo"])
    assert report.violations == []
    assert report.stale_allowlist == []
    assert report.ok is True


def test_stale_allowlist_entry_flagged(tmp_path: Path) -> None:
    """allowlist に列挙したが実際には該当する呼び出しが無いエントリは stale として fail。"""
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/reader.py", "def noop():\n    return 1\n")
    report = gate.check_raw_history_reads(
        root, allowlist=["scripts/lib/reader.py:foo_no_longer_exists"]
    )
    assert report.violations == []
    assert report.stale_allowlist == ["scripts/lib/reader.py:foo_no_longer_exists"]
    assert report.ok is False


def test_allowlist_entry_only_exempts_its_own_callsite(tmp_path: Path) -> None:
    """allowlist は関数/callsite 単位。別関数からの呼び出しは別途 violation になる
    （§5 の「ファイル単位にしない」設計判断: fitness_evolution.py のような同一ファイル内
    raw dedup / 業務読取の混在を許可リストが取り違えないことを保証する）。
    """
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "from optimize_history_store import load_history\n\n"
        "def allowed_dedup(slug):\n"
        "    return load_history(slug)\n\n"
        "def business_read(slug):\n"
        "    return load_history(slug)\n",
    )
    report = gate.check_raw_history_reads(
        root, allowlist=["scripts/lib/reader.py:allowed_dedup"]
    )
    assert [v.callsite for v in report.violations] == ["scripts/lib/reader.py:business_read"]


# --- callsite の qualname 解決 ----------------------------------------------


def test_module_level_call_uses_module_sentinel(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "from optimize_history_store import load_history\n\n"
        "history = load_history('proj')\n",
    )
    report = gate.check_raw_history_reads(root)
    assert report.violations[0].callsite == "scripts/lib/reader.py:<module>"


def test_nested_function_qualname(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "from optimize_history_store import load_history\n\n"
        "def outer(slug):\n"
        "    def inner():\n"
        "        return load_history(slug)\n"
        "    return inner()\n",
    )
    report = gate.check_raw_history_reads(root)
    assert report.violations[0].callsite == "scripts/lib/reader.py:outer.inner"


def test_class_method_qualname(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/reader.py",
        "from optimize_history_store import load_history\n\n"
        "class Reader:\n"
        "    def read(self, slug):\n"
        "        return load_history(slug)\n",
    )
    report = gate.check_raw_history_reads(root)
    assert report.violations[0].callsite == "scripts/lib/reader.py:Reader.read"


# --- production ツリーのファイル選定 -----------------------------------------


def test_tests_directory_excluded(tmp_path: Path) -> None:
    """tests/ 配下は fixture セットアップで raw を呼ぶことが多いため対象外。"""
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/tests/test_reader.py",
        "from optimize_history_store import load_history\n\n"
        "def test_it():\n"
        "    return load_history('proj')\n",
    )
    report = gate.check_raw_history_reads(root)
    assert report.violations == []


def test_test_prefixed_file_outside_tests_dir_excluded(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root / "scripts/lib/test_helper.py",
        "from optimize_history_store import load_history\n\n"
        "def test_thing():\n"
        "    return load_history('proj')\n",
    )
    report = gate.check_raw_history_reads(root)
    assert report.violations == []


def test_scans_skills_scripts_tree_too(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(
        root / "skills/demo/scripts/reader.py",
        "from optimize_history_store import load_history\n\n"
        "def foo(slug):\n"
        "    return load_history(slug)\n",
    )
    report = gate.check_raw_history_reads(root)
    assert [v.callsite for v in report.violations] == ["skills/demo/scripts/reader.py:foo"]


def test_no_matches_reports_no_violations(tmp_path: Path) -> None:
    root = _make_repo(tmp_path)
    _write(root / "scripts/lib/plain.py", "def foo():\n    return 1\n")
    report = gate.check_raw_history_reads(root)
    assert report.violations == []
    assert report.stale_allowlist == []
    assert report.ok is True
