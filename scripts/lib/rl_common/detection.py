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
    # 「いやいや」「いやいやいや」等の反復強調も同義（#336）。文頭アンカー + 区切り文字は
    # 従来どおりで偽陽性リスクは変えない（{1,3} は暴走防止の保守的な上限）。
    "iya": {"pattern": r"^(いや){1,3}[、,.\s]|^いや違", "confidence": 0.85, "type": "correction", "decay_days": 90},
    # ひらがな表記「ちがう」も同義（#305 の実コーパス目視で "ちがうちがう、…" が未検出だった）。
    # 文頭アンカー付きなので偽陽性リスクは漢字版と同等。
    "chigau": {"pattern": r"^違う[、，,.\s]|^ちがう", "confidence": 0.85, "type": "correction", "decay_days": 90},
    "souja-nakute": {"pattern": r"そうじゃなく[てけ]", "confidence": 0.80, "type": "correction", "decay_days": 90},
    # 以下4パターンは #336（実コーパス 250 件目視で拾えていなかった典型表現）。
    # 「引用マーカー（って/と）+ 言った/いった + のに/けど」＝ I-told-you の日本語相当。
    # 引用マーカーを要求することで「うまくいったのに」（行く＝行った）等の同形異義語との
    # 衝突を避ける（引用マーカー無しの「いった」は対象外）。
    "itte-noni": {"pattern": r"(って|と)(言|い)った(のに|けど)", "confidence": 0.85, "type": "correction", "decay_days": 120, "strong": True, "question_mark_override": True},
    # 「指摘した/あげた + のに/けど」＝ itte-noni と同系だが動詞が異なるため別パターン。
    # 「けど」は日本語では逆接だけでなく単なる話題転換の軽い接続にも使われるため、単独では
    # 過検出する（実測: 「何度も指摘したけど、その内容をスキルにまとめたい」という新規
    # タスク依頼を誤検出した）。「のに」は逆接の意味が強く単独で許容するが、「けど」は
    # 直後に不履行を示す語（何も/まだ/全然/一切 + 否定）を伴う場合のみ許容する。
    "shiteki-noni": {"pattern": r"指摘(した|あげた)(のに|けど.{0,15}(何も|まだ|全然|一切))", "confidence": 0.85, "type": "correction", "decay_days": 120, "strong": True, "question_mark_override": True},
    # 「依頼していないことへの訂正」＝「〜って言ってないんだよね」。
    "itte-nai-yone": {"pattern": r"(言って|いって)(い)?ない.{0,6}(んだ)?よ?ね", "confidence": 0.75, "type": "correction", "decay_days": 90},
    # 「品質ミスの追及」＝「なんで/なぜ + 失敗・見落としを示す語」（語順が逆の「〜できて
    # ないのはなんで？」も別枝で許容）。素朴な質問（例:「なぜ今熱いのか」）や、失敗語を
    # 含む無関係な語が偶然近くにある場合（実測: 設計テンプレの「各 fix に「なぜ」+
    # 見落としリスク」を誤検出）と区別するため、(a) なんで/なぜ と失敗語の間に文区切り
    # （。！？「」改行）を挟まない、(b) 具体的な失敗語を伴う、の2条件に限定する。
    "naze-dekinakatta": {"pattern": r"(なんで|なぜ)[^。！？!?「」\n]{0,20}(でき(なかった|てない|ていない|てなかった)|くぐりぬけ|くぐり抜け|見落と|漏れ(て|た)|し(なかった|てない|ていない)|変(え|わ)って?(い)?ない|直って?(い)?ない)|(でき(なかった|てない|ていない|てなかった)|くぐりぬけ|くぐり抜け|見落と|漏れ(て|た)|し(なかった|てない|ていない)|変(え|わ)って?(い)?ない|直って?(い)?ない)[^。！？!?「」\n]{0,15}のは(なんで|なぜ)", "confidence": 0.80, "type": "correction", "decay_days": 90, "strong": True, "question_mark_override": True},
    # 「出力そのものへの異議」＝「勘違いしている？／していない？」。過去形「勘違いだったかも」
    # （自己の勘違いの撤回）とは区別するため、現在形 + 疑問符終端に限定する。
    "kanchigai-question": {"pattern": r"勘違い(して)?(い)?(る|ない)[？?]", "confidence": 0.75, "type": "correction", "decay_days": 90, "question_mark_override": True},
    # ADR-054 A0（#379）: census 実測（precision 87.5%、_MACHINERY_MARKERS 追加後）で確認済みの
    # 低リスク・低recall語彙。複合動詞（見直して/作り直して/書き直して/考え直して/やり直して）は
    # lookbehind で除外（対象は「直して」単独のみ。修正して/訂正してには適用しない）。
    "naoshite-request": {
        "pattern": r"(?<!見)(?<!作り)(?<!書き)(?<!考え)(?<!やり)直して|修正して|訂正して",
        "confidence": 0.75, "type": "correction", "decay_days": 90,
    },
    "yamete-request": {
        "pattern": r"やめて(ほしい|ください|くれ)",
        "confidence": 0.75, "type": "correction", "decay_days": 90,
    },
    # #527: 固定評価セットの偽陰性45件を意味別に分類し、文字列単体で既発生の欠陥と
    # 説明できる群だけを追加する。一般的な「〜してください」や中立な質問は含めない。
    "observed-defect": {
        "pattern": r"表示されない(?=[。！!、,]|$)|できて(い)?なかった(し)?(?=[。！!、,]|$)|実バグ",
        "confidence": 0.80, "type": "correction", "decay_days": 90, "strong": True,
    },
    "readability-defect": {
        "pattern": r"(わかり|分かり|み|見)(づら|ずら)い(から)?(?=[。！!、,]|$)",
        "confidence": 0.80, "type": "correction", "decay_days": 90,
    },
    "reconsider-request": {
        "pattern": r"もっと具体的に提案して|もっと文章(を)?短く|設計しなお(す|して)",
        "confidence": 0.75, "type": "correction", "decay_days": 90,
    },
    "unnecessary-action": {
        "pattern": r"(確認|version|バージョン).{0,12}(いらない|不要)(んだよね)?(?=[。！!？?、,]|$)|"
                   r"聞かなくても(?=$)|聞かなくても.{0,20}完結しない[？?]|"
                   r"確認しなくてよいんじゃない[？?]|ブロッカーにしない",
        "confidence": 0.80, "type": "correction", "decay_days": 90, "strong": True,
        "question_mark_override": True,
    },
    "contradiction-question": {
        "pattern": r"(^|\n)これって本当[？?]|説明していたけど.{0,20}これって本当[？?]|"
                   r"(前に|以前).{0,30}(話し|確認し)なかったっけ[？?]|"
                   r"かぶったりしない[？?]",
        "confidence": 0.80, "type": "correction", "decay_days": 90, "strong": True,
        "question_mark_override": True,
    },
    "why-undesired-action": {
        "pattern": r"(なんで|なぜ)[^。！？!?「」\n]{0,30}(しちゃった|なのに[^。！？!?「」\n]{0,20}必要になって)",
        "confidence": 0.80, "type": "correction", "decay_days": 90, "strong": True,
        "question_mark_override": True,
    },
    "prospective-guardrail-ja": {
        "pattern": r"必ず.{0,30}(認識|確認)するようにして",
        "confidence": 0.80, "type": "guardrail", "decay_days": 120, "strong": True,
    },
    "missing-required-capability": {
        "pattern": r"できる必要があるんじゃない[？?]",
        "confidence": 0.80, "type": "correction", "decay_days": 90, "strong": True,
        "question_mark_override": True,
    },
    "refinement-request": {
        "pattern": r"(よいかんじ|いい感じ|良い感じ).{0,80}もうちょっと.{0,30}目立たせたい",
        "confidence": 0.75, "type": "correction", "decay_days": 90,
    },
    "only-said": {
        "pattern": r"って(言|い)っただけ",
        "confidence": 0.75, "type": "correction", "decay_days": 90,
    },
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

