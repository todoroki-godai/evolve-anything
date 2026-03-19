"""Tests for happy-path-test-detection in verification_catalog."""
import sys
from pathlib import Path

import pytest

# ── path setup ────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.verification_catalog import (
    HAPPY_PATH_MIN_PATTERNS,
    VERIFICATION_CATALOG,
    _CONTENT_KEYWORDS_MAP,
    _DETECTION_FN_DISPATCH,
    _detect_pipeline_functions,
    _find_test_files,
    check_verification_installed,
    detect_happy_path_test_gap,
    detect_verification_needs,
)

# ══════════════════════════════════════════════════════
# ヘルパー: パイプラインコードの生成
# ══════════════════════════════════════════════════════

_PY_PIPELINE_CODE = """\
def run_pipeline(data):
    result = step_validate(data)
    result = step_transform(result)
    result = step_save(result)
    return result
"""

_PY_PIPELINE_PHASE = """\
def run_pipeline(data):
    result = phase_init(data)
    result = phase_process(result)
    result = phase_finalize(result)
    return result
"""

_PY_PIPELINE_LOOP = """\
def run_pipeline(data):
    steps = [validate, transform, save]
    for step in steps:
        data = step(data)
    return data
"""

_TS_PIPELINE_CODE = """\
async function runPipeline(data: any) {
    const validated = await stepValidate(data);
    const transformed = await stepTransform(validated);
    const saved = await stepSave(transformed);
    return saved;
}
"""

_PY_NO_PIPELINE = """\
def helper(x):
    return x + 1
"""

_PY_PIPELINE_TEST = """\
def test_run_pipeline():
    result = run_pipeline({"key": "value"})
    assert result is not None
"""


def _create_pipeline_files(tmp_path, count, code=None):
    """パイプラインコードを含む Python ファイルを count 個作成する。"""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    content = code or _PY_PIPELINE_CODE
    for i in range(count):
        (src / f"pipeline_{i}.py").write_text(content)


def _create_pipeline_test(tmp_path, source_name="pipeline_0", test_dir=None):
    """パイプラインのテストファイルを作成する。"""
    if test_dir is None:
        test_dir = tmp_path / "src"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / f"test_{source_name}.py").write_text(_PY_PIPELINE_TEST)


# ══════════════════════════════════════════════════════
# Task 1.1: detect_happy_path_test_gap() テスト
# ══════════════════════════════════════════════════════


class TestDetectHappyPathTestGap:
    def test_pipeline_detected_test_missing_applicable(self, tmp_path):
        """パイプライン検出+テスト欠落 → applicable=True。"""
        _create_pipeline_files(tmp_path, HAPPY_PATH_MIN_PATTERNS + 1)
        result = detect_happy_path_test_gap(tmp_path)
        assert result["applicable"] is True
        assert len(result["evidence"]) >= HAPPY_PATH_MIN_PATTERNS
        assert 0.0 < result["confidence"] <= 0.7

    def test_pipeline_with_test_not_applicable(self, tmp_path):
        """パイプライン検出+テスト存在 → applicable=False。"""
        _create_pipeline_files(tmp_path, HAPPY_PATH_MIN_PATTERNS + 1)
        # 全てのパイプラインファイルにテストを作成
        for i in range(HAPPY_PATH_MIN_PATTERNS + 1):
            _create_pipeline_test(tmp_path, f"pipeline_{i}")
        result = detect_happy_path_test_gap(tmp_path)
        assert result["applicable"] is False

    def test_below_threshold_not_applicable(self, tmp_path):
        """パイプライン関数が閾値未満 → applicable=False。"""
        _create_pipeline_files(tmp_path, HAPPY_PATH_MIN_PATTERNS - 1)
        result = detect_happy_path_test_gap(tmp_path)
        assert result["applicable"] is False
        assert result["confidence"] == 0.0

    def test_nonexistent_dir(self, tmp_path):
        """存在しないディレクトリ → safe_result。"""
        result = detect_happy_path_test_gap(tmp_path / "nonexistent")
        assert result["applicable"] is False
        assert result["confidence"] == 0.0
        assert result["evidence"] == []

    def test_evidence_is_file_path_only(self, tmp_path):
        """evidence はファイルパスのみ（関数名を含まない）。"""
        _create_pipeline_files(tmp_path, HAPPY_PATH_MIN_PATTERNS + 1)
        result = detect_happy_path_test_gap(tmp_path)
        assert result["applicable"] is True
        for ev in result["evidence"]:
            # パスのみ — 関数名 (run_pipeline) を含まない
            assert "run_pipeline" not in ev
            assert "/" in ev or ev.endswith(".py")

    def test_confidence_capped_at_07(self, tmp_path):
        """confidence は 0.7 上限。"""
        _create_pipeline_files(tmp_path, 10)
        result = detect_happy_path_test_gap(tmp_path)
        assert result["confidence"] <= 0.7

    def test_evidence_max_10(self, tmp_path):
        """evidence は最大 10 件。"""
        _create_pipeline_files(tmp_path, 15)
        result = detect_happy_path_test_gap(tmp_path)
        assert len(result["evidence"]) <= 10

    def test_llm_escalation_prompt_present(self, tmp_path):
        """applicable=True の場合 llm_escalation_prompt が含まれる。"""
        _create_pipeline_files(tmp_path, HAPPY_PATH_MIN_PATTERNS + 1)
        result = detect_happy_path_test_gap(tmp_path)
        assert "llm_escalation_prompt" in result

    def test_test_files_not_scanned_as_pipeline(self, tmp_path):
        """テストファイル自体はパイプラインとして検出されない。"""
        src = tmp_path / "src"
        src.mkdir(exist_ok=True)
        for i in range(5):
            (src / f"test_pipeline_{i}.py").write_text(_PY_PIPELINE_CODE)
        result = detect_happy_path_test_gap(tmp_path)
        assert result["applicable"] is False


