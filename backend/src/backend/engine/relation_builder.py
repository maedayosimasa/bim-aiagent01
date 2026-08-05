from ..graph.relation import calculate_relations

from ..database.db import (
    clear_connections,
    insert_connection,
    get_elements,
    save_graph_relation_results,
)


def rebuild_connections():

    print("=== Rebuilding Connections ===")

    clear_connections()

    relations = calculate_relations()

    for relation in relations:

        insert_connection(

            relation["source_guid"],

            relation["target_guid"],

            relation["relation"],

            relation["distance"]

        )

    print(
        f"{len(relations)} connections created."
    )

    # 開発時に検証できるよう、種別情報を付けた同じ計算結果をconnectionsとは
    # 別の専用テーブルにも保存する(connectionsはgraph/topology.pyがグラフ
    # 構築のたびに読む現用データなので、検証用途とは分離しておく)。
    # 再計算のたびに全削除→まとめて書き込みで置き換える。
    type_by_guid = {element["guid"]: element["type"] for element in get_elements()}

    save_graph_relation_results([
        {
            "source_guid": relation["source_guid"],
            "source_type": type_by_guid.get(relation["source_guid"]),
            "target_guid": relation["target_guid"],
            "target_type": type_by_guid.get(relation["target_guid"]),
            "relation": relation["relation"],
            "distance": relation["distance"],
        }
        for relation in relations
    ])

    return relations