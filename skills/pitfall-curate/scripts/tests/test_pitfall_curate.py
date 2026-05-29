"""pitfall_curate の決定論コアのテスト。

LLM は一切呼ばない（分類判断は agent が行い、ここでは parse/dedup/distill/sync
の純粋関数のみを検証する）。正常系 E2E を中心に、副作用（idempotency・
stale 検出）も確認する。
"""
import sys
from pathlib import Path

import pytest

_scripts = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_scripts))
_plugin_root = _scripts.parent.parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

import core as pc
import parse


SAMPLE = """# Pitfalls

## Active Pitfalls

### CDK deploy パラメータ不足
- **Status**: Active
- **Last-seen**: 2026-03-10
- **Root-cause**: action — CDK deploy のパラメータ指定漏れ
- **Pre-flight対応**: Yes
- **Avoidance-count**: 3
- **Transferability**: universal
- **Generality**: 5

### CDK デプロイ時のパラメータ指定ミス
- **Status**: Active
- **Last-seen**: 2026-03-09
- **Root-cause**: action — CDK deploy パラメータ漏れで失敗
- **Pre-flight対応**: Yes
- **Avoidance-count**: 1
- **Transferability**: universal
- **Generality**: 4

### この特定 bucket だけの命名規則
- **Status**: Active
- **Last-seen**: 2026-03-08
- **Root-cause**: tool_use — bucket 名
- **Pre-flight対応**: No
- **Avoidance-count**: 0
- **Transferability**: instance
- **Generality**: 1

## Candidate Pitfalls

### まだ分類していない pitfall
- **Status**: Candidate
- **First-seen**: 2026-03-01
- **Root-cause**: planning — 未分類

## Graduated Pitfalls
"""


# 実 PJ（atlas-browser）で観測された有機的フォーマット。正準スキーマと違い:
# - セクション見出しが ## Active / ## New（## *** Pitfalls でない）
# - 番号付きタイトル ### N. xxx
# - メタは **Last-seen**: ... | **Pre-flight**: ...（- **field**: ブロックでない）
# - 内容フィールドは - **症状** / - **対策** / - **検出**（Root-cause 不在）
# - 日本語主体（ASCII キーワードは一部のみ）
REAL_FORMAT = """# atlas-browser: 既知の問題と対策

## Active

### 1. エラー時の新セッション作成 → リソース枯渇
**Last-seen**: 2026-02-18 | **Pre-flight**: ab-preflight.sh で自動検出

- **症状**: os error 35 が全コマンドで発生する
- **対策**: セッション名を変えない。close → 同名 open → リトライ（最大3回）
- **検出**: agent-browser session list で3個以上

### 5. CDP パイプ飽和で os error 35
**Last-seen**: 2026-02-23 | **Pre-flight**: ab-preflight.sh で自動検出

- **症状**: 5-6操作目で os error 35 が発生する（セッション数正常）
- **対策**: セッション名を変えずリトライ、snapshot の頻度を下げる

## New（未検証 — 再発で Active 昇格）

### 6. ボトムシートの中身が空白
**Last-seen**: 2026-02-20

- **症状**: ボトムシートを開いてもコンテンツが見えない
- **対策**: 負のY座標レイアウトバグ。maxHeight 未解決を疑う
"""


# 実PJ（sys-bots aws-deploy/pitfalls-infra.md）形式。3つ目のメタデータ形式:
# - エントリが ## N.（番号付き H2。ライフサイクルセクションでない）
# - メタが **Status**: X | **Last-seen**: Y | **Pre-flight**: Z（インラインパイプ）
# - ライフサイクルセクション見出しが存在せず、各エントリが Status を自前保持
SYS_BOTS_FORMAT = """# pitfalls: CDK/CloudFormation/IAM/OAuth

## 1. dev環境再作成後のボット消失

**Status**: Active | **Last-seen**: 2026-02-18 | **Pre-flight**: Yes

`just destroy-dev` 後に DynamoDB が再作成されデータ消失。

## 3. Google OAuth認証エラー

**Status**: Active | **Last-seen**: 2026-03-04 | **Pre-flight**: No

Secrets Manager のキー名は camelCase。
"""

