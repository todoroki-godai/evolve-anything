## 1. Correction Detection Hook

- [x] 1.1 `hooks/correction_detect.py` を新規作成: UserPromptSubmit イベントから CJK/英語修正パターンを検出し corrections.jsonl に記録
- [x] 1.2 修正パターン定義を `hooks/common.py` に追加（CORRECTION_PATTERNS, CJK_CORRECTION_PATTERNS, false_positive_filters）
- [x] 1.3 直前スキル紐付けロジック: observe.py が最後に記録した skill_name を `$TMPDIR/rl-anything-last-skill-{session_id}.json` に書き出し、correction_detect.py が last_skill として取得。TTL は 24 時間とし、古いファイルは無視する（MUST）
- [x] 1.4 `hooks/hooks.json` に UserPromptSubmit エントリを追加
- [x] 1.5 correction_detect.py のユニットテスト: パターン検出、疑問文除外、サイレント失敗

## 2. Confidence Decay

- [x] 2.1 `skills/prune/prune.py` に decay 計算ロジックを追加: `confidence = base_score * exp(-age_days / decay_days)`
- [x] 2.2 corrections.jsonl の読み込みと base_score 減点ロジック: correction 1件あたり -0.15
- [x] 2.3 `.pin` ファイルによる淘汰保護: pin 存在チェックを detect_zero_invocations と safe_global_check に追加
- [x] 2.4 decay_threshold 設定の読み込み: evolve-state.json からデフォルト 0.2
- [x] 2.5 prune の decay 関連ユニットテスト: decay 計算、pin 保護、corrections 減点

## 3. Semantic Validation & Multi-Target Routing

- [x] 3.1 `skills/analyze/` に semantic_validate 関数を追加: corrections を LLM で検証
- [x] 3.2 recommendation routing ロジック: correction_count / frequency / project_count に基づく target 振り分け
- [x] 3.3 `--no-llm` フラグ対応: パターンマッチのみの高速モード
- [x] 3.4 analyze 出力フォーマットに target フィールドを追加
- [x] 3.5 semantic validation + routing のユニットテスト

## 4. Backfill Corrections

- [x] 4.1 `skills/backfill/backfill.py` に `--corrections` フラグを追加
- [x] 4.2 human message からの修正パターン遡及抽出ロジック: 直前 assistant ターンの Skill 特定
- [x] 4.3 backfill 由来の confidence 0.60 設定
- [x] 4.4 backfill corrections のユニットテスト

## 5. Reclassify Integration

- [x] 5.1 reclassify extract に corrections 紐付きセッションの優先抽出ロジックを追加
- [x] 5.2 LLM 分類プロンプトに correction context を注入
- [x] 5.3 reclassify integration のユニットテスト

## 6. E2E 検証

- [x] 6.1 correction 検出 → prune decay → analyze routing の一気通貫テスト
- [x] 6.2 evolve dry-run で decay 反映の確認
- [x] 6.3 README / CHANGELOG 更新
