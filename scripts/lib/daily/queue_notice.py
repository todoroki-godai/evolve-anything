"""evolve-queue.json reader + SessionStart 通知メッセージ生成（#80 Phase 1b）。

毎朝の `fleet queue --json` が `$CLAUDE_PLUGIN_DATA/evolve-queue.json` に保存した待ち PJ を
SessionStart で systemMessage（ADR-038 = user 向けチャネル）として surface する。

すべて read 専用・純関数・決定論（LLM 非依存）。`evolve-queue.json` は派生物（SoR は
`fleet queue` の元データで本ファイルではない）だが、writer（daily runner）/ reader
（本モジュール・fleet propose 等）が実在するため store_registry に derived_cache として
宣言済み（#399 codex round1 是正・「SoR でない」は分類理由であって非登録の理由ではない）。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import freshness as _freshness

QUEUE_FILE_NAME = "evolve-queue.json"

# generated_at がこの時間数以上古ければ stale（#466: 「その日のうち」に気づける粒度へ）。
# 09:00 実行を前提に、翌日 08:00 のセッション（23時間経過）では沈黙し、翌日 15:00 に前日分
# しか無い（30時間経過）時点で発火する。
#
# 本閾値の根拠は「週の締切に間に合ううちに気づかせる」ことだけに置く。永久に失われるのは
# correction_rate.FREEZE_DELAY_DAYS（=3）による週の締切（週末+3日）を止まったまま通過した
# 場合であり、「N時間で停止すると即座に不合格が確定する」わけではない。
#
# **未裏取り（#490 codex [Must]）**: 「日次上限 200 件
# （correction_semantic.judge_runner.DEFAULT_DAILY_UTTERANCE_LIMIT）に対し実発話量が
# 1日 84〜120 件だから、停止しても再開後に追いつく」とは**断定できない**。未判定 6,609 件が
# 滞留した状態で毎日上限に張り付いた記録があり、追いつけるかは流入量ではなく
# **backlog の純減量**（成功処理量 − 新規流入）を測らないと分からない（未測定）。
# ゆえに「追いつけるから多少止まってよい」という前提をこの閾値の根拠に使わない。
#
# 旧 DEFAULT_STALE_DAYS（=3）は #490 で削除した。同じ queue が呼び出し側によって
# 30 時間判定と 72 時間判定に分かれ、画面ごとに STALE / FRESH が食い違っていたため
# （fleet propose と SessionStart で不一致）。閾値はこの1定数を単一ソースとする。
DEFAULT_STALE_HOURS = 30


def read_queue(data_dir) -> "dict | None":
    """data_dir/evolve-queue.json を読んで dict を返す。無い/壊れていれば None。"""
    path = Path(data_dir) / QUEUE_FILE_NAME
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def build_queue_notice(
    queue_data: "dict | None",
    now: "datetime | None" = None,
    stale_hours: int = DEFAULT_STALE_HOURS,
) -> "str | None":
    """待ち PJ 一覧の通知メッセージを生成する。

    評価順序（#351 freshness gate）: (a) generated_at の鮮度を先に判定する →
    (b) FRESH でなければ待ち PJ 一覧を一切解釈せず専用メッセージを返す（queue が
    空でも stale なら通知する＝producer 停止の見逃しを防ぐ回帰対応）→
    (c) FRESH のときだけ待ち PJ 一覧を評価し、空なら沈黙する。

    - queue_data が None → None（daily runner 未実行 = 壊れているのでなく単に未セットアップ）
    - FRESH かつ待ち PJ あり → 「evolve 待ち: <pj…>（N 件）」
    - FRESH かつ待ち PJ 無し → None（沈黙）
    - STALE / UNKNOWN → 待ち PJ の中身に関わらず専用メッセージ（旧値は併記しない）

    #466 是正（2026-08-17）: STALE/UNKNOWN のメッセージは ``freshness.health_notice`` の
    汎用文（「現在値は不明です」）を使わない。この通知の対象は「値」でなく「取り込みが
    動いているか」であり、汎用文では読者に意味が伝わらないため、queue 専用の説明文を
    ここで直接組み立てる（``classify_freshness`` を先に評価する契約自体は維持）。

    #490 是正（2026-08-17・codex [Must]）: 文面から2つの過剰な断定を除いた。
    (a) 手動実行は今回分を取り込むだけで、停止・未登録の launchd を修復しない
    (b) 欠測で失われるのは「まだ達成していない連続」であって、既に 4 週連続を達成済みなら
        ``correction_rate`` が最長連続記録（best_run）を保持するため gate は閉じない
    """
    if not isinstance(queue_data, dict):
        return None

    now = now or datetime.now(timezone.utc)
    generated_at = queue_data.get("generated_at")
    state, age_days = _freshness.classify_freshness(
        generated_at,
        now=now,
        stale_hours=stale_hours,
    )
    if state == _freshness.Freshness.STALE:
        elapsed = _freshness.format_elapsed(
            _freshness.age_in_hours(generated_at, now=now), age_days
        )
        return (
            f"⚠ 学習データの自動取り込みが止まっています（最終実行: {elapsed}）。"
            "`bin/evolve-daily-run` を実行すればその場で今回分を取り込めます"
            "（毎朝の自動実行が止まったままなら、それとは別に復旧が必要です）。"
            "止まったまま週の締切（日曜の3日後）を過ぎると、その週は欠測として確定します。"
            "週次の数字がまだ出ていない場合は、表示に必要な「4週連続」の連続がそこで途切れます。"
            "`launchctl list | grep com.evolve-anything.daily` で毎朝の登録を確認してください。"
        )
    if state == _freshness.Freshness.UNKNOWN:
        return (
            "⚠ 学習データの自動取り込みが動いているか判定できません"
            "（記録の生成時刻が欠落しているか壊れています）。"
            "`bin/evolve-daily-run` を1回実行してください。"
            "`launchctl list | grep com.evolve-anything.daily` で毎朝の登録も確認してください。"
        )

    queue = queue_data.get("queue") or []
    if not queue:
        return None

    slugs = [item.get("pj_slug", "?") for item in queue if isinstance(item, dict)]
    count = len(slugs)
    joined = ", ".join(slugs)

    return f"[evolve-anything] evolve 待ち: {joined}（{count} 件）。対話セッションで `/evolve-anything:evolve` を回してください。"


def queue_notice_output(
    queue_data: "dict | None",
    now: "datetime | None" = None,
    stale_hours: int = DEFAULT_STALE_HOURS,
) -> "dict | None":
    """CC hook 出力用に systemMessage dict を返す。待ちが無ければ None。"""
    msg = build_queue_notice(queue_data, now=now, stale_hours=stale_hours)
    if msg is None:
        return None
    return {"systemMessage": msg}


# ─────────────────────────────────────────────────────────────────
# llm_judge 日次上限到達通知（#408）
# ─────────────────────────────────────────────────────────────────
# daily runner（evolve-daily-run）が judge_runner.run_daily_judge の結果を
# evolve-queue.json["llm_judge"] へ埋め込む（新ストアを作らず既存の read 専用派生物を
# 再利用・#379 Step 1 新設凍結中）。上限に当たった日だけ 1 行通知し、当たらない日は
# 沈黙する（承認済み standing budget なので毎日 y/n は挟まない）。


def build_judge_summary(judge_result: "dict | None") -> dict:
    """run_daily_judge の戻り値から evolve-queue.json["llm_judge"] へ転記するサマリを
    組み立てる（#410 round4 [Should]4: この転記の単一ソース）。

    evolve-daily-run が judge_result（run_daily_judge の全フィールドを持つ大きな dict）を
    そのまま queue.json へ書くと運用者向けの表示が肥大化するため、observability に必要な
    フィールドだけを選んで転記する。従来は転記漏れ（out_of_range_verdicts / reserved_batches
    が queue.json に含まれず SessionStart から観測できなかった）が起きていたため、転記先を
    1 関数に集約しテストで固定する。

    欠けているキーは KeyError にせず既定値で埋める（``run_daily_judge`` の早期 return dict
    が将来フィールド増減しても呼び出し側を壊さないため）。``judge_result=None``（llm_judge
    ステップ自体が例外送出した場合）も全既定値の dict を返す。
    """
    judge_result = judge_result or {}
    return {
        "unjudged_before": judge_result.get("unjudged_total", 0),
        "selected": judge_result.get("selected", 0),
        "capped": bool(judge_result.get("capped", False)),
        "corrections": judge_result.get("corrections", 0),
        "call_failed": judge_result.get("call_failed", 0),
        # #410 [Must]E: 発話ソース（utterances.db）取得の DB/schema 障害を
        # capped=False の健康そうなサマリに埋もれさせない（silence != evaluated）。
        "source_failed": bool(judge_result.get("source_failed", False)),
        "source_error": judge_result.get("source_error"),
        # #410 round2 [Should]②: 別プロセスが lock 保持中で non-blocking skip したことを
        # 沈黙させない。
        "skipped_locked": bool(judge_result.get("skipped_locked", False)),
        # #410 round4 [Should]4: 従来は転記漏れで queue.json に含まれていなかった。
        "out_of_range_verdicts": judge_result.get("out_of_range_verdicts", 0),
        "reserved_batches": judge_result.get("reserved_batches", 0),
        # #442 契約4・5: judge 母集団を tracked_projects + cutoff に絞った際の除外件数。
        # silence != evaluated — dry-run / run / lock-skip / source-failure の全分岐で
        # run_daily_judge が返すので、転記漏れなく queue.json へ載せる。
        "excluded_untracked_total": judge_result.get("excluded_untracked_total", 0),
        "excluded_untracked_by_pj": judge_result.get("excluded_untracked_by_pj", {}),
        "excluded_before_cutoff_total": judge_result.get("excluded_before_cutoff_total", 0),
    }


def _exclusion_suffix(judge: dict) -> str:
    """除外内訳を通知に付け足す1文（#442 契約4・5: silence != evaluated）。

    新しい通知系統は作らず、``build_judge_cap_notice`` が既に生成した判定サマリ行の
    末尾に足すだけ（除外が 0 件なら空文字＝何も足さない・ノイズにしない）。
    """
    untracked = judge.get("excluded_untracked_total")
    before_cutoff = judge.get("excluded_before_cutoff_total")
    parts = []
    if isinstance(untracked, (int, float)) and not isinstance(untracked, bool) and untracked > 0:
        parts.append(f"tracked外{int(untracked)}件")
    if (
        isinstance(before_cutoff, (int, float))
        and not isinstance(before_cutoff, bool)
        and before_cutoff > 0
    ):
        parts.append(f"cutoff外{int(before_cutoff)}件")
    if not parts:
        return ""
    return f"（除外: {' / '.join(parts)}）"


def build_judge_cap_notice(
    queue_data: "dict | None",
    now: "datetime | None" = None,
    stale_hours: int = DEFAULT_STALE_HOURS,
) -> "str | None":
    """llm_judge の日次処理が上限到達 or 障害だった日だけ 1 行通知する。当たらない日は
    None（沈黙）。

    freshness gate は ``build_queue_notice`` と同じ ``generated_at``（evolve-queue.json
    全体）を共有する。STALE / UNKNOWN のときは health notice を二重に出さず沈黙する
    （health notice 自体は ``build_queue_notice`` 側が既に出すため）。

    ``source_failed=True``（#410 [Must]E: 発話ソース取得の DB/schema 障害）は
    ``capped`` の値に関わらず優先して通知する（silence != evaluated — capped=False の
    健康そうな値に障害シグナルを埋もれさせない）。

    ``skipped_locked=True``（#410 round3 [Should]4: 別プロセスが選定〜記録の sidecar
    ロックを保持中で non-blocking 取得に失敗し即座に skip した・round2 [Should]②）も
    ``source_failed`` に次いで優先通知する。連日 skip が続くと供給が実質止まるのに、
    ``capped=False``（0件処理・上限未到達）の健康そうな値に埋もれて沈黙していた。

    ``out_of_range_verdicts > 0``（#410 round4 [Should]4）: モデルがバッチ対象外の
    verdict index を返した件数。judge_runner の戻り値・ログには出るが、従来
    evolve-daily-run の judge_summary 転記に含まれておらず SessionStart から観測
    できなかった（連日発生してもモデル応答の質劣化に気づけない）。``capped`` より
    優先度は下げる（capped=True は供給が現に止まっている状態で運用上より緊急度が高い）。
    """
    if not isinstance(queue_data, dict):
        return None

    now = now or datetime.now(timezone.utc)
    state, _age_days = _freshness.classify_freshness(
        queue_data.get("generated_at"),
        now=now,
        stale_hours=stale_hours,
    )
    if state != _freshness.Freshness.FRESH:
        return None

    judge = queue_data.get("llm_judge")
    if not isinstance(judge, dict):
        return None

    # #442 契約4・5: 除外内訳（tracked外 / cutoff外）を、どの分岐でメッセージが生成
    # されても末尾に1文足す（新しい通知系統は作らない・既存サマリ行への追記のみ）。
    suffix = _exclusion_suffix(judge)

    if judge.get("source_failed"):
        error = judge.get("source_error") or "詳細は daily runner のログを確認してください"
        return f"[evolve-anything] llm_judge の発話ソース取得に失敗しました: {error}{suffix}"

    if judge.get("skipped_locked"):
        return (
            "[evolve-anything] llm_judge は別プロセスが実行中のためスキップしました"
            f"（翌日以降に再試行）。{suffix}"
        )

    if not judge.get("capped"):
        out_of_range = judge.get("out_of_range_verdicts")
        if (
            isinstance(out_of_range, (int, float))
            and not isinstance(out_of_range, bool)
            and out_of_range > 0
        ):
            return (
                f"[evolve-anything] llm_judge が範囲外の verdict index を"
                f"{int(out_of_range)}件無視しました（モデル応答の質を確認してください）。{suffix}"
            )
        return None

    selected = judge.get("selected")
    unjudged_before = judge.get("unjudged_before")
    if not isinstance(selected, (int, float)) or isinstance(selected, bool):
        return None
    if not isinstance(unjudged_before, (int, float)) or isinstance(unjudged_before, bool):
        return None

    remaining = int(unjudged_before) - int(selected)
    return (
        f"[evolve-anything] llm_judge 日次上限に到達（{int(selected)}件処理・"
        f"残り{remaining}件は翌日以降に持ち越し）。{suffix}"
    )


def judge_cap_notice_output(
    queue_data: "dict | None",
    now: "datetime | None" = None,
    stale_hours: int = DEFAULT_STALE_HOURS,
) -> "dict | None":
    """CC hook 出力用に systemMessage dict を返す。上限未到達なら None。"""
    msg = build_judge_cap_notice(queue_data, now=now, stale_hours=stale_hours)
    if msg is None:
        return None
    return {"systemMessage": msg}
