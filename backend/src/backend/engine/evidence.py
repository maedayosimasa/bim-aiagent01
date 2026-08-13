"""Evidence Layer: 確定的なBIM実測値と、ヒューリスティック/ノイズを含む候補とを
下流(Claim Validator、将来のエージェント推論)が区別できるよう、
evidence_confidenceタグを明示的に付与する薄いレイヤー。

ユーザー提示のエージェント設計パターン(Router→Evidence Layer→Rule Engine→
分岐{Missing Input→Interrupt→Resume, Determination→Answer Graph→
Claim Validator}→Response)の検討を経て導入した。既存の判定ロジック自体は
変えない(rule_engine.py/window_classifier.py等の計算式はそのまま)——この
プロジェクトはこれまでも「参考値です」「ノイズを含む候補」等の免責文で同じ
区別を人間向けの散文として表現してきたが(rule_engine.pyのlegal_sources、
window_classifier.pyのdisclaimer等)、機械可読なタグとしては存在しなかった。

Legal Knowledge Builder自体が返す"confidence"(正規表現抽出のconfidence
スコア、rule_engine.pyのlegal_sources参照)とは別物なので、フィールド名は
"evidence_confidence"にして衝突を避けている。
"""

from enum import Enum


class EvidenceConfidence(str, Enum):
    DETERMINISTIC = "deterministic"
    """BIM実測値・幾何演算から直接算出し、推測を含まない
    (例: rule_engine.pyのPASS/FAIL/UNKNOWN判定項目)。"""

    HEURISTIC = "heuristic"
    """トポロジー・命名等からの推定で、ドキュメント化された既知の限界がある
    (例: window_classifier.pyの外部窓/内部窓判定)。"""

    CANDIDATE = "candidate"
    """検索・正規表現抽出によるノイズを含む候補集合で、確定的な根拠ではない
    (例: rule_engine.pyのlegal_sources)。"""


def tag(items: list[dict], confidence: EvidenceConfidence) -> list[dict]:
    """items内の各dictにevidence_confidenceフィールドを追加した新しいリストを返す。"""
    return [{**item, "evidence_confidence": confidence.value} for item in items]