# ══════════════════════════════════════════════════════
# Task 1.2: パイプライン検出パターンテスト
# ══════════════════════════════════════════════════════


class TestDetectPipelineFunctions:
    def test_step_pattern(self, tmp_path):
        """step_* パターンの検出。"""
        f = tmp_path / "mod.py"
        f.write_text(_PY_PIPELINE_CODE)
        funcs = _detect_pipeline_functions(f)
        assert len(funcs) >= 1
        assert "run_pipeline" in funcs

    def test_phase_pattern(self, tmp_path):
        """phase_* パターンの検出。"""
        f = tmp_path / "mod.py"
        f.write_text(_PY_PIPELINE_PHASE)
        funcs = _detect_pipeline_functions(f)
        assert "run_pipeline" in funcs

    def test_stage_pattern(self, tmp_path):
        """stage_* パターンの検出。"""
        code = """\
def orchestrate():
    stage_prepare()
    stage_execute()
    stage_cleanup()
"""
        f = tmp_path / "mod.py"
        f.write_text(code)
        funcs = _detect_pipeline_functions(f)
        assert "orchestrate" in funcs

    def test_layer_pattern(self, tmp_path):
        """layer_* パターンの検出。"""
        code = """\
def process():
    layer_input()
    layer_transform()
    layer_output()
"""
        f = tmp_path / "mod.py"
        f.write_text(code)
        funcs = _detect_pipeline_functions(f)
        assert "process" in funcs

    def test_process_pattern(self, tmp_path):
        """process_* パターンの検出。"""
        code = """\
def run():
    process_input()
    process_validate()
    process_output()
"""
        f = tmp_path / "mod.py"
        f.write_text(code)
        funcs = _detect_pipeline_functions(f)
        assert "run" in funcs

    def test_loop_pattern(self, tmp_path):
        """for step in steps ループパターンの検出。"""
        f = tmp_path / "mod.py"
        f.write_text(_PY_PIPELINE_LOOP)
        funcs = _detect_pipeline_functions(f)
        assert "run_pipeline" in funcs

    def test_typescript_camelcase(self, tmp_path):
        """TypeScript camelCase await チェーンパターンの検出。"""
        f = tmp_path / "mod.ts"
        f.write_text(_TS_PIPELINE_CODE)
        funcs = _detect_pipeline_functions(f)
        assert "runPipeline" in funcs

    def test_fewer_than_3_calls_not_detected(self, tmp_path):
        """3 未満のステップ呼び出しはパイプラインとして検出しない。"""
        code = """\
def simple():
    step_validate()
    step_save()
"""
        f = tmp_path / "mod.py"
        f.write_text(code)
        funcs = _detect_pipeline_functions(f)
        assert len(funcs) == 0

    def test_no_pipeline(self, tmp_path):
        """パイプラインパターンなし。"""
        f = tmp_path / "mod.py"
        f.write_text(_PY_NO_PIPELINE)
        funcs = _detect_pipeline_functions(f)
        assert len(funcs) == 0

    def test_nonexistent_file(self, tmp_path):
        """存在しないファイル。"""
        funcs = _detect_pipeline_functions(tmp_path / "missing.py")
        assert funcs == []


