"""Layer 1: dogfood E2E（#496）。

(1a) dry-run 不変: DATA_DIR を一時ディレクトリへコピーしてから dry-run evolve を実行する
     「隔離コピー方式」（#496 改善）。

     【旧方式の問題点】
     実 DATA_DIR を直接 snapshot diff すると、ゲート実行中に hook（trigger_engine の
     _save_state 等）が evolve-state.json を書く ambient write と「dry-run の書込バグ」を
     区別できず flaky になる（実際に偽赤を1回観測）。

     【隔離コピー方式】
     (a) DATA_DIR を一時ディレクトリへ shutil.copytree でコピー
     (b) CLAUDE_PLUGIN_DATA=<コピー先> を subprocess の env に設定（DATA_DIR は
         CLAUDE_PLUGIN_DATA 優先で解決される — scripts/lib/common.py 同型）
     (c) SHA256 snapshot 比較はコピー側に対して行う
     効果:
       (a) ambient write 混入ゼロ（実 DATA_DIR への書込はコピー側に影響しない）
       (b) dry-run バグがあっても実環境を汚さない
       (c) 検出力は旧方式と同等（コピー側への書込でゲートが赤になる）

     注: DATA_DIR に DuckDB ファイル（utterances.db 等）が含まれる場合も
     shutil.copytree で問題なくコピーできる。コピー中の concurrent write により部分コピーに
     なっても Layer 1 は「コピー前後の自己比較」なので検出力に影響しない。
     実 DATA_DIR からの concurrent write が起きれば isolated コピーには反映されず、
     比較は常に「隔離されたコピーの before/after」のみで行われる。

     【文書化された cache 除外】
     CACHE_EXCLUDE_NAMES に列挙されたファイルは比較対象外とする。
     これらは evolve-ops の cache warm 設計で意図された dry-run 書込であり、
     bypass フラグでなくモジュール定数として原則ベースで恒久除外する。

(1b) store 差分（書かれるべきものが書かれる方向）: 非 dry-run は実環境を汚すため Wave 0 では
     未実装。NotImplemented 枠だけ予約し #484 修正後に実装する。

dry-run の result JSON は Layer 2（invariants）に渡せるよう返す。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import snapshot
from . import ingest_check


# ─────────────────────────────────────────────────────────────
# 文書化された cache 除外リスト
# ─────────────────────────────────────────────────────────────
# これらのファイルは evolve-ops の cache warm 設計で「意図された」dry-run 書込。
# LLM 再呼び出し回避キャッシュとして正規に書かれるため、dry-run 不変チェックの
# snapshot diff から恒久除外する（bypass フラグでなく原則ベースの除外）。
# 新たに意図された dry-run 書込が設計された場合はここに追記し、理由をコメントで残す。
CACHE_EXCLUDE_NAMES: frozenset = frozenset({
    # evolve の skill-evolve フェーズが LLM 評価結果をキャッシュするファイル。
    # dry-run でも cache warm が行われるのは evolve-ops の設計（再呼び出しコスト削減）。
    "skill-evolve-cache.json",
    # constitutional fitness の LLM Judge 評価結果をキャッシュするファイル。
    # dry-run でも cache warm が行われるのは constitutional フェーズの設計。
    "constitutional_cache.json",
})


# ─────────────────────────────────────────────────────────────
# 文書化された dry-run 書込除外（ディレクトリ prefix 単位）
# ─────────────────────────────────────────────────────────────
# CACHE_EXCLUDE_NAMES はファイル名（basename）単位だが、ディレクトリ配下を丸ごと
# 除外したいケースもある。相対パス（posix）が以下の prefix で始まるファイルを除外する。
#
#   evolve_pending/
#     evolve_decisions の pending marker（emit→drain 捕捉の運用ポインタ）。
#     #402/ADR-041 の意図された dry-run 書込（write_pending_marker の docstring に
#     「emit が dry-run でも書く」と明記）。PR #505 が誤ってこれをゲートした回帰を
#     #513 で revert するため、revert 後は dry-run evolve が marker を書くのが正常動作。
#     bypass フラグでなく原則ベースの恒久除外として扱う。
CACHE_EXCLUDE_PATH_PREFIXES: frozenset = frozenset({
    "evolve_pending/",
})


# ─────────────────────────────────────────────────────────────
# 文書化された cache 除外（JSON キー単位）
# ─────────────────────────────────────────────────────────────
# 上記 CACHE_EXCLUDE_NAMES はファイル単位の除外だが、意図された LLM 再呼び出し回避
# キャッシュが「実 state も持つ共有 JSON ファイル」に同居している場合がある。
# その場合ファイル丸ごと除外すると実 state 書込バグを隠してしまうため、JSON の
# トップレベルキー単位で除外する（cache キーだけを比較から外し、他キーの変更は検出）。
#
#   evolve-state.json::skill_type_cache
#     prune の参照型スキル判定（is_reference_skill）が LLM 推定結果をキャッシュする
#     キー。skill-evolve-cache.json / constitutional_cache.json と同カテゴリの意図された
#     dry-run 書込（再推定コスト削減）だが、evolve-state.json という実 state
#     （last_run_timestamp 等）も持つファイルに同居するため、ファイルでなくこの1キーだけを
#     比較から除外する。実 state 部分の dry-run 書込バグは引き続き検出する。
#
# 新たに同型の「共有ファイル内 cache キー」が設計されたらここに追記し理由を残す。
CACHE_EXCLUDE_JSON_KEYS: Dict[str, frozenset] = {
    "evolve-state.json": frozenset({"skill_type_cache"}),
}


def _default_data_dir() -> Path:
    """正準 DATA_DIR（env 非依存固定: ~/.claude/evolve-anything）。"""
    env = os.environ.get("CLAUDE_PLUGIN_DATA", "")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "evolve-anything"


def _sys_path_dirs(repo_root: Path) -> List[Path]:
    """素の起動経路で evolve.py が必要とする sys.path（conftest 下駄なし）。"""
    return [
        repo_root / "scripts" / "lib",
        repo_root / "scripts",
        repo_root / "skills" / "evolve" / "scripts",
        repo_root / "skills" / "audit" / "scripts",
    ]


def copy_data_dir_to_tmp(src: Path, dest: Path) -> Path:
    """src を dest にコピーして dest を返す。

    src が存在しない場合は空の dest を作成して返す。
    shutil.copytree はシンボリックリンクを含むツリーを再帰コピーする。
    DuckDB ファイル等のバイナリファイルも問題なくコピーできる。
    コピー中の concurrent write により部分コピーになっても Layer 1 は
    「コピー前後の自己比較」なので検出力に影響しない。
    """
    src = Path(src)
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    if src.exists() and src.is_dir():
        shutil.copytree(src, dest, symlinks=True)
    else:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def _run_evolve_dry_run(repo_root: Path, output_path: Path, env: Optional[dict] = None) -> Dict[str, Any]:
    """``evolve.py --dry-run --output <path>`` を素の python subprocess で起動する。

    PYTHONPATH のみで sys.path を構成し、conftest の補完を一切受けない（ユーザーと同じ起動経路）。
    LLM は dry-run 経路では呼ばれない（評価系は --dry-run で skip / cache 参照）。
    env には CLAUDE_PLUGIN_DATA=<isolated_dir> が含まれる（check_dry_run_invariance が設定）。
    """
    run_env = dict(os.environ if env is None else env)
    run_env["PYTHONPATH"] = os.pathsep.join(str(p) for p in _sys_path_dirs(repo_root))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # #531: evolve はパッケージ化（evolve/）したため `-m evolve` で起動する。
    # PYTHONPATH に skills/evolve/scripts を含むので __main__.py が解決される。
    proc = subprocess.run(
        [sys.executable, "-m", "evolve", "--dry-run", "--project-dir", str(repo_root), "--output", str(output_path)],
        capture_output=True,
        text=True,
        env=run_env,
        cwd=str(repo_root),
        timeout=600,
    )
    return {"returncode": proc.returncode, "stderr": proc.stderr, "stdout": proc.stdout}


def check_dry_run_invariance(
    repo_root: Path,
    data_dir: Optional[Path] = None,
    out_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """dry-run evolve が DATA_DIR を一切書き換えないことを検査（Layer 1a）。

    【隔離コピー方式】
    実 DATA_DIR を一時ディレクトリへコピーし、CLAUDE_PLUGIN_DATA=<コピー先> で evolve を
    起動することで ambient write（hook 等による実 DATA_DIR への書込）を隔離する。
    snapshot 比較はコピー側のみで行い、実 DATA_DIR は一切比較対象にしない。
    CACHE_EXCLUDE_NAMES（ファイル名）/ CACHE_EXCLUDE_PATH_PREFIXES（ディレクトリ prefix）/
    CACHE_EXCLUDE_JSON_KEYS（共有 JSON 内 cache キー）に列挙された意図された dry-run 書込は
    diff から除外する。

    返り値: ``{"status": "pass"|"fail"|"error", "diff": {...}, "detail": str,
               "result_path": str|None}``
    """
    repo_root = Path(repo_root)
    data_dir = Path(data_dir) if data_dir is not None else _default_data_dir()
    out_dir = Path(out_dir) if out_dir is not None else (Path("/tmp") / "evolve-dogfood-gate")
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "evolve-dryrun-result.json"

    # 実 DATA_DIR を一時ディレクトリへコピー（ambient write 隔離）
    isolated_dir = copy_data_dir_to_tmp(data_dir, out_dir / "isolated-data-dir")

    # 隔離コピー前のスナップショット（コピー直後なので ambient write の影響ゼロ）
    before = snapshot.snapshot_dir(
        isolated_dir,
        exclude_names=CACHE_EXCLUDE_NAMES,
        exclude_json_keys=CACHE_EXCLUDE_JSON_KEYS,
        exclude_path_prefixes=CACHE_EXCLUDE_PATH_PREFIXES,
    )

    # CLAUDE_PLUGIN_DATA=<コピー先> で evolve を起動
    run_env = dict(os.environ)
    run_env["CLAUDE_PLUGIN_DATA"] = str(isolated_dir)
    run = _run_evolve_dry_run(repo_root, result_path, env=run_env)

    # 隔離コピー後のスナップショット（コピー側のみ比較）
    after = snapshot.snapshot_dir(
        isolated_dir,
        exclude_names=CACHE_EXCLUDE_NAMES,
        exclude_json_keys=CACHE_EXCLUDE_JSON_KEYS,
        exclude_path_prefixes=CACHE_EXCLUDE_PATH_PREFIXES,
    )
    diff = snapshot.diff_snapshots(before, after)

    if run.get("returncode", 1) != 0:
        tail = (run.get("stderr") or "").strip().splitlines()
        return {
            "status": "error",
            "diff": diff,
            "detail": f"evolve --dry-run exit {run.get('returncode')}: {tail[-1] if tail else ''}",
            "result_path": str(result_path) if result_path.exists() else None,
        }

    if snapshot.is_unchanged(diff):
        return {"status": "pass", "diff": diff, "detail": "DATA_DIR 不変", "result_path": str(result_path)}

    changed = []
    for kind in ("added", "removed", "modified"):
        changed += [f"{kind}:{p}" for p in diff[kind]]
    return {
        "status": "fail",
        "diff": diff,
        "detail": f"dry-run が DATA_DIR を書き換えた: {changed}",
        "result_path": str(result_path) if result_path.exists() else None,
    }


def _run_drain(repo_root: Path, result_json: Path, env: Optional[dict] = None) -> Dict[str, Any]:
    """``evolve.py --drain --result-json <path>`` を素の python subprocess で起動する（Layer 1b）。

    apply 境界の drain は **非 dry-run・tool 文脈**で走り、weak_signals / optimize_history を
    正準 DATA_DIR（CLAUDE_PLUGIN_DATA 由来）に書く（#484/#513）。``--result-json`` を渡すことで
    drain_pending は home 固定の MARKER_ROOT を読まず result JSON の ``evolve_decisions.pending``
    から pending を取る（#402）。これにより隔離が完全になり、home の実マーカーに依存しない。
    LLM は drain 経路では呼ばれない（決定論 weak_signals + 既存 pending の ingest のみ）。
    """
    run_env = dict(os.environ if env is None else env)
    run_env["PYTHONPATH"] = os.pathsep.join(str(p) for p in _sys_path_dirs(repo_root))
    # #531: パッケージ化に伴い `-m evolve` で起動（PYTHONPATH に skills/evolve/scripts を含む）。
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "evolve",
            "--drain",
            "--project-dir",
            str(repo_root),
            "--result-json",
            str(result_json),
        ],
        capture_output=True,
        text=True,
        env=run_env,
        cwd=str(repo_root),
        timeout=600,
    )
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def check_store_diff_1b(
    repo_root: Path,
    data_dir: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    result_json: Optional[Path] = None,
) -> Dict[str, Any]:
    """Layer 1b: 「書かれるべきものが書かれる」方向の store 差分検査（#518）。

    Layer 1a が「dry-run は何も書かない」方向を見るのに対し、本検査は apply 境界
    （``evolve --drain``）で **書かれるべき store が実際に書かれる**ことを検査する。
    #484（決定論3チャネルが標準フローで一度も永続化されない繋ぎ目の死）が #513 で
    根治されたことを実環境に近い形で封じる回帰ゲート。

    【隔離コピー方式（#515 流用）】
    (a) DATA_DIR を一時ディレクトリへコピー（実環境を汚さない）
    (b) CLAUDE_PLUGIN_DATA=<コピー先> で ``--drain --result-json <result>`` を起動。
        ``--result-json`` 指定により MARKER_ROOT=home 固定マーカーを読まないため隔離が完全になる。
    (c) コピー側の store 差分 + drain サマリで assert:
        - サマリに ``weak_signals_persisted`` があり ``dry_run`` が False（配線の生存 + 非 dry-run）
        - weak_signals.jsonl 等の決定論チャネル書込が isolated copy に現れる（書込方向）

    result_json 未指定なら dry-run を 1 回回して pending 付き result を生成する。

    返り値: ``{"status": "pass"|"fail"|"error", "detail": str,
               "store_changes": {...}, "weak_signals_persisted": {...}|None}``
    """
    repo_root = Path(repo_root)
    data_dir = Path(data_dir) if data_dir is not None else _default_data_dir()
    out_dir = Path(out_dir) if out_dir is not None else (Path("/tmp") / "evolve-dogfood-gate")
    out_dir.mkdir(parents=True, exist_ok=True)

    # result JSON（drain が pending を読むソース）を用意する。
    if result_json is None:
        result_path = out_dir / "evolve-dryrun-result.json"
        if not result_path.exists():
            run = _run_evolve_dry_run(repo_root, result_path)
            if run.get("returncode", 1) != 0 or not result_path.exists():
                tail = (run.get("stderr") or "").strip().splitlines()
                return {
                    "status": "error",
                    "detail": f"Layer 1b: drain 用 result 生成に失敗: {tail[-1] if tail else ''}",
                    "store_changes": {},
                    "weak_signals_persisted": None,
                }
        result_json = result_path
    result_json = Path(result_json)

    # 実 DATA_DIR を一時ディレクトリへコピー（apply 境界 drain を隔離で走らせる）
    isolated_dir = copy_data_dir_to_tmp(data_dir, out_dir / "isolated-data-dir-1b")

    before = snapshot.snapshot_dir(isolated_dir)
    run_env = dict(os.environ)
    run_env["CLAUDE_PLUGIN_DATA"] = str(isolated_dir)
    run = _run_drain(repo_root, result_json, env=run_env)
    after = snapshot.snapshot_dir(isolated_dir)
    store_changes = snapshot.diff_snapshots(before, after)

    if run.get("returncode", 1) != 0:
        tail = (run.get("stderr") or "").strip().splitlines()
        return {
            "status": "error",
            "detail": f"evolve --drain exit {run.get('returncode')}: {tail[-1] if tail else ''}",
            "store_changes": store_changes,
            "weak_signals_persisted": None,
        }

    # drain サマリ（stdout 末尾の JSON 1 行）を読む。
    summary = _parse_drain_summary(run.get("stdout") or "")
    if summary is None:
        return {
            "status": "error",
            "detail": "evolve --drain の stdout から JSON サマリを取得できず",
            "store_changes": store_changes,
            "weak_signals_persisted": None,
        }

    persisted = summary.get("weak_signals_persisted")
    if persisted is None:
        return {
            "status": "fail",
            "detail": "drain サマリに weak_signals_persisted が無い（apply 境界の永続化配線が消失）",
            "store_changes": store_changes,
            "weak_signals_persisted": None,
        }
    if isinstance(persisted, dict) and persisted.get("error"):
        return {
            "status": "fail",
            "detail": f"weak_signals 永続化が例外: {persisted.get('error')}",
            "store_changes": store_changes,
            "weak_signals_persisted": persisted,
        }
    if persisted.get("dry_run") is not False:
        return {
            "status": "fail",
            "detail": f"apply 境界 drain が dry_run={persisted.get('dry_run')}（非 dry-run でなければ #484 が再発する）",
            "store_changes": store_changes,
            "weak_signals_persisted": persisted,
        }

    return {
        "status": "pass",
        "detail": "apply 境界 drain が非 dry-run で weak_signals を永続化（store 書込方向 OK）",
        "store_changes": store_changes,
        "weak_signals_persisted": persisted,
    }


def _parse_drain_summary(stdout: str) -> Optional[Dict[str, Any]]:
    """drain の stdout から JSON サマリ（最後の有効な JSON 1 行）を取り出す。"""
    import json

    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def run_layer1(repo_root: Path, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Layer 1 全チェックを回す。

    返り値: ``{"checks": [...], "result_path": str|None}``。result_path は Layer 2 が
    読む dry-run result JSON のパス（成功時のみ）。
    """
    repo_root = Path(repo_root)
    out_dir = Path(out_dir) if out_dir is not None else (Path("/tmp") / "evolve-dogfood-gate")
    checks: List[Dict[str, Any]] = []

    inv = check_dry_run_invariance(repo_root, out_dir=out_dir)
    checks.append({"name": "1a_dry_run_invariance", **inv})

    ingest_res = ingest_check.check_real_pj_ingest(db_dir=out_dir / "ingest")
    checks.append({"name": "1_ingest_e2e", **ingest_res})

    b1 = check_store_diff_1b(repo_root, out_dir=out_dir, result_json=inv.get("result_path"))
    checks.append({"name": "1b_store_diff", **b1})

    return {"checks": checks, "result_path": inv.get("result_path")}
