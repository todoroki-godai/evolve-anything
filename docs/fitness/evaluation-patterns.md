# Claude Code 環境全体の Fitness 評価 — 調査結果と10パターン提案

> Source: [GitHub Issue #15](https://github.com/todoroki-godai/evolve-anything/issues/15)

---

## Part 1/5: 全体像 — 問題の構造と「評価の3つの問い」

### 現状の評価: Skill テキストのキーワードマッチ

現在の fitness 評価パイプラインは以下の構造:

```
┌──────────────┐   stdin    ┌──────────────┐   stdout
│  SKILL.md    │──────────▶│  plugin.py   │──────────▶ 0.0〜1.0
│  (テキスト)   │           │ (キーワード)  │
└──────────────┘           └──────────────┘
```

`plugin.py` は4軸（LLM最小化 0.3 / べき等性 0.3 / ユーザー承認 0.2 / 互換性 0.2）でキーワードの有無を加減点し、アンチパターンにペナルティを課す。テキストに「べき等」と書いてあれば +0.08、「claude -p」と書いてあれば -0.1 という仕組み。

**問題**: テキストに良い言葉が並んでいても、そのスキルが実際に PJ を成功に導くかは分からない。

### 評価対象の拡大: 全6レイヤー

Claude Code の拡張環境は6つのレイヤーで構成される:

| レイヤー | 役割 | 例 |
|---------|------|---|
| **CLAUDE.md** | PJ 固有の設定・ルール | コーディング規約、アーキテクチャ方針 |
| **Rules** | 常時適用される行動制約 | 「テストは必ず書く」「日本語でコミット」 |
| **Skills** | 再利用可能なプロンプト集 | /commit, /review |
| **Memory** | セッションを跨ぐ記憶 | PJ 構造、過去の判断根拠 |
| **Hooks** | イベント駆動の自動アクション | ツール使用後にログ記録 |
| **Subagents** | 専門特化した独立 AI | コードレビュー専門、採点専門 |

現状の fitness は **Skills のみ**を、しかも**テキスト表面**だけで評価している。他5レイヤーは評価対象外。

### 本当に測りたいもの: 「環境が PJ を成功に導く力」

```
┌──────────────────────────────────────────────────────────┐
│  Claude Code 環境全体                                     │
│                                                          │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐       │
│  │CLAUDE.md│ │  Rules  │ │ Skills  │ │ Memory  │       │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘       │
│       │           │           │           │             │
│  ┌────┴────┐ ┌────┴────┐                                │
│  │  Hooks  │ │Subagents│                                │
│  └─────────┘ └─────────┘                                │
│                                                          │
│  これら全部が「協調して」PJ を成功に導けているか？         │
└──────────────────────────────────────────────────────────┘
```

しかも PJ ごとに「成功」の定義が全く異なる:

| PJ | 「成功」の定義 |
|---|---|
| rl-anything | LLMコール最小化、べき等性保証、既存インターフェース互換 |
| atlas-breeaders | ゲームバランス維持、React Native パフォーマンス、物語整合性 |
| figma-to-code | デザイン再現度 95%+、CSS 精度、レスポンシブ対応 |
| docs-platform | ドキュメント正確性、更新の自動化安定性 |
| sys-bots | ボット応答品質、マルチテナント安全性、可用性 |

### 評価の3つの問い

調査の結果、環境評価は3つの問いに階層化できることが分かった:

```
信頼性↑  │  Q3. PJ を成功に導けるか？
コスト↑  │      → タスク実行 + 成否判定（直接測定）
         │
         │  Q2. 実際に使われて効いているか？
         │      → テレメトリ + 利用率 + ablation（間接測定）
         │
         │  Q1. 構造的に整っているか？
信頼性↓  │      → 層間整合性 + カバレッジ（静的分析）
コスト↓  │
         │  (現状): テキストにキーワードがある
```

**Q1 と Q2 は PJ 非依存**（全 PJ 共通で使える基盤）。**Q3 だけが PJ 固有**。これがフェーズドアプローチの根拠になる。

以降のコメントで、この3つの問いにどうアプローチするかを、調査知見と5つのパターンで詳述する。

---

## Part 2/5: 調査知見 — 最新研究 10+ 文献のサマリ

2024-2026 年の AI エージェント評価、報酬設計、開発者生産性の研究を幅広く調査した。以下に主要な知見をまとめる。

---

### 2-1. Eureka: LLM による報酬関数の自動生成と進化 (ICLR 2024)

**出典**: [Eureka: Human-Level Reward Design via Coding Large Language Models](https://arxiv.org/abs/2310.12931)

LLM（GPT-4）が環境のソースコードとタスク記述を読み、**報酬関数をコードとして zero-shot 生成**する。生成した複数の候補を実際に RL で評価し、「Reward Reflection」（学習統計のサマリ）をフィードバックして進化的に改善する。

**主要な成果**:
- 29 環境・10 種ロボットで、83% のタスクで人間専門家を上回る
- タスク固有のプロンプトテンプレートなしで動作
- 平均 52% の正規化改善

**rl-anything への示唆**: 現在の `generate-fitness` が生成する keyword マッチの fitness 関数を、PJ コンテキストを読んでコード生成 → accept/reject データで校正 → 進化、というループに発展させられる。Eureka の「Reward Reflection」パターンは `evolve-fitness` の自然な拡張。

---

### 2-2. Anthropic "Demystifying Evals for AI Agents" (2025)

**出典**: [Anthropic Engineering Blog](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

エージェント評価の実践ガイド。数十の実デプロイメントから得た知見を体系化。

**核心的な原則**:

1. **結果を評価せよ、経路は評価するな**: エージェントは設計者が予想しない有効なアプローチを見つける。ツール呼び出しの順序ではなく、最終的な成果物を評価すべき
2. **20-50 タスクから始めよ**: 実際の失敗事例からタスクを作成。数百のタスクは不要
3. **複数トライアルを実行せよ**: モデル出力はラン間で変動する。1回の成功は信頼性を保証しない
4. **次元ごとに独立した grader を使え**: 1つの LLM 判定で全てを評価せず、各評価軸に専用の grader を設ける
5. **Transcript（トラジェクトリ）全体を記録せよ**: 出力だけでなく、ツール呼び出し・中間結果・推論過程を含む完全な記録

**rl-anything への示唆**: 現在の evaluate() は CoT の4軸（clarity/completeness/structure/practicality）を1回の LLM 呼び出しで一括評価している。これを軸ごとに独立した grader に分離し、各 grader に専用のルーブリックを設けるべき。

---

### 2-3. CLEAR Framework: 多次元エージェント評価 (arXiv 2511.14136)

**出典**: [Beyond Accuracy: A Multi-Dimensional Framework for Evaluating Enterprise Agentic AI Systems](https://arxiv.org/html/2511.14136v1)

エンタープライズ向けエージェントの5軸評価フレームワーク:

| 軸 | 意味 | 測定方法 |
|---|---|---|
| **C**ost | 経済効率 | API トークン消費、推論コスト |
| **L**atency | 応答速度 | タスク完了時間 |
| **E**fficacy | 有効性 | タスク成功率 |
| **A**ssurance | 安全性 | ポリシー遵守率 |
| **R**eliability | 信頼性 | pass^k（全回成功率） |

**重要な知見: pass@k vs pass^k**

```
pass@k: k回中1回でも成功すれば OK  → 能力の上限を測る
pass^k: k回全部成功すれば OK       → 信頼性の下限を測る

実例: あるエージェントの性能
  単発成功率: 60%
  8回連続成功率: 25%  ← 実運用での信頼性はこちら
```

**Cost-Normalized Accuracy (CNA)**: 高精度だが高コストなエージェントと、そこそこの精度だが安価なエージェントを公平に比較する指標。

**rl-anything への示唆**: 環境の fitness を「1回うまくいった」で判断せず、複数回の再現性（pass^k）で評価すべき。また、LLM コール数（Cost 軸）も fitness に組み込むべき。

---

### 2-4. "Beyond Task Completion" — 4柱エージェント評価 (arXiv 2512.12791)

**出典**: [Beyond Task Completion: An Assessment Framework for Evaluating Agentic AI Systems](https://arxiv.org/html/2512.12791v1)

タスク完了だけでなく、エージェントシステムの4つの柱を評価:

| 柱 | 評価対象 | 失敗率（複雑シナリオ） |
|---|---|---|
| **LLM** | 推論品質、指示追従 | 中 |
| **Memory** | コンテキスト保持、長期記憶 | 複雑度に比例して増加 |
| **Tools** | ツール選択・実行の適切さ | **最高**（最大の失敗要因） |
| **Environment** | ポリシー遵守、安全性 | マルチエージェント時のみ |

**重要な発見**: Tool orchestration（正しいツールを正しい順序で使う）が最大の失敗要因。静的評価では検出できない。

**rl-anything への示唆**: Rules/Skills の設定が「Claude に正しいツールを正しい順序で使わせるか」を評価すべき。これは静的テキスト分析では不可能で、実タスク実行（Pattern 1）が必要な理由。

---

### 2-5. "Rubric Is All You Need" — タスク固有ルーブリック (ACM ICER 2025)

**出典**: [Rubric Is All You Need: Improving LLM-based Code Evaluation With Question-Specific Rubrics](https://dl.acm.org/doi/10.1145/3702652.3744220)

LLM によるコード評価で、**汎用ルーブリック（Question-Agnostic）vs タスク固有ルーブリック（Question-Specific）** を比較。

**結果**: タスク固有ルーブリックが汎用ルーブリックを大幅に上回る。Amazon Nova の rubric-based LLM judge も同様に、プロンプトごとに動的にルーブリックを生成するアプローチを採用。

**rl-anything への示唆**: 現在の CoT 評価は汎用ルーブリック（clarity/completeness/structure/practicality）。PJ ごとにルーブリックを動的生成する Constitutional 評価（Pattern 3）の根拠。

---

### 2-6. METR 開発者生産性研究 (2025)

**出典**: [Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)

**衝撃的な発見**: 経験豊富な OSS 開発者が Cursor Pro + Claude 3.5/3.7 Sonnet を使用した結果:
- **主観的予測**: +24% の生産性向上
- **実測**: -19% の生産性低下

**原因分析**: AI 生成コードのデバッグ・レビュー・修正に予想以上の時間がかかる。67% の開発者が AI 生成コードのデバッグに手書きより多くの時間を費やしている（Harness 2025）。

**rl-anything への示唆**: 主観的な「良さそう」は信頼できない。テレメトリによる客観測定（Pattern 5）が不可欠。「このスキルを使ったらエラーが減ったか」「修正頻度が下がったか」を実データで測るべき。

---

### 2-7. DORA 2025: AI 生産性パラドックス

**出典**: [DORA State of AI-assisted Software Development 2025](https://dora.dev/research/2025/dora-report/)

**AI 生産性パラドックス**: AI ツールにより個人の出力は向上（21% 多いタスク、98% 多い PR マージ）するが、**組織全体のデリバリーメトリクスは横ばい**。

7つの成功要因:
1. 明確な AI 活用方針の共有
2. 健全なデータエコシステム
3. AI がアクセス可能な内部データ
4. 強固なバージョン管理
5. 小さなバッチで作業
6. ユーザー中心のフォーカス
7. **高品質な内部プラットフォーム** ← これが「環境の品質」に直結

**rl-anything への示唆**: 「良い環境」= 個別 Skill の品質ではなく、プラットフォーム全体の基盤品質。Coherence（Pattern 4）と Telemetry（Pattern 5）が基盤品質の測定に対応。

---

### 2-8. Agent-as-a-Judge: トラジェクトリ評価 (arXiv 2508.02994)

**出典**: [Agent-as-a-Judge](https://arxiv.org/html/2508.02994v1)

LLM-as-Judge を発展させ、**エージェントがエージェントを評価**する。最終出力だけでなく、ツール呼び出し・状態変更・中間推論を含む**トラジェクトリ全体**を評価対象にする。

マルチエージェント評価では、ドメイン専門家・批評家・擁護者など異なる役割のエージェントが協調して評価する。AGENTIF ベンチマークでは GPT-4o による自動評価が人間のアノテーションと 94% 一致。

**rl-anything への示唆**: rl-scorer の発展形として、evaluation 時に「Claude Code がどういう手順でタスクを解いたか」のトラジェクトリも評価に含める。

---

### 2-9. DSPy MIPROv2: 評価メトリクス自体の最適化

**出典**: [DSPy Optimizers](https://dspy.ai/learn/optimization/optimizers/)

DSPy はプロンプト最適化を「コンパイル」として扱うフレームワーク。**評価メトリクスもまた DSPy プログラムとして記述し、最適化可能**にする。

MIPROv2 オプティマイザ:
- 指示文 + few-shot 例を各ステップで生成
- ベイジアン最適化で指示文/デモの空間を探索
- データ対応・デモ対応の指示生成

**rl-anything への示唆**: fitness 関数そのものを最適化可能なプログラムとして扱う。`evolve-fitness` の方向性と一致するが、DSPy のようにベイジアン最適化を適用できれば、ランダムな進化より効率的。

---

### 2-10. OpenAI Self-Evolving Agents Cookbook + GEPA

**出典**: [Self-Evolving Agents Cookbook](https://developers.openai.com/cookbook/examples/partners/self_evolving_agents/autonomous_agent_retraining/)

再トレーニングループの実践ガイド:
1. エージェントの弱点を診断
2. 測定可能なフィードバックシグナルを計装
3. 最適化戦略を比較（手動 ↔ 完全自動）
4. 人間レビュー + LLM-as-judge + 反復的プロンプト改善を組み合わせた自己修復ワークフロー

GEPA（Genetic-Pareto）法: エージェントのトラジェクトリをサンプリング → 自然言語で振り返り → プロンプト改訂を提案 → 反復的フィードバックループで進化。

**rl-anything への示唆**: rl-anything が既にやっている「observe → discover → optimize」のパイプラインは GEPA のパターンそのもの。足りないのは「環境全体のフィードバックシグナル」と「進化対象を Skill 以外に広げること」。

---

### 2-11. その他の重要な知見

| 出典 | 知見 |
|---|---|
| [Constitutional AI / RLAIF](https://www.anthropic.com/research/constitutional-ai-harmlessness-from-ai-feedback) | 原則ベースの AI 評価。人間ラベル不要、<$0.01/判定。Anthropic の 2026 年 constitution は 23,000 語 |
| [LLM-Rubric (Microsoft, ACL 2024)](https://arxiv.org/abs/2501.00274) | 多次元ルーブリック + 確率分布で人間満足度を予測。キャリブレーション後の RMS 誤差が未キャリブレーションの 2 倍改善 |
| [CARD Framework](https://arxiv.org/abs/2410.14660) | Coder + Evaluator の2役で報酬関数を設計。Trajectory Preference Evaluation (TPE) で報酬関数をトラジェクトリ嗜好で評価 |
| [DX Core 4](https://getdx.com/research/measuring-developer-productivity-with-the-dx-core-4/) | DORA + SPACE + DevEx を統合した4次元: Speed, Effectiveness, Quality, Business Impact |
| [MemOS (2025)](https://statics.memtensor.com.cn/files/MemOS_0707.pdf) | 3種メモリ（Plaintext / Activation / Parameter）の構成可能な基盤。メモリレイヤー間の相互作用の評価手法 |
| [Bloom (Anthropic, 2025)](https://alignment.anthropic.com/2025/bloom-auto-evals/) | 行動評価の自動化ツール。エージェントの行動パターンを自動的にテストケース化 |
| [PSL-MORL (arXiv 2501.06773)](https://arxiv.org/html/2501.06773v1) | 多目的 RL のパレートフロント学習。ハイパーネットワークで異なる重み付けのポリシーを同時に生成 |

---

## Part 3/5: 5つの評価パターン — 詳細設計

調査知見を統合し、5つの評価パターンを提案する。各パターンは「評価の3つの問い」（Q1: 整っているか / Q2: 効いているか / Q3: 成功するか）のいずれかに対応する。

---

### Pattern 1: End-to-End Task Execution（SWE-bench アプローチ）

**対応する問い**: Q3「PJ を成功に導けるか？」
**着想**: SWE-bench, Terminal-Bench, Anthropic "Demystifying Evals"

PJ 固有のタスク集を定義し、Claude Code に環境全体を読み込ませてタスクを実行。出力をルールベースで検証する。

```
評価の流れ
──────────────────────────────────────────────────────

  ┌──────────────────┐
  │  PJ固有タスク集    │  ← 手動 or 自動で作成
  │  test-tasks.yaml  │
  │                   │
  │  "APIを設計して"   │
  │  "バグを直して"    │
  │  "テスト書いて"    │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────────────────────────────┐
  │  Claude Code + 全レイヤー読み込み          │
  │  (CLAUDE.md, Rules, Skills, Memory, ...) │
  └────────┬─────────────────────────────────┘
           │  実行
           ▼
  ┌──────────────────┐
  │  タスク出力        │
  └────────┬─────────┘
           │
           ▼
  ┌──────────────────────────────────┐
  │  ルールベース検証                  │
  │  contains("GET /users") → pass   │
  │  regex(openapi: 3\.) → pass      │
  │  exit_code(pytest) → 0           │
  │  file_exists(src/api.ts) → true  │
  └────────┬─────────────────────────┘
           │
           ▼
       score = pass率 × pass^k 信頼性
```

**PJ ごとの test-tasks 例**:

| PJ | タスク例 | 検証方法 |
|---|---|---|
| rl-anything | "discover を実行して構造化結果を出力" | `json_schema` で出力形式検証 |
| atlas-breeaders | "戦闘ダメージ計算のバグ修正" | `exit_code(npx jest)` でテスト通過 |
| figma-to-code | "この Figma export から CSS を生成" | `regex` で主要プロパティの存在確認 |
| docs-platform | "API ドキュメントの自動更新" | `contains` + `no_contains("TODO")` |
| sys-bots | "Slack ボットの応答ロジック実装" | `exit_code` + `json_schema` |

**信頼性の測定（pass^k）**:

Anthropic の推奨に従い、同一タスクを k 回実行して再現性を測定:

```
pass@k = 1 - (1 - p)^k  ← k回中1回でも成功する確率（能力上限）
pass^k = p^k             ← k回全て成功する確率（信頼性下限）

例: p = 0.8 の場合
  pass@3 = 0.992  (ほぼ確実に1回は成功)
  pass^3 = 0.512  (半分の確率で全成功)
  → 見かけの成功率と実運用の信頼性に大きなギャップ
```

**強み**:
- 最も信頼性が高い（「環境が実際に成果を出すか」を直接測定）
- ルールベース検証は再現可能
- pass^k で信頼性も測定

**弱み**:
- タスク集の作成コスト大（PJ ごとに 20-50 タスク推奨）
- LLM コール多い（タスクあたり 1-2 コール）
- 実行時間が長い

---

### Pattern 2: Eureka 式 — LLM が fitness 関数自体を進化させる

**対応する問い**: Q3「PJ を成功に導けるか？」（間接的に）
**着想**: Eureka (ICLR 2024), CARD Framework, DSPy MIPROv2

LLM が PJ コンテキストを読み、**fitness 関数をコードとして自動生成**。人間の accept/reject データとの相関で校正し、進化させる。

```
自己進化ループ
──────────────────────────────────────────────────────

  ┌─────────────────────────┐
  │  PJ コンテキスト          │
  │  ├─ CLAUDE.md            │
  │  ├─ ディレクトリ構造      │
  │  ├─ 最近の git log       │
  │  └─ 過去の accept/reject │
  └────────┬────────────────┘
           │
           ▼
  ┌──────────────────────────────────────┐
  │  LLM: fitness 関数コードを N 個生成   │
  │                                      │
  │  # 候補 A                            │
  │  def evaluate(content, context):     │
  │      if "error handling" in content: │
  │          score += 0.1                │
  │      ...                              │
  │                                      │
  │  # 候補 B                            │
  │  def evaluate(content, context):     │
  │      sections = parse_headings(...)  │
  │      score = coverage(sections, ...) │
  │      ...                              │
  └────────┬─────────────────────────────┘
           │  N 候補生成
           ▼
  ┌──────────────────────────────────────┐
  │  各候補を「人間の判断」と照合         │
  │                                      │
  │  history.jsonl:                      │
  │    skill_v1 → accept                 │
  │    skill_v2 → reject                 │
  │    skill_v3 → accept                 │
  │                                      │
  │  fitness_A(v1)=0.8, A(v2)=0.3 → ✓  │
  │  fitness_B(v1)=0.5, B(v2)=0.7 → ✗  │
  │  → A は人間判断と一致、B は逆転     │
  └────────┬─────────────────────────────┘
           │  best 候補を選択
           ▼
  ┌──────────────────────────────────────┐
  │  Reward Reflection（Eureka パターン） │
  │                                      │
  │  "fitness_A は構造評価が強いが       │
  │   ゲームバランスの評価軸が欠けている │
  │   → atlas-breeaders では性能低下"    │
  │                                      │
  │  → 改善版を再生成                    │
  └──────────────────────────────────────┘
           │
           ▼  繰り返し（世代を重ねて進化）
```

**現行の `generate-fitness` + `evolve-fitness` からの発展**:

| 現状 | Eureka 式への発展 |
|---|---|
| CLAUDE.md のキーワードから手動でルール抽出 | PJ コンテキスト全体から自動生成 |
| 固定の評価軸 | PJ ごとに軸を動的生成 |
| 人間が fitness 関数を修正 | accept/reject データで自動校正 |
| 1世代のみ | Reward Reflection で反復進化 |

**強み**:
- PJ コンテキストを自動読み取りで fitness 生成
- accept/reject データで校正可能（人間判断との整合性を保証）
- 現在の generate-fitness + evolve-fitness の自然な拡張

**弱み**:
- 初期の accept/reject データが少ないと校正が不安定
- 生成された fitness 関数の品質が LLM の能力に依存
- 校正データの蓄積に時間がかかる

---

### Pattern 3: Constitutional Evaluation（原則ベース評価）

**対応する問い**: Q3「PJ 原則に沿っているか？」（Q2 と Q3 の間）
**着想**: Constitutional AI (Anthropic), RLAIF, Rubric Is All You Need (ACM ICER 2025)

PJ 固有の「憲法」（原則集）を定義し、LLM Judge が**全レイヤーを原則に照らして**採点する。

```
PJ 固有の「憲法」
──────────────────────────────────────────────────────

  rl-anything の憲法:
  ┌─────────────────────────────────────────────┐
  │  1. LLM呼び出しを最小化すべき               │
  │  2. べき等性を保証すべき                     │
  │  3. ユーザー承認なしに変更しない             │
  │  4. stdin/stdout インターフェースを維持       │
  │  5. 既存パターンと整合すべき                 │
  │  6. テレメトリを正確に記録すべき             │
  │  7. 段階的開示で情報過多を避けるべき         │
  └──────────────────────────────────────────────┘

  atlas-breeaders の憲法:
  ┌─────────────────────────────────────────────┐
  │  1. ゲームバランスを壊さない                 │
  │  2. React Native のパフォーマンスを考慮      │
  │  3. 物語の整合性を維持                       │
  │  4. プラットフォーム差異を吸収               │
  │  5. Expo のビルドパイプラインに従う           │
  │  6. アセット管理のルールに従う               │
  └──────────────────────────────────────────────┘
```

**評価プロセス**:

```
  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐
  │ CLAUDE.md │  │  Rules    │  │  Skills   │  │  Memory   │
  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘  └─────┬─────┘
        └───────────────┼───────────────┼───────────────┘
                        ▼
              ┌──────────────────────────────────┐
              │  LLM Judge × 原則数              │
              │                                  │
              │  原則1: 0.9  "LLMコール最小化 ✓" │
              │  原則2: 0.7  "一部冪等性が弱い"  │
              │  原則3: 1.0  "承認フロー完備"    │
              │  原則4: 0.8  "互換性概ね維持"    │
              │  ...                              │
              │                                  │
              │  → 加重平均 = 0.85               │
              └──────────────────────────────────┘
```

**憲法の半自動抽出**: CLAUDE.md から原則を自動抽出可能:

```python
# CLAUDE.md のセクション構造から原則を抽出
# "## ルール", "## 方針", "## 制約" 等のセクションを解析
# → 各ルール/制約を Constitutional 原則に変換
```

**強み**:
- **全レイヤーをまとめて評価可能**（CLAUDE.md も Rules も Memory も同じ枠組み）
- 人間ラベル不要（RLAIF: <$0.01/判定）
- PJ 固有の原則は CLAUDE.md から半自動抽出可能
- タスク固有ルーブリック（ACM ICER 2025）のように、PJ ごとに動的生成

**弱み**:
- LLM 採点の不安定さ（現状の CoT と同じ問題）
- 原則の粒度設計が難しい（粗すぎると無意味、細かすぎるとコスト増）
- 「原則に沿っている ≠ 実際に成功する」のギャップ

---

### Pattern 4: Multi-Layer Coherence Graph（層間整合性グラフ）

**対応する問い**: Q1「構造的に整っているか？」
**着想**: 多目的最適化, Pareto fitness, 静的解析, 既存 audit の拡張

6レイヤー間の「つながり」を評価する。LLM コストゼロ。

```
レイヤー間の「つながり」を評価する
──────────────────────────────────────────────────────

  ┌──────────┐     references?    ┌──────────┐
  │ CLAUDE.md│ ◀─────────────────▶│  Rules   │
  └────┬─────┘                    └────┬─────┘
       │ consistent?                   │ enforced?
       ▼                               ▼
  ┌──────────┐     follows?       ┌──────────┐
  │  Memory  │ ◀─────────────────▶│  Skills  │
  └────┬─────┘                    └────┬─────┘
       │ reflects?                     │ triggers?
       ▼                               ▼
  ┌──────────┐     observes?      ┌──────────┐
  │  Hooks   │ ◀─────────────────▶│Subagents │
  └──────────┘                    └──────────┘
```

**4つの評価軸**:

| 軸 | 意味 | 具体例 |
|---|---|---|
| **Coverage** | 全レイヤーが PJ の主要関心事をカバーしているか | ゲーム PJ なのにバランス関連の Rule がない → 低 |
| **Consistency** | レイヤー間に矛盾がないか | Rules で「テスト必須」だが Skills にテスト実行スキルがない → 矛盾 |
| **Completeness** | 推奨レイヤーが揃っているか | Hooks がない → observe データなし → discover/reflect が機能しない |
| **Efficiency** | 冗長な定義がないか | 同じルールが CLAUDE.md と Rules に重複 → 非効率 |

**実装イメージ**（audit の拡張として）:

```python
def evaluate_coherence(env: Environment) -> CoherenceScore:
    scores = {}
    
    # Coverage: CLAUDE.md のキーワードが Rules/Skills でカバーされているか
    topics = extract_topics(env.claude_md)
    covered = sum(1 for t in topics if t in env.rules or t in env.skills)
    scores["coverage"] = covered / len(topics)
    
    # Consistency: Rules の制約が Skills で実装されているか
    constraints = extract_constraints(env.rules)
    implemented = sum(1 for c in constraints if has_skill_for(c, env.skills))
    scores["consistency"] = implemented / len(constraints)
    
    # Completeness: 推奨レイヤーが存在するか
    required = ["claude_md", "rules", "hooks"]
    present = sum(1 for r in required if getattr(env, r))
    scores["completeness"] = present / len(required)
    
    # Efficiency: 重複検出
    duplicates = detect_duplicates(env.all_layers())
    scores["efficiency"] = 1.0 - (duplicates / total_definitions)
    
    return CoherenceScore(**scores)
```

**強み**:
- **LLM コストゼロ**（完全にルールベース）
- 即座に実装可能（audit の拡張）
- 全レイヤーの相互作用を評価
- PJ 非依存（共通基盤として全 PJ で使える）

**弱み**:
- 整合性が高い ≠ 良い環境（内容がダメでも構造は整合しうる）
- キーワードベースのトピック抽出に限界
- レイヤー間の暗黙的な依存関係は検出困難

---

### Pattern 5: Telemetry-Driven Adaptive Fitness（テレメトリ駆動）

**対応する問い**: Q2「実際に使われて効いているか？」
**着想**: DORA メトリクス, DX Core 4, DevEx 研究, METR

hooks が自動収集する使用データから、環境の「実効性」を客観的に測定する。

```
実際の使用データから「環境の良さ」を逆算する
──────────────────────────────────────────────────────

  日常の使用
  ┌──────────────────────────────────────────────┐
  │  Hooks が自動収集                              │
  │                                                │
  │  usage.jsonl   → スキル使用頻度               │
  │  errors.jsonl  → エラー発生率                 │
  │  sessions.jsonl → セッション長、ツール使用     │
  │  corrections.jsonl → ユーザー修正パターン     │
  └───────────────────┬──────────────────────────┘
                      │
                      ▼
  ┌──────────────────────────────────────────────┐
  │  シグナル抽出                                  │
  │                                                │
  │  ✓ エラー率の推移（下がってる → 環境が効いてる）│
  │  ✓ 修正頻度の推移（下がってる → 学習済み）    │
  │  ✓ スキル使用率（使われてる → 有用）          │
  │  ✓ セッション効率（短くなってる → 生産的）    │
  │  ✗ ゼロ使用スキル（無駄 → ペナルティ）       │
  │  ✗ 繰り返しエラー（Rules が効いてない）       │
  │  ✗ 高頻度修正パターン（→ Rule 追加が必要）    │
  └───────────────────┬──────────────────────────┘
                      │
                      ▼
  ┌──────────────────────────────────────────────┐
  │  Adaptive Fitness Score                        │
  │                                                │
  │  env_fitness = Σ(                             │
  │    error_reduction    * 0.30                   │
  │    + correction_reduction * 0.25               │
  │    + skill_utilization    * 0.20               │
  │    + session_efficiency   * 0.15               │
  │    - unused_artifact_penalty * 0.10            │
  │  )                                             │
  └──────────────────────────────────────────────┘
```

**DX Core 4 との対応**:

| DX Core 4 | テレメトリ指標 | 測定方法 |
|---|---|---|
| Speed | セッション効率 | sessions.jsonl: 平均ツール呼び出し数/セッション |
| Effectiveness | スキル使用率 | usage.jsonl: invoke 頻度、ゼロ使用検出 |
| Quality | エラー減少率 | errors.jsonl: 週次エラー数の推移 |
| Impact | 修正頻度減少 | corrections.jsonl: ユーザー修正回数の推移 |

**Configuration Utilization（構成利用率）の測定**:

METR の発見（主観 +24% vs 実測 -19%）を踏まえ、各構成要素が**実際に使われて効果を発揮しているか**を測定:

```
Rule "テストは必ず書く"
  → sessions.jsonl で「テストファイル作成」の頻度を確認
  → Rule 追加前後でテスト作成率が上がったか？
  → 上がった → 実効性あり
  → 変わらない → Rule が効いていない（表現改善 or 削除候補）
```

**強み**:
- **LLM コストゼロ**（既存 hooks データの集計のみ）
- 客観的な指標（主観評価の罠を回避）
- PJ 間の横断比較が可能
- 既存の hooks インフラをそのまま活用
- discover / audit が既にシグナルの一部を使っている

**弱み**:
- データ蓄積に時間がかかる（cold start 問題）
- 「使われてない ≠ 悪い」の場合がある（低頻度だが重要なスキル）
- 因果関係の特定が難しい（エラー減少は環境改善のおかげ？それとも他の要因？）
- 新しい PJ では蓄積がゼロ

---

## Part 4/5: 追加知見 — Configuration Utilization / Ablation / pass^k

調査から得た、5パターンを横断する重要な補足知見。

---

### 4-1. Configuration Utilization（構成利用率）

**出典**: [Beyond Task Completion (arXiv 2512.12791)](https://arxiv.org/html/2512.12791v1), Claude Code Best Practices

「良い環境」≠「良いコンテンツがある」。**「Claude が実際にそれを使って成果を出しているか」**を測る軸。

```
従来の発想:
  "この Rule はよく書けている" → スコア高い

本来の発想:
  "この Rule のおかげで Claude の出力が改善された" → スコア高い
  "この Rule は書いてあるが Claude が無視している" → スコア = 0
```

**具体例**:

| 構成要素 | テキスト品質 | 実効性 |
|---|---|---|
| Rule: "テストは必ず書く" | 明確、簡潔 → 高 | Claude がテスト書かずにコミット → **0** |
| Skill: /commit | 適切な構造 → 高 | 週50回使用、エラー0 → **高** |
| Memory: "org は todoroki-godai" | 正確 → 高 | push 時に毎回参照 → **高** |
| Rule: "SOLID原則に従う" | 明確 → 高 | このPJにクラスがない → **無意味** |

**測定方法**: Telemetry（Pattern 5）のデータから算出可能:
- usage.jsonl で各スキルの invoke 頻度
- sessions.jsonl で Rule に関連するパターンの出現頻度
- corrections.jsonl で「Rule に反する修正」の頻度

---

### 4-2. Ablation-Based Fitness（除去テスト）

**出典**: Claude Code Best Practices — "For each line, ask: Would removing this cause Claude to make mistakes?"

各構成要素の**個別貢献度**を測定する手法。Leave-One-Out（LOO）テスト:

```
  ベースライン: 全レイヤーあり → タスク実行 → score = 0.85
  
  Rule A を除去 → タスク実行 → score = 0.60  → ΔA = -0.25 (重要！)
  Rule B を除去 → タスク実行 → score = 0.84  → ΔB = -0.01 (死に体)
  Rule C を除去 → タスク実行 → score = 0.90  → ΔC = +0.05 (有害！)
  
  結論:
    Rule A: 必須（keep）
    Rule B: 効果なし（prune 候補）
    Rule C: 有害（remove）
```

**コスト問題**: 全構成要素に LOO を実行すると N+1 回のタスク実行が必要。

**軽量版**: Telemetry データで代替可能:
- ゼロ使用のスキル → ablation なしで prune 候補
- 高頻度修正パターン → 関連 Rule の ablation を優先実行
- 新規追加した Rule → 追加前後のエラー率比較で効果測定

**既存の prune スキルとの関係**: prune が既にゼロ使用検出・類似マージを行っている。Ablation はその「本当に必要か？」の判定を強化する。

---

### 4-3. pass^k — 信頼性の測定

**出典**: [CLEAR Framework (arXiv 2511.14136)](https://arxiv.org/html/2511.14136v1)

単発の成功ではなく**再現性**を評価する。

```
pass@k と pass^k の違い
──────────────────────────────────────────────────────

  p = 0.7（単発成功率 70%）の場合:

  k=1:  pass@1 = 0.70    pass^1 = 0.70   (同じ)
  k=3:  pass@3 = 0.97    pass^3 = 0.34   (3回とも成功は34%)
  k=5:  pass@5 = 0.998   pass^5 = 0.17   (5回とも成功は17%)
  k=8:  pass@8 = 0.9999  pass^8 = 0.06   (8回とも成功は6%)

  → pass@k は「能力がある」ことを示すが
    pass^k は「信頼して任せられる」ことを示す
```

**環境評価への応用**:

良い環境 = **pass^k が高い環境**（安定して成果を出せる）

```
環境 A: 高機能だが不安定
  → p = 0.9, pass^3 = 0.73, pass^5 = 0.59

環境 B: シンプルだが安定
  → p = 0.8, pass^3 = 0.51, pass^5 = 0.33

環境 C: 最適化済み（Pattern 1-5 適用後）
  → p = 0.95, pass^3 = 0.86, pass^5 = 0.77  ← 目標
```

**Anthropic の推奨**: 最低 3 回のトライアルで結果を平均化。重要なタスクは 5-8 回。

---

### 4-4. 多次元評価 vs 単一スコア

**出典**: [PSL-MORL (arXiv 2501.06773)](https://arxiv.org/html/2501.06773v1), [LLM-Rubric (Microsoft)](https://arxiv.org/abs/2501.00274)

単一の fitness スコア（0.0-1.0）に潰すのではなく、**多次元のまま管理**する方が情報量が多い。

```
単一スコア（現状）:
  fitness = 0.73  ← 何が良くて何が悪いか分からない

多次元スコア（提案）:
  ┌─────────────────────────────────┐
  │  coherence:    0.90  ← 構造OK   │
  │  utilization:  0.65  ← 使われてない物がある │
  │  execution:    0.80  ← タスク成功率高い │
  │  reliability:  0.55  ← 再現性に課題 │
  │  cost:         0.85  ← LLMコール効率的 │
  └─────────────────────────────────┘
```

**パレート最適化**: 複数の次元で同時に最適化する場合、単純な加重平均ではなくパレートフロントを維持する方が、「何かを犠牲にして何かを改善する」トレードオフを可視化できる。

ただし、実装の第一段階では加重平均で十分。パレート最適化は Phase 3 以降で検討。

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
rl-anything    ─            ─             LLMコール最小化
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
- [ ] Phase 3: rl-anything 用の test-tasks.yaml 雛形作成
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

## Part 6: 追加5パターン（P6-P10）— 異なる角度からの評価手法

Part 3 で提案した P1-P5 に加え、**全く異なる分野**から着想した追加5パターンを提案する。

> **全体像**: P1-P5 が「何を測るか」を軸にしたのに対し、P6-P10 は「どう測るか」の方法論に焦点を当てる。競技レーティング、障害注入、教育学、知識工学、暗黙的フィードバックという5つの異なる学問分野から手法を移植する。

---

### Pattern 6: Elo Arena — 構成同士の対戦ランキング

**着想**: DEEVO (arXiv 2506.00178), Chatbot Arena (LMSYS)
**分野**: 競技レーティング × 進化的最適化

#### コンセプト

```
「絶対スコア」を捨てて「どっちが勝つか」で評価する

┌───────────────┐          ┌───────────────┐
│  Config A      │          │  Config B      │
│  (Rules v1.2) │          │  (Rules v1.3) │
└───────┬───────┘          └───────┬───────┘
        │                          │
        ▼          同一タスク        ▼
┌───────────────┐          ┌───────────────┐
│  Output A      │   VS    │  Output B      │
└───────┬───────┘          └───────┬───────┘
        │                          │
        └──────────┬───────────────┘
                   ▼
        ┌──────────────────┐
        │  LLM Judge:       │
        │  "B の方が良い"   │
        └────────┬─────────┘
                 │
                 ▼
        ┌──────────────────┐
        │  Elo 更新:        │
        │  A: 1500 → 1485  │
        │  B: 1500 → 1515  │
        └──────────────────┘
```

#### なぜこれが重要か

- 現在の fitness は「0.73」のような絶対スコア → **基準が曖昧**
- Elo は「A と B どっちが良い？」の相対評価 → **人間の判断に近い**
- genetic-prompt-optimizer の selection フェーズに直接組み込める
- accept/reject の人間判断も Elo 更新に反映可能

#### DEEVO のアプローチ

「Tournament of Prompts」— プロンプト同士を対戦させ、マルチエージェント議論で勝敗を決め、Elo でランキング。**ground truth なしで**最適化できる。

#### 既存パイプラインとの統合

GeneticOptimizer の `evaluate()` を Elo ベースの pairwise comparison に置き換え。各世代でトーナメントを実行し、Elo 上位を次世代の親にする。

---

### Pattern 7: Chaos Engineering — 環境の耐障害性テスト

**着想**: Netflix Chaos Monkey (2011), Chaos Engineering 原則
**分野**: SRE × 障害注入テスト

#### コンセプト

```
「うまくいく条件」ではなく「壊れにくさ」を測る

Steady State 定義:
┌─────────────────────────────┐
│  タスク成功率 > 80%          │
│  ユーザー修正率 < 10%        │
│  平均ターン数 < 15           │
└──────────────┬──────────────┘
               │
               ▼  障害注入
┌──────────────────────────────────────────────┐
│                                              │
│  🔥 Rule を1つランダムに無効化               │
│  🔥 Memory を空にする                        │
│  🔥 矛盾する指示を追加                       │
│  🔥 曖昧なタスクを投入                       │
│  🔥 巨大ファイルを渡す                       │
│  🔥 存在しないスキルを参照させる              │
│                                              │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  Steady State が維持されたか？                │
│                                              │
│  ✓ Rule 除去しても成功率 75% → 堅牢          │
│  ✗ Memory 消去で成功率 30% → Memory 依存大   │
│  ✗ 矛盾指示で完全停止 → 脆弱                 │
└──────────────────────────────────────────────┘
```

#### ゲームバランステストとの共通点

ゲームデザイナーは「1つの戦略が支配的になっていないか」をテストする。同様に「1つのレイヤーが消えたら全体が崩壊するか」をテストする。

#### Red Teaming との統合

[Learning-Based Automated Red-Teaming (arXiv 2512.20677)](https://arxiv.org/abs/2512.20677) は6カテゴリの脅威を自動探索し、手動テストの 3.9 倍の脆弱性を発見する。これを構成の堅牢性テストに応用:

```
堅牢性スコア = Σ(障害シナリオ i の生存率) / シナリオ数

PJ 固有の障害シナリオ:
  rl-anything:     "LLMコール制限を超えた場合に graceful degradation するか"
  atlas-breeaders: "ゲームバランスパラメータの極端な値でクラッシュしないか"
  figma-to-code:   "不正な Figma export を処理できるか"
```

---

### Pattern 8: Kirkpatrick 4段階評価 — 教育学からの移植

**着想**: Kirkpatrick (1959), カリキュラム評価
**分野**: 教育学 × 組織学習

#### コンセプト

```
「教材が良いか」ではなく「学習者が変わったか」を測る
→ 「環境が良いか」ではなく「Claude の行動が変わったか」

Level 1: Reaction（反応）
┌─────────────────────────────────────────────────┐
│  Claude は Rule に従っているか？                  │
│  測定: Rule 遵守率 = Rule に沿った行動 / 全行動  │
│  低い → Rule の表現が曖昧 or 非現実的            │
└─────────────────────────────────────────────────┘

Level 2: Learning（学習）
┌─────────────────────────────────────────────────┐
│  Rule が適用されるべき場面で正しい出力をするか？  │
│  測定: Rule 関連タスクの正答率                    │
│  低い → Rule の内容が不十分 or 例が足りない      │
└─────────────────────────────────────────────────┘

Level 3: Behavior（行動変容）
┌─────────────────────────────────────────────────┐
│  Rule 追加前後で行動パターンが変わったか？        │
│  測定: corrections.jsonl の頻度推移              │
│  変わらない → Rule が効いていない                │
└─────────────────────────────────────────────────┘

Level 4: Results（成果）
┌─────────────────────────────────────────────────┐
│  環境全体で PJ の成果指標が改善したか？           │
│  測定: バグ減少、タスク完了時間短縮、etc.        │
│  改善なし → 環境の方向性自体を見直す             │
└─────────────────────────────────────────────────┘
```

#### なぜこれが重要か

Pattern 5 (Telemetry) が Level 3-4 をカバーし、Pattern 4 (Coherence) が構造品質を見るが、**Level 1-2（Rule を Claude が本当に理解し従っているか）**を測る手法がない。

#### エキスパートシステム評価との合流

1980-90年代のエキスパートシステム研究 (Preece 1990) で発見された4つのルールベース欠陥:
- **冗長**: 同条件・同結論の重複ルール
- **矛盾**: 同条件だが矛盾する結論のルール
- **循環**: A→B→A のルール連鎖
- **欠損**: 参照されるが生成されない条件

これは **Pattern 4 (Coherence)** の精密版として統合可能。

#### Argyris のダブルループ学習との接続

組織学習論では「ルールに従ったか」（シングルループ）だけでなく「そもそもルールが正しいか」（ダブルループ）を評価する。

```
シングルループ: Rule → 行動 → 結果OK？ → Rule に従い続ける
ダブルループ:   Rule → 行動 → 結果NG → Rule 自体を見直す ← これが reflect/evolve
```

---

### Pattern 9: Knowledge Graph Quality — 環境のオントロジー評価

**着想**: OntoQA (Tartir 2007), ナレッジベース品質研究
**分野**: 知識工学 × オントロジー評価

#### コンセプト

```
環境を「知識グラフ」として捉え、構造的な豊かさを測定する

┌──────────────────────────────────────────────────────┐
│                   環境の知識グラフ                     │
│                                                      │
│  [CLAUDE.md] ──defines──▶ [coding-style]             │
│       │                        │                     │
│       │                  enforced-by                  │
│       │                        │                     │
│       ├──references──▶ [Rule: テスト必須]             │
│       │                        │                     │
│       │                  implemented-by               │
│       │                        │                     │
│       └──mentions──▶ [Skill: /commit] ──triggers──▶  │
│                          │          [Hook: observe]   │
│                     uses-memory                       │
│                          │                            │
│                     [Memory: PJ構造]                  │
└──────────────────────────────────────────────────────┘
```

#### OntoQA の3つの構造メトリクス

| メトリクス | 定義 | 環境への適用 |
|---|---|---|
| **Relationship Richness** | 非階層関係 / 全関係 | Skill 間に triggers, conflicts, data-flow 等の多様な関係があるか？全部 "depends-on" だけだと貧弱 |
| **Attribute Richness** | 平均属性数 / クラス | 各 Skill の設定パラメータ数。0 = 硬直的、多い = 柔軟だが複雑 |
| **Population Completeness** | 実データあり / 定義済みスロット | CLAUDE.md で宣言されたスキルに全て実体があるか？スタブだらけ = 不完全 |

#### ソフトウェア品質メトリクスとの合流

McCabe の循環的複雑度を Skill のロジック分岐に、Constantine の結合度・凝集度を Skill 間の依存関係に適用:

```python
# 各 Skill の構造品質
instability = efferent_coupling / (afferent_coupling + efferent_coupling)
# instability → 1.0: 多くに依存するが誰にも依存されない（脆弱）
# instability → 0.0: 多くに依存されるが自身は依存が少ない（安定）

# 環境全体のバランス
skill_usage_entropy = -Σ(p_i * log(p_i))  # p_i = skill_i の使用割合
# entropy 高 → 使用が均等に分散（健全）
# entropy 低 → 1つの skill が支配的（不均衡）
```

#### ゲームバランスとの接続

ゲームデザイナーは「1つの戦略が勝率 60% 超 → 支配的 → バランス崩壊」と判定する。同様に「1つの Skill が invoke の 60% 超 → 他の Skill が弱すぎるか、その Skill が広すぎる」。

---

### Pattern 10: Implicit Reward Learning — 行動から暗黙的に報酬を学習

**着想**: iStar (arXiv 2509.19199), Implicit Process Reward Models (arXiv 2502.01456)
**分野**: 強化学習 × 暗黙的フィードバック

#### コンセプト

```
明示的な fitness 関数を「書く」のではなく
ユーザー行動から「学ぶ」

┌──────────────────────────────────────────────────────┐
│  暗黙的シグナル（既に hooks が収集中）                 │
│                                                      │
│  ✓ ユーザーが修正せずに accept → 暗黙の positive     │
│  ✗ ユーザーが直後に修正 → 暗黙の negative            │
│  ✓ git commit された → 成果物として採用               │
│  ✗ ファイルが revert された → 品質不足                │
│  ✓ スキルが繰り返し使われる → 有用                    │
│  ✗ /clear 直後に同じタスクを再試行 → 失敗             │
│                                                      │
│  ✓ セッション短い + タスク完了 → 効率的               │
│  ✗ セッション長い + 同じ修正が繰り返される → 非効率   │
└──────────────────────┬───────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────┐
│  Step-Level Credit Assignment                        │
│                                                      │
│  iStar のアプローチ:                                 │
│  トラジェクトリ全体の結果（accept/reject）から        │
│  各ステップ（= 各 Rule/Skill の使用）に              │
│  貢献度を逆算する                                    │
│                                                      │
│  Session 1: [Rule A 適用, Skill B 使用, 結果: accept] │
│  Session 2: [Rule A 適用, Skill C 使用, 結果: reject] │
│  Session 3: [Rule D 適用, Skill B 使用, 結果: accept] │
│                                                      │
│  → Rule A: 2/3 sessions で使用、2/2 accept 時に存在  │
│  → Skill B: 2/3 sessions、2/2 accept → 高貢献       │
│  → Skill C: 1/3 sessions、0/1 accept → 低or負の貢献 │
└──────────────────────────────────────────────────────┘
```

#### Pattern 5 (Telemetry) との違い

Telemetry は「エラー率が下がったか」という集約指標。Implicit Reward Learning は「**どの構成要素が成功に貢献したか**」をステップレベルで帰属させる。

#### Metacognitive Learning との接続

[Intrinsic Metacognitive Learning (OpenReview)](https://openreview.net/forum?id=4KhDd0Ozqe) は「外部報酬だけでなく、自己内省による学習」を提唱。rl-anything の `reflect` スキルがまさにこれ — corrections から学習パターンを抽出し、環境を改善する。Pattern 10 はこれを**定量化**する。

---
