# 570: skill_vuln_scan の ReDoS（O(n²)化）を別プロセス隔離の時間予算で止血する（R4）

- issue: #570（refs #566）
- 状態: ドラフト（R4・signal ベースから別プロセス隔離へ変更。ユーザー裁定＋巡3承認 2026-09-09）
- 作成日: 2026-09-09（R4）
- 前身:
  - R1（`fix/570-redos-scan` commit `c37c7f9b`）: 正規表現の線形時間化を推奨。
    codex 設計レビュー巡1 **設計修正要・[Must] 5件**（推奨案が反例で線形にならない）
  - R2（commit `08f2546e`）: 「1論理行の長さ」で打ち切る方式へ縮小。team-lead の実測で
    行長閾値が合計停止時間の上限にならないと判明し破棄
  - R3（commit `5a0694d7`）: `signal.setitimer(ITIMER_REAL)` によるハードタイムアウト方式へ変更。
    **codex 設計レビュー巡2 で設計修正要・[Must] 11件**（うち7件が signal 使用に由来。詳細は
    「巡2の指摘と対応」節）
  - **本 R4 は R1 の設計レビュー1巡を継続して引き継ぐ（通算で codex レビュー2巡・[Must] 16件）**
    （`review.md`: 変更系列はリセットできない）。ユーザー裁定により、本 R4 の方針
    （別プロセス隔離）は**巡3として承認済み**（承認行は issue #570 本文）

## R3 からの変更点（signal 方式の破棄）

codex 設計レビュー巡2（`~/.codex-watch/design-570-r2-20260909-185052-24567.log`）で
**設計修正要・[Must] 11件**。うち7件が `signal.setitimer(ITIMER_REAL)` を使うこと自体に
由来する:

1. SIGALRM・タイマーはプロセス全体の共有状態。既存の SIGALRM 利用箇所との保存・復元・
   所有権の確認が要る
2. 非メインスレッドでは `signal.signal` が `ValueError` を投げるが、`setitimer` は
   成功してしまうため、検査と無関係の処理へ例外が飛ぶ経路が残る
3. 非対応 OS でも fail-open にできない。**`auto_memory_broker.py:689` は `inspect_content` の
   任意例外を `try/except Exception: guard = None` で捕まえ書込を継続する
   ため、攻撃入力ほど無検査で保存される**（fail-open の実例）
4. timeout 例外の捕捉境界を公開関数内に固定する必要がある（外へ漏らすと3の広い捕捉に
   握り潰される）
9. timeout 後に2回目が正常完了するテストでは解除漏れを検出できない（タイマーは発火後0に
   戻るため）
10. signal 関連テストは pytest 自体を落とし得るため、子プロセスへ隔離が要る

**ユーザー裁定**: 検査を別プロセスで実行し、呼び出し側が `subprocess` の時間切れで押さえる
方式へ変更する。これにより 1・2・4・9・10 は**構造的に消える**（共有状態を触らない・
スレッド制約なし・例外がプロセス境界を越えない・タイマー状態が呼び出しごとに独立する）。

## 巡2の指摘と対応（11件）

| # | 指摘 | 本 R4 での扱い |
|---|---|---|
| 1 | SIGALRM は共有状態 | **構造的に解消**（別プロセスは signal を使わない） |
| 2 | 非メインスレッドで `setitimer` だけ成功 | **構造的に解消** |
| 3 | fail-open で攻撃入力ほど無検査保存 | **設計で対応**（下記「fail-closed への転換」節。実装対象に `auto_memory_broker.py` を含める） |
| 4 | timeout 捕捉境界の固定 | **構造的に緩和**（`subprocess.TimeoutExpired` は呼び出し元の1箇所でしか発生しない） |
| 5 | `scan_text` 戻り値契約 | **設計で対応**（下記「戻り値の契約」節、擬似コードあり） |
| 6 | 部分結果を空のまま保存させない | **設計で対応**（下記「fail-closed への転換」節） |
| 7 | 予算300秒は受け入れ不可（呼び出し元の締切から逆算） | **設計で対応**（下記「呼び出し経路と締切の実測」節） |
| 8 | 陰性試験②が既定値の変異を殺せない | **設計で対応**（下記「検証方法」節、契約テストと動作テストを分離） |
| 9 | timeout 後の2回目確認では解除漏れを検出できない | **構造的に解消**（別プロセスは状態を残さない） |
| 10 | signal テストは子プロセス隔離が要る | **構造的に緩和**（検査ロジック自体が既に別プロセスで動くため、pytest 側のクラッシュリスクが下がる） |
| 11 | golden は変更後の実装から生成しない | **設計で対応**（下記「検証方法」節） |

