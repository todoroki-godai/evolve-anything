Related: #21

## Context

現状の fitness 評価は `scripts/rl/fitness/plugin.py`（Skill テキストのキーワードマッチ）のみ。環境全体の構造的整合性を測る仕組みがない。

既存資産:
- `audit.collect_issues()` — 構造違反リスト（line_limit, stale_ref, duplicate, hardcoded）
- `scripts/rl/fitness/plugin.py` — PJ固有キーワードスコア（stdin/stdout I/F）
- `scripts/lib/hardcoded_detector.py` — ハードコード値検出
- `scripts/lib/skill_triggers.py` — スキル名+トリガーワード抽出
- `scripts/reflect_utils.py` — find_claude_files() 等

## Goals / Non-Goals

**Goals:**
- Coverage / Consistency / Completeness / Efficiency の4軸で環境構造スコアを算出する `coherence.py` を実装
- audit に `--coherence-score` オプションを追加し、レポートに統合表示
- 既存モジュールを最大限再利用し、新規ロジックを最小化
- LLM コストゼロ（静的分析のみ）

**Non-Goals:**
- テレメトリベースの動的評価（Phase 1 で対応）
- LLM Judge による原則評価（Phase 2 で対応）
- stdin/stdout fitness インターフェースのラッパー（Phase 0-1 統合時に対応）
- PJ 固有の評価基準（Phase 2-3 で対応）

## Decisions

### D1: モジュール構成 — 単一ファイル `coherence.py`

4軸のスコア関数を `scripts/rl/fitness/coherence.py` に集約する。

**理由**: 各軸は 20-40 行程度の小さな関数で、ファイル分割するほどの複雑さがない。既存 `plugin.py` と同じ粒度。

**代替案**: 軸ごとにファイル分割 → 過剰分割。`audit.py` に直接追加 → audit の責務が膨らみすぎる。

### D2: スコア算出 — 重み付き平均（Coverage 0.25, Consistency 0.30, Completeness 0.25, Efficiency 0.20）

`implementation-plan.md` で設計済みの重み配分をそのまま採用する。

**理由**: Consistency（レイヤー間の矛盾）が最も環境品質に影響するため最重。Efficiency（冗長さ）は他3軸が整っていれば軽微。

### D3: audit 統合 — `--coherence-score` フラグで有効化

デフォルトでは既存レポートに影響を与えず、明示的にフラグ指定で Coherence Score セクションを追加する。

**理由**: 既存の audit ワークフローを壊さない。evolve パイプラインからは明示的に呼び出せる。

**代替案**: 常時表示 → 情報過多。別コマンド → 発見性が低い。

### D4: プロジェクトディレクトリの解決

`coherence.py` は `project_dir` を引数で受け取り、そこから `.claude/` 配下を探索する。audit と同じ `find_claude_files()` ベースのパス解決を使う。

### D5: 既存チェックの再利用方針

| チェック | 再利用元 | 方法 |
|---------|---------|------|
| Skill 行数・構造 | `audit.collect_issues()` | issues リストから line_limit/duplicate を集計 |
| ハードコード値 | `hardcoded_detector.py` | `detect_hardcoded_values()` を直接呼び出し |
| トリガー重複 | `skill_triggers.py` | `extract_skill_triggers()` を直接呼び出し |
| CLAUDE.md パス | `reflect_utils.py` | `find_claude_files()` を直接呼び出し |

新規実装が必要なチェック:
- CLAUDE.md で言及された Skill の実在チェック（Consistency）
- Memory のパス存在チェック（Consistency）
- Hooks 設定の存在チェック（Coverage）
- 孤立 Rule の検出（Efficiency）

### D6: 閾値の一括管理 — `THRESHOLDS` 定数 dict

`coherence.py` 先頭に `THRESHOLDS` dict を定義し、全閾値を一括管理する。

```python
THRESHOLDS = {
    "skill_min_lines": 50,
    "rule_max_lines": 3,
    "claude_md_max_lines": 200,
    "near_limit_pct": 0.80,
    "unused_skill_days": 30,
    "advice_threshold": 0.7,
}
```

**理由**: ハードコード散在を防ぎ、将来の設定ファイル外出しや Phase 1 統合時のチューニングを容易にする。

## Risks / Trade-offs

- **[Risk] チェック項目の過不足** → implementation-plan.md の設計を忠実に実装し、運用後にチューニング
- **[Risk] 既存モジュールの import 依存が深い** → coherence.py は薄いラッパーに徹し、ロジックは既存モジュールに任せる
- **[Trade-off] 重み配分の固定** → v1 は固定値、Phase 1 統合時にテレメトリで校正する余地を残す
