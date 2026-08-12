#!/usr/bin/env python3
"""SessionStart hook — チェックポイントから進化状態を復元する。

保存済み checkpoint.json が存在する場合、前回の進化状態を復元して
stdout に JSON で出力する。

ADR-054 Phase 0（B1・SessionStart 通知の1行化）: 9系統の通知（+work_context summary）は
それぞれ「印字を行わない収集関数」（``_build_*_output``）が ``NotificationItem`` を返し、
``handle_session_start`` が1箇所で merge・print・commit（副作用の確定）を行う。収集関数・
``NotificationItem``・digest/merge ロジックの実体は ``scripts/lib/session_notify/``
パッケージにあり（file-size-budget.md の 800行 hard limit 対応・純粋な移動で振る舞いは
不変）、本ファイルは「収集を呼ぶ → merge → print → commit」の薄いオーケストレーションのみを
持つ。詳細設計は ``docs/decisions/drafts/054-phase0-notification-routing.md``。
"""
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Callable

import common

_plugin_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_plugin_root / "scripts" / "lib"))

from session_notify import (  # noqa: E402
    NotificationItem,
    peek_pending_trigger,
    delete_pending_trigger,
    _build_pending_trigger_output,
    _build_spec_drift_output,
    _build_evolve_drain_output,
    _build_data_dir_migration_output,
    utterance_staleness_advisory,
    _build_utterance_staleness_output,
    _resolve_queue_data,
    _build_evolve_queue_output,
    _build_session_proposal_output,
    _build_judge_cap_output,
    _build_icebox_output,
    _merge_notification_text,
    _build_additional_context,
    _pj_slug,
    _queue_notice,
)

