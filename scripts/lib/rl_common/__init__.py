#!/usr/bin/env python3
"""rl-anything 共通ユーティリティ。

hooks/common.py から移動。スキルスクリプト・フックスクリプトの両方から
参照される共有コード。hooks/ への直接 sys.path 操作を不要にする。

DATA_DIR, ensure_data_dir, append_jsonl, read_workflow_context,
classify_prompt, CORRECTION_PATTERNS 等を提供する。
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PLUGIN_DATA_ENV = os.environ.get("CLAUDE_PLUGIN_DATA", "")
DATA_DIR = Path(_PLUGIN_DATA_ENV) if _PLUGIN_DATA_ENV else Path.home() / ".claude" / "rl-anything"
CHECKPOINTS_DIR = DATA_DIR / "checkpoints"
CHECKPOINT_TTL_HOURS = 48.0

# ワークフロー文脈ファイルの有効期限（秒）
_WORKFLOW_CONTEXT_EXPIRE_SECONDS = 24 * 60 * 60  # 24時間

# InstructionsLoaded hook の定数
INSTRUCTIONS_LOADED_FLAG_PREFIX = "instructions_loaded_"
STALE_FLAG_TTL_HOURS = 24

# userConfig (CC v2.1.83 manifest.userConfig) は rl_common/config.py に集約済み。
# 後方互換のため `_parse_bool` / `load_user_config` / `is_user_config_explicit` /
# `USER_CONFIG_DEFAULTS` / `_USER_CONFIG_PREFIX` を re-export する (Phase 13 / Slice 1)。
from .config import (  # noqa: F401
    USER_CONFIG_DEFAULTS,
    _USER_CONFIG_PREFIX,
    _parse_bool,
    is_user_config_explicit,
    load_user_config,
)


def ensure_data_dir() -> None:
    """ディレクトリが存在しない場合 MUST 自動作成する。パーミッション 700。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DATA_DIR.chmod(0o700)
    except OSError as e:
        print(f"[rl-anything] chmod data dir warning: {e}", file=sys.stderr)


# checkpoint 系は rl_common/checkpoint.py に集約済み (Phase 13 / Slice 2)
# workflow_context / skill_stack / last_skill 系は rl_common/workflow.py に集約済み
from .checkpoint import (  # noqa: F401, E402
    _load_legacy_checkpoint,
    cleanup_old_checkpoints,
    find_latest_checkpoint,
)
from .workflow import (  # noqa: F401, E402
    _WORKFLOW_CONTEXT_EXPIRE_SECONDS,
    last_skill_path,
    read_last_skill,
    read_skill_stack,
    read_workflow_context,
    skill_stack_path,
    workflow_context_path,
    write_last_skill,
    write_skill_stack,
)


# Agent prompt を簡易分類するキーワードマップ
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
    "conversation:approval": ["はい", "いいえ", "ok", "いいよ", "よろしく", "採用", "accept"],
    "conversation:confirmation": ["お願い", "やって", "進めて", "対応して", "続けて"],
    "conversation:question": [r"なに", r"どう", r"なぜ", "教えて", "？"],
    "conversation:direction": ["こうして", "やめて", "変えて", "代わりに", "ではなく"],
    "conversation:thanks": ["ありがと", "感謝", "サンクス", "thx", "thanks"],
}


def classify_prompt(prompt: str) -> str:
    """prompt をキーワードベースで簡易分類する。"""
    prompt_lower = prompt.lower()
    for category, keywords in PROMPT_CATEGORIES.items():
        for kw in keywords:
            if re.search(kw, prompt_lower):
                return category
    return "other"