# ══════════════════════════════════════════════════════
# Task 1.3: テストファイル対応解決テスト
# ══════════════════════════════════════════════════════


class TestFindTestFiles:
    def test_same_directory_test_prefix(self, tmp_path):
        """同ディレクトリの test_*.py。"""
        src = tmp_path / "src"
        src.mkdir()
        source = src / "pipeline.py"
        source.write_text(_PY_PIPELINE_CODE)
        test_file = src / "test_pipeline.py"
        test_file.write_text(_PY_PIPELINE_TEST)
        found = _find_test_files(source, tmp_path)
        assert test_file in found

    def test_same_directory_test_suffix(self, tmp_path):
        """同ディレクトリの *_test.py。"""
        src = tmp_path / "src"
        src.mkdir()
        source = src / "pipeline.py"
        source.write_text(_PY_PIPELINE_CODE)
        test_file = src / "pipeline_test.py"
        test_file.write_text(_PY_PIPELINE_TEST)
        found = _find_test_files(source, tmp_path)
        assert test_file in found

    def test_source_parent_tests_subdir(self, tmp_path):
        """ソース親の tests/ サブディレクトリ。"""
        src = tmp_path / "src"
        src.mkdir()
        source = src / "pipeline.py"
        source.write_text(_PY_PIPELINE_CODE)
        tests_dir = src / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_pipeline.py"
        test_file.write_text(_PY_PIPELINE_TEST)
        found = _find_test_files(source, tmp_path)
        assert test_file in found

    def test_project_root_tests_dir(self, tmp_path):
        """プロジェクトルート直下の tests/。"""
        src = tmp_path / "src"
        src.mkdir()
        source = src / "pipeline.py"
        source.write_text(_PY_PIPELINE_CODE)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_pipeline.py"
        test_file.write_text(_PY_PIPELINE_TEST)
        found = _find_test_files(source, tmp_path)
        assert test_file in found

    def test_typescript_test_suffix(self, tmp_path):
        """TypeScript *.test.ts。"""
        src = tmp_path / "src"
        src.mkdir()
        source = src / "pipeline.ts"
        source.write_text(_TS_PIPELINE_CODE)
        test_file = src / "pipeline.test.ts"
        test_file.write_text("test('works', () => {});")
        found = _find_test_files(source, tmp_path)
        assert test_file in found

    def test_typescript_dunder_tests_dir(self, tmp_path):
        """TypeScript __tests__/ サブディレクトリ。"""
        src = tmp_path / "src"
        src.mkdir()
        source = src / "pipeline.ts"
        source.write_text(_TS_PIPELINE_CODE)
        tests_dir = src / "__tests__"
        tests_dir.mkdir()
        test_file = tests_dir / "pipeline.test.ts"
        test_file.write_text("test('works', () => {});")
        found = _find_test_files(source, tmp_path)
        assert test_file in found

    def test_no_test_file_found(self, tmp_path):
        """テストファイルが見つからない場合。"""
        src = tmp_path / "src"
        src.mkdir()
        source = src / "pipeline.py"
        source.write_text(_PY_PIPELINE_CODE)
        found = _find_test_files(source, tmp_path)
        assert found == []


# ══════════════════════════════════════════════════════
# Task 1.3 (cont): content-aware 検出テスト
# ══════════════════════════════════════════════════════


