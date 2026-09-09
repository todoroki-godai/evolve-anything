# #542 — model-tiers.json の TIER_ORDER 固定を解消する設計

対象: issue #542。本文と訂正コメント（2026-08-24, codex 指摘）の両方を反映する。
訂正の要旨: 当初「`show`/`sync` が黙って無視する」は過大な一般化だった。正しくは
**load 自体は保持しており、落ちるのはテキスト render 経路（`show` テキストと
`sync` の routing_rule 生成）だけ**。`show --json` と agent/settings sync 適用は
未知 tier を正しく扱う。問題の性質は「無視される」ではなく
**「経路ごとに見える集合が違う」一貫性の欠如**。

---

## 完成条件（round 0）

① **守る対象**: `model-tiers.json`（正典）に書いた tier が、参照経路（`show` テキスト /
`show --json` / `sync` の routing_rule 生成 / `sync` の agent・settings 適用）によって
見える・見えないが分かれること。

② **信頼境界**: 自分（ユーザー本人）が正典ファイルを手で編集する運用ミスのみを脅威に数える。
悪意ある第三者・外部からの書込みは対象外（`model-tiers.json` は `~/.claude` 配下の
ローカル config で、外部入力を受け付けない）。

③ **対象外**（本変更では扱わない。理由つき）:
- **`ALLOWED_TIER_MODELS`（`MODEL_ALIASES` = opus/sonnet/haiku/fable）が Codex 等の
  外部 runtime のモデル名を受け付けない問題**。これは「未知の tier 名」ではなく
  「未知の **model** 値」の話で、別の不変条件（`tier_policy.py:181` の
  `model_key not in MODEL_ALIASES` 判定）に属する。今回 TIER_ORDER を直しても
  Codex は依然として model-tiers.json に登録できない。下記「実測: 二重管理 1件」参照。
  → 別 issue（起票は司令塔判断）に切り出すことを推奨する。
- `drift`（stale-mention advisory）の挙動変更。`tier_policy_drift.py` は `TIER_ORDER` を
  使っておらず（`config["tiers"].values()` を直接読む・確認済み）、今回無関係。
- config の schema 変更・新規フィールド追加。

④ **blocking の定義**: 「同一 `model-tiers.json` に対して、`show`（テキスト）が返す
tier 集合と `show --json` が返す tier 集合が食い違う」または「`sync` の
routing_rule 生成に含まれる tier 集合が `tiers` dict のキー集合と食い違う」状態。

⑤ **検証方法**: 下記「検証方法」節の契約テスト + 陰性試験 + 陽性対照。

⑥ **この成果物が目的文の物差しで削る量**:
物差し = 「正典ファイルに書いたのに、いずれかの参照経路で反映されない設定の件数」。

**実測（2026-09-09・`~/.claude/model-tiers.json` を read-only で確認）**:
```
$ python3 -c "import json; d=json.load(open('/Users/matsukaze-takashi/.claude/model-tiers.json')); print(list(d['tiers'].keys()))"
['HEAD', 'HARD', 'NORMAL', 'MECH', 'REVIEW']
```
現状 `tiers` は `TIER_ORDER` と同じ5件のみで、**未知 tier は現に0件**。
今この瞬間の実害（削る量）は **0**。過去に「JSON に手で追加したが反映されない」という
事故も、`git log` / issue コメントに実例の添付はなく確認できない（issue 本文の DEEP/CODEX
は説明用の作例で、実データではない）。

**ただし別軸で実害を1件確認した**: `~/.claude/model-tiers.json` に Codex の実値
（`gpt-5.6-sol` 等）を登録できないため、`~/.claude/rules/pipeline-routing.md:29` と
`~/.claude/rules/model-routing.md:7` の `refs/` 側が正典として運用されている
（`model-routing.md:1` コメント「Codex側との関係…は refs/model-routing.md を…読む」）。
これは model-tiers.json という単一正典が機能せず rules 文書に二重化している実例だが、
**原因は③で対象外とした `ALLOWED_TIER_MODELS` 側であり、TIER_ORDER 側ではない**。

