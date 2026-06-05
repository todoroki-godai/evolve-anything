"""原則ベース判断 / FP 除外 / 独立検証 (旧 remediation.py 由来)。

remediation/__init__.py から re-export される（後方互換）。
"""
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------- 原則ベース判断 (/autoplan 連携) ----------

REMEDIATION_PRINCIPLES = {
    "completeness": {
        "description": "修正は完全であるべき — 部分的修正は新たな問題を生む",
        "bonus": 0.08,
        "applies_to": ["stale_ref", "claudemd_phantom_ref", "claudemd_missing_section"],
    },
    "pragmatic": {
        "description": "実用的な修正を優先 — 理論的完璧さより実効性",
        "bonus": 0.06,
        "applies_to": ["line_limit_violation", "untagged_reference_candidates"],
    },
    "dry": {
        "description": "重複を排除 — 同じ情報を2箇所に持たない",
        "bonus": 0.07,
        "applies_to": ["memory_duplicate", "stale_memory"],
    },
    "explicit_over_clever": {
        "description": "明示的な修正を優先 — 暗黙の挙動に頼らない",
        "bonus": 0.05,
        "applies_to": ["stale_rule", "split_candidate"],
    },
}


def _apply_principles(issue: Dict[str, Any]) -> float:
    """issue type に該当する原則のボーナス合計を返す。

    Returns:
        0.0 ~ 0.15 のボーナス値
    """
    issue_type = issue.get("type", "")
    total_bonus = 0.0
    for _name, principle in REMEDIATION_PRINCIPLES.items():
        if issue_type in principle["applies_to"]:
            total_bonus += principle["bonus"]
    return min(total_bonus, 0.15)


# FP 除外パターン（ゼロノイズ FP 排除）
FP_EXCLUSIONS: List[str] = [
    "test_file",           # テストファイル内の参照は stale 判定しない
    "archive_path",        # openspec/archive/ 配下の参照
    "external_url",        # http(s):// で始まる外部参照
    "numeric_only",        # 429/500 等の数値のみパターン
    "code_block_ref",      # コードブロック内の参照
    "frontmatter_path",    # frontmatter の paths/globs キー内の参照
    "example_snippet",     # 例示コード内の参照
    "commented_out",       # HTML/Markdown コメント内
    "changelog_entry",     # CHANGELOG.md 内の過去参照
    "memory_index_only",   # MEMORY.md のインデックス行
    "plugin_managed",      # plugin origin のスキル参照
    "short_field_name",    # 8文字未満のフィールド名
    "tmp_path",            # /tmp 等の一時ファイルパス（実体は履歴的引用・存在しなくて当然）
    "logical_path",        # SSM 等の論理パス（/service/key、拡張子なし・ファイルシステム外）
]

# tmp_path: OS の一時ディレクトリプレフィックス（履歴的引用であり stale 判定対象外）
_TMP_PATH_PREFIXES = ("/tmp/", "/var/tmp/", "/private/tmp/", "/var/folders/")

# logical_path: SSM 風論理パスと実ファイルシステムルートを区別するための「実ルート」先頭セグメント。
# これらで始まる絶対パスは（拡張子の有無に関わらず）実ファイル参照とみなし logical_path にしない。
_REAL_FS_ROOT_SEGMENTS = {
    "users", "home", "root", "var", "tmp", "opt", "etc", "usr",
    "private", "mnt", "volumes", "bin", "sbin", "lib", "srv", "data",
}


# ---------- FP 除外判定 ----------

