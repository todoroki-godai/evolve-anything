"""成功した最適化パターンを JSONL で永続化するモジュール。

データファイル: DATA_DIR / evolution_memory.jsonl
  - DATA_DIR は CLAUDE_PLUGIN_DATA 環境変数で上書き可能
    (未設定時: ~/.claude/evolve-anything/)

公開関数:
  save_winner  -- 成功パターンを追記（max 1000件ローテーション）
  load_patterns -- パターンを読み込み（skill_name フィルタ + limit + 新しい順）
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── DATA_DIR（テスト時は monkeypatch.setattr で差し替え）────────────────────
_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR: Path = (
    Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
)

_MEMORY_FILENAME = "evolution_memory.jsonl"
_MAX_RECORDS = 1000
_MAX_PATCH_SUMMARY_LEN = 200


def _memory_file() -> Path:
    """現在の DATA_DIR に基づくメモリファイルパスを返す。

    DATA_DIR がモジュール変数として参照されるため、monkeypatch.setattr による
    差し替えが即座に反映されるよう、呼び出し時に評価する。
    """
    return DATA_DIR / _MEMORY_FILENAME


def _ensure_dir() -> None:
    """データディレクトリが存在しない場合に作成する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _read_records_from(path: Path) -> List[Dict[str, Any]]:
    """指定 path の JSONL を全件読み込む。ファイル不在・破損行はスキップ。"""
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except (OSError, PermissionError) as e:
        print(f"[evolution_memory] read failed: {e}", file=sys.stderr)
    return records


def _read_all_records() -> List[Dict[str, Any]]:
    """canonical（現 DATA_DIR）の JSONL を全件読み込む。

    save_winner のローテーション用 reader。write 先（canonical）だけを対象にする
    （ADR-049: write 側 self-resolver は意図的に維持。union read で legacy を巻き込んで
    canonical へ書き戻す＝暗黙の物理 merge を起こさないため、write path は canonical 固定）。
    """
    return _read_records_from(_memory_file())


def _read_all_records_union() -> List[Dict[str, Any]]:
    """canonical + legacy/plugins-data の evolution_memory.jsonl を cross-dir union read する（#45）。

    DATA_DIR 断片化（rename rl-anything→evolve-anything / plugins-data hook split）の移行期、
    canonical だけ読むと legacy にのみ残った成功パターンを取り逃す（pipeline_eval の
    convergence_cycles が 0 になる）。``rl_common.iter_read_data_dirs`` が DATA_DIR の親から
    候補 dir を導出し、各候補の ``evolution_memory.jsonl`` を合算する。自然な PK が無いため
    レコード全体（ts/skill/strategy/scores/patch_summary）の同一性で dedup する（候補列は
    canonical 先頭 → 先勝ち）。read 専用で write（save_winner）には影響しない。
    """
    from rl_common import iter_read_data_dirs

    seen: set = set()
    out: List[Dict[str, Any]] = []
    for d in iter_read_data_dirs(DATA_DIR):
        for rec in _read_records_from(d / _MEMORY_FILENAME):
            key = (
                rec.get("ts"),
                rec.get("skill_name"),
                rec.get("strategy"),
                rec.get("score_before"),
                rec.get("score_after"),
                rec.get("patch_summary"),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(rec)
    return out


def _write_all_records(records: List[Dict[str, Any]]) -> None:
    """レコードリストを JSONL ファイルに書き戻す。"""
    path = _memory_file()
    try:
        with open(path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        try:
            path.chmod(0o600)
        except OSError as e:
            print(f"[evolution_memory] chmod warning: {e}", file=sys.stderr)
    except (OSError, PermissionError) as e:
        print(f"[evolution_memory] write failed: {e}", file=sys.stderr)


def save_winner(
    skill_name: str,
    strategy: str,
    score_before: float,
    score_after: float,
    patch_summary: str,
) -> None:
    """成功パターンを JSONL に追記する。max 1000件でローテーション。

    Args:
        skill_name:    最適化対象のスキル名。
        strategy:      最適化戦略 ("error_guided" | "llm_improve")。
        score_before:  最適化前スコア。
        score_after:   最適化後スコア。
        patch_summary: 変更内容の要約（max 200文字。超過分は切り詰め）。
    """
    _ensure_dir()

    # patch_summary を 200 文字に切り詰め
    if len(patch_summary) > _MAX_PATCH_SUMMARY_LEN:
        patch_summary = patch_summary[:_MAX_PATCH_SUMMARY_LEN]

    record: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "skill_name": skill_name,
        "strategy": strategy,
        "score_before": score_before,
        "score_after": score_after,
        "patch_summary": patch_summary,
    }

    # 既存レコードを読んで末尾に追加し、超過分を古いものから削除
    records = _read_all_records()
    records.append(record)

    if len(records) > _MAX_RECORDS:
        records = records[-_MAX_RECORDS:]

    _write_all_records(records)


def load_patterns(
    skill_name: Optional[str] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """パターンを読み込む。skill_name 指定時はフィルタ。新しい順に limit 件返す。

    Args:
        skill_name: 指定時、該当スキルのみ返す。None のとき全スキル対象。
        limit:      最大返却件数（デフォルト 10）。

    Returns:
        新しい順（ts 降順）で最大 limit 件のレコードリスト。

    read 層 union（#45）: canonical だけでなく legacy/plugins-data も読み、rename 移行で
    取り残された過去パターンを欠落させない（write 側 save_winner は canonical 固定・ADR-049）。
    """
    records = _read_all_records_union()

    if skill_name is not None:
        records = [r for r in records if r.get("skill_name") == skill_name]

    # 新しい順にソート（ts は ISO8601 文字列なので文字列比較で降順ソート可）
    records.sort(key=lambda r: r.get("ts", ""), reverse=True)

    return records[:limit]
