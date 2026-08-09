
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

`main.py`(FastAPIルート) → `engine/`(`spatial.py`が解析全体のオーケストレーション、`relation_builder.py`が関係再計算、`vector_store.py`がChromaDB連携、`site.py`が敷地/道路Zoneの名前検索、`room_engine.py`/`evacuation_engine.py`/`code_engine.py`/`accessibility.py`/`equipment.py`が空間知能エンジンの用途別モジュール、いずれも2026-08-08追加)→ `graph/`(`builder.py`でNetworkXグラフ生成、`topology.py`でエッジ付与、`relation.py`+`relation_rules.py`で要素タイプ間の隣接/接続判定、`envelope.py`(2026-08-08追加、大分類ゾーンの幾何包含判定)、`door_ownership.py`(2026-08-08追加、ドアのowner壁ベースの部屋判定)、`export.py`でJSON変換、`analyzer.py`/`room.py`/`search.py`/`geometry.py`/`path.py`(2026-08-08追加、経路探索)) → `database/db.py`(生sqlite3、SQLAlchemyは未使用。`elements`/`connections`に加え、`engine`/`graph`の計算結果を開発時に検証できるよう保持する`engine_analysis_results`(`analyze_space()`のスナップショット、1行のみ)/`graph_relation_results`(`calculate_relations()`の結果を要素タイプ付きで保持、`connections`とは別の検証専用テーブル)がある。いずれも再計算のたびに全削除→書き込み直す方式で、履歴は積まない。`GET /engine/analysis_snapshot` / `GET /graph/relation_snapshot`で参照できる)。

`relation_rules.py`の`RELATION_RULES`はタプルキー`(type_a, type_b)`で隣接/接続の距離閾値を定義する。Archicad実データは部屋を`"Room"`ではなく`"Zone"`と呼ぶため、`Room`用ルールと`Zone`用ルールを両方定義してある(`archicad_mcp/tapir.py`のモジュールdocstring参照)。

### Archicad連携 (`archicad_mcp/`)

backendはArchicad本体に直接接続しない。必ず「archicad-mcp」(Tapir Add-on経由でArchicadのJSON APIを叩く、Windows PC上で動く別プロセス)をHTTP MCP経由で呼び出す1段構成:

- `client.py`: archicad-mcpへのMCPクライアント。接続先URLは`ARCHICAD_MCP_URL`環境変数、またはランタイムオーバーライド(`POST /archicad/connection`、backend再起動でリセットされる)で決まる。未設定でも例外にせず`/archicad/status`が理由付きで返す設計。
- `server.py`: この backend 自身のMCPサーバー定義(`@mcp_server.tool()`)。デコレータは元の関数をそのまま返すだけなので、`main.py`のREST エンドポイントは同じ関数を直接importして呼んでいる(MCPプロトコルを介さない)。
- `tapir.py`: Tapirコマンドの型付きラッパー。Archicad座標系はメートル、本プロジェクトの座標系はミリ単位なので`_to_mm()`で変換している。

`sync_from_archicad()`は差分マージではなく**全削除してから今回取得分を保存**する方式(開発時のデータ確認用途)。`limit<=0`は全件取得を意味する — 全削除方式と組み合わせているため、中途半端なlimitで打ち切ると以前の要素の大半を失う。

ローカル開発時の注意: WSL上でbackendを動かす場合、Windows側の127.0.0.1はWSLから直接届かない(別ネットワーク名前空間)。WSLのデフォルトゲートウェイIP(`ip route`で確認)を使う必要がある。

### Legal Knowledge Builder連携 (`legal_mcp/`)

建築関連法(建築基準法・建築士法・都市計画法等)の条文検索は、別リポジトリ`~/Legal Knowledge Builder/`(e-Gov法令XML → Knowledge Package(`knowledge/`)を構築するビルドパイプライン。法令改訂時のみ再ビルドされる想定で、本プロジェクトの開発サイクルとは独立)が公開する読み取り専用の検索API(`uv run legal-knowledge-builder serve`、既定ポート8100)を呼ぶ。`archicad_mcp/`と同じ「別プロセス+HTTPクライアント、URLで接続先を切替可能、未設定/未接続でもクラッシュせずステータスを返す」構成を踏襲している(MCPプロトコルではなく素のREST/JSON)。

