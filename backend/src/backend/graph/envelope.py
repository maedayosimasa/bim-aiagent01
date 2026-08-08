"""住戸/区画全体を表す「大分類ゾーン」(実室ではない)を幾何的に検出する。

Archicadのゾーンカテゴリ属性の命名規則(大分類名/番号付きサブタイプ等)は
Archicadのテンプレートによって書き方が様々でありうる。特定プロジェクトの
命名パターンに依存した判定は、別のテンプレートでは通用しない可能性が
ある。一方、「あるZoneが同じ階の他の複数のZoneを面積の大部分にわたって
幾何的に包含している」という事実はテンプレートに関わらず常に成り立つ
(実データで検証済み: "Cタイプ"という57.3m2のZoneが、同じ住戸内の実室
Zone14件を面積100%包含していた)。そのため除外判定はこの幾何包含を
唯一の根拠にする(カテゴリ名は補助的な表示情報にとどめる、
archicad_mcp/tapir.pyのget_zone_categories()参照)。
"""

# 他の同一階Zoneをこの割合以上の面積で覆っていれば「包含している」とみなす。
CONTAINMENT_AREA_RATIO = 0.8

# 包含している側は包含されている側よりこの倍率以上大きい面積を持つことを
# 要求する。ほぼ同面積のZone同士が偶然大きく重なっただけのケース(実データ
# の異常/重複)を「包含関係」と誤認しないための歯止め。
MIN_SIZE_RATIO = 1.3

# 包含しているZoneの件数がこれ未満なら、単なる隣接部屋同士のわずかな重なり
# とみなし、大分類ゾーンとは判定しない。
MIN_CONTAINED_COUNT = 3


def find_envelope_zone_guids(zone_records):
    """住戸/区画全体を表す大分類ゾーンのguid集合を返す。

    zone_recordsは{"guid", "floor", "polygon"}(polygonはshapely Polygon、
    floorは同一階かどうかを比較できる値であれば何でも良い)のリスト。
    """

    envelope_guids = set()

    for outer in zone_records:

        outer_area = outer["polygon"].area

        if outer_area == 0:
            continue

        contained_count = 0

        for inner in zone_records:

            if inner["guid"] == outer["guid"] or inner["floor"] != outer["floor"]:
                continue

            inner_area = inner["polygon"].area

            if inner_area == 0 or outer_area <= inner_area * MIN_SIZE_RATIO:
                continue

            overlap_ratio = outer["polygon"].intersection(inner["polygon"]).area / inner_area

            if overlap_ratio > CONTAINMENT_AREA_RATIO:
                contained_count += 1

        if contained_count >= MIN_CONTAINED_COUNT:
            envelope_guids.add(outer["guid"])

    return envelope_guids
