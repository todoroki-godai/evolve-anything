"""MEMORY ファイルの検証・健康診断ロジック。

audit パッケージから切り出された Memory verification モジュール。
- build_memory_verification_context: LLM 検証用の構造化コンテキスト生成
- build_memory_health_section: stale references / near limit の検出と report 行生成
- build_temporal_memory_warnings: APEX-MEM A++ temporal frontmatter ベースの stale/superseded 検出
- build_memory_trace_audit_section: MemTrace 帰属診断の可視化（misretrieval / context_drift / corruption）
"""
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rl_common import DATA_DIR
from reflect_utils import read_all_memory_entries, read_auto_memory, split_memory_sections
from path_extractor import extract_paths_outside_codeblocks as _extract_paths_outside_codeblocks, KNOWN_DIR_PREFIXES
from line_limit import NEAR_LIMIT_RATIO

from ._constants import LIMITS, _STOPWORDS


def _extract_section_keywords(text: str) -> List[str]:
    """MEMORY セクションのテキストからキーワードを抽出する。

    ストップワードと2文字以下の単語を除外して返す。
    """
    import re as _re

    # コードブロック除去
    cleaned = _re.sub(r"```[\s\S]*?```", "", text)
    # Markdown 記法除去（リンク、強調等）
    cleaned = _re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cleaned)
    cleaned = _re.sub(r"[*_`#>|]", " ", cleaned)
    # トークン化: アルファベット/数字/CJK を含む単語（CJK句読点を除外）
    tokens = _re.findall(r"[\w぀-ゟ゠-ヿ一-鿿豈-﫿]+", cleaned, _re.UNICODE)
    # フィルタリング
    keywords = []
    seen: set = set()
    for token in tokens:
        lower = token.lower()
        if len(token) <= 2 and not any("぀" <= c <= "鿿" for c in token):
            continue
        if lower in _STOPWORDS:
            continue
        if lower not in seen:
            seen.add(lower)
            keywords.append(token)
    return keywords


def _find_archive_mentions(
    keywords: List[str],
    project_dir: Path,
) -> List[str]:
    """OpenSpec archive ディレクトリ名とキーワードを照合しメンションを返す。"""
    archive_dir = project_dir / "openspec" / "changes" / "archive"
    if not archive_dir.is_dir():
        return []
    mentions = []
    kw_lower = {kw.lower() for kw in keywords}
    for entry in sorted(archive_dir.iterdir()):
        if not entry.is_dir():
            continue
        # アーカイブ名は "YYYY-MM-DD-name" 形式 → 日付部分を除去
        name = entry.name
        parts = name.split("-", 3)
        if len(parts) >= 4:
            name_part = parts[3]
        else:
            name_part = name
        # アーカイブ名のトークンとキーワードをマッチ
        name_tokens = {t.lower() for t in name_part.replace("-", " ").split()}
        if name_tokens & kw_lower:
            mentions.append(entry.name)
    return mentions


def _is_project_specific_section(
    section: Dict[str, Any],
    project_dir: Path,
) -> bool:
    """global memory のセクションが PJ 固有の記述を含むか判定する。"""
    project_name = project_dir.name
    content_lower = section.get("content", "").lower()
    heading_lower = section.get("heading", "").lower()
    combined = f"{heading_lower} {content_lower}"
    # PJ 名がセクション内に出現するか
    if project_name.lower() in combined:
        return True
    # PJ ディレクトリ内の主要ファイル名がメンションされているか
    for child in project_dir.iterdir():
        if child.name.startswith("."):
            continue
        if child.name.lower() in combined:
            return True
    return False


