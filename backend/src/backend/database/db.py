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


    # AIエージェント(agent/service.py)のClaude API呼び出し1回ごとの
    # トークン使用量。session_idはrun_chat(会話)のみ持ち、run_legal_report
    # (単発実行、session_id無し)はNULLになる。cost_usdはagent/pricing.pyの
    # 料金表に無いモデルの場合NULL(料金不明)。他のスナップショット系テーブルと
    # 異なり、これは履歴を積む(全削除しない)——日次/作業ごとの集計に使うため。
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS token_usage
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            created_at TEXT,

            kind TEXT NOT NULL,

            session_id TEXT,

            model TEXT,

            input_tokens INTEGER NOT NULL,

            output_tokens INTEGER NOT NULL,

            cost_usd REAL
        )
        """
    )


    # (2026-08-13追加)Archicad本体への書き込み系操作(engine/height_
    # restriction_write.py、道路斜線制限のenvelopeをMeshとして作成する機能で
    # 初めて追加)の監査ログ。CLAUDE.md「⑩ アクション/操作層」が既存の
    # 書き込み系Tapirラッパー(move_archicad_element等)について「誰が/いつ/
    # 何を変更したか監査ログが無い」と指摘していた前提を、この機能で初めて
    # 満たす。statusは"proposed"(提案のみ、Archicadへはまだ未書き込み)→
    # "written"(承認され実際に書き込み成功)/"failed"(承認され書き込みを
    # 試みたが失敗)/"rejected"(未使用、将来の明示的却下用に予約)の遷移を
    # 取る。token_usage同様、履歴を積む(全削除しない)。
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS write_audit_log
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            created_at TEXT NOT NULL,

            action TEXT NOT NULL,

            status TEXT NOT NULL,

            summary TEXT,

            payload_json TEXT,

            result_guid TEXT,

            error_message TEXT,

            decided_at TEXT
        )
        """
    )


    # (2026-08-14追加)agent/report_graph.pyの法規レポート生成結果を保存する。
    # 従来は/agent/legal_reportのレスポンスとして返されるだけでデータベース
    # には一切保存されておらず、レポートを生成するたびに閾値(基準値)・
    # evidence(参照値・途中結果)・PASS/FAIL/UNKNOWN(判定)が失われていた
    # (ユーザーから「最終結果だけでなく、それぞれの計算の結果を説明できる
    # ものも一緒に保存してほしい」との指摘を受けて追加)。checks_jsonには
    # evaluate_legal_rule()の戻り値のリストをそのまま(rule_id/title/
    # threshold/comparator/threshold_unit/disclaimer/items[].evidence/
    # items[].measured_value/items[].status/legal_sources/missing_inputsを
    # 全て含む)JSON文字列として保存する——チェック・項目ごとに個別カラムへ
    # 分解する専用スキーマは作らず、write_audit_log.payload_json・
    # engine_analysis_results.graph_dataと同じ「計算結果一式をそのまま
    # JSONで保持する」方針を踏襲する(理由: checksの構造はチェックの種類
    # ごとにevidenceのキーが異なり(engine/rule_engine.pyのRULE_CHECK_
    # REGISTRY参照)、正規化した固定スキーマでは新しいチェック追加のたびに
    # マイグレーションが必要になってしまう)。token_usage・write_audit_logと
    # 同様、履歴を積む(全削除しない、生成のたびに追記する)。
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS legal_report_history
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            generated_at TEXT NOT NULL,

            report TEXT,

            checks_json TEXT NOT NULL
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


def insert_token_usage(kind, session_id, model, input_tokens, output_tokens, cost_usd):
    """AIエージェントのClaude API呼び出し1回分のトークン使用量を1行追加する。

    engine_analysis_results等と異なり全削除しない(履歴を積む)。
    kindは"chat"(run_chat、session_id有り)/"legal_report"(run_legal_report、
    session_id無し)。cost_usdはagent/pricing.pyで料金表に無いモデルの場合None。
    """

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO token_usage
        (
            created_at,
            kind,
            session_id,
            model,
            input_tokens,
            output_tokens,
            cost_usd
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            kind,
            session_id,
            model,
            input_tokens,
            output_tokens,
            cost_usd,
        ),
    )

    conn.commit()

    conn.close()


