"""レポートセクション生成（Constitutional Score / Token Consumption / Test Guard / LSP）。

audit パッケージから切り出された Sections モジュール。generate_report が呼ぶ
セクション生成関数を集約。
- _format_constitutional_report: Constitutional Score → Markdown
- _short_int: 大きい整数 → 短縮表記 (1.2K / 3.4M / 5.6B)
- build_token_consumption_section: PJ別トークン消費 TOP3 + 異常検知
- _build_test_guard_section: LLM SDK 利用 PJ への guard 導入推奨
- build_lsp_suggestion_section: LSP未設定PJへの導入提案
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _format_constitutional_report(result: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Constitutional Score をレポート用にフォーマットする。"""
    if result is None:
        return ["## Constitutional Score", "", "LLM 評価に失敗しました", ""]

    if result.get("overall") is None:
        skip_reason = result.get("skip_reason", "unknown")
        coverage = result.get("coverage_value", "N/A")
        return [
            "## Constitutional Score",
            "",
            f"Skipped: {skip_reason} (coverage={coverage})",
            "",
        ]

    lines = [f"## Constitutional Score: {result['overall']:.2f}", ""]

    per_principle = result.get("per_principle", [])
    if per_principle:
        lines.append("### Per-Principle Scores")
        for p in per_principle:
            score = p.get("score", 0.0)
            bar_filled = int(score * 20)
            bar_empty = 20 - bar_filled
            bar = "█" * bar_filled + "░" * bar_empty
            lines.append(f"  {p.get('id', '?'):30s} {score:.2f} {bar}")
        lines.append("")

    cost = result.get("estimated_cost_usd", 0)
    calls = result.get("llm_calls_count", 0)
    lines.append(f"LLM calls: {calls}, Estimated cost: ${cost:.4f}")
    lines.append("")

    return lines


def _short_int(n: int | None) -> str:
    if n is None:
        return "--"
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def build_token_consumption_section(days: int = 30) -> List[str]:
    """Token Consumption セクションを生成する。

    データ無し → 1 行ヒントのみ返す。
    データあり → TOP 3 / Anomalies / Hints。
    """
    try:
        import token_usage_query as tuq  # type: ignore
        import token_usage_store as tus  # type: ignore
    except ImportError:
        return []

    db_empty = (not tus.HAS_DUCKDB) or (not tus.USAGE_DB.exists())
    if not db_empty:
        try:
            row = tus.query("SELECT COUNT(*) FROM token_usage")
            db_empty = (not row) or (row[0][0] == 0)
        except Exception:
            db_empty = True

    if db_empty:
        return [
            "## Token Consumption",
            "",
            "(Token tracking not initialized — run `rl-fleet tokens --backfill` to enable)",
            "",
        ]

    try:
        top = tuq.top_n_consumers(days=days, n=3)
        wow = tuq.wow_anomalies()
        cache = tuq.cache_hit_anomalies()
    except Exception:
        return []

    lines: List[str] = [f"## Token Consumption (last {days} days)", ""]
    if top:
        lines.append("TOP 3 consumers:")
        for i, c in enumerate(top, 1):
            hit = (
                f"  (cache hit {c['cache_hit_pct']:.0f}%)"
                if c.get("cache_hit_pct") is not None
                else ""
            )
            label = c.get("pj_slug") or c["pj_id"]
            lines.append(f"  {i}. {label}\t{_short_int(c['tokens'])}{hit}")
        lines.append("")
    if wow or cache:
        lines.append("Anomalies detected:")
        for a in wow:
            lines.append(
                f"  • {a['pj_id']}: WoW +{a['wow_pct']:.0f}% "
                f"({_short_int(a['last_week'])} → {_short_int(a['this_week'])})"
            )
        for a in cache:
            lines.append(
                f"  • {a['pj_id']}: cache hit {a['last_hit_pct']:.0f}% → "
                f"{a['this_hit_pct']:.0f}% (drop {a['drop_pt']:.0f}pt)"
            )
        lines.append("")
    lines.append("Hints:")
    lines.append("  • Low cache hit (<40%) often means CLAUDE.md / system prompt changes per session")
    lines.append("  • WoW spikes often correlate with subagent loops — check SUBAGENTS_30d column")
    lines.append("")
    return lines


