"""usage.jsonl 読み込み + スキル使用集計。

audit パッケージから切り出された Usage モジュール。
- load_usage_data: usage.jsonl から直近N日のレコードを取得
- _is_openspec_skill / _is_plugin_skill: スキル名分類ヘルパー
- aggregate_usage: スキル使用回数（基本ツール除外、プラグイン除外オプション）
- aggregate_plugin_usage: プラグイン名で集計
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_classifier import BUILTIN_AGENT_NAMES

from .classification import classify_usage_skill
from .gstack import _is_gstack_skill


_BUILTIN_TOOLS = {f"Agent:{n}" for n in BUILTIN_AGENT_NAMES} | {"commit"}


def load_usage_data(
    days: int = 30,
    *,
    project_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """usage.jsonl から直近N日のデータを読み込む。

    Args:
        days: 直近何日分のデータを読み込むか。
        project_root: 指定時は該当プロジェクトのレコードのみ返す。
    """
    from telemetry_query import query_usage

    # DATA_DIR は audit パッケージ経由で取得（テストが audit.DATA_DIR を
    # mock.patch.object で差し替えるケースに追従するため遅延参照）
    from . import DATA_DIR as _DATA_DIR

    # usage.jsonl は hook（PostToolUse）が plugin-data dir に書く。tool 実行時は
    # env 未設定で _DATA_DIR が fallback に解決され live テレメトリを取り逃すため、
    # 従来は hook-writer 系 resolver で単一 dir を正準解決していた（#358）。
    #
    # #45 ① read 統一（ADR-049）: DATA_DIR 断片化（canonical / legacy rename /
    # plugins-data hook split）の移行期は usage.jsonl が複数 dir に分裂しており、
    # 単一 dir では母集団を取り逃す（実測: canonical 54KB に対し legacy 1.4MB）。
    # iter_read_data_dirs で全候補 dir を cross-dir union read する（hook_store_path の
    # 単一 dir probe を包含・strictly more）。append-only の event log で同一レコードが
    # 複数 dir に重複しないため dedup なしの concat で正しい。cold-path（audit/prune の
    # 集計）専用。write 統一（#55）+ merge（#46）後は canonical 1 つに収束する。
    from rl_common import iter_read_data_dirs
    from pj_slug import pj_slug_aliases_for

    project_name = project_root.name if project_root else None
    include_unknown = project_root is None
    # #45/#47 ① read 統一: 当 PJ が rename されている場合、旧 slug でタグ付けされた legacy
    # も同一 PJ として含める（read 専用別名）。rename されていない PJ は {project_name} のみ
    # なので他 PJ は現状維持。project_name=None（全 PJ）は project フィルタなし（[None]）。
    accept_slugs = sorted(pj_slug_aliases_for(project_name)) if project_name else [None]
    records: List[Dict[str, Any]] = []
    for d in iter_read_data_dirs(_DATA_DIR):
        usage_file = d / "usage.jsonl"
        for idx, slug in enumerate(accept_slugs):
            # include_unknown（project 未帰属レコードの救済）は最初の slug の1回だけ。
            # 残り別名は False で引き、unknown の重複 pull を防ぐ。
            iu = include_unknown if idx == 0 else False
            records.extend(
                query_usage(
                    project=slug,
                    include_unknown=iu,
                    usage_file=usage_file,
                )
            )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [r for r in records if (r.get("ts") or r.get("timestamp") or "") >= cutoff]


def _is_openspec_skill(skill_name: str) -> bool:
    """スキル名が OpenSpec 関連（レガシー）かどうかを判定する。"""
    if not skill_name:
        return False
    name_lower = skill_name.lower()
    base = name_lower[6:] if name_lower.startswith("agent:") else name_lower
    return "openspec" in base or base.startswith("opsx:")


def _is_plugin_skill(skill_name: str) -> bool:
    """スキル名がプラグイン由来かどうかを判定する。

    classify_usage_skill（完全一致 + prefix マッチ）、_is_gstack_skill、
    _is_openspec_skill（レガシー）を併用。
    """
    if classify_usage_skill(skill_name) is not None:
        return True
    if _is_gstack_skill(skill_name):
        return True
    if _is_openspec_skill(skill_name):
        return True
    return False


def aggregate_usage(
    records: List[Dict[str, Any]],
    exclude_plugins: bool = False,
) -> Dict[str, int]:
    """スキル使用回数を集計する。基本ツールはノイズのため除外。

    Args:
        records: usage レコードのリスト
        exclude_plugins: True の場合、プラグインスキルを除外して PJ 固有のみ返す
    """
    counts: Dict[str, int] = {}
    for rec in records:
        # implement 等は skill フィールドで自己報告するため skill_name → skill の順でフォールバック
        skill = rec.get("skill_name") or rec.get("skill") or "unknown"
        if skill in _BUILTIN_TOOLS:
            continue
        if exclude_plugins and _is_plugin_skill(skill):
            continue
        counts[skill] = counts.get(skill, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))


def aggregate_contribution_scores(
    records: List[Dict[str, Any]],
    min_invocations: int = 3,
) -> Dict[str, Dict[str, Any]]:
    """スキル別の貢献スコアを算出する。

    outcome フィールドを持つレコードのみを集計対象とする。
    invocations が min_invocations 未満のスキルは score=None（データ不足）とする。

    Returns:
        {skill_name: {"score": float|None, "success": int, "error": int, "total": int}}
    """
    buckets: Dict[str, Dict[str, int]] = {}
    for rec in records:
        skill = rec.get("skill_name", "")
        outcome = rec.get("outcome")
        if not skill or outcome not in ("success", "error", "skip"):
            continue
        if skill in _BUILTIN_TOOLS:
            continue
        b = buckets.setdefault(skill, {"success": 0, "error": 0, "skip": 0})
        b[outcome] = b.get(outcome, 0) + 1

    result: Dict[str, Dict[str, Any]] = {}
    for skill, b in buckets.items():
        total = b["success"] + b["error"] + b.get("skip", 0)
        score: float | None = None
        if total >= min_invocations:
            score = b["success"] / total if total > 0 else None
        result[skill] = {
            "score": score,
            "success": b["success"],
            "error": b["error"],
            "total": total,
        }
    return result


def compute_negative_transfer(
    usage_data: List[Dict[str, Any]],
    delta_threshold: float = -0.05,
    window: int = 10,
) -> List[Dict[str, Any]]:
    """スキル追加前後の成功率 delta を計算して負の転移を検出する。

    arXiv 2605.23899 の知見: スキル追加が他スキルのパフォーマンスを下げる
    「負の転移」を測定する。

    実際の usage.jsonl スキーマ（{skill_name, ts, session_id, outcome}）に対応。

    アルゴリズム:
    - 「スキル追加」= usage_data に初めて登場するスキル名の最初のレコード
    - 「fitness_score」= outcome=="success" の比率（rolling window）で代替
    - before_rate: スキル追加前 window 件の success 率
    - after_rate: スキル追加後 window 件の success 率
    - delta = after_rate - before_rate < delta_threshold → negative_transfer フラグ

    Args:
        usage_data: usage.jsonl 由来のレコードリスト（{skill_name, ts, outcome, ...}）
        delta_threshold: 負の転移と判定するスコア変化の閾値（デフォルト -0.05）
        window: before/after それぞれ最大 N 件のレコードを使用

    Returns:
        [{"skill_name": str, "delta_score": float,
          "negative_transfer": bool, "before_score": float, "after_score": float}]

    エッジケース:
    - usage_data が空 → []
    - スキルが1件のみ（追加イベントなし）→ []
    - before/after データが不足（window 内のレコードなし）→ skip
    """
    if not usage_data:
        return []

    def _get_ts(rec: Dict[str, Any]) -> str:
        """ts または timestamp フィールドを取得する。"""
        return rec.get("ts") or rec.get("timestamp") or ""

    # タイムスタンプでソート
    sorted_data = sorted(
        [r for r in usage_data if _get_ts(r)],
        key=_get_ts,
    )

    if not sorted_data:
        return []

    # 各スキルの「初回登場タイムスタンプ」を収集（スキル追加の代理）
    skill_first_seen: Dict[str, str] = {}
    for rec in sorted_data:
        skill = rec.get("skill_name", "")
        if not skill:
            continue
        if skill not in skill_first_seen:
            skill_first_seen[skill] = _get_ts(rec)

    # スキルが1種類以下（新規追加スキルを検出できない）
    if len(skill_first_seen) < 2:
        return []

    # 最初に登場したスキル（既存スキルの基準）と、後から登場したスキル（新規追加）を分離
    # 最も早い登場時刻を「既存環境の基準点」とする
    sorted_skills_by_ts = sorted(skill_first_seen.items(), key=lambda x: x[1])
    baseline_ts = sorted_skills_by_ts[0][1]  # 最初のスキルの登場時刻

    # 2番目以降に登場したスキルをスキル追加イベントとして扱う
    # 追加スキルの中で最初に登場したものを「転移点」とする
    skill_added_ts: Optional[str] = None
    for skill, ts in sorted_skills_by_ts[1:]:
        if ts > baseline_ts:
            skill_added_ts = ts
            break

    if skill_added_ts is None:
        return []

    # skill_added_ts の前後で既存スキルの success 率を比較
    # 「既存スキル」= skill_added_ts より前に初回登場したスキル
    existing_skills = {skill for skill, ts in skill_first_seen.items() if ts < skill_added_ts}

    before_records: Dict[str, List[str]] = {}
    after_records: Dict[str, List[str]] = {}

    for rec in sorted_data:
        skill = rec.get("skill_name", "")
        outcome = rec.get("outcome")
        ts = _get_ts(rec)
        if not skill or outcome not in ("success", "error") or not ts:
            continue
        if skill not in existing_skills:
            continue
        if ts < skill_added_ts:
            before_records.setdefault(skill, []).append(outcome)
        elif ts > skill_added_ts:
            after_records.setdefault(skill, []).append(outcome)

    # 前後両方データがあるスキルのみ delta を計算
    results: List[Dict[str, Any]] = []
    all_skills = set(before_records.keys()) & set(after_records.keys())
    for skill in sorted(all_skills):
        before_window = before_records[skill][-window:]
        after_window = after_records[skill][:window]
        if not before_window or not after_window:
            continue
        before_rate = sum(1 for o in before_window if o == "success") / len(before_window)
        after_rate = sum(1 for o in after_window if o == "success") / len(after_window)
        delta = after_rate - before_rate
        results.append({
            "skill_name": skill,
            "delta_score": delta,
            "negative_transfer": delta < delta_threshold,
            "before_score": before_rate,
            "after_score": after_rate,
        })

    return results


def compute_component_transfer(
    usage_data: List[Dict[str, Any]],
    delta_threshold: float = -0.05,
    window: int = 10,
) -> List[Dict[str, Any]]:
    """更新コンポーネント（追加スキル）別に既存スキルの成功率 delta を分離して算出する。

    arXiv 2605.30621「Harness Updating Is Not Harness Benefit」の ablation 視点:
    compute_negative_transfer() は最初の追加スキル 1 点だけを転移点とし、after を
    データ終端まで取るため、複数の更新が混ざって「どの更新が効いたのか」を分離できない
    （ある時点で何かが起きた、までしか言えない）。本関数は各追加スキルを 1 つの更新
    コンポーネントとみなし、隣接する追加イベントで before/after を区切る isolation
    window で各コンポーネントの寄与を分離する。

    各コンポーネント c（追加ts=t_c）について:
    - before 区間 = [前コンポーネントの追加ts（無ければ baseline_ts）, t_c)
    - after 区間  = [t_c, 次コンポーネントの追加ts（無ければ +∞))
      → after_i と before_{i+1} は同一区間（更新 i の後 = 更新 i+1 の前）。これにより
        更新 i+1 で起きた回帰が更新 i に誤帰属しない。
    - 既存スキル = first_seen < t_c のスキル（先に追加された他コンポーネントも含む）
    各既存スキルの before/after success 率を区間内で算出（各 window 件まで）、
    net_delta = 影響を受けた既存スキルの delta 平均。

    Args:
        usage_data: usage.jsonl 由来のレコードリスト（{skill_name, ts, outcome, ...}）
        delta_threshold: 負の転移と判定する net_delta の閾値（デフォルト -0.05）
        window: before/after それぞれ最大 N 件のレコードを使用

    Returns:
        [{
          "component": str, "added_ts": str,
          "net_delta": float, "negative_transfer": bool,
          "affected": [{"skill_name", "delta_score", "before_score",
                        "after_score", "negative_transfer"}],
        }]  added_ts 昇順、affected を 1 件以上持つコンポーネントのみ。

    エッジケース:
    - usage_data が空 / 全件 ts 無し → []
    - スキルが1種類以下（追加イベントなし）→ []
    - before/after データ不足のコンポーネント → 除外
    """
    if not usage_data:
        return []

    def _get_ts(rec: Dict[str, Any]) -> str:
        return rec.get("ts") or rec.get("timestamp") or ""

    sorted_data = sorted(
        [r for r in usage_data if _get_ts(r)],
        key=_get_ts,
    )
    if not sorted_data:
        return []

    skill_first_seen: Dict[str, str] = {}
    for rec in sorted_data:
        skill = rec.get("skill_name", "")
        if skill and skill not in skill_first_seen:
            skill_first_seen[skill] = _get_ts(rec)

    if len(skill_first_seen) < 2:
        return []

    baseline_ts = min(skill_first_seen.values())
    # 更新コンポーネント = baseline より後に初回登場したスキル（追加ts 昇順）
    components = sorted(
        [(s, t) for s, t in skill_first_seen.items() if t > baseline_ts],
        key=lambda x: x[1],
    )
    if not components:
        return []

    comp_ts = [t for _, t in components]

    results: List[Dict[str, Any]] = []
    for idx, (comp_name, t_c) in enumerate(components):
        before_lo = comp_ts[idx - 1] if idx > 0 else baseline_ts
        after_hi = comp_ts[idx + 1] if idx + 1 < len(components) else None
        existing = {s for s, t in skill_first_seen.items() if t < t_c}

        before_rec: Dict[str, List[str]] = {}
        after_rec: Dict[str, List[str]] = {}
        for rec in sorted_data:
            skill = rec.get("skill_name", "")
            outcome = rec.get("outcome")
            ts = _get_ts(rec)
            if not skill or outcome not in ("success", "error") or not ts:
                continue
            if skill not in existing:
                continue
            if before_lo <= ts < t_c:
                before_rec.setdefault(skill, []).append(outcome)
            elif ts >= t_c and (after_hi is None or ts < after_hi):
                after_rec.setdefault(skill, []).append(outcome)

        affected: List[Dict[str, Any]] = []
        for skill in sorted(set(before_rec) & set(after_rec)):
            before_window = before_rec[skill][-window:]
            after_window = after_rec[skill][:window]
            if not before_window or not after_window:
                continue
            before_rate = sum(1 for o in before_window if o == "success") / len(before_window)
            after_rate = sum(1 for o in after_window if o == "success") / len(after_window)
            delta = after_rate - before_rate
            affected.append({
                "skill_name": skill,
                "delta_score": delta,
                "before_score": before_rate,
                "after_score": after_rate,
                "negative_transfer": delta < delta_threshold,
            })

        if not affected:
            continue
        net_delta = sum(a["delta_score"] for a in affected) / len(affected)
        results.append({
            "component": comp_name,
            "added_ts": t_c,
            "net_delta": net_delta,
            "negative_transfer": net_delta < delta_threshold,
            "affected": affected,
        })

    return results


def compute_paired_trajectory(
    *,
    usage: List[Dict[str, Any]],
    sessions: List[Dict[str, Any]],
    min_per_arm: int = 1,
    delta_threshold: float = -0.05,
) -> List[Dict[str, Any]]:
    """paired trajectory auditing（観測版）— 同一タスク種別で skill 有/無の挙動を対照する。

    SkillAudit (arXiv 2606.14239) は同一タスクをスキル有/無で**能動再実行**して挙動軌跡の
    差分を診断信号にする。rl-anything は受動観測（再実行しない）設計なので、本関数は既存
    テレメトリ（usage + sessions）から「同一 task-type のセッションで対象スキルが使われた群
    vs 使われなかった群」を準実験的に拾い、挙動メトリクスのデルタを算出する観測版である（#15）。

    compute_component_transfer / compute_negative_transfer との違い（流用でなく新規）:
      - それらは「スキル追加の**時系列前後**」での既存スキル success デルタ（準実験だが時間軸）。
      - 本関数は「同一 task-type 内での skill **有/無**の対照」（横断・同時点の対照）。
        SkillAudit の paired 対照に対応するのはこちら。

    task-type の定義（決定論・LLM 非依存）:
      1 セッションの task-type key = そのセッションで呼ばれたスキル集合から**対象スキルを
      除いた残り**（= タスク文脈）。同じ context-skill-set を持つセッションを「同一タスク種別」
      とみなす。空 context（対象スキル単独セッション）は文脈不明として除外する。

    挙動メトリクス: 一発成功率 = error_count==0 のセッション割合（outcome_attribution と同義）。
    error_count 欠損セッションは分母から除外する（None 比較落ち pitfall 回避）。

    paired 成立条件: ある task-type バケットで with 腕・without 腕の双方に min_per_arm 以上の
    有効セッション（error_count を持つ）があること。両腕が揃ったバケットのみ delta を計上する。

    Args:
        usage: usage.jsonl 相当（skill/skill_name → session_id）。
        sessions: sessions 相当（session_id → error_count）。
        min_per_arm: paired 成立に必要な各腕の最小有効セッション数。
        delta_threshold: behavior_delta がこの値未満なら regression=True（挙動悪化兆候）。

    Returns:
        対象スキルごとに集約した
        [{"skill", "behavior_delta", "with_success", "without_success",
          "n_with", "n_without", "paired_task_types", "regression"}]
        behavior_delta = with_success - without_success（高いほどスキルが挙動を改善）。
        paired バケットが 1 つも無いスキルは除外。behavior_delta 昇順（悪い順）→ skill 名。

    エッジケース:
    - usage / sessions が空 → []
    - 対象スキルが全 task-type で常に存在（without 腕なし）→ そのスキルは除外
    """
    if not usage or not sessions:
        return []

    # session_id → そのセッションで呼ばれたスキル集合。
    skills_by_session: Dict[str, set] = {}
    for rec in usage:
        skill = rec.get("skill_name") or rec.get("skill") or ""
        sid = rec.get("session_id") or ""
        if not skill or sid_excluded(skill) or not sid:
            continue
        skills_by_session.setdefault(sid, set()).add(skill)

    # session_id → error_count（欠損は None）。
    err_by_session: Dict[str, Optional[int]] = {}
    for s in sessions:
        sid = s.get("session_id") or ""
        if sid:
            err_by_session[sid] = s.get("error_count")

    # 観測対象スキル = usage に現れた全スキル。
    all_skills = {sk for sks in skills_by_session.values() for sk in sks}

    results: List[Dict[str, Any]] = []
    for target in sorted(all_skills):
        # target ごとに task-type（= target を除いた context-skill-set）でバケット化し、
        # 各バケットを with/without 腕に分けて有効セッション（error_count あり）を集める。
        buckets: Dict[frozenset, Dict[str, List[int]]] = {}
        for sid, sks in skills_by_session.items():
            context = frozenset(sks - {target})
            if not context:
                continue  # 文脈不明（対象スキル単独）は除外。
            err = err_by_session.get(sid)
            if err is None:
                continue  # 挙動メトリクス算出不能 → 分母から除外。
            arm = "with" if target in sks else "without"
            bucket = buckets.setdefault(context, {"with": [], "without": []})
            bucket[arm].append(err)

        with_clean = with_total = 0
        without_clean = without_total = 0
        paired = 0
        for ctx, arms in buckets.items():
            w, wo = arms["with"], arms["without"]
            if len(w) < min_per_arm or len(wo) < min_per_arm:
                continue  # 片腕しかない / 件数不足 → paired 不成立。
            paired += 1
            with_clean += sum(1 for e in w if e == 0)
            with_total += len(w)
            without_clean += sum(1 for e in wo if e == 0)
            without_total += len(wo)

        if paired == 0:
            continue

        with_success = with_clean / with_total if with_total else 0.0
        without_success = without_clean / without_total if without_total else 0.0
        delta = with_success - without_success
        results.append({
            "skill": target,
            "behavior_delta": delta,
            "with_success": with_success,
            "without_success": without_success,
            "n_with": with_total,
            "n_without": without_total,
            "paired_task_types": paired,
            "regression": delta < delta_threshold,
        })

    results.sort(key=lambda r: (r["behavior_delta"], r["skill"]))
    return results


def sid_excluded(skill: str) -> bool:
    """task-type 文脈の組み立てから除外すべきノイズスキルか（基本ツール）。"""
    return skill in _BUILTIN_TOOLS


def aggregate_plugin_usage(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """プラグイン別の使用回数を集計する。

    classify_usage_skill でプラグイン名が判定できるものはプラグイン名で集計。
    gstack スキルは "gstack" として、OpenSpec レガシーは "openspec(legacy)" として集計。

    Returns:
        {plugin_name: total_count} の辞書（降順ソート）
    """
    plugin_counts: Dict[str, int] = {}
    for rec in records:
        skill = rec.get("skill_name", "unknown")
        if skill in _BUILTIN_TOOLS:
            continue
        plugin_name = classify_usage_skill(skill)
        if plugin_name:
            plugin_counts[plugin_name] = plugin_counts.get(plugin_name, 0) + 1
        elif _is_gstack_skill(skill):
            key = "gstack"
            plugin_counts[key] = plugin_counts.get(key, 0) + 1
        elif _is_openspec_skill(skill):
            key = "openspec(legacy)"
            plugin_counts[key] = plugin_counts.get(key, 0) + 1
    return dict(sorted(plugin_counts.items(), key=lambda x: x[1], reverse=True))