# SessionStart で Claude に注入する corrections_snapshot の上限（セキュリティ監査）。
# restore_state は checkpoint 全体を print するため、corrections.jsonl 全件を含む
# corrections_snapshot がそのまま Claude context に注入され、毎セッション巨大テキスト
# （実測 ~102KB）を無駄消費し、外部テキストが correction に化けた場合は無期限で再注入
# される運び屋になりうる。raw correction は復元に使われない（post_compact は件数のみ
# 参照）ため、直近 N 件 + 合計文字数上限に truncate し真の総数は別フィールドで保持する。
MAX_SNAPSHOT_ITEMS = 20
MAX_SNAPSHOT_CHARS = 8000


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
    """work_context から人間可読な圧縮サマリーを生成する（ADR-054 Phase 0 §4.5/§6.4）。

    直近コミットは件数 + 先頭1件（50字超は truncate）。未コミットファイルは3件以下なら
    列挙、4件以上なら件数のみ。``save_state.py`` 側の保存（5件/30件キャップ）は変更しない
    — ここは SessionStart 表示専用の圧縮であり、Claude はいつでも git log/status で詳細を
    取得できる（要約であって隠蔽ではない）。additionalContext へ統合するため、旧実装の
    `[evolve-anything:restore_state]` prefix・複数行整形は廃止する（§4.1）。
    """
    parts = []

    branch = work_context.get("git_branch", "")
    if branch:
        parts.append(f"ブランチ: {branch}")

    commits = work_context.get("recent_commits", [])
    if commits:
        first = commits[0]
        first = first if len(first) <= 50 else first[:50] + "..."
        parts.append(f"直近コミット{len(commits)}件（先頭: {first}）")

    files = work_context.get("uncommitted_files", [])
    if files:
        if len(files) <= 3:
            parts.append(f"未コミット{len(files)}件（{', '.join(files)}）")
        else:
            parts.append(f"未コミット{len(files)}件")

    return "作業コンテキスト復元: " + " / ".join(parts) if parts else ""


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

    DATA_DIR は他 build 関数と同じく ``rl_common.resolve_data_dir``（env 優先・#364）で解決する。
    fail-safe: 例外で hook を落とさない（try/except で degrade、stderr に 1 行）。
    """
    if _pj_slug is None:
        return
    try:
        import rl_common  # 遅延 import（patch 追従・他 build 関数と同型）

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


# ─────────────────────────────────────────────────────────────────
# 収集 → merge → print → commit（ADR-054 Phase 0 §6.2/§6.3）
# ─────────────────────────────────────────────────────────────────
def _call_builder(builder: "Callable", *args):
    """収集関数を個別 try/except で保護して呼ぶ（§5: 1系統の例外が他系統を巻き込まない）。

    各 ``_build_*_output`` は内部で既に自前の try/except を持つが、ここでも独立に
    包むことで「呼び出し規約そのもの」を構造的に保証する（内部 guard の有無に依存しない
    defense-in-depth）。例外は stderr へ 1 行出し、収集結果は None 扱いにする。
    """
    try:
        return builder(*args)
    except Exception as e:
        name = getattr(builder, "__name__", repr(builder))
        print(f"[evolve-anything:restore_state] {name} error: {e}", file=sys.stderr)
        return None


def _collect_notifications(stack: "ExitStack") -> "tuple[list[NotificationItem], dict | None]":
    """9系統＋corrupt判定を順に呼び、``(items, proposal_output)`` を返す。

    各系統呼び出しは ``_call_builder`` で個別に保護されるため、1系統の例外が他系統の
    収集結果を巻き込まない。pending_trigger・icebox レーン1 は ``stack`` に lock を
    登録するが、この関数を抜けても解放しない（呼び出し元の ``with ExitStack()`` が
    抜けるまで保持される）。
    """
    items: "list[NotificationItem]" = []

    for item in (
        _call_builder(_build_pending_trigger_output, stack),
        _call_builder(_build_spec_drift_output),
        _call_builder(_build_evolve_drain_output),
        _call_builder(_build_data_dir_migration_output),
        _call_builder(_build_utterance_staleness_output),
    ):
        if item is not None:
            items.append(item)

    # evolve-queue.json の env ガード + read を1回だけ行い、以下3箇所に使い回す（#412 [Should]6）。
    shared_queue = _call_builder(_resolve_queue_data) or (None, None, "absent")

    queue_item = _call_builder(_build_evolve_queue_output, shared_queue)
    if queue_item is not None:
        items.append(queue_item)

    proposal_output = _call_builder(_build_session_proposal_output, shared_queue)
    if proposal_output and proposal_output.get("systemMessage"):
        items.append(NotificationItem(
            label="proposal", tier=2,
            text=proposal_output["systemMessage"],
            digest=proposal_output.get("digest") or proposal_output["systemMessage"],
        ))

    judge_item = _call_builder(_build_judge_cap_output, shared_queue)
    if judge_item is not None:
        items.append(judge_item)

    icebox_item = _call_builder(_build_icebox_output, stack)
    if icebox_item is not None:
        items.append(icebox_item)

    return items, proposal_output


def _commit_all(items: "list[NotificationItem]") -> None:
    """print 成功後にのみ呼ぶ。実際に結合文字列へ含めた item の commit を実行する（ack）。

    1系統の commit 失敗が他系統の commit を止めない（spec_drift/pending_trigger/icebox の
    いずれかで ``save_marker``/``delete_pending_trigger``/``record_seen`` が例外を出しても、
    print は既に成功しているため今回はユーザーに表示される。次回also再表示されうる
    ことは許容する — 情報消失より優先、§7.1）。
    """
    for item in items:
        if item.commit is None:
            continue
        try:
            item.commit()
        except Exception as e:
            print(
                f"[evolve-anything:restore_state] commit failed for {item.label}: {e}",
                file=sys.stderr,
            )


def handle_session_start(event: dict) -> None:
    """SessionStart イベントを処理する（ADR-054 Phase 0 §6.3）。

    stdout は「0行」か「厳密に1行の JSON dict」の二値。commit（副作用の確定）は必ず
    print 成功の後に来る — collect フェーズ中に取得した lock（pending_trigger・icebox
    レーン1）は ``with ExitStack()`` を抜けるまで保持され、成功時は commit 済みの状態で、
    失敗時は commit されないまま、必ず解放される。
    """
    # sibling-dir worktree の write 時 slug 解決用 cache を更新（#29/#593）
    _persist_pj_slug_cache()

    with ExitStack() as stack:
        items, proposal_output = _collect_notifications(stack)
        try:
            system_message = _merge_notification_text(items)

            proposal_context = None
            if proposal_output:
                proposal_context = (proposal_output.get("hookSpecificOutput") or {}).get(
                    "additionalContext"
                )

            project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "") or None
            checkpoint = common.find_latest_checkpoint(project_dir)

            work_context_summary = None
            if checkpoint:
                work_context = checkpoint.get("work_context")
                if work_context:
                    work_context_summary = _format_work_context_summary(work_context) or None

            additional_context = _build_additional_context(work_context_summary, proposal_context)

            output: dict = {}
            if system_message:
                output["systemMessage"] = system_message

            hook_specific: dict = {}
            if additional_context:
                hook_specific["additionalContext"] = additional_context
            if checkpoint:
                session_title = _make_session_title(checkpoint)
                if session_title:
                    hook_specific["sessionTitle"] = session_title
            if hook_specific:
                hook_specific["hookEventName"] = "SessionStart"
                output["hookSpecificOutput"] = hook_specific

            if checkpoint:
                output["restored"] = True
                output["checkpoint"] = _summarize_checkpoint_for_output(checkpoint)

            if output:
                print(json.dumps(output, ensure_ascii=False))

            # ── ここに到達 = print 成功 ── 副作用を確定してよい
            _commit_all(items)
        except Exception as e:
            print(f"[evolve-anything:restore_state] merge/print failed: {e}", file=sys.stderr)
            # commit を一切呼ばない。pending_trigger は未削除、icebox は未既読、
            # spec_drift は marker 未保存のまま → 次回セッションで再度候補になる
    # `with ExitStack()` を抜けた時点で pending_trigger / icebox の lock は
    # 成功時は commit 済みの状態で、失敗時は commit されないまま、必ず解放される。


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
