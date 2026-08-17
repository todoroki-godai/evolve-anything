# #415 keyset 完全化 — 分類結果と検証記録（2026-08-17）

対象: `scripts/lib/claude_md_contract.py`（`REQUIRED_INVARIANTS`）。CLAUDE.md 本体は無編集。

## 手法

CLAUDE.md 全文（353行）から契約語彙（凍結/reject/dry-run/fail-open/人間承認/単一ソース/承認/
no-op/非書込/ブロック/消さない/再提示しない/auto-apply/auto-fix/降格/人間/強制/既定/のみ/不可/
禁止/拒否/保留/必須）を含む行を機械抽出し、1行ずつ削除して `check_claude_md_contracts` +
`check_must_stay_sections` にかけ、「黙って消える（＝どの REQUIRED_INVARIANTS にも
引っかからない）」行を洗い出した。表 123 行に限らず全文を対象にした。

- 変更前（27件時点）: 71行が語彙ヒット、うち **50行が黙って消える**
- 変更後（60件時点、本PRの末尾で再計測）: 71行ヒット、うち **17行が黙って消える**
  （すべて下表の (a) 誤検出 12件 + (b) 省略可 2グループ3行 と一致。機械的に突合済み）

## 分類結果: (a) 誤検出 14件 / (b) 省略可 3行（2グループ） / (c) 追加 33件

### (a) 誤検出（契約でない・invariant を追加しない）

| 行 | 抜粋 | 理由 |
|---|---|---|
| L52 | 「実データ dry-run 較正」 | GEPA ガードレールの技術的説明。「常に○○」の拘束ではない |
| L63 | 「廃止リダイレクトのみ」 | 移行済み機能の歴史的注記 |
| L77 | 「人間確認のループ統合」 | 一般アーキテクチャ記述。pillar4 の一般契約と重複するだけで固有の拘束を追加しない |
| L90 | 「accept/reject 履歴の正準ストア」 | reject はストアに入るデータの種類の名前であって動作契約ではない |
| L116 | 「emit→drain lane での accept/reject」 | 同上（データ種別の記述） |
| L119 | 「型 drift のみ」 | 検出器の対象範囲を述べているだけで write/遷移ゲートではない |
| L155 | 「`--dry-run` 対応」 | CLI がオプションを持つという事実の記述。既定値の保証ではない |
| L160 | 「user 発話のみ抽出」 | 入力ソースの種類を述べているだけ |
| L237/L240/L242/L247 | quickstart コード例内のコメント（`--dry-run` フラグ例・既定値の数値） | bash コマンド例・非критical な数値既定（`--threshold`/`--time` で上書き可能なことが同じ節に明記済み） |
| L263 | 「承認フロー付き」 | 上位の tier スキル説明。実体は L55/L187 で保護済み |
| L326 | 「SKILL.md コードブロック」 | 「ブロック」は「code block」の意味の部分文字列で、`commit をブロック` 等とは無関係（誤検出） |

### (b) 省略可（コード実測で降格経路なしを確認・invariant を追加しない）

| 行 | 根拠 file:line |
|---|---|
| L96 `raw_history_gate` の stale_allowlist fail | `scripts/lib/raw_history_gate.py`（grep で downgrade/env/WARN 該当0件）+ `scripts/lib/tests/test_raw_history_gate_production.py:1`（production tree を AST で全走査するテスト時契約。`shrink_freeze.assert_no_new_keys` と同型で CLAUDE.md 自身が omit 可能の正典例として挙げているものと同じクラス） |
| L259/L260（quickstart の evolve-tier sync 使用例コメント） | 既存 `tier_sync_explicit_approval`（CLAUDE.md:55）が同一事実を別の言い回しで既に hot に保持。本PRで追加した `evolve_tier_cli_sync_default`（L187 相当）も同事実を独立に保護しており、L259/L260 はその三重目の再述にすぎない |

### (c) 追加（33件・全て `REQUIRED_INVARIANTS` へ追加済み）

