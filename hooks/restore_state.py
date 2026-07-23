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

# SessionStart で Claude に注入する corrections_snapshot の上限（セキュリティ監査）。
# restore_state は checkpoint 全体を print するため、corrections.jsonl 全件を含む
# corrections_snapshot がそのまま Claude context に注入され、毎セッション巨大テキスト
# （実測 ~102KB）を無駄消費し、外部テキストが correction に化けた場合は無期限で再注入
# される運び屋になりうる。raw correction は復元に使われない（post_compact は件数のみ
# 参照）ため、直近 N 件 + 合計文字数上限に truncate し真の総数は別フィールドで保持する。
MAX_SNAPSHOT_ITEMS = 20
MAX_SNAPSHOT_CHARS = 8000

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

# utterance_archive.store import (optional) — staleness marker のみ読む（#430）
_utterance_store = None
try:
    from utterance_archive import store as _utterance_store
except ImportError:
    pass

# pj_slug import (optional) — SessionStart で sibling-dir worktree の slug を cache（#29/#593）
_pj_slug = None
try:
    import pj_slug as _pj_slug
except ImportError:
    pass

# daily.queue_notice import (optional) — 毎朝の evolve-queue を SessionStart で通知（#80）
_queue_notice = None
try:
    from daily import queue_notice as _queue_notice
except ImportError:
    pass

# daily.icebox_notice import (optional) — icebox 棚卸しの気づきトリガー（#194）
_icebox_notice = None
try:
    from daily import icebox_notice as _icebox_notice
except ImportError:
    pass


def _summarize_checkpoint_for_output(checkpoint: dict) -> dict:
    """SessionStart stdout に載せる checkpoint を安全なサイズに要約する（セキュリティ監査）。

    save_state（保存）は corrections_snapshot を全件ディスク保存したまま無改変。ここで縮めるのは
    「SessionStart で Claude に print する分」だけ（保存と表示の分離）。raw correction text は
    復元に使われない（post_compact は件数のみ参照）ため、直近 ``MAX_SNAPSHOT_ITEMS`` 件かつ
    合計 ``MAX_SNAPSHOT_CHARS`` 文字に truncate し、真の総数は ``corrections_snapshot_count``
    に保持する。これにより毎セッションの巨大注入と、外部テキスト由来 correction の無期限再注入
    （運び屋化）を抑える。

    corrections_snapshot キーが無い旧 checkpoint は無改変で返す（後方互換）。
    """
    snapshot = checkpoint.get("corrections_snapshot")
    if not isinstance(snapshot, list):
        return checkpoint

    total = len(snapshot)
    # 末尾＝最新（corrections.jsonl は追記順）。直近 N 件を残す。
    recent = snapshot[-MAX_SNAPSHOT_ITEMS:] if MAX_SNAPSHOT_ITEMS > 0 else []
    # 合計文字数上限: 収まるまで古い方（先頭）から落とす。単体で超過する場合は空に degrade。
    while recent and len(json.dumps(recent, ensure_ascii=False)) > MAX_SNAPSHOT_CHARS:
        recent = recent[1:]

    summarized = dict(checkpoint)
    summarized["corrections_snapshot"] = recent
    summarized["corrections_snapshot_count"] = total
    summarized["corrections_snapshot_truncated"] = len(recent) < total
    return summarized


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
    parts = ["[evolve-anything:restore_state] 作業コンテキスト復元:"]

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
            print(f"[evolve-anything:auto-trigger] {message}")
    except Exception as e:
        print(f"[evolve-anything:restore_state] trigger delivery error: {e}", file=sys.stderr)


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
        print(f"[evolve-anything:restore_state] spec-trigger error: {e}", file=sys.stderr)


