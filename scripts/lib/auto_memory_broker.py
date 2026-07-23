"""auto_memory_broker — auto-memory のファイルベース2相ブローカ ([ADR-037] Phase 2)。

Stop hook（auto_memory_runner.py）から **LLM 生成と全ファイル書き込み** を引き取る。
hook 側は corrections を生成前ゲート（memory_gating）でふるって PJ スコープの
キュー `DATA_DIR/auto_memory_queue/<slug>.jsonl` に enqueue するだけになり、
ゼロ LLM・ゼロ memory 書き込みになる（claude -p を完全に追い出す）。

本モジュールが evolve drain の2相を担う:
  Phase A（決定論・IO なし）: emit_memory_requests でキュー record → prompts を生成
  Phase B（LLM・assistant）   : assistant が各 prompt にインライン応答（subscription 課金）
  Phase C（決定論）           : ingest_memory_results で応答を回収 → 生成後ゲート
                                （belief_entropy）→ .md 書き込み + index + importance +
                                archive → 処理済み record をキューから消化

決定論・LLM 非依存（claude subprocess を一切含めない）。no-llm-in-tests と完全整合
（テストは responses dict を直接渡す）。

キュー dedup は内容ハッシュ in-queue dedup:
  compute_dedup_key(corrections) = gated corrections の (session_id, timestamp) タプル
  列を JSON 化して sha256 先頭16hex。enqueue 前に未消化キューの dedup_key を読み、
  同 key が既存なら enqueue をスキップ（毎 Stop の同一 last-5 重複を防ぐ）。
  新 correction で窓がずれれば新 key → enqueue されるため cursor ファイル不要。

slug は memory dir と一致させる必要があるため、optimize_history_store / triage_ledger の
git-common-dir 方式ではなく `rl_common.project_name_from_dir(CLAUDE_PROJECT_DIR)` を使う
（memory は `~/.claude/projects/<slug>/memory/` に書かれるため）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# scripts/lib を sys.path に載せて共通モジュールを import する
import sys

_lib_dir = Path(__file__).resolve().parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from llm_broker import build_requests, parse_responses, passthrough  # noqa: E402
from pj_slug import record_project_match  # noqa: E402

# 生成後ゲート（belief_entropy）— オプショナル import
try:
    from belief_entropy import BLOCKS_FILENAME as _BELIEF_BLOCKS_FILENAME
    from belief_entropy import clean_summary_head as _clean_summary_head
    from belief_entropy import score_belief as _score_belief
    _HAS_BELIEF = True
except ImportError:
    _BELIEF_BLOCKS_FILENAME = "belief_blocks.jsonl"

    def _clean_summary_head(text: str, limit: int = 80) -> str:  # type: ignore
        return " ".join((text or "").split())[:limit]

    _HAS_BELIEF = False

try:
    from memory_temporal import compute_importance_score as _compute_importance_score
    from memory_temporal import write_importance_score as _write_importance_score
    from memory_temporal import write_temporal_metadata as _write_temporal_metadata
    from memory_temporal import make_source_correction_id as _make_source_correction_id
    _HAS_MEMORY_TEMPORAL = True
except ImportError:
    _HAS_MEMORY_TEMPORAL = False

# runtime 記憶汚染検出（#108）— オプショナル import。未解決なら fail-open（belief と同方針）。
try:
    from memory_guard import inspect_content as _inspect_memory_content
    from memory_guard import inspect_transition as _inspect_memory_transition
    from memory_guard import TRANSITION_STORE_NAME as _TRANSITION_STORE_NAME
    _HAS_MEMORY_GUARD = True
except ImportError:
    _HAS_MEMORY_GUARD = False

# MEMORY.md の行数上限（超えたら archive）
MEMORY_LINE_LIMIT = 200

# キューのサブディレクトリ名
QUEUE_SUBDIR = "auto_memory_queue"

# ─────────────────────────────────────────────────────────────────
# rule citation フィルタ
# ─────────────────────────────────────────────────────────────────
# 既知のグローバル・PJ 固有 rule スラッグ集合。
# Stop hook 由来の「既存ルール再掲リマインダ」が auto-memory に enqueue されるのを防ぐ。
#
# 検出方針:
#   - ハイフン区切りのスラッグ（例: "no-defer-use-subagent"）は一般文章への混入が稀なため
#     部分文字列マッチで安全に検出できる。
#   - 汎用英単語（"auth"、"commit"、"safety" 等）は FP リスクが高いため除外する。
#     代わりにハイフン付き形式（"commit-version" 等）で検出する。
# 新しい rule ファイル（.claude/rules/ 配下）を追加した際はここにも追記する。
_KNOWN_RULE_SLUGS: frozenset[str] = frozenset({
    # グローバル rules (~/.claude/rules/) — ハイフン入りのみ（FP 防止）
    "avoid-bash-builtin",
    "background-execution",
    "code-quality",
    "copy-paste-output",
    "delegate-implementation",
    "estimate-data-feasibility",
    "evolve-ops",
    "explain-clearly",
    "factual-claims",
    "loop-safety",
    "lsp-first",
    "memory-context",
    "model-routing",
    "review-routing",
    "skill-ops",
    "spec-keeper-trigger",
    "subagent-guard",
    "think-before-coding",
    "worktree-parallel",
    # PJ 固有 rules (.claude/rules/) — ハイフン入りのみ
    "commit-version",
    "file-size-budget",
    "git-push",
    "infra-ship-gate",
    "issue-link",
    "llm-batch-guard",
    "no-llm-in-tests",
    "parallel-session-guard",
    "root-cause-first",
    "tdd-first",
    "transcript-store-bench",
    "verify-before-claim",
    "verify-data-contract",
    "verify-side-effects",
    # よく現れる略語・別名（ハイフン入りのみ）
    "no-defer-use-subagent",
    "no-defer",
})


# 環境変数でゲーティングを無効化できる（ingest 内で毎回評価してテスト中の monkeypatch を有効にする）
def _is_gating_enabled() -> bool:
    return os.environ.get("RL_GATING_DISABLED", "0") != "1"


def is_rule_citation(correction: dict) -> bool:
    """correction が既存 rule の再掲リマインダかどうかを判定する（保守的検出）。

    `message` / `corrected` / `original` フィールドに既知の rule slug が含まれる場合に
    True を返す。検出は大小文字区別なし・部分文字列マッチ。
    FP を最小化するため、slug が明確に識別できるものだけを対象にする。

    Args:
        correction: 1件の correction dict。

    Returns:
        True なら rule citation（enqueue 対象外）、False なら通常 correction。
    """
    if not isinstance(correction, dict):
        return False
    # 対象フィールド: message > corrected > original の順で検査
    target_text = " ".join(
        str(correction.get(field, "") or "")
        for field in ("message", "corrected", "original")
    ).lower()
    if not target_text.strip():
        return False
    return any(slug in target_text for slug in _KNOWN_RULE_SLUGS)


# ─────────────────────────────────────────────────────────────────
# dedup key
# ─────────────────────────────────────────────────────────────────
def compute_dedup_key(corrections: List[dict]) -> str:
    """gated corrections の同一性を表す決定論ハッシュキーを返す。

    (session_id, timestamp) タプル列を JSON 化して sha256 先頭16hex。
    同一 corrections 窓は同一 key、窓がずれれば別 key（cursor 不要）。
    """
    pairs = [
        [c.get("session_id", ""), c.get("timestamp", "")]
        for c in (corrections or [])
    ]
    blob = json.dumps(pairs, ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────
# キューストア（PJ スコープ jsonl）
# ─────────────────────────────────────────────────────────────────
def queue_path_for(slug: str, data_dir: Path) -> Path:
    """slug の auto-memory キューファイルパスを返す。"""
    return Path(data_dir) / QUEUE_SUBDIR / f"{slug}.jsonl"


def read_queue(slug: str, data_dir: Path) -> List[dict]:
    """slug の未消化キュー record を返す（dedup_key で重複排除した union）。

    append-only ファイルを dedup_key で collapse（last-write-wins）する。
    壊れた JSON 行・dedup_key 欠落行はスキップ。ファイル不在なら []。
    挿入順を保つため dict（Python 3.7+ は順序保持）で集約する。
    """
    path = queue_path_for(slug, data_dir)
    if not path.exists():
        return []
    records: Dict[str, dict] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = rec.get("dedup_key")
        if not key:
            continue
        records[key] = rec  # last-write-wins（union）
    return list(records.values())


def enqueue(corrections: List[dict], slug: str, data_dir: Path) -> bool:
    """gated corrections をキューに append する（内容ハッシュ in-queue dedup）。

    rule citation フィルタ: 各 correction を `is_rule_citation` で検査し、
    既存 rule slug を再掲するだけのリマインダを除外する。除外後に空になった場合は
    enqueue せず False を返す。

    project スコープ不一致 reject（#206・多層防御の最終防衛ライン）: 各 correction を
    `record_project_match(c, slug)` で検査し、他 PJ の project_path を持つ correction
    （呼び出し元の読み出しフィルタをすり抜けた場合の最終防衛）を除外する。silent drop に
    せず、reject 件数を stderr に出力して可視化する（memory_guard の warn/reject print と
    同じ観測パターン）。除外後に空になった場合は enqueue せず False を返す。

    キューの未消化 dedup_key を best-effort で読み、同 key が既存なら enqueue を
    スキップして False を返す。新規なら record を append("a") して True を返す。

    record: {"dedup_key", "slug", "corrections", "enqueued_at"}
    """
    if not corrections:
        return False

    # rule citation フィルタ: 既存ルール再掲リマインダを除外
    filtered_corrections = [c for c in corrections if not is_rule_citation(c)]
    if not filtered_corrections:
        return False  # 全件が rule citation → enqueue しない
    corrections = filtered_corrections

    # project スコープ不一致 reject（#206）
    scope_ok = [c for c in corrections if record_project_match(c, slug)]
    n_rejected = len(corrections) - len(scope_ok)
    if n_rejected:
        print(
            f"[evolve-anything:auto-memory] project scope mismatch: "
            f"{n_rejected} correction(s) excluded (slug={slug})",
            file=sys.stderr,
        )
    if not scope_ok:
        return False  # 全件が他 PJ スコープ → enqueue しない
    corrections = scope_ok

    key = compute_dedup_key(corrections)
    existing_keys = {r.get("dedup_key") for r in read_queue(slug, data_dir)}
    if key in existing_keys:
        return False  # 同一窓は既にキュー済み

    record = {
        "dedup_key": key,
        "slug": slug,
        "corrections": corrections,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    }
    path = queue_path_for(slug, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return True


def clear_queue_entries(slug: str, data_dir: Path, consumed_keys: Set[str]) -> None:
    """consumed_keys を除いた record だけでキューファイルを書き直す（原子的）。

    consumed には stored も blocked も含める（処理済みは再キューしない）。
    consumed 後に残 record が無ければファイルを空にする（削除はしない）。
    """
    path = queue_path_for(slug, data_dir)
    if not path.exists():
        return
    remaining = [r for r in read_queue(slug, data_dir) if r.get("dedup_key") not in consumed_keys]
    path.parent.mkdir(parents=True, exist_ok=True)
    new_content = "".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in remaining
    )
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────
# プロンプト生成 / ファイル書き込み helper（hook から移設）
# ─────────────────────────────────────────────────────────────────
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
    失敗してもサイレントに続行する。
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


def _derive_source_correction_ids(corrections: List[dict]) -> List[str]:
    """corrections から source_correction_ids（"{session_id}#{timestamp}"）を導出する。

    memory→correction の単方向因果リンク（#2）。session_id / timestamp の両方が空の
    correction はスキップし、順序を保ったまま重複を排除する。
    """
    if not _HAS_MEMORY_TEMPORAL:
        return []
    ids: List[str] = []
    seen: Set[str] = set()
    for c in (corrections or []):
        if not isinstance(c, dict):
            continue
        sid = str(c.get("session_id", "") or "")
        ts = str(c.get("timestamp", "") or "")
        if not sid and not ts:
            continue
        cid = _make_source_correction_id(sid, ts)
        if cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids


def _apply_temporal_metadata(entry_path: Path, corrections: List[dict]) -> None:
    """エントリの frontmatter に valid_from + source_correction_ids を書き込む（#2 配線）。

    valid_from は生成時刻（now）。source_correction_ids は corrections から導出した
    memory→correction 因果リンク。decay_days / superseded_at は書かないため stale/superseded
    は発火しない（純加算）。compute_importance_score の correction_bonus を効かせるため
    _apply_importance_score の **前** に呼ぶ。失敗してもサイレント継続。
    """
    if not _HAS_MEMORY_TEMPORAL:
        return
    try:
        valid_from = datetime.now(timezone.utc).isoformat()
        ids = _derive_source_correction_ids(corrections)
        _write_temporal_metadata(
            entry_path, valid_from=valid_from, source_correction_ids=ids,
        )
    except Exception:
        pass  # サイレント継続


def _record_belief_block(data_dir: Path, belief, summary: str) -> None:
    """belief_entropy ゲートでブロックした要約を belief_blocks.jsonl に記録する。

    audit の #285 observability builder が件数を surface するためのログ。
    append-only 1 行書き込み。失敗してもサイレント継続。
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "retention": round(float(belief.retention), 4),
            "drift": round(float(belief.drift), 4),
            # #69: frontmatter 除去 + 1 行化して記録（表示崩れ防止）。full summary を渡すので本文が入る。
            "summary_head": _clean_summary_head(summary),
        }
        with (data_dir / _BELIEF_BLOCKS_FILENAME).open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _record_transition_event(
    data_dir: Path, slug: Optional[str], transition: Dict[str, Any]
) -> None:
    """記憶遷移検証（#93）の1件を memory_transition_checks.jsonl に記録する。

    store_write barrier（ADR-049）経由の唯一の書込口。audit の memory_capability
    maintain 軸が「reject 件数 / 検査件数」を surface するための読み取り専用ログ。
    checked=True（同名衝突を実際に比較した）イベントのみ呼ばれる想定。
    失敗してもサイレント継続（検証自体のブロック判断は既に確定済みのため）。
    """
    try:
        from rl_common.store_write import store_write as _store_write
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "pj_slug": slug or "",
            "matched_name": transition.get("matched_name") or "",
            "rejected": bool(transition.get("block")),
            "axes": sorted({i.axis for i in transition.get("issues", [])}),
        }
        _store_write(_TRANSITION_STORE_NAME, record)
    except Exception:
        pass  # サイレント継続


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


