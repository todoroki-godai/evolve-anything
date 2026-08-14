"""ADR-054 Phase 0（B1）: SessionStart 通知9系統の収集関数（印字しない）。

各 ``_build_*_output`` は ``NotificationItem | None``（session_proposal のみ 2 チャネル
のため ``dict | None``）を返す純粋な収集関数で、print は一切行わない。副作用（marker
保存・pending 削除・icebox 既読化）を持つ系統は ``commit`` コールバックとして保持し、
呼び出し元（``hooks/restore_state.py``）が print 成功後にのみ実行する（ack 方式・§5）。

設計: ``docs/decisions/drafts/054-phase0-notification-routing.md``
"""
import os
import sys
from contextlib import ExitStack
from pathlib import Path

from .model import NotificationItem, _classify_daily_snapshot_file

# trigger_engine import (optional) — session_notify 自体が scripts/lib 配下の top-level
# package なので、import 可能な時点で sys.path に scripts/lib は既に入っている
# （sys.path 操作は不要）。
_trigger_engine = None
try:
    from trigger_engine import peek_pending_trigger, delete_pending_trigger
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

# pj_slug import (optional) — session_proposal の PJ 帰属判定に使う（#29/#593）
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

# daily.icebox_notice import (optional) — icebox 棚卸しの気づきトリガー（#194, #352）
_icebox_notice = None
try:
    from daily import icebox_notice as _icebox_notice
except ImportError:
    pass

# daily.proposal_digest import (optional) — 改善案 digest の SessionStart 提示（#409）
_proposal_digest = None
try:
    from daily import proposal_digest as _proposal_digest
except ImportError:
    pass

# daily.freshness import (optional) — Tier 判定用に generated_at の鮮度を再利用（#054 Phase 0）
_daily_freshness = None
try:
    from daily import freshness as _daily_freshness
except ImportError:
    pass

# daily.plist import (optional) — icebox-verdicts.json のパス解決（#054 Phase 0 §5.4）
_plist = None
try:
    from daily import plist as _plist
except ImportError:
    pass

# icebox_verdict_seen import (optional) — icebox 3レーン棚卸しレーン1「成立」の既読管理（#352）
_icebox_verdict_seen = None
try:
    import icebox_verdict_seen as _icebox_verdict_seen
except ImportError:
    pass


def _build_pending_trigger_output(stack: "ExitStack") -> "NotificationItem | None":
    """pending-trigger.json を ack 方式で収集する（§5.5）。

    read-only: ファイルが無ければ lock も作らない（icebox と同じ read-only 契約）。
    ある場合だけ sidecar lock を ``ExitStack`` に登録して他プロセスの並行収集を
    ブロックし、削除（commit）は print 成功後まで defer する。
    """
    if _trigger_engine is None:
        return None
    try:
        import trigger_engine as _te  # 遅延 import（patch 追従）

        if not _te.PENDING_TRIGGER_FILE.exists():
            return None
        from rl_common.file_lock import file_lock as _file_lock

        pending_file = _te.PENDING_TRIGGER_FILE
        lock_path = pending_file.with_name(pending_file.name + ".lock")
        stack.enter_context(_file_lock(lock_path))

        data = peek_pending_trigger()
        if data is None:
            return None
        message = data.get("message", "")
        if not message:
            return None
        text = f"[evolve-anything:auto-trigger] {message}"
        # digest 化しない（常にフル文・§4.2 例外2）: digest に text と同一の値を入れる。
        return NotificationItem(
            label="trigger", tier=1, text=text, digest=text, commit=delete_pending_trigger,
        )
    except Exception as e:
        print(f"[evolve-anything:restore_state] trigger delivery error: {e}", file=sys.stderr)
        return None


