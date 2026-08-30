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
    session_id=None,
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
    if session_id:
        record["session_id"] = session_id
    return record


def _write_corrections(tmp_path, corrections):
    """corrections.jsonl を一時ディレクトリに書き出す。"""
    filepath = tmp_path / "corrections.jsonl"
    lines = [json.dumps(c, ensure_ascii=False) for c in corrections]
    filepath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return filepath


def _fresh_detected_at() -> str:
    """TTL 対象の weak signal を常に有効な時刻で seed する。"""
    return datetime.now(timezone.utc).isoformat()


# --- Test: extract_pending ---

class TestExtractPending:
    def test_pending_and_promoted_only(self):
        """#475 §5.1: pending と promoted（昇格済み・反映先未定）を返す。

        applied/skipped は拾わない。promoted を落とすと「いまは反映しない」を選んだ
        保留が reflect のバッチレビューから永久に消える（§5.1 の穴の再発）。
        """
        records = [
            _make_correction(reflect_status="pending"),
            _make_correction(reflect_status="applied"),
            _make_correction(reflect_status="skipped"),
            _make_correction(reflect_status="pending"),
            _make_correction(reflect_status="promoted"),
        ]
        result = reflect.extract_pending(records)
        assert len(result) == 3
        assert {r["reflect_status"] for r in result} == {"pending", "promoted"}

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

    def test_skip_all_includes_promoted(self, tmp_path):
        """#475 §5.1: --skip-all の対象は pending だけでなく promoted も含めて畳める。"""
        corrections = [
            _make_correction(reflect_status="pending"),
            _make_correction(reflect_status="promoted"),
            _make_correction(reflect_status="applied"),
        ]
        filepath = _write_corrections(tmp_path, corrections)

        with mock.patch("sys.argv", ["reflect.py", "--skip-all", "--corrections-file", str(filepath)]):
            reflect.main()

        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "skipped"
        assert updated[1]["reflect_status"] == "skipped"
        assert updated[2]["reflect_status"] == "applied"

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
        """趣旨が類似した message が2回以上再発 → 1クラスタ = 1昇格候補（#184:
        message/extracted_learning の類似度クラスタで判定。correction_type バケット総数
        では判定しない）。occurrences はクラスタ内の再発回数。"""
        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        records = [
            _make_correction(correction_type="iya", reflect_status="applied",
                             message="always run tests before commit", timestamp=old_ts),
            _make_correction(correction_type="iya", reflect_status="applied",
                             message="always run tests before merge", timestamp=old_ts),
        ]
        with mock.patch("reflect.read_auto_memory", return_value=[]):
            result = reflect.find_promotion_candidates(records)
        # 2つの類似 message は1クラスタに合流し occurrences=2 の候補1件になる
        assert len(result) == 1
        assert result[0]["occurrences"] == 2

    def test_same_type_unrelated_messages_not_conflated(self):
        """同一 correction_type でも message が無関係なら再発件数を混同しない（#184 回帰）。

        修正前は correction_type バケット総数を occurrences に代入していたため、
        無関係な message でも同一 type というだけで昇格候補が誤検出されていた。
        """
        recent_ts = datetime.now(timezone.utc).isoformat()
        records = [
            _make_correction(correction_type="iya", reflect_status="applied",
                             message="always run tests before commit", timestamp=recent_ts),
            _make_correction(correction_type="iya", reflect_status="applied",
                             message="please write clear docstrings for new functions",
                             timestamp=recent_ts),
        ]
        with mock.patch("reflect.read_auto_memory", return_value=[]):
            result = reflect.find_promotion_candidates(records)
        # 両方 recent（age 未達）かつ意味的に無関係（occurrences=1）なので昇格候補なし
        assert result == []

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
    def test_logical_index_reidentifies_physical_line_past_blank_and_malformed_rows(
        self, tmp_path
    ):
        """#588: filtered index must not be reused as a physical JSONL line number."""
        first = _make_correction(
            message="first", session_id="sess-first", timestamp="2026-08-16T00:00:00Z"
        )
        target = _make_correction(
            message="target", session_id="sess-target", timestamp="2026-08-17T00:00:00Z"
        )
        filepath = tmp_path / "corrections.jsonl"
        filepath.write_text(
            json.dumps(first, ensure_ascii=False)
            + "\n\n{broken json\n"
            + json.dumps(target, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )

        reflect.update_reflect_status(filepath, [1], "skipped")

        physical_lines = filepath.read_text(encoding="utf-8").splitlines()
        assert physical_lines[1] == ""
        assert physical_lines[2] == "{broken json"
        assert json.loads(physical_lines[0])["reflect_status"] == "pending"
        assert json.loads(physical_lines[3])["reflect_status"] == "skipped"

    def test_update_specific_indices(self, tmp_path):
        """指定インデックスのみ更新する（"applied" 以外の status には target_path/draft_line 不要）。"""
        corrections = [
            _make_correction(message="msg0"),
            _make_correction(message="msg1"),
            _make_correction(message="msg2"),
        ]
        filepath = _write_corrections(tmp_path, corrections)
        reflect.update_reflect_status(filepath, [0, 2], "skipped")

        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "skipped"
        assert updated[1]["reflect_status"] == "pending"
        assert updated[2]["reflect_status"] == "skipped"

    def test_skip_all_signature_unchanged(self, tmp_path):
        """既存 --skip-all 呼び出し（target_path/draft_line 省略）が無改修で動く（MUST）。"""
        corrections = [_make_correction(message="msg0")]
        filepath = _write_corrections(tmp_path, corrections)
        result = reflect.update_reflect_status(filepath, [0], "skipped")
        assert result["status"] == "skipped"

    def test_applied_without_target_path_raises(self, tmp_path):
        """#475 §6.1 関数契約: status="applied" は target_path/draft_line が必須。"""
        corrections = [_make_correction(message="msg0")]
        filepath = _write_corrections(tmp_path, corrections)
        with pytest.raises(ValueError):
            reflect.update_reflect_status(filepath, [0], "applied")

    def test_applied_without_draft_line_raises(self, tmp_path):
        corrections = [_make_correction(message="msg0")]
        filepath = _write_corrections(tmp_path, corrections)
        with pytest.raises(ValueError):
            reflect.update_reflect_status(
                filepath, [0], "applied", target_path=str(tmp_path / "rule.md"),
            )

    def test_applied_when_line_matches_target_file(self, tmp_path):
        """#475 §6.1: 反映先ファイルに該当行が実在すれば applied を書く。"""
        corrections = [_make_correction(message="msg0")]
        filepath = _write_corrections(tmp_path, corrections)
        target = tmp_path / "rule.md"
        target.write_text("- 起草した行そのもの\n", encoding="utf-8")

        result = reflect.update_reflect_status(
            filepath, [0], "applied",
            target_path=str(target), draft_line="起草した行そのもの",
        )

        assert result["status"] == "applied"
        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "applied"

    def test_apply_unverified_when_line_absent(self, tmp_path):
        """#475 §6.1: 反映先ファイルに該当行が無ければ applied を書かず promoted のまま残す。"""
        corrections = [_make_correction(message="msg0", reflect_status="promoted")]
        filepath = _write_corrections(tmp_path, corrections)
        target = tmp_path / "rule.md"
        target.write_text("- 別の行\n", encoding="utf-8")

        result = reflect.update_reflect_status(
            filepath, [0], "applied",
            target_path=str(target), draft_line="書いていない行",
        )

        assert result["status"] == "apply_unverified"
        updated = reflect.load_corrections(filepath)
        # 黙って成功にしない: reflect_status は変更されない
        assert updated[0]["reflect_status"] == "promoted"

    def test_apply_unverified_when_target_missing(self, tmp_path):
        corrections = [_make_correction(message="msg0", reflect_status="promoted")]
        filepath = _write_corrections(tmp_path, corrections)
        target = tmp_path / "does-not-exist.md"

        result = reflect.update_reflect_status(
            filepath, [0], "applied",
            target_path=str(target), draft_line="何かの行",
        )

        assert result["status"] == "apply_unverified"
        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "promoted"


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


# --- Test: --apply CLI (#475 §6.1) ---

class TestApplyCLI:
    def test_apply_reidentifies_target_after_blank_physical_line(self, tmp_path, capsys):
        """#588: CLI may report applied only after updating the matching source ID."""
        other = _make_correction(
            message="other", reflect_status="pending", session_id="sess0",
            timestamp="2026-08-16T00:00:00Z",
        )
        corr = _make_correction(
            message="target", reflect_status="promoted", session_id="sess1",
            timestamp="2026-08-17T00:00:00Z",
        )
        filepath = tmp_path / "corrections.jsonl"
        filepath.write_text(
            json.dumps(other, ensure_ascii=False) + "\n\n"
            + json.dumps(corr, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        target = tmp_path / "rule.md"
        target.write_text("- 起草した行\n", encoding="utf-8")
        draft_line_file = tmp_path / "draft.txt"
        draft_line_file.write_text("起草した行", encoding="utf-8")
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--apply", source_id,
            "--target-path", str(target),
            "--draft-line-file", str(draft_line_file),
            "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "applied"
        physical_lines = filepath.read_text(encoding="utf-8").splitlines()
        assert json.loads(physical_lines[0])["reflect_status"] == "pending"
        assert physical_lines[1] == ""
        assert json.loads(physical_lines[2])["reflect_status"] == "applied"

    def test_apply_marks_applied_when_line_matches(self, tmp_path, capsys):
        """反映先ファイルに該当行が実在すれば applied を書く。"""
        corr = _make_correction(reflect_status="promoted", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        target = tmp_path / "rule.md"
        target.write_text("- 起草した行\n", encoding="utf-8")
        draft_line_file = tmp_path / "draft.txt"
        draft_line_file.write_text("起草した行", encoding="utf-8")
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--apply", source_id,
            "--target-path", str(target),
            "--draft-line-file", str(draft_line_file),
            "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "applied"
        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "applied"

    def test_apply_unverified_when_line_missing(self, tmp_path, capsys):
        """反映先ファイルに該当行が無ければ apply_unverified を返し promoted のまま残す。"""
        corr = _make_correction(reflect_status="promoted", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        target = tmp_path / "rule.md"
        target.write_text("- 別の行\n", encoding="utf-8")
        draft_line_file = tmp_path / "draft.txt"
        draft_line_file.write_text("書いていない行", encoding="utf-8")
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--apply", source_id,
            "--target-path", str(target),
            "--draft-line-file", str(draft_line_file),
            "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "apply_unverified"
        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "promoted"

    def test_apply_not_found(self, tmp_path, capsys):
        """source_correction_id に一致する correction が無ければ not_found。"""
        filepath = _write_corrections(tmp_path, [_make_correction(session_id="sess1", timestamp="2026-08-17T00:00:00Z")])
        target = tmp_path / "rule.md"
        target.write_text("- x\n", encoding="utf-8")
        draft_line_file = tmp_path / "draft.txt"
        draft_line_file.write_text("x", encoding="utf-8")

        with mock.patch("sys.argv", [
            "reflect.py", "--apply", "no-such-id",
            "--target-path", str(target),
            "--draft-line-file", str(draft_line_file),
            "--corrections-file", str(filepath),
        ]):
            with pytest.raises(SystemExit):
                reflect.main()

    def test_apply_missing_target_path_errors(self, tmp_path, capsys):
        """--target-path/--draft-line-file を欠くと error で終了する。"""
        filepath = _write_corrections(tmp_path, [_make_correction()])
        with mock.patch("sys.argv", [
            "reflect.py", "--apply", "some-id", "--corrections-file", str(filepath),
        ]):
            with pytest.raises(SystemExit):
                reflect.main()

    def test_apply_dry_run_writes_nothing(self, tmp_path, capsys):
        """--dry-run では一切書かない（既存 dry-run ゲート貫通規約）。"""
        corr = _make_correction(reflect_status="promoted", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        before_bytes = filepath.read_bytes()
        target = tmp_path / "rule.md"
        target.write_text("- 起草した行\n", encoding="utf-8")
        draft_line_file = tmp_path / "draft.txt"
        draft_line_file.write_text("起草した行", encoding="utf-8")
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--dry-run", "--apply", source_id,
            "--target-path", str(target),
            "--draft-line-file", str(draft_line_file),
            "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "dry_run"
        assert filepath.read_bytes() == before_bytes


# --- Test: --skip（#514 修正在庫の『もう出さない』） ---

class TestSkipCLI:
    def test_skip_marks_skipped_when_promoted(self, tmp_path, capsys):
        """promoted の correction を skipped にする。"""
        corr = _make_correction(reflect_status="promoted", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--skip", source_id, "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "skipped"
        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "skipped"

    def test_skip_not_found(self, tmp_path, capsys):
        """source_correction_id に一致する correction が無ければ not_found。"""
        filepath = _write_corrections(tmp_path, [_make_correction(session_id="sess1", timestamp="2026-08-17T00:00:00Z")])

        with mock.patch("sys.argv", [
            "reflect.py", "--skip", "no-such-id", "--corrections-file", str(filepath),
        ]):
            with pytest.raises(SystemExit):
                reflect.main()

    def test_skip_does_not_overwrite_applied(self, tmp_path, capsys):
        """既に applied 済みのレコードは --skip で上書きしない（安全弁）。"""
        corr = _make_correction(reflect_status="applied", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--skip", source_id, "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "already_applied"
        updated = reflect.load_corrections(filepath)
        assert updated[0]["reflect_status"] == "applied"

    def test_skip_dry_run_writes_nothing(self, tmp_path, capsys):
        """--dry-run では一切書かない（--apply と同じゲート貫通規約）。"""
        corr = _make_correction(reflect_status="promoted", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        before_bytes = filepath.read_bytes()
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--dry-run", "--skip", source_id, "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "dry_run"
        assert filepath.read_bytes() == before_bytes


# --- Test: 取り消し記録（#475 §8.2） ---

class TestRuleRevertRecording:
    def test_global_rule_scope_detected(self, tmp_path):
        """~/.claude/rules 配下は global_rule scope になる。"""
        target = Path.home() / ".claude" / "rules" / "some-rule.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- 行\n", encoding="utf-8")
        identity = reflect._rule_scope_identity(str(target))
        assert identity["scope"] == "global_rule"
        assert identity["relative_path"] == "some-rule.md"

    def test_project_rule_scope_relative_path_excludes_claude_rules_prefix(self, tmp_path, monkeypatch):
        """#475 rev2: project_rule の relative_path は `.claude/rules/` を含まない
        （rules root からの相対）。B レーンの resolve_target が
        `<repo_id>/.claude/rules/<relative_path>` で解決するため、含めると二重パスになる。
        """
        repo = tmp_path / "repo"
        rules_dir = repo / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        target = rules_dir / "some-rule.md"
        target.write_text("- 行\n", encoding="utf-8")
        subprocess_run = __import__("subprocess").run
        subprocess_run(["git", "init", "-q"], cwd=str(repo), check=True)

        identity = reflect._rule_scope_identity(str(target))

        assert identity["scope"] == "project_rule"
        assert identity["relative_path"] == "some-rule.md"
        assert "/.claude/rules/" not in identity["relative_path"]

    def test_non_rule_path_returns_none(self, tmp_path):
        """rules 配下でないファイルは記録対象外（None）。"""
        target = tmp_path / "not-a-rule.md"
        target.write_text("x\n", encoding="utf-8")
        identity = reflect._rule_scope_identity(str(target))
        assert identity is None

    def test_record_rule_revert_entry_writes_optimize_history(self, tmp_path):
        """revert 記録が optimize_history の既存フォーマットで1件 append される。"""
        target = Path.home() / ".claude" / "rules" / "some-rule.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- 既存行\n- 起草した行\n", encoding="utf-8")

        result = reflect.record_rule_revert_entry(
            str(target),
            before_content="- 既存行\n",
            after_content=target.read_text(encoding="utf-8"),
            pj_slug="test-slug",
        )
        assert result["recorded"] is True
        assert result["written"] is True

        from optimize_history_store import history_path
        entries = [
            json.loads(l) for l in history_path("test-slug").read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["scope"] == "global_rule"
        assert entry["relative_path"] == "some-rule.md"
        assert "revert_before_b64" in entry
        assert "revert_schema_version" in entry

    def test_record_rule_revert_entry_roundtrips_through_target_resolve_global(self, tmp_path):
        """#475 rev2 修正2: A が書いた entry を B の resolve_target が実際に解決できる
        （global_rule）。片側だけの検査では今回の食い違いを検出できなかったため往復させる。
        """
        target = Path.home() / ".claude" / "rules" / "roundtrip-global.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- 既存行\n- 起草した行\n", encoding="utf-8")

        result = reflect.record_rule_revert_entry(
            str(target),
            before_content="- 既存行\n",
            after_content=target.read_text(encoding="utf-8"),
            pj_slug="test-slug",
        )
        assert result["recorded"] is True

        from optimize_history_store import history_path
        entries = [
            json.loads(l) for l in history_path("test-slug").read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        entry = entries[0]

        from evolve_revert._target import resolve_target
        resolution = resolve_target(entry)

        assert resolution.ok is True
        assert resolution.path == target.resolve()

    def test_record_rule_revert_entry_roundtrips_through_target_resolve_project(self, tmp_path):
        """#475 rev2 修正2: 往復テスト（project_rule）。global 側だけでなく project 側も
        resolve_target が同じファイルを解決できることを確認する（今回 project 側だけ壊れていた）。
        """
        repo = tmp_path / "repo"
        rules_dir = repo / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        target = rules_dir / "roundtrip-project.md"
        target.write_text("- 既存行\n- 起草した行\n", encoding="utf-8")
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)

        result = reflect.record_rule_revert_entry(
            str(target),
            before_content="- 既存行\n",
            after_content=target.read_text(encoding="utf-8"),
            pj_slug="test-slug",
        )
        assert result["recorded"] is True

        from optimize_history_store import history_path
        entries = [
            json.loads(l) for l in history_path("test-slug").read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        entry = entries[0]

        from evolve_revert._target import resolve_target
        resolution = resolve_target(entry)

        assert resolution.ok is True
        assert resolution.path == target.resolve()

    def test_record_rule_revert_entry_not_recorded_for_non_rule_target(self, tmp_path):
        target = tmp_path / "not-a-rule.md"
        target.write_text("x\n", encoding="utf-8")
        result = reflect.record_rule_revert_entry(
            str(target), before_content="x\n", after_content="x\n", pj_slug="test-slug",
        )
        assert result["recorded"] is False
        assert result["reason"] == "not_rule_scope"

    def test_record_rule_revert_entry_new_file_not_revertible(self, tmp_path):
        """#475 rev2 修正3: before が空（新規ファイル作成）は revert 未対応を明示する
        （黙って revert_recorded=False を返すだけでなく reason を残す・optimize_history は書かない）。
        """
        target = Path.home() / ".claude" / "rules" / "brand-new.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- 起草した行\n", encoding="utf-8")

        result = reflect.record_rule_revert_entry(
            str(target), before_content="", after_content="- 起草した行\n", pj_slug="test-slug",
        )

        assert result["recorded"] is False
        assert result["reason"] == "new_file_not_revertible"
        from optimize_history_store import history_path
        assert not history_path("test-slug").exists()

    def test_record_rule_revert_entry_is_classified_accepted(self, tmp_path):
        """#512: 書いた entry が reader 側（results_board.classify_decision）で accepted になる。

        writer 単体の形だけを見る検査では #512 を検出できなかった（entry は正しく書けていたが
        reader が pending に落としていた）ため、writer→reader を往復させる。
        """
        target = Path.home() / ".claude" / "rules" / "classify-roundtrip.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- 既存行\n- 起草した行\n", encoding="utf-8")

        result = reflect.record_rule_revert_entry(
            str(target),
            before_content="- 既存行\n",
            after_content=target.read_text(encoding="utf-8"),
            pj_slug="test-slug",
        )
        assert result["recorded"] is True

        from optimize_history_store import history_path
        entries = [
            json.loads(l) for l in history_path("test-slug").read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        assert len(entries) == 1

        import results_board
        from evolve_revert import compute_revert_availability

        assert results_board.classify_decision(entries[0]) == "accepted"
        # 「戻せると判定されるのに一覧から脱落する」状態を再発させない（#512 の症状そのもの）。
        assert compute_revert_availability(entries[0]) == (True, None)

    def test_apply_cli_records_revert_when_before_content_file_given(self, tmp_path, capsys):
        """--apply に --before-content-file を渡すと revert_recorded=True になる。"""
        corr = _make_correction(reflect_status="promoted", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        target = Path.home() / ".claude" / "rules" / "apply-record.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- 既存行\n- 起草した行\n", encoding="utf-8")
        draft_line_file = tmp_path / "draft.txt"
        draft_line_file.write_text("起草した行", encoding="utf-8")
        before_content_file = tmp_path / "before.txt"
        before_content_file.write_text("- 既存行\n", encoding="utf-8")
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--apply", source_id,
            "--target-path", str(target),
            "--draft-line-file", str(draft_line_file),
            "--before-content-file", str(before_content_file),
            "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "applied"
        assert output["revert_recorded"] is True

    def test_apply_cli_without_before_content_file_skips_recording_when_not_rule_scope(self, tmp_path, capsys):
        """反映先が rules 配下でなければ --before-content-file は不要（従来どおり）。"""
        corr = _make_correction(reflect_status="promoted", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        target = tmp_path / "rule.md"
        target.write_text("- 起草した行\n", encoding="utf-8")
        draft_line_file = tmp_path / "draft.txt"
        draft_line_file.write_text("起草した行", encoding="utf-8")
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--apply", source_id,
            "--target-path", str(target),
            "--draft-line-file", str(draft_line_file),
            "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "applied"
        assert "revert_recorded" not in output

    def test_apply_cli_requires_before_content_file_when_rule_scope(self, tmp_path, capsys):
        """#475 rev2 修正3: 反映先が rules 配下なのに --before-content-file を省略すると
        error で落ちる（黙って revert 記録をスキップしない）。
        """
        corr = _make_correction(reflect_status="promoted", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        target = Path.home() / ".claude" / "rules" / "requires-before.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- 起草した行\n", encoding="utf-8")
        draft_line_file = tmp_path / "draft.txt"
        draft_line_file.write_text("起草した行", encoding="utf-8")
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--apply", source_id,
            "--target-path", str(target),
            "--draft-line-file", str(draft_line_file),
            "--corrections-file", str(filepath),
        ]):
            with pytest.raises(SystemExit):
                reflect.main()

    def test_apply_cli_new_file_records_not_revertible(self, tmp_path, capsys):
        """新規ファイル作成（--before-content-file が空ファイル）は applied にはなるが
        revert_recorded=False・revert_reason=new_file_not_revertible を明示する。
        """
        corr = _make_correction(reflect_status="promoted", session_id="sess1", timestamp="2026-08-17T00:00:00Z")
        filepath = _write_corrections(tmp_path, [corr])
        target = Path.home() / ".claude" / "rules" / "brand-new-cli.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("- 起草した行\n", encoding="utf-8")
        draft_line_file = tmp_path / "draft.txt"
        draft_line_file.write_text("起草した行", encoding="utf-8")
        before_content_file = tmp_path / "before-empty.txt"
        before_content_file.write_text("", encoding="utf-8")
        source_id = reflect.make_source_correction_id("sess1", "2026-08-17T00:00:00Z")

        with mock.patch("sys.argv", [
            "reflect.py", "--apply", source_id,
            "--target-path", str(target),
            "--draft-line-file", str(draft_line_file),
            "--before-content-file", str(before_content_file),
            "--corrections-file", str(filepath),
        ]):
            reflect.main()

        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "applied"
        assert output["revert_recorded"] is False
        assert output["revert_reason"] == "new_file_not_revertible"


# --- Test: weak_signals レーンは view-only 診断・昇格は evolve へ委譲（#117） ---

class TestWeakSignalsDelegation:
    """#117: reflect の weak_signals レーンは view-only 診断で、確認・昇格の主入口は evolve の
    「今日の修正確認」phase（daily_review）へ委譲する。--show-weak-signals 出力は昇格入口を指す
    promotion_hint を必ず含む（散文 SKILL.md だけに頼らず出力自体が委譲先を示す）。
    """

    def test_show_weak_signals_includes_promotion_hint(self, tmp_path, capsys):
        weak_file = tmp_path / "weak_signals.jsonl"
        weak_file.write_text("", encoding="utf-8")
        with mock.patch("sys.argv", [
            "reflect.py", "--show-weak-signals", "--weak-signals-file", str(weak_file),
        ]):
            reflect.main()
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "weak_signals"
        assert "promotion_hint" in output
        # 昇格の主入口が evolve であることを機械可読に示す。
        assert "evolve" in output["promotion_hint"]

    def test_show_weak_signals_context_branch_includes_promotion_hint(self, tmp_path, capsys):
        # --context（relevance_gate）経路でも同じ委譲 hint を出す（両経路で非対称にしない）。
        weak_file = tmp_path / "weak_signals.jsonl"
        weak_file.write_text("", encoding="utf-8")
        with mock.patch("sys.argv", [
            "reflect.py", "--show-weak-signals", "--weak-signals-file", str(weak_file),
            "--context", "認証ルーティングを直している",
        ]):
            reflect.main()
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "weak_signals"
        assert "relevance_gate" in output  # --context 経路である確認
        assert "promotion_hint" in output
        assert "evolve" in output["promotion_hint"]


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
        detected_at = _fresh_detected_at()
        sigs = [
            WeakSignal("llm_judge", {"source_path": "/a.jsonl", "line_no": 1,
                                     "text": "緑にして赤じゃなくて", "reason": "後置型"},
                       detected_at, "s1", "evolve-anything"),
            WeakSignal("rephrase", {"x": 1}, detected_at, "s2", "evolve-anything"),
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
        """--promote-weak が昇格後の corrections_human_allpj カウントを返す（#476-4 stale 表示の解消）。

        growth_report の promoted_today は対話前スナップショットで固定されるため、promote CLI
        が更新後カウントを返し assistant が最新値を表示できるようにする。
        キーは corrections_human_allpj（全PJ集計）— per-PJ の growth_report.corrections_human と
        区別するため #557 でリネーム。
        """
        ws, sigs = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--promote-weak", sigs[0].signal_key,
                                     "--weak-signals-file", str(ws),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "promoted_weak"
        # 昇格直後の corrections_human_allpj（全PJ集計・source=reflect_confirmed の human-source）が反映される
        assert out["corrections_human_allpj"] == 1
        # 旧キー（全PJ集計値）は削除済み — per-PJ の growth_report.corrections_human と混同防止 (#557)
        assert "corrections_human" not in out

    def test_promote_weak_dry_run_human_count_unchanged(self, tmp_path, capsys):
        """dry_run では corrections に書かないので corrections_human_allpj は変動しない（#476-4）。"""
        ws, sigs = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--promote-weak", sigs[0].signal_key,
                                     "--dry-run",
                                     "--weak-signals-file", str(ws),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True
        assert out["corrections_human_allpj"] == 0
        # 旧キー（全PJ集計値）は削除済み — per-PJ の growth_report.corrections_human と混同防止 (#557)
        assert "corrections_human" not in out

    def test_promote_weak_surfaces_skip_reason_for_ttl_expired(self, tmp_path, capsys):
        """#326: TTL 超・expired フラグ未設定の signal_key を --promote-weak に渡すと、従来は
        promoted=0 のみで理由不明の silent failure だった。CLI 出力の skipped で理由が分かる。
        """
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals

        ws = tmp_path / "weak_signals.jsonl"
        corr = tmp_path / "corrections.jsonl"
        old = WeakSignal(
            "rephrase", {"line_no": 1},
            (datetime.now(timezone.utc) - timedelta(days=46)).isoformat(),
            "s1", "evolve-anything",
        )
        append_signals([old], path=ws)
        with mock.patch("sys.argv", ["reflect", "--promote-weak", old.signal_key,
                                     "--weak-signals-file", str(ws),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["promoted"] == 0
        assert out["requested"] == 1
        assert out["promoted_keys"] == []
        assert out["skipped"] == [{"signal_key": old.signal_key, "reason": "expired"}]


# --- Test: --project-dir が cwd/env より優先して project_path を決める（#400 cwd-leak 修理） ---

class TestProjectDirFlag:
    """単一 cwd から他 PJ の project_dir を渡すバッチ経路（fleet propose 等）で、
    実行元 PJ の cwd/env が対象 PJ の project_path に混入しないことを保証する。
    優先順位: --project-dir（明示引数） > env CLAUDE_PROJECT_DIR > cwd。
    """

    def _seed_ws(self, tmp_path):
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals
        ws = tmp_path / "weak_signals.jsonl"
        detected_at = _fresh_detected_at()
        sig = WeakSignal(
            "llm_judge",
            {"source_path": "/a.jsonl", "line_no": 1, "text": "project-dir 経路のテスト", "reason": "後置型"},
            detected_at, "s1", "evolve-anything",
        )
        append_signals([sig], path=ws)
        return ws, sig

    def _promote(self, tmp_path, argv_extra):
        ws, sig = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", [
            "reflect", "--promote-weak", sig.signal_key,
            "--weak-signals-file", str(ws),
            "--corrections-file", str(corr),
            *argv_extra,
        ]):
            reflect.main()
        return [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_project_dir_overrides_cwd(self, tmp_path, monkeypatch):
        """cwd=PJ-A のまま --project-dir PJ-B を渡すと、昇格レコードは PJ-B に帰属する。"""
        pj_a = tmp_path / "pj-a"
        pj_b = tmp_path / "pj-b"
        pj_a.mkdir()
        pj_b.mkdir()
        monkeypatch.chdir(pj_a)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

        recs = self._promote(tmp_path, ["--project-dir", str(pj_b)])
        assert len(recs) == 1
        assert recs[0]["project_path"] == "pj-b"
        assert recs[0]["project_path"] != "pj-a"

    def test_project_dir_overrides_env_var(self, tmp_path, monkeypatch):
        """env CLAUDE_PROJECT_DIR が別 PJ を指していても --project-dir が優先される。"""
        pj_env = tmp_path / "pj-env"
        pj_explicit = tmp_path / "pj-explicit"
        pj_env.mkdir()
        pj_explicit.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(pj_env))

        recs = self._promote(tmp_path, ["--project-dir", str(pj_explicit)])
        assert recs[0]["project_path"] == "pj-explicit"

    def test_env_var_still_used_when_project_dir_omitted(self, tmp_path, monkeypatch):
        """--project-dir 省略時は従来どおり env CLAUDE_PROJECT_DIR にフォールバックする（後方互換）。"""
        pj_env = tmp_path / "pj-env2"
        pj_env.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(pj_env))

        recs = self._promote(tmp_path, [])
        assert recs[0]["project_path"] == "pj-env2"


# --- Test: --promote-weak が idiom を confirmed 化する閉ループ（#463 配線漏れ修正） ---

class TestPromoteWeakConfirmsIdiom:
    SLUG = "evolve-anything"

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
        detected_at = _fresh_detected_at()
        sig = WeakSignal("llm_judge", prov, detected_at, "s1", self.SLUG)
        append_signals([sig], path=ws)
        it = cs_store.CorrectionIdiom(
            idiom=text, provenance=prov, detected_at=detected_at, pj_slug=self.SLUG,
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

    def test_promote_weak_partial_failure_only_confirms_succeeded_idiom(self, tmp_path, capsys):
        """#412 round2 [Must]B: 昇格に失敗した key（expired）の idiom まで confirmed 化しない。

        旧実装は resolve_idiom_keys_for_signals に要求 key 全件（成功/失敗問わず）を渡して
        いたため、expired で昇格に失敗した key の idiom まで confirmed になり、将来の
        idiom_autopromote（ADR-047）の発火ゲートを誤って開いてしまう。
        """
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        import correction_semantic.store as cs_store
        ok_text = "成功する方のidiom"
        expired_text = "失敗する方のidiom"
        ws, idioms, ok_sig, _ok_it = self._seed(tmp_path, line_no=1, text=ok_text)

        # 2件目（expired）を同じストアに追加で seed する。
        from weak_signals.store import WeakSignal, append_signals
        expired_prov = self._prov(2, expired_text)
        expired_sig = WeakSignal(
            "llm_judge", expired_prov,
            (datetime.now(timezone.utc) - timedelta(days=46)).isoformat(),
            "s2", self.SLUG,
        )
        append_signals([expired_sig], path=ws)
        expired_it = cs_store.CorrectionIdiom(
            idiom=expired_text, provenance=expired_prov,
            detected_at=(datetime.now(timezone.utc) - timedelta(days=46)).isoformat(),
            pj_slug=self.SLUG,
        )
        cs_store.append_idioms([expired_it], path=idioms)

        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", [
            "reflect", "--promote-weak", f"{ok_sig.signal_key},{expired_sig.signal_key}",
            "--weak-signals-file", str(ws), "--idioms-file", str(idioms),
            "--corrections-file", str(corr),
        ]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["promoted"] == 1
        assert out["promoted_keys"] == [ok_sig.signal_key]

        confirmed = cs_store.read_confirmed_idiom_texts(self.SLUG, idioms)
        assert confirmed == {ok_text}
        assert expired_text not in confirmed

    def test_closed_loop_autopromote_fires_after_confirm(self, tmp_path, capsys, monkeypatch):
        """閉ループ E2E: --promote-weak で confirmed 化 → 同テキストの新規 signal を autopromote が昇格。

        本テストは confirm→autopromote の閉ループ配線を検証するのが目的で、#379 Step 1 の
        凍結対象外の関心事。凍結中は autopromote が no-op になるため既定で凍結を解除する。
        """
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals
        import correction_semantic.store as cs_store
        from correction_semantic import idiom_autopromote as iap
        import shrink_freeze
        monkeypatch.setattr(shrink_freeze, "SHRINK_FREEZE_ACTIVE", False)
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
        detected_at = _fresh_detected_at()
        append_signals([WeakSignal("llm_judge", prov99, detected_at,
                                   "s1", self.SLUG)], path=ws)
        it99 = cs_store.CorrectionIdiom(
            idiom=text, provenance=prov99, detected_at=detected_at, pj_slug=self.SLUG,
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
            detected_at="2026-06-10T00:00:00+00:00", pj_slug="evolve-anything",
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
        assert cs_store.read_confirmed_idiom_texts("evolve-anything", idioms) == set()
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


# --- Test: --show-weak-signals に --context 関連度ゲートを配線（#565） ---

class TestWeakSignalRelevanceGate:
    """FinAcumen 流の関連度ゲート（#565）が reflect --show-weak-signals に効いている。

    --context（現在の文脈）を渡すと、語彙が重なる過去経験だけが unpromoted（提案根拠）に
    残り、無関係な経験は suppressed に分離され関連度スコア付きで提示される。
    --context 無し（後方互換）なら従来通り全件提示で suppressed フィールドは付かない。
    """

    def _seed_two(self, tmp_path):
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals
        ws = tmp_path / "weak_signals.jsonl"
        detected_at = _fresh_detected_at()
        sigs = [
            WeakSignal("llm_judge", {"source_path": "/a.jsonl", "line_no": 1,
                                     "text": "認証ルーティングの設定を確認", "reason": "r"},
                       detected_at, "s1", "evolve-anything"),
            WeakSignal("llm_judge", {"source_path": "/a.jsonl", "line_no": 2,
                                     "text": "チョコレートケーキのレシピ", "reason": "r"},
                       detected_at, "s2", "evolve-anything"),
        ]
        append_signals(sigs, path=ws)
        return ws

    def test_context_gates_unrelated_into_suppressed(self, tmp_path, capsys):
        ws = self._seed_two(tmp_path)
        with mock.patch("sys.argv", ["reflect", "--show-weak-signals",
                                     "--context", "認証ルーティングの設定を直したい",
                                     "--weak-signals-file", str(ws)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "weak_signals"
        # 関連する経験だけが提案根拠（unpromoted=kept）に残る
        kept_texts = [c["provenance"]["text"] for c in out["unpromoted"]]
        assert "認証ルーティングの設定を確認" in kept_texts
        assert "チョコレートケーキのレシピ" not in kept_texts
        # 無関係な経験は黙って消さず suppressed に分離して理由を残す
        sup_texts = [c["provenance"]["text"] for c in out["suppressed"]]
        assert "チョコレートケーキのレシピ" in sup_texts
        assert "suppressed_reason" in out["suppressed"][0]
        # 各候補に関連度スコアが付く（observability）
        assert "relevance_score" in out["unpromoted"][0]
        assert out["relevance_gate"]["gate_applied"] is True
        assert out["relevance_gate"]["kept"] == 1
        assert out["relevance_gate"]["suppressed"] == 1

    def test_context_threshold_overridable(self, tmp_path, capsys):
        ws = self._seed_two(tmp_path)
        # 極端に高い閾値なら関連経験も suppressed に落ちる
        with mock.patch("sys.argv", ["reflect", "--show-weak-signals",
                                     "--context", "認証ルーティングの設定を直したい",
                                     "--relevance-threshold", "0.99",
                                     "--weak-signals-file", str(ws)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["count"] == 0
        assert out["relevance_gate"]["threshold"] == 0.99

    def test_no_context_keeps_backward_compat(self, tmp_path, capsys):
        ws = self._seed_two(tmp_path)
        with mock.patch("sys.argv", ["reflect", "--show-weak-signals",
                                     "--weak-signals-file", str(ws)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        # --context 無しは従来通り全件・suppressed/relevance_gate フィールド無し
        assert out["count"] == 2
        assert "suppressed" not in out
        assert "relevance_gate" not in out


# --- Test: --reject-weak / --promote-weak の既読化配線（#409 SessionStart 改善案提示） ---

class TestWeakReviewRecording:
    """SessionStart の改善案提示（daily.proposal_digest）が既読ストア
    （correction_review_seen.jsonl）を見て再提示を止められるよう、--promote-weak /
    --reject-weak の両方が daily_review.record_reviewed を呼ぶことを検証する。
    """

    def _seed_ws(self, tmp_path):
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals
        ws = tmp_path / "weak_signals.jsonl"
        detected_at = _fresh_detected_at()
        sig = WeakSignal(
            "llm_judge",
            {"source_path": "/a.jsonl", "line_no": 1, "text": "既読化のテスト", "reason": "r"},
            detected_at, "s1", "evolve-anything",
        )
        append_signals([sig], path=ws)
        return ws, sig

    def test_promote_weak_records_reviewed_as_promoted(self, tmp_path, capsys):
        ws, sig = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--promote-weak", sig.signal_key,
                                     "--weak-signals-file", str(ws),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        json.loads(capsys.readouterr().out)  # 既存フォーマットが壊れていないことも確認

        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from correction_semantic.daily_review import read_reviewed_keys
        assert sig.signal_key in read_reviewed_keys()

    def test_promote_weak_dry_run_does_not_record_reviewed(self, tmp_path, capsys):
        ws, sig = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--promote-weak", sig.signal_key,
                                     "--dry-run",
                                     "--weak-signals-file", str(ws),
                                     "--corrections-file", str(corr)]):
            reflect.main()
        capsys.readouterr()

        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from correction_semantic.daily_review import read_reviewed_keys
        assert sig.signal_key not in read_reviewed_keys()

    def test_reject_weak_records_reviewed_as_rejected(self, tmp_path, capsys):
        with mock.patch("sys.argv", ["reflect", "--reject-weak", "k1,k2", "--pj", "myproj"]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "rejected_weak"
        assert out["pj_slug"] == "myproj"
        assert out["written"] == 2

        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from correction_semantic.daily_review import read_reviewed_keys
        seen = read_reviewed_keys()
        assert "k1" in seen
        assert "k2" in seen

    def test_reject_weak_dry_run_writes_nothing(self, tmp_path, capsys):
        with mock.patch("sys.argv", ["reflect", "--reject-weak", "k1", "--pj", "myproj",
                                     "--dry-run"]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True

        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from correction_semantic.daily_review import read_reviewed_keys
        assert "k1" not in read_reviewed_keys()

    def test_reject_weak_defaults_pj_to_resolved_slug(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        with mock.patch("sys.argv", ["reflect", "--reject-weak", "k1"]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "rejected_weak"
        assert out["pj_slug"]  # 空でない（resolve_pj_slug が basename 等を返す）

    # --- #541 D-2: --already-reflected-weak（既読化のみ・promote しない） ---

    def test_already_reflected_weak_records_reviewed_without_promoting(self, tmp_path, capsys):
        """#541 Must2 決着: 「既に反映済み」は record_reviewed(decision="already_reflected")
        のみで、--promote-weak のように corrections.jsonl へ reflect_status="promoted" の
        レコードを新規作成しない（在庫レーンへバグが引っ越すのを防ぐ）。
        """
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--already-reflected-weak", "k1,k2",
                                     "--pj", "myproj", "--corrections-file", str(corr)]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "already_reflected_weak"
        assert out["pj_slug"] == "myproj"
        assert out["written"] == 2

        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from correction_semantic.daily_review import read_reviewed_keys
        seen = read_reviewed_keys()
        assert "k1" in seen
        assert "k2" in seen

        # corrections.jsonl には何も書かれない（promote を呼んでいない）
        assert not corr.exists() or corr.read_text(encoding="utf-8").strip() == ""

    def test_already_reflected_weak_dry_run_writes_nothing(self, tmp_path, capsys):
        with mock.patch("sys.argv", ["reflect", "--already-reflected-weak", "k1",
                                     "--pj", "myproj", "--dry-run"]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["dry_run"] is True

        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from correction_semantic.daily_review import read_reviewed_keys
        assert "k1" not in read_reviewed_keys()

    def test_already_reflected_weak_defaults_pj_to_resolved_slug(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        with mock.patch("sys.argv", ["reflect", "--already-reflected-weak", "k1"]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "already_reflected_weak"
        assert out["pj_slug"]

    def test_already_reflected_weak_decision_value_is_already_reflected(self, tmp_path, capsys):
        """既読レコードの decision フィールドが厳密に "already_reflected" であること
        （"promoted"/"rejected"/"deferred" のいずれとも混同しない・#541 計測要件の前提）。
        """
        with mock.patch("sys.argv", ["reflect", "--already-reflected-weak", "k1", "--pj", "myproj"]):
            reflect.main()
        capsys.readouterr()

        import rl_common
        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        seen_path = Path(rl_common.resolve_data_dir(env)) / "correction_review_seen.jsonl"
        lines = [json.loads(l) for l in seen_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        recs = [r for r in lines if r.get("key") == "k1"]
        assert recs
        assert recs[-1]["decision"] == "already_reflected"

    def test_promote_weak_and_already_reflected_weak_are_mutually_exclusive(self, tmp_path, capsys):
        """#541 codex M1: --promote-weak と --already-reflected-weak を同時指定すると、
        分岐順（--promote-weak が先に判定される）で promote が勝ち、corrections.jsonl に
        reflect_status="promoted" のレコードが作られてしまう（「既に反映済みは promote を
        呼ばない」という設計の中核を破る）。argparse レベルで排他にし、SystemExit で拒否する。
        """
        corr = tmp_path / "corrections.jsonl"
        with mock.patch("sys.argv", ["reflect", "--promote-weak", "k1",
                                     "--already-reflected-weak", "k1",
                                     "--pj", "myproj", "--corrections-file", str(corr)]):
            with pytest.raises(SystemExit):
                reflect.main()
        # corrections.jsonl には何も書かれない（拒否されたので promote 側の副作用が発生しない）。
        assert not corr.exists() or corr.read_text(encoding="utf-8").strip() == ""

    def test_reject_weak_and_already_reflected_weak_are_mutually_exclusive(self, tmp_path, capsys):
        """同様に --reject-weak / --already-reflected-weak も排他（3値択一の decision を
        同時に2つ指定させない）。"""
        with mock.patch("sys.argv", ["reflect", "--reject-weak", "k1",
                                     "--already-reflected-weak", "k1", "--pj", "myproj"]):
            with pytest.raises(SystemExit):
                reflect.main()

    def test_promote_weak_and_reject_weak_are_mutually_exclusive(self, tmp_path, capsys):
        """既存の2フラグ同士も排他にする（#541 M1 是正で3値とも同一グループに揃える）。"""
        with mock.patch("sys.argv", ["reflect", "--promote-weak", "k1",
                                     "--reject-weak", "k1", "--pj", "myproj"]):
            with pytest.raises(SystemExit):
                reflect.main()

    # --- #412 [Must]5: promote 失敗時に既読化しない ---

    def test_promote_weak_partial_failure_only_records_succeeded_key(self, tmp_path, capsys):
        """requested 2件中 1件が expired で失敗した場合、成功した key だけ既読化される。

        旧実装は record_reviewed に args.promote_weak が要求した keys（全件）をそのまま渡して
        いたため、promoted=0 でも「はい」を押したこと自体が既読化され、次回セッションでも
        digest から永久に外れる silent failure だった（#412 [Must]5）。
        """
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals
        ws = tmp_path / "weak_signals.jsonl"
        corr = tmp_path / "corrections.jsonl"
        ok = WeakSignal(
            "llm_judge", {"source_path": "/a.jsonl", "line_no": 1, "text": "成功する方", "reason": "r"},
            _fresh_detected_at(), "s1", "evolve-anything",
        )
        expired = WeakSignal(
            "rephrase", {"line_no": 2},
            (datetime.now(timezone.utc) - timedelta(days=46)).isoformat(),
            "s2", "evolve-anything",
        )
        append_signals([ok, expired], path=ws)

        with mock.patch("sys.argv", [
            "reflect", "--promote-weak", f"{ok.signal_key},{expired.signal_key}",
            "--weak-signals-file", str(ws), "--corrections-file", str(corr),
        ]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["promoted"] == 1
        assert out["promoted_keys"] == [ok.signal_key]
        assert out["skipped"] == [{"signal_key": expired.signal_key, "reason": "expired"}]

        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from correction_semantic.daily_review import read_reviewed_keys
        seen = read_reviewed_keys()
        assert ok.signal_key in seen
        assert expired.signal_key not in seen  # 失敗した key は既読化されない

    def test_promote_weak_all_failed_writes_nothing_to_seen_store(self, tmp_path, capsys):
        """requested 全件が失敗（not_found）した場合、既読ストアに1行も書かれない。"""
        with mock.patch("sys.argv", [
            "reflect", "--promote-weak", "does-not-exist-key",
            "--weak-signals-file", str(tmp_path / "weak_signals.jsonl"),
            "--corrections-file", str(tmp_path / "corrections.jsonl"),
        ]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["promoted"] == 0
        assert out["promoted_keys"] == []

        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from correction_semantic.daily_review import read_reviewed_keys
        assert read_reviewed_keys() == set()


# --- Test: --project-path が global 提案の origin PJ 帰属を正しくする（#412 [Must]4） ---

class TestProjectPathFlag:
    """global レーンの改善案は複数 PJ のキーを束ねるため、単一 cwd（現在 PJ）から
    ``--promote-weak`` を叩くと従来は全 correction の project_path が現在 PJ に固定され、
    他PJ由来の signal が誤って現在PJの実績として記録された。``--project-path`` は
    ``--project-dir`` と独立に、``promote_signals(project_path=...)`` にだけ渡す絶対パスを
    指定する。
    """

    def _seed_ws(self, tmp_path):
        sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
        from weak_signals.store import WeakSignal, append_signals
        ws = tmp_path / "weak_signals.jsonl"
        sig = WeakSignal(
            "llm_judge",
            {"source_path": "/other-pj.jsonl", "line_no": 1, "text": "他PJ由来の指摘", "reason": "r"},
            _fresh_detected_at(), "s1", "other-pj",
        )
        append_signals([sig], path=ws)
        return ws, sig

    def test_project_path_overrides_attributed_project_path(self, tmp_path, monkeypatch, capsys):
        ws, sig = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        current_pj = tmp_path / "current-pj"
        current_pj.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(current_pj))
        other_pj_path = str(tmp_path / "other-pj-abs")

        with mock.patch("sys.argv", [
            "reflect", "--promote-weak", sig.signal_key,
            "--weak-signals-file", str(ws), "--corrections-file", str(corr),
            "--project-path", other_pj_path, "--pj", "other-pj",
        ]):
            reflect.main()
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "promoted_weak"
        assert out["promoted"] == 1

        recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(recs) == 1
        # promote_signals は project_path を worktree 安全 slug に正規化する（#593・既存契約）。
        # ここでの回帰確認は「どちらの PJ に帰属したか」— 明示した other_pj_path 由来であり、
        # 現在 PJ（current_pj）ではないこと。
        assert recs[0]["project_path"] == Path(other_pj_path).name
        assert recs[0]["project_path"] != current_pj.name

    def test_project_path_omitted_falls_back_to_current_project(self, tmp_path, monkeypatch, capsys):
        """--project-path 省略時は従来どおり現在 PJ（CLAUDE_PROJECT_DIR）に帰属する（後方互換）。"""
        ws, sig = self._seed_ws(tmp_path)
        corr = tmp_path / "corrections.jsonl"
        current_pj = tmp_path / "current-pj"
        current_pj.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(current_pj))

        with mock.patch("sys.argv", [
            "reflect", "--promote-weak", sig.signal_key,
            "--weak-signals-file", str(ws), "--corrections-file", str(corr),
        ]):
            reflect.main()
        json.loads(capsys.readouterr().out)

        recs = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert recs[0]["project_path"] == current_pj.name
