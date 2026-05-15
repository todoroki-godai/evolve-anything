"""verification_catalog の高度な検出関数（happy-path / cross-layer / IaC）。

`detect_happy_path_test_gap` (パイプライン関数の正常系E2Eテスト欠落検出) /
`detect_iac_project` (IaC マーカーファイル判定) /
`detect_cross_layer_consistency` (IaC PJ での環境変数 + AWS SDK 使用パターン) を提供する。
"""
import re
from pathlib import Path
from typing import Any, Dict, List

from .helpers import (
    _is_test_file,
    _iter_source_files,
    _safe_result,
)


def _happy_path_min_patterns() -> int:
    from . import HAPPY_PATH_MIN_PATTERNS
    return HAPPY_PATH_MIN_PATTERNS


def _min_cross_layer_patterns() -> int:
    from . import MIN_CROSS_LAYER_PATTERNS
    return MIN_CROSS_LAYER_PATTERNS


# ── ハッピーパステスト欠落検出 ──────────────────────────

# Python: step_*() / phase_*() / stage_*() / layer_*() / process_*() 呼び出し
_PIPELINE_CALL_PATTERN_PY = re.compile(
    r"(?:step|phase|stage|layer|process)_\w+\s*\(",
)
# TypeScript: await stepValidate() / phaseInit() 等 (camelCase)
_PIPELINE_CALL_PATTERN_TS = re.compile(
    r"await\s+(?:step|phase|stage|layer|process)\w+\s*\(",
)
# ループ型パイプライン: for step in steps / for phase in phases 等
_PIPELINE_LOOP_PATTERN = re.compile(
    r"for\s+\w+\s+in\s+(?:steps|phases|stages|layers|processes)\s*:",
)
# Python 関数定義
_PY_FUNC_DEF = re.compile(r"^def\s+(\w+)\s*\(", re.MULTILINE)
# TypeScript/JS 関数定義 (async function / function / const = async)
_TS_FUNC_DEF = re.compile(
    r"(?:async\s+)?function\s+(\w+)\s*\(|(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\(",
    re.MULTILINE,
)

_MIN_PIPELINE_CALLS = 3  # 1 関数内に 3 つ以上のステップ呼び出しで検出


def _detect_pipeline_functions(filepath: Path) -> List[str]:
    """ファイル内のパイプライン関数名を検出して返す。"""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
    except (PermissionError, OSError):
        return []

    is_ts = filepath.suffix in (".ts", ".tsx")
    call_pattern = _PIPELINE_CALL_PATTERN_TS if is_ts else _PIPELINE_CALL_PATTERN_PY
    func_def_pattern = _TS_FUNC_DEF if is_ts else _PY_FUNC_DEF

    pipeline_funcs: List[str] = []
    lines = content.split("\n")

    # 関数の範囲を特定して、呼び出しパターンを数える
    func_starts: List[tuple] = []  # (name, line_idx)
    for i, line in enumerate(lines):
        m = func_def_pattern.search(line)
        if m:
            name = m.group(1) or (m.group(2) if m.lastindex >= 2 else None)
            if name:
                func_starts.append((name, i))

    for idx, (name, start_line) in enumerate(func_starts):
        # 関数の終了行を推定（次の関数定義 or EOF）
        end_line = func_starts[idx + 1][1] if idx + 1 < len(func_starts) else len(lines)
        func_body = "\n".join(lines[start_line:end_line])

        call_count = len(call_pattern.findall(func_body))
        loop_count = len(_PIPELINE_LOOP_PATTERN.findall(func_body))

        if call_count >= _MIN_PIPELINE_CALLS or loop_count >= 1:
            pipeline_funcs.append(name)

    return pipeline_funcs


