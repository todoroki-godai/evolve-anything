# 設計原則と落とし穴

## 実証済みパターン（採用）

| 原則 | 根拠 | 適用箇所 |
|------|------|---------|
| 構造的制約はコードで強制 | [32世代実験](https://dev.to/stefan_nitu/32-more-generations-my-self-evolving-ai-agent-learned-to-delete-its-own-code-18bp): プロンプトでの制約は2回失敗 | 全アーティファクトの行数制限 |
| 使用回数の可視化が淘汰を自然に促す | 32世代実験: 「0回使用」が見えた瞬間に削除が始まった | usage.jsonl + Report |
| Hook 観測は 100% 信頼、スキル観測は 50-80% | [Homunculus](https://github.com/humanplane/homunculus) v1→v2 の知見 | async hooks を採用 |
| 人間レビューは信頼性機能 | 全成熟ツールのコンセンサス | 全変更に承認ステップ |
| 5+ クラスタでスキル候補 | Homunculus, claude-reflect 共通 | Discover の閾値 |
| ファイルベースのプラグイン連携 | Claude Code にネイティブ IPC なし | claude-reflect 連携 |
| 観測フェーズで LLM を呼ばない | コスト管理 + UX影響ゼロ | async hook + regex/集計のみ |

## 構造的制約（コードで強制）

> **「膨張するな」とプロンプトに書いても効かない。コードで構造的に制約する。**

| アーティファクト | 制約 | 強制方法 |
|-----------------|------|---------|
| SKILL.md | 500行以下 | 生成/最適化時にバリデーション。超過時は圧縮版を再生成 |
| rules/*.md | 3行以内 | rules-style.md と一致。生成時にバリデーション |
| CLAUDE.md | 200行以下 | evolve 実行時にチェック。超過時は圧縮提案 |
| memory/*.md | 個別ファイル120行以下 | evolve 実行時にチェック。超過時は分割/圧縮提案 |

## 落とし穴と対策

| リスク | 対策 |
|--------|------|
| **ルール肥大化** | コードで行数制限を強制。evolve 時に自動チェック |
| **モデル崩壊**（自己生成データで品質退化） | 全変更に人間レビュー。自動適用は行わない |
| **過学習**（1回の失敗から過度に一般化） | 複数回検出（3-5回）の閾値。3行ルールが抽象化を強制 |
| **観測コスト爆発** | async hook + LLM 呼び出しなし。JSONL 追記のみ |
| **claude-reflect 依存** | 入力ソースの1つとして利用。未インストールでも動作 |
| **淘汰の誤判断** | アーカイブ方式（復元可能）。30日ルール。参照チェック |
| **フィードバックのプライバシー** | Issue 送信前にプレビュー。スキル内容/パスを含めない |

## 参考にした主要プロジェクト

| プロジェクト | 取り入れた点 |
|---|---|
| [Homunculus](https://github.com/humanplane/homunculus) | instinct → cluster → evolve のフロー、Hook v2 アーキテクチャ |
| [SkillRL](https://arxiv.org/abs/2602.08234) | SkillBank 階層、成功率閾値による再帰的進化トリガー |
| [claude-rules-doctor](https://github.com/nulone/claude-rules-doctor) | dead glob 検出による Prune |
| [claude-reflect](https://github.com/BayramAnnakov/claude-reflect) | 6層メモリ階層、`--dedupe`、/reflect-skills |
| [everything-claude-code](https://github.com/affaan-m/everything-claude-code) | Stop hook パターン検出、learned/ ディレクトリ |
| [GEPA](https://arxiv.org/abs/2507.19457) | 実行トレース活用、Pareto-aware 選択（ICLR 2026 Oral） |
| [GAAPO](https://arxiv.org/abs/2504.07157) | Bandit ベース評価予算配分 |
| [SpecWeave](https://spec-weave.com/docs/skills/extensible/self-improving-skills/) | 中央集権メモリ、重複排除 |
