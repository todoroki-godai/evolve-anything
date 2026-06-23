"""token_usage_ingest — transcript JSONL → token_usage SoR の取り込み。

データソース: ~/.claude/projects/<pj_dir>/*.jsonl
パース対象は top-level uuid + message.usage を持つ行のみ。

issue #28 redesign:
  - `ingest_all_projects` で 1 connection を共有 (write amplification 回避)
  - jsonl 単位の差分 ingest: `session_progress` (pj_id, session_id=stem, last_uuid, last_ts)
    を持ち、再 ingest 時は last_uuid 以降の行のみ batch に積む
  - 100 jsonl ごとに transaction commit (クラッシュ時のロスト上限)
  - time.perf_counter() で (glob/parse/commit/progress) 4 段計測 → progress=True で stderr 出力
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

try:
    from . import token_usage_store as _store  # type: ignore
except ImportError:  # pragma: no cover - script-style import
    import token_usage_store as _store  # type: ignore


# 100 jsonl ごとに transaction commit + session_progress 永続化。
# クラッシュ時のロスト上限 = 100 jsonl 分の差分。
_CHUNK_SIZE = 100


def _pj_slug_from_id(pj_id: str) -> str:
    """encoded path → 表示用 short name（pj_slug 単一ソースに委譲・#68）。

    旧実装は ``-`` 単純 split の末尾採用で ``figma-to-code`` → ``code`` /
    ``sys-bots`` → ``bots`` と化け、別 PJ と名前空間衝突していた。実 dir 名を
    ファイルシステム貪欲探索で復元する ``pj_slug.pj_id_to_slug`` に一本化する。
    """
    if not pj_id:
        return pj_id
    try:
        from pj_slug import pj_id_to_slug  # type: ignore
    except ImportError:  # pragma: no cover - script-style import fallback
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from pj_slug import pj_id_to_slug  # type: ignore
    return pj_id_to_slug(pj_id)


def _slug_from_cwd(cwd: object) -> str:
    """transcript の ``cwd`` フィールドから authoritative な pj_slug を導く（#68）。

    ``cwd`` は曖昧さのない絶対パスなので、pj_id の ``-``/``/`` 両義性を回避できる最も
    確実なソース。worktree 正規化込みで ``pj_slug.pj_slug_fast`` に委譲する。
    """
    if not isinstance(cwd, str) or not cwd:
        return ""
    try:
        from pj_slug import pj_slug_fast  # type: ignore
    except ImportError:  # pragma: no cover
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from pj_slug import pj_slug_fast  # type: ignore
    return pj_slug_fast(cwd) or ""


def _parse_cache_creation_tokens(usage: dict) -> int:
    """cache_creation_input_tokens を取得する。

    CC v2.1.152 以前はトップレベルが 0 で nested usage.cache_creation.input_tokens に
    実値が入るケースがあったため、フォールバックで読む。
    """
    value = int(usage.get("cache_creation_input_tokens") or 0)
    if value == 0:
        nested = usage.get("cache_creation")
        if isinstance(nested, dict):
            value = int(nested.get("input_tokens") or 0)
    return value


def parse_transcript_line(line: str, pj_id: str = "", pj_slug: str = "") -> dict | None:
    """transcript JSONL 1 行を token_usage record に変換する。

    None を返す条件:
      - JSON パース失敗
      - top-level uuid 無し
      - message.usage 無し
    """
    line = (line or "").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None

    uuid = obj.get("uuid")
    if not uuid:
        return None

    message = obj.get("message") or {}
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not usage or not isinstance(usage, dict):
        return None

    server_tool_use = usage.get("server_tool_use") or {}
    if not isinstance(server_tool_use, dict):
        server_tool_use = {}

    is_sidechain = bool(obj.get("isSidechain", False))
    role = message.get("role") or ""
    model = message.get("model") if role == "assistant" else None

    # slug 解決順（#68）: 明示引数 > transcript の cwd（曖昧さなし）> pj_id の fs 復元。
    slug = pj_slug or _slug_from_cwd(obj.get("cwd")) or _pj_slug_from_id(pj_id)

    return {
        "uuid": uuid,
        "pj_id": pj_id,
        "pj_slug": slug,
        "session_id": obj.get("sessionId") or "",
        "parent_uuid": obj.get("parentUuid"),
        "is_sidechain": is_sidechain,
        "ts": obj.get("timestamp") or "",
        "model": model,
        "role": role,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_creation_input_tokens": _parse_cache_creation_tokens(usage),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "web_search_requests": int(server_tool_use.get("web_search_requests") or 0),
        "web_fetch_requests": int(server_tool_use.get("web_fetch_requests") or 0),
    }


def _scan_jsonl(
    jsonl: Path,
    pj_id: str,
    pj_slug: str,
    last_uuid: str | None,
) -> tuple[list[dict], int, str | None, str | None]:
    """jsonl を順方向 scan して、last_uuid 以降の行を batch に積む。

    Args:
        last_uuid: None → 全行 parse、指定 → 当該 uuid を観測した次の行から積む

    Returns:
        (batch, skipped, new_last_uuid, new_last_ts)
        new_last_uuid/new_last_ts は jsonl の最後に観測した record の値。
        batch が空でも新規 uuid を観測していれば返す。
        last_uuid が file 中に存在しなかった場合は安全側で全行を batch に入れる
        (INSERT OR IGNORE で重複は弾かれる)。
    """
    batch: list[dict] = []
    skipped = 0
    new_last_uuid: str | None = None
    new_last_ts: str | None = None
    found_cursor = (last_uuid is None)
    fallback_full: list[dict] = []  # last_uuid drift 時の保険

    with open(jsonl, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            rec = parse_transcript_line(line, pj_id=pj_id, pj_slug=pj_slug)
            if rec is None:
                skipped += 1
                continue
            new_last_uuid = rec["uuid"]
            new_last_ts = rec["ts"] or new_last_ts
            if last_uuid is not None and not found_cursor:
                fallback_full.append(rec)  # cursor 未到達時は保険として保持
                if rec["uuid"] == last_uuid:
                    found_cursor = True
                continue
            batch.append(rec)

    # last_uuid が file 中に存在しなかった (drift) → 全行 INSERT OR IGNORE で再 ingest
    if last_uuid is not None and not found_cursor:
        batch = fallback_full

    return batch, skipped, new_last_uuid, new_last_ts


def ingest_pj_dir(
    pj_dir: Path,
    days: int | None = 90,
    con=None,
    progress: bool = False,
) -> dict:
    """1 つの PJ ディレクトリを ingest。

    Args:
        pj_dir: ~/.claude/projects/<pj_id>/ 相当
        days: mtime フィルタ (None = 無制限)
        con: 外部 connection。指定時は session_progress 差分 ingest (新挙動)。
             None → file ごとに append_batch が短命 connection を作る (旧挙動 / テスト互換)
        progress: True で計測値を stderr に出力

    Returns:
        {'inserted', 'skipped', 'errors', 'files_processed',
         'timings': {'glob_s', 'parse_s', 'commit_s', 'progress_s'}}
    """
    pj_dir = Path(pj_dir)
    pj_id = pj_dir.name
    # 空を渡して parse_transcript_line に slug を解決させる（cwd 優先・#68）。
    # 行に cwd が無い場合のみ _pj_slug_from_id(pj_id) に fallback する。
    pj_slug = ""

    cutoff = None
    if days is not None and days >= 0:
        cutoff = time.time() - days * 86400

    timings = {"glob_s": 0.0, "parse_s": 0.0, "commit_s": 0.0, "progress_s": 0.0}
    inserted = 0
    skipped = 0
    errors = 0
    files_processed = 0

    base = {
        "inserted": 0, "skipped": 0, "errors": 0,
        "files_processed": 0, "timings": timings,
    }
    if not pj_dir.exists() or not pj_dir.is_dir():
        return base

    # (a) glob + mtime filter
    # トップレベル *.jsonl に加え <session-uuid>/subagents/*.jsonl も取り込む
    t0 = time.perf_counter()
    candidates: list[Path] = []
    for jsonl in sorted(pj_dir.glob("*.jsonl")) + sorted(pj_dir.glob("*/subagents/*.jsonl")):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            errors += 1
            continue
        if cutoff is not None and mtime < cutoff:
            continue
        candidates.append(jsonl)
    timings["glob_s"] = time.perf_counter() - t0

    # con=None → 旧挙動 (テスト互換): file ごとに append_batch (内部 _connect/close)
    # con 指定 → session_progress 差分 ingest + chunk transaction
    if con is None:
        for jsonl in candidates:
            files_processed += 1
            batch: list[dict] = []
            t_p = time.perf_counter()
            try:
                with open(jsonl, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        rec = parse_transcript_line(line, pj_id=pj_id, pj_slug=pj_slug)
                        if rec is None:
                            skipped += 1
                            continue
                        batch.append(rec)
            except OSError:
                errors += 1
                timings["parse_s"] += time.perf_counter() - t_p
                continue
            timings["parse_s"] += time.perf_counter() - t_p

            if batch:
                t_c = time.perf_counter()
                try:
                    inserted += _store.append_batch(batch)
                except Exception:
                    errors += 1
                timings["commit_s"] += time.perf_counter() - t_c
        return {
            "inserted": inserted, "skipped": skipped, "errors": errors,
            "files_processed": files_processed, "timings": timings,
        }

    # ── 新挙動: session_progress 差分 ingest ──────────────────────────
    progress_map = _store.get_session_progress_for_pj(con, pj_id)

    pending_records: list[dict] = []
    pending_progress: list[tuple[str, str, str | None]] = []  # (session_id, last_uuid, last_ts)

    def _flush(reason: str):
        nonlocal inserted
        if not pending_records and not pending_progress:
            return
        t_c = time.perf_counter()
        try:
            con.execute("BEGIN TRANSACTION")
            if pending_records:
                inserted += _store._append_batch_with_con(con, pending_records)
            timings["commit_s"] += time.perf_counter() - t_c
            t_p = time.perf_counter()
            if pending_progress:
                _store.upsert_session_progress_batch(con, pj_id, pending_progress)
            con.execute("COMMIT")
            timings["progress_s"] += time.perf_counter() - t_p
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            pending_records.clear()
            pending_progress.clear()

    for idx, jsonl in enumerate(candidates, 1):
        files_processed += 1
        # subagents/ 配下は stem が衝突するため pj_dir からの相対パス（拡張子なし）を使う
        session_id = str(jsonl.relative_to(pj_dir).with_suffix(""))
        last_uuid, _last_ts = progress_map.get(session_id, (None, None))

        t_p = time.perf_counter()
        try:
            batch, sk, new_last_uuid, new_last_ts = _scan_jsonl(
                jsonl, pj_id, pj_slug, last_uuid
            )
        except OSError:
            errors += 1
            timings["parse_s"] += time.perf_counter() - t_p
            continue
        timings["parse_s"] += time.perf_counter() - t_p
        skipped += sk

        if batch:
            pending_records.extend(batch)
        if new_last_uuid is not None:
            # last_ts が空 (malformed transcript) は NULL として記録 → 次回 ingest で fallback 経路
            pending_progress.append((session_id, new_last_uuid, new_last_ts or None))

        # _CHUNK_SIZE jsonl ごとに commit
        if idx % _CHUNK_SIZE == 0:
            _flush(f"chunk@{idx}")
            if progress:
                sys.stderr.write(
                    f"  [{pj_id}] {idx}/{len(candidates)} files "
                    f"glob={timings['glob_s']:.2f}s parse={timings['parse_s']:.2f}s "
                    f"commit={timings['commit_s']:.2f}s progress={timings['progress_s']:.2f}s\n"
                )
                sys.stderr.flush()

    _flush("final")

    return {
        "inserted": inserted, "skipped": skipped, "errors": errors,
        "files_processed": files_processed, "timings": timings,
    }


def ingest_all_projects(
    claude_projects_root: Path | None = None,
    days: int | None = 90,
    progress: bool = True,
) -> dict:
    """全 PJ を ingest。1 connection を共有 (issue #28 対応)。"""
    root = Path(claude_projects_root) if claude_projects_root else Path.home() / ".claude" / "projects"
    if not root.exists():
        return {"inserted": 0, "skipped": 0, "errors": 0, "files_processed": 0, "projects": 0}

    pj_dirs = sorted(p for p in root.iterdir() if p.is_dir())
    total = len(pj_dirs)
    agg = {
        "inserted": 0, "skipped": 0, "errors": 0,
        "files_processed": 0, "projects": total,
        "timings": {"glob_s": 0.0, "parse_s": 0.0, "commit_s": 0.0, "progress_s": 0.0},
    }

    # 1 connection を全 PJ で共有 → checkpoint を 1 回に集約
    with _store.connection() as con:
        for i, pj_dir in enumerate(pj_dirs, 1):
            res = ingest_pj_dir(pj_dir, days=days, con=con, progress=progress)
            agg["inserted"] += res["inserted"]
            agg["skipped"] += res["skipped"]
            agg["errors"] += res["errors"]
            agg["files_processed"] += res["files_processed"]
            for k, v in res.get("timings", {}).items():
                agg["timings"][k] += v
            if progress:
                t = res.get("timings", {})
                sys.stderr.write(
                    f"[{i}/{total}] {pj_dir.name}: inserted={res['inserted']} "
                    f"skipped={res['skipped']} files={res['files_processed']} "
                    f"glob={t.get('glob_s', 0.0):.2f}s parse={t.get('parse_s', 0.0):.2f}s "
                    f"commit={t.get('commit_s', 0.0):.2f}s\n"
                )
                sys.stderr.flush()

    if progress:
        t = agg["timings"]
        total_s = t["glob_s"] + t["parse_s"] + t["commit_s"] + t["progress_s"]
        # parse 支配 vs commit 支配の判定 (Step 0 計測フックの本旨)
        ratio = (t["parse_s"] / t["commit_s"]) if t["commit_s"] > 0 else float("inf")
        verdict = "parse-bound (consider byte-offset seek)" if ratio >= 2.0 else "commit-bound (Approach B sufficient)"
        sys.stderr.write(
            f"\nTOTAL: inserted={agg['inserted']} files={agg['files_processed']} "
            f"projects={total} elapsed={total_s:.2f}s\n"
            f"BREAKDOWN: glob={t['glob_s']:.2f}s parse={t['parse_s']:.2f}s "
            f"commit={t['commit_s']:.2f}s progress={t['progress_s']:.2f}s\n"
            f"VERDICT: parse/commit={ratio:.2f} → {verdict}\n"
        )
        sys.stderr.flush()

    return agg
