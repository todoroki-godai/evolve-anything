"""memory_guard.py — 記憶・correction 書込境界の runtime 汚染検出（#108）。

`skill_vuln_scan` の較正済みパターン（combo 必須・FP 較正済み）を **import で再利用** し、
単一テキスト（auto-memory 本文 / correction テキスト）を行単位でスキャンする免疫層。
記憶の書込境界（`auto_memory_broker.ingest_memory_results`）と audit の read-time
再スキャン（`audit/sections_memory.build_memory_contamination_section`）から呼ばれる。

決定論・LLM 非依存・読み取りのみ（ファイル / store 書込なし）。

背景（記憶汚染 = memory poisoning）:
  auto-memory は correction から LLM が要約を生成して memory ファイルに **自動書き込み** する。
  その要約が prompt injection（「これまでの指示を無視」等）や secret exfil の payload を
  含んでいると、後で context に注入される記憶に汚染が居座る。本モジュールは書込の直前に
  検査し、汚染レコードを弾く（＝免疫層）。

FP 較正（このリポジトリの鉄則 = 偽陽性に極めて厳格。誤 reject は正当な記憶の**無音喪失**）:
- reject 対象は **combo 必須・FP 較正済みの高信頼カテゴリのみ**（`_REJECT_CATEGORIES`）。
  記憶汚染の核心は「指示レイヤーへの注入」= ``prompt_injection`` と、明確な情報漏洩 payload
  である ``secret_exfil`` の 2 つに限定する。
- ``remote_exec`` / ``destructive`` / ``overbroad_tools`` は scan_text には出るが reject には
  昇格しない（記憶本文が危険コマンドを**説明**する pitfall メモ等で FP しやすく、かつ記憶は
  散文であって実行されないため）。これらは audit の advisory 表示にのみ回す。
- 単独キーワード一致では reject しない（skill_vuln_scan の combo 較正をそのまま継承）。

緊急避難: env ``EVOLVE_MEMORY_GUARD=warn`` で reject → warn（書込継続・警告のみ）に降格。
不正値は warn へ de-escalate（store_write の ``_resolve_guard_mode`` と同型）。

【記憶遷移検証（#93・TRUSTMEM Memory Transition Verifier の決定論移植）】
TRUSTMEM (arXiv 2606.25161) の Memory Transition Verifier（網羅性/保存性/忠実性の3観点）を
RL を含まない決定論版として移植する。auto-memory は同名（frontmatter ``name`` 一致）の
既存エントリを「更新」として上書きすることは物理的にない（``_write_entry_file`` は
排他作成・上書き不可）が、同名の新規エントリが並存すると論理的には「同じ概念の更新」で
あり、既存エントリの重要 fact を消す/矛盾するリスクがある。``inspect_transition`` は
書込前にこの同名衝突を検出し、以下3観点で決定論検証する（過剰検出を避ける保守側較正）:

- coverage    : 既存 body の「重要行」が新 body でごっそり失われていないか
                （difflib 類似度ベースの行単位 retained 判定・比率が閾値未満なら reject 候補）
- preservation: frontmatter の構造化フィールド（broker が事後追加するフィールドを除く）が
                既存値から矛盾する値に上書きされていないか（辞書比較）
- fidelity    : 冒頭行の極性反転（同名かつ高い文字列類似度の場合のみ判定・幻覚疑い）

同名の既存エントリが無ければ検証対象外（``checked=False``）で block しない
（＝大多数の通常書込は無関係で FP リスクゼロ）。reject 実績は
``memory_transition_checks.jsonl``（store_write barrier 経由・writer は auto_memory_broker）
に1件ずつ記録され、``transition_check_counts`` が audit 用に集計する。
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# scripts/lib を sys.path に載せて skill_vuln_scan を import する（auto_memory_broker と同慣習）。
_lib_dir = Path(__file__).resolve().parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

# skill_vuln_scan の較正済み行スキャナを再利用（パターン定数の複製をしない・単一ソース）。
from skill_vuln_scan import _scan_line as _vuln_scan_line  # noqa: E402
from frontmatter import find_frontmatter_close as _find_frontmatter_close  # noqa: E402
import yaml  # noqa: E402

# read 層 alias fold（legacy 旧 slug も当 PJ として拾う・verbosity/subagent_traces と同慣習）。
try:
    from store_read_union import pj_slug_match as _pj_slug_match  # noqa: E402
except ImportError:  # pragma: no cover - パス未解決時のフォールバック
    def _pj_slug_match(rec_slug, slug):  # type: ignore
        return rec_slug == slug

# reject 対象カテゴリ（記憶汚染の核心＝高信頼 combo のみ）。
# remote_exec / destructive / overbroad_tools は advisory 表示のみで書込は止めない。
_REJECT_CATEGORIES = frozenset({"prompt_injection", "secret_exfil"})

# guard モード（store_write と同型）。
_VALID_GUARD_MODES = ("warn", "reject")
_DEFAULT_GUARD_MODE = "reject"

# 対象拡張子（audit の memory dir 走査用。auto-memory は .md）。
_MEMORY_SCAN_EXTENSIONS = {".md"}


@dataclass(frozen=True)
class ContaminationHit:
    """1 件の汚染ヒット（`skill_vuln_scan.Finding` の text-scan 版）。

    category:   prompt_injection / secret_exfil / remote_exec / destructive / overbroad_tools
    severity:   HIGH / MEDIUM / LOW
    pattern_id: マッチした pattern 識別子
    snippet:    マッチ行を strip し truncate したもの
    line:       1 始まりの行番号
    filename:   memory dir 走査時のファイル名（単一テキスト走査では空文字）
    """

    category: str
    severity: str
    pattern_id: str
    snippet: str
    line: int
    filename: str = ""


@dataclass
class MemoryContaminationReport:
    """memory dir 走査結果（audit section 用）。

    applicable:    memory dir が存在し走査対象があったか（無ければ False＝沈黙）
    scanned_files: 走査した .md ファイル数
    hits:          reject 対象カテゴリのヒット（(filename, line, pattern_id) で安定ソート）
    """

    applicable: bool = False
    scanned_files: int = 0
    hits: List[ContaminationHit] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.hits)


def scan_text(text: str) -> List[ContaminationHit]:
    """text を行単位でスキャンし、全カテゴリの汚染ヒットを返す（決定論・読取のみ）。

    skill_vuln_scan の較正済み combo パターンを再利用する。空 / 非 str は []。
    """
    if not text or not isinstance(text, str):
        return []
    hits: List[ContaminationHit] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        for f in _vuln_scan_line("<text>", idx, line):
            hits.append(
                ContaminationHit(
                    category=f.category,
                    severity=f.severity,
                    pattern_id=f.pattern_id,
                    snippet=f.snippet,
                    line=idx,
                )
            )
    return hits


def reject_hits(text: str) -> List[ContaminationHit]:
    """scan_text のうち reject 対象カテゴリ（高信頼 combo）のヒットだけを返す。"""
    return [h for h in scan_text(text) if h.category in _REJECT_CATEGORIES]


def resolve_guard_mode(explicit: Optional[str] = None) -> str:
    """明示指定 > env EVOLVE_MEMORY_GUARD > 既定 reject。

    不正値は warn へ de-escalate（typo を理由に reject へ昇格させない＝誤爆で正当記憶を
    無音喪失させない安全側）。store_write._resolve_guard_mode と同型。
    """
    mode = (
        explicit
        if explicit is not None
        else os.environ.get("EVOLVE_MEMORY_GUARD", _DEFAULT_GUARD_MODE)
    )
    return mode if mode in _VALID_GUARD_MODES else "warn"


def inspect_content(text: str, *, guard_mode: Optional[str] = None) -> dict:
    """記憶本文の書込可否を判定する。

    Returns:
        {
          "hits": [ContaminationHit...],  # reject 対象ヒット（warn でも可視化＝無音にしない）
          "block": bool,                  # reject モードかつ reject 対象ヒットありなら True
          "mode": str,                    # 実効 guard モード
        }
    warn モードでは block=False（書込は継続）だが hits は返す（呼び出し元が記録・警告できる）。
    """
    mode = resolve_guard_mode(guard_mode)
    hits = reject_hits(text)
    block = bool(hits) and mode == "reject"
    return {"hits": hits, "block": block, "mode": mode}


def scan_memory_dir(memory_dir: Path) -> MemoryContaminationReport:
    """memory dir 配下の .md を read-time スキャンし、既に着地した汚染を検出する（audit 用）。

    書込境界（broker）を通らずに紛れ込んだ / guard 導入前に書かれた汚染記憶を surface する。
    reject 対象カテゴリのみを対象にし、write 境界の「汚染」の定義と一致させる。
    """
    memory_dir = Path(memory_dir)
    if not memory_dir.is_dir():
        return MemoryContaminationReport(applicable=False, scanned_files=0, hits=[])

    hits: List[ContaminationHit] = []
    scanned = 0
    for path in sorted(memory_dir.rglob("*")):
        if not path.is_file() or path.suffix not in _MEMORY_SCAN_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        fname = path.name
        for h in reject_hits(text):
            hits.append(
                ContaminationHit(
                    category=h.category,
                    severity=h.severity,
                    pattern_id=h.pattern_id,
                    snippet=h.snippet,
                    line=h.line,
                    filename=fname,
                )
            )

    hits.sort(key=lambda h: (h.filename, h.line, h.pattern_id))
    return MemoryContaminationReport(
        applicable=scanned > 0, scanned_files=scanned, hits=hits
    )


# ─────────────────────────────────────────────────────────────────
# 記憶遷移検証（#93・TRUSTMEM Memory Transition Verifier の決定論移植）
# ─────────────────────────────────────────────────────────────────

# preservation: 比較対象は allowlist（構造的に安定しているべきフィールドのみ）。
# description / importance は自然言語の要約・再評価値であり「同じ概念の更新」でも
# 正当に書き換わる（memory 運用の MUST: 「Keep name/description/type up-to-date」）。
# これらを denylist 式で比較すると通常の健全な更新まで reject する重大 FP になるため、
# metadata.type（記憶の種別軸=user/feedback/project/reference）のみを比較する。
# 種別が同名の記憶で反転するのは「別概念を誤って同名で書いた」強いシグナルであり、
# 誤 reject リスクが低い。


def _extract_metadata_type(fm: dict) -> Optional[str]:
    meta = fm.get("metadata")
    if isinstance(meta, dict):
        t = meta.get("type")
        return t if isinstance(t, str) else None
    return None


# coverage: 判定対象とする body 行の最小長（見出し記号等の短い行はノイズになるため除外）。
_MIN_SIGNIFICANT_LINE_LEN = 8
# coverage: old body の重要行が最低何行あれば判定するか（1行のみは言い換えで簡単に
# 0/1 になり誤検出しやすいため保守的にスキップする）。
_COVERAGE_MIN_OLD_LINES = 2
# coverage: 1行が新 body の何らかの行と「同じ内容」とみなす difflib 類似度の下限。
_COVERAGE_LINE_SIM_MIN = 0.5
# coverage: 既存重要行のうち新body に残っている比率がこれ未満なら reject 候補
# （誤 reject を避けるため低め＝大量欠落のみ検出）。
_COVERAGE_MIN_RATIO = 0.3

# fidelity: 冒頭行が「同じ主張」とみなす類似度の下限（高めにして誤爆を避ける）。
_FIDELITY_SIMILARITY_MIN = 0.6
_NEGATION_MARKERS_JA = ("ない", "禁止", "だめ", "ダメ", "不可", "やめる", "せずに", "NG")
_NEGATION_MARKERS_EN = ("not", "never", "avoid", "cannot", "can't", "don't", "stop")
_EN_NEGATION_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _NEGATION_MARKERS_EN) + r")\b",
    re.IGNORECASE,
)

# 遷移検証イベントの永続化ストア名（store_registry 登録名と一致させる単一ソース）。
TRANSITION_STORE_NAME = "memory_transition_checks.jsonl"


@dataclass(frozen=True)
class TransitionIssue:
    """遷移検証（coverage/preservation/fidelity）の1件の指摘。

    axis:   "coverage" | "preservation" | "fidelity"
    detail: 1行の人間可読な理由
    """

    axis: str
    detail: str


@dataclass
class TransitionResult:
    """``verify_transition`` / ``inspect_transition`` の結果。

    checked:      同名の既存エントリが見つかり実際に比較したか
    matched_name: 一致した frontmatter name（未一致なら None）
    issues:       指摘（空なら問題なし）
    """

    checked: bool = False
    matched_name: Optional[str] = None
    issues: List[TransitionIssue] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)


def _parse_frontmatter_text(text: str) -> dict:
    """raw text（未書込の LLM 生成テキスト等）から YAML frontmatter を辞書として抽出する。

    ``frontmatter.parse_frontmatter`` はファイルパス専用のため、書込前のテキストを
    そのまま検査したい本モジュール向けに text 版を用意する（区切り探索は
    ``find_frontmatter_close`` を共有し実装を複製しない）。
    """
    if not text or not text.startswith("---"):
        return {}
    end = _find_frontmatter_close(text)
    if end == -1:
        return {}
    yaml_str = text[3:end].strip()
    if not yaml_str:
        return {}
    try:
        parsed = yaml.safe_load(yaml_str)
        return parsed if isinstance(parsed, dict) else {}
    except yaml.YAMLError:
        return {}


def _body_from_text(text: str) -> str:
    """raw text から frontmatter を除いた本文部分を返す（``frontmatter.count_content_lines``
    と同じ slice 規約を再利用し、閉じ ``---`` 直後の空行も1行分除去する）。
    """
    if not text or not text.startswith("---"):
        return text or ""
    end = _find_frontmatter_close(text)
    if end == -1:
        return text
    after_close = end + 4  # "\n---" の直後（閉じ行の改行を含めて1つ先へ）
    if after_close < len(text) and text[after_close] == "\n":
        after_close += 1  # frontmatter 直後の空行を1行分スキップ
    return text[after_close:].lstrip("\n")


def find_existing_entry_by_name(memory_dir: Path, name: str) -> Optional[Path]:
    """memory_dir 内で frontmatter ``name`` が一致する既存エントリを探す（更新対象の特定）。

    複数マッチする場合は sorted 順で最後（最新のファイル名）を返す。name が空 / memory_dir
    が存在しない場合は None（判定不能なので検証対象外＝安全側）。
    """
    if not name or not Path(memory_dir).is_dir():
        return None
    memory_dir = Path(memory_dir)
    candidates: List[Path] = []
    for path in sorted(memory_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = _parse_frontmatter_text(text)
        if fm.get("name") == name:
            candidates.append(path)
    return candidates[-1] if candidates else None


def _significant_lines(body: str) -> List[str]:
    return [
        ln.strip()
        for ln in body.splitlines()
        if len(ln.strip()) >= _MIN_SIGNIFICANT_LINE_LEN
    ]


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _is_retained(old_line: str, new_lines: List[str]) -> bool:
    return any(_similarity(old_line, nl) >= _COVERAGE_LINE_SIM_MIN for nl in new_lines)


def _first_nonempty_line(body: str) -> str:
    for ln in body.splitlines():
        s = ln.strip()
        if s:
            return s
    return ""


def _has_negation(line: str) -> bool:
    if any(m in line for m in _NEGATION_MARKERS_JA):
        return True
    return bool(_EN_NEGATION_RE.search(line))


def _detect_negation_conflict(old_body: str, new_body: str) -> Optional[str]:
    """冒頭行の極性反転を検出する（fidelity・幻覚疑い）。

    誤検出回避のため「冒頭行同士が高い文字列類似度（主語が同じ疑い）」かつ
    「一方にのみ否定マーカーがある」場合のみ検出する。
    """
    old_first = _first_nonempty_line(old_body)
    new_first = _first_nonempty_line(new_body)
    if not old_first or not new_first:
        return None
    if _has_negation(old_first) == _has_negation(new_first):
        return None  # 両方肯定 or 両方否定 → 極性反転なし
    similarity = _similarity(old_first, new_first)
    if similarity < _FIDELITY_SIMILARITY_MIN:
        return None  # 主語が違う可能性が高く誤検出回避のため対象外
    return (
        f"冒頭行の極性が反転している疑い（類似度 {similarity:.2f}）: "
        f"旧={old_first!r} / 新={new_first!r}"
    )


def verify_transition(new_text: str, old_text: str) -> TransitionResult:
    """new_text（これから書く内容）と old_text（既存の同名エントリ）を coverage/
    preservation/fidelity の3観点で決定論比較する。

    保守的方針: 疑わしきは reject しない。判定は文字列/構造比較のみ（LLM 不使用）。
    """
    issues: List[TransitionIssue] = []

    old_fm = _parse_frontmatter_text(old_text)
    new_fm = _parse_frontmatter_text(new_text)
    old_body = _body_from_text(old_text)
    new_body = _body_from_text(new_text)

    # preservation: allowlist フィールド（metadata.type）が矛盾する値に上書きされて
    # いないか。description/importance は自然に書き換わりうるため対象外（over-detection
    # 回避・#93 dogfood で実際に FP を踏んだ較正）。
    old_type = _extract_metadata_type(old_fm)
    new_type = _extract_metadata_type(new_fm)
    if old_type and new_type and old_type != new_type:
        issues.append(TransitionIssue(
            axis="preservation",
            detail=f"metadata.type changed: {old_type!r} -> {new_type!r}",
        ))

    # coverage: 既存本文の重要行が新本文でごっそり失われていないか。
    old_lines = _significant_lines(old_body)
    if len(old_lines) >= _COVERAGE_MIN_OLD_LINES:
        new_lines = _significant_lines(new_body)
        retained = sum(1 for ln in old_lines if _is_retained(ln, new_lines))
        ratio = retained / len(old_lines)
        if ratio < _COVERAGE_MIN_RATIO:
            issues.append(TransitionIssue(
                axis="coverage",
                detail=(
                    f"existing body retained {retained}/{len(old_lines)} lines "
                    f"(ratio={ratio:.2f} < {_COVERAGE_MIN_RATIO})"
                ),
            ))

    # fidelity: 冒頭行の極性反転（同一主語の疑いが強い場合のみ）。
    conflict = _detect_negation_conflict(old_body, new_body)
    if conflict:
        issues.append(TransitionIssue(axis="fidelity", detail=conflict))

    return TransitionResult(
        checked=True,
        matched_name=new_fm.get("name") or old_fm.get("name"),
        issues=issues,
    )


def inspect_transition(
    new_text: str, memory_dir: Path, *, guard_mode: Optional[str] = None
) -> dict:
    """new_text の書込可否を、既存 memory dir との遷移検証で判定する。

    Returns:
        {
          "checked": bool,               # 同名の既存エントリが見つかり比較したか
          "matched_name": Optional[str],
          "issues": [TransitionIssue...],
          "block": bool,                 # reject モードかつ issues ありなら True
          "mode": str,
        }
    同名の既存エントリが無ければ checked=False・block=False（検証対象外）。
    """
    mode = resolve_guard_mode(guard_mode)
    memory_dir = Path(memory_dir)
    new_fm = _parse_frontmatter_text(new_text)
    name = new_fm.get("name")
    name = name if isinstance(name, str) and name.strip() else None
    if not name:
        return {"checked": False, "matched_name": None, "issues": [], "block": False, "mode": mode}

    existing_path = find_existing_entry_by_name(memory_dir, name)
    if existing_path is None:
        return {"checked": False, "matched_name": name, "issues": [], "block": False, "mode": mode}

    try:
        old_text = existing_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"checked": False, "matched_name": name, "issues": [], "block": False, "mode": mode}

    result = verify_transition(new_text, old_text)
    block = result.has_issues and mode == "reject"
    return {
        "checked": True,
        "matched_name": result.matched_name,
        "issues": result.issues,
        "block": block,
        "mode": mode,
    }


def _read_transition_events(data_dir: Optional[Path] = None) -> List[dict]:
    """``memory_transition_checks.jsonl`` を読み取る（read-only 純度・ファイルを作らない）。

    ``rl_common.DATA_DIR`` は呼び出し時（call-time）に live 参照する（import 時コピーは
    monkeypatch/env 追従が壊れる既知 pitfall・#96。store_write の書込先と一致させるため
    ここも同じ属性を同じタイミングで見る）。
    """
    if data_dir is not None:
        base = Path(data_dir)
    else:
        try:
            import rl_common
            base = rl_common.DATA_DIR
        except ImportError:  # pragma: no cover - パス未解決時のフォールバック
            base = Path.home() / ".claude" / "evolve-anything"
    path = base / TRANSITION_STORE_NAME
    if not path.exists():
        return []
    out: List[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:
        return []
    return out


def transition_check_counts(slug: str, *, data_dir: Optional[Path] = None) -> Dict[str, int]:
    """当 PJ スコープの遷移検証イベントを集計する（audit の maintain 軸 evidence 用）。

    checked:  同名衝突が見つかり実際に比較したイベント数
    rejected: そのうち reject（block=True）だった件数
    """
    checked = 0
    rejected = 0
    for rec in _read_transition_events(data_dir):
        if not _pj_slug_match(rec.get("pj_slug"), slug):
            continue
        checked += 1
        if rec.get("rejected"):
            rejected += 1
    return {"checked": checked, "rejected": rejected}
