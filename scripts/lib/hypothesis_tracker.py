"""仮説ツリーの JSONL 永続化モジュール（VeriTrace Phase 1）。

データファイル: DATA_DIR / hypothesis_{session_id}.jsonl
  - DATA_DIR は CLAUDE_PLUGIN_DATA 環境変数で上書き可能
    (未設定時: ~/.claude/evolve-anything/)

公開関数:
  save_hypothesis      -- 仮説を JSONL に追記
  load_hypotheses      -- セッションの仮説一覧を返す
  update_confidence    -- 証拠を追加して confidence を更新
  detect_contradiction -- evidence_against が3件以上の active 仮説ペアを返す
"""
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

# ── DATA_DIR（テスト時は monkeypatch.setattr で差し替え）────────────────────
_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR: Path = (
    Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "evolve-anything"
)

_CONFIDENCE_STEP_SUPPORTING = 0.1
_CONFIDENCE_STEP_AGAINST = 0.15
_CONTRADICTION_EVIDENCE_THRESHOLD = 3


@dataclass
class Hypothesis:
    """仮説の単一エントリ。"""

    hypothesis_id: str
    """仮説の識別子（例: "h1", "h2"）。"""

    statement: str
    """仮説の内容。"""

    confidence: float
    """確信度（0.0-1.0）。"""

    status: str
    """ステータス: "active" | "confirmed" | "refuted" | "suspended"。"""

    evidence_for: List[str] = field(default_factory=list)
    """支持する証拠のリスト。"""

    evidence_against: List[str] = field(default_factory=list)
    """反証のリスト。"""

    parent_hypothesis_id: Optional[str] = None
    """親仮説の ID（上位スキーマ統合時に使用）。"""

    created_at: str = ""
    """作成日時（ISO 8601）。"""

    updated_at: str = ""
    """更新日時（ISO 8601）。"""


def _hypothesis_file(session_id: str) -> Path:
    """セッション ID に対応する JSONL ファイルパスを返す。

    DATA_DIR がモジュール変数として参照されるため、monkeypatch.setattr による
    差し替えが即座に反映されるよう、呼び出し時に評価する。
    session_id は英数字・アンダースコア・ハイフン以外の文字を '_' に置換してパストラバーサルを防ぐ。
    """
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", session_id)
    return DATA_DIR / f"hypothesis_{safe_id}.jsonl"


