# #601: 柱2 scope 判定に `reflect_target_path` を併用する設計（第2版）

> **第2版は巡1レビュー（Claude系cold read・対象 SHA `240ae4ac`・判定「設計修正要」・
> [Must]7件・[Should]多数）への対応**。頭（司令塔）の裁定5点（起点帰属／fail-closed／
> 判定不能の可視化／対象種別の拡張／解析の単一ソース化）に従い、**判定方式を全面的に
> 作り直した**。第1版の「`project_rule` だけを `repo_id` 比較し、他は message
> ヒューリスティックへフォールバックする」設計は、fallback 経路が過大計上を素通りさせる
> ことが実例つきで指摘され撤回した。
>
> **第2版で判明した新事実（§1.2）**: `global_rule`／`global_claude_md`／グローバル
> `skill` への反映も、`normalize_reflect_target_path` が `repo_identity()` を無条件に
> 呼ぶため **`~/.claude`（claude-config repo）を repo_id とする `repo_id:relative_path`
> 形式**になる。第1版は「global 種別には repo 情報が無い」という誤った前提で書かれていた
> （実行して確認しないまま書いた）。この事実により、`project_rule` だけでなく全種別を
> 単一の「repo アンカー比較」で扱う、より単純で頑健な設計に変わった（§2）。

対象: `#601`（`#600` レビューの [Should] を切り出したもの）。本文書は**設計のみ**。コードは1行も変更しない。

## 巡1 [Must] 対応表

| # | 指摘の要旨 | どう直したか |
|---|---|---|
| 1 | 判定不能時に message ヒューリスティックへ fallback し、除外の裁定が素通りする | §2.2 で fallback を完全に廃止。`has_pillar2_fields=True` の行は target 情報だけで判定し、判定不能は `undetermined`（除外＋可視化）へ倒す。message ヒューリスティックは `has_pillar2_fields=False`（旧データ・そもそも target 情報が存在しない行）専用に限定（§2.6 対象外） |
| 2 | `global_rule`／他PJの `project_claude_md`／他PJ repo 内の `skill` が対象外で誤計上できる | §2.1-§2.4 で `reflect_target_kind` を判定に使わず、**`reflect_target_path` の repo アンカー一致だけ**で全種別を一様に扱う設計に変更。`global_rule`／`global_claude_md`／グローバル `skill` は「グローバルアンカー一致」を経由し起点PJ帰属（§2.3） |
| 3 | repo rename/move で自PJの過去反映が `other-project` に誤って落ち、`measured=True` のまま隠れる | §2.4 で `PJ_SLUG_ALIASES` を使った同一親ディレクトリ rename の alias 展開を実装。alias 未収載の rename（親ディレクトリが変わる移動等）は残存リスクとして§6に明記し、「安全側（過小計上）に倒れる」ことを保証 |
| 4 | `repo_id:relative_path` の `:` 分割で repo ディレクトリ名や Windows drive letter の `:` と衝突しうる | §2.2 で**汎用的な `:` 分割を一切行わない**設計に変更。既知の2つのアンカー文字列（自PJ repo_id・グローバル home repo_id）に対する `startswith(anchor + ":")` の prefix 一致のみを使い、未知の repo_id を文字列から抽出することをやめた |
| 5（§2「global_rule 裁定」1件目） | 「現行ヒューリスティック維持」は二重計上を認めながら「難しいから触らない」と言っているだけ | 頭裁定どおり**起点帰属**を採用（§2.3）。message ヒューリスティックは完全に廃止 |
| 6（§2「global_rule 裁定」2件目） | 帰属軸（起点 vs 反映先）が種別ごとに矛盾している | §2.1 で明文化: **repo に属す反映先（`project_rule`／`project_claude_md`／project-local `skill`）は反映先PJ帰属、repo に属さない共有 home（`global_rule`／`global_claude_md`／グローバル `skill`）は起点PJ帰属**。判定基準は「target のアンカーがどちらに一致するか」であり種別ラベルではないため、種別と反映先が矛盾するデータ（手編集等）でも軸は割れない |
| 7（§3「判定不能を除外に倒す向き」） | 除外件数が既存 health に反映されず `measured` も落とさないため「0件」と「判定不能で落ちた」を区別できない | §2.5 で `target_scope_undetermined_row_count` を既存 `health` dict に追加し、1件でもあれば `degraded`（→`measured=False`）に含める。`other_project_target_row_count`（確信を持って他PJと判定できた件数）も別途可視化し、両者を区別する |

（[Should] への対応は §2〜§5 内に統合。対応不要と判断したものは無し——全項目に §2〜§5 のどこかで応答している）

## 0. Round 0 完成条件

### ① 守る対象

柱2（「実際に反映された改善」件数）として自PJの board に表示される件数に、**他PJで反映された
改善が紛れ込むこと**（過大計上）。加えて（巡1 [Must]7 由来）、**判定できないケースが
「0件」として静かに扱われ、人間が「本当に0件」と「判定できず落ちた」を区別できないこと**。

### ② 信頼境界（誰の能力を脅威に数えるか）

**自分たちの運用ミスのみ**。数えるのは: `message` が偶然 generic に見える他PJ由来の
correction／複数PJが同一 `corrections.jsonl` を共有する運用そのものが持つ曖昧さ／
反映先イベントの手編集／PJ の repo rename・移動。
**数えない**: 悪意ある偽装・意図的な水増し・第三者による改竄。

