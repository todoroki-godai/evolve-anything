"""rl-fleet CLI エントリポイント (argparse + status / discover / test-guard サブコマンド)。

`tokens` サブコマンドは `cli_tokens.py` に分離済み。fleet/__init__.py から
re-export される（後方互換、`bin/rl-fleet` は `fleet.main` を呼ぶ）。
"""
from __future__ import annotations

import argparse
import json as _json
import subprocess
from pathlib import Path

from . import (
    STATUS_STALE,
    _DEFAULT_MAX_WORKERS,
    _DEFAULT_TIMEOUT_SEC,
)
from .cli_tokens import _inject_token_metrics, _run_tokens
from .collectors import collect_fleet_status, write_fleet_run
from .formatters import format_status_table
from .project_loader import enumerate_projects


def main(argv: list[str] | None = None) -> int:
    """`bin/rl-fleet` エントリポイント。"""
    parser = argparse.ArgumentParser(
        prog="rl-fleet",
        description="全 PJ 横断で rl-anything の健康状態を一覧表示する",
    )
    sub = parser.add_subparsers(dest="command")
    status_p = sub.add_parser("status", help="各 PJ のステータスを表形式で表示（default）")
    for p in (parser, status_p):
        p.add_argument("--root", type=Path, default=None, help="PJ 列挙のルート（config 未設定時の fallback、default: ~/tools）")
        p.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SEC, help="PJ 毎の audit タイムアウト秒 (default: 10)")
        p.add_argument("--max-workers", type=int, default=_DEFAULT_MAX_WORKERS, help="並列数 (default: 2)")
        p.add_argument("--no-write", action="store_true", help="fleet-runs/*.jsonl への追記をスキップ")
        p.add_argument("--all", dest="show_all", action="store_true", help="STALE/NOT_ENABLED PJ も含めて全表示（デフォルトは STALE 除外）")

    discover_p = sub.add_parser(
        "discover",
        help="Claude Code が認識している PJ を検出し、track/ignore を対話的に設定",
    )
    discover_p.add_argument("--non-interactive", action="store_true", help="候補を表示するのみ（承認なし）")

    tg_p = sub.add_parser(
        "test-guard",
        help="各 PJ で no-llm-in-tests / pytest-no-llm の導入状況を一覧",
    )
    tg_sub = tg_p.add_subparsers(dest="tg_command")
    tg_status = tg_sub.add_parser("status", help="導入状況を表形式で表示（default）")
    tg_status.add_argument("--root", type=Path, default=None,
                           help="PJ 列挙のルート (fleet-config 未設定時の fallback)")
    tg_status.add_argument("--json", action="store_true", help="JSON 出力")

    tokens_p = sub.add_parser(
        "tokens",
        help="PJ別 LLM トークン消費 (TOP-N / anomaly / drill-down / backfill)",
    )
    tokens_p.add_argument("--days", type=int, default=30, help="集計期間（日）。default: 30")
    tokens_p.add_argument("--pj", type=str, default=None, help="特定 PJ にドリルダウン (canonical pj_id)")
    tokens_p.add_argument("--by", type=str, default="session", choices=["session", "model", "week"], help="--pj 指定時の分解軸")
    tokens_p.add_argument("--anomaly", action="store_true", help="WoW + cache hit 異常のみ表示")
    tokens_p.add_argument("--backfill", action="store_true", help="transcript JSONL を ingest")
    tokens_p.add_argument("--all", action="store_true", help="--backfill 時に全期間 ingest（default 90 日）")
    tokens_p.add_argument("--json", action="store_true", help="JSON 出力")

    import_p = sub.add_parser(
        "import",
        help="コミュニティスキルを GitHub またはローカルパスから import する",
    )
    import_p.add_argument(
        "source",
        help="スキルのソース: 'owner/repo', 'owner/repo/path', '/local/path', または GitHub URL",
    )
    import_p.add_argument("--force", action="store_true", help="名前衝突時に上書きする")
    import_p.add_argument("--yes", "-y", action="store_true", help="確認プロンプトをスキップ（CI用）")
    import_p.add_argument(
        "--skills-dir",
        type=Path,
        default=None,
        help="インストール先スキルディレクトリ（default: <plugin_root>/skills/）",
    )

    args = parser.parse_args(argv)

    if args.command == "discover":
        return _run_discover(args)
    if args.command == "tokens":
        return _run_tokens(args)
    if args.command == "test-guard":
        return _run_test_guard(args)
    if args.command == "import":
        return _run_import(args)

    # default: status
    return _run_status(args)


