#!/usr/bin/env python3
"""warning / stderr sink ヘルパー（evolve パッケージ分割 PR 3/8, refs #531）。

末端モジュール（他の evolve sub-module に依存しない）。`run_evolve` 内で
フェーズ実行中の警告・stderr を決定論的に捕捉し、self_analysis（evolve_introspect）が
`result["warnings"]` 経由で surface できるようにする sink を提供する。
振る舞いは __init__.py から移設したまま不変（#341 / #523-1）。
"""
import sys
import warnings as _warnings
from contextlib import contextmanager
from typing import Any, Dict, List


@contextmanager
def _capture_warnings(sink: List[Dict[str, Any]]):
    """フェーズ実行中に出た警告（scipy RuntimeWarning(NaN) 等）を sink に記録する（#341）。

    phase が throw しない警告は phase.error に乗らず stderr に流れて消える。
    self_analysis（evolve_introspect）が `result["warnings"]` を読んで surface できるよう、
    ここで決定論的にシリアライズして溜める。LLM 非依存・副作用は sink への append のみ。
    """
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        try:
            yield
        finally:
            for w in caught:
                try:
                    cat = getattr(w.category, "__name__", str(w.category))
                    sink.append({
                        "category": cat,
                        "message": str(w.message),
                        "filename": str(getattr(w, "filename", "")),
                        "lineno": int(getattr(w, "lineno", 0) or 0),
                    })
                except Exception:
                    # 記録失敗で本流を壊さない（警告は best-effort 観測）。
                    continue


class _TeeStderr:
    """stderr を元の stream へ素通しつつ書き込み内容を buffer に溜める tee（#523-1）。

    run_audit は Chaos/Constitutional のスキップ等を `print(..., file=sys.stderr)` で
    出すが、これは Python warnings ではないため `_capture_warnings` では拾えず、
    self_analysis.runtime_errors が「stderr 警告なし」と誤報告していた。本 tee で
    audit 実行中の stderr を捕捉し、`_warning_sink` 経由で self_analysis に渡す。
    """

    def __init__(self, original):
        self._original = original
        self._buf: List[str] = []

    def write(self, s):
        try:
            self._original.write(s)
        except Exception:
            pass
        if s:
            self._buf.append(s)
        return len(s) if s else 0

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def captured_lines(self) -> List[str]:
        text = "".join(self._buf)
        return [ln for ln in text.splitlines() if ln.strip()]


@contextmanager
def _capture_audit_stderr(sink: List[Dict[str, Any]]):
    """audit phase 実行中の stderr 行を sink に記録する（#523-1）。

    各行を `{"category": "stderr", "message": <行>}` として append し、
    evolve_introspect の `_detect_captured_warnings` が runtime_errors に昇格できるようにする。
    本流は壊さない（捕捉失敗しても素通しは継続）。
    """
    original = sys.stderr
    tee = _TeeStderr(original)
    sys.stderr = tee
    try:
        yield
    finally:
        sys.stderr = original
        for line in tee.captured_lines():
            sink.append({"category": "stderr", "message": line})