# 疑問符終端フィルタ。詰問形のバイパス（#336・下の _bypasses_question_mark_filter）が
# 名指しで参照するため、リスト内の位置に依存しない名前付き定数として切り出す
# （添字参照だと並べ替えた瞬間に別のフィルタをバイパスする silent drift になる）。
QUESTION_MARK_FILTER = r"[？\?]$"

# 偽陽性フィルター
FALSE_POSITIVE_FILTERS = [
    QUESTION_MARK_FILTER,
    r"(?i)^(please|can you|could you|would you|help me)\b",
    r"(?i)(help|fix|check|review|figure out|set up)\s+(this|that|it|the)\b",
    r"(?i)(error|failed|could not|cannot|can't|unable to)\s+\w+",
    r"(?i)(is|was|are|were)\s+(not|broken|failing)",
    r"(?i)^I (need|want|would like)\b",
    r"(?i)^(ok|okay|alright)[,.]?\s+(so|now|let)",
]

# #336: 先頭の疑問符終端フィルタ（末尾が ? / ？ なら無条件除外）は素朴な質問には有効だが、
# 「なんで/なぜ + 失敗語」「勘違いしている？」「〜って言ったのに/けど」のような**詰問形**
# （既遂を前提に相手の理解・実行を問い詰める文）も疑問符で終わるため構造的に落ちる（issue
# 本文の実測: 「〜勘違いしている？」「なんで〜できなかったの？」「何も変更していない？」は
# いずれも疑問符終端）。該当パターンだけ metadata で疑問符終端フィルタをバイパスする。
#
# 対象を全 CORRECTION_PATTERNS に広げない（英語 "no, is that right?" 等、素朴な疑問文が
# 既存パターンに偶然マッチするケースまでバイパスすると疑問符フィルタの本来の役目
# ＝素朴な質問の除外が壊れる。test_question_mark_ascii 参照）。
#
def _bypasses_question_mark_filter(text: str) -> bool:
    """詰問形パターンにマッチするか（疑問符終端フィルタの対象外か）を判定する（#336）。"""
    for info in CORRECTION_PATTERNS.values():
        if not info.get("question_mark_override", False):
            continue
        pattern = info["pattern"]
        if re.search(pattern, text) or re.search(pattern, text.lower()):
            return True
    return False


