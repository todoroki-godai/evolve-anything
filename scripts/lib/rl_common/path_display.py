"""外部提案（GitHub issue 等）に載せる絶対パスをホーム相対表示へ畳む単一ソース。

Path.home() 配下の絶対パスをそのまま recommendation / issue 本文へ埋め込むと、
`/Users/<ユーザー名>/...` のような個人特定可能なローカルパスが外部流出する
（グローバル rule `no-personal-dir-in-external-artifacts`）。

もとは ``rule_violation_lane._display_hook_path``（#479 Must2）としてのみ存在した。
discover の instruction_violations 検出（#467 名前空間解決フォローアップ）で
プラグインキャッシュ配下の絶対パスを同じ理由で畳む必要が生じ、判定ロジックの
重複実装（検出器間の矛盾の温床）を避けるためここへ抽出した。
"""
from pathlib import Path


def home_relative_display(path: Path) -> str:
    """絶対パスを表示用に畳む。

    Path.home() 配下であれば ``~/...`` 形式に、そうでなければ末尾のファイル名
    のみを返す（絶対パスをそのまま出さない安全側）。
    """
    home = Path.home()
    try:
        rel = path.relative_to(home)
    except ValueError:
        return path.name
    return f"~/{rel}"
