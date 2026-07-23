"""sibling_copy_guard.py — diff-scoped 兄弟コピー検出 partial-fix ガード（#210）。

決定論・LLM 非依存。commit/push 対象の変更行を正規化し、同一正規化形のコードが repo 内の
**他の場所に変更されず残っている**場合、「片側修正の疑い」を検出する。

射程の明記（過剰約束の防止）:
  - **拾える**: 同一の弱いロジック片が複数箇所に文字列レベルでコピペされ、片側だけ変更
    された場合（実例: pitfall #40 — `text.find("---", 3)` という式が reader/writer 6箇所に
    コピーされ片側修正で本文破壊）。
  - **拾えない**: consumer 側が**別の表現**で同じ壊れた規則を実装しているケース
    （正規化後も一致しない）。
  - **汎用 clone detection（type-2/3）には広げない**。範囲は「変更行と同型の未変更行」の
    みに厳格に限定する。type-2/3 clone 検出（変数名だけ違う・構造は同じ等）は重くて FP
    地獄になるため対象外。
  - **既知の残存 FP 源（実コーパス dry-run #210 で確認・意図的に未対応）**: `if X is None:`
    / `if X.returncode != 0:` / `assert len(result) >= 1` のような**汎用的な短い定型句**は
    真の #40 型モチーフ（`end = text.find("---", 3)`、正規化後トークン数4）とちょうど同じ
    トークン数帯に位置するため、`min_tokens` を上げると本来検出したい実例まで一緒に消える
    （dry-run で実測確認）。トークン数だけでは「弱いロジック片」と「言語イディオム」を
    安全に区別できない既知のトレードオフとして残す（無闇な閾値調整で recall を落とさない）。

設計判断（`skill_declaration_reachability.py` の慣習に倣い a) b) c)... 形式で明記）:

  a) **走査スコープは `scripts/**.py` + `skills/**/scripts/**.py`**（`skill_declaration_
     reachability._iter_py_files` と同じスコープ）。`.claude` 除外は絶対パス全体でなく
     **repo_root からの相対パス**で判定する（worktree（`<repo>/.claude/worktrees/<id>/...`）
     で実行すると repo_root 自身の絶対パスに `.claude` を含むため、絶対パス全体判定だと
     全ファイルを誤除外する。#191 で実機発見済みの pitfall と同型）。

  b) **正規化は軽量**（前後 trim + 内部連続空白を1個に圧縮のみ）。AST 式 dump のような重い
     正規化はしない（issue が明示的に軽量に留めている・type-2/3 clone 検出への拡張を避ける
     ため）。

  c) **trivial 行除外床**: 空行 / `pass` / `return` / `return None` / `continue` / `break`
     / `else:` / `try:` / コメントのみの行 / **import 文**（`import ...` / `from ... import
     ...`）、および正規化後トークン数（空白区切り）が `min_tokens`（既定4）未満の行は除外
     する。import 文の除外は実コーパス dry-run 較正で追加した判断: import 文は「モジュール
     依存の宣言」であり「弱いロジック片の複製」ではない（同じ名前を同じモジュールから
     import する行が repo 全体で大量一致するのは意図的な正常状態で、`text.find("---", 3)`
     型の「コピペされた壊れたロジック」とは性質が異なる）。閾値は実コーパス dry-run で
     調整可能なパラメータとして公開する。

  d) **diff 対象のファイルは丸ごと除外する**（変更行自身だけでなく、同一ファイル内の
     他の未変更行も除外対象に含める）。issue 本文が「diff 対象のファイル・変更された
     行自体は除外する」と明記しているため、対象ファイル内の別行に同型コードが残っていても
     「兄弟コピー」としては報告しない（同一ファイル内の重複は別種の問題であり、このガードの
     射程は「他ファイルへの文字列コピペ」に限定する）。

  e) **抽出対象は「削除された行」（diff の `-` 側 = 変更前の内容）**。「追加された行」
     （`+` 側 = 変更後の内容）ではない。理由: 検出したいのは「この行は変更前どんな内容
     だったか、それが他ファイルに未変更のまま残っていないか」（#40 型: 6箇所にコピーされた
     弱いロジックのうち1箇所だけ変更＝他5箇所は変更前の内容のまま）。変更後の内容（新しく
     書いた行）で照合すると、他ファイルはまだその新しい書き方に追従していないため一致せず、
     検出したい「取り残された兄弟」を素通りしてしまう。純粋な追加行（対応する `-` の無い
     新規行）は「変更前の内容」が存在しないため対象外——新規追加コードが既存コードとたまたま
     一致する type-2 clone 検出はこのガードの射程外。
     diff のパースは unified diff（`git diff` 既定フォーマット）のみを対象にする。
     `+++ b/<path>` 形式のファイルヘッダ（新ファイル側パス。rename でもリネーム後のパスを
     使う——repo 全体照合は現在のワーキングツリーに対して行うため、現在存在するパスで
     exclude する必要がある）と `@@ -a,b +c,d @@` 形式の hunk ヘッダを解析し、削除行の
     位置を新ファイル側 line 番号のアンカー（周辺の new-line カウンタ位置）で表す。
     `\\ No newline at end of file` マーカーは line 番号に影響しない。バイナリ diff・
     rename のみ（内容変更なし）の diff は hunk が無いため自然に無視される。削除ファイル
     全体（`+++ /dev/null`）は新ファイル側パスが存在しないため対象外（ファイル自体が
     消滅する場合は「片側だけ変更」の文脈に当てはまらない）。

  f) **repo 全体照合はメモリ上のインデックス**（正規化後テキスト → (file, line) のリスト）
     を1回構築して行う。コミットごとに repo 全体を再スキャンする用途（実コーパス dry-run）
     では、履歴上の各時点のスナップショットを別ディレクトリに用意してこの関数を呼ぶ想定
     （このモジュール自体は「特定時点のワーキングツリー」に対してのみ動作する）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

DEFAULT_MIN_TOKENS = 4

_WS_RE = re.compile(r"\s+")

_TRIVIAL_EXACT = {
    "",
    "pass",
    "return",
    "return None",
    "continue",
    "break",
    "else:",
    "try:",
}

RunFunc = Callable[..., "subprocess.CompletedProcess"]


def _default_repo_root() -> Path:
    """evolve-anything 自身のリポジトリ（プラグイン）ルート。

    module 定数でなく関数にして呼び出し時に解決する（skill_declaration_reachability の
    `_default_repo_root` と同じ慣習）。テストは `repo_root` 引数で疑似ツリーに差し替える。
    """
    from plugin_root import PLUGIN_ROOT

    return PLUGIN_ROOT


# --- normalize / trivial filter ------------------------------------------------


def normalize_line(text: str) -> str:
    """前後空白を trim し、内部の連続空白（タブ含む）を1個の半角スペースに圧縮する。"""
    return _WS_RE.sub(" ", text.strip())


def is_trivial_line(normalized: str, *, min_tokens: int = DEFAULT_MIN_TOKENS) -> bool:
    """正規化済みの行が「片側修正検出の対象外」の trivial 行かどうかを判定する（設計 c）。"""
    if normalized in _TRIVIAL_EXACT:
        return True
    if normalized.startswith("#"):
        return True
    if normalized.startswith("import ") or normalized.startswith("from "):
        # import 文はモジュール依存の宣言であり「弱いロジック片の複製」ではない
        # （#210 実コーパス dry-run 較正: FP の大半が `from typing import ...` 等の
        # import 文の repo 全体一致だったため追加。設計 c の trivial 床の一部）。
        return True
    if len(normalized.split(" ")) < min_tokens:
        return True
    return False


# --- diff parsing --------------------------------------------------------------


@dataclass(frozen=True)
class ChangedLine:
    """`git diff` の削除行1件（＝変更前の内容。設計 e で述べた通り検出の起点はこちら）。

    file:    新ファイル側の相対パス（posix 区切り、`b/` プレフィックスは除去済み）。
    line_no: 変更が発生した位置の新ファイル側1-origin行番号（削除行自体は新ファイルに
             存在しないため、周辺の new-line カウンタ位置をアンカーとして使う目安値）。
    text:    削除された行の生テキスト（変更前の内容、先頭の `-` を除いたもの）。
    """

    file: str
    line_no: int
    text: str


_DIFF_GIT_RE = re.compile(r"^diff --git ")
_FILE_HEADER_RE = re.compile(r"^\+\+\+ (?:b/(?P<path>.+)|(?P<devnull>/dev/null))$")
_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff_removed_lines(diff_text: str) -> List[ChangedLine]:
    """unified diff（`git diff` 出力）から削除行（変更前の内容）を抽出する（設計 e）。"""
    out: List[ChangedLine] = []
    current_file: Optional[str] = None
    new_line_no = 0
    in_hunk = False

    for raw_line in diff_text.splitlines():
        if _DIFF_GIT_RE.match(raw_line):
            current_file = None
            in_hunk = False
            continue

        m = _FILE_HEADER_RE.match(raw_line)
        if m:
            current_file = m.group("path")  # None のとき /dev/null（削除ファイル）
            in_hunk = False
            continue

        hm = _HUNK_HEADER_RE.match(raw_line)
        if hm:
            new_line_no = int(hm.group(1))
            in_hunk = True
            continue

        if not in_hunk or current_file is None:
            continue

        if raw_line.startswith("\\"):
            # "\ No newline at end of file" — line 番号に影響しない
            continue
        if raw_line.startswith("+"):
            new_line_no += 1  # 追加行は新ファイル側の line 番号を進める（削除行は対象外）
        elif raw_line.startswith("-"):
            out.append(ChangedLine(file=current_file, line_no=new_line_no, text=raw_line[1:]))
            # 削除行自体は新ファイルに存在しないので line 番号は進めない
        else:
            new_line_no += 1  # context 行（" " 始まり、または空行）

    return out


def get_diff_text(
    repo_root: Path, diff_args: Sequence[str], *, run: RunFunc = subprocess.run
) -> str:
    """`git -C <repo_root> diff <diff_args...>` の出力を返す（失敗時は空文字）。"""
    proc = run(
        ["git", "-C", str(repo_root), "diff", *diff_args],
        capture_output=True,
        text=True,
        timeout=30.0,
    )
    return proc.stdout if proc.returncode == 0 else ""


# --- repo-wide sibling index ----------------------------------------------------


def _iter_py_files(repo_root: Path) -> List[Path]:
    """scripts/**.py + skills/**/scripts/**.py を列挙する（相対パスで `.claude` 除外）。

    除外判定は **repo_root からの相対パス**の parts で行う（設計 a・skill_declaration_
    reachability._iter_py_files と同じ慣習・同じ pitfall 回避）。
    """
    root = Path(repo_root)
    files = set(root.glob("scripts/**/*.py")) | set(root.glob("skills/**/scripts/**/*.py"))
    return sorted(f for f in files if ".claude" not in f.relative_to(root).parts)


