## Context

rl-anything の evolve パイプラインは、プロジェクトの使用パターンを分析してルール/フック/スキルの改善を提案する。現在の検出対象は:
- `builtin_replaceable`: Bash で Built-in ツール代替可能なコマンド使用
- `sleep-polling-guard`: sleep ポーリングパターン

これらは RECOMMENDED_ARTIFACTS + tool_usage_analyzer.py で実装されているが、対象が「Bash コマンド使用パターン」に限定されている。一方、「モジュール間データ契約の不整合」のようなコード構造に関する検証知見は、多くのプロジェクトで繰り返し発生する汎用的な問題であり、evolve で自動提案できるべきもの。

現在の検出フロー:
1. discover → tool_usage_analyzer.py で Bash コマンドパターンを分析
2. RECOMMENDED_ARTIFACTS で既存ルール/フックの導入状態を確認
3. 未導入の artifact を提案

## Goals / Non-Goals

**Goals:**
- 汎用検証知見のカタログをプラグイン内に持ち、discover/evolve で PJ 固有ルールとして提案する
- 初期カタログエントリとして「データ契約検証」を含める
- 既存の RECOMMENDED_ARTIFACTS と同じパイプラインに乗せる（新規フレームワーク不要）
- コードパターンの静的検出で提案の精度を上げる（B アプローチ）

**Non-Goals:**
- AST ベースの完全な静的解析（Python AST ライブラリ依存は避ける）
- 検証知見のバージョン管理や更新メカニズム（初回は静的カタログ）

## Decisions

### Decision 1: カタログの配置場所

**選択: `scripts/lib/verification_catalog.py`**

代替案:
- A) `skills/discover/scripts/` 内 → discover に密結合しすぎる
- B) 独立の YAML/JSON カタログファイル → パースコスト、型安全性なし
- C) `scripts/lib/` にモジュール配置 → 他のモジュール（tool_usage_analyzer.py, skill_evolve.py）と同じパターン

理由: discover と evolve の両方から参照でき、Python dict でカタログエントリを定義すれば型安全かつテスト容易。

### Decision 2: カタログエントリの構造

各エントリは以下の形式:

```python
{
    "id": "data-contract-verification",
    "type": "rule",
    "description": "モジュール間データ変換コード記述前にソース関数の返り値構造を確認する",
    "rule_template": "# データ変換コードの契約確認\n...",
    "detection_fn": "detect_cross_module_conversion",  # 検出関数名
    "applicability": "conditional",  # always | conditional
}
```

`detection_fn` は `verification_catalog.py` 内の関数を指し、以下のシグネチャで返す:
```python
{
    "applicable": bool,
    "evidence": list[str],  # project_dir からの相対パス、最大10件
    "confidence": float,    # 0.0-1.0
    "llm_escalation_prompt": Optional[str],  # confidence 0.4-0.7 時の LLM 再判定プロンプト
}
```

LLM エスカレーション: confidence が 0.4-0.7 の場合、`llm_escalation_prompt` を `claude --print` に渡して再判定するオプションを提供（参照: `skill_evolve.py:204-232` の `_score_judgment_complexity_llm()`）。初期実装は regex のみ、拡張パスを明示。

`rule_filename` は catalog 内で一意でなければならない。

### Decision 3: 検出アプローチ — regex + import グラフ軽量分析

**AST は使わない。** 代わりに:
1. **import パターン検出**: Grep で `from X import Y` を検出し、モジュール間の依存関係を把握
2. **dict 変換パターン検出**: `issues.append({`, `result["phases"]` 等の glue コードパターンを検出
3. **閾値判定**: 検出数が一定以上（3箇所以上）なら「統合コードが多いプロジェクト」と判定

### Decision 4: RECOMMENDED_ARTIFACTS への統合方式

**選択: 既存の `RECOMMENDED_ARTIFACTS` に `detection_fn: Optional[str]` フィールドを追加して統合**

代替案:
- A) VERIFICATION_ARTIFACTS として分離 → discover 内で2系統のパイプライン保守が必要、DRY 違反
- B) RECOMMENDED_ARTIFACTS に統合、`detection_fn` フィールド追加 → 既存エントリは `detection_fn: None`、検証知見エントリは `detection_fn` を持つ。`detect_recommended_artifacts()` 内で `detection_fn` があるエントリは関数呼び出しで判定

理由: 既存の `detect_recommended_artifacts()` / `detect_installed_artifacts()` のループを1つのまま拡張でき、保守コストが最小。`detection_fn` が `None` のエントリは従来通りパス存在チェックのみ。

### 閾値定数

```python
DATA_CONTRACT_MIN_PATTERNS = 3    # 検出パターンの最小マッチ数
DETECTION_TIMEOUT_SECONDS = 5     # 検出関数の実行タイムアウト
MAX_CATALOG_ENTRIES = 10          # カタログの最大エントリ数
LARGE_REPO_FILE_THRESHOLD = 1000  # 大規模リポジトリ判定のファイル数閾値
```

### Decision 5: evolve での提案フロー

```
Phase 2 (discover)
  └→ detect_verification_needs(project_dir) → verification_results
Phase 3.5 (remediation)
  └→ verification_results から verification_rule_candidate issue を生成
  └→ classify_issues() で proposable に分類
  └→ generate_proposals() で PJ 固有ルールの内容を提案
```

remediation の `FIX_DISPATCH` に `verification_rule_candidate` を追加し、承認時にプロジェクトの `.claude/rules/` にルールファイルを作成する。

### Decision 6: 初期カタログエントリ

| ID | 検証知見 | 検出パターン |
|----|---------|-------------|
| `data-contract-verification` | データ変換コードの契約確認 | `from X import Y` + dict 変換パターンが3箇所以上 |
| `error-boundary-verification` | 外部 API 呼び出しのエラー境界確認 | `subprocess.run` / `requests.` / `fetch` + try/except 不在 |
| `test-fixture-independence` | テストの自作 fixture が実データと乖離しないか | テストファイル内の dict リテラルが対応するソースの返り値と乖離 |

初期リリースは `data-contract-verification` のみ。他は検出精度が十分になってから追加。

## Risks / Trade-offs

- **[偽陽性]** regex ベースの検出は精度が低い → 初期は閾値を高めに設定し、proposable（手動承認）に限定。auto_fixable にはしない
- **[カタログ肥大化]** エントリが増えると discover の実行時間が伸びる → エントリ数に上限（10件）、`applicability: conditional` のエントリは検出関数が軽量であること
- **[PJ 固有ルール増殖]** evolve のたびに提案されると煩雑 → 導入済みチェック（既に `.claude/rules/` に同名ファイルがあればスキップ）
- **[検出関数の保守コスト]** 各エントリに検出関数が必要 → `applicability: always` のエントリは検出関数不要（常に提案）