def _ensure_dir() -> None:
    """データディレクトリが存在しない場合に作成する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    """現在時刻を UTC ISO 8601 文字列で返す。"""
    return datetime.now(timezone.utc).isoformat()


def _read_all(session_id: str) -> List[Hypothesis]:
    """JSONL ファイルから全仮説を読み込む。破損行はスキップ。"""
    path = _hypothesis_file(session_id)
    if not path.exists():
        return []
    hypotheses: List[Hypothesis] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    hypotheses.append(Hypothesis(**data))
                except (json.JSONDecodeError, TypeError):
                    continue
    except (OSError, PermissionError) as e:
        print(f"[hypothesis_tracker] read failed: {e}", file=sys.stderr)
    return hypotheses


def _write_all(session_id: str, hypotheses: List[Hypothesis]) -> None:
    """仮説リストを JSONL ファイルに書き戻す。

    write-then-rename パターン: tmp ファイルへ書いてから os.replace() でアトミックに切り替える。
    書き途中のプロセス終了でもデータが破損しない。

    Note:
        シングルプロセス・シングルスレッドからの呼び出しを前提とする。
        並行呼び出しは read-modify-write の競合を引き起こす可能性がある。

    TODO(perf): 現在は O(N) 全量書き直し。セッションあたり仮説数が数百を超える場合は
        append-only + 定期 compaction（例: 更新時に末尾追記し、load 時に重複排除）に
        移行を検討する。Phase 1 スコープでは 10-20 件程度を想定しており問題ない。
    """
    path = _hypothesis_file(session_id)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as tf:
            for h in hypotheses:
                tf.write(json.dumps(asdict(h), ensure_ascii=False) + "\n")
            tmp_path = Path(tf.name)
        try:
            tmp_path.chmod(0o600)
        except OSError as e:
            print(f"[hypothesis_tracker] chmod warning: {e}", file=sys.stderr)
        os.replace(tmp_path, path)
    except (OSError, PermissionError) as e:
        print(f"[hypothesis_tracker] write failed: {e}", file=sys.stderr)


def save_hypothesis(session_id: str, hypothesis: Hypothesis) -> None:
    """仮説を JSONL に追記する。同一 hypothesis_id が存在する場合は上書き。

    Args:
        session_id:  セッションの識別子。
        hypothesis:  保存する仮説エントリ。
    """
    _ensure_dir()

    now = _now_iso()
    if not hypothesis.created_at:
        hypothesis.created_at = now
    hypothesis.updated_at = now

    existing = _read_all(session_id)
    updated = False
    for i, h in enumerate(existing):
        if h.hypothesis_id == hypothesis.hypothesis_id:
            existing[i] = hypothesis
            updated = True
            break
    if not updated:
        existing.append(hypothesis)

    _write_all(session_id, existing)


def load_hypotheses(session_id: str) -> List[Hypothesis]:
    """セッションの仮説一覧を返す。

    Args:
        session_id: セッションの識別子。

    Returns:
        仮説のリスト（作成順）。
    """
    return _read_all(session_id)


def update_confidence(
    session_id: str,
    hypothesis_id: str,
    evidence: str,
    is_supporting: bool,
) -> Hypothesis:
    """証拠を追加して confidence を更新する。

    supporting 証拠の場合 confidence を +0.1 する。
    against 証拠の場合 confidence を -0.15 する。
    confidence は 0.0-1.0 にクランプされる。

    Args:
        session_id:     セッションの識別子。
        hypothesis_id:  更新する仮説の ID。
        evidence:       証拠の内容。
        is_supporting:  True なら支持証拠、False なら反証。

    Returns:
        更新後の Hypothesis。

    Raises:
        KeyError: hypothesis_id が存在しない場合。
    """
    hypotheses = _read_all(session_id)
    target: Optional[Hypothesis] = None
    for h in hypotheses:
        if h.hypothesis_id == hypothesis_id:
            target = h
            break

    if target is None:
        raise KeyError(
            f"[hypothesis_tracker] hypothesis_id '{hypothesis_id}' not found "
            f"in session '{session_id}'"
        )

    if is_supporting:
        target.evidence_for.append(evidence)
        target.confidence = min(1.0, target.confidence + _CONFIDENCE_STEP_SUPPORTING)
    else:
        target.evidence_against.append(evidence)
        target.confidence = max(0.0, target.confidence - _CONFIDENCE_STEP_AGAINST)

    target.updated_at = _now_iso()
    _write_all(session_id, hypotheses)
    return target


def detect_contradiction(hypotheses: List[Hypothesis]) -> List[Tuple[str, str]]:
    """evidence_against が threshold 件以上の active 仮説ペアを返す。

    両者がともに active かつ、いずれかが evidence_against を
    _CONTRADICTION_EVIDENCE_THRESHOLD 件以上持つ組み合わせを全列挙する。
    「一方が強く否定されている仮説とのペア」を矛盾として扱う。
    同一ペアは (小さい ID, 大きい ID) の順で重複排除される。

    Args:
        hypotheses: 仮説のリスト。

    Returns:
        矛盾ペアの (hypothesis_id_a, hypothesis_id_b) のリスト。
    """
    active = [h for h in hypotheses if h.status == "active"]

    pairs: List[Tuple[str, str]] = []
    seen = set()
    for i, h1 in enumerate(active):
        for h2 in active[i + 1 :]:
            if (
                len(h1.evidence_against) >= _CONTRADICTION_EVIDENCE_THRESHOLD
                or len(h2.evidence_against) >= _CONTRADICTION_EVIDENCE_THRESHOLD
            ):
                key = (
                    min(h1.hypothesis_id, h2.hypothesis_id),
                    max(h1.hypothesis_id, h2.hypothesis_id),
                )
                if key not in seen:
                    seen.add(key)
                    pairs.append(key)

    return pairs
