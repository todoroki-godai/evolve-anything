"""evolve-fleet CLI エントリポイント (argparse + status / discover / test-guard サブコマンド)。

`tokens` サブコマンドは `cli_tokens.py` に分離済み。fleet/__init__.py から
re-export される（後方互換、`bin/evolve-fleet` は `fleet.main` を呼ぶ）。
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
from .collectors import collect_fleet_status, detect_equal_issue_counts, write_fleet_run
from .formatters import format_status_json, format_status_table
from .project_loader import enumerate_projects
from .recall import format_hits, recall, reinforce_recall_hits


def main(argv: list[str] | None = None) -> int:
    """`bin/evolve-fleet` エントリポイント。"""
    parser = argparse.ArgumentParser(
        prog="evolve-fleet",
        description="全 PJ 横断で evolve-anything の健康状態を一覧表示する",
    )
    sub = parser.add_subparsers(dest="command")
    status_p = sub.add_parser("status", help="各 PJ のステータスを表形式で表示（default）")
    for p in (parser, status_p):
        p.add_argument("--root", type=Path, default=None, help="PJ 列挙のルート（config 未設定時の fallback、default: ~/tools）")
        p.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SEC, help="PJ 毎の audit タイムアウト秒 (default: 30、超過時は前回 cache を CACHED 表示)")
        p.add_argument("--max-workers", type=int, default=_DEFAULT_MAX_WORKERS, help="並列数 (default: 2)")
        p.add_argument("--no-write", action="store_true", help="fleet-runs/*.jsonl への追記をスキップ")
        p.add_argument("--all", dest="show_all", action="store_true", help="STALE/NOT_ENABLED PJ も含めて全表示（デフォルトは STALE 除外）")
        p.add_argument("--json", action="store_true", help="JSON 出力（複数 PJ の env_score / 導入状況を構造化・#53）")

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

    plugins_p = sub.add_parser(
        "plugins",
        help="インストール済み CC プラグインの最新性を診断（update/drift 検出、決定論）",
    )
    plugins_p.add_argument("--root", type=Path, default=None,
                           help="plugins ルート (default: ~/.claude/plugins)")
    plugins_p.add_argument("--json", action="store_true", help="JSON 出力")

    migrate_p = sub.add_parser(
        "migrate-data",
        help="DATA_DIR hook/tool 分裂を一元化（plugin-data 側ストアを ~/.claude/evolve-anything にマージ + marker 設置、#364）",
    )
    migrate_p.add_argument("--dry-run", action="store_true",
                           help="マージ内容の確認のみ（書き込みゼロ）。他の CC セッションを閉じた idle 時の実行を推奨（並行書き込み窓を回避）")
    migrate_p.add_argument("--canonical", type=Path, default=None, help="正準 dir（default: ~/.claude/evolve-anything）")
    migrate_p.add_argument("--source", type=Path, default=None, help="旧 plugin-data dir（default: install レイアウトを自動探索）")

    pjslug_p = sub.add_parser(
        "migrate-pj-slug",
        help="既存レコードの幻PJ slug（worktree フルパス等）を worktree 安全 slug へ遡及正規化（全7ストア: corrections/subagents/usage/workflows/skill_activations/errors/usage-registry/sessions.db、#593/#602）",
    )
    pjslug_p.add_argument("--apply", action="store_true",
                          help="実書込（既定は dry-run = 1バイトも書かず正規化予定件数だけ表示）")
    pjslug_p.add_argument("--data-dir", type=Path, default=None,
                          help="対象 DATA_DIR（default: ~/.claude/evolve-anything）。他の CC セッションを閉じた idle 時の --apply を推奨")

    uttr_p = sub.add_parser(
        "ingest",
        help="全PJ human 発話を utterances.db に増分 ingest（#430・ゼロ LLM）",
    )
    uttr_p.add_argument("--root", type=Path, default=None,
                        help="projects ルート (default: ~/.claude/projects)")
    uttr_p.add_argument("--days", type=int, default=None,
                        help="mtime フィルタ（日）。default: 無制限（初回 backfill）")
    uttr_p.add_argument("--max-files", type=int, default=None,
                        help="PJ ごとの取り込みファイル上限（backfill bench 用サンプリング）")
    uttr_p.add_argument("--quiet", action="store_true", help="進捗 stderr を抑制")

    recall_p = sub.add_parser(
        "recall",
        help="全 PJ の memory を横断 keyword 検索（決定論・LLM 非依存）",
    )
    recall_p.add_argument("query", help="検索クエリ（空白区切りの token）")
    recall_p.add_argument("--limit", type=int, default=10, help="表示件数の上限 (default: 10)")
    recall_p.add_argument("--root", type=Path, default=None,
                          help="projects ルート (default: ~/.claude/projects)")
    recall_p.add_argument("--json", action="store_true", help="JSON 出力")

    queue_p = sub.add_parser(
        "queue",
        help="学習素材ベースで『今 evolve すべき PJ』を決定論・ゼロ LLM で列挙（#79）",
    )
    queue_p.add_argument(
        "--threshold", type=int, default=_default_queue_threshold(),
        help="待ち判定の学習素材（weak 未処理 + 新規 corr）合算下限。"
             "env EVOLVE_QUEUE_THRESHOLD で上書き可（default: 5・実コーパス dry-run で決定）",
    )
    queue_p.add_argument("--root", type=Path, default=None,
                         help="PJ 列挙のルート（fleet-config 未設定時の fallback、default: ~/tools）")
    queue_p.add_argument("--timeout", type=float, default=_DEFAULT_TIMEOUT_SEC,
                         help="（予約・現状 queue は audit を回さないため未使用）")
    queue_p.add_argument("--max-workers", type=int, default=_DEFAULT_MAX_WORKERS,
                         help="（予約・現状 queue は逐次集計のため未使用）")
    queue_p.add_argument("--json", action="store_true", help="JSON 出力（Phase 1b #80 契約）")

    args = parser.parse_args(argv)

    if args.command == "discover":
        return _run_discover(args)
    if args.command == "tokens":
        return _run_tokens(args)
    if args.command == "test-guard":
        return _run_test_guard(args)
    if args.command == "import":
        return _run_import(args)
    if args.command == "plugins":
        return _run_plugins(args)
    if args.command == "recall":
        return _run_recall(args)
    if args.command == "ingest":
        return _run_ingest(args)
    if args.command == "migrate-data":
        return _run_migrate_data(args)
    if args.command == "migrate-pj-slug":
        return _run_migrate_pj_slug(args)
    if args.command == "queue":
        return _run_queue(args)

    # default: status
    return _run_status(args)


def _run_ingest(args: argparse.Namespace) -> int:
    """ingest サブコマンド: 全PJ human 発話を utterances.db に増分 ingest する（#430）。"""
    from utterance_archive import ingest as utterance_ingest

    res = utterance_ingest.ingest_all_projects(
        projects_root=args.root,
        days=args.days,
        max_files=args.max_files,
        progress=not args.quiet,
    )
    if res.get("duckdb") is False:
        print("[fleet:ingest] DuckDB 未インストールのため utterances.db を作成できません。")
        return 1
    print(
        f"[fleet:ingest] inserted={res.get('inserted', 0)} "
        f"files={res.get('files_processed', 0)} "
        f"projects={res.get('projects', 0)} "
        f"elapsed={res.get('elapsed_s', 0.0):.1f}s"
    )
    return 0


