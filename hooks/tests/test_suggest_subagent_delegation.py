"""suggest_subagent_delegation hook のテスト。"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# hooks/ を sys.path に追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from suggest_subagent_delegation import (
    LONG_RUNNING_PATTERNS,
    _detect_category,
)


class TestDetectCategory:
    """_detect_category のパターンマッチテスト。"""

    @pytest.mark.parametrize("command,expected", [
        # deploy
        ("cdk deploy --all", "deploy"),
        ("cdk synth", "deploy"),
        ("sam deploy --guided", "deploy"),
        ("terraform apply -auto-approve", "deploy"),
        ("terraform plan", "deploy"),
        ("pulumi up", "deploy"),
        ("serverless deploy --stage prod", "deploy"),
        ("aws cloudformation create-stack --stack-name foo", "deploy"),
        # build
        ("docker build -t my-app .", "build"),
        ("docker compose up -d", "build"),
        ("npm run build", "build"),
        ("yarn build", "build"),
        ("pnpm build", "build"),
        ("next build", "build"),
        ("cargo build --release", "build"),
        ("go build ./...", "build"),
        # test-suite
        ("pytest", "test-suite"),
        ("pytest -v", "test-suite"),
        ("npm test", "test-suite"),
        ("yarn test", "test-suite"),
        ("cargo test", "test-suite"),
        # install
        ("npm install", "install"),
        ("pip install -r requirements.txt", "install"),
        ("brew install jq", "install"),
        # push
        ("git push origin main", "push"),
        ("docker push my-app:latest", "push"),
        # migration
        ("prisma migrate deploy", "migration"),
        ("alembic migrate", "migration"),
        ("python manage.py migrate", "migration"),
    ])
    def test_detects_long_running_commands(self, command: str, expected: str):
        assert _detect_category(command) == expected

    @pytest.mark.parametrize("command", [
        # 通常の短時間コマンド
        "ls -la",
        "git status",
        "git diff",
        "cat README.md",
        "echo hello",
        "python3 -c 'print(1)'",
        "git log --oneline -5",
        "npm --version",
        # pytest の部分実行（除外対象）
        "pytest tests/test_foo.py::test_bar",
        "pytest -k test_specific",
    ])
    def test_ignores_short_commands(self, command: str):
        assert _detect_category(command) is None

    def test_case_insensitive(self):
        assert _detect_category("CDK DEPLOY --all") == "deploy"
        assert _detect_category("Docker Build .") == "build"


class TestMainFunction:
    """main() の統合テスト。"""

    def test_non_bash_tool_exits_silently(self, tmp_path: Path):
        """Bash 以外のツールでは何も出力しない。"""
        from suggest_subagent_delegation import main

        input_data = json.dumps({"tool_name": "Edit", "session_id": "test"})
        with mock.patch("sys.stdin", __class__=type(sys.stdin)):
            with mock.patch("sys.stdin", new=__import__("io").StringIO(input_data)):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0

    def test_short_command_exits_silently(self, tmp_path: Path):
        """短時間コマンドでは何も出力しない。"""
        from suggest_subagent_delegation import main

        input_data = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "ls -la"},
            "session_id": "test-short",
        })
        with mock.patch("sys.stdin", new=__import__("io").StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_deploy_command_suggests(self, tmp_path: Path, capsys):
        """デプロイコマンドで提案を出力する。"""
        from suggest_subagent_delegation import main, COUNTER_DIR

        # カウンターを tmp_path に向ける
        with mock.patch("suggest_subagent_delegation.COUNTER_DIR", tmp_path):
            input_data = json.dumps({
                "tool_name": "Bash",
                "tool_input": {"command": "cdk deploy --all"},
                "session_id": "test-deploy",
            })
            with mock.patch("sys.stdin", new=__import__("io").StringIO(input_data)):
                main()

            captured = capsys.readouterr()
            output = json.loads(captured.out)
            assert "subagent 移譲提案" in output["systemMessage"]
            assert "デプロイ" in output["systemMessage"]

    def test_same_category_not_repeated(self, tmp_path: Path, capsys):
        """同じカテゴリは1セッションで1回だけ提案。"""
        from suggest_subagent_delegation import main

        with mock.patch("suggest_subagent_delegation.COUNTER_DIR", tmp_path):
            # 1回目: 提案あり
            input_data = json.dumps({
                "tool_name": "Bash",
                "tool_input": {"command": "cdk deploy --all"},
                "session_id": "test-repeat",
            })
            with mock.patch("sys.stdin", new=__import__("io").StringIO(input_data)):
                main()

            first = capsys.readouterr()
            assert "subagent 移譲提案" in first.out

            # 2回目: 同カテゴリなので提案なし
            input_data2 = json.dumps({
                "tool_name": "Bash",
                "tool_input": {"command": "cdk deploy --stage prod"},
                "session_id": "test-repeat",
            })
            with mock.patch("sys.stdin", new=__import__("io").StringIO(input_data2)):
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0

            second = capsys.readouterr()
            assert second.out == ""

    def test_different_category_suggests_again(self, tmp_path: Path, capsys):
        """異なるカテゴリなら再度提案する。"""
        from suggest_subagent_delegation import main

        with mock.patch("suggest_subagent_delegation.COUNTER_DIR", tmp_path):
            # deploy
            input_data = json.dumps({
                "tool_name": "Bash",
                "tool_input": {"command": "cdk deploy --all"},
                "session_id": "test-diff",
            })
            with mock.patch("sys.stdin", new=__import__("io").StringIO(input_data)):
                main()
            capsys.readouterr()

            # build (別カテゴリ)
            input_data2 = json.dumps({
                "tool_name": "Bash",
                "tool_input": {"command": "docker build ."},
                "session_id": "test-diff",
            })
            with mock.patch("sys.stdin", new=__import__("io").StringIO(input_data2)):
                main()

            second = capsys.readouterr()
            assert "ビルド" in second.out
