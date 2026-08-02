import json
import sqlite3
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

def insert_connection(
    source_guid,
    target_guid,
    relation,
    distance
    ):

    conn=get_connection()

    cursor=conn.cursor()


    cursor.execute(
        """
        INSERT INTO connections
        (
        source_guid,
        target_guid,
        relation,
        distance
        )

        VALUES
        (
        ?,
        ?,
        ?,
        ?
        )
        """,
        (
        source_guid,
        target_guid,
        relation,
        distance
        )
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