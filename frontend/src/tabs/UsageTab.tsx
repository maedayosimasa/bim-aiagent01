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
  getTokenUsageDaily,
  getTokenUsageJobs,
  type TokenUsageDailyRow,
  type TokenUsageJobRow,
} from "../api/client";

ModuleRegistry.registerModules([AllCommunityModule]);

const prefersDark =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-color-scheme: dark)").matches;

const gridTheme = (
  prefersDark ? themeQuartz.withPart(colorSchemeDarkBlue) : themeQuartz
).withParams({ accentColor: prefersDark ? "#d4af37" : "#b8860b" });

const KIND_LABELS_JA: Record<TokenUsageJobRow["kind"], string> = {
  chat: "AIエージェント会話",
  legal_report: "法規レポート",
};

function formatUsd(value: number | null): string {
  if (value === null) return "不明";
  return `$${value.toFixed(4)}`;
}

const DAILY_COLUMN_DEFS: ColDef<TokenUsageDailyRow>[] = [
  { field: "date", headerName: "日付(UTC)", width: 130 },
  { field: "call_count", headerName: "呼び出し回数", width: 130 },
  { field: "input_tokens", headerName: "入力トークン", width: 130 },
  { field: "output_tokens", headerName: "出力トークン", width: 130 },
  {
    field: "cost_usd",
    headerName: "料金(USD)",
    width: 130,
    valueFormatter: (params) => formatUsd(params.value ?? null),
  },
];

type JobRow = TokenUsageJobRow & { kindLabel: string };

const JOB_COLUMN_DEFS: ColDef<JobRow>[] = [
  { field: "kindLabel", headerName: "種別", width: 150 },
  { field: "job_id", headerName: "作業(session_id等)", width: 220 },
  { field: "started_at", headerName: "開始", width: 210 },
  { field: "last_at", headerName: "最終", width: 210 },
  { field: "call_count", headerName: "呼び出し回数", width: 120 },
  { field: "input_tokens", headerName: "入力トークン", width: 130 },
  { field: "output_tokens", headerName: "出力トークン", width: 130 },
  {
    field: "cost_usd",
    headerName: "料金(USD)",
    width: 130,
    valueFormatter: (params) => formatUsd(params.value ?? null),
  },
];

// 料金不明(cost_usd === null)の行は合計から除外する(不明分はゼロ扱いに
// せず表示上は別途「不明」で示すため、ここでは既知分だけを合算する)。
function sumCost(rows: { cost_usd: number | null }[]): number | null {
  if (rows.length === 0) return null;
  return rows.reduce((sum, row) => sum + (row.cost_usd ?? 0), 0);
}

function UsageTab() {
  const queryClient = useQueryClient();

  const dailyQuery = useQuery({
    queryKey: ["token-usage-daily"],
    queryFn: getTokenUsageDaily,
  });

  const jobsQuery = useQuery({
    queryKey: ["token-usage-jobs"],
    queryFn: getTokenUsageJobs,
  });

  const dailyRows = dailyQuery.data?.days ?? [];
  const jobRows = useMemo<JobRow[]>(
    () =>
      (jobsQuery.data?.jobs ?? []).map((row) => ({
        ...row,
        kindLabel: KIND_LABELS_JA[row.kind] ?? row.kind,
      })),
    [jobsQuery.data]
  );

  const totalCostUsd = sumCost(dailyRows);
  const totalCalls = dailyRows.reduce((sum, row) => sum + row.call_count, 0);

  const isFetching = dailyQuery.isFetching || jobsQuery.isFetching;

  const refetchAll = () => {
    queryClient.invalidateQueries({ queryKey: ["token-usage-daily"] });
    queryClient.invalidateQueries({ queryKey: ["token-usage-jobs"] });
  };

  return (
    <div className="tab-panel">
      <h2>利用状況(Claude API トークン使用量)</h2>

      <p className="hint">
        AIエージェント(会話・法規レポート)がClaude API(現在{" "}
        <code>ANTHROPIC_AGENT_MODEL</code>、既定 claude-opus-5)を呼び出す
        たびに実測されたトークン数(agent/service.pyがLangChainの
        AIMessage.usage_metadataから取得)を記録したものです。料金は
        Anthropic公式の1Mトークンあたり単価(agent/pricing.py)から算出した
        概算で、実際の請求額(割引・為替等)とは異なる場合があります。
        料金表に無いモデルを使っている場合は「不明」と表示されます。
      </p>

      <div className="button-row">
        <button onClick={refetchAll} disabled={isFetching}>
          {isFetching ? "取得中..." : "再取得"}
        </button>
      </div>

      <p className="status-line">
        合計: {totalCalls}回の呼び出し / 合計料金 {formatUsd(totalCostUsd)}
      </p>

      <h3>1日ごとの集計(UTC日付)</h3>
      {dailyQuery.isLoading && <p>読み込み中...</p>}
      {dailyQuery.isError && <p className="error">{String(dailyQuery.error)}</p>}
      {!dailyQuery.isLoading && !dailyQuery.isError && dailyRows.length === 0 && (
        <p>まだ記録がありません。AIエージェントを使用すると記録されます。</p>
      )}
      {dailyRows.length > 0 && (
        <div className="graph-canvas" style={{ height: 220 }}>
          <AgGridReact<TokenUsageDailyRow>
            theme={gridTheme}
            rowData={dailyRows}
            columnDefs={DAILY_COLUMN_DEFS}
            defaultColDef={{ sortable: true, filter: true, resizable: true }}
            getRowId={(params) => params.data.date}
          />
        </div>
      )}

      <h3>
        作業ごとの集計(AIエージェント会話はsession_id単位、法規レポートは実行ごと)
      </h3>
      {jobsQuery.isLoading && <p>読み込み中...</p>}
      {jobsQuery.isError && <p className="error">{String(jobsQuery.error)}</p>}
      {!jobsQuery.isLoading && !jobsQuery.isError && jobRows.length === 0 && (
        <p>まだ記録がありません。</p>
      )}
      {jobRows.length > 0 && (
        <div className="graph-canvas" style={{ height: 300 }}>
          <AgGridReact<JobRow>
            theme={gridTheme}
            rowData={jobRows}
            columnDefs={JOB_COLUMN_DEFS}
            defaultColDef={{ sortable: true, filter: true, resizable: true }}
            getRowId={(params) => `${params.data.kind}:${params.data.job_id}`}
          />
        </div>
      )}
    </div>
  );
}

export default UsageTab;
