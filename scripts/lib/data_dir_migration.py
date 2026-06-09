"""DATA_DIR hook/tool 分裂の一元化 migration（#364 Phase 2）。

背景（#358 / pitfall_datadir_hook_tool_split）: DATA_DIR が hook 文脈
（CC が CLAUDE_PLUGIN_DATA を設定 → ``~/.claude/plugins/data/<mp>-<plugin>``）と
tool 文脈（env 無し → fallback ``~/.claude/rl-anything``）で別 dir に解決され、
ストアごとに正準 dir が割れる。ADR-042 は reader 側の正準化（``hook_store_path``）
で最小修正したが、sessions.jsonl / errors.jsonl 等の鮮度逆転・usage.jsonl の
二重書きという実害が残った。本モジュールはその Phase 2 = **書き込み側の一元化**。

設計:
- 正準 = ``~/.claude/rl-anything`` 固定（#402 の「env 非依存固定パス」前例に整合。
  plugin-data dir は ``<marketplace>-<plugin>`` 命名に依存し脆い）
- redirect は **marker ゲート**: ``rl_common.resolve_data_dir`` が CC install
  レイアウトを指す env を、正準 dir に marker（``.data-dir-unified``）が存在する
  ときだけ正準へ向け直す。テスト isolation（tmp dir env）は無条件で尊重される
  ため、conftest の ``CLAUDE_PLUGIN_DATA=tmp_path`` 隔離を壊さない
- migration 実行順序が重要: **旧版プラグインの hook が動いている間に実行すると
  分裂が即再発**する（旧版は redirect を知らず plugin-data に書き続ける）。
  よって本 fix を含む版をインストールした後に ``rl-fleet migrate-data`` を1回
  実行する。SessionStart（restore_state）が ``needs_migration`` を検出して案内
  し、実行で marker が立って自然終息する（install ≠ enforcement 対策）
- マージ規則: ``.jsonl`` は行単位 dedup append（append-only ログ前提）、
  ``.db`` は DuckDB テーブル単位の union dedup（フレッシュ側へ INSERT … EXCEPT。
  per-fire connection 開閉で肥大したファイルの compaction も兼ねる）、
  その他ファイル / dir 配下は mtime 新しい方優先。``tmp`` / ``__pycache__`` は対象外
- dry-run は書き込みゼロ（pitfall_dryrun_stateful_store_write）
- marker は全 entry 成功時のみ書く。失敗があれば次回再実行で残りを回収（冪等）

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# マージ対象外（運用上の一時物・marker 自身）
_SKIP_NAMES = {"tmp", "__pycache__"}


def _marker_name() -> str:
    import rl_common
    return getattr(rl_common, "DATA_DIR_UNIFIED_MARKER", ".data-dir-unified")


def default_canonical() -> Path:
    """正準 DATA_DIR（env 非依存固定パス、#402 前例に整合）。"""
    return Path.home() / ".claude" / "rl-anything"


def find_source(plugin_data_base: Optional[Path] = None) -> Optional[Path]:
    """hook が書いてきた plugin-data dir を決定論で 1 つ選ぶ（store_paths を再利用）。"""
    from rl_common.store_paths import PLUGIN_DATA_BASE, _probe_install_layout
    return _probe_install_layout(plugin_data_base or PLUGIN_DATA_BASE)


def is_cc_install_layout(path: Path) -> bool:
    """path が CC の plugin-data install レイアウト（~/.claude/plugins/data/ 配下）か。

    SessionStart リマインドはこれが True のときだけ発火する。テスト isolation
    （CLAUDE_PLUGIN_DATA=tmp_path）や custom 環境では False になり、実環境の
    グローバル状態を読まない（test 衛生: グローバル状態読みの FP を構造回避）。
    """
    try:
        import rl_common
        base = getattr(
            rl_common, "_CC_PLUGIN_DATA_BASE",
            Path.home() / ".claude" / "plugins" / "data",
        )
        return Path(path).resolve().is_relative_to(Path(base).resolve())
    except OSError:
        return False


def needs_migration(source: Optional[Path] = "<probe>") -> bool:
    """旧 plugin-data dir にマージ未消化のストアが残っているか。

    marker の有無は見ない（marker 後に stranded データが見つかった場合も
    再実行で回収できるよう、判定は「source に実データがあるか」のみ）。
    """
    if source == "<probe>":
        source = find_source()
    if source is None or not Path(source).is_dir():
        return False
    try:
        entries = [
            e for e in Path(source).iterdir()
            if e.name not in _SKIP_NAMES and e.name != _marker_name()
        ]
    except OSError:
        return False
    return bool(entries)


