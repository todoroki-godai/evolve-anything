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
    _STALE_PENALTY,
    _SUPERSEDED_PENALTY,
    Fact,
    RecallHit,
    _score,
    format_hits,
    parse_fact_file,
    recall,
    reinforce_recall_hits,
)
from memory_temporal import parse_memory_temporal  # noqa: E402


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


class TestParseFactLinks:
    """parse_fact_file が本文中の [[name]] リンクを抽出する（#11）。"""

    def test_本文のlinkを抽出(self, tmp_path):
        f = _write(
            tmp_path / "f.md",
            "---\nname: my-fact\ndescription: d\n---\n"
            "see [[other-fact]] and [[third-fact]] for detail.\n",
        )
        fact = parse_fact_file(f)
        assert fact.links == ["other-fact", "third-fact"]

    def test_linkなしは空リスト(self, tmp_path):
        f = _write(tmp_path / "f.md", "---\nname: n\ndescription: d\n---\nno links here\n")
        fact = parse_fact_file(f)
        assert fact.links == []

    def test_link重複は一意化し順序保持(self, tmp_path):
        f = _write(
            tmp_path / "f.md",
            "---\nname: n\ndescription: d\n---\n[[a]] then [[b]] then [[a]] again\n",
        )
        fact = parse_fact_file(f)
        assert fact.links == ["a", "b"]

    def test_frontmatter内のlinkは無視_本文のみ(self, tmp_path):
        # links は body から抽出されるので、frontmatter 内に [[x]] があっても拾わない
        f = _write(
            tmp_path / "f.md",
            "---\nname: n\ndescription: refs [[infm]]\n---\n[[inbody]]\n",
        )
        fact = parse_fact_file(f)
        assert fact.links == ["inbody"]


class TestRecallLinkExpansion:
    """recall が hit fact の [[link]] 先を 1-hop 展開して併記する（#11）。"""

    def _corpus(self, tmp_path):
        root = tmp_path / "projects"
        _write(
            root / "-pj-a" / "memory" / "main.md",
            "---\nname: main-fix\ndescription: about widgets\n---\n"
            "widget core. see [[helper]] for more.\n",
        )
        _write(
            root / "-pj-a" / "memory" / "helper.md",
            "---\nname: helper\ndescription: helper detail\n---\n"
            "helper body explaining the supporting bits.\n",
        )
        return root

    def test_hit_factのlink先が併記される(self, tmp_path):
        root = self._corpus(tmp_path)
        hits = recall("widget", projects_root=root)
        assert len(hits) == 1
        assert hits[0].file_path.name == "main.md"
        linked_names = [lk.file_path.name for lk in hits[0].linked]
        assert linked_names == ["helper.md"]

    def test_link先はスコア対象でない_直接hitに混ざらない(self, tmp_path):
        root = self._corpus(tmp_path)
        # "widget" は helper.md 本体にはマッチしない。直接 hit は main.md のみ
        hits = recall("widget", projects_root=root)
        direct_names = [h.file_path.name for h in hits]
        assert direct_names == ["main.md"]
        # helper.md は linked として添付（直接 hit ではない）
        assert hits[0].linked[0].is_linked is True

    def test_直接hitでもあるlink先はlinkedに重複させない(self, tmp_path):
        root = tmp_path / "projects"
        # 両方が query にマッチし、main が helper を指す
        _write(
            root / "-pj-a" / "memory" / "main.md",
            "---\nname: main\ndescription: common\n---\ncommon term see [[helper]]\n",
        )
        _write(
            root / "-pj-a" / "memory" / "helper.md",
            "---\nname: helper\ndescription: common\n---\ncommon term helper\n",
        )
        hits = recall("common", projects_root=root)
        direct = {h.file_path.name for h in hits}
        assert direct == {"main.md", "helper.md"}
        # helper は既に直接 hit なので、main の linked には現れない
        main_hit = next(h for h in hits if h.file_path.name == "main.md")
        assert "helper.md" not in [lk.file_path.name for lk in main_hit.linked]

    def test_dangling_linkは無視しエラーにならない(self, tmp_path):
        root = tmp_path / "projects"
        _write(
            root / "-pj-a" / "memory" / "main.md",
            "---\nname: main\ndescription: about widgets\n---\nwidget see [[ghost]]\n",
        )
        hits = recall("widget", projects_root=root)
        assert len(hits) == 1
        assert hits[0].linked == []

    def test_link解決は同一PJ内のみ(self, tmp_path):
        root = tmp_path / "projects"
        _write(
            root / "-pj-a" / "memory" / "main.md",
            "---\nname: main\ndescription: about widgets\n---\nwidget see [[shared]]\n",
        )
        # 別 PJ に同名 name の fact があっても解決しない
        _write(
            root / "-pj-b" / "memory" / "shared.md",
            "---\nname: shared\ndescription: other pj\n---\nshared in pj-b\n",
        )
        hits = recall("widget", projects_root=root)
        assert hits[0].linked == []

    def test_link先のnameでもファイル名stemでも解決(self, tmp_path):
        root = tmp_path / "projects"
        _write(
            root / "-pj-a" / "memory" / "main.md",
            "---\nname: main\ndescription: about widgets\n---\nwidget see [[helper-fix]]\n",
        )
        # frontmatter name が helper-fix、ファイル名は別
        _write(
            root / "-pj-a" / "memory" / "helper_file.md",
            "---\nname: helper-fix\ndescription: d\n---\nhelper body\n",
        )
        hits = recall("widget", projects_root=root)
        assert [lk.file_path.name for lk in hits[0].linked] == ["helper_file.md"]


