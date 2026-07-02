"""環境健全性 observability advisory セクション生成（#122 Phase 4 で sections.py から分離）。

evolve が消費する audit レポートに、手動確認に依存せず surface する advisory 群を集約する。
いずれも「アーティファクト駆動の適用判定 + silence != evaluated」の観測可能性契約に従い、
評価対象があれば該当なしでも「✓ 評価したが該当なし」を 1 行残す。
- build_unmanaged_pitfalls_section: 自動強制 未登録の育った pitfalls.md を可視化
- build_belief_blocks_section: belief_entropy ゲートが破棄した低信頼 memory 要約（#285）
- build_calibration_drift_section: fitness の score-acceptance 相関 drift（#286）
- build_negative_transfer_section: 更新コンポーネント別 negative transfer（#288）
- build_glossary_drift_section: CONTEXT.md（用語集）の drift / seed 提案（#275）

report-format 系（Constitutional / Token / Test Guard / LSP / Corrections）は sections.py に残置。
後方互換のため sections.py が本モジュールの builder / helper を re-export する。
"""
from pathlib import Path
from typing import List, Optional

from .advisory import build_advisory_section


_PITFALL_MIN_ENTRIES = 3  # この件数以上「育っている」pitfalls.md だけを advisory 対象にする


def _load_count_entries():
    """pitfall-curate の正準パーサから count_entries をロードする（sys.path 非汚染）。

    parse.py は skills/pitfall-curate/scripts/ にあり sys.path 外。かつ core/parse 等の
    generic 名を持つため、sys.path に足さず importlib でファイル指定ロードする。
    取得不能時は None（呼び出し側でセクション skip）。
    """
    try:
        import importlib.util

        from plugin_root import PLUGIN_ROOT

        parse_path = PLUGIN_ROOT / "skills" / "pitfall-curate" / "scripts" / "parse.py"
        if not parse_path.exists():
            return None
        spec = importlib.util.spec_from_file_location("_pitfall_curate_parse", parse_path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.count_entries
    except (ImportError, OSError, AttributeError):
        return None


def build_unmanaged_pitfalls_section(project_dir: Path) -> Optional[List[str]]:
    """自動強制（pitfall lint / commit-gate）の対象になり得るが未登録の pitfalls.md を可視化。

    install ≠ enforcement（オプトイン設計）のため、育っている pitfalls.md があっても
    enable しなければ hook は無反応。evolve は audit を消費するので、evolve のたびに
    「登録すべき pitfalls.md」が advisory として出る。実際の登録は pitfall-curate に誘導。

    観測可能性: pitfalls.md が 1 件でもある PJ では、該当なしでも「評価したが対象なし ✓」を
    必ず 1 行残す（沈黙だと「評価して該当なし」か「配線漏れ」か区別できないため。
    glossary drift と同じ方針）。pitfalls.md が 1 件も無い PJ のみ None（対象外）。
    advisory 対象は実エントリ >= _PITFALL_MIN_ENTRIES の「育っている」未登録ファイルのみ
    （空・書きかけはノイズ抑制で path を出さない）。
    """
    try:
        import pitfall_registry
    except ImportError:
        return None

    discovered = pitfall_registry.discover_pitfalls(project_dir)
    if not discovered:
        # pitfalls 運用のない PJ — 評価対象がそもそも無いので非表示
        return None

    candidates = pitfall_registry.unmanaged_candidates(project_dir)
    count_entries = _load_count_entries()

    header = ["## Unmanaged Pitfalls (自動強制 未登録)", ""]

    if count_entries is None:
        # 正準パーサをロードできない — liveness は判定できないが事実は残す
        if candidates:
            lines = header + [
                f"⚠ 未登録 pitfalls.md {len(candidates)} 件あり"
                "（エントリ数の liveness 判定不可・parser ロード失敗）:"
            ]
            lines += [f"  - {rel}" for rel in candidates]
        else:
            lines = header + [
                f"✓ 未登録の pitfalls.md なし（検査 {len(discovered)} 件すべて登録済み）"
            ]
        lines.append("")
        return lines

    live: List[tuple] = []
    for rel in candidates:
        p = project_dir / rel
        try:
            n = count_entries(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            # 読めない / 非 UTF-8 の 1 ファイルで全体を落とさない
            continue
        if n >= _PITFALL_MIN_ENTRIES:
            live.append((rel, n))

    if live:
        lines = header + [
            f"以下の pitfalls.md は育っています（エントリ {_PITFALL_MIN_ENTRIES}+ 件）が、"
            "自動強制ルールに未登録です。`/evolve-anything:pitfall-curate` で enable すると、"
            "編集/commit 時に正準フォーマットが自動で当たります:"
        ]
        lines += [f"  - {rel} ({n} entries)" for rel, n in live]
    elif not candidates:
        lines = header + [
            f"✓ enable すべき育った pitfalls.md なし（検査 {len(discovered)} 件すべて登録済み）"
        ]
    else:
        lines = header + [
            f"✓ enable すべき育った pitfalls.md なし"
            f"（検査 {len(discovered)} 件 / 未登録 {len(candidates)} 件はいずれもエントリ "
            f"{_PITFALL_MIN_ENTRIES} 件未満の書きかけ）"
        ]
    lines.append("")
    return lines


# belief block を「直近」とみなすウィンドウ（日数）
_BELIEF_BLOCKS_WINDOW_DAYS = 30


def build_belief_blocks_section(project_dir: Path) -> Optional[List[str]]:
    """belief_entropy 生成後ゲートが block した低信頼 memory 要約を可視化（#285）。

    auto_memory_runner は Stop hook 毎回 belief_entropy で要約の retention/drift を
    評価し、低信頼要約を書込前に破棄して belief_blocks.jsonl に記録する。evolve は
    audit を消費するので、evolve のたびに「直近どれだけ block したか」が surface される
    — 手動確認に依存しない配線。

    観測可能性: belief_blocks.jsonl が存在する（ゲートが一度でも稼働した）環境では、
    直近ウィンドウの block が 0 件でも「評価したが直近 block なし ✓」を 1 行残す
    （silence ≠ evaluated）。ログ自体が無い環境（gate 未稼働）は None（対象外）—
    pitfalls.md / CONTEXT.md 不在時と同じ「アーティファクト駆動」の適用判定。

    #115: header/trailer 規約は build_advisory_section に集約。import guard・ログ不在短絡は
    compute 内に残し、log が存在すれば（count==0 でも）評価対象 → applicable=True。
    """

    def compute(_proj: Path):
        try:
            import belief_entropy
        except ImportError:
            return None
        try:
            import rl_common
            data_dir = Path(rl_common.DATA_DIR)
        except Exception:
            return None
        # belief_blocks.jsonl が無い = ゲート未稼働 → 対象外（None）
        # #67: belief_blocks.jsonl は DATA_DIR 直下の全 PJ 共通ストア。PJ 別 audit でも横断集計。
        if not (data_dir / belief_entropy.BLOCKS_FILENAME).exists():
            return None
        try:
            return belief_entropy.summarize_blocks(
                data_dir, days=_BELIEF_BLOCKS_WINDOW_DAYS
            )
        except Exception:
            return None

    def render(data) -> List[str]:
        count, heads = data
        if count <= 0:
            return [
                f"✓ 評価したが直近 {_BELIEF_BLOCKS_WINDOW_DAYS} 日の block なし"
                "（auto-memory の要約はソース corrections を保持）",
            ]
        lines = [
            f"⚠ 直近 {_BELIEF_BLOCKS_WINDOW_DAYS} 日で {count} 件の低信頼要約を書込前に破棄"
            "（retention 低 or drift 過剰）。頻発する場合は corrections の質か要約プロンプトを点検:",
        ]
        lines += [f"  - {h}" for h in heads]
        return lines

    return build_advisory_section(
        project_dir,
        title="Belief Entropy Gate（全PJ横断・低信頼 memory ブロック）",
        compute=compute,
        applicable=lambda data: True,
        render=render,
    )


def _load_fitness_evolution():
    """evolve-fitness の fitness_evolution モジュールを遅延 import する。"""
    try:
        import fitness_evolution  # type: ignore
        return fitness_evolution
    except ImportError:
        import sys
        fe_dir = (
            Path(__file__).resolve().parents[3]
            / "skills" / "evolve-fitness" / "scripts"
        )
        if str(fe_dir) not in sys.path:
            sys.path.insert(0, str(fe_dir))
        try:
            import fitness_evolution  # type: ignore
            return fitness_evolution
        except Exception:
            return None


def build_calibration_drift_section(project_dir: Path) -> Optional[List[str]]:
    """fitness 評価関数の score-acceptance 相関 drift を surface（#286）。

    accept/reject（optimize/evolve の history.jsonl）から score と human_accepted の
    相関を fitness_func ごとに評価し、相関が CORRELATION_THRESHOLD を割った評価関数を
    「再 calibration 推奨」として advisory 提示する。evolve は audit を消費するので、
    evolve のたびに calibration drift が surface される — 手動の evolve-fitness 起動に
    依存しない配線（trigger_engine の proactive 提案と二段で効かせる）。

    全 fitness 変更は人間承認が MUST のため、本 section は advisory のみ（自動適用しない）。

    観測可能性:
    - accept/reject 履歴なし → None（対象外。belief_blocks と同じデータ駆動の適用判定）
    - 履歴ありだが < MIN_DATA_COUNT → 「評価したがデータ不足 N/30」（silence != evaluated）
    - 十分なデータで drift なし → 「評価したが drift なし ✓」
    - drift あり → ⚠ で対象 fitness_func と相関値、evolve-fitness 起動を提案
    """
    fe = _load_fitness_evolution()
    if fe is None:
        return None

    try:
        history = fe.load_history()
    except Exception:
        return None
    if not history:
        return None  # accept/reject 履歴なし → 対象外

    try:
        drift = fe.detect_drifted_funcs(history)  # trigger_engine と共有の単一ソース
    except Exception:
        return None

    header = ["## Fitness Calibration Drift (score-acceptance)", ""]
    valid_count = drift.get("valid_count", 0)
    min_count = fe.MIN_DATA_COUNT

    if not drift.get("sufficient"):
        # #479 item 3: fitness_evolution が insufficient_data + structural_reason を返す
        # （skill_evolve_not_scored = 提案が構造的に出ない PJ）場合、「あと N 件で判定可能」を
        # 蓄積前提の断定として単独で出すと fitness_evolution の next_action
        # （「fitness は使わない設計。対応不要」）と同一 run で矛盾する。母集団は『提案が出て
        # 初めて』accept/reject が発生して積み上がるため、構造的に対象外になり得る旨を明示し
        # 3 箇所（Step 2 / fitness_evolution / calibration_drift）の文言を統一する。
        structural = False
        try:
            fe_result = fe.run_fitness_evolution()
            # #479/#584/#105: 構造的スキップ判定は fitness_evolution.is_structural_skip に単一ソース化。
            # insufficient_data + structural_reason、または bootstrap（structural_reason を持たない契約
            # だが同じく提案が出て初めて積み上がる）を「構造的に対象外」として畳む。
            structural = fe.is_structural_skip(fe_result)
        except Exception:
            structural = False

        if structural:
            return header + [
                f"ℹ 評価したが accept/reject データ不足 {valid_count}/{min_count} 件 — "
                "calibration drift 判定は保留。"
                "母集団は『提案が出て初めて』accept/reject が発生して積み上がるため、"
                "skill 提案が構造的に出ない PJ では構造的に対象外（無理に貯める必要はない）。",
                "",
            ]
        return header + [
            f"ℹ 評価したが accept/reject データ不足 {valid_count}/{min_count} 件 — "
            f"calibration drift 判定は保留（あと {min_count - valid_count} 件）。",
            "",
        ]

    drifted = drift.get("drifted", [])
    if not drifted:
        return header + [
            f"✓ 評価したが calibration drift なし（{valid_count} 件で score-acceptance 相関良好）",
            "",
        ]

    lines = header + [
        "⚠ score-acceptance 相関が低下した fitness_func あり。"
        "`/evolve-anything:evolve-fitness` で再 calibration を検討（変更は人間承認 MUST）:",
    ]
    for d in drifted:
        corr = d.get("correlation")
        corr_str = f"{corr:.3f}" if isinstance(corr, (int, float)) else "n/a"
        lines.append(f"  - {d.get('func')}: 相関 {corr_str} (< {fe.CORRELATION_THRESHOLD})")
    lines.append("")
    return lines


_NEG_TRANSFER_WINDOW_DAYS = 30


def build_negative_transfer_section(project_dir: Path) -> Optional[List[str]]:
    """更新コンポーネント（追加スキル）別の negative transfer を surface（#288）。

    arXiv 2605.30621「Harness Updating Is Not Harness Benefit」の ablation 視点で、
    「どの更新が既存スキルの成功率を下げたか」を更新コンポーネント単位に分離して提示する。
    従来の単一転移点 negative_transfer（report 直書き）を observability contract に載せ替え、
    evolve は audit を消費するので evolve のたびに surface される — 手動確認に依存しない配線。

    観測可能性（calibration_drift と同じデータ駆動の適用判定）:
    - usage.jsonl のレコードが無い（テレメトリ未蓄積）→ None（対象外）
    - レコードはあるが component transfer を算出できない（既存スキル前後データ不足）
      → 「評価したが算出対象なし」ℹ 行（silence != evaluated）
    - 算出できて回帰なし → 「評価したが negative transfer なし ✓」
    - 回帰あり → ⚠ で対象コンポーネントと影響スキル、evolve-skill 起動を提案

    #115: skeleton（header/trailer + not-applicable 短絡）は build_advisory_section へ集約。
    厚い render（ℹ/✓/⚠ 分岐・affected 入れ子 loop・軸別文言）は render callback に残置。
    compute は「component 算出まで（[] も評価対象＝ℹ）」を返し、テレメトリ未蓄積のみ None。
    """

    def compute(proj: Path):
        from .usage import compute_component_transfer, load_usage_data

        try:
            usage_data = load_usage_data(
                days=_NEG_TRANSFER_WINDOW_DAYS, project_root=proj
            )
        except Exception:
            return None
        if not usage_data:
            return None  # テレメトリ未蓄積 → 対象外
        try:
            return compute_component_transfer(usage_data)
        except Exception:
            return None

    def render(components) -> List[str]:
        # [] は「評価したが算出対象なし」＝ ℹ（silence != evaluated）。
        if not components:
            return [
                "ℹ 評価したが component transfer 算出対象なし"
                "（追加スキルの前後で既存スキルの success/error データが不足）。",
            ]

        flagged = [c for c in components if c.get("negative_transfer")]
        if not flagged:
            return [
                f"✓ 評価したが negative transfer なし（{len(components)} 件の更新コンポーネントを評価）",
            ]

        lines = [
            "⚠ 既存スキルの成功率を下げた更新コンポーネントあり。"
            "`/evolve-anything:evolve-skill` で該当スキルの見直しを検討:",
        ]
        for c in flagged:
            net = c.get("net_delta", 0.0)
            lines.append(f"- **{c['component']}** (net Δ{net:+.0%}):")
            for a in c.get("affected", []):
                if not a.get("negative_transfer"):
                    continue
                lines.append(
                    f"    - {a['skill_name']}: "
                    f"before={a['before_score']:.0%} → after={a['after_score']:.0%} "
                    f"(Δ{a['delta_score']:+.0%})"
                )
        return lines

    # compute が None（テレメトリ未蓄積 / 算出失敗）→ 沈黙。[] を含む list は評価対象。
    return build_advisory_section(
        project_dir,
        title="Negative Transfer (更新コンポーネント別)",
        compute=compute,
        applicable=lambda components: True,
        render=render,
    )


def build_glossary_drift_section(project_dir: Path) -> Optional[List[str]]:
    """CONTEXT.md（用語集）の drift を audit レポートに出す。

    CONTEXT.md がある PJ では drift（構造/未検証/未登録）を surface する。
    CONTEXT.md が無い PJ では、未登録 jargon 候補が SEED_MIN_CANDIDATES 以上なら
    「用語集未作成 — seed 提案対象」section を emit する（#275）。creation→detection の
    creation gap を埋める作成 trigger。候補が薄い PJ は None で沈黙（空の用語集を作らない）。

    evolve は audit を消費するため、evolve のたびに用語集の鮮度（または未作成）が可視化される
    — 手動の spec-keeper update / 散文ステップに依存しない配線。本 section は
    `_OBSERVABILITY_BUILDERS`（#278）経由で markdown と result['observability'] の両経路へ
    surface する（glossary_seed を独立 phase にしていた #275 初版を contract に統合）。
    """
    context_path = project_dir / "CONTEXT.md"
    source_paths = [
        str(project_dir / name)
        for name in ("SPEC.md", "CLAUDE.md")
        if (project_dir / name).exists()
    ]
    try:
        from glossary_drift import (
            SEED_MIN_CANDIDATES,
            check_glossary,
            find_undefined_terms,
        )
    except ImportError:
        return None

    # CONTEXT.md 不在: 用語集ブートストラップの適格性を判定（決定論・LLM 非依存）。
    if not context_path.exists():
        try:
            candidates = find_undefined_terms([], source_paths)
        except Exception:
            candidates = []
        if len(candidates) < SEED_MIN_CANDIDATES:
            return None  # jargon の薄い PJ には seed を勧めない
        return [
            "## Glossary Drift (CONTEXT.md)",
            "",
            f"ℹ 用語集未作成（CONTEXT.md 不在）— 未登録 jargon 候補 {len(candidates)} 件。"
            " spec-keeper init / evolve Step 7.7 で seed 生成を検討:",
            f"  {', '.join(candidates)}",
            "",
        ]

    report = check_glossary(str(context_path), source_paths)

    lines = ["## Glossary Drift (CONTEXT.md)", ""]
    if report.has_drift():
        lines.append("⚠ 構造 drift あり — 用語集自体の整合性が壊れています:")
        if report.malformed_lines:
            lines.append(f"  - スキーマ不一致行: {len(report.malformed_lines)}")
        if report.duplicate_terms:
            lines.append(f"  - 重複定義: {', '.join(report.duplicate_terms)}")
        if report.missing_first_seen:
            lines.append(f"  - 初出欠落: {', '.join(report.missing_first_seen)}")
    else:
        lines.append(f"✓ 構造 drift なし（用語集 {len(report.entries)} 件）")
    if report.has_unverified():
        lines.append("")
        lines.append(
            f"ℹ auto 生成・未検証のエントリ ({len(report.unverified_terms)}) "
            "— 意味を確認し初出を埋めて ⚠UNVERIFIED を外す:"
        )
        lines.append(f"  {', '.join(report.unverified_terms)}")
    if report.has_undefined():
        lines.append("")
        lines.append(
            f"ℹ 用語集未登録の jargon 候補 ({len(report.undefined_terms)}) "
            "— CONTEXT.md への追記を検討:"
        )
        lines.append(f"  {', '.join(report.undefined_terms)}")
    lines.append("")
    return lines
