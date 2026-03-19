Closes: #35

## Context

rl-anything の discover はセッションtranscript（`~/.claude/projects/<encoded>/*.jsonl`）からツール使用パターンを分析し、ルール/hook/スキル候補を提案する。`extract_tool_calls()`（`tool_usage_analyzer.py:70-121`）が各セッションファイルの assistant メッセージから tool_use ブロックを抽出し、Bash コマンド文字列を収集している。現在は以下の検出が可能:
- `classify_bash_commands()`: builtin_replaceable / sleep_polling 分類
- `detect_repeating_commands()`: 同一コマンドの繰り返し（閾値 5 回以上）
- `detect_recommended_artifacts()`: 推奨 rule/hook の未導入チェック

しかし、**プロセス停滞→調査→kill→リトライ**という時系列シーケンスの検出はできない。セッションtranscript にはツール呼び出しの完全な履歴（コマンド文字列含む）があり、セッション単位で時系列分析すれば停滞パターンの検出は可能。

## Goals / Non-Goals

**Goals:**
- セッションtranscript の Bash コマンドシーケンスから「長時間コマンド実行→プロセス調査→kill→リトライ」パターンを検出する
- 検出パターンを issue_schema 経由で既存 remediation パイプラインに統合する
- RECOMMENDED_ARTIFACTS にプロセスガードエントリを追加し、未導入プロジェクトに提案する
- pitfall candidate として自動登録できるデータ形式で出力する

**Non-Goals:**
- リアルタイムのプロセス監視（hook レベルで実行中プロセスを watch する機能）
- 特定ツール（CDK, Docker 等）のドメイン固有リカバリロジック実装
- Bash コマンド引数のセマンティック解析（正規表現マッチで十分）

## Decisions

### D1: 検出対象データソース — セッションtranscript

**選択**: セッションtranscript（`~/.claude/projects/<encoded>/*.jsonl`）の assistant メッセージ内 tool_use ブロックから Bash コマンド文字列を時系列分析する。新関数 `extract_tool_calls_by_session()` を追加し、`Dict[str, List[str]]`（session_id → commands）を返す。

**既存 `extract_tool_calls()` との関係**: 既存関数はセッション境界を失い全コマンドをフラットに返すため、停滞パターンのようなセッション内時系列分析には使えない。新関数は同じ JSONL パースロジック（`tool_usage_analyzer.py:87-121`）を再利用しつつ、セッションファイル単位で結果を分離する。

**代替案（却下）**: workflows.jsonl の step シーケンス → workflows.jsonl の step には Bash コマンド文字列が含まれない（tool 名と intent_category のみ）。実際のコマンド内容を知るにはセッションtranscript が必須。

### D2: 停滞パターンの定義 — 3段階シーケンスマッチ

**選択**: 以下の 3 段階シーケンスをパターンとして定義:
1. **Long command**: 長時間実行が予想されるコマンド（`cdk deploy`, `docker build`, `npm install`, `pip install` 等）
2. **Investigation**: プロセス調査コマンド（`pgrep`, `ps aux`, `lsof`, `fuser` 等）
3. **Recovery**: プロセス終了コマンド（`kill`, `pkill`, `rm -rf` 等）

同一セッション内で `Long → Investigation → Recovery → Long` の部分シーケンスが出現した場合に検出。完全一致ではなく、間に他のコマンドが挟まっても可。

**時系列順序保証**: セッション JSONL は append-only で時系列順。セッション内で完結するため追加ソート不要。

**代替案**: LLM で step シーケンスを評価 → コスト過大。正規表現ベースのパターンマッチで十分な精度が期待できる。

### D3: 配置場所 — tool_usage_analyzer.py に追加

**選択**: `scripts/lib/tool_usage_analyzer.py` に `extract_tool_calls_by_session()` と `detect_stall_recovery_patterns()` を追加。

**理由**: 既存の `extract_tool_calls()` / `classify_bash_commands()` / `detect_repeating_commands()` と同レベルのツール使用分析であり、同モジュールに凝集させるのが自然。

### D4: 出力形式 — issue_schema 準拠

**選択**: 検出結果を `issue_schema.make_stall_recovery_issue()` で生成し、remediation パイプラインに統合する。

**理由**: 既存の rule_candidate / hook_candidate / verification_rule_candidate と同じフローで処理でき、evolve レポートにも自然に表示される。

### D5: 閾値 — セッション横断で 2 回以上 + recency フィルタ

**選択**: 同一コマンドパターンの停滞→リカバリが **2 セッション以上** で検出された場合に候補として出力（`STALL_RECOVERY_MIN_SESSIONS = 2`）。`STALL_RECOVERY_RECENCY_DAYS = 30` でセッションファイルの mtime が 30 日以上前のものを除外し、古いパターンの再浮上を防止する。

**理由**: 1 回限りの事故は学習対象外。繰り返しパターンのみを対象とすることで FP を抑制。recency フィルタにより環境変更後の陳腐化パターンも除外。

### D6: Confidence 算出式

**選択**: `confidence = min(0.5 + session_count * 0.1, 0.95)`

**理由**: 2 セッション（最小閾値）で 0.7、5 セッション以上で上限 0.95。セッション数に比例して確信度が上がるが、完全な確信（1.0）は避ける。既存の issue_schema パターン（`SPLIT_CANDIDATE_CONFIDENCE = 0.70` 等）と整合。

### D7: pitfall candidate 統合

**選択**: 検出された停滞パターンを pitfall candidate に変換して出力する。

- root_cause フォーマット: `"stall_recovery — {command_pattern}: {session_count} sessions"`
- 既存 `find_matching_candidate()`（`pitfall_manager.py:495-626`）の Jaccard 重複排除を再利用
- pitfall ライフサイクル（Candidate → New → Active → Graduated）に統合

**理由**: 検出パターンをスキルの pitfalls.md に永続化し、Pre-flight チェックとして機能させる。既存の pitfall 基盤を再利用することで新規コードを最小化。

## Risks / Trade-offs

- **FP リスク: 正常な kill → restart が停滞と誤判定される** → Mitigation: Investigation step（pgrep/ps）の存在を必須条件とし、意図的な restart と区別する
- **パターン辞書のメンテナンス**: 長時間コマンドのリストは有限 → LONG_COMMANDS / INVESTIGATION_COMMANDS / RECOVERY_COMMANDS を定数化し、ユーザーが追加可能にする
- **セッションtranscript のデータ不足**: backfill 未実行や新規プロジェクトではデータが少ない → data_sufficiency チェックと連携し、不足時は空リスト返却（エラーなし）