| invariant name | 対象行 | 一言 |
|---|---|---|
| revert_scope_freeze | L37 | optimize.py/evolve-loop 経路の revert 対象外は「凍結」であり将来解凍しうる |
| contract_flag_preservation_rule | L68 | 圧縮時に契約フラグ語彙を必ず残せという meta ルールそのもの |
| evolve_decisions_flat_result_path_scope | L91 | flat result_path は run 1件時のみ有効という data-shape 契約 |
| triage_ledger_dry_run_no_write | L101 | triage_ledger は dry-run で書き込まない |
| pitfall_enforcement_commit_block | L111 | danger 判定時に commit をブロックする |
| observability_contract_single_source | L113 | observability 行の単一ソース |
| outcome_attribution_dry_run_diff_surface | L129 | dry-run で before/after 順位差分を surface する |
| correction_semantic_human_source_only_promotion | L131 | フェーズ昇格は human-source のみが駆動（#99 学習の再発防止） |
| daily_review_success_only_marking | L134 | promote 成功後のみ既読追記・部分失敗は対象外 |
| growth_report_single_source | L138 | 閾値は growth_engine が単一ソース |
| correction_rate_full_coverage_only | L140 | カバレッジ100%確定週のみ表示（柱3(a)の測定整合性の要） |
| subagent_noise_single_source | L145 | noise_agent_type_kind が単一ソース |
| verbosity_no_auto_apply | L147 | verbosity 学習ループは auto-apply しない |
| cross_pj_priority_no_auto_approval | L148 | 提示のみ・自動承認しない |
| plugin_self_auto_apply_downgrade | L151 | plugin_self の auto-apply は人間承認必須に降格 |
| dogfood_gate_light_non_blocking | L153 | `--layer light` は pre-push で非ブロッキング自動実行 |
| weak_signals_drain_pending_marker_intentional | L157 | pending marker の dry-run 書込は意図された設計（#505→#513 再発防止・hot 必須と team-lead が明示指定） |
| reconcile_surfaced_drain_persist_false | L158 | phases の dry-run は persist=False で非書込 |
| idiom_filter_manual_reject_option | L159 | idiom 単位拒否も可能 |
| recall_validity_soft_downgrade | L167 | stale/superseded は降格のみ・ハード除外はしない |
| subagents_errors_bugfix_single_source | L170 | is_noise_agent_type が単一ソース |
| memory_capability_single_source | L171 | resolve_cc_memory_dir が単一ソース |
| skill_vuln_scan_combo_required | L172 | combo 必須で検出（単一シグナルでは誤検出増） |
| daily_runner_human_approval | L179 | 適用は対話で人間承認 |
| memory_hygiene_no_auto_apply | L183 | 重複残骸は手順提案のみで auto-apply しない |
| invalid_frontmatter_no_auto_fix | L185 | auto-fix せず人手修正提案 |
| evolve_tier_cli_sync_default | L187 | sync は既定 dry-run、--apply のみ書込 |
| evaluation_provenance_no_guessing | L188 | 不明値は推測せず None（factual-claims の実装） |
| fleet_propose_no_re_present_rejected | L190 | reject 済み提案は再提示しない |
| codex_usage_fail_open_no_merge | L195 | fail-open・CC 側 token_usage とは合算しない |
| evolve_revert_cli_default_dry_run | L250 | entry_id なし apply は既定 dry-run |
| testpaths_single_source | L319 | testpaths が単一ソース。新規 tests/ は追記が必要 |
| evolve_keyset_snapshot_union_merge | L323 | UPDATE_SNAPSHOTS は union merge・条件付きキーを golden から消さない |

`store_write_barrier_core` 等、既存27件のうち単一の汎用語1件だけで判定していたものは無し
（既存コメントに「pj_slug は3回出現するので識別トークンを追加した」等の補強済み注記があり、
現行27件はいずれも複数トークンの組で一意化済みだったため追加補強は不要と判断）。

## 陰性試験