**この設計の物差し上の効果は 0 件**（review.md の既定では「0 なら発注せず issue へ落とす」）。
それでも本設計書を書き切るのは、司令塔（team-lead）が issue #542 に対する設計書作成を
明示指示したため。**この評価はレビュー発注前にユーザーへ確認事項として提示すること**
（「実測 0 件だが、③の別軸実害 1 件を解消する設計変更を別 issue として切り出す方が
物差しに対する効果が大きい」という判断材料込みで）。⑥ の判定自体は下で扱う根本原因
（経路間の構造的不一致）が「未発症だが再現可能な欠陥」であることは変えない。

---

## 前提の evidence

| 前提 | 値 | 取得コマンド | 取得日 |
|---|---|---|---|
| `TIER_ORDER` の定義 | `("HEAD", "HARD", "NORMAL", "MECH", "REVIEW")` | `sed -n '55p' scripts/lib/tier_policy.py` | 2026-09-09 |
| `TIER_ORDER` の全参照箇所 | 5箇所（後述の表） | `grep -rn "TIER_ORDER" scripts/lib scripts/tests` | 2026-09-09 |
| 実行環境の `model-tiers.json` の tiers | `HEAD, HARD, NORMAL, MECH, REVIEW`（5件、TIER_ORDER と同一） | `python3 -c "import json; print(list(json.load(open('~/.claude/model-tiers.json'))['tiers'].keys()))"` | 2026-09-09 |
| `tier_policy_drift.py` は `TIER_ORDER` を使わない | 未使用（`config["tiers"].values()` を直接走査） | `grep -n TIER_ORDER scripts/lib/tier_policy_drift.py` → ヒットなし | 2026-09-09 |
| root conftest の HOME 隔離により既存テストは実 `~/.claude` 非依存 | `scripts/lib/tests/test_tier_policy.py:4-6` のコメントで確認 | Read | 2026-09-09 |
| Python 3.7+ の dict は挿入順を保持する（言語仕様） | `json.loads` はソースの key 出現順で dict を構築する（CPython 実装で確認） | 別紙「現状（実測）」節の再現手順 | 2026-09-09 |

---

## 現状（実測）

### `TIER_ORDER` を読んでいる箇所（全件・file:line）と用途分類

| file:line | コード | 用途 |
|---|---|---|
| `scripts/lib/tier_policy.py:55` | `TIER_ORDER = (...)` | 定義そのもの |
| `scripts/lib/tier_policy.py:177-178` | `if tier_key not in TIER_ORDER: raise ValueError(...)`（`set_tier` 内） | **妥当性を検証する**（`set` コマンドのタイポガード） |
| `scripts/lib/tier_policy_cli.py:86` | `for tier in tier_policy.TIER_ORDER:`（`_run_show` のテキスト出力ループ） | **並び順を決める**（が、実質は「集合をフィルタする」副作用を持つ） |
| `scripts/lib/tier_policy_sync.py:27` | `from tier_policy import DEFAULT_TIER_POLICY, TIER_ORDER` | import（用途は次項） |
| `scripts/lib/tier_policy_sync.py:192` | `for tier in TIER_ORDER:`（`render_routing_line` のループ） | **並び順を決める**（同上、routing_rule 生成の1行を組み立てる際に未知 tier を落とす） |

**3用途は混ざっていない**: 「並び順を決める」（cli.py:86, sync.py:192）と「妥当性を検証する」
（tier_policy.py:177-178）は別物。「集合の正典として使う」用途は無い
（`tiers` dict 自体が集合の正典で、`TIER_ORDER` は本来ただの表示順であるべきだった）。

### `load` が黙って落とす経路と `validate` が弾く経路（両方）

