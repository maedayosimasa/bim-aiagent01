import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[3]

DB_PATH = BASE_DIR / "bim_cache.db"



def get_connection():

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    return conn


@contextmanager
def transaction():
    """1つの接続の上で1つのトランザクションとして読み書きを行うための
    コンテキストマネージャ。ブロックが正常終了すればコミットし、例外が
    送出されればロールバックする。いずれの場合も接続を確実にクローズする。

    (2026-08-14追加)このプロジェクトの各DB関数はこれまで「1関数=1接続=
    1コミット」という単純な方針で個別にconn = get_connection()...
    conn.commit(); conn.close()を書いていた。これには2つの信頼性上の
    問題があった:
      1. 例外発生時にcommit()もrollback()も呼ばれず、conn.close()にも
         到達しないまま関数を抜けてしまう(接続リーク、コミットされて
         いない変更が宙に浮く)。
      2. 「全削除してから書き込み直す」処理(elements/connections等)が、
         削除と書き込みで別々の接続・別々のコミットにまたがっていた。
         同期処理が削除後・書き込み完了前にクラッシュすると、DBは
         「既存データより悪い、中途半端に空の状態」のまま残ってしまう
         (実際にsync_from_archicad()・rebuild_connections()で発生し
         うる不具合だった)。
    全てのDB関数をこのコンテキストマネージャ経由に統一し、(1)を解消する。
    (2)は個別にreplace_all_elements()/replace_all_connections()という
    「削除+書き込みを1トランザクションにまとめた」専用関数を新設して
    解消する(下記参照)。
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



def create_tables():

    with transaction() as conn:
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
        # reused_from_id: (2026-08-14追加、差分キャッシュ)このレポートが新規に
        # LLMで生成されたのではなく、前回保存済みの判定結果(checks)と完全一致
        # したため、そのレポート文をそのまま再利用したことを示す。再利用時の
        # 参照元行のid、新規生成時はNULL。save_legal_report()のdocstring参照。
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS legal_report_history
            (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                generated_at TEXT NOT NULL,

                report TEXT,

                checks_json TEXT NOT NULL,

                reused_from_id INTEGER
            )
            """
        )

        # (2026-08-14追加)legal_report_historyがreused_from_id列の追加より前に
        # 既に作成されている既存のbim_cache.dbでは、上のCREATE TABLE IF NOT
        # EXISTSは列追加を反映しない(SQLiteは既存テーブルのスキーマを変更
        # しない)。列が無ければALTER TABLEで追加する(このプロジェクトで初めて
        # 必要になった、テーブル作成後のスキーマ移行)。
        existing_columns = {
            row["name"] for row in cursor.execute("PRAGMA table_info(legal_report_history)")
        }
        if "reused_from_id" not in existing_columns:
            cursor.execute("ALTER TABLE legal_report_history ADD COLUMN reused_from_id INTEGER")