def _build_spec_drift_output() -> "NotificationItem | None":
    """main に着地した仕様未追従の変更があれば spec-keeper 提案を収集する（ADR-044/§5.2）。

    ``persist=False`` で ``detect()`` を呼び、marker 保存を defer する（two-phase 化）。
    実際に最終出力へ含められた場合だけ ``commit``（``save_marker``）が呼ばれる。
    ``persist=False`` は spec_trigger 側の契約により書き込みゼロ（dry-run 純度契約・
    pitfall_dryrun_stateful_store_write）。

    例外: ``result["first_run"]`` が真の初回セットアップ分岐は表示する message を
    持たない副作用のみの処理であり、defer する対象（display に紐づく ack）ではない。
    この分岐だけは persist=False でも spec_trigger 側が書かないため、ここで明示的に
    ``save_marker()`` を呼ぶ（さもないと次回セッションも「初回扱い」が繰り返され、
    spec_drift が永久に発火しなくなる）。
    fail-safe: spec_trigger 内部で git/IO 例外は握られるが、念のため全体を保護する。
    """
    if _spec_trigger is None:
        return None
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        cwd_path = Path(project_dir) if project_dir else Path.cwd()
        result = _spec_trigger.detect(cwd=cwd_path, persist=False)
        slug = _spec_trigger.resolve_slug(cwd_path)
        marker = result.get("marker")
        if result.get("first_run"):
            # 表示すべき message が無い副作用のみの分岐 → defer せず即時保存する。
            if marker is not None and slug:
                _spec_trigger.save_marker(slug, marker)
            return None
        message = result.get("message")
        if not message:
            return None
        commit = None
        if marker is not None and slug:
            commit = lambda: _spec_trigger.save_marker(slug, marker)
        surfaced_count = len(result.get("fires") or []) + len(result.get("reminders") or [])
        digest = f"spec-keeper提案{surfaced_count}件"
        return NotificationItem(label="spec", tier=2, text=message, digest=digest, commit=commit)
    except Exception as e:
        print(f"[evolve-anything:restore_state] spec-trigger error: {e}", file=sys.stderr)
        return None


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


def _build_evolve_drain_output() -> "NotificationItem | None":
    """前回 evolve で emit→apply 済みの提案を SessionStart で検出し drain を試みる（#421, #376）。

    Tier1固定・副作用は収集時点で完結し defer しない（§5.1）。#376 是正後の契約により
    明示 accept を渡さずに drain を呼ぶため、この hook からは実際には optimize_history に
    何も記録されない（安全側のデフォルト）。marker は温存され、次回の対話 drain
    （SKILL.md Step 7.8）でユーザーが明示的に accept/reject するまでリマインドし続ける。

    レイテンシ予算（pitfall_hot_hook_eager_import）: pending marker が無いケースは軽い判定で
    early-return し、重い経路（drain_pending）に入らない。
    """
    if _evolve_decisions is None:
        return None
    try:
        # 軽量 early-return: marker root が無ければ未 drain 提案は存在しない。
        if not _evolve_decisions.MARKER_ROOT.exists():
            return None
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        cwd = Path(project_dir) if project_dir else None
        slug = _evolve_decisions.resolve_slug(cwd)
        if not _evolve_decisions.marker_path(slug).exists():
            return None  # この slug に未 drain marker なし → 沈黙（重い drain に入らない）
        applied = _evolve_decisions.undrained_applied(slug)
        if not applied:
            return None
    except Exception as e:
        print(f"[evolve-anything:restore_state] evolve-drain pre-check error: {e}", file=sys.stderr)
        return None

    try:
        history_file = _resolve_canonical_history_file(slug)
        # #376: accepted を渡さない — SessionStart には対話チャネルが無く、この hook が
        # 人間の承認を代弁することはできない。orphan 除去だけは行われる。
        summary = _evolve_decisions.drain_pending(slug=slug, history_file=history_file)
        accepted = summary.get("accepted") or []
        rejected = summary.get("rejected") or []
        if accepted or rejected:
            text = (
                f"[evolve-anything] evolve 提案を自動 drain しました: "
                f"accept {len(accepted)} 件 / reject {len(rejected)} 件を "
                f"fitness 母集団（optimize_history）に記録（#421）。"
            )
            digest = f"evolve自動drain: accept{len(accepted)}/reject{len(rejected)}件"
        else:
            # #376: 明示 decision イベントが無いので何も記録されない（正しい挙動）。
            # 「記録した」と偽らず、対話 drain（Step 7.8）を促すリマインドに徹する。
            text = (
                f"[evolve-anything] 適用済みの evolve 提案が {len(applied)} 件あります。"
                f"次回セッションの `evolve --drain`（Step 7.8）で accept/reject を"
                f"明示的に記録してください（#376）。"
            )
            digest = f"記録待ち提案{len(applied)}件（evolve --drain）"
        return NotificationItem(label="drain", tier=1, text=text, digest=digest, tail_link=True)
    except Exception as e:
        print(f"[evolve-anything:restore_state] evolve-drain error: {e}", file=sys.stderr)
        return None