### ③ 対象外

- `message` ヒューリスティック自体の精度改善（`always`/`never`/モデル名キーワード等）。
  このヒューリスティックは `has_pillar2_fields=False`（反映先情報が存在しない旧データ）
  専用に限定し、それ以外では一切使わない（§2.6）
- 柱2以外（柱1・柱3・柱4）の scope 判定
- `#379` 新設凍結の解除。本設計は新しいストア・チャネルを一切作らない。追加するのは
  既存の `count_applied_reflections` 戻り値 dict（`health`）内のキー2つのみ
- `corrections.jsonl` を PJ ごとに物理分割する等の構造変更
- `reflect_apply_events` の schema 変更（`repo_id`/`relative_path` を別フィールドに
  分離する等）。§2.7 で提案はするが、実装可否は本設計の対象外（頭裁定に委ねる）
- `hook`/`pitfall_memory` への反映測定（既存の `PILLAR2_NOT_MEASURED_TARGETS` の対象。
  本設計が触るのは `has_pillar2_fields=True` の行の scope 判定のみ）
- macOS APFS の大小文字別名・シンボリックリンク経由の重複repo検出（§6 残存リスク）

### ④ blocking の定義

| | 内容 | 出所 | 第2版での扱い |
|---|---|---|---|
| (a) | `project_rule` の他PJ反映を `same-project`/`global-looking` として計上できる | 巡1 [Must]1・issue本文 | 解消（§2.2 fail-closed・repo アンカー比較） |
| (b) | `global_rule`/`project_claude_md`/他PJ `skill` が対象外で誤計上できる | 巡1 [Must]2 | 解消（§2.1 種別非依存のアンカー判定） |
| (c) | repo rename 後に自PJ分が誤って除外される（過小計上が不可視） | 巡1 [Must]3 | 部分解消（§2.4 alias。親ディレクトリが変わる rename は残存リスク・§6） |
| (d) | 判定不能が「0件」として不可視になる | 巡1 [Must]7 | 解消（§2.5 `target_scope_undetermined_row_count` → `degraded`） |
| (e) | `:` 分割が repo_id/relative_path 内の `:` と衝突する | 巡1 [Must]4 | 構造的に解消（§2.2 は分割せず prefix 一致のみ使用） |
| (f) | `global_rule` の帰属軸が種別ごとに矛盾する | 巡1 [Must]5・6 | 解消（§2.1 で単一の判定軸に統一） |

### ⑤ 検証方法

§5 の陰性試験（4件・各「壊す不変条件」「通したい検査経路」を明記）＋陽性対照（1件）＋
統合試験（§5.4）を実測する。

### ⑥ この成果物が目的の物差しで削る量

**目的の単位**: 誤計上しうる件数（柱2 `count` に紛れ込む他PJ由来の反映行数）。

**実測値（再現手順つき・取得時刻 2026-09-03T01:31:17Z・commit `240ae4ac`）**:

```
$ python3 -c "
import json
from pathlib import Path
p = Path('~/.claude/evolve-anything/reflect_apply_events.jsonl').expanduser()
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
print('event_count', len(rows))
for r in rows:
    print({k: r.get(k) for k in ('correction_id','reflect_target_kind','reflect_target_path','project_path')})
"
event_count 2
{'correction_id': 'b195b2bf9ef54cc487a087ae3cb374fd', 'reflect_target_kind': 'project_rule', 'reflect_target_path': '/Users/matsukaze-takashi/matsukaze-utils/evolve-anything:.claude/rules/pillars-before-polish.md', 'project_path': None}
{'correction_id': 'dbcfcecff3f34b0481249046ce5bdc6e', 'reflect_target_kind': None, 'reflect_target_path': None, 'project_path': None}
```

`reflect_target_kind` が判定可能なのは1件のみ（`project_rule`）で、`reflect_target_path` の
repo アンカーは `/Users/matsukaze-takashi/matsukaze-utils/evolve-anything`——これは
`project_root` 自身の repo_id と一致する（同一PJ）。**この修正が現時点で実際に取り除く
誤計上は 0 件**（全事象を目視で確認済み。上記コマンドの出力2行が全件であることは
`event_count 2` で担保される——サンプリングではなく全数）。

**将来リスクの実測代理値**（0件と決めつけないための傍証。目的指標そのものではないので
⑥には算入しない。再現手順つき・取得時刻 2026-09-03T01:31:24Z）:

```
$ python3 -c "
import json
from pathlib import Path
p = Path('~/.claude/evolve-anything/corrections.jsonl').expanduser()
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
applied = [r for r in rows if r.get('reflect_status') == 'applied']
print('applied_count', len(applied))
for r in applied:
    print(r.get('correction_id'), r.get('project_path'))
"
applied_count 5
411114e30ec74a1aacf14a1c0572daff evolve-anything
c25c83983e1f4a0a98b11133a02cab66 updater-index
74f0215b71b847a388f3a5af55e24b22 updater-index
0f94d4a14da5472c93010b644f6ce46b updater-index
6aa192618b1043c3a8afe19ecab18c85 evolve-anything
```

