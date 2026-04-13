"""bin/rl-usage-log のテスト。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BIN_DIR / "rl-usage-log"


def test_writes_usage_record(tmp_path):
    """スキル名を指定して usage.jsonl にレコードが書き込まれる。"""
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path)
    env["CLAUDE_PROJECT_DIR"] = "/proj/my-project"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "evolve"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

    usage_file = tmp_path / "usage.jsonl"
    assert usage_file.exists()

    record = json.loads(usage_file.read_text().strip())
    assert record["skill_name"] == "evolve"
    assert record["project"] == "my-project"
    assert record["source"] == "self-report"
    assert "ts" in record


def test_no_args_exits_with_error():
    """引数なしで実行するとエラー終了する。"""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1


def test_custom_project(tmp_path):
    """--project オプションでプロジェクト名を指定できる。"""
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path)
    env.pop("CLAUDE_PROJECT_DIR", None)

    subprocess.run(
        [sys.executable, str(SCRIPT), "audit", "--project", "custom-proj"],
        env=env,
        capture_output=True,
    )

    record = json.loads((tmp_path / "usage.jsonl").read_text().strip())
    assert record["project"] == "custom-proj"
