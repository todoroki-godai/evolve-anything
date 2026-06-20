"""汎用 YAML frontmatter パーサー / ライター。

SKILL.md / rule ファイルの YAML frontmatter を解析・更新する共通ユーティリティ。
prune.py と reflect_utils.py の両方から利用する。
"""
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml


def find_frontmatter_close(text: str) -> int:
    """開き `---` に対応する閉じ `---` の開始インデックスを返す（#40）。

    閉じ区切りは「行頭の `---`」（直前が改行）のみとみなし、YAML 値の中に
    現れる `---`（例: `description: a --- b`）を区切りと誤認しない。
    `text.find("---", 3)` は値内の `---` にマッチして frontmatter を破壊する
    弱い慣習で、reader と writer が別々に同じ式を持つと read/write が desync
    する。本関数を frontmatter 区切り探索の単一ソースとする。

    返り値は閉じ `---` の開始インデックスなので、呼び出し側は従来どおり
    `text[3:end]`（YAML ブロック）/ `text[end + 3:]`（本文）で slice できる。
    正常な frontmatter（閉じ `---` が行頭）では `text.find("---", 3)` と同じ
    インデックスを返すため後方互換。

    前提: 開き `---` の直後は改行であること（正常な frontmatter は必ず `---\\n`）。
    単一行の `---...---`（開き行に内容も閉じも乗る不正形）は「閉じなし」(-1) として
    扱う。旧 `find("---", 3)` は値内の `---` を拾って誤パースしていたため、これは
    退行ではなくより安全側の挙動。

    Args:
        text: `---` で始まる前提のファイル内容。

    Returns:
        閉じ `---` の開始インデックス。見つからなければ -1。
    """
    nl = text.find("\n---", 3)
    if nl == -1:
        return -1
    return nl + 1


def count_content_lines(content: str) -> int:
    """frontmatter を除外したコンテンツ部分の行数を返す。

    YAML frontmatter（`---` で始まり `---` で閉じるブロック）がある場合、
    閉じ `---` 以降の行数を返す。frontmatter がなければ全体行数を返す。

    Args:
        content: ファイル内容の文字列

    Returns:
        コンテンツ部分の行数
    """
    if not content or not content.strip():
        return 0

    if not content.startswith("---"):
        return content.count("\n") + 1

    # 閉じ --- を探す（3文字目以降）
    end = content.find("\n---", 3)
    if end == -1:
        # 閉じられていない → 全体行数
        return content.count("\n") + 1

    # 閉じ --- の行末の次の文字位置
    after_close = end + 4  # len("\n---")
    # 閉じ --- の後に改行がある場合はスキップ
    if after_close < len(content) and content[after_close] == "\n":
        after_close += 1

    body = content[after_close:]
    # frontmatter 直後の空行を除外 (#47)
    body = body.lstrip("\n")
    if not body or not body.strip():
        return 0

    return body.count("\n") + 1


def parse_frontmatter(filepath: Path) -> Dict[str, Any]:
    """YAML frontmatter（--- 区切り）を辞書として返す。

    Args:
        filepath: 対象ファイルのパス

    Returns:
        frontmatter の辞書。frontmatter がなければ空辞書。
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}

    if not text.startswith("---"):
        return {}

    end = find_frontmatter_close(text)
    if end == -1:
        return {}

    yaml_str = text[3:end].strip()
    if not yaml_str:
        return {}

    try:
        parsed = yaml.safe_load(yaml_str)
        return parsed if isinstance(parsed, dict) else {}
    except yaml.YAMLError:
        return {}


def update_frontmatter(filepath: Path, updates: Dict[str, Any]) -> Tuple[bool, str]:
    """frontmatter のキー/値を追加・更新してファイルを書き戻す。

    Args:
        filepath: 対象ファイルのパス
        updates: 追加/更新するキー/値の辞書

    Returns:
        (success, error_message): 成功時は (True, "")、失敗時は (False, エラー詳細)
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return False, str(e)

    if not text.strip():
        return False, "empty_file"

    if text.startswith("---"):
        end = find_frontmatter_close(text)
        if end == -1:
            return False, "yaml_parse_error"

        yaml_str = text[3:end].strip()
        try:
            parsed = yaml.safe_load(yaml_str)
            if not isinstance(parsed, dict):
                parsed = {}
        except yaml.YAMLError:
            return False, "yaml_parse_error"

        parsed.update(updates)
        new_yaml = yaml.dump(parsed, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip()
        body = text[end + 3:]  # content after closing ---
        new_text = f"---\n{new_yaml}\n---{body}"
    else:
        # No existing frontmatter — add one at the top
        new_yaml = yaml.dump(updates, default_flow_style=False, allow_unicode=True).rstrip()
        new_text = f"---\n{new_yaml}\n---\n{text}"

    try:
        filepath.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return False, str(e)

    return True, ""


def extract_description(filepath: Path) -> str:
    """frontmatter から description を抽出する。multiline の場合は1行目のみ返す。

    Args:
        filepath: 対象ファイルのパス

    Returns:
        description 文字列。取得不可の場合は空文字。
    """
    fm = parse_frontmatter(filepath)
    desc = fm.get("description", "")
    if not isinstance(desc, str):
        desc = str(desc) if desc is not None else ""
    # multiline 対応: 1行目のみ返す
    first_line = desc.strip().split("\n")[0].strip() if desc.strip() else ""
    return first_line
