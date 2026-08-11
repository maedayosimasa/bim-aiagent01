// バックエンド(FastAPI)への型付きAPIクライアント。
// 各コンポーネントで API_BASE をハードコードせず、ここに集約する。

export const API_BASE = "http://localhost:8000";

// timeoutMsを指定すると、その時間内にbackendから応答が無い場合にリクエストを
// 中断してエラーにする(2026-08-11、AIエージェントが「考え中...」のまま
// 無期限にハングして見えた問題への対策 — backend自体は数十秒でエラー応答を
// 返していたが、backendプロセスの再起動等でTCP接続が宙に浮くと、ブラウザの
// fetch()にはデフォルトのタイムアウトが無いため何も表示されないまま止まって
// 見えることがあった)。
async function request<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number }
): Promise<T> {
  const { timeoutMs, ...fetchInit } = init ?? {};
  const controller = timeoutMs ? new AbortController() : undefined;
  const timeoutId = timeoutMs
    ? setTimeout(() => controller!.abort(), timeoutMs)
    : undefined;

  try {
    const res = await fetch(`${API_BASE}${path}`, {
      headers: fetchInit.body ? { "Content-Type": "application/json" } : undefined,
      signal: controller?.signal,
      ...fetchInit,
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      // backend(FastAPI)のHTTPExceptionは{"detail": "..."}を返す。生JSONを
      // そのままエラーメッセージに出すと読みにくいので、detailだけ取り出す。
      let detail = text;
      try {
        const parsed = JSON.parse(text);
        if (parsed && typeof parsed.detail === "string") {
          detail = parsed.detail;
        }
      } catch {
        // JSONでなければtextのまま使う
      }
      throw new Error(`${path} failed: ${res.status} ${detail}`);
    }

    return res.json();
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(
        `${path} がタイムアウトしました(${Math.round((timeoutMs ?? 0) / 1000)}秒応答がありませんでした)。バックエンドが再起動中でないか確認してください。`,
        { cause: err }
      );
    }
    throw err;
  } finally {
    if (timeoutId !== undefined) clearTimeout(timeoutId);
  }
}

const get = <T,>(path: string, timeoutMs?: number) => request<T>(path, { timeoutMs });

const post = <T,>(path: string, body?: unknown, timeoutMs?: number) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
    timeoutMs,
  });

// ==============================
// 型定義
// バックエンド main.py / graph / engine の実装に対応
// ==============================

export type HealthStatus = {
  status: string;
};

export type BimElement = {
  guid: string;
  type: string;
  name: string;
  properties: Record<string, unknown>;
  geometry: Record<string, unknown>;
};

export type GraphNode = {
  id: string;
  type: string | null;
  name: string | null;
  x: number | null;
  y: number | null;
};

export type GraphEdge = {
  source: string;
  target: string;
  relation: string | null;
  distance: number | null;
};

