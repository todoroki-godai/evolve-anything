"""Correction capture 率の observability セクション生成（#421）。

RL ループの報酬入力（corrections）が枯渇していないかを surface する。capture 率が低いとき、
それが「検出器の仕様通りの少なさ」なのか「capture 漏れ」なのかを人が判別できるよう、
分母（active session）と分子（correction を持つ session）を併記する。

**advisory のみ**: スコア重みには入れない（壊れた入力の上に重みを作らない、#421）。
observability contract から参照される `build_*_section` 契約
（`(project_dir) -> Optional[List[str]]`）は他 builder と同一。決定論・LLM 非依存。
"""
from pathlib import Path
from typing import List, Optional, Tuple

# capture 率が「枯渇」とみなされる閾値（advisory のしきい値で、スコアには影響しない）。
_STARVATION_THRESHOLD = 0.10


def _llm_judge_count(project_dir: Optional[Path] = None) -> int:
    """**当 PJ slug の** weak_signals llm_judge channel 件数を返す（#476-1 / #476 fixup）。

    capture_rate は hook が書く corrections.jsonl のみを分子にするが、correction の
    意味判定は weak_signals レーンの llm_judge channel に隔離される（#431）。hook capture
    が 0% でも llm_judge が大量捕捉していれば「報酬入力枯渇」は誤警告。channel 別表示で
    実態（hook N / llm_judge M）を併記し、llm_judge があれば枯渇判定を抑制する。

    **slug スコープ必須（全PJ共通 DATA_DIR pitfall）**: weak_signals.jsonl は全 PJ 共通
    ストアなので、PJ フィルタなしで数えると hook N（当PJ window 集計）と桁が混在し、さらに
    他 PJ の llm_judge シグナルが当 PJ の枯渇警告を誤って抑制する。

    **#492: 書込側と同じ slug 関数に揃える。** weak_signals の llm_judge channel は
    evolve.py が ``_resolve_pj_slug``（= ``pj_slug_fast`` / ``utterance_archive.pj_slug_from_cwd``
    の文字列方式）で書く。読み側がここで git-common-dir 方式（旧 ``resolve_slug``）かつ
    引数なし=cwd で導出すると、評価が ``project_dir != cwd`` や worktree から起動された
    場合に書込 slug と食い違い、当 PJ の llm_judge を 0 と誤読する（read/write split-brain）。
    ``project_dir`` を引数で受け、``pj_slug_fast`` で書込側と同一の slug に揃える。

    store / slug 未解決 / 読込失敗は 0（防御的・沈黙でなく従来挙動へフォールバック）。

    **#94（codex round2 [Must]）: この関数は表示専用の raw 値**。promoted も bootstrap
    消化除外も適用しない（「捕捉済み総数」という観測値の意味を変えないため意図的に raw）。
    「未昇格の llm_judge シグナルは...昇格可能」という actionable な案内の分岐条件には
    **絶対にこの raw 値を使わない**こと — 全件 promoted 済み・全件 bootstrap 消化済みでも
    raw は非ゼロのままなので、案内の誤表示を招く（実データで確認済み: 全PJ合計 313 件中
    123 件が promoted 済みで raw に混入していた）。案内の判定には
    ``_llm_judge_actionable_count`` を使う。
    """
    try:
        from weak_signals.store import read_signals
    except ImportError:
        return 0
    try:
        from pj_slug import pj_slug_fast
        slug = pj_slug_fast(project_dir if project_dir is not None else Path.cwd())
    except Exception:
        slug = None
    if not slug:
        return 0
    try:
        from store_read_union import pj_slug_match
        return sum(
            1
            for r in read_signals()
            if r.get("channel") == "llm_judge" and pj_slug_match(r.get("pj_slug"), slug)
        )
    except Exception:
        return 0