def build_memory_verification_context(
    project_dir: Path,
) -> Dict[str, Any]:
    """MEMORY セクションの検証用コンテキストを構造化 JSON で返す。

    セクション分割 → キーワード抽出 → grep → archive メンション を実行し、
    LLM 検証ステップに渡す構造化データを生成する。
    """
    import subprocess

    sections_out: List[Dict[str, Any]] = []

    # 1. auto-memory の読み取り
    for entry in read_auto_memory(str(project_dir)):
        try:
            sections = split_memory_sections(entry["content"], entry["path"])
            sections_out.extend(sections)
        except Exception as e:
            print(f"Warning: failed to parse {entry['path']}: {e}", file=sys.stderr)

    # 2. global memory（PJ 固有セクションのみ）
    all_entries = read_all_memory_entries(project_dir)
    for entry in all_entries:
        if entry["tier"] != "global":
            continue
        try:
            global_sections = split_memory_sections(entry["content"], entry["path"])
            for sec in global_sections:
                if _is_project_specific_section(sec, project_dir):
                    sections_out.append(sec)
        except Exception as e:
            print(f"Warning: failed to parse global memory: {e}", file=sys.stderr)

    if not sections_out:
        return {"sections": []}

    # 3. 各セクションにキーワード・codebase_evidence・archive_mentions を付与
    for sec in sections_out:
        keywords = _extract_section_keywords(sec["content"])
        sec["keywords"] = keywords

        # codebase grep（上位3キーワードで検索、各最大3件）
        evidence: List[Dict[str, str]] = []
        for kw in keywords[:3]:
            try:
                result = subprocess.run(
                    ["grep", "-r", "-l", "--include=*.py", "--include=*.md",
                     "--include=*.ts", "--include=*.js", "--include=*.yaml",
                     "--include=*.yml", "--include=*.json",
                     "-m", "3", kw, str(project_dir)],
                    capture_output=True, text=True, timeout=10,
                )
                for fpath in result.stdout.strip().splitlines()[:3]:
                    # MEMORY 自身は除外
                    if "memory/" in fpath or ".claude/projects/" in fpath:
                        continue
                    # ファイルからスニペット取得
                    try:
                        snippet_result = subprocess.run(
                            ["grep", "-n", "-m", "2", kw, fpath],
                            capture_output=True, text=True, timeout=5,
                        )
                        snippet = snippet_result.stdout.strip()[:200]
                    except (subprocess.TimeoutExpired, OSError):
                        snippet = ""
                    rel_path = str(Path(fpath).relative_to(project_dir)) if fpath.startswith(str(project_dir)) else fpath
                    evidence.append({"file": rel_path, "snippet": snippet})
            except (subprocess.TimeoutExpired, OSError):
                continue
        sec["codebase_evidence"] = evidence

        # archive メンション
        sec["archive_mentions"] = _find_archive_mentions(keywords, project_dir)

    return {"sections": sections_out}


def _parse_frontmatter_fields(content: str) -> Dict[str, Any]:
    """memory frontmatter v2 の重要フィールドを抽出する。

    frontmatter がない場合はデフォルト値を返す。
    importance 未指定時は "medium" をデフォルト値として返す。

    Returns:
        dict with keys: importance (str), detail_file (str | None),
                        importance_score (float | None)
    """
    result: Dict[str, Any] = {"importance": "medium", "detail_file": None, "importance_score": None}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return result

    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "importance" and value:
                result["importance"] = value
            elif key == "detail_file" and value:
                result["detail_file"] = value
            elif key == "importance_score" and value:
                try:
                    result["importance_score"] = float(value)
                except (ValueError, TypeError):
                    pass

    return result


def _check_detail_file_links(
    memory_files: List[Tuple[Path, str]],
    project_dir: Path,
) -> List[Dict[str, Any]]:
    """memory frontmatter v2 の detail_file フィールドの broken link を検出する。

    detail_file は参照元ファイルの親ディレクトリからの相対パス、または絶対パスとして解釈する。

    Returns:
        broken link 情報の dict リスト。問題なければ空リスト。
    """
    broken: List[Dict[str, Any]] = []
    for path, content in memory_files:
        fields = _parse_frontmatter_fields(content)
        detail_file = fields.get("detail_file")
        if not detail_file:
            continue
        if detail_file.startswith("/"):
            check_path = Path(detail_file)
        else:
            check_path = path.parent / detail_file
        if not check_path.exists():
            broken.append({
                "file": str(path),
                "detail_file": detail_file,
            })
    return broken


