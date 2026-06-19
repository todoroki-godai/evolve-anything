"""pj_slug_backfill — 既存レコードの幻PJ slug を worktree 安全 slug へ回収する（#593）。

背景: #593 write-side fix 以前に書かれたレコードの ``project`` / ``project_path`` には、
worktree フルパス（``.../.claude/worktrees/<name>``）や basename のばらつきが混入し、
worktree が幻の別PJ slug として cross-PJ 統計に紛れ込んでいた。write-side fix は今後の
書込を正規化するが、既存の汚染レコードは回収できないため本バックフィルで遡及正規化する。

対象7ストア（実環境横断スイープの汚染実害ストア・#602）。フィールド名は各 writer hook で確認済み:
  - ``corrections.jsonl``      : ``project_path`` フィールド（フルパス混入）
  - ``subagents.jsonl``        : ``project`` フィールド（basename ばらつき）
  - ``sessions.db``            : ``project`` 列 + ``raw_json`` 内 ``project``（DuckDB UPDATE）
                                 ※ 読み側 ``session_store.query`` は raw_json から project を読むため
                                   列と raw_json の両方を正規化する
  - ``usage.jsonl``            : ``project`` フィールド（hooks/observe.py の Skill/Agent usage）
  - ``workflows.jsonl``        : ``project`` フィールド（hooks/session_summary.py の seq["project"]）
  - ``skill_activations.jsonl``: ``project`` フィールド（hooks/skill_activation_log.py）
  - ``errors.jsonl``           : ``project`` フィールド（observe.py / permission_denied.py / stop_failure.py）
  - ``usage-registry.jsonl``   : ``project_path`` フィールド（hooks/observe.py の global skill registry）

正規化は write-side / read-side と同一の ``pj_slug.pj_slug_fast``（subprocess なしの
軽量版）を再利用する（新方式を発明しない）。挙動:
  - worktree フルパス → ``/.claude/worktrees/`` で切り親 repo basename
  - 通常フルパス      → basename
  - basename だけ     → 原値そのまま（フルパスが無く本体名へ復元不能・情報欠落）

安全運用:
  - **dry-run 既定**（``apply=False`` は1バイトも書かない・正規化予定件数だけ数える）
  - **冪等**（再実行で無変化。既に slug の値は normalize しても同値なので差分ゼロ）
  - 対象 dir は引数 ``data_dir`` で受け、実 ``~/.claude`` は呼び出し側が指定しない限り触らない

決定論・LLM 非依存。jsonl は行単位 rewrite（原子的 rename）、db は DuckDB UPDATE。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


def _normalize(value: Optional[str]) -> Optional[str]:
    """PJ 識別子（フルパス / worktree フルパス / basename）を worktree 安全 slug に正規化する。

    write-side / read-side と同一の ``pj_slug.pj_slug_fast`` を使う（subprocess なし）。
    空 / None は原値のまま返す（None→"" を増幅しない）。
    """
    if not value:
        return value
    try:
        from pj_slug import pj_slug_fast
        slug = pj_slug_fast(value)
        if slug:
            return slug
    except Exception:
        pass
    return Path(str(value)).name or value


def _atomic_write(path: Path, content: str) -> None:
    """jsonl を原子的に書き直す（promote._rewrite_promoted と同型）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _backfill_jsonl(path: Path, field: str, *, apply: bool) -> Dict[str, int]:
    """jsonl の各行 ``field`` を正規化する。

    Returns: {"normalized": 正規化により値が変わった行数, "total": 総行数}。
    dry-run（apply=False）は書込まず予定件数だけ返す。
    """
    if not path.exists():
        return {"normalized": 0, "total": 0}

    recs: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            # 壊れた行は触らず保全したいが、原子的 rewrite では落ちてしまうため
            # 安全側に倒して何もしない（破損行があるストアは backfill 対象外と判断）。
            return {"normalized": 0, "total": len(recs)}

    normalized = 0
    for rec in recs:
        raw = rec.get(field)
        if not raw:
            continue
        new = _normalize(raw)
        if new != raw:
            rec[field] = new
            normalized += 1

    if apply and normalized:
        content = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)
        _atomic_write(path, content)

    return {"normalized": normalized, "total": len(recs)}


