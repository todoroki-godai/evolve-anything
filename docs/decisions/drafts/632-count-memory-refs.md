# #632 memory / refs への反映を柱2に数える

状態: 設計（巡2 [Must] 反映済みの第3版・実装着手可）
対象 issue: todoroki-godai/evolve-anything#632（裁定済み・2026-09-05 ユーザー「数える」）

## 巡の履歴

| 巡 | レビュアー | 判定 | 結果 |
|---|---|---|---|
| 1 | codex（`design-632-20260909-162445-25498`・読んだ SHA `74d9f850`） | 設計修正要 | [Must]4種。うち2種（fold の許可集合・検証方法）を反映、2種（macOS 大小文字別名・hard link）は実測0件のため対象外へ明記 |
| 2 | codex（`design-632-r2-20260909-163420-73916`・読んだ SHA `c139e2d4`） | 設計修正要 | [Must]4件。実測0件で落とした2件は妥当と追認（レビュアーが実データを数え直し一致）。契約テストが閉じない件は **enum 化を切り出して別 issue**（巡数継承）、残り3件（CLI 経路の E2E・symlink 陰性試験・実装対象集合）は反映。**ユーザー裁定 2026-09-09: 縮小して切り出す** |

## 完成条件（round 0）

① **守る対象**: 柱2の計上件数が CLAUDE.md の柱2定義（「反映先は rule に限らない — skill / hook / pitfall / memory も柱2の対象」）と一致すること。
② **信頼境界**: 脅威に数えるのは自分たちの実装ミス（分類漏れ・過剰計上）のみ。悪意ある第三者によるパス偽装は数えない（反映先は人間が `--apply` に明示的に渡す値）。
③ **対象外**: skill / hook / pitfall の分類（`skill` は既に実装済み。hook / pitfall は反映実績0件のため本変更では触らない）。`--growth` の表示文言の変更。#631 の実装。
　**巡1 [Must] のうち実測0件で落とした2件**（`think-before-coding.md`「件数0の条件は既定で落とす」。0件でも落とさない3類型＝事故発生済み／法令・契約・ユーザー明示要求／初回も許容できない安全境界、のいずれにも該当しない。過少計上・二重計上はいずれも可逆で、対象は自分たちのデータのみ）:
　- **macOS の大小文字別名**（`~/.CLAUDE/refs/...` を渡すと `Path.resolve()` が綴りを保存するため `other` に落ちる）。実測: `reflect_apply_events.jsonl` の総ユニークパス14件中、`.claude` 以外の綴りは **0件**（`python3` で `reflect_target_path` を集合化・2026-09-09）。
　- **hard link による別名の二重計上**（`~/.claude/refs/shared.md` と `~/.claude/rules/shared.md` が同一 inode なら別 kind・別グループで2件計上される）。実測: `find ~/.claude/refs ~/.claude/rules -type f -links +1` = **0件**（2026-09-09）。
　いずれも発生したら過少計上／過剰計上として④で検出されるが、**本変更では機構を足さない**。実績が出た時点で別 issue とする。
④ **blocking の定義**: 判定の対象は **`Path.resolve()` 後の実体パス**とする（引数の字句パスではない。したがって root 外から root 内を指す symlink を渡した場合は正しく計上される、が正）。その上で (a) `~/.claude/refs/` 配下・`~/.claude/projects/<encoded>/memory/` 配下への apply が柱2に計上されない（`other` に落ちる場合と、**fold で invalid になる場合の両方を含む**） (b) 本変更により、実体が対象 root の外にあるパスが新たに柱2へ計上される。
⑤ **検証方法**: 単体テスト＋**配線試験**。
　- 陽性（分類）＝refs 配下・memory 配下の各1件が新 kind を返す。
　- **陽性（配線・巡1 [Must]4 / 巡2 [Must]2 対応）＝新 kind ごとに、実際の `reflect --apply` 経路（`skills/reflect/scripts/reflect.py:1399` の writer）を通して、記録イベントの kind・`invalid_events == 0`・`has_pillar2_fields == True`・`count == 1`・`measured == True` を1つの試験で確認する。手製の attempt を fold へ直接渡す形にしない**（writer を `reflect_target_kind="other"` に固定する変異が、分類単体・手製 fold・count のすべてを緑のまま通してしまうため）。
　- 陽性対照＝既存 kind（`global_rule` / `project_rule` / `global_claude_md` / **`project_claude_md`** / `skill`）と `other` の判定が変わらない。
　- 陰性試験（分類）＝`~/.claude/refs` の境界なし prefix（`~/.claude/refs-old/x.md`）・`projects/<encoded>/` 直下（memory の外）・任意階層の `memory` という名のディレクトリ、の3件が `other` のままであること。
　- **陰性試験（実体パス基準・巡2 [Must]3 対応。CLI 経路を通す）**＝(1) root 外の symlink → refs 内の実ファイルが `global_refs` かつ `count == 1` (2) refs 内の symlink → root 外の実ファイルが `other` かつ `count == 0` (3) `refs-old` と `projects/<encoded>/` 直下が、イベント kind `other`・`other_kind_count == 1`・`count == 0`・`measured == True`。**`resolve()` を外す変異でこれらが赤くなることを実際に確認する**（`verify-checks-by-breaking.md`: 変異が当該検査の実行で読まれたことまで機械で確かめる）。
⑥ **目的文の物差しで削る量**: 柱2の計上件数 **+5件**（refs 3 件 / memory 2 件）。
　根拠: `count_applied_reflections(Path(repo))` の `other_kind_count == 5`（実測 2026-09-09・main `74d9f850` と実装版 `cb28e2bb` の両方で `count=12` / `other_kind_count=5`）。
　再現手段: `python3 -c "import sys; sys.path.insert(0,'<repo>/scripts/lib'); from pathlib import Path; from pillar2_metrics import count_applied_reflections; print(count_applied_reflections(Path('<repo>')))"`。
　**issue #632 本文の「refs 6 / memory 2 = 8件」は 2026-09-05 時点の値で、現在の実データとは一致しない**（`factual-claims.md` に従い数え直した）。