## 完成条件（round 0）

① **守る対象**: `skill_vuln_scan` / `memory_guard.scan_text` の**1回の検査呼び出し**が、
悪意ある入力（1行が極端に長い・悪意ある行を大量に並べる、の両方）によって、あらかじめ決めた
合計時間を超えて停止し続けないこと。**加えて、検査が時間切れで打ち切られた場合に、
その事実を握りつぶして「安全」を装わないこと**（R3 にはなかった要件。巡2指摘6を受けて追加）。

② **信頼境界**: 「取り込みスキルの作者が悪意ある static な文字列を書く」ケースを脅威に数える
（#566 の設計 §0② を継承）。1行の内容・1ファイル内の行数・複数ファイルの合計を攻撃者が
制御できる（R3 から継続）。

③ **対象外**:
- **正規表現の線形時間化そのもの**（R1 の設計1巡を継続して別 issue へ切り出す。
  下書き: scratchpad `issue_570_linearize.md`）
- **行長による打ち切り（採らない）**。理由: R2 の実測どおり合計時間の上限にならない
- **signal による打ち切り（採らない）**。理由: 巡2の指摘1・2・3・4・9・10（上表）
- `_PATTERNS` の検出ロジック自体の拡張・FP 較正の見直し

④ **blocking の定義**: 以下のいずれかが1件でもあれば着手不可・マージ不可
- 性能予算テスト（既定値の契約テスト・注入値の動作テストの両方）が赤
- 陽性対照（正常データでの誤打ち切り）が発生する
- 打ち切り発生時に **書込が継続してしまう経路**が1つでも残っている（巡2指摘6・3の核心）
- `scan_text`/`reject_hits`/`guard_hits`/`inspect_content` のいずれかで戻り値契約が
  不整合（新旧シグネチャの混在）
- mutation test（陰性試験、下記5型）のいずれかが緑のまま残る

⑤ **検証方法**: 「検証方法」節

⑥ **この成果物が目的文の物差しで削る量**:
目的の物差しは「悪意ある入力を含むスキルを検査したときの、1回の検査呼び出し全体の
最大停止時間」。
- **現状 — 単発攻撃**（R1 から引き継ぎ再実測。取得日 2026-09-09T08:58:58Z、HEAD `74d9f850`）:
  5000 segment（10027文字）の不一致絶対パス1行で `_scan_line()` が **16.31秒**
- **現状 — 累積攻撃**（R3 から引き継ぎ）: 閾値直下相当の行を10行並べると **53.66秒**
  （行数に比例して**無制限**に伸びる）
- **現状 — さらに極端な累積攻撃**（本 R4 で追加実測。取得日 2026-09-09T11:38:28Z）:
  16秒/行 × 20行（320秒相当）を、別プロセス隔離なしで直接実行すれば単純合算で
  約320秒かかる想定（実行はしていない。単発16秒の実測値からの外挿）
- **打ち切り後の実測**（別プロセス隔離を試作。コードは変更せず scratchpad 内に
  ワーカースクリプトを置いて実験。リポジトリへは commit しない）:
  - 320秒相当の攻撃（16秒/行 × 20行を1ファイルに集約）に対し、
    `subprocess.run(..., timeout=5)` で **実測 5.00秒でぴったり打ち切り**
    （取得日 2026-09-09T11:38:28Z）
  - **削る量**: 「合計時間の上限が無い」状態から「予算値ちょうどで頭打ちになる」状態への変化。
    予算値を実運用の値（下記「予算の値」節、暫定10秒）に設定した場合、**保証できる上限は
    10秒**（現状は無制限）

## 呼び出し経路と締切の実測（巡2指摘7への対応）

**call graph を実物で確認した結果、team-lead が引用した「hook の3〜5秒契約」は
`inspect_content`/`scan_skills` のいずれの呼び出し経路にも直接は適用されないことが判明した。**
以下に実際の呼び出し経路を表にする。

