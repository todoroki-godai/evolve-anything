#!/usr/bin/env python3
"""Stop hook — auto_memory_runner: corrections から memory 候補を非同期生成する。

動作:
1. corrections.jsonl の直近 5 件を読む（light mode: LLM 1 call 上限）
2. claude --print で memory 候補を生成（subprocess mock 対象）
3. 新規タイムスタンプ付き .md ファイルに1エントリ書き出す（上書き NG）
   例: ~/.claude/projects/<slug>/memory/auto_YYYYMMDD_HHMMSS_<hash>.md
   形式: frontmatter（name/description/metadata.type/importance: medium）+ body
4. MEMORY.md の末尾（または ## 変更履歴 セクション）に1行 append-only で index 追加
5. MEMORY.md が 200 行超 → 古い index エントリを archive.md に移動

設計制約:
- os.replace() など read-modify-write は禁止（並行 Stop での race condition 回避）
- 新規ファイル per エントリ が唯一の安全パターン
- MEMORY.md への書き込みは行末 append のみ（open(f, "a")）
- LLM タイムアウト/クラッシュ時はサイレント終了
- 最大 1 LLM call（light mode）
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# hooks/ → plugin_root/ → scripts/lib/
_lib = Path(__file__).resolve().parent.parent / "scripts" / "lib"
sys.path.insert(0, str(_lib))

import rl_common

DATA_DIR: Path = rl_common.DATA_DIR

# ゲーティングモジュール（オプショナル — ImportError 時はゲーティング無効）
try:
    from memory_gating import score_correction as _score_correction
    _HAS_MEMORY_GATING = True
except ImportError:
    _HAS_MEMORY_GATING = False

# 環境変数でゲーティングを無効化できる（run() 内で毎回評価してテスト中の monkeypatch を有効にする）
def _is_gating_enabled() -> bool:
    return os.environ.get("RL_GATING_DISABLED", "0") != "1"

# 生成後ゲート（belief_entropy）— オプショナル import
try:
    from belief_entropy import BLOCKS_FILENAME as _BELIEF_BLOCKS_FILENAME
    from belief_entropy import score_belief as _score_belief
    _HAS_BELIEF = True
except ImportError:
    _BELIEF_BLOCKS_FILENAME = "belief_blocks.jsonl"
    _HAS_BELIEF = False

try:
    from memory_temporal import compute_importance_score as _compute_importance_score
    from memory_temporal import write_importance_score as _write_importance_score
    _HAS_MEMORY_TEMPORAL = True
except ImportError:
    _HAS_MEMORY_TEMPORAL = False

# MEMORY.md の行数上限（超えたら archive）
MEMORY_LINE_LIMIT = 200

# corrections.jsonl から読む最大件数（light mode）
MAX_CORRECTIONS = 5


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


def _build_prompt(corrections: List[dict]) -> str:
    """corrections から LLM に渡すプロンプトを組み立てる。"""
    corrections_text = json.dumps(corrections, ensure_ascii=False, indent=2)
    return (
        "以下は Claude Code セッションで記録された直近の修正パターンです。\n"
        "これらの修正から学習すべき重要なルール・パターンを1件だけ抽出し、\n"
        "memory frontmatter v2 形式（YAML frontmatter + body）で出力してください。\n\n"
        "必須フィールド:\n"
        "- name: <kebab-case-slug>\n"
        "- description: <one-line summary>\n"
        "- metadata.type: feedback\n"
        "- importance: medium\n\n"
        "出力例:\n"
        "---\n"
        "name: example-pattern\n"
        "description: Example memory entry\n"
        "metadata:\n"
        "  type: feedback\n"
        "importance: medium\n"
        "---\n\n"
        "Body text here.\n\n"
        "---\n"
        "corrections:\n"
        f"{corrections_text}\n"
    )


def _call_llm(prompt: str) -> Optional[str]:
    """LLM を呼び出して memory 候補テキストを返す。失敗時は None を返す。

    subprocess.run は mock 対象。
    """
    try:
        result = subprocess.run(
            ["claude", "--print", prompt],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        if not output:
            return None
        return output
    except Exception:
        return None


def _make_filename(content: str) -> str:
    """タイムスタンプ + content ハッシュで一意なファイル名を生成する。

    形式: auto_YYYYMMDD_HHMMSS_<8hex>.md
    """
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:8]
    return f"auto_{timestamp}_{content_hash}.md"


def _write_entry_file(memory_dir: Path, filename: str, content: str) -> Path:
    """新規エントリファイルを書き出す（上書き不可）。

    ファイルが既に存在する場合（ハッシュ衝突、サブ秒衝突、並行プロセス）はスキップ。
    open("x") の排他的作成で TOCTOU を回避する。
    """
    entry_path = memory_dir / filename
    memory_dir.mkdir(parents=True, exist_ok=True)
    try:
        with entry_path.open("x", encoding="utf-8") as f:
            f.write(content)
    except FileExistsError:
        pass  # 別プロセスが先に作成済み — スキップ
    return entry_path


def _extract_one_line_summary(content: str) -> str:
    """frontmatter の description フィールドを one-line summary として抽出する。

    frontmatter がない場合は最初の非空行を返す。
    """
    lines = content.splitlines()
    in_frontmatter = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter and stripped.startswith("description:"):
            desc = stripped[len("description:"):].strip()
            if desc:
                return desc
    # fallback: 最初の非空行
    for line in lines:
        stripped = line.strip()
        if stripped and stripped != "---":
            return stripped[:80]
    return "auto memory entry"


def _append_index_line(memory_md_path: Path, filename: str, summary: str) -> None:
    """MEMORY.md に index 行を append-only で追加する。

    open(f, "a") を使うことで race condition を最小化する。
    ファイルが存在しない場合は作成しない（MEMORY.md は pre-existing が前提）。
    """
    if not memory_md_path.exists():
        # MEMORY.md がない場合は index 追加をスキップ
        return
    index_line = f"- [{filename}]({filename}) — {summary}\n"
    try:
        with memory_md_path.open("a", encoding="utf-8") as f:
            f.write(index_line)
    except OSError:
        pass


def _apply_importance_score(entry_path: Path) -> None:
    """エントリファイルの frontmatter に importance_score をアトミックに書き込む。

    compute_importance_score() が利用可能な場合のみ実行する。
    失敗してもサイレントに続行する（Stop hook への影響を与えない）。
    """
    if not _HAS_MEMORY_TEMPORAL:
        return
    try:
        from frontmatter import parse_frontmatter
        fm = parse_frontmatter(entry_path)
        score = _compute_importance_score(fm)
        _write_importance_score(entry_path, score)
    except Exception:
        pass  # サイレント継続


def _record_belief_block(data_dir: Path, belief, summary: str) -> None:
    """belief_entropy ゲートでブロックした要約を belief_blocks.jsonl に記録する。

    audit の #285 observability builder が件数を surface するためのログ。
    append-only 1 行書き込み（corrections.jsonl と同じ low-risk パターン）。
    失敗してもサイレント継続（Stop hook を壊さない）。
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "retention": round(float(belief.retention), 4),
            "drift": round(float(belief.drift), 4),
            "summary_head": summary.strip()[:80],
        }
        with (data_dir / _BELIEF_BLOCKS_FILENAME).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _archive_old_entries(memory_md_path: Path, memory_dir: Path) -> None:
    """MEMORY.md が MEMORY_LINE_LIMIT 行超の場合、古い index エントリを archive.md に移動する。

    - markdown link 行（- [...] で始まる）のうち、先頭から溢れた分を archive.md に移す
    - archive.md は memory_md_path と同じ階層に作成
    - MEMORY.md 自体は縮小後に上書き（この関数のみ read-modify-write を許可）
    """
    try:
        content = memory_md_path.read_text(encoding="utf-8")
    except OSError:
        return

    line_count = content.count("\n") + 1
    if line_count <= MEMORY_LINE_LIMIT:
        return

    lines = content.splitlines(keepends=True)

    # markdown link index エントリ行のインデックスを特定
    _index_pattern = re.compile(r"^\s*-\s+\[")
    index_line_indices = [
        i for i, line in enumerate(lines)
        if _index_pattern.match(line)
    ]

    if not index_line_indices:
        return

    # 超過行数分、先頭の index エントリをアーカイブ
    excess = line_count - MEMORY_LINE_LIMIT
    archive_count = min(excess, len(index_line_indices))
    if archive_count <= 0:
        return

    indices_to_archive = set(index_line_indices[:archive_count])
    archived_lines = [lines[i] for i in indices_to_archive]
    remaining_lines = [line for i, line in enumerate(lines) if i not in indices_to_archive]

    # archive.md に追記
    archive_path = memory_md_path.parent / "archive.md"
    try:
        with archive_path.open("a", encoding="utf-8") as f:
            f.writelines(archived_lines)
    except OSError:
        return

    # MEMORY.md を縮小後内容で原子的に上書き（tmp → rename）
    new_content = "".join(remaining_lines)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=memory_md_path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, memory_md_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def run(
    memory_dir: Optional[Path] = None,
    memory_md_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
) -> None:
    """auto_memory_runner のメイン処理。

    Args:
        memory_dir: auto-memory ファイルの書き出し先ディレクトリ。
                    None の場合は CLAUDE_PROJECT_DIR から推定する。
        memory_md_path: MEMORY.md のパス。None の場合は memory_dir の親。
        data_dir: corrections.jsonl の読み取り元。None の場合は DATA_DIR。
    """
    _data_dir = data_dir or DATA_DIR

    # 1. corrections.jsonl から直近5件を読む
    corrections = read_recent_corrections(data_dir=_data_dir)
    if not corrections:
        return  # graceful exit

    # 1a. ゲーティング: 重要度スコアが低い correction はスキップ
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

    # 2. memory_dir の決定
    if memory_dir is None:
        project_dir_str = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if not project_dir_str:
            return  # graceful exit: project_dir 不明
        slug = rl_common.project_name_from_dir(project_dir_str)
        memory_dir = Path.home() / ".claude" / "projects" / slug / "memory"

    _memory_dir = memory_dir

    # 3. LLM 呼び出し（最大1回）
    prompt = _build_prompt(corrections)
    llm_output = _call_llm(prompt)
    if not llm_output:
        return  # graceful exit

    # 3a. 生成後ゲート（belief_entropy）: 生成された要約が元 corrections を忠実に
    #     表しているか（retention/drift）を決定論で評価。should_store=False なら
    #     書込も index 追記もせず終了する。例外時は fail-open（素通し）で Stop hook を壊さない。
    if _HAS_BELIEF and _is_gating_enabled():
        try:
            belief = _score_belief(llm_output, corrections)
            if not belief.should_store:
                _record_belief_block(_data_dir, belief, llm_output)
                return  # block: 低信頼要約は破棄（書込+index ともにスキップ）
        except Exception:
            pass  # belief ゲートは optional。例外時は素通し

    # 4. 新規 .md ファイルに書き出す
    filename = _make_filename(llm_output)
    entry_path = _write_entry_file(_memory_dir, filename, llm_output)

    # 4a. importance_score を frontmatter に書き込む
    _apply_importance_score(entry_path)

    # 5. MEMORY.md に index 追加
    _memory_md = memory_md_path or (_memory_dir.parent / "MEMORY.md")
    summary = _extract_one_line_summary(llm_output)
    _append_index_line(_memory_md, filename, summary)

    # 6. MEMORY.md 200 行超 → archive 処理
    if _memory_md.exists():
        _archive_old_entries(_memory_md, _memory_dir)


def main() -> None:
    """スタンドアロン実行エントリポイント。例外はすべてサイレント。"""
    try:
        run()
    except Exception:
        pass  # Stop hook への影響を与えない


if __name__ == "__main__":
    main()
