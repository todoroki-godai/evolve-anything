#!/usr/bin/env python3
"""reflect スキルのユニットテスト。"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts"))
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
sys.path.insert(0, str(_plugin_root / "skills" / "reflect" / "scripts"))

import reflect


# --- Fixtures ---

def _make_correction(
    message="いや、bun を使って",
    correction_type="iya",
    confidence=0.85,
    reflect_status="pending",
    project_path=None,
    timestamp=None,
    extracted_learning=None,
):
    """テスト用 correction レコードを生成する。"""
    record = {
        "message": message,
        "correction_type": correction_type,
        "confidence": confidence,
        "reflect_status": reflect_status,
        "project_path": project_path,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }
    if extracted_learning:
        record["extracted_learning"] = extracted_learning
    return record


def _write_corrections(tmp_path, corrections):
    """corrections.jsonl を一時ディレクトリに書き出す。"""
    filepath = tmp_path / "corrections.jsonl"
    lines = [json.dumps(c, ensure_ascii=False) for c in corrections]
    filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return filepath


# --- Test: extract_pending ---

class TestExtractPending:
    def test_pending_only(self):
        """pending のみ返す。"""
        records = [
            _make_correction(reflect_status="pending"),
            _make_correction(reflect_status="applied"),
            _make_correction(reflect_status="skipped"),
            _make_correction(reflect_status="pending"),
        ]
        result = reflect.extract_pending(records)
        assert len(result) == 2
        assert all(r["reflect_status"] == "pending" for r in result)

    def test_missing_status_treated_as_pending(self):
        """reflect_status がないレコードは pending として扱う。"""
        record = {"message": "test", "correction_type": "iya", "confidence": 0.85}
        result = reflect.extract_pending([record])
        assert len(result) == 1

    def test_empty_records(self):
        """空リストには空リストを返す。"""
        assert reflect.extract_pending([]) == []


# --- Test: classify_project_scope ---

class TestClassifyProjectScope:
    def test_same_project(self):
        """同一プロジェクトの場合 same-project を返す。"""
        c = _make_correction(project_path="/home/user/project")
        result = reflect.classify_project_scope(c, "/home/user/project")
        assert result == "same-project"

    def test_null_project_path(self):
        """project_path が null の場合 global-looking を返す。"""
        c = _make_correction(project_path=None)
        result = reflect.classify_project_scope(c, "/home/user/project")
        assert result == "global-looking"

    def test_global_looking_always(self):
        """always/never キーワード含む → global-looking。"""
        c = _make_correction(
            message="always use bun",
            project_path="/other/project",
        )
        result = reflect.classify_project_scope(c, "/home/user/project")
        assert result == "global-looking"

    def test_global_looking_model_keyword(self):
        """モデル名キーワード含む → global-looking。"""
        c = _make_correction(
            message="use sonnet for this",
            project_path="/other/project",
        )
        result = reflect.classify_project_scope(c, "/home/user/project")
        assert result == "global-looking"

    def test_project_specific_other_db(self):
        """DB 名を含む → project-specific-other。"""
        c = _make_correction(
            message="use users.db for the database",
            project_path="/other/project",
        )
        result = reflect.classify_project_scope(c, "/home/user/project")
        assert result == "project-specific-other"

    def test_project_specific_other_filepath(self):
        """ファイルパスを含む → project-specific-other。"""
        c = _make_correction(
            message="edit /src/components/App.tsx instead",
            project_path="/other/project",
        )
        result = reflect.classify_project_scope(c, "/home/user/project")
        assert result == "project-specific-other"

    def test_other_project_generic(self):
        """異なるプロジェクトだが汎用的 → global-looking。"""
        c = _make_correction(
            message="don't add comments",
            project_path="/other/project",
        )
        result = reflect.classify_project_scope(c, "/home/user/project")
        assert result == "global-looking"


# --- Test: detect_duplicates ---

class TestDetectDuplicates:
    def test_no_duplicates(self, tmp_path):
        """重複なしの場合 duplicate_found=False。"""
        corrections = [_make_correction(message="use bun instead of npm")]
        with mock.patch("reflect.read_all_memory_entries", return_value=[]):
            result = reflect.detect_duplicates(corrections, tmp_path)
        assert len(result) == 1
        assert result[0]["duplicate_found"] is False
        assert result[0]["duplicate_in"] is None

    def test_duplicate_found(self, tmp_path):
        """メッセージがメモリに既存の場合 duplicate_found=True。"""
        corrections = [_make_correction(message="use bun instead of npm")]
        memory = [{"tier": "global", "path": "/home/.claude/CLAUDE.md", "content": "use bun instead of npm"}]
        with mock.patch("reflect.read_all_memory_entries", return_value=memory):
            result = reflect.detect_duplicates(corrections, tmp_path)
        assert result[0]["duplicate_found"] is True
        assert result[0]["duplicate_in"] == "/home/.claude/CLAUDE.md"

    def test_short_message_no_false_positive(self, tmp_path):
        """短いメッセージ（10文字以下）は重複チェックしない。"""
        corrections = [_make_correction(message="use bun")]
        memory = [{"tier": "global", "path": "/home/.claude/CLAUDE.md", "content": "use bun for everything"}]
        with mock.patch("reflect.read_all_memory_entries", return_value=memory):
            result = reflect.detect_duplicates(corrections, tmp_path)
        assert result[0]["duplicate_found"] is False

    def test_duplicate_via_extracted_learning(self, tmp_path):
        """extracted_learning がメモリに存在する場合も重複検出する。"""
        corrections = [_make_correction(
            message="いや、bun を使って",
            extracted_learning="パッケージマネージャーには bun を使用する",
        )]
        memory = [{"tier": "rule", "path": "/project/.claude/rules/tools.md",
                    "content": "パッケージマネージャーには bun を使用する"}]
        with mock.patch("reflect.read_all_memory_entries", return_value=memory):
            result = reflect.detect_duplicates(corrections, tmp_path)
        assert result[0]["duplicate_found"] is True


# --- Test: route_corrections ---

class TestRouteCorrections:
    def test_global_scope(self, tmp_path):
        """global-looking スコープ → routing_hint="global"。"""
        corrections = [dict(_make_correction(), _scope="global-looking")]
        with mock.patch("reflect.suggest_claude_file", return_value=(str(Path.home() / ".claude/CLAUDE.md"), 0.80)):
            result = reflect.route_corrections(corrections, tmp_path)
        assert result[0]["routing_hint"] == "global"
        assert result[0]["suggested_file"] is not None

    def test_project_scope(self, tmp_path):
        """same-project スコープ → routing_hint="project"。"""
        corrections = [dict(_make_correction(), _scope="same-project")]
        with mock.patch("reflect.suggest_claude_file", return_value=("/project/CLAUDE.md", 0.75)):
            result = reflect.route_corrections(corrections, tmp_path)
        assert result[0]["routing_hint"] == "project"

    def test_skip_scope(self, tmp_path):
        """project-specific-other スコープ → routing_hint="skip"。"""
        corrections = [dict(_make_correction(), _scope="project-specific-other")]
        with mock.patch("reflect.suggest_claude_file", return_value=None):
            result = reflect.route_corrections(corrections, tmp_path)
        assert result[0]["routing_hint"] == "skip"
        assert result[0]["suggested_file"] is None

    def test_no_suggestion(self, tmp_path):
        """suggest_claude_file が None → suggested_file=None。"""
        corrections = [dict(_make_correction(), _scope="same-project")]
        with mock.patch("reflect.suggest_claude_file", return_value=None):
            result = reflect.route_corrections(corrections, tmp_path)
        assert result[0]["suggested_file"] is None

    def test_line_limit_warning_on_overflowed_rule(self, tmp_path):
        """反映先 rule が既に行数超過の場合 line_limit_warning が付与される。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        rule = rules_dir / "big-rule.md"
        rule.write_text("\n".join(f"line{i}" for i in range(1, 13)) + "\n")  # 12行 > MAX_RULE_LINES(10)

        corrections = [dict(_make_correction(), _scope="same-project")]
        with mock.patch("reflect.suggest_claude_file", return_value=(str(rule), 0.80)):
            result = reflect.route_corrections(corrections, tmp_path)
        assert "line_limit_warning" in result[0]
        assert "分離" in result[0]["line_limit_warning"]

    def test_no_line_limit_warning_within_limit(self, tmp_path):
        """反映先 rule が行数制限内の場合 line_limit_warning は付与されない。"""
        rules_dir = tmp_path / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        rule = rules_dir / "ok-rule.md"
        rule.write_text("# Rule\nShort.\n")

        corrections = [dict(_make_correction(), _scope="same-project")]
        with mock.patch("reflect.suggest_claude_file", return_value=(str(rule), 0.80)):
            result = reflect.route_corrections(corrections, tmp_path)
        assert "line_limit_warning" not in result[0]


