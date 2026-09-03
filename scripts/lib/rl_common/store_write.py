"""store_write.py — write barrier の単一書込ゲート（ADR-049 / #55 Phase 2a）。

全ストア書込を `store_write(store_name, record)` に集約する。各モジュールは保存場所を
一切知らない・触れない（場所決定は `store_name` → `DATA_DIR/<name>` の内部解決のみ）。
`store_registry` 未登録 / 非 active ストアへの書込を **runtime guard** で弾く。

既定モードは **reject**（ADR-049 ②・全 writer 移行 2b 完了後に warn-only から昇格済み）: 未登録 /
非 active ストアへの書込を実行時に StoreWriteError で弾く。全 production caller（hooks 10 +
scripts/lib 6）は登録済み active ストア名のみを使うため、reject は登録外書込（＝バグ）にのみ発火する。
guard は store_registry 不在環境で fail-open する（barrier 不在でも従来挙動を壊さない安全側）。
緊急避難は env `EVOLVE_WRITE_GUARD=warn` で warn へ戻せる（コード変更不要）。

設計（ADR-049）:
- 主防御は runtime guard。静的 AST open 禁止は FP/FN が増えるため不採用（advisory のみ）。
- read（iter_read_data_dirs の union 寛容さ）と write（canonical 1 箇所への厳格さ）は分離。
  共有は `store_registry`（場所定義の単一ソース）だけ。
- 例外口はフラグでなく **別名関数** `store_write_raw()`（決定5）。`allow_unregistered=True` の
  ようなフラグは半年で本番に混入する。別名なら raw を使う diff が静的 advisory に必ず上がる。
- atomic append は `append_jsonl`（flock + 600 perms + silent-on-failure）に委譲（既存 primitive）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional


class StoreWriteError(Exception):
    """未登録 / 非 active ストアへの書込を reject モードで弾いたときに送出する。"""


# 既定は reject（ADR-049 ②・全 writer 移行 2b 完了で warn-only から昇格）。
# 環境変数 EVOLVE_WRITE_GUARD で上書きできる（"warn" | "reject"）。
_VALID_GUARD_MODES = ("warn", "reject")
_DEFAULT_GUARD_MODE = "reject"


def _resolve_guard_mode(explicit: Optional[str]) -> str:
    """明示指定 > env > 既定（reject）。

    不正値は warn へ **de-escalate** する（typo を理由に reject へ昇格させない＝誤爆防止）。
    既定が reject になった後も、無効な guard 値は破壊側でなく安全側（warn・書込継続）に倒す。
    """
    mode = explicit if explicit is not None else os.environ.get(
        "EVOLVE_WRITE_GUARD", _DEFAULT_GUARD_MODE
    )
    return mode if mode in _VALID_GUARD_MODES else "warn"


def _guard_problem(store_name: str) -> Optional[str]:
    """store_name の write 可否を store_registry で照合。問題があれば理由、無ければ None。

    registry に到達できない環境（store_registry が sys.path に無い）では guard を無効化
    （fail-open）。barrier 不在でも挙動は従来通り＝既存テレメトリを壊さない安全側。
    """
    try:
        import store_registry  # scripts/lib/ on sys.path
    except ImportError:
        return None
    decl = store_registry.declaration_for(store_name)
    if decl is None:
        return f"未登録ストア '{store_name}'（store_registry に宣言が無い）"
    status = getattr(decl, "status", "active")
    if status != "active":
        return f"非 active ストア '{store_name}'（status={status}・write は active のみ許可）"
    kind = getattr(decl, "kind", "jsonl")
    if kind == "json":
        return (
            f"kind=json ストア '{store_name}' への store_write（jsonl append 専用）は禁止"
            "（単一 JSON オブジェクトに jsonl 行を追記するとファイルが壊れる。各モジュール "
            "固有の read-modify-write 関数を使う・#399）"
        )
    return None


def guard_problem(store_name: str) -> Optional[str]:
    """store_name の write 可否を返す公開API。判定実装は `_guard_problem` が単一ソース。"""
    return _guard_problem(store_name)


def store_write(
    store_name: str, record: dict, *, guard_mode: Optional[str] = None
) -> None:
    """write barrier の唯一の書込口（ADR-049 / #55）。

    store_name（basename・例 "corrections.jsonl"）を canonical DATA_DIR 配下に解決し、
    store_registry の active 登録を照合してから atomic append する。保存先は呼び出し側が
    一切指定できない（勝手な場所への保存を作らせない＝ユーザー要件）。

    guard_mode:
      - "reject"（既定）: 未登録 / 非 active は StoreWriteError を送出し書込しない。
      - "warn": 未登録 / 非 active でも stderr 警告のみで書込は継続（移行期・緊急避難用）。
    """
    mode = _resolve_guard_mode(guard_mode)
    problem = _guard_problem(store_name)
    if problem is not None:
        msg = f"[evolve-anything:write-barrier] {problem}"
        if mode == "reject":
            raise StoreWriteError(msg)
        print(msg + "（warn-only: 書込は継続）", file=sys.stderr)

    # 専用境界自身も公開 guard_problem を使うため、この拒否は generic writer の
    # 本体だけに置く（#587・2026-09-01 裁定）。
    try:
        import store_registry
    except ImportError:
        store_registry = None
    decl = store_registry.declaration_for(store_name) if store_registry is not None else None
    boundary = getattr(decl, "write_boundary", None)
    if boundary is not None:
        raise StoreWriteError(
            f"[evolve-anything:write-barrier] ストア '{store_name}' は専用の追記境界 "
            f"'{boundary}' を経由する必要があります（generic store_write からの直接書込みは拒否・#587）"
        )

    # DATA_DIR は rl_common パッケージ属性（mock.patch.object(rl_common, "DATA_DIR", ...)
    # 経路の SoT）。遅延 import で call-time の live 値を参照する。
    import rl_common
    from rl_common import append_jsonl

    rl_common.ensure_data_dir()
    append_jsonl(rl_common.DATA_DIR / store_name, record)


def _raw_freeze_problem(filepath: Path) -> Optional[str]:
    """store_write_raw への #379 Step 1 凍結ゲート（未登録 basename の raw 直書き穴・
    外部レビュー指摘）。書込み先が正準 DATA_DIR 配下のときに限り basename を照合する。

    DATA_DIR 外の明示パス（テスト isolation の tmp 等）は対象外＝store_write_raw 本来の
    「場所を尊重する」契約は変えない。凍結解除中・shrink_freeze/store_registry へ
    到達不能な環境は fail-open（`_guard_problem` と同じ流儀）。
    """
    try:
        import shrink_freeze
    except ImportError:
        return None
    if not shrink_freeze.is_frozen():
        return None
    try:
        import rl_common

        target = Path(filepath).resolve()
        canonical = Path(rl_common.DATA_DIR).resolve()
        if not target.is_relative_to(canonical):
            return None
    except (OSError, ValueError):
        return None
    try:
        import store_registry
    except ImportError:
        return None
    basename = Path(filepath).name
    known = set(shrink_freeze.FROZEN_STORES) | set(store_registry.declared_store_names())
    if basename not in known:
        return f"未登録ストア '{basename}'（#379 Step 1 新設凍結中の store_write_raw 直書き）"
    return None


def _raw_boundary_problem(filepath: Path) -> Optional[str]:
    """正準 DATA_DIR 配下の raw 書込が専用境界を迂回しないか照合する。

    basename が宣言と一致しない場合も、既存ファイルなら samefile で照合する。対象が
    未作成で samefile 不能なら fail-open とし、既存の raw 例外口の用途を狭めない。
    """
    try:
        import rl_common

        target = Path(filepath).resolve()
        canonical = Path(rl_common.DATA_DIR).resolve()
        if not target.is_relative_to(canonical):
            return None
    except (OSError, ValueError):
        return None
    try:
        import store_registry
    except ImportError:
        return None
    declaration = store_registry.declaration_for(Path(filepath).name)
    if declaration is None:
        for declared_name in store_registry.declared_store_names():
            candidate = store_registry.declaration_for(declared_name)
            if getattr(candidate, "write_boundary", None) is None:
                continue
            try:
                if Path(filepath).samefile(canonical / declared_name):
                    declaration = candidate
                    break
            except OSError:
                continue
    boundary = getattr(declaration, "write_boundary", None)
    if boundary is None:
        return None
    return (
        f"ストア '{Path(filepath).name}' は専用の追記境界 '{boundary}' を経由する必要があります"
        "（store_write_raw からの直接書込みは拒否・#597）"
    )


def store_write_raw(
    filepath: Path, record: dict, *, guard_mode: Optional[str] = None
) -> None:
    """明示パス指定の例外口（ADR-049 決定5）。通常の registry guard を通さない直接書込。

    テスト / 特殊ケース用。フラグでなく別名関数にすることで、raw を使う diff が
    静的 advisory（store_write 非経由の DATA_DIR 参照）の検出対象に上がる。

    #379 Step 1 凍結ゲート（修正4）: 書込み先が正準 DATA_DIR 配下のときに限り、
    basename を shrink_freeze.FROZEN_STORES ∪ store_registry 宣言名と照合し、未知なら
    guard_mode（既定 reject、`store_write` と同じ env `EVOLVE_WRITE_GUARD` 解決）に従う。
    """
    mode = _resolve_guard_mode(guard_mode)
    problem = _raw_freeze_problem(filepath)
    if problem is not None:
        msg = f"[evolve-anything:write-barrier] {problem}"
        if mode == "reject":
            raise StoreWriteError(msg)
        print(msg + "（warn-only: 書込は継続）", file=sys.stderr)

    boundary_problem = _raw_boundary_problem(filepath)
    if boundary_problem is not None:
        raise StoreWriteError(f"[evolve-anything:write-barrier] {boundary_problem}")

    from rl_common import append_jsonl

    append_jsonl(filepath, record)
