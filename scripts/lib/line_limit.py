"""行数制限チェック共通モジュール。

スキル/ルールファイルの行数上限を一元管理する Single Source of Truth。
"""
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frontmatter import count_content_lines

MAX_SKILL_LINES = 500
MAX_RULE_LINES = 10
MAX_PROJECT_RULE_LINES = 10
CLAUDEMD_WARNING_LINES = 300

# GEPA ガードレール（#120）: 最適化パッチの文字数上限を行上限から導出する。
# 行数だけでは「行内 bloat（1 行が異常に長い）」を検出できないため、
# ``max_lines × MAX_CHARS_PER_LINE`` をパッチの char 上限とする（GEPA の
# プロンプト肥大化抑制知見: 上限を設けると性能低下ほぼ無しで 4x 圧縮）。
# 値 200 は他ドメイン数値の流用でなく、当 PJ の skill/rule 実ファイルへの
# dry-run 較正で決定した（誤ブロック 0 件・skill 実測最大 59.7 / rule 実測最大
# 136.8 chars/line に対し headroom 1.5〜3.4x）。[[learning_gate_design_needs_real_corpus_dryrun]]
MAX_CHARS_PER_LINE = 200


def max_chars_for(max_lines: int) -> int:
    """行上限からパッチの文字数上限を導出する（#120 GEPA ガードレール）。

    行数ゲートを通っても 1 行あたりの文字数が異常に多いと bloat になるため、
    ``max_lines × MAX_CHARS_PER_LINE`` を char 上限として regression gate に渡す。
    """
    return max_lines * MAX_CHARS_PER_LINE

# Python source ファイルの行数バジェット（audit.py 2046行肥大化の反省、Slice 13）
# warn: 分割検討を促す / hard: violation として issues に積む
# __init__.py / migrations / 自動生成ファイルは除外（PYTHON_SOURCE_BUDGET_EXCLUDE_BASENAMES）
MAX_PYTHON_SOURCE_LINES = 500
MAX_PYTHON_SOURCE_HARD = 800
PYTHON_SOURCE_BUDGET_EXCLUDE_BASENAMES = {"__init__.py", "conftest.py"}

# 制限値の何割を超えたら「near-limit」警告を出すか。audit/discover/remediation で共用。
NEAR_LIMIT_RATIO = 0.8

# MEMORY.md バイトサイズ制限（CC v2.1.83 で 25KB 切り詰め追加）
MEMORY_MAX_BYTES = 25_000
MEMORY_NEAR_LIMIT_BYTES = 20_000  # 80% 警告閾値


def check_memory_byte_limit(content: str) -> tuple[bool, int]:
    """MEMORY.md のバイトサイズ制限をチェックする。

    Args:
        content: ファイル内容

    Returns:
        (within_limit, byte_size) のタプル。
        within_limit は MEMORY_MAX_BYTES 以下なら True。
    """
    byte_size = len(content.encode("utf-8"))
    return byte_size <= MEMORY_MAX_BYTES, byte_size


@dataclass
class SeparationProposal:
    """rule 行数超過時の分離提案。"""

    target_path: str
    reference_path: str
    summary_template: str
    excess_lines: int


def _is_global_rule(target_path: str) -> bool:
    """グローバルルール（~/.claude/rules/ 配下）かどうかを判定する。"""
    home = str(Path.home())
    return target_path.startswith(home) and ".claude/rules/" in target_path


def check_line_limit(target_path: str, content: str) -> bool:
    """行数制限をチェック。超過時は stderr に警告を出して False を返す。

    Args:
        target_path: 対象ファイルのパス文字列
        content: ファイル内容

    Returns:
        行数制限内なら True、超過なら False
    """
    is_rule = ".claude/rules/" in target_path
    if is_rule:
        if _is_global_rule(target_path):
            max_lines = MAX_RULE_LINES
        else:
            max_lines = MAX_PROJECT_RULE_LINES
    else:
        max_lines = MAX_SKILL_LINES
    lines = count_content_lines(content) if is_rule else content.count("\n") + 1
    if lines > max_lines:
        file_type = "ルール" if is_rule else "スキル"
        print(
            f"  行数超過: {lines}/{max_lines}行（{file_type}制限）。適用を拒否。",
            file=sys.stderr,
        )
        return False
    return True


def _resolve_reference_path(rule_path: Path) -> Path:
    """rule ファイルから分離先の references ディレクトリパスを決定する。"""
    rules_dir = rule_path.parent
    return rules_dir.parent / "references" / rule_path.name


def _deduplicate_path(ref_path: Path) -> Path:
    """既存ファイルと衝突しない参照先パスを返す。"""
    if not ref_path.exists():
        return ref_path
    stem = ref_path.stem
    suffix = ref_path.suffix
    parent = ref_path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def suggest_separation(
    target_path: str, content: str
) -> Optional[SeparationProposal]:
    """rule 行数超過時の分離提案を生成する。

    rule ファイルが行数制限を超過している場合に分離先パスと
    要約テンプレートを含む SeparationProposal を返す。
    rule 以外のファイルや制限内の場合は None を返す。
    """
    if ".claude/rules/" not in target_path:
        return None

    is_global = _is_global_rule(target_path)
    max_lines = MAX_RULE_LINES if is_global else MAX_PROJECT_RULE_LINES
    lines = count_content_lines(content)
    excess = lines - max_lines

    if excess <= 0:
        return None

    rule_path = Path(target_path)
    ref_path = _resolve_reference_path(rule_path)
    ref_path = _deduplicate_path(ref_path)

    rel_ref = ref_path.name
    summary_template = (
        f"# {rule_path.stem}\n"
        f"詳細は [references/{rel_ref}](../references/{rel_ref}) を参照。"
    )

    return SeparationProposal(
        target_path=target_path,
        reference_path=str(ref_path),
        summary_template=summary_template,
        excess_lines=excess,
    )
