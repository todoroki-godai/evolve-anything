"""pj_slug — PJ slug 導出の単一ソース（#492）。

背景: slug 導出が2系統に分裂していた。
  - (a) ``optimize_history_store.resolve_slug``: ``git --git-common-dir`` の親 basename
        （authoritative・worktree から呼んでも本体 repo 名に正規化・subprocess あり）
  - (b) ``utterance_archive.extractor.pj_slug_from_cwd``: ``/.claude/worktrees/`` で
        切る文字列処理（高速・subprocess なし）
同一ストアの read/write で別方式が混ざると worktree 環境で slug が食い違い、
書いたレコードを読めない時限式 silent mismatch を生む（pitfall_worktree_slug_show_toplevel / #440）。

本モジュールがその恒久解（単一関数化）:
  - ``resolve_pj_slug(path_or_cwd)``: authoritative。git-common-dir があれば親 basename、
    git 不可（repo 外 / git 未インストール）なら文字列フォールバック（``pj_slug_fast``）。
  - ``pj_slug_fast(path)``: 文字列処理のみ。hot path（hooks）はこちらを使う（毎発火 hook で
    subprocess 禁止 — pitfall_hot_hook_eager_import / hot hook レイテンシ）。

既存2関数（``resolve_slug`` / ``pj_slug_from_cwd``）は本モジュールの thin wrapper に寄せ、
後方互換 re-export を維持する（呼び出し元の一斉書き換えはしない・段階移行）。

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional, Union

# git repo 外（slug 解決不能）の保全先。calibration 母集団からは除外される。
# optimize_history_store.UNATTRIBUTED_SLUG と同値（後方互換のため重複定義）。
UNATTRIBUTED_SLUG = "_unattributed"

# worktree セッションを本体 repo に帰属させるためのマーカー（path 中で切る位置）。
# utterance_archive.extractor._WORKTREE_MARKER と同値（後方互換のため重複定義）。
_WORKTREE_MARKER = "/.claude/worktrees/"

# ── PJ rename の read 層 slug 別名（#45/#47・ADR-049 ①）────────────────────
# PJ が rename されると、rename 前に書かれた legacy テレメトリは旧 slug でタグ付け
# されたまま残る（例: evolve-anything は旧名 rl-anything 名義で session 8万件・error 3万件・
# subagents 2千件を蓄積）。self-audit/dogfood がリネーム前の自分の履歴を回収できるよう、
# **読み取り層でのみ** 旧 slug を現 slug に畳む別名表。
#   - read 側の slug 比較（``_normalize_pj``）と query 系 reader（``pj_slug_aliases_for``）が
#     共有する単一ソース。
#   - write 側 deriver（``pj_slug_fast`` / ``resolve_pj_slug`` / ``pj_slug_from_cwd``）には
#     適用しない（可逆性のため・データは書き換えない・物理 merge #46 とは独立）。
# 新しい rename はここに ``{旧 slug: 現 slug}`` を1行追記する。
PJ_SLUG_ALIASES = {
    "rl-anything": "evolve-anything",
}


def canonical_pj_slug(slug: Optional[str]) -> Optional[str]:
    """旧 slug を現 slug に畳む（read 層の別名解決・SoT）。

    ``PJ_SLUG_ALIASES`` に旧名があれば現名を返す。未知 / None / 空はそのまま返す
    （非破壊・冪等）。write 側には適用しないこと（別名は読み取り専用契約）。
    """
    if not slug:
        return slug
    return PJ_SLUG_ALIASES.get(str(slug), str(slug))


def pj_slug_aliases_for(target: Optional[str]) -> set:
    """``target``（現 slug）にマッチすべき全 slug（自身 + 畳まれる旧名）の集合を返す。

    exact-match で project フィルタする query 系 reader（query_usage / query_errors）が
    別名込みに絞り込みを広げるのに使う。rename されていない PJ では ``{target}`` のみを
    返す（他 PJ は現状維持・cross-PJ 副作用なし）。``target`` が空なら空集合。
    """
    if not target:
        return set()
    target = str(target)
    out = {target}
    for old, new in PJ_SLUG_ALIASES.items():
        if new == target:
            out.add(old)
    return out


# SessionStart cache（#29/#593）: sibling-dir worktree（``/.claude/worktrees/`` マーカー外）の
# write 時 slug 解決のためのキャッシュファイル名。DATA_DIR 直下に置く。
#   - SessionStart（hot path でない）が `resolve_pj_slug(cwd)`（authoritative・subprocess 可）を
#     1回だけ呼び、{cwd: slug} を本ファイルに書く（``write_pj_slug_cache``）。
#   - hooks hot path の ``pj_slug_fast`` は worktree マーカーで畳めなかったときだけ本ファイルを
#     参照し、cwd 一致なら authoritative slug を返す（subprocess なし＝hot-path 安全を維持）。
# キャッシュ miss / 未生成 / 破損は従来 basename 挙動へフォールバック（後方互換）。
PJ_SLUG_CACHE_FILENAME = "pj_slug_cache.json"


def _normalize_cache_key(path: Union[str, Path]) -> str:
    """cache のキー正規化（write/read 同形）。末尾スラッシュ差等を吸収する。"""
    return str(Path(str(path)))


def write_pj_slug_cache(
    cwd: Union[str, Path],
    slug: str,
    *,
    data_dir: Path,
) -> None:
    """{cwd: slug} を DATA_DIR/pj_slug_cache.json に書く（SessionStart 用・#29/#593）。

    SessionStart（hot path でない）から1回だけ呼ぶ前提。``slug`` は呼び出し側で
    ``resolve_pj_slug(cwd)``（authoritative）を解決して渡す。既存エントリは保持して
    マージする（複数 PJ の cwd を共存させる）。破損キャッシュは無視して上書き再構築する。

    決定論・subprocess なし（slug 解決は呼び出し側の責務）。
    """
    data_dir = Path(data_dir)
    cache_path = data_dir / PJ_SLUG_CACHE_FILENAME
    existing: dict = {}
    if cache_path.exists():
        try:
            loaded = json.loads(cache_path.read_text())
            if isinstance(loaded, dict):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = {}  # 破損キャッシュは捨てて再構築
    existing[_normalize_cache_key(cwd)] = slug
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(existing, ensure_ascii=False))


def _lookup_pj_slug_cache(path: Union[str, Path], data_dir: Path) -> Optional[str]:
    """cache から cwd 一致の authoritative slug を引く（hot path・subprocess なし）。

    未生成 / miss / 破損は None（呼び出し側が従来挙動へフォールバック）。
    """
    cache_path = Path(data_dir) / PJ_SLUG_CACHE_FILENAME
    if not cache_path.exists():
        return None
    try:
        loaded = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded.get(_normalize_cache_key(path))


def pj_slug_fast(
    path: Optional[Union[str, Path]],
    *,
    data_dir: Optional[Path] = None,
) -> Optional[str]:
    """文字列処理のみで worktree 安全な pj_slug を導出する（hot path 用・subprocess なし）。

    1. path に ``/.claude/worktrees/`` が含まれればそこで切って本体側パスへ正規化
       （worktree セッションを main repo に帰属させる）
    2. (1) で畳めない sibling-dir worktree（マーカー外）は、``data_dir`` が渡されていれば
       SessionStart cache（``write_pj_slug_cache``）を参照し、cwd 一致なら authoritative
       slug を返す（subprocess なし＝hot-path 安全を維持・#29/#593）
    3. cache miss / 未生成 / 破損 / ``data_dir`` 未指定なら正規化後パスの basename

    path が None / 空なら None（呼び出し側が fallback する）。
    ``git rev-parse`` を呼ばないため、毎発火 hook から安全に使える。
    """
    if not path:
        return None
    s = str(path)
    marker_idx = s.find(_WORKTREE_MARKER)
    if marker_idx != -1:
        # worktree マーカーで本体 repo root まで畳めるケース（従来どおり・cache 不要）。
        base = Path(s[:marker_idx]).name
        return base or None
    # sibling-dir worktree（マーカー外）: SessionStart cache を参照（subprocess なし）。
    if data_dir is not None:
        cached = _lookup_pj_slug_cache(s, data_dir)
        if cached:
            return cached
    base = Path(s).name
    return base or None


def resolve_pj_slug(path_or_cwd: Optional[Union[str, Path]] = None) -> str:
    """authoritative な pj_slug を返す（git-common-dir 親 basename・worktree 安全）。

    解決順:
      1. ``git rev-parse --git-common-dir`` で本体 repo の .git を取り、その親 basename。
         worktree から呼んでも本体 slug に正規化される（最も正確）。
      2. git 不可（repo 外 / git 未インストール / OS エラー）のフォールバックは
         ``pj_slug_fast`` に委譲する（worktree マーカーがあれば本体 slug に正規化、無ければ
         basename）。これにより **writer の hot-path（``pj_slug_fast``）と reader（本関数）が
         非git PJ でも同一規約（basename）になり**、hook が basename で書いたレコードを reader が
         ``_unattributed`` 名前空間で探して交差ゼロになる silent bug を根治する（#47）。
      3. basename も取れない真の空 path（``Path('').name`` 等）のみ ``UNATTRIBUTED_SLUG``
         （calibration 母集団からの除外センチネルとして残す）。

    ``path_or_cwd`` が None のときは現在の cwd（``Path.cwd()``）を使う。

    注意（#47 セマンティクス変更）: 旧版は worktree マーカー無しの非git dir を ``_unattributed``
    固定にしていた（calibration 除外）。非git PJ は稀だが、その間 writer/reader slug が割れて
    全 section 沈黙＋他の非git PJ 同士が ``_unattributed`` に混ざり誤帰属していた。basename は
    pj_slug_fast が既に write 側で使っている識別子であり、reader を揃えることで読めるようにする。
    """
    cwd_path = Path(path_or_cwd) if path_or_cwd is not None else Path.cwd()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd_path),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        out = ""

    if out:
        common_dir = Path(out)
        if not common_dir.is_absolute():
            common_dir = (cwd_path / common_dir).resolve()
        # common_dir は本体 repo の .git（または bare repo path）。親が repo root。
        slug = common_dir.parent.name
        if slug:
            return slug

    # git 不可: 文字列フォールバック（pj_slug_fast）に委譲し writer と同一規約に揃える（#47）。
    # pj_slug_fast: worktree マーカーがあれば本体 slug へ畳む / 無ければ basename。
    # basename も取れない真の空 path のみ _unattributed（calibration 除外センチネル）。
    fast = pj_slug_fast(cwd_path)
    return fast or UNATTRIBUTED_SLUG


def resolve_cc_memory_dir(path_or_cwd: Optional[Union[str, Path]] = None) -> Path:
    """CC の ``~/.claude/projects/<path-encoded>/memory`` を返す（単一ソース・#18/#19）。

    Claude Code は projects ディレクトリを **cwd 絶対パスの ``/`` を ``-`` に置換した名前**
    で持つ（例: ``/Users/x/proj`` → ``-Users-x-proj``）。これは ``resolve_pj_slug`` が返す
    repo-basename slug（例 ``proj``）とは **名前空間が別物**。memory dir 解決に
    ``resolve_pj_slug`` を使うと別の場所を指して section が常に沈黙する（#19 で実際に踏んだ
    バグ）。memory dir を引く箇所は必ず本関数を使うこと。

    存在する candidate（先頭 ``-`` 有無の2通り）を優先して返す。どちらも無ければ primary
    candidate（非存在 Path）を返すので、呼び出し側は ``is_dir()`` で不在を扱える。

    既知の限界: worktree から渡された path はその worktree の encoded dir を見る（#18 と同挙動。
    memory は CC が cwd 単位で projects dir を持つため本体 repo へは自動正規化しない）。
    """
    base = Path.home() / ".claude" / "projects"
    target = Path(path_or_cwd) if path_or_cwd is not None else Path.cwd()
    encoded = str(target).replace("/", "-")
    for candidate in (encoded, encoded.lstrip("-")):
        memory_dir = base / candidate / "memory"
        if memory_dir.is_dir():
            return memory_dir
    return base / encoded / "memory"
