# tech-eval: 2026-05-15 triage 中判定の深掘り検証

- **評価日**: 2026-05-15
- **対象**: ai-github-trending-2026-05-15.md の triage で 🔶 中 と判定した 5 件の妥当性を verify
- **結論**: **4 件は低に降格、1 件 (RS-Claw) のみ中維持**

## 検証結果サマリ

| # | 対象 | 当初 | **再判定** | 根拠 |
|---|------|------|-----------|------|
| 4 | aidlc-workflows (OSS) | 中 | **低** | implement + spec-keeper + growth_journal で phase 遷移は既実装、AWS lifecycle はスコープ外 |
| 5 | cocoindex (OSS) | 中 | **低** | `hooks/save_state.py` + `post_compact.py` の compaction checkpoint で session 横断圧縮は CC 純正機構で実装済み |
| 9 | Interpret Agent Behavior (論文) | 中 | **低** | evolve-anything は LLM 推論を多段化していない (hooks/rule ベース)、解釈レイヤー自体が不要 |
| 14 | Executable Multi-Hop RAG (論文) | 中 | **低** | `remediation/verify.py` の 17 個 `_verify_*` + `VERIFY_DISPATCH` で「修正後の実機検証で幻覚削減」は既実装、ドメインが違うだけ |
| 17 | RS-Claw (論文) | 中 | **中** | `reorganize.py` で hierarchical clustering は使うが「基本スキル→複合スキル自動合成」は未実装 |

## 個別深掘り

### #4 aidlc-workflows → 低

**論文/OSS の主張**: 要件 → 設計 → 実装 → テスト → デプロイの phase をエージェントが自律遷移、品質メトリクスで路線変更。

**evolve-anything 側の実装**:
- `skills/implement/SKILL.md` — plan artifact → タスク分解 → single/parallel 実装 → 検証 → テレメトリ記録 (5 phase)
- `skills/spec-keeper/SKILL.md` — SPEC.md + ADR 管理、L1/L2 自動昇格
- `scripts/lib/growth_journal.py:45` — `phase: str` パラメータで phase 区別、`growth_narrative.py:211-217` で phase 遷移ログ化
- `scripts/lib/effort_detector.py:23` — multi-phase 検出パターン

**判定理由**:
- AWS の AI-DLC (Software Lifecycle) は **enterprise 開発フロー全体** が対象、evolve-anything は **plugin 内のスキル進化** に閉じている
- phase 遷移ロジック自体は既実装、AWS SDK / WorkflowEngine のような重い抽象を追加する利得なし
- 「適応的路線変更」も `audit` → `evolve` → `prune` のループで既にカバー

### #5 cocoindex → 低

**論文/OSS の主張**: 長期エージェントが過去経験を段階的に圧縮しながら新情報を追加、メモリ効率を保持。

**evolve-anything 側の実装**:
- `hooks/save_state.py:98 handle_pre_compact` — Claude Code の PreCompact event を捕捉して `checkpoint.json` に evolve 中間状態 + 直近 commit + uncommitted files + branch を保存
- `hooks/post_compact.py:15 _build_context_message` — Compact 後に checkpoint を復元してユーザーに見せる
- `MEMORY.md` (auto-memory) — session 横断永続化レイヤー
- `scripts/tests/bench_ingest.py:98-103` — token_usage_ingest の incremental insert (別目的: SoR 取り込み)

**判定理由**:
- Claude Code が `PreCompact` / `PostCompact` hook を提供しており、evolve-anything はそれを使い倒している
- cocoindex は「外部ライブラリで増分処理エンジンを足す」アプローチだが、CC 純正機構と二重実装になる
- "long-horizon メモリ" は MEMORY.md + checkpoint.json で十分機能している

### #9 Interpret Agent Behavior → 低

**論文の主張**: エージェント決定の因果追跡、注意機構可視化、中間推論ステップ抽出、代替案比較。

**evolve-anything 側の実装**:
- 該当する解釈レイヤーなし (grep でゼロヒット)
- ただし `scripts/tests/test_reflect_provenance.py` で reflect の出所追跡は最低限ある

