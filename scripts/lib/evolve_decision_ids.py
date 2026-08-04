"""evolve decision の identity 関数群（`evolve_decisions` から分離・#287）。

**提案の identity**（「同じ提案か」）と**判断イベントの identity**（「同じ判断か」）を
別関数として並べて置くための module。この2つを1つの ID に兼ねさせたことが
#279 → #286 → #290 で3回同じ場所を踏んだ根因なので、定義を隣り合わせにして
「どちらの identity の話をしているか」を読み違えにくくする。

module 定数を持たない純関数だけを置く（`evolve_decisions` 側の `QUEUE_ROOT` /
`MARKER_ROOT` を monkeypatch するテスト経路に影響しない）。
"""
from __future__ import annotations

import hashlib
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_run_id() -> str:
    return "evrun_" + uuid.uuid4().hex


def _legacy_run_id(pending: List[Dict[str, Any]]) -> str:
    """旧 marker を安定した synthetic run として扱う。"""
    identity = "\n".join(sorted(str(entry.get("id", "")) for entry in pending))
    return "legacy_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]


def _repo_identity(path: str) -> Dict[str, Optional[str]]:
    """path の所属 repo 情報を返す（#376 AC4）。

    ``repo_id``（git-common-dir の親 — worktree 間で共有される本体 repo の識別子）と
    ``relative_path``（その worktree の toplevel から見た相対パス）を分離することで、
    同じスキルを別 worktree の絶対パスで emit しても同一の論理 identity に畳める。
    ``worktree_root``（``git rev-parse --show-toplevel``）は orphan 判定（#376 AC5）用に
    別途返す — worktree ごとに異なる値になるのが意図（repo_id とは非対称）。

    git 管理外 / git 不可 / 親ディレクトリ不在（未存在の対象パスを先読みするケース）は
    全て None にフォールバックし、``relative_path`` には元の path をそのまま入れる
    （＝旧来の絶対パスベース identity と同じ挙動に自然縮退する）。
    """
    p = Path(path)
    directory = p.parent
    if not directory.is_dir():
        return {"repo_id": None, "relative_path": str(p), "worktree_root": None}
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(directory), check=True, capture_output=True, text=True,
        ).stdout.strip()
        common_dir = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(directory), check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return {"repo_id": None, "relative_path": str(p), "worktree_root": None}

    if not toplevel or not common_dir:
        return {"repo_id": None, "relative_path": str(p), "worktree_root": None}

    top = Path(toplevel)
    common = Path(common_dir)
    if not common.is_absolute():
        common = (directory / common).resolve()
    repo_id = str(common.parent)  # 本体 repo root（worktree 間で共有）

    try:
        relative_path = str(p.resolve().relative_to(top.resolve()))
    except ValueError:
        relative_path = str(p)

    return {
        "repo_id": repo_id,
        "relative_path": relative_path,
        "worktree_root": str(top.resolve()),
    }


def _proposal_id_from_identity(
    identity: Dict[str, Optional[str]], before_sha: str
) -> str:
    """``_repo_identity`` の結果 + before_sha から提案 ID を作る純関数版。

    subprocess を伴う ``_repo_identity`` を呼び直さずに済むよう、既に identity を
    持っている呼び出し元（emit_decisions）はこちらを直接使う。
    """
    key = f"{identity.get('repo_id') or ''}\n{identity.get('relative_path')}\n{before_sha}"
    return "evdiff_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _proposal_id(skill_path: str, before_sha: str) -> str:
    """**提案**の content identity = (repo_id, repo 相対パス, 適用前の内容)（#376 AC4）。

    「同じ提案か」だけを表す。「同じ判断イベントか」は別キー（``_decision_event_id``）で
    表す — 1つの ID に両方を兼ねさせると必ずどちらかが壊れる（#279→#286→#290 で
    3回踏んだ）:

    - **run_id を混ぜてはいけない**（#279）: ID が run ごとに変わると判断イベントも
      run 跨ぎで別物になり、1回の apply が optimize_history に N 重記録される。
    - **パス単独にしてもいけない**（#286）: 判断イベントキーが恒久キーになり、同じ
      スキルの2回目以降の accept が冪等 dedup で捨てられる（生涯1件しか母集団に入らない）。
    - **before_sha を混ぜても、これ単独では足りない**（#290）: 対象の内容が過去の状態へ
      循環すると過去の ID が再利用されるため、判断イベントキーが再び衝突する。

    パス成分は絶対パスでなく ``_repo_identity`` の repo 相対パス + repo_id を使う
    （#376）— worktree ごとに絶対パスが異なる同一スキルが別提案として重複登録される
    バグ（同一 slug の queue/marker に worktree 数だけ residue し、1回の apply が
    worktree 数だけ accept 計上される）の根治。git 管理外は絶対パスへ自然縮退する。
    """
    return _proposal_id_from_identity(_repo_identity(skill_path), before_sha)


