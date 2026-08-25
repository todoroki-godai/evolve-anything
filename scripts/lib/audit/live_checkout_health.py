"""#548: audit へ live_checkout の健全性を surface する（fail-visible）。

判定ロジックの単一ソースは ``live_checkout.py``（``scripts/lib/``）。ここは audit レポート
向けの薄い adapter で、hook 側（``session_notify/live_checkout_notice.py``）とは**独立に**
import 失敗・実行失敗を捕捉する。同じ ``live_checkout`` モジュールを両方が import する以上、
構文エラーは共通原因障害になるため、捕捉は呼出側それぞれに置く（rule 正典）。

``bin/evolve-audit`` だけに出すのは不足（人が実行しなければ無音）。この section は
SessionStart hook の代替ではなく補完 — 手動で audit を回したときにも見える経路を確保する。
"""
from typing import List, Optional


def build_live_checkout_health_section() -> Optional[List[str]]:
    """common section: import/実行失敗は health notice、danger/unknown は要対応として出す。

    plugin_self（evolve-anything 本体の checkout）に関する判定であり、audit 対象の
    ``project_dir``（audit されているユーザー PJ）とは独立。常に評価する。
    """
    try:
        import live_checkout
    except Exception as e:
        return [
            "## live_checkout health（#548）",
            "",
            f"⚠ live_checkout モジュールの import に失敗しました: {e}",
            "共有 checkout の生存確認ができていません。`scripts/lib/live_checkout.py` の構文を確認してください。",
            "",
        ]

    try:
        from plugin_root import PLUGIN_ROOT

        result = live_checkout.check(caller_file=__file__, expected_root=str(PLUGIN_ROOT))
    except Exception as e:
        return [
            "## live_checkout health（#548）",
            "",
            f"⚠ live_checkout の判定実行に失敗しました: {e}",
            "共有 checkout の生存確認ができていません。",
            "",
        ]

    if result.status == "danger":
        reasons = []
        if result.branch != result.default_branch:
            reasons.append(f"branch={result.branch}（既定={result.default_branch}）")
        if result.dirty_count:
            reasons.append(f"dirty {result.dirty_count} 件")
        if result.ahead_count:
            reasons.append(f"ahead {result.ahead_count} 件")
        return [
            "## live_checkout health（#548）",
            "",
            f"🔴 共有 checkout がユーザー実行環境として危険な状態です（{'、'.join(reasons)}）。",
            f"→ `git -C {result.root} checkout {result.default_branch}` で既定ブランチへ戻してください"
            "（dirty がある場合は先に退避）。",
            "",
        ]

    if result.status == "unknown":
        return [
            "## live_checkout health（#548）",
            "",
            f"ℹ 共有 checkout の安全性を判定できません: {result.reason}",
            "",
        ]

    registry = result.registry
    if registry is not None and registry.status in ("mismatch", "unreadable"):
        return [
            "## live_checkout health（#548）",
            "",
            f"ℹ plugin marketplace registry の照合ができません（副次警告）: {registry.detail}",
            "",
        ]

    return None  # safe かつ registry ok → 沈黙（silence は評価済みの結果）
