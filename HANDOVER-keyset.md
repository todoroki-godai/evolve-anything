# #415 keyset 完全化 — 分類結果と検証記録（2026-08-17）

対象: `scripts/lib/claude_md_contract.py`（`REQUIRED_INVARIANTS`）。CLAUDE.md 本体は無編集。
27件 → 60件（行単位スイープ）→ 75件（句単位スイープ第1巡・codex cold review 反映）→
**76件**（句単位スイープ第2巡・team-lead 指摘: 汎用句重複8件を句固有トークンで是正）。

## 手法（2段階）

### 段階1: 行単位スイープ
CLAUDE.md 全文（353行）から契約語彙（凍結/reject/dry-run/fail-open/人間承認/単一ソース/承認/
no-op/非書込/ブロック/消さない/再提示しない/auto-apply/auto-fix/降格/人間/強制/既定/のみ/不可/
禁止/拒否/保留/必須）を含む行を機械抽出し、1行ずつ削除して `check_claude_md_contracts` +
`check_must_stay_sections` にかけ、「黙って消える」行を洗い出した（表 123 行に限らず全文）。
変更前 50行 → 変更後 17行（(a)誤検出14 + (b)省略可3行）。33件を追加（27→60件）。

### 段階2: 句単位スイープ（codex cold review [Must] 反映）
行単位スイープには盲点がある: **1行に契約句が2つあり片方だけ登録済みの場合、行削除では
赤くなるため「守られている」と誤判定されるが、未登録の第2契約句は無防備なまま残る**
（`reconcile_surfaced drain 永続化` の行で実際に発覚）。そこで行を `。` で句に分割し、
**句だけを行から削除**（行自体は残す）して検査にかける句単位スイープを実施した。
89句中 42句が黙って消える → 15件を追加（60→75件）→ 再計測で 27句へ縮小（内訳は下記）。

## 分類結果

### (a) 誤検出（契約でない・invariant を追加しない） — 17件

| 行 | 抜粋 | 理由 |
|---|---|---|
| L52 | 「実データ dry-run 較正」 | GEPA ガードレールの技術的説明。拘束ではない |
| L63 | 「廃止リダイレクトのみ」 | 移行済み機能の歴史的注記 |
| L77 | 「人間確認のループ統合」 | 一般アーキテクチャ記述。pillar4 の一般契約と重複するだけ |
| L90 | 「accept/reject 履歴の正準ストア」 | reject はストアに入るデータの種類の名前 |
| L116 | 「emit→drain lane での accept/reject」 | 同上（データ種別の記述） |
| L119 | 「型 drift のみ」 | 検出器の対象範囲の記述 |
| L155 | 「`--dry-run` 対応」 | CLI がオプションを持つという事実の記述 |
| L160 | 「user 発話のみ抽出」 | 入力ソースの種類の記述 |
| L190 | 「queue 待ち PJ に evolve --dry-run を順次実行し提案を集約レポート化」 | 通常動作の説明。承認ゲート等の拘束は別句で捕捉済み |
| L191 | 「承認済み evolve 提案を repo 外 worktree で commit→push→PR 化」 | 入力前提の記述。マージ人間ゲートは別句/別行で捕捉済み |
| L237/L240/L242/L247 | quickstart コード例内のコメント | bash コマンド例・上書き可能な既定値 |
| L263 | 「承認フロー付き」 | 上位スキル説明。実体は L55/evolve_tier_cli_sync_default で保護済み |
| L323 | 「dry-run するため、計測窓 suppress の暦日境界等で...」 | 背景説明（なぜ flaky か）。対処法（二層golden方式）は別句で捕捉済み |
| L326 | 「SKILL.md コードブロック」 | 「ブロック」は「code block」の部分文字列で無関係（誤検出） |

### (b) 省略可・意図的に無対応 — 3件（句単位スイープ第2巡で8件→2件+既存三重化1件に縮小）

| 分類 | 行/句 | 理由 |
|---|---|---|
| 既存invariantと三重化 | L259/L260 quickstart の evolve-tier sync 使用例 | `tier_sync_explicit_approval`（L55）+ 新規 `evolve_tier_cli_sync_default`（L187）が既に同一事実を保護 |

**訂正（句単位スイープ第2巡・team-lead 指摘）**: 当初「共起判定を再導入しない限り解消不能」と
判定した8件（L92/L96/L135/L152/L153/L156/L180/L250）は**誤り**だった。必要なのは共起判定
ではなく、**その句にしか出現しない部分文字列を `all_of` に追加するだけ**（#492 の
`hook_fail_open`+`icebox_notice` と同じ手法）。8件すべてに句固有トークンを追加して是正した
（1件は新規 invariant `raw_history_gate_single_source` として追加、7件は既存 invariant を
widen）。是正後、句単位スイープでこれら8句が実際に赤くなることを実測確認した（詳細は下記
「句単位スイープ・第2巡」節）。この訂正により (b) は「既存invariantと真に重複しているだけ」
の1件（2行）に縮小した。

