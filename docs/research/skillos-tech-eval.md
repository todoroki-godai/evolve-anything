# tech-eval（深掘り版）: SkillOS

- **論文**: SkillOS: Learning Skill Curation for Self-Evolving Agents
- **著者**: Siru Ouyang ほか16名 (Google DeepMind / UIUC)
- **arXiv**: <https://arxiv.org/abs/2605.06614>
- **提出日**: 2026-05-07
- **評価日**: 2026-05-14
- **評価スキル**: `/tech-eval`
- **関連 Issue**: #67, #68, #69, umbrella #70

## 論文の手法詳細

| 要素 | SkillOS の設計 |
|------|----------------|
| SkillRepo | Markdown ファイル + YAML frontmatter (name, when_to_use) + body (workflow/constraints/heuristics) — Anthropic Skills と同形式 |
| Retrieval | BM25（task description → top-k skills）— 著者自身が limitation として明記 |
| Curator π_𝒮 の action 空間 | 3操作のみ: `insert_skill` / `update_skill` / `delete_skill`（split / merge は無い） |
| 報酬 | `r = r^task + λ_f·r^fc + λ_u·r^cnt + λ_c·r^comp` |
| ├ r^task | タスク成功率 |
| ├ r^fc | 有効な function call 率 |
| ├ r^cnt | Qwen3-32B を judge にした content quality score |
| └ r^comp | `1 - |𝒮|/|χ|`（skill 数 / 経験数）= 圧縮度、肥大化罰 |
| 学習 | GRPO（DeepSeek-R1 系のグループ相対 advantage） |
| Executor | 凍結（Qwen3-8B / Gemini-2.5-Pro） — curator のみ訓練 |
| ベースライン | No Memory / ReasoningBank / MemP / SkillOS-base (RL前) / SkillOS-gemini (Gemini を直接 curator) |
| 主要結果 (Qwen3-8B) | ALFWorld 61.2% (+5.5 vs ReasoningBank) / WebShop SR 16.5% (+5.1) / 推論 79.7% (+10.1) |
| Cross-backbone | Gemini-2.5-Pro executor に訓練済 curator を移すと ALFWorld 80.2%（汎化性あり） |
| Code | 未公開（論文内に URL なし） |

## 査読コメント

### 強い点

- **r^comp の存在が決定的**: skill 数を experience 数で正規化して罰する明示項。skill バブル（増やすほど報酬になる罠）を構造的に防いでいる。rl-anything には対応項が無い（`prune` は LLM judge で都度判断）。
- **r^fc**: 「skill 指示通りに valid な function call が出たか」を直接 reward 化。スキル品質の操作的定義としてシンプルで強い。rl-anything は observe hooks (15個) で tool 呼び出しを全量取れているのに、この signal を skill 品質に back-prop していない。
- **frozen executor + trainable curator の分離**: rl-anything と相性が良い（Claude Code 自体は frozen、rl-anything plugin だけが進化対象）。設計の正当化に使える。
- **Cross-backbone 汎化**: curator policy が executor を取り替えても効く → メタ層で学べる証拠。

### 弱い点 / 違和感

- **Action 空間が 3 操作のみ**（SPLIT/MERGE 無し）。rl-anything の `skill_triage` (5択) のほうが豊富。論文ではこれを simplicity と呼んでいるが、reorganize 系の判断は LLM curator の prompt 任せになっており、ablation でも切り分けられていない。
- **r^cnt が Qwen3-32B judge**: judge model を curator と同系統に取ると報酬ハッキング懸念。独立 judge にすべきだが論文内に検証なし。
- **BM25 retrieval 限界**を著者が認めている: skill 数増加でノイズ拾い、コンテキスト汚染。rl-anything も skill 全部 load なので同種の問題。
- **Compression 項のスケール感**: 経験数 χ が分母なので新規 PJ 立ち上げ時は ε に弱い。rl-anything のように長期 telemetry がある環境では効くが、cold start で振動する懸念。
- **Ablation が薄い**: λ_f / λ_u / λ_c を個別に 0 にした表が無く、各項の寄与が不明。Appendix C 言及のみ。
- **Failure mode の議論が浅い**: skill が誤って削除された後の recovery、curator が wrong update を打った時の rollback について記述なし。rl-anything の regression gate（`scripts/lib/regression_gate.py`）に相当する safety 層が無い。
- **コード未公開**。再現困難。

## rl-anything との対応関係（構造同型と差分）

| 観点 | SkillOS | rl-anything | 評価 |
|------|---------|-------------|------|
| Skill 表現 | MD + YAML | MD + YAML | 完全同型 |
| Curator action | 3操作 | 5択 (CREATE/UPDATE/SPLIT/MERGE/OK) | rl-anything 優位 |
| Retrieval | BM25 | file listing + LLM context | 同レベル（両者の弱点） |
| Curator 学習 | GRPO | LLM 1-pass + regression gate | SkillOS 優位（学習 vs ヒューリスティック） |
| 圧縮度報酬 | r^comp 明示 | `prune` の LLM judge のみ | SkillOS 優位（最大の差分） |
| Function call signal | r^fc 報酬 | observe hooks で記録のみ | rl-anything は signal あるが活用してない |
| Safety / rollback | 記述なし | regression gate あり | rl-anything 優位 |
| Cross-backbone | Qwen→Gemini で汎化検証済 | Claude Code 単一環境 | scope 外 |

## 取り入れる優先順位

| 取り入れる概念 | 推奨度 | 理由 | Issue |
|---------------|--------|------|-------|
| r^comp（skill 数 / experience 数 の圧縮ペナルティ）を `fitness/environment.py` に追加 | 高 | 実装 30 行、ハイパラ 1 個、現在の prune ヒューリスティックを補強。skill バブル防止の明示シグナル | #67 |
| r^fc（valid function call rate）を skill ごとに集計し fitness 項に追加 | 高 | observe hooks のデータは既にある (`token_usage_store` と同じ系)。skill ごとに「指示通り tool が呼ばれた率」を出すだけ | #68 |
| Frozen executor / trainable curator の分離記述を SPEC.md / ADR に追記 | 中 | 設計レベルでは既にそうなっている。論文を引用して正当化 | #69 |
| skill 変更の遅延 attribution | 中 | r^comp と r^fc が入れば必要性が部分的に消化される。優先度は後 | — |
| GRPO による curator policy 学習 | 低 | データ量・実装コスト過大。単一 PJ では rollout 不足 | — |
| BM25 retrieval | 不要 | SkillOS 自身が limitation と認めている。CC の skill 自動 load 機構があるので不要 | — |

## 総評

論文を読み込むと、**r^comp と r^fc の 2 つの reward 項のほうが ROI が圧倒的に高い**。両方とも

- rl-anything のデータ基盤（`token_usage_store`, observe hooks, skill 数カウント）で既に取れる
- fitness 関数への加算項として実装 30〜50 行
- ハイパラ 1 個追加で済む

ので、`fitness/environment.py` の重み（現状 coherence 0.25 / telemetry 0.45 / constitutional 0.30）を維持したまま `telemetry` の内訳に compression と function-call validity を追加するのが妥当。GRPO 化は不要。
