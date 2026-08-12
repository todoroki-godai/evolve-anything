# ADR-054 Phase D 設計 — 残存2経路の revert lane 統合（D1）+ entry_id 導線拡充（D2）

- Status: **Confirmed（設計確定・実装着手可）**。codex round1/round2 の [Must] は全解消。
  round3 以降の codex 巡は行わない——残る未決8点は 2026-08-12 に頭（team-lead）が直接裁定し確定した（§7）。
  **PR2/PR3 は 2026-08-13 に追加裁定で凍結**（実装しない。解凍条件は上記凍結裁定を参照。PR1/PR4 は対象外）
- Date: 2026-08-12（初版）/ 改訂1（codex round1 反映）/ 改訂2（codex round2 反映——B の pending→accept
  契約を単一の表に一本化）/ 改訂3（team-lead 裁定反映——未決8点の確定・PR 構成の最終化・decision entry
  の append-only 化）
- 対象 repo: `/Users/matsukaze-takashi/matsukaze-utils/evolve-anything`（ブランチ `docs/379-four-pillars-design`、HEAD `66c9975a`）
- 上位: [ADR-054](../054-four-pillars-completion-design.md) §5 Phase D / §8 D行、[ADR-053](../053-revert-cli-design.md)（#402 PR-2 の revert lane 実装そのもの）
- 実測日: 2026-08-12（本文中の行番号・関数名は全てこの時点の実測。改訂で追加実測した箇所は明記）

> **本文書のスコープは D1（`pre_extension` 残存2経路の解消）と D2（entry_id 導線拡充）のみ**。
> §1 の全数調査で ADR-054 の記述と食い違った点は §6、codex round1/round2 の指摘対応は §9 にまとめる。
> **B（optimize.py）の pending→accept 契約は §2.2 の1つの表に一本化した**——round1/round2 で継ぎ足しの
> 修正を重ねたことで記述が競合していた反省（round2 Must1/Must3/Must4/Must-new×2）を踏まえ、他の節から
> はこの表を参照する形にする。

> **凍結裁定（2026-08-13・ユーザー判断）**: 実測（`~/.claude/evolve-anything/optimize_history/` 全41
> entry の writer 別分解）の結果、**PR2（`run_loop.py` 経路）・PR3（`optimize.py` 経路）は凍結する**。
> B の accept は史上0件、C の accept は実質1件（2件のうち1件は pytest tmpdir 汚染）。採用実績が
> ほぼ無いレーンに append-only decision chain + `supersedes_id` + 2段検索という本設計中最も複雑な
> 仕組みを入れるのは #379 の縮小方針に反する。**実施するのは PR1（実装済み）・PR4（`--list` 導線）のみ**。
> **本文書 §2〜§5 の PR2/PR3 設計本文は削除しない**（解凍時にそのまま使うため保持する。以下は
> 「凍結中」の設計として読むこと）。解凍条件: どちらかのレーンで**テスト由来でない accept が3件以上**
> に達した時点。裁定の正典は
> [ADR-054 §5 Phase D](../054-four-pillars-completion-design.md)（D1 の PR2/PR3 凍結裁定の節）。

## PR 構成（最終確定・team-lead 裁定 2026-08-12 → PR2/PR3 凍結は 2026-08-13 追加裁定）

4 PR に分割する（順序固定。各 PR の完了条件は §5.3 に PR 単位で記載）:

| PR | 内容 |
|---|---|
| **PR1（共有 helper 契約）** | `merge_revert_fields`/`_decision_event_id_from_sha`/`append_history_entry_deduped`+`_locked` 版の新設、A writer（`record_evolve_diff_decision`）のリファクタ（byte-equivalent）、**`evolve_decision_ids.py` の private 関数群の public rename**、**`run_loop.py` の `--auto --dry-run` approved 汚染修正**、**`evolve_revert/_availability.py` の after_sha/id schema 一貫性検査追加** — **✅ 実装済み** |
| **PR2** | C（`run_loop.py`）経路を revert lane へ寄せる — **🧊 2026-08-13 凍結・実施しない（設計は保持、解凍条件は上記）** |
| **PR3** | B（`optimize.py`）経路を revert lane へ寄せる（複雑さが集中するため単独 PR。decision entry の append-only 化を含む） — **🧊 2026-08-13 凍結・実施しない（設計は保持、解凍条件は上記）** |
| **PR4** | D2（`bin/evolve-revert --list`/`--all` 導線）+ `.backup`/`--restore` と entry_id revert の使い分けドキュメント化 — **実施する（PR2/PR3 に非依存）** |

---

## 0. 前提の再確認（実測で判明した重要事実）

**ADR-053（#402 PR-1/PR-2）は本文書作成時点で main に完全にマージ済み**（`4364ae52` / `9aa26abb` / `bc264f94`）。
revert lane のコア機構——`optimize_history_store.py` の `load_raw_history` / `load_effective_history` /
`load_revert_events` / `fold_effective` / `REVERT_EVENT_REQUIRED_FIELDS`、`evolve_decision_ids.py` の
`REVERT_FIELD_KEYS` / `_path_scope_identity` / `_compress_before_for_revert` / `_revert_generation_for_target`、
`evolve_revert/` パッケージ一式（`_entry.py` `_target.py` `_metadata.py` `_apply.py` `_dump.py` `_render.py`
`_availability.py`）、`bin/evolve-revert`、`results_board.py` の `withdrawal_candidates`（entry_id /
revert_available / revert_unavailable_reason / reverted の4フィールドと実行コマンド印字）——は**全て実装済み**。

**したがって D1/D2 は「revert 機構を新規に作る」設計ではない。** 既存の read 側（`compute_revert_availability` /
`find_entry` / `apply_revert` / `results_board`）は**原則不変**で、**write 側（3系統中2系統）が revert
契約に必要なフィールドを書いていないために `pre_extension` に落ちている**、という write-only のギャップを
埋める設計。**唯一の例外は `compute_revert_availability`（§2.7）**——「`available=true` と表示したのに
apply が失敗する」は柱4「信頼」を直接壊すため、この不変条件だけは read 側非変更という自己制約より優先する
（team-lead 裁定・2026-08-12・§7-6）。

**codex round1 で判明した本質**: この write-only ギャップは「フィールドを additive に足せば済む」という
単純な話ではなかった。`apply_revert` は `after_sha`（`_apply.py:258-267`）が無ければ即座に
`REASON_AFTER_SHA_MISSING` で失敗する——つまり `revert_before_b64` だけ足して `after_sha` を足し忘れると
**「戻せます」と表示されるのに実際には戻せない**という、柱4「信頼」が最も嫌う状態を新規に作ってしまう。
B（`optimize.py`）はさらに「accept が別プロセスの CLI 呼び出しで非同期に来る」「before 内容の再構成に
既存 `.backup` ファイルを使うと過去 run の内容を誤って保存しうる」という2つの構造的な罠を持つ。
本改訂はこれら（and it's not just B — C にも after_sha 欠落・冪等性未定義という同型の穴があった）を
全て設計レベルで塞ぐ。

---

## 1. accept 記録経路の全数調査（実測）

### 1.1 直接 writer は3系統（ADR-054 の記述と一致）

`grep -rn '\.append_entry(\|save_history_entry(\|record_evolve_diff_decision(\|record_human_decision(' scripts skills bin`（tests 除外）の実測結果:

| # | 経路 | 実体 | 呼び出し箇所 |
|---|---|---|---|
| A | **emit→drain lane** | `evolve_decisions/_ingest.py:128` `recorder(...)` → `_ed._load_recorder()` が解決する実体は `fitness_evolution.record_evolve_diff_decision`（`evolve_decisions/_candidates.py:145`） | Step 7.8 drain（`evolve --drain` / inline drain） |
| B | **optimize.py** | `DirectPatchOptimizer.save_history_entry`（`optimize.py:303-365`）。呼び出しは `run()` 内 4箇所（dry-run/LLM失敗/gate拒否/成功、`optimize.py:161,189,229,251`）。accept/reject 確定は**別プロセス**の `--accept`/`--reject` CLI 呼び出しが `record_human_decision`（`optimize.py:368-394`）を叩く | `/evolve-anything:optimize` （genetic-prompt-optimizer） |
| C | **run_loop.py** | `_history_store.append_entry(loop_result, _history_slug)`（`run_loop.py:679`）。`optimize_history_store.append_entry` を**直接**呼ぶ（record_evolve_diff_decision を経由しない）。accept（`approved`）は同一ループ反復内で確定済み | `/evolve-anything:evolve-loop-orchestrator` |

ADR-054 §2.5「emit→drain 由来の accept だけが revert 情報を保存する。`optimize.py::save_history_entry` /
`run_loop.py` 経由は今後も `pre_extension`」という記述は**正確**（今回の実測で食い違いなし）。

別途、**revert イベント自体の writer**が4本目として存在する（accept writer ではないので上表に含めない）:

| # | 経路 | 実体 |
|---|---|---|
| D | revert 実行時の revert イベント追記 | `evolve_revert/_apply.py:128` `_store.append_entry(event, slug)` |

### 1.2 revert 情報の保存有無（実測）

| 経路 | `id` フィールド | `after_sha` | revert フィールド（`REVERT_FIELD_KEYS`） | `compute_revert_availability` の結果 |
|---|---|---|---|---|
| A（emit→drain） | ✅ `_decision_event_id(pid, kind, after_content, generation)`（`_ingest.py:137`） | ✅ `_ingest.py:127` でローカル計算し `_revert_fields["after_sha"]` に純加算 | ✅ `kind=="accept"` のときのみ付与（`_ingest.py:124-127`） | schema あれば `available=True`（かつ **実際に apply も成功する**——A は after_sha を確実に付与しているため） |
| B（optimize.py） | ❌ **付与されていない**（`optimize.py:317-327` の entry dict に `id` キーが無い） | ❌ 付与されていない | ❌ 一切なし | `revert_schema_version` 欠落 → `pre_extension` |
| C（run_loop.py） | ❌ **付与されていない**（`run_loop.py:649-664` の `loop_result` dict に `id` キーが無い） | ❌ 付与されていない | ❌ 一切なし | 同上 → `pre_extension` |