def _find_test_files(source_file: Path, project_dir: Path) -> List[Path]:
    """ソースファイルに対応するテストファイルを探索して返す。"""
    stem = source_file.stem
    suffix = source_file.suffix
    parent = source_file.parent
    found: List[Path] = []

    if suffix == ".py":
        candidates = [
            parent / f"test_{stem}.py",
            parent / f"{stem}_test.py",
            parent / "tests" / f"test_{stem}.py",
            parent / "tests" / f"{stem}_test.py",
            project_dir / "tests" / f"test_{stem}.py",
            project_dir / "tests" / f"{stem}_test.py",
        ]
    elif suffix in (".ts", ".tsx"):
        candidates = [
            parent / f"{stem}.test.ts",
            parent / f"{stem}.test.tsx",
            parent / "__tests__" / f"{stem}.test.ts",
            parent / "__tests__" / f"{stem}.test.tsx",
        ]
    else:
        return []

    for c in candidates:
        if c.exists():
            found.append(c)
    return found


def _test_has_function_call(test_files: List[Path], func_name: str) -> bool:
    """テストファイル内にパイプライン関数名の呼び出しがあるか判定する。"""
    for tf in test_files:
        try:
            content = tf.read_text(encoding="utf-8", errors="ignore")
            if func_name in content:
                return True
        except (PermissionError, OSError):
            continue
    return False


def detect_happy_path_test_gap(project_dir: Path) -> Dict[str, Any]:
    """happy-path-test-verification の検出関数。

    パイプライン/オーケストレーションコードを走査し、
    正常系E2Eテストが欠落しているケースを検出する。
    """
    if not project_dir.is_dir():
        return _safe_result("project_dir does not exist")

    evidence: List[str] = []
    try:
        for filepath in _iter_source_files(project_dir):
            if _is_test_file(filepath):
                continue
            pipeline_funcs = _detect_pipeline_functions(filepath)
            if not pipeline_funcs:
                continue

            # テストファイル探索
            test_files = _find_test_files(filepath, project_dir)
            # いずれかのパイプライン関数がテスト未呼び出しなら evidence に追加
            has_gap = False
            for fn in pipeline_funcs:
                if not _test_has_function_call(test_files, fn):
                    has_gap = True
                    break

            if has_gap:
                try:
                    rel = str(filepath.relative_to(project_dir))
                except ValueError:
                    rel = str(filepath)
                evidence.append(rel)
                if len(evidence) >= 10:
                    break
    except Exception as e:
        return _safe_result(str(e))

    count = len(evidence)
    if count >= _happy_path_min_patterns():
        confidence = min(0.7, 0.5 + count * 0.04)
        return {
            "applicable": True,
            "evidence": evidence,
            "confidence": confidence,
            "llm_escalation_prompt": (
                f"以下のプロジェクトで {count} 箇所のパイプライン/オーケストレーションコードに"
                f"正常系E2Eテストの欠落が検出されました。"
                f"「テストはハッピーパスから書く」ルールは有用ですか？"
                f"yes/no で回答してください。\n\n検出ファイル:\n" + "\n".join(evidence[:5])
            ),
        }
    return {
        "applicable": False,
        "evidence": evidence,
        "confidence": 0.0,
    }


# ── クロスレイヤー検出パターン ────────────────────────

# 環境変数参照: Python (os.environ.get/os.environ[]/os.getenv) / TS (process.env.)
_ENV_VAR_PY_RE = re.compile(
    r"(?:os\.environ\.get\(|os\.environ\[|os\.getenv\()",
)
_ENV_VAR_TS_RE = re.compile(r"process\.env\.\w+")

# AWS SDK: Python (boto3.client/resource) / TS (new *Client())
_AWS_SDK_PY_RE = re.compile(r"boto3\.(?:client|resource)\(")
_AWS_SDK_TS_RE = re.compile(r"new\s+\w+Client\(")

# IaC マーカー判定テーブル
_IAC_MARKERS = [
    # (check_fn, iac_type, marker_description)
    # check_fn: Path -> Optional[str] (marker_path or None)
]


