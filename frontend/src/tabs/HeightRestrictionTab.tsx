import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approveRoadSlantEnvelope,
  getRoadSlantEnvelope,
  getWriteAuditLog,
  proposeRoadSlantEnvelope,
  type RoadSlantEnvelopeEntry,
  type RoadSlantEnvelopeProposal,
} from "../api/client";

const LAND_USE_OPTIONS: { value: string; label: string }[] = [
  { value: "residential", label: "住居系(勾配1.25)" },
  { value: "commercial", label: "商業系(勾配1.5)" },
  { value: "industrial", label: "工業系(勾配1.5)" },
];

function heightRange(entry: RoadSlantEnvelopeEntry): string {
  if (!entry.resolved || entry.vertices.length === 0) return "-";
  const heights = entry.vertices.map((v) => v.z_m);
  const min = Math.min(...heights);
  const max = Math.max(...heights);
  return `${min.toFixed(2)}m 〜 ${max.toFixed(2)}m`;
}

function HeightRestrictionTab() {
  const queryClient = useQueryClient();
  const [landUseCategory, setLandUseCategory] = useState("residential");
  const [previewed, setPreviewed] = useState(false);
  const [approvedResults, setApprovedResults] = useState<Record<number, string | null>>({});
  const [showAuditLog, setShowAuditLog] = useState(false);

  const previewQuery = useQuery({
    queryKey: ["road-slant-envelope", landUseCategory],
    queryFn: () => getRoadSlantEnvelope(landUseCategory),
    enabled: previewed,
  });

  const proposeMutation = useMutation({
    mutationFn: () => proposeRoadSlantEnvelope(landUseCategory),
  });

  const approveMutation = useMutation({
    mutationFn: (proposalId: number) => approveRoadSlantEnvelope(proposalId),
    onSuccess: async (data, proposalId) => {
      setApprovedResults((prev) => ({ ...prev, [proposalId]: data.result_guid }));
      await queryClient.invalidateQueries({ queryKey: ["write-audit-log"] });
    },
  });

  const auditLogQuery = useQuery({
    queryKey: ["write-audit-log"],
    queryFn: () => getWriteAuditLog(),
    enabled: showAuditLog,
  });

  const resolvedEntries = (previewQuery.data ?? []).filter((e) => e.resolved);

  return (
    <div className="tab-panel">
      <h2>高さ制限(道路斜線制限)</h2>

      <p className="hint">
        建築基準法56条1項1号の道路斜線制限を、敷地境界線Zoneと前面道路Zoneの
        幾何データから近似計算し、確認用のMesh要素としてArchicad本体へ
        書き込めます(参考値、法的な適合を保証するものではありません)。
        Archicadへの書き込みは必ず内容を確認し、明示的に承認した場合のみ
        実行されます(許可制)。
      </p>

      <div className="status-line">
        <label>
          用途地域:{" "}
          <select
            value={landUseCategory}
            onChange={(e) => {
              setLandUseCategory(e.target.value);
              setPreviewed(false);
            }}
          >
            {LAND_USE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => setPreviewed(true)}>計算する</button>
      </div>

      {previewQuery.isLoading && <p>計算中...</p>}
      {previewQuery.isError && <p className="error">{String(previewQuery.error)}</p>}

      {previewed && previewQuery.data && (
        <div className="height-restriction-preview">
          <table>
            <thead>
              <tr>
                <th>敷地</th>
                <th>判定</th>
                <th>頂点数</th>
                <th>高さ範囲</th>
              </tr>
            </thead>
            <tbody>
              {previewQuery.data.map((entry) => (
                <tr key={entry.site_guid}>
                  <td>{entry.site_name}</td>
                  <td>{entry.resolved ? "計算可能" : "前面道路が見つからず判定不能"}</td>
                  <td>{entry.vertices.length}</td>
                  <td>{heightRange(entry)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {resolvedEntries.length > 0 && (
            <button onClick={() => proposeMutation.mutate()} disabled={proposeMutation.isPending}>
              {proposeMutation.isPending ? "提案中..." : "Archicadへの書き込みを提案する"}
            </button>
          )}
        </div>
      )}

      {proposeMutation.isError && <p className="error">{String(proposeMutation.error)}</p>}

      {proposeMutation.data && (
        <div className="height-restriction-proposals">
          <h3>提案内容(まだArchicadへは書き込まれていません)</h3>
          {proposeMutation.data.proposals.length === 0 && (
            <p className="hint">提案できる敷地がありませんでした。</p>
          )}
          {proposeMutation.data.proposals.map((proposal: RoadSlantEnvelopeProposal) => {
            const approvedGuid = approvedResults[proposal.proposal_id];
            return (
              <div key={proposal.proposal_id} className="height-restriction-proposal-card">
                <p>{proposal.summary}</p>
                {approvedGuid === undefined ? (
                  <button
                    onClick={() => approveMutation.mutate(proposal.proposal_id)}
                    disabled={approveMutation.isPending}
                  >
                    {approveMutation.isPending ? "書き込み中..." : "承認してArchicadへ書き込む"}
                  </button>
                ) : (
                  <p className="hint">
                    {approvedGuid
                      ? `書き込み完了(guid: ${approvedGuid})`
                      : "書き込みは完了しましたが、作成された要素のguidを取得できませんでした。"}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}

      {approveMutation.isError && <p className="error">{String(approveMutation.error)}</p>}

      <div className="status-line">
        <button onClick={() => setShowAuditLog((v) => !v)}>
          {showAuditLog ? "監査ログを隠す" : "監査ログを表示"}
        </button>
      </div>

      {showAuditLog && (
        <div className="height-restriction-audit-log">
          {auditLogQuery.isLoading && <p>読み込み中...</p>}
          {auditLogQuery.data && auditLogQuery.data.length === 0 && (
            <p className="hint">まだ書き込み操作はありません。</p>
          )}
          {auditLogQuery.data && auditLogQuery.data.length > 0 && (
            <table>
              <thead>
                <tr>
                  <th>日時</th>
                  <th>操作</th>
                  <th>状態</th>
                  <th>作成guid</th>
                  <th>エラー</th>
                </tr>
              </thead>
              <tbody>
                {auditLogQuery.data.map((entry) => (
                  <tr key={entry.id}>
                    <td>{entry.created_at}</td>
                    <td>{entry.action}</td>
                    <td>{entry.status}</td>
                    <td>{entry.result_guid ?? "-"}</td>
                    <td>{entry.error_message ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

export default HeightRestrictionTab;
