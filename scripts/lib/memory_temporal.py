#!/usr/bin/env python3
"""memory ファイルの temporal frontmatter ヘルパー。

APEX-MEM インスパイアの A++ 設計:
- valid_from / superseded_at / decay_days / source_correction_ids の読み取り
- 既存 frontmatter.parse_frontmatter() を流用（後方互換保証済み）
- frontmatter なしの既存ファイルは例外なくデフォルト値を返す

# TODO(APEX-MEM-C): Event-Centric Rewrite への移行時、このモジュールを
# 6ノード型 JSONL グラフ（Rule/Skill/Correction/Session/Pitfall/Memory）の
# Memory ノードパーサーに置き換える。
# 参照: issue #13
"""
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from frontmatter import parse_frontmatter

TEMPORAL_DEFAULTS: dict[str, Any] = {
    "valid_from": None,
    "superseded_at": None,
    "decay_days": None,
    "source_correction_ids": [],
    "update_count": 0,
    "importance_score": 0.5,
    "last_reinforced_at": None,
}

# importance ラベル → base スコアのマッピング
_IMPORTANCE_BASE: dict[str, float] = {
    "high": 0.8,
    "medium": 0.5,
    "low": 0.2,
}


def parse_memory_temporal(filepath: Path) -> dict[str, Any]:
    """memory ファイルから temporal フィールドを読み取る。

    frontmatter がないファイル（既存ファイル）は TEMPORAL_DEFAULTS を返す。
    例外は発生しない。
    """
    fm = parse_frontmatter(filepath)
    result = dict(TEMPORAL_DEFAULTS)
    result["valid_from"] = fm.get("valid_from", None)
    result["superseded_at"] = fm.get("superseded_at", None)
    decay = fm.get("decay_days", None)
    # decay_days: 0 以下は null と同じ扱い（負値・ゼロは期限なし）
    result["decay_days"] = decay if (isinstance(decay, int) and decay > 0) else None
    ids = fm.get("source_correction_ids", [])
    result["source_correction_ids"] = ids if isinstance(ids, list) else []
    update_count = fm.get("update_count", 0)
    result["update_count"] = (
        update_count
        if (isinstance(update_count, int) and not isinstance(update_count, bool) and update_count >= 0)
        else 0
    )
    return result


def is_stale(temporal: dict[str, Any]) -> bool:
    """decay_days を超過しているか判定する。

    - decay_days が None or 0 → 期限なし → False
    - valid_from がない → 判定不能 → False
    """
    decay_days = temporal.get("decay_days")
    if not decay_days:
        return False

    valid_from_str = temporal.get("valid_from")
    if not valid_from_str:
        return False

    try:
        valid_from = datetime.fromisoformat(
            valid_from_str.replace("Z", "+00:00")
        )
        if valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - valid_from).days
        return age_days > decay_days
    except (ValueError, TypeError):
        return False


def is_superseded(temporal: dict[str, Any]) -> bool:
    """superseded_at が過去かどうか判定する。"""
    superseded_at_str = temporal.get("superseded_at")
    if not superseded_at_str:
        return False

    try:
        superseded_at = datetime.fromisoformat(
            superseded_at_str.replace("Z", "+00:00")
        )
        if superseded_at.tzinfo is None:
            superseded_at = superseded_at.replace(tzinfo=timezone.utc)
        return superseded_at < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def compute_importance_score(fm: dict[str, Any]) -> float:
    """frontmatter 辞書から rule-based で importance_score を計算する。

    計算式:
    - base: high=0.8 / medium=0.5 / low=0.2 (fm["importance"]、デフォルト medium)
    - correction_bonus: min(0.15, len(source_correction_ids) * 0.03)
    - update_bonus: min(0.10, update_count * 0.02)
    - result: min(1.0, base + correction_bonus + update_bonus)

    access_count は hook で取得不能なため除外。
    """
    importance_label = fm.get("importance", "medium")
    if not isinstance(importance_label, str):
        importance_label = "medium"
    base = _IMPORTANCE_BASE.get(importance_label.lower(), 0.5)

    source_ids = fm.get("source_correction_ids", [])
    correction_count = len(source_ids) if isinstance(source_ids, list) else 0
    correction_bonus = min(0.15, correction_count * 0.03)

    update_count = fm.get("update_count", 0)
    if not isinstance(update_count, int) or isinstance(update_count, bool):
        update_count = 0
    update_bonus = min(0.10, max(0, update_count) * 0.02)

    return min(1.0, base + correction_bonus + update_bonus)


