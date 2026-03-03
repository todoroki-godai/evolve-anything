## Context

evolve パイプラインは現在 7 Phase で構成される:
Observe → Fitness Check → Discover → Audit → Prune → Fitness Evolution → Report

Discover は error/rejection パターンを検出するが「新規スキル候補」としてしか提案しない。
Audit は重複を検出するが「どちらかをアーカイブ」する二択。
スキルが増えるにつれ、関連スキルの分散・知見の未反映が課題になっている。

### 現在のデータフロー

- `discover.py`: usage.jsonl, errors.jsonl, history.jsonl → behavior/error/rejection_patterns
- `audit.py`: find_artifacts() → 重複検出(semantic_similarity_check)、使用状況集計
- `prune.py`: audit の結果 + usage → archive 候補
- `evolve.py`: 上記を順次実行するオーケストレーター

### 既存の共通基盤

- `audit.classify_artifact_origin()`: plugin/global/custom の3分類
- `audit.find_artifacts()`: 全スキル/ルール/メモリの走査
- `audit.load_usage_data()`: 直近N日の usage.jsonl 読み込み
- `prune.archive_file()`: タイムスタンプ付きアーカイブ + メタデータ保存

## Goals / Non-Goals

**Goals:**
- Discover の error/rejection パターンを既存スキルに照合し、改善提案を生成する（Enrich Phase）
- 重複スキルを LLM で統合して1つにする（Merge サブステップ）
- スキル群全体をクラスタ分析し、再編提案を出す（Reorganize Phase）
- 全て dry-run 対応（提案のみ、変更なし）
- ユーザー承認なしに変更を加えない

**Non-Goals:**
- ルールの自動統合（ルールは3行制約があり、統合の意味が薄い）
- plugin 由来スキルの変更（既存ポリシー通り情報提供のみ）
- LLM embedding ベースのベクトル検索（コスト・複雑性が高すぎる）

## Decisions

### D1: Enrich Phase の配置 — Discover の直後

**選択**: Discover → **Enrich** → Optimize → ...
**理由**: Discover の出力（error_patterns, rejection_patterns）を直接入力として使うため。Optimize より前に配置することで、Enrich が改善した内容を Optimize がさらに磨ける。

**代替案**:
- Optimize の後に配置 → Optimize 結果と Enrich 結果が競合する可能性があるため却下

### D2: パターン→スキル照合 — キーワードマッチ + LLM 確認の2段階

**選択**: まず error/rejection パターンのテキストとスキル名・SKILL.md 冒頭をキーワードマッチし、候補を絞った上で LLM に改善 diff を生成させる。
**理由**: 全スキル × 全パターンを LLM に投げるとコスト爆発する。キーワードで候補を 0〜3 件に絞ってから LLM 呼び出し。

**マッチングロジック**:
1. パターンテキストをトークン分割（空白・記号）
2. 各スキルの `skill_name` と SKILL.md の先頭50行をトークン分割
3. Jaccard 係数 ≥ 0.15 のペアを候補とする（低閾値は LLM 確認で補完）
4. 候補がない場合は「既存スキルに関連なし → 新規候補」として従来の Discover フローに戻す

### D3: Merge の配置 — Prune Phase 内のサブステップ（Reorganize 連携）

**選択**: Prune の merge サブステップは、`reorganize.merge_groups` と `duplicate_candidates` の**和集合（重複排除済み）**を入力とする
**理由**: Reorganize が TF-IDF で類似スキルグループを検出し、Prune 内の Merge がその結果を活用する。二重提案を防止するため、和集合から重複を排除してから処理する。

**Merge フロー**:
1. `reorganize.merge_groups` と `duplicate_candidates` の和集合を構築し、スキルペアの重複を排除
2. Python（prune.py）が統合候補を JSON で出力（型A パターン、D8 参照）
3. SKILL.md の指示に従い Claude が統合版を生成し、ユーザーに提示 → 承認されたら: 統合版で上書き + 片方をアーカイブ
4. dry-run 時は提案のみ

### D4: Reorganize Phase の配置 — Prune の直前

