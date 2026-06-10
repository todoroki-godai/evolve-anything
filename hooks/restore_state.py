#!/usr/bin/env python3
"""SessionStart hook — チェックポイントから進化状態を復元する。

保存済み checkpoint.json が存在する場合、前回の進化状態を復元して
stdout に JSON で出力する。
"""
import json
import os
import sys
from pathlib import Path

import common

# trigger_engine import (optional)
_trigger_engine = None
try:
    _plugin_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))
    from trigger_engine import read_and_delete_pending_trigger
    _trigger_engine = True
except ImportError:
    pass

# spec_trigger import (optional) — main 着地の仕様未追従変更を検出（ADR-044）
_spec_trigger = None
try:
    import spec_trigger as _spec_trigger
except ImportError:
    pass

# evolve_decisions import (optional) — 未 drain の適用済み提案を検出（#402）
_evolve_decisions = None
try:
    import evolve_decisions as _evolve_decisions
except ImportError:
    pass

# data_dir_migration import (optional) — DATA_DIR 分裂の未解消を検出（#364）
_data_dir_migration = None
try:
    import data_dir_migration as _data_dir_migration
except ImportError:
    pass


def _make_session_title(checkpoint: dict) -> str:
    """checkpoint から claude agents 表示用のセッションタイトルを生成する。"""
    work_context = checkpoint.get("work_context") or {}
    branch = work_context.get("git_branch", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    pj_name = Path(project_dir).name if project_dir else ""
    if pj_name and branch:
        return f"{pj_name} | {branch}"
    if pj_name:
        return pj_name
    if branch:
        return branch
    return ""


def _format_work_context_summary(work_context: dict) -> str:
    """work_context から人間可読なサマリーを生成する。"""
    parts = ["[rl-anything:restore_state] 作業コンテキスト復元:"]

    branch = work_context.get("git_branch", "")
    if branch:
        parts.append(f"  ブランチ: {branch}")

    commits = work_context.get("recent_commits", [])
    if commits:
        parts.append(f"  完了: {', '.join(commits)}")

    files = work_context.get("uncommitted_files", [])
    if files:
        parts.append(f"  作業中: {', '.join(files)}")

    if len(parts) == 1:
        return ""
    return "\n".join(parts)


def _deliver_pending_trigger() -> None:
    """pending-trigger.json があれば読み取り、提案メッセージを stdout に出力する。"""
    if _trigger_engine is None:
        return
    try:
        data = read_and_delete_pending_trigger()
        if data is None:
            return
        message = data.get("message", "")
        if message:
            print(f"[rl-anything:auto-trigger] {message}")
    except Exception as e:
        print(f"[rl-anything:restore_state] trigger delivery error: {e}", file=sys.stderr)


def _deliver_spec_drift() -> None:
    """main に着地した仕様未追従の変更があれば spec-keeper 提案を stdout に出す（ADR-044）。

    fail-safe: spec_trigger 内部で git/IO 例外は握られるが、念のため全体を保護する。
    """
    if _spec_trigger is None:
        return
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        cwd = Path(project_dir) if project_dir else None
        result = _spec_trigger.detect(cwd=cwd)
        message = result.get("message")
        if message:
            print(message)
    except Exception as e:
        print(f"[rl-anything:restore_state] spec-trigger error: {e}", file=sys.stderr)


def _resolve_canonical_history_file(slug: str):
    """drain の書き込み先 optimize_history を **tool 文脈の正準 DATA_DIR** に解決する（#421）。

    `optimize_history_store.DATA_DIR`/`HISTORY_ROOT` は import 時に raw `CLAUDE_PLUGIN_DATA`
    から確定するため、hook 文脈（CC が env=plugin-data を設定）でそのまま drain すると
    plugin-data dir へ書き、tool 文脈の `rl-evolve --drain`（env 無 → fallback/正準）と
    書き込み先が割れる（pitfall_datadir_hook_tool_split, #358/#364）。

    そこで marker ゲート付きの `rl_common.resolve_data_dir` で tool reader と同じ正準 dir を
    解決し、`<canonical>/optimize_history/<sanitized_slug>.jsonl` を返して drain_pending に
    history_file として渡す。これで hook 文脈でも drain は tool reader と同一ファイルに書く。
    """
    import rl_common
    import optimize_history_store as _ohs

    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    canonical = rl_common.resolve_data_dir(env)
    return canonical / "optimize_history" / f"{_ohs._sanitize_slug(slug)}.jsonl"


def _deliver_evolve_drain() -> None:
    """前回 evolve で emit→apply 済みの提案を SessionStart で自動 drain する（#421）。

    #402 はリマインド表示のみで、実 drain は assistant が手で `rl-evolve --drain` を叩く
    SKILL.md prose 依存（install ≠ enforcement）だった。本関数はそれを人手ゼロの自動回収へ
    昇格させ、apply 済み提案を optimize_history（fitness 母集団）へ決定論記録する。

    レイテンシ予算（pitfall_hot_hook_eager_import）:
      - pending marker が無いケースは MARKER_ROOT のディレクトリ存在チェック → slug 解決 →
        marker ファイル存在チェックの軽い判定で early-return し、重い経路（optimize_history
        書き込みを伴う drain_pending）に入らない。duckdb 等の eager import もしない。
    DATA_DIR split（#364）:
      - drain の書き込み先は `_resolve_canonical_history_file` で tool reader と同一の正準
        DATA_DIR に固定する。
    fail-safe:
      - drain 中の例外で hook を落とさない（try/except で degrade、stderr に 1 行）。
    冪等: drain_pending（ingest）が `{pid}_{kind}` entry_id で dedup するため再発火しても二重記録なし。
    """
    if _evolve_decisions is None:
        return
    try:
        # 軽量 early-return: marker root が無ければ未 drain 提案は存在しない。
        if not _evolve_decisions.MARKER_ROOT.exists():
            return
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        cwd = Path(project_dir) if project_dir else None
        slug = _evolve_decisions.resolve_slug(cwd)
        if not _evolve_decisions.marker_path(slug).exists():
            return  # この slug に未 drain marker なし → 沈黙（重い drain に入らない）
        # apply 済み（ディスク sha が before と異なる）entry が無ければ drain しない。
        # undrained_applied は optimize_history を読まず marker の sha 突合だけ（#358 を踏まない）。
        # 「未 apply のまま marker をクリア」して将来の apply を取り逃すのを防ぐため、ここを
        # ゲートにする（apply されるまで marker は残し、次 SessionStart で再評価する）。
        applied = _evolve_decisions.undrained_applied(slug)
        if not applied:
            return
    except Exception as e:
        print(f"[rl-anything:restore_state] evolve-drain pre-check error: {e}", file=sys.stderr)
        return

    try:
        history_file = _resolve_canonical_history_file(slug)
        summary = _evolve_decisions.drain_pending(slug=slug, history_file=history_file)
        accepted = summary.get("accepted") or []
        rejected = summary.get("rejected") or []
        print(
            f"[rl-anything] evolve 提案を自動 drain しました: "
            f"accept {len(accepted)} 件 / reject {len(rejected)} 件を "
            f"fitness 母集団（optimize_history）に記録（#421）。"
        )
    except Exception as e:
        print(f"[rl-anything:restore_state] evolve-drain error: {e}", file=sys.stderr)


def _deliver_data_dir_migration_reminder() -> None:
    """DATA_DIR 分裂が未解消なら `rl-fleet migrate-data` を1行案内する（#364）。

    marker（一元化済）があれば沈黙。marker 無し & 旧 plugin-data dir に
    ストアが残っていれば案内する。migration 実行で marker が立ち自然終息
    （install ≠ enforcement 対策の検出層、#402 の drain リマインドと同型）。
    """
    if _data_dir_migration is None:
        return
    try:
        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if not env:
            return  # hook 文脈でなければ判定しない（probe で実環境を読まない）
        source = Path(env)
        if not _data_dir_migration.is_cc_install_layout(source):
            return  # テスト isolation / custom 環境
        canonical = _data_dir_migration.default_canonical()
        marker = canonical / _data_dir_migration._marker_name()
        if marker.exists():
            return
        if _data_dir_migration.needs_migration(source=source):
            print(
                "[rl-anything] DATA_DIR が hook/tool 文脈で分裂しています（#364）。"
                "`rl-fleet migrate-data --dry-run` で内容確認後、"
                "`rl-fleet migrate-data` で一元化してください。"
            )
    except Exception as e:
        print(f"[rl-anything:restore_state] data-dir migration reminder error: {e}", file=sys.stderr)


def handle_session_start(event: dict) -> None:
    """SessionStart イベントを処理する。"""
    # Deliver pending trigger messages first
    _deliver_pending_trigger()
    # 仕様未追従マージの提案
    _deliver_spec_drift()
    # apply 済み evolve 提案の SessionStart 自動 drain（#421, #402 リマインドからの昇格）
    _deliver_evolve_drain()
    # DATA_DIR 分裂の未解消検出（#364）
    _deliver_data_dir_migration_reminder()

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "") or None
    checkpoint = common.find_latest_checkpoint(project_dir)

    if not checkpoint:
        return

    try:
        # 復元した状態を stdout に出力（Claude Code が利用可能）
        session_title = _make_session_title(checkpoint)
        output: dict = {"restored": True, "checkpoint": checkpoint}
        if session_title:
            output["hookSpecificOutput"] = {"sessionTitle": session_title}
        print(json.dumps(output, ensure_ascii=False))

        # work_context がある場合はサマリーも出力
        work_context = checkpoint.get("work_context")
        if work_context:
            summary = _format_work_context_summary(work_context)
            if summary:
                print(summary)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[rl-anything:restore_state] restore failed: {e}", file=sys.stderr)


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            # stdin なしでも checkpoint 復元は試みる
            handle_session_start({})
            return
        event = json.loads(raw)
        handle_session_start(event)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[rl-anything:restore_state] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[rl-anything:restore_state] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
