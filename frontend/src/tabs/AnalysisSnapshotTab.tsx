import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  colorSchemeDarkBlue,
  type ColDef,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import {
  getEngineAnalysisSnapshot,
  getGraphRelationSnapshot,
  listElements,
  type GraphRelationResult,
} from "../api/client";

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

type EngineSnapshotRow = {
  model_id: string;
  computed_at: string;
  node_count: number;
  edge_count: number;
  connected: string;
  wall_count: number;
  door_count: number;
  window_count: number;
  room_count: number;
  issues_count: number;
};

const ENGINE_COLUMN_DEFS: ColDef<EngineSnapshotRow>[] = [
  { field: "model_id", headerName: "model_id", width: 130 },
  { field: "computed_at", headerName: "計算日時", width: 220 },
  { field: "node_count", headerName: "ノード数", width: 110 },
  { field: "edge_count", headerName: "エッジ数", width: 110 },
  { field: "connected", headerName: "連結", width: 90 },
  { field: "wall_count", headerName: "壁", width: 80 },
  { field: "door_count", headerName: "ドア", width: 80 },
  { field: "window_count", headerName: "窓", width: 80 },
  { field: "room_count", headerName: "部屋(Room/Zone)", width: 140 },
  { field: "issues_count", headerName: "issues件数", width: 110 },
];

type RelationRow = GraphRelationResult & {
  relationLabel: string;
  sourceName: string;
  targetName: string;
  distanceM: number | null;
};

const RELATION_COLUMN_DEFS: ColDef<RelationRow>[] = [
  { field: "computed_at", headerName: "計算日時", width: 200 },
  { field: "source_type", headerName: "関係元 種別", width: 110 },
  { field: "sourceName", headerName: "関係元 名前", width: 160 },
  { field: "target_type", headerName: "関係先 種別", width: 110 },
  { field: "targetName", headerName: "関係先 名前", width: 160 },
  { field: "relationLabel", headerName: "関係種別", width: 100 },
  {
    field: "distance",
    headerName: "距離(mm)",
    width: 120,
    valueFormatter: (params) =>
      typeof params.value === "number" ? params.value.toFixed(1) : "",
  },
  {
    field: "distanceM",
    headerName: "距離(m)",
    width: 100,
    valueFormatter: (params) =>
      typeof params.value === "number" ? params.value.toFixed(2) : "",
  },
  { field: "source_guid", headerName: "関係元 GUID", width: 280 },
  { field: "target_guid", headerName: "関係先 GUID", width: 280 },
];

function AnalysisSnapshotTab() {
  const queryClient = useQueryClient();

  const snapshotQuery = useQuery({
    queryKey: ["engine-analysis-snapshot"],
    queryFn: getEngineAnalysisSnapshot,
  });

  const relationsQuery = useQuery({
    queryKey: ["graph-relation-snapshot"],
    queryFn: getGraphRelationSnapshot,
  });

  const elementsQuery = useQuery({
    queryKey: ["bim-elements"],
    queryFn: listElements,
  });

  const nameByGuid = useMemo(
    () => new Map((elementsQuery.data ?? []).map((el) => [el.guid, el.name])),
    [elementsQuery.data]
  );

  const snapshot = snapshotQuery.data;

  const engineRows = useMemo<EngineSnapshotRow[]>(() => {
    if (!snapshot) return [];
    return [
      {
        model_id: snapshot.model_id,
        computed_at: snapshot.computed_at,
        node_count: snapshot.node_count,
        edge_count: snapshot.edge_count,
        connected: snapshot.connected ? "はい" : "いいえ",
        wall_count: snapshot.wall_count,
        door_count: snapshot.door_count,
        window_count: snapshot.window_count,
        room_count: snapshot.room_count,
        issues_count: snapshot.issues.length,
      },
    ];
  }, [snapshot]);

  const relationRows = useMemo<RelationRow[]>(
    () =>
      (relationsQuery.data ?? []).map((row) => ({
        ...row,
        relationLabel: RELATION_LABELS_JA[row.relation] ?? row.relation,
        sourceName: nameByGuid.get(row.source_guid) ?? row.source_guid.slice(0, 8),
        targetName: nameByGuid.get(row.target_guid) ?? row.target_guid.slice(0, 8),
        distanceM: typeof row.distance === "number" ? row.distance / 1000 : null,
      })),
    [relationsQuery.data, nameByGuid]
  );

  const isFetching = snapshotQuery.isFetching || relationsQuery.isFetching;

  const refetchAll = () => {
    queryClient.invalidateQueries({ queryKey: ["engine-analysis-snapshot"] });
    queryClient.invalidateQueries({ queryKey: ["graph-relation-snapshot"] });
  };

  return (
    <div className="tab-panel">
      <h2>解析結果DB</h2>

      <p className="hint">
        engine(analyze_space)/graph(calculate_relations)の計算結果をSQLiteに保存したものを
        そのまま表示します(再計算のたびに全削除→書き込み直す方式で、履歴は積みません)。
        ここでは新規計算は行わず、DBに保存されている内容だけを確認します。新しく計算し直すには
        「空間関係グラフ」タブの「空間解析を実行」、または「要素同期」タブの「関係を再構築」を
        実行してください。
      </p>

      <div className="button-row">
        <button onClick={refetchAll} disabled={isFetching}>
          {isFetching ? "取得中..." : "DBから再取得"}
        </button>
      </div>

      <h3>Engine解析スナップショット(常に1件のみ保持)</h3>
      {snapshotQuery.isLoading && <p>読み込み中...</p>}
      {snapshotQuery.isError && <p className="error">{String(snapshotQuery.error)}</p>}
      {!snapshotQuery.isLoading && !snapshotQuery.isError && !snapshot && (
        <p>まだ保存された解析結果がありません。「空間解析を実行」してください。</p>
      )}
      {engineRows.length > 0 && (
        <div className="graph-canvas" style={{ height: 120 }}>
          <AgGridReact<EngineSnapshotRow>
            theme={gridTheme}
            rowData={engineRows}
            columnDefs={ENGINE_COLUMN_DEFS}
            defaultColDef={{ sortable: true, filter: true, resizable: true }}
          />
        </div>
      )}

      <h3>Graph関係計算結果(DB保存分)</h3>
      {relationsQuery.isLoading && <p>読み込み中...</p>}
      {relationsQuery.isError && <p className="error">{String(relationsQuery.error)}</p>}
      {!relationsQuery.isLoading && !relationsQuery.isError && relationRows.length === 0 && (
        <p>保存された関係計算結果がありません。</p>
      )}
      {relationRows.length > 0 && (
        <>
          <p className="status-line">件数: {relationRows.length}件</p>
          <div className="graph-canvas">
            <AgGridReact<RelationRow>
              theme={gridTheme}
              rowData={relationRows}
              columnDefs={RELATION_COLUMN_DEFS}
              defaultColDef={{ sortable: true, filter: true, resizable: true }}
              getRowId={(params) => String(params.data.id)}
            />
          </div>
        </>
      )}
    </div>
  );
}

export default AnalysisSnapshotTab;