def _show_active_agents() -> str | None:
    """claude agents --json でアクティブセッション数と名前を取得して返す。

    失敗・空・不正 JSON の場合は None を返す（表示しない）。
    v2.1.145+ で利用可能。
    """
    try:
        proc = subprocess.run(
            ["claude", "agents", "--json"],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return None

    if proc.returncode != 0 or not proc.stdout:
        return None

    try:
        sessions: list = _json.loads(proc.stdout)
    except _json.JSONDecodeError:
        return None

    if not sessions:
        return None

    names = [s.get("name") or s.get("id", "?") for s in sessions[:5]]
    suffix = f" (…他 {len(sessions) - 5} 件)" if len(sessions) > 5 else ""
    return f"[fleet] アクティブセッション: {len(sessions)} 件 — {', '.join(names)}{suffix}"


def _run_status(args) -> int:
    """fleet-config.json の tracked_projects を優先、未設定時は --root で fallback。"""
    import fleet_config

    config = fleet_config.load_config()
    tracked = config.get("tracked_projects", [])

    projects: list[Path] | None = None
    new_candidates: list[Path] = []
    if tracked:
        projects = [Path(p) for p in tracked]
        # ついでに新候補を検出して hint 表示
        discovered = fleet_config.filter_valid_projects(
            fleet_config.discover_cc_projects()
        )
        new_candidates = fleet_config.diff_candidates(config, discovered)

    rows = collect_fleet_status(
        root=args.root,
        timeout=args.timeout,
        max_workers=args.max_workers,
        projects=projects,
    )
    _inject_token_metrics(rows, days=30)
    show_all = getattr(args, "show_all", False)
    if not show_all:
        stale_count = sum(1 for r in rows if r.status == STATUS_STALE)
        rows = [r for r in rows if r.status != STATUS_STALE]
    else:
        stale_count = 0
    print(format_status_table(rows), end="")
    if not show_all and stale_count:
        print(f"[fleet] STALE {stale_count} PJ を非表示にしています（--all で全表示）")
    if new_candidates:
        print(
            f"\n[fleet] 新しい PJ 候補を {len(new_candidates)} 件検出しました。"
            f" `rl-fleet discover` で track/ignore を設定してください。",
        )
    agent_line = _show_active_agents()
    if agent_line:
        print(agent_line)
    if not args.no_write:
        write_fleet_run(rows)
    return 0


def _run_test_guard(args) -> int:
    """各 PJ の test-guard 導入状況を表示。tracked_projects 優先、未設定なら --root。"""
    import fleet_config
    import test_guard

    config = fleet_config.load_config()
    tracked = config.get("tracked_projects", [])
    if tracked:
        projects = [Path(p) for p in tracked]
    else:
        root = args.root or Path.home() / "tools"
        projects = enumerate_projects(root)

    rows = test_guard.collect_test_guard_rows(projects)
    if getattr(args, "json", False):
        print(_json.dumps([{
            "pj_name": r.pj_name,
            "pj_path": str(r.pj_path),
            "languages": sorted(r.languages),
            "uses_llm": r.uses_llm,
            "has_precommit_hook": r.has_precommit_hook,
            "has_pytest_no_llm": r.has_pytest_no_llm,
            "needs_attention": r.needs_attention,
        } for r in rows], indent=2, ensure_ascii=False))
    else:
        print(test_guard.format_test_guard_table(rows), end="")
    return 0


def _run_discover(args) -> int:
    """CC の `~/.claude/projects/` から PJ を検出し、対話的に track/ignore を決定。"""
    import fleet_config

    config = fleet_config.load_config()
    discovered = fleet_config.filter_valid_projects(
        fleet_config.discover_cc_projects()
    )
    candidates = fleet_config.diff_candidates(config, discovered)

    tracked_count = len(config.get("tracked_projects", []))
    ignored_count = len(config.get("ignored_projects", []))
    print(f"[fleet] 既存設定: tracked={tracked_count}, ignored={ignored_count}")

    if not candidates:
        print("[fleet] 新しい PJ 候補はありません。")
        return 0

    print(f"[fleet] 新候補 {len(candidates)} 件:")
    for i, pj in enumerate(candidates, 1):
        markers = []
        if (pj / "CLAUDE.md").is_file():
            markers.append("CLAUDE.md")
        if (pj / ".claude").is_dir():
            markers.append(".claude/")
        marker_str = ", ".join(markers) if markers else "(none)"
        print(f"  [{i:>2}] {pj}  ({marker_str})")

    if args.non_interactive:
        print("[fleet] --non-interactive 指定のため承認せず終了。")
        return 0

    print()
    print("各 PJ について 'a' (track), 'i' (ignore), 's' (skip=次回再提案) で回答。")
    print("'q' で中断・保存。空入力は skip 扱い。")
    print()

    changed = False
    for i, pj in enumerate(candidates, 1):
        while True:
            ans = input(f"  [{i}/{len(candidates)}] {pj}: [a/i/s/q] ").strip().lower()
            if ans in ("a", "i", "s", "q", ""):
                break
            print("  → 'a' (track), 'i' (ignore), 's' (skip), 'q' (quit) のいずれかを入力")
        if ans == "q":
            print("[fleet] 中断しました。ここまでの変更を保存します。")
            break
        if ans == "a":
            fleet_config.track_project(config, pj)
            changed = True
            print(f"    → tracked")
        elif ans == "i":
            fleet_config.ignore_project(config, pj)
            changed = True
            print(f"    → ignored")
        # s or empty: skip (do nothing)

    if changed:
        from datetime import datetime, timezone
        config["last_discovery"] = datetime.now(timezone.utc).isoformat()
        fleet_config.save_config(config)
        final_tracked = len(config.get("tracked_projects", []))
        final_ignored = len(config.get("ignored_projects", []))
        print(f"\n[fleet] 保存しました: tracked={final_tracked}, ignored={final_ignored}")
    else:
        print("\n[fleet] 変更なし（skip のみ）。")
    return 0


def _run_import(args) -> int:
    """コミュニティスキルを GitHub またはローカルパスから import する。

    フロー:
    1. parse_source() でソース解析
    2. tmpdir に fetch_skill()
    3. validate_skill() で検証 → エラーなら終了
    4. preview_skill() を表示
    5. [y/N] でユーザー確認（--yes でスキップ）
    6. install_skill() でインストール
    7. 完了メッセージ
    """
    import tempfile

    from skill_importer import (
        fetch_skill,
        install_skill,
        parse_source,
        preview_skill,
        validate_skill,
    )

    # デフォルトのスキルインストール先
    if args.skills_dir is not None:
        skills_dir = args.skills_dir
    else:
        # plugin root / skills/
        plugin_root = Path(__file__).resolve().parent.parent.parent.parent
        skills_dir = plugin_root / "skills"

    # Step 1: ソース解析
    try:
        source = parse_source(args.source)
    except ValueError as e:
        print(f"[import] エラー: {e}")
        return 1

    print(f"[import] ソース: {args.source}")

    # Step 2: fetch（tmpdir に clone/copy）
    with tempfile.TemporaryDirectory(prefix="rl-fleet-import-") as tmp:
        tmp_dir = Path(tmp)
        print("[import] スキルを取得中...")
        try:
            skill_path = fetch_skill(source, tmp_dir)
        except subprocess.CalledProcessError as e:
            print(f"[import] git clone 失敗 (exit code: {e.returncode})")
            return 1
        except Exception as e:
            print(f"[import] 取得エラー: {type(e).__name__}")
            return 1

        # Step 3: validate
        metadata, result = validate_skill(
            skill_path,
            skills_dir=skills_dir if not args.force else None,
        )
        if result.warnings:
            for w in result.warnings:
                print(f"[import] 警告: {w}")
        if not result.valid:
            for e in result.errors:
                print(f"[import] エラー: {e}")
            return 1

        # Step 4: preview
        print()
        print(preview_skill(metadata))
        print()

        # Step 5: 確認
        if not args.yes:
            try:
                ans = input("インストールしますか？ [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n[import] キャンセルしました。")
                return 1
            if ans not in ("y", "yes"):
                print("[import] キャンセルしました。")
                return 1

        # Step 6: install
        try:
            install_skill(metadata, skills_dir, force=args.force)
        except FileExistsError as e:
            print(f"[import] エラー: {e}")
            return 1
        except Exception as e:
            print(f"[import] インストールエラー: {e}")
            return 1

    # Step 7: 完了
    print(f"[import] スキル '{metadata.name}' を {skills_dir / metadata.name} にインストールしました。")
    return 0