# docs-platform 形式: 正準フォーマットだが中身は <!-- --> コメント内テンプレートのみ。
# コメント内の ### [タイトル] を phantom エントリとして拾ってはいけない。
DOCS_TEMPLATE = """# Pitfalls

## Active Pitfalls

<!-- 項目テンプレート:
### [タイトル]
- **Status**: Active
- **Root-cause**: [category] — [説明]
-->

_まだ記録がありません。_

## Candidate Pitfalls

_まだ記録がありません。_
"""


# --- parse -------------------------------------------------------------------

def test_parse_sections_and_fields():
    parsed = pc.parse_pitfalls(SAMPLE)
    assert len(parsed["active"]) == 3
    assert len(parsed["candidate"]) == 1
    assert len(parsed["graduated"]) == 0
    first = parsed["active"][0]
    assert first["title"] == "CDK deploy パラメータ不足"
    assert first["fields"]["Status"] == "Active"
    assert first["fields"]["Transferability"] == "universal"
    assert first["fields"]["Generality"] == "5"


def test_parse_real_format_fuzzy_sections():
    """## Active / ## New を正準セクションに fuzzy マッピングする。"""
    parsed = pc.parse_pitfalls(REAL_FORMAT)
    # ## Active 配下の2件は active
    active_titles = [p["title"] for p in parsed["active"]]
    assert "1. エラー時の新セッション作成 → リソース枯渇" in active_titles
    assert "5. CDP パイプ飽和で os error 35" in active_titles
    # ## New 配下は candidate（未検証 = candidate 相当）
    candidate_titles = [p["title"] for p in parsed["candidate"]]
    assert "6. ボトムシートの中身が空白" in candidate_titles
    # 内容フィールドはパースされる
    assert parsed["active"][0]["fields"]["症状"].startswith("os error 35")


def test_parse_numbered_h2_entries_with_inline_pipe():
    """## N. をエントリ認識し、インラインパイプ・メタdata をフィールド化する。"""
    parsed = pc.parse_pitfalls(SYS_BOTS_FORMAT)
    # ライフサイクルセクション見出しが無いので全エントリは既定 active に入る
    titles = [p["title"] for p in parsed["active"]]
    assert "1. dev環境再作成後のボット消失" in titles
    assert "3. Google OAuth認証エラー" in titles
    # インラインパイプがフィールドに展開される
    first = next(p for p in parsed["active"] if p["title"].startswith("1."))
    assert first["fields"]["Status"] == "Active"
    assert first["fields"]["Last-seen"] == "2026-02-18"
    assert first["fields"]["Pre-flight"] == "Yes"


def test_parse_skips_html_comment_entries():
    """<!-- --> 内の ### [タイトル] を phantom エントリとして拾わない。"""
    parsed = pc.parse_pitfalls(DOCS_TEMPLATE)
    all_titles = [p["title"] for s in parsed.values() for p in s]
    assert "[タイトル]" not in all_titles
    # 実エントリは無い（空ひな型）
    assert sum(len(s) for s in parsed.values()) == 0


# --- classification helpers --------------------------------------------------

def test_is_classified():
    parsed = pc.parse_pitfalls(SAMPLE)
    assert pc.is_classified(parsed["active"][0]) is True
    assert pc.is_classified(parsed["candidate"][0]) is False


def test_list_unclassified_returns_only_missing():
    parsed = pc.parse_pitfalls(SAMPLE)
    unclassified = pc.list_unclassified(parsed)
    titles = [u["title"] for u in unclassified]
    assert titles == ["まだ分類していない pitfall"]
    assert "Root-cause" in unclassified[0]["root_cause"] or unclassified[0]["root_cause"]


def test_set_classification_writes_fields():
    updated = pc.set_classification(
        SAMPLE, "まだ分類していない pitfall", "project", 3
    )
    parsed = pc.parse_pitfalls(updated)
    target = next(p for p in parsed["candidate"] if p["title"] == "まだ分類していない pitfall")
    assert target["fields"]["Transferability"] == "project"
    assert target["fields"]["Generality"] == "3"
    # 既存フィールドは保持される
    assert target["fields"]["Status"] == "Candidate"


