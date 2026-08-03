import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  colorSchemeDarkBlue,
  type ColDef,
  type RowClickedEvent,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import {
  indexElements,
  listElements,
  rebuildRelations,
  syncFromArchicad,
  type BimElement,
} from "../api/client";

ModuleRegistry.registerModules([AllCommunityModule]);

const prefersDark =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-color-scheme: dark)").matches;

// 金基調のテーマに合わせてアクセントカラーもgold系に統一する。
const GOLD_ACCENT = prefersDark ? "#d4af37" : "#b8860b";

const gridTheme = (
  prefersDark ? themeQuartz.withPart(colorSchemeDarkBlue) : themeQuartz
).withParams({ accentColor: GOLD_ACCENT });

const COLUMN_DEFS: ColDef<BimElement>[] = [
  { field: "guid", headerName: "GUID", flex: 1.5 },
  { field: "type", headerName: "種別", flex: 1 },
  { field: "name", headerName: "名前", flex: 1.5 },
];

function ElementsTab() {
  const queryClient = useQueryClient();
  const [selectedGuid, setSelectedGuid] = useState<string | null>(null);

  const elementsQuery = useQuery({
    queryKey: ["bim-elements"],
    queryFn: listElements,
  });

  const invalidateElements = () =>
    queryClient.invalidateQueries({ queryKey: ["bim-elements"] });

  const syncMutation = useMutation({
    // limit<=0 = 全件取得。同期のたびにキャッシュ(SQLite)を全削除してから
    // 保存し直す(差分マージではない)ので、途中で打ち切ると以前あった
    // 要素の大半が失われる - 開発確認用途では常に全件取得する。
    mutationFn: () => syncFromArchicad(0),
    onSuccess: invalidateElements,
  });

  const rebuildMutation = useMutation({
    mutationFn: rebuildRelations,
  });

  const indexMutation = useMutation({
    mutationFn: indexElements,
  });

  const selectedElement = useMemo(
    () => elementsQuery.data?.find((el) => el.guid === selectedGuid),
    [elementsQuery.data, selectedGuid]
  );

  return (
    <div className="tab-panel">
      <h2>要素同期</h2>

      <p className="hint">
        「Archicadから同期」を実行すると、キャッシュ(SQLite)上の要素データは
        全削除された上で、その時点のArchicadの全要素で置き換わります
        (差分マージではありません)。開発時のデータ確認用途を想定しています。
      </p>

      <div className="button-row">
        <button onClick={() => syncMutation.mutate()} disabled={syncMutation.isPending}>
          {syncMutation.isPending ? "同期中..." : "Archicadから同期(全件・既存データを置換)"}
        </button>
        <button
          onClick={() => rebuildMutation.mutate()}
          disabled={rebuildMutation.isPending}
        >
          {rebuildMutation.isPending ? "再構築中..." : "関係を再構築"}
        </button>
        <button onClick={() => indexMutation.mutate()} disabled={indexMutation.isPending}>
          {indexMutation.isPending ? "インデックス中..." : "検索インデックス化"}
        </button>
        <button onClick={() => invalidateElements()}>一覧を再取得</button>
      </div>

      <div className="status-line">
        {syncMutation.isSuccess && (
          <span>
            同期完了: {syncMutation.data.synced}/{syncMutation.data.requested}件
          </span>
        )}
        {syncMutation.isError && <span className="error">{String(syncMutation.error)}</span>}
        {rebuildMutation.isSuccess && (
          <span>関係再構築: {rebuildMutation.data.count}件</span>
        )}
        {indexMutation.isSuccess && (
          <span>インデックス化: {indexMutation.data.count}件</span>
        )}
      </div>

      {elementsQuery.isLoading && <p>読み込み中...</p>}
      {elementsQuery.isError && <p className="error">{String(elementsQuery.error)}</p>}

      {elementsQuery.data && elementsQuery.data.length === 0 && (
        <p>要素がありません。「Archicadから同期」で取得してください。</p>
      )}

      {elementsQuery.data && elementsQuery.data.length > 0 && (
        <div style={{ height: "60vh", width: "100%" }}>
          <AgGridReact<BimElement>
            theme={gridTheme}
            rowData={elementsQuery.data}
            columnDefs={COLUMN_DEFS}
            getRowId={(params) => params.data.guid}
            onRowClicked={(event: RowClickedEvent<BimElement>) =>
              setSelectedGuid(
                event.data?.guid === selectedGuid ? null : event.data?.guid ?? null
              )
            }
          />
        </div>
      )}

      {selectedElement && (
        <>
          <h3>詳細(properties / geometry)</h3>
          <pre className="json-block">{JSON.stringify(selectedElement, null, 2)}</pre>
        </>
      )}
    </div>
  );
}

export default ElementsTab;