def _resolve_canonical_history_file(slug: str):
    """drain の書き込み先 optimize_history を **tool 文脈の正準 DATA_DIR** に解決する（#421）。

    `optimize_history_store.DATA_DIR`/`HISTORY_ROOT` は import 時に raw `CLAUDE_PLUGIN_DATA`
    から確定するため、hook 文脈（CC が env=plugin-data を設定）でそのまま drain すると
    plugin-data dir へ書き、tool 文脈の `evolve --drain`（env 無 → fallback/正準）と
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

    #402 はリマインド表示のみで、実 drain は assistant が手で `evolve --drain` を叩く
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
        print(f"[evolve-anything:restore_state] evolve-drain pre-check error: {e}", file=sys.stderr)
        return

    try:
        history_file = _resolve_canonical_history_file(slug)
        summary = _evolve_decisions.drain_pending(slug=slug, history_file=history_file)
        accepted = summary.get("accepted") or []
        rejected = summary.get("rejected") or []
        print(
            f"[evolve-anything] evolve 提案を自動 drain しました: "
            f"accept {len(accepted)} 件 / reject {len(rejected)} 件を "
            f"fitness 母集団（optimize_history）に記録（#421）。"
        )
    except Exception as e:
        print(f"[evolve-anything:restore_state] evolve-drain error: {e}", file=sys.stderr)


def _deliver_data_dir_migration_reminder() -> None:
    """DATA_DIR 分裂が未解消なら `evolve-fleet migrate-data` を1行案内する（#364/#137）。

    判定の要は「source（plugin-data dir）に未マージのストアが残っているか」
    （``needs_migration``）であり、**marker の有無ではない**。marker は「一度 migrate
    した」事実しか意味しないため、marker 済みでも旧版 hook の書込等で分裂が再発した
    場合に案内し続ける必要がある（#137: 旧実装は ``if marker.exists(): return`` で
    再分裂を恒久沈黙させていた split-brain の根因）。

    - source に未マージストアなし → 沈黙（migrate 完了の定常状態。marker 有無を問わない）
    - source に未マージストアあり:
        - marker 無し → 初回分裂の案内（#364）
        - marker 有り → 再分裂（recurrence）の案内（#137）。旧版プラグインを掴んだ
          セッションが plugin-data に書き続けている等が原因。migrate-data 再実行で回収

    レイテンシ: ``needs_migration`` は source を1回 ``iterdir()`` するだけ（deep walk /
    DuckDB import なし）。SessionStart hot path に重い走査を足さない（#hot_hook_eager_import）。
    install ≠ enforcement 対策の検出層（#402 の drain リマインドと同型）。
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
        # marker の有無に関わらず「未マージストアが残っているか」を毎回評価する（#137）。
        if not _data_dir_migration.needs_migration(source=source):
            return  # 定常状態（source は空 / marker のみ）→ 沈黙
        canonical = _data_dir_migration.default_canonical()
        marker = canonical / _data_dir_migration._marker_name()
        if marker.exists():
            # marker 済みなのに未マージストアが再蓄積 = 分裂の再発（recurrence）。
            print(
                "[evolve-anything] DATA_DIR 分裂が再発しています（#137）。marker は設置済みですが"
                " plugin-data 側に未マージのストアが再蓄積しています（旧版 hook が書き続けている"
                "可能性）。`evolve-fleet migrate-data --dry-run` で内容確認後、"
                "`evolve-fleet migrate-data` で再度一元化してください。"
            )
        else:
            print(
                "[evolve-anything] DATA_DIR が hook/tool 文脈で分裂しています（#364）。"
                "`evolve-fleet migrate-data --dry-run` で内容確認後、"
                "`evolve-fleet migrate-data` で一元化してください。"
            )
    except Exception as e:
        print(f"[evolve-anything:restore_state] data-dir migration reminder error: {e}", file=sys.stderr)


def utterance_staleness_advisory(data_dir) -> str | None:
    """data_dir の utterance アーカイブが stale なら advisory メッセージを返す（純関数・#430）。

    observe-first pre-flight: staleness marker（last_ingest_at ファイル）を読むだけで
    DuckDB 接続も transcript 走査もしない（0.1 秒以下、pitfall_hot_hook_eager_import）。
    marker 不在 = 「未 ingest」と解釈して advisory を返す（∞ 扱い・0日でない）。
    閾値は最終 ingest > 14 日。fresh なら None。
    """
    if _utterance_store is None:
        return None
    if not _utterance_store.is_stale(data_dir, threshold_days=14):
        return None
    last = _utterance_store.read_last_ingest_at(data_dir)
    detail = "未 ingest（marker なし）" if last is None else f"最終 ingest {last}"
    return (
        "[evolve-anything] utterance アーカイブが 14 日以上 ingest されていません"
        f"（{detail}, #430）。`evolve-fleet ingest` で取り込むか、`evolve`/`audit` を回すと"
        "自動取り込みされます。"
    )


