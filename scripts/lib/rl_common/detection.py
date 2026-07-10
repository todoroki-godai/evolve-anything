"""evolve-anything correction / prompt detection。

`PROMPT_CATEGORIES` / `CORRECTION_PATTERNS` / `FALSE_POSITIVE_FILTERS` 等の
分類・修正パターン辞書と、`classify_prompt` / `sanitize_message` /
`should_include_message` / `calculate_confidence` / `detect_correction` /
`detect_all_patterns` を提供する。

`detect_correction` は `load_false_positives` / `message_hash`
（rl_common/__init__.py 内の関数、FALSE_POSITIVES_FILE/DATA_DIR に依存）を
呼ぶため、関数本体内で `import rl_common` 経由で動的 lookup する。
"""
import re

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
    # FALSE_POSITIVES_FILE 系は __init__.py に残置のため動的 lookup
    import rl_common as _root
    fp_hashes = _root.load_false_positives()
    if fp_hashes and _root.message_hash(text_stripped) in fp_hashes:
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


# subagents.jsonl の agent_type ノイズ判定（writer/reader 単一ソース）。
# hex 桁とハイフンのみで構成される opaque identifier を検出する正規表現。
_OPAQUE_ID_RE = re.compile(r"^[0-9a-fA-F-]+$")
# 本物の agent 種別名と ID 形を分ける hex 桁数の floor。ID（pure hex 17 桁・UUID 32 桁・
# agent_id 形）は十分長く、人間可読な agent 名がこの桁数に達することはない。
_OPAQUE_ID_MIN_HEX_DIGITS = 12


def noise_agent_type_kind(agent_type):
    """ノイズ agent_type の種別を返す（内訳分解の単一ソース・#142-8b）。

    is_noise_agent_type と同じ判定基準を **種別付き**で返す（`is_noise = kind is not None`）。
    subagents.jsonl のノイズ（本物の Task subagent でない）を 2 種に分ける:
    - ``"empty"``: 空 / 空白のみ（#36）。SubagentStop は本物の Task agent 以外
      （compaction 要約・メインセッション Stop・rate-limit メッセージ等）でも発火し空になる。
    - ``"id_form"``: harness が agent_type に ID 形の値（pure hex `aab2173eb119c5b91` /
      UUID / `agent_id` 形）を渡すケース（#44）。hex 桁とハイフンのみ・hex 桁が floor 以上。

    本物の agent 種別名（build-a1 / gamer-mvp29 / fapo-impl 等）は非 hex 文字を含むので None。
    """
    s = str(agent_type or "").strip()
    if not s:
        return "empty"
    if _OPAQUE_ID_RE.match(s):
        hex_digits = sum(1 for c in s if c in "0123456789abcdefABCDEF")
        if hex_digits >= _OPAQUE_ID_MIN_HEX_DIGITS:
            return "id_form"
    return None


def is_noise_agent_type(agent_type) -> bool:
    """subagents.jsonl の agent_type がノイズ（本物の Task subagent でない）か判定する。

    writer（subagent_observe）と reader（fleet.collectors / fanout_cost）が同じ判定を
    共有するための単一ソース。片側だけ直すと read/write が desync するため
    （copied-parse-convention pitfall・#40 の教訓）、全 call site はこの関数を呼ぶ。
    ノイズ種別（空 / ID 形）の内訳は noise_agent_type_kind を参照（#142-8b）。
    """
    return noise_agent_type_kind(agent_type) is not None


# worker-takeoff（completed≠完遂）の決定論検知（#161）。
# hooks/subagent_observe.py の MAX_MESSAGE_LENGTH と同値（値のみ複製・意図的）。
# last_assistant_message はそこで**先頭から**この長さに切り詰められるため、切り詰め後の
# 末尾は実際の文末ではない（元がもっと長ければ、完了署名がちょうど末尾に来る規約
# （例: `=== IMPL COMPLETE ... ===`）でも打ち切りで欠落しうる）。判定材料として
# 信用できないため、この長さに達したメッセージは判定不能（None）として扱う。
TRUNCATED_LEN = 500