def test_set_classification_rejects_bad_values():
    with pytest.raises(ValueError):
        pc.set_classification(SAMPLE, "まだ分類していない pitfall", "global", 3)
    with pytest.raises(ValueError):
        pc.set_classification(SAMPLE, "まだ分類していない pitfall", "project", 9)


# --- dedup -------------------------------------------------------------------

def test_find_similar_pairs_detects_duplicate():
    parsed = pc.parse_pitfalls(SAMPLE)
    pairs = pc.find_similar_pairs(parsed, threshold=0.3)
    # 2つの CDK パラメータ pitfall が検出される
    titles = {tuple(sorted([p["a"], p["b"]])) for p in pairs}
    assert (
        "CDK deploy パラメータ不足",
        "CDK デプロイ時のパラメータ指定ミス",
    ) in {tuple(sorted(t)) for t in titles}


def test_dedup_real_format_uses_body_when_no_root_cause():
    """Root-cause 不在の実フォーマットでも、本文（症状/対策）から重複を検出する。

    #1 と #5 はどちらも「os error 35 / セッション名 / リトライ」を含む同一事象。
    Root-cause フィールドが無くても本文 fallback + 日本語 bigram で検出できる。
    """
    # 日本語 bigram jaccard は英単語より低く出る（CLI デフォルトも 0.2）。
    # この2件は「関連あり・別root-cause」のソフトシグナル（実測 ~0.19）なので
    # recall 寄り閾値で候補に上がることを検証する。
    parsed = pc.parse_pitfalls(REAL_FORMAT)
    pairs = pc.find_similar_pairs(parsed, threshold=0.15)
    detected = {tuple(sorted([p["a"], p["b"]])) for p in pairs}
    target = tuple(sorted([
        "1. エラー時の新セッション作成 → リソース枯渇",
        "5. CDP パイプ飽和で os error 35",
    ]))
    assert target in detected, f"os error 35 の重複が未検出: {pairs}"


def test_mark_superseded_mutates_and_is_idempotent():
    old = "CDK デプロイ時のパラメータ指定ミス"
    new = "CDK deploy パラメータ不足"
    once = pc.mark_superseded(SAMPLE, old, new)
    parsed = pc.parse_pitfalls(once)
    target = next(p for p in parsed["active"] if p["title"] == old)
    assert target["fields"]["Superseded-by"] == new
    assert target["fields"]["Status"].startswith("Superseded")
    # 冪等: 2回目で内容が変わらない
    twice = pc.mark_superseded(once, old, new)
    assert twice == once


# --- distill -----------------------------------------------------------------

def test_select_distill_includes_mandatory():
    parsed = pc.parse_pitfalls(SAMPLE)
    result = pc.select_distill(parsed, top_n=2, mandatory_generality=4)
    # universal かつ generality>=4 は必須
    assert "CDK deploy パラメータ不足" in result["selected"]
    assert "CDK デプロイ時のパラメータ指定ミス" in result["selected"]
    # instance/generality1 は選ばれない
    assert "この特定 bucket だけの命名規則" not in result["selected"]


def test_select_distill_excludes_superseded():
    superseded = pc.mark_superseded(
        SAMPLE, "CDK デプロイ時のパラメータ指定ミス", "CDK deploy パラメータ不足"
    )
    parsed = pc.parse_pitfalls(superseded)
    result = pc.select_distill(parsed, top_n=5, mandatory_generality=4)
    assert "CDK デプロイ時のパラメータ指定ミス" not in result["selected"]


def test_render_distribution_lists_selected_titles():
    parsed = pc.parse_pitfalls(SAMPLE)
    result = pc.select_distill(parsed, top_n=2, mandatory_generality=4)
    md = pc.render_distribution(parsed, result["selected"])
    for title in result["selected"]:
        assert title in md


# --- seed / normalize --------------------------------------------------------

def test_seed_renders_canonical_empty_scaffold():
    seed = pc.render_seed()
    assert "## Active Pitfalls" in seed
    assert "## Candidate Pitfalls" in seed
    assert "## Graduated Pitfalls" in seed
    # テンプレートはコメント内なので phantom エントリにならない
    parsed = pc.parse_pitfalls(seed)
    assert sum(len(s) for s in parsed.values()) == 0