# --- Test: --view mode ---

class TestViewMode:
    def test_view_output(self):
        """--view は corrections 一覧と total を含む JSON を返す。"""
        ts = datetime.now(timezone.utc).isoformat()
        pending = [
            _make_correction(message="use bun", confidence=0.85, timestamp=ts),
            _make_correction(message="no comments", confidence=0.70, timestamp=ts),
        ]
        result = reflect.build_view_output(pending, pending)
        assert result["status"] == "view"
        assert result["total"] == 2
        assert len(result["corrections"]) == 2
        assert result["corrections"][0]["age_days"] is not None

    def test_view_empty(self):
        """pending が空なら empty ステータス。"""
        result = reflect.build_view_output([], [])
        assert result["status"] == "empty"


# --- Test: --skip-all mode ---

class TestSkipAllMode:
    def test_skip_all_updates_status(self, tmp_path):
        """--skip-all は全 pending を skipped に更新する。"""
        corrections = [
            _make_correction(reflect_status="pending"),
            _make_correction(reflect_status="applied"),
            _make_correction(reflect_status="pending"),
        ]
        filepath = _write_corrections(tmp_path, corrections)

        reflect.update_reflect_status(filepath, [0, 2], "skipped")

        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "skipped"
        assert updated[1]["reflect_status"] == "applied"
        assert updated[2]["reflect_status"] == "skipped"

    def test_skip_all_empty(self, tmp_path):
        """pending が空なら更新しない。"""
        filepath = tmp_path / "corrections.jsonl"
        filepath.write_text("", encoding="utf-8")
        reflect.update_reflect_status(filepath, [], "skipped")
        # エラーなく完了すること
        assert True


# --- Test: --apply-all mode ---