def _entry_worktree_root(entry: Dict[str, Any]) -> Optional[str]:
    """entry の所属 worktree ルート（emit 時に記録した ``worktree_root`` を優先）。

    旧 entry（この項目導入前に emit された marker residue）は値を持たないため、
    ``_tracked_path`` から都度導出する。ただし対象の worktree が既に消えていると
    git コマンド自体が引けない（ディレクトリが無い）ため、その場合は None を返す
    （＝判定不能・orphan とは断定しない。保守的に残す）。
    """
    root = entry.get("worktree_root")
    if root:
        return root
    tracked = entry.get("target_path") or entry.get("skill_path")
    if not tracked:
        return None
    return _repo_identity(tracked).get("worktree_root")


def is_orphaned_worktree(entry: Dict[str, Any]) -> bool:
    """entry の所属 worktree が既にディスク上から消えているか（#376 AC5）。

    ``git worktree remove`` は登録解除と物理削除を通常同時に行うため、worktree
    ルートディレクトリの存在有無で十分に判定できる（追加の ``git worktree list``
    呼び出しは、削除済み worktree だと元 repo 側からの subprocess 依存を増やすだけで
    判定精度は変わらない）。worktree_root が不明（判定不能）なときは保守的に
    orphan でない扱いにする（誤って生きている pending を消さない）。
    """
    root = _entry_worktree_root(entry)
    if not root:
        return False
    return not Path(root).is_dir()


def _decision_event_id(proposal_id: str, kind: str, after_content: str) -> str:
    """**判断イベント**の identity = (提案, 判断種別, 判断時点の内容)（#290）。

    ``record_evolve_diff_decision`` の冪等 dedup キー。提案 ID と分離することで、

    - 同じ apply を二重 drain しても after が同じ＝同キー（冪等は保つ）
    - 内容が循環して提案 ID が再利用されても after が違う＝別キー（欠落しない）

    の両方が成り立つ。提案 ID 側の identity 設計を変えても、この分離がある限り
    判断イベントの冪等性は巻き添えにならない。
    """
    return f"{proposal_id}_{kind}_{_sha256(after_content)[:12]}"


def _tracked_path(entry: Dict[str, Any]) -> Optional[str]:
    """entry が accept 判定に使うファイルパス（skill 提案 / advisory 提案の単一ソース）。

    advisory は対象が SKILL.md とは限らない（pytest.ini 等）ので ``target_path`` を持つ。
    パースを2箇所に分けると片側だけ直して desync する（pitfall_copied_parse_convention_partial_fix）
    ため、ingest・``undrained_applied``・marker supersede はこの1関数を共有する。
    """
    return entry.get("target_path") or entry.get("skill_path")


def _supersede_keys(pending: List[Dict[str, Any]]) -> tuple:
    """新しい pending が置き換える対象の判定材料（marker / queue の共有・#287-1）。

    ID 一致だけで消すと、`before_sha` 込みの ID は内容が変わるたびに変わるので同じ
    ファイルの提案が複数世代 residue し、1回の apply が全世代 accept 判定される（#290 で
    marker を塞いだ N 重記録。queue も同契約でないと別経路で再発する）。
    """
    ids = {entry.get("id") for entry in pending if entry.get("id")}
    paths = {path for path in (_tracked_path(entry) for entry in pending) if path}
    return ids, paths


def _is_superseded(entry: Dict[str, Any], ids: Set[str], paths: Set[str]) -> bool:
    return entry.get("id") in ids or _tracked_path(entry) in paths


def _entry_generation(entry: Dict[str, Any]) -> tuple:
    """marker entry の「世代」= (run, 提案, 適用前の内容)（#287-3）。

    drain 中に別 run が同じ対象を再 emit するとその entry は別世代になる。ID だけで
    purge すると新世代を巻き込むので、世代一致するものだけを消す。
    """
    return (entry.get("run_id"), entry.get("id"), entry.get("before_sha"))
