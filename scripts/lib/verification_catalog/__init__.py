"""検証知見カタログ — 汎用的な検証ルールのテンプレート管理。

verification_catalog（定義）→ discover（検出）→ evolve（変換）→ remediation（修正）
の4層で共有する。
"""
import logging
from typing import Any, Dict, List

# slice 1: helpers / templates 再エクスポート（後方互換）
from .helpers import (  # noqa: F401
    EXCLUDE_DIRS,
    LARGE_REPO_FILE_THRESHOLD,
    PRIORITY_DIRS,
    _PY_DICT_BUILD_RE,
    _PY_IMPORT_RE,
    _TS_IMPORT_RE,
    _TS_OBJ_BUILD_RE,
    _detect_primary_language,
    _has_cross_module_pattern,
    _is_test_file,
    _iter_source_files,
    _safe_result,
)
from .templates import (  # noqa: F401
    _CROSS_LAYER_RULE_TEMPLATE,
    _EVIDENCE_RULE_TEMPLATE,
    _HAPPY_PATH_RULE_TEMPLATE,
    _PYTHON_RULE_TEMPLATE,
    _SIDE_EFFECT_API_PATTERNS,
    _SIDE_EFFECT_CATEGORIES,
    _SIDE_EFFECT_DB_PATTERNS,
    _SIDE_EFFECT_MQ_PATTERNS,
    _SIDE_EFFECT_RULE_TEMPLATE,
    _TEST_DIR_NAMES,
    _TEST_FILE_PATTERNS,
    _TYPESCRIPT_RULE_TEMPLATE,
)

logger = logging.getLogger(__name__)

# ── 閾値定数 ──────────────────────────────────────────
DATA_CONTRACT_MIN_PATTERNS = 3
SIDE_EFFECT_MIN_PATTERNS = 3
EVIDENCE_MIN_PATTERNS = 3
MIN_CROSS_LAYER_PATTERNS = 3
HAPPY_PATH_MIN_PATTERNS = 2
DETECTION_TIMEOUT_SECONDS = 5
MAX_CATALOG_ENTRIES = 10

# slice 2: basic detectors 再エクスポート
from .detectors_basic import (  # noqa: E402,F401
    _EVIDENCE_REQUEST_PATTERNS,
    detect_data_contract_verification,
    detect_evidence_verification,
    detect_side_effect_verification,
)
# slice 3: advanced detectors + runner 再エクスポート
from .detectors_advanced import (  # noqa: E402,F401
    _AWS_SDK_PY_RE,
    _AWS_SDK_TS_RE,
    _ENV_VAR_PY_RE,
    _ENV_VAR_TS_RE,
    _IAC_MARKERS,
    _MIN_PIPELINE_CALLS,
    _PIPELINE_CALL_PATTERN_PY,
    _PIPELINE_CALL_PATTERN_TS,
    _PIPELINE_LOOP_PATTERN,
    _PY_FUNC_DEF,
    _TS_FUNC_DEF,
    _detect_pipeline_functions,
    _find_test_files,
    _test_has_function_call,
    detect_cross_layer_consistency,
    detect_happy_path_test_gap,
    detect_iac_project,
)
from .runner import (  # noqa: E402,F401
    _CONTENT_KEYWORDS_MAP,
    _CROSS_LAYER_CONTENT_KEYWORDS,
    _DETECTION_FN_DISPATCH,
    _EVIDENCE_CONTENT_KEYWORDS,
    _HAPPY_PATH_CONTENT_KEYWORDS,
    _SIDE_EFFECT_CONTENT_KEYWORDS,
    _run_detection_fn,
    check_verification_installed,
    detect_verification_needs,
    get_rule_template,
)

# ── カタログ定義 ──────────────────────────────────────
VERIFICATION_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "data-contract-verification",
        "type": "rule",
        "description": "モジュール間データ変換コード記述前にソース関数の返り値構造を確認する",
        "rule_template": _PYTHON_RULE_TEMPLATE.strip(),
        "rule_filename": "verify-data-contract.md",
        "detection_fn": "detect_data_contract_verification",
        "applicability": "conditional",
    },
    {
        "id": "side-effect-verification",
        "type": "rule",
        "description": "テスト検証時に副作用（DB残留・共有リソース書き込み・非同期連鎖）を確認する",
        "rule_template": _SIDE_EFFECT_RULE_TEMPLATE.strip(),
        "rule_filename": "verify-side-effects.md",
        "detection_fn": "detect_side_effect_verification",
        "applicability": "conditional",
    },
    {
        "id": "evidence-before-claims",
        "type": "rule",
        "description": "完了主張の前に検証コマンドの実行結果を提示する証拠提示義務パターン",
        "rule_template": _EVIDENCE_RULE_TEMPLATE.strip(),
        "rule_filename": "verify-before-claim.md",
        "detection_fn": "detect_evidence_verification",
        "applicability": "conditional",
        "content_patterns": [
            "証拠", "evidence", "verify", "確認して", "テスト実行",
            "動作確認", "before claim", "証拠提示",
        ],
    },
    {
        "id": "happy-path-test-verification",
        "type": "rule",
        "description": "パイプライン/オーケストレーションコードに正常系E2Eテストの欠落を検出する",
        "rule_template": _HAPPY_PATH_RULE_TEMPLATE.strip(),
        "rule_filename": "test-happy-path-first.md",
        "detection_fn": "detect_happy_path_test_gap",
        "applicability": "conditional",
        "content_patterns": [
            "ハッピーパス", "happy path", "E2Eテスト", "正常系テスト",
        ],
    },
    {
        "id": "cross-layer-consistency",
        "type": "rule",
        "description": "コード↔IaC 間の整合性（環境変数・IAM 権限）を確認するルール",
        "rule_template": _CROSS_LAYER_RULE_TEMPLATE.strip(),
        "rule_filename": "verify-cross-layer.md",
        "detection_fn": "detect_cross_layer_consistency",
        "applicability": "conditional",
        "content_patterns": [
            "cross-layer", "IaC", "環境変数", "IAM", "cdk", "aws",
        ],
    },
]