class TestApplyAllMode:
    def test_apply_all_separates_by_threshold(self):
        """apply_all は閾値以上に apply=True、未満に apply=False を付与する。"""
        pending = [
            _make_correction(confidence=0.90),
            _make_correction(confidence=0.70),
            _make_correction(confidence=0.85),
        ]
        # build_output に必要なフィールドを追加
        for c in pending:
            c["_scope"] = "same-project"
            c["routing_hint"] = "project"
            c["suggested_file"] = "/tmp/test.md"
            c["duplicate_found"] = False
            c["duplicate_in"] = None

        with mock.patch("reflect.find_promotion_candidates", return_value=[]):
            result = reflect.build_output(
                pending, pending,
                min_confidence=0.85,
                apply_all=True,
            )

        corrections = result["corrections"]
        assert corrections[0]["apply"] is True   # 0.90 >= 0.85
        assert corrections[1]["apply"] is False  # 0.70 < 0.85
        assert corrections[2]["apply"] is True   # 0.85 >= 0.85

    def test_apply_all_summary(self):
        """apply_all でもサマリは正常に生成される。"""
        pending = [_make_correction(confidence=0.90)]
        for c in pending:
            c["_scope"] = "same-project"
            c["routing_hint"] = "project"
            c["suggested_file"] = "/tmp/test.md"
            c["duplicate_found"] = False
            c["duplicate_in"] = None

        with mock.patch("reflect.find_promotion_candidates", return_value=[]):
            result = reflect.build_output(pending, pending, apply_all=True)

        assert result["status"] == "has_pending"
        assert result["summary"]["total"] == 1


# --- Test: promotion candidates ---

class TestPromotionCandidates:
    def test_reoccurrence_promotion(self):
        """同一 correction_type が2回以上 → 昇格候補。"""
        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        records = [
            _make_correction(correction_type="iya", reflect_status="applied", timestamp=old_ts),
            _make_correction(correction_type="iya", reflect_status="applied",
                             message="いや、そうじゃない", timestamp=old_ts),
        ]
        with mock.patch("reflect.read_auto_memory", return_value=[]):
            result = reflect.find_promotion_candidates(records)
        assert len(result) >= 1
        assert result[0]["occurrences"] >= 2

    def test_age_promotion(self):
        """14日以上経過 → 昇格候補。"""
        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        records = [
            _make_correction(correction_type="unique_type", reflect_status="applied", timestamp=old_ts),
        ]
        with mock.patch("reflect.read_auto_memory", return_value=[]):
            result = reflect.find_promotion_candidates(records)
        assert len(result) == 1
        assert result[0]["age_qualified"] is True

    def test_no_promotion_recent_single(self):
        """出現1回かつ14日未満 → 昇格候補なし。"""
        recent_ts = datetime.now(timezone.utc).isoformat()
        records = [
            _make_correction(correction_type="unique_type", reflect_status="applied", timestamp=recent_ts),
        ]
        with mock.patch("reflect.read_auto_memory", return_value=[]):
            result = reflect.find_promotion_candidates(records)
        assert len(result) == 0

    def test_already_in_auto_memory(self):
        """auto-memory に既存なら昇格候補から除外。"""
        old_ts = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
        records = [
            _make_correction(
                message="use bun",
                correction_type="iya",
                reflect_status="applied",
                timestamp=old_ts,
            ),
        ]
        auto_memory = [{"path": "/memory/MEMORY.md", "topic": "general", "content": "use bun"}]
        with mock.patch("reflect.read_auto_memory", return_value=auto_memory):
            result = reflect.find_promotion_candidates(records)
        assert len(result) == 0


# --- Test: load_corrections ---