def test_normalize_sys_bots_to_canonical():
    out = pc.normalize(SYS_BOTS_FORMAT)
    # 正準セクション見出しが付与される
    assert "## Active Pitfalls" in out
    # 見出しが ### に揃う（## N. でなく）
    assert "### 1. dev環境再作成後のボット消失" in out
    # 見出しが ## N. のまま残っていない（改行アンカーで判定。### は ## を部分包含するため）
    assert "\n## 1. dev環境再作成後のボット消失" not in out
    # インラインパイプがバレットに展開される
    assert "- **Status**: Active" in out
    assert "- **Last-seen**: 2026-02-18" in out
    # 本文（散文）は保持される
    assert "DynamoDB" in out
    # 正準として再パースでき、フィールドが取れる
    reparsed = pc.parse_pitfalls(out)
    assert len(reparsed["active"]) == 2
    assert reparsed["active"][0]["fields"]["Status"] == "Active"


def test_normalize_is_idempotent():
    once = pc.normalize(SYS_BOTS_FORMAT)
    twice = pc.normalize(once)
    assert once == twice


def test_normalize_preserves_canonical_entries():
    out = pc.normalize(SAMPLE)
    before = pc.parse_pitfalls(SAMPLE)
    after = pc.parse_pitfalls(out)
    assert [p["title"] for p in after["active"]] == [p["title"] for p in before["active"]]
    # 分類フィールドが保持される
    assert after["active"][0]["fields"]["Transferability"] == "universal"
    assert after["active"][0]["fields"]["Generality"] == "5"


# 実 PJ（atlas-browser）にあった、説明的 H1 + プリアンブル散文（blockquote）。
# normalize がこれらを捨てるとユーザー記述のデータが消失する（ドッグフードで発見）。
PREAMBLE_FORMAT = """# atlas-browser: 既知の問題と対策

> **自動チェック**: `./scripts/ab-preflight.sh` で Pre-flight 項目を一括検証できる。
> **ライフサイクル**: New → Active → スクリプト化して削除。

---

## Active

### 1. エラー時の新セッション作成 → リソース枯渇
**Last-seen**: 2026-02-18 | **Pre-flight**: ab-preflight.sh で自動検出

- **症状**: os error 35 が全コマンドで発生する
"""


def test_normalize_preserves_h1_title():
    """正準でない説明的 H1 を汎用 '# Pitfalls' に置換して捨てない。"""
    out = pc.normalize(SYS_BOTS_FORMAT)
    assert out.startswith("# pitfalls: CDK/CloudFormation/IAM/OAuth")


def test_normalize_preserves_preamble_prose():
    """H1 と最初のセクション/エントリの間の散文（blockquote 等）を保持する。"""
    out = pc.normalize(PREAMBLE_FORMAT)
    assert "**自動チェック**" in out
    assert "ab-preflight.sh" in out
    assert "**ライフサイクル**" in out
    # H1 タイトルも元のまま
    assert out.startswith("# atlas-browser: 既知の問題と対策")
    # エントリも正準化される
    assert "### 1. エラー時の新セッション作成 → リソース枯渇" in out


def test_normalize_preamble_is_idempotent():
    """プリアンブル付きでも冪等（正準化後を再 normalize して不変）。"""
    once = pc.normalize(PREAMBLE_FORMAT)
    twice = pc.normalize(once)
    assert once == twice


# 実 PJ（atlas-browser）で観測: 番号付き `### N.` エントリと、番号なし `### サブ見出し`
# （`### 真の原因` 等。1エントリ内の小見出し）が混在する。パーサが後者もエントリ扱いすると
# 幽霊エントリが生まれる（ドッグフードで発見）。
NUMBERED_WITH_SUBSECTIONS = """# Pitfalls

## Active

### 1. ボトムシートが空白になる
**Last-seen**: 2026-05-29

- **症状**: 上部コンテンツが空白
- **対策**: maxHeight を高さ確定祖先へ移す

### 2. タイルクリックのタイムアウト
**Last-seen**: 2026-03-18

これは番号付きエントリの本文。

### 真の原因（実機でも起きる）
- パーセンテージ max-height が無効化される

### 検出方法
```bash
echo measure
```

### 対策（コード修正）
- maxHeight を移す
"""