`reflect_status="applied"` の5件中3件（`c25c8398`/`74f0215b`/`0f94d4a1`）が
`project_path=updater-index`（＝`scripts/lib/pillar2_metrics.py:22` の
`PRE_SCHEME_APPLIED_BASELINE` と同一集合）。これらは `has_pillar2_fields=False`
（新方式導入前）のため `pre_scheme_excluded_count` に落ち、**柱2の `count` には
含まれていない**（実害は無い）が、`message` ヒューリスティックが実際に他PJの
correction を通す実例として使える（`evolve-anything` から見て `updater-index`
由来3件は現行コードだと `global-looking` に分類される——`message` に generic な
文面が含まれるため）。

**第2版で判明した追加の露出面（§1.2）**: `global_rule`/`global_claude_md`/グローバル
`skill` への反映も repo アンカー付き形式になることが判明した。これにより第1版で
想定していたより広い種別が同じ脆弱性（message ヒューリスティックによる過大計上）に
さらされていたことが分かったが、**実データではこの経路のイベント自体が0件**
（上記 `event_count 2` の内訳どおり）のため、⑥の実測値そのものは変わらない。

**結論**: ⑥は実測ベースでは **0**。理由は運用の初期段階で他PJの `--apply` が
まだ十分に走っていないためで、修正の正しさとは無関係。着手判断は本設計の対象外
（人間裁定）とし、§7 実行契約に判断材料を残す。

## 1. 現状の事実

### 1.1 現行の scope 判定（`message` ベース、`reflect_target_path` 未使用）

第1版 §1.1 と同じ（変更なし）。要点のみ再掲:

- `scripts/lib/pillar2_metrics.py:207-239` `count_applied_reflections` は
  `folded_correction.base`（生の correction dict）だけを `_pillar2_project_scope` に
  渡しており、`folded_correction` 自身が持つ `reflect_target_kind`/`reflect_target_path`
  （`reflect_fold.py:55-56`）を一切参照していない
- `skills/reflect/scripts/reflect.py:179-211` `classify_project_scope` は
  `project_path` が現在PJと不一致のとき、`message` に `always`/`never`/モデル名が
  含まれるか、DB名・ファイルパスらしき文字列が含まれるかだけで
  `global-looking`/`project-specific-other` を振り分ける（デフォルトは
  `global-looking`）

### 1.2 〔第2版で新規検証〕`reflect_target_path` は全種別で repo アンカー形式になりうる

`scripts/lib/reflect_apply_match.py:130-144` `normalize_reflect_target_path` は
**`reflect_target_kind` の値に関わらず無条件で** `repo_identity()` を呼ぶ。`~/.claude`
自体が git 管理下（`todoroki-godai/claude-config`）であるため、`global_rule`/
`global_claude_md`/グローバル `skill` への反映も `repo_id:relative_path` 形式になり、
その `repo_id` は **`~/.claude`（claude-config repo）の絶対パス** になる。

実測（再現コマンドと出力・取得時刻 2026-09-03T01:20頃・commit `240ae4ac`）:

```
$ PYTHONPATH=/Users/matsukaze-takashi/wt/ea-601/scripts/lib python3 -c "
from reflect_apply_match import classify_reflect_target_kind, normalize_reflect_target_path
p = '/Users/matsukaze-takashi/.claude/rules/model-routing.md'
print('kind', classify_reflect_target_kind(p))
print('norm', normalize_reflect_target_path(p))
"
kind global_rule
norm /Users/matsukaze-takashi/.claude:rules/model-routing.md
```

```
$ PYTHONPATH=/Users/matsukaze-takashi/wt/ea-601/scripts/lib python3 -c "
from evolve_decision_ids import repo_identity
from reflect_apply_match import classify_reflect_target_kind
import glob
p = glob.glob('/Users/matsukaze-takashi/.claude/skills/*/SKILL.md')[0]
print(p)
print('identity', repo_identity(p))
print('kind', classify_reflect_target_kind(p))
"
/Users/matsukaze-takashi/.claude/skills/pair-agent/SKILL.md
identity {'repo_id': '/Users/matsukaze-takashi/.claude', 'relative_path': 'skills/gstack/pair-agent/SKILL.md', 'worktree_root': '/Users/matsukaze-takashi/.claude'}
kind skill
```

**この実測により、第1版 §2.4 の前提（「global 種別は反映先パスに帰属情報を持たない」）は
誤りだったと判明した**。正しくは「`global_rule`/`global_claude_md`/グローバル `skill`
は、`project_root` とは別の**共有 home リポジトリ**（`~/.claude`）を repo_id とする」
という事実であり、これは第1版で想定していなかった第3のアンカー（自PJ でも他PJ でもない
「共有 home」）の存在を意味する。これが第2版の設計（§2）の基礎になっている。

**未実測・別経路の既知の異常（本設計の対象外として明記）**: 上記2番目の実測で
`normalize_reflect_target_path` 側の `repo_id`（後続の grep で `/Users/matsukaze-takashi/
.claude/skills/gstack` を含む値が出た——本文書のこの版では確認のみで深掘りしていない）と
直接 `repo_identity()` を呼んだ結果の `repo_id`（`/Users/matsukaze-takashi/.claude`）が
食い違う挙動を観測した。これは `normalize_reflect_target_path` が `path.resolve()`
（symlink 解決込み）した後の path を `repo_identity()` に渡すのに対し、直接呼び出しでは
symlink 解決前の path を渡したことによる差だと推測されるが、**未検証**。この差異が
本設計の repo アンカー比較（§2.2）にどう影響するかは、§5.4 の統合試験（実 git repo・
実 symlink を使う）で検証対象に含める。本設計自体は `normalize_reflect_target_path`
が生成した文字列をそのまま prefix 比較するだけなので、**生成側のこの異常を修正する
ことは本 issue の対象外**（`reflect_apply_match.py` 側の別 issue）。