@dataclass(frozen=True)
class SiblingLocation:
    """repo 全体照合で見つかった「同型の未変更行」の所在。"""

    file: str
    line: int


def _build_normalized_line_index(
    repo_root: Path, *, exclude_files: Set[str]
) -> Dict[str, List[SiblingLocation]]:
    root = Path(repo_root)
    index: Dict[str, List[SiblingLocation]] = {}
    for f in _iter_py_files(root):
        rel = f.relative_to(root).as_posix()
        if rel in exclude_files:
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(lines, start=1):
            norm = normalize_line(line)
            if not norm:
                continue
            index.setdefault(norm, []).append(SiblingLocation(file=rel, line=lineno))
    return index


# --- detection -------------------------------------------------------------------


@dataclass(frozen=True)
class SiblingCopyMatch:
    """変更行 (changed_file:changed_line) → 同型の未変更行のリスト。

    changed_text は diff の変更前内容（削除行の生テキスト）——検出の起点になった、
    「この commit で書き換えられた箇所は元々どんな内容だったか」を表す。
    """

    changed_file: str
    changed_line: int
    changed_text: str
    normalized: str
    siblings: Tuple[SiblingLocation, ...]


def detect_sibling_copies(
    diff_text: str,
    repo_root: Path,
    *,
    min_tokens: int = DEFAULT_MIN_TOKENS,
) -> List[SiblingCopyMatch]:
    """diff の変更前内容と同型の未変更行が repo 内の他の場所に残っていないかを検出する。"""
    changed = parse_diff_removed_lines(diff_text)
    if not changed:
        return []

    changed_files = {c.file for c in changed}  # 設計 d: diff 対象ファイルを丸ごと除外

    candidates: List[Tuple[ChangedLine, str]] = []
    for c in changed:
        normalized = normalize_line(c.text)
        if is_trivial_line(normalized, min_tokens=min_tokens):
            continue
        candidates.append((c, normalized))
    if not candidates:
        return []

    index = _build_normalized_line_index(repo_root, exclude_files=changed_files)

    matches: List[SiblingCopyMatch] = []
    for c, normalized in candidates:
        siblings = index.get(normalized)
        if not siblings:
            continue
        matches.append(
            SiblingCopyMatch(
                changed_file=c.file,
                changed_line=c.line_no,
                changed_text=c.text,
                normalized=normalized,
                siblings=tuple(sorted(set(siblings), key=lambda s: (s.file, s.line))),
            )
        )
    return matches