def _build_test_guard_section(project_dir: Path) -> Optional[List[str]]:
    """PJ が LLM SDK を使うのに no-llm-in-tests / pytest-no-llm が未導入なら勧める。"""
    try:
        import test_guard
    except ImportError:
        return None
    rows = test_guard.collect_test_guard_rows([project_dir])
    if not rows:
        return None
    r = rows[0]
    if not r.uses_llm:
        return None
    if not r.needs_attention and not r.preventive_candidate:
        return None
    lines = ["## Test Guard", ""]
    lines.append(f"このPJはLLM SDKを利用しています ({', '.join(sorted(r.languages))})。")
    if r.preventive_candidate:
        lines.append("現在テストフレームワーク未導入のため即時の事故リスクは低いですが、")
        lines.append("テスト追加時に備え以下のguardを予防的に導入することを推奨します:")
    else:
        lines.append("ユニットテストでLLMを誤って実呼び出ししないよう、以下のguardの導入を推奨します:")
    if not r.has_precommit_hook:
        lines.append("- pre-commit: `no-llm-in-tests` (静的検出、全言語)")
    if "python" in r.languages and not r.has_pytest_no_llm:
        lines.append("- pip: `pytest-no-llm` (実行時 guard、Python のみ)")
    lines.append("")
    lines.append("導入方法は ~/tools/no-llm-in-tests/README.md, ~/tools/pytest-no-llm/README.md を参照。")
    lines.append("")
    return lines


# 言語 → (拡張子リスト, LSP コマンド, インストール方法, .lsp.json キー)
_LSP_CATALOG: Dict[str, Dict[str, Any]] = {
    "python": {
        "extensions": [".py"],
        "command": "pylsp",
        "install": "pip install python-lsp-server",
        "lsp_key": "python",
        "config": {
            "command": "pylsp",
            "args": [],
            "extensionToLanguage": {".py": "python"},
        },
    },
    "typescript": {
        "extensions": [".ts", ".tsx"],
        "command": "typescript-language-server",
        "install": "npm install -g typescript-language-server typescript",
        "lsp_key": "typescript",
        "config": {
            "command": "typescript-language-server",
            "args": ["--stdio"],
            "extensionToLanguage": {".ts": "typescript", ".tsx": "typescriptreact"},
        },
    },
    "javascript": {
        "extensions": [".js", ".jsx"],
        "command": "typescript-language-server",
        "install": "npm install -g typescript-language-server typescript",
        "lsp_key": "javascript",
        "config": {
            "command": "typescript-language-server",
            "args": ["--stdio"],
            "extensionToLanguage": {".js": "javascript", ".jsx": "javascriptreact"},
        },
    },
    "go": {
        "extensions": [".go"],
        "command": "gopls",
        "install": "go install golang.org/x/tools/gopls@latest",
        "lsp_key": "go",
        "config": {
            "command": "gopls",
            "args": [],
            "extensionToLanguage": {".go": "go"},
        },
    },
    "rust": {
        "extensions": [".rs"],
        "command": "rust-analyzer",
        "install": "rustup component add rust-analyzer",
        "lsp_key": "rust",
        "config": {
            "command": "rust-analyzer",
            "args": [],
            "extensionToLanguage": {".rs": "rust"},
        },
    },
}
_LSP_SCAN_EXCLUDE = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "vendor", "dist", "build", "target", ".tox", "bower_components",
}
_LSP_MIN_FILES = 3  # 提案を出す最低ファイル数


def _detect_project_languages(project_dir: Path) -> List[str]:
    """プロジェクトの主要言語をファイル拡張子から検出する。"""
    ext_counts: Dict[str, int] = {}
    for lang, info in _LSP_CATALOG.items():
        for ext in info["extensions"]:
            ext_counts[ext] = 0

    try:
        for path in project_dir.rglob("*"):
            try:
                rel_parts = path.relative_to(project_dir).parts
            except ValueError:
                continue
            if any(part in _LSP_SCAN_EXCLUDE for part in rel_parts):
                continue
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix in ext_counts:
                ext_counts[suffix] = ext_counts.get(suffix, 0) + 1
    except (PermissionError, OSError):
        return []


    detected = []
    for lang, info in _LSP_CATALOG.items():
        total = sum(ext_counts.get(ext, 0) for ext in info["extensions"])
        if total >= _LSP_MIN_FILES:
            detected.append(lang)
    return detected


