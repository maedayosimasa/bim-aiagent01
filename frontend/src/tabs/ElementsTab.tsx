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
import { useArchicadFocus } from "../hooks/useArchicadFocus";

ModuleRegistry.registerModules([AllCommunityModule]);

const prefersDark =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-color-scheme: dark)").matches;

// 金基調のテーマに合わせてアクセントカラーもgold系に統一する。
const GOLD_ACCENT = prefersDark ? "#d4af37" : "#b8860b";

const gridTheme = (
  prefersDark ? themeQuartz.withPart(colorSchemeDarkBlue) : themeQuartz
).withParams({ accentColor: GOLD_ACCENT });

// properties/geometryはArchicad要素タイプごとに形が異なるネストしたオブジェクトなので、
// 取得した内容を漏れなく点検できるようドット区切りで再帰的にフラット化してカラム化する。
// 配列(座標列など)は要素ごとに長さが揃わないため個別カラム化はせず、JSON文字列として1カラムに収める。
type FlatRow = { guid: string; type: string; name: string } & Record<string, unknown>;

function flattenInto(value: unknown, prefix: string, into: Record<string, unknown>) {
  if (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.keys(value).length > 0
  ) {
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      flattenInto(child, prefix ? `${prefix}.${key}` : key, into);
    }
    return;
  }
  into[prefix] = value;
}

function flattenElement(el: BimElement): FlatRow {
  const flat: Record<string, unknown> = {};
  flattenInto(el.properties, "properties", flat);
  flattenInto(el.geometry, "geometry", flat);
  return { guid: el.guid, type: el.type, name: el.name, ...flat };
}

const BASE_COLUMN_DEFS: ColDef<FlatRow>[] = [
  { field: "guid", headerName: "GUID", pinned: "left", width: 300 },
  { field: "type", headerName: "種別", width: 120 },
  { field: "name", headerName: "名前", width: 180 },
];

function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return JSON.stringify(value);
  return String(value);
}

