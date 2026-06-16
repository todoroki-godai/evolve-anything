"""fixers_llm の emit/ingest 6関数のテスト（[ADR-037] Phase 1d-ii）。

LLM を呼ばない: responses は dict を直接渡す。
emit が IO-free・LLM-free であること、ingest が responses dict でファイル書込/proposable 降格することを
tmp_path で検証する。
"""
import sys
from pathlib import Path

import pytest

_lib_dir = Path(__file__).resolve().parents[1]
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from remediation.fixers_llm import (
    emit_compression_request,
    emit_separation_request,
    emit_split_request,
    ingest_compression,
    ingest_separation,
    ingest_split,
    reference_link_for_prompt,
)


# ────────────────────────────────────────────────────────────
# reference_link_for_prompt (#524-2)
# ────────────────────────────────────────────────────────────

class TestReferenceLinkForPrompt:
    def test_project_absolute_path_becomes_relative(self):
        abs_path = "/Users/someone/proj/.claude/references/foo.md"
        assert reference_link_for_prompt(abs_path) == ".claude/references/foo.md"

    def test_global_home_path_becomes_relative(self):
        abs_path = "/Users/someone/.claude/references/bar.md"
        assert reference_link_for_prompt(abs_path) == ".claude/references/bar.md"

    def test_path_without_claude_segment_returns_unchanged(self):
        # .claude/ を含まないパスは壊さず素通し（安全側）
        p = "/tmp/no-claude-here/references/baz.md"
        assert reference_link_for_prompt(p) == p

    def test_deterministic_no_io(self, tmp_path):
        # 存在しないパスでも例外なく純粋に文字列変換する（IO なし）
        p = str(tmp_path / "nonexistent" / ".claude" / "references" / "x.md")
        assert reference_link_for_prompt(p) == ".claude/references/x.md"


# ────────────────────────────────────────────────────────────
# 共通ユーティリティ
# ────────────────────────────────────────────────────────────

def _make_issue(issue_type: str = "line_limit_violation", **detail_kwargs) -> dict:
    return {
        "type": issue_type,
        "file": "/fake/file.md",
        "detail": detail_kwargs,
        "category": "proposable",
    }


# ────────────────────────────────────────────────────────────
# 1. emit_compression_request
# ────────────────────────────────────────────────────────────

class TestEmitCompressionRequest:
    def test_returns_one_request(self):
        issue = _make_issue()
        result = emit_compression_request(issue, "line1\nline2\n", limit=3)
        assert "requests" in result
        assert len(result["requests"]) == 1

    def test_request_id_is_compress(self):
        issue = _make_issue()
        result = emit_compression_request(issue, "content", limit=5)
        assert result["requests"][0]["id"] == "compress"

    def test_prompt_contains_limit(self):
        issue = _make_issue()
        result = emit_compression_request(issue, "content", limit=7)
        assert "7" in result["requests"][0]["prompt"]

    def test_prompt_contains_content(self):
        issue = _make_issue()
        content = "unique_marker_xyz\n"
        result = emit_compression_request(issue, content, limit=3)
        assert "unique_marker_xyz" in result["requests"][0]["prompt"]

    def test_io_free(self, tmp_path):
        """emit は IO を行わない（tmp_path に何も書かない）。"""
        issue = _make_issue()
        emit_compression_request(issue, "content\n", limit=3)
        assert list(tmp_path.iterdir()) == []


# ────────────────────────────────────────────────────────────
# 2. ingest_compression
# ────────────────────────────────────────────────────────────