### ① 行削除スイープ
- 変更前: 50行が黙って消える
- **変更後: 17行**（(a)誤検出14 + (b)省略可3 と完全一致。0 でない理由は上表の通り）
- 追加した33件それぞれについて「その行を実 CLAUDE.md から削除すると red になる」ことを
  `test_deleting_the_row_each_invariant_protects_flags_it_in_real_claude_md`（既存テスト、
  60件全件を自動で回す設計）で実測 → 全件 pass

### ② 語は残して意味を壊す
実際に3件、real CLAUDE.md に適用して確認（部分文字列は残したまま矛盾する注記を追記）:
  - `verbosity_no_auto_apply` 直後に「実際には auto-apply する」を追記 → **緑のまま（検出漏れ）**
  - `daily_runner_human_approval` 直後に「無人で即時適用に変更済み」を追記 → **緑のまま（検出漏れ）**
  - `evolve_tier_cli_sync_default` 直後に「既定 apply に変更中」を追記 → **緑のまま（検出漏れ）**
  - 対照: `correction_rate_full_coverage_only` の値そのものを書き換え（トークン破壊）→ 赤

3件とも検出できないことを確認し、`claude_md_contract.py` の docstring に
「既知の検出漏れクラス」として明記した（隠さない）。この検査の脅威モデル（うっかり削除のみ
を守る）の対象外であることも併記。

### ③ 分散・入替
- 行の移設（`noise_agent_type_kind` 行を Compaction Instructions 直前へ移設）→ 緑のまま
- 2行 swap（subagent_noise_single_source と memory_capability_single_source の行を交換）→ 緑のまま
どちらも部分文字列自体は文書のどこかに残るため事実は失われておらず、緑のままで正しい
（docstring にも既存の設計方針として追記）。

### ④ 検査を無効化する
1. `REQUIRED_INVARIANTS = ()` → golden テスト（`test_required_invariants_count_golden`）が
   `len(())=0 != 60` で red（自明・実測で golden 比較式を確認）
2. `_missing_tokens` を常に `[]` を返すよう monkeypatch → `test_removing_one_token_flags_only_that_invariant`
   等が期待した findings を得られず red（実測: neutered 状態で findings=[] になることを確認）
3. `dogfood/cli.py` の `_run_layer2` から `checks.append(claude_md_contract.layer2_check(repo_root))`
   を実際に削除して `pytest -k dogfood`（120件）を実行 → **全緑のまま（検出漏れ）**。
   既存テストにこの配線を守るものが1つも無かったため、新規テスト
   `test_dogfood_layer2_wires_claude_md_contract`（ソース静的検査）を追加して穴を塞いだ。
   追加後に同じ削除を再適用 → 正しく red になることを確認済み。コメントアウトのみ（部分文字列
   温存）だと検出できないことも実測で確認し、削除でテストしている。

### 自分で追加した回避手段2件以上（team-lead提示の①〜④とは別種）
- Unicode 全角ダッシュ置換（`auto-apply` のハイフンを全角 `－` に）→ 赤（ASCII トークンが
  消えるため正しく検出）
- ゼロ幅スペース（U+200B）をトークン中間に挿入 → 赤（部分文字列が字面上不一致になり検出）
- 全角空白5000個のパディングでトークンを分断 → 赤（同上）

いずれも「トークンの部分文字列が壊れる」変換は過剰検出側に倒れて正しく赤くなることを確認。
唯一の検出漏れクラスは②（部分文字列を残したまま意味だけ反転させる自然文の追記）のみ。

### 探索した入力クラス・変換
要素削除（行単位）/ 語は残し意味反転（矛盾注記の追記）/ 分散・入替（移設・swap）/
検査無効化（golden 空・関数無効化・CI配線削除）/ Unicode 全角文字置換 / ゼロ幅文字混入 /
巨大空白パディング / 表記ゆれ（行末空白・CRLF、陽性対照）。

## 陽性対照
- 無改変の real CLAUDE.md（`test_real_repo_claude_md_has_no_missing_contracts` 他） → 緑
- 全行末尾スペース付与 → 緑
- CRLF 化 → 緑
（既存の4件の陽性対照テストが60件化後も全て green のまま pass することを pytest で確認済み）
