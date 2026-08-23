"""fleet.queue の store reader + material 分類ロジック（#298 分割）。

``fleet/queue.py`` が 792 行に達し ``line_limit.MAX_PYTHON_SOURCE_HARD``（800）まで
残り僅かだったため、weak_signals/corrections store の読み取り関数群と
untracked/phantom material 分類関数群を切り出した**純リファクタ分割**（振る舞い不変）。

``queue.py`` は本モジュールから必要な名前を re-export し、``from fleet.queue import X`` /
``from .queue import X`` の既存 import path はそのまま動く（audit.py 2046→178行・
evolve.py 1739→156行+7 sub-module と同じ手法・ADR-048）。

含む関数:
  - ``_aliases_for``: alias fold の primitive（``queue.py`` の ``_equivalence_slugs`` が使用）
  - ``weak_unprocessed_by_pj`` / ``weak_content_poor_by_pj`` / ``weak_machinery_by_pj`` /
    ``bootstrap_consumed_by_pj``: weak_signals.jsonl の PJ 別未処理件数 reader
  - ``new_corrections_by_pj`` / ``count_unattributed_corrections``: corrections.jsonl の
    PJ 別新規件数 reader
  - ``collect_untracked_materials`` / ``collect_phantom_materials``: tracked 母集団外の
    material 保有 PJ を advisory 列挙する分類関数
"""
from __future__ import annotations

import json
import os
import stat as _stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# --- alias fold primitive（queue.py._equivalence_slugs が使用）----------------


def _aliases_for(slug: str) -> set:
    """``slug``（現 slug）に集計すべき全 slug（自身 + 畳まれる旧名）の集合を返す。

    rename 済 PJ（例: ``rl-anything`` → ``evolve-anything``）の旧 slug レコードを
    現 slug の集計に含めるため、``pj_slug.pj_slug_aliases_for`` を read 層別名 SoT として
    再利用する（write 側 deriver には適用しない）。import 失敗時は ``{slug}`` で保守的に
    フォールバック（自身のみ・cross-PJ 副作用なし）。
    """
    try:
        from pj_slug import pj_slug_aliases_for
        a = pj_slug_aliases_for(slug)
        return a or {slug}
    except Exception:
        return {slug}


# --- store reader: weak_signals 未処理カウント（PJ 別）-----------------------

# #94 の除外 predicate は ``correction_semantic.bootstrap_backlog`` へ移設済み（daily_review
# の毎日確認 phase も同じ判定を必要とするため・非対称是正）。ここは単一ソースからの re-export
# （公開名 ``_exclude_bootstrap_consumed`` を参照する既存コード/テストとの後方互換のため）。
from correction_semantic.bootstrap_backlog import (  # noqa: E402, F401 (re-export)
    _exclude_bootstrap_consumed,
)


def _scoped_kept_signals(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path],
    marker_base: Optional[Path],
) -> List[Dict[str, Any]]:
    """pj_slug scope + actionable 除外を通した未処理 weak レコードを返す（共有 helper）。

    ``weak_unprocessed_by_pj`` / ``weak_content_poor_by_pj`` /
    ``bootstrap_consumed_by_pj`` が同じ read/scope/除外パスを通すための単一ソース
    （partial fix で reader が食い違うのを避ける）。channel フィルタは呼び側で行う。

    #405 round5 [Must]2 是正: promoted/TTL失効/既読・却下済み/bootstrap消化済みの4軸を
    ``correction_semantic.promote.filter_actionable``（全 actionable reader の単一
    predicate）経由で適用する。read（``read_signals``）と pj_slug スコープ（alias fold）は
    従来どおりこの関数の責務のまま維持する（filter_actionable の契約：レコードは呼び出し側が
    既にスコープ済みであること）。挙動は不変（``read_unpromoted`` の既定 exclude_reviewed=True
    を暗黙に使っていた従来実装と同じ default を ``filter_actionable`` も持つ）。
    """
    from correction_semantic.promote import filter_actionable
    from weak_signals.store import read_signals

    recs = read_signals(weak_signals_path)
    aliases = _aliases_for(pj_slug)
    scoped = [r for r in recs if r.get("pj_slug") in aliases]
    return filter_actionable(scoped, pj_slug, marker_base=marker_base)