class TestParseFactTemporalValidity:
    """parse_fact_file が temporal frontmatter から is_stale / is_superseded を埋める（#74）。

    grounding metadata（valid_from / superseded_at / decay_days）は memory_temporal に
    既存だが recall._score が消費していなかった配線漏れを塞ぐ。parse_ok のときのみ計算し、
    frontmatter 無し（MEMORY.md index 等）は後方互換で両方 False のまま。
    """

    def test_superseded_at_過去はis_superseded_True(self, tmp_path):
        # 実 writer（write_temporal_metadata）は isoformat 文字列を quoted で書く。
        # YAML は unquoted ISO を datetime に自動変換し is_superseded が解釈不能になるため、
        # 実データ同様に quote する。
        f = _write(
            tmp_path / "s.md",
            "---\nname: old-fact\ndescription: d\nsuperseded_at: '2020-01-01T00:00:00+00:00'\n---\n"
            "replaced body\n",
        )
        fact = parse_fact_file(f)
        assert fact.is_superseded is True

    def test_decay超過はis_stale_True(self, tmp_path):
        # valid_from=2020 + decay_days=1 → now() 基準で確実に超過
        f = _write(
            tmp_path / "st.md",
            "---\nname: aged-fact\ndescription: d\nvalid_from: '2020-01-01T00:00:00+00:00'\ndecay_days: 1\n---\n"
            "aged body\n",
        )
        fact = parse_fact_file(f)
        assert fact.is_stale is True
        assert fact.is_superseded is False

    def test_新鮮なfactは両方False(self, tmp_path):
        # decay_days 無し → 期限なし → is_stale False
        f = _write(
            tmp_path / "fresh.md",
            "---\nname: fresh-fact\ndescription: d\nvalid_from: '2020-01-01T00:00:00+00:00'\n---\n"
            "fresh body\n",
        )
        fact = parse_fact_file(f)
        assert fact.is_stale is False
        assert fact.is_superseded is False

    def test_temporal_frontmatter無しは両方False_後方互換(self, tmp_path):
        f = _write(
            tmp_path / "plain.md",
            "---\nname: plain\ndescription: d\n---\nplain body\n",
        )
        fact = parse_fact_file(f)
        assert fact.is_stale is False
        assert fact.is_superseded is False

    def test_frontmatter無しファイルは両方False(self, tmp_path):
        # parse_ok=False（frontmatter 無し）は temporal 計算せず False のまま
        f = _write(tmp_path / "index.md", "# Index\n- entry\n")
        fact = parse_fact_file(f)
        assert fact.parse_ok is False
        assert fact.is_stale is False
        assert fact.is_superseded is False


class TestScoreTemporalPenalty:
    """_score が validity を消費し stale/superseded を降格する（ハード除外しない・#74）。

    RaMem(iii): validity で降格はするが結果には残す（フォールバック保持）。
    """

    def _fact(self, *, is_stale=False, is_superseded=False):
        return Fact(
            file_path=Path("/x/memory/f.md"),
            name="f",
            description="",
            body="duckdb duckdb duckdb",
            parse_ok=True,
            is_stale=is_stale,
            is_superseded=is_superseded,
        )

    def test_fresh_factはペナルティ無し(self):
        terms = ["duckdb"]
        baseline = _score(self._fact(), terms)
        assert baseline == 3.0  # tf=3, boost なし

    def test_stale_factは降格するが正のまま(self):
        terms = ["duckdb"]
        baseline = _score(self._fact(), terms)
        stale = _score(self._fact(is_stale=True), terms)
        assert stale == baseline * _STALE_PENALTY
        assert stale > 0

    def test_superseded_factは強く降格するが正のまま(self):
        terms = ["duckdb"]
        baseline = _score(self._fact(), terms)
        sup = _score(self._fact(is_superseded=True), terms)
        assert sup == baseline * _SUPERSEDED_PENALTY
        assert sup > 0

    def test_順位ロック_fresh_gt_stale_gt_0(self):
        terms = ["duckdb"]
        fresh = _score(self._fact(), terms)
        stale = _score(self._fact(is_stale=True), terms)
        assert fresh > stale > 0

    def test_superseded優先_stackしない(self):
        # 両方 True → superseded penalty のみ適用（stack しない）
        terms = ["duckdb"]
        baseline = _score(self._fact(), terms)
        both = _score(self._fact(is_stale=True, is_superseded=True), terms)
        assert both == baseline * _SUPERSEDED_PENALTY

    def test_スコア0は降格適用しない(self):
        # query 不一致で score==0 のときは penalty を掛けない（0 のまま）
        terms = ["nomatch"]
        fact = Fact(
            file_path=Path("/x/memory/f.md"),
            name="f",
            description="",
            body="duckdb",
            parse_ok=True,
            is_stale=True,
        )
        assert _score(fact, terms) == 0.0