class TestLoadCorrections:
    def test_load_valid(self, tmp_path):
        """正常な JSONL を読み込める。"""
        corrections = [_make_correction(), _make_correction(message="test2")]
        filepath = _write_corrections(tmp_path, corrections)
        result = reflect.load_corrections(filepath)
        assert len(result) == 2

    def test_load_nonexistent(self, tmp_path):
        """存在しないファイルは空リストを返す。"""
        result = reflect.load_corrections(tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_load_with_invalid_lines(self, tmp_path):
        """不正な JSON 行はスキップする。"""
        filepath = tmp_path / "corrections.jsonl"
        filepath.write_text(
            json.dumps(_make_correction()) + "\n"
            + "invalid json\n"
            + json.dumps(_make_correction(message="valid")) + "\n",
            encoding="utf-8",
        )
        result = reflect.load_corrections(filepath)
        assert len(result) == 2


# --- Test: update_reflect_status ---

class TestUpdateReflectStatus:
    def test_update_specific_indices(self, tmp_path):
        """指定インデックスのみ更新する。"""
        corrections = [
            _make_correction(message="msg0"),
            _make_correction(message="msg1"),
            _make_correction(message="msg2"),
        ]
        filepath = _write_corrections(tmp_path, corrections)
        reflect.update_reflect_status(filepath, [0, 2], "applied")

        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "applied"
        assert updated[1]["reflect_status"] == "pending"
        assert updated[2]["reflect_status"] == "applied"


# --- Test: build_output ---

class TestBuildOutput:
    def test_empty_pending(self):
        """pending が空なら empty ステータスを返す。"""
        result = reflect.build_output([], [])
        assert result["status"] == "empty"

    def test_has_pending(self):
        """pending がある場合 has_pending ステータスを返す。"""
        pending = [_make_correction()]
        for c in pending:
            c["_scope"] = "same-project"
            c["routing_hint"] = "project"
            c["suggested_file"] = "/tmp/test.md"
            c["duplicate_found"] = False
            c["duplicate_in"] = None

        with mock.patch("reflect.find_promotion_candidates", return_value=[]):
            result = reflect.build_output(pending, pending)

        assert result["status"] == "has_pending"
        assert len(result["corrections"]) == 1
        assert "summary" in result
        assert result["summary"]["total"] == 1


# --- Test: CLI integration ---

class TestCLI:
    def test_view_mode_cli(self, tmp_path, capsys):
        """CLI --view モードが JSON を出力する。"""
        corrections = [_make_correction()]
        filepath = _write_corrections(tmp_path, corrections)

        with mock.patch("sys.argv", ["reflect.py", "--view", "--corrections-file", str(filepath)]):
            reflect.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["status"] == "view"

    def test_skip_all_mode_cli(self, tmp_path, capsys):
        """CLI --skip-all モードが全 pending をスキップする。"""
        corrections = [_make_correction(), _make_correction(reflect_status="applied")]
        filepath = _write_corrections(tmp_path, corrections)

        with mock.patch("sys.argv", ["reflect.py", "--skip-all", "--corrections-file", str(filepath)]):
            reflect.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["status"] == "skipped_all"
        assert output["count"] == 1

    def test_empty_corrections_cli(self, tmp_path, capsys):
        """corrections が空の場合 empty を出力する。"""
        filepath = tmp_path / "corrections.jsonl"
        filepath.write_text("", encoding="utf-8")

        with mock.patch("sys.argv", ["reflect.py", "--view", "--corrections-file", str(filepath)]):
            reflect.main()

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["status"] == "empty"


# --- Test: semantic validation failure does not zero out corrections ---

class TestSemanticValidationFallback:
    def test_validation_failure_preserves_corrections(self):
        """semantic validation が例外で失敗しても corrections が 0 件にならない。"""
        corrections = [
            _make_correction(message="use bun instead of npm"),
            _make_correction(message="always use TypeScript"),
        ]
        # validate_corrections が例外時に is_learning=True でフォールバックすることを確認
        with mock.patch("reflect.validate_corrections") as mock_validate:
            mock_validate.return_value = [
                {"is_learning": True, "extracted_learning": None},
                {"is_learning": True, "extracted_learning": None},
            ]
            result = reflect.apply_semantic_validation(corrections)
        assert len(result) == 2
        assert all(r["is_learning"] is True for r in result)

    def test_validation_count_mismatch_preserves_corrections(self):
        """semantic validation の件数不一致でも corrections が全件除外されない。"""
        corrections = [
            _make_correction(message="msg1"),
            _make_correction(message="msg2"),
            _make_correction(message="msg3"),
        ]
        # validate_corrections が partial success で一部 True を返す
        with mock.patch("reflect.validate_corrections") as mock_validate:
            mock_validate.return_value = [
                {"is_learning": False, "extracted_learning": None},
                {"is_learning": True, "extracted_learning": None},
                {"is_learning": True, "extracted_learning": None},
            ]
            result = reflect.apply_semantic_validation(corrections)
        # is_learning フィルタ後でも 0 件にはならない
        learning_items = [r for r in result if r.get("is_learning", True)]
        assert len(learning_items) >= 1


# --- Test: find_memory_update_candidates ---

class TestFindMemoryUpdateCandidates:
    def test_match_detected(self):
        """共通キーワードが MIN_KEYWORD_MATCH 以上なら候補として検出される。"""
        c = _make_correction(message="npm install instead of bun install for package management")
        c["duplicate_found"] = False
        corrections = [c]

        memory_entries = [{
            "tier": "auto-memory",
            "path": "/memory/MEMORY.md",
            "content": "## Package Management\n\n- bun install for package management\n",
        }]
        with mock.patch("reflect.read_all_memory_entries", return_value=memory_entries):
            result = reflect.find_memory_update_candidates(corrections)
        assert len(result) >= 1
        assert result[0]["suggested_action"] == "update"

    def test_no_match(self):
        """共通キーワードがない場合は空リスト。"""
        c = _make_correction(message="completely unrelated topic about database")
        c["duplicate_found"] = False
        corrections = [c]

        memory_entries = [{
            "tier": "auto-memory",
            "path": "/memory/MEMORY.md",
            "content": "## Git Config\n\n- todoroki-godai account for push\n",
        }]
        with mock.patch("reflect.read_all_memory_entries", return_value=memory_entries):
            result = reflect.find_memory_update_candidates(corrections)
        assert len(result) == 0

    def test_skip_duplicates(self):
        """duplicate_found=True の correction は除外される。"""
        c = _make_correction(message="npm install instead of bun install for package management")
        c["duplicate_found"] = True
        corrections = [c]

        memory_entries = [{
            "tier": "auto-memory",
            "path": "/memory/MEMORY.md",
            "content": "## Package Management\n\n- bun install for package management\n",
        }]
        with mock.patch("reflect.read_all_memory_entries", return_value=memory_entries):
            result = reflect.find_memory_update_candidates(corrections)
        assert len(result) == 0

    def test_below_min_keyword_match(self):
        """共通キーワードが MIN_KEYWORD_MATCH 未満なら候補にならない。"""
        c = _make_correction(message="hello world")
        c["duplicate_found"] = False
        corrections = [c]

        memory_entries = [{
            "tier": "auto-memory",
            "path": "/memory/MEMORY.md",
            "content": "## Notes\n\n- hello world example\n",
        }]
        with mock.patch("reflect.read_all_memory_entries", return_value=memory_entries):
            result = reflect.find_memory_update_candidates(corrections)
        # "hello" と "world" の2語のみ（ストップワード除外後）→ MIN_KEYWORD_MATCH=3 未満
        assert len(result) == 0


# --- Test: analyze_tool_call_patterns ---

class TestAnalyzeToolCallPatterns:
    def test_empty_corrections(self):
        """preceding_tool_calls がない corrections → 空の分析結果。"""
        corrections = [_make_correction()]
        result = reflect.analyze_tool_call_patterns(corrections)
        assert result["failure_patterns"] == []
        assert result["failure_rate_by_tool"] == {}

    def test_failure_rate_by_tool(self):
        """失敗したツール呼び出しの failure_rate が計算される。"""
        corrections = [
            {**_make_correction(), "preceding_tool_calls": [
                {"tool": "Bash", "success": False},
                {"tool": "Bash", "success": True},
                {"tool": "Edit", "success": True},
            ]},
        ]
        result = reflect.analyze_tool_call_patterns(corrections)
        assert "Bash" in result["failure_rate_by_tool"]
        assert result["failure_rate_by_tool"]["Bash"] == 0.5
        assert result["failure_rate_by_tool"]["Edit"] == 0.0

    def test_sequence_pattern_detected(self):
        """同一シーケンスが2件以上出現 → failure_patterns に記録される。"""
        tool_calls = [
            {"tool": "Bash", "success": False},
            {"tool": "Edit", "success": True},
        ]
        corrections = [
            {**_make_correction(), "preceding_tool_calls": tool_calls},
            {**_make_correction(message="別の修正"), "preceding_tool_calls": tool_calls},
        ]
        result = reflect.analyze_tool_call_patterns(corrections)
        assert len(result["failure_patterns"]) >= 1
        assert result["failure_patterns"][0]["count"] >= 2
        assert result["failure_patterns"][0]["sequence"] == "Bash(fail) → Edit"

    def test_sequence_below_threshold_not_included(self):
        """シーケンス出現が1件のみ → failure_patterns に含まれない。"""
        corrections = [
            {**_make_correction(), "preceding_tool_calls": [
                {"tool": "Bash", "success": True},
                {"tool": "Read", "success": True},
            ]},
        ]
        result = reflect.analyze_tool_call_patterns(corrections)
        assert result["failure_patterns"] == []

    def test_null_preceding_tool_calls_skipped(self):
        """preceding_tool_calls が null や空のエントリはスキップされる。"""
        corrections = [
            {**_make_correction(), "preceding_tool_calls": None},
            {**_make_correction(), "preceding_tool_calls": []},
            {**_make_correction()},  # フィールドなし
        ]
        result = reflect.analyze_tool_call_patterns(corrections)
        assert result["failure_patterns"] == []
        assert result["failure_rate_by_tool"] == {}


# --- Test: load_recent_error_classes ---

class TestLoadRecentErrorClasses:
    def test_nonexistent_file(self, tmp_path):
        """errors.jsonl が存在しない場合、空の結果を返す。"""
        result = reflect.load_recent_error_classes(errors_file=tmp_path / "errors.jsonl")
        assert result == {"by_class": {}, "by_type": {}}

    def test_load_and_count(self, tmp_path):
        """errors.jsonl から error_class / error_type を集計する。"""
        errors_file = tmp_path / "errors.jsonl"
        records = [
            {"error_class": "tech", "error_type": "rate_limit", "session_id": "s1"},
            {"error_class": "tech", "error_type": "timeout", "session_id": "s2"},
            {"error_class": "tech", "error_type": "rate_limit", "session_id": "s1"},
        ]
        errors_file.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )
        result = reflect.load_recent_error_classes(errors_file=errors_file)
        assert result["by_class"]["tech"] == 3
        assert result["by_type"]["rate_limit"] == 2
        assert result["by_type"]["timeout"] == 1

    def test_session_filter(self, tmp_path):
        """session_ids フィルタを指定した場合、一致するセッションのみ集計する。"""
        errors_file = tmp_path / "errors.jsonl"
        records = [
            {"error_class": "tech", "error_type": "rate_limit", "session_id": "s1"},
            {"error_class": "tech", "error_type": "timeout", "session_id": "s2"},
        ]
        errors_file.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )
        result = reflect.load_recent_error_classes(
            errors_file=errors_file, session_ids=["s1"]
        )
        assert result["by_class"]["tech"] == 1
        assert result["by_type"]["rate_limit"] == 1
        assert "timeout" not in result["by_type"]

    def test_invalid_lines_skipped(self, tmp_path):
        """不正な JSON 行はスキップされる。"""
        errors_file = tmp_path / "errors.jsonl"
        errors_file.write_text(
            '{"error_class": "tech", "error_type": "rate_limit", "session_id": "s1"}\n'
            "invalid json\n",
            encoding="utf-8",
        )
        result = reflect.load_recent_error_classes(errors_file=errors_file)
        assert result["by_class"]["tech"] == 1