# 完了署名: `=== ... ===` 終端マーカー（IMPL COMPLETE / IMPL BLOCKED / SCOUT COMPLETE 等の
# 具体語彙を限定せず、bookend 構造そのものを見る）。
_TAKEOFF_COMPLETION_MARKER_RE = re.compile(r"^===\s*\S.*\S\s*===\s*$")
# 報告見出し（`## 実装完了報告` 等）。markdown 見出し行に完了/報告語を含むかで判定。
_TAKEOFF_REPORT_HEADING_KEYWORDS = ("完了報告", "完了", "レポート", "報告")
# 前向きナレーション終端: 最終行が Now/Next/Let's 系の進行形で始まる（英語）。
_TAKEOFF_FORWARD_START_RE = re.compile(
    r"^(now|next|let[\'’]?s|let me|i\'ll|i will|going to|we\'ll|we will)\b",
    re.IGNORECASE,
)


def _takeoff_has_completion_signature(text: str) -> bool:
    """完了署名（=== ... === マーカー or 完了/報告見出し）がテキスト中に無いかを判定する。"""
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if _TAKEOFF_COMPLETION_MARKER_RE.match(s):
            return True
        if s.startswith("#") and any(kw in s for kw in _TAKEOFF_REPORT_HEADING_KEYWORDS):
            return True
    return False


_TAKEOFF_SENTENCE_SPLIT_RE = re.compile(r"[\n。！？.!?]")


def _takeoff_last_segment(text: str) -> str:
    """改行 or 文末記号（。！？.!?）で区切った最後の非空セグメントを返す。

    1 行の中に複数文が詰まっている（transcript のテキストブロックは改行を挟まないことが
    多い）ケースでも、"...しました。Now let's ..." のように**文単位**で前向きナレーション
    開始を検出できるようにする（行単位だと文頭にならず検出漏れになるため）。
    """
    for seg in reversed(_TAKEOFF_SENTENCE_SPLIT_RE.split(text)):
        s = seg.strip()
        if s:
            return s
    return ""


def _takeoff_ends_with_forward_narration(text: str) -> bool:
    """最終行が `:`/`：` 終端、または最終文が Now/Next/Let's 系進行形で始まるかを判定する。"""
    stripped = text.strip()
    if stripped.endswith(":") or stripped.endswith("："):
        return True
    last = _takeoff_last_segment(text)
    if not last:
        return False
    return bool(_TAKEOFF_FORWARD_START_RE.match(last))


def detect_takeoff_divergence(last_assistant_message):
    """worker-takeoff（completed≠完遂）の疑いを最終 assistant メッセージから判定する（#161）。

    subagent が harness に completed 扱いされたのに、報告テキストが「完了報告」でなく
    中間ナレーションのまま終わっている疑いを検出する。保守側（FP 抑制）の2シグナル AND:
    ① 完了署名が無い（`=== ... ===` マーカー / 報告見出しがテキスト中に見当たらない）
    ② 前向きナレーション終端（最終行が `:`/`：` で終わる、または Now/Next/Let's 系の
       進行形で始まる）
    ①単独では flag しない（終端マーカー規約を持たない agent 種で FP になるため）。

    Returns:
        True: 疑いあり（① and ②）
        False: 疑いなし（完了署名がある、または前向きナレーション終端でない）
        None: 判定不能（空 / 非文字列 / TRUNCATED_LEN 到達で末尾情報が信用できない）
    """
    if not isinstance(last_assistant_message, str):
        return None
    if len(last_assistant_message) >= TRUNCATED_LEN:
        return None
    text = last_assistant_message.strip()
    if not text:
        return None
    no_signature = not _takeoff_has_completion_signature(text)
    forward_ending = _takeoff_ends_with_forward_narration(text)
    return no_signature and forward_ending