export type GraphData = {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type AnalyzeResult = {
  model_id: string;
  graph: { nodes: number; edges: number; connected: boolean };
  graph_data: GraphData;
  elements: { walls: number; doors: number; windows: number; rooms: number };
  analysis: { wall_check: string; door_check: string };
  issues: unknown[];
};

// engine/graphの計算結果をSQLiteに保存したもの(再計算のたびに全削除→
// 書き込み直す方式、履歴は積まない)を開発時に検証するための型。
export type EngineAnalysisSnapshot = {
  id: number;
  model_id: string;
  computed_at: string;
  node_count: number;
  edge_count: number;
  connected: boolean;
  wall_count: number;
  door_count: number;
  window_count: number;
  room_count: number;
  issues: unknown[];
  graph_data: GraphData;
} | null;

export type GraphRelationResult = {
  id: number;
  computed_at: string;
  source_guid: string;
  source_type: string | null;
  target_guid: string;
  target_type: string | null;
  relation: string;
  distance: number;
};

export type SearchHit = {
  guid: string;
  document: string;
  type: string | null;
  name: string | null;
  distance: number;
};

export type SearchResponse = {
  query: string;
  results: SearchHit[];
};

export type ArchicadConnectionInfo = {
  active_url: string | null;
  overridden: boolean;
  env_default: string | null;
  local_preset_url: string;
};

export type ArchicadStatus = {
  configured: boolean;
  reachable: boolean;
  tools?: string[];
  error?: string;
};

// GetAllProperties (Tapir)の生の構造。プロジェクト側で正規化していない
// (archicad_mcp/tapir.pyのdocstring参照)ため、既知フィールドのみ緩く型付けする。
export type ArchicadPropertyDefinition = {
  propertyId?: { guid: string };
  name?: string;
  group?: { name?: string };
  [key: string]: unknown;
};

export function getHealth() {
  return get<HealthStatus>("/health");
}

export function analyzeSpace(modelId: string) {
  return post<AnalyzeResult>("/analyze", { model_id: modelId });
}

export function importTestData() {
  return post<{ status: string }>("/bim/import_test");
}

export function rebuildRelations() {
  return post<{ status: string; count: number }>("/bim/rebuild_relations");
}

export function getEngineAnalysisSnapshot() {
  return get<EngineAnalysisSnapshot>("/engine/analysis_snapshot");
}

export function getGraphRelationSnapshot() {
  return get<GraphRelationResult[]>("/graph/relation_snapshot");
}

export function indexElements() {
  return post<{ status: string; count: number }>("/bim/index");
}

export function searchElements(query: string, nResults = 5) {
  return post<SearchResponse>("/bim/search", { query, n_results: nResults });
}

export function listElements() {
  return get<BimElement[]>("/bim/elements");
}

export function getArchicadStatus() {
  return get<ArchicadStatus>("/archicad/status");
}

export function getArchicadConnection() {
  return get<ArchicadConnectionInfo>("/archicad/connection");
}

// ==============================
// Legal Knowledge Builder連携
// 別リポジトリ(~/Legal Knowledge Builder/)が構築したKnowledge Package
// (建築基準法等の条文・引用関係・数値ルール)を検索するための型・関数。
// backend側の /legal/* は Legal Knowledge Builder の検索API(別プロセス、
// LEGAL_API_URL)への薄いプロキシ。
// ==============================

export type LegalSearchHit = {
  node_id: string;
  law_id: string | null;
  law_title: string | null;
  citation: string | null;
  text: string | null;
  distance: number;
};

export type LegalSearchResponse = {
  query: string;
  results: LegalSearchHit[];
};

export type LegalLawMetadata = {
  law_id: string;
  law_title: string;
  law_type: string;
  category: string;
  as_of_date: string;
  parent_law_title_hint: string | null;
};

// Legal Knowledge BuilderのGET /health(manifest.json由来)の内容。
// build allの結果(法令数・ノード数・引用/未解決数・ルール数・validation可否等)、
// つまり「今読み込んでいるKnowledge Packageの構成」そのもの。
export type LegalManifest = {
  version: string;
  built_at: string;
  law_ids: string[];
  law_count: number;
  node_count: number;
  reference_count: number;
  unresolved_reference_count: number;
  rule_count: number;
  validation_passed: boolean;
};

export type LegalHealthDetail = {
  status: string;
  manifest?: LegalManifest;
};

export type LegalApiStatus = {
  configured: boolean;
  reachable: boolean;
  detail?: LegalHealthDetail;
  error?: string;
};

export type LegalSearchBackend = "chroma" | "pgvector";

export function searchLegal(
  query: string,
  topK = 5,
  lawId?: string,
  backend: LegalSearchBackend = "chroma"
) {
  const params = new URLSearchParams({ q: query, top_k: String(topK), backend });
  if (lawId) params.set("law_id", lawId);
  return get<LegalSearchResponse>(`/legal/search?${params.toString()}`);
}

export function getLegalLaws() {
  return get<LegalLawMetadata[]>("/legal/laws");
}

export function getLegalStatus() {
  return get<LegalApiStatus>("/legal/status");
}

// rule_graph / reference_graph(検索結果1件ごとの詳細表示用)。
// バックエンドのモデル(legal_knowledge_builder.models.rule/.reference)に対応。

export type LegalComparator = "gte" | "lte" | "lt" | "gt" | "eq";

export type LegalNumericCondition = {
  raw_text: string;
  value: number;
  unit: string | null;
  comparator: LegalComparator | null;
};

export type LegalModality =
  | "obligation"
  | "prohibition"
  | "permission"
  | "exception"
  | "definition";

export type LegalRule = {
  rule_id: string;
  node_id: string;
  law_id: string;
  raw_sentence: string;
  modality: LegalModality | null;
  conditions: LegalNumericCondition[];
  concept_ids: string[];
  confidence: number;
};

export type LegalReferenceType =
  | "article_citation"
  | "relative"
  | "external_law"
  | "apply_mutatis_mutandis"
  | "deemed_reading";

export type LegalReferenceEdge = {
  from_node_id: string;
  to_node_id: string | null;
  to_law_id: string | null;
  to_law_title: string | null;
  ref_type: LegalReferenceType;
  raw_text: string;
  resolved: boolean;
  unresolved_reason: string | null;
};

export type LegalReferenceResult = {
  outgoing: LegalReferenceEdge[];
  incoming: LegalReferenceEdge[];
};

export function getLegalRules(lawId: string, nodeId: string) {
  const params = new URLSearchParams({ law_id: lawId, node_id: nodeId });
  return get<LegalRule[]>(`/legal/rules?${params.toString()}`);
}

export function getLegalReference(lawId: string, nodeId: string) {
  const params = new URLSearchParams({ law_id: lawId, node_id: nodeId });
  return get<LegalReferenceResult>(`/legal/reference?${params.toString()}`);
}

export function setArchicadConnection(url: string | null) {
  return post<{ connection: ArchicadConnectionInfo; status: ArchicadStatus }>(
    "/archicad/connection",
    { url }
  );
}

export function syncFromArchicad(limit = 50) {
  return post<{ synced: number; requested: number }>("/archicad/sync", { limit });
}

export function getArchicadProperties() {
  return get<ArchicadPropertyDefinition[]>("/archicad/properties");
}

export function getArchicadPropertyValues(guids: string[], propertyGuids: string[]) {
  return post<unknown[]>("/archicad/properties/values", {
    guids,
    property_guids: propertyGuids,
  });
}

export function setArchicadPropertyValue(
  guid: string,
  propertyGuid: string,
  value: string
) {
  return post<unknown>("/archicad/properties/set", {
    guid,
    property_guid: propertyGuid,
    value,
  });
}

export function moveArchicadElement(
  guid: string,
  dx: number,
  dy: number,
  dz = 0,
  duplicate = false
) {
  return post<unknown>("/archicad/elements/move", { guid, dx, dy, dz, duplicate });
}

export function deleteArchicadElements(guids: string[]) {
  return post<unknown>("/archicad/elements/delete", { guids });
}

// フロントエンドでの選択とArchicad本体の選択+ハイライトを連動させる。
// guidsを空配列で呼ぶと選択/ハイライトを解除する。
// (カメラ移動には対応していない - Tapirにその機能がないため)
export function focusArchicadElements(guids: string[]) {
  return post<unknown>("/archicad/elements/focus", { guids });
}

// ==============================
// AIエージェント(LangGraph、backend/src/backend/agent/)
// ツールは読み取り専用・解析系のみ(Archicad書き込み系ツールは未対応)。
// 会話履歴はsession_id単位でバックエンド側(SQLite)に永続化される。
// ==============================

export type AgentStatus = {
  configured: boolean;
  model: string;
};

export type AgentToolCallResult = {
  name: string;
  args: Record<string, unknown>;
  result: string;
};

export type AgentChatResponse = {
  session_id: string;
  response: string;
  tool_calls: AgentToolCallResult[];
};

export type AgentHistoryMessage =
  | { role: "human"; content: string }
  | { role: "ai"; content: string }
  | { role: "tool"; name: string; content: string };

export type AgentHistoryResponse = {
  session_id: string;
  messages: AgentHistoryMessage[];
};

export function getAgentStatus() {
  return get<AgentStatus>("/agent/status");
}

// ツール呼び出しを何度も往復する可能性があるため、他のAPI呼び出しより長め
// (3分)のタイムアウトにする。これを超えても応答が無ければ、backendの
// プロセス再起動等で接続が宙に浮いている可能性が高いと判断してエラーにする。
const AGENT_TIMEOUT_MS = 180_000;

export function sendAgentMessage(sessionId: string, message: string) {
  return post<AgentChatResponse>(
    "/agent/chat",
    { session_id: sessionId, message },
    AGENT_TIMEOUT_MS
  );
}

export function getAgentHistory(sessionId: string) {
  return get<AgentHistoryResponse>(
    `/agent/history/${encodeURIComponent(sessionId)}`,
    30_000
  );
}

// 法規チェック→引用条文添付→レポート生成、の複数ステップグラフ
// (backend/src/backend/agent/report_graph.py、LangGraphのStateGraph)。
// run_chat/getAgentHistoryの会話エージェントとは別物で、session_idは使わない
// (毎回、登録済み全ルールを対象に最初から実行する単発のパイプライン)。

export type LegalReportStatus = "pass" | "fail" | "unknown" | "not_applicable";

export type LegalReportItem = {
  target_guid: string;
  target_name: string | null;
  status: LegalReportStatus;
  measured_value: number | null;
  unit: string | null;
  evidence: Record<string, unknown>;
};

export type LegalReportSource = {
  rule_id: string | null;
  law_id: string | null;
  node_id: string | null;
  raw_sentence: string;
  modality: string | null;
  confidence: number | null;
};

export type LegalReportMissingInput = {
  key: string;
  label: string;
  description: string | null;
};

export type LegalReportCheck = {
  rule_id: string;
  title: string;
  concept_id: string;
  threshold: number;
  threshold_unit: string | null;
  comparator: string;
  disclaimer: string;
  legal_sources: LegalReportSource[];
  items: LegalReportItem[];
  missing_inputs: LegalReportMissingInput[];
};

export type LegalReportResponse = {
  checks: LegalReportCheck[];
  report: string;
};

export function generateLegalReport() {
  return post<LegalReportResponse>("/agent/legal_report", undefined, AGENT_TIMEOUT_MS);
}