function ElementsTab() {
  const queryClient = useQueryClient();
  const [selectedGuid, setSelectedGuid] = useState<string | null>(null);
  const focusInArchicad = useArchicadFocus();

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

  const flatRows = useMemo(
    () => elementsQuery.data?.map(flattenElement) ?? [],
    [elementsQuery.data]
  );

  const columnDefs = useMemo<ColDef<FlatRow>[]>(() => {
    // 要素タイプによって値が入っているカラムがバラバラなので、単純なアルファベット順だと
    // 大半の行で空になる項目が先頭に来て「同じカラムしか取れていない」ように見えてしまう。
    // そのため値が入っている行数(埋まり具合)が多い項目から並べ、点検しやすくする。
    const fillCount = new Map<string, number>();
    for (const row of flatRows) {
      for (const [key, value] of Object.entries(row)) {
        if (key === "guid" || key === "type" || key === "name") continue;
        if (value === null || value === undefined || value === "") continue;
        fillCount.set(key, (fillCount.get(key) ?? 0) + 1);
      }
    }
    const sortedKeys = Array.from(fillCount.keys()).sort((a, b) => {
      const diff = (fillCount.get(b) ?? 0) - (fillCount.get(a) ?? 0);
      return diff !== 0 ? diff : a.localeCompare(b);
    });
    const dynamicColumns: ColDef<FlatRow>[] = sortedKeys.map((key) => ({
      field: key,
      headerName: `${key} (${fillCount.get(key)}/${flatRows.length})`,
      width: 200,
      valueFormatter: (params) => formatCellValue(params.value),
    }));
    return [...BASE_COLUMN_DEFS, ...dynamicColumns];
  }, [flatRows]);

  return (
    <div className="tab-panel">
      <h2>要素同期</h2>

      <p className="hint">
        「Archicadから同期」を実行すると、キャッシュ(SQLite)上の要素データは
        全削除された上で、その時点のArchicadの全要素で置き換わります
        (差分マージではありません)。開発時のデータ確認用途を想定しています。
      </p>
      <p className="hint">
        (2026-08-14追加)同期のたびに前回の内容と比較し、追加・変更・削除
        された要素があれば、空間関係(隣接・接続)の再計算と検索インデックス
        の更新が自動的に行われます。何も変化が無い場合はこれらの処理を
        スキップするため、変化が無ければ数秒で完了します(初回同期・大量の
        変更がある場合は検索インデックスの更新に数十秒かかることがあります)。
        「関係を再構築」「検索インデックス化」は通常は不要ですが、キャッシュの
        不整合が疑われる場合などに手動で全件を強制的に再実行するためのボタン
        として残しています。
      </p>
      <p className="hint">
        行をクリックするとArchicad本体でも同じ要素が選択+ハイライトされます
        (ブリッジ接続時のみ)。画面をその位置までスクロールする機能はArchicad側の
        APIにないため、選択された要素を手動で探してください。
      </p>

      <div className="button-row">
        <button onClick={() => syncMutation.mutate()} disabled={syncMutation.isPending}>
          {syncMutation.isPending ? "同期中..." : "Archicadから同期(全件・既存データを置換)"}
        </button>
        <button
          onClick={() => rebuildMutation.mutate()}
          disabled={rebuildMutation.isPending}
        >
          {rebuildMutation.isPending ? "再構築中..." : "関係を手動で再構築(通常は同期時に自動実行)"}
        </button>
        <button onClick={() => indexMutation.mutate()} disabled={indexMutation.isPending}>
          {indexMutation.isPending ? "インデックス中..." : "検索インデックスを手動で全件再構築"}
        </button>
        <button onClick={() => invalidateElements()} disabled={elementsQuery.isFetching}>
          {elementsQuery.isFetching ? "取得中..." : "一覧を再取得"}
        </button>
      </div>

      <div className="status-line">
        {syncMutation.isSuccess && (
          <span>
            同期完了: {syncMutation.data.synced}/{syncMutation.data.requested}件
            (追加{syncMutation.data.diff.added}・変更{syncMutation.data.diff.changed}・
            削除{syncMutation.data.diff.removed}・不変{syncMutation.data.diff.unchanged})
            {syncMutation.data.relations_rebuilt ? (
              <> / 関係再計算: {syncMutation.data.relations_count}件</>
            ) : (
              <> / 変化なしのため関係再計算・インデックス更新はスキップしました</>
            )}
            {syncMutation.data.relations_rebuilt && (
              <>
                {" "}
                / インデックス更新: {syncMutation.data.index_updated_count}件追加・更新
                {syncMutation.data.index_removed_count > 0 &&
                  `、${syncMutation.data.index_removed_count}件削除`}
              </>
            )}
          </span>
        )}
        {syncMutation.isError && <span className="error">{String(syncMutation.error)}</span>}
        {rebuildMutation.isSuccess && (
          <span>関係再構築: {rebuildMutation.data.count}件</span>
        )}
        {indexMutation.isSuccess && (
          <span>インデックス化: {indexMutation.data.count}件</span>
        )}
        <span>
          現在の表示件数: {flatRows.length}件
          {elementsQuery.isFetching && "(更新中...)"}
        </span>
        {elementsQuery.dataUpdatedAt > 0 && (
          <span>
            最終取得: {new Date(elementsQuery.dataUpdatedAt).toLocaleTimeString("ja-JP")}
          </span>
        )}
      </div>

      {elementsQuery.isLoading && <p>読み込み中...</p>}
      {elementsQuery.isError && <p className="error">{String(elementsQuery.error)}</p>}

      {elementsQuery.data && elementsQuery.data.length === 0 && (
        <p>要素がありません。「Archicadから同期」で取得してください。</p>
      )}

      {elementsQuery.data && elementsQuery.data.length > 0 && (
        <div style={{ height: "60vh", width: "100%" }}>
          <AgGridReact<FlatRow>
            theme={gridTheme}
            rowData={flatRows}
            columnDefs={columnDefs}
            // フィールド名がドット区切り("properties.archicad_details.x")の平坦化済みキーなので、
            // AG Gridがそれをネストしたオブジェクトパスとして解釈しないよう無効化する
            // (デフォルトだとfieldのドットをパス扱いしてしまい、セルが常に空になる)。
            suppressFieldDotNotation
            defaultColDef={{
              sortable: true,
              filter: true,
              resizable: true,
              wrapHeaderText: true,
              autoHeaderHeight: true,
            }}
            getRowId={(params) => params.data.guid}
            onRowClicked={(event: RowClickedEvent<FlatRow>) => {
              const nextGuid =
                event.data?.guid === selectedGuid ? null : event.data?.guid ?? null;
              setSelectedGuid(nextGuid);
              focusInArchicad(nextGuid);
            }}
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