# 修正パターン: ユーザーのフィードバックを検出するための統一辞書
CORRECTION_PATTERNS = {
    "remember": {"pattern": r"(?i)^remember:", "confidence": 0.90, "type": "explicit", "decay_days": 120},
    "dont-unless-asked": {"pattern": r"(?i)don't (?:add|include|create) .{1,40} unless", "confidence": 0.90, "type": "guardrail", "decay_days": 120},
    "only-what-asked": {"pattern": r"(?i)only (?:change|modify|edit|touch) what I (?:asked|requested|said)", "confidence": 0.90, "type": "guardrail", "decay_days": 120},
    "stop-unrelated": {"pattern": r"(?i)stop (?:refactoring|changing|modifying|editing) (?:unrelated|other|surrounding)", "confidence": 0.90, "type": "guardrail", "decay_days": 120},
    "dont-over-engineer": {"pattern": r"(?i)don't (?:over-engineer|add extra|be too|make unnecessary)", "confidence": 0.85, "type": "guardrail", "decay_days": 90},
    "dont-refactor-unless": {"pattern": r"(?i)don't (?:refactor|reorganize|restructure) (?:unless|without)", "confidence": 0.85, "type": "guardrail", "decay_days": 90},
    "leave-alone": {"pattern": r"(?i)leave .{1,30} (?:alone|unchanged|as is)", "confidence": 0.85, "type": "guardrail", "decay_days": 90},
    "dont-add-annotations": {"pattern": r"(?i)don't (?:add|include) (?:comments|docstrings|type hints|annotations) (?:unless|to code)", "confidence": 0.85, "type": "guardrail", "decay_days": 90},
    "minimal-changes": {"pattern": r"(?i)(?:minimal|minimum|only necessary) changes", "confidence": 0.80, "type": "guardrail", "decay_days": 90},
    "iya": {"pattern": r"^いや[、,.\s]|^いや違", "confidence": 0.85, "type": "correction", "decay_days": 90},
    "chigau": {"pattern": r"^違う[、，,.\s]", "confidence": 0.85, "type": "correction", "decay_days": 90},
    "souja-nakute": {"pattern": r"そうじゃなく[てけ]", "confidence": 0.80, "type": "correction", "decay_days": 90},
    "perfect": {"pattern": r"(?i)perfect!|exactly right|that's exactly", "confidence": 0.70, "type": "positive", "decay_days": 90},
    "great-approach": {"pattern": r"(?i)that's what I wanted|great approach", "confidence": 0.70, "type": "positive", "decay_days": 90},
    "keep-doing": {"pattern": r"(?i)keep doing this|love it|excellent|nailed it", "confidence": 0.70, "type": "positive", "decay_days": 90},
    "no": {"pattern": r"^no[,. ]+", "confidence": 0.70, "type": "correction", "decay_days": 60, "strong": True},
    "dont": {"pattern": r"(?i)^don't\b|^do not\b", "confidence": 0.70, "type": "correction", "decay_days": 60, "strong": True},
    "stop": {"pattern": r"(?i)^stop\b|^never\b", "confidence": 0.70, "type": "correction", "decay_days": 60, "strong": True},
    "thats-wrong": {"pattern": r"(?i)that's (wrong|incorrect)|that is (wrong|incorrect)", "confidence": 0.70, "type": "correction", "decay_days": 60, "strong": True},
    "I-meant": {"pattern": r"(?i)^I meant\b|^I said\b", "confidence": 0.70, "type": "correction", "decay_days": 60, "strong": True},
    "I-told-you": {"pattern": r"(?i)^I told you\b|^I already told\b", "confidence": 0.85, "type": "correction", "decay_days": 120, "strong": True},
    "use-X-not-Y": {"pattern": r"(?i)use .{1,30} not\b", "confidence": 0.70, "type": "correction", "decay_days": 60, "strong": True},
    "actually": {"pattern": r"(?i)^actually[,. ]", "confidence": 0.55, "type": "correction", "decay_days": 45},
}

# 偽陽性フィルター
FALSE_POSITIVE_FILTERS = [
    r"[？\?]$",
    r"(?i)^(please|can you|could you|would you|help me)\b",
    r"(?i)(help|fix|check|review|figure out|set up)\s+(this|that|it|the)\b",
    r"(?i)(error|failed|could not|cannot|can't|unable to)\s+\w+",
    r"(?i)(is|was|are|were)\s+(not|broken|failing)",
    r"(?i)^I (need|want|would like)\b",
    r"(?i)^(ok|okay|alright)[,.]?\s+(so|now|let)",
]

_MAX_CAPTURE_PROMPT_LENGTH = 500
_MIN_SHORT_CORRECTION_LENGTH = 80

_SANITIZE_XML_TAGS = [
    "<system>", "</system>",
    "<system-reminder>", "</system-reminder>",
    "<instructions>", "</instructions>",
    "<context>", "</context>",
    "<rules>", "</rules>",
    "<Claude>", "</Claude>",
]

_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_message(text: str, max_length: int = 500) -> str:
    """LLM に渡す corrections メッセージをサニタイズする。"""
    result = _CONTROL_CHAR_PATTERN.sub("", text)
    for tag in _SANITIZE_XML_TAGS:
        result = result.replace(tag, "")
    if len(result) > max_length:
        result = result[:max_length] + "..."
    return result


def should_include_message(text: str) -> bool:
    """メッセージが correction 検出対象かどうかを判定する。"""
    if not text.strip():
        return False
    if re.search(r"(?i)^remember:", text.strip()):
        return True
    if len(text.strip()) > _MAX_CAPTURE_PROMPT_LENGTH:
        return False
    skip_patterns = [
        r"^<", r"^\[", r"^\{",
        r"tool_result", r"tool_use_id",
        r"<command-", r"<task-notification>", r"<system-reminder>",
        r"This session is being continued",
        r"^Analysis:", r"^\*\*", r"^   -",
    ]
    for pattern in skip_patterns:
        if re.search(pattern, text.strip()):
            return False
    return True


