# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 応答言語

このリポジトリで作業する際は、日本語で応答すること。

## プロジェクト概要

BIM空間知能エンジン(bim_aiagent) — Archicadと連携し、BIM要素(壁・部屋・ドア・窓など)の空間関係をグラフ化し、意味検索・空間解析を行うAIエージェントを開発するプロジェクト。将来的にはAIによる解析結果を蓄積・活用するエージェント基盤を目指す。

`backend/`と`frontend/`を含むリポジトリルート全体を単一のgitリポジトリとして管理する。

## コマンド

### backend (`backend/`, uv管理)

```bash
uv sync                          # 依存関係インストール
uv run pytest -q                 # 全テスト実行
uv run pytest tests/test_builder.py::test_name -q   # 単一テスト実行
uv run uvicorn backend.main:app --app-dir src --host 127.0.0.1 --port 8000  # 直接起動(docker外)
```

### frontend (`frontend/`)

```bash
npm install
npm run dev     # Vite開発サーバー (http://localhost:5173)
npm run build   # tsc -b && vite build (型チェック含む)
npm run lint
```

### 全体 (docker-compose、リポジトリルート)

```bash
docker compose up
```
`frontend` + `chromadb` + `backend`(`tailscale`とnetwork namespace共有) の3+1サービス構成。`.env`(`.env.example`参照)に`TS_AUTHKEY`・`ARCHICAD_MCP_URL`を設定する。

## アーキテクチャ

### 全体レイヤー構成(概念図)

