import networkx as nx

from ..database.db import get_elements


def build_graph():

    G = nx.Graph()

    elements = get_elements()

    #
    # 1. ノード作成
    #
    for element in elements:

        guid = element["guid"]

        G.add_node(
            guid,
            type=element["type"],
            name=element["name"]
        )

    #
    # 2. サンプル用エッジ作成
    # （後でArchicad MCPの接続情報に置き換える）
    #
    element_map = {}

    for element in elements:
        element_map[element["guid"]] = element

    # GUID一覧
    guids = list(element_map.keys())

    # サンプルとして順番に接続
    #
    # 例
    #
    # wall001 ---- door001 ---- room001
    #
    if len(guids) >= 2:

        for i in range(len(guids) - 1):

            G.add_edge(
                guids[i],
                guids[i + 1]
            )

    return G