def calculate_confidence(base_confidence: float, text: str, matched_count: int = 1, has_strong: bool = False, has_i_told_you: bool = False) -> tuple[float, int]:
    """信頼度を計算する（長さ調整、パターン数・強度による調整）。"""
    if has_i_told_you:
        confidence = 0.85
        decay_days = 120
    elif matched_count >= 3:
        confidence = 0.85
        decay_days = 120
    elif matched_count >= 2:
        confidence = 0.75
        decay_days = 90
    elif has_strong:
        confidence = max(base_confidence, 0.70)
        decay_days = 60
    else:
        confidence = base_confidence
        decay_days = 45

    text_length = len(text.strip())
    if text_length < _MIN_SHORT_CORRECTION_LENGTH:
        confidence = min(0.90, confidence + 0.10)
    elif text_length > 300:
        confidence = max(0.50, confidence - 0.15)
    elif text_length > 150:
        confidence = max(0.55, confidence - 0.10)

    return (confidence, decay_days)


def detect_correction(text: str):
    """テキストから修正パターンを検出する（最初のマッチのみ）。"""
    text_stripped = text.strip()
    if not text_stripped:
        return None
    for fp in FALSE_POSITIVE_FILTERS:
        if re.search(fp, text_stripped) or re.search(fp, text_stripped.lower()):
            return None
    fp_hashes = load_false_positives()
    if fp_hashes and message_hash(text_stripped) in fp_hashes:
        return None
    for key, info in CORRECTION_PATTERNS.items():
        pattern = info["pattern"]
        if re.search(pattern, text_stripped) or re.search(pattern, text_stripped.lower()):
            return (key, info["confidence"])
    return None


def detect_all_patterns(text: str) -> list[str]:
    """テキストから全マッチするパターンキーのリストを返す。"""
    text_stripped = text.strip()
    if not text_stripped:
        return []
    for fp in FALSE_POSITIVE_FILTERS:
        if re.search(fp, text_stripped) or re.search(fp, text_stripped.lower()):
            return []
    matched = []
    for key, info in CORRECTION_PATTERNS.items():
        pattern = info["pattern"]
        if re.search(pattern, text_stripped) or re.search(pattern, text_stripped.lower()):
            matched.append(key)
    return matched


# last_skill_path / write_last_skill / read_last_skill は rl_common/workflow.py に集約済み
# （上部の re-export に集約）


def project_name_from_dir(project_dir: str) -> str:
    """プロジェクトディレクトリパスから末尾のディレクトリ名を返す。"""
    return Path(project_dir).name


def extract_worktree_info(event: dict) -> dict | None:
    """hook event payload から worktree 情報を抽出する。"""
    wt = event.get("worktree")
    if not isinstance(wt, dict):
        return None
    name = wt.get("name")
    branch = wt.get("branch")
    if not name and not branch:
        return None
    return {"name": name or "", "branch": branch or ""}


def append_jsonl(filepath: Path, record: dict) -> None:
    """JSONL ファイルに1行追記する。新規作成時はパーミッション 600 を設定。失敗時はサイレント。"""
    try:
        is_new = not filepath.exists()
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if is_new:
            try:
                filepath.chmod(0o600)
            except OSError as e:
                print(f"[rl-anything] chmod file warning: {e}", file=sys.stderr)
    except OSError as e:
        print(f"[rl-anything] write failed: {e}", file=sys.stderr)


# --- 偽陽性フィードバック ---

FALSE_POSITIVES_FILE = DATA_DIR / "false_positives.jsonl"
_FALSE_POSITIVE_EXPIRY_DAYS = 180


def message_hash(text: str) -> str:
    """メッセージの SHA-256 ハッシュを返す。"""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def load_false_positives() -> set[str]:
    """false_positives.jsonl から message_hash のセットを読み込む。"""
    if not FALSE_POSITIVES_FILE.exists():
        return set()
    try:
        hashes = set()
        for line in FALSE_POSITIVES_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                h = record.get("message_hash")
                if h:
                    hashes.add(h)
            except json.JSONDecodeError:
                continue
        return hashes
    except OSError as e:
        print(f"[rl-anything] load_false_positives warning: {e}", file=sys.stderr)
        return set()


def add_false_positive(msg: str, correction_type: str) -> None:
    """偽陽性をファイルに追記する。"""
    ensure_data_dir()
    record = {
        "message_hash": message_hash(msg),
        "original_type": correction_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    append_jsonl(FALSE_POSITIVES_FILE, record)


def cleanup_false_positives() -> int:
    """180日超のエントリを false_positives.jsonl から削除する。削除件数を返す。"""
    if not FALSE_POSITIVES_FILE.exists():
        return 0
    try:
        lines = FALSE_POSITIVES_FILE.read_text(encoding="utf-8").splitlines()
        cutoff = datetime.now(timezone.utc) - timedelta(days=_FALSE_POSITIVE_EXPIRY_DAYS)
        kept = []
        removed = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                ts_str = record.get("timestamp", "")
                if ts_str:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts < cutoff:
                        removed += 1
                        continue
                kept.append(json.dumps(record, ensure_ascii=False))
            except (json.JSONDecodeError, ValueError):
                kept.append(line)
        if removed > 0:
            FALSE_POSITIVES_FILE.write_text("\n".join(kept) + "\n" if kept else "", encoding="utf-8")
        return removed
    except OSError as e:
        print(f"[rl-anything] cleanup_false_positives warning: {e}", file=sys.stderr)
        return 0