**ADR-054 が明示していなかった事実（重要・§0 で先述）**: B・C は revert フィールドが無いだけでなく、
**`id` フィールド自体を持たない**。`evolve_revert/_entry.py:find_entry` は `entry.get("id") == entry_id`
で検索し、`results_board.py:230` の `withdrawal_candidates` も `entry_id = e.get("id")` を読む。
**`id` が無ければ revert フィールドを足しても `bin/evolve-revert <entry_id>` で引けない**——D1 は
「revert フィールドを足す」だけでは完結せず、**id 付与が先に必要な前提条件**である（§6 で ADR-054 との
食い違いとして報告する）。さらに `apply_revert`（`_apply.py:258-267`）は **`after_sha` も必須**として
検査するため、**id・after_sha・revert フィールドの3点セットが揃って初めて「実際に戻せる」**。

ADR-054 §2.4 の表にある「`classify_decision` → accepted: 1（`id=None`）」は、この B/C いずれかの writer が
生成した accepted entry を指していると推定される（今回は writer 経路の特定に絞り、当該1件がどちらの
writer 由来かまでは特定していない——**未確認**）。

### 1.3 `pre_extension` が指すものの裏取り

`evolve_revert/_availability.py:13-17` の docstring（実装コード）が既に正確に定義している:

> `pre_extension`: 記録拡張（PR-1）前に採用された entry、**または** PR-1 パイプライン（`evolve_decisions`）を
> 経由しない writer（`optimize.py` の `save_history_entry` / `run_loop.py`）による entry。

`compute_revert_availability`（`_availability.py:59-76`）の判定式は `revert_schema_version` の有無のみを見る
（writer の出所は見ない）。B・C の entry はそもそもこのキーを書かないため、**PR-1 以前の古い entry も、
PR-1 マージ後に B/C 経由で新規に書かれた entry も、区別なく同じ `pre_extension` に落ちる**。ADR-054 §5 の
「`pre_extension` 残存2経路」という表現はこの意味で正確。

**改訂で追加実測**: `compute_revert_availability` は `after_sha`/`id` の有無を検査しない
（`_availability.py:59-76` を再読——`revert_unavailable_reason`/`revert_schema_version`/`scope`/
`revert_before_b64` の4項目のみ判定）。これが「`available=True` だが実際には `apply_revert` が
`after_sha_missing` で失敗する」という**既存契約自体の隙間**であり、D1 の writer 側修正だけでは
閉じない（§9 Should1 で `_availability.py` 側の対応も設計に含める）。

---

## 2. D1 の設計 — `pre_extension` 残存2経路を revert 契約に寄せる

### 2.1 方針: 「emit→drain lane へ寄せる」のではなく「同じ契約を writer 側で満たす」

ADR-054 の文言は「emit→drain lane に寄せる」だが、実装レベルでは B・C を emit→drain の pending
queue/marker 機構（`evolve_decisions/_emit.py` の dry-run snapshot・seqlock check-after・pending
queue）に載せ替えることは**採らない**。理由:

- A 系統の pending queue は「1回の evolve run で複数候補を提案し、朝の y/n で人間が選ぶ」形の非同期フローに
  最適化された機構。B（`optimize.py`）・C（`run_loop.py`）はどちらも**単発 CLI 実行で即座に accept/reject が
  決まる**（または B は accept が別プロセスの `--accept` 呼び出しで来る）同期フローで、pending queue に
  値するデータの形が違う。無理に載せると「動くようになったが不自然な二重表現」が生まれる
  （PR-1 レビューで既に「PR は分割しない」「動かないものを2つ積まない」という教訓が明記されている——ADR-053 §6）。
- revert 契約の実体は **「entry に `id` + `after_sha` + `REVERT_FIELD_KEYS` サブセットを additive に
  持たせること」** であり、この契約は `fitness_evolution.record_evolve_diff_decision` が既に単独で
  満たしている（emit→drain の pending queue を経由しなくても契約は満たせる）。B・C も同じ契約を自分の
  write 経路の中で満たせばよい。

**採る設計**: `fitness_evolution.record_evolve_diff_decision` 内にある2つのロジックを
`evolve_decision_ids.py` / `optimize_history_store.py` へ抽出し**3 writer 共有の単一ソース**にする
（`pitfall_copied_parse_convention_partial_fix` の再発を避ける——ADR-053 §5 と同じ設計判断を writer 側にも適用する）。
**改訂2（codex round2 反映）で3つ目の共有関数を追加し、既存の `_locked` 版分離慣習
（`rl_common/file_lock.py` docstring「ロック下から呼ぶ内部処理はロックを取らない `_locked` 版を使う」・
`evolve_revert/_apply.py` の同慣習）に揃える**:

1. **`merge_revert_fields(entry, revert_fields)`**（`evolve_decision_ids.py` に新設・純関数）:
   `fitness_evolution.py:231-247` の「許可リストフィルタ + 純加算契約の衝突検査」を抽出。
2. **`_decision_event_id_from_sha(proposal_id, kind, after_sha, revert_generation=0)`**
   （`evolve_decision_ids.py` に新設・純関数。**round2 Must4 の核心対応**）:
   既存 `_decision_event_id(proposal_id, kind, after_content, revert_generation)` は
   `base = f"{proposal_id}_{kind}_{_sha256(after_content)[:12]}"` を計算する（`evolve_decision_ids.py:173`）。
   `after_sha`（`_sha256(after_content)` の**フルhex**）を既に持っていれば、`_sha256(after_content)[:12]`
   は `after_sha[:12]` と**ビット同一**——`after_content` の全文を別プロセスへ運ばなくても、保存済みの
   `after_sha` だけから**同じ ID を再構成できる**。**この等価性は契約テストで固定する**（team-lead
   裁定・2026-08-12）——`_decision_event_id` の内部実装（`_sha256(after_content)[:12]` という切り出し方）
   が将来変わると `_decision_event_id_from_sha` だけ黙って乖離しうるため、
   `test_evolve_decision_ids.py`（新規 or 既存拡張）に
   `_decision_event_id_from_sha(pid, kind, _sha256(content), gen) == _decision_event_id(pid, kind, content, gen)`
   を全 kind（"pending"/"accept"/"reject"）× generation 0/非0 の組み合わせで固定する回帰テストを
   PR1 の完了条件に含める（§5.3 PR1）。
   ```python
   def _decision_event_id_from_sha(
       proposal_id: str, kind: str, after_sha: str, revert_generation: int = 0
   ) -> str:
       """_decision_event_id の sha 入力版。after_sha = _sha256(after_content) であるとき
       _decision_event_id(proposal_id, kind, after_content, revert_generation) とビット同一の
       ID を返す（after_content 全文を持たない別プロセスからの ID 再構成用・#402-D round2 Must4）。
       """
       base = f"{proposal_id}_{kind}_{after_sha[:12]}"
       if not revert_generation:
           return base
       return f"{base}_rg{revert_generation}"
   ```
3. **`append_history_entry_deduped(entry, slug, history_file=None)`** + **`_append_history_entry_deduped_locked`**
   （`optimize_history_store.py` に新設）: `fitness_evolution.py:254-264` の「`file_lock` 下で既存 `id`
   と重複しないか確認してから append、重複していれば書かずに既存 entry を返す」を抽出。**これが
   Must5（冪等性）の core**。round2 Must5（二重 lock による自己 deadlock）を避けるため、公開関数
   （自分で lock を取る）と `_locked` 版（呼び出し側が既に lock を保持している前提。lock を取らない）
   に分離する——他の呼び出し元がまとめて1回だけ lock する場合は `_locked` 版を直接呼ぶ。
   `record_evolve_diff_decision` はこれらを呼ぶ形へリファクタ（振る舞い不変・既存契約テストで担保）。

```python
# optimize_history_store.py に新設
def append_history_entry_deduped(
    entry: Dict[str, Any], slug: str, history_file: Optional[Path] = None
) -> Tuple[Dict[str, Any], bool]:
    """entry['id'] が既存なら書かずに既存 entry を返す（written=False）。
    無ければ file_lock を自分で取得し、_append_history_entry_deduped_locked を呼ぶ（written=True）。
    entry['id'] が None/空文字なら ValueError。
    """
    if not entry.get("id"):
        raise ValueError("append_history_entry_deduped requires entry['id']")
    if history_file is None:
        history_file = history_path(slug)
    with file_lock(history_file.with_name(history_file.name + ".lock")):
        return _append_history_entry_deduped_locked(entry, history_file)


def _append_history_entry_deduped_locked(
    entry: Dict[str, Any], history_file: Path
) -> Tuple[Dict[str, Any], bool]:
    """呼び出し側が既に history_file の lock を保持している前提（自己 deadlock を避ける
    _locked 版）。`_read_jsonl(history_file)` で単一ファイルを直接読む——`load_raw_history`
    のような cross-dir alias union はしない（本関数の write 先も常に canonical 単一ファイル
    のため、read/write の対象を一致させる。round2 Must-new #2 対応・詳細は §2.2 の表）。
    """
    existing = next((r for r in _read_jsonl(history_file) if r.get("id") == entry["id"]), None)
    if existing is not None:
        return existing, False
    normalize_entry_timestamp(entry)
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry, True
```

### 2.2 B（optimize.py）の変更点 — pending→accept 契約を1つの表に一本化【codex round2 全 Must 反映】

**round1/round2 で継ぎ足し修正を重ねた結果、「pending 時に何を書き、accept 時に何を確定するか」の
記述が競合していた**（round2 Must1: after_sha の二重記述／Must3: run_id 単独検索の弱さ／Must4:
after_content 全文が無いと ID を再構成できない／Must-new: `revert_generation` が正式フィールドで
ない・generation 読取が write 先と別ストアを見る）。**以下の1つの表を正典とし、他の記述はこれを参照する。**

#### 契約表（B の pending→accept ライフサイクル）

