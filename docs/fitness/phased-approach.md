# フェーズドアプローチ — 環境全体 Fitness 評価の実装計画

> Source: [GitHub Issue #15](https://github.com/todoroki-godai/evolve-anything/issues/15) Part 5, 7


---

## Part 5/5: 全体まとめ — 比較表とフェーズドアプローチ

### 5パターンの比較

|  | Pattern 1<br>Task Exec | Pattern 2<br>Eureka進化 | Pattern 3<br>Constitutional | Pattern 4<br>Coherence | Pattern 5<br>Telemetry |
|---|---|---|---|---|---|
| **対応する問い** | Q3: 成功する？ | Q3: 成功する？ | Q2-Q3 の間 | Q1: 整ってる？ | Q2: 効いてる？ |
| **LLM コスト** | 高 $$$ | 中 $$ | 低 $ | **ゼロ** | **ゼロ** |
| **信頼性** | ★★★★★ | ★★★★ | ★★★ | ★★★ | ★★★★ |
| **全レイヤー評価** | ○ 間接的 | △ Skill中心 | ★★★★★ | ★★★★★ | ★★★★ |
| **PJ 適応** | 要タスク作成 | 自動生成 | 原則定義 | 自動適応 | 自動適応 |
| **Cold Start** | 即使用可 | 要 accept/reject データ | 即使用可 | 即使用可 | 要データ蓄積 |
| **実装コスト** | 中（test-tasks 整備） | 高（進化ループ） | 中（原則抽出） | **低（audit 拡張）** | **低（hooks 活用）** |
| **着想元** | SWE-bench, Anthropic Evals | Eureka, CARD, DSPy | Constitutional AI, RLAIF | 多目的最適化, 静的解析 | DORA, DX Core 4, METR |

### PJ 別で何が変わるか

```
                Q1:整合性    Q2:利用率     Q3:成功の定義
────────────────────────────────────────────────────────
全 PJ 共通     Coverage     エラー減少率    ─
               Consistency  修正頻度       ─
               Completeness スキル使用率    ─
               Efficiency   セッション効率  ─
────────────────────────────────────────────────────────
evolve-anything    ─            ─             LLMコール最小化
                                          べき等性、互換性
atlas-breeaders ─           ─             ゲームバランス
                                          UX品質、物語整合
figma-to-code  ─            ─             デザイン再現度
                                          CSS精度
docs-platform  ─            ─             ドキュメント正確性
                                          自動更新安定性
sys-bots       ─            ─             応答品質、可用性
```

**Q1/Q2 は PJ 非依存**で全 PJ 共通基盤として使える。**Q3 だけが PJ 固有**で個別に育てていく。

### フェーズドアプローチ

安価な proxy 指標を先に積み上げ、たまに高コストの直接測定で校正する戦略:

```
Phase 0 (今すぐ): Pattern 4 — Coherence
───────────────────────────────────────────────
  ├─ audit の拡張として即実装可能
  ├─ LLM コストゼロ
  ├─ Coverage / Consistency / Completeness / Efficiency の4軸
  └─ 「環境として最低限整っているか」のベースライン
      全 PJ で共通利用

Phase 1 (短期): Pattern 5 — Telemetry + Utilization
───────────────────────────────────────────────
  ├─ hooks が既にデータを集めている
  ├─ discover / audit が既にシグナルの一部を使っている
  ├─ エラー減少率、修正頻度、スキル利用率、セッション効率
  ├─ Configuration Utilization: 各構成要素の実効性測定
  └─ 「環境が実際に役立っているか」の客観指標
      全 PJ で共通利用

Phase 2 (中期): Pattern 3 — Constitutional
───────────────────────────────────────────────
  ├─ CLAUDE.md から原則を半自動抽出
  ├─ 全レイヤーを原則に照らして LLM 評価
  ├─ PJ ごとの「憲法」を定義
  └─ 「PJ の価値観に沿っているか」の質的評価
      PJ 固有（ただし枠組みは共通）

Phase 3 (長期): Pattern 1 + 2 — Task Exec + Eureka
───────────────────────────────────────────────
  ├─ PJ 固有の test-tasks.yaml を整備
  ├─ Eureka 式で fitness 関数自体を進化
  ├─ pass^k で信頼性を測定
  └─ 「環境が PJ を成功に導くか」の直接測定
      完全 PJ 固有
```

### 信頼性の階層

```
高  │  Pattern 1: 実際にタスクを成功させた（pass^k で再現性も確認）
    │  Pattern 5: エラーが減り、修正が減った（テレメトリ実績）
    │  Pattern 3: PJ 原則に沿っていると判定（LLM Judge）
    │  Pattern 2: 進化した fitness が高スコア（accept/reject と相関）
低  │  Pattern 4: 構造的に整合している（静的分析）
    │  (現状):   テキストにキーワードがある（plugin.py）
```

### 次のアクション候補

- [ ] Phase 0: audit に Coherence スコアを追加（Coverage/Consistency/Completeness/Efficiency）
- [ ] Phase 1: 既存 hooks データから測定可能な Telemetry 指標の洗い出し
- [ ] Phase 1: Configuration Utilization の測定プロトタイプ
- [ ] Phase 2: CLAUDE.md からの原則自動抽出の PoC
- [ ] Phase 3: evolve-anything 用の test-tasks.yaml 雛形作成
- [ ] Phase 3: Eureka 式 fitness 進化ループの設計
- [ ] #14 のスコープを Phase 3 に位置づけるか、本 issue に統合するか判断

---

### 参考文献一覧

| 文献 | 出典 | 主要知見 |
|---|---|---|
| Eureka | [ICLR 2024](https://arxiv.org/abs/2310.12931) | LLM が reward 関数をコード生成、83% で人間超え |
| Demystifying Evals | [Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) | 結果を評価、20-50タスク、multi-trial |
| CLEAR | [arXiv 2511.14136](https://arxiv.org/html/2511.14136v1) | 5軸評価、pass^k 信頼性 |
| Beyond Task Completion | [arXiv 2512.12791](https://arxiv.org/html/2512.12791v1) | 4柱評価、Tool orchestration が最大失敗要因 |
| Rubric Is All You Need | [ACM ICER 2025](https://dl.acm.org/doi/10.1145/3702652.3744220) | タスク固有ルーブリック >> 汎用ルーブリック |
| METR Study | [METR 2025](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) | 主観 +24% vs 実測 -19% |
| DORA 2025 | [DORA](https://dora.dev/research/2025/dora-report/) | AI 生産性パラドックス |
| Agent-as-a-Judge | [arXiv 2508.02994](https://arxiv.org/html/2508.02994v1) | トラジェクトリ全体評価 |
| DSPy MIPROv2 | [DSPy](https://dspy.ai/learn/optimization/optimizers/) | メトリクス自体を最適化可能 |
| Self-Evolving Agents | [OpenAI Cookbook](https://developers.openai.com/cookbook/examples/partners/self_evolving_agents/autonomous_agent_retraining/) | GEPA、自己修復ワークフロー |
| Constitutional AI | [Anthropic](https://www.anthropic.com/research/constitutional-ai-harmlessness-from-ai-feedback) | 原則ベース評価、<$0.01/判定 |
| LLM-Rubric | [Microsoft, ACL 2024](https://arxiv.org/abs/2501.00274) | 多次元ルーブリック、キャリブレーション |
| CARD | [arXiv 2410.14660](https://arxiv.org/abs/2410.14660) | Coder+Evaluator で reward 設計 |
| DX Core 4 | [DX](https://getdx.com/research/measuring-developer-productivity-with-the-dx-core-4/) | Speed/Effectiveness/Quality/Impact |
| PSL-MORL | [arXiv 2501.06773](https://arxiv.org/html/2501.06773v1) | パレートフロント学習、多目的 RL |
| Bloom | [Anthropic 2025](https://alignment.anthropic.com/2025/bloom-auto-evals/) | 行動評価の自動化 |
| MemOS | [MemTensor 2025](https://statics.memtensor.com.cn/files/MemOS_0707.pdf) | メモリ層間相互作用の評価 |

---

## Part 7: 全10パターン俯瞰 — 比較表とポジショニングマップ

### 追加5パターンの比較

|  | P6 Elo Arena | P7 Chaos | P8 Kirkpatrick | P9 KG Quality | P10 Implicit Reward |
|---|---|---|---|---|---|
| **測定対象** | 相対的な優劣 | 耐障害性・堅牢性 | Rule の実効性 | 環境の構造的豊かさ | 行動からの報酬学習 |
| **コスト** | 中 $$ | 中 $$ | 低 $ | ゼロ | ゼロ |
| **新しさ** | 高 | 高 | 中 | 中 | 高 |
| **既存資産の活用** | genetic-optimizer に統合 | ablation の体系化 | telemetry + reflect | audit の拡張 | hooks データ活用 |

### 全10パターンのポジショニングマップ

```
          静的分析 ◀─────────────────────────────▶ 動的評価
          (テキスト)                                (実行結果)

  安い    P4 Coherence     P9 KG Quality     P5 Telemetry
  │       P8 Kirkpatrick                     P10 Implicit Reward
  │                        P3 Constitutional
  │       P7 Chaos                           P6 Elo Arena
  │                        P2 Eureka進化
  高い                                       P1 Task Execution

  ←──── 構造を見る ────→←── 原則に照らす ──→←── 結果を見る ──→
```

### 評価の問いへのマッピング

| 評価の問い | 対応パターン |
|---|---|
| **Q1: 整ってる？**（構造品質） | P4 Coherence, P9 KG Quality |
| **Q1.5: 壊れにくい？**（堅牢性） | P7 Chaos Engineering |
| **Q2: 効いてる？**（実効性） | P5 Telemetry, P8 Kirkpatrick, P10 Implicit Reward |
| **Q2.5: どっちが良い？**（比較） | P6 Elo Arena |
| **Q3: 成功する？**（PJ成果） | P1 Task Exec, P2 Eureka, P3 Constitutional |

### P1-P5 との関係性

P6-P10 は独立した手法ではなく、P1-P5 を**補完・強化**する関係にある:

- **P6 Elo** → P2 Eureka の selection メカニズムとして統合可能
- **P7 Chaos** → P4 Coherence の「壊れた場合」版（静的→動的の拡張）
- **P8 Kirkpatrick** → P5 Telemetry の「原因帰属」レイヤー（何が効いているか）
- **P9 KG Quality** → P4 Coherence の精密版（構造メトリクスの体系化）
- **P10 Implicit Reward** → P2 Eureka の fitness 自動生成のデータソース

### 推奨フェーズドアプローチ（更新版）

Part 5 で提案した4フェーズに P6-P10 を組み込む:

| Phase | 内容 | P1-P5 | P6-P10 |
|---|---|---|---|
| **Phase 0** | 構造の整合性チェック | P4 Coherence | + P9 KG Quality |
| **Phase 1** | テレメトリ駆動の効果測定 | P5 Telemetry | + P8 Kirkpatrick L1-L3, P10 Implicit |
| **Phase 2** | 原則ベースの自動評価 | P3 Constitutional | + P7 Chaos（堅牢性テスト） |
| **Phase 3** | タスク実行による成果測定 | P1 Task Exec, P2 Eureka | + P6 Elo（選択メカニズム） |

Phase 0-1 は **LLM コストゼロ**で開始可能。Phase 2-3 で段階的に LLM 評価を導入する。