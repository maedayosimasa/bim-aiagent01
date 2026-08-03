# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 応答言語

このリポジトリで作業する際は、日本語で応答すること。

## プロジェクト概要

BIM空間知能エンジン(bim_aiagent) — Archicadと連携し、BIM要素(壁・部屋・ドア・窓など)の空間関係をグラフ化し、意味検索・空間解析を行うAIエージェントを開発するプロジェクト。将来的にはAIによる解析結果を蓄積・活用するエージェント基盤を目指す。

`backend/`と`frontend/`はそれぞれ独立したgitリポジトリ(リポジトリルート自体は無git)。

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

### backend layering

`main.py`(FastAPIルート) → `engine/`(`spatial.py`が解析全体のオーケストレーション、`relation_builder.py`が関係再計算、`vector_store.py`がChromaDB連携) → `graph/`(`builder.py`でNetworkXグラフ生成、`topology.py`でエッジ付与、`relation.py`+`relation_rules.py`で要素タイプ間の隣接/接続判定、`export.py`でJSON変換、`analyzer.py`/`room.py`/`search.py`/`geometry.py`) → `database/db.py`(生sqlite3、SQLAlchemyは未使用。`elements`/`connections`の2テーブルのみ)。

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

現状はSQLite(生sqlite3)+ChromaDBの構成。以下は今後実施予定で、まだ着手していない:

- PostgreSQL(Dockerコンテナ)を構築し、SQLiteから移行
- Raw BIMデータ用テーブルを作成
- 空間知能エンジン用(ノード・エッジ)テーブルを作成
- AI解析結果テーブルを作成
- FastAPIからSQLAlchemyなどのORMを通して保存・取得できるようにする
- Reactで各テーブルの内容を確認できる開発用画面を作成する
