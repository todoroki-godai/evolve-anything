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

# ストアの物理形式。jsonl（hook append 系）と db（batch ingest 系 DuckDB SoR）を区別する。
# orphan_store / contract-drift の hook-writer 突合は jsonl のみ対象（writer が hook の append
# だから）。db ストアは writer が batch ingest なので、その突合の母集団には含めない（#430）。
StoreKind = Literal["jsonl", "db"]

# orphan（reader 0）ストアの処遇分類。
# - keep_future : 将来の基盤として意図的に reader 不在（消さない）
# - drain       : enqueue→drain 2相など、reader が別経路（DB 取り込み等）で jsonl 直読しない
# - remove      : 不要。writer/hook ごと削除予定（暫定で残すが orphan として surface してよい）
DispositionKind = Literal["keep_future", "drain", "remove"]

# writer の所在。stale 突合（宣言あり / 実 hook writer なし）の母集団から除外するかを決める。
# - hook  : hooks.json 登録 hook の append が書く（find_store_writers で拾える。stale 突合対象）
# - batch : evolve/audit 等の batch script が書く（hook には現れない。stale 突合の対象外）
#           db ストアと同じ理由で、jsonl でも batch writer は hook-writer 突合に出ないため除外する。
WriterLocus = Literal["hook", "batch"]