### 1.3 `project_path` の実データ分布

`~/.claude/evolve-anything/corrections.jsonl`（259件・取得時刻 2026-09-03T01:31:17Z）:

```
$ python3 -c "
import json
from pathlib import Path
from collections import Counter
p = Path('~/.claude/evolve-anything/corrections.jsonl').expanduser()
rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
print('total', len(rows))
c = Counter(r.get('project_path') for r in rows)
for k, v in c.most_common(20):
    print(v, repr(k))
"
total 259
53 'evolve-anything'
40 '/Users/matsukaze-takashi/updater/amamo'
36 'updater-index'
30 '/Users/matsukaze-takashi/matsukaze-utils/rl-anything'
20 'receipt'
14 'figma-to-code'
13 'atlas-breeaders'
9 'docs-platform'
8 None
8 'aws-cost-guardian'
8 'amamo'
6 '/Users/matsukaze-takashi/updater/sys-bots'
5 '/Users/matsukaze-takashi/updater/docs-platform'
5 'ai-daily-report'
4 'sys-bots'
```

**bare slug と絶対パスの2形式が混在**している。特に `evolve-anything`（53件・bare slug）
と `/Users/matsukaze-takashi/matsukaze-utils/rl-anything`（30件・絶対パス・旧ディレクトリ名）
は、**同一プロジェクトが `rl-anything` から `evolve-anything` へ rename された実例**
（`scripts/lib/pj_slug.py:47-49` の `PJ_SLUG_ALIASES = {"rl-anything": "evolve-anything"}`
と対応）。両者の絶対パスの親ディレクトリは同一
（`/Users/matsukaze-takashi/matsukaze-utils/`）——同一階層内での basename rename。
この実例が §2.4 の rename alias 設計の直接の根拠になっている。

### 1.4 既存の重複実装確認

- `scripts/lib/skill_origin.py` の `classify_skill_origin` はプラグインスキルの出自判定
  （`installed_plugins.json` ベース、"どのプラグインがこのスキル名をインストールしたか"）
  であり、本設計が必要とする「反映先パスがどの repo に属すか」とは判定対象・入力の両方が
  異なる（`classify_skill_origin` は `installed_plugins.json` を読むだけで、対象ファイルの
  実パスや git identity は見ない）。本設計は git identity ベースの repo アンカー比較のみで
  `skill` 種別も統一的に扱える（§2.1）ため、`classify_skill_origin` を呼び出す必要が無い
  ——**代替ではなく、そもそも解こうとしている問題が違う**（§2.6 で明記）
- `scripts/lib/reflect_apply_match.py` に `normalize_reflect_target_path`（生成）が既にある。
  第1版は pillar2_metrics.py 側で `repo_id:relative_path` を再解析しようとしたが、
  第2版はこれをやめ、**生成側に prefix 一致ヘルパーを追加して単一ソース化**する（§2.2）

## 2. 判定の設計

### 2.1 設計の軸: 種別ラベルではなく repo アンカー一致で判定する

`reflect_target_kind` の値を scope 判定に**使わない**（判定不能な場合の `other_kind_count`
バケット処理でのみ、既存どおり引き続き使う——これは scope とは別の既存カウンタで変更なし）。

代わりに、`reflect_target_path`（正規化済み `repo_id:relative_path` 文字列、または
repo に属さない場合の解決済み絶対パス）が次の**2つの既知アンカー**のどちらに一致するかで
判定する:

1. **自PJアンカー**: `project_root` 自身の `repo_id`（＋ rename alias。§2.4）
2. **共有 home アンカー**: `~/.claude`（claude-config repo）の `repo_id`

一致の可否で3状態に分岐する:

| target のアンカー | 扱い |
|---|---|
| 自PJアンカーに一致 | `same-project`（そのまま計上対象） |
| 共有 home アンカーに一致 | **起点PJ帰属**（§2.3 へ進む。`same`/`other`/`undetermined` のいずれかに解決） |
| どちらにも一致しない（かつ両アンカーとも解決できている） | `other-project`（確信を持って他PJ。除外＋可視化） |
| アンカーが解決できない、または target 自体に repo 情報が無い | `undetermined`（判定不能。除外＋可視化＋`measured=False`） |

これにより `project_rule`/`project_claude_md`/project-local `skill`（＝自PJまたは他PJの
リポジトリに属す反映先）と `global_rule`/`global_claude_md`/グローバル `skill`（＝共有
home に属す反映先）が**同一の比較ロジックで**扱われる。`reflect_target_kind` の値と
`reflect_target_path` の実際の repo が食い違うデータ（手編集等の運用ミス）が来ても、
判定は常に実際の repo アンカーに従うため頑健である。

### 2.2 repo アンカー一致の実装（`:` を分割しない）

```python
# scripts/lib/reflect_apply_match.py に追加（正規化の生成側と対にする）
def reflect_target_matches_repo(normalized_target_path: str, repo_id: str) -> bool:
    """normalize_reflect_target_path() が返した文字列が、指定 repo_id に属すかを判定する。

    repo_id や relative_path 自体に含まれうる ':' の位置に影響されない
    （分割ではなく既知文字列の prefix 一致だけを行うため）。
    """
    if not normalized_target_path or not repo_id:
        return False
    return (
        normalized_target_path == repo_id
        or normalized_target_path.startswith(repo_id + ":")
    )
```