def _llm_judge_actionable_count(project_dir: Optional[Path] = None) -> int:
    """当PJ slug の llm_judge channel のうち **actionable** 件数。

    #94（codex round2 [Must]）是正: ``build_capture_rate_section`` は表示用の raw 値
    （``_llm_judge_count``・promoted 含む・除外なし）を「未昇格の llm_judge シグナルは...
    今日の修正確認 phase で昇格可能」という **actionable な案内**の分岐条件に誤って
    使っていた。全件 promoted 済み・全件 bootstrap marker 以前 detected（= queue_materials /
    daily_review / sections_weak_signals が既に「判断済み」として除外している状態）でも
    raw は非ゼロのため、案内が実態と食い違って表示され続ける（4つ目の reader 非対称）。

    #405 round4 [Must]1 是正: 除外軸が promoted + bootstrap 消化済みだけで、TTL 失効（#89）・
    既読/却下済み（#185）が欠けていた（queue_materials / daily_review は両方適用済み）。
    全 actionable reader の単一 predicate（``correction_semantic.promote.filter_actionable``）
    に揃え、4軸（promoted / TTL失効 / 既読・却下済み / bootstrap消化済み）を通す。

    store / slug 未解決 / 読込失敗は 0（防御的フォールバック）。この戻り値は「昇格可能」の
    案内を出すか否かにのみ使われ、誤って案内を出す（判断済みの項目を再提示する）方が
    誤って案内を出さない（1日待てば次回出る）より害が大きいため、失敗時は安全側（0）。
    """
    try:
        from pj_slug import pj_slug_fast
        slug = pj_slug_fast(project_dir if project_dir is not None else Path.cwd())
    except Exception:
        slug = None
    if not slug:
        return 0
    try:
        from weak_signals.store import read_signals
        from store_read_union import pj_slug_match
        from correction_semantic.promote import filter_actionable

        records = [
            r
            for r in read_signals()
            if r.get("channel") == "llm_judge" and pj_slug_match(r.get("pj_slug"), slug)
        ]
        candidates = filter_actionable(records, slug)
    except Exception:
        return 0
    return len(candidates)


def _resolve_store_files() -> Tuple[Path, Path]:
    """usage.jsonl / corrections.jsonl の正準パスを解決する（#358 hook-writer 系）。

    audit.DATA_DIR を base に hook_store_path で解決し、tool 文脈でも hook が書いた
    plugin-data dir を回収する。テストはこの関数を patch して tmp store に向ける。
    """
    from rl_common import hook_store_path

    from . import DATA_DIR as _DATA_DIR

    return (
        hook_store_path("usage.jsonl", base=_DATA_DIR),
        hook_store_path("corrections.jsonl", base=_DATA_DIR),
    )