def build_memory_health_section(
    artifacts: Dict[str, List[Path]],
    project_dir: Path,
) -> List[str]:
    """MEMORY ファイルの健康度を分析し、レポートセクションの行リストを返す。

    検出項目:
    - 陳腐化参照: MEMORY 内のパス参照がディスク上に存在しない
    - 肥大化警告: NEAR_LIMIT_RATIO 以上の行数
    - broken detail_file リンク: frontmatter v2 の detail_file フィールドが存在しない

    問題がない場合は空リストを返す。
    """
    # project-local memory + auto-memory を統合
    memory_files: List[Tuple[Path, str]] = []  # (path, content)

    for path in artifacts.get("memory", []):
        try:
            content = path.read_text(encoding="utf-8")
            memory_files.append((path, content))
        except (OSError, UnicodeDecodeError) as e:
            print(f"Warning: failed to read {path}: {e}", file=sys.stderr)

    for entry in read_auto_memory(str(project_dir)):
        entry_path = Path(entry["path"])
        # project-local と重複しないように
        if not any(p == entry_path for p, _ in memory_files):
            memory_files.append((entry_path, entry["content"]))

    stale_refs: List[Dict[str, Any]] = []
    near_limits: List[Dict[str, Any]] = []
    broken_detail_files: List[Dict[str, Any]] = _check_detail_file_links(memory_files, project_dir)
    low_importance: List[Dict[str, Any]] = []

    for path, content in memory_files:
        # 陳腐化参照の検出
        extracted = _extract_paths_outside_codeblocks(content)
        for line_num, ref_path in extracted:
            # 絶対パスはそのまま、相対パスは project_dir からの相対で確認
            if ref_path.startswith("/"):
                check_path = Path(ref_path)
            else:
                check_path = project_dir / ref_path
            if not check_path.exists():
                # ファイル位置基準の相対パス解決（参照元ファイルの親ディレクトリ基準）
                if not ref_path.startswith("/"):
                    file_relative = path.parent / ref_path
                    if file_relative.exists():
                        continue
                # トップレベルディレクトリがプロジェクトルートに存在しない場合は除外
                if not ref_path.startswith("/"):
                    top_dir = ref_path.split("/")[0]
                    if top_dir not in KNOWN_DIR_PREFIXES and not (project_dir / top_dir).exists():
                        continue
                stale_refs.append({
                    "file": str(path),
                    "line": line_num,
                    "path": ref_path,
                })

        # 肥大化警告
        line_count = content.count("\n") + 1
        limit = LIMITS["MEMORY.md"] if path.name == "MEMORY.md" else LIMITS["memory"]
        threshold = int(limit * NEAR_LIMIT_RATIO)
        if line_count >= threshold:
            pct = int(line_count / limit * 100)
            near_limits.append({
                "file": str(path),
                "lines": line_count,
                "limit": limit,
                "pct": pct,
            })

        # 低重要度メモリ候補の検出 (importance_score ≤ 0.3)
        fields = _parse_frontmatter_fields(content)
        raw_score = fields.get("importance_score")
        if raw_score is not None:
            try:
                score = float(raw_score)
                if score <= 0.3:
                    rel_path = (
                        str(path.relative_to(project_dir))
                        if str(path).startswith(str(project_dir))
                        else str(path)
                    )
                    low_importance.append({"file": rel_path, "score": score})
            except (TypeError, ValueError):
                pass

    # 問題なしなら空リスト
    if not stale_refs and not near_limits and not broken_detail_files and not low_importance:
        return []

    lines = ["## Memory Health", ""]

    if stale_refs:
        lines.append(f"### Stale References ({len(stale_refs)})")
        for ref in stale_refs:
            lines.append(f"- {ref['file']}:{ref['line']} — \"{ref['path']}\" not found on disk")
        lines.append("")

    if broken_detail_files:
        lines.append(f"### Broken detail_file Links ({len(broken_detail_files)})")
        for bf in broken_detail_files:
            lines.append(f"- {bf['file']} — detail_file \"{bf['detail_file']}\" not found on disk")
        lines.append("")

    if near_limits:
        lines.append("### Near Limit")
        for nl in near_limits:
            lines.append(f"- {nl['file']}: {nl['lines']}/{nl['limit']} lines ({nl['pct']}%)")
        lines.append("")

    if low_importance:
        lines.append("### 低重要度メモリ候補 (importance_score ≤ 0.3)")
        for li in low_importance:
            lines.append(f"- {li['file']}: {li['score']:.2f}")
        lines.append("")

    # Suggestions
    suggestions = []
    if stale_refs:
        suggestions.append("Remove or update stale references")
    if broken_detail_files:
        suggestions.append("Update or remove broken detail_file references in memory frontmatter")
    if near_limits:
        suggestions.append("Split large MEMORY.md entries into topic files")
    if suggestions:
        lines.append("### Suggestions")
        for s in suggestions:
            lines.append(f"- {s}")
        lines.append("")

    return lines