| 検査対象 | 呼び出し元 | 経路 | 外側の締切（実コード確認） | 判定 |
|---|---|---|---|---|
| `scan_skills`（`build_skill_vuln_section`経由） | `audit/sections_skill_vuln.py:33` | fleet の `evolve-fleet` CLI から `run_audit_subprocess` 経由 | **CLI 既定30秒**（`scripts/lib/fleet/__init__.py:31` `_DEFAULT_TIMEOUT_SEC = 30.0`）。**関数自体のデフォルトは10秒**（`scripts/lib/fleet/audit_runner.py:156`、CLI 経由では上書きされる） | audit には他の多数のセクションが同居するため、scan_skills 単体に使える時間は30秒よりかなり少ない。**安全側に関数デフォルトの10秒を採用** |
| `scan_skills`（対話的 `/evolve-anything:audit` 実行） | 同上、subprocess を介さない直接呼び出し | 明示的な締切なし（対話ターン内で完結） | ― | 同じ予算（10秒）を適用しても実害なし（後述の実測どおり通常は1秒未満で完了） |
| `scan_memory_dir`（`audit/sections_memory.py`経由） | `audit/sections_memory.py` | `scan_skills` と同じ fleet audit subprocess の制約下 | 同上（10秒） | 同上 |
| `inspect_content`（`auto_memory_broker.ingest_memory_results`経由） | `scripts/lib/auto_memory_broker.py:690` | **evolve --drain スキル実行中、対話ターン内で assistant がインライン実行**（`skills/evolve/references/auto-memory-drain.md:40`）。**hook からは呼ばれない** | 明示的な締切なし。**team-lead が引用した「hook の3〜5秒」は `auto_memory_runner.py`（enqueue のみ・`inspect_content` を呼ばない）の話であり、`hooks/session_summary.py:258` のコメント「Stop hook の5秒タイムアウトと切り離す」のとおり `auto_memory_runner.py` は既に非同期 subprocess.Popen で hook から完全に切り離されている** | 明示的な外部締切が無いため、ユーザー体験の許容範囲（対話ターン内で数秒程度）を基準に暫定10秒とする |

**重要な訂正（R1〜R3 の前提を修正）**: R1〜R3 で不変条件確認に使った母集団
`~/.claude`（全 PJ 横断、1356ファイル）は、**実際の運用（`build_skill_vuln_section(project_dir)`
経由）では使われない**。実運用の対象は「そのPJ自身の `skills/` ディレクトリ」のみである。
当 PJ（evolve-anything）自身の `skills/` を対象にした実測:
```python
import sys, time
sys.path.insert(0, 'scripts/lib')
import skill_vuln_scan as s
t0 = time.time()
report = s.scan_skills('.')
print(report.scanned_files, len(report.findings), len(report.flow_findings), time.time() - t0)
# => 59 0 0 0.446（秒）
```
取得日 2026-09-09T11:36:25Z。**通常運用では 0.446秒**であり、fleet audit の締切（30秒/10秒）
に対して十分な余裕がある。`~/.claude` 全体（63〜70秒）は「実データでの不変条件確認用の
特殊な参考測定」であり、blocking gate にも予算設計にも使わない（R3 の「不変の再確認」節の
方針をそのまま維持）。

## fail-closed への転換（巡2指摘3・6への対応）

**現状のコード**（`auto_memory_broker.py:688-708`）:
```python
if _HAS_MEMORY_GUARD:
    try:
        guard = _inspect_memory_content(llm_output)
    except Exception:
        guard = None                      # ← 例外時は「検査しなかった」を無視して書込続行
    if guard and guard.get("hits"):
        ...
        if guard.get("block"):
            continue                       # reject
        # warn: 書込は継続
```
`inspect_content` が例外を投げる（＝ R3 で使った signal 方式なら非対応 OS・非メインスレッド等）と
`guard=None` になり、`if guard and guard.get("hits")` が False のまま**素通りして書込が継続する**。
**攻撃者が「検査を殺せる入力」を書けば、無検査で保存される**（fail-open の核心的な欠陥）。

**本 R4 の対応**: `inspect_content` 自体が例外を投げない設計にし（別プロセスの起動失敗・
timeout・異常終了はすべて `inspect_content` 内部で捕捉して `truncated: True` として返す）、
`auto_memory_broker.py` 側は**「検査不能=block」を明示的に扱う**よう変更する
（実装対象に含める。巡2指摘2）:

