"""pitfall-curate のフォーマット I/O 層（純粋関数） — parse / seed / normalize。

curate ロジック（分類 / dedup / distill / sync）は core.py。本モジュールは
「pitfalls.md を読む・正準形に揃える」だけを担い、LLM も類似度エンジンも使わない。

対応フォーマット（有機的に育った実 PJ のゆらぎを吸収する）:
- セクション見出し: `## Active Pitfalls`（正準）/ `## Active` / `## New（…）` の fuzzy
- エントリ見出し: `### <title>`（正準）/ `### N. <title>` / `## N. <title>`（sys-bots）
- メタデータ: `- **Key**: value`（バレット）/ `**Key**: v | **Key**: v`（インラインパイプ）
- `<!-- -->` コメントブロックは無視（docs-platform のテンプレートを phantom 化しない）
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# セクション見出しの fuzzy マッピング。先頭キーワードで判定する。
# "New"（未検証・再発で昇格）は Candidate 相当のライフサイクルとして扱う。
_SECTION_KEYWORDS = (
    ("active", "active"),
    ("candidate", "candidate"),
    ("new", "candidate"),
    ("graduated", "graduated"),
)

_CANONICAL_SECTIONS = [
    ("active", "## Active Pitfalls"),
    ("candidate", "## Candidate Pitfalls"),
    ("graduated", "## Graduated Pitfalls"),
]

_CANONICAL_SEED = """# Pitfalls

## Active Pitfalls

<!-- 項目テンプレート（コメント内なので未記入時はエントリ扱いされない）:
### <タイトル>
- **Status**: Active
- **Last-seen**: YYYY-MM-DD
- **Root-cause**: [category] — [一行で原因]
- **Pre-flight対応**: Yes / No
- **Transferability**: universal | project | instance
- **Generality**: 1-5
-->

_まだ記録がありません。エラーや訂正が起きたら記録してください。_

## Candidate Pitfalls

_まだ記録がありません。_

## Graduated Pitfalls

