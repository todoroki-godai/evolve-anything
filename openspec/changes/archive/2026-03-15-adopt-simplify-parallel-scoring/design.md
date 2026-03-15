## Context

rl-scorer は現在 sonnet 1エージェントが3軸（技術40%・ドメイン40%・構造20%）を同時評価している。Claude Code v2.1.63 の `/simplify` は3つの専門エージェント（再利用性・品質・効率性）を並列起動するアーキテクチャを採用し、単一エージェントより高い評価精度を実現している。

また evolve パイプラインの Compile ステージでは remediation がファイルを自動修正するが、品質チェックは regression_gate.py（構造検証）のみ。コード品質の劣化検出が不足。

## Goals / Non-Goals

**Goals:**
- rl-scorer の評価精度を向上させる（軸ごとの専門エージェント化）
- トークンコストを削減する（sonnet → haiku への切替）
- evolve Compile 後のコード品質保証を追加する（/simplify ゲート）
- 既存インターフェース（0.0-1.0 スコア + JSON 出力）を維持する

**Non-Goals:**
- rl-scorer の評価軸や重みの変更（既存の3軸・重み配分はそのまま）
- /batch の取り込み（rl-loop 並列バリエーションは今回対象外）
- /simplify 自体のカスタマイズ（ビルトインをそのまま使う）

## Decisions

### D1: rl-scorer をオーケストレーター + 3サブエージェント構成にする

**選択**: `agents/rl-scorer.md` をオーケストレーターに変更し、3つのサブエージェント（technical-scorer, domain-scorer, structural-scorer）を Agent ツールで並列起動する。

**理由**: /simplify の設計に倣い、各軸の評価を独立したエージェントに委ねることで、1エージェントの認知負荷を下げ評価精度を上げる。並列起動で所要時間も抑えられる。

**代替案**:
- A) 1エージェントのまま prompt 改善 → 認知負荷の根本解決にならない
- B) Python スクリプトで3回 LLM API 呼び出し → Agent ツールの方がコンテキスト管理が自然

### D2: サブエージェントの model は tiered strategy を使用する

**選択**: technical-scorer と structural-scorer は `model: haiku`、domain-scorer は `model: sonnet` で起動する。

**理由**: 技術品質・構造品質は明確な基準（チェックリスト型）で haiku で十分な精度が出る。一方、ドメイン品質（ゲームの没入感・面白さ、Bot のパーソナリティ等）は主観的判断を含み、haiku では精度不足のリスクがある。haiku×2 + sonnet×1 のコストは旧来の sonnet×1 と同程度。

**代替案**:
- A) 全 haiku → ドメイン評価の精度リスク
- B) 全 sonnet → コスト3倍。精度は上がるがコスパ悪い

**フォールバック**: 精度テストで sonnet が不要と判明した場合、domain-scorer も haiku に変更するだけで切り替え可能。

### D3: オーケストレーターの結果統合は rl-scorer.md 内で行う

**選択**: `agents/rl-scorer.md` が3サブエージェントの JSON 結果を受け取り、重み付き平均で統合スコアを算出して最終 JSON を出力する。

**理由**: 統合ロジックをエージェント定義内に閉じることで、rl-loop や evolve からの呼び出しインターフェースが変わらない。

### D4: /simplify ゲートは evolve SKILL.md の手順追記で実現する

**選択**: Python スクリプトへの組み込みではなく、evolve SKILL.md の Step 5.5 後に「/simplify 条件付き実行」ステップを追記する。

**理由**: /simplify はビルトインスキルなので、LLM がスキルとして呼び出すだけでよい。コード変更不要。evolve.py への依存追加も不要。

**条件**:
- remediation の `record_outcome()` で記録された `fix_detail.changed_files` を集約し、変更ファイルリストを確定する
- 変更対象が Python ファイル（.py）を含む場合のみ発火（Markdown のみの変更では不要）
- `/simplify` が利用できない環境（古い Claude Code）ではスキップ

### D6: run-loop.py の並列スコアリング対応

**選択**: `run-loop.py` の `get_baseline_score()` と `score_variant()` は現在 `subprocess.run(["claude", "-p"])` で直接プロンプトを投げてスコアリングしている。並列化後も `claude -p` 経由であれば、rl-scorer エージェント定義の変更が自動的に反映されるため、run-loop.py 側の変更は最小限（`score_variant()` のプロンプトに rl-scorer の採点基準を明示するだけ）で済む。

**理由**: `agents/rl-scorer.md` はエージェント定義ファイルであり、`claude -p` はそれを自動ロードしない。run-loop.py は独自のプロンプトで直接 claude を呼び出しているため、rl-scorer.md の並列化は run-loop.py に影響しない。ただし、run-loop.py でも並列スコアリングの恩恵を得るには、`concurrent.futures.ThreadPoolExecutor` で3軸を並列評価するか、`claude -p --agent rl-scorer` フラグ（将来対応）を使う方法がある。現時点では ThreadPoolExecutor で3プロセス並列実行する。

**代替案**:
- A) run-loop.py を変更せず claude -p のまま → 並列化の恩恵なし
- B) `--agent rl-scorer` フラグ → Claude Code に agent 呼び出しフラグが実装されていない

### D7: オーケストレーター model は haiku を使用する

**選択**: `agents/rl-scorer.md` の frontmatter `model` を `haiku` に変更する。

**理由**: オーケストレーターの役割は (1) CLAUDE.md 読み込み+ドメイン推定、(2) サブエージェント起動、(3) 結果統合の3つ。いずれも単純なタスクで haiku で十分。sonnet のまま残すと、sonnet(orchestrator) + haiku×2 + sonnet×1 = コスト増になる。

### D5: サブエージェントの prompt 設計

**選択**: 各サブエージェントに対象ファイルの内容と評価基準のみを渡す。CLAUDE.md 読み込みやドメイン推定はオーケストレーター（rl-scorer.md）が行い、結果をサブエージェントの prompt に含める。

**理由**: ドメイン推定を3回繰り返すのは無駄。オーケストレーターで1回行い、domain-scorer には推定済みドメインと対応する評価軸を渡す。

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| haiku の評価精度が sonnet より低い | 精度比較テスト実施。差が大きければ model を sonnet にフォールバック |
| 3エージェント並列起動のレイテンシ | 並列実行なので所要時間は最遅エージェントに依存。haiku は高速なので問題なし |
| /simplify が evolve の意図しない変更を行う | git diff で確認ステップを挟む。不要なら revert |
| サブエージェント間の評価不整合 | 各軸は独立なので不整合は起きにくい。統合はオーケストレーターの責務 |
| 古い Claude Code で /simplify が使えない | バージョンチェックまたはエラーハンドリングでスキップ |
| run-loop.py の並列化が複雑になる | ThreadPoolExecutor で3プロセス並列。フォールバックは逐次実行 |
