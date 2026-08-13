
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
| ⑨ | RAG / AIエージェント層 | △ | `agent/`(2026-08-11追加、LangGraph + Claude API)。会話の永続化・ツール呼び出しループはあるが、書き込み系操作は未対応(下記参照) |
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
- **(2026-08-13追加)`local_process.py`: Legal Knowledge Builderをbackendと同じホスト上でサブプロセスとしてローカル起動する補助機能。** Archicad連携のWindows側ブリッジ(下記「制約・方針」参照、backendとは別のリモートPC上で動くため意図的に起動する仕組みを作っていない)とは前提が異なり、Legal Knowledge Builderは`LOCAL_PRESET_URL`(127.0.0.1:8100)が示す通りbackendプロセスと同じホスト上で動かす運用(開発時は同じWSL環境)が前提のため、backendプロセスからのサブプロセス起動として実装できる(別ホストへのリモートコード実行ではない)。起動コマンドは`uv run legal-knowledge-builder serve`固定でユーザー入力を含まない。リポジトリの場所は既定`~/Legal Knowledge Builder/`、環境変数`LEGAL_KNOWLEDGE_BUILDER_DIR`で上書き可能。`POST /legal/start_server`(起動、既に起動中なら`already_running: true`を返すだけで二重起動しない。`LEGAL_API_URL`未設定時は起動と同時に`LOCAL_PRESET_URL`へ接続先も合わせる)・`GET /legal/start_server/status`を追加し、frontend「ダッシュボード」タブに`LegalKnowledgeBuilderLauncher.tsx`(起動ボタン+接続完了までのポーリング表示、初回は埋め込みモデルのダウンロードで数十秒〜数分かかることがある旨を案内)を追加した。本番(AWS+Tailscale経由でbackendが動く構成)にリポジトリが無い場合は`start_server()`が`LocalServerNotFoundError`を送出し、この機能はローカル開発専用であることを明示する。実データで動作確認済み(起動→3秒後に`reachable: true`、二重起動防止も確認)。
- `main.py`の`/legal/*`(`search`/`laws`/`article`/`rules`/`reference`/`graph/neighbors`/`status`/`connection`)は薄いプロキシ(埋め込みモデル等の重い依存はbim-aiagent01側には持ち込まない。埋め込み計算・ChromaDB・条文データ・グラフ(GraphRAG)はすべてLegal Knowledge Builder側で完結する)。`graph/neighbors`はcontainment+引用関係+オントロジーを統合したグラフを起点ノードから多段階に辿る(GraphRAGの多段階検索、2026-08-10追加)。
- frontend「法令検索」タブ(`LegalSearchTab.tsx`)は既存の「意味検索」タブ(`SearchTab.tsx`)とほぼ同じUIパターン。

### AIエージェント層 (`agent/`, 2026-08-11追加)

CLAUDE.md「⑨ RAG / AIエージェント層」の発話→ツール選択→実行→観察→次の発話ループを実装する。オーケストレーションはLangGraph(`langchain.agents.create_agent`、LangGraph 1.x移行後のprebuilt ReActエージェント。旧`langgraph.prebuilt.create_react_agent`はdeprecated)、LLMはAnthropic Claude(`langchain-anthropic`)。ベクトルストアは既存のChromaDB(`engine/vector_store.py`)をそのまま利用し、新規ミドルウェア(Qdrant/pgvector等)は導入していない。

- `tools.py`: `engine/*`・`database/db.py`・`legal_mcp/client.py`を直接呼ぶ薄いラッパー(`@tool`デコレータ)。**意図的に読み取り専用・解析系のみ**を公開し、`move_archicad_element`/`delete_archicad_elements`/`set_archicad_property_value`等の書き込み系ツールとフルキャッシュ削除を伴う`sync_from_archicad`は含めていない——CLAUDE.md「⑩ アクション/操作層」に記載の通り監査ログが未整備なため、AIエージェントに書き込み権限を与えるのは監査ログ整備後に回した(意図的な安全側の設計判断)。
  - **(2026-08-11実データで発見・同日修正)実際にANTHROPIC_API_KEYを設定して動かしたところ、実データ(5708要素)で`invalid_request_error: prompt is too long: 2190419 tokens > 1000000 maximum`が発生した。** 原因は`list_bim_elements_tool`が全要素をproperties/geometry込みで無制限にJSON化して返しており、実測で約224万トークン(Claude Opus 5のコンテキスト上限100万トークンの2倍超)あったこと。同様に`get_engine_analysis_snapshot_tool`/`analyze_bim_space_tool`もグラフの生データ(座標付きノード/エッジ、フロントエンドのグラフ可視化専用でLLMには不要)を含み約53万トークン、`get_graph_relation_snapshot_tool`も関係一覧の全件ダンプで約13万トークンあった。**対応**: 一覧/スナップショット系ツールを「絞り込み条件を省略すると件数サマリのみを返し、指定時も返却件数を上限(`_ELEMENT_LIST_LIMIT=200`/`_RELATION_LIST_LIMIT=100`)でクランプする」方針に統一。`analyze_bim_space_tool`/`get_engine_analysis_snapshot_tool`は`graph_data`を常に除外し`issues`を上位20件+総件数に絞る(`_drop_graph_data_and_cap_issues()`)。個別要素の詳細は新設の`get_bim_element_tool(guid)`で1件ずつ取得する方式に変更した。`search_bim_elements_tool`のn_resultsにも上限(50)を追加(LLMが過大な値を指定してもクランプされる)。修正後は実データで`list_bim_elements_tool()`が約400トークン、`analyze_bim_space_tool()`が約2200トークンまで縮小し、実際にAPIキーで動作確認済み。回帰テストは`tests/test_agent_tools.py`。