def build_capture_rate_section(project_dir: Path) -> Optional[List[str]]:
    """correction capture 率を audit に surface する（#421）。

    capture 率 = 「min_turns 以上のターンを持つセッション（usage 行数 proxy）」のうち
    「correction を 1 件以上検出したセッション」の割合。usage/corrections は全PJ共通
    ストアだが、#489 で当PJスコープに直した（project_dir の basename を当PJ識別子として
    渡す）。これで同セクションの llm_judge 行（元から当PJ限定）とスコープが揃う。

    観測可能性:
    - active session が 0（テレメトリ未蓄積 / 長セッション無し）→ None（対象外で沈黙）
    - active session があり capture 率が閾値以上 → 「評価したが枯渇兆候なし ✓」
      （silence != evaluated。値は低い少なさが仕様か漏れか判別できるよう常に併記）
    - 閾値未満 → ⚠ で starvation を surface。分母/分子を併記し、検出器仕様か漏れかの
      判別材料を残す（hook 有用性評価 #318 の follow-through）。
    """
    try:
        import capture_rate
    except ImportError:
        return None

    try:
        usage_file, corrections_file = _resolve_store_files()
        # #489: 当PJスコープに直す。usage/corrections は全PJ共通ストアなので、
        # project_dir を worktree 安全 slug に正規化して当PJ識別子として渡し、他PJの
        # active/captured を当PJレポートに無ラベル混入させない（llm_judge 行は元から
        # 当PJ限定なので併置のスコープ不一致も解消される）。正規化は capture_rate._normalize_pj
        # （= utterance_archive.pj_slug_from_cwd）に寄せ、本体⇔worktree の取りこぼしを防ぐ。
        result = capture_rate.compute_capture_rate(
            usage_file=usage_file,
            corrections_file=corrections_file,
            project=capture_rate._normalize_pj(str(project_dir)),
        )
    except Exception:
        return None

    if not result.get("applicable"):
        return None  # active session なし → テレメトリ未蓄積で対象外

    active = result["active_sessions"]
    captured = result["captured_sessions"]
    rate = result["capture_rate"]
    min_turns = result["min_turns"]
    days = result["days"]

    # #476-1: capture を channel 別に表示する。hook 系（corrections.jsonl）は capture_rate の
    # 分子、意味判定系（weak_signals の llm_judge channel）は別レーン。両方を併記して
    # 「hook 0% だが llm_judge が大量捕捉」の実態を可視化する。
    # #492: project_dir を渡し、weak_signals 書込側（pj_slug_fast）と同じ slug で照合する。
    # 表示用（raw・promoted 含む・bootstrap 除外なし＝観測値としての捕捉総数）と
    # 判定用（actionable・未昇格 + bootstrap 消化除外後）を別変数で明確に区別する
    # （#94 codex round2 [Must]・raw 値を actionable な案内の分岐条件に使わない）。
    llm_judge = _llm_judge_count(project_dir)
    llm_judge_actionable = _llm_judge_actionable_count(project_dir)

    header = ["## Correction Capture (報酬入力の捕捉率)", ""]
    detail = (
        f"（当PJ・直近 {days} 日 / {min_turns}+ ターンのセッション {active} 件中 "
        f"{captured} 件で correction を検出）"
    )
    channel_line = (
        f"channel 別: hook {captured} 件（capture 率 {rate:.0%}）/ "
        f"llm_judge {llm_judge} 件（当PJ・weak_signals レーン・昇格前）"
    )
    # #305/#323: 過去 backfill の自己注入 correction（Stop hook feedback 文）を read 時に
    # 分子から除いた件数。0 件なら黙る（silence != evaluated は上の channel_line が担う）。
    machinery_excluded = result.get("machinery_excluded", 0)
    if machinery_excluded:
        channel_line += (
            f" / 自己注入除外 {machinery_excluded} 件"
            "（Stop hook feedback 由来・read 時除外・ストアは不変, #305）"
        )

    if rate >= _STARVATION_THRESHOLD:
        return header + [
            f"✓ 評価したが枯渇兆候なし: capture 率 {rate:.0%} {detail}",
            channel_line,
            "",
        ]

    # #476-1: hook capture が低くても llm_judge が捕捉していれば「枯渇」ではない。
    # 誤警告を避け、weak_signals → 昇格フローへ誘導する。
    # #141-7b: この分岐は「実質的所見あり（未昇格シグナルが溜まっている）」なので ℹ を付け
    # observability の watch（観察中）に載せる。マーカーが無いと classify_section が clean と
    # 誤判定し『✓ 評価済みクリーン』へ畳まれ、ラベルと中身が矛盾していた。
    if llm_judge > 0:
        lines = header + [
            f"ℹ hook 経由の capture 率は低い（{rate:.0%}）が、意味判定レーン（llm_judge）で "
            f"{llm_judge} 件捕捉済み。{channel_line}",
        ]
        # #94（codex round2 [Must]）: 「昇格可能」の案内は actionable（未昇格 + bootstrap
        # 消化除外後）が 1 件以上あるときだけ出す。raw（llm_judge）が非ゼロでも全件 promoted
        # 済み・全件 bootstrap で判断済みなら実際には昇格対象が無いため、案内を省く。
        # capture 自体は起きているので枯渇警告（⚠ 分岐）へは落とさない（#476-1 の意図を保つ）。
        if llm_judge_actionable > 0:
            lines.append(
                "未昇格の llm_judge シグナルは `/evolve-anything:evolve` の今日の修正確認 phase で昇格可能"
                "（報酬入力は枯渇していない・advisory・スコア非関与, #421/#476）。"
            )
        lines.append("")
        return lines

    # #52-6: 具体手順を番号付きで添える（何をどの順で確認すればよいかを明示）。
    # #48-F3: 「次の一手」の前に、低 capture を生む決定論的な原因の当たりを付けられる
    # よう「考えられる原因」を併記する。0% は「件数が少ないだけ（仕様通り）」のほかに
    # (1) correction_detect hook の未登録/未発火（corrections.jsonl が育たない）、
    # (2) worktree slug 食い違い（corrections が幻 slug に書かれ当PJで 0 に見える
    #     read/write split-brain, #492/#593）が起きやすい。原因の切り分けを先にすると
    # 「件数が少ないだけ」と「capture 漏れ」の取り違えを減らせる（hook 有用性評価 #318）。
    return header + [
        f"⚠ Claude への修正指示の記録率が低い: {rate:.0%} {detail}。"
        "修正フィードバック（corrections）がほとんど貯まっておらず枯渇している可能性があります。"
        "仕様どおり少ないだけか記録漏れかを `corrections.jsonl` の中身で確認し、漏れなら "
        "`correction_detect` hook（修正を検出して記録する仕組み）の発火条件を見直してください"
        "（参考情報・スコアには影響しません, #421）。",
        channel_line,
        "考えられる原因: a) `correction_detect` hook が未登録/未発火 — "
        "`claude hooks list` で登録を確認（未登録なら corrections.jsonl が育たない） / "
        "b) worktree slug 食い違い — 当PJを worktree から起動していると corrections が幻 slug に "
        "書かれ当PJ集計で 0 に見える（read/write split-brain, #492/#593）。"
        "別 slug の corrections.jsonl が無いか確認 / "
        "c) 単純に件数が少ないだけ（仕様通り）— 運用継続で蓄積を待つ。",
        "次の一手: 1) `wc -l <DATA_DIR>/corrections.jsonl` で件数を確認 → "
        "2) `/evolve-anything:reflect` で未処理修正を反映 → "
        "3) 件数が少ないだけなら数週間運用を継続して蓄積を待つ。",
        "",
    ]
