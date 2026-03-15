## Context

rl-anything は Claude Code Plugin として 13 スキル + 7 フック + 1 エージェント（rl-scorer）で構成される。Claude Code v2.1.x で追加された plugin/skill 向け新機能が未適用のまま残っており、コンテキスト効率・コスト・安全性に改善余地がある。

現状:
- 全スキルは標準 frontmatter（name, description, allowed-tools）のみ
- テンプレートパスは Python コード内で `_plugin_root / "skills" / "evolve" / "templates"` とハードコード
- フックは hooks.json で定義（settings-based）、`${CLAUDE_PLUGIN_ROOT}` 変数を使用
- rl-scorer は既に `model: sonnet`, `memory: project` を設定済み

## Goals / Non-Goals

**Goals:**
- evolve/audit/discover の大量出力がメインコンテキストを汚染しないようにする（context:fork）
- スキル内のパス参照をポータブルにする（${CLAUDE_SKILL_DIR}）
- Agent tool 呼び出し時のモデル指定でコスト最適化
- reflect と Claude 組み込み auto-memory の衝突を防止
- optimize でのファイル変更を安全に隔離（worktree isolation）
- PostCompact フック対応で compaction 後の状態保存を強化
- evolve フェーズ別 effort level でコスト最適化

**Non-Goals:**
- trigger_engine の `/loop` への完全移行（調査のみ）
- audit の `background: true` エージェント化（検討のみ）
- 既存フック（hooks.json）の skill hooks への全面移行
- `once: true` の restore_state への適用（settings hooks では不可、skill-only 機能のため）

## Decisions

### D1: context:fork の適用対象

**決定**: evolve, audit, discover の 3 スキルに `context: fork` を追加。

**理由**: この 3 スキルは明確なステップバイステップの実行手順を持ち、大量の出力（レポート、診断結果）を生成する。fork コンテキストでは会話履歴にアクセスできないが、これらはテレメトリデータとファイルシステムから情報を取得するため問題ない。

**結果返却方式**: `context: fork` で起動されたスキルは、fork されたエージェントの最終メッセージが親コンテキストに返される。ただし、大量の中間出力は返されないため、最終レポートを簡潔にまとめる必要がある。詳細結果は `<DATA_DIR>/<skill>-report.json`（例: `evolve-report.json`）にファイル出力し、親コンテキストから Read で参照可能にする（MUST）。

**AskUserQuestion 互換性**: fork コンテキストでは AskUserQuestion が動作しない。evolve/audit/discover の SKILL.md 内で AskUserQuestion を使用する箇所は、ファイル出力＋最終メッセージでの提案に置き換える（MUST）。ユーザー承認が必要な操作（remediation の auto_fix 等）は fork 復帰後にメインコンテキストで実施する。

**除外**: reflect（対話的レビューが必要で会話コンテキストに依存）、optimize/rl-loop（corrections コンテキストが必要）、version/feedback/update（軽量で fork 不要）。

**代替案**: 全スキルに一律適用 → 却下。会話コンテキストに依存するスキルでは動作不良になる。

### D2: ${CLAUDE_SKILL_DIR} vs ${CLAUDE_PLUGIN_ROOT}

**決定**: SKILL.md 内のスキルローカルファイル参照には `${CLAUDE_SKILL_DIR}` を使用。Python スクリプト内のパス参照は変更しない（Python は `__file__` ベースのパス解決が安全）。

**理由**: `${CLAUDE_SKILL_DIR}` はスキルの SKILL.md が存在するディレクトリに解決される。SKILL.md 内で「このスキルの templates/ を読め」等の指示を書く際に有用。Python コードは既に `Path(__file__).resolve().parent` パターンで正しく動作しており変更不要。

### D3: Agent model 指定戦略

**決定**: Agent tool 呼び出し時に `model` パラメータを明示的に指定する指針を SKILL.md に追記。

| 用途 | モデル | 理由 |
|------|--------|------|
| rl-scorer（採点） | sonnet（現状維持） | コスト効率 + 十分な品質 |
| evolve LLM 評価（classify_issues 等） | 指定なし（inherit） | 親スキルのモデルを継承 |
| discover パターン検出 | haiku（Agent tool 経由時） | 大量データの高速処理 |

