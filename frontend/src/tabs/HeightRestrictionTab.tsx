import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  approveHeightRestrictionEnvelope,
  getAdjacentBoundarySlantEnvelope,
  getNorthSlantEnvelope,
  getRoadSlantEnvelope,
  getWriteAuditLog,
  proposeAdjacentBoundarySlantEnvelope,
  proposeNorthSlantEnvelope,
  proposeRoadSlantEnvelope,
  type HeightRestrictionEnvelopeProposal,
  type HeightRestrictionEnvelopeVertex,
} from "../api/client";

type EnvelopeType = "road" | "adjacent" | "north";

type EnvelopeEntry = {
  site_guid: string;
  site_name: string;
  resolved: boolean;
  vertices: HeightRestrictionEnvelopeVertex[];
};

const ENVELOPE_LABELS: Record<EnvelopeType, string> = {
  road: "道路斜線制限(56条1項1号)",
  adjacent: "隣地斜線制限(56条1項2号)",
  north: "北側斜線制限(56条1項3号)",
};

const LAND_USE_OPTIONS: { value: string; label: string }[] = [
  { value: "residential", label: "住居系" },
  { value: "commercial", label: "商業系" },
  { value: "industrial", label: "工業系" },
];

const KITAGAWA_KUBUN_OPTIONS: { value: string; label: string }[] = [
  { value: "low_rise", label: "低層住居専用地域等(立ち上がり5m)" },
  { value: "mid_rise", label: "中高層住居専用地域等(立ち上がり10m)" },
  { value: "not_applicable", label: "対象外の用途地域" },
];

function heightRange(entry: EnvelopeEntry): string {
  if (!entry.resolved || entry.vertices.length === 0) return "-";
  const heights = entry.vertices.map((v) => v.z_m);
  return `${Math.min(...heights).toFixed(2)}m 〜 ${Math.max(...heights).toFixed(2)}m`;
}

function HeightRestrictionTab() {
  const queryClient = useQueryClient();
  const [envelopeType, setEnvelopeType] = useState<EnvelopeType>("road");
  const [landUseCategory, setLandUseCategory] = useState("residential");
  const [kitagawaShasenKubun, setKitagawaShasenKubun] = useState("low_rise");
  const [northDegrees, setNorthDegrees] = useState<string>("");
  const [previewed, setPreviewed] = useState(false);
  const [approvedResults, setApprovedResults] = useState<Record<number, string | null>>({});
  const [showAuditLog, setShowAuditLog] = useState(false);

  const previewQuery = useQuery({
    queryKey: ["height-restriction-envelope", envelopeType, landUseCategory, kitagawaShasenKubun, northDegrees],
    queryFn: async (): Promise<EnvelopeEntry[]> => {
      if (envelopeType === "road") return getRoadSlantEnvelope(landUseCategory);
      if (envelopeType === "adjacent") return getAdjacentBoundarySlantEnvelope(landUseCategory);
      return getNorthSlantEnvelope(kitagawaShasenKubun, northDegrees ? Number(northDegrees) : undefined);
    },
    enabled: previewed,
  });

  const proposeMutation = useMutation({
    mutationFn: async (): Promise<{
      proposals: HeightRestrictionEnvelopeProposal<EnvelopeEntry>[];
      envelopes: EnvelopeEntry[];
    }> => {
      if (envelopeType === "road") return proposeRoadSlantEnvelope(landUseCategory);
      if (envelopeType === "adjacent") return proposeAdjacentBoundarySlantEnvelope(landUseCategory);
      return proposeNorthSlantEnvelope(kitagawaShasenKubun, northDegrees ? Number(northDegrees) : undefined);
    },
  });

  const approveMutation = useMutation({
    mutationFn: (proposalId: number) => approveHeightRestrictionEnvelope(proposalId),
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

  const previewData = previewQuery.data ?? [];
  const resolvedEntries = previewData.filter((e) => e.resolved);
  const proposals = proposeMutation.data?.proposals ?? [];

  const resetPreview = () => {
    setPreviewed(false);
    proposeMutation.reset();
  };

  return (
    <div className="tab-panel">
      <h2>高さ制限(斜線制限)</h2>

      <p className="hint">
        建築基準法56条1項の斜線制限を、敷地境界線Zone等の幾何データから近似
        計算し、確認用のMesh要素としてArchicad本体へ書き込めます(参考値、
        法的な適合を保証するものではありません)。Archicadへの書き込みは
        必ず内容を確認し、明示的に承認した場合のみ実行されます(許可制)。
      </p>

      <div className="status-line">
        <label>
          種類:{" "}
          <select
            value={envelopeType}
            onChange={(e) => {
              setEnvelopeType(e.target.value as EnvelopeType);
              resetPreview();
            }}
          >
            {(Object.keys(ENVELOPE_LABELS) as EnvelopeType[]).map((type) => (
              <option key={type} value={type}>
                {ENVELOPE_LABELS[type]}
              </option>
            ))}
          </select>
        </label>

        {(envelopeType === "road" || envelopeType === "adjacent") && (
          <label>
            用途地域:{" "}
            <select
              value={landUseCategory}
              onChange={(e) => {
                setLandUseCategory(e.target.value);
                resetPreview();
              }}
            >
              {LAND_USE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
        )}

        {envelopeType === "north" && (
          <>
            <label>
              適用区分:{" "}
              <select
                value={kitagawaShasenKubun}
                onChange={(e) => {
                  setKitagawaShasenKubun(e.target.value);
                  resetPreview();
                }}
              >
                {KITAGAWA_KUBUN_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              真北の角度(度、省略時はArchicadから取得):{" "}
              <input
                type="number"
                value={northDegrees}
                onChange={(e) => {
                  setNorthDegrees(e.target.value);
                  resetPreview();
                }}
                placeholder="Archicadから取得"
              />
            </label>
          </>
        )}

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
              {previewData.map((entry) => (
                <tr key={entry.site_guid}>
                  <td>{entry.site_name}</td>
                  <td>{entry.resolved ? "計算可能" : "判定不能(必要な情報が不足)"}</td>
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
          {proposals.length === 0 && (
            <p className="hint">提案できる敷地がありませんでした。</p>
          )}
          {proposals.map((proposal) => {
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