def _run_plugins(args: argparse.Namespace) -> int:
    """plugins サブコマンド: インストール済み CC プラグインの最新性を診断する。"""
    from .plugin_freshness import check_plugin_freshness, format_plugin_freshness_table

    rows = check_plugin_freshness(plugins_root=args.root)
    print(format_plugin_freshness_table(rows, as_json=args.json), end="")
    return 0


def _run_migrate_data(args: argparse.Namespace) -> int:
    """migrate-data サブコマンド: DATA_DIR 分裂の一元化 migration を実行する（#364）。"""
    import data_dir_migration

    kwargs = {"dry_run": args.dry_run}
    if args.canonical is not None:
        kwargs["canonical"] = args.canonical
    if args.source is not None:
        kwargs["source"] = args.source
    summary = data_dir_migration.migrate(**kwargs)
    print(data_dir_migration.format_summary(summary))
    return 1 if summary["failures"] else 0


def _run_migrate_pj_slug(args: argparse.Namespace) -> int:
    """migrate-pj-slug: 既存レコードの幻PJ slug を worktree 安全 slug へ遡及正規化（#593）。"""
    import pj_slug_backfill

    if args.data_dir is not None:
        data_dir = args.data_dir
    else:
        import data_dir_migration
        data_dir = data_dir_migration.default_canonical()

    summary = pj_slug_backfill.backfill(data_dir, apply=args.apply)
    print(pj_slug_backfill.format_summary(summary))
    return 0


def _run_recall(args: argparse.Namespace) -> int:
    """recall サブコマンド: 全 PJ memory を横断検索して結果を出力する。"""
    hits = recall(args.query, limit=args.limit, projects_root=args.root)
    print(format_hits(hits, as_json=args.json))
    # recall ヒットを access proxy として reinforce（#18）。書き込み失敗は recall 体験を壊さない。
    reinforce_recall_hits(hits)
    return 0


# --- queue サブコマンド（#79 Phase 1a）---------------------------------------

_DEFAULT_QUEUE_THRESHOLD = 5  # 実コーパス dry-run で決定（gap: active 最小 5 vs trickle 2）


def _default_queue_threshold() -> int:
    """queue 閾値の既定。env EVOLVE_QUEUE_THRESHOLD があれば優先（不正値は既定）。"""
    import os

    raw = os.environ.get("EVOLVE_QUEUE_THRESHOLD")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return _DEFAULT_QUEUE_THRESHOLD


