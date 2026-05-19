"""LSP導入提案機能のテスト。

build_lsp_suggestion_section の振る舞いを検証:
- .lsp.json が存在しない場合に提案セクションが生成される
- .lsp.json が存在する場合はスキップ
- 検出言語に応じた提案が含まれる
"""
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
_LIB = _PLUGIN_ROOT / "scripts" / "lib"
_SCRIPTS = _PLUGIN_ROOT / "scripts"
for _p in (_LIB, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from audit.sections import build_lsp_suggestion_section  # noqa: E402


def _write_py_files(tmp_path: Path, n: int = 3) -> None:
    for i in range(n):
        (tmp_path / f"module_{i}.py").write_text(f"x = {i}")


def _write_ts_files(tmp_path: Path, n: int = 3) -> None:
    for i in range(n):
        (tmp_path / f"app_{i}.ts").write_text(f"const x{i} = {i};")


def test_no_lsp_json_with_python_files(tmp_path):
    """Pythonファイルがあって.lsp.jsonがない場合に提案が生成される。"""
    _write_py_files(tmp_path)
    result = build_lsp_suggestion_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert "LSP" in combined
    assert "pylsp" in combined


def test_no_lsp_json_with_typescript_files(tmp_path):
    """TypeScriptファイルがあって.lsp.jsonがない場合に提案が生成される。"""
    _write_ts_files(tmp_path)
    result = build_lsp_suggestion_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert "typescript-language-server" in combined


def test_lsp_json_already_exists(tmp_path):
    """既に.lsp.jsonが存在する場合はNoneを返す。"""
    _write_py_files(tmp_path)
    (tmp_path / ".lsp.json").write_text('{"python": {"command": "pylsp"}}')
    result = build_lsp_suggestion_section(tmp_path)
    assert result is None


def test_no_supported_languages(tmp_path):
    """対応言語ファイルがない場合はNoneを返す。"""
    (tmp_path / "README.md").write_text("# hello")
    result = build_lsp_suggestion_section(tmp_path)
    assert result is None


def test_multiple_languages(tmp_path):
    """複数言語が混在する場合、すべての提案が含まれる。"""
    _write_py_files(tmp_path)
    _write_ts_files(tmp_path)
    result = build_lsp_suggestion_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert "pylsp" in combined
    assert "typescript-language-server" in combined


def test_lsp_json_config_example_included(tmp_path):
    """提案セクションに.lsp.json設定例が含まれる。"""
    _write_py_files(tmp_path)
    result = build_lsp_suggestion_section(tmp_path)
    assert result is not None
    combined = "\n".join(result)
    assert ".lsp.json" in combined


def test_partial_lsp_json_missing_language(tmp_path):
    """既存の.lsp.jsonが存在しても対応言語をカバーしていればNoneを返す。"""
    _write_py_files(tmp_path)
    (tmp_path / ".lsp.json").write_text('{"python": {"command": "pylsp"}}')
    result = build_lsp_suggestion_section(tmp_path)
    assert result is None


def test_malformed_lsp_json_treated_as_absent(tmp_path):
    """.lsp.jsonが壊れたJSONの場合は「存在しない」扱いになり提案が生成される。"""
    _write_py_files(tmp_path)
    (tmp_path / ".lsp.json").write_text("{not valid json")
    result = build_lsp_suggestion_section(tmp_path)
    assert result is not None
    assert "pylsp" in "\n".join(result)


def test_files_in_excluded_dirs_not_counted(tmp_path):
    """node_modules/.venv 等の除外ディレクトリ内ファイルはカウントしない。"""
    for excluded in ("node_modules", ".venv", "__pycache__"):
        d = tmp_path / excluded
        d.mkdir()
        for i in range(5):
            (d / f"mod_{i}.py").write_text("x = 1")
    result = build_lsp_suggestion_section(tmp_path)
    assert result is None


def test_file_count_below_threshold_returns_none(tmp_path):
    """閾値(3)未満のファイル数では提案しない。"""
    for i in range(2):
        (tmp_path / f"mod_{i}.py").write_text(f"x = {i}")
    result = build_lsp_suggestion_section(tmp_path)
    assert result is None


def test_go_language_detected(tmp_path):
    """Goファイルが3件以上あれば gopls の提案が含まれる。"""
    for i in range(3):
        (tmp_path / f"main_{i}.go").write_text(f"package main\nfunc f{i}() {{}}")
    result = build_lsp_suggestion_section(tmp_path)
    assert result is not None
    assert "gopls" in "\n".join(result)


def test_generate_report_includes_lsp_section(tmp_path):
    """generate_report が project_dir にPythonファイルある場合 LSP セクションを含む。"""
    from audit.report import generate_report
    for i in range(3):
        (tmp_path / f"m_{i}.py").write_text(f"x={i}")
    report = generate_report(
        artifacts={},
        violations=[],
        usage={},
        duplicates=[],
        advisories=[],
        project_dir=tmp_path,
    )
    assert "LSP Setup Recommendation" in report
