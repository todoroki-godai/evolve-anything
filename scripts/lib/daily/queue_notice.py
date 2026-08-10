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
DEFAULT_STALE_DAYS = 3

REMEDIATION_HINT = "bin/evolve-daily-install で日次更新が回っているか確認してください"


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
        queue_data.get("generated_at"), now=now, stale_days=stale_days
    )
    if state != _freshness.Freshness.FRESH:
        return _freshness.health_notice(
            label="evolve queue",
            freshness=state,
            age_days=age_days,
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
    """
    if not isinstance(queue_data, dict):
        return None

    now = now or datetime.now(timezone.utc)
    state, _age_days = _freshness.classify_freshness(
        queue_data.get("generated_at"), now=now, stale_days=stale_days
    )
    if state != _freshness.Freshness.FRESH:
        return None

    judge = queue_data.get("llm_judge")
    if not isinstance(judge, dict):
        return None

    if judge.get("source_failed"):
        error = judge.get("source_error") or "詳細は daily runner のログを確認してください"
        return f"[evolve-anything] llm_judge の発話ソース取得に失敗しました: {error}"

    if not judge.get("capped"):
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
        f"残り{remaining}件は翌日以降に持ち越し）。"
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
