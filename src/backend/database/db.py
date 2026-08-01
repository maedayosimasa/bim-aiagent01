import sqlite3


DB_PATH = "bim_cache.db"


def get_connection():

    conn = sqlite3.connect(
        DB_PATH
    )

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
        SELECT *
        FROM elements
        """
    )


    rows = cursor.fetchall()


    conn.close()


    return rows