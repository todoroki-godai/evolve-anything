# #601: 柱2 scope 判定に `reflect_target_path` を併用する設計

対象: `#601`（`#600` レビューの [Should] を切り出したもの）。本文書は**設計のみ**。コードは1行も変更しない。

## 0. Round 0 完成条件

### ① 守る対象

柱2（「実際に反映された改善」件数）として自PJの board に表示される件数に、**他PJで反映された
改善が紛れ込むこと**（過大計上）。

### ② 信頼境界（誰の能力を脅威に数えるか）

**自分たちの運用ミスのみ**。数えるのは: `message` が偶然 generic に見える他PJ由来の correction／
複数PJが同一 `corrections.jsonl` を共有する運用そのものが持つ曖昧さ。
**数えない**: 悪意ある偽装・意図的な水増し・第三者による改竄。

### ③ 対象外

- `global_rule`（`~/.claude/rules/` 配下への反映）の帰属判定方式そのものの再設計。
  現行の `message`/`project_path` ヒューリスティックを維持する（§3.3 で理由を説明）
- `message` ヒューリスティック自体の精度改善（`always`/`never`/モデル名キーワード等）。
  対象は「`reflect_target_path` を**併用しなかった**ことによる過大計上」のみ
- 柱2以外（柱1・柱3・柱4）の scope 判定
- `#379` 新設凍結の解除。本設計は新しいストア・チャネルを一切作らない
- `corrections.jsonl` を PJ ごとに物理分割する等の構造変更（`learning_physical_unification_not_the_goal`
  の教訓どおり、union read で足りるなら物理分割は over-eng）

### ④ blocking の定義

`reflect_target_kind == "project_rule"` かつ反映先の repo が現在の project_root と異なる
correction が、`same-project` または `global-looking` として柱2の `count` に計上されること。

### ⑤ 検証方法

§5 の陰性試験（4件）＋陽性対照（1件）を実測する。

### ⑥ この成果物が目的の物差しで削る量

**目的の単位**: 誤計上しうる件数（柱2 `count` に紛れ込む他PJ由来の `project_rule` 反映行数）。

**実測値（取得時刻 2026-09-03T01:12:54Z・commit `4b1565f3`）**:
```
$ wc -l ~/.claude/evolve-anything/reflect_apply_events.jsonl
2
```
`reflect_apply_events.jsonl` は2件のみ。うち `reflect_target_kind` が判定可能なのは1件
（`correction_id=b195b2bf9ef54cc487a087ae3cb374fd`・`reflect_target_path=/Users/matsukaze-takashi/
matsukaze-utils/evolve-anything:.claude/rules/pillars-before-polish.md`）で、これは
evolve-anything 自身への反映（= 現在の修正対象の project_root と repo_id が一致）。
**現時点でこの修正が実際に取り除く誤計上は 0 件**（再現: 上記 `wc -l` コマンド＋
`python3 -c` で `reflect_target_kind` を印字して目視、当セッションで実施）。

**将来リスクの実測代理値**（0件と決めつけないための傍証。目的指標そのものではないので
⑥には算入しない）: `PILLAR2_METRICS.PRE_SCHEME_APPLIED_BASELINE`（新方式導入前の `applied`
4件）のうち **3件は `project_path=updater-index`**（`pillar2_metrics.py:22`のコメントに
「evolve-anything 視点では global-looking として通過している」と明記済み。実際にはこの
4件は `has_pillar2_fields=False` のため `pre_scheme_excluded_count` に落ち柱2の `count` には
入らないので**実害は発生していない**が、`message` ベース判定が本当に他PJの correction を
`global-looking` へ通す実例として使える）。

**結論**: ⑥は実測ベースでは **0**。理由は「まだ他PJの `--apply` が `project_rule` 反映を
1件も出していない」という運用の初期段階にあるためで、修正の正しさとは無関係。
`observation-first.md`／`pillars-before-polish.md` の判断基準に照らすと、これは
「立証された配線断」ではなく「顕在化前のバグ」に分類される。**着手判断は本設計の対象外
（人間裁定）とし、§6・§7 に判断材料を残す。**

## 1. 現状の事実

