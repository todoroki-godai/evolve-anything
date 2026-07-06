"""scripts/lib/frontmatter.py のユニットテスト。"""
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

from lib.frontmatter import (
    count_content_lines,
    detect_frontmatter_error,
    extract_description,
    find_frontmatter_close,
    parse_frontmatter,
)


# --- find_frontmatter_close (#40: 行頭 \n--- アンカー) ---


def test_find_frontmatter_close_basic():
    text = "---\nk: v\n---\nbody"
    end = find_frontmatter_close(text)
    assert text[end:end + 3] == "---"
    # 閉じ --- の直前は改行（行頭区切り）
    assert text[end - 1] == "\n"


def test_find_frontmatter_close_ignores_inline_dashes():
    """値の中の --- を閉じ区切りと誤認しない（#40 の核心）。"""
    text = "---\nk: a---b\n---\nbody"
    end = find_frontmatter_close(text)
    # 値 a---b は yaml ブロック内に丸ごと残る（旧 find('---',3) はここで切れて壊す）
    assert "a---b" in text[3:end]


def test_find_frontmatter_close_no_close():
    assert find_frontmatter_close("---\nk: v\nno close") == -1


def test_find_frontmatter_close_empty_yaml():
    text = "---\n---\nbody"
    end = find_frontmatter_close(text)
    assert text[3:end].strip() == ""


def test_find_frontmatter_close_matches_old_for_wellformed():
    """正常ファイルでは旧 find('---', 3) と同じ index を返す（後方互換）。"""
    for text in (
        "---\nname: foo\ndescription: bar\n---\n# Body",
        "---\npaths:\n  - a\n  - b\n---\n",
        "---\n---\nbody",
        "---\r\nk: v\r\n---\r\nbody",  # CRLF（閉じは \r\n--- でも \n--- が当たる）
        "---\nk: v\n----\nbody",      # 閉じ行が ---- （4ダッシュ）
    ):
        assert find_frontmatter_close(text) == text.find("---", 3)


# --- parse_frontmatter ---


