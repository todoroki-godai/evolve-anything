"""#548: 共有 checkout がユーザー実行環境として危険な状態かを SessionStart で通知する。

判定ロジックの単一ソースは ``live_checkout.py``（``scripts/lib/``）。本モジュールはその
結果を ``NotificationItem`` へ変換する薄い adapter のみを持つ（決定と表示の分離、他
``_build_*_output`` と同型）。import/実行失敗は本関数が独立に捕捉する（rule 正典:
hook と audit の各呼出側が独立に捕捉。同じ ``live_checkout`` モジュールを両方が import する
以上、構文エラーは共通原因障害になるため — 捕捉を呼出側に置く）。

判定不能は危険警告と同じ強さで出さない（tier2＝予算超過時に digest 化・落としてよい）。
"""
import os
import sys
from pathlib import Path

from .model import NotificationItem


def _build_live_checkout_output() -> "NotificationItem | None":
    # 実環境ガード（他 _build_*_output と同型）: hook 文脈（CC install レイアウト）
    # でなければ判定しない。pytest 実行時は CLAUDE_PLUGIN_DATA が未設定のため、
    # このガードが無いと本チェッカー自身の worktree（開発中で非既定ブランチ／dirty
    # が常態）を実 probe してしまい、無関係なテストの stdout を汚染する。
    try:
        import data_dir_migration as _ddm  # 遅延 import（patch 追従・他 build 関数と同型）

        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if not env or not _ddm.is_cc_install_layout(Path(env)):
            return None
    except Exception as e:
        print(f"[evolve-anything:restore_state] live_checkout env-guard error: {e}", file=sys.stderr)
        return None

    try:
        import live_checkout as _lc  # 遅延 import（patch 追従・他 build 関数と同型）
    except Exception as e:
        text = (
            "[evolve-anything] live_checkout モジュールの import に失敗しました"
            f"（#548, {e}）。共有 checkout の生存確認ができていません。"
        )
        print(f"[evolve-anything:restore_state] live_checkout import error: {e}", file=sys.stderr)
        return NotificationItem(
            label="live_checkout", tier=1, text=text, digest="live_checkoutモジュール障害",
        )

    try:
        expected_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or None
        result = _lc.check(caller_file=__file__, expected_root=expected_root)
    except Exception as e:
        text = (
            f"[evolve-anything] live_checkout の判定実行に失敗しました（#548, {e}）。"
            "共有 checkout の生存確認ができていません。"
        )
        print(f"[evolve-anything:restore_state] live_checkout run error: {e}", file=sys.stderr)
        return NotificationItem(
            label="live_checkout", tier=1, text=text, digest="live_checkout実行障害",
        )

    if result.status == "danger":
        reasons = []
        if result.branch != result.default_branch:
            reasons.append(f"branch={result.branch}（既定={result.default_branch}）")
        if result.dirty_count:
            reasons.append(f"dirty {result.dirty_count} 件")
        if result.ahead_count:
            reasons.append(f"ahead {result.ahead_count} 件")
        recover = (
            f"`git -C {result.root} checkout {result.default_branch}`"
            if result.root and result.default_branch else "既定ブランチへの復帰"
        )
        text = (
            "[evolve-anything] 共有 checkout がユーザー実行環境として危険な状態です"
            f"（#548: {'、'.join(reasons)}）。dirty がある場合は先に退避してから "
            f"{recover} で戻してください。"
        )
        digest = f"共有checkout危険（{'、'.join(reasons)}）"
        return NotificationItem(label="live_checkout", tier=1, text=text, digest=digest)

    if result.status == "unknown":
        text = f"[evolve-anything] 共有 checkout の安全性を判定できません（#548: {result.reason}）。"
        return NotificationItem(
            label="live_checkout", tier=2, text=text, digest="共有checkout判定不能",
        )

    registry = result.registry
    if registry is not None and registry.status in ("mismatch", "unreadable"):
        text = (
            "[evolve-anything] plugin marketplace registry の照合ができません"
            f"（#548 副次警告: {registry.detail}）。"
        )
        return NotificationItem(
            label="live_checkout", tier=2, text=text, digest="registry照合不能",
        )

    return None  # safe かつ registry ok → 沈黙