### 1.1 現行の scope 判定（`message` ベース、`reflect_target_path` 未使用）

- `scripts/lib/pillar2_metrics.py:98-116` `_classify_project_scope`: `skills/reflect/scripts/
  reflect.py` の `classify_project_scope` へ委譲する薄いラッパー
- `scripts/lib/pillar2_metrics.py:119-132` `_pillar2_project_scope`: `correction["project_path"]`
  が現在の project_root の slug（別名込み）と一致すれば `"same-project"`。一致しなければ
  `_classify_project_scope` へフォールバック
- `skills/reflect/scripts/reflect.py:179-211` `classify_project_scope`: `project_path` が
  `None` → `"global-looking"`。現在PJと一致 → `"same-project"`。`message` に
  `always`/`never`/モデル名キーワードが含まれる → `"global-looking"`。`message` に
  DB名やファイルパスらしき文字列が含まれる → `"project-specific-other"`。**それ以外は
  すべて `"global-looking"`（デフォルト）**
- `scripts/lib/pillar2_metrics.py:207-239` `count_applied_reflections`: `folded_correction.base`
  （= 生の correction dict）だけを `_pillar2_project_scope` に渡している。`folded_correction`
  自体が持つ `reflect_target_kind` / `reflect_target_path`（`reflect_fold.py:55-56`）は
  **一切参照されていない**（issue の指摘どおり）

### 1.2 `reflect_target_kind` / `reflect_target_path` の実データ形

`scripts/lib/reflect_apply_match.py:88-127` `classify_reflect_target_kind` の値域:
`global_rule` / `project_rule` / `global_claude_md` / `project_claude_md` / `skill` / `other`。

`scripts/lib/reflect_apply_match.py:130-144` `normalize_reflect_target_path`:
- `global_rule` などパス識別に `repo_identity` が失敗するケース → 解決済み絶対パスをそのまま
  文字列化
- `project_rule` 等 `repo_id` が取れるケース → `f"{repo_id}:{relative_path}"`。
  `repo_id` は `scripts/lib/evolve_decision_ids.py:73` `repo_identity()` が返す
  **`git rev-parse --git-common-dir` の親ディレクトリの絶対パス文字列**（worktree 間で
  共有される本体 repo root）

実データ確認（`~/.claude/evolve-anything/reflect_apply_events.jsonl`・2件・
取得時刻 2026-09-03T01:12:54Z）:
```json
{"correction_id": "b195b2bf9ef54cc487a087ae3cb374fd", "reflect_target_kind": "project_rule",
 "reflect_target_path": "/Users/matsukaze-takashi/matsukaze-utils/evolve-anything:.claude/rules/pillars-before-polish.md",
 "project_path": null}
{"correction_id": "dbcfcecff3f34b0481249046ce5bdc6e", "reflect_target_kind": null,
 "reflect_target_path": null, "project_path": null}
```
（再現: `python3 -c "import json; [print(json.loads(l)) for l in open('~/.claude/evolve-anything/
reflect_apply_events.jsonl')]"`。**イベント行自体には `project_path` フィールドが常に
`null`**——反映先の情報しか持たず、correction がどのPJのセッションで生まれたかは基底行
（`corrections.jsonl` 側の `project_path`）にしかない）

### 1.3 `project_path` の実データ分布

`~/.claude/evolve-anything/corrections.jsonl`（259件・取得時刻 2026-09-03T01:12:54Z、
再現: `wc -l` + `python3` で `project_path` を `Counter` 集計）:

| project_path の形 | 件数（上位） |
|---|---|
| `evolve-anything`（bare slug） | 53 |
| `/Users/matsukaze-takashi/updater/amamo`（絶対パス） | 40 |
| `updater-index`（bare slug） | 36 |
| `/Users/matsukaze-takashi/matsukaze-utils/rl-anything`（絶対パス・旧slug） | 30 |
| `receipt` | 20 |
| `figma-to-code` | 14 |
| ...（他 7 PJ） | 各1〜13 |
| `null` | 8 |