# リテラルマッチャ（このモジュール）と llm_judge チャネル（correction_semantic/）の線引き
# （#336 次アクション3）: このモジュールは固定の語彙・文法パターン（否定語の文頭アンカー・
# 引用マーカー付き再掲・疑問符終端の詰問形 等）のみを決定論で検出する。語彙マーカーを
# 伴わない暗黙の異議（皮肉・婉曲な言い換え要求・文脈依存の是非判断）は正規表現で拾うと
# 過検出が避けられないため対象外とし、`correction_semantic/`（utterances.db → Haiku 意味
# 判定 → weak_signals channel=llm_judge）に委ねる。新パターンを足す際は「文字列だけで
# 判定基準を説明できるか」を目安に、説明できなければ llm_judge 側の較正課題として起票する。

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

# 機構ターン判定（#387 で skill_extractor.trajectory_sampler に導入・#305/#323 で
# correction 検出の写像先に単一ソース化）。type=user だがユーザー発話でない harness 注入
# ターン（compaction サマリ・SKILL.md 本体注入・task-notification・system-reminder・
# Stop hook の decision:block reason 文 等）を判定する。
#
# 実コーパス測定（8日窓・real user messages 4237件）: `detect_correction` がマッチした
# 6件は全件 "Stop hook feedback:\n" で始まる Stop hook の自己注入文で、ユーザー発話由来の
# マッチは 0 件だった（#305）。writer 側（`hooks/correction_detect.py` の live hook と
# `scripts/backfill_preceding_tool_calls.py` の offline backfill）はいずれもこの
# `should_include_message` を経由するため、ここに1箇所追加するだけで両方に効く。
#
# reader 側（`skill_extractor.trajectory_sampler._is_machinery_prompt`）とはこの関数を
# 単一ソースとして共有する（片側だけ直すと desync する、pitfall_copied_parse_convention_
# partial_fix）。
_MACHINERY_PREFIXES = (
    "<system-reminder>",
    "<task-notification>",
    "<local-command",
    "<command-output>",
    "<command-message>",
    "[request interrupted",
)
"""lstrip 後この接頭辞で始まるテキストは機構ターン。

``[Request interrupted (by user|by user for tool use)]`` は Esc 中断の harness
マーカー（weak_signals の ``_INTERRUPT_MARKER`` と同じ判定対象、#322）。turn 単体では
``isMeta`` が付かないため content の prefix で判定する。"""

