import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import cytoscape, { type Core, type NodeSingular, type NodePositionMap } from "cytoscape";
import fcose from "cytoscape-fcose";
import ForceGraph3D, {
  type ForceGraphMethods,
  type NodeObject,
} from "react-force-graph-3d";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  colorSchemeDarkBlue,
  type ColDef,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { analyzeSpace, listElements, type GraphData, type GraphNode } from "../api/client";
import { useArchicadFocus } from "../hooks/useArchicadFocus";
import { deriveElevationsMeters } from "../bimElevation";

ModuleRegistry.registerModules([AllCommunityModule]);
cytoscape.use(fcose);

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

type LayoutMode = "floorplan" | "force";

// ノード間隔スライダーの倍率(spacing)に応じてfcoseレイアウトのパラメータをスケールする。
// gravityは値が大きいほど中心に寄る=詰まって見えるので、spacingが大きいほど逆に弱める。
// fcoseはcoseよりも高品質なばね/斥力シミュレーションに加え、非連結成分の
// タイル詰め(tile)を持つため、旧cose実装より絡み合い(ヘアボール化)が少ない。
function buildForceLayoutOptions(spacing: number) {
  return {
    name: "fcose",
    quality: "proof",
    animate: false,
    randomize: true,
    nodeRepulsion: 4500 * spacing * spacing,
    idealEdgeLength: 60 * spacing,
    gravity: 0.25 / spacing,
    tile: true,
    tilingPaddingVertical: 20,
    tilingPaddingHorizontal: 20,
  } as const;
}

type FloorPlanPosition = { x: number; y: number; z: number };

