## Context

evolve の Prune → Merge フローで却下した統合候補ペアが次回実行時に再提案される。現在 `discover-suppression.jsonl` は discover.py の `load_suppression_list()` でのみ参照され、prune.py の `merge_duplicates()` では一切チェックされていない。

既存の merge spec（`openspec/specs/merge/spec.md` line 20）には「当該ペアを `discover-suppression.jsonl` に追加」と記載されているが、読み取り側（prune.py）に実装がない。

## Goals / Non-Goals

**Goals:**
- merge 却下済みペアを `merge_duplicates()` でフィルタリングし再提案を抑制する
- suppression データの永続化を discover.py の既存関数で共通化する
- 既存テストを壊さない

**Non-Goals:**
- discover-suppression の構造自体を大幅に変更すること
- suppression の有効期限（TTL）や自動解除の仕組み（将来の拡張とする）
- merge 以外の prune 候補（zero_invocations, decay 等）への suppression 適用

## Decisions

### 1. suppression ファイルを共有する（merge 専用ファイルを作らない）

**選択**: 既存の `discover-suppression.jsonl` を拡張し、`type` フィールドで区別する

**理由**:
- ファイル管理の一元化（audit 等で1箇所だけ見れば済む）
- discover.py の `add_to_suppression_list()` / `load_suppression_list()` を拡張するだけで済む
- merge suppression エントリは `{"pattern": "skill-a::skill-b", "type": "merge"}` の形式とする

**代替案**: `merge-suppression.jsonl` を新設 → 管理箇所が増え、audit/cleanup で考慮漏れのリスク

### 2. ペアキーの正規化方式

**選択**: スキル名をソートし `::` 区切りで結合（例: `"alpha-skill::beta-skill"`）

**理由**:
- `merge_duplicates()` 内で既に `sorted(pair)` を使っておりペアの順序が確定的
- frozenset と同じ一意性を持ちつつ、JSONL に文字列として保存可能
- `::` はスキル名に使われない区切り文字

### 3. prune.py に suppression チェックを追加する箇所

**選択**: `merge_duplicates()` のペアループ内、`.pin` / plugin チェックの直後

**理由**:
- 既存のスキップロジック（pinned, plugin）と同じパターンで `skipped_suppressed` status を追加
- 呼び出し元（run_prune, evolve）への影響が最小

**import 方式**: prune.py に `sys.path.insert(0, str(_plugin_root / "skills" / "discover" / "scripts"))` を追加し、`from discover import load_merge_suppression` でインポートする

### 4. suppression 登録のタイミング

**選択**: evolve SKILL.md の merge 却下フロー内（既存 spec の記述通り）

**理由**:
- prune.py は検出のみ、判断はスキル側という既存の責務分離を維持
- SKILL.md から `discover.add_merge_suppression()` を呼ぶ指示を明確化するだけ（`add_to_suppression_list()` ではなく専用関数を使用）

## Risks / Trade-offs

- **[suppression の肥大化]** → 長期的に却下ペアが蓄積。将来 TTL や手動リセット機能を追加可能。audit でエントリ数をレポートすれば気づける
- **[discover suppression との混在]** → `type` フィールド未指定の既存エントリは discover 用として扱う。後方互換性を維持
- **[ペアキーの衝突]** → スキル名に `::` を含むケースは現実的にない。万一の場合も sorted + join なので一意性は保たれる
- **[JSONL 破損時の挙動]** → 既存の `load_jsonl()` は `json.JSONDecodeError` を silent skip する（discover.py:44-45）。merge suppression でも同じ挙動を継承するため、破損行は無視されペアが再提案される可能性がある。致命的ではないが、audit で破損検知を追加すると堅牢性が向上する
