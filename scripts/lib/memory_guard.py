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
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# scripts/lib を sys.path に載せて skill_vuln_scan を import する（auto_memory_broker と同慣習）。
_lib_dir = Path(__file__).resolve().parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

# skill_vuln_scan の較正済み行スキャナを再利用（パターン定数の複製をしない・単一ソース）。
from skill_vuln_scan import _scan_line as _vuln_scan_line  # noqa: E402

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