_まだ記録がありません。_
"""


def _section_of_heading(line: str) -> str | None:
    """見出し行を active/candidate/graduated に fuzzy マッピング（非該当は None）。"""
    if not line.startswith("## "):
        return None
    body = line[3:].strip().lower()
    for keyword, section in _SECTION_KEYWORDS:
        if body.startswith(keyword):
            return section
    return None


_NUMBERED_H3 = re.compile(r"^### \d+\.")
_UNNUMBERED_H3 = re.compile(r"^### (?!\d+\.)")


def _demote_subsection_headings(content: str) -> str:
    """番号なし `### 見出し` を `#### ` へ降格する（番号付き `### N.` エントリが在る時のみ）。

    実 PJ（atlas-browser）は番号付き `### N.` をエントリ見出しに、番号なし `### 真の原因`
    等を1エントリ内のサブ見出しに使う。パーサは `### ` を一律エントリ扱いするため、後者が
    幽霊エントリに化ける。文書が「番号付き `### N.` を使う流儀」だと判明した場合に限り、番号
    なし `### ` をサブ見出し(`#### `)とみなして降格する（番号は保持されるので冪等: 出力に
    番号なし `### ` は残らず再変換で no-op）。正準形（番号なしエントリのみ）はシグナル OFF で無変更。
    コードフェンス内の行は触らない。
    """
    lines = content.splitlines()
    has_numbered = any(_NUMBERED_H3.match(ln) for ln in lines)
    if not has_numbered:
        return content
    out: List[str] = []
    in_fence = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append(line)
            continue
        if not in_fence and _UNNUMBERED_H3.match(line):
            out.append("#" + line)  # ### → ####
        else:
            out.append(line)
    return "\n".join(out)


def _is_numbered_h2_entry(line: str) -> bool:
    """`## N. タイトル`（番号付き H2）をエントリとして扱う（sys-bots 形式）。

    ライフサイクルセクション見出し（## Active 等）は _section_of_heading が先に拾う。
    番号なしの構造見出し（## カテゴリ一覧 等）はエントリにしない（足切り）。
    """
    if not line.startswith("## "):
        return False
    return bool(re.match(r"\d+\.", line[3:].strip()))


def _parse_field_line(line: str, fields: Dict[str, str]) -> None:
    """エントリ本文中のメタデータ行をフィールドに取り込む（破壊的）。

    2 形式に対応:
    - バレット: `- **Key**: value`（正準フォーマット）
    - インラインパイプ: `**Key**: v | **Key**: v`（sys-bots / atlas 形式）
    """
    ls = line.lstrip()
    if ls.startswith("- **"):
        key, sep, rest = ls[4:].partition("**:")
        if sep:
            fields[key.strip()] = rest.strip()
        return
    if ls.startswith("**") and "**:" in ls:
        for seg in ls.split(" | "):
            seg = seg.strip()
            if seg.startswith("**"):
                key, sep, rest = seg[2:].partition("**:")
                if sep:
                    fields[key.strip()] = rest.strip()


def parse_pitfalls(content: str) -> Dict[str, List[Dict[str, Any]]]:
    """pitfalls.md を Active/Candidate/Graduated の3セクションに分解する。

    各要素: {"title": str, "fields": {key: value}, "section": str, "raw": str}
    順序を保持するため list で返す。
    """
    sections: Dict[str, List[Dict[str, Any]]] = {
        "active": [],
        "candidate": [],
        "graduated": [],
    }
    section = "active"
    item: Dict[str, Any] | None = None
    buf: List[str] = []

    def flush() -> None:
        nonlocal item, buf
        if item is not None:
            item["raw"] = "\n".join(buf).rstrip()
            sections[item["section"]].append(item)
        item, buf = None, []

    in_comment = False
    for line in content.splitlines():
        # HTML コメントブロックは無視する（docs-platform のテンプレートが phantom
        # エントリにならないように）。見出しは行頭が `<!--` にならないため安全。
        if in_comment:
            if "-->" in line:
                in_comment = False
            if item is not None:
                buf.append(line)
            continue
        if line.lstrip().startswith("<!--"):
            if "-->" not in line:
                in_comment = True
            if item is not None:
                buf.append(line)
            continue

        mapped = _section_of_heading(line)
        if mapped is not None:
            flush()
            section = mapped
            continue
        if line.startswith("### ") or _is_numbered_h2_entry(line):
            flush()
            title = line[4:] if line.startswith("### ") else line[3:]
            item = {"title": title.strip(), "fields": {}, "section": section}
            buf = [line]
            continue
        if item is not None:
            buf.append(line)
            _parse_field_line(line, item["fields"])
    flush()
    return sections


def render_seed() -> str:
    """正準フォーマットの空ひな型を返す（docs-platform 式の seed）。"""
    return _CANONICAL_SEED


def _split_header(content: str) -> tuple[str, List[str]]:
    """H1 タイトル行と、最初のセクション/エントリ前のプリアンブル散文を抽出する。

    normalize はエントリしか保持しないため、ファイル先頭の説明的 H1（`# atlas-browser:
    既知の問題と対策`）やプリアンブル散文（`> 自動チェック…` 等）を捨ててしまう。これらは
    ユーザー記述のデータなので保持する。タイトルが無ければ汎用 `# Pitfalls` を使う。
    """
    lines = content.splitlines()
    title = "# Pitfalls"
    start = 0
    if lines and lines[0].startswith("# "):  # "## " は startswith("# ") に該当しない
        title = lines[0].rstrip()
        start = 1
    preamble: List[str] = []
    for line in lines[start:]:
        if (
            _section_of_heading(line) is not None
            or line.startswith("### ")
            or _is_numbered_h2_entry(line)
            or line.lstrip().startswith("<!--")
        ):
            break
        preamble.append(line)
    while preamble and preamble[0].strip() == "":
        preamble.pop(0)
    while preamble and preamble[-1].strip() == "":
        preamble.pop()
    return title, preamble


def _normalize_entry_lines(item: Dict[str, Any]) -> List[str]:
    """エントリの raw を正準形の行リストへ変換する。

    構造だけを揃える: 見出しを `### <title>` に統一（`## N.` → `### N.`）、
    インラインパイプ・メタdata を `- **K**: v` バレットに展開。本文の散文・コードは
    そのまま保持し、フィールドのキー名はリネームしない（データ忠実性）。
    """
    raw_lines = item["raw"].splitlines() if item.get("raw") else []
    out = [f"### {item['title']}"]
    for line in raw_lines[1:]:  # 先頭行は見出しなので除く
        ls = line.lstrip()
        if ls.startswith("- **"):
            out.append(line)
        elif ls.startswith("**") and "**:" in ls:
            for seg in ls.split(" | "):
                seg = seg.strip()
                if seg.startswith("**"):
                    key, sep, rest = seg[2:].partition("**:")
                    if sep:
                        out.append(f"- **{key.strip()}**: {rest.strip()}")
        else:
            out.append(line)
    while out and out[-1].strip() == "":
        out.pop()
    return out


# エントリ0件で許容する非エントリ行数の上限。空ひな型（プレースホルダ+コメントのみ）は
# 0行に近く、インデックス/TOC（テーブル+リンク）は大きく超えるため両者を分離できる。
_NON_ENTRY_CONTENT_FLOOR = 3


def _count_orphan_content_lines(content: str) -> int:
    """エントリにも見出しにも属さない実質コンテンツ行を数える（コメント/プレースホルダは除外）。

    エントリ0件の文書でこれが多い＝インデックス/TOC 等の非エントリファイル。normalize すると
    全足切りで wipe されるため、ガードの判定に使う。
    """
    n = 0
    in_comment = False
    for line in content.splitlines():
        s = line.strip()
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        if s.startswith("<!--"):
            if "-->" not in s:
                in_comment = True
            continue
        if not s or s.startswith("#"):
            continue
        if s.startswith("_") and s.endswith("_"):  # `_まだ記録がありません。_` 等のプレースホルダ
            continue
        n += 1
    return n


def normalize(content: str) -> str:
    """任意フォーマットの pitfalls.md を正準フォーマットへ変換する（冪等）。

    セクション見出し・エントリ見出しレベル・メタdataのバレット化だけを揃え、本文は保持する。
    正準フォーマットを入力すると不変（idempotent）。

    エントリが1件も無く実質コンテンツ（テーブル/リンク等）がある場合は、インデックス/TOC や
    非エントリファイルとみなし ValueError を送出する（normalize で全足切り wipe するのを防ぐ）。
    """
    content = _demote_subsection_headings(content)
    parsed = parse_pitfalls(content)
    if sum(len(v) for v in parsed.values()) == 0 and (
        _count_orphan_content_lines(content) > _NON_ENTRY_CONTENT_FLOOR
    ):
        raise ValueError(
            "normalize: エントリが見つからないが実質コンテンツがあります"
            "（インデックス/TOC や非正準ファイルの可能性）。wipe を避けるため中断しました。"
            "`### タイトル` 形式のエントリへ再構成するか、別ファイルを指定してください。"
        )
    title, preamble = _split_header(content)
    out: List[str] = [title, ""]
    if preamble:
        out.extend(preamble)
        out.append("")
    for sec_key, header in _CANONICAL_SECTIONS:
        out.append(header)
        out.append("")
        for item in parsed[sec_key]:
            out.extend(_normalize_entry_lines(item))
            out.append("")
    return "\n".join(out).rstrip() + "\n"