@dataclass(frozen=True)
class StoreDeclaration:
    """1 ストアの契約宣言。

    name:         ストアの basename（例: "corrections.jsonl" / "utterances.db"）
    writer:       書き込み側の説明（どの hook/script が書くか — 人間可読 evidence）
    reader:       読み取り側の説明（誰が消費するか。reader 不在の場合は disposition で説明）
    retention:    保持ポリシー種別（permanent / ttl / compaction）
    kind:         物理形式（"jsonl" 既定 / "db"）。db は hook-writer 突合の対象外（#430）
    writer_locus: 書き込み主体（"hook" 既定 / "batch"）。batch は hook-writer 突合に出ない
                  ため stale 突合の対象外（#432: weak_signals.jsonl は batch 書き込み jsonl）
    ttl_days:     retention="ttl" のときの失効日数（それ以外は None）
    compaction:   retention="compaction" のときの圧縮条件（散文。それ以外は None）
    disposition:  reader 不在（orphan）ストアの処遇。reader がある通常ストアは None
    note:         補足（任意）
    """

    name: str
    writer: str
    reader: str
    retention: RetentionKind
    kind: StoreKind = "jsonl"
    writer_locus: WriterLocus = "hook"
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
        name="utterances.db",
        kind="db",
        writer="scripts/lib/utterance_archive/ingest.py（evolve/audit batch + rl-fleet ingest）。"
        "hot path（hooks）からは書かない。",
        reader="utterance_archive.query（query_utterances / query_utterances_all_projects）。"
        "下流: #431 個人辞書・#432 暗黙シグナル・遡及分析。",
        retention="permanent",
        note="全PJ human 発話の恒久アーカイブ（#430）。物理 PK (source_path,line_no) + "
        "論理 UNIQUE (session_id,timestamp,text_hash)。writer は batch ingest のみ。",
    ),
    StoreDeclaration(
        name="weak_signals.jsonl",
        writer="scripts/lib/weak_signals/batch.py（evolve/audit batch から run_batch）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="reflect が確認後に corrections 本流へ昇格（promoted フラグ）。"
        "audit が channel 別件数を advisory surface（sections_weak_signals）。"
        "下流: #431 のバッチ LLM 判定もこのレーンを共有。",
        retention="ttl",
        ttl_days=45,
        note="暗黙修正シグナルの決定論検出レーン（#432）。4 チャネル（直後手編集 / "
        "permission deny / 言い直し / Esc 中断）。corrections に直接入れず昇格は reflect 確認後。"
        "TTL 45 日（#442・corrections decay と整合）: detected_at 超過は削除せず expired=True "
        "マークし read_unpromoted から除外（weak_signals.ttl.mark_expired を evolve phase で常時 emit）。",
    ),
    StoreDeclaration(
        name="correction_idioms.jsonl",
        writer="scripts/lib/correction_semantic/batch.py（evolve batch の Phase C ingest）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="reflect が --show-weak-signals で参照（個人辞書）。"
        "idiom_autopromote が confirmed=True の idiom を read_confirmed_idiom_keys で読み自動昇格（ADR-047）。"
        "実コーパスで precision 検証後に hot hook の補助パターンへ昇格可能（#431 提案2）。",
        retention="permanent",
        note="バッチ LLM 意味判定が抽出した修正言い回しの個人辞書（#431）。provenance"
        "（元発話の物理キー・判定理由）付き。idiom+物理キーの安定ハッシュで dedup。"
        "confirmed/confirmed_at/confirmed_by/revoked_at を持ち（ADR-047・#447）、人間が #446 review で"
        "「はい」確定時に confirmed=True 化。confirmed=True が立つまで idiom_autopromote は一切発動しない"
        "（雪崩防止）。revoke（安全弁③）で confirmed=False + revoked_at に戻す。",
    ),
    StoreDeclaration(
        name="correction_judged.jsonl",
        writer="scripts/lib/correction_semantic/batch.py（Phase C ingest 完了発話の物理キー記録）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="correction_semantic.batch.emit_judgement_requests が再判定除外に参照（自己消費）。",
        retention="permanent",
        disposition="drain",
        note="バッチ LLM 意味判定の進捗カーソル（#431）。判定済み発話（source_path:line_no）を"
        "記録し、無駄な LLM 再判定を防ぐ。reader は同 package の emit のみ（自己消費）。",
    ),
    StoreDeclaration(
        name="bootstrap_done-<slug>.marker",
        writer="scripts/lib/correction_semantic/bootstrap_backlog.mark_done"
        "（evolve の SKILL.md が「まとめて確認」完了時・「TTL 失効に任せる」選択時に呼ぶ）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="bootstrap_backlog.build / is_done が初回判定に参照（自己消費）。"
        "marker 立ち後は is_bootstrap=False で即返す。",
        retention="permanent",
        disposition="drain",
        note="初回バックログ bootstrap の完了 marker（#443）。空ファイル。PJ slug スコープ"
        "（bootstrap_done-<slug>.marker・全PJ共通 DATA_DIR 単一ファイル pitfall 回避）。"
        "立ったら以後 bootstrap を再提示しない（TTL #5 が残りを間引く）。",
    ),
    StoreDeclaration(
        name="correction_review_seen.jsonl",
        writer="scripts/lib/correction_semantic/daily_review.record_reviewed"
        "（evolve の SKILL.md が「今日の修正確認」で「はい/いいえ」確定時に呼ぶ）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="daily_review.build_review / read_reviewed_keys が「新規」判定に参照（自己消費）。"
        "既読 signal_key は次回 evolve で再提示しない。",
        retention="permanent",
        disposition="drain",
        note="今日の修正確認の既読集合（#446）。correction_judged.jsonl と同方式の物理キー集合"
        "（append-only・1 行 {key, pj_slug, decision, reviewed_at}）。PJ slug スコープ"
        "（全PJ共通 DATA_DIR 単一ファイル pitfall 回避）。母集団は weak_signals（TTL 45 日で"
        "自然減衰・数百件規模）なので肥大化は無視できる。重複追記は read 側 set 化で無害。",
    ),
    StoreDeclaration(
        name="remediation_suppression/<slug>.jsonl",
        writer="scripts/lib/remediation/suppression_ledger.record_rejection"
        "（evolve の SKILL.md が remediation 個別承認で「却下/スキップ」確定時に呼ぶ）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="suppression_ledger.is_suppressed / filter_suppressed が次回 evolve の "
        "remediation proposable 候補から却下済みを除外（evolve._apply_remediation_suppression"
        "経由・自己消費）。",
        retention="ttl",
        ttl_days=45,
        disposition="drain",
        note="remediation 個別承認で却下された提案の suppression ledger（#477）。べき等性原則"
        "（重複提案 MUST NOT）の実装。dedup_key（type+file+主要detail の sha256 先頭16hex）単位の"
        "append-only・load 時 last-write-wins collapse。triage_ledger（#308）を範に PJ slug スコープ"
        "（全PJ共通 DATA_DIR 単一ファイル pitfall 回避）・worktree 安全 slug・dry-run 非書込。"
        "TTL45日経過で 1 回だけ再 surface（環境変化での再評価機会）。",
    ),
    StoreDeclaration(
        name="remediation_surfaced/<slug>.json",
        writer="scripts/lib/remediation/suppression_ledger.reconcile_surfaced"
        "（evolve の remediation phase が個別承認候補確定後に毎 run 1 回呼ぶ）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="reconcile_surfaced 自身が次回 evolve で前回の連続提示回数を参照（自己消費）。"
        "閾値到達で remediation_suppression へ自動却下を昇格させる。",
        retention="permanent",
        disposition="drain",
        note="record_rejection の決定論 fallback の surfaced マーカー（#494）。SKILL.md Step 5.5 の"
        "inline record_rejection を取りこぼしても、解決されないまま連続 surface された提案を"
        "閾値回数（既定2）で自動却下する安全網。per-slug 単一 JSON（dedup_key→{count, first_seen,"
        " last_seen}）で上書き。提案が検出されなくなれば marker から落ちる（解決＝却下しない）。"
        "PJ slug スコープ（全PJ共通 DATA_DIR 単一ファイル pitfall 回避）・dry-run 非書込。"
        "肥大化しない（毎 run 上書き・未解決提案のみ保持）。",
    ),
]


def declarations_by_kind(kind: StoreKind) -> List[StoreDeclaration]:
    """指定 kind の宣言だけを返す（jsonl の hook-writer 突合などで使う）。"""
    return [d for d in _DECLARATIONS if d.kind == kind]


def stale_exempt_names() -> List[str]:
    """stale 突合（宣言あり / 実 hook writer なし）から除外すべきストア名（ソート済み）。

    hook-writer 突合（find_store_writers）に現れない writer を持つストアは、
    宣言があっても「実 writer 見当たらず」で stale 誤検知になる。除外対象:
    - kind="db"          : writer が batch ingest（utterances.db）
    - writer_locus="batch": writer が batch script（weak_signals.jsonl 等）

    両者は同じ理由（hook に現れない writer）なので 1 関数で集約する（#432）。
    """
    return sorted(
        {d.name for d in _DECLARATIONS if d.kind == "db" or d.writer_locus == "batch"}
    )


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