`repo_id` は常に「呼び出し側が別途 `repo_identity()` で確定させた既知の文字列」であり、
`reflect_target_path` から未知の `repo_id` を抽出する処理は設計から排除した
（巡1 [Must]4 の解消）。Unicode 正規化（NFC/NFD）の差を吸収するため、両辺とも
`unicodedata.normalize("NFC", ...)` を通してから比較する（§5.3 陰性試験・未探索
入力クラスで検証）。

### 2.3 起点PJ帰属（共有 home アンカーに一致した場合）

```python
# scripts/lib/pillar2_metrics.py
def _origin_project_match(base: dict, project_root: Path) -> Optional[bool]:
    """base correction の project_path から起点PJを判定する。message は見ない。

    True: project_root と一致（bare slug の alias 一致、または絶対パスの basename
          rename alias 一致、または絶対パスの正規化一致）
    False: project_path は存在するが project_root と一致しない（確信を持って他PJ）
    None: project_path が null/空——判定不能
    """
    project_path = base.get("project_path")
    if not isinstance(project_path, str) or not project_path:
        return None
    from pj_slug import pj_slug_aliases_for, resolve_pj_slug, canonical_pj_slug
    from rl_common.persistence import project_name_from_dir

    matching_slugs = pj_slug_aliases_for(resolve_pj_slug(project_root))
    matching_slugs.update(pj_slug_aliases_for(project_name_from_dir(str(project_root))))
    if project_path in matching_slugs:
        return True
    if os.path.isabs(project_path):
        basename = Path(project_path).name
        if canonical_pj_slug(basename) in matching_slugs or basename in matching_slugs:
            return True
        if _normalize_path(project_path) == _normalize_path(str(project_root)):
            return True
    return False
```

`pj_slug_aliases_for`/`resolve_pj_slug`/既存の `_normalize_path`
（`skills/reflect/scripts/reflect.py:214-216` から流用）はすべて既存関数の再利用であり、
**`message` の内容は一切参照しない**（巡1 [Must]5・6 の解消）。

### 2.4 rename alias 展開（自PJアンカーの拡張）

```python
# scripts/lib/pillar2_metrics.py
_PILLAR2_PROBE_FILENAME = ".pillar2-repo-probe"  # 存在しないファイル名でよい
# evolve_decision_ids.repo_identity() の契約（同モジュール 51-53行）:
# 「path の親ディレクトリが存在すれば、path 自体が存在するかは問わない」ため、
# project_root 配下の非存在ファイル名を渡してよい。

def _project_repo_id_aliases(project_root: Path) -> set[str]:
    from evolve_decision_ids import repo_identity
    from pj_slug import PJ_SLUG_ALIASES

    identity = repo_identity(str(project_root / _PILLAR2_PROBE_FILENAME))
    own_repo_id = identity.get("repo_id")
    if not own_repo_id:
        return set()
    aliases = {_nfc(own_repo_id)}
    basename = Path(own_repo_id).name
    for old, new in PJ_SLUG_ALIASES.items():
        if new == basename:
            aliases.add(_nfc(str(Path(own_repo_id).parent / old)))
    return aliases
```

**契約の明記（巡1 [Must]3 の裁定どおり）**: `repo_id` は worktree 間では安定するが、
**repo 自体の移動・rename 間では安定しない**。本設計が rename を検知できるのは
`PJ_SLUG_ALIASES` に登録済みかつ**同一親ディレクトリ内での basename rename**
（実例: `rl-anything`→`evolve-anything`、§1.3）に限られる。親ディレクトリが変わる
移動（別ボリューム・別ユーザーディレクトリへの移動等）は alias 展開の対象外であり、
該当イベントは `other-project` として扱われる（＝過小計上。過大計上より安全な方向——
§6 残存リスクに明記）。

同じ alias 展開は共有 home アンカー側にも将来必要になりうる（`~/.claude` 自体が
rename された場合）が、**現状 `~/.claude` は一度も rename されていない**
（実測確認手段なし・未実測。発生したら同じパターンで対応する）ため本設計では
実装しない（③対象外）。

### 2.5 判定不能の可視化（新ストアを作らず既存 `health` dict へ追加）

```python
# count_applied_reflections() の戻り値 health dict に以下2キーを追加
"target_scope_undetermined_row_count": <int>,  # 判定不能で除外した件数
"other_project_target_row_count": <int>,       # 確信を持って他PJと判定し除外した件数
```

`degraded` の判定式（`pillar2_metrics.py:262-278`）に
`or target_scope_undetermined_row_count > 0` を追加する。これにより判定不能が1件でも
あれば `measured=False` になり、`count=0` が「本当に0件」なのか「判定できず落ちた」のか
を区別できる（巡1 [Must]7 の解消）。`other_project_target_row_count` は
`other_kind_count` と同様、`degraded` には含めない（確信を持った除外は「測定できている」
状態であり、既存の `other_kind_count`/`legacy_unverified_count` の一部——
`legacy_unverified_count`——だけが `degraded` に含まれる非対称性と整合する）。

**新ストア・新 weak_signal channel は作らない**（`#379` 凍結を守る）。追加するのは
既存の戻り値 dict 内のキーのみで、永続化されるファイルは増えない。

### 2.6 message ヒューリスティックの残存範囲（対象外の明確化）