### (c) 追加 — 49件（33件・行単位 + 15件・句単位第1巡 + 1件・句単位第2巡新規）
（+ 既存7件を widen・下記「句単位スイープ・第2巡」参照）

**行単位スイープで追加した33件**（対象行）:
revert_scope_freeze(L37) / contract_flag_preservation_rule(L68) /
evolve_decisions_flat_result_path_scope(L91) / triage_ledger_dry_run_no_write(L101) /
pitfall_enforcement_commit_block(L111) / observability_contract_single_source(L113) /
outcome_attribution_dry_run_diff_surface(L129) / correction_semantic_human_source_only_promotion(L131) /
daily_review_success_only_marking(L134) / growth_report_single_source(L138) /
correction_rate_full_coverage_only(L140) / subagent_noise_single_source(L145) /
verbosity_no_auto_apply(L147) / cross_pj_priority_no_auto_approval(L148) /
plugin_self_auto_apply_downgrade(L151) / dogfood_gate_light_non_blocking(L153) /
weak_signals_drain_pending_marker_intentional(L157・#505→#513再発防止) /
reconcile_surfaced_drain_persist_false(L158) / idiom_filter_manual_reject_option(L159) /
recall_validity_soft_downgrade(L167) / subagents_errors_bugfix_single_source(L170) /
memory_capability_single_source(L171) / skill_vuln_scan_combo_required(L172) /
daily_runner_human_approval(L179) / memory_hygiene_no_auto_apply(L183) /
invalid_frontmatter_no_auto_fix(L185) / evolve_tier_cli_sync_default(L187) /
evaluation_provenance_no_guessing(L188) / fleet_propose_no_re_present_rejected(L190) /
codex_usage_fail_open_no_merge(L195) / evolve_revert_cli_default_dry_run(L250) /
testpaths_single_source(L319) / evolve_keyset_snapshot_union_merge(L323)

**句単位スイープで追加した15件**（team-lead 指摘の盲点是正）:
shrink_freeze_single_source_module(L42) / shrink_freeze_contract_test_blocking(L42) /
shrink_freeze_runtime_write_reject(L42) / scaffold_advisory_write_frozen_reject(L42) /
observability_cull_code_not_deleted(L44) / observability_cull_single_source(L44) /
fleet_propose_batch_approval_gate(L53) / fleet_pr_start_finish_human_merge_always(L53) /
components_table_one_line_summary_rule(L68) / contract_flag_omission_example(L69) /
review_channels_content_rich_scope(L135) /
**reconcile_surfaced_apply_boundary_migration(L158) — team-lead 指摘の具体例そのもの**
（「apply 境界へ移設」句が persist=False 句とは独立に無防備だった） /
evaluation_provenance_envelope_single_source(L188) /
evolve_keyset_snapshot_declared_prefix_only(L323) /
pitfall_enforcement_is_opt_in(L111 — pitfall_enforcement_commit_block と同じ行のもう1つの独立契約句)

### 句単位スイープ・第2巡（team-lead 指摘の8件を句固有トークンで是正）

新規 invariant 1件 + 既存 invariant 7件の widen（widen は「追加」でなく既存 all_of への
トークン追加なので (c) の件数には数えない。詳細は各 invariant のコード上コメント参照）:

| invariant | 追加した句固有トークン | 対象 |
|---|---|---|
| `raw_history_gate_single_source`（新規） | 「許可の単一ソースは production 定数」 | L96 |
| `single_source_file_lock`（widen） | 「ファイル単位排他ロック」 | L92 |
| `single_source_review_channels`（widen） | 「weak チャネルの単一ソース」 | L135 |
| `cli_dry_run_default`（widen） | 「scaffold_advisory.py」（team-lead提示の「builder stub 生成」は既に登録済みかつ別の句に属し対象句を保護しないため、実測で代替） | L152 |
| `dogfood_gate_light_non_blocking`（widen） | 「3層検査」 | L153 |
| `single_source_pj_slug`（widen） | 「PJ slug 導出の単一ソース」 | L156 |
| `hook_fail_open`（widen） | 「既存ファイル非破壊」 | L180 |
| `evolve_revert_cli_default_dry_run`（widen） | 「#402・既定 dry-run」（team-lead提示の「--dump-before」は別行=L254に属し対象句を保護しないため、実測で代替） | L250 |

### 句単位スイープ・第3巡（team-lead 実測指摘の2件を是正・2026-08-17）

第2巡の是正のうち2件は、**より狭い「文中の1フレーズだけ」を削除する変異**（第2巡で使った
「`。`区切りの句全体」削除より厳しいテスト）に耐えられていなかった。team-lead の実測で発覚し、
両方とも是正・実測確認済み。

| 対象 | 未検出だった具体的な変異 | なぜ第2巡の是正で防げなかったか | 是正内容 |
|---|---|---|---|
| L92 `file_lock` | 「ロック下からは `_locked` 版を使い自己 deadlock を回避」だけを削除（第1契約句は無傷） | この行には**独立した契約句が2つ**あり、第2巡で足したトークン「ファイル単位排他ロック」は第1句にしかない。第2句は元々このスイープの vocab リスト（凍結/reject/dry-run 等24語）に該当語が無く、**候補としてすら検出されていなかった**（clause 単位の split はできていたが vocab フィルタで弾かれていた） | `single_source_file_lock` に「自己 deadlock」（count()==1・実測確認）を追加 |
| L152 `scaffold_advisory` | 「CLI は既定 dry-run」だけを削除（前後の句・実体列は無傷） | 第2巡で足した「scaffold_advisory.py」は同じ行の**「実体」列**にあり、`。`区切りの句全体を消す私のテストではこの実体列も道連れに消えるため一見赤くなったが、team-lead のようにこの1フレーズだけを狭く削ると実体列は無傷のまま残り緑だった。加えて「CLI は既定 dry-run」自体も文書内で一意でない（L94 `evolve_revert` 行に同一言い回しが1箇所ある）ため単独では使えない | `cli_dry_run_default` のトークンを「scaffold_advisory.py」→「配線チェックリスト。CLI は既定 dry-run」（`。`境界をまたいで前の句と連結した一意な連続文字列・count()==1・実測確認）に差し替え |

## 陰性試験

### ① 句単位スイープ（行単位でなく句単位が完了判定）
- 第1巡（60→75件時点）: 全89句中 赤62 / 未検出27
- 第2巡（75→76件時点）: 全89句中 赤70 / 未検出19（`。`区切りの句単位削除ベース）
- **第3巡（最終・narrow-deletion 反映後）**: `。`区切りの句単位スイープの機械集計は
  依然 **全89句中 赤70 / 未検出19**（この自動スイープの vocab リスト・分割粒度では
  L92 第2契約句を候補として検出できない構造的限界がある。下記「未検出の全列挙」参照）。
  team-lead 実測指摘の2件（L92 第2契約句・L152 の狭いフレーズ単位削除）は自動スイープの
  検出網の外にあったが、個別に実測・是正・再確認済み（詳細は上記「句単位スイープ・第3巡」表）。

**未検出の全列挙（19句・自動スイープの機械出力。1行1句・省略なし）**:
```
L52  「実データ dry-run 較正」 → a誤検出 / GEPAガードレールの技術説明。拘束ではない
L63  「廃止リダイレクトのみ」 → a誤検出 / 移行済み機能の歴史的注記
L77  「人間確認のループ統合」 → a誤検出 / 一般アーキテクチャ記述。pillar4一般契約と重複
L90  「accept/reject 履歴の正準ストア」 → a誤検出 / rejectはストアに入るデータ種別の名前
L116 「emit→drain laneでのaccept/reject」 → a誤検出 / 同上（データ種別の記述）
L119 「型 drift のみ」 → a誤検出 / 検出器の対象範囲の記述
L155 「--dry-run 対応」 → a誤検出 / CLIがオプションを持つ事実の記述
L160 「user発話のみ抽出」 → a誤検出 / 入力ソース種類の記述
L190 「queue待ちPJにevolve --dry-runを順次実行し提案を集約レポート化」 → a誤検出 / 通常動作の説明。承認ゲート等の拘束は別句(fleet_propose_batch_approval_gate)で捕捉済み
L191 「承認済みevolve提案をrepo外worktreeでcommit→push→PR化」 → a誤検出 / 入力前提の記述。マージ人間ゲートは別句/別行(fleet_pr_start_finish_human_merge_always・L191自体のfleet_pr_human_merge_gate)で捕捉済み
L237 「bin/evolve-fleet detect --pj amamo --dry-run」 → a誤検出 / bashコマンド例
L240 「既定5」 → a誤検出 / --threshold で上書き可能な既定値のコメント
L242 「既定09:00」 → a誤検出 / --time で上書き可能な既定値のコメント
L247 「bin/evolve-scaffold-advisory my_check # dry-run」 → a誤検出 / bashコマンド例
L259 「bin/evolve-tier sync # targets への反映をdry-run（diff表示のみ）」 → b省略可 / tier_sync_explicit_approval(L55)+evolve_tier_cli_sync_default(L187相当)が既に同一事実を保護。三重化
L260 「bin/evolve-tier sync --apply # driftのみ実書込」 → b省略可 / 同上
L263 「承認フロー付き」 → a誤検出 / 上位スキル説明。実体はL55/evolve_tier_cli_sync_defaultで保護済み
L323 「dry-runするため、計測窓suppressの暦日境界等で...」 → a誤検出 / 背景説明（なぜflakyか）。対処法(二層golden方式)は別句(evolve_keyset_snapshot_declared_prefix_only)で捕捉済み
L326 「SKILL.mdコードブロック」 → a誤検出 / 「ブロック」が「code block」の部分文字列で無関係
```

**自動スイープの構造的限界（team-lead 指摘で判明・隠さず明記）**: 上記19句は「`。`区切り
かつ契約語彙24語のいずれかを含む」候補のみを対象にした機械集計であり、(i) 語彙に該当しない
契約句（L92第2句のような「_locked版」「deadlock」等の語）、(ii) `。`より細かい文中の1
フレーズ単位の毀損は、この自動スイープの検出網に入らない。今回 team-lead の手動レビューで
2件発見・是正したが、**同種の未発見句が他にも残っている可能性を否定できない**（悉皆性の
保証はできていない）。

- 追加・widen した invariant すべてについて「その行を実 CLAUDE.md から削除すると red に
  なる」ことを `test_deleting_the_row_each_invariant_protects_flags_it_in_real_claude_md`
  （76件全件を自動で回す設計）で実測 → 全件 pass
- 参考値（行単位）: 50行 → 17行 → 0行相当（widen 後は句単位で赤くなるため行単位でも
  当然赤くなる。行単位は完了判定には使わない）

### ② 語は残して意味を壊す（代表1件のみ・codex指摘反映）
substring 存在確認である以上、必須語を残したまま矛盾する注記を追記する編集は**構造上検出
不可能**（脅威モデルの外側）。代表1件を実際に real CLAUDE.md に適用して確認:
`verbosity_no_auto_apply` の「auto-apply しない」直後に「※ただし2026-09-01のロールアウトで
この制限は撤廃され、実際には auto-apply する」を追記 → **緑のまま（想定通り）**。
`claude_md_contract.py` docstring に明記済み(yes)。

### ③ 分散・入替（変更なし・全部実施）
- 行の移設（`noise_agent_type_kind` 行を Compaction Instructions 直前へ移設）→ 緑のまま
- 2行 swap（subagent_noise_single_source と memory_capability_single_source）→ 緑のまま
どちらも部分文字列が文書のどこかに残るため事実は失われておらず、緑のままで正しい。

### ④ 検査を無効化する（変更なし・全部実施）
1. `REQUIRED_INVARIANTS = ()` → golden テストが `len(())=0 != 76` で red
2. `_missing_tokens` を常に `[]` を返すよう monkeypatch → 既存テストが期待した findings を
   得られず red（実測確認）
3. `dogfood/cli.py` の `_run_layer2` から `checks.append(claude_md_contract.layer2_check(...))`
   を実際に削除して `pytest -k dogfood`（120件）実行 → **全緑のまま（検出漏れ）**。
   守るテストが皆無だったため新規 `test_dogfood_layer2_wires_claude_md_contract`
   （ソース静的検査）を追加して穴を塞いだ。削除→赤、復元→緑を実測して確認済み。
   コメントアウトのみ（部分文字列温存）では検出できないことも実測で確認し、実際の削除で
   テストしている。

### 自分で追加した回避手段2件以上（team-lead提示①〜④とは別種）
- Unicode 全角ダッシュ置換（`auto-apply` のハイフンを全角 `－` に）→ 赤
- ゼロ幅スペース（U+200B）をトークン中間に挿入 → 赤
- 全角空白5000個のパディングでトークンを分断 → 赤

いずれも「トークンの部分文字列が壊れる」変換は過剰検出側に倒れて正しく赤くなることを確認。

### 探索した入力クラス・変換
要素削除（行単位・句単位）/ 語は残し意味反転（矛盾注記の追記・代表1件）/ 分散・入替（移設・
swap）/ 検査無効化（golden空・関数無効化・CI配線削除）/ Unicode全角文字置換 / ゼロ幅文字混入 /
巨大空白パディング / 表記ゆれ（行末空白・CRLF、陽性対照）。

## 陽性対照
- 無改変の real CLAUDE.md（`test_real_repo_claude_md_has_no_missing_contracts` 他） → 緑
- 全行末尾スペース付与 → 緑
- CRLF 化 → 緑
（76件化後も既存4件の陽性対照テストが全て green のまま pass することを確認済み）