def _deliver_utterance_staleness() -> None:
    """utterance アーカイブの staleness advisory を SessionStart に出す（#430・安全弁）。

    実環境ガード: `CLAUDE_PLUGIN_DATA` が CC install レイアウト配下のときだけ判定する
    （migration リマインドと同型）。テスト isolation の tmp env / 非 hook 文脈では実環境を
    一切 probe せず沈黙し、JSON stdout を汚さない。advisory は強制でなく安全弁
    （本線は evolve/audit 同居、install ≠ enforcement）。
    """
    if _utterance_store is None or _data_dir_migration is None:
        return
    try:
        import os as _os
        import rl_common  # 遅延 import（patch 追従）

        env = _os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if not env:
            return  # hook 文脈でなければ判定しない（実環境を probe しない）
        if not _data_dir_migration.is_cc_install_layout(Path(env)):
            return  # テスト isolation / custom 環境
        data_dir = rl_common.resolve_data_dir(env)
        message = utterance_staleness_advisory(data_dir)
        if message:
            print(message)
    except Exception as e:
        print(f"[evolve-anything:restore_state] utterance staleness check error: {e}", file=sys.stderr)


def _deliver_evolve_queue_notice() -> None:
    """毎朝の `fleet queue` が保存した evolve-queue.json の待ち PJ を systemMessage で通知する（#80）。

    無人で回せる決定論パイプライン（ingest→queue）の結果を、対話セッション開始時にユーザーが
    気づける形で surface する（適用＝evolve 自体は対話セッションで人間が承認）。

    実環境ガード: `CLAUDE_PLUGIN_DATA` が CC install レイアウト配下のときだけ判定する
    （utterance staleness と同型）。テスト isolation の tmp env / 非 hook 文脈では実環境を
    一切 probe せず沈黙し、JSON stdout を汚さない。

    observe-first pre-flight: evolve-queue.json を読むだけ（DuckDB 接続なし・走査なし、
    pitfall_hot_hook_eager_import）。queue が空 or ファイル無し → 沈黙。
    出力は `systemMessage` を含む 1 行 JSON（ADR-038 = user 向けチャネル）。
    fail-safe: 例外で hook を落とさない（try/except で degrade、stderr に 1 行）。
    """
    if _queue_notice is None or _data_dir_migration is None:
        return
    try:
        import rl_common  # 遅延 import（patch 追従・他 deliver と同型）

        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if not env:
            return  # hook 文脈でなければ判定しない（実環境を probe しない）
        if not _data_dir_migration.is_cc_install_layout(Path(env)):
            return  # テスト isolation / custom 環境
        data_dir = rl_common.resolve_data_dir(env)
        queue_data = _queue_notice.read_queue(data_dir)
        output = _queue_notice.queue_notice_output(queue_data)
        if output:
            print(json.dumps(output, ensure_ascii=False))
    except Exception as e:
        print(f"[evolve-anything:restore_state] evolve-queue notice error: {e}", file=sys.stderr)


def _deliver_icebox_notice() -> None:
    """毎朝の `gh issue list --label icebox --state closed` が保存した icebox-status.json の
    棚卸し気づきトリガーを systemMessage で通知する（#194）。

    icebox は evolve-anything 自身の GitHub issue backlog なので、**本体リポジトリ
    （`.claude-plugin/plugin.json` を持つ repo）で作業しているときだけ**判定する。他 PJ で
    作業中は plugin_self 判定で即 return し、何も print しない（沈黙）。

    実環境ガード: `CLAUDE_PLUGIN_DATA` が CC install レイアウト配下のときだけ判定する
    （evolve-queue notice と同型）。テスト isolation の tmp env / 非 hook 文脈では実環境を
    一切 probe せず沈黙し、JSON stdout を汚さない。

    observe-first pre-flight: icebox-status.json を読むだけ（DuckDB 接続なし・走査なし）。
    閾値未満 or ファイル無し → 沈黙。出力は `systemMessage` を含む 1 行 JSON
    （ADR-038 = user 向けチャネル）。
    fail-safe: 例外で hook を落とさない（try/except で degrade、stderr に 1 行）。
    """
    if _icebox_notice is None or _data_dir_migration is None:
        return
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if not project_dir:
            return  # cwd 不明なら plugin_self 判定不能 = 沈黙
        if not (Path(project_dir) / ".claude-plugin" / "plugin.json").exists():
            return  # evolve-anything 本体以外の PJ では沈黙

        import rl_common  # 遅延 import（patch 追従・他 deliver と同型）

        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if not env:
            return  # hook 文脈でなければ判定しない（実環境を probe しない）
        if not _data_dir_migration.is_cc_install_layout(Path(env)):
            return  # テスト isolation / custom 環境
        data_dir = rl_common.resolve_data_dir(env)
        status = _icebox_notice.read_icebox_status(data_dir)
        threshold_days = rl_common.load_user_config().get("icebox_review_threshold_days", 30)
        output = _icebox_notice.icebox_notice_output(status, threshold_days=threshold_days)
        if output:
            print(json.dumps(output, ensure_ascii=False))
    except Exception as e:
        print(f"[evolve-anything:restore_state] icebox notice error: {e}", file=sys.stderr)