_MACHINERY_MARKERS = (
    "this session is being continued from a previous conversation",
    "base directory for this skill:",
    "stop hook feedback:",
    "caveat: the messages below were generated",
    # ADR-054 A0（#379）: background agent 停止通知の本文（"...修正してください。"等）を
    # naoshite-request が誤って拾わないよう機構ターンとして除外（census #8 実測）。
    "background agents were stopped by the user",
    # ADR-054 A2（#379）: weak_signals._DISPATCH_MARKERS（rephrase 検出の委譲プロンプト
    # 除外）を単一ソース化。ここに昇格するのは「人間の自然文にまず出現しない構造マーカー」
    # のみ（低リスク）。「あなたは」「エージェントです」「experiment 」のような一般語は
    # is_dispatch_template_marker 側に残す（本関数は should_include_message / extractor の
    # 単独メッセージ判定にも使われるため、広い語を混ぜると correction 検出の recall を壊す。
    # 例:「あなたは間違ったことを言った」は正規の correction 発話であり除外してはいけない）。
    "<tool-use-id>",
    "<summary>",
    "<task-notification>",
    "<teammate-message",
    "作業ディレクトリ:",
    "比較実験パターン",
    "idle_notification",
)
"""lstrip+lower 後、先頭付近にこの語を含むテキストは機構ターン。"""

_MACHINERY_MARKER_WINDOW = 300
"""機構マーカーを探す先頭文字数（依頼文中の偶然一致を避けるため先頭付近に限定）。"""


def is_machinery_prompt(content: str) -> bool:
    """テキストが harness 注入の機構ターンか判定する（#387, #305）。

    compaction サマリ・SKILL.md 本体・task-notification・system-reminder・
    Stop hook feedback 等は type=user だがユーザー発話ではない。これらを
    correction 検出・routing キーワード採掘の両方から除くための単一ソース述語。
    """
    low = content.lstrip().lower()
    if low.startswith(_MACHINERY_PREFIXES):
        return True
    head = low[:_MACHINERY_MARKER_WINDOW]
    return any(marker in head for marker in _MACHINERY_MARKERS)


# ADR-054 A2（#379）: 並列 agent 派遣テンプレの広い語彙判定。is_machinery_prompt に
# 統合しない理由は _MACHINERY_MARKERS のコメント参照（correction 検出の recall を壊すため）。
# 呼び出し側（weak_signals.detectors.detect_rephrase）は隣接する高類似ペアの両方が一致して
# 初めて機構ターンとして扱う構造的に安全な文脈でのみ使う。単独メッセージ判定
# （should_include_message / utterance_archive.extractor）には適用しない。
_DISPATCH_TEMPLATE_MARKERS = (
    "あなたは",
    "エージェントです",
    "experiment ",
)
"""この語を含む（先頭付近に限定しない・全文検索）テキストは派遣テンプレ疑い。"""


def is_dispatch_template_marker(content: str) -> bool:
    """並列 agent 派遣テンプレの広い語彙判定（rephrase の近接高類似ペア専用、#379）。

    is_machinery_prompt とは独立した述語。単独では使わず、呼び出し側が
    「隣接する2発話がどちらも高 jaccard 類似」という構造的な安全条件と AND で
    組み合わせて初めて誤検出リスクが許容水準に収まる（weak_signals.detectors.detect_rephrase
    のみが呼び出す設計。単独メッセージの correction 検出には使わない）。
    """
    return any(m in content for m in _DISPATCH_TEMPLATE_MARKERS)


