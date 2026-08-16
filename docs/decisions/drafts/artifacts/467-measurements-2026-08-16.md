# #467 §1.5 実測 artifact（2026-08-16）

codex cold review 2巡目 [Must]C（`docs/decisions/drafts/467-all-proposal-types-to-morning-yn.md`
§1.5.0「[実測] の再現手順」に再現可能な実行スクリプトが無い）の解消として追加した実測 artifact。
その後の cold review 3巡目で出た [Must]1〜3 + [Should]1（SKILL.md 解決規則の本番不一致・
`--data-dir` が全入力を差し替えない・LLM/書込み無しが実行時に裏取りされていない・同時刻境界の
テスト不足）を本 artifact とスクリプト側で解消した。

- **取得日**: 2026-08-16
- **対象 commit**: `556d846d58a5365c66f47f7f3d0ce70315404812`（本 artifact 再生成時点の HEAD）
- **入力パス**: 下記「入力パスの全件列挙」参照（`--data-dir` で差し替え可能なもの／不能なものを分けて列挙）
- **対象プロジェクト（§1.5.3）**: 本リポジトリ（`--project-root` 既定）
- **スクリプト**: `scripts/bench/measure_467_proposal_kinds.py`
- **実行コマンド**（write-guard / network-guard は常時有効。フラグ不要・誰でも同じ検証を再現できる）:

```
python3 scripts/bench/measure_467_proposal_kinds.py \
  --output docs/decisions/drafts/artifacts/467-measurements-2026-08-16.json
```

- **機械可読 JSON**: [`467-measurements-2026-08-16.json`](467-measurements-2026-08-16.json)
- **join ロジックの単体テスト**: `scripts/lib/tests/test_measure_467_join.py`
  （対象: `scripts/lib/measure_467_join.py`。同時刻境界・global/project 両解決のケースを含む）

## §1.5.1 観測値（今回の実測）

| 観測 | 値 |
|---|---|
| `corrections.jsonl` 総数 | 173（実ストアは追記され続けるため取得時刻で増える） |
| うち `last_skill` truthy | 0 |
| `source` 内訳 | `reflect_confirmed` 163 / `backfill` 8 / `hook` 2 |
| `correction_type` 内訳 | `semantic_idiom` 163 / `stop` 8 / `iya` 1 / `naoshite-request` 1 |
| `usage.jsonl` の Skill 呼び出し総数 | 888（Agent 呼び出し・workflow-conformance 別スキーマを除く。判別規則は `measure_467_join.py::is_skill_usage_record`） |
| correction と同セッションで先行する Skill 呼び出しがあるもの | 30 / 173 |
| その30件で SKILL.md が解決できる数 | 0 / 30（global **と** project 両方を `discover/runner.py:417-419` と同じ順序・規則で解決した結果。2026-08-16 codex cold review [Must]1 是正: 従来 global のみだったが project 側も見るよう修正済み。件数は 0 のまま変わらない） |

## §1.5.3 観測値（今回の実測・未接続13種の産出件数）

| 種別 | 産出件数 |
|---|---|
| `repeating_patterns` | 124 |
| `rule_violation_observed` | 25 |
| `recommended_artifacts` | 12 |
| `trajectory_skill_candidate` | 1 |
| `missed_skill_opportunities` | 1 |
| `pitfall_candidates` | 0 |
| `hook_candidates` | 0 |
| `instruction_violation` | 0 |
| `verification_needs` | 0 |
| `stall_recovery_patterns` | 0 |
| `workflow_checkpoint_gaps` | 0 |
| `constraint_decay_warnings` | 0 |
| `constraint_decay_findings` | 0 |

## 入力パスの全件列挙（2026-08-16 codex cold review [Must]2 是正）

`--data-dir` は全ての入力を差し替えられるわけではない。実装を読んで確認した差し替え可否を
以下に全件列挙する（機械可読版は出力 JSON の `referenced_input_paths`）。

### `--data-dir` で差し替え可能（確認済み）