class TestFormatHits:
    def _hit(self, linked=None):
        return RecallHit(
            pj_display="pj-a",
            file_path=Path("/x/memory/caching.md"),
            score=2.5,
            snippet="duckdb checkpoint",
            is_index=False,
            linked=linked or [],
        )

    def _linked_hit(self):
        return RecallHit(
            pj_display="pj-a",
            file_path=Path("/x/memory/related.md"),
            score=0.0,
            snippet="related context",
            is_index=False,
            is_linked=True,
        )

    def test_json出力は構造化(self):
        out = format_hits([self._hit()], as_json=True)
        data = json.loads(out)
        assert data[0]["pj_display"] == "pj-a"
        assert data[0]["score"] == 2.5
        assert data[0]["is_index"] is False
        assert data[0]["linked"] == []

    def test_json出力にlinkedフィールド(self):
        out = format_hits([self._hit(linked=[self._linked_hit()])], as_json=True)
        data = json.loads(out)
        assert len(data[0]["linked"]) == 1
        assert data[0]["linked"][0]["file_path"].endswith("related.md")

    def test_人間可読出力にPJ名とsnippet(self):
        out = format_hits([self._hit()], as_json=False)
        assert "pj-a" in out
        assert "duckdb checkpoint" in out

    def test_人間可読出力にlinked行(self):
        out = format_hits([self._hit(linked=[self._linked_hit()])], as_json=False)
        assert "↳ linked" in out
        assert "related.md" in out

    def test_空ヒットのメッセージ(self):
        out = format_hits([], as_json=False)
        assert out.strip() != ""


class TestReinforceRecallHits:
    """recall ヒット時に対象 memory ファイルを reinforce する本番配線（#18）。"""

    def test_直接hitファイルがreinforceされる(self, tmp_path):
        f = _write(
            tmp_path / "memory" / "hot.md",
            "---\nname: hot\ndescription: about widgets\nupdate_count: 0\n---\nwidget body\n",
        )
        hit = RecallHit(
            pj_display="pj-a", file_path=f, score=2.0,
            snippet="widget", is_index=False,
        )
        before = parse_memory_temporal(f)
        assert before["last_reinforced_at"] is None
        reinforce_recall_hits([hit])
        after = parse_memory_temporal(f)
        assert after["update_count"] == 1

    def test_last_reinforced_atが書かれる(self, tmp_path):
        f = _write(
            tmp_path / "memory" / "hot.md",
            "---\nname: hot\ndescription: d\nupdate_count: 0\n---\nbody\n",
        )
        reinforce_recall_hits([
            RecallHit(pj_display="p", file_path=f, score=1.0, snippet="", is_index=False)
        ])
        text = f.read_text(encoding="utf-8")
        assert "last_reinforced_at:" in text

    def test_linked先もreinforceされる(self, tmp_path):
        main_f = _write(
            tmp_path / "memory" / "main.md",
            "---\nname: main\ndescription: d\nupdate_count: 0\n---\nbody\n",
        )
        linked_f = _write(
            tmp_path / "memory" / "linked.md",
            "---\nname: linked\ndescription: d\nupdate_count: 0\n---\nbody\n",
        )
        hit = RecallHit(
            pj_display="p", file_path=main_f, score=1.0, snippet="", is_index=False,
            linked=[RecallHit(pj_display="p", file_path=linked_f, score=0.0,
                              snippet="", is_index=False, is_linked=True)],
        )
        reinforce_recall_hits([hit])
        assert parse_memory_temporal(linked_f)["update_count"] == 1

    def test_frontmatterなしファイルはno_op(self, tmp_path):
        f = _write(tmp_path / "memory" / "legacy.md", "# no frontmatter\nbody\n")
        reinforce_recall_hits([
            RecallHit(pj_display="p", file_path=f, score=1.0, snippet="", is_index=False)
        ])
        # no-op: ファイルは変化しない、例外も出ない
        assert f.read_text(encoding="utf-8") == "# no frontmatter\nbody\n"

    def test_空ヒットは例外なし(self):
        reinforce_recall_hits([])  # 何もしない


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