def sanitize_message(text: str, max_length: int = 500) -> str:
    """LLM に渡す corrections メッセージをサニタイズする。"""
    result = _CONTROL_CHAR_PATTERN.sub("", text)
    for tag in _SANITIZE_XML_TAGS:
        result = result.replace(tag, "")
    if len(result) > max_length:
        result = result[:max_length] + "..."
    return result


# #445: Claude Code CLI が画像添付時に text block へ自動挿入する位置マーカー。
# 実コーパス実測（corrections.jsonl の `[Image` 開始 37 件全件）: bare（画像だけ・実
# テキスト無し）のケースは 0 件、全件が同じ text block 内に human の実指摘を伴う
# （例:「[Image #1] Codeタブってないよ」）。そのため全文除外ではなく、マーカーだけを
# strip し周辺の human 実テキストは残す。`#\d+` に一致しない文言（「[Image processing
# failed]」「[Image で始まる行を除外して」のように "#数字]" が続かないもの）は誤って
# 壊さない。
#
# **判定不能な既知のトレードオフ**（#445 codex round1 [Should]1）: transcript には
# 「CLI 自動挿入の添付マーカー」と「そのマーカー文字列への human の意図的な言及」
# （例:「[Image #3] のスクショの話だけど」）を区別する構造情報が無い。どちらも同じ
# text block 内の文字列としてしか観測できないため、``#\d+`` パターンに一致する限り
# 後者も strip される（意図的な設計判断）。実コーパス実測（37件全件目視）では該当形式が
# 全件 CLI 自動挿入のマーカーで意図的な言及は 0 件だったため、区別不能な場合は strip
# 側に倒した。逆に「実コーパスで確認した位置・区切りだけに絞る」設計（例: 先頭のみ・
# 特定の空白パターンのみ）は採用しない — 現在のコーパスへの過学習になり、CLI が
# マーカー形式を変えた瞬間に黙って取りこぼす（`learning_synthetic_fixture_false_
# confidence` と同型のリスク）。任意桁数の # 番号・空白の有無を問わず一致する。
_IMAGE_PLACEHOLDER_RE = re.compile(r"\[Image\s*#\d+\]")


def has_image_placeholder(text: str) -> bool:
    """テキストが ``[Image #N]`` 添付プレースホルダを含むか判定する（#445）。

    ``strip_image_placeholders`` の適用対象かどうかを事前に知りたい呼び出し側
    （strip 前後で件数を分けて集計したい observability 用途）向け。判定は
    ``strip_image_placeholders`` と同じ正規表現を単一ソースとして共有する。
    """
    if not text:
        return False
    return bool(_IMAGE_PLACEHOLDER_RE.search(text))


def strip_image_placeholders(text: str) -> str:
    """テキストから ``[Image #N]`` 添付プレースホルダを除去する（#445）。

    utterance_archive.extractor（utterances.db への取り込み前・upstream）と
    corrections 書込パスの両方が本関数を単一ソースとして共有する（pitfall_copied_
    parse_convention_partial_fix: 弱いパース式の片側だけ直すと desync する）。
    全行が marker のみ（bare な画像添付）なら strip 後に空文字を返す
    （呼び出し側で「発話でない」として扱う）。
    """
    if not text:
        return text
    return _IMAGE_PLACEHOLDER_RE.sub("", text).strip()


def should_include_message(text: str) -> bool:
    """メッセージが correction 検出対象かどうかを判定する。"""
    if not text.strip():
        return False
    if is_machinery_prompt(text.strip()):
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