| パス | 用途 |
|---|---|
| `<data-dir>/corrections.jsonl` | §1.5.1 集計 / `pitfall_candidates` / `hook_candidates` / `instruction_violation` |
| `<data-dir>/usage.jsonl` | §1.5.1 集計 / `missed_skill_opportunities`（`discover.DATA_DIR` の call-time 差し替え経由） |
| `<data-dir>/errors.jsonl` | `pitfall_candidates` |
| `<data-dir>/sessions.jsonl` | `constraint_decay_warnings` / `constraint_decay_findings`（`detect_constraint_decay` に直接パス引数で渡す） |
| `session_store` union read（`sessions.db` + 未 ingest `sessions.jsonl`） | `missed_skill_opportunities` の `query_sessions()` 経路。`detect_missed_skills` は内部で `sessions_file` を渡さず `session_store` の union read に落ちるため `discover.DATA_DIR` の差し替えでは効かなかった（旧実装の欠落）。`session_store._DATA_DIR_OVERRIDE`（テスト用に用意された call-time override。read-only 用途なので流用）を同時に差し替えて解消した |

### `--data-dir` では差し替えられない（`--project-root` / 実 home に紐づく設計）

| パス | 用途 | 備考 |
|---|---|---|
| `~/.claude/projects/<encoded project_root>/*.jsonl`（セッション transcript） | `repeating_patterns` / `rule_violation_observed` / `recommended_artifacts` / `stall_recovery_patterns` / `trajectory_skill_candidate` | `discover/runner.py` と同じ CC エンコード規則。他環境では `--project-root` を差し替えれば各自の transcript に自動で切り替わる（`--data-dir` の対象にする設計上の理由が無い） |
| `<project_root>/.claude/skills/`, `~/.claude/skills/` | `instruction_violation` の SKILL.md 読み込み / `missed_skill_opportunities` の既存スキル名収集 / `workflow_checkpoint_gaps` | スキル定義そのもの（config）。measurement データではない |
| `<project_root>/.claude/rules/`, `~/.claude/rules/` | `repeating_patterns` / `rule_violation_observed`（禁止コマンド抽出） | rule 定義（config） |
| `<project_root>/.claude/`（導入済み hook/artifact 検出）, `<project_root>/CLAUDE.md` | `recommended_artifacts` / `verification_needs` / `missed_skill_opportunities`（skill trigger 抽出） | config |
| `~/.claude/skills/` | §1.5.1 の SKILL.md 解決（`skill_md_resolves`） | `runner.py:417-419` と同じ規則で global を必ず先に見る |

## 安全性検証（実行時証跡・2026-08-16 codex cold review [Must]3 是正）

grep によるコード監査だけでなく、測定本体の実行そのものを2つの execution-time guard で
包んで検証した（フラグ不要・常時有効・同じ再現コマンドで誰でも再現できる）。

| 検証 | 方法 | 結果 |
|---|---|---|
| 書込みなし | `builtins.open` / `os.open` を実行時に差し替え、`~/.claude/` 配下への書込みモード open を検出したら即座に例外送出 | **passed**（違反ゼロで完走） |
| ネットワーク呼び出しなし（LLM 含む） | `socket.socket()` のインスタンス化を実行時に差し替え、呼ばれたら即座に例外送出 | **passed**（違反ゼロで完走） |
| `~/.claude/` 全体の書込み差分（補強材料） | 実行前後に `~/.claude/` 全体（narrow せず。約52,000ファイル、stat のみで約1.6秒）の (相対パス, size, mtime_ns) マニフェストを取り diff | 本 artifact の最終生成実行では **diff_total=0**（差分なし）。write-guard がゼロ違反で完走している以上、この本スクリプトのプロセスが書いたものではないことの直接証拠でもある |

（開発中の別実行では、他の並行 Claude Code セッション/hook による `usage.jsonl` の更新や、本
worker セッション自身の transcript を CC harness が記録した差分が observe されたことがあるが、
write-guard がゼロ違反で完走している以上いずれも本スクリプトのプロセス起因ではない。
`safety_verification.home_claude_manifest_diff` に実行のたびの実測値が記録される）

## 履歴（rev5 初版からの訂正）

設計ドラフト §1.5.0 は commit `556d846d`（本 artifact 生成前のオーケストレーターによる訂正）で
rev5 初版の誤記載2件（`usage.jsonl` Skill 呼び出し総数 5,574→888、先行 Skill 呼び出し 28/171→30/172）
を既に本文へ反映済み。以後、本 artifact を正典とし、設計本文の [実測] 値はこれと一致させる。
§1.5.1/§1.5.3 の結論（SKILL.md 解決 0 件・型フィルタで型不一致・未接続13種の産出件数）は
rev5 初版からこの artifact まで一貫して変わっていない。
