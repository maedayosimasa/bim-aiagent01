import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import cytoscape, { type Core, type NodeSingular } from "cytoscape";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  colorSchemeDarkBlue,
  type ColDef,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { analyzeSpace, type GraphData } from "../api/client";
import { useArchicadFocus } from "../hooks/useArchicadFocus";

ModuleRegistry.registerModules([AllCommunityModule]);

const prefersDark =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-color-scheme: dark)").matches;

const gridTheme = (
  prefersDark ? themeQuartz.withPart(colorSchemeDarkBlue) : themeQuartz
).withParams({ accentColor: prefersDark ? "#d4af37" : "#b8860b" });

const RELATION_LABELS_JA: Record<string, string> = {
  adjacent: "隣接",
  connects: "接続",
  near: "近接",
};

const TYPE_COLORS: Record<string, string> = {
  Room: "#4f9d69",
  Zone: "#4f9d69",
  Wall: "#8a8f98",
  Door: "#d99a3b",
  Window: "#3b8ed9",
};

const TYPE_LABELS_JA: Record<string, string> = {
  Room: "部屋",
  Zone: "部屋",
  Wall: "壁",
  Door: "ドア",
  Window: "窓",
};

// ノード間隔スライダーの倍率(spacing)に応じてcoseレイアウトのパラメータをスケールする。
// gravityは値が大きいほど中心に寄る=詰まって見えるので、spacingが大きいほど逆に弱める。
function buildLayoutOptions(spacing: number) {
  return {
    name: "cose",
    animate: false,
    nodeRepulsion: () => 8000 * spacing,
    idealEdgeLength: () => 60 * spacing,
    nodeOverlap: 8,
    componentSpacing: 60 * spacing,
    gravity: 40 / spacing,
  } as const;
}

function buildElements(graph: GraphData, showIsolated: boolean) {
  const degree = new Map<string, number>();

  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }

  const visibleNodes = showIsolated
    ? graph.nodes
    : graph.nodes.filter((node) => (degree.get(node.id) ?? 0) > 0);

  const nodes = visibleNodes.map((node) => ({
    data: {
      id: node.id,
      label: node.name ?? node.id.slice(0, 8),
      type: node.type ?? "Unknown",
    },
  }));

  const edges = graph.edges.map((edge, i) => {
    const relationLabel = edge.relation ? RELATION_LABELS_JA[edge.relation] ?? edge.relation : "";
    const distanceLabel = typeof edge.distance === "number" ? `${Math.round(edge.distance)}mm` : "";
    return {
      data: {
        id: `e${i}`,
        source: edge.source,
        target: edge.target,
        label: [relationLabel, distanceLabel].filter(Boolean).join(" "),
      },
    };
  });

  return { elements: [...nodes, ...edges], hiddenCount: graph.nodes.length - visibleNodes.length };
}

type RelationRow = {
  id: string;
  sourceGuid: string;
  sourceType: string;
  sourceName: string;
  targetGuid: string;
  targetType: string;
  targetName: string;
  relation: string;
  distanceMm: number | null;
  distanceM: number | null;
};

// engine/graph側の計算結果(どの要素同士がどんな関係で、距離がいくつか)を
// 開発時にそのまま点検できるよう、グラフのnodes/edgesを表形式に変換する。
function buildRelationRows(graph: GraphData): RelationRow[] {
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));

  return graph.edges.map((edge, i) => {
    const source = nodeById.get(edge.source);
    const target = nodeById.get(edge.target);
    return {
      id: `${edge.source}-${edge.target}-${i}`,
      sourceGuid: edge.source,
      sourceType: source?.type ?? "Unknown",
      sourceName: source?.name ?? edge.source.slice(0, 8),
      targetGuid: edge.target,
      targetType: target?.type ?? "Unknown",
      targetName: target?.name ?? edge.target.slice(0, 8),
      relation: edge.relation ? RELATION_LABELS_JA[edge.relation] ?? edge.relation : "",
      distanceMm: edge.distance,
      distanceM: typeof edge.distance === "number" ? edge.distance / 1000 : null,
    };
  });
}

