"""fleet recall（PJ 横断 memory recall）のユニットテスト。

決定論 engine なので LLM mock は不要（no-llm-in-tests guard 対象外）。

カバー:
- enumerate_memory_dirs() — memory dir 存在ベースの列挙（plugin 有効性で絞らない）
- parse_fact_file() — frontmatter パース + 不正時の body フォールバック
- recall() — keyword prefilter + TF/boost rank + dedup + 上位 limit
- format_hits() — 人間可読 / JSON
"""

import json
import sys
from pathlib import Path

_plugin_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from fleet import main  # noqa: E402
from fleet.project_loader import enumerate_memory_dirs  # noqa: E402
from fleet.recall import (  # noqa: E402
    RecallHit,
    format_hits,
    parse_fact_file,
    recall,
)


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class TestEnumerateMemoryDirs:
    """memory dir 存在ベースの列挙。plugin 有効性で絞らない。"""

    def test_memory_dir_を持つ全PJを列挙(self, tmp_path):
        root = tmp_path / "projects"
        _write(root / "-Users-foo-a" / "memory" / "x.md", "# a")
        _write(root / "-Users-foo-b" / "memory" / "y.md", "# b")
        # memory dir が無い PJ は除外
        (root / "-Users-foo-c").mkdir(parents=True)
        result = enumerate_memory_dirs(projects_root=root)
        displays = sorted(m.pj_display for m in result)
        assert displays == ["Users-foo-a", "Users-foo-b"]

    def test_md_が無いmemory_dirは除外(self, tmp_path):
        root = tmp_path / "projects"
        (root / "-Users-foo-a" / "memory").mkdir(parents=True)  # 空
        _write(root / "-Users-foo-b" / "memory" / "y.md", "# b")
        result = enumerate_memory_dirs(projects_root=root)
        assert [m.pj_display for m in result] == ["Users-foo-b"]

    def test_root非存在は空リスト(self, tmp_path):
        assert enumerate_memory_dirs(projects_root=tmp_path / "nope") == []

    def test_dotdir_と_symlink_は除外(self, tmp_path):
        root = tmp_path / "projects"
        _write(root / ".hidden" / "memory" / "x.md", "# x")
        _write(root / "-Users-foo-a" / "memory" / "y.md", "# y")
        real = tmp_path / "real"
        _write(real / "memory" / "z.md", "# z")
        (root / "-link").symlink_to(real, target_is_directory=True)
        result = enumerate_memory_dirs(projects_root=root)
        assert [m.pj_display for m in result] == ["Users-foo-a"]

    def test_ソート安定(self, tmp_path):
        root = tmp_path / "projects"
        for name in ["-c", "-a", "-b"]:
            _write(root / name / "memory" / "m.md", "# m")
        result = enumerate_memory_dirs(projects_root=root)
        assert [m.pj_display for m in result] == ["a", "b", "c"]


class TestParseFactFile:
    """frontmatter パース + 不正時の body フォールバック。"""

    def test_正常_frontmatter(self, tmp_path):
        f = _write(
            tmp_path / "f.md",
            "---\nname: my-fact\ndescription: about caching\n---\n\nbody text here\n",
        )
        fact = parse_fact_file(f)
        assert fact.name == "my-fact"
        assert fact.description == "about caching"
        assert "body text here" in fact.body
        assert fact.parse_ok is True

    def test_frontmatter欠落は本文フォールバック(self, tmp_path):
        f = _write(tmp_path / "g.md", "just plain markdown no frontmatter\n")
        fact = parse_fact_file(f)
        assert fact.parse_ok is False
        assert "just plain markdown" in fact.body
        # name は filename から
        assert fact.name == "g"

    def test_frontmatter不正YAMLは本文フォールバック(self, tmp_path):
        f = _write(tmp_path / "h.md", "---\nname: [unterminated\n---\nbody\n")
        fact = parse_fact_file(f)
        assert fact.parse_ok is False
        assert "body" in fact.body

    def test_空ファイル(self, tmp_path):
        f = _write(tmp_path / "e.md", "")
        fact = parse_fact_file(f)
        assert fact.body == ""