def build_temporal_memory_warnings(
    memory_dir: Path,
    corrections_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """temporal frontmatter に基づいて stale / superseded memory を検出する。

    APEX-MEM A++ 設計: decay_days 超過 or superseded_at 過去 のファイルを WARN。
    source_correction_ids が全て reflect 済みなら deletion_candidate=True。

    Args:
        memory_dir: auto-memory ディレクトリ（~/.claude/projects/.../memory/）
        corrections_path: corrections.jsonl のパス。None なら DATA_DIR 下のデフォルト。

    Returns:
        各警告の dict リスト。問題なければ空リスト。
    """
    try:
        from memory_temporal import parse_memory_temporal, is_stale, is_superseded
    except ImportError:
        return []

    if not memory_dir.is_dir():
        return []

    # corrections.jsonl を一度全読みして反映済みキーの set を作る（O(M) 一回のみ）
    reflected_ids: set[str] = set()
    _corrections_path = corrections_path or (DATA_DIR / "corrections.jsonl")
    if _corrections_path.is_file():
        try:
            for line in _corrections_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("reflect_status") == "applied":
                        sid = rec.get("session_id", "")
                        ts = rec.get("timestamp", "")
                        if sid and ts:
                            from memory_temporal import make_source_correction_id
                            reflected_ids.add(make_source_correction_id(sid, ts))
                except json.JSONDecodeError:
                    pass
        except (OSError, UnicodeDecodeError):
            pass

    warnings: List[Dict[str, Any]] = []
    for md_file in sorted(memory_dir.glob("*.md")):
        try:
            temporal = parse_memory_temporal(md_file)
        except Exception:
            continue

        # frontmatter なし → temporal は全 None → スキップ
        if temporal["valid_from"] is None and temporal["superseded_at"] is None and not temporal["source_correction_ids"]:
            continue

        reason = None
        if is_superseded(temporal):
            reason = "superseded"
        elif is_stale(temporal):
            reason = "stale"

        if reason is None:
            continue

        # deletion_candidate: source_correction_ids が全て reflected かどうか
        source_ids = temporal["source_correction_ids"]
        deletion_candidate = bool(source_ids) and all(sid in reflected_ids for sid in source_ids)

        warnings.append({
            "file": md_file.name,
            "path": str(md_file),
            "reason": reason,
            "valid_from": temporal["valid_from"],
            "superseded_at": temporal["superseded_at"],
            "decay_days": temporal["decay_days"],
            "source_correction_ids": source_ids,
            "deletion_candidate": deletion_candidate,
        })

    return warnings


def build_memory_trace_audit_section(
    project_path: Optional[str] = None,
    corrections_path: Optional[Path] = None,
    keywords: Optional[set] = None,
    score_threshold: float = 0.3,
    staleness_days: int = 30,
    post_retrieval_window_sec: int = 300,
) -> List[str]:
    """MemTrace 帰属診断を実行し、audit レポート形式の行リストを返す。

    episodic_store.query_relevant の結果に memory_trace.attribute_errors を適用して
    misretrieval / context_drift / corruption の3類型を可視化する。

    DuckDB 未インストール時・エラー時・エラーなし時は空リストを返す。

    Args:
        project_path: 診断対象プロジェクトパス。None で全件。
        corrections_path: corrections.jsonl のパス。None なら DATA_DIR 下のデフォルト。
        keywords: 検索キーワード set。None 時は基本キーワードを使用。
        score_threshold: misretrieval 判定スコア閾値（デフォルト 0.3）。
        staleness_days: context_drift 判定の陳腐化日数閾値（デフォルト 30）。
        post_retrieval_window_sec: corruption 判定の検索後ウィンドウ（秒）。

    Returns:
        Markdown 行リスト。問題なければ空リスト。
    """
    try:
        from episodic_store import query_relevant, HAS_DUCKDB
        from memory_temporal import parse_memory_temporal
        import memory_trace as _mt
    except ImportError:
        return []

    if not HAS_DUCKDB:
        return []

    # デフォルトキーワード（診断対象の基本語彙）
    _keywords = keywords or {"memory", "correction", "rule", "skill", "修正", "記憶"}

    # 1. episodic events を取得
    events = query_relevant(_keywords, project_path, limit=20)
    if not events:
        return []

    # 2. temporal を取得（auto-memory ディレクトリ内のファイルから）
    temporals: Dict[str, Any] = {}
    memory_dir = DATA_DIR / "memory"
    if memory_dir.is_dir():
        for md_file in sorted(memory_dir.glob("*.md")):
            try:
                temporal = parse_memory_temporal(md_file)
                # event_id との突合: source_correction_ids にある id を temporal として紐づける
                for src_id in temporal.get("source_correction_ids", []):
                    if src_id:
                        temporals[src_id] = temporal
            except Exception:
                pass

    # 3. corrections を読み込む
    corrections: List[Dict[str, Any]] = []
    _corrections_path = corrections_path or (DATA_DIR / "corrections.jsonl")
    if _corrections_path.is_file():
        try:
            for line in _corrections_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    corrections.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except (OSError, UnicodeDecodeError):
            pass

    # 4. 帰属診断
    errors = _mt.attribute_errors(
        events,
        temporals,
        corrections,
        score_threshold=score_threshold,
        staleness_days=staleness_days,
        post_retrieval_window_sec=post_retrieval_window_sec,
    )

    # 5. 可視化
    return _mt.build_memory_trace_section(errors)