# ─────────────────────────────────────────────────────────────────
# 2相 emit / ingest
# ─────────────────────────────────────────────────────────────────
def emit_memory_requests(records: List[dict]) -> Dict[str, Any]:
    """Phase A: キュー record → LLM リクエストを生成する（決定論・LLM 非依存・IO なし）。

    record ごとに _build_prompt(record["corrections"]) で prompt を組む。
    id は record["dedup_key"]。records 空なら {"requests": []}。

    Returns:
        {"requests": [{"id": dedup_key, "prompt": str, "meta": {...}}]}
    """
    items: List[Dict[str, Any]] = []
    for rec in (records or []):
        key = rec.get("dedup_key")
        if not key:
            continue
        items.append({
            "id": key,
            "corrections": rec.get("corrections", []),
        })
    if not items:
        return {"requests": []}
    requests = build_requests(
        items,
        lambda item: _build_prompt(item.get("corrections", [])),
    )
    return {"requests": requests}


def ingest_memory_results(
    records: List[dict],
    requests: List[Dict[str, Any]],
    responses: Dict[str, Any],
    memory_dir: Path,
    memory_md_path: Path,
    data_dir: Path,
) -> Dict[str, Any]:
    """Phase C: LLM 応答を回収し memory に書き込む（決定論・LLM 非依存）。

    各 request について:
      1. parse_responses + passthrough で生成テキストを回収
      2. 空/missing はスキップ（処理済み扱いせずキューに残す）
      3. runtime 記憶汚染検出（#108・importance 採点前）: prompt injection / secret exfil
         を含む生成物は書込 skip（reject モード）。汚染は terminal 判断として消化する。
      4. 記憶遷移検証（#93・TRUSTMEM Memory Transition Verifier の決定論移植）: 同名の
         既存エントリがあれば coverage/preservation/fidelity を検証し、汚染候補（大量欠落 /
         値矛盾 / 極性反転）は書込 skip（reject モード）。同名なしは no-op。
      5. belief_entropy 生成後ゲート（block なら _record_belief_block + スキップ、書込なし）
      6. pass なら _write_entry_file + _apply_importance_score + _append_index_line + _archive_old_entries

    処理し終えた（stored / blocked / contaminated / transition_rejected）record の dedup_key
    を集め、最後に clear_queue_entries でキューから除去する。空/missing はキューに残す
    （次 drain で再試行）。

    Returns:
        {"stored": int, "blocked": int, "skipped": int, "contaminated": int,
         "contamination_hits": [{"pattern_id", "category", "line"}],
         "transition_checked": int, "transition_rejected": int, "entries": [str paths]}
    """
    memory_dir = Path(memory_dir)
    memory_md_path = Path(memory_md_path)
    data_dir = Path(data_dir)

    # dedup_key → corrections の索引（belief ゲートのソース照合に使う）
    corrections_by_key: Dict[str, List[dict]] = {
        rec.get("dedup_key"): rec.get("corrections", [])
        for rec in (records or [])
        if rec.get("dedup_key")
    }

    parsed = parse_responses(requests or [], responses or {}, parser=passthrough)

    stored = 0
    blocked = 0
    skipped = 0
    contaminated = 0
    contamination_hits: List[dict] = []
    transition_checked = 0
    transition_rejected = 0
    entries: List[str] = []
    consumed_keys: Set[str] = set()
    slug: Optional[str] = None
    for rec in (records or []):
        if rec.get("slug"):
            slug = rec["slug"]
            break

    gating_on = _HAS_BELIEF and _is_gating_enabled()

    for req in (requests or []):
        key = req.get("id")
        if not key:
            continue
        raw = parsed.get(key)
        llm_output = raw if isinstance(raw, str) else ""
        llm_output = llm_output.strip() if llm_output else ""
        if not llm_output:
            # 空/missing: 処理済みにせずキューに残し次 drain で再試行
            skipped += 1
            continue

        # runtime 記憶汚染検出（#108）: prompt injection / secret exfil の payload を
        # 含む生成物は memory へ書き込まない（免疫層）。importance 採点・belief ゲートの
        # 前に走らせ、汚染がスコアリング/ログにも到達しないようにする。fail-open。
        if _HAS_MEMORY_GUARD:
            try:
                guard = _inspect_memory_content(llm_output)
            except Exception:
                guard = None
            if guard and guard.get("hits"):
                hit_details = [
                    {"pattern_id": h.pattern_id, "category": h.category, "line": h.line}
                    for h in guard["hits"]
                ]
                contamination_hits.extend(hit_details)
                pattern_ids = [h["pattern_id"] for h in hit_details]
                if guard.get("block"):
                    # reject: 書込せず消化（terminal 判断・再キューで無限リトライしない）
                    contaminated += 1
                    consumed_keys.add(key)
                    print(
                        f"[evolve-anything:memory-guard] 汚染検出のため書込 skip: {pattern_ids}",
                        file=sys.stderr,
                    )
                    continue
                # warn: 書込は継続するが可視化する（緊急避難・無音にしない）
                print(
                    f"[evolve-anything:memory-guard] 汚染検出（warn・書込継続）: {pattern_ids}",
                    file=sys.stderr,
                )

        # 記憶遷移検証（#93・TRUSTMEM Memory Transition Verifier の決定論移植）:
        # 同名（frontmatter name 一致）の既存エントリがあれば coverage/preservation/
        # fidelity を検証し、汚染候補（大量欠落 / 値矛盾 / 極性反転）を reject する。
        # 同名の既存エントリが無ければ no-op（checked=False・書込には影響しない）。
        if _HAS_MEMORY_GUARD:
            try:
                transition = _inspect_memory_transition(llm_output, memory_dir)
            except Exception:
                transition = None
            if transition and transition.get("checked"):
                transition_checked += 1
                _record_transition_event(data_dir, slug, transition)
                if transition.get("issues"):
                    axis_details = sorted({i.axis for i in transition["issues"]})
                    if transition.get("block"):
                        # reject: 書込せず消化（terminal 判断・再キューで無限リトライしない）
                        transition_rejected += 1
                        consumed_keys.add(key)
                        print(
                            f"[evolve-anything:memory-guard] 記憶遷移検証で reject: {axis_details}",
                            file=sys.stderr,
                        )
                        continue
                    # warn: 書込は継続するが可視化する（緊急避難・無音にしない）
                    print(
                        f"[evolve-anything:memory-guard] 記憶遷移検証ヒット（warn・書込継続）: "
                        f"{axis_details}",
                        file=sys.stderr,
                    )

        # 生成後ゲート（belief_entropy）
        if gating_on:
            try:
                belief = _score_belief(llm_output, corrections_by_key.get(key, []))
                if not belief.should_store:
                    _record_belief_block(data_dir, belief, llm_output)
                    blocked += 1
                    consumed_keys.add(key)  # block は処理済み（再キューしない）
                    continue
            except Exception:
                pass  # belief ゲートは optional。例外時は素通し

        # 書き込み
        filename = _make_filename(llm_output)
        entry_path = _write_entry_file(memory_dir, filename, llm_output)
        # #2: valid_from + source_correction_ids を先に書く（correction_bonus を
        # importance_score に効かせるため _apply_importance_score の前）
        _apply_temporal_metadata(entry_path, corrections_by_key.get(key, []))
        _apply_importance_score(entry_path)
        summary = _extract_one_line_summary(llm_output)
        _append_index_line(memory_md_path, filename, summary)
        if memory_md_path.exists():
            _archive_old_entries(memory_md_path, memory_dir)

        stored += 1
        entries.append(str(entry_path))
        consumed_keys.add(key)

    # 処理済み（stored + blocked）record をキューから消化
    if slug and consumed_keys:
        clear_queue_entries(slug, data_dir, consumed_keys)

    return {
        "stored": stored,
        "blocked": blocked,
        "skipped": skipped,
        "contaminated": contaminated,
        "contamination_hits": contamination_hits,
        "transition_checked": transition_checked,
        "transition_rejected": transition_rejected,
        "entries": entries,
    }