def insert_element(
    guid,
    element_type,
    name,
    properties,
    geometry
):

    with transaction() as conn:
        conn.execute(
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



def replace_all_elements(elements):
    """elementsテーブルを全削除してから、渡されたelements一覧をまとめて
    1トランザクションで書き込む(archicad_mcp/server.pyのsync_from_
    archicad()から呼ばれる)。

    (2026-08-14追加)以前はsync_from_archicad()がclear_elements()
    (1コミット)の後、要素ごとにinsert_element()を個別に呼んでいた
    (要素数分の個別コミット)。同期処理が要素の途中(Archicadブリッジとの
    接続切断・例外等)で中断すると、DBは「全要素削除済みだが新データは
    一部のみ」という、既存データより悪い中途半端な状態のまま残ってしまう
    ——このプロジェクトの他の全ての計算がelementsテーブルを起点にしている
    ため、この状態は下流の全計算を巻き込んで壊れる。1トランザクションに
    まとめることで、失敗時は自動的にロールバックされ、削除前の状態が
    そのまま維持されることを保証する(transaction()のdocstring参照)。

    elementsは(guid, element_type, name, properties_json, geometry_json)
    のタプルのリスト。
    """
    with transaction() as conn:
        conn.execute("DELETE FROM elements")
        if elements:
            conn.executemany(
                """
                INSERT INTO elements (guid, type, name, properties, geometry)
                VALUES (?, ?, ?, ?, ?)
                """,
                elements,
            )



def get_elements():

    with transaction() as conn:
        return conn.execute(
            """
            SELECT
                guid,
                type,
                name,
                properties,
                geometry
            FROM elements
            """
        ).fetchall()


def get_element(guid):

    with transaction() as conn:
        return conn.execute(
            """
            SELECT guid, type, name, properties, geometry
            FROM elements
            WHERE guid = ?
            """,
            (guid,),
        ).fetchone()


def update_element_properties(guid, properties):
    """既存プロパティに指定のキー/値をマージして更新する(全置換ではない)。"""

    with transaction() as conn:
        row = conn.execute(
            "SELECT properties FROM elements WHERE guid = ?", (guid,)
        ).fetchone()

        if row is None:
            return False

        existing = json.loads(row["properties"]) if row["properties"] else {}
        existing.update(properties)

        conn.execute(
            "UPDATE elements SET properties = ? WHERE guid = ?",
            (json.dumps(existing), guid),
        )

        return True


def update_element_geometry(guid, geometry):
    """ジオメトリはマージせず全置換する(座標列の部分マージに意味がないため)。"""

    with transaction() as conn:
        cursor = conn.execute(
            "UPDATE elements SET geometry = ? WHERE guid = ?",
            (json.dumps(geometry), guid),
        )

        return cursor.rowcount > 0


def delete_element(guid):

    with transaction() as conn:
        cursor = conn.execute("DELETE FROM elements WHERE guid = ?", (guid,))

        return cursor.rowcount > 0


def get_connections():

    with transaction() as conn:
        return conn.execute(
            """
            SELECT
                source_guid,
                target_guid,
                relation,
                distance
            FROM connections
            """
        ).fetchall()


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

    with transaction() as conn:
        conn.executemany(
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


def clear_connections():

    with transaction() as conn:
        conn.execute(
            """
            DELETE FROM connections
            """
        )


def replace_all_connections(relations):
    """connectionsテーブルを全削除してから、渡されたrelations一覧をまとめて
    1トランザクションで書き込む(engine/relation_builder.pyの
    rebuild_connections()から呼ばれる)。

    (2026-08-14追加)以前はrebuild_connections()がclear_connections()
    (1コミット)とinsert_connections_bulk()(別の1コミット)を別々の
    トランザクションとして呼んでいた。connectionsはgraph/topology.pyが
    グラフ構築のたびに読む現用データであり、多くのengine計算(法規チェック・
    避難経路解析等)がこれに依存する。削除と書き込みの間でクラッシュすると
    connectionsが空のまま残り、以降の計算が「関係が一切無い」という誤った
    結果を返し続けてしまう。1トランザクションにまとめ、失敗時はロール
    バックされ削除前の状態を維持することを保証する。

    relationsは{source_guid, target_guid, relation, distance}を持つ
    dictのリスト。
    """
    with transaction() as conn:
        conn.execute("DELETE FROM connections")
        if relations:
            conn.executemany(
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


def clear_elements():

    with transaction() as conn:
        conn.execute(
            """
            DELETE FROM elements
            """
        )


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

    with transaction() as conn:
        conn.execute("DELETE FROM engine_analysis_results")

        conn.execute(
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


def get_engine_analysis_result():
    """最後に保存されたengine解析結果スナップショットを返す(なければNone)。"""

    with transaction() as conn:
        return conn.execute(
            "SELECT * FROM engine_analysis_results ORDER BY id DESC LIMIT 1"
        ).fetchone()


def save_graph_relation_results(rows):
    """graph側の関係計算結果一式を全削除→まとめて書き込みで置き換える。

    rowsは{source_guid, source_type, target_guid, target_type, relation,
    distance}を持つdictのリスト。computed_atはこの関数内で統一して付与する。
    """

    with transaction() as conn:
        conn.execute("DELETE FROM graph_relation_results")

        computed_at = datetime.now(timezone.utc).isoformat()

        conn.executemany(
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

        return computed_at


def get_graph_relation_results():

    with transaction() as conn:
        return conn.execute(
            "SELECT * FROM graph_relation_results ORDER BY id"
        ).fetchall()


def insert_token_usage(kind, session_id, model, input_tokens, output_tokens, cost_usd):
    """AIエージェントのClaude API呼び出し1回分のトークン使用量を1行追加する。

    engine_analysis_results等と異なり全削除しない(履歴を積む)。
    kindは"chat"(run_chat、session_id有り)/"legal_report"(run_legal_report、
    session_id無し)。cost_usdはagent/pricing.pyで料金表に無いモデルの場合None。
    """

    with transaction() as conn:
        conn.execute(
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


def get_token_usage_daily():
    """日付(UTC)ごとにトークン使用量・料金を集計して新しい順に返す。"""

    with transaction() as conn:
        return conn.execute(
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
        ).fetchall()


def get_token_usage_by_job():
    """「作業」(chatはsession_id単位、legal_reportは実行ごと)でトークン使用量を集計する。

    legal_reportにはsession_idが無い(単発実行のため)ので、id列を使って
    行ごとに個別の「作業」として扱う。
    """

    with transaction() as conn:
        return conn.execute(
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
        ).fetchall()


def insert_audit_log_proposal(action, summary, payload_json):
    """書き込み系操作の提案(まだArchicadへは未書き込み)を1行追加する。

    engine/height_restriction_write.pyのpropose_*()から呼ばれる。
    statusは"proposed"で作成し、後でmark_audit_log_written()/
    mark_audit_log_failed()が更新する。新規行のidを返す(承認時に
    approve_*(proposal_id)へ渡す)。
    """

    with transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO write_audit_log
            (created_at, action, status, summary, payload_json)
            VALUES (?, ?, 'proposed', ?, ?)
            """,
            (datetime.now(timezone.utc).isoformat(), action, summary, payload_json),
        )

        return cursor.lastrowid


def get_audit_log_entry(entry_id):

    with transaction() as conn:
        return conn.execute(
            "SELECT * FROM write_audit_log WHERE id = ?", (entry_id,)
        ).fetchone()


def mark_audit_log_written(entry_id, result_guid):

    with transaction() as conn:
        conn.execute(
            """
            UPDATE write_audit_log
            SET status = 'written', result_guid = ?, decided_at = ?
            WHERE id = ?
            """,
            (result_guid, datetime.now(timezone.utc).isoformat(), entry_id),
        )


def mark_audit_log_failed(entry_id, error_message):

    with transaction() as conn:
        conn.execute(
            """
            UPDATE write_audit_log
            SET status = 'failed', error_message = ?, decided_at = ?
            WHERE id = ?
            """,
            (error_message, datetime.now(timezone.utc).isoformat(), entry_id),
        )


def list_audit_log(limit=50):
    """監査ログを新しい順に返す(frontend「監査ログ」表示・確認用)。"""

    with transaction() as conn:
        return conn.execute(
            "SELECT * FROM write_audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def save_legal_report(report, checks, reused_from_id=None):
    """法規レポート生成結果(基準値・参照値・途中結果・判定を含む全内容)を
    1行追加する(agent/service.pyのrun_legal_report()から呼ばれる)。

    engine_analysis_results等と異なり全削除しない(履歴を積む、
    token_usage・write_audit_logと同じ方針)。checksはevaluate_legal_rule()
    の戻り値のリストをそのままJSON化して保存する。生成したgenerated_atを
    返す(呼び出し側がレスポンスと紐付けて確認できるように)。

    reused_from_id(2026-08-14追加、差分キャッシュ): 今回のchecksが前回
    保存済みの内容と完全一致し、LLMを呼ばずレポート文を再利用した場合、
    その参照元行のidを渡す。新規にLLMで生成した場合はNone。
    """

    with transaction() as conn:
        generated_at = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """
            INSERT INTO legal_report_history
            (generated_at, report, checks_json, reused_from_id)
            VALUES (?, ?, ?, ?)
            """,
            (generated_at, report, json.dumps(checks), reused_from_id),
        )

        return generated_at


def list_legal_report_history(limit=20):
    """法規レポートの生成履歴を新しい順に返す(概要のみ、checks_jsonは
    含めない——一覧表示で毎回全項目のevidenceまで読み込むと重くなるため、
    詳細はget_legal_report_history_entry()で個別に取得する)。
    """

    with transaction() as conn:
        return conn.execute(
            """
            SELECT id, generated_at, report, reused_from_id
            FROM legal_report_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_legal_report_history_entry(entry_id):
    """法規レポート生成履歴1件の全内容(checks_json含む)を返す(無ければNone)。"""

    with transaction() as conn:
        return conn.execute(
            """
            SELECT id, generated_at, report, checks_json, reused_from_id
            FROM legal_report_history WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()


def get_latest_legal_report():
    """最後に保存された法規レポート生成結果(checks_json含む全内容)を
    返す(無ければNone)。

    (2026-08-14追加、差分キャッシュ)agent/service.pyのrun_legal_report()
    が、今回計算したchecksと前回保存分が完全一致するかを確認するために
    使う(一致すればLLM呼び出しを省略してレポート文を再利用する、
    agent/report_graph.pyのモジュールdocstring参照)。
    """

    with transaction() as conn:
        return conn.execute(
            "SELECT id, generated_at, report, checks_json FROM legal_report_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