BIM空間知能エンジンは以下の順でデータが流れるパイプラインを目指す。状態は現状の進捗(◎実装済み/△部分実装・薄い/✗未実装)。各レイヤーの詳細・ギャップは[今後の開発計画](#今後の開発計画未着手)を参照。

データの流れ: Archicad MCP(Windows側ブリッジ、Tapir Add-on) → ①〜⑩ → React(`frontend/`)

| # | レイヤー | 状態 | 実装 |
|---|---|---|---|
| ① | データ取得層 Data Acquisition | ◎ | `archicad_mcp/client.py`, `tapir.py` |
| ② | データ永続化層 Data Storage | ◎ | `database/db.py`, SQLite |
| ③ | ジオメトリエンジン Geometry Engine | ◎ | `graph/geometry.py`, shapely |
| ④ | 空間関係エンジン Spatial Relation | ◎ | `graph/relation.py`, `relation_rules.py` |
| ⑤ | グラフエンジン Graph Engine | ◎ | `graph/builder.py`, `topology.py`, `export.py` |
| ⑥ | 空間知能エンジン Spatial Intelligence | △ | `engine/spatial.py`, `graph/analyzer.py`, `room.py` |
| ⑦ | 解析結果ストア Analysis Results Store | △ | `database/db.py`の`engine_analysis_results`等、履歴を積まないスナップショットのみ |
| ⑧ | 埋め込み/インデックス層 Embedding & Indexing | △ | `engine/vector_store.py`, ChromaDB |
| ⑨ | RAG / AIエージェント層 | ✗ | 未着手。LLM呼び出し・エージェントループが一切無い |
| ⑩ | アクション/操作層 Action & Audit | △ | `archicad_mcp/server.py`の書き込み系ツールはあるが監査ログが無い |

横断的関心事(全レイヤーに関わる。現状ほぼ未整備):
- **API Gateway**: `main.py`(FastAPI)が全レイヤーをRESTとして束ねる。加えて`/mcp`配下にMCPサーバーとしても公開(ローカルLLM/エージェント用ツール口。DNSリバインディング防止は内部通信専用の前提で無効化してある)。
- **認証・認可**: 未実装。FastAPIエンドポイントに認証が無く、CORSも`http://localhost:5173`固定(本番フロントのオリジン未設定)。
- **可観測性**: 未整備。構造化ログが無く`print()`が数箇所あるのみ。Archicad連携の失敗率/レイテンシ、エンジン計算時間などのメトリクスも無い。

### backend layering

`main.py`(FastAPIルート) → `engine/`(`spatial.py`が解析全体のオーケストレーション、`relation_builder.py`が関係再計算、`vector_store.py`がChromaDB連携、`site.py`が敷地/道路Zoneの名前検索、`room_engine.py`/`evacuation_engine.py`/`code_engine.py`が空間知能エンジンの用途別モジュール、いずれも2026-08-08追加)→ `graph/`(`builder.py`でNetworkXグラフ生成、`topology.py`でエッジ付与、`relation.py`+`relation_rules.py`で要素タイプ間の隣接/接続判定、`export.py`でJSON変換、`analyzer.py`/`room.py`/`search.py`/`geometry.py`/`path.py`(2026-08-08追加、経路探索)) → `database/db.py`(生sqlite3、SQLAlchemyは未使用。`elements`/`connections`に加え、`engine`/`graph`の計算結果を開発時に検証できるよう保持する`engine_analysis_results`(`analyze_space()`のスナップショット、1行のみ)/`graph_relation_results`(`calculate_relations()`の結果を要素タイプ付きで保持、`connections`とは別の検証専用テーブル)がある。いずれも再計算のたびに全削除→書き込み直す方式で、履歴は積まない。`GET /engine/analysis_snapshot` / `GET /graph/relation_snapshot`で参照できる)。

`relation_rules.py`の`RELATION_RULES`はタプルキー`(type_a, type_b)`で隣接/接続の距離閾値を定義する。Archicad実データは部屋を`"Room"`ではなく`"Zone"`と呼ぶため、`Room`用ルールと`Zone`用ルールを両方定義してある(`archicad_mcp/tapir.py`のモジュールdocstring参照)。

### Archicad連携 (`archicad_mcp/`)

backendはArchicad本体に直接接続しない。必ず「archicad-mcp」(Tapir Add-on経由でArchicadのJSON APIを叩く、Windows PC上で動く別プロセス)をHTTP MCP経由で呼び出す1段構成:

- `client.py`: archicad-mcpへのMCPクライアント。接続先URLは`ARCHICAD_MCP_URL`環境変数、またはランタイムオーバーライド(`POST /archicad/connection`、backend再起動でリセットされる)で決まる。未設定でも例外にせず`/archicad/status`が理由付きで返す設計。
- `server.py`: この backend 自身のMCPサーバー定義(`@mcp_server.tool()`)。デコレータは元の関数をそのまま返すだけなので、`main.py`のREST エンドポイントは同じ関数を直接importして呼んでいる(MCPプロトコルを介さない)。
- `tapir.py`: Tapirコマンドの型付きラッパー。Archicad座標系はメートル、本プロジェクトの座標系はミリ単位なので`_to_mm()`で変換している。

`sync_from_archicad()`は差分マージではなく**全削除してから今回取得分を保存**する方式(開発時のデータ確認用途)。`limit<=0`は全件取得を意味する — 全削除方式と組み合わせているため、中途半端なlimitで打ち切ると以前の要素の大半を失う。

ローカル開発時の注意: WSL上でbackendを動かす場合、Windows側の127.0.0.1はWSLから直接届かない(別ネットワーク名前空間)。WSLのデフォルトゲートウェイIP(`ip route`で確認)を使う必要がある。

### frontend

ルーティングライブラリなし、`App.tsx`のローカルstateでタブ切り替え(ダッシュボード/要素同期/意味検索/空間関係グラフ/プロパティ編集)。`api/client.ts`が全エンドポイントの型付きfetchラッパーを集約。サーバー状態管理は`@tanstack/react-query`。表形式表示は`ag-grid-react`、空間関係グラフの可視化は`cytoscape`。

### テストのパターン (`backend/tests/`)

`conftest.py`の`api_client`フィクスチャは session scope で共有される`TestClient`(MCPサーバーの`session_manager.run()`はプロセス内で一度しか呼べないため)。Archicad連携のテストは`mcp.client._memory.InMemoryTransport`+`_make_fake_tapir_server()`(`test_tapir.py`)で実サーバーなしに検証する。

## 制約・方針

- Archicad連携のWindows側ブリッジ(`archicad-mcp\start_http.ps1`)を起動する仕組みはbackend側に作らない。本番はAWS+Tailscale経由で公開される構成のため、リモートでスクリプトを実行できるエンドポイントは設けず、UIには手動起動を促す案内表示のみを置く。

## 今後の開発計画(未着手)

現状はSQLite(生sqlite3)+ChromaDBの構成。[全体レイヤー構成](#全体レイヤー構成概念図)の各レイヤーに対応させて、まだ着手していない項目を挙げる。

### ① データ取得層

- 空間知能エンジンの解析(避難経路解析・法規チェック等)には敷地境界線・道路幅員・道路中心線・方位が必要だが、現状Tapir(archicad-mcp)経由の取得手段には制約がある。2026-08-08時点の対応状況:
  - 敷地境界線・道路: Archicadに専用の要素タイプが無いため、Zoneに用途名(「敷地」「道路」等)を付けて登録する運用を前提に`engine/site.py`で名前検索により暫定対応(`GET /site/boundary`, `GET /site/roads`)。道路幅員・道路中心線はZoneポリゴンの最小外接矩形から幾何的に算出した近似値であり、実測値・設計値そのものではない。
  - 方位: Tapirの`GetGeoLocation`コマンド(`projectLocation.north`、ラジアン)を`GET /archicad/geo_location`で都度取得する形で対応済み。
  - **既知の制約(Windows側の実ソース`archicad-mcp/src/tapir/command_definitions.js`/`common_schema_definitions.js`で確認済み、推測ではない)**: Tapirの`GetDetailsOfElements`が実形状/内容を返せる要素タイプはWall/Beam/Slab/Column/Object/Zone/CurtainWall/Meshのみ。Line/PolyLine/Hatchは実座標列を取得できず`Get3DBoundingBoxes`によるバウンディングボックス矩形近似にしかならず(非直交な線分では誤った形状になる)、Text/Dimensionは`{"error": "Not yet supported element type"}`が返り内容を一切取得できない。そのため敷地境界線・道路がLine/PolyLineとして描かれている場合や、道路幅員がテキスト注記としてのみ記載されている場合は現状取得不可能(`engine/site.py`はこの制約により、敷地・道路がZoneとしてモデリングされている前提でしか動作しない)。
  - **今後の対応方針**: この不足分を埋めるには、現在のMCPサーバー(archicad-mcp、Tapir Add-on経由)とは別経路で、Archicad純正のAPI(C++ Add-On等)を使ってLine/PolyLineの実座標列やTextの表示文字列を直接取得するアドオンを別途開発する必要がある。開発時間の制約により今回は見送り、後日対応とする。

### ② データ永続化層

- PostgreSQL(Dockerコンテナ)を構築し、SQLiteから移行
- Raw BIMデータ用テーブルを作成
- FastAPIからSQLAlchemyなどのORMを通して保存・取得できるようにする
- Reactで各テーブルの内容を確認できる開発用画面を作成する
- `elements`/`connections`に`model_id`列が無く、複数モデル(複数プロジェクト/複数階)を区別できない。`sync_from_archicad()`の全削除方式と合わせて、モデルを跨いだ運用が事実上できない

### ④ 空間関係エンジン

- (2026-08-08対応済み)`calculate_relations()`(`graph/relation.py`)が全要素の総当たり(O(n²))だった問題を、(1)`RELATION_RULES`に登場しない型(Column/Beam/Slab等)の事前除外、(2)`shapely.STRtree`による候補絞り込みの2段階で解消。実データ(5699要素、うち対象1653要素)で約24秒→約0.2秒に短縮。あわせて`database/db.py`の`insert_connection()`を1件ずつ呼んでいた`rebuild_connections()`も、`insert_connections_bulk()`(`executemany`一括書き込み)へ変更(同データで約14秒→約0.3秒)

### ⑥ 空間知能エンジン

- 空間知能エンジン用(ノード・エッジ)テーブルを作成
- (2026-08-06時点で対応済み)`analyze_space()`の`issues`は`find_isolated_elements`/`find_degenerate_walls`/`find_ambiguous_door_ownership`(`graph/analyzer.py`)による実質的な検出を返すようになった。`wall_check`/`door_check`もこれらの検出件数を反映する
- (2026-08-08対応済み)`room_engine.py`(隣室解析)/`evacuation_engine.py`(単一階避難経路解析)/`code_engine.py`(採光有効面積比・バリアフリードア幅の参考値チェック)/`graph/path.py`(経路探索)を追加。`GET /engine/rooms` / `GET /engine/evacuation` / `GET /engine/code/daylighting` / `GET /engine/code/accessible_doors`。詳細と実データでの検証結果は下記「追加モジュール案」の表を参照(表の実現性評価は当時のまま残し、実装後の状況を注記で追記した)
- **(2026-08-08実データで新規発見)EV/PS等の縦シャフト系Zoneは、階をまたいでz範囲が重なる(隙間ではなく重複)ように登録されている**(例: 実データのEVゾーン、1階分`z:400〜3400`・2階分`z:3300〜6300` — 100mm重複)。`calculate_relations()`のz-gap除外(`MAX_Z_GAP_MM=150`)は「隙間」を前提にしており、この重複ケースは原理的に閾値調整では切り分けられない(隙間が負、つまり重なっているため)。結果として`room_engine.py`の隣室解析でPS/EV等が階をまたいで大量に「隣接」判定される、`evacuation_engine.py`の外部ドア判定(Room/Zoneに1つしか繋がらないDoor)が実データでは1件もヒットしない(全ドアがRoom/Zoneに2〜17件接続していた)などの形で表面化する。floorIndexを使った階単位のハードフィルタ、またはシャフト系Zoneの特別扱いが必要(未対応)

#### 追加モジュール案(2026-08-06 実現可否を調査、2026-08-08 実装)

`engine/`配下に機能別モジュールを追加する案。実データ(`bim_cache.db`、当時は4階建てサンプル、2026-08-07に5699要素の実物件へ再同期)で必要なプロパティの有無を確認した結果を含む。

| モジュール | 機能 | 実現性(2026-08-06時点の調査) | 前提条件・注意点 |
|---|---|---|---|
| `room_engine.py`(旧称`room_analyzer.py`) | 隣室解析 | 即着手可 → **実装済み** | `calculate_relations()`が既に計算するRoom/Zone-Room/Zoneの「adjacent」・Room/Zone-Doorの「connects」をそのまま使う。実データで動作確認済みだが、上記のシャフト系Zoneのz重複により隣接件数が過大に出るケースあり |
| `space_classifier.py` | 用途分類(居間/寝室/キッチン等) | **要データ品質改善、または⑨(LLM)向き。未着手** | 2026-08-07の再同期データではZone名が`キッチン`/`洋室`/`LD`等の実名になっており(旧サンプルの「全件"ゾーン"」という制約は解消)、名前ベースの分類は現実的になった可能性がある(未検証) |
| `evacuation_engine.py`(旧称`evacuation.py`) | 避難経路解析 | 単一階なら着手可 → **実装済み(単一階のみ)** | 外部ドアは「Room/Zoneに1つしか"connects"しないDoor」という簡易ヒューリスティックで判定(`find_exterior_doors()`)、経路長は`graph/path.py`でRoom-Doorグラフ上の最短路として計算。**実データでは上記のシャフト系Zoneのz重複により外部ドアが0件と判定された**(全ドアが2件以上のRoom/Zoneに繋がっていたため)。複数階の避難経路(階段の接続)は実データにStair要素が2件存在する(2026-08-07再同期で新たに確認、旧サンプルでは0件だった)ものの、Stair用の関係ルールが未整備のため引き続き未対応 |
| `accessibility.py` | 動線解析 | **着手可。未着手** | 既存グラフ(Room-Door-Room)のトポロジー解析(行き止まり検出、次数の低いハブ部屋の特定など)で実装できる。既存データのみで完結 |
| (同上、または新規`equipment.py`) | 設備到達性 | **未着手・前提が無い** | 「設備」に該当するAIオブジェクトが実データに無い。Object⇔Roomの関係(点-in-ポリゴンによる「収容」関係で、現行のdistance閾値モデルとは別方式が必要)がRELATION_RULESに無いため、まず関係定義から必要 |
| `code_engine.py`(旧称`building_code.py`) | 法規チェック | 狭く始めるべき、要免責表記 → **実装済み(2項目のみ)** | 採光有効面積比(窓面積/床面積、参考値1/7)とバリアフリー最小ドア幅(参考値0.8m)の2項目のみ実装。窓/ドアの`width`/`height`(`properties.archicad_details`、単位m)とZoneのポリゴン面積(shapely)を使用。**いずれも法的な適合保証ではない参考値である旨をAPIレスポンスの`disclaimer`フィールドに明記**している |

**残る推奨実装順序**: `accessibility.py`(動線解析、データ完備)→`space_classifier.py`(実名Zoneが増えたため再評価)→`building_code.py`の数値ルール追加→設備到達性(Object⇔Room関係の新設が前提のため最後)。いずれも上記のシャフト系Zoneのz重複問題(floorIndexベースのハードフィルタ)を先に解消したほうが結果の信頼性が上がる。

### ⑦ 解析結果ストア

- AI解析結果テーブルを作成
- `engine_analysis_results`/`graph_relation_results`は「直近1回分を全削除→書き込み」方式で履歴を持たない。RAGが時系列の変化(いつ何が変わったか)を参照するには、履歴を積む形のテーブルへの拡張が必要

### ⑧ 埋め込み/インデックス層

- `/bim/index`が手動呼び出しのみで、`sync_from_archicad()`や`rebuild_connections()`の後に自動実行されない。同期後にベクトルインデックスが古いまま放置され得る
- 埋め込み対象が要素単位の短い説明文のみ(`engine/vector_store.py`の`_describe_element()`)。部屋ごとに周辺要素をまとめた文書など、RAGでの検索精度を上げるチャンク戦略は未検討

### ⑨ RAG / AIエージェント層(最大のギャップ、実質未着手)

- LLM SDKへの依存が一切ない(`backend/pyproject.toml`にanthropic/openai等の記載なし)
- `search_elements()`(`engine/vector_store.py`)は類似検索のヒットをそのまま返すのみで、LLMによる自然文回答生成(RAGのGeneration部分)が存在しない
- エージェントのツール呼び出しループ(発話→ツール選択→実行→観察→次の発話)が存在しない。`archicad_mcp/server.py`のMCPツール群(`list_elements`/`search_bim_elements`/`update_element_properties`/`move_archicad_element`等)は「ローカルLLM等が呼べるように」用意されているが、実際に呼ぶエージェント本体が無い
- 会話/セッションの永続化が無い

### ⑩ アクション/操作層

- `move_archicad_element`/`delete_archicad_elements`/`set_archicad_property_value`など、実在の建物データを変更する破壊的操作が既にAPIとして露出しているが、誰が/いつ/何を変更したかの監査ログが一切ない。AIエージェント(⑨)が自律的にこれらを呼べるようになった段階で、監査ログと(必要なら)承認フローの追加が必須

### 横断的関心事

- 認証・認可の実装(現状FastAPIエンドポイントに認証なし、CORSも`http://localhost:5173`固定)
- 可観測性の整備(構造化ログ、Archicad連携の失敗率/レイテンシ、エンジン計算時間などのメトリクス)
