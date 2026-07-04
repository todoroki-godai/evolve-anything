"""記憶操作 capability の observability セクション生成（#19, advisory）。

記憶を read/use/write/maintain の観点（OPD-Evolver, arXiv 2606.17628 由来）で評価し、
記憶の死蔵・未活用を audit に advisory 表示する。スコア重みには入れない
（outcome_metrics と同じ advisory レーン, ADR-046 と同方針）。

reason 非永続化のため read/use を統合し3軸算出する（write / maintain / use_read）。
詳細は ``memory_capability.compute_memory_capability`` を参照。

返り値契約は ``sections_outcome.build_outcome_metrics_section`` と同スタイル:
- module import 失敗 → None（沈黙）
- 評価対象なし（memory dir 不在 / 実体 0 件）→ None（沈黙。
  silence != evaluated は評価対象がある場合のみ適用）
- 1 件以上 → ヘッダ + 説明文 + 3軸の行（各軸 evidence 併記）
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .advisory import build_advisory_section


def _axis_line(label: str, axis: Dict[str, Any], direction: str, evidence: str) -> List[str]:
    """1 軸を value + 方向性 + evidence で 2 行にして返す。"""
    value = axis.get("value")
    if value is None:
        return [f"  ・{label}: データ不足"]
    return [
        f"  ・{label}: {value:.2f} — {direction}",
        f"      evidence: {evidence}",
    ]


def build_memory_capability_section(project_dir: Path) -> Optional[List[str]]:
    """記憶操作 capability 3軸を audit に advisory 表示する（決定論・LLM 非依存）。

    観測可能性:
    - memory_capability モジュール未解決 → None（沈黙）
    - 当 PJ に memory 実体が 1 件も無い（dir 不在 / MEMORY.md のみ）→ None（沈黙）
    - 1 件以上 → ヘッダ + 説明文 + 3 軸を出力
    """
    def compute(proj: Path) -> Optional[Dict[str, Any]]:
        try:
            import memory_capability
        except ImportError:
            return None
        return memory_capability.compute_memory_capability(proj)

    def applicable(result: Dict[str, Any]) -> bool:
        return bool(result.get("applicable"))

    def render(result: Dict[str, Any]) -> List[str]:
        total = result["total"]
        write = result["write"]
        maintain = result["maintain"]
        use_read = result["use_read"]

        we = write["evidence"]
        me = maintain["evidence"]
        ue = use_read["evidence"]

        body: List[str] = [
            f"記憶（メモリ）の書き込み・維持・活用を 3 つの観点で評価します"
            f"（当 PJ memory {total} 件）。記憶が死蔵・未活用になっていないかの可視化が目的で、"
            f"スコアの重みには反映しません（参考: OPD-Evolver 論文 arXiv 2606.17628, #19）。"
            f"LLM を使わず決定論で算出。",
            "",
        ]
        body.extend(
            _axis_line(
                "write（記憶量）",
                write,
                "高いほど良い（記憶を構造化して書けている）",
                f"frontmatter あり {we.get('with_frontmatter', 0)} / 総 {we.get('total', 0)} 件",
            )
        )
        body.extend(
            _axis_line(
                "maintain（維持・健全度）",
                maintain,
                "高いほど良い（腐敗を管理できている）",
                f"stale {me.get('stale', 0)} / superseded {me.get('superseded', 0)} "
                f"/ 総 {me.get('total', 0)} 件",
            )
        )
        body.extend(
            _axis_line(
                "use/read（活性）",
                use_read,
                "高いほど良い（書いた記憶が温められている）",
                f"reinforced {ue.get('reinforced', 0)} / 総 {ue.get('total', 0)} 件 "
                f"/ update_count 中央値 {ue.get('update_count_median', 0.0):.1f}",
            )
        )

        # use/read 軸の限界注記（#19 設計・senpai 検証済み）。
        body.extend([
            "",
            "  ※ use/read 軸の限界: reinforce は SessionStart で有効 memory 全件に発火するため "
            "per-memory の recall 回数ではなく PJ の活性 proxy。recall/注入の内訳は "
            "reason 永続化（別 issue 提案予定）後に分離可能。",
        ])
        return body

    return build_advisory_section(
        project_dir,
        title="Memory Capability (read/use/write/maintain — advisory, スコア重みには未反映)",
        compute=compute,
        applicable=applicable,
        render=render,
    )


def build_memory_index_orphan_section(project_dir: Path) -> Optional[List[str]]:
    """MEMORY.md 索引と実 memory ファイルの孤児（不整合）を audit に advisory 表示する（#127）。

    観測可能性:
    - memory_index_orphan / memory_capability 未解決 → None（沈黙）
    - MEMORY.md が無い PJ（索引が無いので検査対象外）→ None（沈黙）
    - 孤児なし → None（「無ければ非表示」= issue #127 の受け入れ基準）
    - 孤児あり → ⚠ で unindexed（索引に無い実ファイル）/ stale リンク（実体の無いリンク）を列挙
    """

    def compute(proj: Path):
        try:
            import memory_capability
            import memory_index_orphan
        except ImportError:
            return None
        memory_dir = memory_capability._resolve_memory_dir(proj)
        return memory_index_orphan.detect_index_orphans(memory_dir)

    def render(report) -> List[str]:
        lines: List[str] = []
        if report.unindexed_files:
            lines.append(
                f"⚠ MEMORY.md 索引に載っていない memory ファイルが "
                f"{len(report.unindexed_files)} 件（索引から不可視＝recall/注入で想起されない）:"
            )
            for name in report.unindexed_files:
                lines.append(f"  ・{name}（索引に未掲載）")
        if report.indexed_missing:
            lines.append(
                f"⚠ MEMORY.md にリンクがあるのに実体が無い（stale リンク）ファイルが "
                f"{len(report.indexed_missing)} 件:"
            )
            for name in report.indexed_missing:
                lines.append(f"  ・{name}（リンク先が不在）")
        return lines

    return build_advisory_section(
        project_dir,
        title="Memory Index Orphans (MEMORY.md 索引 ↔ 実ファイルの不整合 — advisory)",
        compute=compute,
        applicable=lambda report: report.has_index and report.has_findings,
        render=render,
    )


def build_memory_schema_section(project_dir: Path) -> Optional[List[str]]:
    """auto-memory frontmatter スキーマ違反を audit に advisory 表示する（#128）。

    観測可能性:
    - memory_schema_check / memory_capability 未解決 → None（沈黙）
    - memory 実体 0 件 → None（沈黙）
    - 違反なし → None（「無ければ非表示」= issue #128 の受け入れ基準）
    - 違反あり → ⚠ でファイル名 + 違反内容（欠落 / kebab-case 逸脱 / 不正 type）を列挙
    """

    def compute(proj: Path):
        try:
            import memory_capability
            import memory_schema_check
        except ImportError:
            return None
        memory_dir = memory_capability._resolve_memory_dir(proj)
        return memory_schema_check.detect_schema_violations(memory_dir)

    def render(report) -> List[str]:
        lines = [
            f"⚠ auto-memory frontmatter スキーマ違反が {len(report.violations)} 件。"
            "name（kebab-case）/ description / metadata.type"
            "（user|feedback|project|reference）を揃えること（#128）:",
        ]
        for v in report.violations:
            lines.append(f"  ・{v.filename}: {', '.join(v.issues)}")
        return lines

    return build_advisory_section(
        project_dir,
        title="Memory Frontmatter Schema (auto-memory スキーマ違反 — advisory)",
        compute=compute,
        applicable=lambda report: report.has_findings,
        render=render,
    )


def build_memory_contamination_section(project_dir: Path) -> Optional[List[str]]:
    """memory ファイルに着地した記憶汚染パターンを audit に advisory 表示する（#108）。

    書込境界（`auto_memory_broker` + `memory_guard`）は新規汚染を弾くが、guard 導入前に
    書かれた / 別経路で紛れ込んだ汚染は残りうる。ここで read-time に再スキャンして surface
    する（read-only・削除は提案のみ・auto-apply しない）。

    観測可能性:
    - memory_capability / memory_guard 未解決 → None（沈黙）
    - memory dir 不在 / 走査対象 0 件（applicable=False）→ None（沈黙）
    - 汚染なし → None（「無ければ非表示」）
    - 汚染あり → ⚠ でファイル名 / カテゴリ / snippet を列挙
    """

    def compute(proj: Path):
        try:
            import memory_capability
            import memory_guard
        except ImportError:
            return None
        memory_dir = memory_capability._resolve_memory_dir(proj)
        return memory_guard.scan_memory_dir(memory_dir)

    def render(report) -> List[str]:
        lines = [
            f"⚠ memory ファイルに記憶汚染パターン（prompt injection / secret exfil）が "
            f"{len(report.hits)} 件（走査 {report.scanned_files} 件中）。書込境界（#108）を"
            "通らずに紛れ込んだ / guard 導入前の記憶の可能性があります。内容を確認し、"
            "汚染であれば該当ファイルを削除してください（削除は提案のみ・auto-apply しません）:",
        ]
        for h in report.hits:
            lines.append(
                f"  ・{h.filename}:{h.line} [{h.category}/{h.severity}] {h.snippet}"
            )
        return lines

    return build_advisory_section(
        project_dir,
        title="Memory Contamination（記憶汚染パターン検出 — advisory, スコア重みには未反映）",
        compute=compute,
        applicable=lambda report: report.has_findings,
        render=render,
    )


def build_memory_dup_residue_section(project_dir: Path) -> Optional[List[str]]:
    """旧 PJ memory の完全重複残骸（rename 由来 orphan dir）を advisory 表示する（#131）。

    全 PJ 横断走査（``~/.claude/projects/*/memory``）のため project_dir に依存しない
    fleet 向き検出。削除は提案のみで auto-apply しない。

    観測可能性:
    - memory_dup_residue 未解決 → None（沈黙）
    - 完全重複ペアなし → None（「無ければ非表示」= issue #131 の受け入れ基準）
    - 完全重複ペアあり → ℹ で残骸候補 dir / 重複先 dir / ファイル件数 + 退避削除手順を列挙
    """

    def compute(_proj: Path):
        try:
            import memory_dup_residue
        except ImportError:
            return None
        return memory_dup_residue.detect_duplicate_memory_dirs(
            memory_dup_residue.default_projects_dir()
        )

    def render(report) -> List[str]:
        lines = [
            f"ℹ 内容が完全重複する memory ディレクトリ（残骸候補）が {len(report.pairs)} 組。"
            "rename 由来の旧 PJ memory が残置している可能性があります（削除は提案のみ・"
            "auto-apply しません・#131）:",
        ]
        for pair in report.pairs:
            label = "（rename 由来の可能性大）" if pair.rename_suspected else ""
            lines.append(f"  ・残骸候補: {pair.residue_dir}{label}")
            lines.append(
                f"      重複先: {pair.target_dir} / ファイル {pair.file_count} 件"
            )
            lines.append(
                f"      退避してから削除: "
                f"tar czf {pair.residue_dir}-memory-backup.tgz -C \"{pair.residue_path}\" . "
                f"&& rm -rf \"{pair.residue_path}\""
            )
        return lines

    return build_advisory_section(
        project_dir,
        # #142-8a: 全 PJ 走査（当 PJ 別 audit でも無関係 PJ の重複が出る）ゆえ見出しにスコープ
        # を明記する（belief_blocks / Token Consumption / 繰り返し失敗パターンと同慣習）。
        title="Memory Duplicate Residue（全PJ横断・旧 PJ memory の完全重複残骸 — advisory）",
        compute=compute,
        applicable=lambda report: report.has_findings,
        render=render,
    )