`has_pillar2_fields=False` の行（＝反映先イベントが無い、または旧方式のレコードで
`reflect_target_path` 自体が存在しない行）は、target アンカー比較の材料が無いため、
**既存の `_pillar2_project_scope`/`classify_project_scope`（message ヒューリスティック）を
そのまま使う**（変更なし）。この経路は `legacy_unverified_count`/`pre_scheme_excluded_count`
にしか到達しない（`eligible` には入らない——`count_applied_reflections` の既存ロジックで
`has_pillar2_fields=False` は必ず `continue` する）ため、**この経路の message
ヒューリスティックが柱2の `count` を汚すことは無い**（issue #601 が指摘した過大計上の
経路には現れない。①守る対象に対して無害）。

### 2.7 `count_applied_reflections` 内のループ再構成（擬似コード）

```python
for folded_correction in folded:
    base = folded_correction.base

    if folded_correction.has_pillar2_fields:
        scope_state, scope_label = _pillar2_target_scope(folded_correction, Path(project_root))
        if scope_state == "other":
            other_project_target_row_count += 1
            continue
        if scope_state == "undetermined":
            target_scope_undetermined_row_count += 1
            continue
        # scope_state == "same" のみ以降へ進む
    else:
        legacy_scope = _pillar2_project_scope(base, Path(project_root))  # 既存関数（変更なし）
        if legacy_scope not in ("same-project", "global-looking"):
            continue

    if base.get("invalidated"):
        invalidated_count += 1
        continue
    if base.get("reflect_status") != "applied":
        continue
    if not folded_correction.has_pillar2_fields:
        # 既存の pre_scheme/legacy 分岐（変更なし）
        ...
        continue
    if folded_correction.reflect_target_kind == "other":
        other_kind_count += 1
        continue
    # 以降（時間窓・eligible.append）は変更なし
```

`_pillar2_target_scope` は §2.1〜§2.3 のロジックを合成する:

```python
def _pillar2_target_scope(folded_correction, project_root: Path) -> tuple[str, str]:
    target_path = folded_correction.reflect_target_path
    if not target_path or ":" not in _nfc(target_path):
        return ("undetermined", "no_repo_info")
    target_path = _nfc(target_path)

    own_aliases = _project_repo_id_aliases(project_root)
    if any(reflect_target_matches_repo(target_path, a) for a in own_aliases):
        return ("same", "same-project-repo")

    global_repo_id = _global_home_repo_id()
    if global_repo_id and reflect_target_matches_repo(target_path, global_repo_id):
        origin = _origin_project_match(folded_correction.base, project_root)
        if origin is True:
            return ("same", "global-scope-origin-match")
        if origin is False:
            return ("other", "global-scope-origin-other")
        return ("undetermined", "global-scope-origin-unknown")

    if own_aliases and global_repo_id:
        return ("other", "other-project-repo")

    return ("undetermined", "anchor-unresolved")
```

`_global_home_repo_id()` は `evolve_revert._target.global_rules_root()`（既存関数・
B レーンと共有）の親ディレクトリに対して `_project_repo_id_aliases` と同じ
`repo_identity()` 呼び出しパターンを使う（`~/.claude/rules` の親を辿れば `~/.claude`
の repo_id が得られる。§1.2 で実測確認済み）。

## 3. 検討したが採らなかった案

### 案A（第1版から継承）: `corrections.jsonl` を PJ ごとに物理分割する

不採用。`learning_physical_unification_not_the_goal` の教訓どおり、union read
（＝本設計のフィルタリング）で欠落なく対処できるなら物理分割は over-engineering。

### 案B: `classify_skill_origin` を `skill` 種別の判定に組み込む

不採用（§1.4）。`classify_skill_origin` は `installed_plugins.json` ベースの
「プラグイン由来か」判定であり、本設計が必要とする「反映先の実パスがどの git repo に
属すか」とは対象が異なる。repo アンカー比較（§2.1-§2.2）だけで `skill` 種別も
（グローバル/project-local を問わず）統一的に扱えるため、別モジュールを組み合わせる
必要が無い。

### 案C: `reflect_apply_events` の schema を変更し、`repo_id`/`relative_path` を
別フィールドに分離する（`repo_id:relative_path` の joined 文字列をやめる）

**実装は提案するが、本 issue のスコープには含めない**（頭裁定に委ねる・③対象外）。
理由: 現行の joined 文字列フォーマット自体（`normalize_reflect_target_path` の
`f"{repo_id}:{relative_path}"`）は、`repo_id`（絶対パス。POSIX ではディレクトリ名に
`:` を含められる）や `relative_path`（ファイル名に `:` を含められる）のどちらにも
`:` が出現しうるため、**汎用的な分割では原理的に一意復元できない**（巡1レビュー
[Should]）。本設計（§2.2）はこの分割を回避する形で当座しのぎしたが、他の将来の
reader が同じ落とし穴を踏む可能性は残る。**恒久対応としては**、区切り文字を実際の
パスにほぼ出現しない制御文字（例: `"\x1f"` ASCII Unit Separator）に変更するか、
イベントの JSON に `reflect_target_repo_id`/`reflect_target_relative_path` を別キーで
持たせる方が良い。ただし: (1) 既存2件のイベントの再正規化が必要、(2)
`reflect_apply_events` の schema 変更は `#587`/`#595` の管轄、(3) 現行データが2件しか
無く実害が出ていないため優先度は低い——という理由で、本設計では**提案のみに留め、
実装は別 issue（頭裁定）とする**。