def test_normalize_demotes_unnumbered_h3_subsections():
    """番号付きエントリがある文書では、番号なし ### はサブ見出し（####）へ降格する。"""
    out = pc.normalize(NUMBERED_WITH_SUBSECTIONS)
    # 番号付きの実エントリは ### のまま2件
    assert "### 1. ボトムシートが空白になる" in out
    assert "### 2. タイルクリックのタイムアウト" in out
    # 番号なしサブ見出しは #### へ降格（### エントリにならない）
    assert "#### 真の原因（実機でも起きる）" in out
    assert "#### 検出方法" in out
    assert "#### 対策（コード修正）" in out
    # 幽霊エントリが生まれていない: 実エントリは2件だけ
    reparsed = pc.parse_pitfalls(out)
    assert len(reparsed["active"]) == 2
    # サブ見出しの本文は保持される
    assert "パーセンテージ max-height が無効化される" in out


def test_normalize_subsection_demotion_is_idempotent():
    once = pc.normalize(NUMBERED_WITH_SUBSECTIONS)
    twice = pc.normalize(once)
    assert once == twice


def test_normalize_keeps_unnumbered_h3_as_entries_when_no_numbered():
    """番号付きエントリが無い正準文書では、番号なし ### はエントリのまま（降格しない）。"""
    out = pc.normalize(SAMPLE)
    reparsed = pc.parse_pitfalls(out)
    assert len(reparsed["active"]) == 3
    assert "#### " not in out


# 実 PJ（sys-bots aws-deploy/pitfalls.md）はエントリでなくインデックス/TOC（テーブル + リンク）。
# これに normalize をかけるとテーブルが全足切りされファイルが空 wipe される（ドッグフードで発見）。
INDEX_FORMAT = """# aws-deploy: 既知の問題と対策

> ライフサイクル: New → Active → 削除
> カテゴリ別詳細: `references/pitfalls-*.md`

## Active 項目（Pre-flight対応 Yes のみ抜粋）

| # | カテゴリ | 問題 | Pre-flight |
|---|---------|------|-----------|
| 1 | infra | destroy後のボット消失 | Yes |
| 5 | infra | スタック削除順序 | Yes |
| 15 | infra | KB直接削除でCFn失敗 | Yes |

## 詳細リファレンス

- [pitfalls-infra.md](pitfalls-infra.md) — CDK/IAM (#1,5,15)
- [pitfalls-rag.md](pitfalls-rag.md) — Bedrock KB (#2,7)
"""


def test_normalize_refuses_index_file():
    """エントリ0件だが実質コンテンツ（テーブル/リンク）がある index/TOC は wipe せず拒否する。"""
    with pytest.raises(ValueError):
        pc.normalize(INDEX_FORMAT)


def test_normalize_allows_empty_seed():
    """空ひな型（プレースホルダ+コメントのみ）は実質コンテンツが無いので拒否しない。"""
    out = pc.normalize(pc.render_seed())  # raise しなければ OK
    assert "## Active Pitfalls" in out


# --- check（lint: 書き換えずに正準形との差分状態を返す） -----------------------

def test_check_normalized_ok_on_canonical():
    """正準形（normalize 済み）は ok を返し diff は空。"""
    canonical = pc.normalize(SAMPLE)
    res = pc.check_normalized(canonical)
    assert res["state"] == "ok"
    assert res["diff"] == ""


def test_check_normalized_ok_on_seed():
    """正準ひな型を normalize したものは ok。"""
    res = pc.check_normalized(pc.normalize(pc.render_seed()))
    assert res["state"] == "ok"


def test_check_normalized_drift_reports_diff_without_mutating():
    """ドリフト（インラインパイプ・番号なし ### サブ見出し混在）は drift + diff を返す。"""
    res = pc.check_normalized(NUMBERED_WITH_SUBSECTIONS)
    assert res["state"] == "drift"
    assert res["diff"]  # 非空の unified diff
    # lint は提案を返すだけで入力を書き換えない（呼び出し側が承認時に normalize する）
    assert "#### " in res["diff"]  # サブ見出し降格の提案が diff に出る


def test_check_normalized_danger_on_index_does_not_raise():
    """index/TOC は danger を返す。ValueError を投げず lint として扱う（hook が握り潰さない）。"""
    res = pc.check_normalized(INDEX_FORMAT)
    assert res["state"] == "danger"
    assert res["reason"]  # 理由文がある
    assert res["diff"] == ""