def weak_unprocessed_by_pj(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
    marker_base: Optional[Path] = None,
) -> int:
    """pj_slug の未処理 **content-rich** weak 件数を返す（material 計数用・#113）。

    既存 reader（``correction_semantic.promote.read_unpromoted``）を再利用し、pj_slug で
    scope する（weak_signals.jsonl は単一 DATA_DIR ファイル・pj_slug は record 属性）。
    ``weak_signals_path`` を渡すとそのファイルのみ（hermetic・テスト注入）。未指定なら
    production 既定（union read）。ファイル不在 → 0。

    #94: bootstrap で破棄/TTL 任せと判断済み（marker 以前 detected）の weak は除外する
    （``_exclude_bootstrap_consumed``）。``marker_base`` はテスト注入（未指定は DATA_DIR）。

    #113: content-poor channel（``REVIEW_CHANNELS`` 外 = esc_interrupt /
    manual_edit_after_ai 等）は material_count に載せない。y/n 確認から除外され promote しても
    signal_text が空で昇格不能な死荷重ゆえ、「今 evolve すべき PJ」判定を歪めないため。channel
    集合は ``correction_semantic.review_channels.REVIEW_CHANNELS`` を単一ソースとして参照する
    （リスト複製しない）。除外件数は ``weak_content_poor_by_pj`` が footer 透明化用に返す。
    """
    from correction_semantic.review_channels import REVIEW_CHANNELS

    kept = _scoped_kept_signals(
        pj_slug, weak_signals_path=weak_signals_path, marker_base=marker_base
    )
    return sum(1 for r in kept if r.get("channel") in REVIEW_CHANNELS)


def weak_content_poor_by_pj(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
    marker_base: Optional[Path] = None,
) -> int:
    """material から除外した content-poor（``REVIEW_CHANNELS`` 外）weak 件数を返す（#113）。

    queue footer / ``--json`` の透明化用（silent truncation 禁止）。``weak_unprocessed_by_pj``
    と同じ scope/bootstrap 除外パス（``_scoped_kept_signals``）を通し、REVIEW_CHANNELS に**入ら
    ない**未処理 weak を数える。両者を足すと bootstrap 消化後の scoped 全件になる（相補的）。
    """
    from correction_semantic.review_channels import REVIEW_CHANNELS

    kept = _scoped_kept_signals(
        pj_slug, weak_signals_path=weak_signals_path, marker_base=marker_base
    )
    return sum(1 for r in kept if r.get("channel") not in REVIEW_CHANNELS)


def weak_machinery_by_pj(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
    marker_base: Optional[Path] = None,
) -> int:
    """machinery（委譲メッセージ等の harness 注入）を理由に material から除外した weak 件数（#443 PR2-a）。

    queue footer / ``--json`` の透明化用（silent truncation 禁止）。``weak_unprocessed_by_pj``
    と同じ pj_slug scope（alias fold）を通した would-be-actionable 母集団のうち machinery
    だった件数を、``REVIEW_CHANNELS``（content-rich）に絞って返す（content-poor channel の
    machinery は元々 ``weak_unprocessed_by_pj`` の母集団に含まれないため二重計上しない）。
    判定は ``correction_semantic.promote.machinery_exclusion_stats``（単一ソース）を使い、
    新しい判定式は書かない。
    """
    from correction_semantic.promote import machinery_exclusion_stats
    from correction_semantic.review_channels import REVIEW_CHANNELS
    from weak_signals.store import read_signals

    recs = read_signals(weak_signals_path)
    aliases = _aliases_for(pj_slug)
    scoped = [r for r in recs if r.get("pj_slug") in aliases]
    stats = machinery_exclusion_stats(scoped, pj_slug, marker_base=marker_base)
    return sum(
        count for channel, count in stats["by_channel"].items() if channel in REVIEW_CHANNELS
    )