| 経路 | 未知 tier の扱い | file:line |
|---|---|---|
| `load_tiers_config` / `load_tier_policy` | **落とさない**（`tiers` dict 全件を保持） | `tier_policy.py:150-155` |
| `show --json`（`_run_show` の JSON 分岐） | 落とさない（`tiers` dict をそのまま JSON 化） | `tier_policy_cli.py:74-81` |
| `show`（テキスト、既定） | **`TIER_ORDER` でループするため落ちる** | `tier_policy_cli.py:86` |
| `sync` の routing_rule 生成（`render_routing_line`） | **`TIER_ORDER` でループするため落ちる** | `tier_policy_sync.py:189-201` |
| `sync` の agent/settings 適用（`_agent_target_plan`/`_settings_target_plan`） | 落とさない（`tiers` dict を直接引くのみ。`TIER_ORDER` 不参照） | `tier_policy_sync.py:99-183` |
| `set`（`set_tier`） | **`TIER_ORDER` に無ければ ValueError で拒否**（`load` ではなく明示的な validate） | `tier_policy.py:176-178` |

### silent stale / 経路間不一致の再現（一時 HOME・実 `~/.claude/model-tiers.json` は未変更）

再現コマンド（`TMPHOME` は都度生成する一時ディレクトリ。以下は実行時に使った値の記録）:

```bash
TMPHOME=$(mktemp -d); mkdir -p "$TMPHOME/.claude"
cat > "$TMPHOME/.claude/model-tiers.json" <<'EOF'
{
  "version": 1,
  "tiers": {
    "HEAD": {"model": "sonnet", "effort": "max", "description": "d"},
    "HARD": {"model": "sonnet", "effort": "xhigh", "description": "d"},
    "NORMAL": {"model": "sonnet", "effort": "medium", "description": "d"},
    "MECH": {"model": "haiku", "effort": null, "description": "d"},
    "REVIEW": {"model": "fable", "effort": "high", "description": "d"},
    "DEEP": {"model": "opus", "effort": "max", "description": "追加tier"},
    "CODEX": {"model": "sonnet", "effort": "high", "description": "codex台帳"}
  },
  "targets": {"agents": [], "settings": [], "routing_rules": []},
  "advisory_scan": []
}
EOF
HOME="$TMPHOME" PYTHONPATH=scripts/lib python3 bin/evolve-tier show
HOME="$TMPHOME" PYTHONPATH=scripts/lib python3 bin/evolve-tier show --json \
  | python3 -c "import json,sys; print(list(json.load(sys.stdin)['tiers'].keys()))"
HOME="$TMPHOME" PYTHONPATH=scripts/lib python3 bin/evolve-tier set DEEP --model opus --effort max
rm -rf "$TMPHOME"
```

実測結果（2026-09-09）:
- `show`（テキスト）: `HEAD HARD NORMAL MECH REVIEW` の5件のみ表示。**DEEP/CODEX は出ない**
- `show --json`: `['HEAD', 'HARD', 'NORMAL', 'MECH', 'REVIEW', 'DEEP', 'CODEX']` の7件。**一致しない**
- `render_routing_line(tiers)` を直接呼んだ結果にも DEEP/CODEX を含まない
  （`"DEEP" in line` → `False`, `"CODEX" in line` → `False`）
- `set DEEP --model opus --effort max` → `[evolve-tier] エラー: 未知の tier: 'DEEP'（有効: HEAD, HARD, NORMAL, MECH, REVIEW）` / exit 2

作業後 `git -C /Users/matsukaze-takashi/wt/ea-542 status --porcelain` は空を確認済み
（一時ファイルのみで検証し、worktree・実 `~/.claude` は無変更）。

---

## 変更案

### 1. そもそも `TIER_ORDER` は要るのか

`TIER_ORDER` を参照する4箇所のうち、「並び順を決める」用途（cli.py:86, sync.py:192）は
**表示順**であって、優先度計算や集合演算には使われていない
（`sync` の agent/settings 適用は `TIER_ORDER` を通らず正しく動く。上表で確認済み）。

`tiers` dict は `model-tiers.json` を `json.loads` した結果であり、JSON オブジェクトの
key 順は Python の `json.loads` がソース出現順のまま dict へ復元する
（CPython 3.7+ の dict 挿入順保持 + `json` モジュールの実装。標準ライブラリの契約）。
**つまり「正典ファイルに書いた順」がそのまま `tiers.keys()` の順になる**。
表示順だけが目的なら、`TIER_ORDER` という独立した定数を持たず `tiers` dict の
キー順をそのまま使えば同じ結果が得られ、**しかも定数とファイルの二重管理が消える**。