def _build_data_dir_migration_output() -> "NotificationItem | None":
    """DATA_DIR 分裂が未解消なら `evolve-fleet migrate-data` を1行案内する（#364/#137）。

    判定の要は「source（plugin-data dir）に未マージのストアが残っているか」
    （``needs_migration``）であり、**marker の有無ではない**。marker は「一度 migrate
    した」事実しか意味しないため、marker 済みでも旧版 hook の書込等で分裂が再発した
    場合に案内し続ける必要がある（#137）。Tier1固定・副作用なし（純読み取り）。
    """
    if _data_dir_migration is None:
        return None
    try:
        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if not env:
            return None  # hook 文脈でなければ判定しない（probe で実環境を読まない）
        source = Path(env)
        if not _data_dir_migration.is_cc_install_layout(source):
            return None  # テスト isolation / custom 環境
        # marker の有無に関わらず「未マージストアが残っているか」を毎回評価する（#137）。
        if not _data_dir_migration.needs_migration(source=source):
            return None  # 定常状態（source は空 / marker のみ）→ 沈黙
        canonical = _data_dir_migration.default_canonical()
        marker = canonical / _data_dir_migration._marker_name()
        if marker.exists():
            # marker 済みなのに未マージストアが再蓄積 = 分裂の再発（recurrence）。
            text = (
                "[evolve-anything] DATA_DIR 分裂が再発しています（#137）。marker は設置済みですが"
                " plugin-data 側に未マージのストアが再蓄積しています（旧版 hook が書き続けている"
                "可能性）。`evolve-fleet migrate-data --dry-run` で内容確認後、"
                "`evolve-fleet migrate-data` で再度一元化してください。"
            )
        else:
            text = (
                "[evolve-anything] DATA_DIR が hook/tool 文脈で分裂しています（#364）。"
                "`evolve-fleet migrate-data --dry-run` で内容確認後、"
                "`evolve-fleet migrate-data` で一元化してください。"
            )
        return NotificationItem(
            label="datadir", tier=1, text=text, digest="DATA_DIR分裂（要migrate-data）",
        )
    except Exception as e:
        print(f"[evolve-anything:restore_state] data-dir migration reminder error: {e}", file=sys.stderr)
        return None