def bootstrap_consumed_by_pj(
    pj_slug: str,
    *,
    weak_signals_path: Optional[Path] = None,
    marker_base: Optional[Path] = None,
) -> int:
    """bootstrap 消化済み（marker 以前 detected）で material から除外した weak 件数（#94）。

    queue footer の透明化用。bootstrap marker が無い PJ は常に 0。

    #405 round7 [Should]2 是正: 差分算出を bootstrap 除外（``_exclude_bootstrap_consumed``）
    単独の適用結果から取る。以前は「``read_unpromoted`` ベースの独立集計（scoped）」と
    「``_scoped_kept_signals``（＝``filter_actionable`` 経由・kept）」の差分を取っており、
    両者はたまたま同じ3軸（promoted/TTL/reviewed）を ``_filter_unpromoted`` 経由で共有して
    いたため一致していたが、``filter_actionable`` の本体に新しい軸が直接増えると kept 側
    にしか反映されず、差分が bootstrap 軸以外まで拾ってしまう構造だった。``filter_actionable``
    に ``pj_slug=None`` を渡すと「bootstrap 消化除外だけをスキップしつつ他の全軸を通常適用」
    する契約（#405 round6 [Must]1）を利用し、bootstrap 以外の軸を反映した集合を得たうえで
    そこに ``_exclude_bootstrap_consumed`` だけを適用する差分を取ることで、軸の増減に依存
    せず常に bootstrap 軸だけの消化件数になる。
    """
    from correction_semantic.promote import filter_actionable
    from weak_signals.store import read_signals

    recs = read_signals(weak_signals_path)
    aliases = _aliases_for(pj_slug)
    scoped = [r for r in recs if r.get("pj_slug") in aliases]
    without_bootstrap = filter_actionable(scoped, None)
    kept = _exclude_bootstrap_consumed(without_bootstrap, pj_slug, marker_base=marker_base)
    return len(without_bootstrap) - len(kept)


# --- store reader: corrections.jsonl 共有 raw read + read-health（#533）------