### 案D: 判定不能を一律「自PJに数える」（安全側を過小計上でなく過大計上に倒す）

不採用。頭裁定（②fail-closed）どおり、柱2の目的（CLAUDE.md「4. 信頼: 表示する数字が
嘘をつかない」）は過大計上の防止を優先するため、判定不能は常に除外側に倒す。

## 4. 残存リスク（受け入れるもの）

1. **親ディレクトリが変わる repo 移動**は rename alias（§2.4）で拾えず、該当イベントが
   `other-project` として除外される（過小計上。過大計上より安全な方向であることは
   §2.4 に明記）
2. **共有 home（`~/.claude`）自体の rename/移動**は本設計の対象外（③・§2.4末尾）
3. **macOS APFS の大小文字別名**（同一物理ディレクトリを異なる大小文字で参照した場合）
   は未対応・未検証。`repo_identity()` は `.resolve()` を経由するため通常は正規化される
   はずだが、実機での大小文字別名テストは行っていない（§6 未計測）
4. **symlink 経由の repo アンカー**は §1.2 で観測した `normalize_reflect_target_path` の
   異常（生成側の resolve タイミング差）の影響を受けうる。本設計は生成側の出力を
   そのまま信じるため、生成側にバグがあれば本設計の判定も引きずられる（§5.4 の統合
   試験で実 symlink を使い検証する）

## 5. 検証方法

### 5.1 陰性試験（4件。各「壊す不変条件」「通したい検査経路」を明記。重複なし）

| # | 分類 | 変異内容 | 壊す不変条件 | 通したい検査経路 |
|---|---|---|---|---|
| 1 | ①要素を消す | `_project_repo_id_aliases` から `PJ_SLUG_ALIASES` の展開処理を削除し、`own_repo_id` 単体だけを返すようにする | rename 後も自PJの過去反映を `same-project` と判定できること（§2.4） | fixture: 旧 repo_id（`.../rl-anything` 相当）を持つ `project_rule` イベント1件。現在の `project_root` は rename 後（`.../evolve-anything` 相当）。正常時 `count=1`。変異後は alias が展開されず `other-project` へ落ちて `count=0`（赤） |
| 2 | ②意味を壊す | `reflect_target_matches_repo` の prefix 一致を `not normalized_target_path.startswith(...)` へ反転する（真偽を反転） | 他PJの `project_rule` 反映を `same-project` として誤計上しないこと（①守る対象そのもの） | fixture: 他PJ（`/repos/beta`）の `project_rule` イベント1件のみ。正常時 `count=0`（除外）。変異後は反転により `same` 判定になり `count=1`（赤・過大計上） |
| 3 | ③分散・入替 | `_pillar2_target_scope` の呼び出しをループ内で使い回すバグを模す（1件目の `folded_correction` に対して計算した scope をキャッシュし、以降の全行に使い回す実装ミス） | 各行が自分自身の `reflect_target_path` に基づいて独立に判定されること（グループ化・入替に対する頑健性） | fixture: 1件目=他PJの `project_rule`（本来 `other`）、2件目=自PJの `project_rule`（本来 `same`）。正常時 `count=1`（2件目のみ）。変異後は1件目の scope（`other`）が2件目にも使い回され `count=0`（赤・過小計上で検出） |
| 4 | ④検査を無効化する | `degraded` の判定式から `or target_scope_undetermined_row_count > 0` を削除する | 判定不能が1件でもあれば `measured=False` になること（巡1 [Must]7） | fixture: `reflect_target_path=None`（`has_pillar2_fields=True` だが target 情報欠落——手編集を模す）の行1件のみ、他の degraded 条件は全て健全。正常時 `measured=False`。変異後は `measured=True` のまま（赤・可視化契約の消失） |
| 陽性対照 | — | 変異なし。正常データ3件: (a) 自PJの `project_rule` 反映、(b) 他PJの `project_rule` 反映、(c) `global_rule` 反映（起点PJ=自PJ・`project_path` が alias 一致） | 誤検出しないこと | `count=2`（(a)+(c)）、`other_project_target_row_count=1`（(b)）、`target_scope_undetermined_row_count=0`、`measured=True` を全て固定値でassertする（第1版の自己矛盾を解消——repo-scope と global-scope の両方を同時に含めた上で期待値を1つに確定させた） |

### 5.2 未探索入力クラスの一覧（巡1レビュー §4 の列挙に対応）