- `client.py`: Legal Knowledge Builder APIへのHTTPクライアント(`httpx`)。接続先URLは`LEGAL_API_URL`環境変数、またはランタイムオーバーライド(`POST /legal/connection`)で決まる。
- `main.py`の`/legal/*`(`search`/`laws`/`article`/`rules`/`reference`/`graph/neighbors`/`status`/`connection`)は薄いプロキシ(埋め込みモデル等の重い依存はbim-aiagent01側には持ち込まない。埋め込み計算・ChromaDB・条文データ・グラフ(GraphRAG)はすべてLegal Knowledge Builder側で完結する)。`graph/neighbors`はcontainment+引用関係+オントロジーを統合したグラフを起点ノードから多段階に辿る(GraphRAGの多段階検索、2026-08-10追加)。
- frontend「法令検索」タブ(`LegalSearchTab.tsx`)は既存の「意味検索」タブ(`SearchTab.tsx`)とほぼ同じUIパターン。

### frontend

ルーティングライブラリなし、`App.tsx`のローカルstateでタブ切り替え(ダッシュボード/要素同期/意味検索/法令検索/空間関係グラフ/プロパティ編集)。`api/client.ts`が全エンドポイントの型付きfetchラッパーを集約。サーバー状態管理は`@tanstack/react-query`。表形式表示は`ag-grid-react`、空間関係グラフの可視化は`cytoscape`。

### テストのパターン (`backend/tests/`)

`conftest.py`の`api_client`フィクスチャは session scope で共有される`TestClient`(MCPサーバーの`session_manager.run()`はプロセス内で一度しか呼べないため)。Archicad連携のテストは`mcp.client._memory.InMemoryTransport`+`_make_fake_tapir_server()`(`test_tapir.py`)で実サーバーなしに検証する。Legal Knowledge Builder連携のテスト(`test_main_legal_*.py`)は`legal_mcp.client`の関数をmonkeypatchして検証する(実ネットワークに依存しない)。`archicad_client`/`legal_client`ともモジュールレベルのランタイムオーバーライド(`_override_url`)を持つため、`conftest.py`の`autouse`フィクスチャでテスト間リークを防いでいる。

## 制約・方針

- Archicad連携のWindows側ブリッジ(`archicad-mcp\start_http.ps1`)を起動する仕組みはbackend側に作らない。本番はAWS+Tailscale経由で公開される構成のため、リモートでスクリプトを実行できるエンドポイントは設けず、UIには手動起動を促す案内表示のみを置く。

## 今後の開発計画(未着手)