## 実装対象ファイル（巡2 [Must]4 対応・この集合を越えない）

- `scripts/lib/reflect_apply_match.py`（分類器・重複集合の削除）
- `scripts/lib/reflect_fold.py`（許可集合の公開名化・新 kind 登録）
- `scripts/lib/evolve_revert/_target.py`（refs root の正典関数を1つ追加。`global_rules_root` と同居させ単一ソースにする）
- テスト（`scripts/lib/tests/` 配下）

`scripts/lib/pillar2_metrics.py` は変更しない。

## 現状（実測）

- `scripts/lib/reflect_apply_match.py:88-127` `classify_reflect_target_kind` は
  `global_rule` / `global_claude_md` / `skill` / `project_rule` / `project_claude_md` を返し、
  それ以外は `other`。
- `scripts/lib/pillar2_metrics.py:233` が `reflect_target_kind == "other"` を
  `other_kind_count` に積んで **eligible から除外**する。
- したがって `~/.claude/refs/*.md` と `~/.claude/projects/<encoded>/memory/*.md` への
  apply は柱2に計上されない。

## 変更

`classify_reflect_target_kind` に2つの分岐を追加する。判定は **正準 root 配下かどうか**で行い、
ファイル名・文字列パターンでは判定しない（`no-denylist-checks.md`）。

1. `global_refs`: `~/.claude/refs` を root とし、resolve 後のパスが root 配下なら返す。
   root は `global_rules_root()` と同じ形（`Path.home() / ".claude" / "refs"`）の関数を
   `evolve_revert/_target.py` に1つ足し、**単一ソース**にする。
   根拠: `~/.claude/rules/code-quality.md` が「詳細は `~/.claude/refs/<同名>.md` へ verbatim で移す」と
   規定しており、refs は rule 本文の外出し先＝rule と同じく挙動を決める。
2. `project_memory`: `~/.claude/projects/<encoded>/memory` 配下なら返す。
   `resolve_cc_memory_dir`（`pj_slug.py:247`）は **cwd から解決する関数で、任意パスの所属判定には使えない**。
   判定には `Path.home() / ".claude" / "projects"` を root とし、
   root からの相対パスが `<単一セグメント>/memory/...` の形であることを確認する
   （`<encoded>` を列挙しない・`memory` という名の他階層を拾わない）。