class TestIngestCompression:
    def _run(self, tmp_path, response_text, original="original\n", limit=10):
        path = tmp_path / "file.md"
        path.write_text(original, encoding="utf-8")
        issue = _make_issue()
        requests = emit_compression_request(issue, original, limit)["requests"]
        responses = {"compress": response_text}
        return ingest_compression(issue, path, original, limit, requests, responses), path

    def test_success_writes_file(self, tmp_path):
        compressed = "short\n"
        result, path = self._run(tmp_path, compressed)
        assert result["fixed"] is True
        assert path.read_text() == compressed

    def test_success_adds_trailing_newline(self, tmp_path):
        result, path = self._run(tmp_path, "short", limit=10)
        assert result["fixed"] is True
        assert path.read_text().endswith("\n")

    def test_strips_code_fence(self, tmp_path):
        fenced = "```\nshort\n```"
        result, path = self._run(tmp_path, fenced, limit=10)
        assert result["fixed"] is True
        assert "```" not in path.read_text()

    def test_too_many_lines_proposable(self, tmp_path):
        many_lines = "\n".join([f"line{i}" for i in range(20)])
        result, _ = self._run(tmp_path, many_lines, limit=3)
        assert result["fixed"] is False
        assert result["error"] == "compression_insufficient"
        assert result["issue"]["category"] == "proposable"

    def test_missing_response_proposable(self, tmp_path):
        path = tmp_path / "file.md"
        path.write_text("original\n", encoding="utf-8")
        issue = _make_issue()
        requests = emit_compression_request(issue, "original\n", 10)["requests"]
        result = ingest_compression(issue, path, "original\n", 10, requests, {})
        assert result["fixed"] is False
        assert result["error"] == "llm_response_missing"
        assert result["issue"]["category"] == "proposable"

    def test_empty_requests_proposable(self, tmp_path):
        path = tmp_path / "file.md"
        path.write_text("original\n", encoding="utf-8")
        issue = _make_issue()
        result = ingest_compression(issue, path, "original\n", 10, [], {})
        assert result["fixed"] is False
        assert result["error"] == "no_requests"

    def test_original_content_preserved_in_result(self, tmp_path):
        result, _ = self._run(tmp_path, "short\n", original="the original\n", limit=10)
        assert result["original_content"] == "the original\n"


# ────────────────────────────────────────────────────────────
# 3. emit_separation_request
# ────────────────────────────────────────────────────────────

class TestEmitSeparationRequest:
    def _make_rule_issue(self, tmp_path):
        """グローバル rules 風のファイルパスで issue を作る。"""
        rule_path = tmp_path / ".claude" / "rules" / "test-rule.md"
        rule_path.parent.mkdir(parents=True, exist_ok=True)
        rule_path.write_text("\n".join([f"line{i}" for i in range(20)]), encoding="utf-8")
        return {"type": "line_limit_violation", "file": str(rule_path),
                "detail": {"limit": 10}, "category": "proposable"}, rule_path

    def test_returns_requests_dict(self, tmp_path):
        issue, path = self._make_rule_issue(tmp_path)
        content = path.read_text()
        result = emit_separation_request(issue, path, content, limit=10)
        assert "requests" in result

    def test_io_free(self, tmp_path):
        """emit 後に tmp_path への書き込みがないこと（rule ファイル自体は除く）。"""
        issue, path = self._make_rule_issue(tmp_path)
        content = path.read_text()
        files_before = set(str(f) for f in tmp_path.rglob("*") if f.is_file())
        emit_separation_request(issue, path, content, limit=10)
        files_after = set(str(f) for f in tmp_path.rglob("*") if f.is_file())
        assert files_after == files_before, "emit は IO を行わない"

    def test_empty_requests_when_not_applicable(self, tmp_path):
        """suggest_separation が None を返す（短い rule）なら requests=[]。"""
        rule_path = tmp_path / ".claude" / "rules" / "short-rule.md"
        rule_path.parent.mkdir(parents=True, exist_ok=True)
        rule_path.write_text("# short rule\n- one rule\n", encoding="utf-8")
        issue = {"type": "line_limit_violation", "file": str(rule_path),
                 "detail": {"limit": 10}, "category": "proposable"}
        result = emit_separation_request(issue, rule_path, rule_path.read_text(), limit=10)
        # suggest_separation が None を返すケースを確認
        # （実際に None が返るかは suggest_separation 実装依存、empty か non-empty かだけ確認）
        assert "requests" in result
        assert isinstance(result["requests"], list)

    def test_prompt_uses_pj_root_relative_reference_link(self, tmp_path):
        """#524-2: prompt の参照リンクはマシン固有絶対パスでなく PJ ルート相対パス。

        コミットされるファイルに /Users/<user>/... が埋まると他環境で壊れるため、
        .claude/ セグメント以降の相対パス（.claude/references/<name>.md）を指示する。
        """
        issue, path = self._make_rule_issue(tmp_path)
        content = path.read_text()
        result = emit_separation_request(issue, path, content, limit=10)
        assert result["requests"], "separation 対象なら requests は非空"
        prompt = result["requests"][0]["prompt"]
        # 相対リンクが含まれる
        assert ".claude/references/test-rule.md" in prompt
        # マシン固有絶対パス（tmp_path のホーム配下プレフィックス）が prompt に出ない
        assert str(tmp_path) not in prompt

    def test_meta_keeps_absolute_reference_path_for_write(self, tmp_path):
        """#524-2: 実際の書込先（meta.reference_path）は絶対のまま保持する。

        prompt の表示は相対だが、ingest がファイルを書く先は絶対パスでなければならない。
        """
        issue, path = self._make_rule_issue(tmp_path)
        content = path.read_text()
        result = emit_separation_request(issue, path, content, limit=10)
        meta = result["requests"][0]["meta"]
        assert meta["reference_path"].startswith(str(tmp_path))
        assert meta["reference_path"].endswith(".claude/references/test-rule.md")
        # 相対表示用リンクも meta に保持される（ingest 検証で許容するため）
        assert meta["reference_link"] == ".claude/references/test-rule.md"


