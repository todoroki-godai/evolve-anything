#!/usr/bin/env python3
"""tool_durations 観測の後方互換 no-op shim（#426 で廃止）。

hook 登録はセッション開始時に固定されるため、v1.95.0 より前に開始した
セッションは削除済みの本ファイルを発火し続け、毎回 Errno 2 の blocking
error を表示する。実体を no-op で残すことでエラー表示だけを止める。
旧セッションが掃ける次々リリースで削除してよい。何も書き込まない。
"""
import sys

if __name__ == "__main__":
    sys.stdin.read()  # hook 入力を読み捨てる（パイプを閉じたまま放置しない）
    sys.exit(0)
