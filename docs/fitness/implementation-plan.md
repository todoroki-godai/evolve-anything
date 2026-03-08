# 環境全体 Fitness 評価 — 実装計画

> Issue: [#15](https://github.com/todoroki-godai/evolve-anything/issues/15)
> 関連: [evaluation-patterns.md](evaluation-patterns.md) / [phased-approach.md](phased-approach.md)

## 方針

安価な proxy 指標を先に積み上げ、たまに高コストの直接測定で校正する。
Phase 0-1 は **LLM コストゼロ**。既存の audit・hooks・telemetry_query を最大限活用する。

```
Phase 0 ──▶ Phase 1 ──▶ Phase 2 ──▶ Phase 3
構造品質     行動実績     原則評価     タスク実行
(静的分析)   (テレメトリ)  (LLM Judge)  (実行+進化)
コスト:ゼロ  コスト:ゼロ   コスト:低     コスト:高
```

## 既存資産の活用マップ

| 既存コンポーネント | 提供できるもの | 使う Phase |
|-------------------|---------------|-----------|
| `audit.collect_issues()` | 構造違反リスト（line_limit, stale_ref, duplicate, hardcoded） | 0 |
| `scripts/rl/fitness/skill_quality.py` | Skill 構造スコア（7軸） | 0 |
| `scripts/rl/fitness/plugin.py` | PJ固有キーワードスコア（4軸） | 0 |
| `scripts/lib/telemetry_query.py` | usage/sessions/errors の SQL クエリ | 1 |
| `~/.claude/rl-anything/usage.jsonl` | スキル呼び出し履歴（skill_name, project, timestamp） | 1 |
| `~/.claude/rl-anything/sessions.jsonl` | セッション概要（skill_count, error_count） | 1 |
| `~/.claude/rl-anything/workflows.jsonl` | ワークフロー（steps, intent_category） | 1 |
| `scripts/reflect_utils.py` | 8層メモリルーティング、find_claude_files() | 0, 1 |
| `scripts/lib/hardcoded_detector.py` | ハードコード値検出 | 0 |
| `scripts/lib/skill_triggers.py` | スキル名+トリガーワード抽出 | 0, 1 |

---

## Phase 0: 構造の整合性チェック（Coherence + KG Quality）

**目的**: 「環境として最低限整っているか」を LLM なしで測定する。
**パターン**: P4 Coherence + P9 KG Quality
**出力**: 環境構造スコア 0.0〜1.0

### 0-A. Coverage（カバレッジ）

> 各レイヤーに最低限の定義があるか？

| チェック項目 | 判定方法 | 既存 |
|-------------|---------|------|
| CLAUDE.md が存在する | find_claude_files() | ✅ |
| Rules が 1 つ以上ある | glob `.claude/rules/*.md` | ✅ audit |
| Skills が 1 つ以上ある | glob `.claude/skills/*/SKILL.md` | ✅ audit |
| Memory が存在する | glob `memory/*.md` | ✅ audit |
| Hooks が設定されている | `.claude/settings.json` の hooks | 新規 |
| CLAUDE.md に Skills セクションがある | テキスト検索 | 新規 |

**実装**: `scripts/rl/fitness/coherence.py` に `score_coverage()` 関数。

### 0-B. Consistency（整合性）

> レイヤー間に矛盾や断絶がないか？

| チェック項目 | 判定方法 | 既存 |
|-------------|---------|------|
| CLAUDE.md で言及された Skill が実在する | CLAUDE.md パース × glob | 新規 |
| Rule と CLAUDE.md の指示が矛盾しない | キーワード突合（LLM不要） | 新規 |
| Memory の PJ 構造が実際のディレクトリと一致 | Memory パース × fs チェック | 新規 |
| Skill のトリガーワードが重複していない | skill_triggers.py | ✅ audit (duplicate) |

**実装**: `score_consistency()` 関数。

### 0-C. Completeness（充足性）

> 定義されたものが実際に動くレベルで完成しているか？

| チェック項目 | 判定方法 | 既存 |
|-------------|---------|------|
| Skill が空や骨格だけでない（50行以上） | 行数チェック | ✅ audit |
| Rule が 3 行以内（制約遵守） | 行数チェック | ✅ audit |
| CLAUDE.md が 200 行以内 | 行数チェック | ✅ audit |
| Skill に必須セクション（Usage, Steps）がある | skill_quality.py | ✅ |
| ハードコード値がない | hardcoded_detector.py | ✅ audit |

**実装**: `score_completeness()` 関数。既存 audit チェックの再利用。

### 0-D. Efficiency（効率性）

> 冗長さや肥大化がないか？

| チェック項目 | 判定方法 | 既存 |
|-------------|---------|------|
| 意味的重複 Skill がない | TF-IDF cosine similarity | ✅ audit |
| 80% 超えの near-limit がない | 行数チェック | ✅ audit |
| 未使用 Skill がない（30日以上ゼロ invoke） | usage.jsonl | ✅ prune |
| 孤立 Rule がない（どの Skill にも関連しない） | 参照チェック | 新規 |

**実装**: `score_efficiency()` 関数。

### Phase 0 統合

```python
def compute_coherence_score(project_dir: str) -> dict:
    """環境構造スコアを算出"""
    coverage = score_coverage(project_dir)          # 0.0-1.0
    consistency = score_consistency(project_dir)     # 0.0-1.0
    completeness = score_completeness(project_dir)   # 0.0-1.0
    efficiency = score_efficiency(project_dir)       # 0.0-1.0

    overall = (coverage * 0.25 + consistency * 0.30
               + completeness * 0.25 + efficiency * 0.20)

    return {
        "overall": overall,
        "coverage": coverage,
        "consistency": consistency,
        "completeness": completeness,
        "efficiency": efficiency,
        "details": {...}  # 各チェック項目の pass/fail
    }
```

**成果物**:
- `scripts/rl/fitness/coherence.py` — 新規モジュール
- `audit` スキルに `--coherence-score` オプション追加
- 単体テスト

---

## Phase 1: テレメトリ駆動の効果測定（Telemetry + Implicit Reward）

**目的**: 「環境が実際に役立っているか」を行動データから測定する。
**パターン**: P5 Telemetry + P8 Kirkpatrick L1-L3 + P10 Implicit Reward
**出力**: 行動実績スコア 0.0〜1.0
**前提**: hooks データが十分に蓄積されていること（最低 30 セッション）

### 1-A. Utilization（利用率）

> 定義された構成要素が実際に使われているか？

| 指標 | データソース | 計算 |
|------|------------|------|
| Skill 利用率 | usage.jsonl | 過去30日で 1回以上 invoke された Skill / 全 Skill |
| Skill 利用偏り | usage.jsonl | Shannon entropy（均等なら高、偏りなら低） |
| Rule 遵守率推定 | corrections.jsonl | Rule 関連の修正発生率（低いほど遵守） |
| Hook 発火率 | usage.jsonl (hook records) | 期待発火数 vs 実際の発火数 |

**実装**: `scripts/rl/fitness/telemetry.py` に `score_utilization()` 関数。

### 1-B. Effectiveness（実効性）

> 環境があることで成果が改善しているか？

| 指標 | データソース | 計算 |
|------|------------|------|
| エラー減少率 | errors.jsonl | 直近30日 vs 前30日のエラー数比較 |
| 修正頻度の推移 | corrections.jsonl | 同種修正の減少トレンド |
| セッション効率 | sessions.jsonl | タスク完了数 / セッション時間 |
| ワークフロー完走率 | workflows.jsonl | 完走 workflows / 開始 workflows |

**実装**: `score_effectiveness()` 関数。

### 1-C. Implicit Reward（暗黙的フィードバック）

> ユーザー行動から構成要素ごとの貢献度を推定する。

| シグナル | 解釈 | データソース |
|---------|------|------------|
| Skill invoke 後に修正なし | 暗黙の positive | usage.jsonl × corrections.jsonl |
| Skill invoke 後に即修正 | 暗黙の negative | 同上（タイムスタンプ差 < 60s） |
| 同一 Skill の繰り返し利用 | 有用性の証拠 | usage.jsonl 集計 |
| /clear 後に同タスク再試行 | 失敗の証拠 | sessions.jsonl パターン |

**実装**: `score_implicit_reward()` 関数。Step-Level Credit Assignment は Phase 1 では簡易版（Skill 単位の成功率）。

### Phase 1 統合

```python
def compute_telemetry_score(project_dir: str, days: int = 30) -> dict:
    """行動実績スコアを算出"""
    utilization = score_utilization(project_dir, days)
    effectiveness = score_effectiveness(project_dir, days)
    implicit = score_implicit_reward(project_dir, days)

    overall = (utilization * 0.30 + effectiveness * 0.40
               + implicit * 0.30)

    return {
        "overall": overall,
        "utilization": utilization,
        "effectiveness": effectiveness,
        "implicit_reward": implicit,
        "data_sufficiency": ...,  # データ量が十分かの判定
    }
```

**成果物**:
- `scripts/rl/fitness/telemetry.py` — 新規モジュール
- `telemetry_query.py` への時間範囲クエリ追加
- discover/audit にトレンド表示を統合
- 単体テスト

---

## Phase 2: 原則ベースの自動評価（Constitutional + Chaos）

> Phase 0-1 の運用実績を見てから着手。LLM コストが発生する。

**目的**: PJ の価値観に沿っているかを LLM が判定する。
**パターン**: P3 Constitutional + P7 Chaos Engineering

### 2-A. Constitutional Evaluation

1. CLAUDE.md から「原則」を半自動抽出（LLM で構造化）
2. 各レイヤーの構成要素を原則に照らして LLM 評価
3. 原則遵守スコア 0.0〜1.0

### 2-B. Chaos Testing

1. Steady State 定義（Phase 1 のベースライン指標）
2. 障害注入（Rule 無効化 / Memory 空化 / 矛盾指示追加）
3. Steady State 維持率 = 堅牢性スコア

**成果物**:
- `scripts/rl/fitness/constitutional.py`
- `scripts/rl/fitness/chaos.py`
- PJ 固有の「原則ファイル」フォーマット定義

---

## Phase 3: タスク実行による成果測定（Task Exec + Eureka + Elo）

> Phase 0-2 が安定してから着手。最もコストが高いが最も信頼性が高い。

**目的**: 環境が PJ を成功に導けるかを直接測定する。
**パターン**: P1 Task Execution + P2 Eureka 進化 + P6 Elo Arena

### 3-A. Task Execution（#14 と統合）

- PJ 固有の `test-tasks.yaml` を整備
- `claude -p` でタスク実行 → verify ルールで判定
- pass^k で信頼性を保証

### 3-B. Eureka 式 Fitness 進化

- accept/reject 履歴から fitness 関数自体を LLM で生成・進化
- Goodhart's Law 対策（スコア一貫性、Pareto 選択）

### 3-C. Elo Arena

- 環境構成のバリエーション同士を対戦させて相対評価
- genetic-prompt-optimizer の selection に統合

**成果物**:
- `scripts/rl/fitness/task_executor.py`
- `scripts/rl/fitness/eureka.py`
- PJ 固有の `test-tasks.yaml`

---

## 統合: environment_fitness スコア

全 Phase の成果を統合する関数。利用可能なスコアだけでブレンドする。

```python
def compute_environment_fitness(project_dir: str) -> float:
    """環境全体の適応度を算出（0.0-1.0）"""
    coherence = compute_coherence_score(project_dir)     # Phase 0
    telemetry = compute_telemetry_score(project_dir)      # Phase 1
    constitutional = compute_constitutional_score(...)     # Phase 2 (optional)
    task_exec = compute_task_execution_score(...)          # Phase 3 (optional)

    # 利用可能なソースに応じた動的重み付け
    sources = {"coherence": coherence["overall"]}
    if telemetry["data_sufficiency"]:
        sources["telemetry"] = telemetry["overall"]
    if constitutional:
        sources["constitutional"] = constitutional["overall"]
    if task_exec:
        sources["task_exec"] = task_exec["overall"]

    return blend_scores(sources)
```

**stdin/stdout インターフェース互換**: 既存の `--fitness` フラグで使えるラッパーも提供。

---

## 実装順序とマイルストーン

### 1st: Phase 0（見積: 小〜中）

```
0.1  coherence.py 骨格 + score_coverage()
0.2  score_consistency() — CLAUDE.md × Skill/Rule 突合
0.3  score_completeness() — 既存 audit チェックのラッパー
0.4  score_efficiency() — 既存 duplicate/prune チェックのラッパー
0.5  compute_coherence_score() 統合 + CLI
0.6  audit --coherence-score 統合
0.7  単体テスト（各スコア関数 + 統合）
```

### 2nd: Phase 1（見積: 中）

```
1.1  telemetry_query.py に時間範囲クエリ追加
1.2  score_utilization() — Skill 利用率 + entropy
1.3  score_effectiveness() — エラー/修正トレンド
1.4  score_implicit_reward() — 簡易版 credit assignment
1.5  compute_telemetry_score() 統合 + data_sufficiency 判定
1.6  audit にトレンド表示統合
1.7  単体テスト
```

### 3rd: 統合（見積: 小）

```
2.1  environment_fitness.py — coherence + telemetry のブレンド
2.2  stdin/stdout ラッパー（--fitness environment で使用可能に）
2.3  /audit の出力に environment fitness を表示
```

### 4th: Phase 2-3（見積: 大、Phase 0-1 運用後に計画）

Phase 0-1 の運用から得られた知見でスコープを調整してから openspec change を作成する。

---

## 判断基準

### Phase 0→1 に進む条件

- coherence_score が全 PJ で算出可能
- 5 PJ 中 3 PJ 以上で hooks データが 30 セッション以上蓄積

### Phase 1→2 に進む条件

- telemetry_score が 3 PJ 以上で算出可能
- Phase 0-1 のスコアと「体感的な環境品質」が概ね相関している（人間判断）

### Phase 2→3 に進む条件

- Constitutional 評価が 2 PJ 以上で運用されている
- test-tasks.yaml が 1 PJ 以上で整備されている