**bare slug と絶対パスの2形式が混在**している（writer が異なるため）。`_pillar2_project_scope`
は `pj_slug_aliases_for` で bare slug の別名だけを吸収しており、絶対パス形の `project_path`
に対しては `resolve_pj_slug(project_root)`（bare slug）と文字列一致しないため、
`reflect.py` 側の `_normalize_path` ベースの絶対パス比較（`classify_project_scope`
内の `current_project` 引数）にフォールバックする。**この2形式問題は既存動作であり
本設計が新規に作るものではないため、そのまま活用する**（§3.2）。

### 1.4 既存の重複実装確認

`scripts/lib/skill_origin.py` の `classify_skill_origin` はプラグインスキルの出自判定
（`installed_plugins.json` ベース）であり、本 issue の「correction の project scope 判定」とは
対象が異なる別モジュール。重複実装ではない。`_classify_project_scope` /
`_pillar2_project_scope` 以外に同種の判定ロジックは見つからなかった
（`grep -rn "reflect_target_kind\|reflect_target_path" scripts/lib/pillar2_metrics.py` で
1箇所のみ使用を確認）。

## 2. 判定の設計

### 2.1 適用範囲（`count_applied_reflections` の `eligible` 収集ループのみ）

`_count_scoped_invalid_base_ids`（`pillar2_metrics.py:135-156`）が読む
`fold_health.invalid_base_id_records` は生の correction dict（`reflect_fold.py` の
`FoldedCorrection`化前）であり、`reflect_target_kind`/`reflect_target_path` を持たない。
**この関数は対象外**（不正 ID 基底は反映の照合自体が成立していないため、そもそも
反映先パスが存在しない）。

修正対象は `count_applied_reflections` 内、`folded_correction.has_pillar2_fields` が
`True` になった**後**（`pillar2_metrics.py:221`以降）の scope 判定 1箇所のみ。

### 2.2 新関数 `_pillar2_target_repo_scope`

```python
def _pillar2_target_repo_scope(
    reflect_target_kind: str | None,
    reflect_target_path: str | None,
    project_root: Path,
) -> Optional[str]:
    """reflect_target_path から repo_id を取り出し、project_root と比較する。

    判定できるのは reflect_target_kind == "project_rule" のときだけ（repo_id が
    normalize_reflect_target_path で確実に埋まる値域）。それ以外（global_rule 等）は
    None を返し、呼び出し側は既存の message/project_path ヒューリスティックへフォール
    バックする。
    """
    if reflect_target_kind != "project_rule":
        return None
    if not reflect_target_path or ":" not in reflect_target_path:
        return None  # repo_id を含まない形 = 判定不能。安全側（除外）に倒すのは呼び出し側
    target_repo_id = reflect_target_path.split(":", 1)[0]
    own_identity = repo_identity(str(project_root / ".pillar2-scope-probe"))
    own_repo_id = own_identity.get("repo_id")
    if not own_repo_id:
        return None  # project_root 自身が git 管理外 = 判定不能
    return "same-project" if target_repo_id == own_repo_id else "other-project-rule"
```

`repo_identity` は `evolve_decision_ids.py` の既存関数をそのまま import する
（`.pillar2-scope-probe` は存在しないファイル名で構わない — `repo_identity` は
`Path(path).parent` が `is_dir()` であることしか見ない。§1.1参照）。

### 2.3 `count_applied_reflections` 側の呼び出し変更

```python
for folded_correction in folded:
    target_scope = None
    if folded_correction.has_pillar2_fields:
        target_scope = _pillar2_target_repo_scope(
            folded_correction.reflect_target_kind,
            folded_correction.reflect_target_path,
            Path(project_root),
        )
    if target_scope == "other-project-rule":
        continue  # 他PJの project_rule 反映 → 自PJの柱2から除外（新設カウンタなし。
                   # #379 凍結を守るため health 辞書の既存フィールドに漏れなく反映される
                   # 経路だけを使う。専用の row_count は増やさない——③対象外）
    scope = target_scope or _pillar2_project_scope(folded_correction.base, Path(project_root))
    if scope not in ("same-project", "global-looking"):
        continue
    ...（以降は変更なし）
```

`target_scope == "same-project"` のときは `_pillar2_project_scope` を呼ばずに確定させる
（`message` ヒューリスティックより `reflect_target_path` の方が確実な証拠のため、
矛盾時は `reflect_target_path` を優先する）。