def _fails_false_positive_filters(text_stripped: str) -> bool:
    """偽陽性フィルタに引っかかるか判定する（detect_correction / detect_all_patterns 共有）。

    重複実装すると片側だけ改修して desync する（#40 と同型のリスク）ため単一関数に集約する。
    """
    for fp in FALSE_POSITIVE_FILTERS:
        if fp == QUESTION_MARK_FILTER and _bypasses_question_mark_filter(text_stripped):
            continue
        if re.search(fp, text_stripped) or re.search(fp, text_stripped.lower()):
            return True
    return False


def _has_unquoted_match(pattern: str, text: str) -> bool:
    """日本語の括弧内に完全に収まる用例を除き、実発言だけを検出する。"""
    quote_depth = 0
    quoted = [False] * len(text)
    pairs = {"「": "」", "『": "』"}
    closing = set(pairs.values())
    stack = []
    for index, char in enumerate(text):
        if char in pairs:
            stack.append(pairs[char])
            quote_depth += 1
            quoted[index] = True
        elif char in closing and stack and char == stack[-1]:
            quoted[index] = True
            stack.pop()
            quote_depth -= 1
        elif quote_depth:
            quoted[index] = True

    for match in re.finditer(pattern, text):
        if not quoted[match.start()]:
            return True
    return False


def detect_correction(text: str):
    """テキストから修正パターンを検出する（最初のマッチのみ）。"""
    text_stripped = text.strip()
    if not text_stripped:
        return None
    if _fails_false_positive_filters(text_stripped):
        return None
    # FALSE_POSITIVES_FILE 系は __init__.py に残置のため動的 lookup
    import rl_common as _root
    fp_hashes = _root.load_false_positives()
    if fp_hashes and _root.message_hash(text_stripped) in fp_hashes:
        return None
    for key, info in CORRECTION_PATTERNS.items():
        pattern = info["pattern"]
        if _has_unquoted_match(pattern, text_stripped) or _has_unquoted_match(pattern, text_stripped.lower()):
            return (key, info["confidence"])
    return None


def detect_all_patterns(text: str) -> list[str]:
    """テキストから全マッチするパターンキーのリストを返す。"""
    text_stripped = text.strip()
    if not text_stripped:
        return []
    if _fails_false_positive_filters(text_stripped):
        return []
    matched = []
    for key, info in CORRECTION_PATTERNS.items():
        pattern = info["pattern"]
        if _has_unquoted_match(pattern, text_stripped) or _has_unquoted_match(pattern, text_stripped.lower()):
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


# CC 組み込みスラッシュコマンド（`<command-name>` に現れるが SKILL.md を持たない）の
# 単一ソース（#333）。skill_extractor.trajectory_sampler（session 採掘）と
# discover.runner（候補判定）が別々のリテラルで持っていたため内容が食い違い、
# 両方に無かった `/effort` が CREATE 候補としてすり抜けていた（copied-parse-convention
# pitfall と同型・#40）。新しい CC バージョンで組み込みが増えたらここに追記する
# （両 call site とも import するので二重更新は不要）。
#
# 収録方針: 「漏れ」は組み込みコマンドが CREATE 候補ノイズとして triage 母集団を汚すが、
# 「入れすぎ」のコストはほぼゼロ（同名の既存スキルは _is_already_existing_skill が別途
# 除外し、組み込みと同名の新規スキルはそもそも CC 側で衝突する）。よって迷ったら入れる。
# 下段は v2.1.221 の実バイナリ文字列で実在を確認した分（#333 レビュー時に追加）。
CC_BUILTIN_COMMANDS = frozenset({
    "add-dir", "agents", "bug", "clear", "compact", "config", "cost",
    "doctor", "effort", "fast", "help", "init", "login", "logout", "loop",
    "mcp", "memory", "model", "permissions", "plugin", "reload-plugins",
    "rename", "resume", "status", "terminal-setup", "vim",
    # v2.1.221 実測分
    "allowed-tools", "bashes", "cd", "code-review", "context", "exit",
    "export", "fork", "hooks", "ide", "install-github-app", "output-style",
    "privacy-settings", "quit", "release-notes", "rewind", "security-review",
    "statusline", "theme", "todos", "ultrareview", "upgrade", "usage",
    "workflows",
})