# --- Test: build_output includes tool_call_analysis and error_class_summary ---

class TestBuildOutputNewFields:
    def test_tool_call_analysis_in_output(self):
        """build_output の出力に tool_call_analysis が含まれる。"""
        pending = [_make_correction()]
        for c in pending:
            c["_scope"] = "same-project"
            c["routing_hint"] = "project"
            c["suggested_file"] = "/tmp/test.md"
            c["duplicate_found"] = False
            c["duplicate_in"] = None

        with mock.patch("reflect.find_promotion_candidates", return_value=[]):
            with mock.patch("reflect.load_recent_error_classes", return_value={"by_class": {}, "by_type": {}}):
                result = reflect.build_output(pending, pending)

        assert "tool_call_analysis" in result
        assert "failure_patterns" in result["tool_call_analysis"]
        assert "failure_rate_by_tool" in result["tool_call_analysis"]

    def test_error_class_summary_in_output(self):
        """build_output の出力に error_class_summary が含まれる。"""
        pending = [_make_correction()]
        for c in pending:
            c["_scope"] = "same-project"
            c["routing_hint"] = "project"
            c["suggested_file"] = "/tmp/test.md"
            c["duplicate_found"] = False
            c["duplicate_in"] = None

        with mock.patch("reflect.find_promotion_candidates", return_value=[]):
            with mock.patch("reflect.load_recent_error_classes", return_value={"by_class": {"tech": 2}, "by_type": {}}):
                result = reflect.build_output(pending, pending)

        assert "error_class_summary" in result
        assert result["error_class_summary"]["by_class"]["tech"] == 2

    def test_preceding_tool_calls_forwarded(self):
        """preceding_tool_calls がある correction は出力の corrections に含まれる。"""
        calls = [{"tool": "Bash", "success": False}, {"tool": "Edit", "success": True}]
        c = _make_correction()
        c["_scope"] = "same-project"
        c["routing_hint"] = "project"
        c["suggested_file"] = "/tmp/test.md"
        c["duplicate_found"] = False
        c["duplicate_in"] = None
        c["preceding_tool_calls"] = calls

        with mock.patch("reflect.find_promotion_candidates", return_value=[]):
            with mock.patch("reflect.load_recent_error_classes", return_value={"by_class": {}, "by_type": {}}):
                result = reflect.build_output([c], [c])

        assert result["corrections"][0]["preceding_tool_calls"] == calls


