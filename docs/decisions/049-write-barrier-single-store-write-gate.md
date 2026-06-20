# ADR-049: write barrier — 全ストア書込を `store_write` 単一ゲートに集約し runtime で強制

- Status: Accepted（① read 統一 + ② write barrier 実装完了 / ③ #46 は read層 union+alias で merge 相当を達成・v1.108.0 / ④ #54 は削除0件で close＝dead code 無し / 物理単一化は不採用＝下記 不採用案 2026-06-20）
- Date: 2026-06-20
- Issue: #55（本 ADR と同時起票・実装トラッキング）, #45（DATA_DIR SoT 単一ソース化）, #46（legacy merge）, #54（死にストア削除）
- Related: ADR-042（hook-store-dir-resolver — reader 正準化）, ADR-031（PJ スコープ slug）,
  `scripts/lib/store_registry.py`（ストア新設の事前契約ゲート）, `rl_common.resolve_data_dir`,
  pitfall: `pitfall_datadir_hook_tool_split` `pitfall_global_datadir_single_file`,
  learning: `learning_install_is_not_enforcement` `learning_skill_md_must_not_enforcement`,
  セカンドオピニオン: senpai cold-read（2026-06-20）

## 背景（症状）

2026-06-20 の DATA_DIR 棚卸し（決定論 grep + ストア mtime/行数の実測）で、書込先がモジュールごとに
分裂している実害が確定した:

- `CLAUDE_PLUGIN_DATA` を env 直読みするモジュール **40個** / `resolve_data_dir()` 経由はわずか **9個**。
  各モジュールが保存場所を独自にコピペで算出している。
- **sessions が4箇所に分裂、同一日（2026-06-20）に2ファイルへ分割書込**:
  canonical 315行 + `plugins/data/evolve-anything` 398行 + legacy `sessions.db` 71MB + `plugins/data/rl-anything` 7行。
- **errors: reader が見る canonical 2行 vs writer が貯めた legacy 31,972行**。
  corrections は canonical 欠落・legacy のみ90行＝audit の「correction 再発率 no_data・capture 0%」の真因。

根本構造: **観測対象と観測機構が同一 DATA_DIR を共有する自己参照構造に、"書き込みの単一ゲート"が無い**。
`resolve_data_dir()`（ADR-042）は「読み」を正準化したが、「書き」は各モジュールが直接
`open(DATA_DIR / "x.jsonl", "a")` するため、新モジュール追加のたびに同じ分裂が再生産される。

過去の learning（`learning_install_is_not_enforcement` / `learning_skill_md_must_not_enforcement`）の通り、
「SKILL.md に MUST と書く」「resolve_data_dir を使うと規約に書く」では守られない。
**機械的に弾く強制（write barrier）が要る**（ユーザー要件: 「絶対に勝手にデータ保存させない」）。

## 影響

- reader が writer の書込先を見られず no_data / 分裂集計（測定バグの温床）。
- 「勝手な場所への保存」を止める機構が無く、新モジュールが無断で引き出しを増やせる。
- legacy / plugins/data に既存データが孤立し、merge しても writer 未修正なら再分裂。

## 決定

全ストア書込を **単一 API `store_write(store_name, record)`** に集約し、**runtime guard を主防御**として
登録外書込を実行時に弾く。各モジュールは保存場所を一切知らない・触れない。

### 設計（senpai cold-read で補正済み）

1. **形**: `rl_common.store_write(store_name, record)` が唯一の書込口。内部で `resolve_data_dir()` で
   場所決定 + `store_registry` 登録照合 + **atomic append（temp+rename or flock）**。各モジュールの
   直接 `open(DATA_DIR / ...)` を全廃。

2. **主防御は runtime guard（静的は advisory に格下げ）**:
   - 真の choke point は「ファイルを開く瞬間」でなく「ストアに書く瞬間」。`store_write` が
     `store_registry` 未登録 name を例外で reject する runtime guard が主防御。
   - 静的ゲートを「網羅的な open 禁止 AST」にするのは却下（f-string / Path 合成 / os.open / shutil で抜け、
     FP/FN が増えてモグラ叩き化する）。静的は「`store_write` を経由しない `DATA_DIR` 参照を
     **レビューに上げる advisory**」に限定。

3. **registry 照合は起動時1回 frozenset 化で O(1)**: store 定義は実行中増えないので import 時に1回
   frozenset 化し `name in _REGISTERED` の set lookup（数十ns）。hot-path（毎発火 hook 書込）の真のコストは
   fsync/close 側なので、レイテンシ懸念は registry でなく atomic append の実装で測る。

4. **read と write は分離（共有は registry だけ）**: read は「分裂した複数 legacy を union して読む」寛容さ、
   write は「canonical 1箇所にしか書かせない」厳格さ。1関数に畳むと read の寛容さが write 経路に漏れ
   barrier が緩む。#45（read 統一）と write barrier は別関数にし、`store_registry`（場所定義の単一ソース）
   のみ共有する。

5. **例外口はフラグでなく別名関数**: テスト/特殊ケースの直接書込を `store_write(..., allow_unregistered=True)`
   のようなフラグにしない（半年で本番に混入する＝MUST 不遵守の再演）。`store_write_raw()` という
   **別名関数**にし、その名前を静的 advisory の検出対象にする → raw を使う diff は必ずレビューに上がる。

