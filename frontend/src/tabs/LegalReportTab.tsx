import { useEffect, useMemo, useState } from "react";
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
//
// (2026-08-14修正)unit="ratio"の項目(容積率・建蔽率・採光/換気有効面積比)
// は、backend側の比較ロジック(engine/rule_engine.pyの`lte`/`gte`)が
// 0〜N の比率のまま扱う一方、建築実務では容積率・建蔽率は「200%」
// 「60%」のようにパーセント表記が通例(実際、Archicadの敷地Zoneカスタム
// プロパティも"90.000"(%)のまま入力されている)。そのため生の比率
// (例: 2.077)をそのまま出すと「2.077ratio」のように単位が読み取れず、
// ユーザーから「数値の意味がわからない」との指摘を受けた。表示上のみ
// ratio→%に変換する(backend側の判定ロジック・APIレスポンスの
// measured_value自体は比率のまま変更しない)。
function formatRatioOrPlain(value: number, unit: string | null): string {
  if (unit === "ratio") {
    const percent = Math.round(value * 1000) / 10;
    return `${percent}%`;
  }
  const rounded = Math.round(value * 1000) / 1000;
  return `${rounded}${unit ?? ""}`;
}

function formatMeasuredValue(item: LegalReportItem): string {
  if (item.measured_value === null) return "-";
  return formatRatioOrPlain(item.measured_value, item.unit);
}

// (2026-08-14追加)「実測値」だけでは何と比べて合否が出たのか分からない、
// というユーザーからの指摘を受け、基準値(閾値)も表示するようにした。
const COMPARATOR_LABELS: Record<string, string> = {
  lte: "以下",
  gte: "以上",
};

function formatThreshold(check: LegalReportCheck): string {
  const comparatorLabel = COMPARATOR_LABELS[check.comparator] ?? check.comparator;
  return `${formatRatioOrPlain(check.threshold, check.threshold_unit)}${comparatorLabel}`;
}

// (2026-08-14追加)ユーザーから「敷地面積339.5m²・建築面積258.3m²・建蔽率
// 76.1%(建蔽率上限90%に対し合格)」のような、判定の内訳が一目でわかる
// 表示にしてほしいとの依頼を受けた。evidence(engine/*.pyのcheck関数ごとに
// 異なるキーを持つ辞書、rule_engine.pyのRULE_CHECK_REGISTRY参照)を
// ラベル付きで人間可読なテキストに変換する。未知のキーは素のキー名に
// フォールバックする(将来ルールが追加されてもクラッシュしない)。
type EvidenceFieldFormat = {
  label: string;
  unit?: string;
  translate?: Record<string, string>;
};

const EVIDENCE_FIELD_FORMATS: Record<string, EvidenceFieldFormat> = {
  site_area_m2: { label: "敷地面積", unit: "m²" },
  building_area_m2: { label: "建築面積", unit: "m²" },
  total_floor_area_m2: { label: "延べ面積", unit: "m²" },
  floor_area_m2: { label: "床面積", unit: "m²" },
  window_area_m2: { label: "窓面積", unit: "m²" },
  effective_window_area_m2: { label: "有効採光面積", unit: "m²" },
  window_count: { label: "窓の数", unit: "件" },
  unresolved_window_count: { label: "判定不能な窓の数", unit: "件" },
  land_use_category: {
    label: "用途地域",
    translate: { residential: "住居系", industrial: "工業系", commercial: "商業系" },
  },
  reachable: { label: "避難経路", translate: { true: "到達可", false: "到達不可" } },
  worst_element_name: { label: "最も厳しい要素" },
  worst_element_height_m: { label: "実測高さ", unit: "m" },
  height_limit_m_at_worst_element: { label: "その位置の高さ上限", unit: "m" },
  kitagawa_shasen_kubun: {
    label: "北側斜線の適用区分",
    translate: { low_rise: "低層住居系", mid_rise: "中高層住居系", not_applicable: "対象外" },
  },
  kodo_chiku_kubun: {
    label: "高度地区の区分",
    translate: { none: "指定なし", flat: "一律高さ制限", north_slant: "北側斜線型" },
  },
  reason: { label: "理由" },
};

// GUIDは人間には読めないため内訳表示からは除外する(target_nameや
// worst_element_nameで代わりに要素名を示している)。
const EVIDENCE_HIDDEN_KEYS = new Set(["worst_element_guid", "nearest_exit_door_guid"]);

function formatEvidenceEntry(key: string, value: unknown): string | null {
  if (value === null || value === undefined) return null;

  if (key === "road_details" && Array.isArray(value)) {
    const parts = (value as Array<Record<string, unknown>>)
      .map((road) => {
        const name = typeof road.road_name === "string" ? road.road_name : "道路";
        const length = road.frontage_length_m;
        return typeof length === "number" ? `${name}${Math.round(length * 10) / 10}m` : null;
      })
      .filter((part): part is string => part !== null);
    return parts.length > 0 ? `前面道路の接道長さ: ${parts.join("、")}` : null;
  }

  if (EVIDENCE_HIDDEN_KEYS.has(key)) return null;

  const format = EVIDENCE_FIELD_FORMATS[key];
  const label = format?.label ?? key;

  if (typeof value === "boolean") {
    const translated = format?.translate?.[String(value)];
    return `${label}: ${translated ?? (value ? "はい" : "いいえ")}`;
  }

  if (typeof value === "number") {
    const rounded = Math.round(value * 10) / 10;
    return `${label}: ${rounded}${format?.unit ?? ""}`;
  }

  if (typeof value === "string") {
    const translated = format?.translate?.[value];
    return `${label}: ${translated ?? value}`;
  }

  return null;
}