def _gather_queue_result(args: argparse.Namespace) -> dict:
    """tracked PJ を列挙し、各 PJ の学習素材を実ストアから集計して queue result を返す。

    PJ 列挙は status と同じく fleet-config.json の tracked_projects を優先（未設定なら
    --root fallback）。pj_slug は project_name_from_dir で本体 repo slug に正規化し
    weak_signals.pj_slug / corrections の正規化 slug と名前空間を揃える。weak/corr は
    canonical DATA_DIR の各ストアから読む（path=None で union read）。
    """
    from datetime import datetime, timezone

    import fleet_config
    from rl_common import project_name_from_dir

    from . import _current_data_dir
    from .collectors import aggregate_subagents_by_project
    from .project_loader import enumerate_projects
    from .queue import build_queue_result
    from .queue_state import read_last_evolve

    config = fleet_config.load_config()
    tracked = config.get("tracked_projects", [])
    if tracked:
        projects = [Path(p) for p in tracked]
    else:
        root = args.root or Path.home() / "tools"
        projects = enumerate_projects(root)

    pj_slugs = sorted({project_name_from_dir(str(p)) for p in projects if p})
    # slug → 実パス map（queue が dead PJ を skip し、利用側が親 dir 推測なしに /cd できるように
    # project_path を伝播させる・#79）。同 slug 複数 path は最初の登録を採る（setdefault）。
    pj_paths: dict = {}
    for p in projects:
        if not p:
            continue
        pj_paths.setdefault(project_name_from_dir(str(p)), str(p))

    data_dir = _current_data_dir()
    weak_path = data_dir / "weak_signals.jsonl"
    corr_path = data_dir / "corrections.jsonl"

    last_evolve_map = read_last_evolve(data_dir=data_dir)
    subagent_counts = aggregate_subagents_by_project()
    activity_map = {
        slug: {"subagents": subagent_counts.get(slug, 0), "sessions": 0}
        for slug in pj_slugs
    }

    return build_queue_result(
        pj_slugs=pj_slugs,
        threshold=args.threshold,
        weak_signals_path=weak_path if weak_path.exists() else None,
        corrections_path=corr_path,
        last_evolve_map=last_evolve_map,
        activity_map=activity_map,
        generated_at=datetime.now(timezone.utc).isoformat(),
        pj_paths=pj_paths,
    )


def _run_queue(args: argparse.Namespace) -> int:
    """queue サブコマンド: 学習素材ベースで evolve 待ち PJ を列挙する（#79）。"""
    from .formatters import format_queue_table

    result = _gather_queue_result(args)
    if getattr(args, "json", False):
        print(_json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(format_queue_table(result), end="")
    return 0


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

    names = [_session_label(s) for s in sessions[:5]]
    suffix = f" (…他 {len(sessions) - 5} 件)" if len(sessions) > 5 else ""
    return f"[fleet] アクティブセッション: {len(sessions)} 件 — {', '.join(names)}{suffix}"


def _session_label(session: dict) -> str:
    """active session の表示ラベル（#71）。

    `claude agents --json` の session は通常 ``name``/``id`` を持たず
    ``cwd``/``sessionId`` のみ（実測 2026-06-23）。旧実装は ``name or id`` だけ見て
    全件 ``?`` に化けていた。``name`` → ``cwd`` basename → ``sessionId`` 先頭8桁の順で
    意味のあるラベルに落とす。
    """
    name = session.get("name")
    if name:
        return str(name)
    cwd = session.get("cwd")
    if isinstance(cwd, str) and cwd:
        base = Path(cwd).name
        if base:
            return base
    sid = session.get("sessionId") or session.get("id")
    if isinstance(sid, str) and sid:
        return sid[:8]
    return "?"


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
    if getattr(args, "json", False):
        # JSON モード: stdout を JSON のみに保つため alarm/hint/agent 行は出さない（#53）。
        print(format_status_json(rows), end="")
        if not args.no_write:
            write_fleet_run(rows)
        return 0
    print(format_status_table(rows), end="")
    # 複数 PJ の ISSUES total が完全一致 = 検出パイプラインの測定バグの強シグナル（#419）
    for alarm in detect_equal_issue_counts(rows):
        pjs = ", ".join(alarm["projects"])
        print(
            f"[fleet] ⚠ ISSUES total が {len(alarm['projects'])} PJ で同値 "
            f"({alarm['total']}): {pjs} — 独立走査で同値は測定バグの疑い。"
            f"audit 検出パイプラインを確認してください（#419）"
        )
    if not show_all and stale_count:
        print(f"[fleet] STALE {stale_count} PJ を非表示にしています（--all で全表示）")
    if new_candidates:
        print(
            f"\n[fleet] 新しい PJ 候補を {len(new_candidates)} 件検出しました。"
            f" `evolve-fleet discover` で track/ignore を設定してください。",
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
