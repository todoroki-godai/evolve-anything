"""glossary_drift.py — CONTEXT.md（Ubiquitous Language 用語集）の drift 検出。

決定論・LLM 非依存。用語集が SoT（SPEC.md / CLAUDE.md 等）から腐る
（= jargon が増えたのに用語集に追記されない）ことを検出する。
spec-keeper の update フローが advisory として消費する。

検出する drift:
  - malformed_lines:    用語集テーブルのスキーマ不一致行（3列でない等）
  - duplicate_terms:    同一用語の重複定義
  - missing_first_seen: 初出（# 参照）が空のエントリ
  - undefined_terms:    SoT に出現する jargon 候補で用語集に未登録のもの

CLI:
  python3 scripts/lib/glossary_drift.py CONTEXT.md SPEC.md CLAUDE.md
      レポートを stdout に出力。drift があれば exit 1、なければ exit 0。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# evolve が CONTEXT.md を自動 seed する最小 jargon 候補数。これ未満なら seed しない
# （jargon の薄い PJ に空の用語集を作らない）。
SEED_MIN_CANDIDATES = 3

# jargon 候補: ALLCAPS 頭字語(2-6文字) または 内部に大文字を持つ CamelCase。
# 例: BES, RRF, BM25, MemTrace, DuckDB。先頭小文字の通常語は拾わない。
_CANDIDATE_RE = re.compile(r"\b([A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*|[A-Z]{2,6})\b")

# 同梱の常用英単語リスト（google-10000-english, public domain）。詳細は
# scripts/lib/data/NOTICE.md。jargon 候補の `.lower()` がこのリストに含まれるなら
# 一般英単語であり PJ 固有 jargon ではないと判定する（#567）。
_COMMON_WORDS_PATH = Path(__file__).resolve().parent / "data" / "common_english_words.txt"
_COMMON_WORDS_CACHE: frozenset[str] | None = None


def load_common_english_words() -> frozenset[str]:
    """同梱の常用英単語リストを lazy load する（1 回だけ・以降キャッシュ）。

    すべて小文字・1 行 1 語。`find_undefined_terms` が jargon 候補の `.lower()` を
    このリストと突合し、一般英単語（BEGIN/SELECT/INFO 等の ALLCAPS 化を含む）を
    除外する。stoplist の手動 denylist 個別列挙（モグラ叩き）の根治（#567）。
    """
    global _COMMON_WORDS_CACHE
    if _COMMON_WORDS_CACHE is None:
        try:
            with open(_COMMON_WORDS_PATH, encoding="utf-8") as f:
                _COMMON_WORDS_CACHE = frozenset(
                    line.strip().lower() for line in f if line.strip()
                )
        except FileNotFoundError:
            # データファイル不在でも検出は壊さない（stoplist のみで継続）。
            _COMMON_WORDS_CACHE = frozenset()
    return _COMMON_WORDS_CACHE


# 用語集に載せる価値の薄い汎用テック頭字語。undefined 判定から除外する。
# #353⑫: AWS/技術略語（ARN, CDK, SNS 等）が 46件ものノイズを出していたため denylist を拡張。
#
# #567: 一般英単語の FP は辞書フィルタ（load_common_english_words）が根治するため、
# `.lower()` が常用英単語リストに載る語（BEGIN/END/SELECT/FAILED/INFO/GROUP 等）は
# stoplist から除去した。ここに残すのは「辞書に載らない頭字語・固有名」のみ:
#   - 頭字語（API, JSON, AWS, CDK ...）= 辞書に小文字形が無い
#   - framework/サービス固有 CamelCase（TypeScript, CloudFront, DynamoDB ...）
#   - メタファイル名・PJ メタ語（CLAUDE, SPEC, README, ADR, SoT, PJ ...）
# 各グループは拡張しやすいよう明示的に分類する。
DEFAULT_STOPLIST: frozenset[str] = frozenset(
    {
        # 汎用テック頭字語（辞書に小文字形を持たない略語）
        "API", "CLI", "LLM", "JSON", "JSONL", "YAML", "HTML", "HTTP",
        "HTTPS", "URL", "URI", "SQL", "DB", "ID", "UUID", "PK", "TODO",
        "README", "SPEC", "CLAUDE", "ADR", "PR", "CI", "CD", "PJ", "SoT",
        "MCP", "SDK", "CC", "WoW", "ASCII",
        "ROI", "CJK", "NFD", "NaN", "LR", "GitHub",
        "E2E", "TDD", "SDD", "TTL", "CPU", "OSS", "UX",
        # SQL / 制御キーワード（辞書に無い略語のみ。INSERT/SELECT 等は辞書フィルタ）
        "INTO", "IGNORE",
        # skill-triage の状態・hook イベント名・ツール名（辞書に無いもの）
        "PreToolUse", "PostToolUse", "UserPromptSubmit", "AskUserQuestion",
        "MEMORY", "CHANGELOG",
        # 汎用の英大文字ストップワード（#337）で辞書に無いもの。
        "ENV", "TMP", "SRC", "DST",
        # サイズ単位（jargon でない・辞書に無い略語）
        "MB", "KB", "TB", "MD",
        # AWS / クラウドインフラ汎用略語（#353⑫）。辞書に小文字形を持たない略語。
        # PJ 固有語ではなく AWS サービス名・一般的インフラ用語のため除外する。
        "ARN", "CDK", "SNS", "SQS", "S3", "IAM", "VPC", "AWS",
        "EC2", "ECS", "EKS", "RDS", "DMS", "EMR", "KMS", "ACM",
        "ALB", "NLB", "ELB", "WAF", "ACL", "IGW", "AMI",
        "ECR", "EFS", "EBS", "SSM", "SES", "STS", "SLA", "SLO",
        "SLI", "GW",
        # git / メタ / 汎用語で辞書に無いもの。
        # IO/FP/FALLBACK/RM/SKILL は辞書に無い略語・メタ語。HEAD/HOLD/DEPRECATED は
        # 辞書フィルタで落ちるため除去した。
        "IO", "FP", "FALLBACK", "RM", "SKILL", "DEPRECATED",
        # 汎用ドキュメント／業務略語（#477-4）で辞書に無いもの。
        "PDF", "QA", "FAQ", "CSV", "XML", "TSV", "MVP", "KPI", "OKR",
        "PII", "GDPR", "FYI", "ETA", "WIP", "EOD",
    }
    # --- #554 追加: 汎用テック語（辞書に無いもののみ残す） ---
    # GET/POST/PUT/PATCH 等の HTTP メソッドは辞書フィルタで落ちるため除去。
    | frozenset(
        {
            # 汎用テックプロトコル・設計概念（辞書に無い略語）
            "JWT", "CRUD", "SHA", "RPC", "gRPC", "SOAP",
            "OAuth", "SAML", "CORS", "CSRF", "XSS",
            # クラウド配信・運用概念（辞書に無い略語）
            "CDN", "IaC", "SaaS", "PaaS", "IaaS",
            # 言語名 CamelCase（regex が CamelCase を候補にするため明示除外）
            "TypeScript", "GraphQL", "OpenAPI", "WebSocket",
            "PostgreSQL", "MongoDB", "Redis", "Elasticsearch",
        }
    )
    # --- #554 追加: AWS サービス名 CamelCase ---
    # CloudFront / DynamoDB / EventBridge 等は CamelCase として regex に拾われる。
    # 辞書に小文字形が無い AWS 公式サービス名のため除外する。
    # Lambda は辞書に載るため除去（辞書フィルタが落とす）。
    | frozenset(
        {
            "CloudFront", "DynamoDB", "EventBridge",
            "Cognito", "CloudWatch", "CloudFormation", "CloudTrail",
            "Kinesis", "Athena", "Glue", "Redshift",
            "CodeBuild", "CodePipeline", "CodeDeploy",
            "StepFunctions",
        }
    )
    # --- #554-2 由来で辞書に無い語のみ残す ---
    # #554-2 の SQL/ログ/ステータス語の大半（BEGIN/END/SELECT/FAILED/INFO/GROUP/
    # ON/OFF/WEB/APP/...）は辞書フィルタ（#567）が根治するため除去した。
    # 辞書に載らない略語・固有頭字語のみここに残す。
    | frozenset(
        {
            # 辞書に無い SQL/制御語
            "ROLLBACK",
            # 辞書に無いステータス略語
            "SKIPPED", "FIXME", "XXX", "WARN", "STG", "PROD",
            # 辞書に無い汎用テック略語
            "SPA", "BFF", "RAG", "ORM", "OWASP", "MVC", "DAO", "DTO",
            "VM", "OS",
        }
    )
)

_SEP_RE = re.compile(r"^[:\-\s|]+$")

# Slack ID（channel C0.../app A0.../user U0... 等）は jargon でなく ID 文字列（#337）。
# 全大文字+数字で 0 始まり・9文字以上。DuckDB 等の CamelCase 固有語は小文字を含むため誤除外しない。
_SLACK_ID_RE = re.compile(r"^[ABCDGTUW]0[A-Z0-9]{7,}$")


# auto 生成エントリの未検証マーカー。初出列に置く（真の初出も人間確認待ちのため）。
# 人が初出を `#NNN`/`ADR-NNN` に書き換えるとマーカーが消え検証完了扱いになる。
UNVERIFIED_MARKER = "⚠UNVERIFIED"


@dataclass
class GlossaryEntry:
    term: str
    meaning: str
    first_seen: str
    unverified: bool = False


@dataclass
class GlossaryReport:
    entries: list[GlossaryEntry] = field(default_factory=list)
    malformed_lines: list[tuple[int, str]] = field(default_factory=list)
    duplicate_terms: list[str] = field(default_factory=list)
    missing_first_seen: list[str] = field(default_factory=list)
    undefined_terms: list[str] = field(default_factory=list)
    unverified_terms: list[str] = field(default_factory=list)

    def has_drift(self) -> bool:
        """構造的 drift（用語集自体の整合性破れ）。CLI の gate 対象。

        undefined_terms / unverified_terms はヒューリスティック or 人間確認待ちで
        gate しない（has_undefined / has_unverified で別に取る）。オオカミ少年化を避ける。
        """
        return bool(
            self.malformed_lines
            or self.duplicate_terms
            or self.missing_first_seen
        )

    def has_undefined(self) -> bool:
        """SoT に出現する未登録 jargon 候補がある（advisory・非 gate）。"""
        return bool(self.undefined_terms)

    def has_unverified(self) -> bool:
        """auto 生成され人間検証待ちのエントリがある（advisory・非 gate）。"""
        return bool(self.unverified_terms)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_glossary(path: str) -> tuple[list[GlossaryEntry], list[tuple[int, str]]]:
    """CONTEXT.md の Markdown テーブルから用語エントリを抽出する。

    返り値: (entries, malformed_lines)。malformed_lines は (行番号, 生の行)。
    ヘッダ行（用語/意味を含む）と区切り行（---）はスキップする。
    """
    entries: list[GlossaryEntry] = []
    malformed: list[tuple[int, str]] = []
    raw = ""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        return entries, malformed

    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if _SEP_RE.match(stripped):
            continue
        cells = _split_row(stripped)
        # ヘッダ行
        if any("用語" in c for c in cells) and any("意味" in c for c in cells):
            continue
        if len(cells) != 3 or not cells[0]:
            malformed.append((lineno, line))
            continue
        unverified = "UNVERIFIED" in cells[2].upper()
        entries.append(
            GlossaryEntry(
                term=cells[0],
                meaning=cells[1],
                first_seen=cells[2],
                unverified=unverified,
            )
        )
    return entries, malformed


def find_undefined_terms(
    entries: list[GlossaryEntry],
    source_paths: list[str],
    *,
    stoplist: frozenset[str] = DEFAULT_STOPLIST,
) -> list[str]:
    """SoT に出現する jargon 候補で用語集に未登録のものを返す（ソート済み・一意）。"""
    defined = {e.term for e in entries}
    common_words = load_common_english_words()  # #567: 一般英単語の辞書フィルタ
    found: set[str] = set()
    for sp in source_paths:
        try:
            with open(sp, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            continue
        for m in _CANDIDATE_RE.finditer(text):
            tok = m.group(1)
            if tok in defined or tok in stoplist:
                continue
            # #567: `.lower()` が常用英単語（BEGIN/SELECT/INFO 等）なら jargon でない。
            if tok.lower() in common_words:
                continue
            if _SLACK_ID_RE.match(tok):  # Slack ID は jargon でない（#337）
                continue
            found.add(tok)
    return sorted(found)


def check_glossary(
    context_path: str,
    source_paths: list[str],
    *,
    stoplist: frozenset[str] = DEFAULT_STOPLIST,
) -> GlossaryReport:
    entries, malformed = parse_glossary(context_path)

    seen: set[str] = set()
    dups: list[str] = []
    missing: list[str] = []
    unverified: list[str] = []
    for e in entries:
        if e.term in seen and e.term not in dups:
            dups.append(e.term)
        seen.add(e.term)
        if not e.first_seen.strip():
            missing.append(e.term)
        if e.unverified:
            unverified.append(e.term)

    undefined = find_undefined_terms(entries, source_paths, stoplist=stoplist)
    return GlossaryReport(
        entries=entries,
        malformed_lines=malformed,
        duplicate_terms=dups,
        missing_first_seen=missing,
        undefined_terms=undefined,
        unverified_terms=unverified,
    )


def write_context_seed(
    context_path: str,
    rows: list[tuple[str, str]],
    *,
    project_name: str | None = None,
    overwrite: bool = False,
) -> str:
    """auto 生成された用語集 seed を CONTEXT.md に書き出す（決定論・非破壊）。

    rows: (term, meaning) のリスト。意味は LLM が埋めた推定値を渡す。
    初出列には UNVERIFIED_MARKER を置き、人間検証待ちであることを示す
    （以後の evolve/audit が `unverified_terms` advisory で確認を促す）。

    既存ファイルがある場合 overwrite=False なら FileExistsError を投げる
    （silent wipe / 人手編集の上書き防止、ADR-027 の無破壊方針）。
    整形のみ担当し LLM は呼ばない。
    """
    if not overwrite and os.path.exists(context_path):
        raise FileExistsError(
            f"{context_path} は既に存在します（非破壊のため上書きしません）"
        )
    name = project_name or os.path.basename(os.path.dirname(os.path.abspath(context_path)))
    lines = [
        f"# {name} — Ubiquitous Language（用語集）",
        "",
        "このプロジェクト固有の jargon を 1 語で decode するための共有言語（Eric Evans, DDD）。",
        "",
        f"> このファイルは evolve により **auto 生成された seed** です。各行の意味は LLM 推定で、",
        f"> 初出列に `{UNVERIFIED_MARKER}` が付いています。**意味を確認し、初出を `#NNN`/`ADR-NNN` に",
        f"> 書き換えてマーカーを外す**と検証完了です。腐った用語集は無いより悪いので、誤りは消してください。",
        "",
        "| 用語 | 意味 | 初出 |",
        "|------|------|------|",
    ]
    for term, meaning in rows:
        # naive パーサは `\|` をアンエスケープしないため、全角 ｜ に置換してテーブル破壊を防ぐ
        safe_meaning = (meaning or "").replace("|", "｜").strip()
        lines.append(f"| {term} | {safe_meaning} | {UNVERIFIED_MARKER} |")
    lines.append("")
    with open(context_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return context_path


def _format_report(report: GlossaryReport) -> str:
    lines = [f"用語集エントリ: {len(report.entries)} 件"]
    if report.malformed_lines:
        lines.append(f"  ⚠ スキーマ不一致行: {len(report.malformed_lines)}")
        for ln, raw in report.malformed_lines:
            lines.append(f"      L{ln}: {raw.strip()}")
    if report.duplicate_terms:
        lines.append(f"  ⚠ 重複定義: {', '.join(report.duplicate_terms)}")
    if report.missing_first_seen:
        lines.append(f"  ⚠ 初出欠落: {', '.join(report.missing_first_seen)}")
    if not report.has_drift():
        lines.append("  ✓ 構造 drift なし")
    if report.unverified_terms:
        lines.append(
            f"  ℹ advisory: auto 生成され未検証のエントリ ({len(report.unverified_terms)}) "
            f"— 意味を確認し初出を埋めて {UNVERIFIED_MARKER} を外す: "
            f"{', '.join(report.unverified_terms)}"
        )
    if report.undefined_terms:
        lines.append(
            f"  ℹ advisory: 用語集未登録の jargon 候補 ({len(report.undefined_terms)}) "
            f"— 追記を検討: {', '.join(report.undefined_terms)}"
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: glossary_drift.py CONTEXT.md [SOURCE.md ...]", file=sys.stderr)
        return 2
    context_path, source_paths = argv[0], argv[1:]
    report = check_glossary(context_path, source_paths)
    print(_format_report(report))
    return 1 if report.has_drift() else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