→ **表示順の目的では `TIER_ORDER` は不要**と確定できる。

### 2. 「未知の tier を弾く」検査は本当に要るのか

`set_tier`（`tier_policy.py:176-178`）の validate は「`set HEDA --model ...` のような
タイポで意図しない新規 tier キーが作成されるのを防ぐ」ためのもの。この目的自体は
妥当（実際に `set_tier` は「不在なら DEFAULT から生成して作成」する挙動を持つため
無検査だとタイポがそのまま新規 tier として永続化される）。

ただし**判定対象を固定 tuple `TIER_ORDER` に置くのは誤り**。ユーザーが JSON を直接
編集して `DEEP` を追加した後、`set DEEP --model opus --effort max` で model/effort を
更新しようとしても `TIER_ORDER` に無いという理由で拒否される
（上の再現実測で確認済み）。**正典ファイルに書いた内容を、同じ正典を読んでいるはずの
`set` コマンドが「知らない」と言って拒否するのは妥当ではない**（issue の問いへの答え）。

→ **検査の目的（タイポガード）は残すが、判定対象を `TIER_ORDER` から「現在の
`tiers` dict のキー集合」に変える**。これにより「JSON に無い名前を打ち間違えて
新規作成しかける」ことは引き続き防ぎつつ、「JSON に既にある名前」は常に受理される。

**判定に使う識別を1行で書く**（`no-denylist-checks.md`）: 変更後の `set_tier` の判定は
「文字列としての tier 名が、その時点で `model-tiers.json` に実在するキー集合に含まれるか」
という**名前による所属判定**のまま（`TIER_ORDER` → `config["tiers"].keys()` に変わるだけで
判定方式自体は変わらない）。これは `no-denylist-checks.md` が禁じる「名前で同一性を判定する
検査を blocking に使う」の形式的な該当例だが、**対象が安全境界ではなく UX 上のタイポ
ガード**であり、同ルールが想定する「セキュリティ・データ破壊防止の検査」には当たらないと
判断し、除外リスト方針の対象外として扱う（誤って弾いても実害は「JSON を直接編集すれば
即座に回避できる」程度で、`verify-checks-by-breaking.md` の「除外リストは検査を骨抜きに
する」が警戒する重大な迂回耐性の問題ではない）。**この判断は本設計書の書き手以外
（レビュアー）による裁定を要する**（`no-denylist-checks.md` 「健全さの判定手順を自分で
書いて自分で通さない」）。

### 3. 候補

**候補 A（推奨）**: `TIER_ORDER` 定数を削除し、`tiers` dict の宣言順を唯一の順序源にする。
`set_tier` の validate は `TIER_ORDER` の代わりに `config["tiers"]`（load 済みの現在の
正典）のキー集合を使う。

- 変更点:
  - `tier_policy.py:55` の `TIER_ORDER = (...)` を削除
  - `tier_policy.py:176-178`: `if tier_key not in TIER_ORDER` を
    `if tier_key not in config["tiers"]`（`load_tiers_config(strict=True)` 済みの
    `config` を先に読んでから validate する順序に入れ替える。エラーメッセージの
    「有効: ...」も動的な `config["tiers"].keys()` から生成する）
  - `tier_policy_cli.py:86`: `for tier in tier_policy.TIER_ORDER:` を
    `for tier in tiers:`（`tiers` は既に `config.get("tiers")` から得た dict。
    5行上の `tiers = config.get("tiers") or {}` を再利用）
  - `tier_policy_sync.py:27`: `TIER_ORDER` の import を削除
  - `tier_policy_sync.py:189-201`（`render_routing_line`）: `for tier in TIER_ORDER:` を
    `for tier in tiers:`（引数の `tiers` dict をそのまま反復。`DEFAULT_TIER_POLICY` への
    フォールバックは既存どおり `tiers.get(tier) or DEFAULT_TIER_POLICY.get(tier, {})`
    を維持するので、`tiers` dict にキーはあるが値が空という異常系でも壊れない）
