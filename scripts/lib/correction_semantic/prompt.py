"""correction_semantic.prompt — バッチプロンプト組み立て + verdict パース（#431）。

30 件程度の発話を 1 プロンプトにまとめ、Haiku に「ユーザーが Claude の方向を正した
ターンか」を二値判定させ、修正なら言い回し（イディオム）を抽出させる。

#431 背景の修正スタイル（語彙でなく意味論でしか拾えない）を例示する:
- 正しい値の後置型: 「つむぎにしてほしい、四国めたんじゃなくて」
- ソフト指摘型:     「P6のデザインが違うんだけど」
- 観察型:           「〜気がするんだよなぁ」

応答は厳格な JSON（{"verdicts": [{index, is_correction, idiom, reason}]}）を要求する。
パーサは code fence・前後ノイズに頑健。**「解釈できない（壊れた JSON）」と「正しく解釈できて
verdicts が空」は意味が違う**（#273）ため `parse_verdicts_result` で `ok` フラグとして区別する。
`ok=False`（壊れた JSON・応答欠損）を呼び出し側が「該当なし」と誤読すると、パース失敗バッチが
判定済みとして確定し二度と再判定されない（応答欠損は再試行されるのに壊れた応答はされない、
という非対称の温床になる）。`parse_verdicts`（後方互換）は従来どおり両ケースとも [] を返す。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

# 抽出する JSON object をテキストから拾うための緩い探索（code fence 等を剥がす）。
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def build_batch_prompt(utterances: List[Dict[str, Any]]) -> str:
    """発話リストから 1 バッチ分の判定プロンプトを組み立てる（決定論・IO なし）。

    各発話に 0 始まりの index を振り、index でひも付けて判定を返させる。
    prev_action（直前の Claude のツール操作）を文脈として渡す（修正の判定材料）。
    """
    lines: List[str] = []
    for i, u in enumerate(utterances):
        prev = u.get("prev_action") or "(なし)"
        text = (u.get("text") or "").replace("\n", " ").strip()
        lines.append(f"[{i}] 直前のClaudeの操作: {prev}\n    ユーザー発話: {text}")
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
        "出力は厳格な JSON のみ（前後に説明文を付けない）。形式:\n"
        '{"verdicts": [{"index": 0, "is_correction": true, "idiom": "四国めたんじゃなくて", '
        '"reason": "正しい値を後置で言い直している"}, ...]}\n\n'
        "判定対象:\n"
        f"{listing}\n"
    )


def _validate_verdict(v: object) -> Optional[Dict[str, Any]]:
    """1 verdict 要素を厳格型検証し、正規化した dict を返す（不正なら None）。

    #273 P1-1（codex 指摘）: 「構文上 valid だが意味的に壊れている」要素
    （`"index": "0"` の型違い・`"is_correction": "false"` の文字列）を黙って捨てて続行すると、
    捨てた分だけ verdicts が薄くなり `by_index.get(local_i)` が None になって「非修正」と
    誤確定する（#273 が塞いだはずの事故が partial-invalid 型で再発する）。**1 要素でも
    不正なら呼び出し側はバッチ全体を ok=False にすること**（不正要素だけ捨てて部分採用しない）。
    `bool("false") == True` の罠を踏まないよう ``is_correction`` は実 bool のみ許容する。
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
    return {
        "index": idx,
        "is_correction": is_correction,
        "idiom": idiom,
        "reason": reason,
    }


def parse_verdicts_result(raw: Optional[Any]) -> Dict[str, Any]:
    """モデル応答（JSON 文字列）から verdict のリストを取り出し、パース成否も返す（#273）。

    code fence・前後のノイズに頑健。「解釈できない」場合は ``ok=False`` を返す — 呼び出し側は
    これを「該当なし（verdicts=[]）」と区別し、判定済みにせず次回再試行に回すこと。
    正しく解釈できて verdicts が空配列（モデルが「該当なし」と明示判定）は ``ok=True``。
    ``ok=False`` になる条件:
      - 応答欠損・壊れた JSON・期待した `{"verdicts": [...]}` 形でない
      - 要素の型が不正（`_validate_verdict` 参照。1 要素でも不正ならバッチ全体を失格にする）
      - `index` の重複

    index の**網羅性**（batch 内の全 index が揃っているか）はここでは検証しない
    （batch.py の設計判断: モデルが一部 index を省略するのは「その発話は非修正」の
    暗黙表現として許容している。verbosity 側の網羅性検証・#273 P1-2 とは意図的に非対称）。

    Returns:
        {"ok": bool, "verdicts": [{index:int, is_correction:bool, idiom:str|None, reason:str}]}
    """
    if not raw or not isinstance(raw, str):
        return {"ok": False, "verdicts": []}
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
                return {"ok": False, "verdicts": []}
        else:
            return {"ok": False, "verdicts": []}
    if not isinstance(obj, dict):
        return {"ok": False, "verdicts": []}
    verdicts = obj.get("verdicts")
    if not isinstance(verdicts, list):
        return {"ok": False, "verdicts": []}

    out: List[Dict[str, Any]] = []
    seen_idx: set = set()
    for v in verdicts:
        item = _validate_verdict(v)
        if item is None or item["index"] in seen_idx:
            return {"ok": False, "verdicts": []}
        seen_idx.add(item["index"])
        out.append(item)
    return {"ok": True, "verdicts": out}


def parse_verdicts(raw: Optional[Any]) -> List[Dict[str, Any]]:
    """モデル応答（JSON 文字列）から verdict のリストを取り出す（後方互換 wrapper）。

    壊れた/空の応答は [] にフォールバックする（従来どおり・ok/失敗の区別が要る呼び出し側は
    `parse_verdicts_result` を使うこと。#273 で desync を避けるため両者は 1 実装を共有する）。
    """
    return parse_verdicts_result(raw)["verdicts"]