def reinforce_memory(filepath: Path, reason: str) -> None:
    """memory ファイルの importance_score・last_reinforced_at・update_count を更新する。

    frontmatter がない場合は no-op。atomic write（tmp → os.replace）で書き戻す。

    Args:
        filepath: 対象 memory ファイルのパス
        reason: 強化理由（ログ用、ファイルには書かない）
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    if not text.startswith("---"):
        return  # frontmatter なし → no-op

    end = text.find("---", 3)
    if end == -1:
        return

    yaml_str = text[3:end].strip()
    try:
        fm: dict[str, Any] = yaml.safe_load(yaml_str) or {}
        if not isinstance(fm, dict):
            return
    except yaml.YAMLError:
        return

    # update_count をインクリメント
    current_count = fm.get("update_count", 0)
    if not isinstance(current_count, int) or isinstance(current_count, bool):
        current_count = 0
    fm["update_count"] = current_count + 1

    # importance_score を再計算
    fm["importance_score"] = compute_importance_score(fm)

    # last_reinforced_at を現在時刻で更新
    fm["last_reinforced_at"] = datetime.now(timezone.utc).isoformat()

    # frontmatter を再構築して atomic write
    new_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip()
    body = text[end + 3:]  # 閉じ --- の後の本文
    new_text = f"---\n{new_yaml}\n---{body}"

    try:
        dir_path = filepath.parent
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(new_text)
            os.replace(tmp_path, filepath)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except (OSError, PermissionError):
        return


def write_importance_score(filepath: Path, score: float) -> None:
    """frontmatter の importance_score のみをアトミックに書き込む。

    update_count / last_reinforced_at は変更しない（初回採点専用）。
    frontmatter がない場合は no-op。atomic write（tmp → os.replace）で書き戻す。
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    if not text.startswith("---"):
        return

    end = text.find("---", 3)
    if end == -1:
        return

    yaml_str = text[3:end].strip()
    try:
        fm: dict[str, Any] = yaml.safe_load(yaml_str) or {}
        if not isinstance(fm, dict):
            return
    except yaml.YAMLError:
        return

    fm["importance_score"] = round(score, 4)

    new_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip()
    body = text[end + 3:]
    new_text = f"---\n{new_yaml}\n---{body}"

    try:
        dir_path = filepath.parent
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(new_text)
            os.replace(tmp_path, filepath)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except (OSError, PermissionError):
        return


def write_temporal_metadata(
    filepath: Path,
    *,
    valid_from: str | None = None,
    source_correction_ids: list | None = None,
) -> bool:
    """frontmatter に valid_from / source_correction_ids を書き込む（provenance 配線・#2）。

    auto_memory_broker が memory エントリ生成時に呼び、APEX-MEM の時間的妥当性
    （valid_from）と因果リンク（source_correction_ids: memory→corrections の単方向参照）を
    決定論で埋める。reader 側（parse_memory_temporal / build_temporal_memory_warnings /
    instructions_loaded の stale フィルタ）は既に実装済みで、本関数が write 側の休眠配線を
    活性化する。

    冪等性:
    - valid_from は既存値があれば上書きしない（初回設定のみ）
    - source_correction_ids は既存と union（順序保持・重複排除）

    decay_days / superseded_at は書かない（None のまま）ため、本関数だけでは
    is_stale / is_superseded は発火しない（純加算・振る舞い非変更）。

    frontmatter がない場合は no-op。atomic write（tmp → os.replace）。

    Returns:
        変更を書き込んだら True、no-op（frontmatter なし / 変更なし / 失敗）なら False。
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    if not text.startswith("---"):
        return False

    end = text.find("---", 3)
    if end == -1:
        return False

    yaml_str = text[3:end].strip()
    try:
        fm: dict[str, Any] = yaml.safe_load(yaml_str) or {}
        if not isinstance(fm, dict):
            return False
    except yaml.YAMLError:
        return False

    changed = False

    if valid_from and not fm.get("valid_from"):
        fm["valid_from"] = valid_from
        changed = True

    if source_correction_ids:
        existing = fm.get("source_correction_ids", [])
        if not isinstance(existing, list):
            existing = []
        merged = list(existing)
        seen = {str(x) for x in existing}
        for cid in source_correction_ids:
            if str(cid) not in seen:
                seen.add(str(cid))
                merged.append(cid)
        if merged != existing:
            fm["source_correction_ids"] = merged
            changed = True

    if not changed:
        return False

    new_yaml = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).rstrip()
    body = text[end + 3:]
    new_text = f"---\n{new_yaml}\n---{body}"

    try:
        dir_path = filepath.parent
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(new_text)
            os.replace(tmp_path, filepath)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return False
    except (OSError, PermissionError):
        return False

    return True


def make_source_correction_id(session_id: str, timestamp: str) -> str:
    """source_correction_ids の複合キーを生成する。

    形式: "{session_id}#{timestamp}"
    session_id と ms 精度の timestamp の組み合わせで実質一意。
    """
    return f"{session_id}#{timestamp}"