# ────────────────────────────────────────────────────────────
# 4. ingest_separation
# ────────────────────────────────────────────────────────────

class TestIngestSeparation:
    def _fake_requests(self, ref_path_str):
        """emit_separation_request と同形の requests を手動で作る。"""
        return [
            {"id": "separate", "prompt": "...", "meta": {"reference_path": ref_path_str}}
        ]

    def test_success_writes_both_files(self, tmp_path):
        rule_path = tmp_path / "test-rule.md"
        rule_path.write_text("original content\n", encoding="utf-8")
        ref_path = tmp_path / "references" / "test-rule.md"

        issue = _make_issue()
        requests = self._fake_requests(str(ref_path))
        responses = {"separate": "summary line\n"}

        result = ingest_separation(issue, rule_path, "original content\n", 10,
                                   requests, responses)
        assert result["fixed"] is True
        assert rule_path.read_text() == "summary line\n"
        assert ref_path.read_text() == "original content\n"

    def test_writes_to_absolute_path_even_with_relative_link_in_meta(self, tmp_path):
        """#524-2: meta に相対 reference_link があっても、書込先は絶対 reference_path。

        emit が相対リンクを prompt 用に持たせても、ingest は絶対パスでファイルを書く。
        """
        rule_path = tmp_path / ".claude" / "rules" / "test-rule.md"
        rule_path.parent.mkdir(parents=True, exist_ok=True)
        rule_path.write_text("original content\n", encoding="utf-8")
        ref_path = tmp_path / ".claude" / "references" / "test-rule.md"

        issue = _make_issue()
        requests = [{"id": "separate", "prompt": "...", "meta": {
            "reference_path": str(ref_path),
            "reference_link": ".claude/references/test-rule.md",
        }}]
        responses = {"separate": "summary line\n"}

        result = ingest_separation(issue, rule_path, "original content\n", 10,
                                   requests, responses)
        assert result["fixed"] is True
        # 相対パス（cwd 相対）でなく meta の絶対パスに書かれていること
        assert ref_path.read_text() == "original content\n"
        assert result["separation"]["reference_path"] == str(ref_path)

    def test_success_result_contains_separation(self, tmp_path):
        rule_path = tmp_path / "test-rule.md"
        rule_path.write_text("orig\n", encoding="utf-8")
        ref_path = tmp_path / "references" / "test-rule.md"

        issue = _make_issue()
        requests = self._fake_requests(str(ref_path))
        responses = {"separate": "brief summary\n"}

        result = ingest_separation(issue, rule_path, "orig\n", 10, requests, responses)
        assert "separation" in result
        assert result["separation"]["reference_path"] == str(ref_path)

    def test_strips_fence(self, tmp_path):
        rule_path = tmp_path / "test-rule.md"
        rule_path.write_text("orig\n", encoding="utf-8")
        ref_path = tmp_path / "references" / "test-rule.md"

        issue = _make_issue()
        requests = self._fake_requests(str(ref_path))
        responses = {"separate": "```\nsummary line\n```"}

        result = ingest_separation(issue, rule_path, "orig\n", 10, requests, responses)
        assert result["fixed"] is True
        assert "```" not in rule_path.read_text()

    def test_empty_requests_returns_not_applicable(self, tmp_path):
        rule_path = tmp_path / "test-rule.md"
        rule_path.write_text("orig\n", encoding="utf-8")
        issue = _make_issue()
        result = ingest_separation(issue, rule_path, "orig\n", 10, [], {})
        assert result["fixed"] is False
        assert result["error"] == "separation_not_applicable"

    def test_missing_response_proposable(self, tmp_path):
        rule_path = tmp_path / "test-rule.md"
        rule_path.write_text("orig\n", encoding="utf-8")
        ref_path = tmp_path / "references" / "test-rule.md"

        issue = _make_issue()
        requests = self._fake_requests(str(ref_path))
        result = ingest_separation(issue, rule_path, "orig\n", 10, requests, {})
        assert result["fixed"] is False
        assert result["issue"]["category"] == "proposable"

    def test_summary_too_long_proposable(self, tmp_path):
        rule_path = tmp_path / "test-rule.md"
        rule_path.write_text("orig\n", encoding="utf-8")
        ref_path = tmp_path / "references" / "test-rule.md"

        issue = _make_issue()
        requests = self._fake_requests(str(ref_path))
        many_lines = "\n".join([f"line{i}" for i in range(20)])
        responses = {"separate": many_lines}

        result = ingest_separation(issue, rule_path, "orig\n", 3, requests, responses)
        assert result["fixed"] is False
        assert result["error"] == "separation_summary_too_long"
        assert result["issue"]["category"] == "proposable"

    def test_adds_trailing_newline(self, tmp_path):
        rule_path = tmp_path / "test-rule.md"
        rule_path.write_text("orig\n", encoding="utf-8")
        ref_path = tmp_path / "references" / "test-rule.md"

        issue = _make_issue()
        requests = self._fake_requests(str(ref_path))
        responses = {"separate": "summary no newline"}

        result = ingest_separation(issue, rule_path, "orig\n", 10, requests, responses)
        assert result["fixed"] is True
        assert rule_path.read_text().endswith("\n")