### 2.4 グローバル rules への反映（`global_rule`）の裁定

**現行のまま（`message`/`project_path` ヒューリスティック）を維持し、変更しない。**

理由:
1. `~/.claude/rules/` はどのPJのセッションからでも正当に編集されうる共有ファイルであり、
   「反映先パス」自体には帰属情報が存在しない（`reflect_target_path` はグローバル rules
   root からの相対パスのみで repo 情報を持たない）
2. 帰属を知る手がかりは基底 correction の `project_path`（= correction が**生まれた**
   セッションの PJ）しかなく、これは現行コードが既に使っている
3. 新たに「反映イベント発生時にどのPJのセッションから `--apply` を実行したか」を記録する
   フィールドを追加すれば判定可能になるが、これは `#379` 新設凍結の対象（新フィールド追加
   は新設ではないが、記録契約の拡張は本 issue のスコープ外——③参照）で、`reflect_apply_events`
   の schema 変更は `#587`/`#595` 系の管轄
4. グローバル rules への反映は「そのPJ発の学びが全PJに展開された」という意味で、
   起点PJの柱2に計上すること自体は誤りではない（他PJの柱2にも二重計上されうる点は
   既知の残存リスク——§6）

### 2.5 判定不能ケースの裁定（自PJ扱い vs 除外）

**除外（`other-project-rule` 扱いと同じ側）に倒す。**

理由: `reflect_target_path` に `:` が無い（`repo_id` を含まない）、または `project_root`
自身が git 管理外で `own_repo_id` が取れない、のいずれも「`project_rule` として反映された
ことは分かっているが、どのPJに属すか確定できない」状態。柱2の目的は「実際に反映された
確実な件数」（CLAUDE.md 「4. 信頼: 表示する数字が嘘をつかない」）であり、
`legacy_unverified_count`/`pre_scheme_excluded_count`/`other_kind_count` など既存の全ての
「不確実なら除外」という前例（§1.1・`pillar2_metrics.py:207-234`）と整合する。
過小計上（`not_measured`/除外カウンタでの可視化）は許容されるが、過大計上は許容されない
という既存の設計哲学をそのまま踏襲する。

## 3. 検討したが採らなかった案

### 案A: `message` ヒューリスティックを `project_rule` にも `global_rule` にも一切使わず、
`reflect_target_kind` が取れた行は必ず `reflect_target_path` だけで判定する

不採用。`global_rule` は §2.4 の理由で `reflect_target_path` だけでは判定不能なため、
`message`/`project_path` ヒューリスティックを完全に捨てると `global_rule` 反映が
一律「判定不能→除外」になり、**柱2の`count`が現状（0件計上）よりさらに保守的になる**。
これは「削る量」ではなく「別の欠落を作る」変更であり、issue のスコープ（過大計上の防止）
を超える。

### 案B: `corrections.jsonl` を書く全 hook に `reflect_apply_events` 側でも
`project_path`（correction 由来）を複製して持たせ、fold 時に単一ソース化する

不採用。`reflect_apply_events` の schema 変更は `#587`/`#595` の管轄（③対象外）。
また `folded_correction.base`（元の correction 行）に既に `project_path` があるため、
イベント側に複製すると同一情報の二重管理になり `learning_copied_parse_convention_partial_fix`
（片側だけ直す desync の温床）を再生産するリスクがある。

### 案C: `corrections.jsonl` をPJごとに物理分割する

不採用。`learning_physical_unification_not_the_goal` の教訓どおり、union read（= 本設計の
フィルタリング）で欠落なく対処できるなら物理分割は over-engineering。運用中の全 writer
（hook）の書込み先変更を伴う大改修であり、本 issue の被害規模（⑥=0、§0）に見合わない。

## 4. 残存リスク（受け入れるもの）

1. **`global_rule` への反映は、複数PJの柱2へ二重計上されうる**（§2.4 の理由で意図的に
   残す）。将来これが問題になった場合は、`reflect_apply_events` に「`--apply` 実行時の
   `pj_slug`」を追加する別 issue で対処する