def _persist_pj_slug_cache() -> None:
    """sibling-dir worktree の write 時 slug 解決のため authoritative slug を cache する（#29/#593）。

    背景: ``pj_slug_fast``（hooks hot path・subprocess 禁止）は ``/.claude/worktrees/`` マーカー
    配下の worktree しか親 repo へ畳めない。sibling-dir worktree（例 ``rl-anything-wt/issue-593``）は
    マーカーが無く、write 時に basename が「幻 PJ slug」として記録され続ける（#593 残課題）。

    そこで hot path でない SessionStart で1回だけ ``resolve_pj_slug(cwd)``（authoritative・
    git-common-dir 親・subprocess 可）を解決し、``{cwd: slug}`` を DATA_DIR の cache に書く。
    以後 ``pj_slug_fast`` はマーカーで畳めなかったとき本 cache を参照して本体 slug を返す
    （subprocess なし＝hot-path 安全を維持）。read/write 同一 slug の原則（#492）を sibling
    worktree にも拡張する。

    DATA_DIR は他 deliver 関数と同じく ``rl_common.resolve_data_dir``（env 優先・#364）で解決する。
    fail-safe: 例外で hook を落とさない（try/except で degrade、stderr に 1 行）。
    """
    if _pj_slug is None:
        return
    try:
        import rl_common  # 遅延 import（patch 追従・他 deliver と同型）

        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if not project_dir:
            return  # cwd 不明なら cache に書かない
        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        data_dir = rl_common.resolve_data_dir(env)
        slug = _pj_slug.resolve_pj_slug(project_dir)  # authoritative（subprocess 可）
        if not slug or slug == _pj_slug.UNATTRIBUTED_SLUG:
            return  # 帰属不能（git 外の素 dir 等）は cache に書かない
        _pj_slug.write_pj_slug_cache(project_dir, slug, data_dir=data_dir)
    except Exception as e:
        print(f"[evolve-anything:restore_state] pj_slug cache error: {e}", file=sys.stderr)


def handle_session_start(event: dict) -> None:
    """SessionStart イベントを処理する。"""
    # sibling-dir worktree の write 時 slug 解決用 cache を更新（#29/#593）
    _persist_pj_slug_cache()
    # Deliver pending trigger messages first
    _deliver_pending_trigger()
    # 仕様未追従マージの提案
    _deliver_spec_drift()
    # apply 済み evolve 提案の SessionStart 自動 drain（#421, #402 リマインドからの昇格）
    _deliver_evolve_drain()
    # DATA_DIR 分裂の未解消検出（#364）
    _deliver_data_dir_migration_reminder()
    # utterance アーカイブの staleness advisory（#430・marker 読みのみ）
    _deliver_utterance_staleness()
    # 毎朝の evolve-queue 待ち PJ 通知（#80・evolve-queue.json 読みのみ）
    _deliver_evolve_queue_notice()
    # icebox 棚卸しの気づきトリガー（#194・evolve-anything 本体リポジトリのみ・icebox-status.json 読みのみ）
    _deliver_icebox_notice()

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "") or None
    checkpoint = common.find_latest_checkpoint(project_dir)

    if not checkpoint:
        return

    try:
        # 復元した状態を stdout に出力（Claude Code が利用可能）
        session_title = _make_session_title(checkpoint)
        # corrections_snapshot を上限内に要約してから print する（保存と表示の分離・監査対応）
        output: dict = {
            "restored": True,
            "checkpoint": _summarize_checkpoint_for_output(checkpoint),
        }
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
        print(f"[evolve-anything:restore_state] restore failed: {e}", file=sys.stderr)


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
        print(f"[evolve-anything:restore_state] parse error: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[evolve-anything:restore_state] unexpected error: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
