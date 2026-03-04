#!/usr/bin/env python3
"""discover --session-scan のユニットテスト。"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# discover.py を import できるようにパスを追加
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# backfill の ParseResult をモック用に再定義（backfill の import を避ける）
PLUGIN_ROOT = SCRIPTS_DIR.parent.parent.parent
BACKFILL_SCRIPTS = PLUGIN_ROOT / "skills" / "backfill" / "scripts"
if str(BACKFILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(BACKFILL_SCRIPTS))
HOOKS_DIR = PLUGIN_ROOT / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

import discover


def _make_session_jsonl(tmp_path: Path, project_name: str, session_id: str, prompts: list[str]) -> Path:
    """テスト用のセッション JSONL ファイルを作成する。"""
    project_dir = tmp_path / project_name / "sessions"
    project_dir.mkdir(parents=True, exist_ok=True)
    session_file = project_dir / f"{session_id}.jsonl"

    lines = []
    for i, prompt in enumerate(prompts):
        record = {
            "type": "human",
            "timestamp": f"2025-01-01T00:0{i}:00Z",
            "sessionId": session_id,
            "message": {
                "content": prompt,
            },
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    session_file.write_text("\n".join(lines), encoding="utf-8")
    return session_file


class TestDetectSessionPatterns:
    """detect_session_patterns のテスト。"""

    def test_pattern_detected_above_threshold(self, tmp_path: Path):
        """5回以上の繰り返しがスキル候補として検出される。"""
        prompts = ["git status を確認して"] * 6 + ["何か別のこと"]
        _make_session_jsonl(tmp_path, "project-a", "sess1", prompts)

        with patch.object(discover, "load_suppression_list", return_value=set()):
            patterns = discover.detect_session_patterns(
                threshold=5, projects_dir=tmp_path
            )

        matching = [p for p in patterns if p["pattern"] == "git status を確認して"]
        assert len(matching) == 1
        assert matching[0]["count"] == 6
        assert matching[0]["type"] == "session_text"
        assert matching[0]["suggestion"] == "skill_candidate"

    def test_pattern_below_threshold_filtered(self, tmp_path: Path):
        """4回以下のパターンは候補にならない。"""
        prompts = ["deploy してください"] * 4
        _make_session_jsonl(tmp_path, "project-b", "sess2", prompts)

        with patch.object(discover, "load_suppression_list", return_value=set()):
            patterns = discover.detect_session_patterns(
                threshold=5, projects_dir=tmp_path
            )

        assert len(patterns) == 0

    def test_no_sessions_returns_empty(self, tmp_path: Path):
        """セッションファイルが存在しない場合は空結果。"""
        # tmp_path にはセッションファイルがない
        with patch.object(discover, "load_suppression_list", return_value=set()):
            patterns = discover.detect_session_patterns(
                threshold=5, projects_dir=tmp_path
            )

        assert patterns == []

    def test_nonexistent_dir_returns_empty(self):
        """projects_dir が存在しない場合は空結果。"""
        patterns = discover.detect_session_patterns(
            threshold=5, projects_dir=Path("/nonexistent/path")
        )
        assert patterns == []

    def test_parse_error_skipped(self, tmp_path: Path):
        """parse_transcript が例外を送出した場合はスキップして続行。"""
        # 正常なセッション
        prompts_good = ["テスト実行"] * 6
        _make_session_jsonl(tmp_path, "project-c", "good", prompts_good)

        # 壊れたセッション
        broken_dir = tmp_path / "project-d" / "sessions"
        broken_dir.mkdir(parents=True, exist_ok=True)
        broken_file = broken_dir / "broken.jsonl"
        broken_file.write_text("this is not valid json\n" * 10, encoding="utf-8")

        with patch.object(discover, "load_suppression_list", return_value=set()):
            # エラーが発生しても正常なセッションの結果は返される
            patterns = discover.detect_session_patterns(
                threshold=5, projects_dir=tmp_path
            )

        # 正常セッションのパターンが検出される（壊れたセッションはスキップ）
        matching = [p for p in patterns if p["pattern"] == "テスト実行"]
        assert len(matching) == 1
        assert matching[0]["count"] == 6

    def test_short_prompts_ignored(self, tmp_path: Path):
        """5文字未満の短いプロンプトは無視される。"""
        prompts = ["ok"] * 10
        _make_session_jsonl(tmp_path, "project-e", "sess5", prompts)

        with patch.object(discover, "load_suppression_list", return_value=set()):
            patterns = discover.detect_session_patterns(
                threshold=5, projects_dir=tmp_path
            )

        assert len(patterns) == 0

    def test_multiple_sessions_aggregated(self, tmp_path: Path):
        """複数セッションのプロンプトが集約される。"""
        prompts1 = ["ビルドして"] * 3
        prompts2 = ["ビルドして"] * 3
        _make_session_jsonl(tmp_path, "project-f", "sess6a", prompts1)
        _make_session_jsonl(tmp_path, "project-f", "sess6b", prompts2)

        with patch.object(discover, "load_suppression_list", return_value=set()):
            patterns = discover.detect_session_patterns(
                threshold=5, projects_dir=tmp_path
            )

        matching = [p for p in patterns if p["pattern"] == "ビルドして"]
        assert len(matching) == 1
        assert matching[0]["count"] == 6
