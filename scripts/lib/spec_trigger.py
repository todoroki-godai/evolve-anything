"""spec_trigger — main に着地した「仕様未追従の変更」を SessionStart で検出し、
spec-keeper update / ADR 化を1回提案する決定論ゲート（ADR-044）。

## なぜ必要か
`spec-keeper-trigger.md`（グローバルルール）と gstack `ship→spec-keeper` 連鎖は
あるが、hook/コードからは発火せず /ship 経由でしか走らない。`gh pr merge` 直叩きや
GitHub web 上の squash マージ（この PJ の実マージ手段）では何も出ない。
ルール記載は assistant が忘れる＝`SKILL.md MUST ≠ enforcement`。決定論 hook で塞ぐ。

## 検知点（SessionStart）
web squash マージは「自分のセッション外で main が進んだ」状態であり、ローカル
イベント（Stop/PostToolUse）では原理的に拾えない。起動時に git の状態を diff する
SessionStart が唯一の検知点。`restore_state.py` が既に持つ配信機構に相乗りする。

## 較正版ゲート（実コーパス dry-run で確定）
直近 40 commit への dry 適用で:
  - 素朴な `feat:` + plugin.json 監視 → 8件全部が version bump の FP
  - structural-only（skill/hook 追加のみ）→ 0件（この PJ は scripts/lib 改変で進化）
  - 挙動コード変更 × spec 未更新 → 12件だが10件が fix（仕様変えず）の FP
  - **feat/feat!/refactor × 挙動コード変更 × spec 未更新（CLAUDE.md 含む）→ 2件**（真 TP）

ゆえに発火条件は:
  ① コミット種別 ∈ {feat, refactor}（fix/chore/docs/test 等は対象外）または breaking(`!`)
  ② diff が scripts/**.py または hooks/**.py（挙動コード）を変更
  ③ diff が仕様アーティファクトを一切触っていない
     仕様アーティファクト = SPEC.md | spec/** | docs/decisions/** | CONTEXT.md | **CLAUDE.md**
     （この PJ の生きた仕様は CLAUDE.md の component table。SPEC.md 単点は FP/FN 源）

## 重複抑制（cooldown + 解消プロキシ）
`silence ≠ evaluated` の沈黙バグを避けつつ nag も避ける中間:
  - 新規 fire は即時 surface し pending に積む
  - 同一 commit は COOLDOWN 内では再提示しない
  - COOLDOWN 明けに未解消なら1回だけリマインド（MAX_REMINDERS=1）。以後は沈黙
  - **解消プロキシ**: 新スキャン範囲に仕様アーティファクトを触ったコミットが1つでも
    あれば pending を全クリア（dev が仕様を維持している＝good state ＝沈黙でよい）

`--dry-run`/テストでは `persist=False` でマーカー書き込みを一切しない
（pitfall_dryrun_stateful_store_write 準拠）。

決定論・LLM 非依存。slug 解決は optimize_history_store（worktree 安全、ADR-031）に委譲。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# slug は正準解決器を再利用（worktree 安全: git --git-common-dir 親 basename）
from optimize_history_store import resolve_slug, UNATTRIBUTED_SLUG  # noqa: F401

# DATA_DIR 解決（#148 / ADR-042・#137 同型）:
# 従来は module import 時に生 ``CLAUDE_PLUGIN_DATA`` を直読みして DATA_DIR を確定して
# いたため、他ストアが使う ``rl_common.resolve_data_dir()`` の marker ゲート redirect を
# 経由せず、hook 文脈（env=plugins-data）と tool 文脈（env なし）で marker JSON の
# 読み書きが別 dir に分裂した（実測 copied:4）。DATA_DIR を **call-time** に
# ``rl_common.resolve_data_dir(env)`` で解決し、marker（``.data-dir-unified``）が立って
# いれば hook/tool どちらの文脈でも同一 canonical に収束させる（read/write 同一関数 #492）。
#
# テスト専用 override。非 None のとき ``_data_dir()`` は env/marker 解決を迂回して
# この値を返す。production では常に None（call-time env 解決を使う）。
_DATA_DIR_OVERRIDE: "Path | None" = None


def _data_dir() -> Path:
    """DATA_DIR を call-time に解決する（marker ゲート経由・#148 / #137 同型）。

    ``_DATA_DIR_OVERRIDE`` が立っていればそれを返す（テスト経路）。それ以外は
    ``rl_common.resolve_data_dir(CLAUDE_PLUGIN_DATA)`` で hook/tool 文脈を marker
    ゲート経由に統一する（import 時固定でなく毎回解決＝env/monkeypatch 追従）。
    """
    if _DATA_DIR_OVERRIDE is not None:
        return _DATA_DIR_OVERRIDE
    import rl_common

    return rl_common.resolve_data_dir(os.environ.get("CLAUDE_PLUGIN_DATA", ""))


def _marker_root() -> Path:
    return _data_dir() / "spec_trigger"


def __getattr__(name: str):
    """後方互換の外部読み取り shim（``spec_trigger.DATA_DIR`` / ``MARKER_ROOT``）。

    内部は ``_data_dir()`` / ``_marker_root()`` を使うが、外部 reader は従来
    ``spec_trigger.DATA_DIR`` / ``MARKER_ROOT`` を参照する。module ``__getattr__``
    （PEP 562）で call-time 解決値を返し、内部/外部で単一の解決経路にする
    （import 時固定コピーを構造的に排除・#148/#96）。

    NOTE: ここが提供する名前（``DATA_DIR`` / ``MARKER_ROOT``）を
    ``monkeypatch.setattr`` で直接 patch してはならない。teardown で実属性が pin
    され、以後 ``__getattr__`` を恒久 shadow するため（#136 のテストで実際に発生した
    プロセス汚染）。テストの隔離は ``_DATA_DIR_OVERRIDE`` を立てて行う。
    """
    if name == "DATA_DIR":
        return _data_dir()
    if name == "MARKER_ROOT":
        return _marker_root()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# ── ゲートのパラメータ ───────────────────────────────────────────
SPEC_RELEVANT_TYPES = ("feat", "refactor")
BEHAVIOR_PREFIXES = ("scripts/", "hooks/")
SPEC_FILES = ("SPEC.md", "CLAUDE.md", "CONTEXT.md")
SPEC_PREFIXES = ("spec/", "docs/decisions/")

DAY_SECONDS = 86400.0
COOLDOWN_DAYS = 3
COOLDOWN_SECONDS = COOLDOWN_DAYS * DAY_SECONDS
MAX_REMINDERS = 1  # 初回 + リマインド1回 = 最大2回までで打ち止め

# trunk の tip として優先的に見る ref（fetch はしない＝オフライン安全・高速）。
# 解決できなければ沈黙する（HEAD=現在ブランチには**落とさない**。落とすと master 既定リポや
# trunk 不在環境で、ユーザー自身の作業中ブランチのコミットを誤って「仕様未追従」と提案するため）。
HEAD_REFS = ("origin/main", "main", "origin/master", "master")


# ─────────────────────────────────────────────────────────────────
# 純関数: コミット分類（ゲートの中心。テストの主対象）
# ─────────────────────────────────────────────────────────────────
def commit_type(subject: str) -> Tuple[str, bool]:
    """Conventional Commit subject から (type, is_breaking) を返す。

    例: "feat(x)!: y" → ("feat", True) / "fix: y" → ("fix", False)
    """
    head = subject.split(":", 1)[0]
    breaking = head.rstrip().endswith("!")
    ctype = head.split("(", 1)[0].replace("!", "").strip().lower()
    return ctype, breaking


def _touches_spec(paths: List[str]) -> bool:
    for p in paths:
        if p in SPEC_FILES or p.startswith(SPEC_PREFIXES):
            return True
    return False


def _touches_behavior(paths: List[str]) -> bool:
    for p in paths:
        if p.endswith(".py") and p.startswith(BEHAVIOR_PREFIXES):
            return True
    return False


def is_spec_relevant_commit(subject: str, paths: List[str]) -> Tuple[bool, bool]:
    """この commit が仕様追従提案に値するかを返す → (fires, breaking)。

    fires = 種別が spec-relevant か breaking で、かつ挙動コードを変えたのに
            仕様アーティファクトを一切触っていない。
    """
    ctype, breaking = commit_type(subject)
    if ctype not in SPEC_RELEVANT_TYPES and not breaking:
        return False, breaking
    if not _touches_behavior(paths):
        return False, breaking
    if _touches_spec(paths):
        return False, breaking
    return True, breaking


# ─────────────────────────────────────────────────────────────────
# git ヘルパ（cwd で実行。例外は呼び出し側で握って沈黙）
# ─────────────────────────────────────────────────────────────────
def _git(cwd: Path, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return out.stdout


def head_sha(cwd: Path) -> Optional[str]:
    """trunk の tip sha を返す（HEAD_REFS の順で最初に解決できたもの、無ければ None）。"""
    for ref in HEAD_REFS:
        out = _git(cwd, "rev-parse", "--verify", "--quiet", ref)
        if out and out.strip():
            return out.strip()
    return None


def list_commits(cwd: Path, since_sha: str, head: str) -> List[Tuple[str, str]]:
    """since_sha..head の first-parent コミットを (sha, subject) で時系列順に返す。"""
    if since_sha == head:
        return []
    out = _git(
        cwd, "log", "--first-parent", "--reverse",
        "--format=%H%x1f%s", f"{since_sha}..{head}",
    )
    if not out:
        return []
    rows: List[Tuple[str, str]] = []
    for line in out.splitlines():
        if "\x1f" in line:
            sha, subj = line.split("\x1f", 1)
            rows.append((sha.strip(), subj.strip()))
    return rows


def commit_files(cwd: Path, sha: str) -> List[str]:
    out = _git(cwd, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    if not out:
        return []
    return [p.strip() for p in out.splitlines() if p.strip()]


# ─────────────────────────────────────────────────────────────────
# マーカー（PJ スコープ・単一 JSON）
# ─────────────────────────────────────────────────────────────────
def marker_path(slug: str) -> Path:
    return _marker_root() / f"{slug}.json"


def load_marker(slug: str) -> Dict[str, Any]:
    path = marker_path(slug)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_marker(slug: str, data: Dict[str, Any]) -> None:
    path = marker_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────
# config ゲート
# ─────────────────────────────────────────────────────────────────
def _is_enabled() -> bool:
    try:
        from rl_common.config import load_user_config

        return bool(load_user_config().get("spec_trigger_enabled", True))
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────────
# メッセージ整形
# ─────────────────────────────────────────────────────────────────
def _view(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {"sha": entry["sha"], "subject": entry["subject"], "breaking": entry["breaking"]}


def format_message(surfaced: List[Dict[str, Any]]) -> str:
    n = len(surfaced)
    lines = [f"[evolve-anything:spec-trigger] 🔔 main に仕様未追従の変更 {n} 件:"]
    has_breaking = False
    for s in surfaced:
        tag = " 🔁再通知" if s.get("reminder") else ""
        bang = " (breaking)" if s["breaking"] else ""
        if s["breaking"]:
            has_breaking = True
        lines.append(f"  - {s['sha'][:8]} {s['subject'][:64]}{bang}{tag}")
    lines.append(
        "→ コード変更が SPEC.md / CLAUDE.md(component table) に未反映の可能性。"
        " /evolve-anything:spec-keeper update で追従を検討。"
    )
    if has_breaking:
        lines.append("  破壊的変更あり → ADR 化も検討。")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# 本体
# ─────────────────────────────────────────────────────────────────
def detect(
    cwd: Optional[Path] = None,
    *,
    persist: bool = True,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """SessionStart 時に呼ばれ、仕様未追従マージを検出して提案 message を返す。

    返り値: {"message": str|None, "fires": [...], "reminders": [...]}
    すべて fail-safe（git/IO 例外でも message=None を返し、SessionStart を壊さない）。
    """
    result: Dict[str, Any] = {"message": None, "fires": [], "reminders": []}
    if not _is_enabled():
        return result

    cwd_path = Path(cwd) if cwd is not None else Path.cwd()
    now_ts = time.time() if now is None else now

    head = head_sha(cwd_path)
    if not head:
        return result  # git 外 / ref 解決不能 → 沈黙

    slug = resolve_slug(cwd_path)
    marker = load_marker(slug)
    last = marker.get("last_sha")
    pending: List[Dict[str, Any]] = list(marker.get("pending", []))

    # 初回: マーカーをセットするだけで過去分を flood しない
    if not last:
        marker = {"last_sha": head, "pending": []}
        if persist:
            save_marker(slug, marker)
        return result

    new_commits = list_commits(cwd_path, last, head)
    spec_touched = any(_touches_spec(commit_files(cwd_path, sha)) for sha, _ in new_commits)

    surfaced: List[Dict[str, Any]] = []
    if spec_touched:
        # 解消プロキシ: 範囲内で仕様を触った → drift 維持されている。全クリアで沈黙。
        pending = []
    else:
        existing = {p["sha"] for p in pending}
        for sha, subj in new_commits:
            fires, breaking = is_spec_relevant_commit(subj, commit_files(cwd_path, sha))
            if fires and sha not in existing:
                entry = {
                    "sha": sha,
                    "subject": subj,
                    "breaking": breaking,
                    "first_seen": now_ts,
                    "reminders": 0,
                    "cooldown_until": now_ts + COOLDOWN_SECONDS,
                }
                pending.append(entry)
                surfaced.append({**_view(entry), "reminder": False})

        surfaced_shas = {s["sha"] for s in surfaced}
        kept: List[Dict[str, Any]] = []
        for p in pending:
            if p["sha"] in surfaced_shas:
                kept.append(p)
                continue
            if p["cooldown_until"] <= now_ts:
                if p["reminders"] < MAX_REMINDERS:
                    p["reminders"] += 1
                    p["cooldown_until"] = now_ts + COOLDOWN_SECONDS
                    surfaced.append({**_view(p), "reminder": True})
                    kept.append(p)
                # MAX_REMINDERS 到達かつ cooldown 明け → drop（nag しない）
            else:
                kept.append(p)
        pending = kept

    marker["last_sha"] = head
    marker["pending"] = pending
    if persist:
        save_marker(slug, marker)

    result["fires"] = [s for s in surfaced if not s.get("reminder")]
    result["reminders"] = [s for s in surfaced if s.get("reminder")]
    if surfaced:
        result["message"] = format_message(surfaced)
    return result