def _backfill_sessions_db(db_path: Path, *, apply: bool) -> Dict[str, int]:
    """sessions.db の ``project`` 列 + ``raw_json`` 内 ``project`` を正規化する。

    読み側 ``session_store.query`` は raw_json から project を読むため、列と raw_json の
    両方を更新する。最上位 1 connection で UPDATE（per-fire connect の肥大病巣を踏まない）。
    dry-run は read_only で予定件数だけ数える。
    """
    if not db_path.exists():
        return {"normalized": 0, "total": 0}
    try:
        import duckdb
    except ImportError:
        return {"normalized": 0, "total": 0}

    con = duckdb.connect(str(db_path), read_only=not apply)
    try:
        rows = con.execute("SELECT rowid, project, raw_json FROM sessions").fetchall()
        total = len(rows)
        updates: List[tuple] = []  # (new_project, new_raw_json, rowid)
        for rowid, proj, raw in rows:
            try:
                rec = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                rec = {}
            raw_proj = rec.get("project")
            new_proj = _normalize(proj)
            new_raw_proj = _normalize(raw_proj)
            col_changed = new_proj != proj
            raw_changed = new_raw_proj != raw_proj
            if not (col_changed or raw_changed):
                continue
            if raw_changed:
                rec["project"] = new_raw_proj
                new_raw = json.dumps(rec, ensure_ascii=False)
            else:
                new_raw = raw
            updates.append((new_proj, new_raw, rowid))

        if apply and updates:
            con.executemany(
                "UPDATE sessions SET project = ?, raw_json = ? WHERE rowid = ?",
                updates,
            )
        return {"normalized": len(updates), "total": total}
    finally:
        con.close()


# jsonl ストアの (summary キー, ファイル名, 正規化フィールド) 宣言（単一ソース）。
# sessions.db は DuckDB のため別経路（_backfill_sessions_db）。
_JSONL_STORES = (
    ("corrections", "corrections.jsonl", "project_path"),
    ("subagents", "subagents.jsonl", "project"),
    ("usage", "usage.jsonl", "project"),
    ("workflows", "workflows.jsonl", "project"),
    ("skill_activations", "skill_activations.jsonl", "project"),
    ("errors", "errors.jsonl", "project"),
    ("usage_registry", "usage-registry.jsonl", "project_path"),
)


def backfill(data_dir: Path, *, apply: bool = False) -> Dict[str, Any]:
    """全7ストアの project / project_path を worktree 安全 slug に遡及正規化する（#602）。

    Args:
        data_dir: 対象ストアが置かれた DATA_DIR（テストは fixture dir、実運用は ~/.claude/rl-anything）。
        apply:    True で実書込。False（既定）は dry-run（1バイトも書かない・予定件数のみ）。

    Returns:
        {
          "applied": bool,
          "data_dir": str,
          "corrections":       {"normalized": int, "total": int},
          "subagents":         {"normalized": int, "total": int},
          "usage":             {"normalized": int, "total": int},
          "workflows":         {"normalized": int, "total": int},
          "skill_activations": {"normalized": int, "total": int},
          "errors":            {"normalized": int, "total": int},
          "usage_registry":    {"normalized": int, "total": int},
          "sessions_db":       {"normalized": int, "total": int},
        }
    """
    data_dir = Path(data_dir)
    result: Dict[str, Any] = {
        "applied": apply,
        "data_dir": str(data_dir),
    }
    for key, filename, field in _JSONL_STORES:
        result[key] = _backfill_jsonl(data_dir / filename, field, apply=apply)
    result["sessions_db"] = _backfill_sessions_db(data_dir / "sessions.db", apply=apply)
    return result


def format_summary(summary: Dict[str, Any]) -> str:
    """backfill サマリを人間可読な複数行テキストに整形する。"""
    applied = summary.get("applied", False)
    verb = "正規化" if applied else "正規化予定"
    lines = [
        f"[pj_slug_backfill] data_dir={summary.get('data_dir', '?')} "
        f"({'apply' if applied else 'dry-run'})",
    ]
    _STORE_LABELS = (
        ("corrections", "corrections.jsonl (project_path)"),
        ("subagents", "subagents.jsonl (project)"),
        ("usage", "usage.jsonl (project)"),
        ("workflows", "workflows.jsonl (project)"),
        ("skill_activations", "skill_activations.jsonl (project)"),
        ("errors", "errors.jsonl (project)"),
        ("usage_registry", "usage-registry.jsonl (project_path)"),
        ("sessions_db", "sessions.db (project + raw_json)"),
    )
    for store, label in _STORE_LABELS:
        s = summary.get(store, {})
        lines.append(
            f"  {label}: {s.get('normalized', 0)}件{verb} / 総{s.get('total', 0)}件"
        )
    total_norm = sum(
        summary.get(k, {}).get("normalized", 0)
        for k, _ in _STORE_LABELS
    )
    if not applied and total_norm:
        lines.append(f"  → --apply で {total_norm}件を実書込（dry-run のため未適用）")
    return "\n".join(lines)
