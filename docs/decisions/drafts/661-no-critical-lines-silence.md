# #661-1: critical 行 0 件の correction が出力から無音で消える

状態: 実装済み・レビュー1巡（tacchi / `マージ可`・[Must] 0 件） / 起票: #661 の 1 番
巡の履歴: round 0 = 本設計（codex へ発注したが利用上限で空振り・run `661-design-20260920-232203-77511`）
／ round 1 = tacchi によるコードレビュー（2026-09-21・`マージ可`）。
**系統独立レビューは未充足**（codex 復旧後 2026-09-22 10:10 以降に追認する）

## 完成条件（review.md の定型）

- **守る対象**: `instruction_violation` 経路で、correction が候補にならなかった事実が
  件数として出力に残ること（silence != evaluated）
- **信頼境界**: 脅威に数えるのは自分たちの運用ミス（無言 skip の見落とし）のみ。
  悪意ある入力・攻撃者は数えない
- **対象外**: `extract_critical_lines` の抽出精度の改善。廃止済みスキルの候補掃除（#661 の 2 番）。
  誤検知の除去（#661 の 3・4 番）。候補の質そのもの
- **blocking の定義**: SKILL.md を解決できたのに候補化されなかった correction が、
  `result` のどの件数にも現れない状態
- **検証方法**: 陰性試験（下記 4 件）+ 陽性対照（下記 2 件）。本文末尾に記載
- **目的文の物差しで削る量**: 本 PJ の目的指標は柱1（捕捉率）。
  **本変更が捕捉率を直接動かす量は 0**（候補は増えない。増えるのは件数表示だけ）。
  発注理由は柱1の**測定の正しさ**で、`instruction_violation` の産出が 0 である理由を
  段階分解する #467 の実測（`docs/decisions/drafts/artifacts/467-iv-pipeline-2026-09-12.md` S4 行）
  が、本欠陥により 1 件を取りこぼしていたこと。実測値: 当PJ `last_skill` 付き 8 件中 **1 件**
  （`status-recap`）が無音で消えた（取得日 2026-09-15・出典は上記 artifact）

## 現状（実測 2026-09-20）

`scripts/lib/discover/runner.py:488-492`

```python
for skill_md in all_skill_mds:
    content = skill_md.read_text(encoding="utf-8")
    instructions = extract_critical_lines(content)
    if not instructions:
        continue          # ← ここ。件数がどこにも残らない
    violation = detect_instruction_violation(corr, instructions)
    ...
    break                 # instructions が非空なら最初の 1 件で打ち切り
```

- `unresolved_count`（`instruction_violations_unresolved`）は **SKILL.md を解決できなかった** 件数で、
  意味が違う（`proposal_lane_coverage.py:351` のコメントが単一ソース）。ここに混ぜると
  既存の意味が壊れるので**混ぜない**
- `result["instruction_violations_error"]` は例外用。正常系の件数には使えない

## 変更内容

### 1. 新しい件数キーを 1 つ足す

`instruction_violations_no_critical_lines`（int）。
**全候補 SKILL.md で `extract_critical_lines` が空だったときにだけ 1 を足す**
（1 つでも非空のパスがあれば加算しない）。0 件のときはキー自体を書かない
（既存 `instruction_violations_unresolved` と同じ作法）。

```python
had_instructions = False
for skill_md in all_skill_mds:
    content = skill_md.read_text(encoding="utf-8")
    instructions = extract_critical_lines(content)
    if not instructions:
        continue
    had_instructions = True
    violation = detect_instruction_violation(corr, instructions)
    ...
    break
if not had_instructions:
    no_critical_lines_count += 1
```

### 2. 提案レーン被覆の宣言表へ登録する

`scripts/lib/proposal_lane_coverage.py` の非提案キー集合（`instruction_violations_unresolved`
の直後）へ、**基準2（int 件数・個別レビュー対象になり得ない）** として理由コメント付きで追加する。

### 3. 凍結（#379 Step 1）との関係

`shrink_freeze` の凍結対象は store / observability section / advisory proposal adapter /
weak_signal channel の 4 レジストリ。`result` dict のキーはいずれにも属さないため
**凍結には抵触しない**（`scripts/lib/shrink_freeze.py:62-210` を確認済み・2026-09-20）。
新 store も新 weak_signal channel も作らない。

## 変更しないこと

- `break` の位置と「最初にマッチしたスキルのみ」の既存挙動
- `instruction_violations_unresolved` の意味と加算条件
- 候補（`instruction_violations`）の中身・件数

## 検証

### 陰性試験（検査を通したまま仕様を壊す変異。各 1 件以上・実際に適用して赤を確認する）

実施結果は PR #666 本文が正典（設計が挙げた 4 件に、種類の違う自作 2 件を加えた計 6 件を適用し、
すべて赤・緑のまま残ったものは 0 件）。

1. `had_instructions = True` の行を削除 → 候補が作れた correction まで件数に混ざる
2. `if not had_instructions:` を `if True:` に変える → 常に加算される
3. 加算を `unresolved_count += 1` に書き換える（既存キーへ混ぜる）→ 意味の取り違えを検出できること
4. `proposal_lane_coverage` への登録行を削除 → 被覆テストが赤になること

### 陽性対照（緑のままであるべき）

1. critical 行がある SKILL.md で候補が作られるケース → 新キーが出ない・既存の候補件数が不変
2. SKILL.md を解決できないケース → `instruction_violations_unresolved` が従来どおり 1 で、新キーは出ない

陰性・陽性のいずれも、**(a) 守る不変条件を壊す差分があること (b) その分岐が当該テストの実行で
読まれたこと**の両方を機械的な証跡（カバレッジまたは失敗メッセージ）で確認してから判定する。
