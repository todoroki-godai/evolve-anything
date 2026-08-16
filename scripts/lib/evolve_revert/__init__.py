"""evolve_revert — 1コマンド revert の apply engine（#402 PR-2 段階3）。

設計正典: ``design_402_pr2_v2.md`` §2（apply 手順）。復元・冪等・conflict・メタデータ
契約（mode/uid-gid/xattr/flags/hardlink/ACL）のライブラリ実装。CLI
（``bin/evolve-revert`` / ``evolve_revert_cli.py``）は段階4 の対象で本パッケージには
含めない——ここは段階4 の CLI が薄く呼べる「引数で受ける純粋寄りの API」にする
（``bin/evolve-tier`` → ``tier_policy_cli.py`` の分離が雛形）。

## パッケージ構成（M-D: file-size-budget 500行検討/800行必須を最初から避ける設計）

| module | 内容 |
|--------|------|
| `_entry.py` | entry 検索（raw × alias・§2 手順1） |
| `_target.py` | 対象パス解決 + 安全検査（scope 別 root 解決・lstat・containment・hardlink・§2 手順2） |
| `_metadata.py` | メタデータ契約（mode/uid-gid/xattr/flags/hardlink/ACL の検出・比較・override 判定・§2 手順4） |
| `_apply.py` | apply engine 本体: entry 検索 → 対象解決 → 3分岐（正常系/冪等/conflict）→ 復元 → 再検証 → revert イベント追記（§2 手順3-5） |
| `_dump.py` | `--dump-before`（atomic no-clobber publish・§2 手順3） |
| `_render.py` | 利用者に見えるメッセージの生成（conflict 次アクション・hardlink 拒否・メタデータ拒否・diff 向きラベル・N1 の apply 完了メッセージ） |
| `_availability.py` | 段階4: board 表示用の listing 時点 revert 可否判定（§3・理由コード3種 + 日本語ラベル） |

決定論・LLM 非依存。
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from ._entry import EntryLookup, find_entry  # noqa: E402,F401
from ._target import TargetResolution, resolve_target  # noqa: E402,F401
from ._metadata import (  # noqa: E402,F401
    LossReport,
    MetadataSnapshot,
    XattrProbe,
    classify_losses,
    detect_drift,
    preview_losses,
    snapshot_from_fd,
    snapshot_from_path,
)
from ._render import (  # noqa: E402,F401
    BRANCH_LABELS,
    build_diff_summary,
    render_apply_success,
    render_conflict_message,
    render_dry_run_header,
    render_dry_run_preview,
    render_hardlink_rejection,
    render_metadata_loss_rejection,
)
from ._dump import DumpResult, dump_before  # noqa: E402,F401
from ._apply import ApplyResult, apply_revert, detect_subsequent_change  # noqa: E402,F401
from ._availability import (  # noqa: E402,F401
    REASON_BEFORE_TOO_LARGE,
    REASON_LABELS,
    REASON_LANE_UNSUPPORTED,
    REASON_PRE_EXTENSION,
    compute_revert_availability,
)