def get_token_usage_daily():
    """日付(UTC)ごとにトークン使用量・料金を集計して新しい順に返す。"""

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            date(created_at) AS date,
            COUNT(*) AS call_count,
            SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens,
            SUM(cost_usd) AS cost_usd
        FROM token_usage
        GROUP BY date(created_at)
        ORDER BY date DESC
        """
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def get_token_usage_by_job():
    """「作業」(chatはsession_id単位、legal_reportは実行ごと)でトークン使用量を集計する。

    legal_reportにはsession_idが無い(単発実行のため)ので、id列を使って
    行ごとに個別の「作業」として扱う。
    """

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            kind,
            COALESCE(session_id, 'legal_report_' || id) AS job_id,
            MIN(created_at) AS started_at,
            MAX(created_at) AS last_at,
            COUNT(*) AS call_count,
            SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens,
            SUM(cost_usd) AS cost_usd
        FROM token_usage
        GROUP BY kind, job_id
        ORDER BY last_at DESC
        """
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def insert_audit_log_proposal(action, summary, payload_json):
    """書き込み系操作の提案(まだArchicadへは未書き込み)を1行追加する。

    engine/height_restriction_write.pyのpropose_*()から呼ばれる。
    statusは"proposed"で作成し、後でmark_audit_log_written()/
    mark_audit_log_failed()が更新する。新規行のidを返す(承認時に
    approve_*(proposal_id)へ渡す)。
    """

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO write_audit_log
        (created_at, action, status, summary, payload_json)
        VALUES (?, ?, 'proposed', ?, ?)
        """,
        (datetime.now(timezone.utc).isoformat(), action, summary, payload_json),
    )

    conn.commit()

    new_id = cursor.lastrowid

    conn.close()

    return new_id


def get_audit_log_entry(entry_id):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("SELECT * FROM write_audit_log WHERE id = ?", (entry_id,))

    row = cursor.fetchone()

    conn.close()

    return row


def mark_audit_log_written(entry_id, result_guid):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE write_audit_log
        SET status = 'written', result_guid = ?, decided_at = ?
        WHERE id = ?
        """,
        (result_guid, datetime.now(timezone.utc).isoformat(), entry_id),
    )

    conn.commit()

    conn.close()


def mark_audit_log_failed(entry_id, error_message):

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE write_audit_log
        SET status = 'failed', error_message = ?, decided_at = ?
        WHERE id = ?
        """,
        (error_message, datetime.now(timezone.utc).isoformat(), entry_id),
    )

    conn.commit()

    conn.close()


def list_audit_log(limit=50):
    """監査ログを新しい順に返す(frontend「監査ログ」表示・確認用)。"""

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM write_audit_log ORDER BY id DESC LIMIT ?", (limit,)
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def save_legal_report(report, checks):
    """法規レポート生成結果(基準値・参照値・途中結果・判定を含む全内容)を
    1行追加する(agent/service.pyのrun_legal_report()から呼ばれる)。

    engine_analysis_results等と異なり全削除しない(履歴を積む、
    token_usage・write_audit_logと同じ方針)。checksはevaluate_legal_rule()
    の戻り値のリストをそのままJSON化して保存する。生成したgenerated_atを
    返す(呼び出し側がレスポンスと紐付けて確認できるように)。
    """

    conn = get_connection()

    cursor = conn.cursor()

    generated_at = datetime.now(timezone.utc).isoformat()

    cursor.execute(
        """
        INSERT INTO legal_report_history
        (generated_at, report, checks_json)
        VALUES (?, ?, ?)
        """,
        (generated_at, report, json.dumps(checks)),
    )

    conn.commit()

    conn.close()

    return generated_at


def list_legal_report_history(limit=20):
    """法規レポートの生成履歴を新しい順に返す(概要のみ、checks_jsonは
    含めない——一覧表示で毎回全項目のevidenceまで読み込むと重くなるため、
    詳細はget_legal_report_history_entry()で個別に取得する)。
    """

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, generated_at, report
        FROM legal_report_history
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cursor.fetchall()

    conn.close()

    return rows


def get_legal_report_history_entry(entry_id):
    """法規レポート生成履歴1件の全内容(checks_json含む)を返す(無ければNone)。"""

    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, generated_at, report, checks_json FROM legal_report_history WHERE id = ?",
        (entry_id,),
    )

    row = cursor.fetchone()

    conn.close()

    return row