```python
# auto_memory_broker.py ingest_memory_results 内（変更後の擬似コード）
if _HAS_MEMORY_GUARD:
    guard = _inspect_memory_content(llm_output)  # 例外を投げない契約に変更
    if guard.get("truncated") or guard.get("block"):
        contaminated += 1
        consumed_keys.add(key)
        reason = "予算超過（検査不能）" if guard.get("truncated") else \
            [h["pattern_id"] for h in guard.get("hits", [])]
        print(
            f"[evolve-anything:memory-guard] 検査不能/汚染検出のため書込 skip: {reason}",
            file=sys.stderr,
        )
        continue
    if guard.get("hits"):
        # warn: 書込は継続するが可視化する（既存のまま）
        ...
```
**「検査できない」を「安全」の根拠にしない**（守る対象①の追加要件）。これにより巡2指摘6
「遅い無害風の入力を先頭に置き、未走査部分に秘密の持ち出しを書けば部分結果が空のまま保存される」
という攻撃シナリオを塞ぐ。

## 戻り値の契約（巡2指摘5への対応）

**実コード確認**: `scan_text` の直接の外部呼び出し元はゼロ（`memory_guard.py` 内の
`reject_hits`/`guard_hits` の2箇所のみが使用。他ファイルからの直接呼び出しは無い）。
```
scripts/lib/memory_guard.py:209:    return [h for h in scan_text(text) if h.category in _REJECT_CATEGORIES]
scripts/lib/memory_guard.py:220:    return [h for h in scan_text(text) if h.category in _GUARD_TRACKED_CATEGORIES]
scripts/lib/memory_guard.py:257:    hits = guard_hits(text)          # inspect_content 内
scripts/lib/memory_guard.py:286:        for h in guard_hits(text):   # scan_memory_dir 内
```
取得日 2026-09-09。`reject_hits` はプロダクションコードから未使用（外部呼び出しゼロ）。

**採用: `scan_text` 自体のシグネチャを `Tuple[List[ContaminationHit], bool]` へ変更する**
（案X。影響範囲がテストコードのみに限られるため、内部専用の別関数を新設する案Yより
シンプルで「入口を1箇所にする」という R2 からの一貫した方針にも合う）。

擬似コード:
```python
def scan_text(text: str) -> Tuple[List[ContaminationHit], bool]:
    """text を別プロセスで走査し (hits, truncated) を返す。
    truncated=True のとき hits は不完全（空を含む）とみなす。"""
    if not text or not isinstance(text, str):
        return [], False
    try:
        proc = subprocess.run(
            [sys.executable, _WORKER_PATH, "scan_text"],
            input=text, capture_output=True, text=True,
            timeout=_SCAN_TEXT_BUDGET_SEC,
        )
    except subprocess.TimeoutExpired:
        return [], True
    if proc.returncode != 0:
        return [], True  # fail-closed: 異常終了も検査不能として扱う
    data = json.loads(proc.stdout)
    return [ContaminationHit(**h) for h in data["hits"]], False


def reject_hits(text: str) -> Tuple[List[ContaminationHit], bool]:
    hits, truncated = scan_text(text)
    return [h for h in hits if h.category in _REJECT_CATEGORIES], truncated


def guard_hits(text: str) -> Tuple[List[ContaminationHit], bool]:
    hits, truncated = scan_text(text)
    return [h for h in hits if h.category in _GUARD_TRACKED_CATEGORIES], truncated


def inspect_content(text: str, *, guard_mode: Optional[str] = None) -> dict:
    mode = resolve_guard_mode(guard_mode)
    hits, truncated = guard_hits(text)
    reject_relevant = [h for h in hits if h.category in _REJECT_CATEGORIES]
    # 検査不能=block（truncated のとき reject_relevant の中身に関わらず block）
    block = mode == "reject" and (bool(reject_relevant) or truncated)
    return {"hits": hits, "block": block, "mode": mode, "truncated": truncated}


def scan_memory_dir(memory_dir: Path) -> MemoryContaminationReport:
    ...
    scan_errors: List[str] = []          # 新規フィールド（SkillVulnReport と対称）
    for path in sorted(memory_dir.rglob("*")):
        ...
        hits, truncated = guard_hits(text)
        if truncated:
            scan_errors.append(f"{fname}: 予算超過（{_SCAN_TEXT_BUDGET_SEC}秒）につき検査不能")
        for h in hits:
            ...
    return MemoryContaminationReport(
        applicable=True, scanned_files=scanned, hits=hits, scan_errors=scan_errors,
    )
```

