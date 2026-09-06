"""反映先つき4択（#541 D）の実装・doc 3箇所の同期テスト（codex M4 是正）。

`_reflect_choice_lines`（実装）/ `correction-review.md`（詳細手順）/ `SKILL.md`
（1行要約）の3箇所は独立に編集されうるため、字面の部分一致だけを見るテストでは
「表示の3番と4番だけ入替」「correction-review.md の③だけ --promote-weak に戻す」
「SKILL.md だけ旧4択に戻す」の3変異が全て緑のまま通過しうる（#541 codex M4 指摘）。
本テストは実装の実際の出力（動的に呼び出す・ハードコードしない）を正とし、両 doc
ファイルを実際に読んで「選択肢の番号・順序・各選択肢に対応する実行コマンド」まで
突き合わせる。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_lib_dir = Path(__file__).resolve().parent.parent
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))

from daily import proposal_digest as pd  # noqa: E402

_REPO_ROOT = _lib_dir.parent.parent
_CORRECTION_REVIEW_MD = (
    _REPO_ROOT / "skills" / "evolve" / "references" / "correction-review.md"
)
_SKILL_MD = _REPO_ROOT / "skills" / "evolve" / "SKILL.md"


def _implementation_choices() -> dict:
    """`_reflect_choice_lines` を実際に呼び出し、選択肢番号→(ラベル, 実行コマンド)を
    動的に取り出す（doc 側の期待値をここへハードコードしない＝実装が正）。
    """
    lines = pd._reflect_choice_lines(
        "bin/evolve-reflect", "k1",
        promote_extra="", reject_pj_flag=" --pj pj-a", review_ref="ref.md",
    )
    text = "\n".join(lines)

    labels: dict[int, str] = {}
    for m in re.finditer(r"^\s*(\d)\)\s*(.+)$", text, re.MULTILINE):
        labels[int(m.group(1))] = m.group(2).strip()

    commands: dict[int, str] = {}
    for m in re.finditer(r"(\d)\s*を選んだ場合:.*?`([^`]+)`", text):
        commands[int(m.group(1))] = m.group(2).strip()

    assert set(labels) == {1, 2, 3, 4}, f"実装の選択肢が4件そろっていない: {labels}"
    assert set(commands) == {1, 2, 3, 4}, f"実装の実行コマンドが4件そろっていない: {commands}"
    return {n: (labels[n], commands[n]) for n in (1, 2, 3, 4)}


def test_implementation_choice3_uses_already_reflected_not_promote():
    """陽性対照を兼ねた実装自身の不変条件: 選択肢3は --already-reflected-weak を使い、
    --promote-weak は使わない（この関数が拾う実装が正しく実装されていることの前提確認）。
    """
    choices = _implementation_choices()
    label3, cmd3 = choices[3]
    assert "既に反映済み" in label3
    assert "--already-reflected-weak" in cmd3
    assert "--promote-weak" not in cmd3


def _correction_review_table_labels() -> dict:
    """correction-review.md の「反映先つき4択」表（1つ目の固定4択表。修正在庫の3択表とは
    別）から選択肢番号→label を取る。
    """
    text = _CORRECTION_REVIEW_MD.read_text(encoding="utf-8")
    start = text.index("### 反映先つき4択")
    end = text.index("### 修正在庫の3択")
    section = text[start:end]
    labels: dict[int, str] = {}
    for m in re.finditer(r"^\|\s*(\d)\s*\|\s*([^|]+?)\s*\|", section, re.MULTILINE):
        labels[int(m.group(1))] = m.group(2).strip()
    return labels, section


def _correction_review_bullet_flag(section: str, n: int) -> str:
    """`- **N（...）**:` 箇条書きの本文から、``--project-dir "$PJ"`` 直後に続く実行フラグ
    （``--promote-weak``/``--reject-weak``/``--already-reflected-weak`` 等）を抽出する。
    地の文の「`--promote-weak` を呼ばない」等の否定言及ではなく、実際の実行例
    （``evolve-reflect --project-dir "$PJ" --xxx``）だけを拾う。
    """
    m = re.search(rf"^- \*\*{n}（.*", section, re.MULTILINE)
    assert m, f"choice {n} の箇条書きが見つからない"
    # 次の `- **` 行（または section 末尾）までを1項目の本文とする。
    start = m.start()
    next_m = re.search(r"^- \*\*", section[m.end():], re.MULTILINE)
    end = m.end() + next_m.start() if next_m else len(section)
    bullet = section[start:end]

    flags = re.findall(r'--project-dir\s+"\$PJ"\s+--([\w-]+)', bullet)
    assert flags, f"choice {n} の箇条書きに実行コマンド例が無い: {bullet[:200]}"
    return flags


def test_correction_review_md_choice3_executes_already_reflected_weak():
    """codex M4 変異②の検出: correction-review.md の③だけ --promote-weak に戻すと赤くなる。"""
    choices = _implementation_choices()
    labels, section = _correction_review_table_labels()
    assert labels[3] == choices[3][0] or "既に反映済み" in labels[3]

    flags3 = _correction_review_bullet_flag(section, 3)
    assert "already-reflected-weak" in flags3
    assert "promote-weak" not in flags3


def test_correction_review_md_table_labels_match_implementation_order():
    """4択の番号・順序が実装と一致すること（表の1〜4が実装の1〜4と同じ意味を指す）。"""
    choices = _implementation_choices()
    labels, _section = _correction_review_table_labels()
    assert set(labels) == {1, 2, 3, 4}
    # 「ルールに書く」「いまは反映しない」「既に反映済み」「いいえ」という核心語が、
    # 同じ番号のもとで両側に存在すること（表現の細部までは強制しない）。
    core_words = {
        1: "ルールに書く",
        2: "いまは反映しない",
        3: "既に反映済み",
        4: "いいえ",
    }
    for n, word in core_words.items():
        assert word in labels[n], f"choice {n}: doc label={labels[n]!r} に {word!r} が無い"
        assert word in choices[n][0], f"choice {n}: 実装 label={choices[n][0]!r} に {word!r} が無い"


def test_skill_md_step62_lists_four_choices_in_order_with_already_reflected():
    """codex M4 変異③の検出: SKILL.md だけ旧4択（共通ルール/PJルール/いまは反映しない/いいえ）
    に戻すと、この厳密な連番文字列が消えて赤くなる。
    """
    text = _SKILL_MD.read_text(encoding="utf-8")
    assert "①ルールに書く ②いまは反映しない ③既に反映済み ④いいえ" in text


def test_skill_md_choice3_maps_to_already_reflected_weak_not_promote():
    """SKILL.md の「③既に反映済み」節が --already-reflected-weak を実行し、
    decision="already_reflected" のみで decision="promoted" を経由しないこと。
    """
    text = _SKILL_MD.read_text(encoding="utf-8")
    m = re.search(r"「③既に反映済み」→(.+?)。「④いいえ」", text)
    assert m, "SKILL.md に「③既に反映済み」節が見つからない"
    segment = m.group(1)
    assert "--already-reflected-weak" in segment
    assert 'decision="already_reflected"' in segment
    assert 'decision="promoted"' not in segment


def test_proposal_protocol_docs_require_recommendation_field():
    """#582 round2 [Must]: 2文書の必須項目宣言が、正準句と**完全一致**で揃っていること。

    「どこかに『推奨』の語がある」検査だと、片方の節に別定義を足しても緑のまま通る。
    正準句（`proposal_digest.RECOMMENDATION_DOC_CLAUSE`）を単一ソースにし、
    3箇所（定数・SKILL.md・proposal-protocol.md）の同期をここで固定する。

    なお「別の場所に意味を反転させる文が足されていないか」の意味判定は本テストの
    責務外（正準指示の配送までを決定論で固定し、内容の妥当性は評価と運用観測が担う）。
    """
    import sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "scripts"))
    from lib.daily import proposal_digest as pd

    clause = pd.RECOMMENDATION_DOC_CLAUSE
    assert "推奨なし" in clause and "MUST NOT" in clause, "正準句が推奨契約の体を成していない"

    for name in ("skills/evolve/SKILL.md", "skills/evolve/references/proposal-protocol.md"):
        text = (root / name).read_text(encoding="utf-8")
        assert clause in text, f"{name}: 正準句と完全一致する行が無い（片側 desync）"
        assert "次の4点" in text or "4 点提示" in text, f"{name}: 4 点提示の宣言が無い"
        assert "3 点提示" not in text and "次の3点" not in text, f"{name}: 旧 3 点提示が残存"