// BIM要素の実座標(mm)をそのままレイアウトに使う「フロアプラン」モード用。
// 抽象的な力学配置ではなく実際の平面図に近い配置になるため、要素数が多い
// 密なグラフでも「どのあたりの壁/ドアか」が視覚的に把握しやすくなる。
// 画面座標はy軸が下向きのため、BIM側のy(北が正)を反転してから正規化する。
// z(高さ)はelevationsMm(要素の代表高さ、mm)があればx/yと同じ縮尺で正規化し、
// 3D表示で実際の高さ関係(ドアより窓が高い、など)が見えるようにする。
// elevationsMmが無い/空の場合は全ノードz=0(平面)として扱う。
function buildFloorPlanPositions(
  nodes: GraphNode[],
  elevationsMm: Map<string, number>
): Record<string, FloorPlanPosition> | null {
  const withCoords = nodes.filter(
    (node): node is GraphNode & { x: number; y: number } =>
      typeof node.x === "number" && typeof node.y === "number"
  );

  if (withCoords.length === 0) return null;

  const xs = withCoords.map((node) => node.x);
  const ys = withCoords.map((node) => node.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const span = Math.max(maxX - minX, maxY - minY, 1);
  const scale = 1000 / span;

  const elevationValues = [...elevationsMm.values()];
  const minZ = elevationValues.length > 0 ? Math.min(...elevationValues) : 0;

  const positions: Record<string, FloorPlanPosition> = {};
  for (const node of withCoords) {
    positions[node.id] = {
      x: (node.x - minX) * scale,
      y: (maxY - node.y) * scale,
      z: ((elevationsMm.get(node.id) ?? minZ) - minZ) * scale,
    };
  }

  // 座標を持たない要素は図面中央付近にまとめて配置し、見失われないようにする。
  const centerX = ((maxX - minX) * scale) / 2;
  const centerY = ((maxY - minY) * scale) / 2;
  for (const node of nodes) {
    if (!(node.id in positions)) {
      positions[node.id] = { x: centerX, y: centerY, z: 0 };
    }
  }

  return positions;
}

function buildLayoutOptions(
  mode: LayoutMode,
  spacing: number,
  floorPlanPositions: Record<string, FloorPlanPosition> | null
) {
  if (mode === "floorplan" && floorPlanPositions) {
    const positions: NodePositionMap = {};
    for (const [id, pos] of Object.entries(floorPlanPositions)) {
      positions[id] = { x: pos.x, y: pos.y };
    }
    return {
      name: "preset",
      positions,
      fit: true,
      padding: 30,
    } as const;
  }

  return buildForceLayoutOptions(spacing);
}

// 3D表示用のノード/リンクデータ。layoutModeが"floorplan"のときはfx/fy/fzで
// 位置を固定し(力学シミュレーションを止めて実座標のまま表示)、"force"のときは
// 固定せずreact-force-graph-3d標準の3D力学シミュレーションに任せる。
// three.js座標系(Y-up)に合わせ、BIMのz(高さ)→world.y、BIMのy→world.zとする
// (SpaceViewer3Dのtoscene変換と同じ向き)。
function build3DGraphData(
  graph: GraphData,
  showIsolated: boolean,
  layoutMode: LayoutMode,
  floorPlanPositions: Record<string, FloorPlanPosition> | null
) {
  const degree = new Map<string, number>();

  for (const edge of graph.edges) {
    degree.set(edge.source, (degree.get(edge.source) ?? 0) + 1);
    degree.set(edge.target, (degree.get(edge.target) ?? 0) + 1);
  }

  const visibleNodes = showIsolated
    ? graph.nodes
    : graph.nodes.filter((node) => (degree.get(node.id) ?? 0) > 0);

  const fixPositions = layoutMode === "floorplan" && floorPlanPositions !== null;

  const nodes = visibleNodes.map((node) => {
    const pos = floorPlanPositions?.[node.id];
    const fixed = fixPositions && pos ? { fx: pos.x - 500, fy: pos.z, fz: pos.y - 500 } : {};
    return {
      id: node.id,
      label: node.name ?? node.id.slice(0, 8),
      type: node.type ?? "Unknown",
      ...fixed,
    };
  });

  const links = graph.edges.map((edge, i) => {
    const relationLabel = edge.relation ? RELATION_LABELS_JA[edge.relation] ?? edge.relation : "";
    const distanceLabel = typeof edge.distance === "number" ? `${Math.round(edge.distance)}mm` : "";
    return {
      id: `e${i}`,
      source: edge.source,
      target: edge.target,
      label: [relationLabel, distanceLabel].filter(Boolean).join(" "),
    };
  });

  return {
    graphData: { nodes, links },
    hiddenCount: graph.nodes.length - visibleNodes.length,
    fixPositions,
  };
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
  const outerRef = useRef<HTMLDivElement>(null);
  const cyMountRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const fg3dRef = useRef<ForceGraphMethods | undefined>(undefined);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [showIsolated, setShowIsolated] = useState(false);
  const [hiddenCount, setHiddenCount] = useState(0);
  const [spacing, setSpacing] = useState(1);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("floorplan");
  const [dimension, setDimension] = useState<"2d" | "3d">("2d");
  const [containerSize, setContainerSize] = useState({ width: 0, height: 0 });
  const [selectedGuid, setSelectedGuid] = useState<string | null>(null);
  const selectedGuidRef = useRef<string | null>(null);
  const focusInArchicad = useArchicadFocus();

  const analyzeMutation = useMutation({
    mutationFn: () => analyzeSpace("default"),
    onSuccess: (result) => setGraphData(result.graph_data),
  });

  // 3D表示で要素の実際の高さ(Z)を使うため、要素同期タブと同じAPIを叩いて取得する。
  // 2D表示中は使わないので3Dに切り替えたときだけ取得する。
  const elementsQuery = useQuery({
    queryKey: ["bim-elements"],
    queryFn: listElements,
    enabled: dimension === "3d",
  });

  const elevationsMm = useMemo(() => {
    const elevationsM = deriveElevationsMeters(elementsQuery.data ?? []);
    const mm = new Map<string, number>();
    for (const [guid, z] of elevationsM) mm.set(guid, z * 1000);
    return mm;
  }, [elementsQuery.data]);

  const relationRows = useMemo(
    () => (graphData ? buildRelationRows(graphData) : []),
    [graphData]
  );

  const floorPlanPositions = useMemo(
    () => (graphData ? buildFloorPlanPositions(graphData.nodes, elevationsMm) : null),
    [graphData, elevationsMm]
  );

  // フロアプラン配置が使えない(座標を持つ要素が1件もない)場合は自動配置にフォールバックする。
  const effectiveLayoutMode: LayoutMode = floorPlanPositions ? layoutMode : "force";

  const graph3D = useMemo(
    () =>
      graphData
        ? build3DGraphData(graphData, showIsolated, effectiveLayoutMode, floorPlanPositions)
        : null,
    [graphData, showIsolated, effectiveLayoutMode, floorPlanPositions]
  );

  useEffect(() => {
    if (!cyMountRef.current || !graphData || dimension !== "2d") return;

    cyRef.current?.destroy();

    const { elements, hiddenCount: hidden } = buildElements(graphData, showIsolated);
    setHiddenCount(hidden);

    const cy = cytoscape({
      container: cyMountRef.current,
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
      layout: buildLayoutOptions(effectiveLayoutMode, spacing, floorPlanPositions),
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
    // spacing/layoutMode/floorPlanPositionsは初期レイアウトにのみ使う。値が変わる
    // たびに全ノードを作り直す必要はなく、下の別effectで既存インスタンスの
    // レイアウトだけ再計算する。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphData, showIsolated, dimension]);

  useEffect(() => {
    if (!cyRef.current || dimension !== "2d") return;

    cyRef.current.layout(buildLayoutOptions(effectiveLayoutMode, spacing, floorPlanPositions)).run();
    cyRef.current.fit(undefined, 20);
  }, [spacing, effectiveLayoutMode, floorPlanPositions, dimension]);

  useEffect(() => {
    if (!outerRef.current) return;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        const { width, height } = entry.contentRect;
        setContainerSize({ width, height });
      }
      cyRef.current?.resize();
    });

    observer.observe(outerRef.current);

    return () => observer.disconnect();
  }, []);

  return (
    <div className="tab-panel">
      <h2>空間関係グラフ</h2>

      <div className="button-row">
        <button onClick={() => analyzeMutation.mutate()} disabled={analyzeMutation.isPending}>
          {analyzeMutation.isPending ? "解析中..." : "空間解析を実行"}
        </button>
        <button
          onClick={() => {
            if (dimension === "2d") {
              cyRef.current?.fit(undefined, 20);
            } else {
              fg3dRef.current?.zoomToFit(400, 40);
            }
          }}
          disabled={!graphData}
        >
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
          表示
          <select
            value={dimension}
            disabled={!graphData}
            onChange={(e) => setDimension(e.target.value as "2d" | "3d")}
          >
            <option value="2d">2D</option>
            <option value="3d">3D(回転可能)</option>
          </select>
        </label>
        <label className="graph-spacing-control">
          配置方法
          <select
            value={effectiveLayoutMode}
            disabled={!graphData || !floorPlanPositions}
            onChange={(e) => setLayoutMode(e.target.value as LayoutMode)}
          >
            <option value="floorplan">実座標(フロアプラン)</option>
            <option value="force">自動配置(力学モデル)</option>
          </select>
        </label>
        <label className="graph-spacing-control">
          ノード間隔
          <input
            type="range"
            min={0.5}
            max={3}
            step={0.1}
            value={spacing}
            disabled={!graphData || effectiveLayoutMode === "floorplan"}
            onChange={(e) => setSpacing(Number(e.target.value))}
          />
          {spacing.toFixed(1)}x
        </label>
      </div>
      {graphData && !floorPlanPositions && (
        <p className="hint">
          座標を持つ要素がないため、実座標(フロアプラン)配置は利用できません。自動配置で表示しています。
        </p>
      )}

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
      {dimension === "3d" && (
        <p className="hint">
          3D表示: ドラッグで回転、ホイールでズーム、右ドラッグでパンできます。実座標配置では
          要素の実際の高さ(壁の中心、ドア・窓の取付高さなど)がZ方向に反映されます。
        </p>
      )}
      {dimension === "3d" && elementsQuery.isLoading && (
        <p className="hint">高さ情報を読み込み中...</p>
      )}
      {selectedGuid && (
        <p className="status-line">
          選択中: {graphData?.nodes.find((n) => n.id === selectedGuid)?.name ?? selectedGuid}
        </p>
      )}

      <div ref={outerRef} className="graph-canvas">
        <div
          ref={cyMountRef}
          style={{ width: "100%", height: "100%", display: dimension === "2d" ? "block" : "none" }}
        />
        {dimension === "3d" && graphData && graph3D && (
          <ForceGraph3D
            ref={fg3dRef}
            graphData={graph3D.graphData}
            width={containerSize.width || undefined}
            height={containerSize.height || undefined}
            backgroundColor={prefersDark ? "#100d08" : "#faf5e6"}
            controlType="orbit"
            cooldownTicks={graph3D.fixPositions ? 0 : undefined}
            onEngineStop={() => fg3dRef.current?.zoomToFit(400, 40)}
            nodeLabel={(node) => {
              const n = node as NodeObject<{ type: string; label: string }>;
              return `${TYPE_LABELS_JA[n.type] ?? n.type}: ${n.label}`;
            }}
            nodeColor={(node) => {
              const n = node as NodeObject<{ type: string }>;
              if (n.id === selectedGuid) return "#b8860b";
              return TYPE_COLORS[n.type] ?? "#999";
            }}
            nodeVal={(node) => (node.id === selectedGuid ? 6 : 2)}
            linkLabel={(link) => (link as { label?: string }).label ?? ""}
            linkColor={() => (prefersDark ? "#4a3f24" : "#c8c6cf")}
            linkOpacity={0.6}
            linkWidth={1}
            onNodeClick={(node) => {
              const guid = node.id as string;

              if (selectedGuidRef.current === guid) {
                selectedGuidRef.current = null;
                setSelectedGuid(null);
                focusInArchicad(null);
                return;
              }

              selectedGuidRef.current = guid;
              setSelectedGuid(guid);
              focusInArchicad(guid);
            }}
            onBackgroundClick={() => {
              selectedGuidRef.current = null;
              setSelectedGuid(null);
              focusInArchicad(null);
            }}
          />
        )}
      </div>

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
