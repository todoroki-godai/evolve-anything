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


def _deliver_evolve_drain_reminder() -> None:
    """前回 evolve で emit した提案が apply 済みなのに未 drain なら surface する（#402）。

    ingest（Step 7.8 drain）が SKILL.md prose 依存だった enforcement gap の検出層。
    `undrained_applied` は optimize_history を読まず marker の before_sha と現ディスク sha を
    突合するだけなので、hook 文脈でも DATA_DIR split（#358）を踏まない。timing 問題は
    「次 SessionStart で見る」ことで構造的に回避（apply は前セッションで完了済み）。
    apply 済みが無ければ沈黙、drain 実行で marker が消えて自然終息。
    """
    if _evolve_decisions is None:
        return
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        cwd = Path(project_dir) if project_dir else None
        slug = _evolve_decisions.resolve_slug(cwd)
        applied = _evolve_decisions.undrained_applied(slug)
        if applied:
            names = ", ".join(sorted({p.get("skill_name", "?") for p in applied}))
            print(
                f"[rl-anything] 未 drain の適用済み evolve 提案が {len(applied)} 件あります（{names}）。"
                "`rl-evolve --drain` で fitness 母集団（optimize_history）に記録してください（#402）。"
            )
    except Exception as e:
        print(f"[rl-anything:restore_state] evolve-drain reminder error: {e}", file=sys.stderr)


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
    # 未 drain の適用済み evolve 提案の記録リマインド（#402）
    _deliver_evolve_drain_reminder()
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
