import { useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AllCommunityModule,
  ModuleRegistry,
  themeQuartz,
  colorSchemeDarkBlue,
  type ColDef,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import {
  generateLegalReport,
  type LegalReportCheck,
  type LegalReportItem,
  type LegalReportStatus,
} from "../api/client";

ModuleRegistry.registerModules([AllCommunityModule]);

const prefersDark =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-color-scheme: dark)").matches;

const gridTheme = (
  prefersDark ? themeQuartz.withPart(colorSchemeDarkBlue) : themeQuartz
).withParams({ accentColor: prefersDark ? "#d4af37" : "#b8860b" });

// PASS/FAIL/UNKNOWNはbackend側(engine/rule_engine.py等)・CLAUDE.mdの
// 用語でもあるため併記し、表示上の主役は日本語ラベルにする。
const STATUS_LABELS: Record<LegalReportStatus, string> = {
  pass: "合格(PASS)",
  fail: "不合格(FAIL)",
  unknown: "不明(UNKNOWN)",
  not_applicable: "対象外",
};

// グリッドの既定ソート(下記STATUS_SORT_RANK参照)で不合格を先頭に集める
// ための並び順。数値が小さいほど上に来る。
const STATUS_SORT_RANK: Record<LegalReportStatus, number> = {
  fail: 0,
  unknown: 1,
  not_applicable: 2,
  pass: 3,
};

function countByStatus(check: LegalReportCheck): Record<LegalReportStatus, number> {
  const counts: Record<LegalReportStatus, number> = {
    pass: 0,
    fail: 0,
    unknown: 0,
    not_applicable: 0,
  };
  for (const item of check.items) {
    counts[item.status] += 1;
  }
  return counts;
}

// LLMが生成する実測値は浮動小数点演算の誤差で末尾が
// "0.028486593962242776" のように長くなりがちなので、小数点3桁に丸めて
// 読みやすくする(丸めるとJSの数値表現が末尾の0を自動で落とすため
// "2.150" のような不要な0は残らない)。
function formatMeasuredValue(item: LegalReportItem): string {
  if (item.measured_value === null) return "-";
  const rounded = Math.round(item.measured_value * 1000) / 1000;
  return `${rounded}${item.unit ?? ""}`;
}

type ItemRow = LegalReportItem & { statusLabel: string };

const ITEM_COLUMN_DEFS: ColDef<ItemRow>[] = [
  {
    field: "target_name",
    headerName: "対象",
    flex: 1,
    minWidth: 160,
    valueGetter: (params) => params.data?.target_name ?? params.data?.target_guid,
  },
  {
    field: "status",
    headerName: "判定",
    width: 160,
    sort: "asc",
    comparator: (a: LegalReportStatus, b: LegalReportStatus) =>
      STATUS_SORT_RANK[a] - STATUS_SORT_RANK[b],
    cellClass: (params) => `legal-report-status-${params.value}`,
    valueGetter: (params) => params.data?.status,
    valueFormatter: (params) => STATUS_LABELS[params.value as LegalReportStatus],
  },
  {
    headerName: "実測値",
    width: 160,
    valueGetter: (params) => (params.data ? formatMeasuredValue(params.data) : ""),
  },
];

function LegalReportTab() {
  const reportMutation = useMutation({ mutationFn: generateLegalReport });

  return (
    <div className="tab-panel">
      <h2>法規レポート</h2>

      <p className="hint">
        登録されている法規チェック(engine/legal_rules.json、採光有効面積比・
        バリアフリードア幅)を全件実行し、AIエージェントが結果を日本語で
        まとめます。判定自体はLLMではなく決定的な計算(engine/rule_engine.py)
        で行われ、LLMは要約・文章化のみを担当します(複数ステップグラフ:
        法規チェック→引用条文添付→レポート生成、agent/report_graph.py)。
        いずれも参考値であり、法的な適合を保証するものではありません。
      </p>

      <div className="button-row">
        <button onClick={() => reportMutation.mutate()} disabled={reportMutation.isPending}>
          {reportMutation.isPending ? "生成中..." : "レポート生成"}
        </button>
      </div>

      {reportMutation.isError && <p className="error">{String(reportMutation.error)}</p>}

      {reportMutation.data && (
        <>
          <h3>サマリー</h3>
          <div className="agent-message agent-message-ai legal-report-summary markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {reportMutation.data.report}
            </ReactMarkdown>
          </div>

          <h3>チェック項目別詳細</h3>
          {reportMutation.data.checks.map((check) => (
            <LegalReportCheckDetail key={check.rule_id} check={check} />
          ))}
        </>
      )}
    </div>
  );
}

function LegalReportCheckDetail({ check }: { check: LegalReportCheck }) {
  const counts = countByStatus(check);

  const rows = useMemo<ItemRow[]>(
    () => check.items.map((item) => ({ ...item, statusLabel: STATUS_LABELS[item.status] })),
    [check.items]
  );

  // 100件超のチェックもあるため、行数に応じて高さを可変にしつつ上限を設ける
  // (画面いっぱいに1チェックが広がりすぎないように)。
  const gridHeight = Math.min(Math.max(rows.length, 3) * 34 + 46, 480);

  return (
    <details className="legal-report-check" open={counts.fail > 0}>
      <summary>
        {check.title} — 合格 {counts.pass} / 不合格 {counts.fail} / 不明{" "}
        {counts.unknown}
        {counts.not_applicable > 0 ? ` / 対象外 ${counts.not_applicable}` : ""}
      </summary>

      <p className="hint">{check.disclaimer}</p>

      {check.missing_inputs.length > 0 && (
        <p className="error">
          未判定: このチェックには外部の法規条件(
          {check.missing_inputs.map((m) => m.label).join("、")}
          )が必要ですが未設定のため、判定を実行していません。
          backendの環境変数(キーを大文字化したもの、例:
          LAND_USE_CATEGORY)に値を設定し、backendを再起動してください。
        </p>
      )}

      {rows.length > 0 ? (
        <div className="graph-canvas legal-report-grid" style={{ height: gridHeight }}>
          <AgGridReact<ItemRow>
            theme={gridTheme}
            rowData={rows}
            columnDefs={ITEM_COLUMN_DEFS}
            defaultColDef={{ sortable: true, filter: true, resizable: true }}
            getRowId={(params) => params.data.target_guid}
            rowClassRules={{
              "legal-report-row-fail": (params) => params.data?.status === "fail",
              "legal-report-row-unknown": (params) => params.data?.status === "unknown",
            }}
          />
        </div>
      ) : (
        <p>対象要素がありません。</p>
      )}

      {check.legal_sources.length > 0 && (
        <>
          <p className="hint">
            関連しそうな法令根拠({check.legal_sources.length}件、参考情報。
            正規表現ベースの候補でありノイズを含みます):
          </p>
          <ul className="legal-rule-list">
            {check.legal_sources.map((source, index) => (
              <li key={index}>
                {source.law_id ?? "?"}: {source.raw_sentence}
              </li>
            ))}
          </ul>
        </>
      )}
    </details>
  );
}

export default LegalReportTab;