class TestHappyPathContentAwareInstalled:
    def test_filename_match(self, tmp_path):
        """test-happy-path-first.md が存在 → installed。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "test-happy-path-first.md").write_text("# ハッピーパス")
        entry = {"id": "happy-path-test-verification", "rule_filename": "test-happy-path-first.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_content_keyword_ja(self, tmp_path):
        """別ルールに「ハッピーパス」キーワード → installed。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "testing.md").write_text("テストはハッピーパスから書く。")
        entry = {"id": "happy-path-test-verification", "rule_filename": "test-happy-path-first.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_content_keyword_en(self, tmp_path):
        """'happy path' キーワードでマッチ。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "testing.md").write_text("Write happy path tests first.")
        entry = {"id": "happy-path-test-verification", "rule_filename": "test-happy-path-first.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_content_keyword_e2e(self, tmp_path):
        """'E2Eテスト' キーワードでマッチ。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "testing.md").write_text("正常系E2Eテストを最初に書く。")
        entry = {"id": "happy-path-test-verification", "rule_filename": "test-happy-path-first.md"}
        assert check_verification_installed(entry, tmp_path) is True

    def test_no_match(self, tmp_path):
        """キーワードなし → not installed。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "other.md").write_text("# Unrelated rule")
        entry = {"id": "happy-path-test-verification", "rule_filename": "test-happy-path-first.md"}
        assert check_verification_installed(entry, tmp_path) is False


# ══════════════════════════════════════════════════════
# Task 1.4: VERIFICATION_CATALOG エントリ存在テスト
# ══════════════════════════════════════════════════════


class TestHappyPathCatalogEntry:
    def test_entry_exists(self):
        ids = [e["id"] for e in VERIFICATION_CATALOG]
        assert "happy-path-test-verification" in ids

    def test_entry_structure(self):
        entry = next(e for e in VERIFICATION_CATALOG if e["id"] == "happy-path-test-verification")
        assert entry["type"] == "rule"
        assert entry["detection_fn"] == "detect_happy_path_test_gap"
        assert entry["applicability"] == "conditional"
        assert entry["rule_filename"] == "test-happy-path-first.md"

    def test_rule_template_3_lines(self):
        entry = next(e for e in VERIFICATION_CATALOG if e["id"] == "happy-path-test-verification")
        lines = entry["rule_template"].strip().split("\n")
        assert len(lines) <= 3

    def test_detection_fn_registered(self):
        """_DETECTION_FN_DISPATCH に登録されている。"""
        assert "detect_happy_path_test_gap" in _DETECTION_FN_DISPATCH

    def test_content_keywords_registered(self):
        """_CONTENT_KEYWORDS_MAP に登録されている。"""
        assert "happy-path-test-verification" in _CONTENT_KEYWORDS_MAP
        keywords = _CONTENT_KEYWORDS_MAP["happy-path-test-verification"]
        assert "ハッピーパス" in keywords
        assert "happy path" in keywords


# ══════════════════════════════════════════════════════
# Task 1.5: RECOMMENDED_ARTIFACTS エントリ存在テスト
# ══════════════════════════════════════════════════════

# discover の RECOMMENDED_ARTIFACTS テストは既存テストファイルに追加
# ここでは verification_needs 統合テストを実施


class TestHappyPathVerificationNeeds:
    def test_detected_in_needs(self, tmp_path):
        """パイプラインコードがあり、テストがない → needs に含まれる。"""
        _create_pipeline_files(tmp_path, HAPPY_PATH_MIN_PATTERNS + 1)
        needs = detect_verification_needs(tmp_path)
        ids = [n["id"] for n in needs]
        assert "happy-path-test-verification" in ids

    def test_not_detected_below_threshold(self, tmp_path):
        """パイプラインコードが閾値未満 → needs に含まれない。"""
        _create_pipeline_files(tmp_path, HAPPY_PATH_MIN_PATTERNS - 1)
        needs = detect_verification_needs(tmp_path)
        ids = [n["id"] for n in needs]
        assert "happy-path-test-verification" not in ids

    def test_installed_skipped(self, tmp_path):
        """ルールインストール済みならスキップ。"""
        _create_pipeline_files(tmp_path, HAPPY_PATH_MIN_PATTERNS + 1)
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "test-happy-path-first.md").write_text("# rule")
        needs = detect_verification_needs(tmp_path)
        ids = [n["id"] for n in needs]
        assert "happy-path-test-verification" not in ids
