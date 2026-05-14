"""scripts/tests/ 共通の sys.path 設定と test helper。

旧 test_verification_catalog.py から PR-B で分離した共通定数・ヘルパー関数を集約。
テーマ別ファイル (test_verification_catalog_*.py) はここを参照する。
"""
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))


# ── data-contract / language helpers ──────────────────

_PY_CROSS_MODULE = """\
from foo.bar import baz

result = {"key": baz()}
"""

_PY_NO_PATTERN = """\
x = 1
y = 2
"""

_TS_CROSS_MODULE = """\
import { fetchData } from "../api";

const result = {
  data: fetchData(),
};
"""


def _create_py_files(tmp_path: Path, count: int, cross_module: bool = True) -> None:
    """tmp_path に Python ファイルを count 個作成する。"""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    content = _PY_CROSS_MODULE if cross_module else _PY_NO_PATTERN
    for i in range(count):
        (src / f"mod_{i}.py").write_text(content)


def _create_ts_files(tmp_path: Path, count: int) -> None:
    """tmp_path に TypeScript ファイルを count 個作成する。"""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    for i in range(count):
        (src / f"mod_{i}.ts").write_text(_TS_CROSS_MODULE)


# ── side-effect helpers ───────────────────────────────

_DB_CODE = """\
from sqlalchemy.orm import Session

def save_item(session: Session, item):
    session.add(item)
    session.commit()
"""

_MQ_CODE = """\
import boto3

sqs = boto3.client("sqs")
sqs.send_message(QueueUrl="q", MessageBody="hi")
"""

_API_CODE = """\
import requests

def notify():
    requests.post("https://hook.example.com", json={"ok": True})
"""

_INNOCUOUS_CODE = """\
x = 1
y = 2
"""


def _create_side_effect_files(tmp_path, category, count):
    """category ('db'/'mq'/'api') のコードを count 個作成。"""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    code = {"db": _DB_CODE, "mq": _MQ_CODE, "api": _API_CODE}[category]
    for i in range(count):
        (src / f"{category}_{i}.py").write_text(code)


# ── cross-layer / IaC helpers ─────────────────────────

_ENV_VAR_PY_CODE = """\
import os

DATABASE_URL = os.environ.get("DATABASE_URL")
API_KEY = os.getenv("API_KEY")
"""

_ENV_VAR_TS_CODE = """\
const dbUrl = process.env.DATABASE_URL;
const apiKey = process.env.API_KEY;
"""

_AWS_SDK_PY_CODE = """\
import boto3

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
"""

_AWS_SDK_TS_CODE = """\
import { S3Client } from "@aws-sdk/client-s3";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";

const s3 = new S3Client({});
const ddb = new DynamoDBClient({});
"""


def _create_iac_project(tmp_path):
    """IaC マーカーを作成する。"""
    (tmp_path / "cdk.json").write_text("{}")


def _create_env_var_files(tmp_path, count, lang="py"):
    """環境変数参照を含むファイルを作成。"""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    code = _ENV_VAR_PY_CODE if lang == "py" else _ENV_VAR_TS_CODE
    ext = ".py" if lang == "py" else ".ts"
    for i in range(count):
        (src / f"handler_{i}{ext}").write_text(code)


def _create_aws_sdk_files(tmp_path, count, lang="py"):
    """AWS SDK 使用を含むファイルを作成。"""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    code = _AWS_SDK_PY_CODE if lang == "py" else _AWS_SDK_TS_CODE
    ext = ".py" if lang == "py" else ".ts"
    for i in range(count):
        (src / f"aws_{i}{ext}").write_text(code)