2. **`project_path` が絶対パス形式（§1.3）で記録された古い correction**は、
   `pj_slug_aliases_for` の bare slug 突合をすり抜け `reflect.py` 側の絶対パス比較へ
   フォールバックする。これは本設計が新設する `_pillar2_target_repo_scope` の対象外
   （`project_rule` の `reflect_target_path` を見る限り影響しない）だが、`message`
   ヒューリスティックへフォールバックするケースでは既存のまま残る
3. **`skill`/`other`/`global_claude_md`/`project_claude_md` 種別への反映**は本設計の
   対象外（`project_rule` のみ扱う）。同型の帰属曖昧性を持つが、実データ0件（§0⑥の
   `reflect_apply_events.jsonl` 2件はいずれも `project_rule`/`null` のみ）のため、
   発生してから同じ設計パターン（§2.2-2.3）を適用する

## 5. 陰性試験の一覧（実装時に赤になることを確認する対象）

各変異に「壊す不変条件」と「通したい検査経路」を記す。

| # | 分類 | 変異内容 | 壊す不変条件 | 通したい検査経路 |
|---|---|---|---|---|
| 1 | ①要素を消す | `_pillar2_target_repo_scope` の呼び出し自体を削除し、常に `message` ヒューリスティックへフォールバックさせる | 他PJの `project_rule` 反映を除外できる | 他PJ repo_id を持つ `project_rule` イベント1件を含む fixture で `count` が1件多く出る（除外されない）ことを確認するテスト |
| 2 | ②意味を壊す | `target_repo_id == own_repo_id` の比較を `!=` に反転する | 同一PJの `project_rule` 反映を正しく `same-project` にできる | 同一 repo_id の `project_rule` イベント1件が誤って除外されることを確認するテスト |
| 3 | ③分散・入替 | `reflect_target_path` の `repo_id:relative_path` の `:` 分割位置をずらす（例: 最後の `:` で分割）か、`relative_path` 側に `:` を含む fixture を混入 | `repo_id` の抽出が値の内容に依存せず安定していること | Windows 風パス（`relative_path` にドライブ文字由来の `:` を含む）に近い fixture で `repo_id` 抽出が破綻しないことを確認するテスト |
| 4 | ④検査を無効化する | `_pillar2_target_repo_scope` が例外時に `None` ではなく常に `"same-project"` を返すようフォールバックを反転する | 判定不能ケースが除外側に倒れること（§2.5） | `own_repo_id` 取得失敗（git 管理外 `project_root`）を模した fixture で、判定不能行が `count` に混入しないことを確認するテスト |
| 陽性対照 | — | 変異なし。正常データ（同一PJの `project_rule` 反映1件＋他PJの `project_rule` 反映1件＋`global_rule` 反映1件） | 誤検出しないこと | 同一PJの1件だけが `count` に入り、他PJの1件は除外され、`global_rule` の1件は既存ヒューリスティックどおりに扱われることを確認するテスト |

## 6. 未計測の項目

- 実装後にこの修正が実際に何件の誤計上を防いだかは、他PJの `--apply` が `project_rule` へ
  反映するまで観測できない（**未実測**。§0⑥の結論どおり、現時点での効果は0）
- `global_rule` の二重計上（§4-1）が実際にどの程度発生しているかは未計測
- `project_path` 絶対パス形式（§1.3）による突合漏れの実件数は未計測（本設計のスコープ外
  だが、既存動作としての規模感は取っていない）

## 実行契約（本設計の効果測定）

- 起点: 2026-09-03（本設計 commit 時点）
- 再測条件: `reflect_apply_events.jsonl` に `reflect_target_kind == "project_rule"` かつ
  `reflect_target_path` の `repo_id` が読み手の `project_root` と異なるイベントが1件以上
  記録された時点
- 実行者: 頭（次に柱2を計測するセッション）
- 判定者: ユーザー
- 期限: 2026-09-30、または上記イベントが3件到達のいずれか早い方
- 期限超過時: 実害0件が継続しているなら、本設計の実装着手を icebox へ格下げするかを
  ユーザーへ提起する（`pillars-before-polish.md` の判断基準どおり、健全性ではなく
  磨き込みに分類されるため）