| 項目 | 決定 |
|---|---|
| **pending 時**（`save_history_entry` 呼び出し。`run()` 内4箇所全て） | `before_sha = _sha256(self.original_content)`（run 固有・**`.backup` は使わない**——Must2）。`proposal_id = _proposal_id(target_path, before_sha)` を計算し `entry["proposal_id"]` に保存（REVERT_FIELD_KEYS 外の追跡用フィールド）。`entry["before_sha"] = before_sha` も保存。`merge_revert_fields(entry, {revert_before_b64, revert_unavailable_reason, revert_schema_version, revert_encoding, revert_generation, **path_identity, after_sha})` を1回だけ呼ぶ——**`after_sha` は成功パス（disk へ実書込した呼び出し・`optimize.py:251` 相当）だけで値を渡し、それ以外（dry-run/LLM失敗/gate拒否）は `None` を渡す**（`merge_revert_fields` が None を落とすため、対象外の entry には `after_sha` キー自体が現れない——後述のとおりこれは Must3 の対象特定にも使う）。`revert_generation` は**この場所で1回だけ** `_revert_generation_for_target(...)` を読んで確定し、`REVERT_FIELD_KEYS` の正式フィールドとして書く（**内部専用スナップショットは作らない**——round2 Must-new #1 対応。以後どこでも読み直さない） |
| **`id`（pending 時）** | `_decision_event_id_from_sha(proposal_id, "pending", after_sha or before_sha, revert_generation)`。after_sha が無い（未成功）場合は代わりに `before_sha` を使う——pending 時の id は「まだ何も accept されていない」ことを示す内部値であり、A の pending id 相当の意味しか持たない（accept 確定時に必ず作り直される。後述） |
| **generation の読取元** | `_append_history_entry_deduped_locked` と同じ**単一ファイル直読み** `_read_jsonl(history_file)`（`history_file` は `save_history_entry` に渡された/解決された、この後実際に書き込む対象そのもの）。**`_store.load_raw_history(slug)` のような slug 経由の別解決は使わない**——明示 `history_file` が渡された場合でも read/write が同じ物理ファイルになることを構造的に保証する（round2 Must-new #2 の直接対応）。cross-dir alias union をしない理由: B/C の write は常に canonical 単一ファイル固定（`append_entry`/新設 helper とも `history_path(slug)` 相当のみ）であり、revert イベントの writer（`evolve_revert/_apply.py`）も同じく canonical 固定——union が必要なのは PJ rename 等の legacy 分散データを持つ A 系統の事情であり、B/C は新設 writer なので分散を持たない |
| **`accept/reject 確定時**（`record_human_decision(run_dir, human_accepted, ...)`。別プロセス呼び出し） | **append-only（team-lead 裁定 2026-08-12・決定8）**——pending entry を in-place 書き換えしない。代わりに**新しい decision entry を1件 append** する（後述） |
| **同一決定の再実行** | 直近の decision entry の `human_accepted` が今回の要求と同じなら、append せず `True` を返す no-op |
| **決定の反転**（reject→accept 等） | 許容する。**上書きせず新しい decision entry を append**（`supersedes_id` で前の decision entry を指す）。反転した事実は raw history に残り続ける（append-only 原則） |

#### accept 対象 entry の検索仕様（round2 Must3 + team-lead 決定8「append-only」を統合）

**2段検索**にする（「まだ決定されていない pending entry」と「既に決定済みの decision entry」は別物として扱う——決定8 で decision も append 対象になったため、両者を混同すると「pending を探したつもりが古い decision entry を掴む」誤りが起きる）:

```python
run_id = Path(run_dir).name  # = self.run_id（"%Y%m%d_%H%M%S"）
result = json.loads((Path(run_dir) / "result.json").read_text(encoding="utf-8"))
target = result.get("target")

# 1. この run の pending entry を一意に特定する（human_accepted が None のもの＝
#    save_history_entry が書いた「まだ何も決定していない」entry）。
#    after_sha is not None が dry-run/LLM失敗/gate拒否を構造的に除外する（round2 Must3）。
pending_matches = [
    e for e in entries
    if e.get("run_id") == run_id and e.get("target") == target
    and e.get("after_sha") is not None and e.get("human_accepted") is None
]
# 0件→「未発見」エラー / 2件以上→「データ不整合」エラー（run_id 秒精度衝突等の極めて稀なケース）/
# 1件→ pending として採用

# 2. この提案（proposal_id）に対する「最新の」decision entry を探す（reversal chain の先頭）。
#    decision entry は supersedes_id で前の decision entry を指すので、「他のどの decision の
#    supersedes_id からも指されていない」ものが最新（チェーンの tip）。
decision_entries = [e for e in entries if e.get("proposal_id") == pending["proposal_id"] and e.get("human_accepted") is not None]
superseded_ids = {e.get("supersedes_id") for e in decision_entries if e.get("supersedes_id")}
latest_decision = next((e for e in decision_entries if e.get("id") not in superseded_ids), None)
```

```python
@staticmethod
def record_human_decision(run_dir, human_accepted, rejection_reason=None, history_file=None) -> bool:
    run_id = Path(run_dir).name
    result = json.loads((Path(run_dir) / "result.json").read_text(encoding="utf-8"))
    target = result.get("target")
    if history_file is None:
        history_file = _store.history_path(_store.resolve_slug())
    with file_lock(history_file.with_name(history_file.name + ".lock")):
        entries = _read_jsonl(history_file)  # write 先と同一ファイル直読み（round2 Must-new#2 と同じ規約）

        pending_matches = [
            e for e in entries
            if e.get("run_id") == run_id and e.get("target") == target
            and e.get("after_sha") is not None and e.get("human_accepted") is None
        ]
        if not pending_matches:
            print(f"エラー: run_id={run_id}, target={target} に対応する適用済み記録が見つかりません。")
            return False
        if len(pending_matches) > 1:
            print(f"エラー: run_id={run_id}, target={target} に対応する記録が複数あります（データ不整合）。")
            return False
        pending = pending_matches[0]

        decision_entries = [
            e for e in entries
            if e.get("proposal_id") == pending["proposal_id"] and e.get("human_accepted") is not None
        ]
        superseded_ids = {e.get("supersedes_id") for e in decision_entries if e.get("supersedes_id")}
        latest_decision = next((e for e in decision_entries if e.get("id") not in superseded_ids), None)

        if latest_decision is not None and latest_decision["human_accepted"] is human_accepted:
            print(f"既に{'受理' if human_accepted else '却下'}として記録済みです（変更なし）。")
            return True

        kind = "accept" if human_accepted else "reject"
        new_id = _decision_event_id_from_sha(
            pending["proposal_id"], kind, pending["after_sha"], pending.get("revert_generation", 0)
        )
        decision = dict(pending)  # run_id/target/proposal_id/revert_generation/after_sha を継承
        decision["id"] = new_id
        decision["human_accepted"] = human_accepted
        decision["rejection_reason"] = rejection_reason
        decision["supersedes_id"] = latest_decision["id"] if latest_decision is not None else None
        if not human_accepted:
            # reject 確定時は revert 本文を保存しない契約（決定2 の経済性）
            for k in REVERT_FIELD_KEYS:
                decision.pop(k, None)

        # append_history_entry_deduped を使う（PR1 で新設した共有 helper。B/C/decision entry の
        # 3者が同じ「id 重複なら書かない」原子性を共有する——2重 lock を避けるため _locked 版を
        # 使う（外側で既に lock 保持中）。
        _append_history_entry_deduped_locked(decision, history_file)
        return True
```

**decision2（reject/pending の本文は保存しない）からの意図的な逸脱（変更なし）**: pending 時点で
`revert_before_b64` を持つのは A の経済性と異なるが、Must2 対応上避けられない——理由・トレードオフは
round1 の記述のまま（before は accept 確定前にしか同期的に確実な取得手段が無い）。reject 確定時に
本文を pop する設計で経済性を事後的に回復する。

**decision entry の raw history への影響**: 1つの run に対して pending entry 1件 + decision entry
0〜N件（反転のたびに1件ずつ append）が history に並ぶ。`load_effective_history`/`fold_effective`/
`classify_decision`/`results_board` は個々の entry を独立に見るため、**pending entry（`human_accepted
is None`）は「pending」バケットに、各 decision entry は各々の `human_accepted` の値で「accepted」/
「rejected」バケットに入る**——reversal が起きると同じ run から複数の decision entry が異なるバケットに
入りうるが、これは「その run に対して何回・どんな決定イベントが起きたか」という append-only の生ログを
正直に表しているため、意図的な挙動として許容する（team-lead 決定8「反転した事実が履歴に残ることを
テストで担保する」を、既存 reader 側のロジックを変更せずに満たす——**read 側の fold ロジック拡張は
本 PR のスコープに含めない**。将来「最新の decision だけを集計に使う」表示改善が必要になれば別 issue）。

### 2.3 C（run_loop.py）の変更点【codex round1 Must1/Must5・round2 Must5/Must-new2 を反映】

B と異なり、accept（`approved`）は**同一ループ反復内で既に確定している**ので非同期性の問題が無い。
**Must1（`after_sha` 欠落）は C 側の単純な記述漏れ**だったため、以下で明示的に追加する。

```python
# line 636 の Path(target_path).write_text(...) より前に before 内容を保持しておく
content_before_apply = global_best_content  # line 640 で上書きされる前の値
...
history_file = _history_store.history_path(_history_slug)
if approved and not dry_run:
    before_sha = _sha256(content_before_apply)
    path_identity = _path_scope_identity(target_path)
    before_b64, unavailable_reason = _compress_before_for_revert(content_before_apply)
    after_sha = _sha256(best["content"])
    # round2 Must5: file_lock は1回だけ取る（自己 deadlock 回避のため _locked 版を lock 内で呼ぶ）。
    # round2 Must-new #2: generation 読みは write 先と同じ history_file を直接読む
    # （_history_store.load_raw_history(slug) のような別解決をしない・B と同じ規約）。
    with file_lock(history_file.with_name(history_file.name + ".lock")):
        history = _history_store._read_jsonl(history_file)  # generation 読みは lock 下・1回のみ
        revert_generation = _revert_generation_for_target(
            history, path_identity["scope"], path_identity["repo_id"], path_identity["relative_path"]
        )
        proposal_id = _proposal_id(target_path, before_sha)
        loop_result["id"] = _decision_event_id(proposal_id, "accept", best["content"], revert_generation)
        merge_revert_fields(loop_result, {
            "revert_before_b64": before_b64, "revert_unavailable_reason": unavailable_reason,
            "revert_schema_version": REVERT_SCHEMA_VERSION, "revert_encoding": REVERT_ENCODING,
            "revert_generation": revert_generation, **path_identity,
            "after_sha": after_sha,
        })
        _attach_loop_provenance(loop_result, dry_run=dry_run)
        # _locked 版を呼ぶ（外側で既に file_lock 保持中——公開版 append_history_entry_deduped を
        # 呼ぶと二重 lock で自己 deadlock する。round2 Must5 の直接対応）
        _history_store._append_history_entry_deduped_locked(loop_result, history_file)