def test_check_normalized_after_normalize_is_ok():
    """drift なファイルも一度 normalize すれば ok になる（収束）。"""
    once = pc.normalize(NUMBERED_WITH_SUBSECTIONS)
    assert pc.check_normalized(once)["state"] == "ok"


# --- check の CLI 終了コード契約（ok=0 / drift=1 / danger=2） -----------------

def _write(tmp_path, content):
    p = tmp_path / "pitfalls.md"
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_cli_check_exit_ok(tmp_path):
    import pitfall_curate as cli
    path = _write(tmp_path, pc.normalize(SAMPLE))
    assert cli.main(["normalize", "--pitfalls", path, "--check"]) == 0


def test_cli_check_exit_drift(tmp_path):
    import pitfall_curate as cli
    path = _write(tmp_path, NUMBERED_WITH_SUBSECTIONS)
    assert cli.main(["normalize", "--pitfalls", path, "--check"]) == 1


def test_cli_check_exit_danger(tmp_path):
    import pitfall_curate as cli
    path = _write(tmp_path, INDEX_FORMAT)
    assert cli.main(["normalize", "--pitfalls", path, "--check"]) == 2


def test_cli_check_does_not_write(tmp_path):
    """--check は in-place 変換しない（lint なので元ファイルを保つ）。"""
    import pitfall_curate as cli
    path = _write(tmp_path, NUMBERED_WITH_SUBSECTIONS)
    cli.main(["normalize", "--pitfalls", path, "--check"])
    assert Path(path).read_text(encoding="utf-8") == NUMBERED_WITH_SUBSECTIONS


# --- enable（管理対象に登録: install 後の「1コマンド」） ----------------------

def _enable_setup(tmp_path, content):
    """project_dir 配下に pitfalls.md を置きパスを返す。"""
    pf = tmp_path / ".claude" / "skills" / "x" / "references" / "pitfalls.md"
    pf.parent.mkdir(parents=True)
    pf.write_text(content, encoding="utf-8")
    return str(pf)


def test_enable_registers_canonical_file(tmp_path):
    import pitfall_curate as cli
    import pitfall_registry as reg
    path = _enable_setup(tmp_path, pc.normalize(SAMPLE))
    assert cli.main(
        ["enable", "--pitfalls", path, "--project-dir", str(tmp_path)]
    ) == 0
    assert reg.is_managed(tmp_path, path) is True


def test_enable_drift_registers_with_warning(tmp_path):
    import pitfall_curate as cli
    import pitfall_registry as reg
    path = _enable_setup(tmp_path, NUMBERED_WITH_SUBSECTIONS)
    # drift でも登録はする（exit 0）。normalize を促すだけ。
    assert cli.main(
        ["enable", "--pitfalls", path, "--project-dir", str(tmp_path)]
    ) == 0
    assert reg.is_managed(tmp_path, path) is True


def test_enable_refuses_index_file(tmp_path):
    import pitfall_curate as cli
    import pitfall_registry as reg
    path = _enable_setup(tmp_path, INDEX_FORMAT)
    # index/TOC は pitfalls エントリファイルではないので登録を拒否する（exit 2）
    assert cli.main(
        ["enable", "--pitfalls", path, "--project-dir", str(tmp_path)]
    ) == 2
    assert reg.is_managed(tmp_path, path) is False


def test_enable_is_idempotent(tmp_path):
    import pitfall_curate as cli
    import pitfall_registry as reg
    path = _enable_setup(tmp_path, pc.normalize(SAMPLE))
    cli.main(["enable", "--pitfalls", path, "--project-dir", str(tmp_path)])
    assert cli.main(
        ["enable", "--pitfalls", path, "--project-dir", str(tmp_path)]
    ) == 0
    assert reg.load_managed(tmp_path).count(
        ".claude/skills/x/references/pitfalls.md"
    ) == 1


def test_disable_removes_from_registry(tmp_path):
    import pitfall_curate as cli
    import pitfall_registry as reg
    path = _enable_setup(tmp_path, pc.normalize(SAMPLE))
    cli.main(["enable", "--pitfalls", path, "--project-dir", str(tmp_path)])
    assert cli.main(
        ["disable", "--pitfalls", path, "--project-dir", str(tmp_path)]
    ) == 0
    assert reg.is_managed(tmp_path, path) is False


