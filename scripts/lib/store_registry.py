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

# ストアの物理形式。jsonl（hook append 系）・db（batch ingest 系 DuckDB SoR）・
# json（単一 JSON オブジェクトの丸ごと上書き）を区別する。
# orphan_store / contract-drift の hook-writer 突合は jsonl のみ対象（writer が hook の append
# だから）。db ストアは writer が batch ingest なので、その突合の母集団には含めない（#430）。
# json ストアは store_write（jsonl append 専用の write barrier）に basename を渡すと
# 単一 JSON オブジェクトへ jsonl 行を追記してファイルを破壊しうるため、store_write の
# runtime guard が reject する（#399 codex round1 Should 1・rl_common/store_write.py 参照）。
StoreKind = Literal["jsonl", "db", "json"]

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

# ストアの生死ステータス（write barrier・ADR-049 / #55）。
# - active : 通常稼働。store_write の書込許可対象。
# - legacy : 旧 dir に孤立し read-only。merge（#46）の読み元。write barrier は write を弾く。
# - dead   : 廃止予定。reader も無く削除待ち（#54）。write も read もしない。
# 全ストアは現状 active。legacy/dead は migration（#46/#54）で段階導入する。
StoreStatus = Literal["active", "legacy", "dead"]

# ストアの役割 4 分類（#379 Step 3「store 4分類と退役」）。status（write barrier の
# 生死）とは独立の軸で、「このストアは何のためにあるか」を機械可読に表す。
# - raw_event      : 一次イベントの SoR（hook/batch がユーザー操作やテレメトリを直接記録）。
# - workflow_state : 判断・処理の進捗を追跡する状態（既読集合・suppression・カーソル等）。
# - derived_cache   : raw_event からの派生・集計・ローカルキャッシュ（DuckDB SoR や
#                     成功パターン記憶等。再構築可能なことが多い）。
# - dead            : 廃止対象。reader 不在 or writer dormant/opt-in で削除候補
#                     （orphan_store・reader/writer 到達性の既存機構で削除確定へ）。
# classification="dead" は通常 status="dead" を伴うが、write barrier 経由の live な
# writer が既存テストで回帰検証されている等、status 降格が別途の製品判断を要する場合は
# 例外として先に classification のみ dead 化することがある（該当箇所は note に明記）。
StoreClassification = Literal["raw_event", "workflow_state", "derived_cache", "dead"]


@dataclass(frozen=True)
class StoreDeclaration:
    """1 ストアの契約宣言。

    name:         ストアの basename（例: "corrections.jsonl" / "utterances.db"）
    writer:       書き込み側の説明（どの hook/script が書くか — 人間可読 evidence）
    reader:       読み取り側の説明（誰が消費するか。reader 不在の場合は disposition で説明）
    retention:    保持ポリシー種別（permanent / ttl / compaction）
    kind:         物理形式（"jsonl" 既定 / "db" / "json"）。db は hook-writer 突合の対象外
                  （#430）。json は store_write の jsonl append を reject する対象（#399）
    writer_locus: 書き込み主体（"hook" 既定 / "batch"）。batch は hook-writer 突合に出ない
                  ため stale 突合の対象外（#432: weak_signals.jsonl は batch 書き込み jsonl）
    ttl_days:     retention="ttl" のときの失効日数（それ以外は None）
    compaction:   retention="compaction" のときの圧縮条件（散文。それ以外は None）
    disposition:  reader 不在（orphan）ストアの処遇。reader がある通常ストアは None
    status:       生死（active 既定 / legacy / dead）。write barrier の write 許可は active のみ
    classification: 役割 4 分類（raw_event / workflow_state / derived_cache / dead）。#379 Step 3
    writer_module: writer_locus="hook" だが、実際の write 呼び出しが hooks/*.py の直接テキストに
                   現れない（scripts/lib/<pkg>/ へ委譲された）場合の実体モジュール名
                   （scripts/lib 直下の basename、拡張子なし）。orphan_store の
                   find_store_writers は hooks/*.py 本体の単純走査のみ行う（汎用ライブラリの
                   芋づる式誤検出を避けるため import 追跡はしない・ADR-054 Phase 0・
                   #379 #400）ため、hook 本体分割で委譲された writer は本フィールドで明示宣言し、
                   detect_store_contract_drift が reachability（hook からこのモジュールへ
                   到達可能か）だけを確認して stale 誤検知を防ぐ。通常は None（hooks/*.py
                   本体に writer が直接現れる場合は不要）。
    note:         補足（任意）
    """

    name: str
    writer: str
    reader: str
    retention: RetentionKind
    classification: StoreClassification
    kind: StoreKind = "jsonl"
    writer_locus: WriterLocus = "hook"
    ttl_days: Optional[int] = None
    compaction: Optional[str] = None
    disposition: Optional[DispositionKind] = None
    status: StoreStatus = "active"
    writer_module: Optional[str] = None
    write_boundary: Optional[str] = None
    note: Optional[str] = None