# --- CLI ---------------------------------------------------------------------------


def _format_report(matches: List[SiblingCopyMatch]) -> str:
    if not matches:
        return "  ✓ 兄弟コピー検出: 該当なし"
    lines = [f"  ⚠ 兄弟コピー検出 ({len(matches)} 件) — 片側修正の疑い:"]
    for m in matches:
        loc_str = ", ".join(f"{s.file}:{s.line}" for s in m.siblings)
        lines.append(
            f"      {m.changed_file}:{m.changed_line} と同型のコードが他 "
            f"{len(m.siblings)} 箇所に未変更のまま残っている: {loc_str}"
        )
    return "\n".join(lines)


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="diff-scoped 兄弟コピー検出（partial-fix ガード、#210）。"
        " CLI 単体実行用（pre-push 等への自動配線は未実施・スコープ外）。"
    )
    parser.add_argument(
        "diff_args",
        nargs="*",
        default=["HEAD"],
        help="git diff に渡す引数（例: HEAD~1 HEAD / --cached）。省略時は 'HEAD' との差分。",
    )
    parser.add_argument("--repo-root", default=None, help="repo root（省略時はプラグイン自身）")
    parser.add_argument("--min-tokens", type=int, default=DEFAULT_MIN_TOKENS)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else _default_repo_root()
    diff_text = get_diff_text(repo_root, args.diff_args)
    matches = detect_sibling_copies(diff_text, repo_root, min_tokens=args.min_tokens)
    print(_format_report(matches))
    return 1 if matches else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
