"""store_registry.py — ストア新設の事前契約ゲート（#434）。

新しい jsonl ストアを追加するときに **writer / reader / retention の3点宣言** を必須化する。
orphan_store（#422/#426/#427）は「writer あり reader 0」を**事後**検出するモグラ叩きだった。
本 registry は宣言を SoT にすることで、宣言なしの新規 writer を audit が**事前**に検出できるようにする。

機械可読な宣言 dict を採用した理由:
- 既存の `_OBSERVABILITY_BUILDERS`（observability.py）や `hook_drift` の宣言慣習が Python dict なので統一する
- 宣言を消費する orphan_store 検出（同じ scripts/lib 配下）から import 一発で参照でき、JSON parse 経路を増やさない
- retention を enum（恒久 / TTL N日 / compaction 条件）で型付けできる

宣言の単位はストアの **basename**（例: `corrections.jsonl`）。本 PJ のストアは全て
`DATA_DIR / "<name>.jsonl"` 形式で扱われるため、orphan_store の突合（ファイル名文字列）と一致する。

retention の3種別:
- `permanent`   : 恒久保持（SoR / 履歴。削除しない）
- `ttl`         : N 日で失効（`ttl_days` 必須）
- `compaction`  : サイズ/件数条件で圧縮・ローテーション（`compaction` に条件を散文で記述）

各エントリは StoreDeclaration を生成する build。disposition は orphan（reader 0）の処遇を
明示するためのフィールド（issue #434 の「orphan の disposition も宣言に含める」要件）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

# retention の種別。
RetentionKind = Literal["permanent", "ttl", "compaction"]

# orphan（reader 0）ストアの処遇分類。
# - keep_future : 将来の基盤として意図的に reader 不在（消さない）
# - drain       : enqueue→drain 2相など、reader が別経路（DB 取り込み等）で jsonl 直読しない
# - remove      : 不要。writer/hook ごと削除予定（暫定で残すが orphan として surface してよい）
DispositionKind = Literal["keep_future", "drain", "remove"]


@dataclass(frozen=True)
class StoreDeclaration:
    """1 ストアの契約宣言。

    name:        jsonl ストアの basename（例: "corrections.jsonl"）
    writer:      書き込み側の説明（どの hook/script が書くか — 人間可読 evidence）
    reader:      読み取り側の説明（誰が消費するか。reader 不在の場合は disposition で説明）
    retention:   保持ポリシー種別（permanent / ttl / compaction）
    ttl_days:    retention="ttl" のときの失効日数（それ以外は None）
    compaction:  retention="compaction" のときの圧縮条件（散文。それ以外は None）
    disposition: reader 不在（orphan）ストアの処遇。reader がある通常ストアは None
    note:        補足（任意）
    """

    name: str
    writer: str
    reader: str
    retention: RetentionKind
    ttl_days: Optional[int] = None
    compaction: Optional[str] = None
    disposition: Optional[DispositionKind] = None
    note: Optional[str] = None


# 宣言 SoT。新ストアを追加するときはここに 1 エントリ足す。
# 足さずに hook が新 jsonl を書くと orphan_store 検出が `undeclared` として surface する（#434）。
_DECLARATIONS: List[StoreDeclaration] = [
    StoreDeclaration(
        name="corrections.jsonl",
        writer="hooks/correction_detect.py（ユーザー修正の検出時）",
        reader="reflect / discover / optimize 等が消費（reader 多数）",
        retention="permanent",
        note="修正フィードバックの SoR。reflect の入力源。",
    ),
    StoreDeclaration(
        name="usage.jsonl",
        writer="hooks/observe.py（スキル/コマンド使用ごと）",
        reader="audit / discover / trigger が集計",
        retention="permanent",
        note="使用テレメトリの SoR。",
    ),
    StoreDeclaration(
        name="usage-registry.jsonl",
        writer="hooks/observe.py（既知スキル/コマンド名の登録）",
        reader="usage 集計時に既知名の母集団として参照",
        retention="permanent",
        note="usage の名前マスタ。",
    ),
    StoreDeclaration(
        name="sessions.jsonl",
        writer="hooks/observe.py 等（セッション境界の記録）。hot path は jsonl 追記のみ（#415 Phase A）",
        reader="session_store.ingest() が batch で sessions.db へ取り込み（drain 経路）。"
        "audit / trigger / capture_rate は session_store API（union read）経由で集計",
        retention="compaction",
        compaction="batch ingest（evolve 同居）で sessions.db に取り込み後、live jsonl を "
        ".ingested-<ts> へ rotate（glob 恒久除外・1世代保持）。SoR は sessions.db。"
        "db 側は file_size vs rows×平均行長 の乖離 >10倍 で rebuild compaction",
        disposition="drain",
        note="jsonl は hot path 緩衝。per-fire connect→INSERT→close による sessions.db "
        "再肥大（9.6GB）を根治するため jsonl-first + batch ingest に変更（#415）。",
    ),
    StoreDeclaration(
        name="errors.jsonl",
        writer="hooks（ツールエラー検出時）",
        reader="audit / discover がエラー傾向分析に使用",
        retention="permanent",
        note="エラーテレメトリ。",
    ),
    StoreDeclaration(
        name="workflows.jsonl",
        writer="hooks（ワークフロー系イベント記録）",
        reader="audit / discover が消費",
        retention="permanent",
        note="ワークフローテレメトリ。",
    ),
    StoreDeclaration(
        name="skill_activations.jsonl",
        writer="hooks（スキル発火の記録）",
        reader="audit / negative_transfer が消費",
        retention="permanent",
        note="スキル発火テレメトリ。",
    ),
    StoreDeclaration(
        name="subagents.jsonl",
        writer="hooks（サブエージェント生成の記録）",
        reader="audit / subagent 観測が消費",
        retention="permanent",
        note="サブエージェントテレメトリ。",
    ),
    StoreDeclaration(
        name="message_display.jsonl",
        writer="hooks/message_display.py（アシスタント応答テレメトリ）",
        reader="（現状 jsonl 直読の reader なし — 将来の応答フィルタリング基盤）",
        retention="compaction",
        compaction="1MB 超でローテーション（hooks/message_display.py の _MAX_LOG_BYTES）",
        disposition="keep_future",
        note="reader 0 だが意図的。#427 で orphan 検出された当該ストア。"
        "ローテーション済みなので肥大リスクは抑制済み。",
    ),
]


def declarations() -> List[StoreDeclaration]:
    """宣言の一覧（SoT のコピーでなく参照）。"""
    return _DECLARATIONS


def declared_store_names() -> List[str]:
    """宣言済みストアの basename 一覧（ソート済み）。"""
    return sorted(d.name for d in _DECLARATIONS)


def declaration_for(name: str) -> Optional[StoreDeclaration]:
    """ストア名に対応する宣言を返す（無ければ None）。"""
    for d in _DECLARATIONS:
        if d.name == name:
            return d
    return None


def declarations_by_name() -> Dict[str, StoreDeclaration]:
    """ストア名 → 宣言の dict。"""
    return {d.name: d for d in _DECLARATIONS}


def validate_declarations(decls: Optional[List[StoreDeclaration]] = None) -> List[str]:
    """宣言自身の整合性を検証し、問題メッセージのリストを返す（空 = 健全）。

    - retention="ttl" は ttl_days を必須にする
    - retention="compaction" は compaction を必須にする
    - retention="permanent" は ttl_days / compaction を持たない
    - ストア名の重複を禁止する
    """
    items = decls if decls is not None else _DECLARATIONS
    problems: List[str] = []
    seen: Dict[str, int] = {}
    for d in items:
        seen[d.name] = seen.get(d.name, 0) + 1
        if d.retention == "ttl" and d.ttl_days is None:
            problems.append(f"{d.name}: retention=ttl だが ttl_days 未指定")
        if d.retention == "compaction" and not d.compaction:
            problems.append(f"{d.name}: retention=compaction だが compaction 条件未記述")
        if d.retention == "permanent" and (d.ttl_days is not None or d.compaction):
            problems.append(
                f"{d.name}: retention=permanent に ttl_days/compaction は不整合"
            )
    for name, count in seen.items():
        if count > 1:
            problems.append(f"{name}: 宣言が {count} 件重複")
    return problems