# 宣言 SoT。新ストアを追加するときはここに 1 エントリ足す。
# 足さずに hook が新 jsonl を書くと orphan_store 検出が `undeclared` として surface する（#434）。
_DECLARATIONS: List[StoreDeclaration] = [
    StoreDeclaration(
        name="corrections.jsonl",
        writer="hooks/correction_detect.py（ユーザー修正の検出時）",
        reader="reflect / discover / optimize 等が消費（reader 多数）",
        retention="permanent",
        classification="raw_event",
        note="修正フィードバックの SoR。reflect の入力源。",
    ),
    StoreDeclaration(
        name="reflect_apply_events.jsonl",
        writer="skills/reflect/scripts/reflect.py --apply/--skip ハンドラ（柱2反映イベント）",
        reader="scripts/lib/reflect_fold.py・scripts/lib/pillar2_metrics.py",
        retention="permanent",
        classification="raw_event",
        writer_locus="batch",
        write_boundary="rl_common.correction_id.append_unique_record",
        note=(
            "#587 柱2測定用。#379 新設凍結の例外としてユーザー裁定（2026-09-01）で"
            "追加。corrections.jsonl とは別ファイル・別ロック。generic store_write"
            "からの直接書込みは write_boundary により拒否される（#587 巡2 [Must]2）。"
        ),
    ),
    StoreDeclaration(
        name="usage.jsonl",
        writer="hooks/observe.py（スキル/コマンド使用ごと）",
        reader="audit / discover / trigger が集計",
        retention="permanent",
        classification="raw_event",
        note="使用テレメトリの SoR。",
    ),
    StoreDeclaration(
        name="usage-registry.jsonl",
        writer="hooks/observe.py（既知スキル/コマンド名の登録）",
        reader="usage 集計時に既知名の母集団として参照",
        retention="permanent",
        classification="raw_event",
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
        classification="raw_event",
        disposition="drain",
        note="jsonl は hot path 緩衝。per-fire connect→INSERT→close による sessions.db "
        "再肥大（9.6GB）を根治するため jsonl-first + batch ingest に変更（#415）。",
    ),
    StoreDeclaration(
        name="errors.jsonl",
        writer="hooks（ツールエラー検出時）",
        reader="audit / discover がエラー傾向分析に使用",
        retention="permanent",
        classification="raw_event",
        note="エラーテレメトリ。",
    ),
    StoreDeclaration(
        name="false_positives.jsonl",
        writer="scripts/lib/rl_common/false_positive.py の add_false_positive"
        "（correction 偽陽性フィードバックの記録・on-demand）。hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="correction 検出時に load_false_positives が偽陽性フィルタとして参照"
        "（detection.detect_correction 経路）。",
        retention="ttl",
        ttl_days=180,
        classification="workflow_state",
        note="偽陽性フィードバックストア（#55 で registry 登録）。180 日超を cleanup_false_positives "
        "が削除。writer は hook でなく library 関数（reflect/report-feedback から呼ぶ）なので "
        "writer_locus=batch で hook-writer stale 突合から除外。",
    ),
    StoreDeclaration(
        name="workflows.jsonl",
        writer="hooks（ワークフロー系イベント記録）",
        reader="audit / discover が消費",
        retention="permanent",
        classification="raw_event",
        note="ワークフローテレメトリ。",
    ),
    StoreDeclaration(
        name="skill_activations.jsonl",
        writer="hooks（スキル発火の記録）",
        reader="audit / negative_transfer が消費",
        retention="permanent",
        classification="raw_event",
        note="スキル発火テレメトリ。",
    ),
    StoreDeclaration(
        name="subagents.jsonl",
        writer="hooks（サブエージェント生成の記録）",
        reader="audit / subagent 観測が消費。"
        "audit/sections_takeoff.py（worker_takeoff）が `last_assistant_message` を "
        "read-time で判定し完了報告↔内部完遂の乖離を advisory surface（#161）。",
        retention="permanent",
        classification="raw_event",
        note="サブエージェントテレメトリ。",
    ),
    StoreDeclaration(
        name="utterances.db",
        kind="db",
        writer="scripts/lib/utterance_archive/ingest.py（evolve/audit batch + evolve-fleet ingest）。"
        "hot path（hooks）からは書かない。",
        reader="utterance_archive.query（query_utterances / query_utterances_all_projects）。"
        "下流: #431 個人辞書・#432 暗黙シグナル・遡及分析。",
        retention="permanent",
        classification="raw_event",
        note="全PJ human 発話の恒久アーカイブ（#430）。物理 PK (source_path,line_no) + "
        "論理 UNIQUE (session_id,timestamp,text_hash)。writer は batch ingest のみ。",
    ),
    StoreDeclaration(
        name="weak_signals.jsonl",
        writer="scripts/lib/weak_signals/batch.py（evolve/audit batch から run_batch）＋ "
        "scripts/lib/fleet/detect.py（`evolve-fleet detect` / daily runner の全 PJ 検出・#304）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="reflect が確認後に corrections 本流へ昇格（promoted フラグ）。"
        "audit が channel 別件数を advisory surface（sections_weak_signals）。"
        "下流: #431 のバッチ LLM 判定もこのレーンを共有。",
        retention="ttl",
        ttl_days=45,
        classification="workflow_state",
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
        classification="workflow_state",
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
        classification="workflow_state",
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
        "marker 立ち後は is_bootstrap=False で即返す。#94: fleet.queue.weak_unprocessed_by_pj が"
        " bootstrap_done_at 経由で marker 時刻を読み、消化済み weak を material から除外する。",
        retention="permanent",
        classification="workflow_state",
        disposition="drain",
        note="初回バックログ bootstrap の完了 marker（#443）。bootstrap 完了 ISO8601 時刻 1行"
        "（#94。旧形式の空 marker は bootstrap_done_at が mtime fallback で後方互換）。PJ slug "
        "スコープ（bootstrap_done-<slug>.marker・全PJ共通 DATA_DIR 単一ファイル pitfall 回避）。"
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
        classification="workflow_state",
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
        classification="workflow_state",
        disposition="drain",
        note="remediation 個別承認で却下された提案の suppression ledger（#477）。べき等性原則"
        "（重複提案 MUST NOT）の実装。dedup_key（type+file+主要detail の sha256 先頭16hex）単位の"
        "append-only・load 時 last-write-wins collapse。triage_ledger（#308）を範に PJ slug スコープ"
        "（全PJ共通 DATA_DIR 単一ファイル pitfall 回避）・worktree 安全 slug・dry-run 非書込。"
        "TTL45日経過で 1 回だけ再 surface（環境変化での再評価機会）。",
    ),
    StoreDeclaration(
        name="optimize_history/<slug>.jsonl",
        writer="skills/genetic-prompt-optimizer/scripts/optimize.py の save_history_entry"
        "（accept/reject 履歴の追記・history_file を直接 open('a') — append_entry 非経由の"
        "3-writer 規約の1つ）/ skills/evolve-loop-orchestrator/scripts/run_loop.py:685"
        "（loop 結果の append_entry）/ skills/evolve-fitness/scripts/fitness_evolution.py:246"
        "（_append_history_entry_deduped_locked 経由の dedup 追記）/ "
        "scripts/lib/evolve_revert/_apply.py:129（revert 実行時の revert イベント追記）/ "
        "skills/reflect/scripts/reflect.py の record_rule_revert_entry"
        "（#475 §8.2: rule 文書への追記を revert 対象として記録・"
        "append_history_entry_deduped 経由）。hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="optimize_history_store.load_effective_history / results_board.classify_decision"
        "（戦果ボード・採用/却下の集計）/ evolve_revert（entry 検索・apply・listing）が"
        "revert 対象の SoR として参照。",
        retention="permanent",
        classification="raw_event",
        note="#475 §12 決定4: 未登録だった live store の宣言バックフィル（#379 Step 4 PR E と"
        "同型。既存の実ファイル・既存 writer/reader コードの追認であり新設ではない）。"
        "採用（accept/reject）イベントの一次記録 SoR。PJ ごとの `<repo-slug>.jsonl`"
        "（remediation_suppression/<slug>.jsonl と同じ「ディレクトリ + <slug>」形式）。"
        "prune 対象になっていない（#12 決定2 と同型の判断）ため retention=permanent。"
        "同時に shrink_freeze.FROZEN_STORES へも追加登録している"
        "（両側同時追加の理由は shrink_freeze.py 側のコメント参照）。",
    ),
    StoreDeclaration(
        name="reward_ema.jsonl",
        writer="scripts/lib/audit/reward_ema.py（evolve --drain の apply 境界 "
        "persist_reward_ema_batch）。hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="apply_outcome_ranking が read_reward_ema で prior EMA を読み advisory 列を "
        "付与（順位は変えない）。",
        retention="permanent",
        classification="derived_cache",
        note="MAA #64（arXiv:2606.20475）: 各スキルの符号付き advantage を evolve サイクル"
        "（バッチ）跨ぎで EMA 累積し『通時で安定して効くか』を判定する。RODS（#28・単一"
        "スナップショット reward 分散）と相補。plant-the-seed 型で 3-4 サイクルから意味を持つ。"
        "reader は latest-per-skill のみ参照・低書込レート（per-evolve 数件）なので permanent。",
    ),
    StoreDeclaration(
        name="advisory_decisions.jsonl",
        writer="scripts/lib/evolve_decisions/_ingest.py の ingest_decisions（`evolve --drain` の "
        "apply 境界）が advisory 提案の surfaced/accept/reject/deferred を記録。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="audit の Advisory Decisions section（sections_advisory_decisions）が "
        "read_advisory_decisions / summarize_by_detector で detector 別 "
        "surfaced/accept/reject/deferred を advisory surface。加えて fleet/queue_verify.py が "
        "read_advisory_decisions で accept の verify 待ち（exposure セッション数）を算出する。",
        retention="permanent",
        classification="workflow_state",
        note="#284（#267 Sprint 1）: advisory detector を emit→drain の decision lane に "
        "載せた際の判断記録先。optimize_history（fitness_func=skill_quality）と**分ける**のが "
        "設計の核 — advisory の対象は pytest.ini / rules / SKILL.md と異種で、skill_quality の "
        "母集団に混ぜると『混合でなく増量』の不変条件が壊れる（_extract_candidates が "
        "remediation を除外しているのと同じ理由）。冪等性は read 時 collapse で担保するが単位が "
        "2系統ある: accept/reject は排他的な最終状態として (pj_slug, proposal_id) 単位、"
        "surfaced/deferred は最終状態と独立な事実として (pj_slug, proposal_id, decision) 単位 "
        "（同一提案が surfaced かつ accept のように複数事実を同時に持てる。#267 Sprint 1: "
        "採用率の分母＝surfaced を分子＝accept と同じレーンに記録し freeze 解除条件の "
        "計測を可能にする）。**surfaced の実体は「提示された数」ではなく "
        "``ingest_decisions`` が drain（not dry_run）に到達した数**（dry-run のまま放置された "
        "提案は分母に入らず、無視され続ける detector ほど採用率が上振れする逆バイアスがある。"
        "#381 tacchi レビュー）。",
    ),
    StoreDeclaration(
        name="subagent_traces.jsonl",
        writer="scripts/lib/subagent_traces/ingest.py（evolve batch の apply 境界 "
        "ingest_all_projects）。hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="audit の per-agent 品質 section（sections_subagent_traces）が "
        "read_traces で読み、agent_type 別の tool-エラー0率（#342: 旧称「内部一発成功率」"
        "から表示ラベルを是正）を advisory surface。",
        retention="permanent",
        classification="derived_cache",
        note="#38 per-agent 品質帰属。subagents.jsonl の agent_transcript_path が指す "
        "transcript の tool_use/tool_result/is_error をパースし、subagent が内部で何回 error "
        "してからやり直したかを記録する。親セッションの error_count しか見ない既存 outcome "
        "帰属の盲点（内部 error 連発でも最終成功なら一発成功と誤記録）を塞ぐ。agent_id 単位 "
        "last-append-wins・pj_slug スコープ。writer は batch ingest のみ（hook-writer stale "
        "突合から writer_locus=batch で除外）。#200: 委任内容を事後監査可能にするため "
        "delegation_prompt（委任プロンプト先頭300字、300字超は truncate 後 \"…\" 付与）も "
        "併せて保存する。retention=permanent のため、subagent transcript 本体が掃除で消えた "
        "後も delegation_prompt の一部（先頭300字）は恒久的に平文で残り続ける＝transcript "
        "削除後もこの分だけ露出期間が延びる。300字の上限は恣意的な数値ではなく、この露出を "
        "抑えるための設計上の妥協（tacchi レビュー指摘・#200）。#342: エラー発生 tool の "
        "名前別内訳を tool_errors（{tool名: エラー回数}）として TRACE_VERSION 3 で追加。"
        "旧レコード（TRACE_VERSION 2 以前）は tool_errors を持たず、read 側は欠損を空 dict "
        "として graceful に扱う（後方互換）。",
    ),
    StoreDeclaration(
        name="verbosity_candidates.jsonl",
        writer="hooks/record_verbosity.py（Stop hook がゼロ LLM で足切り超の長応答を記録）。",
        reader="scripts/lib/verbosity/judge.py（Haiku バッチ判定）+ "
        "audit/sections_verbosity（冗長率 / パターン Top-N を advisory surface）。",
        retention="ttl",
        ttl_days=45,
        classification="raw_event",
        note="#75 回答冗長性の学習ループ。足切り 800 字超の最終 assistant 応答を pj_slug 付きで "
        "記録し、後段 Haiku バッチ（judge）が『無駄に冗長か』を判定する。standalone "
        "~/.claude/verbosity/candidates.jsonl の移植先。hook が書く（writer_locus=hook 既定）。"
        "TTL 45 日: 判定済みは verdicts に残るので古い未判定候補は失効させてよい。",
    ),
    StoreDeclaration(
        name="verbosity_verdicts.jsonl",
        writer="scripts/lib/verbosity/judge.py（--run の Haiku 判定後 write_verdict）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="scripts/lib/verbosity/judge.py（再判定除外の dedup）+ "
        "audit/sections_verbosity（冗長率集計）。verbose=True は weak_signals "
        "（channel=verbosity）にも emit され reflect 昇格フローに乗る。",
        retention="permanent",
        classification="derived_cache",
        note="#75 回答冗長性の判定結果（hash 単位 last-append-wins・pj_slug スコープ）。"
        "judge が batch で書く（writer_locus=batch で hook-writer stale 突合から除外）。",
    ),
    StoreDeclaration(
        name="memory_transition_checks.jsonl",
        writer="scripts/lib/auto_memory_broker.ingest_memory_results（#93 記憶遷移検証 = "
        "TRUSTMEM Memory Transition Verifier の決定論移植。同名 frontmatter の既存エントリ"
        "との coverage/preservation/fidelity 比較を実施した都度1行）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="audit/sections_memory.build_memory_capability_section（maintain 軸の "
        "evidence に reject 件数 / 検査件数を surface）。",
        retention="permanent",
        classification="workflow_state",
        note="#93: memory_guard.inspect_transition が同名（frontmatter name 一致）の既存 "
        "エントリを検出した場合のみ1件記録する（未マッチ=検証対象外の書込は記録しない）。"
        "coverage（重要行の大量欠落）/ preservation（metadata.type の矛盾上書き）/ "
        "fidelity（冒頭行の極性反転疑い）を決定論・文字列比較ベースで判定し、"
        "rejected=True/False と axes を記録する。誤 reject を避ける保守的較正"
        "（同名一致が前提・difflib 類似度しきい値・description/importance は対象外）。",
    ),
    StoreDeclaration(
        name="remediation_surfaced/<slug>.json",
        kind="json",
        writer="scripts/lib/remediation/suppression_ledger.reconcile_surfaced"
        "（evolve の remediation phase が個別承認候補確定後に毎 run 1 回呼ぶ）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="reconcile_surfaced 自身が次回 evolve で前回の連続提示回数を参照（自己消費）。"
        "閾値到達で remediation_suppression へ自動却下を昇格させる。",
        retention="permanent",
        classification="workflow_state",
        disposition="drain",
        note="record_rejection の決定論 fallback の surfaced マーカー（#494）。SKILL.md Step 5.5 の"
        "inline record_rejection を取りこぼしても、解決されないまま連続 surface された提案を"
        "閾値回数（既定2）で自動却下する安全網。per-slug 単一 JSON（dedup_key→{count, first_seen,"
        " last_seen}）で上書き。提案が検出されなくなれば marker から落ちる（解決＝却下しない）。"
        "PJ slug スコープ（全PJ共通 DATA_DIR 単一ファイル pitfall 回避）・dry-run 非書込。"
        "肥大化しない（毎 run 上書き・未解決提案のみ保持）。",
    ),
    StoreDeclaration(
        name="icebox_verdict_seen.jsonl",
        writer="hooks/restore_state.py → scripts/lib/session_notify/collectors.py の "
        "_build_icebox_output（SessionStart が icebox レーン1「成立」通知を名指しで表示した"
        "直後に icebox_verdict_seen.record_seen を呼ぶ。ADR-054 Phase 0 の 800行分割で "
        "_deliver_icebox_notice の実体が session_notify パッケージへ移設された）。"
        "hot path（hook）書き込みだが低頻度（当PJで作業しているセッションの新規成立時のみ）。",
        reader="icebox_verdict_seen.read_seen_keys / filter_unseen が次回表示判定に参照"
        "（自己消費）。lane または closed_at（再凍結）が変わるまで再提示しない"
        "（value/reason は fingerprint に含めない・#352 B5: 単調増加する value を含めると"
        "毎日再通知される事故になるため）。",
        retention="permanent",
        classification="workflow_state",
        writer_module="icebox_verdict_seen",
        note="#352 icebox 3レーン棚卸しの既読集合（issue番号+lane/closed_atハッシュの物理キー）。"
        "correction_review_seen.jsonl と同型。低書込レート・肥大化しない。"
        "ADR-054 Phase 0（#379 #400）追記: icebox_verdict_seen.py は "
        "session_notify/collectors.py（hook 委譲先）と daily/icebox_notice.py（batch 側の "
        "read-only 消費）の2箇所から import されるため find_store_writers の単純走査では "
        "拾えない。writer_module で reachability 救済する"
        "（詳細は StoreDeclaration.writer_module docstring）。",
    ),
    StoreDeclaration(
        name="evolve-queue-state.jsonl",
        writer="scripts/lib/fleet/queue_state.persist_last_evolve（evolve --drain の "
        "apply 境界が完了 PJ の last_evolve_at を 1 レコード追記）。hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="scripts/lib/fleet/queue.build_queue_result が read_last_evolve で per-PJ "
        "last_evolve_at を読み『前回 evolve 以降』の学習素材を測る（fleet queue・#79）。",
        retention="permanent",
        classification="workflow_state",
        note="#79 per-PJ last_evolve state。既存 evolve-state.json はグローバルで PJ 別に"
        "測れないため新設。append-only jsonl（{pj_slug, last_evolve_at, ts}）+ "
        "read 側 last-append-wins fold。reader は最新の last_evolve_at のみ参照・低書込"
        "レート（per-evolve 1 件）なので permanent。writer は batch（apply 境界）のみ"
        "（hook-writer stale 突合から writer_locus=batch で除外）。",
    ),
    # ------------------------------------------------------------------------
    # #121: 未登録 legacy ストア群のバックフィル宣言。
    #
    # 以下は store_registry 導入前（#434 以前）からある旧ストアで、writer が hooks でなく
    # batch script / DuckDB ingest / 直接 open() のため store_write barrier を経由しない。
    # active（write barrier の許可対象）ではないので status=legacy で宣言し、registry を
    # 全ストアの SoT に近づける。
    # writer/reader の live 判定は grep で実確認（各エントリの writer/reader/note に根拠を明記）。
    # 非 active なので active_store_names()（write-path-set snapshot）には現れず、
    # stale_exempt（status-aware）で contract-drift の stale にも載らない。
    # ------------------------------------------------------------------------
    StoreDeclaration(
        name="audit-history.jsonl",
        writer="scripts/lib/audit/orchestrator.py の _record_audit_completion→"
        "_append_audit_history（audit 完了時・非 dry-run）。batch writer。",
        writer_locus="batch",
        reader="同 orchestrator が劣化検出（check_environment_degradation 相当）で読む。",
        retention="compaction",
        compaction="_append_audit_history が直近 _MAX_AUDIT_HISTORY=100 件に pruning。",
        classification="workflow_state",
        status="legacy",
        note="#121: audit 完了履歴。store_registry 導入前からの旧ストアで writer は batch "
        "（hook 非経由）。active でないので write barrier の許可集合には含めない。"
        "#379 Step 3: 当面 legacy 維持（reader = orchestrator._check_degradation が "
        "audit 完了ごとに劣化検出で読む機能的 consumer。grep 確認済み）。",
    ),
    StoreDeclaration(
        name="belief_blocks.jsonl",
        writer="scripts/lib/auto_memory_broker.py の _record_belief_block（belief_entropy "
        "ゲートで block した要約を記録）。batch writer。",
        writer_locus="batch",
        reader="scripts/lib/belief_entropy.py（直近 days 日の block 集計）・"
        "scripts/lib/audit/sections.py。",
        retention="permanent",
        classification="workflow_state",
        status="legacy",
        note="#121: belief_entropy ゲートのブロック記録。append-only（prune/上限なし）。"
        "writer は batch（hook 非経由）。"
        "#379 Step 3: 当面 legacy 維持（reader = belief_entropy.summarize_blocks が "
        "直近 N 日の block を集計する機能的 consumer。grep 確認済み）。",
    ),
    StoreDeclaration(
        name="discover-suppression.jsonl",
        writer="scripts/lib/discover/suppression.py の記録関数群（merge/pattern/artifact の "
        "見送りを discover flow から記録）。batch writer。",
        writer_locus="batch",
        reader="同 suppression.py の is_*_suppressed / filter 群（TTL 窓内は畳む）。",
        retention="ttl",
        ttl_days=45,
        classification="workflow_state",
        status="legacy",
        note="#121: discover 提案の見送りレジャー。ARTIFACT_SUPPRESSION_TTL_DAYS=45 の"
        "read 時窓（物理 prune はせず weak_signals と同型の read-time 失効）。writer は batch。"
        "#379 Step 3: 当面 legacy 維持（reader = suppression.is_artifact_suppressed 等の "
        "is_*_suppressed 群が discover flow から読む機能的 consumer。grep 確認済み）。",
    ),
    StoreDeclaration(
        name="episodic.db",
        kind="db",
        writer="scripts/lib/episodic_store.py の insert_event（reflect が approve 済み "
        "correction を promote_to_episodic 経由で挿入）。batch writer（DuckDB）。",
        reader="scripts/lib/episodic_store.query_relevant（audit/memory・memory_trace 帰属）。",
        retention="ttl",
        ttl_days=30,
        classification="derived_cache",
        status="legacy",
        note="#121: episodic 層（適用済み修正の DuckDB TTL 管理）。ttl_days 既定 30 で "
        "expires_at を設定し prune_expired が削除。db なので hook-writer 突合の母集団外。"
        "#379 Step 3: 当面 legacy 維持（reader = episodic_retriever / memory_trace / "
        "audit/memory.py が query_relevant 経由で読む機能的 consumer。grep 確認済み）。",
    ),
    StoreDeclaration(
        name="evolution_memory.jsonl",
        writer="scripts/lib/evolution_memory.py の save_winner（genetic-prompt-optimizer の "
        "optimize.py が成功パターンを追記）。batch writer。",
        writer_locus="batch",
        reader="evolution_memory の union read（canonical + legacy dir を cross-dir 合算・#45）。",
        retention="compaction",
        compaction="save_winner が _MAX_RECORDS=1000 件で古い順ローテーション。",
        classification="derived_cache",
        status="legacy",
        note="#121: 直接パッチ最適化の成功パターン記憶。writer は batch（optimize skill）。"
        "#379 Step 3: 当面 legacy 維持（reader = genetic-prompt-optimizer/optimize.py・"
        "pipeline_eval.py が union read で消費する機能的 consumer。grep 確認済み）。",
    ),
    StoreDeclaration(
        name="quality-baselines.jsonl",
        writer="scripts/quality_monitor.py の save_baselines / append_record（audit の "
        "quality 2 相オーケストレーションが呼ぶ）。batch writer。",
        writer_locus="batch",
        reader="scripts/lib/audit/quality.py の load_quality_baselines・quality_monitor 自身。",
        retention="compaction",
        compaction="append_record がスキルあたり MAX_RECORDS_PER_SKILL=100 件に上限適用。",
        classification="derived_cache",
        status="legacy",
        note="#121: スキル品質ベースライン。writer は batch（quality_monitor / audit）。"
        "#379 Step 3: 当面 legacy 維持（reader = audit/quality.py の "
        "load_quality_baselines が audit 品質フェーズから読む機能的 consumer。"
        "grep 確認済み）。",
    ),
    StoreDeclaration(
        name="sessions.db",
        kind="db",
        writer="scripts/lib/session_store.py の ingest（sessions.jsonl → sessions.db の "
        "batch 取り込み）。batch writer（DuckDB）。",
        reader="session_store の union read（audit / trigger / capture_rate / fleet 等が "
        "SoR として参照）。reader 多数。",
        retention="compaction",
        compaction="file_size vs rows×平均行長 の乖離 >10倍 で rebuild（free page 解放）。",
        classification="derived_cache",
        status="legacy",
        note="#121: セッションテレメトリの DuckDB SoR。active な sessions.jsonl（hot-path 緩衝）が "
        "ingest されてくる先。db なので hook-writer 突合の母集団外。"
        "#379 Step 3: 当面 legacy 維持（reader = session_store の union read を "
        "audit / trigger / capture_rate / fleet 等 11 ファイルが利用する live な "
        "DuckDB SoR。grep 確認済み）。",
    ),
    StoreDeclaration(
        name="token_usage.db",
        kind="db",
        writer="scripts/lib/token_usage_store.py の bulk INSERT（transcript 由来の "
        "token 消費を INSERT OR IGNORE で冪等取り込み）。batch writer（DuckDB）。",
        reader="token_usage_store の query 群（fleet tokens・fitness_history_store）。",
        retention="permanent",
        classification="derived_cache",
        status="legacy",
        note="#121: PJ 別 LLM トークン消費の DuckDB SoR。PK は transcript 各行 top-level uuid・"
        "prune なし（permanent）。db なので hook-writer 突合の母集団外。"
        "#379 Step 3: 当面 legacy 維持（reader = token_usage_store の query 群を "
        "fleet tokens / fitness_history_store 等 9 ファイルが利用する live な "
        "DuckDB SoR。grep 確認済み）。",
    ),
    # ── #379 Step 4 PR E: 未登録 live store の棚卸し宣言バックフィル ──────────
    # 以下7件は issue #379 本文が指示する「未登録 store 棚卸し」で発見した、既に live に
    # 書き込まれている store の宣言漏れ（#121 の legacy backfill と同型・新設ではない）。
    # shrink_freeze の新設凍結（#379 Step 1）は「新しい store を作る」変更を止める契約
    # だが、本件は既存の実ファイル・既存の writer/reader コードを registry へ追認するだけ
    # で、新しい書込経路・新しい機能を一切追加しない。shrink_freeze.FROZEN_STORES へも
    # 同時追加している（両側同時追加の理由は shrink_freeze.py 側のコメント参照）。
    StoreDeclaration(
        name="evolve-state.json",
        kind="json",
        writer="複数箇所が個別キーを書く単一 JSON 状態ファイル（グローバル・PJ 非スコープ）: "
        "scripts/lib/trigger_engine/*.py の _save_state（trigger_history。hooks 経由で "
        "file_change/session_corrections/self_evolution の各トリガー発火時に書込・"
        "hooks/*.py 自体には basename 文字列が現れないため hook-writer 静的走査では "
        "検出不能）/ skills/evolve/scripts/evolve/_state.py の save_evolve_state"
        "（calibration_history・tool_usage_snapshot・last_run_timestamp・evolve --drain "
        "apply 境界の batch 書込）/ scripts/lib/prune/skill_inspect.py の "
        "_save_skill_type_cache（skill_type_cache・prune 実行時の batch 書込）/ "
        "scripts/lib/audit/orchestrator.py の _record_audit_completion"
        "（last_audit_timestamp・audit 完了時の batch 書込）。全 writer とも直接 "
        "open()/write_text()（store_write barrier 非経由）。",
        writer_locus="batch",
        reader="trigger_engine/state.py の load_trigger_config・_is_in_cooldown"
        "（trigger_config・trigger_history）/ skills/evolve/scripts/evolve/_state.py の "
        "各関数（load_evolve_state・_load_last_run・_build_trigger_summary 等が "
        "calibration_history/last_run_timestamp/trigger_history を参照）/ "
        "scripts/lib/reorganize.py（reorganize_threshold）/ scripts/lib/prune/config.py"
        "（reorganize_merge_similarity_threshold 等の閾値群）/ "
        "scripts/lib/prune/skill_inspect.py（skill_type_cache）/ "
        "scripts/lib/pipeline_reflector/outcomes.py（self_evolution config）。",
        retention="permanent",
        classification="workflow_state",
        note="#379 Step 4 PR E: 未登録の live store 棚卸し（issue #379 本文）。トリガー "
        "cooldown・self-evolution 較正進捗・prune/reorganize 閾値キャッシュ・audit/evolve "
        "の最終実行時刻を保持するグローバル単一 JSON。dogfood/layer1.py の dry-run "
        "ambient write 監視対象にも含まれる既存の live 主要ストア。"
        "#398 PR #399 round1 是正: 当初 retention=compaction としていたが、実際に "
        "サイズ上限があるのは trigger_history（_MAX_HISTORY_ENTRIES=100・"
        "trigger_engine/state.py::_record_trigger）のみで、calibration_history"
        "（skills/evolve/scripts/evolve/_state.py:246）は無制限 append・ファイル全体の "
        "圧縮/ローテーション条件も無いため retention=permanent に是正した。"
        "calibration_history への上限導入は本 PR の対象外（別途フォローアップ候補）。",
    ),
    StoreDeclaration(
        name="remediation-outcomes.jsonl",
        writer="scripts/lib/remediation/verify.py の record_outcome（evolve SKILL.md の "
        "remediation phase・skills/evolve/references/remediation.md の inline 手順から "
        "承認済み修正の適用結果を記録。dry_run=True では書かない）。",
        writer_locus="batch",
        reader="scripts/lib/trigger_engine/self_evolution.py（false positive 蓄積判定・"
        "skill_evolve_candidate 種別の検出）/ scripts/lib/pipeline_reflector/outcomes.py"
        "（issue_type 別集計・confidence キャリブレーション分析）。",
        retention="permanent",
        classification="raw_event",
        note="#379 Step 4 PR E: 未登録の live store 棚卸し（issue #379 本文）。remediation "
        "修正結果（category/action/result/user_decision/rationale 等）の一次イベント記録。",
    ),
    StoreDeclaration(
        name="fleet-config.json",
        kind="json",
        writer="scripts/lib/fleet_config.py の save_config（fleet track/ignore コマンドが "
        "ユーザー承認時に呼ぶ。atomic write）。",
        writer_locus="batch",
        reader="scripts/lib/fleet_config.py の load_config 経由で fleet status / 各 "
        "bin/evolve-fleet サブコマンドが tracked_projects/ignored_projects を参照。",
        retention="permanent",
        classification="workflow_state",
        note="#379 Step 4 PR E: 未登録の live store 棚卸し（issue #379 本文）。fleet 監視対象 "
        "PJ のユーザー承認 track/ignore リスト。",
    ),
    StoreDeclaration(
        name="agent-brushup-state.json",
        kind="json",
        writer="scripts/lib/agent_quality_upstream.py::check_upstream(state_file=...)"
        "（skills/agent-brushup/SKILL.md Step 3 の diagnose が state_file に "
        "DATA_DIR/agent-brushup-state.json を明示指定して inline python 実行。"
        "state_file 省略時（既定 None）は書かない — 必須引数を渡した呼び出しのみが "
        "writer。hot path（hooks）からは書かない）。",
        writer_locus="batch",
        reader="check_upstream 自身が同じ state_file から前回コミットハッシュ "
        "（upstream_commit_hash）を読み比較する（自己消費・次回 diagnose 呼び出し時）。",
        retention="permanent",
        classification="derived_cache",
        note="#379 Step 4 PR E: 未登録の live store 棚卸し（issue #379 本文）。upstream agent "
        "定義の変更検知用ハッシュキャッシュ（再取得すれば再構築可能）。",
    ),
    StoreDeclaration(
        name="skill-evolve-denylist.json",
        kind="json",
        writer="scripts/lib/skill_evolve/denylist.py の _save_denylist（add_to_denylist / "
        "remove_from_denylist 経由。evolve のスキル改善提案でユーザーが「今後提案不要」を "
        "選んだ時に batch 書込）。",
        writer_locus="batch",
        reader="scripts/lib/skill_evolve/assessment.py の get_denied_skill_names が評価対象 "
        "スキルから除外するために参照。",
        retention="permanent",
        classification="workflow_state",
        note="#379 Step 4 PR E: 未登録の live store 棚卸し（issue #379 本文）。ユーザーが明示的に "
        "「以後提案不要」とした skill の deny リスト（ユーザー意思決定の記録でキャッシュではない）。",
    ),
    StoreDeclaration(
        name="pj_slug_cache.json",
        kind="json",
        writer="scripts/lib/pj_slug.py の write_pj_slug_cache（SessionStart hook が cwd→"
        "authoritative slug の対応を1回書く。hot path の pj_slug_fast 自体は書かない）。",
        writer_locus="batch",
        reader="scripts/lib/pj_slug.py の _lookup_pj_slug_cache（hot path hooks の "
        "pj_slug_fast が worktree マーカーで畳めなかった sibling-dir worktree の write 時 "
        "slug 解決に参照・#29/#593）。",
        retention="permanent",
        classification="derived_cache",
        note="#379 Step 4 PR E: 未登録の live store 棚卸し（issue #379 本文）。cwd→slug 解決の "
        "SessionStart キャッシュ（miss 時は従来の basename フォールバックへ後方互換・"
        "再構築可能）。",
    ),
    StoreDeclaration(
        name="skill-evolve-cache.json",
        kind="json",
        writer="scripts/lib/skill_evolve/classification.py の _save_cache（LLM スコアリング "
        "結果のキャッシュ。skill_evolve の判定処理が batch 書込）。",
        writer_locus="batch",
        reader="scripts/lib/skill_evolve/classification.py の _load_cache（同 package の "
        "llm_scoring が再判定を避けるために参照・自己消費）。",
        retention="permanent",
        classification="derived_cache",
        note="#379 Step 4 PR E: 未登録の live store 棚卸し（issue #379 本文）。skill_evolve の "
        "LLM スコアリングキャッシュ（再実行すれば再構築可能）。dogfood/layer1.py の "
        "dry-run ambient write 監視対象にも含まれる既存の live ストア。",
    ),
    # ── PR #399 round1（codex Must 2）: read専用派生物 4件の宣言追加 ──────────
    # 発端: 当初「read 専用派生物（SoR でない）ため store_registry には登録しない」と
    # 各所（daily/queue_notice.py・daily/icebox_notice.py・daily/__init__.py・
    # fleet/propose.py 等）に明記していたが、これは事前契約ゲート（#434）の SoT 契約
    # （writer/reader が実在すれば derived_cache として登録する）と矛盾していた。
    # 「SoR でない」は classification=derived_cache に分類する理由であって、
    # 登録しない理由にはならない（PR E round1 で barrier 非経由の直接 writer 7件を
    # 既に登録・追認しており、「barrier 管理対象に狭める」という解釈は自己矛盾する）。
    # 上記4ファイルの「登録しない」文言は本 PR で是正済み（grep 全 corpus 一掃・
    # 詳細は round2 実装完了報告の判断表参照）。
    StoreDeclaration(
        name="evolve-queue.json",
        kind="json",
        writer="scripts/lib/daily/plist.py が生成する launchd runner コマンド "
        "（bin/evolve-daily-run 経由で `fleet queue --json` の stdout に llm_judge サマリ "
        "+ proposals digest（#409・scripts/lib/daily/proposal_digest.py）を埋め込んで "
        "保存。毎朝1回・日次上書き）。",
        writer_locus="batch",
        reader="hooks/restore_state.py の _deliver_evolve_queue_notice（SessionStart "
        "systemMessage 通知）/ 同 _build_session_proposal_output（#409, #412・proposals digest の "
        "additionalContext 提示）/ scripts/lib/daily/queue_notice.py の read_queue（stale "
        "判定）/ scripts/lib/fleet/cli_propose.py（--live 未指定時の propose 入力）。",
        retention="permanent",
        classification="derived_cache",
        note="#379 Step 4 PR E round2（#399 codex Must 2）: `fleet queue --json` 出力の "
        "日次スナップショット（毎朝上書き・SoR は fleet queue の元データ）。#409 で "
        "proposals フィールド（改善案 digest）を追加。",
    ),
    StoreDeclaration(
        name="icebox-status.json",
        kind="json",
        writer="bin/evolve-daily-run（icebox 棚卸しステップ・`gh issue list --label icebox "
        "--state closed` の結果を atomic_write_text(icebox_path, ...) で直接保存。"
        "scripts/lib/daily/icebox_notice.py は集計/判定ロジック（build_icebox_notice 等）の "
        "提供のみで本ファイルを書かない。毎朝1回・日次上書き・fail-open 4種は既存ファイル "
        "非破壊）。",
        writer_locus="batch",
        reader="hooks/restore_state.py の _deliver_icebox_notice（SessionStart systemMessage "
        "通知・閾値超過時のみ）。",
        retention="permanent",
        classification="derived_cache",
        note="#379 Step 4 PR E round2（#399 codex Must 2）: icebox（凍結 issue）棚卸しの "
        "日次スナップショット（SoR は gh issue の closedAt）。",
    ),
    StoreDeclaration(
        name="icebox-verdicts.json",
        kind="json",
        writer="bin/evolve-daily-run（icebox reconcile ステップ・"
        "atomic_write_text(verdicts_path, ...) で icebox_reconcile.build_verdicts の "
        "結果を保存。毎朝1回・日次上書き）。",
        writer_locus="batch",
        reader="hooks/restore_state.py（SessionStart・成立レーンのみ名指し通知）/ "
        "scripts/lib/audit/sections_icebox_reconcile.py（audit advisory・観測器不在/"
        "失効候補の surface）。",
        retention="permanent",
        classification="derived_cache",
        note="#379 Step 4 PR E round2（#399 codex Must 2）: icebox 3レーン判定"
        "（成立/観測器不在/失効候補）の日次スナップショット（#352）。",
    ),
    StoreDeclaration(
        name="evolve-proposals-<date>.json",
        kind="json",
        writer="scripts/lib/fleet/propose.py の write_reports（scripts/lib/fleet/cli_propose.py "
        "の `bin/evolve-fleet propose` から呼ばれる。日付ごとに新規ファイル・同日再実行は "
        "上書き）。",
        writer_locus="batch",
        reader="scripts/lib/fleet/pr.py の find_latest_proposals_json（`pr-start` が最新日付の "
        "提案バッチから承認対象を解決）。",
        retention="permanent",
        classification="derived_cache",
        note="#379 Step 4 PR E round2（#399 codex Must 2）: fleet propose の日次提案バッチ "
        "レポート（SoR は run_evolve dry-run の生成結果）。`.md`（人間向け表示専用・"
        "機械可読フィールドを持たず reader 無し）は本宣言の対象外、`.json`（機械可読・"
        "fleet/pr.py が読む）のみ宣言する。同日再実行の上書きはあるが、日付をまたぐ "
        "ファイルの自動ローテーション/削除は実装されていない（無制限累積、将来の "
        "clean-up 候補）。",
    ),
]


