"""verification_catalog のルールテンプレート文字列と副作用検出 regex 定数。

`__init__.py` から集約して再エクスポートされる。
"""
import re

# ── ルールテンプレート ────────────────────────────────
_PYTHON_RULE_TEMPLATE = """# データ変換コードの契約確認
モジュール間のデータ変換・統合コードを書く前に、ソース関数の返り値構造（dictキー・型）を Read で確認する。自作テストデータは自作の誤りを検出できないため、既存テストの fixture も参照する。
"""

_TYPESCRIPT_RULE_TEMPLATE = """# データ変換コードの契約確認
モジュール間のデータ変換・統合コードを書く前に、ソース関数の戻り型（interface/type）を Read で確認する。自作テストデータは自作の誤りを検出できないため、既存テストの fixture も参照する。
"""

_SIDE_EFFECT_RULE_TEMPLATE = """# 副作用チェック
テスト検証時、正パスに加えて副作用を確認する: 意図しない書き込み・状態残留・再帰的トリガー。
"""

_EVIDENCE_RULE_TEMPLATE = """# 証拠提示義務
完了主張の前に検証コマンド（テスト実行・動作確認・ビルド成功）の実行結果を提示する。「できました」の前に証拠を示す。
"""

_CROSS_LAYER_RULE_TEMPLATE = """# クロスレイヤー整合性確認
コード変更時に IaC 定義（環境変数設定・IAM 権限）との整合性を確認する。新しい環境変数参照や AWS サービス利用を追加したら、対応する IaC 定義も更新する。
"""

_HAPPY_PATH_RULE_TEMPLATE = """# テストはハッピーパスから書く
オーケストレーション・パイプライン等の複数ステップを持つコードは、全ステップを通る正常系E2Eテストを最初に書く。
"""

# ── 副作用検出パターン（3カテゴリ）────────────────────
_SIDE_EFFECT_DB_PATTERNS = re.compile(
    r"(?:session\.add|cursor\.execute|\.commit\(\)|INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM"
    r"|prisma\.\w+\.create|\.save\(\)|knex\.\w*insert)",
    re.IGNORECASE,
)
_SIDE_EFFECT_MQ_PATTERNS = re.compile(
    r"(?:sqs\.send_message|\.publish\(|channel\.basic_publish"
    r"|sendMessage|channel\.sendToQueue)",
)
_SIDE_EFFECT_API_PATTERNS = re.compile(
    r"(?:requests\.post|httpx\.post|aiohttp\.\w*post"
    r"|fetch\(|axios\.post|webhook)",
    re.IGNORECASE,
)

_SIDE_EFFECT_CATEGORIES = {
    "db": _SIDE_EFFECT_DB_PATTERNS,
    "mq": _SIDE_EFFECT_MQ_PATTERNS,
    "api": _SIDE_EFFECT_API_PATTERNS,
}

# ── テストファイル除外パターン ────────────────────────
_TEST_FILE_PATTERNS = re.compile(
    r"(?:^test_.*\.py$|.*_test\.py$|.*\.test\.tsx?$)"
)

_TEST_DIR_NAMES = {"__tests__"}
