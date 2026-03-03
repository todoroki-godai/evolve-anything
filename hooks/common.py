#!/usr/bin/env python3
"""hooks 共通ユーティリティ — DATA_DIR, ensure_data_dir, append_jsonl, read_workflow_context, classify_prompt を提供する。"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".claude" / "rl-anything"

# ワークフロー文脈ファイルの有効期限（秒）
_WORKFLOW_CONTEXT_EXPIRE_SECONDS = 24 * 60 * 60  # 24時間


def ensure_data_dir() -> None:
    """ディレクトリが存在しない場合 MUST 自動作成する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def workflow_context_path(session_id: str) -> Path:
    """ワークフロー文脈ファイルのパスを返す。"""
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    return Path(tmpdir) / f"rl-anything-workflow-{session_id}.json"


def read_workflow_context(session_id: str) -> dict:
    """ワークフロー文脈ファイルを読み取り parent_skill/workflow_id を返す。

    文脈ファイルが存在しない、24時間以上経過、破損の場合は
    {"parent_skill": null, "workflow_id": null} を返す。
    セッションをブロックしない（MUST NOT）。
    """
    null_result = {"parent_skill": None, "workflow_id": None}
    try:
        ctx_path = workflow_context_path(session_id)
        if not ctx_path.exists():
            return null_result

        # 24時間 expire チェック
        mtime = ctx_path.stat().st_mtime
        age = datetime.now(timezone.utc).timestamp() - mtime
        if age > _WORKFLOW_CONTEXT_EXPIRE_SECONDS:
            return null_result

        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
        return {
            "parent_skill": ctx.get("skill_name"),
            "workflow_id": ctx.get("workflow_id"),
        }
    except Exception as e:
        print(f"[rl-anything] read_workflow_context error: {e}", file=sys.stderr)
        return null_result


# Agent prompt を簡易分類するキーワードマップ
# 辞書の順序がカテゴリ優先順位を決定する（先にマッチした方が採用される）
PROMPT_CATEGORIES = {
    "spec-review": ["spec", "requirement", "MUST", "quality check", r"review.*spec", "仕様", "要件"],
    "code-review": [r"review.*code", r"review.*change", r"review.*impl", "alignment", "verify", "コードレビュー", "変更確認", "差分"],
    "git-ops": ["merge", "commit", "push", "pull", "branch", "rebase", "cherry-pick", "revert", "stash", r"\btag\b", "マージ", "コミット", "プッシュ", "ブランチ", "取り込"],
    "deploy": ["deploy", "release", "staging", "production", "stg", "prod", "ci/cd", "pipeline", "デプロイ", "リリース", "本番", "環境"],
    "debug": ["debug", "log", "error", "fix", "issue", "bug", "修正", "バグ", "ログ", "エラー", "なおせ", "直せ", "直して", "原因", "調査"],
    "test": ["test", "assert", "pytest", "確認", "テスト", "検証", "動作", "ブラウザ"],
    "code-exploration": ["structure", "explore", "codebase", "directory", r"find.*file", "構造", "探索", "ファイル", "読んで", "見て"],
    "research": ["research", "best practice", "latest", "how to", "pattern", "調べて", "ベストプラクティス", "最新", "方法"],
    "implementation": ["implement", "create", "build", r"write.*code", r"add.*feature", "実装", "作成", "追加", "機能", "作って"],
    "config": ["config", "setting", "setup", "env", "設定", "構成", "セットアップ", "readme"],
    "conversation": ["お願い", "続けて", "ありがと", "よろしく", "はい", "いいえ", "ok", "いいよ", "やって", "進めて", "対応して"],
}


def classify_prompt(prompt: str) -> str:
    """prompt をキーワードベースで簡易分類する。"""
    prompt_lower = prompt.lower()
    for category, keywords in PROMPT_CATEGORIES.items():
        for kw in keywords:
            if re.search(kw, prompt_lower):
                return category
    return "other"


# 修正パターン: ユーザーのフィードバックを検出するための正規表現
# (pattern, correction_type, confidence)
CORRECTION_PATTERNS = [
    (r"^いや[、,.\s]|^いや違", "iya", 0.85),
    (r"^違う[、，,.\s]", "chigau", 0.85),
    (r"そうじゃなく[てけ]", "souja-nakute", 0.80),
    (r"^no[,. ]+", "no", 0.75),
    (r"^don't\b|^do not\b", "dont", 0.75),
    (r"^stop\b|^never\b", "stop", 0.80),
]

# 偽陽性フィルター: マッチしたら correction 検出を無効化
FALSE_POSITIVE_FILTERS = [
    r"[？\?]$",  # 末尾が疑問符
]


def detect_correction(text: str):
    """テキストから修正パターンを検出する。

    Returns:
        (correction_type, confidence) のタプル、または None（検出なし）。
    """
    text_stripped = text.strip()
    if not text_stripped:
        return None

    # 偽陽性チェック
    for fp in FALSE_POSITIVE_FILTERS:
        if re.search(fp, text_stripped):
            return None

    text_lower = text_stripped.lower()
    for pattern, correction_type, confidence in CORRECTION_PATTERNS:
        if re.search(pattern, text_lower) or re.search(pattern, text_stripped):
            return (correction_type, confidence)

    return None


def last_skill_path(session_id: str) -> Path:
    """直前スキル記録ファイルのパスを返す。"""
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    return Path(tmpdir) / f"rl-anything-last-skill-{session_id}.json"


def write_last_skill(session_id: str, skill_name: str) -> None:
    """直前スキル名を一時ファイルに書き出す。"""
    try:
        path = last_skill_path(session_id)
        data = {"skill_name": skill_name, "timestamp": datetime.now(timezone.utc).isoformat()}
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        print(f"[rl-anything] write_last_skill error: {e}", file=sys.stderr)


def read_last_skill(session_id: str) -> str | None:
    """直前スキル名を一時ファイルから読み取る。TTL 24時間。"""
    try:
        path = last_skill_path(session_id)
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        age = datetime.now(timezone.utc).timestamp() - mtime
        if age > _WORKFLOW_CONTEXT_EXPIRE_SECONDS:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("skill_name")
    except Exception as e:
        print(f"[rl-anything] read_last_skill error: {e}", file=sys.stderr)
        return None


def project_name_from_dir(project_dir: str) -> str:
    """プロジェクトディレクトリパスから末尾のディレクトリ名を返す。"""
    return Path(project_dir).name


def append_jsonl(filepath: Path, record: dict) -> None:
    """JSONL ファイルに1行追記する。失敗時はサイレント。"""
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[rl-anything] write failed: {e}", file=sys.stderr)
