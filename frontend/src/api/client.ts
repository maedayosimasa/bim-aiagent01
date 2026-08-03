// バックエンド(FastAPI)への型付きAPIクライアント。
// 各コンポーネントで API_BASE をハードコードせず、ここに集約する。

export const API_BASE = "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });

  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${path} failed: ${res.status} ${detail}`);
  }

  return res.json();
}

const get = <T,>(path: string) => request<T>(path);

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
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