def _should_exclude_fp(issue: Dict[str, Any]) -> Optional[str]:
    """issue が FP_EXCLUSIONS に該当する場合はその理由を返す。該当しなければ None。"""
    file_path = issue.get("file", "")
    detail = issue.get("detail", {})
    ref_path = detail.get("path", "")

    # test_file: テストファイル内の参照
    basename = Path(file_path).name if file_path else ""
    if basename.startswith("test_") or "/tests/" in file_path or "/test/" in file_path:
        return "test_file"

    # changelog_entry: CHANGELOG.md 内の参照
    if basename == "CHANGELOG.md":
        return "changelog_entry"

    # external_url: http(s):// で始まる参照
    if ref_path.startswith("http://") or ref_path.startswith("https://"):
        return "external_url"

    # archive_path: archive/ 配下の参照
    if "/archive/" in ref_path or ref_path.startswith("archive/"):
        return "archive_path"

    # tmp_path: OS 一時ディレクトリ配下の参照（#339）。
    # /tmp/ab_test.py 等は「何を実行したか」の歴史的引用であり、ディスク上に
    # 存在しなくて当然。auto-fix で memory から削除してはならない。
    if ref_path.startswith(_TMP_PATH_PREFIXES):
        return "tmp_path"

    # logical_path: SSM パラメータ等の論理パス（#339）。
    # /docs-platform/strategy のような「絶対・全セグメント拡張子なし・実ファイル
    # システムルートでない」パスはファイル参照ではなく論理識別子。実在しない
    # のが正常なので auto_fixable に入れてはならない。
    # 実ファイルシステムルート（/Users, /home, /var, ~/.claude 等）配下は対象外。
    if ref_path.startswith("/"):
        segments = [s for s in ref_path.split("/") if s]
        if (
            len(segments) >= 2
            and all("." not in s for s in segments)
            and segments[0].lower() not in _REAL_FS_ROOT_SEGMENTS
        ):
            return "logical_path"

    # numeric_only: 数値のみパターン（429, 500 等）
    matched = detail.get("matched", "")
    if matched and re.fullmatch(r"\d+", str(matched)):
        return "numeric_only"

    # plugin_managed: plugin origin のスキル参照
    if detail.get("plugin_managed"):
        return "plugin_managed"

    # code_block_ref: コードブロック内の参照
    if detail.get("in_code_block"):
        return "code_block_ref"

    # frontmatter_path: frontmatter 内の参照
    if detail.get("in_frontmatter"):
        return "frontmatter_path"

    # example_snippet: 例示コード内の参照
    if detail.get("in_example"):
        return "example_snippet"

    # commented_out: コメント内の参照
    if detail.get("commented_out"):
        return "commented_out"

    # memory_index_only: MEMORY.md のインデックス行
    if detail.get("memory_index_only"):
        return "memory_index_only"

    # short_field_name: 8文字未満のフィールド名（パス区切り除く最短セグメント）
    if ref_path and "/" in ref_path:
        segments = [s for s in ref_path.split("/") if s]
        if all(len(s) < 8 for s in segments):
            return "short_field_name"

    return None


# ---------- 独立検証 ----------

def _independent_verify(issue: Dict[str, Any], before_content: str, after_content: str) -> Dict[str, Any]:
    """修正前後のコンテンツを独立したヒューリスティクスで検証。

    LLM 不使用。ヒューリスティクスベース。

    Returns:
        {"passed": bool, "reason": str, "confidence": float}
    """
    reasons: List[str] = []

    # 空ファイルチェック
    if not after_content.strip():
        return {"passed": False, "reason": "修正後のファイルが空です", "confidence": 1.0}

    # 見出し数が減少していないかチェック
    before_headings = re.findall(r"^#{1,6}\s+", before_content, re.MULTILINE)
    after_headings = re.findall(r"^#{1,6}\s+", after_content, re.MULTILINE)
    if len(after_headings) < len(before_headings):
        lost = len(before_headings) - len(after_headings)
        reasons.append(f"見出しが {lost} 個減少しています")

    # コードブロックの対応チェック
    after_fences = after_content.count("```")
    if after_fences % 2 != 0:
        reasons.append("コードブロック(```)の開始/終了が不対応です")

    # Rules ファイルの行数制限チェック
    file_path = issue.get("file", "")
    if ".claude/rules/" in file_path:
        try:
            from line_limit import MAX_RULE_LINES
            line_count = after_content.count("\n") + (1 if after_content and not after_content.endswith("\n") else 0)
            if line_count > MAX_RULE_LINES:
                reasons.append(f"行数が MAX_RULE_LINES ({MAX_RULE_LINES}) を超過しています ({line_count} 行)")
        except ImportError:
            pass

    # before→after で削除された参照パスの妥当性チェック
    before_paths = set(re.findall(r"(?:^|\s)([a-zA-Z_.][a-zA-Z0-9_./\-]+\.(?:py|md|json|jsonl|yaml|yml|sh))", before_content))
    after_paths = set(re.findall(r"(?:^|\s)([a-zA-Z_.][a-zA-Z0-9_./\-]+\.(?:py|md|json|jsonl|yaml|yml|sh))", after_content))
    removed_paths = before_paths - after_paths
    if removed_paths:
        # 削除されたパスが全体の半数以上だと警告
        if len(removed_paths) > len(before_paths) / 2 and len(before_paths) > 2:
            reasons.append(f"参照パスの過半数 ({len(removed_paths)}/{len(before_paths)}) が削除されました")

    if reasons:
        return {"passed": False, "reason": "; ".join(reasons), "confidence": 0.9}

    return {"passed": True, "reason": "", "confidence": 0.95}