`MemoryContaminationReport` への `scan_errors: List[str] = field(default_factory=list)`
フィールド追加は、`SkillVulnReport` と対称的な既存 dataclass への拡張であり、R2/R3 と同じ
根拠（永続化ストア・観測セクションの新設ではない）で #379 新設凍結には非該当と判定する。

`scan_skills` 側も同様に、`_scan_line` を直接呼ぶのではなく、別プロセスワーカーへ
検査対象（root path）を渡し、結果を JSON で受け取って `SkillVulnReport` を再構成する
（下記「実装対象ファイル」節に新規ワーカーファイルを含める）。

## 正常時の追加コスト実測（巡2指摘4への対応）

プロセス起動 + モジュール import のオーバーヘッド:
```python
import subprocess, sys, time
t0 = time.time()
subprocess.run([sys.executable, '-c',
    'import sys; sys.path.insert(0, "scripts/lib"); import skill_vuln_scan'],
    capture_output=True, text=True, timeout=30)
print(time.time() - t0)
# => 0.0439秒
```
取得日 2026-09-09T11:37:38Z。

| 検査対象 | 単位 | 直接呼び出し実測 | 別プロセス実測 | オーバーヘッド | 判定 |
|---|---|---|---|---|---|
| `scan_skills`（当PJ自身） | 1PJ全体を1回のプロセス | 0.446秒 | 0.484秒 | 約0.04秒 | 無視できる |
| `scan_skills`（`~/.claude`全体、参考） | 同上 | 63.68〜70.78秒 | 68.548秒 | ほぼ無し（誤差範囲） | 無視できる |
| `scan_memory_dir`（当PJ、213ファイル） | memory_dir全体を1回のプロセス | 0.7646秒 | 未実測（直接呼び出し値から類推し、プロセス1回分＋0.04秒程度と見込む） | 約0.04秒（見込み） | 無視できる |
| `scan_text`（1テキストずつ） | **1回の呼び出しごとに1プロセス** | ― | 20回で1.01秒（**1回あたり平均0.0505秒**） | 1回あたり約0.05秒 | **呼び出し頻度が高いと積み上がる**（下記） |

取得日は各実測コード直後に記載（`scan_memory_dir`のみ未実測、実装フェーズで確認）。

**重要な設計判断**: `scan_memory_dir` は「ファイルごとに1プロセス」ではなく
**「memory_dir 全体の走査を1回のプロセスにまとめる」**。213ファイルに対しファイル単位で
別プロセス化すると 213 × 0.05秒 ≈ 10.65秒 のオーバーヘッドが追加され無視できない規模になる
（実測1回あたり0.0505秒 × 213 の概算）。一方 `scan_skills` も同じ理由で「1PJ全体を1回の
プロセス」にまとめる。

`inspect_content`（1件の LLM 生成物ごと）は、**呼び出し単位が既に「1テキスト」**であり
これ以上まとめられない。1回あたり約0.05秒のオーバーヘッドは、`auto_memory_runner.py:49`
の `MAX_CORRECTIONS = 5`（1回の Stop hook で enqueue される上限）から、1回の drain 実行で
処理する件数はおおむね数件〜十数件程度と見込まれ（**正確な上限は未確認・未実測**、下記
「未実測」節）、その範囲であれば数百ミリ秒以下のオーバーヘッドに収まり許容範囲と判断する。

## 予算の値（実測から決定）

| 対象 | 締切の根拠 | 採用する予算 | 判定 |
|---|---|---|---|
| `scan_skills` | fleet audit 関数デフォルト10秒（`audit_runner.py:156`）を安全側の上限として採用 | **10秒** | 当PJ実測0.446秒に対し約22倍のマージン |
| `scan_memory_dir` | 同上 | **10秒** | 当PJ実測0.7646秒に対し約13倍のマージン |
| `scan_text`（`inspect_content`経由） | 明示的な外部締切なし。ユーザー体験の許容範囲として暫定 | **10秒** | 通常のLLM生成物は数百文字〜数千文字程度で、直接呼び出しなら瞬時に完了する想定 |