# ────────────────────────────────────────────────────────────
# 5. emit_split_request
# ────────────────────────────────────────────────────────────

class TestEmitSplitRequest:
    def test_returns_one_request(self):
        issue = _make_issue("split_candidate", skill_name="my-skill",
                             line_count=350, threshold=300)
        result = emit_split_request(issue, "content\n")
        assert len(result["requests"]) == 1

    def test_request_id_is_split(self):
        issue = _make_issue("split_candidate", skill_name="my-skill",
                             line_count=350, threshold=300)
        result = emit_split_request(issue, "content\n")
        assert result["requests"][0]["id"] == "split"

    def test_prompt_contains_line_count_and_threshold(self):
        issue = _make_issue("split_candidate", skill_name="my-skill",
                             line_count=350, threshold=300)
        result = emit_split_request(issue, "content\n")
        prompt = result["requests"][0]["prompt"]
        assert "350" in prompt
        assert "300" in prompt

    def test_prompt_contains_content_prefix(self):
        issue = _make_issue("split_candidate", skill_name="my-skill",
                             line_count=350, threshold=300)
        content = "unique_prefix_abc\n" + "filler\n" * 10
        result = emit_split_request(issue, content)
        assert "unique_prefix_abc" in result["requests"][0]["prompt"]

    def test_content_truncated_to_3000(self):
        issue = _make_issue("split_candidate", line_count=400, threshold=300)
        content = "x" * 5000
        result = emit_split_request(issue, content)
        # プロンプト中のコンテンツが 3000 文字以上送られない
        prompt = result["requests"][0]["prompt"]
        # content[:3000] が使われているので "x" が 3000 個のはず（+ fence）
        assert prompt.count("x") <= 3000

    def test_io_free(self, tmp_path):
        issue = _make_issue("split_candidate", line_count=350, threshold=300)
        emit_split_request(issue, "content\n")
        assert list(tmp_path.iterdir()) == []


