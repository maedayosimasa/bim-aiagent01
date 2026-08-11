import { useMutation } from "@tanstack/react-query";
import { generateLegalReport, type LegalReportCheck, type LegalReportStatus } from "../api/client";

const STATUS_LABELS: Record<LegalReportStatus, string> = {
  pass: "PASS",
  fail: "FAIL",
  unknown: "UNKNOWN",
  not_applicable: "対象外",
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
          <div className="agent-message agent-message-ai legal-report-summary">
            {reportMutation.data.report}
          </div>

          <h3>チェック項目別詳細</h3>
          {reportMutation.data.checks.map((check) => {
            const counts = countByStatus(check);
            return (
              <details key={check.rule_id} className="legal-report-check">
                <summary>
                  {check.title} — PASS {counts.pass} / FAIL {counts.fail} / UNKNOWN{" "}
                  {counts.unknown}
                </summary>

                <p className="hint">{check.disclaimer}</p>

                <table className="legal-law-table">
                  <thead>
                    <tr>
                      <th>対象</th>
                      <th>判定</th>
                      <th>実測値</th>
                    </tr>
                  </thead>
                  <tbody>
                    {check.items.map((item) => (
                      <tr key={item.target_guid}>
                        <td>{item.target_name ?? item.target_guid}</td>
                        <td className={`legal-report-status-${item.status}`}>
                          {STATUS_LABELS[item.status]}
                        </td>
                        <td>
                          {item.measured_value ?? "-"}
                          {item.unit ?? ""}
                        </td>
                      </tr>
                    ))}
                    {check.items.length === 0 && (
                      <tr>
                        <td colSpan={3}>対象要素がありません。</td>
                      </tr>
                    )}
                  </tbody>
                </table>

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
          })}
        </>
      )}
    </div>
  );
}

export default LegalReportTab;