**R3 で採用していた300秒は、fleet audit の締切（30秒/10秒）を明確に超えており受け入れ不可**
（巡2指摘7）。**R4 では3つの呼び出し経路すべてに共通の暫定値10秒を採用する**。根拠が
経路ごとに異なる（fleet 締切からの逆算 vs ユーザー体験の見積もり）ことは正直に書く。

## 検証方法

### 性能予算テスト（契約テストと動作テストを分離、巡2指摘8への対応）

1. **既定値の契約テスト**: 予算定数（`_SCAN_SKILLS_BUDGET_SEC`/`_SCAN_TEXT_BUDGET_SEC` 等）
   の値そのものを直接アサートする（例: `assert _SCAN_TEXT_BUDGET_SEC == 10`）。
   これは「予算定数を大きく緩める変異」（陰性試験②）を確実に検出する
2. **注入値の動作テスト**: `budget_seconds` を関数の引数として注入可能にし、極端に小さい
   予算（例: 0.05秒）と確実にそれを超える悪意入力を組み合わせて、`subprocess.TimeoutExpired`
   経路が実際に発火し `truncated=True`/`scan_errors` が記録されることをアサートする。
   これは wall clock をモックせずに決定論的なテストを実現する（R3 から維持する方針）

### 不変の再確認・golden fixture（巡2指摘11への対応）

**golden は変更後の実装から生成しない。** 手順:
1. `docs/decisions/drafts/570-vuln-scan-redos.md` が指す **base SHA（本設計を書いた時点の
   HEAD、変更前）** で、固定 fixture（新規作成、`scripts/lib/tests/fixtures/`）に対する
   `scan_skills`/`scan_memory_dir` の結果を実行して得る
2. その結果を **golden ファイル（JSON 等）としてテストコードと同じ commit で追加する**
   （実装 PR の中で「先に golden を固定してから実装を変更する」順序を守る）
3. 変更後の実装が同じ fixture に対して golden と完全一致することをテストで確認する
4. **実データ（`~/.claude`）での確認は blocking gate にしない**（R2/R3 から継続。参考測定として
   設計書に記載するに留める）

### 陰性試験（`verify-checks-by-breaking.md` + 巡2指摘8・9・10 対応）

各変異について **(a) baseline との差分 (b) 変異行が実行で読まれたことの機械的な証跡
（`coverage.py` 等） (c) 対象テストだけが期待どおり赤化** の3点を要求する。最低5型＋陽性対照:

1. **型①（別プロセス起動自体を削除し直接呼び出しへ戻す変異）** → (a) diff (b) coverage で
   直接呼び出し経路がヒットしたことを確認 (c) 性能予算テスト（注入値の動作テスト）のみ赤化
2. **型②（予算定数を極端に大きくする変異、例: 10秒→1兆秒）** → (a) diff（1行）
   (b) coverage で定数参照行が実行されたことを確認 (c) **既定値の契約テスト**が赤化すること
   （巡2指摘8: 注入値テストではこの変異を検出できないため、契約テストが必須）
3. **型③（`inspect_content` の `truncated` を握りつぶし `block=False` に固定する変異）** →
   (a) diff (b) coverage で該当分岐が実行されたことを確認 (c) 「打ち切り時に
   `guard.get('block')` が True になる」ことを直接アサートするテストのみ赤化
4. **型④（`auto_memory_broker.py` 側で `guard.get('truncated')` の判定を削除する変異）** →
   (a) diff (b) coverage で判定行削除箇所の周辺が実行されたことを確認 (c) 「検査不能時に
   `consumed_keys`/`continue` が発生せず書込が継続してしまう」ことを検出する専用テストのみ赤化
   （巡2指摘6・3 の核心を直接検証する型）
5. **型⑤（`reject_hits`/`guard_hits`/`scan_text` の戻り値タプルを片方だけ壊す変異、例:
   `truncated` を常に `False` 固定にする）** → (a) diff (b) coverage (c) 型③・④のテストが
   連鎖して赤化することを確認する（戻り値契約の一貫性テスト）

**子プロセス隔離の必要性は大きく下がる**（巡2指摘10）: 検査ロジック自体が既に別プロセスの
ワーカーで実行されるため、pytest 実行プロセス（メインプロセス）がクラッシュするリスクは
構造的に低い。念のため、ワーカースクリプトが予期しない例外で異常終了するケースを想定した
テスト（型①〜⑤とは別に、ワーカー自体のクラッシュを模擬する変異）を追加することは実装
フェーズで検討する（「未実測」節に記載）。

