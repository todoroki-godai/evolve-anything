"""optimize_history_store — accept/reject 履歴の正準ストア（ADR-031）。

optimize / evolve-loop / evolve-diff の accept/reject 決定ログ（fitness calibration の母集団）を
DATA_DIR 配下の project スコープ JSONL に集約する単一ソース。

背景: 従来は読み書きが 3 経路に分裂（split-brain）していた:
  - optimize / evolve-diff → <PLUGIN_ROOT>/skills/.../generations/history.jsonl（更新でリセット）
  - run_loop            → <cwd>/.evolve-loop/history.jsonl（readers が読まない孤立）
  - readers             → plugin generations を読む
このモジュールに集約し、保存先を永続 DATA_DIR の `optimize_history/<slug>.jsonl` に一本化する。

slug は worktree 安全に解決する（`git --git-common-dir` 経由）。素直な
`git rev-parse --show-toplevel` の basename は worktree 内で worktree 名を返し、
本体 repo と食い違って二次 split-brain を生むため使わない。

決定論・LLM 非依存。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
HISTORY_ROOT = DATA_DIR / "optimize_history"

# git repo 外（slug 解決不能）の保全先。calibration 母集団からは除外される。
UNATTRIBUTED_SLUG = "_unattributed"

# ファイル名に使えない文字を _ へ。`Path.name` 由来なので traversal は構造的に不可だが、
# world_context（同リリースの per-slug 化）と同じサニタイズで一貫性と防御を揃える。
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_slug(slug: str) -> str:
    # 先頭/末尾の . _ は剥がさない（UNATTRIBUTED_SLUG="_unattributed" を保つため）。
    cleaned = _SLUG_UNSAFE.sub("_", slug)
    return cleaned or UNATTRIBUTED_SLUG


def resolve_slug(cwd: Optional[Path] = None) -> str:
    """current（または指定 cwd の）project slug を返す。

    worktree 安全: `git rev-parse --git-common-dir` で本体 repo の .git を取り、
    その親ディレクトリ名を slug とする。worktree から呼んでも本体 slug に正規化される。
    git repo 外なら UNATTRIBUTED_SLUG。

    #492: 導出ロジックは ``pj_slug.resolve_pj_slug`` に単一ソース化した。本関数は
    後方互換のための thin wrapper（既存呼び出し元の一斉書き換えを避ける段階移行）。
    """
    from pj_slug import resolve_pj_slug

    return resolve_pj_slug(cwd)


def history_path(slug: str) -> Path:
    """slug の履歴ファイルパスを返す（HISTORY_ROOT/<slug>.jsonl）。

    slug はファイル名構築の chokepoint でサニタイズする（resolve_slug 由来でも
    明示渡しでも一律に適用）。
    """
    return HISTORY_ROOT / f"{_sanitize_slug(slug)}.jsonl"


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """1 ファイルを読み込む。未存在なら []。空行・壊れた JSON 行はスキップ。"""
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _merge_dedup(batches: Iterable[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """複数バッチ（候補 dir/slug 順に読んだレコード列）を id dedup（先勝ち）しつつ結合する。

    ``id`` を持たないレコードは安全に dedup できないため無条件で全件保持する。
    ``load_raw_history`` / ``_aliased_raw_records`` の共有 chokepoint（#402 段階2）:
    2つの集約経路が別々に dedup ロジックを持つと desync するため単一化する
    （pitfall_copied_parse_convention_partial_fix と同じ理由）。
    """
    by_id: Dict[Any, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for batch in batches:
        for rec in batch:
            rid = rec.get("id")
            if rid is not None:
                if rid in by_id:
                    continue  # 候補列は先頭ほど優先 → 先勝ち
                by_id[rid] = rec
            out.append(rec)
    return out


def load_raw_history(slug: str) -> List[Dict[str, Any]]:
    """slug の履歴を canonical + legacy/plugins-data から cross-dir union read する（#45）。

    **正準名（#402 段階2 §5）**。DATA_DIR 断片化（rename rl-anything→evolve-anything /
    plugins-data hook split）の移行期、canonical だけ読むと legacy にのみ残った accept/reject
    履歴（fitness calibration の母集団）を取り逃す。``rl_common.iter_read_data_dirs`` が
    ``HISTORY_ROOT.parent``（= DATA_DIR）の親から候補 dir を導出し、各候補の
    ``optimize_history/<slug>.jsonl`` を読んで合算する。候補は **canonical 先頭**なので、
    同一 ``id`` は canonical を優先して dedup する。

    **読み取り専用**: ``append_entry``（write）は canonical 固定のまま（ADR-049: write 側
    self-resolver は意図的に維持）。本関数は read 側だけを union 化する。tmp canonical を
    渡すテストでは兄弟 dir が存在せず canonical のみを読む（hermetic）。

    **PJ rename slug alias は適用しない**（``evolve-anything``/``rl-anything`` のような単一
    slug の cross-dir union のみ）。alias をまたいだ集約が要るのは判断母集団を作る
    ``load_effective_history`` / ``load_revert_events``（``_aliased_raw_records`` 経由）で、
    本関数の既存呼び出し元（未移行 reader）の挙動は変えない（#402 段階2・段階4で移行）。
    """
    from rl_common import iter_read_data_dirs

    safe = _sanitize_slug(slug)
    return _merge_dedup(
        _read_jsonl(d / "optimize_history" / f"{safe}.jsonl")
        for d in iter_read_data_dirs(HISTORY_ROOT.parent)
    )


def load_history(slug: str) -> List[Dict[str, Any]]:
    """[後方互換] ``load_raw_history`` の thin wrapper（#402 段階2 §5）。

    正準名は ``load_raw_history``。本関数は既存呼び出し元の一斉書き換えを避けるための
    後方互換名として残す。**単なる別名（``load_history = load_raw_history``）にはしない** —
    docstring・型注釈・将来の非推奨化（deprecation warning 等）を ``load_raw_history`` と
    独立に管理できるようにするため。新規コードは raw が必要なら ``load_raw_history``、
    通常の業務読取は ``load_effective_history`` を使うこと（raw を読んでよい箇所は
    ``raw_history_gate`` の allowlist で明示管理する）。
    """
    return load_raw_history(slug)


def normalize_entry_timestamp(entry: Dict[str, Any]) -> None:
    """entry の ``timestamp`` が**有効な ISO8601 文字列**なら aware UTC へ正規化する
    （in-place）。writer ごとに naive ローカル / aware UTC が混在する tz 不統一（#297）を
    吸収するための正規化ロジックの単一ソース。

    **単一 chokepoint ではない**（#297 fixup）: ``append_entry`` はこの関数を書込直前に
    必ず呼ぶが、``optimize_history`` には ``append_entry`` を経由しない直接 writer が
    2つある（``fitness_evolution.record_evolve_diff_decision`` / ``optimize.py`` の
    ``save_history_entry`` — いずれも ``history_file`` を明示指定できる後方互換のため
    自前でファイルを開いて追記する）。これらは自分の書込直前でこの関数を明示的に呼ぶ
    **規約**で揃えている。呼び忘れると正規化を素通りするため、各 writer 側に
    「naive を返す clock でも aware UTC で永続化される」回帰テストを置いて検出する。

    - キー無し・None: 現在時刻の aware UTC を付与する
    - naive（tz 情報無し）文字列: ``datetime.astimezone()`` でシステムローカル時刻として
      解釈してから aware 化する。読み側 ``fleet.queue_verify._parse_iso`` と同じ解釈に
      揃えないと、書き込み時と読み込み時で別の tz 前提を使うことになり 9 時間ずれが
      別の形で再発するため、必ず同じ流儀（値は変えず tzinfo だけ付与）に合わせる
    - aware 文字列: instant は変えず UTC 表記に統一する（表記ゆれの解消のみ）
    - 文字列でない・パース不能: 変更しない（形式契約外のデータを壊さない。この関数は
      「aware UTC を保証する」わけではなく「有効な ISO8601 文字列のみ」正規化する）

    **既存データの限界**: naive で書かれた既存レコードは migration しない（ADR-031 の
    read/write 分離方針）。writer が書いた時点のローカル TZ と reader が動く TZ が
    一致する前提が残るため、別 TZ の機械が過去の naive レコードを読むと 9 時間ずれる
    可能性は本関数だけでは解消されない（新規データの混在を止めるのがこの修正の範囲）。
    """
    ts = entry.get("timestamp")
    if ts is None:
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        return
    if not isinstance(ts, str):
        return
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return
    if dt.tzinfo is None:
        dt = dt.astimezone()
    entry["timestamp"] = dt.astimezone(timezone.utc).isoformat()


def append_entry(entry: Dict[str, Any], slug: str) -> None:
    """slug の履歴に 1 レコード追記する（親ディレクトリは自動作成）。

    冪等性（同一 id の二重記録防止）は呼び出し側の責務。本関数は純 append だが、
    ``timestamp`` だけは writer 依存の tz 不統一を防ぐため ``normalize_entry_timestamp``
    で書き込み直前に正規化する（#297）。この経路を通らない直接 writer（history_file を
    自前で開く ``fitness_evolution.py`` / ``optimize.py``）は同関数を各自明示的に呼ぶ
    （単一 chokepoint ではなく規約ベースの統一。詳細は ``normalize_entry_timestamp``）。
    """
    normalize_entry_timestamp(entry)
    path = history_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── revert イベントと effective view（#402 PR-2 段階2・§1） ───────────────

# revert イベント（apply 実行の記録）は accept/reject entry とは別のレコード型。
# ``event_type`` で判別する。writer（apply engine）は段階3 で追加する — 本モジュールは
# schema 契約 + 純粋 fold ロジックのみを持つ。
REVERT_EVENT_TYPE = "revert"

# revert イベントの必須フィールド（設計正典 §1）。``scope``/``repo_id``/``relative_path`` は
# PR-1 の ``evolve_decision_ids._revert_generation_for_target`` が対象一致の判定に使う3つと
# フィールド名を揃えている（食い違わせないこと）。
REVERT_EVENT_REQUIRED_FIELDS: Tuple[str, ...] = (
    "event_type",  # 固定値 "revert"
    "reverted_entry_id",  # 畳む対象の accept entry ID
    "revert_event_id",  # deterministic（決定6 の冪等再実行の判定キー）
    "revert_generation",  # この revert 実行後の世代
    "scope",  # project/global（対象一致判定に必須）
    "repo_id",  # 対象一致判定に必須
    "relative_path",  # 対象一致判定に必須
    "timestamp",  # 表示・診断用
    "skill_name",  # 表示・診断用
)


def is_revert_event(rec: Dict[str, Any]) -> bool:
    """レコードが revert イベントか（``event_type == "revert"``）。"""
    return rec.get("event_type") == REVERT_EVENT_TYPE


def missing_revert_event_fields(rec: Dict[str, Any]) -> List[str]:
    """revert イベントの必須フィールドのうち record に無いものを返す（空なら完全）。

    段階3 の writer 実装時の契約テスト・診断用。値の型検査はしない（キーの有無のみ）。
    """
    return [field for field in REVERT_EVENT_REQUIRED_FIELDS if field not in rec]


def fold_effective(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """revert イベントを反映した effective view を計算する純粋関数（副作用ゼロ・I/O ゼロ）。

    出力契約（設計正典 §1・確定済み）:
      - revert イベントで畳まれた accept entry は出力から**除外**する（フラグを立てない）
      - revert イベント自体も出力に**含めない**（判断母集団ではない。revert の事実が要る
        reader は ``load_revert_events`` を使う）
      - 入力の並びを保った安定フィルタ（並び替えはしない）

    呼び出し側の責務: fold は集約済みレコード集合へ**1回だけ**適用すること
    （ファイル単位で個別に fold すると、accept が legacy 側・revert が canonical 側に
    分かれるケースで両者が出会わない。``_aliased_raw_records`` 参照）。
    """
    reverted_ids = {
        rec.get("reverted_entry_id")
        for rec in records
        if is_revert_event(rec) and rec.get("reverted_entry_id") is not None
    }
    return [
        rec
        for rec in records
        if not is_revert_event(rec) and rec.get("id") not in reverted_ids
    ]


def _aliased_raw_records(slug: str) -> List[Dict[str, Any]]:
    """slug の canonical family（PJ rename alias 全体）を data-dir × alias 全順序で集約する。

    ``load_effective_history`` / ``load_revert_events`` の入力生成専用（内部 helper）。
    集約順序は設計正典 §1「集約順序を以下に固定する」の全順序（将来 rename で slug 辞書順が
    崩れても壊れない）:

      1. ``pj_slug.canonical_pj_slug(slug)`` で canonical 化する
      2. ``pj_slug.pj_slug_aliases_for(canonical_slug)`` で同値 slug 集合を得る
      3. 各 alias × 全 data-dir のレコードを集約する
      4. dedup 優先順位は **data-dir が major・slug が minor** の全順序（先勝ち）:
         data-dir = canonical → ``iter_read_data_dirs`` の順、
         slug = canonical_slug → ``sorted(aliases - {canonical_slug})``
         （既存 ``load_raw_history`` の「候補列 canonical 先頭・先勝ち」契約を data-dir 軸で
         保つため。alias 表の SoT は ``pj_slug`` — 自前で alias 表を持たない）

    **worktree basename 由来 slug**（``detect_worktree_name_slugs`` が検出する ``agent-*`` /
    ``worktree-*`` 系）は alias 対象に含めない（``pj_slug.PJ_SLUG_ALIASES`` に元々含まれない
    ため何もしなくても除外される・診断のみ・revert 非対応）。

    **異なる canonical slug 間では畳まない**: ``pj_slug_aliases_for(canonical_slug)`` は
    その canonical slug に畳まれる旧名だけを返すため、他 PJ の canonical family を
    構造的に取り込まない。
    """
    from pj_slug import canonical_pj_slug, pj_slug_aliases_for
    from rl_common import iter_read_data_dirs

    canonical_slug = canonical_pj_slug(slug) or slug
    aliases = pj_slug_aliases_for(canonical_slug) or {canonical_slug}
    ordered_aliases = [canonical_slug] + sorted(aliases - {canonical_slug})

    return _merge_dedup(
        _read_jsonl(d / "optimize_history" / f"{_sanitize_slug(a)}.jsonl")
        for d in iter_read_data_dirs(HISTORY_ROOT.parent)
        for a in ordered_aliases
    )


def load_effective_history(slug: str) -> List[Dict[str, Any]]:
    """slug の canonical family（rename alias 込み）の effective view（判断母集団）。

    ``_aliased_raw_records``（alias 6段階集約）→ ``fold_effective``（1回適用）。
    revert 済み accept entry・revert イベント自体は出力に含まれない（§1 出力契約）。
    """
    return fold_effective(_aliased_raw_records(slug))


def load_revert_events(slug: str) -> List[Dict[str, Any]]:
    """slug の canonical family（rename alias 込み）の revert イベント（診断用）。

    revert の事実そのものが必要な reader が使う。``load_effective_history`` と同じ
    alias 6段階集約から抽出するため、集約結果に矛盾は生じない。
    """
    return [rec for rec in _aliased_raw_records(slug) if is_revert_event(rec)]