3. **`_KNOWN_TARGET_KINDS` を物理的に1箇所へまとめ、新 kind を登録する（巡1 [Must]1・必須）**。
   現状この集合は **2箇所に重複**して存在する:
   - `scripts/lib/reflect_fold.py:14-21`（`_attempt_is_valid` が実際に使う正典）
   - `scripts/lib/reflect_apply_match.py:29-36`（現状どこからも使われていない）

   後者を削除し、前者を公開名（`KNOWN_TARGET_KINDS`）にして `reflect_apply_match` から参照する。
   これで**集合の定義箇所は1つ**になり、「片方だけ更新して食い違う」経路は消える。

   **本変更で置く再発防止の一手は「集合の物理単一化」まで**とする。
   巡2 の [Must] が指摘したとおり、**分類器が生文字列を返す限り、
   集合に無い値を返す変更（helper 経由・変数経由）は静的検査では捕まらない**。
   これを閉じるには kind を enum にして分類器の戻り値型ごと固定する必要があるが、
   それは「memory / refs を数える」という本 issue の目的とは別目的（構造の根治）なので
   **切り出して別 issue とし、本設計の巡数を継承させる**（③対象外・`review.md` の切り出し規定）。
   代わりに本変更では、**実際の `--apply` 経路を通す E2E 試験**（⑤）で
   「分類器が返した値が fold を通って count に至る」ことを毎回確認する。

`pillar2_metrics` 側は変更しない（読み直しは `reflect_fold` 側で行う）。`reflect_fold` が新 kind を受理しさえすれば、
`pillar2_metrics.py:221-239` は `other` だけを除外して残りを無条件に eligible へ入れるため、
新 kind は自動的に数えられる（**巡1 [Must]1/4 の指摘どおり、fold の更新が前提条件**）。
グループキー `(target_kind, target_path, draft_line)` も変更しない。

4. **読み出し時に分類し直す（実装レビュー巡1 [Must]・必須）**。
   種別は `skills/reflect/scripts/reflect.py:1399` の apply 時にイベントへ**書き込まれて凍結**され、
   `scripts/lib/reflect_fold.py:252,273` は `attempt_event.get("reflect_target_kind")` を**そのまま使う**。
   したがって**分類器を直しても既に記録済みのイベントは `other` のまま**で、⑥の +5件は達成できない
   （実測: main `74d9f850` と分類器修正版 `cb28e2bb` の両方で `count=12` / `other_kind_count=5`＝差ゼロ）。

   `reflect_fold` の読み出し時に、**kind が `other` のイベントについてのみ**、
   記録されている `reflect_target_path` を `classify_reflect_target_kind` へ通し直し、
   新 kind に該当すればその値を使う。既存の非 `other` kind は触らない。

   - **記録は書き換えない**（append-only を守る。過去イベントの rewrite / backfill は行わない）
   - 派生値を読み出し時に導出するのは本 PJ の既存方針（`weak_signals` の TTL も read 時 age 導出）
   - **ユーザー裁定 2026-09-09: 読み出し時に分類し直す**

   追加試験: 既に `other` として記録済みのイベント（refs 配下・memory 配下の各1件）が、
   fold を通したあと新 kind として `count` に載ることを確認する。
   変異（この読み直しを外す）で赤くなることも確認する。

## 判定に使う識別（`no-denylist-checks.md` の要求）

正準 root（`~/.claude/refs` / `~/.claude/projects`）からの **resolve 後の相対位置**。
ファイル名・拡張子・文字列パターンは使わない。本変更は blocking 検査ではなく分類なので、
迂回による安全上の損失は無い（誤分類は計上漏れ／過剰計上として④で検出する）。

## 影響

- 柱2の計上件数が増える（実測 +8件）。**基準値の連続性が切れる**ので、
  `handover_20260905_pillar2_funnel` に凍結した基準値 1/38 とは分母が変わることを PR 本文に明記する。
- CLAUDE.md の柱2定義の変更は不要（定義側が既に「rule に限らない」と書いており、実装が追いつく形）。

## 未実測

- hook / pitfall を反映先とする apply は実績0件のため、分類の要否を測っていない。
  実績が出た時点で別 issue とする。
