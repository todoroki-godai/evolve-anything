"""ADR-054 Phase 0（B1）: SessionStart 通知の共有プリミティブ。

``NotificationItem`` と、daily runner が書く一回性スナップショット JSON の
absent/corrupt/ok 分類ヘルパーを提供する。収集関数（``collectors.py``）・
merge ロジック（``merge.py``）の両方から参照される最小の共有モジュール。
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class NotificationItem:
    """SessionStart 通知1系統分の収集結果（ADR-054 Phase 0 §6.1）。

    収集関数（``_build_*_output``）は print しない。``handle_session_start`` が全系統分を
    集めてから merge・print し、print 成功後にだけ ``commit`` を呼ぶ（ack 方式・§5）。

    - ``tier``: ① 内で絶対に落とさない（1）か、予算超過時に落としてよい（2）か（量の軸）。
    - ``text``: 発火系統が1件のみのときに使うフル文。
    - ``digest``: 発火系統が2件以上のときに使う短縮形（§4.2）。pending_trigger は
      ``digest == text``（完全不変・digest化免除）。icebox レーン1は独自の短縮フレーム。
    - ``commit``: 印字成功後にだけ呼ぶ副作用（ack）。使うのは spec_drift・pending_trigger・
      icebox レーン1 の3系統のみ。他は ``None``。
    - ``tail_link``: このアイテムが発火していれば digest 行末尾に `→ /evolve-anything:queue
      で開始` を付与する対象か（§4.2'）。
    - ``decision_text``: 利用者に判断を求める通知の本文（#503 §3.0）。非 ``None`` の item は
      発火件数・予算にかかわらず digest 化されない・overflow に落ちない（``digest`` の意味は
      変えない・digest 集合からは外れる）。判断を求めない通知は ``None`` のまま。
    """

    label: str
    tier: int
    text: str
    digest: str
    commit: "Callable[[], None] | None" = None
    tail_link: bool = False
    decision_text: "str | None" = None


def _classify_daily_snapshot_file(path: Path) -> str:
    """daily runner が書く一回性スナップショット JSON の状態を分類する（§4.6/§5.4）。

    「ファイル不在＝沈黙」と「ファイル存在するが読めない＝破損＝Tier1 health notice」を
    明示的に分離する。対象 read 関数のシグネチャは変更しない — ここは呼び出し側
    （collectors.py）だけが行う軽量な事前分類。
    """
    if not path.exists():
        return "absent"
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return "ok"
    except (OSError, json.JSONDecodeError, ValueError):
        return "corrupt"