- `graph.py`: `create_agent(model, tools=..., system_prompt=..., checkpointer=...)`の薄いラッパー。会話ループの中でLLMが毎回どのツールを呼ぶか判断する、素直なReActエージェント。`build_agent()`の`tools`引数は既定で全ツール(`AGENT_TOOLS`)だが、下記`router.py`が絞り込んだ部分集合を渡すこともある。
  - **(2026-08-13追加、Capability Routingパターンの実質的解決)** ユーザー提示のCapability Routing案(発話の意図を分類し必須のツール群(BIM_CONTEXT/SPATIAL_ANALYSIS/LEGAL_KNOWLEDGE/RULE_EVALUATION等)を強制する)を検討した結果、専用の構造化Intent分類ステップ(追加LLM呼び出し)を新設するのではなく、この懸念が実害を持つ範囲を特定した上でSYSTEM_PROMPTの指示強化で対応した。**調査の結果、「建蔽率に適合していますか」のような法規適合性の質問は、`report_graph.py`(`/agent/legal_report`)の決定的パイプラインでは`run_checks`が登録済み全Ruleを無条件に評価するためLLMのツール選択判断が介在せず、そもそもこのリスクの対象外であることを確認した。** リスクが実際に残るのは自由対話(`/agent/chat`のReActループ)経路のみで、そこではLLMが`engine_legal_rules_list_tool`→`engine_legal_rules_evaluate_tool`を自分で連鎖させる必要があり呼び忘れが起こりうる。SYSTEM_PROMPTに「法規適合性を問われたら、まずengine_legal_rules_list_toolで全rule_idを確認し、関連する全rule_idを評価してから答える(一部だけで判断しない)」「BIMデータの検索・空間解析だけ、または法令検索の結果だけで適合性を判断しない」を追加した。構造化Intent分類(LLM呼び出し追加)は`router.py`の既存判断(キーワードヒューリスティック、追加LLM呼び出し無し)と衝突するため見送った。
  - **(2026-08-13追加)条文番号の一般知識補完を禁止する指示を追加した。** `report_graph.py`の同種の追加(Evidence Validation、下記参照)と対になるもので、ReActチャットのSYSTEM_PROMPTにも「具体的な条番号はツール結果(legal_search_tool等)に実際に含まれるものだけを使い、ツール結果に無い条番号を一般知識で補って挙げてはいけない」を追加した(こちらはClaim Validatorのような機械的な事後検証は無く、prompt上の指示のみ——ReActチャットの応答は`agent/service.py`の`_finish_turn()`を経由するがreport_graph.pyのような検証ノードは持たないため)。
- `router.py`(2026-08-13追加、Router): ユーザー提示のエージェント設計パターン(Router→並列{Spatial/Legal/BIM}→Evidence Layer→...)のうち、Routerに対応する部分。ユーザーが選んだ判定方式(キーワード/正規表現ヒューリスティック、追加のLLM呼び出し無し)に基づき、発話本文のキーワードから関連しそうなツール集合(`BIM_TOOLS`14件/`LEGAL_TOOLS`9件、合計で`AGENT_TOOLS`23件と一致)を絞り込む`route_tools(message)`を実装した。**現状のツール数・ドメイン数(2つ)の規模ではドメインごとに別々のStateGraphノード/サブグラフへ分岐させる構成は過剰と判断し、「絞り込んだツール集合を束ねたReActエージェントを1つ呼ぶ」という薄い実装にしている**(複数サブグラフ間の状態受け渡しという複雑さを持ち込まずに、ツール選択の精度・プロンプト肥大化の抑制という実質的な効果は同じで得られる)。どちらのキーワードにも一致しない場合・両方に一致する場合は安全側(全ツール)にフォールバックする(過検出よりも見逃しの方が実害が大きいため、ツールを誤って除外するくらいなら多めに残す)。**既知の限界**: 純粋なキーワードマッチングのため、ドメインを示す語を含まない婉曲な質問(例:「これで大丈夫?」)が特定ドメインに偏った文脈の直後に来ても文脈は見ない・単発の発話のみで判定する。回帰テストは`tests/test_router.py`。
  - `service.py`の`run_chat()`のみに適用する(`resume_chat()`には適用しない——LangGraphの`interrupt()`で一時停止した会話は必ず全ツールセットのエージェント(`_get_agent()`)で再開する。一時停止した特定のツール呼び出しが再開先のToolNodeに確実に存在することを保証するため)。`_get_routed_agent(tools)`はツール名の組み合わせごとにエージェントをキャッシュし、全ツールセットと一致する場合は`_get_agent()`のシングルトンをそのまま再利用する(resume_chat()と同じcheckpointerを共有し、無駄な二重構築を避ける)。**実際に絞り込み済みエージェント(routed)と全ツールエージェント(resume用)という異なるグラフオブジェクト間でinterrupt/resumeが正しく機能するかを回帰テストで確認済み**(`test_agent.py`の`test_run_chat_interrupts_on_missing_legal_inputs_then_resumes`が絞り込み後のエージェントで一時停止し、全ツールエージェントで再開する経路をそのまま検証している)——LangGraphのcheckpoint再開はグラフ構造(ノード名)の互換性のみに依存し、Python オブジェクトの同一性には依存しないため(checkpointerさえ共有していれば、backendプロセス再起動を挟んだ再開(Missing Input Interrupt/Resume)と同じ原理)。