def read_corrections_records_with_health(
    corrections_path: Path,
) -> "tuple[List[Dict[str, Any]], Dict[str, Any]]":
    """corrections.jsonl を1回 read し、``(有効な dict レコード, read health)`` を返す。

    ``new_corrections_by_pj`` / ``count_unattributed_corrections`` /
    ``correction_semantic.correction_backlog`` の各 reader が独立に「ファイル不在→空リスト・
    ``OSError``→空リスト・JSON decode 失敗→無言 skip」を行っていたため、読取不能（権限エラー等）
    と壊れた行ありのケースが呼び側から区別できず、queue 側に「在庫ゼロ」として無音で伝播していた
    （issue #533・``silence != evaluated``）。本関数を単一ソースにし、件数計算に使う従来通りの
    レコードリストと、劣化を表す health dict の両方を同時に返す。

    health の契約:
      - ``readable``: ファイルを最後まで読めたか（``OSError`` で失敗すると False）
      - ``error``: ``readable=False`` のときの例外文字列（成功時は None）
      - ``malformed_lines``: 空行以外で JSON decode に失敗した・dict でなかった・不正 UTF-8 で
        decode できなかった行数

    ファイル不在は「劣化」ではなく「正常な空在庫」（``readable=True, malformed_lines=0``）。

    #538 round2 [Must]3: 従来は ``errors="replace"`` で decode していたため、不正バイト列が
    U+FFFD 置換文字へ静かに丸められ、置換後の文字列が偶然 JSON として parse に成功すると
    （例: ``b'{"project_path":"alp\\xffha"}'`` → ``alp�ha``）``readable=true,
    malformed_lines=0`` の健全扱いになり、当該レコードが誰にも気づかれず破損値のまま集計に
    混入していた。行単位で ``strict`` decode し、``UnicodeDecodeError`` を JSON decode 失敗と
    同じ「malformed 行」として数える（ファイル全体を unreadable にはしない — 1行の破損で
    他の健全な行まで読み捨てるのは #533 が解消した「読取不能と壊れた行の混同」を UTF-8 単位で
    再導入することになるため）。

    #538 round3 [Must]2: 親ディレクトリの検索(x)権限が無い場合、``Path.exists()`` /
    ``Path.is_symlink()`` はいずれも内部で ``OSError`` を握りつぶし ``False`` を返す
    （Python 3.8+ の pathlib 仕様）。従来はこの2つで存在判定してから read していたため、
    権限エラーが「真の未作成」と区別できず ``readable=true`` の正常な空在庫に誤判定していた。
    ``exists()``/``is_symlink()`` に頼らず、まず ``lstat()`` を試みて ``FileNotFoundError``
    （真の未作成）と、その他の ``OSError``（権限エラー等）を区別する。
    """
    health: Dict[str, Any] = {"readable": True, "error": None, "malformed_lines": 0}
    path = Path(corrections_path)
    try:
        st = path.lstat()
    except FileNotFoundError:
        # 真の未作成 = 正常な空在庫。
        return [], health
    except OSError as exc:
        # 親ディレクトリの権限エラー等。exists()/is_symlink() は同じ例外を握りつぶして
        # False を返すため、ここで先に区別する必要がある。
        health["readable"] = False
        health["error"] = str(exc)
        return [], health

    if _stat.S_ISLNK(st.st_mode):
        # symlink エントリ自体は存在する。リンク先の実体を辿って確認する。
        try:
            path.stat()
        except FileNotFoundError:
            # #538 round2 [Must]4: dangling symlink（リンク先が存在しない）は「正常な空在庫」
            # と区別し、劣化として surface する。
            health["readable"] = False
            health["error"] = f"dangling symlink: {path} -> {os.readlink(path)}"
            return [], health
        except OSError as exc:
            health["readable"] = False
            health["error"] = str(exc)
            return [], health

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        health["readable"] = False
        health["error"] = str(exc)
        return [], health

    out: List[Dict[str, Any]] = []
    for raw_line in raw_bytes.split(b"\n"):
        line_bytes = raw_line.strip()
        if not line_bytes:
            continue
        try:
            s = line_bytes.decode("utf-8")
        except UnicodeDecodeError:
            health["malformed_lines"] += 1
            continue
        try:
            rec = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            health["malformed_lines"] += 1
            continue
        if not isinstance(rec, dict):
            health["malformed_lines"] += 1
            continue
        out.append(rec)
    return out, health


def corrections_read_health(corrections_path: Path) -> Dict[str, Any]:
    """corrections.jsonl の read health だけを返す（``build_queue_result`` 用の単発 probe）。

    ``{"readable": bool, "error": Optional[str], "malformed_lines": int}``。
    劣化していない（ファイル不在含む）なら ``readable=True and malformed_lines == 0``。
    """
    _, health = read_corrections_records_with_health(corrections_path)
    return health


def _correction_slug(project_path: Any) -> str:
    """corrections の ``project_path`` を weak_signals と同じ bare slug に正規化する。

    実コーパスでは ``project_path`` が **フルパス**（``/Users/.../amamo``・古い hook）と
    **bare slug**（``amamo``・#593 後）の両方で混在する。weak_signals の ``pj_slug`` は
    bare slug なので、突合のため双方を ``project_name_from_dir``（pj_slug_fast → worktree
    切詰 → basename fallback の単一ソース）で bare slug に畳む。新方式を発明しない。
    """
    if not isinstance(project_path, str) or not project_path:
        return ""
    try:
        from rl_common import project_name_from_dir
        slug = project_name_from_dir(project_path)
        if slug:
            return slug
    except Exception:
        pass
    return Path(project_path).name