- 効果: 定数と正典ファイルの二重管理が消える。全4経路（show text/json, sync
  routing_rule, sync agent/settings, set）が同じ `tiers` dict を単一の情報源として
  参照するようになり、構造的に一致が保証される（個々の経路に warning を後付けする
  必要が無くなる）。

**候補 B**: `TIER_ORDER` は残しつつ、`show`/`render_routing_line` の反復を
「`TIER_ORDER` の順 + それ以外は `tiers` dict の残りをキー順で末尾に追記」にする。
未知 tier には出力上 `(未登録順)` 等のマークを付ける。

- 選ばなかった場合に起きること: 定数と実体（`tiers` dict）の二重管理は解消しない。
  次に別の場所（例: 将来 CLI に新しい表示コマンドを足すとき）でまた `TIER_ORDER` を
  素朴に反復するコードが書かれれば、同じ不整合が再発する余地が残る
  （`no-denylist-checks.md` が警戒する「同一の欠陥族の再発」）。マークを付ける分
  機構が1つ増える（候補 A は削除のみで機構が増えない）。

**候補 C**: `load_tiers_config` に「`tiers` のキーが `TIER_ORDER` に無ければ warning を
`_load_error` 相当のフィールドで返す」を足すだけで、表示ループ自体は変えない
（issue 本文の当初案「1. 未知キーがあれば warning を出す」に相当）。

- 選ばなかった場合に起きること: warning は出るが、`show` テキストと `sync` の
  routing_rule 生成では相変わらず未知 tier が消えたまま。訂正コメントが指摘した
  「経路ごとに見える集合が違う」という核心（一貫性の欠如）は解決しない。
  「一貫性」を求める受入条件の差し替え後の要求と食い違う。

**推奨: 候補 A**。理由: ①定数を削除するだけで機構が増えない（`think-before-coding.md`
「機構を足す前に減らせないか」に最も忠実）②表示経路・生成経路・set 経路が構造的に
同じ情報源を見るため、個別の warning 追加や二重チェックが不要になり、将来の再発を
構造的に防ぐ ③受入条件の「テキスト render 経路と JSON 経路で同じ tier 集合が見える
こと」を最小の変更で満たす。

---

## 検証方法

### 契約テスト（全 tmp fixture・実 `~/.claude` 非依存。root conftest の HOME 隔離を利用）

1. `test_show_text_includes_declared_extra_tier`
   （`scripts/lib/tests/test_tier_policy_cli.py`）:
   `tiers` に `HEAD..REVIEW` + `DEEP` を含む一時 `model-tiers.json` を用意し、
   `cli.main(["show"])` のテキスト出力に `DEEP` が含まれることを assert。
2. `test_show_text_and_json_tier_sets_match`（同上）:
   同じ fixture で `show` と `show --json` を両方実行し、テキスト出力から抽出した
   tier 名集合と JSON 出力の `tiers.keys()` が一致することを assert（経路間一貫性の
   直接契約）。
3. `test_render_routing_line_includes_declared_extra_tier`
   （`scripts/lib/tests/test_tier_policy_sync.py`）:
   `DEEP` を含む `tiers` dict を `render_routing_line` に渡し、返り値の文字列に
   `DEEP` が含まれることを assert。
4. `test_set_accepts_tier_present_in_file_but_not_in_default`
   （`scripts/lib/tests/test_tier_policy.py`）:
   一時 config に `DEEP` を追加した状態で `set_tier("DEEP", "opus", "max", config_path=...)`
   を呼び、例外を投げず更新できることを assert。
5. `test_set_rejects_tier_absent_from_file`（同上・タイポガードの維持確認）:
   `tiers` に無い `"FOO"` を `set_tier` に渡すと `ValueError` が飛ぶことを assert
   （候補 A で validate 対象を動的にしても、タイポガードの目的自体は壊れていないことの確認）。

### 陰性試験（`verify-checks-by-breaking.md`。各変異に (a)(b)(c) を別々の機械的成果物で示す）