6. **migration の status**: legacy→canonical merge は「未登録 legacy から読んで canonical に書く」操作で、
   素朴な barrier では migration コード自身が引っかかる。`store_registry` に `status`(active/legacy/dead) を
   持たせ、legacy は read-only 登録、**write は active store のみ許可**。

### 実装順序（順序は崩せない — MUST）

```
① read 統一（#45 read 側）…… legacy を union して全部見えるようにする        ✅ 完了
② write barrier 導入 ………… store_write 唯一API + 未登録reject + atomic + registry status  ✅ 完了
③ legacy→canonical merge（#46）…… **物理 merge ではなく read層 union+alias で「孤立ゼロ」を達成**（Phase1/2・v1.108.0）  ✅ 完了
④ 死にストア削除（#54）…… reader/writer 到達性で突合し **削除0件**（active split / recommended-artifact のみ）で close  ✅ 完了
（静的 advisory は並行：store_write 非経由の DATA_DIR 参照をレビューに上げる）
```

② の完了内訳: Phase 2a（store_write 土台・warn-only / PR #57）→ Phase 2b wave 1-3（全 production
caller を store_write へ移行・hooks 10 + scripts/lib 6 / PR #58-#60）→ reject 昇格（既定を
warn-only → reject へ・#55 capstone）。production 挙動は不変（全 caller が登録済み active 名のみ
使用）、登録外書込のみ `StoreWriteError` で弾く。緊急避難は env `EVOLVE_WRITE_GUARD=warn`。

**🔴 read 統一(①) を write barrier(②) より先に**やる。逆順は事故る: write barrier を先に入れると
legacy への書込が止まり、reader がまだ legacy を読めていない状態で **既存データが即・行方不明**になる。

### 安全網（audit.py / evolve.py 分割の勝ちパターン流用）

- keyset snapshot で「**書込先パス集合の不変**」を assert（Phase ごとに不変条件を切替:
  Phase1=パス不変 / merge Phase=canonical 全行到達 + legacy 書込停止）。値バイト不変だと別 dir 移動を
  見逃すのでパス集合を対象にする。
- 40モジュールは「モジュール単位」でなく「**同一 store に書く群**」単位で1PR にまとめる。
- `bin/evolve-dogfood-gate --layer all` で実環境の繋ぎ目（dry-run 不変 / store 差分）を確認。

## 不採用案

- **静的ゲート（AST open 禁止）を主防御にする**: 網羅すると FP/FN が増え信用されなくなる。
  runtime guard が主、静的は advisory。
- **read と write を1つの SoT 関数に統合**: read の union 寛容さが write に漏れ barrier が緩む。
  共有は registry のみ。
- **write barrier を read 統一より先に導入**: legacy 書込が止まり既存データが即見えなくなる。
- **例外口を `allow_unregistered=True` フラグにする**: フラグは本番に混入する。
  別名 `store_write_raw()` で diff 可視化。
- **migration 後に sessions / spec_trigger を「物理的に単一 dir へ」寄せる（②）**: 不採用
  （2026-06-20 実 PJ 検証 + senpai cold-read）。**🔴 誰かが「self-resolver が2つあるのはバグ」と
  思って marker 対応へ寄せ直すのを止めるための記録。** 検証結果: marker（`.data-dir-unified`）設置後も
  物理 dir は4箇所併存し、`session_store.py`（sessions.jsonl/db）と `spec_trigger.py`（spec_trigger/<pj>.json）は
  **意図的に自前リゾルバ**（`CLAUDE_PLUGIN_DATA` 直読み・marker 非対応）で plugins-data に書き続ける。
  だが reader が union read（#46/#469）するため **欠落ゼロを実測**（usage 21 / subagents 23 / skill_activations 20 PJ・
  corrections 90行 legacy も可視）。物理単一化を**やらない**理由:
  (1) テレメトリは結果整合で十分（金勘定でも監査証跡でもない）。union read で孤立ゼロなら物理収束は容量以外の価値を生まない。
  (2) self-resolver は「write と read が同一 hook 文脈で同一 dir を見る」保証で #364 hook/tool 乖離の再発を構造的に封じる。
      marker 対応へ寄せると spec_trigger（SessionStart 毎発火 hot path）に marker I/O が乗り、sessions.db だけ別 dir に
      取り残される co-location 破壊の回帰がありうる。得るもの（dir が1個減る）にリスクが釣り合わない。
  (3) read=union（寛容）/ write=self-resolver + `store_write_raw`（例外口・上記）は **意図的なレイヤ分離**であって
      「理由の失われた重複（＝負債）」ではない。issue 化不要。
  片付けるのは legacy の**移行バックアップ dir のみ**（`backfill_*_backup_2026-0619`・約240MB・live は backup 外に健在）。

## 残課題（次サイクル実装）

本 ADR は設計合意のみ（Proposed）。実装は #55 で段階 PR 化する。
read 統一(#45)→write barrier→merge(#46)→死にストア削除(#54) の順を厳守。
