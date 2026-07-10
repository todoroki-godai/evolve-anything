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

# ストアの生死ステータス（write barrier・ADR-049 / #55）。
# - active : 通常稼働。store_write の書込許可対象。
# - legacy : 旧 dir に孤立し read-only。merge（#46）の読み元。write barrier は write を弾く。
# - dead   : 廃止予定。reader も無く削除待ち（#54）。write も read もしない。
# 全ストアは現状 active。legacy/dead は migration（#46/#54）で段階導入する。
StoreStatus = Literal["active", "legacy", "dead"]


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
    status:       生死（active 既定 / legacy / dead）。write barrier の write 許可は active のみ
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
    status: StoreStatus = "active"
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
        name="false_positives.jsonl",
        writer="scripts/lib/rl_common/false_positive.py の add_false_positive"
        "（correction 偽陽性フィードバックの記録・on-demand）。hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="correction 検出時に load_false_positives が偽陽性フィルタとして参照"
        "（detection.detect_correction 経路）。",
        retention="ttl",
        ttl_days=180,
        note="偽陽性フィードバックストア（#55 で registry 登録）。180 日超を cleanup_false_positives "
        "が削除。writer は hook でなく library 関数（reflect/report-feedback から呼ぶ）なので "
        "writer_locus=batch で hook-writer stale 突合から除外。",
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
        writer="scripts/lib/utterance_archive/ingest.py（evolve/audit batch + evolve-fleet ingest）。"
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
        "marker 立ち後は is_bootstrap=False で即返す。#94: fleet.queue.weak_unprocessed_by_pj が"
        " bootstrap_done_at 経由で marker 時刻を読み、消化済み weak を material から除外する。",
        retention="permanent",
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
        name="reward_ema.jsonl",
        writer="scripts/lib/audit/reward_ema.py（evolve --drain の apply 境界 "
        "persist_reward_ema_batch）。hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="apply_outcome_ranking が read_reward_ema で prior EMA を読み advisory 列を "
        "付与（順位は変えない）。",
        retention="permanent",
        note="MAA #64（arXiv:2606.20475）: 各スキルの符号付き advantage を evolve サイクル"
        "（バッチ）跨ぎで EMA 累積し『通時で安定して効くか』を判定する。RODS（#28・単一"
        "スナップショット reward 分散）と相補。plant-the-seed 型で 3-4 サイクルから意味を持つ。"
        "reader は latest-per-skill のみ参照・低書込レート（per-evolve 数件）なので permanent。",
    ),
    StoreDeclaration(
        name="subagent_traces.jsonl",
        writer="scripts/lib/subagent_traces/ingest.py（evolve batch の apply 境界 "
        "ingest_all_projects）。hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="audit の per-agent 品質 section（sections_subagent_traces）が "
        "read_traces で読み、agent_type 別の内部一発成功率を advisory surface。",
        retention="permanent",
        note="#38 per-agent 品質帰属。subagents.jsonl の agent_transcript_path が指す "
        "transcript の tool_use/tool_result/is_error をパースし、subagent が内部で何回 error "
        "してからやり直したかを記録する。親セッションの error_count しか見ない既存 outcome "
        "帰属の盲点（内部 error 連発でも最終成功なら一発成功と誤記録）を塞ぐ。agent_id 単位 "
        "last-append-wins・pj_slug スコープ。writer は batch ingest のみ（hook-writer stale "
        "突合から writer_locus=batch で除外）。",
    ),
    StoreDeclaration(
        name="judge_audit_verdicts.jsonl",
        writer="scripts/lib/judge_audit/harness.py（--run の欠陥注入判定後 write_verdict）。"
        "hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="scripts/lib/judge_audit/query.py（false-pass 率集計）+ "
        "audit/sections_judge_audit（advisory surface）。",
        retention="permanent",
        note="#188 The Blind Curator（arXiv 2607.07436）: 既知の欠陥 fixture を judge の実"
        "プロンプト（constitutional._build_eval_prompt/_parse_layer_response 再利用）に流し、"
        "合格(false-pass)判定率を計測する欠陥注入監査。fixture id 単位 last-append-wins・"
        "pj_slug スコープ。judge が false-pass を出すとスキル退役（negative_transfer "
        "rollback）が無音で無効化されるため事前計測する。書込は harness の batch 実行のみ"
        "（hook-writer stale 突合から writer_locus=batch で除外）。",
    ),
    StoreDeclaration(
        name="verbosity_candidates.jsonl",
        writer="hooks/record_verbosity.py（Stop hook がゼロ LLM で足切り超の長応答を記録）。",
        reader="scripts/lib/verbosity/judge.py（Haiku バッチ判定）+ "
        "audit/sections_verbosity（冗長率 / パターン Top-N を advisory surface）。",
        retention="ttl",
        ttl_days=45,
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
        note="#75 回答冗長性の判定結果（hash 単位 last-append-wins・pj_slug スコープ）。"
        "judge が batch で書く（writer_locus=batch で hook-writer stale 突合から除外）。",
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
    StoreDeclaration(
        name="evolve-queue-state.jsonl",
        writer="scripts/lib/fleet/queue_state.persist_last_evolve（evolve --drain の "
        "apply 境界が完了 PJ の last_evolve_at を 1 レコード追記）。hot path（hooks）からは書かない。",
        writer_locus="batch",
        reader="scripts/lib/fleet/queue.build_queue_result が read_last_evolve で per-PJ "
        "last_evolve_at を読み『前回 evolve 以降』の学習素材を測る（fleet queue・#79）。",
        retention="permanent",
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
    # active（write barrier の許可対象）ではないので status=legacy（実体がもう書かれない
    # deferred_tasks.jsonl のみ dead）で宣言し、registry を全ストアの SoT に近づける。
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
        status="legacy",
        note="#121: audit 完了履歴。store_registry 導入前からの旧ストアで writer は batch "
        "（hook 非経由）。active でないので write barrier の許可集合には含めない。",
    ),
    StoreDeclaration(
        name="belief_blocks.jsonl",
        writer="scripts/lib/auto_memory_broker.py の _record_belief_block（belief_entropy "
        "ゲートで block した要約を記録）。batch writer。",
        writer_locus="batch",
        reader="scripts/lib/belief_entropy.py（直近 days 日の block 集計）・"
        "scripts/lib/audit/sections.py。",
        retention="permanent",
        status="legacy",
        note="#121: belief_entropy ゲートのブロック記録。append-only（prune/上限なし）。"
        "writer は batch（hook 非経由）。",
    ),
    StoreDeclaration(
        name="deferred_tasks.jsonl",
        writer="hooks/detect-deferred-task.py の log_deferral（Stop hook）。ただし当該 hook は "
        "hooks.json に未登録＝発火せず、実体はもう書かれない。",
        writer_locus="hook",
        reader="なし（jsonl データを読む consumer は不在。discover/artifacts.py は hook "
        "スクリプトのパスを推奨 artifact として参照するのみでデータは読まない）。",
        retention="permanent",
        disposition="remove",
        status="dead",
        note="#121: hook detect-deferred-task.py は hooks.json 未登録のため発火せず、この "
        "ストアはもう書かれない（=dead）。discover が導入を推奨できる artifact なので、"
        "ユーザーが hook を登録したら writer が live 化する。その際は status を legacy/active に見直す。",
    ),
    StoreDeclaration(
        name="discover-suppression.jsonl",
        writer="scripts/lib/discover/suppression.py の記録関数群（merge/pattern/artifact の "
        "見送りを discover flow から記録）。batch writer。",
        writer_locus="batch",
        reader="同 suppression.py の is_*_suppressed / filter 群（TTL 窓内は畳む）。",
        retention="ttl",
        ttl_days=45,
        status="legacy",
        note="#121: discover 提案の見送りレジャー。ARTIFACT_SUPPRESSION_TTL_DAYS=45 の"
        "read 時窓（物理 prune はせず weak_signals と同型の read-time 失効）。writer は batch。",
    ),
    StoreDeclaration(
        name="episodic.db",
        kind="db",
        writer="scripts/lib/episodic_store.py の insert_event（reflect が approve 済み "
        "correction を promote_to_episodic 経由で挿入）。batch writer（DuckDB）。",
        reader="scripts/lib/episodic_store.query_relevant（audit/memory・memory_trace 帰属）。",
        retention="ttl",
        ttl_days=30,
        status="legacy",
        note="#121: episodic 層（適用済み修正の DuckDB TTL 管理）。ttl_days 既定 30 で "
        "expires_at を設定し prune_expired が削除。db なので hook-writer 突合の母集団外。",
    ),
    StoreDeclaration(
        name="evolution_memory.jsonl",
        writer="scripts/lib/evolution_memory.py の save_winner（genetic-prompt-optimizer の "
        "optimize.py が成功パターンを追記）。batch writer。",
        writer_locus="batch",
        reader="evolution_memory の union read（canonical + legacy dir を cross-dir 合算・#45）。",
        retention="compaction",
        compaction="save_winner が _MAX_RECORDS=1000 件で古い順ローテーション。",
        status="legacy",
        note="#121: 直接パッチ最適化の成功パターン記憶。writer は batch（optimize skill）。",
    ),
    StoreDeclaration(
        name="growth-journal.jsonl",
        writer="scripts/lib/growth_journal.py の emit_crystallization（現状は "
        "backfill_from_git_log 経由のみで dormant・production の常時 writer なし）。batch writer。",
        writer_locus="batch",
        reader="query_crystallizations / count_crystallized_rules（growth_narrative・"
        "audit/orchestrator・sections_milestone が成長ストーリー素材に消費）。reader は live。",
        retention="permanent",
        status="legacy",
        note="#121: 結晶化イベント記録。reader は live だが writer は backfill 経由で dormant。"
        "append-only（_patch_last_event_ts で末尾行更新はするが rotation/上限なし）。",
    ),
    StoreDeclaration(
        name="quality-baselines.jsonl",
        writer="scripts/quality_monitor.py の save_baselines / append_record（audit の "
        "quality 2 相オーケストレーションが呼ぶ）。batch writer。",
        writer_locus="batch",
        reader="scripts/lib/audit/quality.py の load_quality_baselines・quality_monitor 自身。",
        retention="compaction",
        compaction="append_record がスキルあたり MAX_RECORDS_PER_SKILL=100 件に上限適用。",
        status="legacy",
        note="#121: スキル品質ベースライン。writer は batch（quality_monitor / audit）。",
    ),
    StoreDeclaration(
        name="quality-scores.jsonl",
        writer="scripts/lib/quality_engine.py の record_quality_score（evolve の "
        "phases_diagnose がスキル採点を追記）。batch writer。",
        writer_locus="batch",
        reader="現状 consumer 未検出（スコアボード用途の writer-only 傾向）。",
        retention="permanent",
        status="legacy",
        note="#121: スキル品質スコアのスコアボード。writer live（evolve 採点）だが専用 reader は "
        "未検出。append-only（rotation/上限なし）。writer は batch。",
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
        status="legacy",
        note="#121: セッションテレメトリの DuckDB SoR。active な sessions.jsonl（hot-path 緩衝）が "
        "ingest されてくる先。db なので hook-writer 突合の母集団外。",
    ),
    StoreDeclaration(
        name="token_usage.db",
        kind="db",
        writer="scripts/lib/token_usage_store.py の bulk INSERT（transcript 由来の "
        "token 消費を INSERT OR IGNORE で冪等取り込み）。batch writer（DuckDB）。",
        reader="token_usage_store の query 群（fleet tokens・fitness_history_store）。",
        retention="permanent",
        status="legacy",
        note="#121: PJ 別 LLM トークン消費の DuckDB SoR。PK は transcript 各行 top-level uuid・"
        "prune なし（permanent）。db なので hook-writer 突合の母集団外。",
    ),
]


def declarations_by_kind(kind: StoreKind) -> List[StoreDeclaration]:
    """指定 kind の宣言だけを返す（jsonl の hook-writer 突合などで使う）。"""
    return [d for d in _DECLARATIONS if d.kind == kind]


def stale_exempt_names() -> List[str]:
    """stale 突合（宣言あり / 実 hook writer なし）から除外すべきストア名（ソート済み）。

    hook-writer 突合（find_store_writers）に現れない writer を持つストアは、
    宣言があっても「実 writer 見当たらず」で stale 誤検知になる。除外対象:
    - kind="db"           : writer が batch ingest（utterances.db）
    - writer_locus="batch": writer が batch script（weak_signals.jsonl 等）
    - status != "active"  : legacy/dead は writer が batch/直接 or 退役済み（dead）で
                            hook writer 突合に出ないのが当然（#121・#55 status の意図）。
                            特に dead な hook writer（deferred_tasks.jsonl は hook 未登録で
                            発火せず）を「writer 消えた」と drift 扱いするのは冗長なので除外する。

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
