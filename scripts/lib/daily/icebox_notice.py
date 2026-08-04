"""icebox-status.json reader + SessionStart 通知メッセージ生成（#194）。

毎朝の `gh issue list --label icebox --state closed` が `$CLAUDE_PLUGIN_DATA/icebox-status.json`
に保存した凍結 issue の件数・最古経過日数（`oldest_days`）を、対話セッション開始時に
systemMessage（ADR-038 = user 向けチャネル）として surface する軽量な気づきトリガー。

icebox は evolve-anything 自身の GitHub issue backlog なので、本体リポジトリで作業している
ときだけ配信対象になるべきだが、その plugin_self 判定は呼び出し側（hooks/restore_state.py）の
責務とし、本モジュールは read 専用・純関数・決定論（LLM 非依存）に留める。

`icebox-status.json` は派生物（SoR でない）ため store_registry には登録しない。

#352: `icebox-verdicts.json`（daily runner の icebox 3レーン棚卸しステップが書く決定論分類
結果）を読み、レーン1「成立」issue だけを名指しで通知するレーン1 SessionStart 通知も
本モジュールが提供する。個別 issue を列挙しない件数集約通知（上記）とは異なり、レーン1は
仕様上「該当 issue だけ名指し + 根拠1行」（#352 issue 本文）。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import freshness as _freshness  # #351 P0: freshness gate の単一ソース
from . import plist as _plist  # #352 P6: icebox-verdicts.json パスの単一ソース

ICEBOX_FILE_NAME = "icebox-status.json"

# icebox-verdicts.json の generated_at がこれより古ければ「daily runner が止まっている
# 可能性」を通知に添える（daily runner は毎朝1回なので1日の実行漏れまでは許容する）。
STALE_VERDICTS_DAYS = 2

# レーン1「成立」通知で名指しする issue 数の上限（#352 B4）。audit 側の
# MAX_LISTED_ISSUES（`audit/sections_icebox_reconcile.py`）と対称。全件成立が積み上がる
# ケースで systemMessage が無制限に伸びるのを防ぐ（untrusted 入力由来ではないが、
# 可読性・出力サイズの観点で同じ cap パターンを踏襲する）。
MAX_MET_ISSUES = 10

# oldest_days がこの日数以上なら通知する既定閾値。
# 実運用（hooks/restore_state.py 経由）ではこの値でなく rl_common.config の
# userConfig 既定 `icebox_review_threshold_days`（既定30・呼び出し側で override 可）が渡される。
# ここでの 90 は呼び出し元が threshold_days を明示しない場合のライブラリ関数フォールバックに留まる。
DEFAULT_THRESHOLD_DAYS = 90

# icebox-status.json の generated_at がこれより古ければ freshness gate（#351）が
# 業務値（count/oldest_days）の解釈を止め health notice に差し替える。
# STALE_VERDICTS_DAYS（icebox-verdicts.json 用）と同値にして daily runner の
# 実行周期（毎朝1回）に対する許容度を揃える。
STALE_STATUS_DAYS = STALE_VERDICTS_DAYS

ICEBOX_REMEDIATION_HINT = "bin/evolve-daily-install"


def read_icebox_status(data_dir) -> "dict | None":
    """data_dir/icebox-status.json を読んで dict を返す。無い/壊れていれば None。"""
    path = Path(data_dir) / ICEBOX_FILE_NAME
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def build_icebox_notice(
    status: "dict | None",
    now: "datetime | None" = None,
    threshold_days: int = DEFAULT_THRESHOLD_DAYS,
    stale_days: int = STALE_STATUS_DAYS,
) -> "str | None":
    """icebox 棚卸しの気づき通知メッセージを生成する。閾値未満なら None（沈黙）。

    評価順序（#351 freshness gate）: (a) `generated_at` の鮮度を先に判定する →
    (b) FRESH でなければ `count`/`oldest_days` を一切解釈せず health notice を返す
    （閾値未満の値でも stale なら通知する＝producer 停止の見逃しを防ぐ回帰対応）→
    (c) FRESH のときだけ `count`/`oldest_days` を評価する。

    - status が None / dict でない → None
    - FRESH かつ `oldest_days`/`count` が欠落・非数値 → None（判定不能として沈黙）
    - FRESH かつ `oldest_days` が `threshold_days` 未満 → None（沈黙）
    - FRESH かつ `oldest_days` が `threshold_days` 以上 → 1行に集約したメッセージを返す
      （個別 issue ごとの表示は絶対にしない）
    - STALE / UNKNOWN → `count`/`oldest_days` の値に関わらず health notice
      （旧値は併記しない。`oldest_days=0` のような正当な業務値の 0 と混同しない）
    """
    if not isinstance(status, dict):
        return None

    now = now or datetime.now(timezone.utc)
    state, age_days = _freshness.classify_freshness(
        status.get("generated_at"), now=now, stale_days=stale_days
    )
    if state != _freshness.Freshness.FRESH:
        return _freshness.health_notice(
            label="icebox 集計",
            freshness=state,
            age_days=age_days,
            remediation=ICEBOX_REMEDIATION_HINT,
        )

    oldest_days = status.get("oldest_days")
    count = status.get("count")
    if not isinstance(oldest_days, (int, float)) or isinstance(oldest_days, bool):
        return None
    if not isinstance(count, (int, float)) or isinstance(count, bool):
        return None
    if oldest_days < threshold_days:
        return None

    return (
        f"[evolve-anything] icebox {int(count)}件・最古{int(oldest_days)}日。"
        "`gh issue list --label icebox --state closed` で棚卸しを検討してください。"
    )


def icebox_notice_output(
    status: "dict | None",
    now: "datetime | None" = None,
    threshold_days: int = DEFAULT_THRESHOLD_DAYS,
    stale_days: int = STALE_STATUS_DAYS,
) -> "dict | None":
    """CC hook 出力用に systemMessage dict を返す。閾値未満なら None。"""
    msg = build_icebox_notice(status, now=now, threshold_days=threshold_days, stale_days=stale_days)
    if msg is None:
        return None
    return {"systemMessage": msg}


# ─────────────────────────────────────────────────────────────────
# #352: icebox-verdicts.json（3レーン判定）reader + レーン1「成立」通知
# ─────────────────────────────────────────────────────────────────
def read_icebox_verdicts(data_dir) -> "dict | None":
    """data_dir/icebox-verdicts.json を読んで dict を返す。無い/壊れていれば None。"""
    path = Path(_plist.icebox_verdicts_json_path(str(data_dir)))
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def unseen_met_verdicts(
    verdicts_payload: "dict | None", seen_keys: "set[str]"
) -> "list[dict]":
    """`verdicts_payload["verdicts"]` からレーン1「成立」かつ未既読のものだけを返す。

    payload が None / dict でない / `verdicts` を持たない場合は空リスト（沈黙側）。
    既読判定は icebox_verdict_seen（issue番号 + 評価値ハッシュ）に委譲する。
    """
    if not isinstance(verdicts_payload, dict):
        return []
    verdicts = verdicts_payload.get("verdicts")
    if not isinstance(verdicts, list):
        return []
    mets = [v for v in verdicts if isinstance(v, dict) and v.get("lane") == "met"]
    if not mets:
        return []
    import icebox_verdict_seen  # 遅延 import（sys.path 依存を呼び出し側に閉じ込める）

    return icebox_verdict_seen.filter_unseen(mets, seen_keys)


def stale_advisory(generated_at: "str | None", now: "datetime | None") -> str:
    """generated_at が STALE_VERDICTS_DAYS 超過なら advisory 文言、そうでなければ空文字。

    generated_at 欠落・パース不能は「判定不能」として advisory を付けない
    （沈黙よりは本体メッセージを出す方が安全側 — build_met_notice 側の方針）。
    """
    if not generated_at:
        return ""
    try:
        dt = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ref_now = now or datetime.now(timezone.utc)
    age_days = (ref_now - dt).days
    if age_days < STALE_VERDICTS_DAYS:
        return ""
    return f"（verdicts データ生成から{age_days}日経過 — daily runner の実行を確認してください）"


def build_met_notice(
    verdicts: "list[dict]",
    *,
    generated_at: "str | None" = None,
    now: "datetime | None" = None,
) -> "str | None":
    """レーン1「成立」verdict 群から SessionStart 通知メッセージを組み立てる。

    verdicts が空なら None（沈黙）。1件以上あれば該当 issue を名指しし、根拠
    （verdict["reason"]）を添える（件数集約通知とは異なり個別列挙が仕様）。`MAX_MET_ISSUES`
    件を超える分は「...他 N 件」に畳む（#352 B4・audit 側 MAX_LISTED_ISSUES と対称）。
    generated_at が古ければ末尾に staleness advisory を付す。
    """
    if not verdicts:
        return None
    shown = verdicts[:MAX_MET_ISSUES]
    segments = [f"#{v.get('number')}（{v.get('reason', '')}）" for v in shown]
    remaining = len(verdicts) - len(shown)
    body = " / ".join(segments)
    if remaining > 0:
        body = f"{body} ...他 {remaining} 件"
    msg = f"[evolve-anything] icebox 再開条件が成立しました: {body}"
    stale = stale_advisory(generated_at, now)
    if stale:
        msg = f"{msg} {stale}"
    return msg


def icebox_verdicts_notice_output(
    verdicts_payload: "dict | None", seen_keys: "set[str]", now: "datetime | None" = None
) -> "tuple[dict | None, list[dict]]":
    """CC hook 出力用に (systemMessage dict|None, 今回名指しした verdict 群) を返す。

    呼び出し側は返った verdict 群を（表示できたら）icebox_verdict_seen.record_seen に渡し
    既読化する。成立が無ければ (None, []) を返し、呼び出し側は従来の icebox_notice_output
    （件数集約）へフォールバックしてよい。
    """
    shown = unseen_met_verdicts(verdicts_payload, seen_keys)
    if not shown:
        return None, []
    # #352 P9: shown が非空になるのは unseen_met_verdicts 内の isinstance(verdicts_payload, dict)
    # チェックを通過した場合のみ（そうでなければ [] で早期 return 済み）なので、ここで
    # verdicts_payload が dict であることは保証済み。旧実装の isinstance 分岐は到達不能だった。
    generated_at = verdicts_payload.get("generated_at")
    msg = build_met_notice(shown, generated_at=generated_at, now=now)
    if msg is None:
        return None, []
    return {"systemMessage": msg}, shown
