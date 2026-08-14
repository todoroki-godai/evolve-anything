"""evolve_decisions — evolve 提案 accept/reject の決定論キャプチャ（#360-A, ADR-041）。

fitness calibration（check_calibration_regression）の母集団 optimize_history が空だった
根本原因は、accept/reject の記録が SKILL.md の MUST（assistant が手で python を叩く）止まりで
決定論コードから呼ばれなかったこと（install ≠ enforcement の SKILL.md 版）。

本モジュールは evolve SKILL.md 1 実行内で完結する emit→（インライン適用）→drain の2相で、
accept をディスク差分から、reject を明示シグナルから取る（ADR-041, C: ハイブリッド）:

  - emit_decisions  : run_evolve 末尾。候補スキルの before_sha をキューにスナップショット。
  - ingest_decisions: Step 7.8 drain。after_sha != before_sha なら accept、明示却下なら reject、
                      未変更かつ未却下（skip）は記録しない。

書き込みは既存 record_evolve_diff_decision を再利用（fitness_func=skill_quality で採点 →
optimize_history へ冪等記録）。母集団は「混合でなく増量」を保つ。

決定論・LLM 非依存。

## パッケージ構成（#383: 836 行の単一ファイルが file-size-budget HARD 上限 800 行を超過したため分割）

`audit.py`（2046→178 行・PR #51-#61）/ `evolve.py`（1739→156 行・ADR-048・#531）の勝ちパターンを
踏襲し、`evolve_decisions.py`（単一ファイル）を `evolve_decisions/`（パッケージ）へ変換した。
振る舞いはゼロ変更（純粋なリファクタ）。

| module | 内容 |
|--------|------|
| `__init__.py`（本ファイル） | 定数（`DATA_DIR` / `QUEUE_ROOT` / `MARKER_ROOT` / `PENDING_TTL_DAYS`）+ 全 sub-module の re-export |
| `_queue.py` | pending キュー（emit/ingest 共有）: `resolve_slug` / `queue_path_for` / `read_queue` / `_write_queue` / `_queue_lock` |
| `_marker.py` | 「未 drain 提案」マーカー（#402）: `marker_path` / `write_pending_marker` / `read_pending_marker` / purge 系 / `undrained_applied` |
| `_candidates.py` | 提案候補抽出: `_extract_candidates`（discover/skill_evolve）/ `_advisory_pending`（advisory detector, #284）/ `_record_advisory_event`（advisory の surfaced/accept/reject/deferred 記録, #267 Sprint 1）/ `_load_recorder` |
| `_suppression.py` | reject 抑制（#446）: `filter_rejected`（emit 側）/ `record_pending_rejection`（ingest 側）。`remediation.suppression_ledger` を薄い adapter 経由で流用 |
| `_emit.py` | Phase A: `emit_decisions` |
| `_ingest.py` | Phase C: `ingest_decisions` |
| `_drain.py` | `evolve --drain` の実体: `drain_pending` / `_partition_orphaned`（#402, #376 AC5） |

### 束縛フェンス（#531 §3 と同型の規約）

`QUEUE_ROOT` / `MARKER_ROOT` は複数のテストが `monkeypatch.setattr(evolve_decisions, "QUEUE_ROOT", ...)`
/ `monkeypatch.setattr(evolve_decisions, "MARKER_ROOT", ...)`（conftest.py の autouse fixture 含む）で
直接差し替える。この差し替えを sub-module の関数からも確実に効かせるため、両定数は**このファイル
（パッケージ namespace）だけが正典**とし、`_queue.py` / `_marker.py` の関数は呼び出し時に
`import evolve_decisions as _ed; _ed.QUEUE_ROOT`（同 `MARKER_ROOT`）で遅延参照する。
module-top で `from evolve_decisions import QUEUE_ROOT` のように直接束縛すると import 時点の値で
凍結され、テストの差し替えがすり抜ける（pitfall_module_level_datadir_import_copy と同型）。

同じ理由で `resolve_slug`（`_queue.py`）/ `_collect_advisory_proposals`（`_candidates.py`）/
`_load_recorder`（`_candidates.py`）も個別に monkeypatch 対象になっているため、これらを**別
sub-module から呼ぶ**箇所（`_emit.py` の `emit_decisions` / `_drain.py` の `drain_pending` /
`_candidates.py` 内の `_advisory_pending` / `_ingest.py` の `ingest_decisions`）は同じパッケージ
namespace 経由の遅延参照を使う。monkeypatch されない末端 helper は sub-module から直接 import
してよい（`evolve/phases_diagnose.py` の流儀と同じ）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import optimize_history_store as _store  # noqa: E402

# identity 関数は別 module（#287）。名前を re-export し既存参照をそのまま動かす。
from evolve_decision_ids import (  # noqa: E402,F401
    decision_event_id,
    entry_generation,
    is_superseded,
    legacy_run_id,
    new_run_id,
    proposal_id,
    proposal_id_from_identity,
    repo_identity,
    sha256,
    supersede_keys,
    tracked_path,
    is_orphaned_worktree,
)

# #402 PR-1: revert 用「記録拡張」の識別/圧縮 helper（決定1/2/4/5/8）。名前を re-export し
# 他 sub-module・テストから `evolve_decisions.X` で参照できるようにする。
from evolve_decision_ids import (  # noqa: E402,F401
    REVERT_BEFORE_MAX_COMPRESSED_BYTES,
    REVERT_ENCODING,
    REVERT_FIELD_KEYS,
    REVERT_REASON_BEFORE_TOO_LARGE,
    REVERT_SCHEMA_VERSION,
    compress_before_content,
    compress_before_for_revert,
    decompress_before_content,
    filter_monotonic_pending,
    generation_of,
    global_skills_root,
    lexical_absolute,
    path_scope_identity,
    revert_generation_for_target,
)

DATA_DIR = _store.DATA_DIR
QUEUE_ROOT = DATA_DIR / "evolve_decisions"

# 「未 drain 提案」マーカーの root（#402）。QUEUE_ROOT は DATA_DIR(=CLAUDE_PLUGIN_DATA 派生)配下で
# hook(env 有)/tool(env 無)で割れる（pitfall_datadir_hook_tool_split, #358）。SessionStart hook
# (env 有) と emit/drain(tool 文脈, env 無) が**同一パスに合意する必要がある**ため、ここは env を
# 見ず home 基準で固定する。マーカーは評価状態(optimize_history/queue)ではなく「apply→drain 待ちの
# 提案ポインタ」という運用状態で、fitness 母集団には入らず drain で消える。
MARKER_ROOT = Path.home() / ".claude" / "evolve-anything" / "evolve_pending"

# 未 drain 提案の保持上限（日）。他ストア（weak_signals / triage_ledger）と同じ 45 日。
# 判定は read 時の age 導出で行う（forward write に依存しない・#279）。
PENDING_TTL_DAYS = 45

# MVP 対象は discover の matched_skills（#223/Step 3 と同じスキル diff クラス）。
# skill_evolve / remediation への拡張は均質性を崩さないため follow-up（ADR-041）。
FITNESS_FUNC = "skill_quality"

# ─── queue（emit/ingest 共有の pending キュー, #383 で _queue.py へ抽出）───────
from ._queue import (  # noqa: E402
    _queue_lock,
    _write_queue,
    queue_path_for,
    read_queue,
    resolve_slug,
)

# ─── pending marker（#402, #383 で _marker.py へ抽出）─────────────────────
from ._marker import (  # noqa: E402
    _flat_result_path,
    _gc_marker_file,
    _marker_lock,
    _purge_marker_entries_locked,
    _read_pending_marker_file,
    _run_is_expired,
    _write_marker_file,
    clear_pending_marker,
    marker_path,
    purge_marker_entries,
    read_pending_marker,
    undrained_applied,
    write_pending_marker,
)

# ─── 提案候補抽出 helper（#383 で _candidates.py へ抽出）───────────────────
from ._candidates import (  # noqa: E402
    _SKILL_EVOLVE_PROPOSED,
    _advisory_pending,
    _collect_advisory_proposals,
    _extract_candidates,
    _load_recorder,
    _record_advisory_event,
)

# ─── reject 抑制（#446, _suppression.py）───────────────────────────────────
from ._suppression import filter_rejected, record_pending_rejection  # noqa: E402

# ─── Phase A: emit（#383 で _emit.py へ抽出）───────────────────────────────
from ._emit import emit_decisions  # noqa: E402

# ─── Phase C: ingest（#383 で _ingest.py へ抽出）───────────────────────────
from ._ingest import ingest_decisions  # noqa: E402

# ─── drain（`evolve --drain` の実体, #402。#383 で _drain.py へ抽出）───────
from ._drain import _partition_orphaned, drain_pending  # noqa: E402