- `report_graph.py`(2026-08-11追加): **複数ステップの解析フロー(法規チェック→引用条文添付→レポート生成)**を、`graph.py`のReActループとは別に、LangGraphの`StateGraph`で明示的に組んだもの。ノードは`run_checks`(登録済み全`LegalRule`(`engine/legal_rules.json`)を`evaluate_legal_rule()`でPASS/FAIL/UNKNOWN判定する。引用条文の添付(`legal_sources`)も含め、LLMは一切呼ばない決定的な計算)→`generate_report`(判定結果の要約テキストをClaudeに渡し、日本語レポート文を生成する。LLMには数値の再計算・判定の上書きをさせず要約のみ担当させる)→`verify_report`の固定順。`message_utils.py`にAIMessageからテキストを取り出す共通ヘルパーを切り出し、`service.py`と共有する。
  - **(2026-08-13追加、Claim Validatorパターン)`verify_report`ノード: LLMが書いたレポート文中の「PASS n件/FAIL n件/UNKNOWN n件」表記を、ステップ1で確定した実際の件数と機械的に突き合わせる。** ユーザーが提示したエージェント設計パターン(Router→並列Evidence収集→Rule Engine→Claim Validator)を検討した結果、既存の「LLMには数値の再計算・判定の上書きをさせない」という指示(prompt上の制約)だけでは、文章化の過程で件数を書き間違える(ハルシネーション)リスクそのものは防げないと判断し、最初の適用対象としてこのグラフに追加した(4段階の改善案のうち1番目、他は今後着手)。不一致があれば`generate_report`に1回だけ差し戻して訂正版を再生成させ(`_MAX_GENERATION_ATTEMPTS=2`)、それでも一致しなければ`append_verification_warning`ノードでレポート本文に`⚠️ 自動検証の警告`ブロックを付記して確定する(静かに誤った文章のまま返さない)。件数の抽出は正規表現ベースのbest-effortで、想定と異なる書式で抽出できなかった場合は「検証不能」として扱い不一致とはみなさない(過検出よりも見逃しを許容する設計——`missing_inputs`のチェックはそもそもPASS/FAIL文を書かないためこの検証の対象外)。回帰テストは`tests/test_agent.py`(`_validate_report_claims`の単体テスト3件、再生成で一致/一致せず警告付記の2件)。
  - **(2026-08-13追加、Evidence Validation)`verify_report`に条文引用の検証(`_validate_citations`)を追加した。** きっかけはユーザーが4段階改善案をさらに深掘りし、「PASS/FAIL件数だけでなく、レポートが引用した条番号が実際に取得したlegal_sourcesに存在するか」も検証すべきと提案したこと。調査の結果、**当時の`legal_sources`にはlaw_id(e-Govの数値ID、例:"325AC0000000201")とraw_sentence(条文本文、条番号を含まない)しか無く、人間可読な条番号がどこにも存在しなかった**——つまりLLMが「建築基準法第28条」のような具体的な条文引用をレポートに書いても、それは取得した根拠に基づくものではなく一般知識由来である可能性が高いという実際のギャップを発見した。対応として`engine/rule_engine.py`に`_extract_article_label()`(node_id、例:"325AC0000000201:Law:MP#1:Ch2:Art28:Para1"から"第28条"を正規表現抽出、附則(Suppl#)は"附則第◯条"、"Art1_3"のような枝番は"第1条の3"に変換)と`_law_titles()`(`legal_client.list_laws()`でlaw_id→法令名の一覧を取得)を追加し、`legal_sources`各要素に`law_title`/`article`フィールドを追加した。**実データ(ローカルのLegal Knowledge Builder)で動作確認済み**: "建築基準法 第28条"・"建築基準法施行令 第20条"のように正しく解決されることを確認。`report_graph.py`の`_summarize_for_prompt()`もLLMへ渡す表示を`law_id`から`law_title`+`article`(例:"建築基準法第28条")に変更し、REPORT_SYSTEM_PROMPT/SYSTEM_PROMPT(agent/graph.py)双方に「条番号は与えられたデータに実在するものだけを使い、一般知識で補ってはいけない」という指示を追加した。`verify_report`の`_validate_citations()`はレポート本文の「第◯条」表記を全チェックのlegal_sourcesの和集合と突き合わせ、存在しない条番号への言及があれば`_validate_report_claims`と同じ再生成→警告付記のフローに合流させる(チェック単位の位置合わせはせず全体の和集合で照合、過検出よりも見逃しを許容)。回帰テストは`tests/test_agent.py`(`_validate_citations`の単体テスト3件、ハルシネーション引用で警告付記される統合テスト1件)、`tests/test_rule_engine.py`(`_extract_article_label`の単体テスト、`_law_titles`失敗時のフォールバック)。
- `service.py`: セッション管理。`ANTHROPIC_API_KEY`未設定でもimport時にはクラッシュせず、実際に呼び出す時点で`AgentNotConfiguredError`を送出する(`archicad_mcp/client.py`/`legal_mcp/client.py`と同じ「未設定でもクラッシュしない」方針)。会話(`run_chat`)の履歴はLangGraphのcheckpointer(`AsyncSqliteSaver`、`agent_checkpoints.db`。`bim_cache.db`とはスキーマが無関係のため別ファイル)に`session_id`(LangGraphの`thread_id`)単位で永続化される——CLAUDE.md「⑨」に挙げていた「会話/セッションの永続化が無い」というギャップのうち会話ターン単位の永続化はこれで解消した(解析結果側の履歴化(⑦)は別課題として残る)。`run_legal_report`(法規レポート)はsession_id/checkpointerを使わない単発実行。
  - **(2026-08-11発見・同日修正)一度でも巨大なツール結果が会話履歴に書き込まれてしまうと、ツール自体を直しても該当セッションは以後ずっと`prompt is too long`エラーを返し続けることが実データで発覚した。** LangGraphは会話ターンごとに全メッセージ履歴をcheckpointerへ蓄積し、次のターンでも毎回その全履歴をLLMへ送るため、上記の一覧ツール修正後もそのセッション自体は自然には回復しない(実際、この事故の直後にユーザーが同じセッションIDのまま送信を続け、毎回コンテキスト上限エラーで失敗する状態を引き起こした)。`_ainvoke_with_context_guard()`が`anthropic.BadRequestError`の`"prompt is too long"`を検知し、専用の`ConversationTooLongError`(「新しい会話を開始してください」という具体的な次の行動を示す)に変換して送出するようにした。`main.py`はこれをHTTP 413で返す(汎用の502とは区別)。
- `main.py`の`/agent/status`(設定状態確認)・`/agent/chat`(`{session_id, message}` → `{response, tool_calls}`)・`/agent/history/{session_id}`・`/agent/legal_report`(POST、複数ステップグラフの実行)。モデルは`ANTHROPIC_AGENT_MODEL`環境変数(既定`claude-opus-5`)。
- frontend「AIエージェント」タブ(`AgentChatTab.tsx`、2026-08-11追加)は`/agent/chat`とのチャットUI。会話セッションIDは`localStorage`に保持しタブを閉じても続きから会話できる。「法規レポート」タブ(`LegalReportTab.tsx`、2026-08-11追加)はボタン一つで`/agent/legal_report`を実行し、レポート文+チェック項目別の詳細表(PASS/FAIL/UNKNOWN・実測値・関連法令根拠)を表示する。
  - **(2026-08-11発見・同日修正)「考え中...」が数分経っても終わらず無反応に見える不具合があった。** 原因は2つ複合していた: (1) backend自体は上記の`prompt is too long`エラーを約19秒で返していたが、backendプロセスの再起動(uvicorn `--reload`によるコード反映等)のタイミングでリクエストが宙に浮くと、ブラウザの`fetch()`にはデフォルトのタイムアウトが無く無期限に待ち続けてしまう。(2) 「考え中...」表示に経過時間が無く、実際に待っているのか完全に停止しているのか区別できなかった。**対応**: `api/client.ts`の`request()`に`AbortController`ベースのタイムアウト(エージェント関連呼び出しは3分)を追加し、タイムアウト時は専用のエラーメッセージを出す。エラーレスポンスの`{"detail": "..."}`から人間可読なメッセージだけを取り出して表示するよう共通化。`AgentChatTab.tsx`に経過秒数表示(`useElapsedSeconds`フック、1秒おきに更新)を追加し、30秒を超えたら「応答に時間がかかっています」の案内も出す。
- テスト(`tests/test_agent.py`)は実際のAnthropic APIを呼ばず、`langchain_core.language_models.BaseChatModel`を実装した固定応答のフェイクモデルに差し替えて検証する(`test_tapir.py`のフェイクサーバーと同じ考え方)。`prompt is too long`エラーからの`ConversationTooLongError`変換も、`anthropic.BadRequestError`を送出するフェイクモデルで回帰テストしている。
- **(2026-08-13追加)トークン使用量・料金の記録(`agent/pricing.py`、`database/db.py`の`token_usage`テーブル)。** `run_chat`/`run_legal_report`がClaude APIを呼び出すたびに、LangChainの`AIMessage.usage_metadata`(input_tokens/output_tokens)を実測値として`token_usage`テーブルに1行追加する(他のスナップショット系テーブルと異なり全削除しない、履歴を積む方式)。`run_chat`はReActループ内でツール呼び出しを挟み複数回LLMを呼ぶことがあるため、`_extract_turn()`が今回のターンに含まれる全AIMessageのusage_metadataを合算してから1行として記録する。`run_legal_report`(session_id無し)は`report_graph.py`の`LegalReportState`に`usage`フィールドを追加し、`generate_report`ノードからservice.py側へ伝播させている。料金はAnthropic公式の1Mトークンあたり単価表(`agent/pricing.py`、claude-opus-5は$5/$25)から算出し、料金表に無いモデルの場合は`cost_usd`をNULL(不明)にする(判定式自体は変えず、実測トークン数は常に記録する)。`GET /agent/usage/daily`(UTC日付ごとの集計)・`GET /agent/usage/jobs`(「作業」ごとの集計——chatはsession_id単位、legal_reportはsession_idが無いため実行(行)ごとに個別の作業として扱う)を追加し、frontend「利用状況」タブ(`UsageTab.tsx`)で表示する。実データで動作確認済み(実際に1回チャットを送信し、入力5118トークン・出力64トークン・$0.02719が正しく記録・集計されることを確認)。

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
  - **(2026-08-13追加)道路をZoneではなくMeshでモデリングする運用にも対応した。** Tapirの`common_schema_definitions.js`(Windows側実ソース)を確認したところ、MeshDetails(`level`/`skirtType`/`skirtLevel`/`polygonCoordinates`(3D)/`polygonArcs`/`holes`/`sublines`)は実footprintを持つがZoneと違って`name`フィールドが無い。そのため`archicad_mcp/tapir.py`の`details_to_geometry()`にMesh分岐を追加し実境界(`polygonCoordinates`のx,y)を使うようにした一方、`details_to_name()`は引き続き合成名(`Mesh_<guid>`)しか返せない。
  - **(2026-08-13同日追加、実データで確認)Meshの実用上の識別子は、当初想定していたレイヤー名ではなく、Archicad UIの「分類とプロパティ」パネル→「IDとカテゴリ」グループ→「ID」欄だった(ユーザーが実際にArchicad上でMeshを選択しスクリーンショットで確認)。** この「ID」は`GetDetailsOfElements`のレスポンスで型に依存する`details`とは別に、型に依らず返る共通の必須フィールド`"id"`(`command_definitions.js`のGetDetailsOfElements outputSchemeでrequired指定)であることをTapirスキーマと実データ(`{"type": "Mesh", "id": "塗膜防水", ...}`)の両方で確認した。**このフィールドは以前から取得できていたにもかかわらず、`sync_from_archicad()`は一切読んでいなかった。** `properties.archicad_id`として保存するよう追加し、`engine/site.py`の`find_zones_by_name()`はMesh型に限り、`element["name"]`の代わりに`properties.archicad_id`(主)→`properties.layer_name`(フォールバック)の順でキーワード照合するようにした。
  - **実データでの動作確認(5706要素、再同期後)**: ユーザーが実際にMeshの「ID」欄に「前面道路」「道路」と設定した2件が`GET /site/roads`で正しく検出され(いずれも`estimated_width_m: 6.0`)、「周辺敷地」と設定した1件が`GET /site/boundary`で検出された。Zone/Roomの照合ロジック(name一致)は変更していない。
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
- **(2026-08-11追加)`engine/window_classifier.py`: 窓(Window)を外部窓/内部窓に分類する。** きっかけはAIエージェント(⑨)への「外壁の窓の数を教えて」という質問——Archicadの窓要素には外部/内部を示す属性が無く、エージェントはget_bim_element_toolで個別窓を何件も調べた末に「断定できません」と答えていた。これはエージェント(LLM)の限界ではなくエンジン側の実装不足と判断し、`find_exterior_doors()`(避難経路解析、歩行可能グラフ上の次数1)と同じ考え方をWindowに適用する専用モジュールを追加した:Room/Zone-Windowの"adjacent"関係で隣接する部屋数を数え、1部屋のみなら外部窓、2部屋以上なら内部窓、0部屋なら判定不能とする(`GET /engine/windows`、エージェントの`engine_windows_tool`)。**実データ(5708要素、窓114件)での結果**: 外部窓15件・内部窓91件・判定不能8件。ドアの判定と異なりRoom-Windowはowner壁ベースの精緻化(`graph/door_ownership.py`)が適用されておらず距離ベースの隣接判定のみのため、ドアの判定より粗い(既知の限界、モジュールのdocstringに明記)。**実データでの興味深い副産物**: この建物ではバルコニーがRoom/Zoneとしてモデル化されているため、「LD↔バルコニー」のような外皮開口が「2部屋に隣接」=内部窓と判定される(内部窓91件中43件がバルコニー系Zoneとの隣接)。エージェントはこの点を自分で発見し、「15+43≒58件が外壁開口の実質的な件数」という補足込みの回答を生成した(ツールは生データを返すのみで、この解釈はLLM側が行っている)。
- **(2026-08-11追加)`engine/effective_daylighting.py`: 建築基準法施行令第20条の採光補正係数を用いた、法定計算に基づく有効採光面積比を追加した。** きっかけはユーザーからの「`code_engine.py`の採光チェック(窓面積/床面積の単純比)ではなく、本当に法令通りの結果が欲しい」という指摘。legal_search_tool/legal_article_toolで施行令20条の現行条文を実際に取得して確認した上で実装した(有効採光面積=Σ開口部面積×採光補正係数、採光補正係数=採光関係比率(D/H)×用途地域ごとの係数−定数、上限3.0)。
  - **D(水平距離)**: 開口部の所属壁(`graph/door_ownership.py`の`find_opening_outward_direction()`、今回追加)の法線方向のうち部屋の外側を向く方へレイを飛ばし、「敷地境界線」Zoneの境界との交点までの距離を測る。「前面道路」Zoneに面する場合は法令の規定通り道路の反対側の境界線までとする(緩和規定)。
  - **H(垂直距離)**: 開口部のXY位置の直上にあるSlab/Roofのうち最も低いz_minと開口部中心の垂直距離。直上に何も無ければ(屋上に面する開口部等)障害物が無い最も有利なケースとして上限3.0を採用する。
  - **用途地域**: BIMデータのどこにも記録されていない外部の都市計画情報のため、環境変数`LAND_USE_CATEGORY`(residential/industrial/commercial、既定residential)で明示的に指定する仕様にした(推測しない)。
  - 既存の`rule_engine.py`の拡張ポイント(`RULE_CHECK_REGISTRY`+`legal_rules.json`にエントリを足すだけ)にそのまま乗せたため、新規のAPIエンドポイント・エージェントツールを追加する必要がなかった(`GET /engine/legal_rules/effective_daylighting_ratio/evaluate`、エージェントの既存`engine_legal_rules_evaluate_tool`がそのまま使える)。検証用の生データ確認には`GET /engine/code/daylighting_effective`を追加した。
  - **(実装中に発見・同日修正)部屋の内外判定(開口部の外側方向を求めるための壁プローブ点の内側/外側テスト)に、大分類ゾーン(「Aタイプ」等、住戸全体を表すZone、`graph/envelope.py`)を含めてしまっていたバグを発見・修正した。** 大分類ゾーンは実室を丸ごと包含するため、両プローブ点が両方とも「内側」と誤判定され、実データ(143室)で62室が未解決になっていた。`find_rooms()`(既に大分類ゾーン除外済み)の結果に絞り込むことで解消し、未解決16室まで改善(65室PASS・62室FAIL・16室UNKNOWN)。
  - **実データでの結果**: 居室系(LD・洋室等)は軒並みPASS(比率1.2〜1.4、大きな窓+開けた敷地により採光補正係数の上限3.0が頻繁に適用される)。AIエージェント経由での動作確認では、エージェント自身が「PS/MB/EV等の小面積ゾーンに大面積の窓が誤って帰属し比率が異常値になっている」という既知の限界(Room-Windowが距離ベースの隣接判定のみでドアほど精緻でない、`window_classifier.py`と同種)を自ら指摘する回答を生成した。
- **(2026-08-11追加)`engine/legal_inputs.py`: BIMデータからは判定できない法規条件(用途地域・都市計画区域・高度地区・地区計画・建蔽率・容積率・防火地域・防火指定・前面道路種別・道路幅員・接道長さ・日影規制の対象区域指定/規制時間の12項目)が不足している場合に、`LegalRule`の判定を諦めて黙ってUNKNOWNにするのではなく、AIエージェントが「何が足りないか」をユーザーに具体的に聞き返せる仕組みを汎用化した。** きっかけはユーザーが提示した都市計画/建築規制/防火規制/道路条件/日影・斜線の法規条件ツリーをArchicadのプロパティとしてテンプレート化したいという相談で、「必要な条件が足りない時は要求する」仕組みの汎用化(個別の日影規制計算そのものではない)を選択した。
  - `LEGAL_INPUT_DEFINITIONS`(key/label/description/category)が12項目の定義を持つ。値は現状**環境変数のみ**から解決する(`resolve_legal_input(key)`はキーを大文字化した環境変数名、例: `land_use_category`→`LAND_USE_CATEGORY`、を読む)。Archicadのカスタムプロパティ(ユーザーが実際にテンプレート化したいと考えている経路)からの取り込みは未実装——`sync_from_archicad()`が組み込みの`GetDetailsOfElements`情報のみを同期し`GetPropertyValuesOfElements`によるカスタムProperty値を含まないため(既知の今後の課題、`legal_inputs.py`のdocstringに明記)。
  - `LegalRule`(`legal_rules.json`)に`required_inputs`(キー一覧)フィールドを追加した。`effective_daylighting_ratio`ルールに`required_inputs: ["land_use_category"]`を設定済み(既存の`daylighting_ratio`/`accessible_door_width`はBIMデータのみで判定できるため空のまま)。
  - `evaluate_legal_rule()`(`engine/rule_engine.py`)は判定を実行する前に`required_inputs`が揃っているか確認し、不足していれば`check`関数を呼ばずに`missing_inputs`(key/label/description)を返す(`items`は空になる)。個別要素の実測値が欠けている「UNKNOWN」(判定は実行されたが値が無い)とは意味が異なり、「判定そのものが原理的に開始できない」ことを区別して伝える。
  - `GET /engine/legal_inputs`(定義一覧+現在値+どのルールが使うか)、エージェントの`engine_legal_inputs_tool`を追加。`agent/report_graph.py`(法規レポート、LLMがユーザーに聞き返せない単発パイプライン)は`missing_inputs`があるチェックを「未判定」としてPASS/FAIL集計と切り離して要約する。frontend「法規レポート」タブにも同様の「未判定」表示を追加した。
  - **(2026-08-13、Missing Input Interrupt/Resumeパターンへ変更)** 会話(`agent/graph.py`のReActエージェント)側は当初、`SYSTEM_PROMPT`の指示文だけでLLMに「missing_inputsを見たら具体的に尋ね、値の設定・再起動を案内し、また同じツールを呼び直す」という手順を守らせていた。ユーザーが提示したエージェント設計パターン(Router→Evidence Layer→Rule Engine→分岐{Missing Input→Interrupt→Resume, Determination→Answer})を検討した結果、この待ち合わせは本来プロンプト上の作法ではなくグラフの構造(一時停止→外部イベント→再開)として表現すべきだと判断し、`engine_legal_rules_evaluate_tool`(`agent/tools.py`)自体がLangGraphの`interrupt()`を呼ぶよう変更した。判定に必要な値が不足している間は`while result.get("missing_inputs")`ループの中で`interrupt(payload)`を呼び、会話全体(グラフ実行)を一時停止する。値は`legal_inputs.py`の制約により環境変数からしか解決できず、このエージェントには書き換え権限が無い(=「backendを再起動する」という外部イベントを待つしかない)ため、この一時停止はLangGraphのcheckpointer(`AsyncSqliteSaver`)に永続化され、**backendプロセスの再起動を挟んでも再開できる**(interrupt/resumeが本来意図している「長時間・プロセス跨ぎの一時停止」のユースケースに合致する)。
    - `service.py`に`resume_chat(session_id)`を追加(`langgraph.types.Command(resume=True)`で再開する。resumeする値そのものには意味が無く、「ユーザーが値を設定し再起動した上で再開を指示した」という合図でしかない——値がまだ不足していれば、再実行された`evaluate_legal_rule_by_id`が再び`missing_inputs`を返し、ツールは再度`interrupt()`する)。`run_chat`/`resume_chat`とも、結果に`__interrupt__`(LangGraphが一時停止時に`ainvoke()`の戻り値へ含める)が含まれる場合は`interrupted: true`・`interrupt: {type, rule_id, missing_inputs, message}`を返す共通の`_finish_turn()`を経由する。`main.py`に`POST /agent/chat/resume`(`{session_id}`)を追加。
    - `agent/graph.py`の`SYSTEM_PROMPT`から、missing_inputs発生時にLLM自身が文章を組み立てる指示を削除した(ツール自体が一時停止するため、LLMがその場で応答を生成する機会自体が無くなった)。
    - frontend「AIエージェント」タブ(`AgentChatTab.tsx`)に対応を追加: `interrupted`な応答は(一時停止中は最終的なAIMessageがまだ無く会話履歴に現れないため)専用の`interruptState`として保持し、`missing_inputs`の一覧+「再開」ボタン(`POST /agent/chat/resume`)を表示する。再開中は通常の入力欄を無効化する。
    - 回帰テストは`tests/test_agent.py`(`InMemorySaver`をcheckpointerに使い、実際にグラフを一時停止→env var変更→再開して判定が完了することを確認する`test_run_chat_interrupts_on_missing_legal_inputs_then_resumes`、値がまだ不足したまま再開すると再びinterruptされることを確認する`test_run_chat_interrupts_again_when_still_missing_after_resume`)。`tests/test_agent_tools.py`の旧テスト(ツール単体を`ainvoke()`で直接呼び、返り値のJSONに`missing_inputs`が入っていることを検証していたもの)は、`interrupt()`が実際のグラフ実行(checkpointer付き)の外では正しく動作しない(pregelのスクラッチパッドが無く`KeyError`になる)ため、resolved-rule(`accessible_door_width`、`required_inputs`無し)の単体検証に置き換え、missing_inputsの経路はグラフレベルのテストへ移設した。
- **(2026-08-13追加)`engine/evidence.py`(Evidence Layer): 確定的なBIM実測値と、ヒューリスティック/ノイズを含む候補とを下流(Claim Validator、将来のエージェント推論)が機械可読な形で区別できるよう、`evidence_confidence`タグ(`EvidenceConfidence`: `deterministic`/`heuristic`/`candidate`)を付与する薄いレイヤーを追加した。** ユーザー提示のエージェント設計パターン(Router→**Evidence Layer**→Rule Engine→...)の検討結果を踏まえたもので、判定ロジック自体は変えていない——このプロジェクトはこれまでも「参考値です」「ノイズを含む候補」等の免責文で同じ区別を人間向けの散文としてのみ表現してきたが、機械可読なタグとしては存在しなかった。Legal Knowledge Builder自体が返す`confidence`(正規表現抽出のconfidenceスコア、`rule_engine.py`の`legal_sources`に元々ある数値フィールド)とは別物であり、フィールド名を`evidence_confidence`にして衝突を避けている。
  - `rule_engine.py`の`evaluate_legal_rule()`: BIM実測値から直接算出する`items`には`deterministic`、Legal Knowledge Builderの正規表現抽出による`legal_sources`には`candidate`を付与する。
  - `window_classifier.py`の`classify_windows()`: トポロジー(隣接部屋数)からの推定である`windows`各要素には`heuristic`を付与する(ドキュメント化済みの既知の限界がある判定であることの機械可読な明示)。
  - 回帰テストは`tests/test_evidence.py`(`tag()`の単体テスト)、`tests/test_rule_engine.py`(`items`が`deterministic`、`legal_sources`が`candidate`であることの確認)、`tests/test_window_classifier.py`(`windows`が`heuristic`であることの確認)。

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

**残る推奨実装順序**: `building_code.py`の数値ルール追加。CLAUDE.md「⑥ 空間知能エンジン」の追加モジュール案は`equipment.py`まで全て実装済みとなった。

- **(2026-08-13検討・実装後に削除)`space_classifier.py`(用途分類、居室/非居室の判定): 実装したがユーザー判断により削除、未実装のまま。** きっかけはユーザーからの「居室判定をBIM空間知能エンジンに実装できないか」という相談。建築基準法第2条4号(居室の定義)・第28条(居室の採光)等をLegal Knowledge Builderで確認した上で、Zone実名から居室該当性を推定する`room_classifier.py`を実装し(`code_engine.py`/`effective_daylighting.py`の採光チェックに統合、実データでFAILの大半が非居室の誤検出だったことまで確認済みだった)、いったんは動作させた。**しかしユーザーから「名前ベースの推定であり確定的な法的判断でなければ、誤判断を招く原因になるので、削除してください」と明確な差し戻しがあり、実装・テスト・エンドポイント・ドキュメントを全て削除した。** 教訓: このプロジェクトは他の推定ロジック(`window_classifier.py`の外部/内部窓判定、`legal_sources`の関連法令候補等)では「参考値・確定的ではない」という免責付きで推定結果を提供する方針を採ってきたが、居室/非居室のように判定結果がそのまま法規チェックの対象を絞り込む(=チェック自体をスキップさせる)性質のものは、免責を付けても誤判断のリスクが許容されないというユーザー判断が示された。同種の「絞り込み・除外」目的の推定ロジックを再検討する際はこの点に注意すること。
  - **(2026-08-13追記、未検証・出典ファイル未確認)** ユーザーから「S59の全国建築行政連絡会議の取扱い」というファイル名の資料からの引用として、住宅の台所の居室除外基準の提示があった: `\\wsl.localhost\Ubuntu-24.04\home\gdre1\Legal Knowledge Builder\data\source_xml` を実際に確認したが、**該当ファイルは存在しない**(同フォルダにはe-Gov法令XML(建築基準法・建築士法・都市計画法、14件)のみが置かれており、法令ではなく行政実務上の解釈指針である全国建築行政連絡会議の「取扱い」はLegal Knowledge Builderの取り込み対象外)。原典を確認できていないため、以下はユーザー提供情報としてそのまま記録するのみで、内容の正確性・現行性はAI側では検証していない:
    > 住宅の台所においては次に該当するものは、居室として扱わないことができる
    > ①調理のみに使用し、食事等の用を供しない(形態的に十分それが予測される。)
    > ②床面積が小さく(おおむね3〜4.5帖程度)、他の部分と間仕切等で明確に区画されていること
    上記の`room_classifier.py`削除の経緯([[feedback_no_uncertain_filtering_logic]]、Claude memory参照)を踏まえると、仮にこの基準を将来実装で使う場合も、原典(全国建築行政連絡会議の資料そのもの、または所管行政庁への確認)による検証なしに法規チェックの絞り込みロジックへ組み込むべきではない。

### ⑦ 解析結果ストア

- AI解析結果テーブルを作成
- `engine_analysis_results`/`graph_relation_results`は「直近1回分を全削除→書き込み」方式で履歴を持たない。RAGが時系列の変化(いつ何が変わったか)を参照するには、履歴を積む形のテーブルへの拡張が必要

### ⑧ 埋め込み/インデックス層

- `/bim/index`が手動呼び出しのみで、`sync_from_archicad()`や`rebuild_connections()`の後に自動実行されない。同期後にベクトルインデックスが古いまま放置され得る
- 埋め込み対象が要素単位の短い説明文のみ(`engine/vector_store.py`の`_describe_element()`)。部屋ごとに周辺要素をまとめた文書など、RAGでの検索精度を上げるチャンク戦略は未検討
- **(2026-08-08発見・同日修正)フロントエンド「要素同期」タブの「検索インデックス化」ボタン(`POST /bim/index`)が実データで500エラーになるバグがあった。** `index_elements()`(`engine/vector_store.py`)がChromaDBの`collection.upsert()`を全要素一括(5708件)で呼んでいたが、ChromaDBには1回のupsertで送れる件数に上限があり(実測5461件)、これを超えると`chromadb.errors.InternalError`("Batch size of N is greater than max batch size of M")になっていた。ユーザーが敷地/道路Zoneを追加して要素数が上限を超えたことで、このセッション中に初めて顕在化した。`_UPSERT_BATCH_SIZE = 1000`で固定サイズに分割して複数回に分けてupsertするよう修正し、実データ(5708要素)で全件インデックス化・意味検索(`/bim/search`)の動作を確認済み

### ⑨ RAG / AIエージェント層(2026-08-11時点で最小構成を実装済み)

- **(2026-08-11追加)** エージェント本体(`agent/`、LangGraph + Claude API)を実装した。詳細は上記「AIエージェント層(`agent/`, 2026-08-11追加)」参照。`POST /agent/chat`で発話→ツール選択→実行→観察→次の発話のループが動く。会話履歴はLangGraphのcheckpointer(`AsyncSqliteSaver`)で`session_id`単位に永続化される(上記の「会話/セッションの永続化が無い」というギャップは解消)。フロントエンドにも「AIエージェント」チャットタブを追加済み。
- **(2026-08-11同日追加)** 複数ステップの解析フロー(法規チェック→引用条文添付→レポート生成)を`agent/report_graph.py`(LangGraphの`StateGraph`)として実装した。`run_agent`のReActループとは別物で、手順が固定された決定的なパイプライン。`POST /agent/legal_report`、frontend「法規レポート」タブから実行できる。
- **意図的に残した制約**: 公開しているツールは読み取り専用・解析系のみ。`archicad_mcp/server.py`の書き込み系ツール(`update_element_properties`/`move_archicad_element`/`delete_archicad_elements`等)はエージェントには公開していない——監査ログ(下記⑩参照)が未整備のため、AIエージェントが自律的に実在の建物データを書き換えられる状態にするのは時期尚早と判断した。
- **(2026-08-13追加)エージェント設計パターンの強化。** ユーザーが提示したアーキテクチャ図(Router→並列{Spatial/Legal/BIM}→Evidence Layer→Rule Engine→分岐{Missing Input→Interrupt→Resume, Determination→Answer Graph→Claim Validator→Response})を検討し、既存構成(素直なReActループ+`report_graph.py`の固定パイプライン)への適用を4段階で実装した(詳細は各モジュールの記載箇所参照):
  1. `report_graph.py`の`verify_report`ノード(Claim Validatorパターン): LLMが書いたレポート文中のPASS/FAIL/UNKNOWN件数を実際の判定結果と機械的に突き合わせ、不一致なら1回訂正させ、それでも直らなければ警告を付記する。
  2. `agent/tools.py`の`engine_legal_rules_evaluate_tool`(Missing Input Interrupt/Resumeパターン): 法規条件不足時にLangGraphの`interrupt()`で会話を一時停止し、値の設定・backend再起動を挟んでも`resume_chat()`で再開できるようにした。
  3. `engine/evidence.py`(Evidence Layer): BIM実測値(`deterministic`)・ヒューリスティック推定(`heuristic`)・ノイズを含む候補(`candidate`)を機械可読なタグとして区別できるようにした。
  4. `agent/router.py`(Router): 発話のキーワードからツール集合を絞り込み、ReActループに渡すプロンプトの肥大化を抑える(ドメイン別サブグラフへの分岐は現状の規模では過剰と判断し採用していない)。
- **未着手のまま残っている項目**: 解析結果ストア(⑦)側の履歴化(いつ何が変わったか)は未着手のまま。`report_graph.py`は現状「法規チェック→レポート生成→検証」の1系統のみで、他の複数ステップフロー(例: 避難経路解析→改善提案レポート)は未実装。Routerはキーワードヒューリスティックのみで、ドメインを跨ぐ婉曲な質問には弱い(既知の限界、`router.py`のdocstring参照)。
- 建築関連法の条文検索(`/legal/search`等)は先に用意できていたため、エージェントのツールとしてそのままlegal_search_tool等でラップした(GraphRAGの多段階検索`legal_graph_neighbors_tool`も含む)。

### ⑩ アクション/操作層

- `move_archicad_element`/`delete_archicad_elements`/`set_archicad_property_value`など、実在の建物データを変更する破壊的操作が既にAPIとして露出しているが、誰が/いつ/何を変更したかの監査ログが一切ない。AIエージェント(⑨)が自律的にこれらを呼べるようになった段階で、監査ログと(必要なら)承認フローの追加が必須

### 横断的関心事

- 認証・認可の実装(現状FastAPIエンドポイントに認証なし、CORSも`http://localhost:5173`固定)
- 可観測性の整備(構造化ログ、Archicad連携の失敗率/レイテンシ、エンジン計算時間などのメトリクス)