**選択**: Discover → Enrich → Optimize → **Reorganize** → **Prune(+Merge)** → Fitness Evolution → Report
**理由**: Reorganize の `merge_groups` を Prune 内の Merge に渡すため、Reorganize を Prune より前に配置する。Reorganize が先にクラスタリングで類似スキルグループを検出し、Prune の Merge がその結果と `duplicate_candidates` を統合して処理する。

### D5: クラスタリング手法 — キーワード TF-IDF + 階層クラスタリング

**選択**: スキルの SKILL.md テキストから TF-IDF ベクトルを生成し、scipy の階層クラスタリングで分析。
**理由**: Python 標準 + scipy のみで実装可能。LLM embedding 不要でコスト0。クラスタ数は距離閾値で自動決定。

**代替案**:
- LLM embedding → コスト高、外部API依存のため却下
- スキル名のみでクラスタリング → 内容を無視するため精度不足で却下

**出力**:
- クラスタ内スキル数 ≥ 2 のグループ → 「統合候補」として提案
- 単一スキルで行数 > 300 → 「分割候補」として提案

### D6: plugin スキルの取り扱い

**選択**: Enrich/Merge/Reorganize 全てで plugin 由来スキルは対象外（情報提供のみ）
**理由**: 既存の evolve ポリシー（classify_artifact_origin == "plugin" は変更しない）を踏襲

### D7: 新規ファイル構成

```
skills/
  enrich/
    scripts/enrich.py      # Enrich Phase ロジック
  reorganize/
    scripts/reorganize.py  # Reorganize Phase ロジック
  prune/
    scripts/prune.py       # merge サブステップ追加（既存ファイル変更）
  evolve/
    scripts/evolve.py      # Phase 追加（既存ファイル変更）
    SKILL.md               # ドキュメント更新（既存ファイル変更）
```

### D8: LLM 呼び出しパターン — 型A（Python→JSON出力→SKILL.md で Claude に解釈させる）

**選択**: Enrich / Merge ともに**型A**パターンを採用する。Python スクリプトは照合・統合候補の JSON 出力のみを担当し、LLM を直接呼び出さない。SKILL.md の Step 指示として Claude に diff 生成・統合版生成を行わせる。

**理由**:
- ユーザー対話が必須のフロー（改善提案の確認、統合版の承認）であり、Claude の対話コンテキスト内で処理するのが自然
- Python コードが LLM 非依存のデータ処理のみとなり、ユニットテストが容易
- 既存パターン（discover, prune）との整合性が高い

**型Aの動作**:
1. Python スクリプト → 照合結果 / 統合候補を JSON で stdout に出力
2. SKILL.md の Step 指示 → Claude が JSON を読み取り、対話的にユーザーに改善提案 / 統合版を提示

**代替案（型B）**:
- Python 内で `subprocess.run(["claude", "-p", ...])` を呼ぶ（optimize.py のパターン）
- Enrich / Merge ではユーザー対話が必須のため不適切。却下

## Risks / Trade-offs

- **[LLM コスト増加]** → Enrich は候補を 0〜3 件に絞ってから呼び出し。Merge は duplicate_candidates の件数に比例するが、通常 0〜2 件。Reorganize はクラスタリング自体は LLM 不使用。全体で +1〜5 LLM 呼び出し/evolve 実行。
- **[Merge で意図しない情報損失]** → 統合版は必ずユーザーに提示して承認を得る。アーカイブされた元スキルは restore_file() で復元可能。
- **[Reorganize の過度な提案]** → クラスタ距離閾値を保守的に設定（0.7）。スキル数 < 5 の場合は Reorganize をスキップ。
- **[scipy 依存追加]** → reorganize.py のみで使用。scipy がない環境では graceful degradation（スキップ + 警告）。Reorganize 初回実行時に `pip install scipy scikit-learn` のインストール案内をユーザーに表示する。代替案として Python 標準ライブラリのみ（`collections.Counter` + `math.log`）で簡易 TF-IDF を実装する選択肢もあるが、階層クラスタリングには scipy が必要なため、現時点では scipy 依存を維持し graceful degradation で対応する。