def test_parse_frontmatter_basic(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("---\nname: foo\ndescription: bar\n---\n# Body")
    result = parse_frontmatter(f)
    assert result == {"name": "foo", "description": "bar"}


def test_parse_frontmatter_no_frontmatter(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("# Just a heading\nSome content")
    assert parse_frontmatter(f) == {}


def test_parse_frontmatter_empty_yaml(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("---\n---\n# Body")
    assert parse_frontmatter(f) == {}


def test_parse_frontmatter_invalid_yaml(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("---\n: invalid: yaml: [[\n---\n")
    assert parse_frontmatter(f) == {}


def test_parse_frontmatter_missing_end_delimiter(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("---\nname: foo\n# no closing delimiter")
    assert parse_frontmatter(f) == {}


def test_parse_frontmatter_nonexistent_file(tmp_path):
    assert parse_frontmatter(tmp_path / "nonexistent.md") == {}


def test_parse_frontmatter_with_paths(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("---\npaths:\n  - src/**/*.ts\n  - lib/*.py\n---\n")
    result = parse_frontmatter(f)
    assert result["paths"] == ["src/**/*.ts", "lib/*.py"]


def test_parse_frontmatter_value_contains_inline_dashes(tmp_path):
    """frontmatter の値に --- が含まれても全フィールドを正しく読む（#40）。"""
    f = tmp_path / "test.md"
    f.write_text("---\nname: foo\ndescription: uses --- as separator\n---\n# Body")
    result = parse_frontmatter(f)
    assert result == {"name": "foo", "description": "uses --- as separator"}


# --- detect_frontmatter_error (#167: CC 発火不能な壊れ frontmatter の直接検出) ---


def test_detect_frontmatter_error_valid_dict(tmp_path):
    """正常な dict frontmatter は None（エラーなし）。"""
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: foo\ndescription: bar\n---\n# Body")
    assert detect_frontmatter_error(f) is None


def test_detect_frontmatter_error_no_frontmatter(tmp_path):
    """frontmatter が無ければ None（検出対象外）。"""
    f = tmp_path / "SKILL.md"
    f.write_text("# Just a heading\nSome content")
    assert detect_frontmatter_error(f) is None


def test_detect_frontmatter_error_empty_frontmatter(tmp_path):
    """空の frontmatter は None（壊れではない）。"""
    f = tmp_path / "SKILL.md"
    f.write_text("---\n---\n# Body")
    assert detect_frontmatter_error(f) is None


def test_detect_frontmatter_error_colon_space_plain_scalar(tmp_path):
    """atlas-breeaders の実際の壊れ方を写した fixture（#167）。

    description の非引用スカラーに `トリガー: `（コロン+空白）が入り yaml.safe_load が
    ScannerError（mapping values are not allowed here）を投げる。CC は frontmatter を
    yaml.safe_load で読むため name/description/trigger を読めず**自動発火しない**。
    合成 fixture ではなく実際の壊れ方（colon-space plain scalar）を写す。
    """
    f = tmp_path / "SKILL.md"
    f.write_text(
        "---\nname: atlas-breed\n"
        "description: 血統管理スキル。トリガー: (1) 交配計画 (2) 血統照会\n"
        "---\n# Body"
    )
    err = detect_frontmatter_error(f)
    assert err is not None
    assert isinstance(err, str)
    # 1 行に畳まれている（改行を含まない）
    assert "\n" not in err


def test_detect_frontmatter_error_non_mapping(tmp_path):
    """frontmatter が dict でない（list 等）→ error を返す。"""
    f = tmp_path / "SKILL.md"
    f.write_text("---\n- a\n- b\n---\n# Body")
    err = detect_frontmatter_error(f)
    assert err is not None
    assert "not a mapping" in err


def test_detect_frontmatter_error_unterminated_is_none(tmp_path):
    """閉じ `---` が無い（unterminated）は検出しない（保守側・本文の水平線 FP 回避）。"""
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: foo\n# no closing delimiter, and : broken: here")
    assert detect_frontmatter_error(f) is None


def test_detect_frontmatter_error_nonexistent_file(tmp_path):
    """読取不能（未存在）は None。"""
    assert detect_frontmatter_error(tmp_path / "nonexistent.md") is None


# --- extract_description ---


def test_extract_description_basic(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: my-skill\ndescription: A useful skill\n---\n")
    assert extract_description(f) == "A useful skill"


def test_extract_description_multiline(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text(
        "---\nname: my-skill\ndescription: |\n"
        "  First line of description.\n"
        "  Second line of description.\n"
        "---\n"
    )
    assert extract_description(f) == "First line of description."


def test_extract_description_missing(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: my-skill\n---\n")
    assert extract_description(f) == ""


def test_extract_description_no_file(tmp_path):
    assert extract_description(tmp_path / "nonexistent.md") == ""


def test_extract_description_no_frontmatter(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("# No frontmatter here")
    assert extract_description(f) == ""


# --- count_content_lines ---


def test_count_content_lines_with_frontmatter():
    """frontmatter ありのコンテンツ行数。"""
    content = '---\npaths:\n  - "**/*.py"\n---\n# Rule Title\nLine 1\nLine 2'
    assert count_content_lines(content) == 3


def test_count_content_lines_no_frontmatter():
    """frontmatter なしのコンテンツは全体行数。"""
    content = "# Rule Title\nLine 1\nLine 2"
    assert count_content_lines(content) == 3


def test_count_content_lines_frontmatter_only():
    """frontmatter のみ（コンテンツなし）は 0。"""
    content = '---\npaths:\n  - "**/*.py"\n---'
    assert count_content_lines(content) == 0


def test_count_content_lines_unclosed_frontmatter():
    """閉じられていない frontmatter は全体行数。"""
    content = "---\npaths:"
    assert count_content_lines(content) == 2


def test_count_content_lines_blank_after_frontmatter():
    """閉じ --- 後の空行は除外してカウント (#47)。"""
    content = '---\npaths:\n  - "**/*.py"\n---\n\n# Rule Title\nLine 1'
    assert count_content_lines(content) == 2


def test_count_content_lines_multiple_blanks_after_frontmatter():
    """閉じ --- 後の複数空行も全て除外 (#47)。"""
    content = '---\nname: test\n---\n\n\n# Rule Title\nLine 1\nLine 2'
    assert count_content_lines(content) == 3


def test_count_content_lines_blank_within_content_preserved():
    """コンテンツ内部の空行はカウントに含める (#47)。"""
    content = '---\nname: test\n---\n# Rule Title\n\nLine 1'
    assert count_content_lines(content) == 3


def test_count_content_lines_no_frontmatter_blank_preserved():
    """frontmatter なしの場合は空行もカウント。"""
    content = "# Rule Title\n\nLine 1"
    assert count_content_lines(content) == 3