# --- Test: episodic integration (3層メモリ) ---

def _pending_correction(**kwargs):
    c = _make_correction(**kwargs)
    c["_scope"] = "same-project"
    c["routing_hint"] = "project"
    c["suggested_file"] = "/tmp/test.md"
    c["duplicate_found"] = False
    c["duplicate_in"] = None
    return c


class TestBuildOutputEpisodicContext:
    def test_no_episodic_when_disabled(self):
        """_HAS_EPISODIC=False のとき episodic_context フィールドは付かない。"""
        c = _pending_correction()
        with mock.patch("reflect._HAS_EPISODIC", False):
            with mock.patch("reflect.find_promotion_candidates", return_value=[]):
                with mock.patch("reflect.load_recent_error_classes", return_value={"by_class": {}, "by_type": {}}):
                    result = reflect.build_output([c], [c])
        assert "episodic_context" not in result["corrections"][0]

    def test_episodic_context_added_when_match(self):
        """find_episodic_duplicates がマッチを返すと episodic_context が付く。"""
        c = _pending_correction()
        fake_match = [{
            "correction_index": 0,
            "episodic_id": "s1#ts1",
            "episodic_content": "git diff で確認",
            "days_ago": 3,
            "score": 0.5,
        }]
        with mock.patch("reflect._HAS_EPISODIC", True):
            with mock.patch("reflect.find_episodic_duplicates", return_value=fake_match):
                with mock.patch("reflect.find_promotion_candidates", return_value=[]):
                    with mock.patch("reflect.load_recent_error_classes", return_value={"by_class": {}, "by_type": {}}):
                        result = reflect.build_output([c], [c])
        entry = result["corrections"][0]
        assert "episodic_context" in entry
        assert entry["episodic_context"]["days_ago"] == 3
        assert entry["episodic_context"]["score"] == 0.5

    def test_episodic_sets_duplicate_in(self):
        """episodic match があり duplicate_found=False の場合 duplicate_in が 'episodic' になる。"""
        c = _pending_correction()
        fake_match = [{
            "correction_index": 0,
            "episodic_id": "s1#ts1",
            "episodic_content": "既出修正",
            "days_ago": 5,
            "score": 0.4,
        }]
        with mock.patch("reflect._HAS_EPISODIC", True):
            with mock.patch("reflect.find_episodic_duplicates", return_value=fake_match):
                with mock.patch("reflect.find_promotion_candidates", return_value=[]):
                    with mock.patch("reflect.load_recent_error_classes", return_value={"by_class": {}, "by_type": {}}):
                        result = reflect.build_output([c], [c])
        assert result["corrections"][0]["duplicate_in"] == "episodic"

    def test_existing_duplicate_not_overwritten(self):
        """すでに duplicate_found=True の場合 duplicate_in は上書きしない。"""
        c = _pending_correction()
        c["duplicate_found"] = True
        c["duplicate_in"] = "CLAUDE.md"
        fake_match = [{
            "correction_index": 0,
            "episodic_id": "s1#ts1",
            "episodic_content": "既出",
            "days_ago": 1,
            "score": 0.3,
        }]
        with mock.patch("reflect._HAS_EPISODIC", True):
            with mock.patch("reflect.find_episodic_duplicates", return_value=fake_match):
                with mock.patch("reflect.find_promotion_candidates", return_value=[]):
                    with mock.patch("reflect.load_recent_error_classes", return_value={"by_class": {}, "by_type": {}}):
                        result = reflect.build_output([c], [c])
        assert result["corrections"][0]["duplicate_in"] == "CLAUDE.md"


class TestPromoteEpisodicSubcommand:
    def test_promote_episodic_not_found(self, tmp_path, capsys):
        """--promote-episodic で対象 correction が見つからない場合 exit(1) + not_found JSON を返す。"""
        filepath = _write_corrections(tmp_path, [_make_correction()])
        with mock.patch("sys.argv", [
            "reflect", "--promote-episodic",
            "--session-id", "nonexistent",
            "--timestamp", "2099-01-01T00:00:00+00:00",
            "--corrections-file", str(filepath),
        ]):
            with mock.patch("reflect.promote_to_episodic") as mock_promote:
                with pytest.raises(SystemExit) as exc_info:
                    reflect.main()
                assert exc_info.value.code == 1
                mock_promote.assert_not_called()
        captured = capsys.readouterr()
        import json as _json
        out = _json.loads(captured.out)
        assert out["status"] == "not_found"

    def test_promote_episodic_calls_promote(self, tmp_path):
        """--promote-episodic で対象 correction が見つかると promote_to_episodic が呼ばれる。"""
        ts = datetime.now(timezone.utc).isoformat()
        sid = "session-abc"
        c = _make_correction(timestamp=ts)
        c["session_id"] = sid
        filepath = _write_corrections(tmp_path, [c])
        with mock.patch("sys.argv", [
            "reflect", "--promote-episodic",
            "--session-id", sid,
            "--timestamp", ts,
            "--corrections-file", str(filepath),
        ]):
            with mock.patch("reflect.promote_to_episodic", return_value=True) as mock_promote:
                reflect.main()
                mock_promote.assert_called_once()
                called_corr = mock_promote.call_args[0][0]
                assert called_corr["session_id"] == sid


