# spec-keeper update — リカバリーモード（Step R1）

SKILL.md「`/spec-keeper update` — Step 1」の判定でリカバリーモードへ分岐した場合に参照する。

通常の差分ベース更新（Step 2）では精度が落ちるため、**セクション単位の突合**に切り替える。

## R1-1: 変更の棚卸し

feat/refactor コミットを A/B/C に分類してユーザーに提示:

```bash
git log --oneline --since="$(git log -1 --format=%ci -- SPEC.md)" \
  --grep="^feat\|^refactor" --format="%h %s"
```

| カテゴリ | 判定基準 | SPEC.md への影響 |
|---------|---------|----------------|
| **A: Architecture** | 新モジュール、ディレクトリ構造変更、大規模リファクタ | Architecture + API セクション要更新 |
| **B: API/Interface** | 新コマンド、パラメータ変更、新スキル追加 | API/Capabilities セクション要更新 |
| **C: 内部改善** | パフォーマンス、内部リファクタ、バグ修正 | 反映不要（Recent Changes のみ） |

## R1-2: セクション単位の突合と更新

Step 1 の突合表で **差分があるセクションを優先的に更新** する。一度に全セクションを書き換えず、セクションごとに確認しながら更新する。

差分があるセクションでは、`ls` や `find` の結果と SPEC.md のコンポーネント一覧を目視比較し、**何が増えて何が消えたか**を特定してから Edit する。

| セクション | 突合先 | 数値差分時の確認方法 |
|-----------|--------|-------------------|
| Architecture（hooks） | `ls hooks/*.py` | SPEC.md の hooks 一覧と diff |
| Architecture（scripts/lib） | `ls scripts/lib/*.py` | SPEC.md のモジュール一覧と diff |
| Architecture（fitness） | `ls scripts/rl/fitness/*.py` | SPEC.md の適応度関数一覧と diff |
| API/Interface / Capabilities | `ls -d skills/*/` | SPEC.md のスキルコマンド表と diff |
| Design Decisions | `ls docs/decisions/*.md` | SPEC.md の ADR 件数・リンクと diff |
| Recent Changes | git log | 直近5件に絞る、古い項目は CHANGELOG.md へ移動 |
| Overview | CLAUDE.md | 差分検出不可、意味的に確認 |
| 用語集（CONTEXT.md） | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/lib/glossary_drift.py" CONTEXT.md SPEC.md CLAUDE.md` | 構造 drift は exit 1。advisory は未登録 jargon |
| Limitations / Next | コード観察 | 差分検出不可、意味的に確認 |

## R1-3: 未記録の設計判断を ADR に救出（大乖離の場合のみ）

```bash
git log --since="$(git log -1 --format=%ci -- SPEC.md)" \
  --grep="廃止\|移行\|置換\|replace\|migrate\|deprecate\|breaking" --oneline
```

設計判断を含むコミットが見つかったら、ユーザーに ADR 作成を提案する。

## R1-4: 更新完了

- `Last updated:` を更新（`(recovery)` を付記: 例 `Last updated: 2026-03-25 by /spec-keeper update (recovery)`）
- 肥大化チェック実行
- **README.md 更新（README.md が存在する場合のみ）**: リカバリーで更新したセクションのうち、ユーザー向けの変化があれば Step 2 の README.md 更新ルールに従って Edit する
- 次回からの乖離防止のため、`/spec-keeper update` の実行タイミングをユーザーにリマインド