**陽性対照**: 意味を変えない書き換え（変数名変更・コメント追加・予算値を実装どおりの値に戻す）
で、性能予算テスト・不変再確認テスト・機能テストのすべてが緑のままであることを確認する。
加えて、「呼び出し経路と締切の実測」節・「正常時の追加コスト実測」節の実測（当PJ実測
0.446秒/0.484秒、誤検出なし）を陽性対照の実測として明記済み。

## 実装対象ファイル

この集合を越えない:
- 新規: `scripts/lib/skill_vuln_worker.py`（別プロセスのエントリポイント。
  `scan_skills`/`scan_text`/`scan_memory_dir` の3モードを argv/stdin で切り替え、
  結果を JSON で stdout へ出力する）
- `scripts/lib/skill_vuln_scan.py`（`scan_skills` を別プロセス経由に変更する薄いラッパー追加、
  予算定数の定義）
- `scripts/lib/memory_guard.py`（`scan_text`/`reject_hits`/`guard_hits`/`inspect_content`/
  `scan_memory_dir` の戻り値変更・`MemoryContaminationReport.scan_errors` 追加）
- `scripts/lib/auto_memory_broker.py`（`ingest_memory_results` 内の fail-open → fail-closed
  転換。巡2指摘2・3・6の核心）
- `scripts/lib/tests/test_skill_vuln_shell.py` / `test_skill_vuln_scan.py` /
  `test_memory_guard.py` / `test_auto_memory_broker.py`（性能予算・不変再確認・mutation test
  追加。`test_auto_memory_broker.py` は fail-closed 転換の回帰テストのため新規に追加対象へ
  含める）
- 新規: `scripts/lib/tests/fixtures/`（固定 golden fixture、base SHA 時点で生成し commit）
- 新規テストファイルを追加する場合: `scripts/lib/tests/test_skill_vuln_perf.py`

**対象外（変更しない）**:
- `scripts/lib/skill_vuln_flow.py`（`SHELL_EXEC_SUBJECT` 非依存と確認済み）
- `scripts/lib/audit/sections_skill_vuln.py` / `sections_memory.py`（呼び出し側。
  `SkillVulnReport`/`MemoryContaminationReport` の既存フィールド経由で打ち切りが
  自動的に反映されるため、変更不要と見込む。実装フェーズで再確認する）
- `_PATTERNS` の内容・FP 較正ロジック

## 未実測

- `scan_memory_dir` を「memory_dir全体を1回のプロセスにまとめた」場合の実測オーバーヘッド
  （直接呼び出し0.7646秒からの類推のみ。実装フェーズで確認）
- `inspect_content`（drain Phase C）の1回あたりの実際の呼び出し件数（`MAX_CORRECTIONS=5`
  という enqueue 側の上限は確認したが、drain 実行1回で処理される件数の実測はしていない）
- ワーカープロセス自体が予期しない例外でクラッシュした場合の挙動（`returncode != 0` を
  `truncated=True` 相当として扱う設計にしたが、実際のクラッシュパターンごとの網羅的な
  テストは実装フェーズで検討する）
- `scan_skills`/`scan_memory_dir` が対話的に（fleet subprocess を介さず）呼ばれる場合の
  実際のユーザー体験（10秒の打ち切りが対話的操作として妥当かは未検証）
- 別プロセス化によって `_iter_target_files` 等のファイルシステムアクセスがワーカー側で
  再度行われることになるが、これによる I/O 面での追加コストは「正常時の追加コスト実測」の
  実測値に含まれている（`scan_skills`/`scan_memory_dir` の別プロセス実測は実際にファイル
  システムを読んでいる）ため未実測ではないが、**ネットワークファイルシステム等、I/O 特性が
  大きく異なる環境での挙動**は未実測

## 判定に使う識別・裁定者（`no-denylist-checks.md` 手続き）

打ち切り判定は経過時間（`subprocess.run` の `timeout` パラメータ）という単一の観測可能な量で
行うため blocking として扱う。本設計書自体の裁定は `review.md` の系統独立レビューに委ねる。
R1 からの累計巡数（codex 設計レビュー巡1・巡2、[Must] 通算16件）を継承しており、本 R4 は
ユーザー裁定により**巡3として承認済み**（issue #570 本文に承認行）。
