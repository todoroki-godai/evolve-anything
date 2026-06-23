"""optimize_history_store — accept/reject 履歴の正準ストア（ADR-031）。

optimize / evolve-loop / evolve-diff の accept/reject 決定ログ（fitness calibration の母集団）を
DATA_DIR 配下の project スコープ JSONL に集約する単一ソース。

背景: 従来は読み書きが 3 経路に分裂（split-brain）していた:
  - optimize / evolve-diff → <PLUGIN_ROOT>/skills/.../generations/history.jsonl（更新でリセット）
  - run_loop            → <cwd>/.evolve-loop/history.jsonl（readers が読まない孤立）
  - readers             → plugin generations を読む
このモジュールに集約し、保存先を永続 DATA_DIR の `optimize_history/<slug>.jsonl` に一本化する。

slug は worktree 安全に解決する（`git --git-common-dir` 経由）。素直な
`git rev-parse --show-toplevel` の basename は worktree 内で worktree 名を返し、
本体 repo と食い違って二次 split-brain を生むため使わない。

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
HISTORY_ROOT = DATA_DIR / "optimize_history"

# git repo 外（slug 解決不能）の保全先。calibration 母集団からは除外される。
UNATTRIBUTED_SLUG = "_unattributed"

# ファイル名に使えない文字を _ へ。`Path.name` 由来なので traversal は構造的に不可だが、
# world_context（同リリースの per-slug 化）と同じサニタイズで一貫性と防御を揃える。
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_slug(slug: str) -> str:
    # 先頭/末尾の . _ は剥がさない（UNATTRIBUTED_SLUG="_unattributed" を保つため）。
    cleaned = _SLUG_UNSAFE.sub("_", slug)
    return cleaned or UNATTRIBUTED_SLUG


def resolve_slug(cwd: Optional[Path] = None) -> str:
    """current（または指定 cwd の）project slug を返す。

    worktree 安全: `git rev-parse --git-common-dir` で本体 repo の .git を取り、
    その親ディレクトリ名を slug とする。worktree から呼んでも本体 slug に正規化される。
    git repo 外なら UNATTRIBUTED_SLUG。

    #492: 導出ロジックは ``pj_slug.resolve_pj_slug`` に単一ソース化した。本関数は
    後方互換のための thin wrapper（既存呼び出し元の一斉書き換えを避ける段階移行）。
    """
    from pj_slug import resolve_pj_slug

    return resolve_pj_slug(cwd)


def history_path(slug: str) -> Path:
    """slug の履歴ファイルパスを返す（HISTORY_ROOT/<slug>.jsonl）。

    slug はファイル名構築の chokepoint でサニタイズする（resolve_slug 由来でも
    明示渡しでも一律に適用）。
    """
    return HISTORY_ROOT / f"{_sanitize_slug(slug)}.jsonl"


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """1 ファイルを読み込む。未存在なら []。空行・壊れた JSON 行はスキップ。"""
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_history(slug: str) -> List[Dict[str, Any]]:
    """slug の履歴を canonical + legacy/plugins-data から cross-dir union read する（#45）。

    DATA_DIR 断片化（rename rl-anything→evolve-anything / plugins-data hook split）の移行期、
    canonical だけ読むと legacy にのみ残った accept/reject 履歴（fitness calibration の母集団）
    を取り逃す。``rl_common.iter_read_data_dirs`` が ``HISTORY_ROOT.parent``（= DATA_DIR）の親
    から候補 dir を導出し、各候補の ``optimize_history/<slug>.jsonl`` を読んで合算する。
    候補は **canonical 先頭**なので、同一 ``id`` は canonical を優先して dedup する
    （``id`` を持たないレコードは安全に dedup できないため全件保持）。

    **読み取り専用**: ``append_entry``（write）は canonical 固定のまま（ADR-049: write 側
    self-resolver は意図的に維持）。本関数は read 側だけを union 化する。tmp canonical を
    渡すテストでは兄弟 dir が存在せず canonical のみを読む（hermetic）。
    """
    from rl_common import iter_read_data_dirs

    safe = _sanitize_slug(slug)
    by_id: Dict[Any, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for d in iter_read_data_dirs(HISTORY_ROOT.parent):
        for rec in _read_jsonl(d / "optimize_history" / f"{safe}.jsonl"):
            rid = rec.get("id")
            if rid is not None:
                if rid in by_id:
                    continue  # 候補列は canonical 先頭 → 先勝ち（canonical 優先）
                by_id[rid] = rec
            out.append(rec)
    return out


def append_entry(entry: Dict[str, Any], slug: str) -> None:
    """slug の履歴に 1 レコード追記する（親ディレクトリは自動作成）。

    冪等性（同一 id の二重記録防止）は呼び出し側の責務。本関数は純 append。
    """
    path = history_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
