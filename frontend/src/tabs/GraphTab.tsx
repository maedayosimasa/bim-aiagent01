import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import cytoscape, { type Core, type NodeSingular } from "cytoscape";
import { analyzeSpace, type GraphData } from "../api/client";

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

  const edges = graph.edges.map((edge, i) => ({
    data: {
      id: `e${i}`,
      source: edge.source,
      target: edge.target,
      label: edge.relation ? RELATION_LABELS_JA[edge.relation] ?? edge.relation : "",
    },
  }));

  return { elements: [...nodes, ...edges], hiddenCount: graph.nodes.length - visibleNodes.length };
}

function GraphTab() {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [showIsolated, setShowIsolated] = useState(false);
  const [hiddenCount, setHiddenCount] = useState(0);

  const analyzeMutation = useMutation({
    mutationFn: () => analyzeSpace("default"),
    onSuccess: (result) => setGraphData(result.graph_data),
  });

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
      layout: {
        name: "cose",
        animate: false,
        nodeRepulsion: () => 8000,
        idealEdgeLength: () => 60,
        nodeOverlap: 8,
        componentSpacing: 60,
        gravity: 40,
      },
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

    cyRef.current = cy;

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [graphData, showIsolated]);

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

      <div ref={containerRef} className="graph-canvas" />
    </div>
  );
}

export default GraphTab;