const RELATION_COLUMN_DEFS: ColDef<RelationRow>[] = [
  { field: "sourceType", headerName: "関係元 種別", width: 110 },
  { field: "sourceName", headerName: "関係元 名前", width: 160 },
  { field: "targetType", headerName: "関係先 種別", width: 110 },
  { field: "targetName", headerName: "関係先 名前", width: 160 },
  { field: "relation", headerName: "関係種別", width: 110 },
  {
    field: "distanceMm",
    headerName: "距離(mm)",
    width: 130,
    valueFormatter: (params) =>
      typeof params.value === "number" ? params.value.toFixed(1) : "",
  },
  {
    field: "distanceM",
    headerName: "距離(m)",
    width: 110,
    valueFormatter: (params) =>
      typeof params.value === "number" ? params.value.toFixed(2) : "",
  },
  { field: "sourceGuid", headerName: "関係元 GUID", width: 280 },
  { field: "targetGuid", headerName: "関係先 GUID", width: 280 },
];

function GraphTab() {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [showIsolated, setShowIsolated] = useState(false);
  const [hiddenCount, setHiddenCount] = useState(0);
  const [spacing, setSpacing] = useState(1);
  const [selectedGuid, setSelectedGuid] = useState<string | null>(null);
  const selectedGuidRef = useRef<string | null>(null);
  const focusInArchicad = useArchicadFocus();

  const analyzeMutation = useMutation({
    mutationFn: () => analyzeSpace("default"),
    onSuccess: (result) => setGraphData(result.graph_data),
  });

  const relationRows = useMemo(
    () => (graphData ? buildRelationRows(graphData) : []),
    [graphData]
  );

  useEffect(() => {
    if (!containerRef.current || !graphData) return;

    cyRef.current?.destroy();

    const { elements, hiddenCount: hidden } = buildElements(graphData, showIsolated);
    setHiddenCount(hidden);

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            "background-color": (ele) =>
              TYPE_COLORS[ele.data("type") as string] ?? "#999",
            "border-width": 1,
            "border-color": "rgba(255, 255, 255, 0.53)",
            label: "",
            color: "#08060d",
            "font-size": 11,
            "text-valign": "bottom",
            "text-margin-y": 6,
            "text-background-color": "#fff",
            "text-background-opacity": 0.85,
            "text-background-padding": "2px",
            width: 16,
            height: 16,
          },
        },
        {
          selector: "node.hovered",
          style: {
            label: "data(label)",
            "border-width": 2,
            "border-color": "#08060d",
            "z-index": 10,
          },
        },
        {
          selector: "node.selected",
          style: {
            label: "data(label)",
            "border-width": 3,
            "border-color": "#b8860b",
            "z-index": 20,
            width: 22,
            height: 22,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#c8c6cf",
            "curve-style": "haystack",
            "haystack-radius": 0,
            label: "",
            "font-size": 9,
            color: "#6b6375",
            "text-background-color": "#fff",
            "text-background-opacity": 0.8,
          },
        },
        {
          selector: "edge.hovered",
          style: {
            label: "data(label)",
            "line-color": "#b8860b",
            width: 2.5,
          },
        },
      ],
      layout: buildLayoutOptions(spacing),
      minZoom: 0.1,
      maxZoom: 4,
    });

    cy.on("mouseover", "node", (evt) => {
      const node = evt.target as NodeSingular;
      node.addClass("hovered");
      node.connectedEdges().addClass("hovered");
    });

    cy.on("mouseout", "node", (evt) => {
      const node = evt.target as NodeSingular;
      node.removeClass("hovered");
      node.connectedEdges().removeClass("hovered");
    });

    cy.on("tap", "node", (evt) => {
      const node = evt.target as NodeSingular;
      const guid = node.id();

      cy.nodes(".selected").removeClass("selected");

      if (selectedGuidRef.current === guid) {
        selectedGuidRef.current = null;
        setSelectedGuid(null);
        focusInArchicad(null);
        return;
      }

      node.addClass("selected");
      selectedGuidRef.current = guid;
      setSelectedGuid(guid);
      focusInArchicad(guid);
    });

    cy.on("tap", (evt) => {
      if (evt.target !== cy) return;
      cy.nodes(".selected").removeClass("selected");
      selectedGuidRef.current = null;
      setSelectedGuid(null);
      focusInArchicad(null);
    });

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // spacingは初期レイアウトにのみ使う。スライダー操作のたびに全ノードを作り直す
    // 必要はなく、下の別effectで既存インスタンスのレイアウトだけ再計算する。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphData, showIsolated]);

  useEffect(() => {
    if (!cyRef.current) return;

    cyRef.current.layout(buildLayoutOptions(spacing)).run();
    cyRef.current.fit(undefined, 20);
  }, [spacing]);

  useEffect(() => {
    if (!containerRef.current) return;

    const observer = new ResizeObserver(() => {
      cyRef.current?.resize();
    });

    observer.observe(containerRef.current);

    return () => observer.disconnect();
  }, []);

  return (
    <div className="tab-panel">
      <h2>空間関係グラフ</h2>

      <div className="button-row">
        <button onClick={() => analyzeMutation.mutate()} disabled={analyzeMutation.isPending}>
          {analyzeMutation.isPending ? "解析中..." : "空間解析を実行"}
        </button>
        <button onClick={() => cyRef.current?.fit(undefined, 20)} disabled={!graphData}>
          全体を表示
        </button>
        <label>
          <input
            type="checkbox"
            checked={showIsolated}
            onChange={(e) => setShowIsolated(e.target.checked)}
          />
          孤立ノード(関係を持たない要素)も表示
        </label>
        <label className="graph-spacing-control">
          ノード間隔
          <input
            type="range"
            min={0.5}
            max={3}
            step={0.1}
            value={spacing}
            disabled={!graphData}
            onChange={(e) => setSpacing(Number(e.target.value))}
          />
          {spacing.toFixed(1)}x
        </label>
      </div>

      {analyzeMutation.isError && (
        <p className="error">{String(analyzeMutation.error)}</p>
      )}

      {analyzeMutation.data && (
        <p className="status-line">
          部屋 {analyzeMutation.data.elements.rooms} / 壁 {analyzeMutation.data.elements.walls} /
          ドア {analyzeMutation.data.elements.doors} / 窓 {analyzeMutation.data.elements.windows}
          （ノード{analyzeMutation.data.graph.nodes} / エッジ{analyzeMutation.data.graph.edges} /
          {analyzeMutation.data.graph.connected ? "連結" : "非連結"}
          {!showIsolated && hiddenCount > 0 && ` / 孤立ノード${hiddenCount}件は非表示`}）
        </p>
      )}

      {graphData && graphData.nodes.length === 0 && (
        <p>要素がありません。先に「要素同期」タブで同期してください。</p>
      )}

      <div className="graph-legend">
        {Object.entries(TYPE_COLORS).map(([type, color]) => (
          <span key={type} className="graph-legend-item">
            <span className="graph-legend-swatch" style={{ background: color }} />
            {TYPE_LABELS_JA[type] ?? type}
          </span>
        ))}
      </div>
      <p className="hint">ノードにマウスを乗せると名前と関係ラベルが表示されます。</p>
      <p className="hint">
        ノードをクリックするとArchicad本体でも同じ要素が選択+ハイライトされます
        (ブリッジ接続時のみ)。画面をその位置までスクロールする機能はArchicad側の
        APIにないため、選択された要素を手動で探してください。もう一度クリック、
        または背景をクリックすると選択解除されます。
      </p>
      {selectedGuid && (
        <p className="status-line">
          選択中: {graphData?.nodes.find((n) => n.id === selectedGuid)?.name ?? selectedGuid}
        </p>
      )}

      <div ref={containerRef} className="graph-canvas" />

      {graphData && (
        <>
          <h3>関係一覧(表)</h3>
          <p className="hint">
            engine/graph側(relation.py の calculate_relations)が計算した「どの要素同士が
            どの関係種別・距離で結びついたか」をそのまま一覧化しています。距離はgeometryと
            同じくmm単位です(RELATION_RULESの閾値もmm)。
          </p>
          {relationRows.length === 0 ? (
            <p>現在、関係(隣接/接続)は0件です。</p>
          ) : (
            <>
              <p className="status-line">関係件数: {relationRows.length}件</p>
              <div className="graph-canvas">
                <AgGridReact<RelationRow>
                  theme={gridTheme}
                  rowData={relationRows}
                  columnDefs={RELATION_COLUMN_DEFS}
                  defaultColDef={{ sortable: true, filter: true, resizable: true }}
                  getRowId={(params) => params.data.id}
                />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}

export default GraphTab;
