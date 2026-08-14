from ..graph.relation import calculate_relations

from ..database.db import (
    get_elements,
    replace_all_connections,
    save_graph_relation_results,
)


def rebuild_connections():

    print("=== Rebuilding Connections ===")

    # (2026-08-14追加、トランザクション化)以前はclear_connections()
    # (1コミット)の直後にcalculate_relations()を計算し、insert_
    # connections_bulk()(別の1コミット)で書き込んでいたため、計算中は
    # connectionsテーブルが空のまま放置されていた(多くのengine計算が
    # グラフ構築のたびにconnectionsを読むため、その間に読まれると
    # 「関係が一切無い」という誤った結果になりうる)。計算を先に済ませて
    # から、削除+書き込みをreplace_all_connections()で1トランザクション
    # として実行することで、この空白期間自体を無くす
    # (database.db.replace_all_connections()のdocstring参照)。
    relations = calculate_relations()

    replace_all_connections(relations)

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