**判定理由**:
- evolve-anything の主要パスは **hooks / rule ベースで決定論的**。LLM 推論を多段化しているのは `evolve` / `optimize` / `evolve-loop` の評価軸のみ
- 「なぜこの決定をしたか」を後から追跡する必要があるのは LLM heavy なエージェントで、evolve-anything はそうではない
- correction_detect も「LLM が判断した理由を可視化」より「pattern match で検出」が主流
- 解釈レイヤーを足すコスト > 期待効果

### #14 Executable Multi-Hop RAG → 低 (実質既実装)

**論文の主張**: RAG にコード実行を組み込み、中間結果を Python 等で検証して幻覚を削減。

**evolve-anything 側の実装**:
- `scripts/lib/remediation/verify.py` — `VERIFY_DISPATCH` 経由で 17 個の `_verify_*` 関数 (`_verify_stale_ref`, `_verify_line_limit_violation`, `_verify_skill_evolve`, `_verify_verification_rule`, `_verify_instruction_violation` 等)
- `remediation/__init__.py:137-180` — fix 適用後に対象ファイルを実機で再評価 → regression gate
- `record_outcome` で `remediation-outcomes.jsonl` に検証結果を記録

**判定理由**:
- 論文は「RAG 出力を code exec で検証」、evolve-anything は「remediation 出力を file inspection + re-evaluation で検証」
- **ドメインは違うが core 発想（実行ベース検証で幻覚削減）は既実装**
- 論文側に独自の RAG 拡張 (例: multi-hop chain across docs) があれば検討余地だが、現状の evolve-anything に RAG は存在しない (memory は MD 直読み)

### #17 RS-Claw → 中 維持

**論文の主張**: 階層スキルツリーで基本スキルから複合スキルへ自動組み合わせ、アクティブラーニングで有用な組み合わせを発見。

**evolve-anything 側の実装**:
- `scripts/lib/reorganize.py:50` — `scipy.cluster.hierarchy.linkage / fcluster` で **階層クラスタリングは使用済み** (ただし用途は「分割提案」のみ、合成方向は未着手)
- `discover` skill — 行動パターン → missed skill 検出 (Jaccard) で「新スキル候補」は提案する
- ❌ **基本スキル → 複合スキルの自動合成** は実装なし
- ❌ アクティブラーニングで組み合わせ探索する loop はなし

**判定理由 (中維持)**:
- reorganize の分割方向は逆向きの操作で、合成方向は未開拓領域
- ただし「複合スキル自動合成」を採用するには:
  1. スキル間の依存グラフ表現が必要 (現状なし)
  2. 合成スキルの効用を測る fitness が必要 (`environment` fitness の単体スキル向け重みでは不足)
  3. ユーザー操作との接続点が不明 (合成スキルを誰がいつ承認するか)
- 現時点で Issue 化するには **下地が足りない**。「再評価条件: スキル数 30 超 + skill 間依存が課題化したら」として保留

## 推奨アクション (更新版)

| 概念 | 推奨度 | アクション | 再評価条件 |
|------|--------|------------|------------|
| aidlc phase 遷移 | 低 | 取り入れない | implement skill の phase 構造が破綻したら |
| cocoindex incremental | 低 | 取り入れない | CC の compaction hook が機能不足になったら |
| Interpret Agent Behavior | 低 | 取り入れない | evolve-anything が LLM heavy 化したら |
| Executable Multi-Hop RAG | 低 (実質既実装) | 取り入れない | evolve-anything に RAG レイヤーが入ったら |
| RS-Claw 階層スキル合成 | **中** | 保留、下地整備が先 | スキル数 30 超 + skill 間依存グラフ化が必要になったとき |

## 結論

**当初 5 件中の中判定 → 実質 1 件 (RS-Claw) のみ妥当。残り 4 件は低**。

triage 段階で「ギャップは中程度」と直感的に置いたが、コード照合すると 4 件は既実装で、1 件は採用余地はあるが下地不足。トリアージで甘く中に振った傾向あり、**今後の triage では `grep -rn` で 1 概念あたり最低 1 ヒット根拠を取ってから中判定する** ルールに引き締めるとよい。
