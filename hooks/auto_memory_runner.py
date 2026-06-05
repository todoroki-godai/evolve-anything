#!/usr/bin/env python3
"""Stop hook — auto_memory_runner: corrections を生成前ゲートしてキューに enqueue する。

[ADR-037] Phase 2 で claude -p を全廃。この hook は **決定論・ゼロ LLM** に徹し、
LLM 生成・生成後ゲート（belief_entropy）・memory 書き込みは evolve drain の2相
（auto_memory_broker）へ移設した。

動作:
1. corrections.jsonl の直近 5 件を読む
2. memory_gating（生成前ゲート, LLM 不要）で重要度の低い correction を落とす
3. 生き残りを内容ハッシュ dedup して PJ スコープキュー
   `DATA_DIR/auto_memory_queue/<slug>.jsonl` に enqueue する（.md 書込なし /
   belief ゲートなし / claude -p なし）

設計制約:
- claude subprocess を一切含めない（claude -p 全廃の AST 回帰ゲート対象）
- enqueue は append-only（並行 Stop での race condition 回避）
- 例外はすべてサイレント（Stop hook を壊さない）

slug は memory dir（`~/.claude/projects/<slug>/memory/`）と一致させる必要があるため
`rl_common.project_name_from_dir(CLAUDE_PROJECT_DIR)` で解決する。
"""

import json
import os
import sys
from pathlib import Path
from typing import List, Optional

# hooks/ → plugin_root/ → scripts/lib/
_lib = Path(__file__).resolve().parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib))

import rl_common
import auto_memory_broker

DATA_DIR: Path = rl_common.DATA_DIR

# ゲーティングモジュール（オプショナル — ImportError 時はゲーティング無効）
try:
    from memory_gating import score_correction as _score_correction
    _HAS_MEMORY_GATING = True
except ImportError:
    _HAS_MEMORY_GATING = False

# corrections.jsonl から読む最大件数（light mode）
MAX_CORRECTIONS = 5


# 環境変数でゲーティングを無効化できる（run() 内で毎回評価してテスト中の monkeypatch を有効にする）
def _is_gating_enabled() -> bool:
    return os.environ.get("RL_GATING_DISABLED", "0") != "1"


def read_recent_corrections(data_dir: Optional[Path] = None) -> List[dict]:
    """corrections.jsonl から最新 MAX_CORRECTIONS 件を返す。

    ファイル不在・空の場合は [] を返す。
    """
    _data_dir = data_dir or DATA_DIR
    corrections_path = _data_dir / "corrections.jsonl"
    if not corrections_path.exists():
        return []

    records = []
    try:
        for line in corrections_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except (OSError, UnicodeDecodeError):
        return []

    return records[-MAX_CORRECTIONS:]


def _load_existing_memory_texts(memory_dir: Optional[Path] = None) -> List[str]:
    """memory ディレクトリ配下の .md ファイルのテキストを収集して返す。

    ファイルが存在しない場合や読み取りエラーの場合は空リストを返す。
    """
    if memory_dir is None:
        project_dir_str = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if not project_dir_str:
            return []
        slug = rl_common.project_name_from_dir(project_dir_str)
        memory_dir = Path.home() / ".claude" / "projects" / slug / "memory"

    if not memory_dir.exists():
        return []

    texts: List[str] = []
    try:
        for md_file in memory_dir.glob("*.md"):
            try:
                text = md_file.read_text(encoding="utf-8")
                if text.strip():
                    texts.append(text)
            except (OSError, UnicodeDecodeError):
                continue
    except OSError:
        return []

    return texts


def _load_all_corrections(data_dir: Optional[Path] = None, max_records: int = 50) -> List[dict]:
    """corrections.jsonl から最大 max_records 件を返す（ゲーティング用ウィンドウ）。

    ファイル不在・空の場合は [] を返す。
    """
    _data_dir = data_dir or DATA_DIR
    corrections_path = _data_dir / "corrections.jsonl"
    if not corrections_path.exists():
        return []

    records = []
    try:
        for line in corrections_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except (OSError, UnicodeDecodeError):
        return []

    return records[-max_records:]


def _resolve_slug() -> Optional[str]:
    """CLAUDE_PROJECT_DIR から memory dir と一致する slug を解決する。

    project_dir 不明なら None を返す（graceful exit のため）。
    """
    project_dir_str = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir_str:
        return None
    slug = rl_common.project_name_from_dir(project_dir_str)
    return slug or None


def run(
    memory_dir: Optional[Path] = None,
    memory_md_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    slug: Optional[str] = None,
) -> None:
    """auto_memory_runner のメイン処理（決定論・ゼロ LLM）。

    corrections を生成前ゲートして PJ スコープキューに enqueue するだけ。
    LLM 生成・belief ゲート・memory 書き込みは evolve drain（auto_memory_broker）が担う。

    Args:
        memory_dir: 生成前ゲートの既存 memory 照合に使う（None なら slug から推定）。
        memory_md_path: 互換のため受けるが本 hook では未使用（drain 側が使う）。
        data_dir: corrections.jsonl の読み取り元 & キューの書き込み先。None なら DATA_DIR。
        slug: PJ スコープキューの slug。None なら CLAUDE_PROJECT_DIR から解決する。
    """
    _data_dir = data_dir or DATA_DIR

    # 1. corrections.jsonl から直近5件を読む
    corrections = read_recent_corrections(data_dir=_data_dir)
    if not corrections:
        return  # graceful exit

    # 2. 生成前ゲート（memory_gating, LLM 不要）: 重要度スコアが低い correction はスキップ
    if _HAS_MEMORY_GATING and _is_gating_enabled():
        try:
            # all_corrections は重複検出のウィンドウ用（最大50件）
            all_corrections = _load_all_corrections(data_dir=_data_dir)
            existing_memories = _load_existing_memory_texts(memory_dir=memory_dir)
            filtered = [
                c for c in corrections
                if _score_correction(c, existing_memories, all_corrections).should_store
            ]
            if not filtered:
                return  # 全件ゲーティングでスキップ
            corrections = filtered
        except Exception:
            pass  # ゲーティングは optional。例外時は元の corrections をそのまま使う

    # 3. slug 解決（memory dir と一致させる）
    if slug is None:
        slug = _resolve_slug()
        if slug is None:
            return  # graceful exit: project_dir 不明

    # 4. キューに enqueue するだけ（LLM 呼び出し・memory 書込・belief ゲートは一切しない）
    try:
        auto_memory_broker.enqueue(corrections, slug, _data_dir)
    except Exception:
        pass  # enqueue 失敗もサイレント（Stop hook を壊さない）


def main() -> None:
    """スタンドアロン実行エントリポイント。例外はすべてサイレント。"""
    try:
        run()
    except Exception:
        pass  # Stop hook への影響を与えない


if __name__ == "__main__":
    main()