else:
    loop_result["id"] = <kind="reject"/"pending" の id。revert_fields は付与しない>
    _attach_loop_provenance(loop_result, dry_run=dry_run)
    # こちらは lock を保持していないので公開版（自分で lock を取る）を使う
    _history_store.append_history_entry_deduped(loop_result, _history_slug, history_file)
```

**id 計算に `_decision_event_id`（sha版でなく通常版）を使う理由**: C は同一プロセス・同一ループ反復内で
`best["content"]`（after 本文そのもの）を既に持っているため、B のような「別プロセスへ sha だけ運ぶ」
制約が無い。素直に通常版を使う（`_decision_event_id_from_sha` は B 専用ではなく汎用の共有関数だが、
C では使う必然性が無いので使わない——両者とも同じ `base` 文字列を生成するため、どちらを使っても
生成される ID は同一）。

**Must5 への対応（冪等性の意味論 + round2 で発覚した二重 lock の解消）**: `_decision_event_id` は
`(proposal_id, kind, after_content, generation)` から決定論的に計算されるため、**同一 loop を独立に
2回実行して before/after が同一内容になった場合のみ**同一 id になり、`_append_history_entry_deduped_locked`
が2回目を no-op にする。**before/after が異なれば（＝実際に別の提案）別 id になり、2件書かれるのは
意図どおりの新規 accept**——「同じ判断イベントの再試行（id 一致）」と「独立した次回 accept（id 不一致）」
を id の値そのもので機械的に区別する。
**round2 Must5（二重 lock による自己 deadlock）**: 初版のコード例は `with file_lock(...)` の内側から
（自分で lock を取る）公開版 `append_history_entry_deduped` を呼んでいたため、同一 lock を二重取得し
自己 deadlock していた。§2.3 のコード例を「外側で1回だけ lock を取り、lock 保持中は `_locked` 版
（lock を取らない内部版）だけを呼ぶ」形に修正した（`rl_common/file_lock.py` / `evolve_revert/_apply.py`
が既に持つ既存慣習——本 repo で lock 下から呼ぶ関数は必ず `_locked` 版を使う——にB/Cとも揃えた）。

**`not dry_run` ゲートの必要性（既存発見・維持）**: `--auto --dry-run` 実行では `approved=True` かつ
`dry_run=True` の entry が history に書かれる既存挙動（`run_loop.py:605-607` の `elif auto: approved
= True` が `not dry_run` 非条件）があるため、revert_fields の付与は必ず `approved and not dry_run`
を条件にする（実書込ゲート `if approved and not dry_run:` と同じ条件式を流用）。

### 2.4 データ契約の変更点

**純加算 + 追跡用の非 REVERT_FIELD_KEYS フィールド1種**（round2 で `revert_generation_snapshot` /
`_pending_after_sha` を廃止し §2.2 の契約表に一本化——`revert_generation` は正式な `REVERT_FIELD_KEYS`
として write 時に確定し、`after_sha` も同じく write 時に確定するため、内部専用の仮フィールドは不要
になった）。既存 entry のキー集合・型は変更しない。新規に増えるのは:
- `id`（B・C とも今回初めて付与。**既存の過去 entry には遡及しない**）
- `REVERT_FIELD_KEYS` のサブセット（B・C とも **write 時に一度に確定**。B は accept 確定時に `id`
  だけを作り直すが、`after_sha`/`revert_generation` 等の値そのものは書き換えない——§2.2 の契約表）
- B のみ: `proposal_id` / `before_sha`（`REVERT_FIELD_KEYS` に含めない追跡用フィールド。
  `merge_revert_fields` の対象外として直接 `entry[...]` に代入する）

**既存 entry（revert 情報なし）との後方互換**: `compute_revert_availability` は `revert_schema_version`
の有無だけを見るので、過去の B/C entry は今まで通り `pre_extension` のまま。

**`load_effective_history` / `fold_effective` / `raw_history_gate` への影響**:
- `load_effective_history`/`fold_effective`: **影響なし**（revert イベントの畳み込みロジックは entry の
  出所を区別しない）。
- **`raw_history_gate.py` への影響は「ゼロ」（round2 で読取方式を変更したため初版・改訂1の判断を再訂正）**:
  §2.1/§2.2/§2.3 のとおり、B・C の `revert_generation` 読取は `optimize_history_store.load_raw_history(...)`
  ではなく `_read_jsonl(history_file)`（write 先と同一の単一ファイルを直接読む）に変更した
  （round2 Must-new #2 対応の副産物）。`raw_history_gate._TARGET_FUNCS = ("load_history",
  "load_raw_history", "load_raw_history_with_aliases")`（`raw_history_gate.py` 実測）は **`_read_jsonl`
  を対象に含まない**——AST gate はこの callsite を検出しないため、**`PRODUCTION_ALLOWLIST` への追加は
  不要**（改訂1の「2エントリ追加が必要」という判断を撤回する）。
  ただし `raw_history_gate.py` の docstring 自身が明記するとおり「AST の限界: 任意の `_read_jsonl()` /
  `Path.read_text()` が『raw history 読取』かは AST では判定できない」——**AST gate の射程外であって
  『raw 読取でない』わけではない**。既存の `outcome_promotion_readiness` の glob 直読みと同じ扱いで、
  **`raw_history_gate.py` docstring の「既知の history direct reader」棚卸しリストに B/C の2件を明記
  として追加する**（D1 の必須差分——AST では検出できないが人間のレビュー時に「意図的な直接読取」だと
  分かるようにする。契約テスト・固定件数テストの更新は不要）。

### 2.5 冪等性・失敗時の挙動

- **B**: `save_history_entry`（write 時）は毎回新規 append であり冪等性の懸念は無い（同一 run が
  2回呼ばれることは `run()` の構造上起きない）。`record_human_decision`（accept/reject 確定）は
  `run_id`+`target`+`after_sha 存在` の複合検索 + `entry.get("human_accepted") is human_accepted` の
  no-op 判定で冪等（§2.2 契約表）。
  **失敗時の surface**: 対象 entry が見つからない／複数一致した場合は `print` で明示エラーを出し、
  `False` を返す。呼び出し元 CLI（`optimize.py:572-581`）はこの返り値を見て**非ゼロ終了**する
  よう変更する（現状 `record_human_decision` は返り値を持たず、CLI は常に「結果を{status}として
  記録しました」と無条件成功表示している——**これは既存の marker_error 原則違反**であり、D1 で
  修正する。codex round1 Should2 に対応）。
- **C**: `_append_history_entry_deduped_locked`（外側の単一 lock 内から呼ぶ）が「既存 id 確認 →
  append」を原子化するため、同一 id での二重記録を構造的に防ぐ（§2.1・round2 Must5 対応で lock
  境界を修正済み）。
- **共通**: `merge_revert_fields` が衝突検出時に `ValueError` を送出する契約（`record_evolve_diff_decision`
  と同じ）。B/C の呼び出しでは既存キー（`id`/`skill_name`/`timestamp`/`target`/`run_id`/`proposal_id`/
  `before_sha` 等）と `REVERT_FIELD_KEYS` が重複しないことを実装時に確認し、契約テストで固定する。

### 2.6 既存の契約テスト・snapshot テストへの影響（grep 実測 + 改訂で追加）

| ファイル | 影響 |
|---|---|
| `skills/genetic-prompt-optimizer/tests/test_optimizer.py` | `save_history_entry`/`record_human_decision` の entry 形・戻り値契約が変わる（大幅リライト——`.backup` 依存を前提にした既存テストがあれば削除し、`run_id`+`target`+`after_sha` 複合検索・no-op 判定・`proposal_id`/`before_sha` フィールドのテストに置き換え） |
| `skills/genetic-prompt-optimizer/tests/test_integration.py` | 同上（E2E）。`--accept` の CLI 経路が非ゼロ終了しうるようになるため exit code の assert を追加 |
| `skills/evolve-loop-orchestrator/tests/test_loop.py` | `loop_result` の形が変わる（`id`/`after_sha`/`revert_generation` 追加）。`_history_store.append_entry` の mock を `_append_history_entry_deduped_locked`（accept時）/`append_history_entry_deduped`（reject/pending時）へ差し替え |
| `scripts/lib/tests/test_results_board.py` | fixture に B/C 型 entry（`after_sha` 込み）を追加し、「revert 可能かつ apply も成功する」ケースを明示的にカバー |
| `scripts/lib/tests/test_raw_history_gate*.py` | **影響なし（round2 で `_read_jsonl` 直読み方式に変更したため改訂1の判断を再訂正）**。`PRODUCTION_ALLOWLIST`・固定件数テストとも変更不要 |
| `scripts/lib/tests/test_evolve_revert_apply.py`・`_entry.py`・`_target.py`・`_render.py`・`_metadata.py`・`_dump.py` のテスト | 直接の破壊的影響は無い想定（apply engine 自体は変更しない）。新規カバレッジは §4 の synthetic E2E で追加 |
| **`scripts/lib/tests/test_evolve_revert_availability.py`** | **影響あり**（§2.7 の schema 一貫性検査追加に伴い単体テストを追加・既存テストの fixture が after_sha/id を持たない場合の期待値を確認） |
| `scripts/lib/tests/test_shrink_freeze.py` | 影響なし（新規 store/observability/adapter/channel を作らないため） |
| `scripts/lib/evolve_decision_ids.py` の既存テスト | `merge_revert_fields`/`_decision_event_id_from_sha` 抽出・private 関数の public rename により新規テスト対象が増える（特に `_decision_event_id_from_sha(...) == _decision_event_id(...)` のビット同一性を回帰テストで固定する） |
| **`scripts/lib/tests/test_optimize_history_store.py`（新規 or 既存拡張）** | `append_history_entry_deduped`/`_append_history_entry_deduped_locked` の単体テスト（重複 id・id 無し ValueError・lock 下原子性・**二重 lock を取らないことの回帰テスト**） |

### 2.7 `evolve_revert/_availability.py` の schema 一貫性検査（team-lead 裁定・§0 の唯一の例外）

**動機**: `compute_revert_availability`（`_availability.py:59-76`）は現状 `revert_unavailable_reason`/
`revert_schema_version`/`scope`/`revert_before_b64` の4項目しか見ない。`after_sha`/`id` の有無を検査
しないため、**理論上**「schema はあるが `after_sha`/`id` が欠けている entry」に対して
`available=True` を返してしまい、実際の `apply_revert`/`find_entry` は失敗する——「戻せると表示した
のに戻せない」は柱4「信頼」を直接壊す（round1 Should1）。

D1 の新規 entry（A/B/C 全経路）は本設計により `id`/`after_sha` を常に伴って書かれるため、**今この時点
では実害は無い**（round1 Should1 の議論どおり）。しかし team-lead 裁定は「将来 D1 以外の新規 writer が
増えたときに同じ穴が再発するのを構造で防ぐ」ことを優先し、`_availability.py` への追加を決定した。

**設計**: 既存の3理由コード（`pre_extension`/`lane_unsupported`/`before_too_large`）を**増やさない**。
`id`/`after_sha` が欠けている場合は既存の `pre_extension`（「戻す機能の導入前に採用されたため…」）に
**意味的に相乗り**させる——「revert に必要なデータが揃っていない」という利用者向けメッセージの意味は
共通しており、4つ目のコードを新設するコストに見合わない:

```python
def compute_revert_availability(entry: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if entry.get("revert_unavailable_reason") == REASON_BEFORE_TOO_LARGE:
        return False, REASON_BEFORE_TOO_LARGE
    if not entry.get("revert_schema_version"):
        return False, REASON_PRE_EXTENSION
    if entry.get("scope") not in _SUPPORTED_SCOPES:
        return False, REASON_LANE_UNSUPPORTED
    if not entry.get("revert_before_b64"):
        return False, REASON_PRE_EXTENSION
    # 新設（team-lead 裁定）: apply_revert の必須検査（_apply.py:258-267）と同じ2条件を
    # listing 時点でも検査する。schema はあるが id/after_sha が無い entry は
    # 「revert に必要なデータが揃っていない」という意味で pre_extension に相乗りさせる
    # （新しい理由コードは作らない）。
    if not entry.get("id") or not entry.get("after_sha"):
        return False, REASON_PRE_EXTENSION
    return True, None
```

**影響範囲の確認**: `compute_revert_availability` の呼び出し元は `results_board.py`（withdrawal
candidates）と D2 の `--list`（§3.2）の2箇所のみ（実測: `grep -rn "compute_revert_availability"`）。
両方とも「戻せない理由」の表示ロジックは理由コード→日本語ラベルの変換（`REASON_LABELS`）を経由するため、
新しい分岐を追加でハンドルする必要はない。

---

## 3. D2 の設計 — entry_id の導線拡充（`bin/evolve-revert --list` 相当）【codex Must8 を反映】

### 3.1 現状の導線（実測）

`results_board.py` の `withdrawal_candidates`（`build_results_board:226-240`）は**既に** entry_id /
revert_available / revert_unavailable_reason / reverted の4フィールドを構造化結果に含み、
`render_results_board`（`:292-311`）は `revert_available=true` の行に実行コマンドそのものを2段（dry-run
案内 + `--apply`）で印字する。これは ADR-053 §3・S4 の完了条件を**満たしている**（実装済み）。

**ただし対象が限定的**: `withdrawal_candidates` は `buckets["accepted"]` のうち
**`verdict == "REGRESSED"` の entry だけ**を対象にする（`results_board.py:227-229`）。`bin/evolve-revert`
自体（`evolve_revert_cli.py`）は「サブコマンドを持たない単一動作の CLI」で、**`--list` に相当するもの
は存在しない**。

### 3.2 `--list` の出力仕様案【Must8: accept のみを対象にする】

**codex Must8 への対応**: `load_effective_history(slug)` は revert 済み entry と revert イベント自体を
除くだけで、**reject/pending も含めて返す**（`fold_effective` の出力契約——`optimize_history_store.py:
250-272` を再読して確認: フィルタ条件は `is_revert_event`/`reverted_entry_id` の2つのみで、
`human_accepted`/`approved` の値は一切見ない）。よって `--list --all` を素朴に実装すると **reject や
pending の entry まで「revert 候補」として並ぶ**という誤りが起きる。

**修正した設計**: `results_board.classify_decision` を再利用し、**`accepted` に分類される entry だけを
対象**にしてから `compute_revert_availability` を計算する（判定ロジックの二重実装を避ける——
`results_board.py` の `buckets["accepted"]` と全く同じ絞り込みを行う）。

```python
# evolve_revert/_render.py に新設
def collect_revertable_entries(slug: str, *, include_unavailable: bool = False) -> List[Dict[str, Any]]:
    from results_board import classify_decision  # 既存の accepted 判定を単一ソースとして再利用
    history = load_effective_history(slug) or []
    accepted = [e for e in history if classify_decision(e) == "accepted"]
    out = []
    for e in accepted:
        available, reason = compute_revert_availability(e)
        if available or include_unavailable:
            out.append({...})
    return sorted(out, key=lambda x: _sort_key(x["timestamp"]), reverse=True)
```

`bin/evolve-revert --list` は `include_unavailable=False`、`--list --all` は `include_unavailable=True`
を渡す。**出力仕様（列・並び順・実行コマンド印字）は初版の記述と同じ**（entry_id / skill_name /
timestamp / revert_available / 理由。timestamp降順。`revert_available=true` の行に2段コマンド印字。
**既定20件・`--all` 提供**——team-lead 裁定で確定（§7-3））。

**循環 import への注意**: `evolve_revert/_render.py` から `results_board.classify_decision` を import
すると、`results_board.py` が将来 `evolve_revert` 側の関数を import するように変更された場合に循環が
起きる。現状 `results_board.py` は `evolve_revert` の `compute_revert_availability`/`REASON_LABELS` のみ
import しており逆方向の依存は無いため、**今回時点では安全**（実装時に `import` 文を確認し、循環が
生じる場合は `classify_decision` を `results_board.py` から `optimize_history_store.py` 寄りの共有
モジュールへ抽出する代替案に切り替える）。

### 3.3 凍結非抵触の根拠

`shrink_freeze.py` の凍結対象4集合（`store_registry` / `audit.observability._OBSERVABILITY_BUILDERS` /
`advisory_proposals.ADVISORY_PROPOSAL_ADAPTERS` / `weak_signals.channels.WEAK_SIGNAL_CHANNELS`）を
D1/D2 のどちらも一切増やさない（初版から変更なし）:

- D1 は既存 store（`optimize_history/<slug>.jsonl`）への**純加算 write**のみ。新しい store basename は
  作らない。
- D2 の `--list` は既存 read-only 関数（`load_effective_history`・`results_board.classify_decision`・
  `compute_revert_availability`）を組み合わせるだけの**新しい CLI フラグ**であり、observability
  section でも advisory adapter でも weak-signal channel でもない。

**結論**: D1/D2 とも `shrink_freeze.CULLED_OBSERVABILITY_SECTIONS`/`FROZEN_*` のいずれにも触れず、
凍結解除を待たずに着手できる。

---

## 4. 完了条件（ADR-054 §8 D行）の synthetic E2E テスト設計【codex Must1/Must6/Must7 を全面反映】

**完了条件**: 「新規 accept が `revert_available=true` で記録され、`bin/evolve-revert` が dry-run で
復元内容を印字」。実データでは B/C 経路の accept 対象が0件なので **synthetic 必須**。

### 4.1 CLI entry point を実際に起動する（Must6）

**初版の誤り**: `apply_revert()` を直接呼ぶ設計では引数解析・slug 解決・render・exit code を通らず、
完了条件が求める「`bin/evolve-revert` が印字」を検証したことにならない。既存
`test_evolve_revert_cli.py`（`TestEndToEndDryRunNoWrite`, `:131-168`）が採用している実際の慣習——
`evolve_revert_cli` モジュールを import して **`cli.main([...])`** を呼ぶ（`bin/evolve-revert` の
シェバン経由フル subprocess 起動ではないが、引数解析・`apply_revert`/`dump_before` への委譲・
render・`sys.exit` 相当の戻り値まで全て通る）——を synthetic E2E でも**そのまま踏襲する**。

### 4.2 fixture の形

`scripts/lib/tests/test_phase_d_synthetic_e2e.py`（新規）に2シナリオ（B用・C用）を置く。共通セットアップ:
`tmp_path` を `DATA_DIR`/slug 双方で隔離（既存 root conftest の `isolate_home` autouse を利用）。
`monkeypatch.setattr(store, "resolve_slug", lambda cwd=None: "proj")` パターンを既存 CLI テストから流用。

**B（optimize.py）シナリオ**（正常系を最後まで通す——Must1 の「`apply_revert(...).ok is True` まで検証」対応）:

```python
def test_optimize_accept_produces_apply_success(tmp_path, monkeypatch):
    # 1. 対象ファイル・git repo を tmp_path に用意。DirectPatchOptimizer.run() 相当を呼ぶ
    #    （LLM 呼び出しは mock——subprocess.run/anthropic SDK を必ず mock する。no-llm-in-tests 対象）
    # 2. save_history_entry(result) が id・proposal_id・before_sha・after_sha・
    #    revert_generation（正式 REVERT_FIELD_KEYS）を持つ pending entry を1行書くことを確認
    #    （§2.2 契約表どおり、after_sha は成功パスの呼び出しでのみ付与される）
    # 3. record_human_decision(run_dir, human_accepted=True) を呼び、True を返すことを確認
    #    （§2.2「accept対象entryの検索仕様」どおり run_id+target+after_sha存在の複合検索で対象特定）
    # 4. history の対象行: human_accepted=True / id が
    #    _decision_event_id_from_sha(proposal_id, "accept", after_sha, revert_generation) と一致 /
    #    after_sha == sha256(patched_content)（write時の値のまま・再計算されていない） /
    #    revert_schema_version あり / revert_before_b64 の decompress が original_content と一致 /
    #    scope=="project" / repo_id が git repo root と一致
    # 5. compute_revert_availability(entry) == (True, None)
    # 6a. dry-run: evolve_revert_cli.main([entry_id]) を呼ぶ。rc == 0。
    #     capsys の stdout が render_dry_run_preview の構成要素（"保持: mode" 等）を含む
    #     （「before 全文が出る」という初版の誤った assert を削除——Must6 の指摘どおり
    #     preview は losses の要約であって全文ではない）
    # 6b. dry-run のゼロ書込を §4.3 の厳密スナップショットで確認
    # 7. apply: evolve_revert_cli.main([entry_id, "--apply"]) を呼ぶ。rc == 0。
    #    対象ファイルの内容が original_content に戻っている。
    #    stdout に render_apply_success() の文言（N1_REAPPEAR_NOTICE）が含まれる。
    #    history に revert イベント（event_type=="revert"）が追記されている
    #    （Must1 完了条件そのもの: apply_revert(...).ok is True を CLI 経由で確認）
    # 8. reject シナリオ: 別 run を1つ作り record_human_decision(human_accepted=False) を呼び、
    #    REVERT_FIELD_KEYS が entry から pop されている（本文を保存しない契約の回帰テスト）
    # 9. 二重 accept: 手順3を再度呼び、2回目は False を返さず True・かつ history 行数が
    #    増えていないことを確認（no-op の回帰テスト・Must4）
```

**C（run_loop.py）シナリオ**: 同型。`run_loop(..., auto=True, dry_run=False)` を1ループだけ回し
（フィットネス関数・variant 生成は決定論 stub に差し替え、LLM 非依存で完走させる）、`approved=True`
かつ `dry_run=False` の loop_result が `after_sha` 込みの revert_fields で書かれることを確認し、
同じ手順6a/6b/7 を CLI 経由で実行する。加えて:

```python
    # 10. 同一 before/after 内容で run_loop をもう一度呼ぶ（決定論 stub なので再現可能）。
    #     _append_history_entry_deduped_locked が2回目を書かないこと（Must5・id 一致による no-op）
    # 11. before/after 内容が異なる2回目（stub の返す improved 内容を変える）を呼ぶ。
    #     id が1回目と異なり、2件とも history に存在すること（意図した新規 accept との区別）
```

### 4.3 dry-run ゼロ書込の厳密な検証（Must7・round2 で「監視リスト方式」から「sandbox 全体方式」へ訂正）

**改訂1の弱点（round2 で指摘）**: 対象ファイル・history・lock sidecar という**監視リストに限定した**
bytes/stat 突合は、**リスト外の既存ファイル**（例: git 管理ファイル、隣接する他 slug の history 等）
が書き換わっても検出できない。「1バイトも書かない」という契約は sandbox 全体に対して主張すべきもの
であり、監視リストという事前の決め打ちに依存すべきではない。

**改訂2の検証**: `tmp_path` 配下の**全ての通常ファイル**（`is_file()` のもの。ディレクトリ自体の
存在は path 集合比較で拾う）について `(相対パス, content bytes, os.stat の
st_mtime_ns/st_size/st_mode)` のタプルを作り、dry-run 呼び出しの前後で**辞書として完全一致**する
ことを assert する。監視リストという概念自体を廃止し、sandbox 全体を対象にする:

```python
def _snapshot_sandbox(root: Path) -> Dict[str, Tuple[bytes, int, int, int]]:
    """root 配下の全ファイル（ディレクトリは除く）の内容+stat を丸ごとスナップショットする。
    監視対象を事前に決め打ちしない——「1バイトも書かない」を sandbox 全体に対して検証する
    （round2 Must7: 監視リスト外の既存ファイルの書換えも検出できることが必須）。
    """
    return {
        str(p.relative_to(root)): (
            p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_size, p.stat().st_mode,
        )
        for p in root.rglob("*") if p.is_file()
    }

before_snap = _snapshot_sandbox(tmp_path)
rc = evolve_revert_cli.main([entry_id])
after_snap = _snapshot_sandbox(tmp_path)
assert rc == 0
assert before_snap == after_snap  # キー集合（＝新規/削除ファイルの有無）も値（＝内容/stat）も両方比較
```

`dict` の等価比較はキー集合・値の両方を見るため、新規ファイル創出・削除・既存ファイルの内容変化の
**全てを1つの assert で検出**する（改訂1の「path 集合比較 + 監視リスト bytes/stat 比較」の2本立てを
1本に統合し、かつ監視対象の限定を撤廃した）。**既存 `test_evolve_revert_cli.py:TestEndToEndDryRunNoWrite`
（`set(tmp_path.rglob("*"))` のみ）にもこの `_snapshot_sandbox` を遡って適用する**——team-lead 裁定で
確定（§7-7）。同一テストファイルの小規模改修のため PR2（C を lane へ寄せる PR）に相乗りする（§5.1・§5.3 PR2）。

---

## 5. 想定差分規模とリスク

### 5.1 ファイル・行数の見積もり（改訂3: team-lead 裁定8点を反映）

| ファイル | 種別 | 見積行数 |
|---|---|---|
| `scripts/lib/evolve_decision_ids.py` | 変更（`merge_revert_fields`/`_decision_event_id_from_sha` 抽出・**private 関数の public rename**） | +40〜55 |
| `scripts/lib/optimize_history_store.py` | 変更（`append_history_entry_deduped`/`_append_history_entry_deduped_locked` 新設） | +30〜45 |
| `scripts/lib/evolve_decisions/_emit.py`・`_candidates.py`・`__init__.py` | 変更（rename に伴う import 元の追従） | +5〜15（機械的） |
| `scripts/lib/evolve_revert/_apply.py`・`_entry.py`・`_target.py` | 変更（同上） | +5〜10（機械的） |
| `scripts/lib/raw_history_gate.py` | 変更（「既知の history direct reader」棚卸しリストへ B/C の2件を明記。**allowlist 自体・固定件数テストは変更不要**） | +5〜10 |
| `scripts/lib/evolve_revert/_availability.py` | 変更（**after_sha/id の schema 一貫性検査を追加——team-lead 裁定で確定**） | +10〜15 |
| `skills/evolve-fitness/scripts/fitness_evolution.py` | 変更（共有関数呼び出しへリファクタ） | -20/+10（純減） |
| `skills/genetic-prompt-optimizer/scripts/optimize.py` | 変更（`save_history_entry` 全面改訂・`record_human_decision` を2段検索+append-only へ書き換え・CLI の exit code 対応） | +80〜110 |
| `skills/evolve-loop-orchestrator/scripts/run_loop.py` | 変更（revert_fields 組み立て・after_sha 追加・dedup 呼び出し・**`--auto --dry-run` の `approved` 汚染修正**） | +40〜60 |
| `scripts/lib/evolve_revert/_render.py` | 変更（`collect_revertable_entries`/`render_list` 追加） | +35〜50 |
| `scripts/lib/evolve_revert_cli.py` | 変更（`--list`/`--all` フラグ・`_run_list`） | +25〜35 |
| `scripts/lib/tests/test_phase_d_synthetic_e2e.py` | 新規（apply 成功まで検証・厳密スナップショット・**反転シナリオ**） | +250〜350 |
| `scripts/lib/tests/test_evolve_decision_ids.py` | 変更（**`_decision_event_id_from_sha` のビット同一性契約テスト**） | +20〜30 |
| `scripts/lib/tests/test_evolve_revert_render.py` | 変更（`render_list`/`collect_revertable_entries` の単体テスト追加） | +50〜70 |
| `scripts/lib/tests/test_evolve_revert_cli.py` | 変更（`--list` の CLI テスト追加。**既存 `TestEndToEndDryRunNoWrite` へ sandbox 全体スナップショットを遡及適用**） | +40〜70 |
| `scripts/lib/tests/test_evolve_revert_availability.py` | 変更（schema 一貫性検査の単体テスト追加） | +20〜30 |
| `scripts/lib/tests/test_optimize_history_store.py` | 新規 or 拡張（`append_history_entry_deduped` 単体テスト） | +40〜60 |
| `skills/genetic-prompt-optimizer/tests/test_optimizer.py` | 変更（既存 `.backup`/最終行前提のテストを置き換え・**反転（reject→accept）テスト追加**） | +60〜90 |
| `skills/evolve-loop-orchestrator/tests/test_loop.py` | 変更（同上 + `--auto --dry-run` 汚染修正の回帰テスト） | +40〜60 |
| `scripts/lib/tests/test_results_board.py` | 変更（B/C 由来 entry の revert_available fixture 追加） | +15〜25 |
| `.claude-plugin/`（CHANGELOG・SKILL.md 等） | 変更（**`.backup`/`--restore` と entry_id revert の使い分け1段落を追記**——PR4） | +10〜20 |
| **合計（概算）** | | **約 800〜1200 行**（うちテストが約6割） |

`file-size-budget.md` の 500行/800行しきい値は個別ファイルの source 行数が対象。`optimize.py`・
`run_loop.py` は実装時に `wc -l` で現状行数を確認し分割要否を判断する（未実測・別リスク、変更なし）。

### 5.2 回帰リスク（改訂3）

| リスク | 深刻度 | 緩和策 |
|---|---|---|
| B/C の entry 形変更で既存 assert が壊れる | 中 | §2.6 の影響表どおり実装前に確認・置き換え |
| B の `record_human_decision` を「最終行書き換え」から「2段検索 + append-only」へ全面変更 | **中〜高** | 既存の呼び出し元（CLI のみ・実測要）を確認。挙動変更が大きいため既存 `test_optimizer.py`/`test_integration.py` の該当テストは書き換え前提。CLI の「記録しました」無条件成功表示を非ゼロ終了対応に変える点は**利用者が見る文言が変わる**ため、SKILL.md/CHANGELOG に明記が必要 |
| `evolve_decision_ids.py` の private 関数 public rename が既存 5 ファイル（`_emit.py`/`_candidates.py`/`__init__.py`/`_apply.py`/`_entry.py`/`_target.py`）の import を巻き込む | 低〜中 | 機械的な rename なので破壊的ではないが、実装時に**monkeypatch 対象（`evolve_decisions/__init__.py` の「束縛フェンス」）になっていないか**を rename 前に確認する（`_decision_event_id` 等が個別 monkeypatch されていれば rename で test が壊れる） |
| `_availability.py` への schema 一貫性検査追加が既存 `compute_revert_availability` の呼び出し元（`results_board.py`）の挙動を変える | 低 | 追加する検査は「after_sha/id が無ければ `pre_extension` 扱いにする」という**既存の `pre_extension` 判定を厳密化するだけ**（新しい理由コードは増やさない）。D1 以前の entry は元々これらのフィールドを持たないため実質的な挙動変化は無い（D1 以降の新規 entry が「常に schema が揃っている」保証が崩れた場合のセーフティネットとして機能する） |
| `.backup` 依存を撤去したことで既存 `--restore` CLI（`optimize.py:266-276`）との関係が変わるか | 低 | **変わらない**——`--restore` は `.backup` ファイルを直接使う独立した機能で、D1 は `save_history_entry`/`record_human_decision` 側の before 取得元を変えるだけ。`.backup` ファイル自体・`backup_original()`・`restore()` は無変更 |
| `run_loop.py` の `--auto --dry-run` approved 汚染修正が既存の dry-run 出力・呼び出し元の期待を変える | 中 | `elif auto: approved = True` を `elif auto and not dry_run: approved = True` へ変更する想定（dry-run 時は自動承認をそもそも成立させない）。既存 `test_loop.py` の dry-run 系テストで `approved` の期待値を確認・更新する |
| history entry のサイズ増（B は accept 前でも revert_before_b64 を持つ。reversal のたびに decision entry が append されるため、複数回反転した run は entry 数がさらに増える） | 低〜中 | `REVERT_BEFORE_MAX_COMPRESSED_BYTES` 上限は既存のまま流用。反転は稀な操作前提（対話的 CLI）なので総量は限定的 |
| optimize.py の既存 `.backup`/`--restore` 機構と `bin/evolve-revert` の entry_id 機構が2つの独立した revert 手段として併存する | 中 | 統合しない（用途が異なる）。使い分けは PR4 で1段落ドキュメント化する（§7 で確定） |

### 5.3 PR 分割の最終形と各 PR の完了条件（team-lead 裁定確定）

**PR1（共有 helper 契約）**
- 内容: `merge_revert_fields`/`_decision_event_id_from_sha`/`append_history_entry_deduped`+`_locked`版 の新設、`record_evolve_diff_decision`（A）のリファクタ、`evolve_decision_ids.py` private 関数の public rename（+ 5ファイルの import 追従）、`run_loop.py` の `--auto --dry-run` approved 汚染修正、`evolve_revert/_availability.py` の schema 一貫性検査追加
- 完了条件: ①既存 emit→drain（A）の契約テストが byte-equivalent entry・既存 dedup 挙動を維持したまま全緑 ②`_decision_event_id_from_sha` のビット同一性契約テストが全緑 ③rename が monkeypatch 対象と衝突しないことを既存テストスイートで確認 ④`--auto --dry-run` で `approved=True` の entry が記録されなくなることを回帰テストで確認 ⑤`compute_revert_availability` の schema 一貫性検査を単体テストで固定 ⑥`python3 -m pytest` 全緑

**PR2（C: run_loop.py を revert lane へ）**
- 内容: §2.3 のとおり。after_sha 付与・単一 lock 境界・`_read_jsonl(history_file)` 直読み・`_append_history_entry_deduped_locked` 経由の書込。**加えて既存 `test_evolve_revert_cli.py:TestEndToEndDryRunNoWrite` への `_snapshot_sandbox` 遡及適用（§4.3・§7-7）をこの PR に含める**
- 完了条件: ①§4.2 の C シナリオ synthetic E2E（`evolve_revert_cli.main` 経由・dry-run→apply の両方）が全緑 ②同一 loop 二重実行の dedup 回帰テスト ③既存 `test_loop.py` 全緑 ④既存 `TestEndToEndDryRunNoWrite` が `_snapshot_sandbox` 方式で全緑

**PR3（B: optimize.py を revert lane へ・複雑さが集中するため単独）**
- 内容: §2.2 のとおり。pending/decision の2段検索・append-only・`proposal_id`/`before_sha` フィールド追加・CLI 非ゼロ終了対応
- 完了条件: ①§4.2 の B シナリオ synthetic E2E（apply 成功まで）が全緑 ②reject→accept 反転シナリオで decision entry が2件（reject 1件 + accept 1件、`supersedes_id` で連結）append され、双方が raw history に残ることをテストで確認 ③二重 accept no-op 回帰テスト ④既存 `test_optimizer.py`/`test_integration.py` 全緑

**PR4（D2 + ドキュメント）**
- 内容: `bin/evolve-revert --list`/`--all`、`.backup`/`--restore` と entry_id revert の使い分け1段落
- 完了条件: ①§3.2 の `--list`/`--all` 出力仕様どおりのテスト全緑（accept のみ抽出・`classify_decision` 再利用の確認含む）②使い分けドキュメントが該当箇所（SKILL.md または CLAUDE.md の該当節）に追記されている

**実施順は PR1→PR2→PR3→PR4 固定**（PR1 の契約確定なしに PR2/PR3 は着手不可。PR4 は PR3 完了後——実データで `--list` の動作を確認できる状態が望ましいが、実装自体は PR1 完了後から並行着手可能）。

---

## 6. 実測で ADR-054 と食い違った点

1. B・C の entry は revert フィールドだけでなく `id` フィールド自体を欠いている（§1.2）。
2. `optimize.py` の accept は同一プロセス内で完結しない（`--accept` は別プロセス呼び出し）。
3. **`record_human_decision`（B）に file_lock が存在しない**（既存のレース状態。D1 で lock 化必須）。
4. `run_loop.py` の `--auto --dry-run` 実行で `approved=True` かつ実際には未適用の entry が history に
   書かれる既存挙動がある（`elif auto: approved = True` が `not dry_run` 非条件）。
5. `optimize.py` には entry_id 機構と独立した既存 revert 手段（`.backup` ファイル + `--restore` CLI）
   が既にある。
6. **（改訂で新規）`optimize.py::save_history_entry` は `after_sha` を一切記録しない**——A は
   `_ingest.py:127` で必ず付与するが、B/C はどちらも欠落しており、`apply_revert` の必須検査
   （`_apply.py:258-267`）に照らすと「revert フィールドを足しただけでは戻せない」構造的な穴が
   B/C 両方にあった（初版はこの穴に気づかず、codex round1 の Must1 で発覚）。
7. **（改訂で新規）`self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")` は秒精度のタイムスタンプ**
   （`optimize.py:97`）——1秒未満で複数 run が走る状況（自動テスト・スクリプト連続実行等）では
   衝突しうる。D1 の Must3 対応（run_id 一意検索）はこの前提の上に成り立つため、**衝突時は
   `record_human_decision` の「複数一致」エラーとして正しく検出される**設計にしたが、根本原因
   （run_id の一意性が秒精度に依存）自体は D1 のスコープ外の既存設計——UUID 化等の改善は
   **別 issue #424 として起票済み**（§7-5）。
8. **（round2 反映で判明）`after_sha`（フルhex sha256）だけで `_decision_event_id` と同一の ID を
   再構成できる**——`_decision_event_id` 内部の計算は `_sha256(after_content)[:12]` であり、
   `after_sha = _sha256(after_content)` を既に持っていれば `after_sha[:12]` と完全に一致する。
   ADR/設計のどこにもこの等価性は明記されていなかった（round1 時点では気づかず、「after 本文の
   全文を別プロセスへ運ぶ必要がある」という誤った前提で設計していた——round2 codex Must4 で発覚）。

---

## 7. 未決事項（**全件 team-lead 裁定済み・2026-08-12。設計は確定**）

| # | 論点 | 裁定 | 反映箇所 |
|---|---|---|---|
| 1 | `--auto --dry-run` の `approved` 汚染修正を D1 に含めるか | **含める**（PR1） | §2.3・§5.1・§5.2・§5.3 PR1 |
| 2 | `.backup`/`--restore` と entry_id revert の使い分けドキュメント化 | **含める**（PR4・1段落） | §5.1・§5.3 PR4 |
| 3 | D2 `--list` の既定件数上限と `--all` | **既定20件・`--all` 提供**（確定） | §3.2 |
| 4 | `evolve_decision_ids.py` の private 関数を public rename するか | **する（PR1）** | §5.1・§5.2・§5.3 PR1 |
| 5 | `run_id` の秒精度を UUID 化するか | **D1 スコープ外。別 issue #424 として起票済み**（§6-7 の複数一致エラー検出で D1 の正しさ自体は担保済み） | §6-7・issue #424 |
| 6 | `_availability.py` に after_sha/id の schema 一貫性検査を追加するか | **追加する**（§0 の「read側不変」制約の例外として明記） | §0・§2.7（新設）・§5.1・§5.2・§5.3 PR1 |
| 7 | 既存 `TestEndToEndDryRunNoWrite` への厳密スナップショット遡及適用 | **本 PR（PR2 相当のタイミング）に相乗り** | §5.1 |
| 8 | reject 済み entry への後からの `--accept`（決定の反転） | **許容。ただし上書きせず append。effective view は最新イベント（チェーンの tip）を採用。反転の事実をテストで担保** | §2.2（append-only decision entry・`supersedes_id`）・§5.3 PR3 完了条件 |

これで未決事項は残らない。**D1/D2 の設計は本改訂3をもって確定とし、codex への再レビュー依頼は行わない**
（team-lead 裁定・7条件充足済みのため）。

---

## 8. 参照

- 上位設計: [ADR-054](../054-four-pillars-completion-design.md)（§2.5・§5 Phase D・§6・§8 D行）
- revert lane 実装設計（実装済み）: [ADR-053](../053-revert-cli-design.md)
- 主要実装ファイル（本文書内で実測・引用）: `scripts/lib/optimize_history_store.py`、
  `scripts/lib/evolve_decision_ids.py`、`scripts/lib/evolve_decisions/_ingest.py`、
  `scripts/lib/evolve_decisions/_emit.py`、`scripts/lib/evolve_revert/`（`_apply.py`/`_render.py`/
  `_availability.py`/`_target.py` 含む）、`scripts/lib/evolve_revert_cli.py`、`scripts/lib/raw_history_gate.py`、
  `bin/evolve-revert`、`scripts/lib/results_board.py`、`scripts/lib/tests/test_evolve_revert_cli.py`、
  `skills/genetic-prompt-optimizer/scripts/optimize.py`、`skills/evolve-loop-orchestrator/scripts/run_loop.py`、
  `skills/evolve-fitness/scripts/fitness_evolution.py`
- codex round1 レビュー全文: `<session scratchpad>/codex_phaseD_findings.md`（判定 `設計修正要`
  [Must 8/Should 4/Nit 1]）
- codex round2 レビュー全文: `<session scratchpad>/codex_phaseD_r2.log`（判定 `設計修正要`
  [解消3/部分解消5/Must-new 2]）。**両巡とも要点は §9 に反映済みだが、原文はセッション scratchpad
  消失時に失われる可能性があるため、本 ADR ドラフトの §9 が要点の正典**
- 関連 issue: #402（revert 機構本体・マージ済み）/ #379 / #400 / #401

---

## 9. codex レビュー対応表

### 9.1 round1 対応表

| # | 重大度 | 指摘要旨 | 反映箇所 | round1 時点の対応 | **round2 判定** |
|---|---|---|---|---|---|
| Must1 | Must | B/C 両案で `after_sha` が保存設計から欠落 | §2.2/§2.3/§4.2 | 解消と判定 | **部分解消**（B の pending/accept 二重記述が競合。§9.2 Must1 で再対応） |
| Must2 | Must | B の `.backup` 依存は run 固有の before を保証しない | §2.2 | 解消と判定 | **解消（確定）** |
| Must3 | Must | B の「history 最終行」accept 対象選定は並行 run・別 target で誤帰属する | §2.2 | 解消と判定 | **部分解消**（`run_id` 単独では不十分。§9.2 Must3 で再対応） |
| Must4 | Must | B の「2回 accept で同じ id」という冪等性主張が generation 再読で成立しない | §2.2 | 解消と判定 | **部分解消**（ID再構成に after 全文が要り、sha だけでは持たない。§9.2 Must4 で再対応） |
| Must5 | Must | C の dedup が無い | §2.3 | 解消と判定 | **部分解消**（コード例が二重 lock で自己 deadlock。§9.2 Must5 で再対応） |
| Must6 | Must | synthetic E2E が CLI E2E になっていない | §4.1/§4.2 | 解消と判定 | **解消（確定）** |
| Must7 | Must | dry-run ゼロ書込検証が弱い | §4.3 | 解消と判定 | **部分解消**（監視リスト限定で sandbox 全体でない。§9.2 Must7 で再対応） |
| Must8 | Must | D2 の入力集合が accept 限定でない | §3.2 | 解消と判定 | **解消（確定）** |
| Should1 | Should | availability が after_sha/id を検査しない契約差 | §2.7 | 確認事項として保留 | round2 対象外。**改訂3で team-lead が裁定し「追加する」で確定**（§2.7・§7-6） |
| Should2 | Should | backup 不在の失敗を偽装しない | §2.2/§2.5 | 解消と判定 | 前提（`.backup`依存）消滅により該当なし。新failure modeは§2.2の複合検索が担う |
| Should3 | Should | raw/effective 契約影響「ゼロ」は誤り | §2.4 | 解消と判定（allowlist2件追加） | **round2 で再訂正**: `_read_jsonl` 直読みへ変更したため allowlist 追加は不要と判明（§2.4） |
| Should4 | Should | 共有helper抽出を最初の契約PRへ | §5.3 | 解消（採用） | 変更なし（§9.2 で4分割として再確定） |
| Nit | Nit | 全数調査・凍結非抵触は正確 | §1.1・§3.3 | 確認のみ | 変更なし |

### 9.2 round2 対応表（本改訂で反映）

codex round2 判定: `設計修正要`（解消3=Must2/Must6/Must8・部分解消5=Must1/Must3/Must4/Must5/Must7・
Must-new 2件）。診断: 部分解消5件と Must-new 2件は**すべて「B の pending→accept 契約が一本化されて
いない」ことに由来**すると指摘され、**個別パッチでなく §2.2 に契約表として一本化**する形で対応した。

「これを直せば着手可」として codex が明示した**7条件**への充足状況:

| # | 条件 | 充足状況 | 反映箇所 |
|---|---|---|---|
| 1 | B の pending→accept 契約の一本化（continuationでなく1つの表） | ✅ | §2.2「契約表（B の pending→accept ライフサイクル）」に一本化。他の節はこの表を参照する形に変更 |
| 2 | accept 対象特定に proposal identity を併用（run_id 単独からの脱却） | ✅ | §2.2「accept 対象 entry の検索仕様」——`run_id`+`target`+`after_sha 存在`の複合条件。`after_sha 存在`条件が dry-run/失敗/gate拒否 entry を構造的に除外する |
| 3 | after 本文 または ID 再構成可能な値を保存する | ✅ | §2.1 に `_decision_event_id_from_sha(proposal_id, kind, after_sha, generation)` を新設。`after_sha`（フルhex）から `_sha256(after_content)[:12]` とビット同一の値を再構成できることを明記（after 全文の保存は不要） |
| 4 | `revert_generation` を正式な `REVERT_FIELD_KEYS` として保存 | ✅ | §2.2 契約表「pending 時」の行で `merge_revert_fields` の呼び出しに `revert_generation` を含める形に統一。内部専用スナップショット（`revert_generation_snapshot`）は廃止 |
| 5 | ID世代計算のhistoryと書込先historyを同一に固定 | ✅ | §2.2「generation の読取元」行 + §2.3 で `_read_jsonl(history_file)`（write先と同一の物理ファイル直読み）に統一。`_store.load_raw_history(slug)`のような別解決を廃止 |
| 6 | C の二重lock解消 | ✅ | §2.3 のコード例を「外側で1回だけ lock を取り、lock保持中は `_locked` 版だけを呼ぶ」形に修正。§2.1 に公開版/`_locked`版の分離を新設 |
| 7 | sandbox全体のdry-run不変テスト | ✅ | §4.3 を「監視リスト限定」から「`tmp_path` 配下の全ファイルの content+stat 完全一致」に変更 |

**新たに生じた欠陥への対応（Must-new）**:

| # | 指摘 | 対応 |
|---|---|---|
| Must-new1 | `revert_generation` が内部フィールドどまりで正式 `REVERT_FIELD_KEYS` でない | 条件4で解消（`revert_generation` は他の revert フィールドと同じ `merge_revert_fields` 経由で正式に永続化） |
| Must-new2 | 明示 `history_file` 経路でも generation 読取が別ストア（`resolve_slug()` 経由）を見る | 条件5で解消（`_read_jsonl(history_file)` で write 先そのものを読む。B・C とも同一規約） |

**[Must]（round1由来 + Must-new 含め）残存: 0件**（7条件すべて充足）。**[Should] 残存: 0件**——Should1 は
round2 時点では確認事項として保留していたが、**改訂3（本改訂）で team-lead が「追加する」と裁定し §2.7
として設計に反映済み**（§7-6）。

**round2 で追加判明した副次的な設計改善（Should3 の再訂正）**: 条件5の fix（`_read_jsonl` 直読みへの
変更）が、副次的に round1 Should3（raw_history_gate allowlist 追加）の必要性そのものを消した——
`_read_jsonl` は AST gate の `_TARGET_FUNCS` に含まれないため、allowlist 拡張は不要になった（§2.4）。
ただし AST 射程外の直接読取であることは `raw_history_gate.py` の「既知の history direct reader」
棚卸しリストに明記する（新設ではなく既存パターンへの追記）。

**再レビュー方針**: 上記7条件を全て充足したため、team-lead の指示（「7条件を満たしたらそれ以上巡を
重ねない」）に従い、**round2 の時点で codex への次巡依頼は行わない**方針だった。ただし §2.2 は
round1→round2 で3回書き直された箇所であり、**実装着手時に実コードで動作させながら契約表どおりに
振る舞うか再確認することを強く推奨する**（設計文書上の整合と実装の整合は別物——
`learning_synthetic_fixture_false_confidence` と同種の注意）。

### 9.3 未決8点の team-lead 裁定と設計確定（改訂3・最終）

round2 の [Must] 解消後に残った未決8点（§7 旧版）は、2026-08-12 に team-lead が直接裁定した（codex には
再度回さない——「codex 巡はここで打ち切り」という明示指示）。裁定内容は §7 の表に集約し、各裁定の設計への
反映は本文中（§2.2 の decision entry append-only 化・§2.3 の `--auto --dry-run` 修正・§2.7 の availability
schema 検査・§3.2 の `--list` 既定値確定・§5.1/§5.2 の rename 反映・§5.3 の PR4 完了条件）に埋め込んだ。

**本 ADR ドラフトの Status は改訂3をもって `Confirmed（設計確定・実装着手可）` とする。** これ以降の変更は
実装フェーズでの発見事項に基づく追補として扱い、新たな Must/Should ラベルは付けない（設計レビューの
往復はここで終了）。
