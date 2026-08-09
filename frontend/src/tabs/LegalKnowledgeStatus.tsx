import { useEffect, useState } from "react";
import {
  getLegalLaws,
  getLegalStatus,
  type LegalApiStatus,
  type LegalLawMetadata,
} from "../api/client";

const LAW_TYPE_LABELS: Record<string, string> = {
  Act: "法律",
  CabinetOrder: "政令",
  MinisterialOrdinance: "省令",
  Rule: "規則",
  Other: "その他",
};

function LegalKnowledgeStatus() {
  const [status, setStatus] = useState<LegalApiStatus | null>(null);
  const [laws, setLaws] = useState<LegalLawMetadata[] | null>(null);
  const [lawsError, setLawsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    setLoading(true);
    getLegalStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
    getLegalLaws()
      .then((data) => {
        setLaws(data);
        setLawsError(null);
      })
      .catch((err) => {
        setLaws(null);
        setLawsError(String(err));
      })
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  const manifest = status?.detail?.manifest;

  return (
    <div className="legal-status">
      <div className="legal-status-header">
        <h3>Knowledge Package構成</h3>
        <button onClick={refresh} disabled={loading}>
          {loading ? "確認中..." : "再確認"}
        </button>
      </div>

      {status?.reachable && manifest && (
        <>
          <div className="connection-notice success">
            接続完了 — Legal Knowledge Builder APIから読み込み済み(バージョン {manifest.version})
          </div>
          <div className="legal-status-stats">
            <div className="legal-stat">
              <span className="legal-stat-value">{manifest.law_count}</span>
              <span className="legal-stat-label">法令</span>
            </div>
            <div className="legal-stat">
              <span className="legal-stat-value">{manifest.node_count.toLocaleString()}</span>
              <span className="legal-stat-label">条文ノード</span>
            </div>
            <div className="legal-stat">
              <span className="legal-stat-value">{manifest.reference_count.toLocaleString()}</span>
              <span className="legal-stat-label">
                引用関係
                <br />
                (未解決{" "}
                {manifest.reference_count > 0
                  ? Math.round(
                      (manifest.unresolved_reference_count / manifest.reference_count) * 100
                    )
                  : 0}
                %)
              </span>
            </div>
            <div className="legal-stat">
              <span className="legal-stat-value">{manifest.rule_count.toLocaleString()}</span>
              <span className="legal-stat-label">抽出ルール</span>
            </div>
            <div className="legal-stat">
              <span
                className={
                  "legal-stat-value " + (manifest.validation_passed ? "ok" : "ng")
                }
              >
                {manifest.validation_passed ? "OK" : "NG"}
              </span>
              <span className="legal-stat-label">Validation</span>
            </div>
          </div>
          <div className="hint">ビルド日時: {new Date(manifest.built_at).toLocaleString("ja-JP")}</div>
        </>
      )}

      {status && status.configured && !status.reachable && (
        <div className="connection-notice warning">
          Legal Knowledge Builder APIに接続できません({status.error ?? "不明なエラー"})。
          `uv run legal-knowledge-builder serve` が起動しているか確認してください。
        </div>
      )}

      {status && !status.configured && (
        <div className="connection-notice warning">
          LEGAL_API_URLが未設定です。backendの.envに設定してください(既定:
          http://127.0.0.1:8100)。
        </div>
      )}

      {laws && laws.length > 0 && (
        <table className="legal-law-table">
          <thead>
            <tr>
              <th>法令名</th>
              <th>種別</th>
              <th>カテゴリ</th>
              <th>時点</th>
            </tr>
          </thead>
          <tbody>
            {laws.map((law) => (
              <tr key={law.law_id}>
                <td>{law.law_title}</td>
                <td>{LAW_TYPE_LABELS[law.law_type] ?? law.law_type}</td>
                <td>{law.category}</td>
                <td>{law.as_of_date}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {lawsError && <p className="error">{lawsError}</p>}
    </div>
  );
}

export default LegalKnowledgeStatus;