def merge_jsonl(src: Path, dst: Path, dry_run: bool = False) -> Dict[str, Any]:
    """append-only ログの行単位 dedup マージ。既存 dst 行はそのまま、新規行のみ末尾追記。"""
    src_lines = [
        line for line in src.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    existing = set()
    if dst.exists():
        existing = set(dst.read_text(encoding="utf-8", errors="replace").splitlines())
    new_lines = [line for line in src_lines if line not in existing]
    if dry_run:
        return {"action": "would_merge_jsonl", "new_lines": len(new_lines)}
    if new_lines:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with dst.open("a", encoding="utf-8") as f:
            for line in new_lines:
                f.write(line + "\n")
    return {"action": "merged_jsonl", "new_lines": len(new_lines)}


def merge_db(src: Path, dst: Path, dry_run: bool = False) -> Dict[str, Any]:
    """DuckDB のテーブル単位 union dedup マージ + compaction。

    フレッシュな一時 db に src/dst 両側のテーブルを ``UNION``（全行一致 dedup）で
    書き出し、完了後に dst へ atomic swap する。既存 dst への INSERT 方式だと
    per-fire connection 開閉で肥大したファイル（pitfall_duckdb_checkpoint の運用面）
    が縮まないため、rebuild 方式でマージと compaction を同時に行う
    （実測: sessions.db 9.6GB / 84k 行 / 実データ約 14MB）。

    注意: CREATE TABLE AS のため PK/index は引き継がない（対象の telemetry 系
    ストアは制約なしの append ログ。制約付き db が両側に現れたら手動マージ）。
    """
    if dry_run:
        return {"action": "would_merge_db"}
    try:
        import duckdb
    except ImportError:
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return {"action": "copied_db_no_duckdb"}
        raise RuntimeError(f"duckdb 不在で両側に db が存在: {src} → {dst} は手動マージが必要")

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".migrate-tmp")
    if tmp.exists():
        tmp.unlink()
    con = duckdb.connect(str(tmp))
    try:
        # READ_ONLY を付けない: src に WAL（未 checkpoint の追記）が残っている場合、
        # 書込可 ATTACH でないと WAL が replay されず末尾の行を取りこぼす。
        # src はこの migration で退役するため書込可で開いて問題ない。
        con.execute(f"ATTACH '{src}' AS src")
        src_tables = {
            r[0] for r in con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE database_name = 'src'"
            ).fetchall()
        }
        old_tables: set = set()
        if dst.exists():
            con.execute(f"ATTACH '{dst}' AS old")
            old_tables = {
                r[0] for r in con.execute(
                    "SELECT table_name FROM duckdb_tables() WHERE database_name = 'old'"
                ).fetchall()
            }
        for t in sorted(src_tables | old_tables):
            qt = '"' + t.replace('"', '""') + '"'
            if t in src_tables and t in old_tables:
                con.execute(f"CREATE TABLE {qt} AS SELECT * FROM src.{qt} UNION SELECT * FROM old.{qt}")
            elif t in src_tables:
                con.execute(f"CREATE TABLE {qt} AS SELECT * FROM src.{qt}")
            else:
                con.execute(f"CREATE TABLE {qt} AS SELECT * FROM old.{qt}")
        con.execute("DETACH src")
        if old_tables or dst.exists():
            try:
                con.execute("DETACH old")
            except Exception:
                pass
    finally:
        con.close()
    tmp.replace(dst)  # 旧 bloat ファイルを置換 = compaction
    return {"action": "merged_db", "tables": sorted(src_tables | old_tables)}


def merge_file_newer_wins(src: Path, dst: Path, dry_run: bool = False) -> Dict[str, Any]:
    """単発状態ファイル（.json 等）は mtime が新しい方を採る。"""
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return {"action": "kept_existing"}
    if dry_run:
        return {"action": "would_copy"}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"action": "copied"}