class TestRecall:
    """keyword prefilter + TF/boost rank + dedup + limit。"""

    def _corpus(self, tmp_path):
        root = tmp_path / "projects"
        _write(
            root / "-pj-a" / "memory" / "caching.md",
            "---\nname: caching-fix\ndescription: duckdb cache invalidation\n---\n"
            "duckdb checkpoint flushes on close. cache cache cache.\n",
        )
        _write(
            root / "-pj-b" / "memory" / "auth.md",
            "---\nname: auth-note\ndescription: github account routing\n---\n"
            "use todoroki-godai for push.\n",
        )
        return root

    def test_keyword一致のみ返す(self, tmp_path):
        root = self._corpus(tmp_path)
        hits = recall("duckdb", projects_root=root)
        assert len(hits) == 1
        assert hits[0].pj_display == "pj-a"

    def test_ヒットゼロ(self, tmp_path):
        root = self._corpus(tmp_path)
        assert recall("nonexistentterm", projects_root=root) == []

    def test_TFが高い方が上位_決定論(self, tmp_path):
        root = self._corpus(tmp_path)
        # "cache" は caching.md に複数回 → 上位
        hits = recall("cache", projects_root=root)
        assert hits[0].pj_display == "pj-a"
        # 同じ query で順位不変（決定論）
        again = recall("cache", projects_root=root)
        assert [h.file_path for h in hits] == [h.file_path for h in again]

    def test_description一致はブースト(self, tmp_path):
        root = self._corpus(tmp_path)
        hits = recall("routing", projects_root=root)
        assert hits[0].pj_display == "pj-b"

    def test_limitで切り詰め(self, tmp_path):
        root = tmp_path / "projects"
        for i in range(5):
            _write(
                root / f"-pj-{i}" / "memory" / "m.md",
                f"---\nname: n{i}\ndescription: common\n---\ncommon term\n",
            )
        hits = recall("common", projects_root=root, limit=3)
        assert len(hits) == 3

    def test_index行とfact本体のdedup(self, tmp_path):
        root = tmp_path / "projects"
        # MEMORY.md (index) と本体が同じ語にヒット → 同一PJで本体優先、index は is_index
        _write(
            root / "-pj-x" / "memory" / "MEMORY.md",
            "# Index\n- [widget fix](widget.md) — widget summary\n",
        )
        _write(
            root / "-pj-x" / "memory" / "widget.md",
            "---\nname: widget-fix\ndescription: widget detail\n---\nwidget body widget\n",
        )
        hits = recall("widget", projects_root=root)
        # 本体ファイルが index 行より上位
        assert hits[0].file_path.name == "widget.md"
        assert hits[0].is_index is False

    def test_壊れたfrontmatterでも本文で拾える(self, tmp_path):
        root = tmp_path / "projects"
        _write(
            root / "-pj-z" / "memory" / "broken.md",
            "---\nname: [bad yaml\n---\nrecoverable keyword here\n",
        )
        hits = recall("recoverable", projects_root=root)
        assert len(hits) == 1


class TestFormatHits:
    def _hit(self):
        return RecallHit(
            pj_display="pj-a",
            file_path=Path("/x/memory/caching.md"),
            score=2.5,
            snippet="duckdb checkpoint",
            is_index=False,
        )

    def test_json出力は構造化(self):
        out = format_hits([self._hit()], as_json=True)
        data = json.loads(out)
        assert data[0]["pj_display"] == "pj-a"
        assert data[0]["score"] == 2.5
        assert data[0]["is_index"] is False

    def test_人間可読出力にPJ名とsnippet(self):
        out = format_hits([self._hit()], as_json=False)
        assert "pj-a" in out
        assert "duckdb checkpoint" in out

    def test_空ヒットのメッセージ(self):
        out = format_hits([], as_json=False)
        assert out.strip() != ""


class TestRecallCLI:
    """evolve-fleet recall サブコマンドの dispatch。"""

    def _corpus(self, tmp_path):
        root = tmp_path / "projects"
        (root / "-pj-a" / "memory").mkdir(parents=True)
        (root / "-pj-a" / "memory" / "c.md").write_text(
            "---\nname: caching\ndescription: duckdb cache\n---\ncache body\n",
            encoding="utf-8",
        )
        return root

    def test_recall_dispatch_json(self, tmp_path, capsys):
        root = self._corpus(tmp_path)
        rc = main(["recall", "cache", "--root", str(root), "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data[0]["pj_display"] == "pj-a"

    def test_recall_dispatch_human_limit(self, tmp_path, capsys):
        root = self._corpus(tmp_path)
        rc = main(["recall", "cache", "--root", str(root), "--limit", "5"])
        assert rc == 0
        assert "pj-a" in capsys.readouterr().out

    def test_recall_ヒットなし(self, tmp_path, capsys):
        root = self._corpus(tmp_path)
        rc = main(["recall", "zzznotfound", "--root", str(root)])
        assert rc == 0
        assert "該当する memory" in capsys.readouterr().out