# --- status（skill が enable 状態を1発で把握する入口） ------------------------

def test_status_json_reports_discovered_and_enable_state(tmp_path, capsys):
    import json as _json
    import pitfall_curate as cli
    # 1件は canonical（未登録）、1件は drift（未登録）を配置
    p1 = _enable_setup(tmp_path, pc.normalize(SAMPLE))
    p2dir = tmp_path / "docs"
    p2dir.mkdir()
    p2 = p2dir / "pitfalls.md"
    p2.write_text(NUMBERED_WITH_SUBSECTIONS, encoding="utf-8")
    # canonical の方だけ enable
    cli.main(["enable", "--pitfalls", p1, "--project-dir", str(tmp_path)])
    capsys.readouterr()  # enable の出力を捨てる

    assert cli.main(["status", "--project-dir", str(tmp_path), "--json"]) == 0
    data = _json.loads(capsys.readouterr().out)
    items = {it["path"]: it for it in data["items"]}
    assert items[".claude/skills/x/references/pitfalls.md"]["managed"] is True
    assert items[".claude/skills/x/references/pitfalls.md"]["state"] == "ok"
    assert items["docs/pitfalls.md"]["managed"] is False
    assert items["docs/pitfalls.md"]["state"] == "drift"


def test_status_empty_when_no_pitfalls(tmp_path, capsys):
    import json as _json
    import pitfall_curate as cli
    assert cli.main(["status", "--project-dir", str(tmp_path), "--json"]) == 0
    data = _json.loads(capsys.readouterr().out)
    assert data["items"] == []


def test_status_survives_non_utf8_file(tmp_path, capsys):
    # 非 UTF-8 の pitfalls.md が1件あっても全スキャンを落とさない（unreadable 扱い）
    import json as _json
    import pitfall_curate as cli
    d = tmp_path / "docs"
    d.mkdir()
    (d / "pitfalls.md").write_bytes(b"\xff\xfe not utf-8 \x80\x81")
    assert cli.main(["status", "--project-dir", str(tmp_path), "--json"]) == 0
    data = _json.loads(capsys.readouterr().out)
    items = {it["path"]: it for it in data["items"]}
    assert items["docs/pitfalls.md"]["state"] == "unreadable"


# --- sync --------------------------------------------------------------------

def test_check_sync_detects_unclassified():
    parsed = pc.parse_pitfalls(SAMPLE)
    dist = pc.render_distribution(parsed, ["CDK deploy パラメータ不足"])
    report = pc.check_sync(parsed, dist, top_n=2, mandatory_generality=4)
    assert "まだ分類していない pitfall" in report["unclassified"]
    assert report["healthy"] is False


def test_check_sync_detects_missing_mandatory():
    parsed = pc.parse_pitfalls(SAMPLE)
    # mandatory の片方しか配布版に入れない
    dist = pc.render_distribution(parsed, ["CDK deploy パラメータ不足"])
    report = pc.check_sync(parsed, dist, top_n=2, mandatory_generality=4)
    assert "CDK デプロイ時のパラメータ指定ミス" in report["missing_mandatory"]


def test_check_sync_detects_stale_in_distribution():
    parsed = pc.parse_pitfalls(SAMPLE)
    # 配布版に instance/generality1（資格なし）が混入
    dist = pc.render_distribution(
        parsed, ["CDK deploy パラメータ不足", "この特定 bucket だけの命名規則"]
    )
    report = pc.check_sync(parsed, dist, top_n=5, mandatory_generality=4)
    assert "この特定 bucket だけの命名規則" in report["stale"]


def test_count_entries_counts_all_lifecycle_sections():
    content = """# Pitfalls

## Active Pitfalls

### A
- **Status**: Active

### B
- **Status**: Active

## Candidate Pitfalls

### C
- **Status**: Candidate
"""
    assert parse.count_entries(content) == 3


def test_count_entries_ignores_template_and_placeholders():
    # 正準 seed はコメント内テンプレ + 「まだ記録がありません」placeholder のみ → 0 件
    assert parse.count_entries(parse.render_seed()) == 0