現状はSQLite(生sqlite3)+ChromaDBの構成。[全体レイヤー構成](#全体レイヤー構成概念図)の各レイヤーに対応させて、まだ着手していない項目を挙げる。

### ① データ取得層

- 空間知能エンジンの解析(避難経路解析・法規チェック等)には敷地境界線・道路幅員・道路中心線・方位が必要だが、現状Tapir(archicad-mcp)経由の取得手段には制約がある。2026-08-08時点の対応状況:
  - 敷地境界線・道路: Archicadに専用の要素タイプが無いため、Zoneに用途名(「敷地」「道路」等)を付けて登録する運用を前提に`engine/site.py`で名前検索により暫定対応(`GET /site/boundary`, `GET /site/roads`)。道路幅員・道路中心線はZoneポリゴンの最小外接矩形から幾何的に算出した近似値であり、実測値・設計値そのものではない。**(2026-08-08実データで動作確認済み)** ユーザーが実際に「敷地境界線」「前面道路」という名前のZoneをArchicadへ追加して再同期した結果、`GET /site/boundary`/`GET /site/roads`が正しくヒットすることを確認した(道路幅員は実測と近い6.0mと算出)。
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
- **(2026-08-08対応済み)floorIndexによる階またぎハードフィルタを追加した。** 両要素にfloorIndex(Archicad同期時に必ず記録される、階を表す整数)が揃っている場合、値が異なれば無条件で除外する(z-gapとは別のAND条件)。下記「⑥ 空間知能エンジン」のEV/PS縦シャフト問題の解消策
- **(2026-08-08対応済み)ドアが実際に接する部屋(Room/Zone)の判定を、距離ベースから壁の所属情報ベースに変更した。** 詳細・実データでの効果は下記「⑥ 空間知能エンジン」参照(`graph/door_ownership.py`)

### ⑥ 空間知能エンジン

- 空間知能エンジン用(ノード・エッジ)テーブルを作成
- (2026-08-06時点で対応済み)`analyze_space()`の`issues`は`find_isolated_elements`/`find_degenerate_walls`/`find_ambiguous_door_ownership`(`graph/analyzer.py`)による実質的な検出を返すようになった。`wall_check`/`door_check`もこれらの検出件数を反映する
- (2026-08-08対応済み)`room_engine.py`(隣室解析)/`evacuation_engine.py`(単一階避難経路解析)/`code_engine.py`(採光有効面積比・バリアフリードア幅の参考値チェック)/`graph/path.py`(経路探索)を追加。`GET /engine/rooms` / `GET /engine/evacuation` / `GET /engine/code/daylighting` / `GET /engine/code/accessible_doors`。詳細と実データでの検証結果は下記「追加モジュール案」の表を参照(表の実現性評価は当時のまま残し、実装後の状況を注記で追記した)
- **(2026-08-10追加)`engine/rule_engine.py`(Rule Engine): `code_engine.py`の2チェックをPASS/FAIL/UNKNOWN判定として構造化し、Legal Knowledge Builder(`/rules/by_concept`)から該当法令Rule(law_id/node_id/raw_sentence/modality/confidence)を`legal_sources`として結果に添付する。** `GET /engine/rules/daylighting` / `GET /engine/rules/accessible_doors`(既存の`/engine/code/*`はそのまま残す。フロントエンドはまだどちらも未使用)。判定式自体は変えない(LLMは計算しない。Legal Knowledge Builder側のRuleは正規表現抽出+confidence付きの候補であり、この判定式がそこから自動導出されたことは意味しない。あくまで「関連しそうな条文」の一覧として添付するのみ)。Legal Knowledge Builder未接続時は`legal_sources`が空リストになるだけで判定自体は継続する。実データ(bim_cache.db、実案件+実法令14法令4964ルール)で動作確認済み: ドア101件(pass 53/fail 48)、部屋143件(pass 67/fail 76)、`legal_sources`は概念ごとに8〜11件取得できた(ただしこの正規表現ベースの概念grounding自体にノイズがあり、「バリアフリー」概念に紐づく引用が附則の別法律改正規定を拾う等、必ずしも的確な条文とは限らない。今後の精度向上はLegal Knowledge Builder側のontology/rule_builder次第)。
- **(2026-08-10追加)法規チェックの定義をコード(Python関数)からJSON(`engine/legal_rules.json`)に切り出した(`LegalRule`モデル、`rule_id`/`concept_id`/`applies_when`/`check`/`verification`(comparator+threshold+unit)/`disclaimer`)。** `RULE_CHECK_REGISTRY`(`check`キー→実測値を計算するPython関数、実体は引き続き`code_engine.py`)と組み合わせ、`evaluate_legal_rule()`が汎用的にPASS/FAIL判定を行う。新しい法令チェックを足す場合は(1)`RULE_CHECK_REGISTRY`に計測用のPython関数を登録し、(2)`legal_rules.json`にrule_id/concept_id/verificationを追記するだけでよい(判定式自体をJSONから自動生成する汎用インタプリタではない。測定ロジックはPythonのまま、閾値と法概念の紐付けだけを宣言的データにした)。`GET /engine/legal_rules`(定義一覧)/ `GET /engine/legal_rules/{rule_id}/evaluate`(汎用実行、未登録rule_idは404)を追加。既存の`/engine/rules/daylighting`・`/engine/rules/accessible_doors`は`evaluate_legal_rule_by_id()`の薄いラッパーとして残した(挙動は変えていない。実データで旧実装と同一の結果になることを確認済み)。
- **(2026-08-08実データで新規発見・同日対応済み)EV/PS等の縦シャフト系Zoneは、階をまたいでz範囲が重なる(隙間ではなく重複)ように登録されている**(例: 実データのEVゾーン、1階分`z:400〜3400`・2階分`z:3300〜6300` — 100mm重複)。`calculate_relations()`のz-gap除外(`MAX_Z_GAP_MM=150`)は「隙間」を前提にしており、この重複ケースは原理的に閾値調整では切り分けられない(隙間が負、つまり重なっているため)。**対応**: 両要素のfloorIndexが分かる場合はz-gapとは別に一致を必須条件にするハードフィルタを追加した(`graph/relation.py`、上記「④ 空間関係エンジン」参照)
- **(2026-08-08対応済み・実データで検証・その後テンプレート非依存の判定へ改修)住戸/区画全体を表す大分類ゾーンを実室から除外するようにした。** ユーザー指摘(「部屋名ゾーンの命名で隣接部屋が同じ部屋と誤判定していないか」)を機に実データを詳細検証した結果、当初疑っていた「同名ゾーンの混同」ではなく、**「Cタイプ」のような大分類ゾーン(住戸全体、57.3㎡)が同じ住戸内の実室ゾーン14件(LD・キッチン・トイレ等)を幾何的に100%包含しており、これが実室と同列に扱われていた**ことが根本原因だった(1つのドアが同時に3〜11部屋へ「接続」と誤判定される主因)。
  - **第1版(命名規則ベース、置き換え済み)**: Archicadの「ゾーンカテゴリ」属性(`categoryAttributeId`、名前と位置タブの「カテゴリ」欄)が「大分類(例:住宅)」と番号付きサブタイプ(住宅-1〜6等)の2階層構造で、大分類名(サブタイプ番号なし)のZoneが常に「住戸全体」パターンに一致することを実データで確認し、これを判定根拠にしていた。
  - **第2版(幾何包含ベース、現行)**: 上記の命名規則(大分類/番号付きサブタイプという構成)はArchicadのテンプレートによって書き方が様々でありうるとの指摘を受け、**判定根拠をテンプレートに依存しない幾何包含のみに変更した**(`graph/envelope.py`の`find_envelope_zone_guids()`)。「同一階の他Zoneを`MIN_CONTAINED_COUNT`(=3)件以上、`CONTAINMENT_AREA_RATIO`(=0.8)以上の面積割合で、かつ自身が`MIN_SIZE_RATIO`(=1.3)倍以上大きい面積で包含している」Zoneを大分類ゾーンとみなす。ゾーンカテゴリ名(`properties.zone_category`、`archicad_mcp/tapir.py`の`get_zone_categories()`で解決)は表示用の付随情報として保存するのみで、除外判定には使わない。`calculate_relations()`(`graph/relation.py`)と`find_rooms()`(`graph/room.py`)がこの幾何判定を使う。
  - **実データでの比較検証**(5708要素、再同期): 命名規則ベース(第1版)は35件のZoneを除外し関係件数4847→3344件・外部ドア0→4件・避難経路が届かない部屋157/157→17/122(86%到達可能)。幾何包含ベース(第2版)は14件のみ除外し関係件数4847→3673件・外部ドア0→3件・避難経路が届かない部屋157/157→27/143(81%到達可能)。**幾何判定の方がやや保守的(一部の大分類ゾーンを見逃す)だが、テンプレート非依存という安全性を優先しこの閾値のまま採用した**(閾値の調整は複数の実物件データで比較しないと判断できないため、今回は据え置き。将来別のArchicadプロジェクトで再検証する際の対応方針として記録)。
- **(2026-08-08対応済み・実データで検証)ドアが実際に接する部屋(Room/Zone)の判定を、距離ベースから壁の所属情報ベースに変更した。** 大分類ゾーン除外後も、Room/Zone-Doorの"connects"(距離閾値700mm)は密集した実データでは1つのドアが同時に3〜11件の部屋へ「接続」と誤判定される問題が残っていた——壁の場合(所属壁は常に距離0mmで一意に決まる)と異なり、部屋の場合は距離を0.5mmまで絞っても3〜6件残ることを実データで確認済み(距離ベースでは原理的に解決不可能)。
  - Door/Windowの`properties.archicad_details.ownerElementId`(所属壁のGUID、Archicad側の確定情報で幾何推測ではない)を起点に、その壁の両側(厚み方向)にプローブ点を置き、実際にどのRoom/Zoneポリゴンに含まれるか(`polygon.contains(point)`)で判定する方式に変更した(`graph/door_ownership.py`の`find_door_room_guids()`、`graph/relation.py`の`_refine_door_room_connections()`から呼ばれる)。owner壁が特定できないドア(実データでは稀)は従来の距離ベース判定にフォールバックする。
  - **実装中に新たなバグを作り込み、同日中に発見・修正した**: この点-in-ポリゴン判定はfloorIndexを考慮していなかったため、平面(x,y)上で同じ座標に重なる別階のZone(EV/PS等の縦シャフト、実データで"MB""共用廊下"等の同名Zoneが複数階に重複)まで誤ってヒットしていた。ドアと同じfloorIndexのRoom/Zoneのみを候補にするよう修正(`room_records_by_floor`)。
  - **実データでの効果**(5708要素、floorIndexフィルタ適用後の最終値): ドアの接続数分布が3〜40件という無秩序な状態から、**1件(31件)・2件(56件)のみ**という建築的に正しいパターン(内部ドア=2部屋、外部ドア=1部屋)に収束した。避難経路が届かない部屋は143室中87室(接続0件の76室の内訳はPS/MB(配管・メーター)/バルコニー(引き戸でWindow扱いの可能性)/屋外階段/EV/前面道路が大半を占め、ドアが存在しないのが自然な場所が中心で、RAG等による用途分類が無い現状では妥当な結果と判断)。
- **(2026-08-08対応済み・実データで検証)Wall-Door/Wall-Windowの"adjacent"にも同じowner壁ベースの絞り込みを適用した。** ユーザーが「解析結果DB」タブの空間関係グラフ一覧表を実際に見て、壁-ドア間の距離が300〜470mm前後とバラつく行が多数あることを指摘したのがきっかけ。実データで具体的な組を検証したところ、その壁が実際にはそのドアの所属壁ではなく(所属壁は別にあり距離0.0mmで存在)、単に600mm閾値内にたまたま入っていただけの無関係な壁(角で交わる隣の壁等)だった。`graph/door_ownership.py`に`find_owner_wall_guid()`を追加(Door/Window共通、`_door_owner_wall()`のpublicラッパー)し、`graph/relation.py`の`_refine_wall_opening_adjacency()`で、owner壁が特定できるDoor/Windowについては、owner壁以外との"adjacent"を`calculate_relations()`の結果から除外する(owner壁が特定できない場合は既存の距離ベース判定にフォールバック)。
  - **実データでの効果**(5708要素): 関係総数2867→**945件**に減少。Wall-Door"adjacent"は101件のドア全てで**所属壁1枚のみ・距離0.0mm**に完全収束(以前は0〜472.7mmに散らばっていた)。Wall-Window"adjacent"も110件全て同様。空間関係グラフ・関係一覧テーブルには、実際に一致する(所属する)組み合わせのみが表示されるようになった

#### 追加モジュール案(2026-08-06 実現可否を調査、2026-08-08 実装)

`engine/`配下に機能別モジュールを追加する案。実データ(`bim_cache.db`、当時は4階建てサンプル、2026-08-07に5699要素の実物件へ再同期)で必要なプロパティの有無を確認した結果を含む。

| モジュール | 機能 | 実現性(2026-08-06時点の調査) | 前提条件・注意点 |
|---|---|---|---|
| `room_engine.py`(旧称`room_analyzer.py`) | 隣室解析 | 即着手可 → **実装済み** | `calculate_relations()`が既に計算するRoom/Zone-Room/Zoneの「adjacent」・Room/Zone-Doorの「connects」をそのまま使う。2026-08-08に幾何包含による大分類ゾーン除外・floorIndexハードフィルタ・ドアのowner壁ベース接続判定(いずれも上記参照)を追加し実データで大幅改善 |
| `space_classifier.py` | 用途分類(居間/寝室/キッチン等) | **要データ品質改善、または⑨(LLM)向き。未着手** | 2026-08-07の再同期データではZone名が`キッチン`/`洋室`/`LD`等の実名になっており(旧サンプルの「全件"ゾーン"」という制約は解消)、名前ベースの分類は現実的になった可能性がある(未検証) |
| `evacuation_engine.py`(旧称`evacuation.py`) | 避難経路解析 | 単一階なら着手可 → **実装済み(単一階のみ)** | 外部ドアは「Room/Zoneに1つしか"connects"しないDoor」という簡易ヒューリスティックで判定(`find_exterior_doors()`)、経路長は`graph/path.py`でRoom-Doorグラフ上の最短路として計算。**2026-08-08の一連の対応(大分類ゾーン除外→floorIndexハードフィルタ→ドアのowner壁ベース接続判定)を経て、外部ドア31件・避難経路が届く部屋143件中56件に到達**(実データ5708要素で確認。届かない87件のうち大半はPS/MB/バルコニー等ドアが無いのが自然な場所)。複数階の避難経路(階段の接続)は実データにStair要素が2件存在するものの、Tapirの`GetDetailsOfElements`がStair型も"Not yet supported element type"を返し詳細取得不可(Line/PolyLine/Textと同種の制約)なため引き続き未対応 |
| `accessibility.py` | 動線解析 | 着手可 → **実装済み** | 既存グラフ(Room-Door-Room、`graph/path.py`の`walkable_subgraph()`)のトポロジー解析。次数1を行き止まり、次数`HUB_DEGREE_THRESHOLD`(=4)以上をハブ部屋として報告する(`GET /engine/accessibility`)。実データで検証したところ、ハブ部屋の上位が「風除室」「玄関」「管理人室」「廊下」「ホール」となり、建築的に妥当な結果が得られた |
| `equipment.py` | 設備到達性 | 未着手・要データ品質検証 → **実装済み** | Object⇔Roomの関係を点-in-ポリゴン(Objectのバウンディングボックス重心とRoom/Zoneポリゴンの`contains()`判定、`graph/door_ownership.py`と同じ考え方)で実装(`GET /engine/equipment`)。`EQUIPMENT_KEYWORDS`(`libPart.name`のキーワード一致、家具・空調・水回り設備)で対象を絞り、floorIndex一致・大分類ゾーン除外も他モジュールと同様に適用。**着手前に「幅26m相当の異常な換気扇オブジェクト」への懸念を記録していたが、再検証したところ再現せず(再同期でデータが変わったため、または一時的な観測ミス)、実データでは家具/設備オブジェクトはすべて妥当なサイズ(数百mm〜2.5m程度)だった。**実データ(5708要素)で家具/設備161件全てがいずれかの部屋に収容判定され(unplaced 0件)、UBにUB1216・洗面所に洗面化粧台+換気扇・LDにソファ/テーブル/家電一式など、建築的に妥当な分布が得られた |
| `code_engine.py`(旧称`building_code.py`) | 法規チェック | 狭く始めるべき、要免責表記 → **実装済み(2項目のみ)** | 採光有効面積比(窓面積/床面積、参考値1/7)とバリアフリー最小ドア幅(参考値0.8m)の2項目のみ実装。窓/ドアの`width`/`height`(`properties.archicad_details`、単位m)とZoneのポリゴン面積(shapely)を使用。**いずれも法的な適合保証ではない参考値である旨をAPIレスポンスの`disclaimer`フィールドに明記**している |

**残る推奨実装順序**: `space_classifier.py`(実名Zoneが増えたため再評価)→`building_code.py`の数値ルール追加。CLAUDE.md「⑥ 空間知能エンジン」の追加モジュール案は`equipment.py`まで全て実装済みとなった。

### ⑦ 解析結果ストア

- AI解析結果テーブルを作成
- `engine_analysis_results`/`graph_relation_results`は「直近1回分を全削除→書き込み」方式で履歴を持たない。RAGが時系列の変化(いつ何が変わったか)を参照するには、履歴を積む形のテーブルへの拡張が必要

### ⑧ 埋め込み/インデックス層

- `/bim/index`が手動呼び出しのみで、`sync_from_archicad()`や`rebuild_connections()`の後に自動実行されない。同期後にベクトルインデックスが古いまま放置され得る
- 埋め込み対象が要素単位の短い説明文のみ(`engine/vector_store.py`の`_describe_element()`)。部屋ごとに周辺要素をまとめた文書など、RAGでの検索精度を上げるチャンク戦略は未検討
- **(2026-08-08発見・同日修正)フロントエンド「要素同期」タブの「検索インデックス化」ボタン(`POST /bim/index`)が実データで500エラーになるバグがあった。** `index_elements()`(`engine/vector_store.py`)がChromaDBの`collection.upsert()`を全要素一括(5708件)で呼んでいたが、ChromaDBには1回のupsertで送れる件数に上限があり(実測5461件)、これを超えると`chromadb.errors.InternalError`("Batch size of N is greater than max batch size of M")になっていた。ユーザーが敷地/道路Zoneを追加して要素数が上限を超えたことで、このセッション中に初めて顕在化した。`_UPSERT_BATCH_SIZE = 1000`で固定サイズに分割して複数回に分けてupsertするよう修正し、実データ(5708要素)で全件インデックス化・意味検索(`/bim/search`)の動作を確認済み

### ⑨ RAG / AIエージェント層(最大のギャップ、実質未着手)

- LLM SDKへの依存が一切ない(`backend/pyproject.toml`にanthropic/openai等の記載なし)
- `search_elements()`(`engine/vector_store.py`)は類似検索のヒットをそのまま返すのみで、LLMによる自然文回答生成(RAGのGeneration部分)が存在しない
- エージェントのツール呼び出しループ(発話→ツール選択→実行→観察→次の発話)が存在しない。`archicad_mcp/server.py`のMCPツール群(`list_elements`/`search_bim_elements`/`update_element_properties`/`move_archicad_element`等)は「ローカルLLM等が呼べるように」用意されているが、実際に呼ぶエージェント本体が無い
- 会話/セッションの永続化が無い
- **(2026-08-09追加)** 建築関連法の条文検索(`/legal/search`等、上記「Legal Knowledge Builder連携」参照)は先に用意できた。将来エージェントループを実装する際、「法規チェック」ツールの土台として使える状態にはなっているが、これ自体はエージェント本体でもLLM呼び出しでもない(素朴なベクトル検索の結果を返すだけ)。

### ⑩ アクション/操作層

- `move_archicad_element`/`delete_archicad_elements`/`set_archicad_property_value`など、実在の建物データを変更する破壊的操作が既にAPIとして露出しているが、誰が/いつ/何を変更したかの監査ログが一切ない。AIエージェント(⑨)が自律的にこれらを呼べるようになった段階で、監査ログと(必要なら)承認フローの追加が必須

### 横断的関心事

- 認証・認可の実装(現状FastAPIエンドポイントに認証なし、CORSも`http://localhost:5173`固定)
- 可観測性の整備(構造化ログ、Archicad連携の失敗率/レイテンシ、エンジン計算時間などのメトリクス)
