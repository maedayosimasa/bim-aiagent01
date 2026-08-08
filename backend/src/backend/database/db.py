import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[3]

DB_PATH = BASE_DIR / "bim_cache.db"



def get_connection():

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    return conn



def create_tables():

    conn = get_connection()

    cursor = conn.cursor()


    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS elements
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            guid TEXT UNIQUE,

            type TEXT,

            name TEXT,

            properties TEXT,

            geometry TEXT
        )
        """
    )


    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS connections
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            source_guid TEXT NOT NULL,

            target_guid TEXT NOT NULL,

            relation TEXT NOT NULL,

            distance REAL DEFAULT 0
        )
        """
    )


    # engine/spatial.py の analyze_space() が計算した解析結果一式(グラフの
    # ノード/エッジ数・連結判定・要素種別ごとの件数・issues・graph_data)を
    # 開発時に検証できるよう保持する。再計算のたびに全削除してから1行だけ
    # 書き込む(sync_from_archicad/rebuild_connectionsと同じ、履歴を積まず
    # 常に最新のみを保持する方式)。
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS engine_analysis_results
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            model_id TEXT,

            computed_at TEXT,

            node_count INTEGER,

            edge_count INTEGER,

            connected INTEGER,

            wall_count INTEGER,

            door_count INTEGER,

            window_count INTEGER,

            room_count INTEGER,

            issues TEXT,

            graph_data TEXT
        )
        """
    )


    # graph/relation.py の calculate_relations() が計算した関係(隣接/接続)
    # 一式を、要素タイプ付きで検証用に保持する。connectionsテーブルは
    # graph/topology.pyがNetworkXグラフ構築のたびに読む「現用データ」なので、
    # それとは別に「最後に計算された内容をそのまま確認する」ための専用テーブル
    # として分離する。再計算のたびに全削除してから書き込み直す。
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS graph_relation_results
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            computed_at TEXT,

            source_guid TEXT NOT NULL,

            source_type TEXT,

            target_guid TEXT NOT NULL,

            target_type TEXT,

            relation TEXT NOT NULL,

            distance REAL DEFAULT 0
        )
        """
    )


    conn.commit()

    conn.close()



def insert_element(
    guid,
    element_type,
    name,
    properties,
    geometry
):

    conn = get_connection()

    cursor = conn.cursor()


    cursor.execute(
        """
        INSERT OR REPLACE INTO elements
        (
            guid,
            type,
            name,
            properties,
            geometry
        )
        VALUES
        (
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            guid,
            element_type,
            name,
            properties,
            geometry
        )
    )


    conn.commit()

    conn.close()



def get_elements():

    conn = get_connection()

    cursor = conn.cursor()


    cursor.execute(
        """
        SELECT
            guid,
            type,
            name,
            properties,
            geometry
        FROM elements
        """
    )


    rows = cursor.fetchall()


    conn.close()


    return rows


def get_element(guid):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT guid, type, name, properties, geometry
        FROM elements
        WHERE guid = ?
        """,
        (guid,),
    )

    row = cursor.fetchone()

    conn.close()

    return row


def update_element_properties(guid, properties):
    """既存プロパティに指定のキー/値をマージして更新する(全置換ではない)。"""

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("SELECT properties FROM elements WHERE guid = ?", (guid,))

    row = cursor.fetchone()

    if row is None:
        conn.close()
        return False

    existing = json.loads(row["properties"]) if row["properties"] else {}
    existing.update(properties)

    cursor.execute(
        "UPDATE elements SET properties = ? WHERE guid = ?",
        (json.dumps(existing), guid),
    )

    conn.commit()

    conn.close()

    return True


def update_element_geometry(guid, geometry):
    """ジオメトリはマージせず全置換する(座標列の部分マージに意味がないため)。"""

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        "UPDATE elements SET geometry = ? WHERE guid = ?",
        (json.dumps(geometry), guid),
    )

    conn.commit()

    updated = cursor.rowcount > 0

    conn.close()

    return updated


def delete_element(guid):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("DELETE FROM elements WHERE guid = ?", (guid,))

    conn.commit()

    deleted = cursor.rowcount > 0

    conn.close()

    return deleted


def get_connections():

    conn = get_connection()

    cursor = conn.cursor()


    cursor.execute(
        """
        SELECT
            source_guid,
            target_guid,
            relation,
            distance
        FROM connections
        """
    )


    rows = cursor.fetchall()


    conn.close()


    return rows

def insert_connections_bulk(relations):
    """複数件の関係をまとめて1コネクション・1トランザクションで書き込む。

    以前はrelation件数分ループしてSQLite接続を1件ずつopen/commit/close
    していたが、実データ(bim_cache.db、4847件)ではこれだけで約14秒
    かかっていた(計算自体はSTRtree化済みで0.2秒程度、ボトルネックは
    DB書き込み側だった)。save_graph_relation_results()と同じく
    executemany()でまとめて書き込む。

    relationsは{source_guid, target_guid, relation, distance}を持つ
    dictのリスト。
    """

    if not relations:
        return

    conn = get_connection()

    cursor = conn.cursor()

    cursor.executemany(
        """
        INSERT INTO connections
        (source_guid, target_guid, relation, distance)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                relation["source_guid"],
                relation["target_guid"],
                relation["relation"],
                relation["distance"],
            )
            for relation in relations
        ],
    )

    conn.commit()

    conn.close()


def clear_connections():

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM connections
        """
    )

    conn.commit()

    conn.close()


def clear_elements():

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM elements
        """
    )

    conn.commit()

    conn.close()


def save_engine_analysis_result(
    model_id,
    computed_at,
    node_count,
    edge_count,
    connected,
    wall_count,
    door_count,
    window_count,
    room_count,
    issues,
    graph_data,
):
    """engine側の解析結果スナップショットを全削除→1行だけ書き込みで置き換える。"""

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("DELETE FROM engine_analysis_results")

    cursor.execute(
        """
        INSERT INTO engine_analysis_results
        (
            model_id,
            computed_at,
            node_count,
            edge_count,
            connected,
            wall_count,
            door_count,
            window_count,
            room_count,
            issues,
            graph_data
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model_id,
            computed_at,
            node_count,
            edge_count,
            1 if connected else 0,
            wall_count,
            door_count,
            window_count,
            room_count,
            issues,
            graph_data,
        ),
    )

    conn.commit()

    conn.close()


def get_engine_analysis_result():
    """最後に保存されたengine解析結果スナップショットを返す(なければNone)。"""

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM engine_analysis_results ORDER BY id DESC LIMIT 1"
    )

    row = cursor.fetchone()

    conn.close()

    return row


def save_graph_relation_results(rows):
    """graph側の関係計算結果一式を全削除→まとめて書き込みで置き換える。

    rowsは{source_guid, source_type, target_guid, target_type, relation,
    distance}を持つdictのリスト。computed_atはこの関数内で統一して付与する。
    """

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("DELETE FROM graph_relation_results")

    computed_at = datetime.now(timezone.utc).isoformat()

    cursor.executemany(
        """
        INSERT INTO graph_relation_results
        (
            computed_at,
            source_guid,
            source_type,
            target_guid,
            target_type,
            relation,
            distance
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                computed_at,
                row["source_guid"],
                row.get("source_type"),
                row["target_guid"],
                row.get("target_type"),
                row["relation"],
                row["distance"],
            )
            for row in rows
        ],
    )

    conn.commit()

    conn.close()

    return computed_at


def get_graph_relation_results():

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM graph_relation_results ORDER BY id"
    )

    rows = cursor.fetchall()

    conn.close()

    return rows