| # | 変異 | (a) 差分 | (b) 変異行が実行で読まれたことの確認 | (c) 対象テストのみ赤化 |
|---|---|---|---|---|
| ①「並び順を決める」の巻き戻し | `tier_policy_cli.py:86` の `for tier in tiers:` を `for tier in tier_policy.TIER_ORDER:` へ戻す（`TIER_ORDER` 定数も復元） | `git diff` で該当2行を提示 | `pytest --cov=tier_policy_cli` で該当行の hit を確認 | 上記 #1・#2 が FAIL、他の `TestShow*` は通ることを `pytest -k Show -v` で確認 |
| ②生成経路の巻き戻し | `tier_policy_sync.py:192` の `for tier in tiers:` を `for tier in TIER_ORDER:` へ戻す（import も復元） | `git diff` | `pytest --cov=tier_policy_sync` で該当行 hit | 上記 #3 が FAIL、他の sync テストは通る |
| ③タイポガード対象の巻き戻し | `tier_policy.py` の validate を `config["tiers"]` から `TIER_ORDER` へ戻す | `git diff` | `pytest --cov=tier_policy` で該当行 hit | 上記 #4 が FAIL（#5 は通る＝タイポガード自体は維持） |
| ④順序保持の前提が崩れるケース | `_default_config()` の `DEFAULT_TIER_POLICY` を dict 内包表記でなく `set()` 経由で順序をシャッフルする変異（意図的に順序非決定にする） | `git diff` | `pytest --cov=tier_policy` | `show`（defaults 経路）の出力順が実行のたびに変わり、順序 assert を持つ既存テスト（あれば）が不安定化・赤化することを確認 |

**陽性対照**: 既存5 tier（`HEAD/HARD/NORMAL/MECH/REVIEW`）のみの `model-tiers.json` で
`show`（テキスト・JSON 両方）、`sync --json`、`set` の出力が変更前後で**全フィールド一致**
することを、変更前 HEAD（`4fbcac85`）と変更後の diff で比較し確認する
（`git stash` で変更を退避し新旧の出力を採取して `diff` する）。

**テストは `-n 0` で直列実行する**（PJ ルール `commit-version.md` 系の既定と同じ）。

---

## 実装対象ファイル（この集合を越えない）

- `scripts/lib/tier_policy.py`（`TIER_ORDER` 削除、`set_tier` の validate 対象変更）
- `scripts/lib/tier_policy_cli.py`（`_run_show` のテキスト表示ループ）
- `scripts/lib/tier_policy_sync.py`（`render_routing_line` のループ、`TIER_ORDER` import 削除）
- `scripts/lib/tests/test_tier_policy.py`（新規契約テスト #4, #5 + 既存 validate テストの調整）
- `scripts/lib/tests/test_tier_policy_cli.py`（新規契約テスト #1, #2）
- `scripts/lib/tests/test_tier_policy_sync.py`（新規契約テスト #3）

**変更しないファイル（確認済み・触らない）**: `scripts/lib/tier_policy_drift.py`
（`TIER_ORDER` 未参照）、`scripts/lib/agent_quality_catalog.py`（`MODEL_ALIASES` は
③で対象外とした別軸）。

---

## 未実測

- **本番の `model-tiers.json` に実際に新規 tier を追加して運用に使う」ケースの実測**は
  行っていない（③で対象外とした Codex 統合が前提になるため）。今回の設計は
  「未知 tier をユーザーが手で追加した場合の一貫性」までを保証するもので、
  Codex を含む外部 runtime の実運用投入は別 issue の範囲。
- **陰性試験④（順序保持崩れ）の実装は未着手**。`DEFAULT_TIER_POLICY` は現状 dict
  リテラルで順序が決定的なため、この変異が実際に「壊せる」形で構成できるかは
  実装フェーズで確認する（`dict` を `set` 経由で経由させる変異が Python の型検査・
  既存コードのシグネチャと整合するかは未検証）。
- **⑥ の物差しが実測 0 件である点への司令塔の裁定**は未取得。本設計書は指示に
  従い作成したが、review.md の既定（「0 なら発注せず issue へ落とす」）との
  整合はユーザー確認が必要（冒頭「完成条件⑥」参照）。