**代替案**: 全て opus → コスト過大。全て haiku → 品質不足。

### D4: auto-memory 協調方式

**決定**: reflect_utils.py の memory ルーティングに「auto-memory 重複チェック」フェーズを追加。

**方式**:
1. reflect が correction を memory にルーティングする前に、auto-memory ディレクトリ（`~/.claude/projects/<encoded>/memory/`）の既存ファイルを走査
2. **比較粒度**: correction テキスト全体 vs auto-memory 各ファイルのセクション単位（`split_memory_sections()` を再利用してセクション分割）
3. Jaccard 類似度で重複判定（既存の similarity_engine を再利用、**閾値 0.6**）。文書レベル Jaccard は長文で精度が低下するため、セクション単位で比較し最大スコアを採用する
4. 重複検出時はスキップ + ログ出力（auto-memory が既にカバー済み）
5. CLAUDE.md に auto-memory との棲み分けガイドを記載

**閾値選定理由**: semantic similarity 0.85 が業界標準だが、Jaccard はトークンベースのため低めに設定。0.5 では false positive（過度なスキップ）リスクがあるため 0.6 に引き上げ。将来的に semantic embedding が利用可能になった場合は 0.85 に切り替え可能な設計にする。

**代替案**: auto-memory を完全無効化 → ユーザー体験が悪化。rl-anything 側の memory 書き込みを廃止 → reflect の価値が減少。semantic embedding → 外部依存が増加、現時点では Jaccard で十分。

### D5: worktree isolation の適用

**決定**: optimize スキルの実行アーキテクチャを Agent tool 経由に変更し、`isolation: "worktree"` で隔離実行する。

**実行アーキテクチャ**: 現在 optimize は Bash 経由で `optimize.py` を実行するため Agent tool の `isolation` パラメータが直接使えない。以下の方式で対応する:

1. **patch-apply-test サイクルを Agent tool 経由に変更**: optimize SKILL.md の指示として、パッチ適用→テスト→結果確認のサイクルを Agent tool（`isolation: "worktree"`）で起動するサブエージェントに委譲する
2. **サブエージェント内での処理**: worktree 内で `optimize.py --apply-patch` を実行し、`pytest` でリグレッションチェック
3. **結果返却**: サブエージェントがパッチ適用結果（diff + テスト結果）をファイル出力し、親コンテキストでユーザー承認を求める

**cleanup 戦略**: Agent tool の worktree isolation は自動クリーンアップを提供する（変更なしなら自動削除、変更ありなら worktree パスとブランチ名を返却）。明示的な `git worktree remove` は不要。

**代替案**: optimize.py 内で `git worktree add/remove` を自前実装 → Agent tool の isolation 機能と重複するため却下。

### D6: PostCompact フック対応

**決定**: save_state.py を PostCompact フックとしても登録（PreCompact と PostCompact の両方で動作）。ただし PostCompact 時は別キーに保存し、PreCompact の情報量を保護する。

**方式**:
1. hooks.json に PostCompact エントリを追加
2. checkpoint.json に `hook_type` フィールドを追加（"pre_compact" | "post_compact" | "session_end"）
3. **PostCompact 時は `post_compact_checkpoint` キーに保存**し、PreCompact の `checkpoint` キーを上書きしない。PreCompact 側が情報量が多い（compaction 前の context_summary を含む）ため、上書きすると情報消失する
4. restore_state.py は `checkpoint`（PreCompact）を優先的に参照し、存在しない場合のみ `post_compact_checkpoint` にフォールバック

**代替案**: PostCompact 専用スクリプトを新設 → 不要な重複。同一キーで上書き → PreCompact の情報量の多い checkpoint が消失するため却下。

### D7: effort level routing

**決定**: evolve スキルの各フェーズ実行前に、Agent tool の呼び出し時にモデルの effort 指示を自然言語で含める。

**方式**: Claude Code API には直接 effort パラメータを渡す手段がないため、スキルの指示文に「このフェーズは簡潔に（low effort 相当）」「このフェーズは慎重に（high effort 相当）」等の指示を追加。