| 入力クラス | 本設計での扱い | 対応の有無 |
|---|---|---|
| `reflect_target_path` が空文字列／空白のみ | `":" not in target_path` で `undetermined` | 対応済み（§2.7 冒頭ガード）。テスト追加要 |
| `:` が無い | 同上 | 対応済み・テスト追加要 |
| `:` が複数（relative_path 側にも `:` を含む） | prefix 一致のみを使うため無関係（§2.2）。テストで実証: `own_repo_id="X"` に対し `"X:notes:draft.md"` が正しく `same` になることを確認 | 対応済み・テスト追加要 |
| `reflect_target_kind` の空白・大小文字異常 | **本設計では scope 判定に `reflect_target_kind` を使わないため無関係**（§2.1）。この入力クラスは第2版の設計変更によって scope 判定上は無害化された（`other_kind_count` バケットへの影響は既存動作のまま・本設計の対象外） | 無効化（テスト不要と判断した理由を明記） |
| Unicode NFC/NFD の repo path | `_nfc()` で両辺正規化してから比較（§2.2） | 対応済み・テスト追加要（NFD 表現の repo_id で fixture を作る） |
| symlink 経由の同一 repo | §1.2 で異常を観測済み。§5.4 統合試験で実 symlink を使い検証 | 未検証（統合試験へ委譲） |
| case-insensitive filesystem 上の大小文字別名 | 未対応（§4 残存リスク3） | 未対応・テスト無し（実機依存で構成困難） |
| repo rename・`pj_slug` alias | §2.4 で対応（同一親ディレクトリの basename rename） | 対応済み（陰性試験#1が該当） |
| main checkout と通常 worktree・sibling worktree | `repo_identity()` 自体が worktree 間で安定する `repo_id` を返す契約（`evolve_decision_ids.py:40-44` docstring）ため、本設計は追加対応不要——ただし統合試験で実 worktree を使い確認する | 未検証（統合試験へ委譲） |
| git 外・相対 `project_root` | `_project_repo_id_aliases` が空集合を返し、`own_aliases` 判定を素通りして global アンカー判定へ進む。どちらにもマッチしなければ `undetermined`（§2.7 最後の分岐） | 対応済み・テスト追加要 |
| 他PJの `project_claude_md`・`skill`・`global_rule` | §2.1 の統一ロジックで対応（種別非依存） | 対応済み（陽性対照・陰性試験#2でカバー） |
| 基底 `project_path` と反映先 repo が矛盾する（起点PJと反映先PJが異なる） | `project_rule` 等の repo-scope 反映は**反映先 repo だけ**で判定し `project_path` を無視するため、矛盾があっても判定は反映先基準で一貫する（§2.1 の設計意図どおり）。テストで明示する | 対応済み・テスト追加要（意図的な設計であることを固定するテスト） |
| 同一反映の同PJ・他PJ correction が混在した場合の filter 後 dedup | `eligible` へは `same` 判定の行しか入らないため、他PJの重複が dedup 前に既に除外される。既存の `groups` dict によるグルーピング（`pillar2_metrics.py:241-248`）は変更しない | 対応済み・テスト追加要 |

### 5.3 プロパティ（不変条件）としての固定

- **過大計上をしない**: `count` に入る行は必ず `scope_state == "same"` である
  （陰性試験#2・陽性対照で固定）
- **判定不能は不可視にしない**: `target_scope_undetermined_row_count > 0` ⟹
  `measured == False`（陰性試験#4で固定）
- **repo_id の解析に汎用分割を使わない**: `reflect_target_matches_repo` は
  `str.split` を一切呼ばない（実装レビュー時に grep で確認できる契約として明記）

### 5.4 統合試験（producer → fold → scope → dedup）

文字列を手組みした fixture だけでなく、次を実施する:

1. `tmp_path` に3つの実 git repo を `git init` で作る: `own_repo`（`project_root` 役）、
   `other_repo`（他PJ役）、`home_repo`（`~/.claude` 役。`HOME` 環境変数を
   `monkeypatch.setenv` で `tmp_path` 配下に差し替える）
2. `own_repo` 配下に `.claude/rules/x.md` を作り、実際に
   `classify_reflect_target_kind`/`normalize_reflect_target_path` を呼んで
   `reflect_target_kind`/`reflect_target_path` を得る（手組み文字列にしない）
3. `home_repo/rules/y.md` に対しても同様に呼び、`global_rule` の実際の正規化結果を得る
4. `other_repo` に対しても同様
5. これらを correction/event の JSONL 行として組み立て、
   `count_applied_reflections()` をエンドツーエンドで呼び、`count`/`health` の
   各キーを assert する
6. `own_repo` を `git mv`/ディレクトリ rename して `PJ_SLUG_ALIASES` 相当の
   エイリアスを設定し、rename 前に記録したイベントが rename 後も `same` と
   判定されることを確認する（陰性試験#1の統合版）
7. `own_repo` 配下に symlink（`.claude/skills/foo -> ../../shared-skills/foo`）を作り、
   §1.2 で観測した resolve タイミング差の影響を確認する

## 6. 未計測の項目

- 実装後にこの修正が実際に何件の誤計上を防いだかは、他PJの `--apply` が
  repo-scope/global-scope の反映へ進むまで観測できない（**未実測**。§0⑥の結論どおり、
  現時点での効果は0）
- macOS APFS の大小文字別名による影響（§4-3）は未計測
- `normalize_reflect_target_path` の symlink resolve タイミング差（§1.2）が実運用で
  どれだけ発生しているかは未計測
- `global_rule`/`global_claude_md`/グローバル `skill` への反映が実際にどれだけ
  発生しているか（現状0件・§0⑥）の推移は未計測

## 7. 実行契約（本設計の効果測定）

- 起点: 2026-09-03（第2版 commit 時点）
- 再測条件: `reflect_apply_events.jsonl` に repo-scope または global-scope の
  反映イベントが、`project_root` と異なるアンカーを持つ形で1件以上記録された時点
- 実行者: 頭（次に柱2を計測するセッション）
- 判定者: ユーザー
- 期限: 2026-09-30、または該当イベントが3件到達のいずれか早い方
- 期限超過時: 実害0件が継続しているなら、本設計の実装着手を icebox へ格下げするかを
  ユーザーへ提起する