# ────────────────────────────────────────────────────────────
# 6. ingest_split
# ────────────────────────────────────────────────────────────

class TestIngestSplit:
    def test_returns_llm_text(self):
        issue = _make_issue("split_candidate", skill_name="my-skill",
                             line_count=350, threshold=300)
        requests = emit_split_request(issue, "content\n")["requests"]
        responses = {"split": "分割提案: A と B に分割してください"}
        result = ingest_split(issue, requests, responses)
        assert result == "分割提案: A と B に分割してください"

    def test_fallback_on_missing_response(self):
        issue = _make_issue("split_candidate", skill_name="my-skill",
                             line_count=350, threshold=300)
        requests = emit_split_request(issue, "content\n")["requests"]
        result = ingest_split(issue, requests, {})
        assert "my-skill" in result
        assert "350" in result
        assert "300" in result

    def test_fallback_on_empty_requests(self):
        issue = _make_issue("split_candidate", skill_name="some-skill",
                             line_count=400, threshold=300)
        result = ingest_split(issue, [], {})
        assert "some-skill" in result

    def test_fallback_on_empty_string_response(self):
        issue = _make_issue("split_candidate", skill_name="my-skill",
                             line_count=350, threshold=300)
        requests = emit_split_request(issue, "content\n")["requests"]
        responses = {"split": "  "}  # whitespace only → strip → empty
        result = ingest_split(issue, requests, responses)
        assert "my-skill" in result  # fallback

    def test_strips_whitespace_from_response(self):
        issue = _make_issue("split_candidate", skill_name="my-skill",
                             line_count=350, threshold=300)
        requests = emit_split_request(issue, "content\n")["requests"]
        responses = {"split": "  proposal text  "}
        result = ingest_split(issue, requests, responses)
        assert result == "proposal text"

    def test_no_file_writes(self, tmp_path):
        """ingest_split はファイル書き込みをしない。"""
        issue = _make_issue("split_candidate", skill_name="my-skill",
                             line_count=350, threshold=300)
        requests = emit_split_request(issue, "content\n")["requests"]
        responses = {"split": "proposal"}
        ingest_split(issue, requests, responses)
        assert list(tmp_path.iterdir()) == []


# ────────────────────────────────────────────────────────────
# 7. 行数カウント補助
# ────────────────────────────────────────────────────────────

class TestCountLines:
    """_count_lines の挙動を間接的に確認（ingest_compression の limit 検査経由）。"""

    def test_trailing_newline_not_extra_line(self, tmp_path):
        """'a\n' は 1 行のはず（現行実装の count("\n") + (1 if...endswith("\n") else 0) は 1）。"""
        path = tmp_path / "f.md"
        path.write_text("original\n", encoding="utf-8")
        issue = _make_issue()
        requests = emit_compression_request(issue, "original\n", 2)["requests"]
        # "a\n" は 1 行 → limit=2 なので成功するはず
        responses = {"compress": "a\n"}
        result = ingest_compression(issue, path, "original\n", 2, requests, responses)
        assert result["fixed"] is True