| フェーズ | 指示 |
|---------|------|
| Diagnose（Step 1-3） | 「簡潔にデータを集計」 |
| Compile（Step 4-7） | 標準（指示なし） |
| Self-Evolution（Step 8） | 「慎重に分析」 |

**効果測定**: telemetry_query.py でフェーズ別トークン使用量を比較し、effort routing 導入前後の差分を測定する。2週間後に有意な差がなければ effort 指示を削除する（SHOULD）。

### D8: memory last-modified timestamp 活用

**決定**: layer_diagnose.py の stale_memory 検出に、ファイルの mtime を参照する追加チェックを導入。

**方式**: 既存の stale_memory 検出は内容ベース（usage.jsonl との突合）。これに加えて、memory ファイルの mtime が 90 日以上前の場合を warning として追加。Claude Code v2.1.75 で memory ファイルに last-modified timestamp が付与されるようになったため、この情報を活用。

**mtime 信頼性対策**: `git clone` / `git checkout` 操作時にファイルの mtime がリセットされ、false positive が多発するリスクがある。以下の緩和策を実装する:
1. **git 操作直後検出**: 対象ディレクトリ内の全ファイルの mtime が近似（標準偏差 < 60秒）の場合、git 操作直後と判断しチェックをスキップする（SHOULD）
2. **frontmatter メタデータ優先**: memory ファイルの frontmatter に `last_modified` フィールドがある場合はそちらを優先し、mtime はフォールバックとする（MUST）

**定数配置**: `MEMORY_STALE_DAYS` は `layer_diagnose.py` に配置する（line_limit.py は行数制限の責務であり、staleness 検出とは無関係）。

### D9: skill hooks の活用

**決定**: evolve スキルに PostToolUse skill hook を追加し、remediation 実行後のリグレッション検出を自動化。

**方式**: evolve SKILL.md の frontmatter に hooks セクションを追加:
```yaml
hooks:
  PostToolUse:
    - matcher: "Bash"
      hooks:
        - type: command
          command: "${CLAUDE_PLUGIN_ROOT}/scripts/lib/regression_gate.py --quick-check"
```

**入出力契約（--quick-check モード）**:
- **入力**: PostToolUse イベント JSON（stdin）。`tool_name`, `tool_input.command` フィールドを参照
- **対象ファイル推定**: Bash command 文字列から変更対象 `.py` ファイルを正規表現で抽出（`Edit`, `Write` ツール使用時はファイルパスを直接取得）
- **チェック内容**: 対象 `.py` ファイルに対して `py_compile.compile()` で構文チェック
- **出力**: exit code 0（正常）/ 1（エラー）+ stderr に JSON result（`{"passed": bool, "errors": [{"file": str, "error": str}]}`）
- **スコープ**: 構文チェックのみ。既存の `check_gates()` とは独立した新規関数 `quick_check()` として実装

**配置判断**: regression_gate.py に `quick_check()` を追加する（新規 `syntax_check.py` ではなく）。理由: (1) PostToolUse hook から呼ばれる「ゲート」としての性質が共通、(2) 将来的に `check_gates()` に統合可能、(3) ファイル増加を避ける。

## Risks / Trade-offs

- **[context:fork でのコンテキスト喪失]** → evolve/audit/discover はファイルシステムベースで動作するため影響小。ユーザーが「さっきの audit 結果を踏まえて〜」と言う場合のみ制約になるが、結果はファイル出力されるので Read で参照可能。
- **[auto-memory 重複チェックの誤判定]** → Jaccard 閾値 0.6（セクション単位比較）。false negative（見逃し）は許容し、false positive（過度なスキップ）は避ける。
- **[worktree isolation のオーバーヘッド]** → worktree 作成に数秒かかる。optimize は元々時間のかかる操作なので許容範囲。
- **[PostCompact フックの二重実行]** → PreCompact と PostCompact で別キーに保存するため情報消失なし。restore_state は PreCompact チェックポイントを優先参照。
- **[effort level 指示の不確実性]** → 自然言語での effort 制御は厳密ではない。効果が不十分な場合は将来の API 対応待ち。