def utterance_staleness_advisory(data_dir) -> "str | None":
    """data_dir の utterance アーカイブが stale なら advisory メッセージを返す（純関数・#430）。

    observe-first pre-flight: staleness marker（last_ingest_at ファイル）を読むだけで
    DuckDB 接続も transcript 走査もしない（0.1 秒以下、pitfall_hot_hook_eager_import）。
    marker 不在 = 「未 ingest」と解釈して advisory を返す（∞ 扱い・0日でない）。
    閾値は最終 ingest > 14 日。fresh なら None。

    シグネチャ・戻り値（str|None）は ADR-054 Phase 0 でも変更しない（§7.2 既存契約）。
    digest 用の経過日数は呼び出し側（``_build_utterance_staleness_output``）が別途算出する。
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


def _utterance_staleness_age_days(data_dir) -> "int | None":
    """digest 用に utterance staleness の経過日数だけを算出する（marker 不在なら None）。

    ``utterance_staleness_advisory`` と同じ軽量 marker read を再利用する（DuckDB 接続なし）。
    """
    if _utterance_store is None:
        return None
    last = _utterance_store.read_last_ingest_at(data_dir)
    if last is None:
        return None
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except ValueError:
        return None


def _build_utterance_staleness_output() -> "NotificationItem | None":
    """utterance アーカイブの staleness advisory を収集する（#430・安全弁）。Tier1固定。

    実環境ガード: `CLAUDE_PLUGIN_DATA` が CC install レイアウト配下のときだけ判定する
    （migration リマインドと同型）。テスト isolation の tmp env / 非 hook 文脈では実環境を
    一切 probe せず沈黙する。
    """
    if _utterance_store is None or _data_dir_migration is None:
        return None
    try:
        import rl_common  # 遅延 import（patch 追従）

        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if not env:
            return None  # hook 文脈でなければ判定しない（実環境を probe しない）
        if not _data_dir_migration.is_cc_install_layout(Path(env)):
            return None  # テスト isolation / custom 環境
        data_dir = rl_common.resolve_data_dir(env)
        message = utterance_staleness_advisory(data_dir)
        if not message:
            return None
        age = _utterance_staleness_age_days(data_dir)
        digest = f"発話取込{age}日停止（要ingest）" if age is not None else "発話取込停止（要ingest）"
        return NotificationItem(label="utterance", tier=1, text=message, digest=digest)
    except Exception as e:
        print(f"[evolve-anything:restore_state] utterance staleness check error: {e}", file=sys.stderr)
        return None


def _resolve_queue_data() -> "tuple":
    """evolve-queue.json の env ガード + 破損分類 + read を1回だけ行う（#412 [Should]6 / §4.6）。

    ``_build_evolve_queue_output`` / ``_build_session_proposal_output`` /
    ``_build_judge_cap_output`` が個別に env ガード〜read_queue を行っていた（同じ内容を
    1セッション開始ごとに3回パース）。``_collect_notifications``（restore_state.py）が
    ここで1回だけ解決し、3箇所へ ``(data_dir, queue_data, file_state)`` を配る。

    Returns: ``(None, None, "absent")`` — hook 文脈でない/install レイアウト外/モジュール
             未解決のいずれか。``file_state`` は ``"absent" | "corrupt" | "ok"``
             （§4.6 の producer 破損判定。呼び出し側は evolve_queue の収集関数だけが
             corrupt を Tier1 health notice に昇格させる）。
    """
    if _queue_notice is None or _data_dir_migration is None:
        return None, None, "absent"
    try:
        import rl_common  # 遅延 import（patch 追従・他 build 関数と同型）

        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if not env:
            return None, None, "absent"  # hook 文脈でなければ判定しない（実環境を probe しない）
        if not _data_dir_migration.is_cc_install_layout(Path(env)):
            return None, None, "absent"  # テスト isolation / custom 環境
        data_dir = rl_common.resolve_data_dir(env)
        queue_path = Path(data_dir) / _queue_notice.QUEUE_FILE_NAME
        file_state = _classify_daily_snapshot_file(queue_path)
        queue_data = _queue_notice.read_queue(data_dir) if file_state == "ok" else None
        return data_dir, queue_data, file_state
    except Exception as e:
        print(f"[evolve-anything:restore_state] evolve-queue resolve error: {e}", file=sys.stderr)
        return None, None, "absent"


def _build_evolve_queue_output(shared: "tuple | None" = None) -> "NotificationItem | None":
    """毎朝の `fleet queue` が保存した evolve-queue.json の待ち PJ を収集する（#80）。

    無人で回せる決定論パイプライン（ingest→queue）の結果を、対話セッション開始時にユーザーが
    気づける形で surface する（適用＝evolve 自体は対話セッションで人間が承認）。

    ``shared``: ``_resolve_queue_data()`` の戻り値（#412 [Should]6）。None（省略）なら本関数が
    自前で解決する（直接呼び出す単体テストとの後方互換）。
    """
    if _queue_notice is None or _data_dir_migration is None:
        return None
    try:
        data_dir, queue_data, file_state = shared if shared is not None else _resolve_queue_data()
        if data_dir is None:
            return None

        if file_state == "corrupt":
            text = (
                "[evolve-anything] evolve-queue.json が壊れています"
                "（daily runner の書き込みが壊れた可能性）。"
                "`bin/evolve-daily-run` のログを確認してください。"
            )
            return NotificationItem(label="queue", tier=1, text=text, digest="evolve-queue破損")

        output = _queue_notice.queue_notice_output(queue_data)
        if not output:
            return None
        text = output["systemMessage"]

        if _daily_freshness is not None:
            state, _age = _daily_freshness.classify_freshness(
                (queue_data or {}).get("generated_at"),
                stale_days=_queue_notice.DEFAULT_STALE_DAYS,
            )
            if state != _daily_freshness.Freshness.FRESH:
                return NotificationItem(label="queue", tier=1, text=text, digest="evolve-queue更新停止")

        count = len((queue_data or {}).get("queue") or [])
        return NotificationItem(
            label="queue", tier=2, text=text, digest=f"evolve待ち{count}PJ", tail_link=True,
        )
    except Exception as e:
        print(f"[evolve-anything:restore_state] evolve-queue notice error: {e}", file=sys.stderr)
        return None


def _build_session_proposal_output(shared: "tuple | None" = None) -> "dict | None":
    """毎朝の改善案 digest（evolve-queue.json の proposals フィールド）から SessionStart 提示用の
    出力 dict を組み立てる（#409, #412）。**print しない収集関数**（#412 [Must]2）— SessionStart
    hook の stdout は「hookSpecificOutput を含む行が高々1つ」でなければならず（複数行に分かれると
    片方が黙って捨てられうる）。

    出力は 2 チャネル同時（#412 [Must]1・ADR-038 代替案C と同型）:
    - ``systemMessage``（user 向け）: 代表テキストの可視化。additionalContext は Claude にしか
      届かず、ユーザーが何か打つまで中身を確認する手段が無いため
    - ``hookSpecificOutput.additionalContext``（Claude 向け）: 「ユーザーの最初の応答を終えた
      直後に必ず AskUserQuestion で y/n 提示せよ」という行動指示

    ADR-054 Phase 0: ``digest``（§4.2 テンプレート）を追加で返す。呼び出し元
    （``_collect_notifications``）が ``systemMessage``/``digest`` を ``NotificationItem`` に
    包んで Tier2 として merge へ渡し、``additionalContext`` は別枠（work_context summary と
    連結）で扱う（§6.1）。

    実環境ガード・observe-first pre-flight（evolve-queue.json 読み込みのみ・DuckDB 接続や
    transcript 走査なし）は他 build 関数と同型。既読フィルタ（correction_review_seen.jsonl）
    は 1 回の read_reviewed_keys 呼び出しのみで、書き込みは一切しない（read-only）。
    fail-safe: 例外で hook を落とさない（try/except で degrade、stderr に 1 行、None を返す）。

    ``shared``: ``_resolve_queue_data()`` が返す ``(data_dir, queue_data, file_state)`` タプル
    （#412 [Should]6: evolve-queue.json の3重読みを1回に集約）。None（省略）なら本関数が
    自前で解決する（直接呼び出す単体テストとの後方互換）。
    """
    if _proposal_digest is None or _queue_notice is None or _data_dir_migration is None:
        return None
    try:
        data_dir, queue_data, _file_state = shared if shared is not None else _resolve_queue_data()
        if data_dir is None:
            return None  # env ガード不通過（呼び出し元が既に判定済み）

        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if not project_dir or _pj_slug is None:
            return None  # cwd 不明 / pj_slug モジュール不在なら判定不能
        slug = _pj_slug.resolve_pj_slug(project_dir)
        if not slug or slug == _pj_slug.UNATTRIBUTED_SLUG:
            return None  # 帰属不能な PJ は判定しない

        if not isinstance(queue_data, dict):
            return None

        from correction_semantic import daily_review as _daily_review

        # 既読ストアは digest 生成（bin/evolve-daily-run）と同じ data_dir を明示指定して読む。
        seen_path = _daily_review.default_seen_path(base=data_dir)
        seen_keys = _daily_review.read_reviewed_keys(path=seen_path)
        groups = _proposal_digest.build_session_proposals(queue_data, slug, seen_keys=seen_keys)
        # #412 [Must]4: global レーンの group を PJ ごとの --project-path で正しく帰属させる。
        proposals = queue_data.get("proposals")
        project_paths = proposals.get("project_paths") if isinstance(proposals, dict) else None
        # codex 2巡目 [Must]1（#443 PR2-a）: machinery 除外件数は `groups` の有無に関わらず
        # 先に取り出す。旧実装は `if not groups: return None` の**後**でこの値を計算しており、
        # 「候補が全件 machinery で groups が空になる」最悪ケース（除外が最も効いた瞬間）で
        # 早期 return に阻まれ、除外件数を読む行自体に到達しなかった（1巡目 [Must]2 と同型の
        # 欠陥が別レイヤーに残っていた）。
        excluded_machinery = 0
        machinery_by_pj = (
            proposals.get("excluded_machinery_by_pj") if isinstance(proposals, dict) else None
        )
        if isinstance(machinery_by_pj, dict):
            entry = machinery_by_pj.get(slug)
            if isinstance(entry, dict):
                excluded_machinery = entry.get("total") or 0

        if not groups:
            if excluded_machinery <= 0:
                return None  # 提案も除外もゼロ → 完全な無音（従来どおり）
            # 提案は無いが machinery 除外は非ゼロ（silence != evaluated）。通常の「改善案が
            # あります」文面は使わず、提案が無い事実を明示する専用の短い1行だけを返す。
            # hookSpecificOutput は付けない（AskUserQuestion で確認すべき提案が無いため）。
            notice = (
                "[evolve-anything] 今日の新規提案はありません（machinery 除外 "
                f"{excluded_machinery} 件 — 委譲メッセージ等の harness 注入のため"
                "候補に含まれていません）。"
            )
            return {
                "systemMessage": notice,
                "digest": f"machinery除外{excluded_machinery}件",
            }
        # 回答コマンドは**絶対パス**で埋め込む。提示先は他 PJ の cwd であり、相対
        # `bin/evolve-reflect` は "No such file" になる（pitfall_skill_md_plugin_root と同型）。
        reflect_cmd = str(Path(__file__).resolve().parent.parent.parent.parent / "bin" / "evolve-reflect")
        message = _proposal_digest.build_proposal_prompt(
            groups, slug, reflect_cmd=reflect_cmd, project_paths=project_paths,
        )
        system_message = _proposal_digest.build_proposal_systemmessage(
            groups, excluded_machinery=excluded_machinery,
        )
        # hookEventName は ADR-038 のスキーマ必須項目（subagent_observe.py と同型）。
        # 省略すると additionalContext が解釈されず機能が無言で死ぬ。
        return {
            "systemMessage": system_message,
            "digest": f"改善案{len(groups)}件",
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": message,
            },
        }
    except Exception as e:
        print(f"[evolve-anything:restore_state] session proposals error: {e}", file=sys.stderr)
        return None


def _judge_cap_digest(judge: dict) -> str:
    """llm_judge の分岐から digest テンプレートを組み立てる（§4.2・全分岐 Tier1）。

    ``daily/queue_notice.build_judge_cap_notice`` の優先順位（source_failed >
    skipped_locked > out_of_range > capped）を restore_state 側で再利用し、library は
    無改修のまま保つ（§6.2: digest はここで生データから組み立てる）。
    """
    if judge.get("source_failed"):
        return "judge障害"
    if judge.get("skipped_locked"):
        return "judgeスキップ"
    if not judge.get("capped"):
        out_of_range = judge.get("out_of_range_verdicts")
        if isinstance(out_of_range, (int, float)) and not isinstance(out_of_range, bool) and out_of_range > 0:
            return f"judge異常応答{int(out_of_range)}件（要確認）"
        return "judge異常"

    selected = judge.get("selected")
    unjudged_before = judge.get("unjudged_before")
    remaining = 0
    if (
        isinstance(selected, (int, float)) and not isinstance(selected, bool)
        and isinstance(unjudged_before, (int, float)) and not isinstance(unjudged_before, bool)
    ):
        remaining = int(unjudged_before) - int(selected)
    return f"judge持ち越し{remaining}件（自動）"


def _build_judge_cap_output(shared: "tuple | None" = None) -> "NotificationItem | None":
    """llm_judge Phase B の日次上限到達を収集する（#408）。全分岐 Tier1（§5.1）。

    daily runner（evolve-daily-run）が `judge_runner.run_daily_judge` の結果を
    evolve-queue.json の `llm_judge` フィールドへ埋め込む（新ストアを作らず既存の
    read 専用派生物を再利用）。上限に当たった日だけ 1 行通知し、当たらない日は沈黙する
    （承認済み standing budget のため毎日 y/n は挟まない）。

    ``shared``: ``_resolve_queue_data()`` の戻り値。None（省略）なら自前で解決する。
    """
    if _queue_notice is None or _data_dir_migration is None:
        return None
    try:
        data_dir, queue_data, _file_state = shared if shared is not None else _resolve_queue_data()
        if data_dir is None:
            return None
        output = _queue_notice.judge_cap_notice_output(queue_data)
        if not output:
            return None
        text = output["systemMessage"]
        judge = queue_data.get("llm_judge") if isinstance(queue_data, dict) else None
        digest = _judge_cap_digest(judge or {})
        return NotificationItem(label="judge", tier=1, text=text, digest=digest)
    except Exception as e:
        print(f"[evolve-anything:restore_state] llm_judge cap notice error: {e}", file=sys.stderr)
        return None


def _build_icebox_output(stack: "ExitStack") -> "NotificationItem | None":
    """icebox 棚卸しの気づきトリガーを収集する（#194, #352）。

    #352: daily runner の icebox 3レーン棚卸しステップが書いた icebox-verdicts.json に
    レーン1「成立」（未既読）があれば、それを**該当 issue だけ名指し + 根拠1行**で優先通知する
    （Tier1固定・digest化免除・ack 方式・§5.3）。成立が無ければ icebox-status.json ベースの
    件数集約通知（#194・Tier2）にフォールバックする。

    icebox は evolve-anything 自身の GitHub issue backlog なので、**本体リポジトリ
    （`.claude-plugin/plugin.json` を持つ repo）で作業しているときだけ**判定する。他 PJ で
    作業中は plugin_self 判定で即 return（沈黙）。
    """
    if _icebox_notice is None or _data_dir_migration is None:
        return None
    try:
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        if not project_dir:
            return None  # cwd 不明なら plugin_self 判定不能 = 沈黙
        if not (Path(project_dir) / ".claude-plugin" / "plugin.json").exists():
            return None  # evolve-anything 本体以外の PJ では沈黙

        import rl_common  # 遅延 import（patch 追従・他 build 関数と同型）

        env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
        if not env:
            return None  # hook 文脈でなければ判定しない（実環境を probe しない）
        if not _data_dir_migration.is_cc_install_layout(Path(env)):
            return None  # テスト isolation / custom 環境
        data_dir = rl_common.resolve_data_dir(env)

        # ── #352 レーン1「成立」（ack 方式・§5.3）──
        if _icebox_verdict_seen is not None and _plist is not None:
            verdicts_path = Path(_plist.icebox_verdicts_json_path(str(data_dir)))
            verdicts_state = _classify_daily_snapshot_file(verdicts_path)
            if verdicts_state == "corrupt":
                text = (
                    "[evolve-anything] icebox-verdicts.json が壊れています"
                    "（daily runner の書き込みが壊れた可能性）。"
                    "`bin/evolve-daily-run` のログを確認してください。"
                )
                return NotificationItem(label="icebox", tier=1, text=text, digest="icebox破損")

            if verdicts_state == "ok":
                verdicts_payload = _icebox_notice.read_icebox_verdicts(data_dir)
                # icebox-verdicts.json 自体が無い場合はロックファイルすら作らず read-only を
                # 維持する既存契約（`test_deliver_does_not_write`）を保つため、"ok" のときだけ
                # lock を取得する。
                seen_path = _icebox_verdict_seen.default_seen_path(data_dir)
                from rl_common.file_lock import file_lock as _file_lock  # 遅延 import

                lock_path = seen_path.with_name(seen_path.name + ".lock")
                # #352 P1: read（seen_keys）→ decide（未既読判定）→ print（merge/print 後）→
                # write（既読化）を file_lock で1トランザクション化する。lock は decide 直後
                # に解放せず、他8系統の収集・merge・print 完了後の commit まで保持する
                # （ExitStack 登録・§5.3 rev6）。
                stack.enter_context(_file_lock(lock_path))
                seen_keys = _icebox_verdict_seen.read_seen_keys(seen_path)
                text_output, shown = _icebox_notice.icebox_verdicts_notice_output(
                    verdicts_payload, seen_keys
                )
                if text_output:
                    body = _icebox_notice.build_met_body(shown)
                    digest = f"icebox成立: {body}"
                    return NotificationItem(
                        label="icebox", tier=1,
                        text=text_output["systemMessage"], digest=digest,
                        commit=lambda: _icebox_verdict_seen.record_seen(shown, path=seen_path),
                    )
                # 成立なし → lock は保持されたままフォールバックへ進む（他プロセスとの
                # 直列化を維持）。

        # ── health notice / フォールバック件数集約 / 破損（icebox-status.json）──
        status_path = Path(data_dir) / _icebox_notice.ICEBOX_FILE_NAME
        status_state = _classify_daily_snapshot_file(status_path)
        if status_state == "corrupt":
            text = (
                "[evolve-anything] icebox-status.json が壊れています"
                "（daily runner の書き込みが壊れた可能性）。"
                "`bin/evolve-daily-run` のログを確認してください。"
            )
            return NotificationItem(label="icebox", tier=1, text=text, digest="icebox破損")

        status = _icebox_notice.read_icebox_status(data_dir)
        threshold_days = rl_common.load_user_config().get("icebox_review_threshold_days", 30)
        output = _icebox_notice.icebox_notice_output(status, threshold_days=threshold_days)
        if not output:
            return None
        text = output["systemMessage"]

        if _daily_freshness is not None:
            gen_at = status.get("generated_at") if isinstance(status, dict) else None
            state, _age = _daily_freshness.classify_freshness(
                gen_at, stale_days=_icebox_notice.STALE_STATUS_DAYS,
            )
            if state != _daily_freshness.Freshness.FRESH:
                return NotificationItem(label="icebox", tier=1, text=text, digest="icebox更新停止")

        count = status.get("count") if isinstance(status, dict) else None
        oldest = status.get("oldest_days") if isinstance(status, dict) else None
        count = int(count) if isinstance(count, (int, float)) and not isinstance(count, bool) else 0
        oldest = int(oldest) if isinstance(oldest, (int, float)) and not isinstance(oldest, bool) else 0
        return NotificationItem(
            label="icebox", tier=2, text=text,
            digest=f"icebox{count}件・最古{oldest}日", tail_link=True,
        )
    except Exception as e:
        print(f"[evolve-anything:restore_state] icebox notice error: {e}", file=sys.stderr)
        return None
