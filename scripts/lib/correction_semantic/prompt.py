"""correction_semantic.prompt — バッチプロンプト組み立て + verdict パース（#431 / #400 A5）。

30 件程度の発話を 1 プロンプトにまとめ、Haiku に「ユーザーが Claude の方向を正した
ターンか」を二値判定させ、修正なら言い回し（イディオム）を抽出させる。#400 A5 で
「何を直させられたか（対象軸）」を表す `category`（8値 enum）判定を同じ呼び出しに相乗りさせた
（設計正典 docs/decisions/drafts/054-a5-correction-category.md §2.1/§2.2）。

#431 背景の修正スタイル（語彙でなく意味論でしか拾えない）を例示する:
- 正しい値の後置型: 「つむぎにしてほしい、四国めたんじゃなくて」
- ソフト指摘型:     「P6のデザインが違うんだけど」
- 観察型:           「〜気がするんだよなぁ」

応答は厳格な JSON（{"verdicts": [{index, is_correction, idiom, category, reason}]}）を要求する。
パーサは code fence・前後ノイズに頑健。**「解釈できない（壊れた JSON）」と「正しく解釈できて
verdicts が空」は意味が違う**（#273）ため `parse_verdicts_result` で `ok` フラグとして区別する。
`ok=False`（壊れた JSON・応答欠損）を呼び出し側が「該当なし」と誤読すると、パース失敗バッチが
判定済みとして確定し二度と再判定されない（応答欠損は再試行されるのに壊れた応答はされない、
という非対称の温床になる）。`parse_verdicts`（後方互換）は従来どおり両ケースとも [] を返す。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional

from . import MAX_CHARS_PER_UTTERANCE

# 抽出する JSON object をテキストから拾うための緩い探索（code fence 等を剥がす）。
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

# prev_action（直前 Claude 操作の1行要約）の切り詰め上限。text ほど長大化しない想定だが
# 防御的に上限を設ける（#410 [Must]C）。
_MAX_PREV_ACTION_CHARS = 300


# ─────────────────────────────────────────────────────────────────
# A5 — 指摘カテゴリ（対象軸 8 値 enum）
# 設計正典: docs/decisions/drafts/054-a5-correction-category.md §2.1
# ─────────────────────────────────────────────────────────────────

# enum の並びは境界優先規則（同率時の固定順）と一致させる: factual > process > omission >
# excess > presentation > explanation > approach（+ other）。プロンプト文面・
# _validate_verdict の厳格検証・correction_rate の内訳集計が全てこのタプルを単一ソースにする。
CATEGORY_ENUM = (
    "factual",
    "process",
    "omission",
    "excess",
    "presentation",
    "explanation",
    "approach",
    "other",
)

# 表示用の日本語ラベル（§1.3 の塊の呼称）。results_board の内訳表示と共有する単一ソース。
CATEGORY_LABELS_JA = {
    "presentation": "見た目",
    "explanation": "説明",
    "factual": "事実",
    "approach": "やり方",
    "omission": "やり残し",
    "excess": "余計",
    "process": "手順",
    "other": "その他",
}

# judge_runner.call_haiku が毎バッチ safe_llm_call へ渡す response schema（#625 [Should]）。
# 従来は judge_runner.py 内の _VERDICT_JSON_SCHEMA としてのみ定義され、batch.py の
# 事前予約（_PROMPT_OVERHEAD_TOKENS）はこの schema 長を計上していなかった（バッチ本体の
# プロンプトのみを見積もり、毎回送信される json_schema 引数のコストが予約外だった）。
# ここへ移して公開し、prompt（本モジュール）→ batch → judge_runner の依存順で
# 両モジュールが単一ソースとして参照する。
VERDICT_JSON_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "is_correction": {"type": "boolean"},
                        "idiom": {"type": ["string", "null"]},
                        "category": {
                            "type": ["string", "null"],
                            "enum": [*CATEGORY_ENUM, None],
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["index", "is_correction"],
                },
            }
        },
        "required": ["verdicts"],
    }
)

# verdict の category フィールドが従う契約のバージョン。プロンプト文面・enum・優先規則・
# schema の構造を変えたら上げる（producer 時点で provenance に保存し、断絶が起きたことを
# 後から識別できるようにする・§2.4/§2.5）。
CATEGORY_SCHEMA_VERSION = 2

# プロンプトに埋め込む語彙表（意味 + 境界優先規則）。§2.1 の表・優先規則をそのまま使う。
_CATEGORY_VOCAB_TABLE = (
    "- presentation: 見た目・レイアウト・表示崩れ・図表の読みにくさ\n"
    "- explanation:  説明が長い・難しい・わかりにくい\n"
    "- factual:      事実・前提・認識の誤り（値の取り違え含む）\n"
    "- approach:     やり方・方針・設計そのものへの異議\n"
    "- omission:     やり残し・不足・詰めが甘い\n"
    "- excess:       余計・不要・削除要求・やりすぎ\n"
    "- process:      手順・ツール・ルールの不遵守（使うべきものを使わなかった）\n"
    "- other:        上記のどれでもない\n"
)

_CATEGORY_PRIORITY_RULES = (
    "カテゴリの境界判定は次の優先規則に従ってください:\n"
    "- presentation vs explanation: **成果物の見た目・文言**なら presentation、"
    "**Claude 自身の説明・回答**なら explanation\n"
    "- approach vs process: 設計選択そのものへの異議は approach。"
    "既に合意済み・明文化済みの手順への違反だけ process\n"
    "- omission vs excess: 欠けている成果物・要件は omission。存在する不要物の削除要求は excess\n"
    "- factual vs approach: 検証可能な前提・値の誤りは factual。"
    "前提が正しくても選択が不適切なら approach\n"
    "- 複合発話は主たる修正対象を1つ選ぶこと。同率で決めがたい場合は other に逃がさず、"
    "factual > process > omission > excess > presentation > explanation > approach の順で"
    "優先度が高いものを選ぶこと\n"
)


def format_utterance_line(
    index: int, u: Dict[str, Any], *, max_chars: int = MAX_CHARS_PER_UTTERANCE
) -> str:
    """1 発話分のプロンプト行を組み立てる（決定論・IO なし）。

    ``build_batch_prompt``（実送信）と ``batch.estimate_utterance_tokens``（見積もり）の
    単一ソース（#410 round2 [Must]C）。見積もりが本文長のみを測り prev_action（最大
    ``_MAX_PREV_ACTION_CHARS`` 字）・ラベル文言を固定オーバーヘッドで丸めていたため、
    長い日本語 prev_action が多いバッチで大幅な過小評価になっていた。ロジックを2箇所に
    複製すると片方だけ切り詰め幅を変えたときに乖離が再発するため、行の組み立てそのものを
    共有し「実際に組み立てたプロンプトの長さをそのまま測る」方式にした。
    """
    prev = (u.get("prev_action") or "(なし)")[:_MAX_PREV_ACTION_CHARS]
    text = (u.get("text") or "").replace("\n", " ").strip()[:max_chars]
    return f"[{index}] 直前のClaudeの操作: {prev}\n    ユーザー発話: {text}"


def build_batch_prompt(
    utterances: List[Dict[str, Any]], *, max_chars: int = MAX_CHARS_PER_UTTERANCE
) -> str:
    """発話リストから 1 バッチ分の判定プロンプトを組み立てる（決定論・IO なし）。

    各発話に 0 始まりの index を振り、index でひも付けて判定を返させる。
    prev_action（直前の Claude のツール操作）を文脈として渡す（修正の判定材料）。

    ``max_chars``（既定 ``MAX_CHARS_PER_UTTERANCE``・#410 [Must]C）: 本文が貼り付けられた
    長文等で青天井に膨張しないよう切り詰める。``batch.estimate_tokens`` も同じ上限を参照し、
    見積もりと実送信の文字数が乖離しないようにする（単一ソース）。
    """
    lines = [format_utterance_line(i, u, max_chars=max_chars) for i, u in enumerate(utterances)]
    listing = "\n".join(lines)

    return (
        "あなたは Claude Code セッションのログを監査するアシスタントです。\n"
        "以下は、各ターンで「直前に Claude が行った操作」と「その後のユーザー発話」の組です。\n"
        "各発話について、**ユーザーが Claude の方向・出力・判断を正そうとしたターンか**を\n"
        "二値で判定してください。語彙でなく意味で判断します。修正は次のような多様な形を取ります:\n"
        "- 正しい値の後置型: 「つむぎにしてほしい、四国めたんじゃなくて」\n"
        "- ソフト指摘型:     「P6のデザインが違うんだけど」\n"
        "- 観察・違和感型:   「ここ、ちょっとずれてる気がするんだよなぁ」\n"
        "- 明示否定型:       「いや、そうじゃない」「やり直して」\n\n"
        "修正でない例（is_correction=false にする）:\n"
        "- 新規の依頼・質問・雑談・感謝・相槌（「ありがとう」「次これやって」「これ何?」）\n"
        "- 文字起こしや貼り付けられたテキストの一部\n\n"
        "修正と判定した場合は、その修正を端的に表す**言い回し（idiom）**を発話から抜き出して\n"
        "ください（例: 「四国めたんじゃなくて」「違うんだけど」「気がする」）。\n"
        "修正でなければ idiom は null にします。\n\n"
        "修正と判定した場合は、**何を直させられたか（対象）**を表す category を"
        "以下の8種類から1つ選んでください:\n"
        f"{_CATEGORY_VOCAB_TABLE}\n"
        f"{_CATEGORY_PRIORITY_RULES}\n"
        "修正でなければ category は null にします。\n\n"
        "**判定対象の全 index について、必ず 1 件ずつ verdict を返してください**"
        "（非修正の発話も is_correction=false で必ず含める。省略しない）。\n\n"
        "以下の形式で判定結果を返してください:\n"
        '{"verdicts": [{"index": 0, "is_correction": true, "idiom": "四国めたんじゃなくて", '
        '"category": "factual", '
        '"reason": "正しい値を後置で言い直している"}, ...]}\n\n'
        "判定対象:\n"
        f"{listing}\n"
    )


def prompt_fingerprint() -> str:
    """固定プロンプトテンプレートの fingerprint（sha256 先頭12桁）。

    設計正典 drafts/054-a5-correction-category.md §2.4 / drafts/054-c-a-numerator.md §2.5:
    ``category`` は「事実」でなく「その judge 実行時の測定値」。プロンプトが変われば判定基準も
    変わるため、producer 時点で fingerprint を provenance に保存し、系列断絶の検出材料にする。

    発話に依存しない固定部分だけをハッシュ対象にするため ``build_batch_prompt([])``
    （``batch.estimate_tokens`` の固定費導出と同じ基準文字列・単一ソース）を入力にする。
    プロンプト文面・語彙表・優先規則のどれを変えてもこの値は変わる。
    """
    basis = build_batch_prompt([])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def _validate_verdict(v: object) -> Optional[Dict[str, Any]]:
    """1 verdict 要素を厳格型検証し、正規化した dict を返す（不正なら None）。

    #273 P1-1（codex 指摘）: 「構文上 valid だが意味的に壊れている」要素
    （`"index": "0"` の型違い・`"is_correction": "false"` の文字列）を黙って捨てて続行すると、
    捨てた分だけ verdicts が薄くなり `by_index.get(local_i)` が None になって「非修正」と
    誤確定する（#273 が塞いだはずの事故が partial-invalid 型で再発する）。**1 要素でも
    不正なら呼び出し側はバッチ全体を ok=False にすること**（不正要素だけ捨てて部分採用しない）。
    `bool("false") == True` の罠を踏まないよう ``is_correction`` は実 bool のみ許容する。

    #400 A5（設計 §2.4）: ``category`` は ``is_correction`` に**従属**する契約:
    - ``is_correction=False`` のとき category は必ず ``None``（モデルが値を返しても無視する）
    - ``is_correction=True`` のとき ``CATEGORY_ENUM`` のいずれかを要求するが、
      **enum 不正値・型違い・欠落は verdict 全体を落とさず ``category=None`` に正規化する**
      （category は「対象軸の粒度」であって「修正か否か」の判定そのものではないため、
      ここで厳格に reject すると本体の二値判定まで巻き込んで捨ててしまう）。
    """
    if not isinstance(v, dict):
        return None
    idx = v.get("index")
    if not isinstance(idx, int) or isinstance(idx, bool):  # bool は int のサブクラスなので明示除外
        return None
    is_correction = v.get("is_correction")
    if not isinstance(is_correction, bool):
        return None
    idiom = v.get("idiom")
    if idiom is not None and not isinstance(idiom, str):
        return None
    if isinstance(idiom, str) and not idiom.strip():
        idiom = None
    reason = v.get("reason", "")
    if reason is None:
        reason = ""
    if not isinstance(reason, str):
        return None
    raw_category = v.get("category")
    category = (
        raw_category
        if is_correction and isinstance(raw_category, str) and raw_category in CATEGORY_ENUM
        else None
    )
    return {
        "index": idx,
        "is_correction": is_correction,
        "idiom": idiom,
        "category": category,
        "reason": reason,
    }


def parse_verdicts_result(
    raw: Optional[Any], *, expected_len: Optional[int] = None
) -> Dict[str, Any]:
    """モデル応答（JSON 文字列）から verdict のリストを取り出し、パース成否も返す（#273）。

    code fence・前後のノイズに頑健。「解釈できない」場合は ``ok=False`` を返す — 呼び出し側は
    これを「該当なし（verdicts=[]）」と区別し、判定済みにせず次回再試行に回すこと。
    正しく解釈できて verdicts が空配列（モデルが「該当なし」と明示判定）は ``ok=True``。
    ``ok=False`` になる条件:
      - 応答欠損・壊れた JSON・期待した `{"verdicts": [...]}` 形でない
      - 要素の型が不正（`_validate_verdict` 参照。1 要素でも不正ならバッチ全体を失格にする）
      - `index` の重複

    ``expected_len`` を渡した場合、``0 <= index < expected_len`` の範囲外の要素は
    **その要素だけを無視**し、バッチ全体は失格にしない（#410 round3 [Should]⑤ — round2 では
    「範囲外 index があれば全体失格」だったが、有効な全 index に加えて余分な1件を返した
    だけで再判定され続けるのは過剰、かつ [Must]2 の billed-but-unconfirmed 予算漏れと
    組み合わさると「無限再試行 × 予算漏れ」になるため round3 で方針変更した）。無視した
    件数は ``out_of_range`` に surface する（黙って捨てない）。範囲内の要素は通常どおり
    処理される。

    index の**網羅性**（batch 内の全 index が揃っているか）はここでは検証しない
    （verbosity 側の網羅性検証・#273 P1-2 とは意図的に非対称。範囲検証とは別軸）。
    プロンプトで全 index を返すよう明示したうえで、欠落は `batch.ingest_judgement_results`
    が非修正として確定し件数を `omitted_verdicts` に surface する。欠落を未判定に残す設計は
    「全件非修正のバッチにモデルが `{"verdicts": []}` で答える」正当なケースを毎 drain
    再判定させ費用が際限なく積むため採らない（この契約は
    `test_ingest_legitimate_empty_verdicts_still_marks_judged` が固定している）。

    Returns:
        {"ok": bool, "verdicts": [{index:int, is_correction:bool, idiom:str|None,
         category:str|None, reason:str}], "out_of_range": int}
    """
    if not raw or not isinstance(raw, str):
        return {"ok": False, "verdicts": [], "out_of_range": 0}
    text = raw.strip()
    obj = None
    # まず素直に parse、ダメなら最初の {...} ブロックを拾う
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        m = _JSON_OBJ_RE.search(text)
        if m:
            try:
                obj = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                return {"ok": False, "verdicts": [], "out_of_range": 0}
        else:
            return {"ok": False, "verdicts": [], "out_of_range": 0}
    if not isinstance(obj, dict):
        return {"ok": False, "verdicts": [], "out_of_range": 0}
    verdicts = obj.get("verdicts")
    if not isinstance(verdicts, list):
        return {"ok": False, "verdicts": [], "out_of_range": 0}

    out: List[Dict[str, Any]] = []
    seen_idx: set = set()
    out_of_range = 0
    for v in verdicts:
        item = _validate_verdict(v)
        if item is None or item["index"] in seen_idx:
            return {"ok": False, "verdicts": [], "out_of_range": 0}
        if expected_len is not None and not (0 <= item["index"] < expected_len):
            out_of_range += 1
            continue
        seen_idx.add(item["index"])
        out.append(item)
    return {"ok": True, "verdicts": out, "out_of_range": out_of_range}


def parse_verdicts(raw: Optional[Any]) -> List[Dict[str, Any]]:
    """モデル応答（JSON 文字列）から verdict のリストを取り出す（後方互換 wrapper）。

    壊れた/空の応答は [] にフォールバックする（従来どおり・ok/失敗の区別が要る呼び出し側は
    `parse_verdicts_result` を使うこと。#273 で desync を避けるため両者は 1 実装を共有する）。
    """
    return parse_verdicts_result(raw)["verdicts"]