def declarations_by_kind(kind: StoreKind) -> List[StoreDeclaration]:
    """指定 kind の宣言だけを返す（jsonl の hook-writer 突合などで使う）。"""
    return [d for d in _DECLARATIONS if d.kind == kind]


def declarations_by_classification(
    classification: StoreClassification,
) -> List[StoreDeclaration]:
    """指定 classification の宣言だけを返す（#379 Step 3・Step 4 の削除候補抽出等で使う）。"""
    return [d for d in _DECLARATIONS if d.classification == classification]


def stale_exempt_names() -> List[str]:
    """stale 突合（宣言あり / 実 hook writer なし）から除外すべきストア名（ソート済み）。

    hook-writer 突合（find_store_writers）に現れない writer を持つストアは、
    宣言があっても「実 writer 見当たらず」で stale 誤検知になる。除外対象:
    - kind="db"           : writer が batch ingest（utterances.db）
    - writer_locus="batch": writer が batch script（weak_signals.jsonl 等）
    - status != "active"  : legacy/dead は writer が batch/直接 or 退役済み（dead）で
                            hook writer 突合に出ないのが当然（#121・#55 status の意図）。
                            退役済みストアを「writer 消えた」と drift 扱いするのは冗長なので除外する。

    いずれも同じ理由（hook writer 突合に現れない）なので 1 関数で集約する（#432・#121）。
    """
    return sorted(
        {
            d.name
            for d in _DECLARATIONS
            if d.kind == "db" or d.writer_locus == "batch" or d.status != "active"
        }
    )


