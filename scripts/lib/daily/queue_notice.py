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

# generated_at がこの日数以上古ければ stale（#351 freshness gate の共通閾値として使う）。
# #351 以前は「本来の通知に stale 一文を添える」だけだったので 2 日で足りたが、
# gate 化で **通知本体（待ち PJ 一覧）が消える**ようになったため誤検知コストが上がった。
# 週末に PC を閉じて launchd が2日走らないケースを FRESH に保つため 3 日に緩めている。
# 恒久障害（#351 の16日沈黙）の検出力は 3 日でも変わらない。
# #466 で queue 系の既定判定は下記 DEFAULT_STALE_HOURS（時間単位）へ切り替えた。この定数は
# 他モジュール（fleet/cli_propose.py 等）が独立に参照しているため互換のため残す。
DEFAULT_STALE_DAYS = 3

# generated_at がこの時間数以上古ければ stale（#466: 日単位の 3 日 = 72 時間だと停止に
# 気づけるのが最大 72 時間後になり手遅れ。当日中に気づければ手動で
# `bin/evolve-daily-run` を回して取り返せるため、時間単位に緩和する）。
# 30 時間の根拠: 09:00 実行が正常な運用サイクルで、
#   - 翌日 08:00 のセッションは前回実行から 23 時間 → 正常な沈黙（まだ発火させない）
#   - 翌日 15:00 のセッションで前日分しか無ければ 30 時間 → 当日中にまだ手動実行で
#     取り返せるタイミングで発火させる
DEFAULT_STALE_HOURS = 30

REMEDIATION_HINT = (
    "今日中に `bin/evolve-daily-run` を1回実行すれば、その日の分は取り返せます。"
    "止まったまま日をまたぐと、その週の集計は不合格として確定し、週次の数字が出るのが"
    "さらに1週間先送りになります。繰り返すなら "
    "`launchctl list | grep com.evolve-anything.daily` で毎朝の登録状況を確認してください。"
)


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
    stale_days: int = DEFAULT_STALE_DAYS,
) -> "str | None":
    """待ち PJ 一覧の通知メッセージを生成する。

    評価順序（#351 freshness gate）: (a) generated_at の鮮度を先に判定する →
    (b) FRESH でなければ待ち PJ 一覧を一切解釈せず health notice を返す（queue が
    空でも stale なら通知する＝producer 停止の見逃しを防ぐ回帰対応）→
    (c) FRESH のときだけ待ち PJ 一覧を評価し、空なら沈黙する。

    - queue_data が None → None（daily runner 未実行 = 壊れているのでなく単に未セットアップ）
    - FRESH かつ待ち PJ あり → 「evolve 待ち: <pj…>（N 件）」
    - FRESH かつ待ち PJ 無し → None（沈黙）
    - STALE / UNKNOWN → 待ち PJ の中身に関わらず health notice（旧値は併記しない）
    """
    if not isinstance(queue_data, dict):
        return None

    now = now or datetime.now(timezone.utc)
    state, age_days = _freshness.classify_freshness(
        queue_data.get("generated_at"),
        now=now,
        stale_days=stale_days,
        stale_hours=DEFAULT_STALE_HOURS,
    )
    if state != _freshness.Freshness.FRESH:
        age_hours = _freshness.age_in_hours(queue_data.get("generated_at"), now=now)
        return _freshness.health_notice(
            label="毎朝の自動記録",
            freshness=state,
            age_days=age_days,
            age_hours=age_hours,
            remediation=REMEDIATION_HINT,
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
    stale_days: int = DEFAULT_STALE_DAYS,
) -> "dict | None":
    """CC hook 出力用に systemMessage dict を返す。待ちが無ければ None。"""
    msg = build_queue_notice(queue_data, now=now, stale_days=stale_days)
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
    stale_days: int = DEFAULT_STALE_DAYS,
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
        stale_days=stale_days,
        stale_hours=DEFAULT_STALE_HOURS,
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
    stale_days: int = DEFAULT_STALE_DAYS,
) -> "dict | None":
    """CC hook 出力用に systemMessage dict を返す。上限未到達なら None。"""
    msg = build_judge_cap_notice(queue_data, now=now, stale_days=stale_days)
    if msg is None:
        return None
    return {"systemMessage": msg}