def _load_lsp_json(project_dir: Path) -> Optional[Dict[str, Any]]:
    lsp_path = project_dir / ".lsp.json"
    if not lsp_path.exists():
        return None
    try:
        return json.loads(lsp_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        import sys as _sys
        print(f"[rl-anything:audit] .lsp.json が不正な JSON です: {lsp_path}", file=_sys.stderr)
        return None
    except OSError:
        return None


def build_corrections_insights_section(
    corrections_file: Path | None = None,
    top_n: int = 5,
) -> List[str]:
    """繰り返し失敗パターン TOP-N セクションを生成する。

    corrections が MIN_DISPLAY_RECORDS 件未満の場合はスキップ（空リスト返却）。
    """
    import sys as _sys
    _LIB = Path(__file__).resolve().parent.parent
    if str(_LIB) not in _sys.path:
        _sys.path.insert(0, str(_LIB))

    from corrections_insights import count_repeated_patterns  # noqa: PLC0415

    # count_repeated_patterns 内部で MIN_DISPLAY_RECORDS チェック済み → 二重読み込み不要
    patterns = count_repeated_patterns(corrections_file=corrections_file, top_n=top_n)
    if not patterns:
        return []

    lines: List[str] = [f"## 繰り返し失敗パターン TOP-{top_n}", ""]
    for i, p in enumerate(patterns, 1):
        lines.append(f"{i}. `{p['correction_type']}` — {p['count']} 回")
        if p.get("example_messages"):
            lines.append(f"   例: 「{p['example_messages'][0]}」")
    lines.append("")
    return lines


def build_lsp_suggestion_section(project_dir: Path) -> Optional[List[str]]:
    """LSP未設定のPJに対して導入提案セクションを生成する。

    - .lsp.json が存在しない場合 → 全検出言語の提案を生成
    - .lsp.json が存在する場合 → None を返す（既設定）
    - 対応言語ファイルが閾値未満の場合 → None を返す
    """
    existing = _load_lsp_json(project_dir)
    if existing is not None:
        return None

    detected = _detect_project_languages(project_dir)
    if not detected:
        return None

    lines = ["## LSP Setup Recommendation", ""]
    lines.append(
        f"このPJには {', '.join(detected)} のファイルが検出されましたが、"
        "`.lsp.json` が設定されていません。"
    )
    lines.append(
        "LSP（Language Server Protocol）を導入すると、"
        "Claude Code が `goToDefinition` / `findReferences` 等のツールを活用でき、"
        "Read ツールの呼び出し回数を削減できます。"
    )
    lines.append("")

    seen_installs: set = set()
    config_example: Dict[str, Any] = {}
    for lang in detected:
        info = _LSP_CATALOG[lang]
        install_cmd = info["install"]
        if install_cmd not in seen_installs:
            lines.append(f"**{lang}**: `{install_cmd}`")
            seen_installs.add(install_cmd)
        config_example[info["lsp_key"]] = info["config"]

    lines.append("")
    lines.append("`.lsp.json` 設定例（プロジェクトルートに配置）:")
    lines.append("```json")
    lines.append(json.dumps(config_example, indent=2, ensure_ascii=False))
    lines.append("```")
    lines.append("")
    return lines


def build_glossary_drift_section(project_dir: Path) -> Optional[List[str]]:
    """CONTEXT.md（用語集）の drift を audit レポートに出す。

    CONTEXT.md が無い PJ では None（spec-keeper init 前は対象外）。
    evolve は audit を消費するため、evolve のたびに用語集の鮮度が可視化される
    — 手動の spec-keeper update を待たずに発火する配線。
    """
    context_path = project_dir / "CONTEXT.md"
    if not context_path.exists():
        return None
    try:
        from glossary_drift import check_glossary
    except ImportError:
        return None

    source_paths = [
        str(project_dir / name)
        for name in ("SPEC.md", "CLAUDE.md")
        if (project_dir / name).exists()
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
    if report.has_undefined():
        lines.append("")
        lines.append(
            f"ℹ 用語集未登録の jargon 候補 ({len(report.undefined_terms)}) "
            "— CONTEXT.md への追記を検討:"
        )
        lines.append(f"  {', '.join(report.undefined_terms)}")
    lines.append("")
    return lines