function formatEvidence(evidence: Record<string, unknown>): string {
  return Object.entries(evidence)
    .map(([key, value]) => formatEvidenceEntry(key, value))
    .filter((part): part is string => part !== null && part.length > 0)
    .join(" ・ ");
}

type ItemRow = LegalReportItem & {
  statusLabel: string;
  thresholdText: string;
  evidenceText: string;
};

const ITEM_COLUMN_DEFS: ColDef<ItemRow>[] = [
  {
    field: "target_name",
    headerName: "対象",
    width: 140,
    valueGetter: (params) => params.data?.target_name ?? params.data?.target_guid,
  },
  {
    // (2026-08-14追加)ユーザーから「対象の階数も表示できるように」との
    // 依頼を受けて追加。SpaceViewer3D.tsxの階数表示("{floorIndex}F")と
    // 同じ表記(0始まりのfloorIndexをそのまま使う)に揃える。
    field: "floor_index",
    headerName: "階",
    width: 80,
    valueFormatter: (params) =>
      params.value === null || params.value === undefined ? "-" : `${params.value}F`,
  },
  {
    field: "status",
    headerName: "判定",
    width: 140,
    sort: "asc",
    comparator: (a: LegalReportStatus, b: LegalReportStatus) =>
      STATUS_SORT_RANK[a] - STATUS_SORT_RANK[b],
    cellClass: (params) => `legal-report-status-${params.value}`,
    valueGetter: (params) => params.data?.status,
    valueFormatter: (params) => STATUS_LABELS[params.value as LegalReportStatus],
  },
  {
    headerName: "実測値",
    width: 110,
    valueGetter: (params) => (params.data ? formatMeasuredValue(params.data) : ""),
  },
  {
    field: "thresholdText",
    headerName: "基準値",
    width: 130,
  },
  {
    field: "evidenceText",
    headerName: "内訳(参考値)",
    flex: 1,
    minWidth: 260,
    wrapText: true,
    autoHeight: true,
  },
];

// activeがtrueの間、経過秒数を1秒おきに更新して返す(AgentChatTab.tsxの
// 同名フックと同じ考え方)。法規レポート生成は登録済み全ルール(12件)を
// BIMデータから再計算した上でLLM(Claude)を呼び、件数・条文引用の不一致が
// 検出されると最大1回追加でレポートを再生成する(=最大2回連続でClaude
// 呼び出し)ため、他のAPI呼び出しより本質的に時間がかかる
// (2026-08-14実測: 約178秒)。「生成中...」のまま止まって見えないよう、
// 経過時間と目安を可視化する。
function useElapsedSeconds(active: boolean): number {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    if (!active) {
      return;
    }

    const startedAt = Date.now();

    const interval = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);

    return () => {
      window.clearInterval(interval);
      setSeconds(0);
    };
  }, [active]);

  return seconds;
}

// この秒数を超えたら「時間がかかっています」の案内を出す
// (api/client.tsのLEGAL_REPORT_TIMEOUT_MS、8分より十分短く設定)。
const SLOW_RESPONSE_HINT_SECONDS = 60;

function LegalReportTab() {
  const reportMutation = useMutation({ mutationFn: generateLegalReport });
  const elapsedSeconds = useElapsedSeconds(reportMutation.isPending);

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
        登録ルール数が多く、LLM呼び出しが複数回連続することがあるため、
        生成には数分かかることがあります。
      </p>

      <div className="button-row">
        <button onClick={() => reportMutation.mutate()} disabled={reportMutation.isPending}>
          {reportMutation.isPending ? `生成中...(${elapsedSeconds}秒経過)` : "レポート生成"}
        </button>
      </div>

      {reportMutation.isPending && elapsedSeconds >= SLOW_RESPONSE_HINT_SECONDS && (
        <p className="hint">
          法規レポートは全チェック項目の再計算とLLMによる要約を行うため、
          数分かかることがあります(目安: 3〜5分)。しばらくお待ちください。
        </p>
      )}

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
    () =>
      check.items.map((item) => ({
        ...item,
        statusLabel: STATUS_LABELS[item.status],
        thresholdText: formatThreshold(check),
        evidenceText: formatEvidence(item.evidence),
      })),
    [check]
  );

  // 100件超のチェックもあるため、行数に応じて高さを可変にしつつ上限を設ける
  // (画面いっぱいに1チェックが広がりすぎないように)。
  // (2026-08-14修正)内訳(参考値)列がwrapText/autoHeightで複数行になり
  // うるため、1行あたりの見積もりと上限を引き上げた。
  const gridHeight = Math.min(Math.max(rows.length, 3) * 46 + 46, 560);

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
