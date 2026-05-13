"""test-guard: 各 PJ で no-llm-in-tests / pytest-no-llm の導入状況を可視化する。

責務: 検出と一覧表示のみ。配布・install は scope 外 (senior 指摘: rl-anything は
CC plugin なので Python 依存配布の主体にはしない)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

LANG_MARKERS: dict[str, tuple[str, ...]] = {
    "python": ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile",
               "setup.cfg", "tox.ini", "pytest.ini", "conftest.py"),
    "js": ("package.json",),
    "ruby": ("Gemfile",),
    "go": ("go.mod",),
}

LLM_SDK_HINTS_PYTHON = ("anthropic", "openai")
LLM_SDK_HINTS_JS = ("@anthropic-ai/sdk", "openai")

TEST_FRAMEWORK_HINTS_PYTHON = ("pytest", "unittest", "nose")
TEST_FRAMEWORK_HINTS_JS = ("jest", "vitest", "mocha", "ava", "playwright/test")


@dataclass
class TestGuardRow:
    pj_name: str
    pj_path: Path
    languages: set[str]
    uses_llm: bool          # any SDK referenced in deps
    has_tests: bool         # test framework configured or test files present
    has_precommit_hook: bool   # no-llm-in-tests in .pre-commit-config.yaml
    has_pytest_no_llm: bool    # pytest-no-llm in Python deps

    @property
    def needs_attention(self) -> bool:
        """LLM 利用 + テスト存在 + ガード未導入 → 要対応 (高優先度)。"""
        if not self.uses_llm or not self.has_tests:
            return False
        if not self.has_precommit_hook:
            return True
        if "python" in self.languages and not self.has_pytest_no_llm:
            return True
        return False

    @property
    def preventive_candidate(self) -> bool:
        """LLM 利用 + テスト無し + pre-commit hook 未導入 → 予防導入候補。
        pytest-no-llm は Python テスト追加時に同時導入が自然なので、テスト無しの段階では
        precommit hook のみを判定基準にする。
        """
        return self.uses_llm and not self.has_tests and not self.has_precommit_hook


def detect_languages(pj_path: Path) -> set[str]:
    langs: set[str] = set()
    for lang, markers in LANG_MARKERS.items():
        if any((pj_path / m).is_file() for m in markers):
            langs.add(lang)
    return langs


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _python_deps_text(pj_path: Path) -> str:
    parts = []
    for name in ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile"):
        p = pj_path / name
        if p.is_file():
            parts.append(_read_text_safe(p))
    return "\n".join(parts)


def _js_deps_text(pj_path: Path) -> str:
    p = pj_path / "package.json"
    return _read_text_safe(p) if p.is_file() else ""


def uses_llm_sdk(pj_path: Path, languages: set[str]) -> bool:
    if "python" in languages:
        text = _python_deps_text(pj_path).lower()
        if any(h in text for h in LLM_SDK_HINTS_PYTHON):
            return True
    if "js" in languages:
        text = _js_deps_text(pj_path).lower()
        if any(h in text for h in LLM_SDK_HINTS_JS):
            return True
    return False


def has_tests(pj_path: Path, languages: set[str]) -> bool:
    """テストフレームワークの設定 or テストファイルの存在を検出。"""
    # 設定ファイル: フレームワーク導入の最強シグナル
    if (pj_path / "pytest.ini").is_file():
        return True
    if (pj_path / "conftest.py").is_file():
        return True
    if "python" in languages:
        text = _python_deps_text(pj_path).lower()
        if any(h in text for h in TEST_FRAMEWORK_HINTS_PYTHON):
            return True
    if "js" in languages:
        text = _js_deps_text(pj_path).lower()
        if any(h in text for h in TEST_FRAMEWORK_HINTS_JS):
            return True
        # package.json に test script が定義されてれば「テストを意図している」
        if '"test"' in text and "no test specified" not in text:
            return True
    # ファイル探索: フレームワーク設定なしでも test_*.py / *_test.go 等があればテストあり
    for pat in ("test_*.py", "*_test.py", "*.test.ts", "*.test.tsx",
                "*.test.js", "*.spec.ts", "*.spec.js", "*_test.go", "*_spec.rb"):
        try:
            if any(p for p in pj_path.rglob(pat)
                   if "node_modules" not in p.parts and ".venv" not in p.parts):
                return True
        except (OSError, PermissionError):
            continue
    return False


def has_precommit_hook(pj_path: Path) -> bool:
    cfg = pj_path / ".pre-commit-config.yaml"
    if not cfg.is_file():
        return False
    return "no-llm-in-tests" in _read_text_safe(cfg)


def has_pytest_no_llm(pj_path: Path) -> bool:
    return "pytest-no-llm" in _python_deps_text(pj_path).lower()


def collect_test_guard_rows(projects: Iterable[Path]) -> list[TestGuardRow]:
    rows: list[TestGuardRow] = []
    for pj in projects:
        langs = detect_languages(pj)
        rows.append(TestGuardRow(
            pj_name=pj.name,
            pj_path=pj,
            languages=langs,
            uses_llm=uses_llm_sdk(pj, langs),
            has_tests=has_tests(pj, langs),
            has_precommit_hook=has_precommit_hook(pj),
            has_pytest_no_llm=has_pytest_no_llm(pj) if "python" in langs else False,
        ))
    return rows


def format_test_guard_table(rows: list[TestGuardRow]) -> str:
    if not rows:
        return "[test-guard] tracked PJ がありません。\n"
    headers = ["PJ", "LANGS", "LLM?", "TESTS?", "PRECOMMIT", "PYTEST-NO-LLM", "ACTION"]
    body = []
    for r in rows:
        langs = ",".join(sorted(r.languages)) or "-"
        llm = "yes" if r.uses_llm else "no"
        tests = "yes" if r.has_tests else "no"
        pre = "✓" if r.has_precommit_hook else "✗"
        pyg = "✓" if r.has_pytest_no_llm else ("-" if "python" not in r.languages else "✗")
        if r.needs_attention:
            action = "install guard"
        elif r.preventive_candidate:
            action = "preventive (no tests yet)"
        else:
            action = "ok"
        body.append([r.pj_name, langs, llm, tests, pre, pyg, action])
    rows_all = [headers] + body
    widths = [max(len(row[i]) for row in rows_all) for i in range(len(headers))]
    lines = []
    for i, row in enumerate(rows_all):
        line = "  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row))
        lines.append(line)
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    needs = sum(1 for r in rows if r.needs_attention)
    preventive = sum(1 for r in rows if r.preventive_candidate)
    summary = (
        f"\n[test-guard] 要対応 {needs} PJ / 予防導入候補 {preventive} PJ "
        f"(全 {len(rows)} PJ 中)\n"
    )
    return "\n".join(lines) + "\n" + summary