def detect_iac_project(project_dir: Path) -> Dict[str, Any]:
    """IaC プロジェクト判定。マーカーファイル/ディレクトリの存在チェックで判定する。"""
    no_iac = {"is_iac": False, "iac_type": None, "marker_path": None}
    if not project_dir.is_dir():
        return no_iac

    # CDK
    cdk_json = project_dir / "cdk.json"
    if cdk_json.exists():
        return {"is_iac": True, "iac_type": "cdk", "marker_path": "cdk.json"}

    # SAM (template.yaml + AWSTemplateFormatVersion) — CDK の次に優先
    template_yaml = project_dir / "template.yaml"
    if template_yaml.exists():
        try:
            content = template_yaml.read_text(encoding="utf-8", errors="ignore")
            if "AWSTemplateFormatVersion" in content:
                return {"is_iac": True, "iac_type": "sam", "marker_path": "template.yaml"}
        except (PermissionError, OSError):
            pass

    # Serverless Framework
    for name in ("serverless.yml", "serverless.yaml"):
        if (project_dir / name).exists():
            return {"is_iac": True, "iac_type": "serverless", "marker_path": name}

    # CloudFormation 直書き (*.template.json / *.template.yaml)
    for pattern in ("*.template.json", "*.template.yaml"):
        for cf_file in project_dir.glob(pattern):
            try:
                content = cf_file.read_text(encoding="utf-8", errors="ignore")
                if "AWSTemplateFormatVersion" in content:
                    return {"is_iac": True, "iac_type": "cloudformation", "marker_path": cf_file.name}
            except (PermissionError, OSError):
                continue

    return no_iac


def detect_cross_layer_consistency(project_dir: Path) -> Dict[str, Any]:
    """クロスレイヤー整合性の検出関数。

    IaC プロジェクトでのみ環境変数参照・AWS SDK 使用をスキャンし、
    IaC 定義との突合を提案する。
    """
    if not project_dir.is_dir():
        return _safe_result("project_dir does not exist")

    # IaC ゲート
    iac_result = detect_iac_project(project_dir)
    if not iac_result["is_iac"]:
        return {"applicable": False, "evidence": [], "confidence": 0.0}

    evidence: List[str] = []
    detected_categories: set = set()

    try:
        for filepath in _iter_source_files(project_dir):
            if _is_test_file(filepath):
                continue
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
            except (PermissionError, OSError):
                continue

            try:
                rel = str(filepath.relative_to(project_dir))
            except ValueError:
                rel = str(filepath)

            # 環境変数参照チェック
            if filepath.suffix == ".py":
                if _ENV_VAR_PY_RE.search(content):
                    if rel not in evidence:
                        evidence.append(rel)
                    detected_categories.add("env_var")
            elif filepath.suffix in (".ts", ".tsx"):
                if _ENV_VAR_TS_RE.search(content):
                    if rel not in evidence:
                        evidence.append(rel)
                    detected_categories.add("env_var")

            # AWS SDK チェック
            if filepath.suffix == ".py":
                if _AWS_SDK_PY_RE.search(content):
                    if rel not in evidence:
                        evidence.append(rel)
                    detected_categories.add("aws_service")
            elif filepath.suffix in (".ts", ".tsx"):
                if _AWS_SDK_TS_RE.search(content):
                    if rel not in evidence:
                        evidence.append(rel)
                    detected_categories.add("aws_service")

            if len(evidence) >= 10:
                break
    except Exception as e:
        return _safe_result(str(e))

    count = len(evidence)
    categories = sorted(detected_categories)

    if count >= _min_cross_layer_patterns():
        confidence = min(0.7, 0.5 + count * 0.04)
        cat_labels = {"env_var": "環境変数参照", "aws_service": "AWS SDK使用"}
        cat_str = "・".join(cat_labels.get(c, c) for c in categories)
        return {
            "applicable": True,
            "evidence": evidence,
            "detected_categories": categories,
            "confidence": confidence,
            "llm_escalation_prompt": (
                f"以下のプロジェクトで {count} 箇所のクロスレイヤーパターン（{cat_str}）が検出されました。"
                f"IaC 定義ファイルとの整合性を確認してください。\n\n"
                f"検出ファイル:\n" + "\n".join(evidence[:5])
            ),
        }
    return {
        "applicable": False,
        "evidence": evidence,
        "detected_categories": categories,
        "confidence": 0.0,
    }