def declarations() -> List[StoreDeclaration]:
    """宣言の一覧（SoT のコピーでなく参照）。"""
    return _DECLARATIONS


def declared_store_names() -> List[str]:
    """宣言済みストアの basename 一覧（ソート済み）。"""
    return sorted(d.name for d in _DECLARATIONS)


def active_store_names(decls: Optional[List[StoreDeclaration]] = None) -> List[str]:
    """status=active のストア名のみソートして返す（write barrier の write 許可集合・#55）。

    legacy/dead は除外。write-path-set keyset snapshot の対象（ADR-049 安全網）。
    """
    items = decls if decls is not None else _DECLARATIONS
    return sorted(d.name for d in items if d.status == "active")


def is_active_store(name: str) -> bool:
    """name が active 登録ストアなら True（未登録 / legacy / dead は False）。

    store_write の runtime guard が write 可否判定に使う単一ソース（#55）。
    """
    decl = declaration_for(name)
    return decl is not None and decl.status == "active"


def is_dead_store(name: str) -> bool:
    """name が status=dead 登録ストアなら True（未登録 / active / legacy は False）。

    store_write barrier 非経由の直接 open() writer が、writer 関数の冒頭でこの判定を使って
    dead ストアへの書込を自ら止めるためのゲート（#379 Step 3 レビュー: barrier bypass 修正）。
    未登録 / lookup 不能時は False（fail-open で書込継続。ゲート起因で本体機能を壊さない）。
    """
    decl = declaration_for(name)
    return decl is not None and decl.status == "dead"


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