def merge_dir(src_dir: Path, dst_dir: Path, dry_run: bool = False) -> Dict[str, Any]:
    """dir 配下を per-file の newer-wins で再帰マージ（__pycache__ は除外）。"""
    copied = 0
    kept = 0
    for path in sorted(src_dir.rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        target = dst_dir / path.relative_to(src_dir)
        if target.exists() and target.stat().st_mtime >= path.stat().st_mtime:
            kept += 1
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        copied += 1
    action = "would_merge_dir" if dry_run else "merged_dir"
    return {"action": action, "copied": copied, "kept_existing": kept}


def _remove_entry(entry: Path) -> None:
    if entry.is_dir():
        shutil.rmtree(entry, ignore_errors=True)
    else:
        entry.unlink(missing_ok=True)


def _write_marker(canonical: Path, source: Optional[Path], summary: Dict[str, Any]) -> None:
    marker = canonical / _marker_name()
    payload = {
        "migrated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source) if source else None,
        "entries": [e.get("name") for e in summary["entries"]],
    }
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def migrate(
    canonical: Optional[Path] = None,
    source: Optional[Path] = "<probe>",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """plugin-data 側ストアを正準 dir にマージし、成功時に marker を書く。

    - 全 entry 成功時のみ marker（部分失敗は次回再実行で回収＝冪等）
    - 成功した entry は source から削除（二重マージ・誤読の芽を残さない）
    - dry_run は一切書き込まない
    """
    canonical = Path(canonical) if canonical else default_canonical()
    if source == "<probe>":
        source = find_source()
    summary: Dict[str, Any] = {
        "canonical": str(canonical),
        "source": str(source) if source else None,
        "entries": [],
        "failures": 0,
        "dry_run": dry_run,
        "marker_written": False,
    }

    no_source = source is None or not Path(source).is_dir()
    same_dir = (not no_source) and Path(source).resolve() == canonical.resolve()
    if no_source or same_dir:
        if not dry_run:
            canonical.mkdir(parents=True, exist_ok=True)
            _write_marker(canonical, None if no_source else source, summary)
            summary["marker_written"] = True
        return summary

    source = Path(source)
    if not dry_run:
        canonical.mkdir(parents=True, exist_ok=True)

    for entry in sorted(source.iterdir()):
        if entry.name in _SKIP_NAMES or entry.name == _marker_name():
            summary["entries"].append({"name": entry.name, "action": "skipped"})
            continue
        if entry.suffix == ".wal":
            # DuckDB の WAL は対応する .db の merge_db（書込可 ATTACH）で replay 済み。
            # 単独コピーすると正準側の別 db と不整合ペアになるため、コピーせず削除のみ。
            if not dry_run:
                _remove_entry(entry)
            summary["entries"].append({"name": entry.name, "action": "skipped_wal"})
            continue
        dst = canonical / entry.name
        try:
            if entry.is_dir():
                info = merge_dir(entry, dst, dry_run=dry_run)
            elif entry.suffix == ".jsonl":
                info = merge_jsonl(entry, dst, dry_run=dry_run)
            elif entry.suffix == ".db":
                info = merge_db(entry, dst, dry_run=dry_run)
            else:
                info = merge_file_newer_wins(entry, dst, dry_run=dry_run)
            if not dry_run:
                _remove_entry(entry)
            info["name"] = entry.name
            summary["entries"].append(info)
        except Exception as e:  # 個別失敗は記録して続行（次回再実行で回収）
            summary["failures"] += 1
            summary["entries"].append({"name": entry.name, "action": "error", "error": str(e)})
            print(f"[rl-anything:migrate] {entry.name}: {e}", file=sys.stderr)

    if not dry_run and summary["failures"] == 0:
        _write_marker(canonical, source, summary)
        summary["marker_written"] = True
    return summary


def format_summary(summary: Dict[str, Any]) -> str:
    """migrate() の結果を人間可読 1 ブロックに整形する。"""
    lines = []
    mode = "（dry-run・書き込みなし）" if summary["dry_run"] else ""
    lines.append(f"DATA_DIR 一元化 {mode}")
    lines.append(f"  正準: {summary['canonical']}")
    lines.append(f"  source: {summary['source'] or '(なし — marker のみ設置)'}")
    for e in summary["entries"]:
        detail = {k: v for k, v in e.items() if k not in ("name", "action")}
        lines.append(f"  - {e['name']}: {e['action']}" + (f" {detail}" if detail else ""))
    if summary["failures"]:
        lines.append(f"  ⚠ 失敗 {summary['failures']} 件 — 再実行で回収してください")
    lines.append(f"  marker: {'書込済 ✓' if summary['marker_written'] else '未書込'}")
    return "\n".join(lines)
