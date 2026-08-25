"""live_checkout — 共有 checkout がユーザー実行環境として「生きている」かの判定（#548）。

正典: ``.claude/rules/live-checkout-guard.md``。判定ロジックの単一ソースはここ1箇所のみ
（hook / audit の両呼出側はここを import して結果を表示に変換するだけ）。

## 何を検出するか
守る対象は「ユーザー実行環境がレビュー未了のコードを実行している状態が、人間に気づかれない
まま継続すること」（自分たちの運用ミスのみを脅威に数える。悪意ある第三者は対象外）。

## 3者照合（実行木の取り違え防止）
``Path(__file__)`` 単独は判定の根拠にしない — cache へのコピー・symlink・``PYTHONPATH``
差し替え・worktree の wrapper が clean な共有 checkout のモジュールを import する配置では、
実際に動いている木とは別の木を指しうる。``check()`` は
①呼出元の ``__file__``（``caller_file``）②本モジュール自身の ``__file__``
③呼出側が明示的に渡す「自分が属すると期待する root」（``expected_root``）の3つが
同一の木（``.claude-plugin/plugin.json`` を持つ最も近い祖先ディレクトリ）に属するかを照合する。
1つでも別の木なら「判定不能」を返す。

## 危険判定（OR）
①既定ブランチでない ②tracked file が dirty ③解決済み既定ブランチに対して ahead
（``origin/<resolved-default>`` — ``origin/main`` に固定しない。``master``/``trunk`` の repo で
誤判定するため）。既定ブランチが確定できないときは「安全」と扱わず「判定不能」を返す
（``main`` を仮定しない）。

## registry は副次情報
``~/.claude/plugins/known_marketplaces.json`` の値は照合用の副次情報として読み、実行時 root
と食い違ったら別種の警告（``RegistryCheck``）として返す。判定の一次情報にはしない
（primary の ``status`` は git の結果だけで決まる）。

状態は永続化しない（``#379`` の新設凍結を守る）。read 時に毎回導出する。
決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_PLUGIN_MARKER = Path(".claude-plugin") / "plugin.json"
_MARKETPLACE_REGISTRY = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"

# テスト専用 override（spec_trigger._DATA_DIR_OVERRIDE と同型の慣習）。
# 本モジュール自身の __file__ は実インストール先を指すため、check() の module_root 解決を
# tmp fixture repo に向けさせたいテストはこれを monkeypatch する。production では常に None。
_MODULE_ROOT_OVERRIDE: "Path | None" = None


@dataclass
class RegistryCheck:
    """marketplace registry 照合の副次結果。"""

    status: str  # "skipped" | "ok" | "mismatch" | "unreadable"
    detail: "str | None" = None


@dataclass
class LiveCheckoutResult:
    """``check()`` の戻り値。"""

    status: str  # "danger" | "safe" | "unknown"
    reason: "str | None" = None
    root: "Path | None" = None
    branch: "str | None" = None
    default_branch: "str | None" = None
    dirty_count: int = 0
    ahead_count: int = 0
    registry: "RegistryCheck | None" = None


def _find_plugin_root(start) -> "Path | None":
    """``start``（file or dir）から親方向に ``.claude-plugin/plugin.json`` を探索する。

    見つかった directory を返す。見つからなければ None（判定不能の材料）。
    ``restore_state.py`` の icebox plugin_self 判定と同じマーカーファイル規約を再利用する。
    """
    try:
        p = Path(start).resolve()
    except OSError:
        return None
    if p.is_file():
        p = p.parent
    for candidate in (p, *p.parents):
        if (candidate / _PLUGIN_MARKER).exists():
            return candidate
    return None


def _module_root() -> "Path | None":
    if _MODULE_ROOT_OVERRIDE is not None:
        try:
            return Path(_MODULE_ROOT_OVERRIDE).resolve()
        except OSError:
            return None
    return _find_plugin_root(__file__)


def _git(cwd: Path, *args: str) -> "tuple[bool, str]":
    """git を ``cwd`` で実行する。``(成功したか, stdout or エラー理由)`` を返す。例外を投げない。"""
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError) as e:
        return False, f"git 実行不能: {e}"
    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "").strip() or f"git {' '.join(args)} failed"
        return False, detail
    return True, out.stdout


def _resolve_default_branch(root: Path) -> "tuple[str | None, str | None]":
    """``origin/HEAD`` から既定ブランチ名を解決する。``(branch_name, error_reason)`` を返す。

    ``main`` を仮定しない（``master``/``trunk`` の repo で誤判定するため・rule 正典）。
    """
    ok, out = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if not ok or not out.strip():
        return None, f"origin/HEAD 未解決（削除済み or 未 fetch or origin 不在）: {out.strip()}"
    ref = out.strip()  # 例: "origin/main"
    if "/" not in ref:
        return None, f"origin/HEAD の形式が不正: {ref!r}"
    return ref.split("/", 1)[1], None


def _check_registry(root: Path) -> RegistryCheck:
    """``known_marketplaces.json`` を副次情報として照合する（判定の一次情報にしない）。

    JSON 破損は「判定不能・理由」を registry 面にだけ立てる（primary 判定は git のみで完結する）。
    """
    if not _MARKETPLACE_REGISTRY.exists():
        return RegistryCheck(status="skipped", detail="registry 不在")
    try:
        data = json.loads(_MARKETPLACE_REGISTRY.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return RegistryCheck(status="unreadable", detail=f"registry JSON 破損: {e}")
    if not isinstance(data, dict):
        return RegistryCheck(status="unreadable", detail="registry JSON の形式が不正（dict でない）")
    for name, entry in data.items():
        install = entry.get("installLocation") if isinstance(entry, dict) else None
        if not install:
            continue
        try:
            if Path(install).resolve() == root:
                return RegistryCheck(status="ok", detail=name)
        except OSError:
            continue
    return RegistryCheck(status="mismatch", detail=f"registry に一致する installLocation が無い（root={root}）")


def check(caller_file, expected_root: "Optional[str]" = None) -> LiveCheckoutResult:
    """3者照合 → OR 判定（非既定ブランチ／dirty／ahead）を行う（本モジュールの唯一の公開判定 API）。

    Args:
        caller_file: 呼出元モジュールの ``__file__``。
        expected_root: 呼出側が「自分が属すると期待する plugin root」（渡された root）。
            未指定（``None``）なら③の照合をスキップする（明示できない呼出文脈向けの緩和。
            ①②の照合は常に行う）。
    """
    caller_root = _find_plugin_root(caller_file)
    module_root = _module_root()

    if caller_root is None or module_root is None:
        return LiveCheckoutResult(
            status="unknown",
            reason=(
                "plugin root マーカー（.claude-plugin/plugin.json）が見つからない"
                f"（caller={caller_root}, module={module_root}）"
            ),
        )

    if caller_root != module_root:
        return LiveCheckoutResult(
            status="unknown",
            reason=f"呼出元とモジュールが別の木を指している（caller={caller_root}, module={module_root}）",
        )

    if expected_root is not None:
        try:
            passed_root = Path(expected_root).resolve()
        except OSError:
            passed_root = None
        if passed_root is not None and passed_root != module_root:
            return LiveCheckoutResult(
                status="unknown",
                reason=f"渡された root が実行木と食い違う（渡された={passed_root}, 実行={module_root}）",
                root=module_root,
            )

    root = module_root
    registry = _check_registry(root)

    ok, branch_or_err = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if not ok:
        return LiveCheckoutResult(
            status="unknown", reason=f"HEAD 解決不能: {branch_or_err}", root=root, registry=registry,
        )
    branch = branch_or_err.strip()

    default_branch, err = _resolve_default_branch(root)
    if default_branch is None:
        return LiveCheckoutResult(
            status="unknown", reason=err, root=root, branch=branch, registry=registry,
        )

    ok, dirty_out = _git(root, "status", "--porcelain", "-uno")
    if not ok:
        return LiveCheckoutResult(
            status="unknown", reason=f"status 取得不能: {dirty_out}",
            root=root, branch=branch, default_branch=default_branch, registry=registry,
        )
    dirty_count = len([ln for ln in dirty_out.splitlines() if ln.strip()])

    ok, ahead_out = _git(
        root, "rev-list", "--left-right", "--count", f"origin/{default_branch}...HEAD",
    )
    if not ok:
        return LiveCheckoutResult(
            status="unknown", reason=f"ahead 判定不能: {ahead_out}",
            root=root, branch=branch, default_branch=default_branch,
            dirty_count=dirty_count, registry=registry,
        )
    parts = ahead_out.split()
    ahead_count = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0

    dangerous = (branch != default_branch) or dirty_count > 0 or ahead_count > 0

    return LiveCheckoutResult(
        status="danger" if dangerous else "safe",
        root=root, branch=branch, default_branch=default_branch,
        dirty_count=dirty_count, ahead_count=ahead_count, registry=registry,
    )
