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
from datetime import datetime, timezone
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


def normalize_entry_timestamp(entry: Dict[str, Any]) -> None:
    """entry の ``timestamp`` が**有効な ISO8601 文字列**なら aware UTC へ正規化する
    （in-place）。writer ごとに naive ローカル / aware UTC が混在する tz 不統一（#297）を
    吸収するための正規化ロジックの単一ソース。

    **単一 chokepoint ではない**（#297 fixup）: ``append_entry`` はこの関数を書込直前に
    必ず呼ぶが、``optimize_history`` には ``append_entry`` を経由しない直接 writer が
    2つある（``fitness_evolution.record_evolve_diff_decision`` / ``optimize.py`` の
    ``save_history_entry`` — いずれも ``history_file`` を明示指定できる後方互換のため
    自前でファイルを開いて追記する）。これらは自分の書込直前でこの関数を明示的に呼ぶ
    **規約**で揃えている。呼び忘れると正規化を素通りするため、各 writer 側に
    「naive を返す clock でも aware UTC で永続化される」回帰テストを置いて検出する。

    - キー無し・None: 現在時刻の aware UTC を付与する
    - naive（tz 情報無し）文字列: ``datetime.astimezone()`` でシステムローカル時刻として
      解釈してから aware 化する。読み側 ``fleet.queue_verify._parse_iso`` と同じ解釈に
      揃えないと、書き込み時と読み込み時で別の tz 前提を使うことになり 9 時間ずれが
      別の形で再発するため、必ず同じ流儀（値は変えず tzinfo だけ付与）に合わせる
    - aware 文字列: instant は変えず UTC 表記に統一する（表記ゆれの解消のみ）
    - 文字列でない・パース不能: 変更しない（形式契約外のデータを壊さない。この関数は
      「aware UTC を保証する」わけではなく「有効な ISO8601 文字列のみ」正規化する）

    **既存データの限界**: naive で書かれた既存レコードは migration しない（ADR-031 の
    read/write 分離方針）。writer が書いた時点のローカル TZ と reader が動く TZ が
    一致する前提が残るため、別 TZ の機械が過去の naive レコードを読むと 9 時間ずれる
    可能性は本関数だけでは解消されない（新規データの混在を止めるのがこの修正の範囲）。
    """
    ts = entry.get("timestamp")
    if ts is None:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        return
    if not isinstance(ts, str):
        return
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return
    if dt.tzinfo is None:
        dt = dt.astimezone()
    entry["timestamp"] = dt.astimezone(timezone.utc).isoformat()


def append_entry(entry: Dict[str, Any], slug: str) -> None:
    """slug の履歴に 1 レコード追記する（親ディレクトリは自動作成）。

    冪等性（同一 id の二重記録防止）は呼び出し側の責務。本関数は純 append だが、
    ``timestamp`` だけは writer 依存の tz 不統一を防ぐため ``normalize_entry_timestamp``
    で書き込み直前に正規化する（#297）。この経路を通らない直接 writer（history_file を
    自前で開く ``fitness_evolution.py`` / ``optimize.py``）は同関数を各自明示的に呼ぶ
    （単一 chokepoint ではなく規約ベースの統一。詳細は ``normalize_entry_timestamp``）。
    """
    normalize_entry_timestamp(entry)
    path = history_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