def _parse_iso(s: Any) -> Optional[datetime]:
    """ISO8601 文字列を tz-aware datetime にする。`Z` / `+00:00` 終端を吸収。

    naive（tz 無し）は UTC とみなして aware 比較を可能にする。パース不能 → None。
    """
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _ts_strictly_after(ts: Any, last: str) -> bool:
    """``ts`` が ``last`` より厳密に後なら True（同一 instant は False＝除外）。

    実コーパスの corrections は `Z` 終端 / `+00:00` 終端が混在し、``last_evolve_at`` は
    ``persist_last_evolve`` が ``.isoformat()``＝`+00:00` で書く。辞書順だと
    ``"...Z" > "...+00:00"`` が同一 instant でも True になり drain と同時刻の corr を
    誤って新規計上する。両者を datetime にパースして比較し、片方でもパース不能なときのみ
    辞書順へフォールバック（旧挙動温存）。
    """
    a = _parse_iso(ts)
    b = _parse_iso(last)
    if a is not None and b is not None:
        return a > b
    return isinstance(ts, str) and ts > last


def new_corrections_by_pj(
    pj_slug: str,
    *,
    last_evolve_at: Optional[str] = None,
    corrections_path: Optional[Path] = None,
    records: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """pj_slug の corrections のうち ``last_evolve_at`` 以降の件数を返す。

    ``last_evolve_at=None``（state 不在 = 初回）は全件カウント（初回＝全件待ち）。
    corrections は ``project_path`` を bare slug に正規化（``_correction_slug``）してから
    scope する（実コーパスでフルパス / slug が混在するため）。timestamp は ``_ts_strictly_after``
    で datetime 比較する（`Z` / `+00:00` 終端混在を吸収・None 時は全件なので影響しない）。
    ファイル不在 → 0。読取不能・壊れた行の可視化は ``corrections_read_health``（#533）。

    ``records``（``read_corrections_records_with_health`` が既に返した有効レコード列）を渡すと
    再 read しない。同一 ``build_queue_result`` 呼び出し内で probe 時の health と集計対象の
    records が同一スナップショットであることを保証するため（#538 round2 [Must]1 —
    probe と各集計が別読みだと、probe 成功後に read が ``OSError`` になる/その逆で
    「readable=true のまま在庫ゼロ」を返しうる）。未指定時は従来通り ``corrections_path`` から
    自前で read する（後方互換）。
    """
    if records is None:
        if corrections_path is None:
            raise TypeError("new_corrections_by_pj には corrections_path か records のどちらかが必要")
        records, _health = read_corrections_records_with_health(Path(corrections_path))
    count = 0
    for rec in records:
        if _correction_slug(rec.get("project_path")) not in _aliases_for(pj_slug):
            continue
        if last_evolve_at is not None:
            ts = rec.get("timestamp")
            if not _ts_strictly_after(ts, last_evolve_at):
                continue
        count += 1
    return count


def count_unattributed_corrections(
    corrections_path: Optional[Path] = None,
    *,
    since: Optional[str] = None,
    records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """``project_path`` 欠落で PJ 帰属不能な corrections を source 別に数える（#91）。

    ``_correction_slug`` が空文字に落ちるレコード（``project_path`` が空/None）は、どの PJ の
    ``material_count`` にも数えられず ``untracked_with_material`` にも ``skipped_phantom`` にも
    出ないため queue から構造的に完全不可視になる（silent truncation の一種）。#86/#88 の
    「無音で落とさない」原則の最後の穴埋めとして、件数 + source 内訳を advisory に surface する。

    ``since``（ISO8601、既定 None=全件・後方互換）: 指定時は ``timestamp`` が ``since`` より
    厳密に後のレコードのみ数える（``_ts_strictly_after`` と同じ比較。#267 C5）。project_path
    欠落は帰属先 PJ が無く自然失効しないため、時刻窓を付けないと1件の古い未帰属レコードが
    ``unattributed_total`` を永久に非ゼロにし SETUP_REQUIRED を永久ラッチさせる。
    ``build_queue_result`` は直近30日窓を渡す。

    返り値: ``{"total": int, "by_source": {source: count}}``。``source`` 欠落は ``(unknown)``。
    ファイル不在 / 読込失敗 → ``{"total": 0, "by_source": {}}``（advisory ゆえ落とさない）。
    読取不能・壊れた行の可視化は ``corrections_read_health``（#533）。

    ``records``（既に read 済みのレコード列）を渡すと再 read しない（#538 round2 [Must]1・
    ``new_corrections_by_pj`` と同じ理由）。未指定時は ``corrections_path`` から自前で read する
    （後方互換）。
    """
    result: Dict[str, Any] = {"total": 0, "by_source": {}}
    by_source: Dict[str, int] = result["by_source"]
    if records is None:
        if corrections_path is None:
            raise TypeError(
                "count_unattributed_corrections には corrections_path か records のどちらかが必要"
            )
        records, _health = read_corrections_records_with_health(Path(corrections_path))
    for rec in records:
        if _correction_slug(rec.get("project_path")):
            continue  # 帰属可能なものは対象外
        if since is not None and not _ts_strictly_after(rec.get("timestamp"), since):
            continue
        result["total"] += 1
        src = rec.get("source") or "(unknown)"
        by_source[src] = by_source.get(src, 0) + 1
    return result


# --- untracked だが学習素材を持つ PJ の advisory 列挙（#86）-------------------

_UNKNOWN_PROJECT_LABEL = "(unknown)"  # collectors._UNKNOWN_PROJECT_LABEL と一致させる


def _canonical_slug(slug: str) -> str:
    """slug を canonical（rename 旧→現）に畳む。import 失敗時は素通し。"""
    try:
        from pj_slug import canonical_pj_slug
        return canonical_pj_slug(slug) or slug
    except Exception:
        return slug


def collect_untracked_materials(
    *,
    material_slugs: List[str],
    tracked_slugs: set,
    threshold: int,
    weak_signals_path: Optional[Path],
    corrections_path: Path,
    dir_map: Dict[str, str],
    correction_backlog_counts: Optional[Dict[str, int]] = None,
    corr_records: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """material（weak/corr）を持つが queue 母集団（tracked）に居ない PJ を advisory 列挙する（#86）。

    queue の母集団は fleet-config.json の ``tracked_projects`` 限定だが、material 母集団
    （weak_signals / corrections に出現する全 pj_slug）の方が広い。この不一致で、material を
    持つ untracked PJ（例: amamo weak 64 件だが tracked 外）が待ちにも skipped_dead にも
    出ず完全沈黙し真の evolve 候補を取りこぼす（O2）。本関数はその差集合を surface する。

    対象 slug は以下を**全て満たす**もの: ① ``tracked_slugs`` に無い ② ``dir_map`` に
    実 dir を持つ（``Path(dir_map[slug]).is_dir()`` が真＝phantom/temp slug 除外ゲート）
    ③ ``(unknown)`` でない。各 ``material_slugs`` は ``canonical_pj_slug`` で fold
    （rename 旧 slug を現 slug に畳む。import 失敗時は素通し）してから dedup する。

    対象 slug について ``weak_unprocessed_by_pj`` + ``new_corrections_by_pj``
    （untracked は last_evolve state 無し＝全件）を集計し、``material_count >= threshold``
    または ``correction_backlog > 0`` のものを返す。在庫は material_count に加算しない。

    ``corr_records``（``read_corrections_records_with_health`` が既に返した有効レコード列）を
    渡すと corrections.jsonl を再 read しない（#538 round3 [Must]1 — ``build_queue_result`` が
    先に読んだ1回の snapshot を使い回さないと、probe/backlog 集計とこの collector の間で別読みに
    なり、read 結果が変化したときに snapshot 不一致が起きる）。未指定時は従来通り
    ``corrections_path`` から自前で read する（後方互換）。

    Returns:
        ``[{pj_slug, project_path, material_count, weak_unprocessed, new_corrections,
        correction_backlog}]``。
        純関数（store I/O は既存 reader 経由・dir_map/material_slugs は呼び側が用意）。
    """
    tracked_canon = {_canonical_slug(s) for s in tracked_slugs}
    seen: set = set()
    candidates: List[str] = []
    backlog_counts = correction_backlog_counts or {}
    for raw in list(material_slugs) + list(backlog_counts):
        slug = _canonical_slug(raw)
        if not slug or slug == _UNKNOWN_PROJECT_LABEL:
            continue
        if slug in tracked_canon:
            continue
        path = dir_map.get(slug)
        if not path or not Path(path).is_dir():
            continue
        if slug in seen:
            continue
        seen.add(slug)
        candidates.append(slug)

    out: List[Dict[str, Any]] = []
    for slug in candidates:
        weak = weak_unprocessed_by_pj(slug, weak_signals_path=weak_signals_path)
        if corr_records is not None:
            corr = new_corrections_by_pj(slug, last_evolve_at=None, records=corr_records)
        else:
            corr = new_corrections_by_pj(
                slug, last_evolve_at=None, corrections_path=corrections_path
            )
        count = weak + corr
        backlog = int(backlog_counts.get(slug, 0) or 0)
        if count < threshold and backlog <= 0:
            continue
        out.append(
            {
                "pj_slug": slug,
                "project_path": dir_map[slug],
                "material_count": count,
                "weak_unprocessed": weak,
                "new_corrections": corr,
                "correction_backlog": backlog,
            }
        )
    out.sort(key=lambda x: (-x["material_count"], x["pj_slug"]))
    return out


def collect_phantom_materials(
    *,
    material_slugs: List[str],
    tracked_slugs: set,
    threshold: int,
    weak_signals_path: Optional[Path],
    corrections_path: Path,
    dir_map: Dict[str, str],
    correction_backlog_counts: Optional[Dict[str, int]] = None,
    corr_records: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """閾値以上 material または修正在庫を持ち、実 dir に解決できない slug を列挙する（#88/#515）。

    ``collect_untracked_materials`` の ``is_dir()`` ゲートで黙って drop される slug
    （例: temp slug ``tmpdcm8avo8`` material=5）を透明化するための対称関数。
    ``skipped_dead`` は透明化するのに phantom だけ不可視という非対称（O1 と非対称）を是正する。

    対象 slug は: ① ``tracked_slugs``（canonical fold 後）に無い ② ``(unknown)`` でない
    ③ ``dir_map`` で実 dir に**解決できない**（``collect_untracked_materials`` の補集合）
    ④ material_count（weak + corr・untracked は全件）が threshold 以上、または
    correction_backlog が1件以上。material_count 降順
    （同数は pj_slug 昇順）で返す。waiting には昇格させない（temp slug は意図的に除外）。

    ``corr_records`` は ``collect_untracked_materials`` と同じ契約（渡すと再 read しない・
    #538 round3 [Must]1）。

    Returns:
        ``[{pj_slug, material_count, weak_unprocessed, new_corrections, correction_backlog}]``（project_path は
        解決できないので付けない）。純関数（store I/O は既存 reader 経由）。
    """
    tracked_canon = {_canonical_slug(s) for s in tracked_slugs}
    seen: set = set()
    candidates: List[str] = []
    backlog_counts = correction_backlog_counts or {}
    for raw in list(material_slugs) + list(backlog_counts):
        slug = _canonical_slug(raw)
        if not slug or slug == _UNKNOWN_PROJECT_LABEL:
            continue
        if slug in tracked_canon:
            continue
        path = dir_map.get(slug)
        if path and Path(path).is_dir():
            continue  # 実 dir 解決可 → untracked 側（phantom でない）
        if slug in seen:
            continue
        seen.add(slug)
        candidates.append(slug)

    out: List[Dict[str, Any]] = []
    for slug in candidates:
        weak = weak_unprocessed_by_pj(slug, weak_signals_path=weak_signals_path)
        if corr_records is not None:
            corr = new_corrections_by_pj(slug, last_evolve_at=None, records=corr_records)
        else:
            corr = new_corrections_by_pj(
                slug, last_evolve_at=None, corrections_path=corrections_path
            )
        count = weak + corr
        backlog = int(backlog_counts.get(slug, 0) or 0)
        if count < threshold and backlog <= 0:
            continue
        out.append(
            {
                "pj_slug": slug,
                "material_count": count,
                "weak_unprocessed": weak,
                "new_corrections": corr,
                "correction_backlog": backlog,
            }
        )
    out.sort(key=lambda x: (-x["material_count"], x["pj_slug"]))
    return out
