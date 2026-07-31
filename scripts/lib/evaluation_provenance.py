#!/usr/bin/env python3
"""evaluation_provenance — 評価実行条件（harness）の記録契約（#309）。

スコアは「モデル単体の性能」ではなく **model × effort × tool policy × plugin version**
という実行条件込みの束を測っている。条件を記録していなければ、スコアが動いたときに
「スキルが劣化した」のか「判定モデルを差し替えた」のかを事後に分離できない。
過去分は遡及不能なので、記録だけを先に始める（比較・交絡補正は #240 で凍結中）。

契約:
  - **不明値を推測しない。** 観測できなければ ``None`` を記録する
  - **非該当と観測不能を区別する。** 決定論評価に ``judge`` キーは持たせない
    （``evaluation_kind`` で判別できる）。LLM 判定なのに条件が取れなかった場合は
    ``judge`` キーを残したまま中身を ``None`` にする
  - **model alias は verbatim。** ``sonnet`` を具体バージョンへ展開しない
  - **集約は単一 model へ潰さない。** ``judge_models`` + ``mixed_provenance`` で表現する

レイヤーごとの責務（この module はその 2 段目と 3 段目を提供する）:
  1. 評価実行地点 — model / effort / tool policy を捕捉するのは producer の責務。
     ストア層は評価条件を知る手段がないので、そこで発見させない（誤った provenance を生む）
  2. 共通ビルダー — schema version / plugin version / runtime / 時刻の正規化（本 module）
  3. writer 直前 — provenance の存在確認と共通項目の補完（``finalize_provenance``）
  4. store 関数 — 渡されたレコードをそのまま append（変更しない）

新ストアは作らない。既存 jsonl（judge_audit_verdicts / optimize_history）と
constitutional の中間 cache をフィールド拡張するだけで足りる
（``store_write`` barrier はレコード内フィールドを検証しないため barrier 変更も不要）。
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 1

PLUGIN_NAME = "evolve-anything"

# --- evaluation_kind ---------------------------------------------------------
# LLM judge が直接スコアを出した評価。
KIND_LLM_JUDGE = "llm_judge"
# 複数レイヤー（別時点・別モデルでありうる）の judge 結果を集約したスコア。
KIND_LLM_JUDGE_AGGREGATE = "llm_judge_aggregate"
# LLM を呼ばない評価。model は非該当。
KIND_DETERMINISTIC = "deterministic"
# provenance が付かないまま writer 境界に到達したレコード（旧経路・観測失敗）。
KIND_UNKNOWN = "unknown"

_KINDS = frozenset({KIND_LLM_JUDGE, KIND_LLM_JUDGE_AGGREGATE, KIND_DETERMINISTIC, KIND_UNKNOWN})

# --- tool policy の観測方法 ---------------------------------------------------
# `claude -p <prompt> --model <m>` のようにツール指定なしで CLI を起動した（観測済み）。
TOOL_POLICY_CLI_DEFAULT = "cli_default"
# 対話セッションのポリシーを継承した（呼出側の申告であって実効ポリシーの観測ではない）。
TOOL_POLICY_SESSION = "session"
# 観測できなかった。
TOOL_POLICY_UNKNOWN = "unknown"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def plugin_version() -> Optional[str]:
    """実行中コードの ``.claude-plugin/plugin.json`` から version を読む。

    ハードコードすると bump 忘れで嘘の provenance になるため実ファイルから読む。
    読めなければ推測せず None（fail-open）。
    """
    try:
        from plugin_root import PLUGIN_ROOT

        raw = (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        version = json.loads(raw).get("version")
        return version if isinstance(version, str) and version else None
    except Exception:  # noqa: BLE001 - provenance の欠損は本処理を止めない
        return None


def build_judge_context(
    *,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    tool_policy_mode: str = TOOL_POLICY_UNKNOWN,
    allowed_tools: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """LLM judge の実行条件を組み立てる（値は verbatim・欠損は None）。"""
    return {
        "model": model,
        "effort": effort,
        "tool_policy": {"mode": tool_policy_mode, "allowed_tools": allowed_tools},
    }


def build_provenance(
    *,
    evaluation_kind: str,
    producer: str,
    judge: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    inputs: Optional[Dict[str, Any]] = None,
    runtime_name: Optional[str] = None,
    session_id: Optional[str] = None,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    """provenance envelope を組み立てる。

    Args:
        evaluation_kind: ``KIND_*`` のいずれか。未知の値は ValueError（契約違反）。
        producer:        評価を出した主体の識別子（例 ``judge_audit`` / ``chaos.compute_chaos_score``）。
        judge:           ``build_judge_context`` の戻り。LLM judge 系 kind では省略しても
                         「観測不能」を表す空の judge が入る（キー自体は残す）。
        config:          結果を変え得る設定またはその fingerprint（決定論評価向け）。
        inputs:          入力窓など結果を左右する入力条件（telemetry 等）。
        runtime_name:    観測できた runtime 名。**推測しない**（未観測は None のまま）。
        recorded_at:     provenance を組み立てた時刻。評価結果自体の時刻
                         （``judged_at`` / ``timestamp``）とは別物なので上書きしない。
    """
    if evaluation_kind not in _KINDS:
        raise ValueError(f"unknown evaluation_kind: {evaluation_kind!r}")

    prov: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_kind": evaluation_kind,
        "producer": producer,
        "runtime": {"name": runtime_name, "session_id": session_id},
        "plugin": {"name": PLUGIN_NAME, "version": plugin_version()},
        "recorded_at": recorded_at or _now_iso(),
    }
    if evaluation_kind in (KIND_LLM_JUDGE, KIND_LLM_JUDGE_AGGREGATE):
        # LLM 判定であることは分かっているので、条件が取れなくても judge キーは残す
        # （決定論評価の「非該当」と区別するため）。
        prov["judge"] = judge if judge is not None else build_judge_context()
    elif judge is not None:
        prov["judge"] = judge
    if config is not None:
        prov["config"] = config
    if inputs is not None:
        prov["inputs"] = inputs
    return prov


def judge_model(prov: Optional[Dict[str, Any]]) -> Optional[str]:
    """provenance から judge model を取り出す（欠損に寛容）。"""
    if not isinstance(prov, dict):
        return None
    judge = prov.get("judge")
    if not isinstance(judge, dict):
        return None
    model = judge.get("model")
    return model if isinstance(model, str) and model else None


def aggregate_provenance(
    producer: str,
    layer_provenances: Iterable[Optional[Dict[str, Any]]],
    *,
    layers_total: Optional[int] = None,
    runtime_name: Optional[str] = None,
    recorded_at: Optional[str] = None,
) -> Dict[str, Any]:
    """レイヤー単位 provenance を集約する（単一 model へ潰さない）。

    集約スコアに 1 つのモデル名を無理に入れると嘘になる。cache は別時点・別モデルで
    生成されたレイヤーが混在しうるため、モデルの**集合**と混在フラグで表現する。
    provenance を持たないレイヤー（旧 cache）が 1 つでもあれば揃っていない扱いにする。
    """
    provs = list(layer_provenances)
    with_prov = [p for p in provs if isinstance(p, dict)]
    models = sorted({m for m in (judge_model(p) for p in with_prov) if m})
    total = layers_total if layers_total is not None else len(provs)
    missing = total - len(with_prov)

    agg = build_provenance(
        evaluation_kind=KIND_LLM_JUDGE_AGGREGATE,
        producer=producer,
        judge=build_judge_context(),
        runtime_name=runtime_name,
        recorded_at=recorded_at,
    )
    agg["judge_models"] = models
    agg["mixed_provenance"] = bool(len(models) > 1 or (missing > 0 and with_prov))
    agg["layers_with_provenance"] = len(with_prov)
    agg["layers_total"] = total
    return agg


def finalize_provenance(prov: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """writer 直前で共通項目を補完する（producer が入れた値は上書きしない）。

    ``None``（provenance を作らずここまで来たレコード）は捏造せず、
    ``evaluation_kind=unknown`` の明示的な「観測なし」として記録する。冪等。
    """
    out: Dict[str, Any] = dict(prov) if isinstance(prov, dict) else {}
    out.setdefault("schema_version", SCHEMA_VERSION)
    out.setdefault("evaluation_kind", KIND_UNKNOWN)
    out.setdefault("producer", None)
    runtime = out.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    out["runtime"] = {"name": runtime.get("name"), "session_id": runtime.get("session_id")}
    plugin = out.get("plugin")
    if not isinstance(plugin, dict) or not plugin.get("version"):
        out["plugin"] = {"name": PLUGIN_NAME, "version": plugin_version()}
    out.setdefault("recorded_at", _now_iso())
    return out


def attach_provenance(
    record: Dict[str, Any], prov: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """レコードに provenance を付けて返す（永続化境界で呼ぶ・in-place）。"""
    record["provenance"] = finalize_provenance(prov)
    return record


def read_provenance(record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """レコードから provenance を読む。旧レコード（欠損）は None（遡及埋めしない）。"""
    if not isinstance(record, dict):
        return None
    prov = record.get("provenance")
    return prov if isinstance(prov, dict) else None