# --- Test: weak_signals 昇格フロー（#431/#432 二層化） ---

class TestWeakSignalPromotion:
    def _seed_ws(self, tmp_path):
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals
        ws = tmp_path / "weak_signals.jsonl"
        sigs = [
            WeakSignal("llm_judge", {"source_path": "/a.jsonl", "line_no": 1,
                                     "text": "緑にして赤じゃなくて", "reason": "後置型"},
                       "2026-06-10T00:00:00+00:00", "s1", "rl-anything"),
            WeakSignal("rephrase", {"x": 1}, "2026-06-10T00:01:00+00:00", "s2", "rl-anything"),
        ]
        append_signals(sigs, path=ws)
        return ws, sigs

    def test_show_weak_signals_cli(self, tmp_path, capsys):
        ws, _ = self._seed_ws(tmp_path)
        with mock.patch("sys.argv", ["reflect", "--show-weak-signals",
                                     "--weak-signals-file", str(ws)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "weak_signals"
        assert out["count"] == 2

    def test_show_weak_signals_channel_filter(self, tmp_path, capsys):
        ws, _ = self._seed_ws(tmp_path)
        with mock.patch("sys.argv", ["reflect", "--show-weak-signals",
                                     "--weak-channel", "llm_judge",
                                     "--weak-signals-file", str(ws)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 1

    def test_promote_weak_writes_human_correction(self, tmp_path, capsys):
        ws, sigs = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--promote-weak", sigs[0].signal_key,
                                     "--weak-signals-file", str(ws),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "promoted_weak"
        assert out["promoted"] == 1
        recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(recs) == 1
        assert recs[0]["source"] == "reflect_confirmed"

    def test_promote_weak_dry_run(self, tmp_path, capsys):
        ws, sigs = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--promote-weak", sigs[0].signal_key,
                                     "--dry-run",
                                     "--weak-signals-file", str(ws),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True
        assert out["promoted"] == 1
        assert not corr.exists()

    def test_promote_weak_returns_updated_human_count(self, tmp_path, capsys):
        """--promote-weak が昇格後の corrections_human カウントを返す（#476-4 stale 表示の解消）。

        growth_report の promoted_today は対話前スナップショットで固定されるため、promote CLI
        が更新後カウントを返し assistant が最新値を表示できるようにする。
        """
        ws, sigs = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--promote-weak", sigs[0].signal_key,
                                     "--weak-signals-file", str(ws),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "promoted_weak"
        # 昇格直後の corrections_human（source=reflect_confirmed の human-source）が反映される
        assert out["corrections_human"] == 1

    def test_promote_weak_dry_run_human_count_unchanged(self, tmp_path, capsys):
        """dry_run では corrections に書かないので corrections_human は変動しない（#476-4）。"""
        ws, sigs = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--promote-weak", sigs[0].signal_key,
                                     "--dry-run",
                                     "--weak-signals-file", str(ws),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True
        assert out["corrections_human"] == 0


# --- Test: --promote-weak が idiom を confirmed 化する閉ループ（#463 配線漏れ修正） ---

class TestPromoteWeakConfirmsIdiom:
    SLUG = "rl-anything"

    def _prov(self, line_no, text):
        return {"source_path": "/a.jsonl", "line_no": line_no, "session_id": "s1",
                "text": text, "reason": "後置型", "judge": "llm_haiku"}

    def _seed(self, tmp_path, line_no, text):
        """同じ provenance を共有する weak_signal + idiom を seed（batch.py と同型）。"""
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals
        import correction_semantic.store as cs_store
        ws = tmp_path / "weak_signals.jsonl"
        idioms = tmp_path / "correction_idioms.jsonl"
        prov = self._prov(line_no, text)
        sig = WeakSignal("llm_judge", prov, "2026-06-10T00:00:00+00:00", "s1", self.SLUG)
        append_signals([sig], path=ws)
        it = cs_store.CorrectionIdiom(
            idiom=text, provenance=prov, detected_at="2026-06-10T00:00:00+00:00", pj_slug=self.SLUG,
        )
        cs_store.append_idioms([it], path=idioms)
        return ws, idioms, sig, it

    def test_promote_weak_confirms_corresponding_idiom(self, tmp_path, capsys):
        """正規フロー（CLI 経由 --promote-weak）の承認だけで idiom confirmed=True が立つ。"""
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        import correction_semantic.store as cs_store
        text = "四国めたんじゃなくて"
        ws, idioms, sig, it = self._seed(tmp_path, line_no=1, text=text)
        corr = tmp_path / "corrections.jsonl"

        with mock.patch("sys.argv", ["reflect", "--promote-weak", sig.signal_key,
                                     "--weak-signals-file", str(ws),
                                     "--idioms-file", str(idioms),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "promoted_weak"
        assert out["promoted"] == 1
        assert out.get("confirmed_idioms", 0) >= 1
        # corrections に human-source 1 件
        recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(recs) == 1
        # 当該 idiom が confirmed=True
        assert cs_store.read_confirmed_idiom_texts(self.SLUG, idioms) == {text}

    def test_closed_loop_autopromote_fires_after_confirm(self, tmp_path, capsys):
        """閉ループ E2E: --promote-weak で confirmed 化 → 同テキストの新規 signal を autopromote が昇格。"""
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals
        import correction_semantic.store as cs_store
        from correction_semantic import idiom_autopromote as iap
        text = "四国めたんじゃなくて"
        ws, idioms, sig, it = self._seed(tmp_path, line_no=1, text=text)
        corr = tmp_path / "corrections.jsonl"

        # (b) --promote-weak 相当のフロー（reflect.py 経由）
        with mock.patch("sys.argv", ["reflect", "--promote-weak", sig.signal_key,
                                     "--weak-signals-file", str(ws),
                                     "--idioms-file", str(idioms),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        capsys.readouterr()
        # (c) corrections +1 / idiom confirmed=True
        recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(recs) == 1
        assert cs_store.read_confirmed_idiom_texts(self.SLUG, idioms) == {text}

        # (d) 同テキストの新規 weak_signal（別 phys）+ 新 idiom record を投入
        prov99 = self._prov(99, text)
        append_signals([WeakSignal("llm_judge", prov99, "2026-06-20T00:00:00+00:00",
                                   "s1", self.SLUG)], path=ws)
        it99 = cs_store.CorrectionIdiom(
            idiom=text, provenance=prov99, detected_at="2026-06-20T00:00:00+00:00", pj_slug=self.SLUG,
        )
        cs_store.append_idioms([it99], path=idioms)

        ap = iap.autopromote(self.SLUG, weak_signals_path=ws, idioms_path=idioms,
                             corrections_path=corr)
        assert ap["promoted"] >= 1  # confirmed 後の再発で実発火
        recs2 = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert any(r.get("source") == "idiom_dict" for r in recs2)

    def test_promote_weak_confirm_dry_run_writes_nothing(self, tmp_path, capsys):
        """dry-run: corrections / weak_signals / idioms すべてバイト不変（最下層 write ゲート）。"""
        text = "四国めたんじゃなくて"
        ws, idioms, sig, it = self._seed(tmp_path, line_no=1, text=text)
        corr = tmp_path / "corrections.jsonl"
        before_ws = ws.read_text(encoding="utf-8")
        before_idioms = idioms.read_text(encoding="utf-8")

        with mock.patch("sys.argv", ["reflect", "--promote-weak", sig.signal_key, "--dry-run",
                                     "--weak-signals-file", str(ws),
                                     "--idioms-file", str(idioms),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True
        assert not corr.exists()  # corrections 非書込
        assert ws.read_text(encoding="utf-8") == before_ws  # weak_signals 不変
        assert idioms.read_text(encoding="utf-8") == before_idioms  # idioms 不変（confirmed 立たず）


# --- Test: --revoke-idiom（安全弁③・ADR-047 #447） ---

class TestRevokeIdiom:
    def _seed(self, tmp_path):
        """confirmed idiom + その idiom_key 由来の idiom_dict 昇格 corrections を作る。"""
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        import correction_semantic.store as cs_store

        idioms = tmp_path / "correction_idioms.jsonl"
        it = cs_store.CorrectionIdiom(
            idiom="四国めたんじゃなくて",
            provenance={"source_path": "/a.jsonl", "line_no": 1, "reason": "後置型"},
            detected_at="2026-06-10T00:00:00+00:00", pj_slug="rl-anything",
        )
        cs_store.append_idioms([it], path=idioms)
        cs_store.confirm_idioms([it.idiom_key], path=idioms, confirmed_by="daily_review")

        corr = tmp_path / "corrections.jsonl"
        with open(corr, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "source": "idiom_dict", "promoted_by": "idiom_dict",
                "idiom_key": it.idiom_key, "invalidated": False,
                "correction_type": "semantic_idiom", "message": "四国めたんじゃなくて",
            }, ensure_ascii=False) + "\n")
        return idioms, corr, it.idiom_key

    def test_revoke_idiom_rolls_back(self, tmp_path, capsys):
        idioms, corr, key = self._seed(tmp_path)
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        import correction_semantic.store as cs_store
        from correction_semantic import provenance_weight as pw

        # 巻き戻し前: human-source 1 件
        before = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert pw.count_human_corrections(before) == 1

        with mock.patch("sys.argv", ["reflect", "--revoke-idiom", key,
                                     "--idioms-file", str(idioms),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "revoked_idiom"
        assert out["revoked"] >= 1
        assert out["invalidated"] == 1

        # idiom は confirmed=False + revoked_at（autopromote 対象外）
        assert cs_store.read_confirmed_idiom_texts("rl-anything", idioms) == set()
        # corrections は invalidated=True → human カウントから除外（進捗巻き戻り）
        after = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert after[0]["invalidated"] is True
        assert pw.count_human_corrections(after) == 0

    def test_revoke_idiom_dry_run_writes_nothing(self, tmp_path, capsys):
        idioms, corr, key = self._seed(tmp_path)
        before_corr = corr.read_text(encoding="utf-8")
        before_idioms = idioms.read_text(encoding="utf-8")
        with mock.patch("sys.argv", ["reflect", "--revoke-idiom", key, "--dry-run",
                                     "--idioms-file", str(idioms),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True
        assert corr.read_text(encoding="utf-8") == before_corr
        assert idioms.read_text(encoding="utf-8") == before_idioms
