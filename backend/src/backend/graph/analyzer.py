import json

import networkx as nx

from .geometry import geometry_from_json
from .relation_rules import RELATION_RULES


# RELATION_RULESに登場する要素タイプの集合。Column/Beam/Slabなど関係計算の
# 対象外タイプはRELATION_RULESにどの組み合わせでも登場しないため、
# calculate_relations()側で永遠にエッジを持てず孤立ノードのまま残る。
# connected判定にこれらを含めてしまうと、壁/部屋/ドア/窓が完全に連結して
# いても対象外ノードのせいで常にFalseになってしまうため、判定対象から除く。
RELATION_TARGET_TYPES = {element_type for pair in RELATION_RULES for element_type in pair}


def analyze_graph(graph):

    relevant_nodes = [
        node
        for node, data in graph.nodes(data=True)
        if data.get("type") in RELATION_TARGET_TYPES
    ]
    relevant_subgraph = graph.subgraph(relevant_nodes)

    return {

        "nodes": graph.number_of_nodes(),

        "edges": graph.number_of_edges(),

        "connected":
            nx.is_connected(relevant_subgraph)
            if relevant_subgraph.number_of_nodes() > 0
            else False

    }



def graph_summary(graph):

    rooms = []

    for node, data in graph.nodes(data=True):

        if data.get("type") == "Room":

            rooms.append(node)


    return {

        "nodes":
            graph.number_of_nodes(),

        "edges":
            graph.number_of_edges(),

        "rooms":
            rooms

    }


# 隣接/接続関係を一つも持たない主要建築要素(壁・部屋・ドア・窓)を報告する
# メッセージ。calculate_relations()の距離閾値(relation_rules.py)に一つも
# 該当しなかった要素なので、ジオメトリの欠損/座標ズレや、ドアが壁のオーナー
# に正しく紐付いていない、といったデータ品質上の問題を示している可能性が高い。
ISOLATED_ELEMENT_MESSAGES_JA = {
    "Wall": "どの要素とも隣接/接続していない壁です",
    "Door": "どの壁/部屋とも接続していないドアです",
    "Window": "どの壁とも接続していない窓です",
    "Room": "どの壁とも接続していない部屋です",
    "Zone": "どの壁とも接続していない部屋です",
}


def find_isolated_elements(graph):

    issues = []

    for node, data in graph.nodes(data=True):

        element_type = data.get("type")
        message = ISOLATED_ELEMENT_MESSAGES_JA.get(element_type)

        if message is None or graph.degree(node) > 0:
            continue

        issues.append({
            "type": "isolated_element",
            "severity": "warning",
            "element_guid": node,
            "element_type": element_type,
            "element_name": data.get("name"),
            "message": message,
        })

    return issues


# 壁の芯線(line)の長さがこれ未満なら、始点/終点が実質同じ座標(またはジオメトリ
# 自体が不正)とみなす。単位はmm。
MIN_WALL_LENGTH_MM = 1.0


def find_degenerate_walls(graph):
    """始点と終点が(ほぼ)同じ座標の壁、またはジオメトリが壊れている壁を検出する。

    Archicad同期時の座標欠損やジオメトリ形式の取り違え(line以外の型で保存
    されてしまった等)は、隣接/接続判定(relation.py)では検出できない
    ("孤立"にはならず、たまたま近くの要素と誤って結びつく可能性すらある)。
    ジオメトリそのものの妥当性を直接チェックする必要がある。
    """

    issues = []

    for node, data in graph.nodes(data=True):

        if data.get("type") != "Wall":
            continue

        geometry_json = data.get("geometry")

        try:
            geom = geometry_from_json(geometry_json) if geometry_json else None
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            geom = None

        length = geom.length if geom is not None else 0.0

        if geom is not None and length >= MIN_WALL_LENGTH_MM:
            continue

        issues.append({
            "type": "degenerate_wall_geometry",
            "severity": "error",
            "element_guid": node,
            "element_type": "Wall",
            "element_name": data.get("name"),
            "message": "壁のジオメトリの長さが0、または形状が不正です",
        })

    return issues


def find_ambiguous_door_ownership(graph):
    """2枚以上の壁に同時に"adjacent"判定されているドアを検出する。

    通常ドアは1枚の壁に属するはずなので、2枚以上に隣接している場合は
    ドアの座標が壁のコーナー付近にある、あるいは閾値(relation_rules.py)を
    跨いで複数の壁を拾ってしまっているなど、所属が一意に定まらない状態を
    示す。0枚(どの壁にも属さない)はfind_isolated_elements()側で別途
    検出されるため、ここでは対象にしない。
    """

    issues = []

    for node, data in graph.nodes(data=True):

        if data.get("type") != "Door":
            continue

        owner_walls = [
            neighbor
            for neighbor in graph.neighbors(node)
            if graph.nodes[neighbor].get("type") == "Wall"
            and graph.edges[node, neighbor].get("relation") == "adjacent"
        ]

        if len(owner_walls) <= 1:
            continue

        issues.append({
            "type": "ambiguous_door_wall_ownership",
            "severity": "warning",
            "element_guid": node,
            "element_type": "Door",
            "element_name": data.get("name"),
            "message": f"ドアが{len(owner_walls)}枚の壁に同時に隣接しており、所属する壁が一意に定まりません",
        })

    return issues